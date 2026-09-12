"""End-to-end tests for Mamba Core lifecycle, capabilities, security metadata, and memory."""

from __future__ import annotations

import pytest

from core.brain import Brain, create_brain
from core.context import ExecutionContext
from core.types import ExecutionPlan, ExecutionResult, Observation, PlanStep, ResultStatus, UserRequest
from memory.persistent import PersistentStore
from memory.store import InMemoryStore
from memory.types import MemoryEntry, MemoryQuery
from permissions.policy import DefaultPermissionPolicy
from permissions.types import PermissionDecision, RiskLevel
from skills.analyze import AnalyzeSkill, AnalyzeTaskHandler
from skills.filesystem import (
    CreateDirectorySkill,
    DeleteSkill,
    FilesystemTaskHandler,
    ListDirectorySkill,
    ReadFileSkill,
    WriteFileSkill,
)
from skills.memory import MemorySkill, MemoryTaskHandler
from skills.mixed import create_mixed_task_executor
from skills.terminal import TerminalSkill, TerminalTaskHandler
from tasks.executor import TaskExecutor
from tasks.types import TaskInput, TaskOutput
from verification.types import UNAVAILABLE, VerificationRequest, VerificationStatus
from verification.verifier import DefaultVerifier


class StaticPlanner:
    """Deterministic test planner that returns predefined plans in sequence."""

    def __init__(self, plans: list[ExecutionPlan]) -> None:
        self._plans = list(plans)
        self._call_count = 0

    @property
    def call_count(self) -> int:
        return self._call_count

    def plan(self, context: ExecutionContext) -> ExecutionPlan:
        self._call_count += 1
        if self._plans:
            return self._plans.pop(0)
        # Default empty plan if exhausted
        return ExecutionPlan(steps=())


class EchoTaskHandler:
    """Mock handler for testing execution flow."""

    def __init__(self) -> None:
        self.executed_steps: list[TaskInput] = []

    def run(self, task_input: TaskInput, context: ExecutionContext) -> TaskOutput:
        self.executed_steps.append(task_input)
        return TaskOutput(
            content=f"Executed {task_input.intent}: {task_input.description}",
            success=True,
            metadata={"intent": task_input.intent},
        )


def test_filesystem_and_terminal_handlers_provide_security_metadata():
    """Verify that task handlers provide authoritative security metadata to TaskExecutor."""
    fs_handler = FilesystemTaskHandler(
        list_directory_skill=ListDirectorySkill(),
        read_file_skill=ReadFileSkill(),
        write_file_skill=WriteFileSkill(),
        create_directory_skill=CreateDirectorySkill(),
        delete_skill=DeleteSkill(),
    )
    term_handler = TerminalTaskHandler(terminal_skill=TerminalSkill())
    executor = TaskExecutor(handlers={
        "delete_file": fs_handler,
        "write_file": fs_handler,
        "read_file": fs_handler,
        "execute_command": term_handler,
    })

    # File deletion must be HIGH risk, destructive, irreversible
    del_step = PlanStep(description="delete temp", intent="delete_file", metadata={"path": "tmp.txt"})
    del_meta = executor.get_metadata(del_step)
    assert del_meta.get("risk_level") == "high"
    assert del_meta.get("destructive") is True
    assert del_meta.get("irreversible") is True

    # Write file must be destructive, medium risk
    write_step = PlanStep(description="write config", intent="write_file", metadata={"path": "cfg.json", "content": "{}"})
    write_meta = executor.get_metadata(write_step)
    assert write_meta.get("destructive") is True
    assert write_meta.get("risk_level") == "medium"

    # Terminal command must be HIGH risk, destructive, user_sensitive
    term_step = PlanStep(description="run bash script", intent="execute_command", metadata={"command": "ls"})
    term_meta = executor.get_metadata(term_step)
    assert term_meta.get("risk_level") == "high"
    assert term_meta.get("destructive") is True
    assert term_meta.get("user_sensitive") is True


def test_permission_denies_high_risk_destructive_actions_without_approval():
    """Verify Brain blocks high-risk destructive step when permission policy requires approval."""
    fs_handler = FilesystemTaskHandler(
        list_directory_skill=ListDirectorySkill(),
        read_file_skill=ReadFileSkill(),
        write_file_skill=WriteFileSkill(),
        create_directory_skill=CreateDirectorySkill(),
        delete_skill=DeleteSkill(),
    )
    executor = TaskExecutor(handlers={"delete_file": fs_handler})

    plan = ExecutionPlan(steps=(
        PlanStep(description="remove file", intent="delete_file", metadata={"path": "important.txt"}),
    ))
    planner = StaticPlanner([plan])
    brain = Brain(
        planner=planner,
        executor=executor,
        permissions=DefaultPermissionPolicy(),
    )

    # High risk destructive action without approved flag must fail
    res = brain.run("delete important.txt")
    assert res.status == ResultStatus.FAILED
    assert "permission" in res.error.lower() or "denied" in res.error.lower() or "approval" in res.error.lower()


