"""``data`` — query CSV/TSV/JSON/JSONL files in the workspace.

A small, explicit query language (no SQL engine, no ``eval``) covering the
common analysis loop: inspect schema, filter, project, group, aggregate,
sort, limit. Files are read through the workspace confinement layer with a
hard size cap and row cap, and parsing runs off the event loop.

    data query sales.csv where region == EU and amount > 100 \
        group by product agg count sum:amount sort sum_amount desc limit 5
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import math
import re
import statistics
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from botboy.skills.native import workspace
from botboy.skills.native.base import (
    NativeSkillContext,
    SkillUsageError,
    failure,
    format_table,
    result,
    split_command,
    truncate,
)

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_ROWS = 200_000
MAX_LIMIT = 1000
DEFAULT_LIMIT = 50
MAX_DATA_ROWS = 200
MAX_FLATTEN_DEPTH = 4
SUPPORTED_SUFFIXES = {".csv", ".tsv", ".json", ".jsonl", ".ndjson"}

_INT_RE = re.compile(r"[+-]?\d{1,18}")
_FLOAT_RE = re.compile(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?")
_CLAUSES = {"where", "select", "group", "agg", "sort", "order", "limit", "format"}
_OPERATORS = {"==", "=", "!=", ">", ">=", "<", "<=", "contains", "startswith", "endswith", "in"}
_AGGREGATES = {"count", "sum", "avg", "mean", "min", "max", "median", "distinct"}

_USAGE = (
    "Usage (paths are relative to the workspace root; .csv .tsv .json .jsonl):\n"
    "  data schema <file> [--path a.b]\n"
    "  data head <file> [n]\n"
    "  data stats <file> <column>\n"
    "  data query <file> [where <col> <op> <value> [and|or ...]] [select a,b]\n"
    "             [group by a,b] [agg count sum:col avg:col min:col max:col median:col distinct:col]\n"
    "             [sort <col> [asc|desc], ...] [limit N] [format table|json|csv]\n"
    "  operators: == != > >= < <= contains startswith endswith in (comma list), isnull, notnull"
)


@dataclass
class Dataset:
    path: str
    rows: List[Dict[str, Any]]
    columns: List[str]
    truncated: bool = False


def _coerce_cell(value: str) -> Any:
    text = value.strip()
    if text == "":
        return None
    if _INT_RE.fullmatch(text):
        return int(text)
    if _FLOAT_RE.fullmatch(text):
        return float(text)
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none", "nan", "n/a", "na"}:
        return None
    return value


def _flatten(record: Any, prefix: str = "", depth: int = 0) -> Dict[str, Any]:
    if not isinstance(record, dict):
        return {prefix or "value": record}
    flat: Dict[str, Any] = {}
    for key, value in record.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict) and depth < MAX_FLATTEN_DEPTH:
            flat.update(_flatten(value, name, depth + 1))
        elif isinstance(value, (dict, list)):
            flat[name] = json.dumps(value, ensure_ascii=False, sort_keys=True)
        else:
            flat[name] = value
    return flat


def _columns_of(rows: Iterable[Dict[str, Any]]) -> List[str]:
    seen: Dict[str, None] = {}
    for row in rows:
        for key in row:
            seen.setdefault(key, None)
    return list(seen)


def _select_json_records(document: Any, path: Optional[str]) -> List[Any]:
    node = document
    if path:
        for part in path.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
                node = node[int(part)]
            else:
                raise SkillUsageError(f"--path segment {part!r} not found")
    if isinstance(node, list):
        return node
    if isinstance(node, dict):
        candidates = [key for key, value in node.items() if isinstance(value, list) and value and all(isinstance(v, dict) for v in value)]
        if len(candidates) == 1:
            return node[candidates[0]]
        if len(candidates) > 1:
            raise SkillUsageError(f"Several record arrays found ({', '.join(candidates)}); choose one with --path")
        return [node]
    return [node]


def load_dataset(ctx: NativeSkillContext, user_path: str, json_path: Optional[str] = None) -> Dataset:
    path = workspace.resolve(ctx.workspace_root, user_path, expect="file")
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise SkillUsageError(f"Unsupported file type {suffix or '(none)'}; use {', '.join(sorted(SUPPORTED_SUFFIXES))}")
    text, _ = workspace.read_text(path, MAX_FILE_BYTES, allow_truncation=False)
    rel = workspace.relative(ctx.workspace_root, path)
    rows: List[Dict[str, Any]] = []
    truncated = False
    if suffix in {".csv", ".tsv"}:
        if suffix == ".tsv":
            dialect: Any = csv.excel_tab
        else:
            try:
                dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
        reader = csv.reader(io.StringIO(text), dialect)
        try:
            header = next(reader)
        except StopIteration:
            return Dataset(rel, [], [], False)
        header = [name.strip() or f"column_{idx + 1}" for idx, name in enumerate(header)]
        try:
            for record in reader:
                if not record:
                    continue
                if len(rows) >= MAX_ROWS:
                    truncated = True
                    break
                rows.append({header[i] if i < len(header) else f"column_{i + 1}": _coerce_cell(cell) for i, cell in enumerate(record)})
        except csv.Error as exc:
            raise SkillUsageError(f"CSV parse error near row {len(rows) + 2}: {exc}") from exc
        columns = header + [col for col in _columns_of(rows) if col not in header]
        return Dataset(rel, rows, columns, truncated)
    if suffix == ".json":
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SkillUsageError(f"JSON parse error: {exc}") from exc
        records = _select_json_records(document, json_path)
    else:
        records = []
        for line_no, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SkillUsageError(f"JSONL parse error on line {line_no}: {exc.msg}") from exc
            if len(records) > MAX_ROWS:
                break
    if len(records) > MAX_ROWS:
        records = records[:MAX_ROWS]
        truncated = True
    rows = [_flatten(record) for record in records]
    return Dataset(rel, rows, _columns_of(rows), truncated)


# ── query model ─────────────────────────────────────────────────────────────

@dataclass
class Condition:
    column: str
    op: str
    value: Any = None

    def matches(self, row: Dict[str, Any]) -> bool:
        cell = row.get(self.column)
        if self.op == "isnull":
            return cell is None or cell == ""
        if self.op == "notnull":
            return not (cell is None or cell == "")
        if cell is None:
            return self.op == "!="
        if self.op in {"contains", "startswith", "endswith"}:
            hay, needle = str(cell).lower(), str(self.value).lower()
            return getattr(hay, "__contains__" if self.op == "contains" else self.op)(needle)
        if self.op == "in":
            return any(_equal(cell, candidate) for candidate in self.value)
        if self.op in {"==", "="}:
            return _equal(cell, self.value)
        if self.op == "!=":
            return not _equal(cell, self.value)
        left, right = _comparable(cell, self.value)
        try:
            return {">": left > right, ">=": left >= right, "<": left < right, "<=": left <= right}[self.op]
        except TypeError:
            return False


def _as_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and _FLOAT_RE.fullmatch(value.strip()):
        return float(value)
    return None


def _comparable(left: Any, right: Any) -> Tuple[Any, Any]:
    left_num, right_num = _as_number(left), _as_number(right)
    if left_num is not None and right_num is not None:
        return left_num, right_num
    return str(left), str(right)


def _equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return str(left).lower() == str(right).lower()
    a, b = _comparable(left, right)
    return a == b


@dataclass
class Query:
    where: List[List[Condition]] = field(default_factory=list)  # OR of AND-groups
    select: List[str] = field(default_factory=list)
    group_by: List[str] = field(default_factory=list)
    aggregates: List[Tuple[str, Optional[str]]] = field(default_factory=list)
    sort: List[Tuple[str, bool]] = field(default_factory=list)
    limit: int = DEFAULT_LIMIT
    output_format: str = "table"


def _split_names(tokens: Sequence[str]) -> List[str]:
    names = [name.strip() for name in ",".join(tokens).split(",")]
    return [name for name in names if name]


def parse_query(tokens: Sequence[str]) -> Query:
    query = Query()
    sections: List[Tuple[str, List[str]]] = []
    index = 0
    while index < len(tokens):
        word = tokens[index].lower()
        if word not in _CLAUSES:
            raise SkillUsageError(f"Expected a clause ({', '.join(sorted(_CLAUSES))}), got {tokens[index]!r}")
        index += 1
        if word in {"group", "order"} or (word == "sort" and index < len(tokens) and tokens[index].lower() == "by"):
            if index < len(tokens) and tokens[index].lower() == "by":
                index += 1
            elif word == "group":
                raise SkillUsageError("Use 'group by <columns>'")
        body: List[str] = []
        while index < len(tokens) and not (tokens[index].lower() in _CLAUSES and not _is_value_position(body, word)):
            body.append(tokens[index])
            index += 1
        sections.append(("sort" if word == "order" else word, body))
    for name, body in sections:
        if not body:
            raise SkillUsageError(f"Clause '{name}' is empty")
        if name == "where":
            query.where = _parse_conditions(body)
        elif name == "select":
            query.select = _split_names(body)
        elif name == "group":
            query.group_by = _split_names(body)
        elif name == "agg":
            for spec in _split_names([" ".join(body).replace(" ", ",")]):
                func, _, column = spec.partition(":")
                func = func.lower()
                if func not in _AGGREGATES:
                    raise SkillUsageError(f"Unknown aggregate {func!r}; use {', '.join(sorted(_AGGREGATES))}")
                if func != "count" and not column:
                    raise SkillUsageError(f"Aggregate {func} needs a column, e.g. {func}:amount")
                query.aggregates.append(("avg" if func == "mean" else func, column or None))
        elif name == "sort":
            for spec in " ".join(body).split(","):
                parts = spec.split()
                if not parts:
                    continue
                descending = len(parts) > 1 and parts[-1].lower() == "desc"
                if len(parts) > 1 and parts[-1].lower() in {"asc", "desc"}:
                    parts = parts[:-1]
                query.sort.append((" ".join(parts), descending))
        elif name == "limit":
            if len(body) != 1 or not body[0].isdigit():
                raise SkillUsageError("limit expects a single positive integer")
            query.limit = int(body[0])
            if not 1 <= query.limit <= MAX_LIMIT:
                raise SkillUsageError(f"limit must be between 1 and {MAX_LIMIT}")
        elif name == "format":
            if len(body) != 1 or body[0].lower() not in {"table", "json", "csv"}:
                raise SkillUsageError("format expects table, json or csv")
            query.output_format = body[0].lower()
    if query.group_by and not query.aggregates:
        query.aggregates = [("count", None)]
    return query


def _is_value_position(body: List[str], clause: str) -> bool:
    # In "where status == limit", the token after an operator is a value even
    # if it spells a clause keyword.
    return clause == "where" and bool(body) and body[-1].lower() in _OPERATORS


def _parse_conditions(tokens: Sequence[str]) -> List[List[Condition]]:
    groups: List[List[Condition]] = [[]]
    index = 0
    expect_condition = True
    while index < len(tokens):
        token = tokens[index]
        lowered = token.lower()
        if not expect_condition:
            if lowered == "and":
                expect_condition = True
                index += 1
                continue
            if lowered == "or":
                groups.append([])
                expect_condition = True
                index += 1
                continue
            raise SkillUsageError(f"Expected 'and' / 'or', got {token!r}")
        if index + 1 >= len(tokens):
            raise SkillUsageError(f"Incomplete condition after {token!r}")
        op = tokens[index + 1].lower()
        if op in {"isnull", "notnull"}:
            groups[-1].append(Condition(token, op))
            index += 2
        else:
            if op not in _OPERATORS:
                raise SkillUsageError(f"Unknown operator {tokens[index + 1]!r}; use {', '.join(sorted(_OPERATORS))}")
            if index + 2 >= len(tokens):
                raise SkillUsageError(f"Condition on {token!r} is missing a value")
            raw_value = tokens[index + 2]
            value: Any = [item.strip() for item in raw_value.split(",")] if op == "in" else raw_value
            groups[-1].append(Condition(token, op, value))
            index += 3
        expect_condition = False
    if expect_condition:
        raise SkillUsageError("Condition list ends with 'and'/'or'")
    return groups


def _aggregate(func: str, column: Optional[str], rows: List[Dict[str, Any]]) -> Any:
    if func == "count":
        return len(rows) if column is None else sum(1 for row in rows if row.get(column) is not None)
    values = [row.get(column) for row in rows if row.get(column) is not None]  # type: ignore[arg-type]
    if func == "distinct":
        return len({json.dumps(value, sort_keys=True, default=str) for value in values})
    if func in {"min", "max"}:
        numbers = [_as_number(value) for value in values]
        if values and all(number is not None for number in numbers):
            chosen = (min if func == "min" else max)(numbers)  # type: ignore[type-var]
            return int(chosen) if float(chosen).is_integer() else chosen  # type: ignore[arg-type]
        return (min if func == "min" else max)((str(value) for value in values), default=None)
    numbers = [number for number in (_as_number(value) for value in values) if number is not None]
    if not numbers:
        return None
    if func == "sum":
        total = math.fsum(numbers)
        return int(total) if total.is_integer() else round(total, 10)
    if func == "avg":
        return round(statistics.fmean(numbers), 10)
    if func == "median":
        return statistics.median(numbers)
    raise SkillUsageError(f"Unknown aggregate {func}")


def _agg_name(func: str, column: Optional[str]) -> str:
    return func if column is None else f"{func}_{column}"


def _sort_key(column: str) -> Callable[[Dict[str, Any]], Tuple[int, int, Any]]:
    def key(row: Dict[str, Any]) -> Tuple[int, int, Any]:
        value = row.get(column)
        if value is None:
            return (1, 0, "")
        number = _as_number(value)
        if number is not None:
            return (0, 0, number)
        return (0, 1, str(value).lower())
    return key


def execute_query(dataset: Dataset, query: Query) -> Tuple[List[Dict[str, Any]], List[str], int]:
    """Return (rows, columns, matched_before_limit)."""
    known = set(dataset.columns)
    referenced = [c.column for group in query.where for c in group] + query.group_by
    referenced += [column for _, column in query.aggregates if column]
    for column in referenced:
        if column not in known:
            raise SkillUsageError(f"Unknown column {column!r}; columns: {', '.join(dataset.columns[:40])}")
    rows = dataset.rows
    if query.where:
        rows = [row for row in rows if any(all(cond.matches(row) for cond in group) for group in query.where)]
    if query.group_by:
        buckets: Dict[Tuple[str, ...], List[Dict[str, Any]]] = {}
        exemplars: Dict[Tuple[str, ...], Dict[str, Any]] = {}
        for row in rows:
            key = tuple(json.dumps(row.get(col), sort_keys=True, default=str) for col in query.group_by)
            buckets.setdefault(key, []).append(row)
            exemplars.setdefault(key, row)
        grouped: List[Dict[str, Any]] = []
        for key, members in buckets.items():
            record = {col: exemplars[key].get(col) for col in query.group_by}
            for func, column in query.aggregates:
                record[_agg_name(func, column)] = _aggregate(func, column, members)
            grouped.append(record)
        rows = grouped
        columns = query.group_by + [_agg_name(func, column) for func, column in query.aggregates]
    elif query.aggregates:
        rows = [{_agg_name(func, column): _aggregate(func, column, rows) for func, column in query.aggregates}]
        columns = list(rows[0])
    else:
        columns = list(dataset.columns)
    for column, _ in query.sort:
        if column not in columns:
            raise SkillUsageError(f"Cannot sort by unknown column {column!r}; available: {', '.join(columns[:40])}")
    for column, descending in reversed(query.sort):
        none_rows = [row for row in rows if row.get(column) is None]
        value_rows = [row for row in rows if row.get(column) is not None]
        value_rows.sort(key=_sort_key(column), reverse=descending)
        rows = value_rows + none_rows
    if query.select:
        for column in query.select:
            if column not in columns:
                raise SkillUsageError(f"Cannot select unknown column {column!r}; available: {', '.join(columns[:40])}")
        columns = list(query.select)
        rows = [{col: row.get(col) for col in columns} for row in rows]
    matched = len(rows)
    return rows[: query.limit], columns, matched


def render_rows(rows: List[Dict[str, Any]], columns: List[str], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(rows, ensure_ascii=False, indent=2, default=str)
    if output_format == "csv":
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        return buffer.getvalue().rstrip("\n")
    return format_table(rows, columns) if rows else "(no rows)"


def _infer_type(values: List[Any]) -> str:
    kinds = set()
    for value in values:
        if isinstance(value, bool):
            kinds.add("bool")
        elif isinstance(value, int):
            kinds.add("int")
        elif isinstance(value, float):
            kinds.add("float")
        else:
            kinds.add("str")
    if not kinds:
        return "empty"
    if kinds == {"int", "float"}:
        return "float"
    return kinds.pop() if len(kinds) == 1 else "mixed(" + ",".join(sorted(kinds)) + ")"


# ── actions ─────────────────────────────────────────────────────────────────

def _schema(dataset: Dataset) -> dict:
    columns_info: List[Dict[str, Any]] = []
    for column in dataset.columns:
        values = [row.get(column) for row in dataset.rows]
        present = [value for value in values if value is not None and value != ""]
        distinct = len({json.dumps(value, sort_keys=True, default=str) for value in present[:50000]})
        columns_info.append({
            "column": column,
            "type": _infer_type(present),
            "nulls": len(values) - len(present),
            "distinct": distinct,
            "example": present[0] if present else None,
        })
    table = format_table(columns_info, ["column", "type", "nulls", "distinct", "example"])
    note = f" (first {MAX_ROWS} rows)" if dataset.truncated else ""
    output = f"{dataset.path}: {len(dataset.rows)} rows{note}, {len(dataset.columns)} columns\n{table}"
    return result("data", output, action="schema", path=dataset.path, rows=len(dataset.rows),
                  columns=columns_info, truncated=dataset.truncated)


def _stats(dataset: Dataset, column: str) -> dict:
    if column not in dataset.columns:
        raise SkillUsageError(f"Unknown column {column!r}; columns: {', '.join(dataset.columns[:40])}")
    values = [row.get(column) for row in dataset.rows]
    present = [value for value in values if value is not None and value != ""]
    numbers = [n for n in (_as_number(value) for value in present) if n is not None]
    info: Dict[str, Any] = {
        "column": column, "count": len(present), "nulls": len(values) - len(present),
        "distinct": len({json.dumps(v, sort_keys=True, default=str) for v in present}),
        "type": _infer_type(present),
    }
    if numbers and len(numbers) == len(present):
        ordered = sorted(numbers)
        quartiles = statistics.quantiles(ordered, n=4, method="inclusive") if len(ordered) > 1 else [ordered[0]] * 3
        info.update({
            "min": ordered[0], "max": ordered[-1], "sum": round(math.fsum(ordered), 10),
            "mean": round(statistics.fmean(ordered), 10), "median": statistics.median(ordered),
            "stdev": round(statistics.stdev(ordered), 10) if len(ordered) > 1 else 0.0,
            "p25": quartiles[0], "p75": quartiles[2],
        })
    counts: Dict[str, int] = {}
    for value in present:
        label = str(value)
        counts[label] = counts.get(label, 0) + 1
    info["top_values"] = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    lines = [f"{dataset.path} :: {column}"]
    for key, value in info.items():
        if key in {"column", "top_values"}:
            continue
        lines.append(f"  {key}: {value:.6g}" if isinstance(value, float) else f"  {key}: {value}")
    lines.append("  top: " + ", ".join(f"{label} ({count})" for label, count in info["top_values"]))
    return result("data", "\n".join(lines), action="stats", path=dataset.path, **info)


def _execute(ctx: NativeSkillContext, tokens: List[str]) -> dict:
    action, args = tokens[0].lower(), tokens[1:]
    json_path: Optional[str] = None
    if "--path" in args:
        position = args.index("--path")
        if position + 1 >= len(args):
            raise SkillUsageError("--path requires a value")
        json_path = args[position + 1]
        args = args[:position] + args[position + 2:]
    if not args:
        raise SkillUsageError(f"data {action} needs a file")
    dataset = load_dataset(ctx, args[0], json_path)
    if action == "schema":
        return _schema(dataset)
    if action == "head":
        count = int(args[1]) if len(args) > 1 and args[1].isdigit() else 10
        count = max(1, min(count, 100))
        rows = dataset.rows[:count]
        return result("data", f"{dataset.path}: first {len(rows)} of {len(dataset.rows)} rows\n"
                      + render_rows(rows, dataset.columns, "table"),
                      action="head", path=dataset.path, rows=rows, columns=dataset.columns)
    if action == "stats":
        if len(args) != 2:
            raise SkillUsageError("data stats <file> <column>")
        return _stats(dataset, args[1])
    query = parse_query(args[1:])
    rows, columns, matched = execute_query(dataset, query)
    body = render_rows(rows, columns, query.output_format)
    body, _ = truncate(body, 12000)
    header = f"{dataset.path}: {matched} matching row(s)" + (f", showing {len(rows)}" if matched > len(rows) else "")
    return result(
        "data", f"{header}\n{body}", action="query", path=dataset.path, columns=columns,
        rows=rows[:MAX_DATA_ROWS], row_count=len(rows), matched=matched,
        rows_truncated=len(rows) > MAX_DATA_ROWS, source_truncated=dataset.truncated,
    )


async def run(command: str, ctx: NativeSkillContext) -> dict:
    try:
        _, tokens = split_command(command)
        if not tokens or tokens[0].lower() in {"help", "--help"}:
            return result("data", _USAGE, usage=True) if tokens else failure("data", _USAGE)
        if tokens[0].lower() not in {"schema", "head", "stats", "query"}:
            return failure("data", f"Unknown data action {tokens[0]!r}.\n{_USAGE}")
        return await asyncio.to_thread(_execute, ctx, tokens)
    except (SkillUsageError, workspace.WorkspaceError) as exc:
        return failure("data", f"data: {exc}")
