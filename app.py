"""Mamba runnable entrypoint."""

from __future__ import annotations

import os
import sys

from agents.planner import AgentPlanner
from agents.planning_agent import PlanningAgent
from core import CapabilityRegistry, default_capability_registry
from core.brain import Brain
from core.runtime import MambaRuntime
from core.types import ExecutionResult, ResultStatus
from memory import MemoryStore, PersistentStore
from models import DefaultModelRouter, GeminiModelProvider, GroqModelProvider, NVIDIAModelProvider
from models.errors import ModelProviderError
from skills import create_mixed_task_executor

try:
    import dotenv

    dotenv.load_dotenv()
except ImportError:
    pass

_DEFAULT_VISION_MODEL = "meta/llama-3.2-11b-vision-instruct"


def create_runtime(
    *,
    memory: MemoryStore | None = None,
    capabilities: CapabilityRegistry | None = None,
) -> MambaRuntime:
    """Compose and wire the Mamba runtime components.

    Returns the canonical MambaRuntime boundary wrapping a fully
    configured Brain instance.
    """
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

    # Gemini provider — optional; skip if credentials are missing.
    try:
        providers.append(GeminiModelProvider())
    except ModelProviderError:
        pass

    if not providers:
        raise RuntimeError(
            "No model providers available. Set NVIDIA_API_KEY, GROQ_API_KEY, "
            "or GEMINI_API_KEY in your environment or .env file."
        )

    if capabilities is None:
        capabilities = default_capability_registry()

    router = DefaultModelRouter(providers)
    planning_agent = PlanningAgent(router=router, capabilities=capabilities)
    planner = AgentPlanner(handler=planning_agent)
    if memory is None:
        memory = PersistentStore()
    executor = create_mixed_task_executor(model_router=router, memory_store=memory)

    brain = Brain(
        planner=planner,
        executor=executor,
        memory=memory,
        model_router=router,
        capabilities=capabilities,
    )

    return MambaRuntime(brain=brain)


def create_brain(
    *,
    memory: MemoryStore | None = None,
    capabilities: CapabilityRegistry | None = None,
) -> Brain:
    """Backwards-compatible convenience: returns Brain directly."""
    return create_runtime(memory=memory, capabilities=capabilities).brain


def _display_result(result: ExecutionResult) -> None:
    """Display execution result to user with clean formatting."""
    # Detect pending approval prompt — do not show as "failed"
    is_approval = any(
        obs.metadata.get("awaiting_approval") for obs in result.observations
    )

    if is_approval:
        print("\n[Confirmation Required]")
        if result.output:
            print(result.output.strip())
        print()
        return

    print(f"\n[Status: {result.status.value}]")
    if result.status == ResultStatus.COMPLETED:
        if result.output:
            print(result.output.strip())
        elif result.observations:
            for obs in result.observations:
                print(obs.content.strip())
    else:
        if result.output:
            print(result.output.strip())
        elif result.error:
            print(f"Error: {result.error}")
    print()


def _show_progress(milestone: str) -> None:
    """Print a lightweight progress milestone inline."""
    print(f"  ⟩ {milestone}", flush=True)


def main() -> None:
    """Run interactive or one-shot command line interface."""
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        runtime = create_runtime()
    except Exception as exc:
        print(f"Failed to initialize Mamba: {exc}", file=sys.stderr)
        sys.exit(1)

    # Voice interface mode
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("--voice", "-v", "voice"):
        from voice import VoiceInterface

        try:
            voice_app = VoiceInterface(runtime)
            voice_app.voice_loop()
        except Exception as exc:
            print(f"Voice interface error: {exc}", file=sys.stderr)
        return

    # One-shot mode if arguments provided
    if len(sys.argv) > 1:
        request_text = " ".join(sys.argv[1:]).strip()
        result = runtime.run(request_text, on_progress=_show_progress)
        _display_result(result)
        return

    # Interactive input loop
    print("Mamba AI (type 'voice' for voice mode, 'exit' or 'quit' to quit)\n")
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
        if user_input.lower() in ("voice", "--voice"):
            from voice import VoiceInterface

            try:
                voice_app = VoiceInterface(runtime)
                voice_app.voice_loop()
            except Exception as exc:
                print(f"Voice interface error: {exc}", file=sys.stderr)
            continue

        result = runtime.run(user_input, on_progress=_show_progress)
        _display_result(result)


if __name__ == "__main__":
    main()