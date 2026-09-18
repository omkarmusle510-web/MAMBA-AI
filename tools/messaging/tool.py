"""Messaging tool implementation for Mamba."""

from __future__ import annotations

from typing import Any

from tools.protocols import ToolHandler
from tools.tool import BaseTool
from tools.types import Tool, ToolInput, ToolOutput

from .errors import MessagingError, MessagingValidationError
from .providers import MessagingProvider, SimulatedMessagingProvider
from .types import MESSAGING_OPERATIONS, MessagingAction


class MessagingHandler(ToolHandler):
    """Executes messaging operations against a configured MessagingProvider."""

    def __init__(self, provider: MessagingProvider | None = None) -> None:
        self._provider = provider or SimulatedMessagingProvider()

    def execute(self, input: ToolInput) -> ToolOutput:
        args = input.arguments
        action_name = str(args.get("action") or input.metadata.get("action") or "").strip().lower()

        try:
            if action_name in (MessagingAction.LIST_CONVERSATIONS.value, "list"):
                limit = int(args.get("limit") or 10)
                convs = self._provider.list_conversations(limit=limit)
                dict_convs = [c.to_dict() for c in convs]
                lines = [
                    f"- [{c.id}] {c.name} (last: '{c.last_message or 'No messages'}')"
                    for c in convs
                ]
                text_content = (
                    f"Active conversations ({len(convs)}):\n" + "\n".join(lines)
                    if convs
                    else "No active conversations found."
                )
                return ToolOutput(
                    success=True,
                    result={"conversations": dict_convs, "count": len(convs)},
                    content=text_content,
                    metadata={"action": MessagingAction.LIST_CONVERSATIONS.value, "count": len(convs)},
                )

            elif action_name in (MessagingAction.SEARCH_CONVERSATIONS.value, "search"):
                query = str(args.get("query") or args.get("q") or args.get("recipient") or "").strip()
                limit = int(args.get("limit") or 5)
                convs = self._provider.search_conversations(query, limit=limit)
                dict_convs = [c.to_dict() for c in convs]
                lines = [
                    f"- [{c.id}] {c.name} (participants: {', '.join(c.participants)})"
                    for c in convs
                ]
                text_content = (
                    f"Found {len(convs)} conversation(s) matching '{query}':\n" + "\n".join(lines)
                    if convs
                    else f"No conversations found matching '{query}'."
                )
                return ToolOutput(
                    success=True,
                    result={"conversations": dict_convs, "count": len(convs)},
                    content=text_content,
                    metadata={"action": MessagingAction.SEARCH_CONVERSATIONS.value, "count": len(convs)},
                )

            elif action_name in (MessagingAction.READ_MESSAGES.value, "read"):
                conv_id = str(args.get("conversation_id") or args.get("id") or args.get("recipient") or "").strip()
                limit = int(args.get("limit") or 20)
                if not conv_id:
                    raise MessagingValidationError("Missing required 'conversation_id' or 'recipient' argument.")

                messages = self._provider.read_messages(conv_id, limit=limit)
                dict_msgs = [m.to_dict() for m in messages]
                lines = [f"{m.sender} [{m.timestamp.split('T')[-1][:5]}]: {m.content}" for m in messages]
                text_content = (
                    f"Messages in {conv_id} ({len(messages)}):\n" + "\n".join(lines)
                    if messages
                    else f"No messages in conversation '{conv_id}'."
                )
                return ToolOutput(
                    success=True,
                    result={"messages": dict_msgs, "count": len(messages)},
                    content=text_content,
                    metadata={"action": MessagingAction.READ_MESSAGES.value, "conversation_id": conv_id},
                )

            elif action_name in (MessagingAction.DRAFT_MESSAGE.value, "draft"):
                recipient = str(args.get("recipient") or args.get("to") or "").strip()
                content = str(args.get("content") or args.get("message") or "").strip()
                conv_id = args.get("conversation_id")

                draft = self._provider.draft_message(
                    recipient=recipient,
                    content=content,
                    conversation_id=str(conv_id) if conv_id else None,
                )
                formatted = f"Drafted message to {draft.recipient}: \"{draft.content}\""
                return ToolOutput(
                    success=True,
                    result=draft.to_dict(),
                    content=formatted,
                    metadata={"action": MessagingAction.DRAFT_MESSAGE.value, "draft_id": draft.draft_id},
                )

            elif action_name in (MessagingAction.SEND_MESSAGE.value, "send"):
                recipient = str(args.get("recipient") or args.get("to") or "").strip()
                content = str(args.get("content") or args.get("message") or "").strip()
                conv_id = args.get("conversation_id")

                receipt = self._provider.send_message(
                    recipient=recipient,
                    content=content,
                    conversation_id=str(conv_id) if conv_id else None,
                )
                formatted = f"Message sent to {receipt.recipient}: \"{receipt.content}\" (ID: {receipt.message_id})."
                return ToolOutput(
                    success=True,
                    result=receipt.to_dict(),
                    content=formatted,
                    metadata={
                        "action": MessagingAction.SEND_MESSAGE.value,
                        "message_id": receipt.message_id,
                        "conversation_id": receipt.conversation_id,
                        "recipient": receipt.recipient,
                        "status": "sent",
                        "verified": True,
                    },
                )

            elif action_name in (MessagingAction.REPLY_MESSAGE.value, "reply"):
                conv_id = str(args.get("conversation_id") or args.get("recipient") or "").strip()
                content = str(args.get("content") or args.get("message") or "").strip()
                if not conv_id:
                    raise MessagingValidationError("Missing required 'conversation_id' or 'recipient' to reply to.")

                receipt = self._provider.reply_message(
                    conversation_id=conv_id,
                    content=content,
                )
                formatted = f"Reply sent to {receipt.recipient}: \"{receipt.content}\" (ID: {receipt.message_id})."
                return ToolOutput(
                    success=True,
                    result=receipt.to_dict(),
                    content=formatted,
                    metadata={
                        "action": MessagingAction.REPLY_MESSAGE.value,
                        "message_id": receipt.message_id,
                        "conversation_id": receipt.conversation_id,
                        "recipient": receipt.recipient,
                        "status": "sent",
                        "verified": True,
                    },
                )

            else:
                raise MessagingValidationError(f"Unknown messaging action: '{action_name}'")

        except MessagingError as exc:
            return ToolOutput(
                success=False,
                error=str(exc),
                content=f"Messaging operation failed: {exc}",
                metadata={"action": action_name, "error": type(exc).__name__},
            )
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=str(exc),
                content=f"Messaging operation failed: {exc}",
                metadata={"action": action_name, "error": "unexpected_error"},
            )


class MessagingTool(BaseTool):
    """Tool exposing messaging capabilities to Mamba."""

    def __init__(self, provider: MessagingProvider | None = None) -> None:
        tool = Tool(
            name="messaging",
            description="Manage, read, search, draft, and send messages across conversations.",
            metadata={"tool": "messaging"},
        )
        handler = MessagingHandler(provider=provider)
        super().__init__(tool=tool, handler=handler)

