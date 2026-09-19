"""Tests for Mamba AI Capability Registry and Runtime Capability Awareness."""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from agents.planning_agent import PlanningAgent, _SYSTEM_PROMPT, _build_user_message
from core.brain import Brain, create_brain
from core.capabilities import (
    CapabilityDescriptor,
    CapabilityRegistry,
    CapabilityStatus,
    default_capability_registry,
)
from core.context import ExecutionContext
from core.types import ExecutionPlan, PlanStep, ResultStatus, UserRequest
from skills.analyze import AnalyzeSkill, _DEFAULT_SYSTEM_INSTRUCTION, _build_analyze_prompt
from skills.mixed import create_mixed_task_executor
from skills.types import SkillInput
from tasks.types import TaskInput


# =====================================================================
# 1. Capability Registry Core Tests
# =====================================================================

def test_default_registry_all_twelve_capabilities():
    """Verify default_capability_registry populates all 12 core Mamba capabilities."""
    registry = default_capability_registry(web_configured=True, github_configured=True)
    caps = {c.capability_id: c for c in registry.list_capabilities()}

    expected_ids = {
        "filesystem",
        "terminal",
        "desktop",
        "system",
        "screen",
        "web",
        "github",
        "memory",
        "email",
        "calendar",
        "messaging",
        "analyze",
    }
    assert expected_ids.issubset(caps.keys())

    # Verify key descriptors have required actions and limitations
    desktop = caps["desktop"]
    assert "open_url" in desktop.supported_actions
    assert any("YouTube" in lim for lim in desktop.limitations)

    email = caps["email"]
    assert "send_email" in email.supported_actions
    assert "read_email" in email.supported_actions


def test_registry_registration_and_retrieval():
    """Test custom registration, querying, and unregistration."""
    registry = CapabilityRegistry()
    desc = CapabilityDescriptor(
        capability_id="custom_tool",
        name="Custom Tool",
        description="A test tool",
        supported_actions=("do_foo", "do_bar"),
        status=CapabilityStatus.AVAILABLE,
    )
    registry.register(desc)

    assert registry.is_available("custom_tool") is True
    assert registry.get_status("custom_tool") == CapabilityStatus.AVAILABLE
    assert registry.get_supported_actions("custom_tool") == ("do_foo", "do_bar")
    assert registry.get_capability("custom_tool") == desc

    # Duplicate registration raises ValueError when overwrite=False
    with pytest.raises(ValueError, match="already registered"):
        registry.register(desc, overwrite=False)

    # Unregister
    registry.unregister("custom_tool")
    assert registry.is_available("custom_tool") is False
    assert registry.get_status("custom_tool") == CapabilityStatus.UNSUPPORTED


def test_find_capability_for_action():
    """Test locating capability by action name, capability id, or prefix."""
    registry = default_capability_registry()

    # Exact action
    cap_mail = registry.find_capability_for_action("send_email")
    assert cap_mail is not None
    assert cap_mail.capability_id == "email"

    cap_url = registry.find_capability_for_action("open_url")
    assert cap_url is not None
    assert cap_url.capability_id == "desktop"

    cap_cmd = registry.find_capability_for_action("run_command")
    assert cap_cmd is not None
    assert cap_cmd.capability_id == "terminal"

    # Capability ID match
    assert registry.find_capability_for_action("calendar").capability_id == "calendar"

    # Prefix match
    assert registry.find_capability_for_action("messaging.send").capability_id == "messaging"
    assert registry.find_capability_for_action("email_send").capability_id == "email"

    # Non-existent
    assert registry.find_capability_for_action("unknown_quantum_teleport") is None


def test_capability_status_mutation():
    """Test updating capability status and provider configuration."""
    registry = default_capability_registry()

    # Initially email is available (simulated provider)
    assert registry.is_available("email") is True

    # Mark email as NOT_CONFIGURED
    registry.set_status("email", CapabilityStatus.NOT_CONFIGURED)
    assert registry.is_available("email") is False
    assert registry.get_status("email") == CapabilityStatus.NOT_CONFIGURED

    # Re-enable email with configured provider
    registry.set_status("email", CapabilityStatus.AVAILABLE, provider="smtp", provider_configured=True)
    assert registry.is_available("email") is True
    assert registry.get_status("email") == CapabilityStatus.AVAILABLE
    assert registry.get_capability("email").provider == "smtp"

    # Setting status on non-existent capability raises KeyError
    with pytest.raises(KeyError):
        registry.set_status("nonexistent", CapabilityStatus.DISABLED)


