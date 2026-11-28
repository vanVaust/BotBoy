"""``ws`` — read-only workspace file tools: list, read, grep, stat, analyse, diff.

All paths go through ``workspace.resolve``; nothing is ever written. Reads
are size-capped, binary files are refused, hidden entries and symlinks are
invisible, and regex grep runs in the killable isolated worker.
"""
from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from botboy.skills.native import textkit, workspace
from botboy.skills.native.base import (
    NativeSkillContext,
    SkillUsageError,
    failure,
    parse_options,
    result,
    split_command,
    truncate,
)
from botboy.skills.native.isolated_regex import RegexSandboxError, run_regex

MAX_READ_BYTES = 2 * 1024 * 1024
MAX_GREP_FILES = 2000
MAX_GREP_BYTES = 12 * 1024 * 1024
MAX_GREP_FILE_BYTES = 1024 * 1024
DEFAULT_OUTPUT_CHARS = 8000

_USAGE = (
    "Usage (paths are relative to the workspace root):\n"
    "  ws list [dir] [--depth N]\n"
    "  ws read <file> [--lines A:B] [--max-chars N]\n"
    "  ws grep <pattern> [dir|file] [--glob '*.py'] [--ignore-case] [--fixed] [--max N]\n"
    "  ws stat <path>\n"
    "  ws stats|keywords|summarize <file> [--top N] [--sentences N]\n"
    "  ws chunk <file> [--size N] [--overlap N]\n"
    "  ws diff <file-a> <file-b>"
)


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{size}B"


def _parse_line_range(spec: str) -> Tuple[int, Optional[int]]:
    match = re.fullmatch(r"(\d+)?:(\d+)?|(\d+)", spec.strip())
    if not match:
        raise SkillUsageError("--lines expects A:B, A:, :B or N")
    if match.group(3):
        line = int(match.group(3))
        return max(line, 1), line
    start = int(match.group(1)) if match.group(1) else 1
    end = int(match.group(2)) if match.group(2) else None
    if end is not None and end < start:
        raise SkillUsageError("--lines end must not be before start")
    return max(start, 1), end


def _load_document(ctx: NativeSkillContext, user_path: str) -> Tuple[Path, str, bool]:
    path = workspace.resolve(ctx.workspace_root, user_path, expect="file")
    text, truncated = workspace.read_text(path, MAX_READ_BYTES, root=ctx.workspace_root)
    return path, text, truncated


async def _list(ctx: NativeSkillContext, args: List[str]) -> dict:
    opts = parse_options(args, valued=("depth",))
    if len(opts.positionals) > 1:
        raise SkillUsageError("ws list takes at most one directory")
    depth = opts.get_int("depth", 1, minimum=1, maximum=6)
    target = workspace.resolve(ctx.workspace_root, opts.positionals[0] if opts.positionals else "", expect="dir")
    entries: List[Dict[str, Any]] = []
    lines: List[str] = []
    for entry in workspace.walk(ctx.workspace_root, target, max_depth=depth, max_entries=500):
        info = entry.path.stat()
        record = {
            "path": entry.relative,
            "type": "dir" if entry.is_dir else "file",
            "size": None if entry.is_dir else info.st_size,
            "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(info.st_mtime)),
        }
        entries.append(record)
        indent = "  " * entry.depth
        name = entry.path.name + ("/" if entry.is_dir else "")
        size = "" if entry.is_dir else f"  {_human_size(info.st_size)}"
        lines.append(f"{indent}{name}{size}  {record['modified']}")
    rel = workspace.relative(ctx.workspace_root, target)
    header = f"{rel}: {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}" + (" (limit 500 reached)" if len(entries) >= 500 else "")
    return result("ws", "\n".join([header, *lines]) if lines else f"{rel}: empty", action="list", path=rel, entries=entries)


async def _read(ctx: NativeSkillContext, args: List[str]) -> dict:
    opts = parse_options(args, valued=("lines", "max-chars"))
    if len(opts.positionals) != 1:
        raise SkillUsageError("ws read <file> [--lines A:B] [--max-chars N]")
    max_chars = opts.get_int("max-chars", DEFAULT_OUTPUT_CHARS, minimum=100, maximum=60000)
    path, text, file_truncated = _load_document(ctx, opts.positionals[0])
    all_lines = text.splitlines()
    start, end = _parse_line_range(opts.get("lines", "1:") or "1:")
    selected = all_lines[start - 1: end]
    width = len(str(start + max(len(selected) - 1, 0)))
    numbered = "\n".join(f"{start + idx:>{width}}  {line}" for idx, line in enumerate(selected))
    output, out_truncated = truncate(numbered, max_chars)
    rel = workspace.relative(ctx.workspace_root, path)
    header = f"{rel} (lines {start}-{start + len(selected) - 1} of {len(all_lines)})"
    if file_truncated:
        header += f" [file truncated at {MAX_READ_BYTES} bytes]"
    # Unnumbered text of the same lines, within the same character budget,
    # for callers that process the content (the agent's ws.read tool).
    plain, plain_truncated = truncate("\n".join(selected), max_chars)
    return result(
        "ws", f"{header}\n{output}", action="read", path=rel, start_line=start,
        line_count=len(selected), total_lines=len(all_lines), truncated=file_truncated or out_truncated,
        text=plain, text_truncated=file_truncated or plain_truncated,
    )


