"""Provenance-guarded agent loop (plan-then-execute with information-flow control).

Design references:
  * Debenedetti et al., "Defeating Prompt Injections by Design" (CaMeL), 2025:
    control flow comes only from trusted input; every value carries provenance;
    a policy checks data flow before each tool call.
  * Beurer-Kellner et al., "Design Patterns for Securing LLM Agents against
    Prompt Injections", 2025: plan-then-execute and dual-LLM patterns.

Invariants enforced here (each has a regression test in tests/test_secure_agent.py):
  I1  The planner LLM sees only the trusted user task and the tool catalogue,
      never a tool result.  Tool output therefore cannot change which tools run.
  I2  Every runtime value carries a trust label; the label of a derived value
      is the join of its inputs (UNTRUSTED dominates).
  I3  An UNTRUSTED value never reaches a ``control`` parameter (recipient,
      query sent off-host, deletion selector, ...).
  I4  Destructive steps, and writes that persist UNTRUSTED data, need an
      approval bound to the exact tool and argument values (SHA-256 digest).
  I5  Tool output is processed by an LLM only in the quarantined role, whose
      output is UNTRUSTED and which has no tools.
"""

from botboy.agent.engine import AgentRun, SecureAgent, StepRecord
from botboy.agent.plan import AgentPlan, PlanError, parse_plan
from botboy.agent.policy import Decision, Labeled, Trust
from botboy.agent.tools import ToolSpec, default_tools

__all__ = [
    "AgentPlan",
    "AgentRun",
    "Decision",
    "Labeled",
    "PlanError",
    "SecureAgent",
    "StepRecord",
    "ToolSpec",
    "Trust",
    "default_tools",
    "parse_plan",
]