def test_autonomous_replanning_on_step_failure():
    """Verify that when a step fails, Brain observes the failure and replans a recovery step."""
    class Step1FailHandler:
        def __init__(self):
            self.attempts = 0

        def run(self, task_input: TaskInput, context: ExecutionContext) -> TaskOutput:
            self.attempts += 1
            if task_input.intent == "try_primary":
                return TaskOutput(content="Primary resource unavailable", success=False)
            return TaskOutput(content="Backup resource retrieved successfully", success=True)

    handler = Step1FailHandler()
    executor = TaskExecutor(handlers={"try_primary": handler, "try_backup": handler})

    # Plan 1 tries primary; Plan 2 observes failure and tries backup
    plan1 = ExecutionPlan(steps=(
        PlanStep(description="Fetch primary", intent="try_primary", metadata={}),
    ))
    plan2 = ExecutionPlan(steps=(
        PlanStep(description="Fetch backup", intent="try_backup", metadata={}),
    ))

    planner = StaticPlanner([plan1, plan2])
    brain = Brain(planner=planner, executor=executor)

    res = brain.run("Retrieve data")
    assert res.status == ResultStatus.COMPLETED
    assert planner.call_count == 2
    assert "Backup resource retrieved successfully" in res.output


def test_autonomous_replanning_on_verification_failure():
    """Verify that when verification fails, Brain allows replanning so planner can correct it."""
    class CounterHandler:
        def __init__(self):
            self.step = 0

        def run(self, task_input: TaskInput, context: ExecutionContext) -> TaskOutput:
            self.step += 1
            if self.step == 1:
                return TaskOutput(content="draft version", success=True)
            return TaskOutput(content="final verified version", success=True)

    handler = CounterHandler()
    executor = TaskExecutor(handlers={"generate": handler})

    # Step 1 expects "final verified version" but produces "draft version"
    step1 = PlanStep(
        description="generate doc",
        intent="generate",
        metadata={"expected": "final verified version"},
    )
    # Step 2 produces the verified version
    step2 = PlanStep(
        description="regenerate doc",
        intent="generate",
        metadata={"expected": "final verified version"},
    )

    plan1 = ExecutionPlan(steps=(step1,))
    plan2 = ExecutionPlan(steps=(step2,))

    planner = StaticPlanner([plan1, plan2])
    brain = Brain(planner=planner, executor=executor, verifier=DefaultVerifier())

    res = brain.run("Generate final doc")
    assert res.status == ResultStatus.COMPLETED
    assert planner.call_count == 2
    assert "final verified version" in res.output


def test_enhanced_verification_predicates():
    """Test DefaultVerifier supports predicate dictionaries and contains matching."""
    verifier = DefaultVerifier()

    # Substring contains
    req1 = VerificationRequest(expected={"contains": "healthy"}, actual="System is healthy and online")
    assert verifier.verify(req1).status == VerificationStatus.VERIFIED

    # Substring not_contains
    req2 = VerificationRequest(expected={"not_contains": "error"}, actual="System is healthy and online")
    assert verifier.verify(req2).status == VerificationStatus.VERIFIED

    # Exit code
    req3 = VerificationRequest(expected={"exit_code": 0}, actual={"exit_code": 0, "stdout": "ok"})
    assert verifier.verify(req3).status == VerificationStatus.VERIFIED

    # Non-empty output check when verify=True
    req4 = VerificationRequest(expected=UNAVAILABLE, actual="Result text", metadata={"verify": True})
    assert verifier.verify(req4).status == VerificationStatus.VERIFIED

    # Empty output check when verify=True
    req5 = VerificationRequest(expected=UNAVAILABLE, actual="", metadata={"verify": True})
    assert verifier.verify(req5).status == VerificationStatus.FAILED


def test_memory_store_keyword_matching():
    """Test keyword token overlap in MemoryStore."""
    store = InMemoryStore()
    entry = MemoryEntry(content="The database port is 5432 and user is postgres")
    store.store(entry)

    # Query with stopwords and natural phrasing
    query = MemoryQuery(query="What is the database port?")
    result = store.retrieve(query)
    assert len(result.entries) == 1
    assert result.entries[0].id == entry.id

    # Irrelevant query
    query_irr = MemoryQuery(query="What is my favorite animal?")
    result_irr = store.retrieve(query_irr)
    assert len(result_irr.entries) == 0