def _collect_grep_documents(ctx: NativeSkillContext, user_path: str, glob: Optional[str]) -> Tuple[List[str], List[str], int]:
    """Read grep candidates off the event loop within file/byte budgets."""
    target = workspace.resolve(ctx.workspace_root, user_path)
    files: List[Path] = []
    if target.is_file():
        files.append(target)
    else:
        for entry in workspace.walk(ctx.workspace_root, target, max_depth=20, max_entries=20000):
            if entry.is_dir:
                continue
            if glob and not fnmatch.fnmatch(entry.path.name, glob) and not fnmatch.fnmatch(entry.relative, glob):
                continue
            files.append(entry.path)
            if len(files) >= MAX_GREP_FILES:
                break
    documents: List[str] = []
    names: List[str] = []
    total = 0
    skipped = 0
    for path in files:
        try:
            data, truncated = workspace.read_bytes(path, MAX_GREP_FILE_BYTES, root=ctx.workspace_root)
        except workspace.WorkspaceError:
            skipped += 1
            continue
        if truncated or workspace.looks_binary(data):
            skipped += 1
            continue
        if total + len(data) > MAX_GREP_BYTES:
            skipped += 1
            continue
        total += len(data)
        documents.append(data.decode("utf-8", errors="replace"))
        names.append(workspace.relative(ctx.workspace_root, path))
    return documents, names, skipped


async def _grep(ctx: NativeSkillContext, args: List[str]) -> dict:
    opts = parse_options(args, valued=("glob", "max"), flags=("ignore-case", "fixed"))
    if not 1 <= len(opts.positionals) <= 2:
        raise SkillUsageError("ws grep <pattern> [dir|file] [--glob G] [--ignore-case] [--fixed] [--max N]")
    pattern = opts.positionals[0]
    limit = opts.get_int("max", 100, minimum=1, maximum=500)
    glob = opts.get("glob")
    target_path = opts.positionals[1] if len(opts.positionals) > 1 else ""
    documents, names, skipped = await asyncio.to_thread(_collect_grep_documents, ctx, target_path, glob)
    hits: List[Dict[str, Any]] = []
    truncated_hits = False
    if opts.flag("fixed"):
        needle = pattern.lower() if opts.flag("ignore-case") else pattern
        for doc_index, doc in enumerate(documents):
            for line_no, line in enumerate(doc.split("\n"), 1):
                hay = line.lower() if opts.flag("ignore-case") else line
                position = hay.find(needle)
                if position != -1:
                    hits.append({"doc": doc_index, "line": line_no, "text": line[:400], "start": position, "end": position + len(needle)})
                    if len(hits) >= limit:
                        truncated_hits = True
                        break
            if truncated_hits:
                break
    elif documents:
        outcome = await run_regex(
            pattern, documents, flags=["IGNORECASE"] if opts.flag("ignore-case") else [],
            mode="lines", max_matches=limit, timeout_seconds=5.0,
        )
        hits, truncated_hits = outcome.matches, outcome.truncated
    matches = [{"path": names[hit["doc"]], "line": hit["line"], "text": hit["text"]} for hit in hits]
    lines = [f"{m['path']}:{m['line']}: {m['text']}" for m in matches]
    summary = f"{len(matches)} match(es) in {len({m['path'] for m in matches})} file(s); searched {len(documents)} file(s)"
    if skipped:
        summary += f", skipped {skipped} binary/oversized"
    if truncated_hits:
        summary += f" (stopped at {limit})"
    output, _ = truncate("\n".join([summary, *lines]), DEFAULT_OUTPUT_CHARS)
    return result("ws", output, action="grep", pattern=pattern, matches=matches,
                  files_searched=len(documents), skipped=skipped, truncated=truncated_hits)


async def _stat(ctx: NativeSkillContext, args: List[str]) -> dict:
    if len(args) != 1:
        raise SkillUsageError("ws stat <path>")
    path = workspace.resolve(ctx.workspace_root, args[0])
    info = path.stat()
    rel = workspace.relative(ctx.workspace_root, path)
    data: Dict[str, Any] = {
        "path": rel,
        "type": "dir" if path.is_dir() else "file",
        "size": info.st_size,
        "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(info.st_mtime)),
    }
    if path.is_file():
        content, truncated = workspace.read_bytes(path, MAX_READ_BYTES, root=ctx.workspace_root)
        data["sha256"] = hashlib.sha256(content).hexdigest() if not truncated else None
        data["binary"] = workspace.looks_binary(content)
        if not data["binary"]:
            data["lines"] = content.count(b"\n") + (1 if content and not content.endswith(b"\n") else 0)
    output = "\n".join(f"{key}: {value}" for key, value in data.items() if value is not None)
    return result("ws", output, action="stat", **data)