# =====================================================================
# 2. Planner Summary and Grounded System Context Tests
# =====================================================================

def test_planner_summary_formatting():
    """Verify format_summary_for_planner produces compact actionable summaries."""
    registry = default_capability_registry()
    registry.set_status("email", CapabilityStatus.NOT_CONFIGURED)

    summary = registry.format_summary_for_planner()
    assert "- desktop (available):" in summary
    assert "- email (not configured):" in summary
    assert "no provider" in summary


def test_system_context_formatting():
    """Verify format_system_context produces identity and capability boundary guidance."""
    registry = default_capability_registry()
    sys_context = registry.format_system_context()

    # Grounding instructions
    assert "MAMBA RUNTIME CAPABILITIES & LIMITATIONS" in sys_context
    assert "operating layer between the user and digital tools" in sys_context
    assert "Never claim you are just a text model" in sys_context

    # Desktop / YouTube guidance
    assert "YouTube" in sys_context
    assert "cannot interact with in-page media controls like playing YouTube videos" in sys_context


# =====================================================================
# 3. Brain Integration & Unconfigured Capability Enforcement
# =====================================================================

def test_brain_default_capabilities_initialized():
    """Verify Brain initializes default capabilities if none provided."""
    brain = Brain(planner=MagicMock(), executor=MagicMock())
    assert brain.capabilities is not None
    assert brain.is_capability_available("filesystem") is True
    assert brain.get_capability("desktop") is not None


def test_brain_injects_capability_metadata_into_request():
    """Verify Brain.run injects capability metadata into UserRequest."""
    planner = MagicMock()
    captured_request = []

    def mock_plan(ctx: ExecutionContext):
        captured_request.append(ctx.request)
        return ExecutionPlan(steps=())

    planner.plan.side_effect = mock_plan
    brain = Brain(planner=planner, executor=MagicMock())

    brain.run("Inspect system hardware")
    assert len(captured_request) == 1
    req = captured_request[0]
    assert "capabilities" in req.metadata
    assert "capability_context" in req.metadata
    assert "desktop (available)" in req.metadata["capabilities"]


def test_brain_blocks_unconfigured_capability_with_clean_explanation():
    """Verify that attempting to execute an unconfigured capability returns a clean explanation."""
    registry = default_capability_registry()
    registry.set_status("email", CapabilityStatus.NOT_CONFIGURED)

    plan = ExecutionPlan(
        steps=(
            PlanStep(
                description="Send email update to team",
                intent="send_email",
                metadata={"to": "team@example.com", "subject": "Update", "body": "All done."},
            ),
        )
    )
    brain = Brain(
        planner=MagicMock(plan=lambda ctx: plan),
        executor=MagicMock(),
        capabilities=registry,
    )

    result = brain.run("Send an email to team@example.com")
    assert result.status == ResultStatus.FAILED
    expected_msg = "I have email capabilities, but no email provider is currently configured."
    assert expected_msg in result.error
    assert expected_msg in result.output
    assert "text model" not in result.output.lower()


def test_brain_blocks_disabled_capability_with_clean_explanation():
    """Verify that attempting to execute a disabled capability returns a clean explanation."""
    registry = default_capability_registry()
    registry.set_status("terminal", CapabilityStatus.DISABLED)

    plan = ExecutionPlan(
        steps=(
            PlanStep(
                description="Run shell command",
                intent="run_command",
                metadata={"command": "dir"},
            ),
        )
    )
    brain = Brain(
        planner=MagicMock(plan=lambda ctx: plan),
        executor=MagicMock(),
        capabilities=registry,
    )

    result = brain.run("Run dir")
    assert result.status == ResultStatus.FAILED
    assert "The terminal & shell capability is currently disabled." in result.error


def test_create_brain_factory_wires_capabilities():
    """Verify create_brain factory accepts and sets capabilities."""
    registry = CapabilityRegistry()
    b = create_brain(
        planner=MagicMock(),
        executor=MagicMock(),
        capabilities=registry,
    )
    assert b.capabilities is registry


# =====================================================================
# 4. PlanningAgent Grounding & Response Formatting Constraint Tests
# =====================================================================

