"""Checkpoint serializer with an explicit allowlist of every type Foreman stores in graph state.

LangGraph deserialises checkpoints with msgpack extensions keyed by (module, class). Its default is
"allow anything, warn"; a future version blocks unregistered types. Registering ours keeps a resume
from a checkpoint deterministic and refuses anything written by something that is not Foreman.
"""

from __future__ import annotations

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from packages.orchestrator.graph.state import PendingApproval
from packages.orchestrator.loop.agent_loop import LoopCheckpoint, PausedLoop, PendingCall
from packages.shared.types.approval import (
    ApprovalDecision,
    ApprovalKind,
    ApprovalLevel,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalTrigger,
    DecisionKind,
)
from packages.shared.types.cost import CostEntry
from packages.shared.types.deliverable import Deliverable
from packages.shared.types.gate import Decision, GateAction, ToolEvent
from packages.shared.types.llm import Usage
from packages.shared.types.plan import ExecutionPlan
from packages.shared.types.review import ReviewJudgement, ReviewVerdict
from packages.shared.types.subtask import (
    Complexity,
    Specialist,
    SubmittedResult,
    Subtask,
    SubtaskResult,
    SubtaskStatus,
)
from packages.shared.types.task import TaskEvent, TaskOptions, TaskStatus
from packages.shared.types.tools import RiskClass, ToolCall, ToolResult, ToolSpec

CHECKPOINT_TYPES: tuple[type, ...] = (
    TaskStatus,
    TaskOptions,
    TaskEvent,
    Deliverable,
    ExecutionPlan,
    Subtask,
    Specialist,
    Complexity,
    SubtaskStatus,
    SubmittedResult,
    SubtaskResult,
    ReviewJudgement,
    ReviewVerdict,
    RiskClass,
    ToolCall,
    ToolResult,
    ToolSpec,
    GateAction,
    Decision,
    ToolEvent,
    CostEntry,
    Usage,
    ApprovalLevel,
    ApprovalKind,
    ApprovalTrigger,
    ApprovalStatus,
    DecisionKind,
    ApprovalDecision,
    ApprovalRequest,
    PendingApproval,
    PendingCall,
    LoopCheckpoint,
    PausedLoop,
)


def checkpoint_serde() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES)