def _analyse(ctx: NativeSkillContext, action: str, args: List[str]) -> dict:
    opts = parse_options(args, valued=("top", "sentences", "size", "overlap"))
    if len(opts.positionals) != 1:
        raise SkillUsageError(f"ws {action} <file>")
    path, raw, truncated = _load_document(ctx, opts.positionals[0])
    rel = workspace.relative(ctx.workspace_root, path)
    text = textkit.strip_markup(raw, path.suffix)
    note = f" [analysed first {MAX_READ_BYTES} bytes]" if truncated else ""
    if action == "stats":
        stats = textkit.text_stats(text)
        return result("ws", f"{rel}{note}\n{textkit.render_stats(stats)}", action=action, path=rel, stats=stats.to_dict())
    if action == "keywords":
        top = opts.get_int("top", 15, minimum=1, maximum=50)
        keywords = textkit.extract_keywords(text, top)
        return result(
            "ws", f"{rel}{note}\n{textkit.render_keywords(keywords)}", action=action, path=rel,
            keywords=[{"phrase": k.phrase, "score": k.score, "frequency": k.frequency} for k in keywords],
        )
    if action == "summarize":
        count = opts.get_int("sentences", 5, minimum=1, maximum=30)
        summary = textkit.summarize(text, count)
        body = "\n".join(f"- {sentence}" for sentence in summary.sentences) or "Nothing to summarise."
        return result(
            "ws", f"{rel}{note}: {len(summary.sentences)} of {summary.total_sentences} sentences\n{body}",
            action=action, path=rel, sentences=summary.sentences, indices=summary.indices,
            total_sentences=summary.total_sentences,
        )
    size = opts.get_int("size", 1200, minimum=50, maximum=20000)
    overlap = opts.get_int("overlap", min(150, size // 4), minimum=0, maximum=size - 1)
    spans = textkit.chunk_spans(raw, size, overlap)
    total = len(spans)
    chunks = [textkit.Chunk(i, start, end, raw[start:end]) for i, (start, end) in enumerate(spans[:200])]
    preview = "\n".join(
        f"#{chunk.index} [{chunk.start}:{chunk.end}] {len(chunk.text)} chars: {' '.join(chunk.text.split())[:80]}"
        for chunk in chunks[:50]
    )
    return result(
        "ws", f"{rel}{note}: {total} chunk(s) (size<={size}, overlap<={overlap})\n{preview}",
        action=action, path=rel, size=size, overlap=overlap,
        chunks=[{"index": c.index, "start": c.start, "end": c.end, "text": c.text} for c in chunks[:200]],
        total_chunks=total,
    )


async def _diff(ctx: NativeSkillContext, args: List[str]) -> dict:
    opts = parse_options(args, valued=("context",))
    if len(opts.positionals) != 2:
        raise SkillUsageError("ws diff <file-a> <file-b>")
    context = opts.get_int("context", 3, minimum=0, maximum=20)
    path_a, text_a, trunc_a = _load_document(ctx, opts.positionals[0])
    path_b, text_b, trunc_b = _load_document(ctx, opts.positionals[1])
    rel_a = workspace.relative(ctx.workspace_root, path_a)
    rel_b = workspace.relative(ctx.workspace_root, path_b)
    diff = await asyncio.to_thread(textkit.diff_texts, text_a, text_b, a_label=rel_a, b_label=rel_b, context=context)
    body, truncated = truncate(diff.unified or "Files are identical.", DEFAULT_OUTPUT_CHARS)
    summary = f"similarity={diff.similarity} +{diff.added_lines}/-{diff.removed_lines} lines"
    return result(
        "ws", f"{body}\n{summary}", action="diff", a=rel_a, b=rel_b, similarity=diff.similarity,
        added_lines=diff.added_lines, removed_lines=diff.removed_lines,
        truncated=truncated or trunc_a or trunc_b,
    )


async def run(command: str, ctx: NativeSkillContext) -> dict:
    try:
        _, tokens = split_command(command)
        if not tokens or tokens[0].lower() in {"help", "--help"}:
            return result("ws", _USAGE, usage=True) if tokens else failure("ws", _USAGE)
        action, args = tokens[0].lower(), tokens[1:]
        if action in {"list", "ls"}:
            return await _list(ctx, args)
        if action in {"read", "cat"}:
            return await _read(ctx, args)
        if action == "grep":
            return await _grep(ctx, args)
        if action == "stat":
            return await _stat(ctx, args)
        if action in {"stats", "keywords", "summarize", "chunk"}:
            return await asyncio.to_thread(_analyse, ctx, action, args)
        if action == "diff":
            return await _diff(ctx, args)
        return failure("ws", f"Unknown ws action {action!r}.\n{_USAGE}")
    except (SkillUsageError, workspace.WorkspaceError, RegexSandboxError, ValueError) as exc:
        return failure("ws", f"ws: {exc}")
