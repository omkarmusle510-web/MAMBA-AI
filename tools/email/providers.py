"""Email provider protocols and development simulation implementation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, Sequence
from uuid import uuid4

from .errors import EmailNotFoundError, EmailValidationError
from .types import EmailDraft, EmailMessage, EmailSendReceipt


class EmailProvider(Protocol):
    """Protocol for email service providers."""

    def search(self, query: str, *, max_results: int = 5) -> Sequence[EmailMessage]:
        """Search emails matching query in sender, recipient, subject, or body."""
        ...

    def list_recent(self, *, max_results: int = 5) -> Sequence[EmailMessage]:
        """List the most recent emails."""
        ...

    def get_email(self, email_id: str) -> EmailMessage:
        """Retrieve a specific email message by ID."""
        ...

    def create_draft(
        self,
        *,
        to: tuple[str, ...],
        subject: str,
        body: str,
        cc: tuple[str, ...] = (),
        reply_to_id: str | None = None,
    ) -> EmailDraft:
        """Create and store an email draft."""
        ...

    def send(
        self,
        *,
        to: tuple[str, ...],
        subject: str,
        body: str,
        cc: tuple[str, ...] = (),
        reply_to_id: str | None = None,
    ) -> EmailSendReceipt:
        """Send an email message and return a verified send receipt."""
        ...

    def reply(
        self,
        *,
        email_id: str,
        body: str,
        cc: tuple[str, ...] = (),
    ) -> EmailSendReceipt:
        """Reply to an existing email message."""
        ...


class SimulatedEmailProvider:
    """Development and testing simulation provider with sample messages.

    Explicitly tagged as a development/simulation provider, not a production fallback.
    Provides deterministic behavior for tests and local interactive development.
    """

    def __init__(self, initial_emails: Sequence[EmailMessage] | None = None) -> None:
        self.provider_name = "simulated_email"
        self._emails: dict[str, EmailMessage] = {}
        self._drafts: dict[str, EmailDraft] = {}
        self._sent_receipts: list[EmailSendReceipt] = []

        if initial_emails is not None:
            for em in initial_emails:
                self._emails[em.id] = em
        else:
            self._populate_sample_inbox()

    def _populate_sample_inbox(self) -> None:
        sample_messages = [
            EmailMessage(
                id="em-101",
                sender="events@nvidia.com",
                recipients=("user@mamba.ai",),
                subject="NVIDIA AI Developer Day & Keynote Invitation",
                body=(
                    "Hi Mamba Team,\n\n"
                    "You are cordially invited to the NVIDIA AI Developer Day next Wednesday at 10:00 AM PST. "
                    "Jensen Huang will present new breakthroughs in accelerated agentic AI and edge inference. "
                    "Please confirm your attendance by replying to this invitation.\n\n"
                    "Best regards,\nNVIDIA Developer Relations"
                ),
                date="2026-09-12T14:30:00Z",
            ),
            EmailMessage(
                id="em-102",
                sender="rahul@partner.org",
                recipients=("user@mamba.ai",),
                subject="Project Mamba Sync & Tomorrow's Meeting",
                body=(
                    "Hey,\n\n"
                    "Let's catch up on the communication skills roadmap. We have a sync scheduled for tomorrow at 4:00 PM. "
                    "Let me know if this time still works for you or if we should adjust.\n\n"
                    "Thanks,\nRahul"
                ),
                date="2026-09-12T16:45:00Z",
            ),
            EmailMessage(
                id="em-103",
                sender="security@service.com",
                recipients=("user@mamba.ai",),
                subject="Security notification: New login from Chrome",
                body="A new login was recorded on your account from Windows 11. No action needed if this was you.",
                date="2026-09-13T02:15:00Z",
            ),
        ]
        for msg in sample_messages:
            self._emails[msg.id] = msg

    def search(self, query: str, *, max_results: int = 5) -> Sequence[EmailMessage]:
        q = query.lower().strip()
        matches = [
            em for em in self._emails.values()
            if q in em.subject.lower()
            or q in em.sender.lower()
            or q in em.body.lower()
            or any(q in r.lower() for r in em.recipients)
        ]
        return tuple(sorted(matches, key=lambda m: m.date, reverse=True)[:max_results])

    def list_recent(self, *, max_results: int = 5) -> Sequence[EmailMessage]:
        sorted_emails = sorted(self._emails.values(), key=lambda m: m.date, reverse=True)
        return tuple(sorted_emails[:max_results])

    def get_email(self, email_id: str) -> EmailMessage:
        email = self._emails.get(email_id)
        if email is None:
            raise EmailNotFoundError(f"Email with ID '{email_id}' was not found.")
        return email

    def create_draft(
        self,
        *,
        to: tuple[str, ...],
        subject: str,
        body: str,
        cc: tuple[str, ...] = (),
        reply_to_id: str | None = None,
    ) -> EmailDraft:
        if not to:
            raise EmailValidationError("Cannot create email draft without recipients ('to').")
        draft_id = f"draft-{uuid4().hex[:8]}"
        draft = EmailDraft(
            draft_id=draft_id,
            to=to,
            subject=subject or "(No Subject)",
            body=body,
            cc=cc,
            reply_to_id=reply_to_id,
        )
        self._drafts[draft_id] = draft
        return draft

    def send(
        self,
        *,
        to: tuple[str, ...],
        subject: str,
        body: str,
        cc: tuple[str, ...] = (),
        reply_to_id: str | None = None,
    ) -> EmailSendReceipt:
        if not to:
            raise EmailValidationError("Cannot send email without recipients ('to').")
        msg_id = f"msg-{uuid4().hex[:8]}"
        now_str = datetime.now(UTC).isoformat()
        receipt = EmailSendReceipt(
            message_id=msg_id,
            to=to,
            subject=subject,
            sent_at=now_str,
            status="sent",
        )
        self._sent_receipts.append(receipt)

        # Store as outgoing message
        sent_email = EmailMessage(
            id=msg_id,
            sender="user@mamba.ai",
            recipients=to,
            subject=subject,
            body=body,
            date=now_str,
            cc=cc,
            reply_to=reply_to_id,
        )
        self._emails[msg_id] = sent_email
        return receipt

    def reply(
        self,
        *,
        email_id: str,
        body: str,
        cc: tuple[str, ...] = (),
    ) -> EmailSendReceipt:
        orig = self.get_email(email_id)
        reply_to = (orig.sender,)
        reply_subj = orig.subject if orig.subject.lower().startswith("re:") else f"Re: {orig.subject}"
        return self.send(
            to=reply_to,
            subject=reply_subj,
            body=body,
            cc=cc,
            reply_to_id=email_id,
        )

