"""Types and data structures for email capabilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from permissions.types import RiskLevel


class EmailAction(StrEnum):
    """Supported email operations."""

    SEARCH_EMAILS = "search_emails"
    LIST_EMAILS = "list_emails"
    READ_EMAIL = "read_email"
    SUMMARIZE_EMAIL = "summarize_email"
    DRAFT_EMAIL = "draft_email"
    SEND_EMAIL = "send_email"
    REPLY_EMAIL = "reply_email"


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """Structured representation of an email message."""

    id: str
    sender: str
    recipients: tuple[str, ...]
    subject: str
    body: str
    date: str
    cc: tuple[str, ...] = field(default_factory=tuple)
    reply_to: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sender": self.sender,
            "recipients": list(self.recipients),
            "subject": self.subject,
            "body": self.body,
            "date": self.date,
            "cc": list(self.cc),
            "reply_to": self.reply_to,
        }


@dataclass(frozen=True, slots=True)
class EmailDraft:
    """Structured email draft."""

    draft_id: str
    to: tuple[str, ...]
    subject: str
    body: str
    cc: tuple[str, ...] = field(default_factory=tuple)
    reply_to_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "draft_id": self.draft_id,
            "to": list(self.to),
            "subject": self.subject,
            "body": self.body,
            "cc": list(self.cc),
            "reply_to_id": self.reply_to_id,
        }


@dataclass(frozen=True, slots=True)
class EmailSendReceipt:
    """Confirmation receipt returned after sending an email."""

    message_id: str
    to: tuple[str, ...]
    subject: str
    sent_at: str
    status: str = "sent"

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "to": list(self.to),
            "subject": self.subject,
            "sent_at": self.sent_at,
            "status": self.status,
            "verified": True,
        }


@dataclass(frozen=True, slots=True)
class EmailOperationDefinition:
    """Metadata definition for an email operation."""

    name: str
    description: str
    action: EmailAction
    risk_level: RiskLevel = RiskLevel.LOW
    destructive: bool = False
    user_sensitive: bool = False
    irreversible: bool = False
    externally_visible: bool = False

    def to_metadata(self) -> dict[str, Any]:
        return {
            "action": self.name,
            "risk_level": self.risk_level,
            "destructive": self.destructive,
            "user_sensitive": self.user_sensitive,
            "irreversible": self.irreversible,
            "externally_visible": self.externally_visible,
        }


EMAIL_OPERATIONS: dict[EmailAction, EmailOperationDefinition] = {
    EmailAction.SEARCH_EMAILS: EmailOperationDefinition(
        name=EmailAction.SEARCH_EMAILS.value,
        description="Search emails by sender, recipient, subject, or content keyword.",
        action=EmailAction.SEARCH_EMAILS,
        risk_level=RiskLevel.LOW,
        destructive=False,
        user_sensitive=False,
        irreversible=False,
        externally_visible=False,
    ),
    EmailAction.LIST_EMAILS: EmailOperationDefinition(
        name=EmailAction.LIST_EMAILS.value,
        description="List recent emails in the inbox or specified mailbox.",
        action=EmailAction.LIST_EMAILS,
        risk_level=RiskLevel.LOW,
        destructive=False,
        user_sensitive=False,
        irreversible=False,
        externally_visible=False,
    ),
    EmailAction.READ_EMAIL: EmailOperationDefinition(
        name=EmailAction.READ_EMAIL.value,
        description="Read an email message and inspect its sender, recipients, subject, date, and body.",
        action=EmailAction.READ_EMAIL,
        risk_level=RiskLevel.LOW,
        destructive=False,
        user_sensitive=False,
        irreversible=False,
        externally_visible=False,
    ),
    EmailAction.SUMMARIZE_EMAIL: EmailOperationDefinition(
        name=EmailAction.SUMMARIZE_EMAIL.value,
        description="Summarize an email's key points and action items.",
        action=EmailAction.SUMMARIZE_EMAIL,
        risk_level=RiskLevel.LOW,
        destructive=False,
        user_sensitive=False,
        irreversible=False,
        externally_visible=False,
    ),
    EmailAction.DRAFT_EMAIL: EmailOperationDefinition(
        name=EmailAction.DRAFT_EMAIL.value,
        description="Create an email draft without sending it.",
        action=EmailAction.DRAFT_EMAIL,
        risk_level=RiskLevel.LOW,
        destructive=False,
        user_sensitive=False,
        irreversible=False,
        externally_visible=False,
    ),
    EmailAction.SEND_EMAIL: EmailOperationDefinition(
        name=EmailAction.SEND_EMAIL.value,
        description="Send an email to external recipients.",
        action=EmailAction.SEND_EMAIL,
        risk_level=RiskLevel.HIGH,
        destructive=False,
        user_sensitive=True,
        irreversible=True,
        externally_visible=True,
    ),
    EmailAction.REPLY_EMAIL: EmailOperationDefinition(
        name=EmailAction.REPLY_EMAIL.value,
        description="Reply to an existing email message.",
        action=EmailAction.REPLY_EMAIL,
        risk_level=RiskLevel.HIGH,
        destructive=False,
        user_sensitive=True,
        irreversible=True,
        externally_visible=True,
    ),
}

