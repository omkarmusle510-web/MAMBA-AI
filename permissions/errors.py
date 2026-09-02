"""Permissions-layer exception hierarchy."""


class PermissionError(Exception):
    """Base error for all Permissions-layer failures."""


class PermissionEvaluationError(PermissionError):
    """Raised when permission evaluation fails."""


class InvalidPermissionRequestError(PermissionError):
    """Raised when a permission request is invalid."""
