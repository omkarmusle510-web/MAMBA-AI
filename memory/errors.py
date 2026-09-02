"""Memory-layer exception hierarchy."""


class MemoryError(Exception):
    """Base error for all Memory-layer failures."""


class MemoryStorageError(MemoryError):
    """Raised when storing memory fails."""


class MemoryRetrievalError(MemoryError):
    """Raised when retrieving memory fails."""


class InvalidMemoryRequestError(MemoryError):
    """Raised when a memory request is invalid."""
