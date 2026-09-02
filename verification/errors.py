"""Verification-layer exception hierarchy."""


class VerificationError(Exception):
    """Base error for all Verification-layer failures."""


class VerificationEvaluationError(VerificationError):
    """Raised when verification evaluation fails."""


class InvalidVerificationRequestError(VerificationError):
    """Raised when a verification request is invalid."""
