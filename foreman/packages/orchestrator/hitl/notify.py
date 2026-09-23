"""Tell a human there is something to decide: structured log always, Slack webhook when set."""

from __future__ import annotations

import httpx
import structlog

from packages.orchestrator.hitl.approvals import level_label
from packages.shared.config import Settings
from packages.shared.types.approval import ApprovalRequest

log = structlog.get_logger(__name__)


async def notify_approval(settings: Settings, request: ApprovalRequest, approval_id: int) -> None:
    log.info(
        "hitl.approval_requested",
        approval_id=approval_id,
        task_id=request.task_id,
        level=request.level.value,
        kind=request.kind.value,
        trigger=request.trigger.value,
        subtask_id=request.subtask_id,
        tool=request.proposed_action.get("tool"),
    )
    if not settings.slack_webhook_url:
        return
    action = request.proposed_action.get("tool") or request.kind.value
    text = (
        f":raised_hand: Foreman needs a decision — *{request.level.value} {level_label(request.level)}*\n"
        f"task `{request.task_id}` · {request.kind.value} · {action}\n"
        f"{settings.api_base_url}/v1/approvals/{approval_id}"
    )
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(settings.slack_webhook_url, json={"text": text})
    except Exception as e:  # noqa: BLE001 — notification failure must never block the graph
        log.warning("hitl.notify_failed", error=str(e)[:200])
