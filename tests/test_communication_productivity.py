"""Tests for Mamba AI's real-world communication and productivity capabilities:
Email, Calendar, and Messaging.
"""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from core.brain import Brain
from core.context import ExecutionContext
from core.types import ExecutionPlan, PlanStep, ResultStatus, UserRequest
from permissions.policy import DefaultPermissionPolicy
from permissions.types import PermissionDecision, RiskLevel
from skills.calendar import CalendarSkill, CalendarTaskHandler
from skills.email import EmailSkill, EmailTaskHandler
from skills.messaging import MessagingSkill, MessagingTaskHandler
from skills.mixed import create_mixed_task_executor
from tasks.executor import TaskExecutor
from tools.calendar.errors import CalendarEventNotFoundError
from tools.calendar.providers import CalendarProvider, SimulatedCalendarProvider
from tools.calendar.types import CalendarAction, CalendarEvent
from tools.email.errors import EmailNotFoundError
from tools.email.providers import EmailProvider, SimulatedEmailProvider
from tools.email.types import EmailAction, EmailMessage
from tools.messaging.errors import ConversationNotFoundError
from tools.messaging.providers import MessagingProvider, SimulatedMessagingProvider
from tools.messaging.types import MessagingAction
from verification.verifier import DefaultVerifier


# =====================================================================
# 1. Email Capability Tests
# =====================================================================

def test_email_search_and_read_automatic_execution():
    """Verify search_emails and read_email are LOW risk and execute automatically."""
    handler = EmailTaskHandler()
    meta_search = handler.get_metadata(MagicMock(step_metadata={"action": "search_emails"}, intent="search_emails"))
    assert meta_search["risk_level"] == RiskLevel.LOW
    assert meta_search["destructive"] is False
    assert meta_search["externally_visible"] is False

    meta_read = handler.get_metadata(MagicMock(step_metadata={"action": "read_email"}, intent="read_email"))
    assert meta_read["risk_level"] == RiskLevel.LOW

    executor = create_mixed_task_executor()
    plan = ExecutionPlan(steps=(
        PlanStep(description="search nvidia email", intent="search_emails", metadata={"query": "NVIDIA"}),
        PlanStep(description="read nvidia email", intent="read_email", metadata={"email_id": "em-101"}),
    ))
    brain = Brain(planner=MagicMock(plan=lambda ctx: plan), executor=executor)

    res = brain.run("Find the email from NVIDIA and read it")
    assert res.status == ResultStatus.COMPLETED
    assert "NVIDIA AI Developer Day" in res.output
    assert brain._active_entities.get("email") == "em-101"


def test_email_draft_is_automatic():
    """Verify draft_email is LOW risk and executes automatically."""
    handler = EmailTaskHandler()
    meta_draft = handler.get_metadata(MagicMock(step_metadata={"action": "draft_email"}, intent="draft_email"))
    assert meta_draft["risk_level"] == RiskLevel.LOW

    executor = create_mixed_task_executor()
    plan = ExecutionPlan(steps=(
        PlanStep(
            description="draft reply",
            intent="draft_email",
            metadata={"to": "events@nvidia.com", "subject": "Re: Invitation", "body": "I will attend."},
        ),
    ))
    brain = Brain(planner=MagicMock(plan=lambda ctx: plan), executor=executor)

    res = brain.run("Draft a reply saying I'll attend")
    assert res.status == ResultStatus.COMPLETED
    assert "Created draft" in res.output
    assert brain._pending_approval is None


def test_email_send_requires_approval_and_verifies_receipt():
    """Verify send_email is HIGH risk, requires approval, and verifies provider receipt."""
    handler = EmailTaskHandler()
    meta_send = handler.get_metadata(MagicMock(step_metadata={"action": "send_email"}, intent="send_email"))
    assert meta_send["risk_level"] == RiskLevel.HIGH
    assert meta_send["externally_visible"] is True
    assert meta_send["user_sensitive"] is True

    executor = create_mixed_task_executor()
    plan = ExecutionPlan(steps=(
        PlanStep(
            description="send email to NVIDIA",
            intent="send_email",
            metadata={"to": "events@nvidia.com", "subject": "Attending", "body": "Confirmed."},
        ),
    ))
    brain = Brain(planner=MagicMock(plan=lambda ctx: plan), executor=executor)

    # Turn 1: Should pause for approval
    res1 = brain.run("Send the email to events@nvidia.com")
    assert brain._pending_approval is not None
    assert "Action requires user confirmation" in res1.output

    # Turn 2: Natural confirmation resumes, sends, and verifies
    res2 = brain.run("Yes, please send it.")
    assert res2.status == ResultStatus.COMPLETED
    assert "Email sent successfully to events@nvidia.com" in res2.output
    assert brain._pending_approval is None


# =====================================================================
# 2. Calendar Capability Tests
# =====================================================================

