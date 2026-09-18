"""Email tool implementation for Mamba."""

from __future__ import annotations

from typing import Any

from tools.errors import ToolError
from tools.protocols import ToolHandler
from tools.tool import BaseTool
from tools.types import Tool, ToolInput, ToolOutput

from .errors import EmailError, EmailValidationError
from .providers import EmailProvider, SimulatedEmailProvider
from .types import EMAIL_OPERATIONS, EmailAction


class EmailHandler(ToolHandler):
    """Executes email actions against a configured EmailProvider."""

    def __init__(self, provider: EmailProvider | None = None) -> None:
        self._provider = provider or SimulatedEmailProvider()

    def execute(self, input: ToolInput) -> ToolOutput:
        args = input.arguments
        action_name = str(args.get("action") or input.metadata.get("action") or "").strip().lower()

        try:
            if action_name in (EmailAction.SEARCH_EMAILS.value, "search"):
                query = str(args.get("query") or args.get("q") or "").strip()
                max_results = int(args.get("max_results") or 5)
                results = self._provider.search(query, max_results=max_results)
                dict_results = [r.to_dict() for r in results]
                summary_lines = [
                    f"- [{r.id}] {r.sender} | {r.subject} ({r.date[:10]})"
                    for r in results
                ]
                text_content = (
                    f"Found {len(results)} email(s) matching '{query}':\n" + "\n".join(summary_lines)
                    if results
                    else f"No emails found matching '{query}'."
                )
                return ToolOutput(
                    success=True,
                    result={"emails": dict_results, "count": len(results)},
                    content=text_content,
                    metadata={"action": EmailAction.SEARCH_EMAILS.value, "count": len(results)},
                )

            elif action_name in (EmailAction.LIST_EMAILS.value, "list"):
                max_results = int(args.get("max_results") or 5)
                results = self._provider.list_recent(max_results=max_results)
                dict_results = [r.to_dict() for r in results]
                summary_lines = [
                    f"- [{r.id}] {r.sender} | {r.subject} ({r.date[:10]})"
                    for r in results
                ]
                text_content = (
                    f"Recent emails ({len(results)}):\n" + "\n".join(summary_lines)
                    if results
                    else "No emails in inbox."
                )
                return ToolOutput(
                    success=True,
                    result={"emails": dict_results, "count": len(results)},
                    content=text_content,
                    metadata={"action": EmailAction.LIST_EMAILS.value, "count": len(results)},
                )

            elif action_name in (EmailAction.READ_EMAIL.value, "read"):
                email_id = str(args.get("email_id") or args.get("id") or "").strip()
                if not email_id:
                    raise EmailValidationError("Missing required 'email_id' argument.")
                msg = self._provider.get_email(email_id)
                formatted = (
                    f"From: {msg.sender}\n"
                    f"To: {', '.join(msg.recipients)}\n"
                    f"Date: {msg.date}\n"
                    f"Subject: {msg.subject}\n\n"
                    f"{msg.body}"
                )
                return ToolOutput(
                    success=True,
                    result=msg.to_dict(),
                    content=formatted,
                    metadata={"action": EmailAction.READ_EMAIL.value, "email_id": msg.id, "subject": msg.subject},
                )

            elif action_name in (EmailAction.SUMMARIZE_EMAIL.value, "summarize"):
                email_id = str(args.get("email_id") or args.get("id") or "").strip()
                if email_id:
                    msg = self._provider.get_email(email_id)
                    subject = msg.subject
                    body = msg.body
                    sender = msg.sender
                else:
                    subject = str(args.get("subject") or "Email")
                    body = str(args.get("body") or args.get("content") or "")
                    sender = str(args.get("sender") or "Unknown")

                # Build a concise structured summary
                summary_text = (
                    f"Summary of email '{subject}' from {sender}:\n"
                    f"- Topic: {subject}\n"
                    f"- Content: {body[:300]}..." if len(body) > 300 else f"Summary: {body}"
                )
                return ToolOutput(
                    success=True,
                    result={"subject": subject, "sender": sender, "summary": summary_text},
                    content=summary_text,
                    metadata={"action": EmailAction.SUMMARIZE_EMAIL.value, "email_id": email_id},
                )

            elif action_name in (EmailAction.DRAFT_EMAIL.value, "draft"):
                to_val = args.get("to")
                if isinstance(to_val, str):
                    to = tuple(t.strip() for t in to_val.split(",") if t.strip())
                elif isinstance(to_val, (list, tuple)):
                    to = tuple(str(t) for t in to_val)
                else:
                    to = ()

                subject = str(args.get("subject") or "")
                body = str(args.get("body") or args.get("content") or "")
                reply_to_id = args.get("reply_to_id") or args.get("email_id")

                draft = self._provider.create_draft(
                    to=to,
                    subject=subject,
                    body=body,
                    reply_to_id=str(reply_to_id) if reply_to_id else None,
                )
                formatted = (
                    f"Created draft [{draft.draft_id}]:\n"
                    f"To: {', '.join(draft.to)}\n"
                    f"Subject: {draft.subject}\n\n"
                    f"{draft.body}"
                )
                return ToolOutput(
                    success=True,
                    result=draft.to_dict(),
                    content=formatted,
                    metadata={"action": EmailAction.DRAFT_EMAIL.value, "draft_id": draft.draft_id},
                )

            elif action_name in (EmailAction.SEND_EMAIL.value, "send"):
                to_val = args.get("to")
                if isinstance(to_val, str):
                    to = tuple(t.strip() for t in to_val.split(",") if t.strip())
                elif isinstance(to_val, (list, tuple)):
                    to = tuple(str(t) for t in to_val)
                else:
                    to = ()

                subject = str(args.get("subject") or "")
                body = str(args.get("body") or args.get("content") or "")
                reply_to_id = args.get("reply_to_id")

                receipt = self._provider.send(
                    to=to,
                    subject=subject,
                    body=body,
                    reply_to_id=str(reply_to_id) if reply_to_id else None,
                )
                formatted = f"Email sent successfully to {', '.join(receipt.to)} (Message ID: {receipt.message_id})."
                return ToolOutput(
                    success=True,
                    result=receipt.to_dict(),
                    content=formatted,
                    metadata={
                        "action": EmailAction.SEND_EMAIL.value,
                        "message_id": receipt.message_id,
                        "verified": True,
                        "status": "sent",
                    },
                )

            elif action_name in (EmailAction.REPLY_EMAIL.value, "reply"):
                email_id = str(args.get("email_id") or args.get("id") or "").strip()
                body = str(args.get("body") or args.get("content") or "")
                if not email_id:
                    raise EmailValidationError("Missing required 'email_id' to reply to.")

                receipt = self._provider.reply(email_id=email_id, body=body)
                formatted = f"Reply sent successfully to {', '.join(receipt.to)} (Message ID: {receipt.message_id})."
                return ToolOutput(
                    success=True,
                    result=receipt.to_dict(),
                    content=formatted,
                    metadata={
                        "action": EmailAction.REPLY_EMAIL.value,
                        "message_id": receipt.message_id,
                        "verified": True,
                        "status": "sent",
                    },
                )

            else:
                raise EmailValidationError(f"Unknown email action: '{action_name}'")

        except EmailError as exc:
            return ToolOutput(
                success=False,
                error=str(exc),
                content=f"Email operation failed: {exc}",
                metadata={"action": action_name, "error": type(exc).__name__},
            )
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=str(exc),
                content=f"Email operation failed: {exc}",
                metadata={"action": action_name, "error": "unexpected_error"},
            )


class EmailTool(BaseTool):
    """Tool exposing email capabilities to Mamba."""

    def __init__(self, provider: EmailProvider | None = None) -> None:
        tool = Tool(
            name="email",
            description="Manage, read, search, draft, and send emails.",
            metadata={"tool": "email"},
        )
        handler = EmailHandler(provider=provider)
        super().__init__(tool=tool, handler=handler)

