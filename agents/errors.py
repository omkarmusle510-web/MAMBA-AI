"""Agents-layer exception hierarchy."""


class AgentError(Exception):
    """Base error for all Agents-layer failures."""


class AgentReasoningError(AgentError):
    """Raised when agent reasoning fails."""


class AgentPlanningError(AgentError):
    """Raised when converting agent output into an execution plan fails."""
