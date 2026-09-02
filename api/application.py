"""Application boundary implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core.errors import CoreError
from core.types import ExecutionResult, ResultStatus, UserRequest

from .errors import InvalidAPIRequestError
from .types import APIRequest, APIResponse


class ExecutionRunner(Protocol):
    """Runs a normalized user request through Mamba execution."""

    def run(self, request: str | UserRequest) -> ExecutionResult: ...


@dataclass(slots=True)
class MambaApplication:
    """Adapter between external callers and Mamba execution."""

    runner: ExecutionRunner

    def handle(self, request: APIRequest) -> APIResponse:
        self._validate_request(request)
        normalized_input = request.input.strip()

        try:
            execution_result = self.runner.run(
                UserRequest(goal=normalized_input, metadata=dict(request.metadata))
            )
        except CoreError as exc:
            return APIResponse(
                success=False,
                error=str(exc),
                metadata=dict(request.metadata),
            )

        return self._to_response(execution_result, request)

    def _validate_request(self, request: APIRequest) -> None:
        if not isinstance(request, APIRequest):
            raise InvalidAPIRequestError("request must be an APIRequest")

        if not isinstance(request.input, str):
            raise InvalidAPIRequestError("input must be a string")

        if not request.input.strip():
            raise InvalidAPIRequestError("input must not be empty")

    def _to_response(
        self,
        execution_result: ExecutionResult,
        request: APIRequest,
    ) -> APIResponse:
        metadata = dict(request.metadata)
        metadata["execution_id"] = execution_result.execution_id
        metadata["status"] = execution_result.status.value

        if execution_result.status == ResultStatus.COMPLETED:
            return APIResponse(
                success=True,
                result=execution_result.output,
                metadata=metadata,
            )

        return APIResponse(
            success=False,
            result=execution_result.output,
            error=execution_result.error or "execution failed",
            metadata=metadata,
        )
