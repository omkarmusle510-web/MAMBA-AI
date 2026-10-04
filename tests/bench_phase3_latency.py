"""Phase 3 deterministic latency harness (NOT a pytest test — run explicitly).

Usage:
    MAMBA_TIMING=1 .venv/Scripts/python.exe tests/bench_phase3_latency.py

It isolates the ORCHESTRATION overhead that Phase 3 targets (project
discovery, pre-plan memory retrieval, the planning model call) from the
provider/tool latency that is environment dependent. Provider + model
loading are faked with representative fixed sleeps so the numbers are
reproducible; one REAL discover_project() measurement is also printed so
the emulated discovery cost can be sanity-checked against this machine.

Captures the per-span latency_ms the instrumentation emits, so a
"before/after" comparison across Task 4 (discovery gate), Task 5 (fast
path) and Task 6 (memory gating) is direct and honest.
"""
from __future__ import annotations

import statistics
import time

import core.timing as timing
from core.brain import Brain
from core.context import ExecutionContext
from core.types import ExecutionPlan, PlanStep, ResultStatus
from tasks.executor import TaskExecutor

# Representative fixed costs (ms) for the emulated collaborators.
_DISCOVERY_MS = 480.0     # measured real discover_project() cost on this machine (~479ms)
_PLANNING_MS = 300.0      # one planning model call
_ANSWER_MS = 250.0        # one answer/execute model call in the respond handler
_MEMORY_RETRIEVE_MS = 10.0
_MEMORY_WRITE_MS = 5.0

REQUESTS = [
    ("simple conversation", "hello there"),
    ("arithmetic", "what is 15 * 28?"),
    ("simple explanation", "explain recursion"),
    ("memory recall", "what do you remember about my favorite color?"),
    ("memory write", "remember that my favorite color is blue"),
    ("web search", "search the web for python 3.12 release date"),
    ("project question", "what's the architecture of this project?"),
    ("coding analysis", "find the bug in my code"),
    ("tool execution", "list the files in the current directory"),
    ("browser request", "open example.com in the browser"),
]


class _StubProjectContext:
    name = "proj"

    def format_summary(self) -> str:
        return "stub project context"


class _SleepyPlanner:
    """Emulates a single planning model call at a fixed cost."""

    def __init__(self, plan: ExecutionPlan, ms: float) -> None:
        self._plan = plan
        self._ms = ms

    def plan(self, context: ExecutionContext) -> ExecutionPlan:
        time.sleep(self._ms / 1000.0)
        return self._plan


class _RespondHandler:
    """Emulates the answer/execute model call for a respond step."""

    def __init__(self, ms: float) -> None:
        self._ms = ms

    def run(self, task_input, context):
        from tasks.types import TaskOutput

        time.sleep(self._ms / 1000.0)
        return TaskOutput(content="ok", success=True, metadata={"intent": task_input.intent})


class _FakeMemory:
    def __init__(self, r_ms: float, w_ms: float) -> None:
        self._r = r_ms
        self._w = w_ms

    def retrieve(self, *a, **k):
        time.sleep(self._r / 1000.0)
        return None

    def remember(self, *a, **k):
        time.sleep(self._w / 1000.0)

    def close(self) -> None:
        pass


def _build_brain() -> Brain:
    import core.project as project_mod

    def _fake_discover(*a, **k):
        time.sleep(_DISCOVERY_MS / 1000.0)
        return _StubProjectContext()

    project_mod.discover_project = _fake_discover  # bench runs in-process, not under pytest
    plan = ExecutionPlan(steps=(PlanStep(description="Respond", intent="respond"),))
    executor = TaskExecutor(handlers={"respond": _RespondHandler(_ANSWER_MS)})
    return Brain(
        planner=_SleepyPlanner(plan, _PLANNING_MS),
        executor=executor,
        memory=_FakeMemory(_MEMORY_RETRIEVE_MS, _MEMORY_WRITE_MS),
    )


def _measure_real_discovery() -> float:
    """One real discover_project() timing in this environment (sanity check)."""
    from core.project import discover_project

    t = time.perf_counter()
    discover_project()
    return (time.perf_counter() - t) * 1000.0


def run_class(brain: Brain, goal: str, repeats: int) -> dict:
    medians: dict[str, float] = {}
    samples: dict[str, list[float]] = {}
    totals: list[float] = []
    for _ in range(repeats):
        t = time.perf_counter()
        result = brain.run(goal)
        wall = (time.perf_counter() - t) * 1000.0
        totals.append(wall)
        for key, val in (result.metadata.get("latency_ms") or {}).items():
            samples.setdefault(key, []).append(val)
    for key, vals in samples.items():
        medians[key] = statistics.median(vals)
    medians["wall_total"] = statistics.median(totals)
    medians["completed"] = result.status == ResultStatus.COMPLETED
    return medians


def main() -> None:
    timing.set_enabled(True)
    try:
        real_disc = _measure_real_discovery()
    except Exception as exc:  # noqa: BLE001 - bench is best-effort
        real_disc = float("nan")
        print(f"(real discovery unavailable: {exc})")

    print(f"REAL discover_project() in this environment: {real_disc:6.1f} ms "
          f"(bench emulates {_DISCOVERY_MS:.0f} ms)\n")

    brain = _build_brain()
    order = ["wall_total", "project_discovery", "memory_retrieve",
             "planning", "execution", "verification", "memory_write",
             "request_total", "intake", "context_assembly"]
    print(f"{'request class':>20} | " + " | ".join(f"{k[:10]:>10}" for k in order))
    print("-" * (22 + 13 * len(order)))
    for label, goal in REQUESTS:
        m = run_class(brain, goal, repeats=3)
        row = " | ".join(f"{(m.get(k, 0.0)):>10.1f}" for k in order)
        flag = "" if m["completed"] else "  (not completed)"
        print(f"{label:>20} | {row}{flag}")


if __name__ == "__main__":
    main()
