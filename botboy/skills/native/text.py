"""``text`` — analytics on inline text: stats, keywords, summary, diff, regex.

Pure computation on the command's own text: no filesystem, no network. The
only side effect is the short-lived regex worker process (see
``isolated_regex``), which receives data on stdin and is killed on timeout.
"""
from __future__ import annotations

from typing import Dict, Tuple

from botboy.skills.native import textkit
from botboy.skills.native.base import (
    NativeSkillContext,
    SkillUsageError,
    failure,
    raw_argument_text,
    result,
    truncate,
)
from botboy.skills.native.isolated_regex import RegexSandboxError, parse_flags, run_regex

SEPARATOR = "|||"
_USAGE = (
    "Usage:\n"
    "  text stats <text>\n"
    "  text keywords [--top N] <text>\n"
    "  text summarize [--sentences N] <text>\n"
    "  text diff <old text> ||| <new text>\n"
    "  text regex [--flags imsxa] <pattern> ||| <subject>"
)


def leading_options(raw: str, valued: Tuple[str, ...]) -> Tuple[Dict[str, str], str]:
    """Consume ``--name value`` pairs at the start of free text.

    Free text is not shlex-split (apostrophes in prose are not quotes), so
    options are only recognised before the text begins.
    """
    options: Dict[str, str] = {}
    rest = raw.lstrip()
    while rest.startswith("--"):
        head, _, tail = rest.partition(" ")
        if head == "--":
            # End of options: the text that follows is taken verbatim, even
            # if it starts with "--" (the agent bridge always emits this).
            return options, tail.lstrip()
        name, sep, inline = head[2:].partition("=")
        if name not in valued:
            raise SkillUsageError(f"Unknown option --{name}")
        if sep:
            options[name] = inline
            rest = tail.lstrip()
            continue
        value, _, tail = tail.lstrip().partition(" ")
        if not value:
            raise SkillUsageError(f"--{name} requires a value")
        options[name] = value
        rest = tail.lstrip()
    return options, rest


def bounded_int(options: Dict[str, str], name: str, default: int, low: int, high: int) -> int:
    if name not in options:
        return default
    try:
        value = int(options[name])
    except ValueError as exc:
        raise SkillUsageError(f"--{name} expects an integer") from exc
    if not low <= value <= high:
        raise SkillUsageError(f"--{name} must be between {low} and {high}")
    return value


def split_pair(raw: str, what: str) -> Tuple[str, str]:
    if SEPARATOR not in raw:
        raise SkillUsageError(f"{what} needs two parts separated by '{SEPARATOR}'")
    left, _, right = raw.partition(SEPARATOR)
    return left.strip(), right.strip()


async def run(command: str, ctx: NativeSkillContext) -> dict:
    parts = command.strip().split(None, 2)
    if len(parts) < 2 or parts[1].lower() in {"help", "--help"}:
        return result("text", _USAGE, usage=True) if len(parts) >= 2 else failure("text", _USAGE)
    action = parts[1].lower()
    raw = raw_argument_text(command, 2)
    try:
        if action == "stats":
            if not raw.strip():
                raise SkillUsageError("text stats needs some text")
            stats = textkit.text_stats(raw)
            return result("text", textkit.render_stats(stats), action="stats", stats=stats.to_dict())
        if action == "keywords":
            options, body = leading_options(raw, ("top",))
            top = bounded_int(options, "top", 10, 1, 50)
            if not body.strip():
                raise SkillUsageError("text keywords needs some text")
            keywords = textkit.extract_keywords(body, top)
            return result(
                "text",
                textkit.render_keywords(keywords),
                action="keywords",
                keywords=[{"phrase": k.phrase, "score": k.score, "frequency": k.frequency} for k in keywords],
                terms=textkit.term_frequencies(body, top),
            )
        if action == "summarize":
            options, body = leading_options(raw, ("sentences",))
            count = bounded_int(options, "sentences", 3, 1, 20)
            if not body.strip():
                raise SkillUsageError("text summarize needs some text")
            summary = textkit.summarize(body, count)
            return result(
                "text",
                " ".join(summary.sentences) or "Nothing to summarise.",
                action="summarize",
                sentences=summary.sentences,
                indices=summary.indices,
                total_sentences=summary.total_sentences,
            )
        if action == "diff":
            old, new = split_pair(raw, "text diff")
            diff = textkit.diff_texts(old, new, a_label="old", b_label="new")
            body = diff.inline or diff.unified or "No differences."
            output = f"{body}\nsimilarity={diff.similarity} +{diff.added_lines}/-{diff.removed_lines} lines"
            return result(
                "text", output, action="diff", unified=diff.unified, inline=diff.inline,
                similarity=diff.similarity, added_lines=diff.added_lines, removed_lines=diff.removed_lines,
            )
        if action == "regex":
            options, body = leading_options(raw, ("flags",))
            pattern, subject = split_pair(body, "text regex")
            outcome = await run_regex(pattern, [subject], flags=parse_flags(options.get("flags", "")), max_matches=100)
            if not outcome.matches:
                return result("text", f"No match for /{pattern}/.", action="regex", pattern=pattern, matches=[])
            lines = [f"{len(outcome.matches)} match(es) for /{pattern}/" + (" (truncated)" if outcome.truncated else "")]
            for match in outcome.matches[:50]:
                groups = f" groups={match['groups']}" if match["groups"] else ""
                named = f" named={match['named']}" if match["named"] else ""
                lines.append(f"  [{match['start']}:{match['end']}] {match['match']!r}{groups}{named}")
            output, _ = truncate("\n".join(lines), 6000)
            return result(
                "text", output, action="regex", pattern=pattern, matches=outcome.matches,
                truncated=outcome.truncated, group_count=outcome.group_count,
            )
    except (SkillUsageError, RegexSandboxError) as exc:
        return failure("text", f"text {action}: {exc}", action=action)
    return failure("text", f"Unknown text action {action!r}.\n{_USAGE}", action=action)
