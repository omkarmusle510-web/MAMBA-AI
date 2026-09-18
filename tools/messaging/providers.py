"""Messaging provider protocol and development simulation implementation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, Sequence
from uuid import uuid4

from .errors import ConversationNotFoundError, MessagingValidationError
from .types import Conversation, Message, MessageDraft, MessageSendReceipt


class MessagingProvider(Protocol):
    """Protocol for messaging service providers."""

    def list_conversations(self, *, limit: int = 10) -> Sequence[Conversation]:
        """List active conversation threads."""
        ...

    def search_conversations(self, query: str, *, limit: int = 5) -> Sequence[Conversation]:
        """Search conversations by contact name or participant."""
        ...

    def read_messages(self, conversation_id: str, *, limit: int = 20) -> Sequence[Message]:
        """Read recent messages from a conversation."""
        ...

    def draft_message(
        self,
        *,
        recipient: str,
        content: str,
        conversation_id: str | None = None,
    ) -> MessageDraft:
        """Create a message draft without sending it."""
        ...

    def send_message(
        self,
        *,
        recipient: str,
        content: str,
        conversation_id: str | None = None,
    ) -> MessageSendReceipt:
        """Send a message to a recipient or conversation."""
        ...

    def reply_message(
        self,
        *,
        conversation_id: str,
        content: str,
    ) -> MessageSendReceipt:
        """Reply to an existing conversation thread."""
        ...


class SimulatedMessagingProvider:
    """Development and testing messaging simulation provider.

    Explicitly tagged as a development/simulation provider, not a production fallback.
    Provides deterministic messaging behavior for tests and local interactive development.
    """

    def __init__(self, initial_conversations: Sequence[Conversation] | None = None) -> None:
        self.provider_name = "simulated_messaging"
        self._conversations: dict[str, Conversation] = {}
        self._messages: dict[str, list[Message]] = {}
        self._drafts: dict[str, MessageDraft] = {}

        if initial_conversations is not None:
            for conv in initial_conversations:
                self._conversations[conv.id] = conv
                self._messages[conv.id] = []
        else:
            self._populate_sample_conversations()

    def _populate_sample_conversations(self) -> None:
        c1 = Conversation(
            id="conv-301",
            name="Rahul Sharma",
            participants=("Rahul", "user@mamba.ai"),
            last_message="Let me know if tomorrow at 4 PM works for our sync!",
            last_activity="2026-09-13T08:30:00Z",
        )
        c2 = Conversation(
            id="conv-302",
            name="Core Engineering Team",
            participants=("Sarah", "Alex", "Dev", "user@mamba.ai"),
            last_message="Reliability loop tests are passing.",
            last_activity="2026-09-13T07:15:00Z",
        )
        self._conversations[c1.id] = c1
        self._conversations[c2.id] = c2

        self._messages[c1.id] = [
            Message(
                id="msg-1",
                conversation_id=c1.id,
                sender="Rahul",
                content="Hey, are we still meeting tomorrow?",
                timestamp="2026-09-13T08:20:00Z",
            ),
            Message(
                id="msg-2",
                conversation_id=c1.id,
                sender="Rahul",
                content="Let me know if tomorrow at 4 PM works for our sync!",
                timestamp="2026-09-13T08:30:00Z",
            ),
        ]
        self._messages[c2.id] = [
            Message(
                id="msg-3",
                conversation_id=c2.id,
                sender="Sarah",
                content="Reliability loop tests are passing.",
                timestamp="2026-09-13T07:15:00Z",
            )
        ]

    def list_conversations(self, *, limit: int = 10) -> Sequence[Conversation]:
        sorted_convs = sorted(
            self._conversations.values(),
            key=lambda c: c.last_activity or "",
            reverse=True,
        )
        return tuple(sorted_convs[:limit])

    def search_conversations(self, query: str, *, limit: int = 5) -> Sequence[Conversation]:
        q = query.lower().strip()
        matches = [
            c for c in self._conversations.values()
            if q in c.name.lower()
            or any(q in p.lower() for p in c.participants)
            or (c.last_message and q in c.last_message.lower())
        ]
        return tuple(sorted(matches, key=lambda c: c.last_activity or "", reverse=True)[:limit])

    def _resolve_conversation_for_recipient(self, recipient: str) -> Conversation:
        """Match recipient name against known conversations or create a new one."""
        rec_clean = recipient.strip()
        # Direct match by ID
        if rec_clean in self._conversations:
            return self._conversations[rec_clean]

        # Match by name or participant
        matches = self.search_conversations(rec_clean, limit=1)
        if matches:
            return matches[0]

        # Create new conversation for recipient
        new_id = f"conv-{uuid4().hex[:6]}"
        new_conv = Conversation(
            id=new_id,
            name=rec_clean,
            participants=(rec_clean, "user@mamba.ai"),
            last_activity=datetime.now(UTC).isoformat(),
        )
        self._conversations[new_id] = new_conv
        self._messages[new_id] = []
        return new_conv

    def read_messages(self, conversation_id: str, *, limit: int = 20) -> Sequence[Message]:
        conv = self._conversations.get(conversation_id)
        if conv is None:
            # Try searching by name if an arbitrary name was provided as ID
            matches = self.search_conversations(conversation_id, limit=1)
            if matches:
                conv = matches[0]
                conversation_id = conv.id
            else:
                raise ConversationNotFoundError(f"Conversation '{conversation_id}' was not found.")
        msgs = self._messages.get(conversation_id, [])
        return tuple(msgs[-limit:])

    def draft_message(
        self,
        *,
        recipient: str,
        content: str,
        conversation_id: str | None = None,
    ) -> MessageDraft:
        if not recipient.strip() and not conversation_id:
            raise MessagingValidationError("Recipient or conversation_id must be provided to draft message.")
        draft_id = f"mdraft-{uuid4().hex[:6]}"
        draft = MessageDraft(
            draft_id=draft_id,
            recipient=recipient.strip(),
            content=content.strip(),
            conversation_id=conversation_id,
        )
        self._drafts[draft_id] = draft
        return draft

    def send_message(
        self,
        *,
        recipient: str,
        content: str,
        conversation_id: str | None = None,
    ) -> MessageSendReceipt:
        if not content.strip():
            raise MessagingValidationError("Cannot send empty message.")

        if conversation_id and conversation_id in self._conversations:
            conv = self._conversations[conversation_id]
        else:
            conv = self._resolve_conversation_for_recipient(recipient)

        now_str = datetime.now(UTC).isoformat()
        msg_id = f"msg-{uuid4().hex[:6]}"
        msg = Message(
            id=msg_id,
            conversation_id=conv.id,
            sender="user@mamba.ai",
            content=content.strip(),
            timestamp=now_str,
        )

        if conv.id not in self._messages:
            self._messages[conv.id] = []
        self._messages[conv.id].append(msg)

        # Update conversation activity
        updated_conv = Conversation(
            id=conv.id,
            name=conv.name,
            participants=conv.participants,
            last_message=content.strip(),
            last_activity=now_str,
        )
        self._conversations[conv.id] = updated_conv

        return MessageSendReceipt(
            message_id=msg_id,
            conversation_id=conv.id,
            recipient=conv.name,
            content=content.strip(),
            sent_at=now_str,
            status="sent",
            verified=True,
        )

    def reply_message(
        self,
        *,
        conversation_id: str,
        content: str,
    ) -> MessageSendReceipt:
        conv = self._conversations.get(conversation_id)
        if conv is None:
            matches = self.search_conversations(conversation_id, limit=1)
            if matches:
                conv = matches[0]
            else:
                raise ConversationNotFoundError(f"Conversation '{conversation_id}' was not found.")
        return self.send_message(
            recipient=conv.name,
            content=content,
            conversation_id=conv.id,
        )

