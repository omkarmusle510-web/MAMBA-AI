"""Shared, conservative simple-request classifier for Phase 3.

Used by BOTH the project-discovery relevance gate and the simple-request
fast path, so Parts 2/3/6/7 share one signal instead of two brains. There
is no LLM call and no second planner here: it only inspects the request
text and existing context, and it is deliberately conservative — when a
request is not confidently simple it reports itself as NOT eligible, so
the normal authoritative path (with project discovery and full planning)
runs instead.

Safety rule (Spec Part 2): when uncertain, retaining project context is
safer than incorrectly skipping it, so every "irrelevant"/"simple" check
defaults to the conservative answer.
"""
from __future__ import annotations

import ast
import operator
import re

from core.types import ExecutionPlan, PlanStep

# --- Confidently-generic request shapes -------------------------------------

# Pure arithmetic asked as "what is <num expression>?".
_ARITHMETIC = re.compile(r"^\s*(what\s+is|calculate|compute)\s+[-+/*%().\d\s]+\??\s*$", re.I)
# Time / date / day queries never need project context.
_TIME_QUERY = re.compile(r"\b(what|tell me)\b.{0,12}\b(time|date|day of the week)\b", re.I)
# Explicit memory writes and recalls.
_MEMORY_WRITE = re.compile(r"^\s*(remember|note that|don't forget|keep in mind)\b", re.I)
_MEMORY_RECALL = re.compile(
    r"\b(what do you (remember|recall)|do you remember|from (your )?memory)\b", re.I
)
# Bare greetings / small talk.
_GREETING = re.compile(
    r"^\s*(hi|hello|hey|thanks|thank you|goodbye|bye|good morning|good evening)[\s!.]*$",
    re.I,
)

# --- Project / code tie ------------------------------------------------------
# Presence of any of these means the request may need project context, so we
# retain discovery (conservative).
_PROJECT_TOKENS = re.compile(
    r"\b(project|repo|repository|code|source|file|directory|folder|function|class|"
    r"method|bug|error|exception|traceback|stack trace|architecture|build|compile|"
    r"test|pytest|module|package|dependency|git|commit|branch|merge|refactor|"
    r"this (?:code|file|project|repo)|my (?:code|file|project|repo))\b",
    re.I,
)


def is_arithmetic(goal: str) -> bool:
    return bool(_ARITHMETIC.match(goal.strip()))


def is_time_query(goal: str) -> bool:
    return bool(_TIME_QUERY.search(goal))


def is_memory_write(goal: str) -> bool:
    return bool(_MEMORY_WRITE.match(goal.strip()))


def is_memory_recall(goal: str) -> bool:
    return bool(_MEMORY_RECALL.search(goal))


def is_greeting(goal: str) -> bool:
    return bool(_GREETING.match(goal.strip()))


def has_project_signal(goal: str) -> bool:
    return bool(_PROJECT_TOKENS.search(goal))


def is_clearly_project_irrelevant(
    goal: str, *, has_active_repository: bool, metadata: dict
) -> bool:
    """True only for confidently generic requests; False on any uncertainty.

    Retaining project discovery is the safe default, so anything that is not
    a clear arithmetic / time / memory-write / greeting request — or that has
    any project/code signal, an active repository, or explicit project
    metadata — keeps discovery.
    """
    if has_active_repository:
        return False
    if metadata.get("project"):
        return False
    if has_project_signal(goal):
        return False
    if is_arithmetic(goal) or is_time_query(goal) or is_memory_write(goal) or is_greeting(goal):
        return True
    # Uncertain -> retain discovery (safe default).
    return False


# --- Deterministic arithmetic (no eval, no model call) -----------------------

# Intentionally excludes Pow: exponentiation is unbounded and could hang the
# worker (e.g. "9 ** 999999"). Only additive/multiplicative arithmetic is
# resolved here; anything more exotic falls through to the normal path.
_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError("unsupported arithmetic expression")  # names/calls/attrs rejected


def evaluate_arithmetic(goal: str) -> str | None:
    """Safely resolve "what is <arith>?" to a text answer, or None if not pure math."""
    if not is_arithmetic(goal):
        return None
    expr = re.sub(r"^\s*(?:what\s+is|calculate|compute)\s+", "", goal, flags=re.I)
    expr = expr.strip().rstrip("?").strip()
    if len(expr) > 40:  # bounded, never a huge computation
        return None
    try:
        value = _eval_node(ast.parse(expr, mode="eval"))
    except Exception:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f"{expr} = {value}"


# --- Narrow simple-request fast path -----------------------------------------
#
# The fast path is deliberately restricted to DETERMINISTIC arithmetic, which
# needs no planning call, no model call and no side effects. It is NOT a second
# brain: a general question/explanation is intentionally left on the full
# authoritative path (the planner may route it to tools), and every other
# request kind returns None. Regressions in the planner-driven tests made a
# broader "respond" heuristic unsafe, so it was rejected on measurement.


def build_fast_plan(user_request) -> ExecutionPlan | None:
    """Return a single deterministic step for pure arithmetic, else None.

    Anything other than a confidently pure arithmetic expression returns None,
    so it continues on the full authoritative path (discovery gate, planning,
    permissions, verification, cancellation all intact). The arithmetic step
    is still executed through _execute_step, so no safety boundary is skipped.
    """
    answer = evaluate_arithmetic((user_request.goal or "").strip())
    if answer is None:
        return None
    step = PlanStep(
        description=f"Answer: {user_request.goal.strip()}",
        intent="calculate",
        metadata={"fast_path": True, "resolved_text": answer},
    )
    return ExecutionPlan(steps=(step,))
