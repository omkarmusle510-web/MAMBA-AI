"""Types and data structures for messaging capabilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from permissions.types import RiskLevel


class MessagingAction(StrEnum):
    """Supported messaging operations."""

    LIST_CONVERSATIONS = "list_conversations"
    SEARCH_CONVERSATIONS = "search_conversations"
    READ_MESSAGES = "read_messages"
    DRAFT_MESSAGE = "draft_message"
    SEND_MESSAGE = "send_message"
    REPLY_MESSAGE = "reply_message"


@dataclass(frozen=True, slots=True)
class Message:
    """A message in a conversation."""

    id: str
    conversation_id: str
    sender: str
    content: str
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "sender": self.sender,
            "content": self.content,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True, slots=True)
class Conversation:
    """A chat conversation or thread with one or more participants."""

    id: str
    name: str
    participants: tuple[str, ...]
    last_message: str | None = None
    last_activity: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "participants": list(self.participants),
            "last_message": self.last_message,
            "last_activity": self.last_activity,
        }


@dataclass(frozen=True, slots=True)
class MessageDraft:
    """A prepared message draft."""

    draft_id: str
    recipient: str
    content: str
    conversation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "draft_id": self.draft_id,
            "recipient": self.recipient,
            "content": self.content,
            "conversation_id": self.conversation_id,
        }


@dataclass(frozen=True, slots=True)
class MessageSendReceipt:
    """Verified delivery receipt returned after sending a message."""

    message_id: str
    conversation_id: str
    recipient: str
    content: str
    sent_at: str
    status: str = "sent"
    verified: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "recipient": self.recipient,
            "content": self.content,
            "sent_at": self.sent_at,
            "status": self.status,
            "verified": self.verified,
        }


@dataclass(frozen=True, slots=True)
class MessagingOperationDefinition:
    """Metadata definition for a messaging operation."""

    name: str
    description: str
    action: MessagingAction
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


MESSAGING_OPERATIONS: dict[MessagingAction, MessagingOperationDefinition] = {
    MessagingAction.LIST_CONVERSATIONS: MessagingOperationDefinition(
        name=MessagingAction.LIST_CONVERSATIONS.value,
        description="List active message threads and chats.",
        action=MessagingAction.LIST_CONVERSATIONS,
        risk_level=RiskLevel.LOW,
        destructive=False,
        user_sensitive=False,
        irreversible=False,
        externally_visible=False,
    ),
    MessagingAction.SEARCH_CONVERSATIONS: MessagingOperationDefinition(
        name=MessagingAction.SEARCH_CONVERSATIONS.value,
        description="Search for conversations by contact name or participant.",
        action=MessagingAction.SEARCH_CONVERSATIONS,
        risk_level=RiskLevel.LOW,
        destructive=False,
        user_sensitive=False,
        irreversible=False,
        externally_visible=False,
    ),
    MessagingAction.READ_MESSAGES: MessagingOperationDefinition(
        name=MessagingAction.READ_MESSAGES.value,
        description="Read messages from a specific conversation.",
        action=MessagingAction.READ_MESSAGES,
        risk_level=RiskLevel.LOW,
        destructive=False,
        user_sensitive=False,
        irreversible=False,
        externally_visible=False,
    ),
    MessagingAction.DRAFT_MESSAGE: MessagingOperationDefinition(
        name=MessagingAction.DRAFT_MESSAGE.value,
        description="Create a message draft without sending it.",
        action=MessagingAction.DRAFT_MESSAGE,
        risk_level=RiskLevel.LOW,
        destructive=False,
        user_sensitive=False,
        irreversible=False,
        externally_visible=False,
    ),
    MessagingAction.SEND_MESSAGE: MessagingOperationDefinition(
        name=MessagingAction.SEND_MESSAGE.value,
        description="Send a message to a contact or channel.",
        action=MessagingAction.SEND_MESSAGE,
        risk_level=RiskLevel.HIGH,
        destructive=False,
        user_sensitive=True,
        irreversible=True,
        externally_visible=True,
    ),
    MessagingAction.REPLY_MESSAGE: MessagingOperationDefinition(
        name=MessagingAction.REPLY_MESSAGE.value,
        description="Reply with a message to an existing conversation thread.",
        action=MessagingAction.REPLY_MESSAGE,
        risk_level=RiskLevel.HIGH,
        destructive=False,
        user_sensitive=True,
        irreversible=True,
        externally_visible=True,
    ),
}