def test_memory_skill_remember_and_recall():
    """Test MemorySkill directly for storing and recalling facts."""
    store = InMemoryStore()
    skill = MemorySkill(store=store)
    handler = MemoryTaskHandler(memory_skill=skill)

    # Remember
    t_in = TaskInput(
        step_id="s1",
        description="remember preferred language",
        intent="remember",
        execution_id="e1",
        goal="Remember that my preferred language is Python",
        step_metadata={"content": "Preferred language is Python"},
    )
    context = ExecutionContext.from_request(UserRequest(goal="remember language"))
    out = handler.run(t_in, context)
    assert out.success is True
    assert "Remembered" in out.content

    # Recall
    t_recall = TaskInput(
        step_id="s2",
        description="recall language",
        intent="recall",
        execution_id="e1",
        goal="What is my preferred language?",
        step_metadata={"query": "preferred language"},
    )
    out_recall = handler.run(t_recall, context)
    assert out_recall.success is True
    assert "Preferred language is Python" in out_recall.content


def test_mixed_task_executor_routes_core_and_memory_intents():
    """Verify that create_mixed_task_executor registers memory, clarify, and response intents."""
    store = InMemoryStore()
    executor = create_mixed_task_executor(memory_store=store)

    assert "remember" in executor.handlers
    assert "recall" in executor.handlers
    assert "delete_memory" in executor.handlers
    assert "clarify" in executor.handlers
    assert "respond" in executor.handlers
    assert "summarize" in executor.handlers
    assert "explain" in executor.handlers


def test_persistent_store_keyword_matching(tmp_path):
    """Test keyword token overlap in SQLite-backed PersistentStore."""
    db_file = tmp_path / "test_memory.db"
    store = PersistentStore(db_path=db_file)
    entry = MemoryEntry(content="Project root is located at C:\\mamba and uses python virtual environment")
    store.store(entry)

    # Query with natural question
    res = store.retrieve(MemoryQuery(query="Where is the project root located?"))
    assert len(res.entries) == 1
    assert res.entries[0].id == entry.id


def test_end_to_end_mamba_pipeline_with_mixed_executor(tmp_path):
    """Verify full end-to-end lifecycle: Planning -> Permission -> Execution -> Observation -> Verification -> Memory."""
    db_file = tmp_path / "lifecycle_memory.db"
    store = PersistentStore(db_path=db_file)
    executor = create_mixed_task_executor(root_dir=tmp_path, memory_store=store)

    plan = ExecutionPlan(steps=(
        PlanStep(
            description="Write greeting",
            intent="write_file",
            metadata={"path": "greet.txt", "content": "Hello Mamba AI System", "approved": True},
        ),
        PlanStep(
            description="Read greeting",
            intent="read_file",
            metadata={"path": "greet.txt"},
        ),
        PlanStep(
            description="Synthesize greeting",
            intent="respond",
            metadata={"expected": {"contains": "Hello Mamba AI System"}},
        ),
    ))

    planner = StaticPlanner([plan])
    brain = Brain(
        planner=planner,
        executor=executor,
        memory=store,
        permissions=DefaultPermissionPolicy(),
        verifier=DefaultVerifier(),
    )

    result = brain.run("Write greeting to greet.txt, read it back, and respond with greeting")
    assert result.status == ResultStatus.COMPLETED
    assert result.output is not None
    assert "Hello Mamba AI System" in result.output

    # Verify file was written to disk
    written_file = tmp_path / "greet.txt"
    assert written_file.exists()
    assert written_file.read_text(encoding="utf-8") == "Hello Mamba AI System"

    # Verify memory was updated with the goal outcome
    recalled = store.retrieve(MemoryQuery(query="greeting"))
    assert len(recalled.entries) >= 1
    assert "Hello Mamba AI System" in recalled.entries[0].content


def test_analyze_skill_uses_model_router_fallback():
    """Verify AnalyzeSkill invokes model router with provider fallback when primary fails."""
    from models.router import DefaultModelRouter
    from models.types import ModelInfo, ModelRequest, ModelResponse

    class FailPrimaryProvider:
        @property
        def info(self) -> ModelInfo:
            return ModelInfo(provider="primary", model="primary-model")

        def invoke(self, request: ModelRequest) -> ModelResponse:
            raise RuntimeError("Primary provider rate limit exceeded")

    class SuccessBackupProvider:
        @property
        def info(self) -> ModelInfo:
            return ModelInfo(provider="backup", model="backup-model")

        def invoke(self, request: ModelRequest) -> ModelResponse:
            return ModelResponse(
                content="Analysis successfully synthesized by backup",
                provider="backup",
                model="backup-model",
                success=True,
            )

    router = DefaultModelRouter([FailPrimaryProvider(), SuccessBackupProvider()])
    skill = AnalyzeSkill(model_router=router)
    handler = AnalyzeTaskHandler(analyze_skill=skill)

    t_in = TaskInput(
        step_id="a1",
        description="summarize results",
        intent="summarize",
        execution_id="ex1",
        goal="Summarize findings",
        step_metadata={},
    )
    context = ExecutionContext.from_request(UserRequest(goal="Summarize findings"))
    out = handler.run(t_in, context)

    assert out.success is True
    assert "Analysis successfully synthesized by backup" in out.content
    assert out.metadata.get("fallback_from_primary") is True
    assert out.metadata.get("provider") == "backup"


