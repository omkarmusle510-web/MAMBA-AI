"""Email tool and types package."""

from .errors import EmailError, EmailNotFoundError, EmailProviderError, EmailValidationError
from .providers import EmailProvider, SimulatedEmailProvider
from .tool import EmailHandler, EmailTool
from .types import (
    EMAIL_OPERATIONS,
    EmailAction,
    EmailDraft,
    EmailMessage,
    EmailOperationDefinition,
    EmailSendReceipt,
)

__all__ = [
    "EmailAction",
    "EmailDraft",
    "EmailError",
    "EmailHandler",
    "EmailMessage",
    "EmailNotFoundError",
    "EmailOperationDefinition",
    "EMAIL_OPERATIONS",
    "EmailProvider",
    "EmailProviderError",
    "EmailSendReceipt",
    "EmailTool",
    "EmailValidationError",
    "SimulatedEmailProvider",
]