def test_calendar_list_and_search_automatic_execution():
    """Verify list_events and search_events are LOW risk and execute automatically."""
    handler = CalendarTaskHandler()
    meta_list = handler.get_metadata(MagicMock(step_metadata={"action": "list_events"}, intent="list_events"))
    assert meta_list["risk_level"] == RiskLevel.LOW

    executor = create_mixed_task_executor()
    plan = ExecutionPlan(steps=(
        PlanStep(description="search sync meeting", intent="search_events", metadata={"query": "Rahul"}),
    ))
    brain = Brain(planner=MagicMock(plan=lambda ctx: plan), executor=executor)

    res = brain.run("When is my meeting with Rahul?")
    assert res.status == ResultStatus.COMPLETED
    assert "Project Mamba Sync with Rahul" in res.output
    assert brain._active_entities.get("meeting") == "cal-201"


def test_calendar_check_conflicts():
    """Verify check_conflicts detects overlapping events."""
    provider = SimulatedCalendarProvider()
    conflicts = provider.check_conflicts(
        start_time="2026-09-14T16:30:00",
        end_time="2026-09-14T17:30:00",
    )
    assert len(conflicts) == 1
    assert conflicts[0].title == "Project Mamba Sync with Rahul"


def test_calendar_create_and_modify_require_approval():
    """Verify create_event and modify_event require confirmation as HIGH risk."""
    handler = CalendarTaskHandler()
    meta_create = handler.get_metadata(MagicMock(step_metadata={"action": "create_event"}, intent="create_event"))
    assert meta_create["risk_level"] == RiskLevel.HIGH
    assert meta_create["externally_visible"] is True

    executor = create_mixed_task_executor()
    plan = ExecutionPlan(steps=(
        PlanStep(
            description="schedule sync",
            intent="create_event",
            metadata={"title": "Coffee Sync", "start_time": "2026-09-15T11:00:00", "end_time": "2026-09-15T11:30:00"},
        ),
    ))
    brain = Brain(planner=MagicMock(plan=lambda ctx: plan), executor=executor)

    # Turn 1: Pauses for approval
    res1 = brain.run("Schedule a coffee sync on Sept 15 at 11 AM")
    assert brain._pending_approval is not None

    # Turn 2: User approves
    res2 = brain.run("Yes, go ahead.")
    assert res2.status == ResultStatus.COMPLETED
    assert "Created calendar event 'Coffee Sync'" in res2.output


def test_calendar_cancel_event_destructive_requires_approval():
    """Verify cancel_event is HIGH risk & destructive and requires confirmation."""
    handler = CalendarTaskHandler()
    meta_cancel = handler.get_metadata(MagicMock(step_metadata={"action": "cancel_event"}, intent="cancel_event"))
    assert meta_cancel["risk_level"] == RiskLevel.HIGH
    assert meta_cancel["destructive"] is True
    assert meta_cancel["irreversible"] is True

    executor = create_mixed_task_executor()
    plan = ExecutionPlan(steps=(
        PlanStep(description="cancel rahul meeting", intent="cancel_event", metadata={"event_id": "cal-201"}),
    ))
    brain = Brain(planner=MagicMock(plan=lambda ctx: plan), executor=executor)

    res1 = brain.run("Cancel that meeting")
    assert brain._pending_approval is not None

    res2 = brain.run("Confirmed.")
    assert res2.status == ResultStatus.COMPLETED
    assert "Cancelled calendar event" in res2.output


# =====================================================================
# 3. Messaging Capability Tests
# =====================================================================

def test_messaging_read_and_search_automatic():
    """Verify read_messages and search_conversations execute automatically."""
    handler = MessagingTaskHandler()
    meta_read = handler.get_metadata(MagicMock(step_metadata={"action": "read_messages"}, intent="read_messages"))
    assert meta_read["risk_level"] == RiskLevel.LOW

    executor = create_mixed_task_executor()
    plan = ExecutionPlan(steps=(
        PlanStep(description="read rahul chat", intent="read_messages", metadata={"conversation_id": "conv-301"}),
    ))
    brain = Brain(planner=MagicMock(plan=lambda ctx: plan), executor=executor)

    res = brain.run("Read messages from Rahul")
    assert res.status == ResultStatus.COMPLETED
    assert "Rahul" in res.output
    assert brain._active_entities.get("conversation") == "conv-301"