def test_planning_agent_system_prompt_rules():
    """Verify _SYSTEM_PROMPT instructs that format instructions are response constraints, not plan steps."""
    assert "formatting constraints for the final response, NOT separate operational plan steps" in _SYSTEM_PROMPT
    assert "Do not invent capabilities or actions that do not exist or are unsupported" in _SYSTEM_PROMPT
    assert "opening a YouTube URL via desktop \"open_url\" is supported, but in-page video playback" in _SYSTEM_PROMPT


def test_planning_agent_build_user_message_includes_capabilities():
    """Verify _build_user_message injects available capabilities into planner message."""
    registry = default_capability_registry()
    agent_input = MagicMock()
    ctx = MagicMock()
    ctx.request.goal = "Open YouTube video"
    ctx.request.metadata = {}
    ctx.observations = []
    agent_input.context = ctx

    user_msg = _build_user_message(agent_input, capabilities=registry)
    assert "Goal: Open YouTube video" in user_msg
    assert "Available capabilities:" in user_msg
    assert "desktop (available)" in user_msg


# =====================================================================
# 5. AnalyzeSkill Prompt Enhancement & Formatting Constraints Tests
# =====================================================================

def test_analyze_skill_default_system_instruction_grounding():
    """Verify AnalyzeSkill system instruction grounds Mamba as operating layer."""
    assert "personal AI operating layer" in _DEFAULT_SYSTEM_INSTRUCTION
    assert "Never claim to be a generic text-only AI" in _DEFAULT_SYSTEM_INSTRUCTION


def test_analyze_skill_build_prompt_detects_formatting_constraints():
    """Verify _build_analyze_prompt detects formatting constraints from user goal."""
    skill_input = SkillInput(
        task_input=TaskInput(
            step_id="s-1",
            execution_id="e-1",
            description="Can you play YouTube videos? Give me a short answer using bullet points.",
            intent="analyze",
            step_metadata={},
            goal="Can you play YouTube videos? Give me a short answer using bullet points.",
        ),
        context=ExecutionContext.from_request(
            UserRequest(
                goal="Can you play YouTube videos? Give me a short answer using bullet points.",
                metadata={"capability_context": "Desktop: open_url supported. In-page video playback unsupported."},
            )
        ),
    )

    prompt = _build_analyze_prompt(skill_input)
    assert "Mamba Runtime Capability Context:" in prompt
    assert "Formatting Constraints:" in prompt
    assert "- Present your response using clean bullet points." in prompt
    assert "- Keep the answer concise and direct; avoid unnecessary filler." in prompt


def test_analyze_skill_build_prompt_detects_single_sentence():
    """Verify _build_analyze_prompt detects single sentence constraint."""
    req = UserRequest(goal="Explain Mamba capabilities in one sentence.")
    skill_input = SkillInput(
        task_input=TaskInput(
            step_id="s-2",
            execution_id="e-2",
            description="Explain Mamba capabilities in one sentence.",
            intent="explain",
            step_metadata={},
            goal="Explain Mamba capabilities in one sentence.",
        ),
        context=ExecutionContext.from_request(req),
    )

    prompt = _build_analyze_prompt(skill_input)
    assert "Formatting Constraints:" in prompt
    assert "- Deliver the response in a single sentence." in prompt


def test_analyze_skill_build_prompt_includes_prior_turn_and_context():
    """Verify _build_analyze_prompt includes prior conversation turn, active entities, and retrieved memory."""
    req = UserRequest(
        goal="What did you find?",
        metadata={
            "prior_turn": {
                "goal": "Scan project for errors",
                "output": "Found 2 syntax warnings in test_app.py",
            },
            "active_entities": {
                "file": "test_app.py",
                "repository": "mamba",
            },
            "retrieved_memories": [
                "User prefers concise explanations",
            ],
        },
    )
    skill_input = SkillInput(
        task_input=TaskInput(
            step_id="s-3",
            execution_id="e-3",
            description="Answer user query about findings",
            intent="respond",
            step_metadata={},
            goal="What did you find?",
        ),
        context=ExecutionContext.from_request(req),
    )

    prompt = _build_analyze_prompt(skill_input)
    assert "Prior conversation turn:" in prompt
    assert "User asked: Scan project for errors" in prompt
    assert "Found 2 syntax warnings in test_app.py" in prompt
    assert "Active context entities:" in prompt
    assert "- file: test_app.py" in prompt
    assert "Relevant memory: User prefers concise explanations" in prompt

