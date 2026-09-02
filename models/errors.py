"""Models-layer exception hierarchy."""


class ModelError(Exception):
    """Base error for all Models-layer failures."""


class ModelRequestError(ModelError):
    """Raised when a model request is invalid."""


class ModelProviderError(ModelError):
    """Raised when a model provider fails."""


class ModelRoutingError(ModelError):
    """Raised when model routing fails."""