def test_messaging_send_requires_approval_and_verifies_receipt():
    """Verify send_message requires approval and verifies transmission receipt."""
    handler = MessagingTaskHandler()
    meta_send = handler.get_metadata(MagicMock(step_metadata={"action": "send_message"}, intent="send_message"))
    assert meta_send["risk_level"] == RiskLevel.HIGH
    assert meta_send["externally_visible"] is True
    assert meta_send["user_sensitive"] is True

    executor = create_mixed_task_executor()
    plan = ExecutionPlan(steps=(
        PlanStep(
            description="send message to Rahul",
            intent="send_message",
            metadata={"recipient": "Rahul", "content": "I'll be 10 minutes late."},
        ),
    ))
    brain = Brain(planner=MagicMock(plan=lambda ctx: plan), executor=executor)

    # Turn 1: Pauses for approval
    res1 = brain.run("Message Rahul that I'll be 10 minutes late")
    assert brain._pending_approval is not None

    # Turn 2: User approves
    res2 = brain.run("Yes, send it.")
    assert res2.status == ResultStatus.COMPLETED
    assert "Message sent to Rahul Sharma" in res2.output
    assert "I'll be 10 minutes late" in res2.output


# =====================================================================
# 4. Conversational Referent Context Tests
# =====================================================================

def test_referent_resolution_across_turns_for_email_calendar_messaging():
    """Verify 'that email', 'that meeting', 'that conversation' resolve across turns."""
    plan1 = ExecutionPlan(steps=(
        PlanStep(description="find email", intent="search_emails", metadata={"query": "NVIDIA"}),
    ))
    plan2 = ExecutionPlan(steps=(
        PlanStep(description="summarize email", intent="summarize_email", metadata={"email_id": "em-101"}),
    ))

    captured_goals = []
    class ReferentPlanner:
        def __init__(self):
            self.plans = [plan1, plan2]
            self.idx = 0

        def plan(self, context):
            captured_goals.append(context.request.goal)
            p = self.plans[self.idx]
            self.idx = min(self.idx + 1, len(self.plans) - 1)
            return p

    executor = create_mixed_task_executor()
    brain = Brain(planner=ReferentPlanner(), executor=executor)

    # Turn 1: Search email
    res1 = brain.run("Find the email from NVIDIA")
    assert res1.status == ResultStatus.COMPLETED
    assert brain._active_entities.get("email") == "em-101"

    # Turn 2: "summarize that email"
    res2 = brain.run("summarize that email")
    assert res2.status == ResultStatus.COMPLETED
    assert "em-101" in captured_goals[1]


# =====================================================================
# 5. Outcome Verification Tests
# =====================================================================

def test_verifier_provider_verified_predicate():
    """Verify DefaultVerifier verifies provider_verified flag on receipt."""
    verifier = DefaultVerifier()
    from verification.types import VerificationRequest, VerificationStatus

    # Succeeded case: verified = True
    req_success = VerificationRequest(
        expected={"provider_verified": True},
        actual={"message_id": "msg-123", "status": "sent", "verified": True},
        metadata={"verified": True},
    )
    res_success = verifier.verify(req_success)
    assert res_success.status == VerificationStatus.VERIFIED

    # Failed case: missing or unverified
    req_fail = VerificationRequest(
        expected={"provider_verified": True},
        actual={"error": "Rate limit exceeded", "verified": False},
        metadata={"verified": False},
    )
    res_fail = verifier.verify(req_fail)
    assert res_fail.status == VerificationStatus.FAILED


# =====================================================================
# 6. Cross-Capability Chaining Workflow Test
# =====================================================================

def test_cross_capability_chaining_email_calendar_messaging():
    """Verify end-to-end chaining: Email -> Calendar -> Messaging with intermediate approval.

    Goal: "Find the email about tomorrow's meeting, check when the meeting is,
           and message Rahul that I'll attend."
    """
    step1 = PlanStep(
        description="search email about meeting",
        intent="search_emails",
        metadata={"query": "tomorrow's meeting"},
    )
    step2 = PlanStep(
        description="check calendar for meeting time",
        intent="search_events",
        metadata={"query": "Rahul"},
    )
    step3 = PlanStep(
        description="message Rahul about attendance",
        intent="send_message",
        metadata={"recipient": "Rahul", "content": "Confirmed, I will attend tomorrow's sync at 4 PM."},
    )

    plan = ExecutionPlan(steps=(step1, step2, step3))
    executor = create_mixed_task_executor()
    brain = Brain(planner=MagicMock(plan=lambda ctx: plan), executor=executor)

    # Turn 1: Step 1 and Step 2 execute automatically, Step 3 pauses for confirmation
    res1 = brain.run("Find the email about tomorrow's meeting, check when the meeting is, and message Rahul that I'll attend.")
    assert brain._pending_approval is not None
    assert "Action requires user confirmation" in res1.output
    # Observation history shows step 1 and 2 succeeded
    obs_steps = [obs.metadata.get("action") for obs in brain._pending_approval.context.observations]
    assert "search_emails" in obs_steps
    assert "search_events" in obs_steps

    # Turn 2: User confirms the message send
    res2 = brain.run("Yes, send the message.")
    assert res2.status == ResultStatus.COMPLETED
    assert "Message sent to Rahul Sharma" in res2.output
    assert brain._pending_approval is None

