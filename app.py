"""Mamba runnable entrypoint."""

from __future__ import annotations

import os
import sys

from agents.planner import AgentPlanner
from agents.planning_agent import PlanningAgent
from core.brain import Brain
from core.types import ExecutionResult, ResultStatus
from memory import InMemoryStore
from models import DefaultModelRouter, GroqModelProvider, NVIDIAModelProvider
from models.errors import ModelProviderError
from skills import create_mixed_task_executor

try:
    import dotenv

    dotenv.load_dotenv()
except ImportError:
    pass

_DEFAULT_VISION_MODEL = "meta/llama-3.2-11b-vision-instruct"


def create_brain() -> Brain:
    """Compose and wire the Mamba runtime components."""
    providers = []

    # NVIDIA text provider — optional; skip if credentials are missing.
    try:
        text_provider = NVIDIAModelProvider()
        providers.append(text_provider)
    except ModelProviderError:
        pass

    # NVIDIA vision provider — optional; only when text provider exists.
    if providers:
        vision_model = os.environ.get("NVIDIA_VISION_MODEL", _DEFAULT_VISION_MODEL).strip()
        if vision_model:
            try:
                providers.append(
                    NVIDIAModelProvider(
                        model=vision_model,
                        supports_multimodal=True,
                    )
                )
            except ModelProviderError:
                pass

    # Groq text provider — optional; skip if credentials are missing.
    try:
        providers.append(GroqModelProvider())
    except ModelProviderError:
        pass

    if not providers:
        raise RuntimeError(
            "No model providers available. Set NVIDIA_API_KEY or GROQ_API_KEY "
            "in your environment or .env file."
        )

    router = DefaultModelRouter(providers)
    planning_agent = PlanningAgent(router=router)
    planner = AgentPlanner(handler=planning_agent)
    executor = create_mixed_task_executor(model_router=router)
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

