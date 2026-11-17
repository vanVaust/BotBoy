"""Planner output: strict JSON schema, parsed and validated before anything runs.

Schema (produced by the planner LLM from the trusted task only)::

    {
      "steps": [
        {"id": "s1", "tool": "memory.search", "args": {"query": {"literal": "zebra"}}},
        {"id": "s2", "tool": "llm.extract",
         "args": {"instruction": {"literal": "list the colours"}, "data": {"ref": "s1"}}}
      ],
      "answer": "Colours: {s2}"
    }

``literal`` values are trusted (they come from the planner, which saw only the
trusted task).  ``ref`` names an earlier step; its value carries that step's
label.  References may point backwards only, so the plan is a DAG executed in
order and needs no scheduler.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Mapping, Union

from botboy.agent.tools import ToolSpec

MAX_STEPS = 8
_STEP_ID = re.compile(r"^[a-z][a-z0-9_]{0,15}$")
_PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]{0,15})\}")
_MAX_ANSWER_TEMPLATE = 4_000
_MAX_LITERAL = 2_000


class PlanError(ValueError):
    pass


@dataclass(frozen=True)
class Literal:
    value: str


@dataclass(frozen=True)
class Ref:
    step_id: str


ArgSource = Union[Literal, Ref]


@dataclass(frozen=True)
class PlannedStep:
    step_id: str
    tool: str
    args: Mapping[str, ArgSource]


@dataclass(frozen=True)
class AgentPlan:
    steps: tuple[PlannedStep, ...]
    answer_template: str

    def answer_refs(self) -> list[str]:
        return _PLACEHOLDER.findall(self.answer_template)


def extract_json_object(text: str) -> str:
    """Return the first balanced top-level JSON object in ``text``.

    Models often wrap JSON in prose or code fences; a brace scanner that
    respects string literals is more reliable than fence stripping.
    """
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            ch = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
            elif ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
        start = text.find("{", start + 1)
    raise PlanError("planner output contains no JSON object")


def _parse_arg(step_id: str, name: str, raw: object, known: set[str]) -> ArgSource:
    if not isinstance(raw, dict) or len(raw) != 1:
        raise PlanError(f"{step_id}.{name}: argument must be {{\"literal\": ...}} or {{\"ref\": ...}}")
    (kind, value), = raw.items()
    if kind == "literal":
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            raise PlanError(f"{step_id}.{name}: literal must be a string or number")
        text = str(value)
        if len(text) > _MAX_LITERAL:
            raise PlanError(f"{step_id}.{name}: literal exceeds {_MAX_LITERAL} characters")
        return Literal(text)
    if kind == "ref":
        if not isinstance(value, str) or value not in known:
            raise PlanError(f"{step_id}.{name}: ref '{value}' does not name an earlier step")
        return Ref(value)
    raise PlanError(f"{step_id}.{name}: unknown argument kind '{kind}'")


def parse_plan(text: str, tools: Mapping[str, ToolSpec]) -> AgentPlan:
    try:
        data = json.loads(extract_json_object(text))
    except json.JSONDecodeError as exc:
        raise PlanError(f"planner output is not valid JSON: {exc.msg}") from exc
    if not isinstance(data, dict) or set(data) - {"steps", "answer"}:
        raise PlanError("plan must be an object with only 'steps' and 'answer'")
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise PlanError("plan needs a non-empty 'steps' list")
    if len(raw_steps) > MAX_STEPS:
        raise PlanError(f"plan has {len(raw_steps)} steps; maximum is {MAX_STEPS}")

    known: set[str] = set()
    steps: list[PlannedStep] = []
    for position, raw in enumerate(raw_steps):
        if not isinstance(raw, dict) or set(raw) != {"id", "tool", "args"}:
            raise PlanError(f"step #{position + 1} must have exactly 'id', 'tool', 'args'")
        step_id, tool_name, raw_args = raw["id"], raw["tool"], raw["args"]
        if not isinstance(step_id, str) or not _STEP_ID.match(step_id):
            raise PlanError(f"step #{position + 1}: invalid id {step_id!r}")
        if step_id in known:
            raise PlanError(f"duplicate step id '{step_id}'")
        tool = tools.get(tool_name) if isinstance(tool_name, str) else None
        if tool is None:
            raise PlanError(f"{step_id}: unknown tool {tool_name!r}")
        if not isinstance(raw_args, dict) or set(raw_args) != set(tool.params):
            raise PlanError(
                f"{step_id}: {tool.name} takes exactly {sorted(tool.params)}, got "
                f"{sorted(raw_args) if isinstance(raw_args, dict) else type(raw_args).__name__}"
            )
        args = {name: _parse_arg(step_id, name, value, known) for name, value in raw_args.items()}
        steps.append(PlannedStep(step_id, tool.name, args))
        known.add(step_id)

    answer = data.get("answer", "")
    if not isinstance(answer, str) or len(answer) > _MAX_ANSWER_TEMPLATE:
        raise PlanError("'answer' must be a string template of bounded length")
    for ref in _PLACEHOLDER.findall(answer):
        if ref not in known:
            raise PlanError(f"answer references unknown step '{ref}'")
    return AgentPlan(tuple(steps), answer)
