"""Skills-layer exception hierarchy."""


class SkillError(Exception):
    """Base error for all Skills-layer failures."""


class SkillExecutionError(SkillError):
    """Raised when skill execution fails."""
