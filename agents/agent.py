"""Agent definition and base abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod

from models.protocols import ModelProvider
from models.types import ModelRequest, ModelResponse

from .types import Agent, AgentInput, AgentOutput


class BaseAgent(ABC):
    """Minimal common structure for concrete agents."""

    def __init__(self, agent: Agent, provider: ModelProvider) -> None:
        self._agent = agent
        self._provider = provider

    @property
    def agent(self) -> Agent:
        return self._agent

    @property
    def provider(self) -> ModelProvider:
        return self._provider

    def invoke_model(self, request: ModelRequest) -> ModelResponse:
        return self._provider.invoke(request)

    @abstractmethod
    def reason(self, input: AgentInput) -> AgentOutput: ...
