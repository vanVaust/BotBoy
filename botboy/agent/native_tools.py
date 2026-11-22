"""Native skills exposed to the secure agent as labelled tools.

Every tool here compiles to a command for one packaged native skill
(``botboy/skills/native``). The labels follow one rule set, applied per tool
in :func:`native_tools` and checked by ``tests/test_agent_native_tools.py``:

Parameter kinds
  ``control``  anything that selects *what* is read or *where* data goes: file
               and directory paths, URLs, SQL, query clauses, revisions, column
               names, regex patterns, archive member names and numeric knobs.
               Policy invariant I3 keeps untrusted values out of them, so a
               prompt injection cannot pick the next file, query or URL.
  ``data``     payload that is only computed over (text to analyse, an
               arithmetic program). Pure functions without effects may take
               untrusted data; their output then stays untrusted.

Effects
  ``read``     every tool except ``fetch.url``. No tool here writes, deletes
               or executes anything; the skills behind them are read-only and
               workspace-confined (or, for ``text``/``expr``, have no I/O).
  ``egress``   ``fetch.url``: the request itself leaves the machine, so the
               URL (which can encode data in its path or query) is control.

Output trust
  ``untrusted`` for every tool whose output contains bytes from outside the
               trusted task: workspace files and names, CSV/JSON cells, SQLite
               rows, archive members, git history, fetched pages.
  ``inherit``  for pure computations over their arguments (``expr``, ``text``):
               the result is as trustworthy as the least trusted input.

Command compilation
  Each template starts with a literal ``<trigger> <action>`` prefix. Arguments
  are first flattened by :func:`botboy.agent.tools.sanitize_argument`, then
  encoded per parameter:

  ``word``  one shell-style token (``shlex.quote``); values starting with
            ``--`` are refused so they cannot turn into options.
  ``int``   a bounded decimal integer.
  ``text``  verbatim free text; only in positions the skill reads as raw text
            (after ``--`` where the skill parses leading options), and never
            containing a separator the skill splits on.

  So argument text can neither change which handler runs nor add options.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Tuple

from botboy.agent.tools import ParamSpec, ToolArgumentError, ToolSpec, sanitize_argument

# The router rejects commands longer than 4096 characters (validation.py);
# leave room for the template and quoting so a valid argument never produces
# an over-long command.
_TEXT_DATA_CHARS = 3_500
_PATH_CHARS = 512
_DECIMAL = re.compile(r"^[0-9]{1,6}$")
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")

Encoding = str  # "word" | "int" | "text"


@dataclass(frozen=True)
class NativeToolSpec(ToolSpec):
    """A :class:`ToolSpec` whose arguments are encoded per parameter.

    ``encodings`` maps each parameter to ``word``, ``int`` or ``text``;
    ``int_ranges`` bounds ``int`` parameters; ``forbidden`` lists substrings
    a parameter may not contain (separators the skill splits on).
    """

    skill: str = ""
    encodings: Mapping[str, Encoding] = field(default_factory=dict)
    int_ranges: Mapping[str, Tuple[int, int]] = field(default_factory=dict)
    forbidden: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        names = _PLACEHOLDER.findall(self.command_template)
        if sorted(names) != sorted(self.params) or len(set(names)) != len(names):
            raise ValueError(f"{self.name}: template placeholders must match params exactly once")
        prefix = self.command_template.split("{", 1)[0]
        head = prefix.split()
        if not head or head[0] != self.skill:
            raise ValueError(f"{self.name}: template must start with the literal trigger {self.skill!r}")
        if set(self.encodings) != set(self.params):
            raise ValueError(f"{self.name}: every parameter needs an encoding")
        for name, encoding in self.encodings.items():
            if encoding not in {"word", "int", "text"}:
                raise ValueError(f"{self.name}: unknown encoding {encoding!r} for {name}")
            if encoding == "int" and name not in self.int_ranges:
                raise ValueError(f"{self.name}: int parameter {name} needs a range")

    @property
    def fixed_prefix(self) -> str:
        return self.command_template.split("{", 1)[0].strip()

    def build_command(self, values: Mapping[str, str]) -> str:
        encoded = {}
        for name, spec in self.params.items():
            text = sanitize_argument(values[name], spec.max_chars, name)
            for token in self.forbidden.get(name, ()):
                if token in text:
                    raise ToolArgumentError(f"argument '{name}' may not contain {token!r}")
            encoded[name] = self._encode(name, text)
        return self.command_template.format(**encoded)

    def _encode(self, name: str, text: str) -> str:
        encoding = self.encodings[name]
        if encoding == "int":
            low, high = self.int_ranges[name]
            if not _DECIMAL.match(text) or not low <= int(text) <= high:
                raise ToolArgumentError(f"argument '{name}' must be an integer between {low} and {high}")
            return str(int(text))
        if encoding == "word":
            if text.startswith("--"):
                raise ToolArgumentError(f"argument '{name}' may not start with '--'")
            return shlex.quote(text)
        return text


def _data_text(result: Mapping[str, Any]) -> Optional[str]:
    """``data.text`` (content without line numbers); ``None`` falls back to output."""
    data = result.get("data")
    text = data.get("text") if isinstance(data, dict) else None
    return text if isinstance(text, str) else None


def _tool(name: str, description: str, template: str, params: Mapping[str, Tuple[str, str, Encoding, int]],
          *, effect: str = "read", output_trust: str = "untrusted",
          int_ranges: Optional[Mapping[str, Tuple[int, int]]] = None,
          forbidden: Optional[Mapping[str, Tuple[str, ...]]] = None,
          extract_output: Optional[Callable[[Mapping[str, Any]], Optional[str]]] = None) -> NativeToolSpec:
    return NativeToolSpec(
        name=name,
        description=description,
        params={pname: ParamSpec(kind, desc, limit) for pname, (kind, desc, _enc, limit) in params.items()},
        effect=effect,  # type: ignore[arg-type]
        output_trust=output_trust,  # type: ignore[arg-type]
        command_template=template,
        tags=("native",),
        extract_output=extract_output,  # type: ignore[arg-type]
        skill=template.split()[0],
        encodings={pname: enc for pname, (_kind, _desc, enc, _limit) in params.items()},
        int_ranges=dict(int_ranges or {}),
        forbidden=dict(forbidden or {}),
    )


_PATH = ("control", "path relative to the workspace root ('.' for the root)", "word", _PATH_CHARS)
_FILE = ("control", "file path relative to the workspace root", "word", _PATH_CHARS)
_REPO = ("control", "repository directory relative to the workspace root ('.' for the root)", "word",
         _PATH_CHARS)


def native_tools() -> dict[str, NativeToolSpec]:
    """Agent tools for every packaged native skill (see module docstring)."""
    tools = (
        # ── pure computation: data in, output inherits the inputs' trust ──
        _tool("expr.eval",
              "Evaluate a sandboxed math/statistics program (variables, lists, mean, stdev, ...).",
              "expr {expression}",
              {"expression": ("data", "expression or ';'-separated program", "text", _TEXT_DATA_CHARS)},
              output_trust="inherit"),
        _tool("text.stats", "Word, sentence and readability statistics of a text.",
              "text stats {text}",
              {"text": ("data", "text to analyse", "text", _TEXT_DATA_CHARS)},
              output_trust="inherit"),
        _tool("text.keywords", "Top key phrases of a text (RAKE).",
              "text keywords --top {top} -- {text}",
              {"top": ("control", "number of phrases, 1-50", "int", 4),
               "text": ("data", "text to analyse", "text", _TEXT_DATA_CHARS)},
              output_trust="inherit", int_ranges={"top": (1, 50)}),
        _tool("text.summarize", "Extractive summary: the most central sentences of a text.",
              "text summarize --sentences {sentences} -- {text}",
              {"sentences": ("control", "number of sentences, 1-20", "int", 4),
               "text": ("data", "text to summarise", "text", _TEXT_DATA_CHARS)},
              output_trust="inherit", int_ranges={"sentences": (1, 20)}),
        _tool("text.diff", "Line and word diff between two texts with a similarity score.",
              "text diff {old} ||| {new}",
              {"old": ("data", "original text (may not contain '|||')", "text", 1_700),
               "new": ("data", "changed text", "text", 1_700)},
              output_trust="inherit", forbidden={"old": ("|||",)}),
        _tool("text.regex", "Find regular-expression matches (with groups) in a text; runs in a killable worker.",
              "text regex -- {pattern} ||| {subject}",
              {"pattern": ("control", "Python regular expression (may not contain '|||')", "text", 500),
               "subject": ("data", "text to search", "text", 3_000)},
              output_trust="inherit", forbidden={"pattern": ("|||",)}),
        # ── workspace files: names and contents are attacker-controllable ──
        _tool("ws.list", "List a workspace directory (names, sizes, dates; hidden entries and links skipped).",
              "ws list {path}", {"path": _PATH}),
        _tool("ws.read", "Read a text file from the workspace (content, capped at 8000 characters).",
              "ws read {path}", {"path": _FILE}, extract_output=_data_text),
        _tool("ws.grep", "Search workspace text files for a regular expression; returns matching lines.",
              "ws grep {pattern} {path}",
              {"pattern": ("control", "regular expression", "word", 500), "path": _PATH}),
        _tool("ws.stat", "Size, type, modification time, line count and SHA-256 of a workspace path.",
              "ws stat {path}", {"path": _PATH}),
        _tool("ws.summarize", "Extractive summary of a workspace text/Markdown/HTML file.",
              "ws summarize {path}", {"path": _FILE}),
        _tool("ws.diff", "Unified diff between two workspace text files.",
              "ws diff {a} {b}", {"a": _FILE, "b": _FILE}),
        # ── tabular data files ──
        _tool("data.schema", "Columns, inferred types and null counts of a CSV/TSV/JSON/JSONL file.",
              "data schema {file}", {"file": _FILE}),
        _tool("data.head", "First rows of a CSV/TSV/JSON/JSONL file.",
              "data head {file} {rows}",
              {"file": _FILE, "rows": ("control", "number of rows, 1-100", "int", 4)},
              int_ranges={"rows": (1, 100)}),
        _tool("data.stats", "Statistics (count, min, max, mean, median, quartiles, top values) of one column.",
              "data stats {file} {column}",
              {"file": _FILE, "column": ("control", "column name", "word", 200)}),
        _tool("data.query", "Filter/select/group/aggregate/sort a data file, e.g. "
              "'where region == EU select date,amount sort amount desc limit 10'.",
              "data query {file} {query}",
              {"file": _FILE, "query": ("control", "query clauses (where/select/group by/agg/sort/limit)",
                                        "text", 1_000)}),
        # ── network egress ──
        _tool("fetch.url", "Fetch a public http(s) page as readable text (SSRF-protected; request leaves this machine).",
              "fetch {url}",
              {"url": ("control", "http(s) URL; it is sent off-host", "word", 2_000)},
              effect="egress"),
        # ── git history: commit messages, names and diffs are attacker-controllable ──
        _tool("repo.status", "Branch, ahead/behind and changed files of a git repository in the workspace.",
              "repo status {path}", {"path": _REPO}),
        _tool("repo.log", "Recent commits of a git repository in the workspace.",
              "repo log {path} -n {count}",
              {"path": _REPO, "count": ("control", "number of commits, 1-200", "int", 4)},
              int_ranges={"count": (1, 200)}),
        _tool("repo.diff", "Changed files with line counts in the working tree of a repository.",
              "repo diff {path}", {"path": _REPO}),
        _tool("repo.show", "One commit: header, message and changed files.",
              "repo show {rev} {path}",
              {"rev": ("control", "commit, branch or tag", "word", 200), "path": _REPO}),
        _tool("forge.lint", "Validate a SKILL.md file or directory of skills in the workspace.",
              "forge lint {path}", {"path": _PATH}),
        # ── SQLite databases ──
        _tool("sqlite.tables", "Tables and views of an SQLite database in the workspace, with row counts.",
              "sqlite tables {file}", {"file": _FILE}),
        _tool("sqlite.schema", "Columns, keys and indexes of one table of an SQLite database.",
              "sqlite schema {file} {table}",
              {"file": _FILE, "table": ("control", "table or view name", "word", 200)}),
        _tool("sqlite.query", "Run one read-only SELECT on an SQLite database in the workspace "
              "(no writes, PRAGMA or ATTACH; row, time and memory budgets).",
              "sqlite query {file} {sql}",
              {"file": _FILE, "sql": ("control", "a single SELECT/WITH statement", "text", 2_000)}),
        # ── archives (never extracted to disk) ──
        _tool("archive.list", "List a zip/tar(.gz/.bz2/.xz) archive and flag unsafe entries "
              "(path traversal, links, bombs).",
              "archive list {file}", {"file": _FILE}),
        _tool("archive.check", "Verify an archive by streaming every member under a budget "
              "(CRC, real vs declared size, compression ratio); verdict on safe extraction.",
              "archive check {file}", {"file": _FILE}),
        _tool("archive.read", "Read one text member of an archive without extracting it.",
              "archive read {file} {member}",
              {"file": _FILE, "member": ("control", "exact member name as listed", "word", 1_000)},
              extract_output=_data_text),
    )
    return {tool.name: tool for tool in tools}


__all__ = ["NativeToolSpec", "native_tools"]
