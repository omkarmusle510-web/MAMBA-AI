"""Mamba runnable entrypoint."""

from __future__ import annotations

import os
import sys

from agents.planner import AgentPlanner
from agents.planning_agent import PlanningAgent
from core.brain import Brain
from core.types import ExecutionResult, ResultStatus
from memory import InMemoryStore
from models import DefaultModelRouter, NVIDIAModelProvider
from skills import create_mixed_task_executor


def create_brain() -> Brain:
    """Compose and wire the Mamba runtime components."""
    provider = NVIDIAModelProvider()
    router = DefaultModelRouter([provider])
    planning_agent = PlanningAgent(router=router)
    planner = AgentPlanner(handler=planning_agent)
    executor = create_mixed_task_executor()
    memory = InMemoryStore()

    return Brain(
        planner=planner,
        executor=executor,
        memory=memory,
        model_router=router,
    )


def _display_result(result: ExecutionResult) -> None:
    """Display execution result and observations."""
    print(f"\n[Status: {result.status.value}]")
    if result.status == ResultStatus.COMPLETED:
        if result.output:
            print(result.output.strip())
        elif result.observations:
            for obs in result.observations:
                print(obs.content.strip())
    else:
        if result.error:
            print(f"Error: {result.error}")
        elif result.output:
            print(result.output.strip())
    print()


def main() -> None:
    """Run interactive or one-shot command line interface."""
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        brain = create_brain()
    except Exception as exc:
        print(f"Failed to initialize Mamba: {exc}", file=sys.stderr)
        sys.exit(1)

    # One-shot mode if arguments provided
    if len(sys.argv) > 1:
        request_text = " ".join(sys.argv[1:]).strip()
        result = brain.run(request_text)
        _display_result(result)
        return

    # Interactive input loop
    print("Mamba AI (type 'exit' or 'quit' to quit)\n")
    while True:
        try:
            user_input = input("mamba> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "q"):
            break

        result = brain.run(user_input)
        _display_result(result)


if __name__ == "__main__":
    main()

