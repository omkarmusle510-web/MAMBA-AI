"""API-layer exception hierarchy."""


class APIError(Exception):
    """Base error for all API-layer failures."""


class InvalidAPIRequestError(APIError):
    """Raised when an API request is invalid or fails validation."""


class APIExecutionError(APIError):
    """Raised when execution fails at the API boundary."""
