"""Wire the nodes and edges into the LangGraph state machine (diagram 02, Architecture.md §4).

    START → intake → recall_memory → plan ─┬→ dispatch ─(Send per ready subtask)→ specialist_* → review
                                           └→ approve_plan ⏸ ─┬→ dispatch
                                                              └→ deliver (cancelled / taken over)
    review ─┬→ await_approval ⏸ ─(Send: resume the paused specialist)→ specialist_* → review …
            ├→ (Send: retries / newly ready) → specialist_* → review …
            ├→ synthesize → deliver → write_memory → END
            └→ escalate ⏸ ─┬→ (Send: retry / human-supplied result) → specialist_* / synthesize
                           └→ deliver (cancelled / taken over)

⏸ = the node calls interrupt(); the checkpointer persists the state and the worker returns.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from packages.orchestrator.graph.deps import GraphDeps
from packages.orchestrator.graph.edges import (
    NODE_APPROVE_PLAN,
    NODE_AWAIT_APPROVAL,
    NODE_DELIVER,
    NODE_DISPATCH,
    NODE_ESCALATE,
    NODE_SYNTHESIZE,
    make_route_after_escalate,
    make_route_after_plan,
    make_route_after_review,
    route_after_approve_plan,
    route_after_await_approval,
    route_dispatch,
    specialist_node_name,
)
from packages.orchestrator.graph.nodes.approve_plan import make_approve_plan_node
from packages.orchestrator.graph.nodes.await_approval import make_await_approval_node
from packages.orchestrator.graph.nodes.deliver import make_deliver_node
from packages.orchestrator.graph.nodes.dispatch import dispatch
from packages.orchestrator.graph.nodes.escalate import make_escalate_node
from packages.orchestrator.graph.nodes.intake import intake
from packages.orchestrator.graph.nodes.plan import make_plan_node
from packages.orchestrator.graph.nodes.recall_memory import make_recall_memory_node
from packages.orchestrator.graph.nodes.review import make_review_node
from packages.orchestrator.graph.nodes.specialist import make_specialist_node
from packages.orchestrator.graph.nodes.synthesize import make_synthesize_node
from packages.orchestrator.graph.nodes.write_memory import make_write_memory_node
from packages.orchestrator.graph.state import TaskState


def build_graph(deps: GraphDeps, checkpointer: Any = None) -> CompiledStateGraph:  # type: ignore[type-arg]
    g: StateGraph = StateGraph(TaskState)  # type: ignore[type-arg]
    cfg = deps.config

    g.add_node("intake", intake)
    g.add_node("recall_memory", make_recall_memory_node(deps))
    g.add_node("plan", make_plan_node(deps))
    g.add_node(NODE_APPROVE_PLAN, make_approve_plan_node(deps))
    g.add_node(NODE_DISPATCH, dispatch)
    specialist_nodes = []
    for name, agent in deps.specialists.items():
        node_name = specialist_node_name(name)
        specialist_nodes.append(node_name)
        g.add_node(node_name, make_specialist_node(agent, deps))
    g.add_node("review", make_review_node(deps))
    g.add_node(NODE_AWAIT_APPROVAL, make_await_approval_node(deps))
    g.add_node(NODE_SYNTHESIZE, make_synthesize_node(deps))
    g.add_node(NODE_ESCALATE, make_escalate_node(deps))
    g.add_node(NODE_DELIVER, make_deliver_node(deps))
    g.add_node("write_memory", make_write_memory_node(deps))

    g.add_edge(START, "intake")
    g.add_conditional_edges(
        "intake", _route_after_intake, {"plan": "recall_memory", "fail": NODE_DELIVER}
    )
    g.add_edge("recall_memory", "plan")
    g.add_conditional_edges(
        "plan",
        make_route_after_plan(cfg.plan_confidence_threshold),
        [NODE_DISPATCH, NODE_APPROVE_PLAN, NODE_ESCALATE],
    )
    g.add_conditional_edges(
        NODE_APPROVE_PLAN, route_after_approve_plan, [NODE_DISPATCH, NODE_DELIVER]
    )
    g.add_conditional_edges(NODE_DISPATCH, route_dispatch, [*specialist_nodes, NODE_ESCALATE])
    for node_name in specialist_nodes:
        g.add_edge(node_name, "review")
    g.add_conditional_edges(
        "review",
        make_route_after_review(cfg.max_retries),
        [*specialist_nodes, NODE_AWAIT_APPROVAL, NODE_SYNTHESIZE, NODE_ESCALATE],
    )
    g.add_conditional_edges(
        NODE_AWAIT_APPROVAL, route_after_await_approval, [*specialist_nodes, NODE_ESCALATE]
    )
    g.add_conditional_edges(
        NODE_ESCALATE,
        make_route_after_escalate(cfg.max_retries),
        [*specialist_nodes, NODE_SYNTHESIZE, NODE_DELIVER, NODE_ESCALATE],
    )
    g.add_edge(NODE_SYNTHESIZE, NODE_DELIVER)
    g.add_conditional_edges(
        NODE_DELIVER, _route_after_deliver, {"memory": "write_memory", "end": END}
    )
    g.add_edge("write_memory", END)

    return g.compile(checkpointer=checkpointer, name="foreman")


def _route_after_intake(state: TaskState) -> str:
    return "fail" if state.get("status") == "failed" else "plan"


def _route_after_deliver(state: TaskState) -> str:
    return "memory" if state.get("status") == "done" else "end"
