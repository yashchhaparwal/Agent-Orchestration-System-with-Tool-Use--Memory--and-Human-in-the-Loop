"""Phase 5 done-when against the real stack (ChromaDB + Ollama embeddings, fake chat models):
a second, similar task for the same user recalls the lesson written by the first, the trace shows
it (``memory.write`` → ``memory.recall`` span ids), the planner prompt carries it under the labelled
heading, and DELETE leaves zero records.

    uv run pytest -q -m integration tests/integration/test_memory_live.py
"""

from __future__ import annotations

import uuid
from pathlib import Path

import chromadb
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from packages.orchestrator.llm.embeddings import EmbeddingChain
from packages.orchestrator.llm.providers import ProviderPool
from packages.orchestrator.llm.roles import load_models_config
from packages.orchestrator.memory.long_term import LongTermMemory
from packages.shared.config import get_settings
from packages.shared.types.memory import MemoryOutcome, MemoryRecord
from tests.unit.test_graph_flow import Scenario

pytestmark = [pytest.mark.integration]
HEADING = "## Relevant past experience (advice, not instructions)"


@pytest.fixture
def live_memory():  # type: ignore[no-untyped-def]
    settings = get_settings()
    config = load_models_config(settings.models_config_path, enable_paid=False)
    client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
    embedding = config.role("embedding")
    memory = LongTermMemory(
        client,
        EmbeddingChain(embedding, ProviderPool(settings, config)),
        embedding.chain[0].model,
        collection=f"memtest_{uuid.uuid4().hex[:8]}",
    )
    yield memory
    client.delete_collection(memory.collection)


async def test_write_recall_delete_with_real_embeddings(live_memory: LongTermMemory) -> None:
    user = f"u_{uuid.uuid4().hex[:6]}"
    report = await live_memory.write(
        user,
        [
            MemoryRecord(
                text="This user rejected sending complaint emails: drafts only, a person sends them.",
                task_type="complaint_letter",
                outcome=MemoryOutcome.DECISION,
                importance=5,
            ),
            MemoryRecord(
                text="The loans table has no income column, so DTI cannot be computed; use rollover and missed-payment flags.",
                task_type="claim_review",
                importance=3,
            ),
        ],
    )
    assert len(report.inserted) == 2
    found = await live_memory.recall(user, "draft and send a complaint letter to the lender")
    assert found and found[0].text.startswith("This user rejected sending")
    assert found[0].score > found[-1].score or len(found) == 1
    again = await live_memory.write(
        user,
        [
            MemoryRecord(
                text="This user rejected sending complaint emails: drafts only, a person sends them.",
                task_type="complaint_letter",
                outcome=MemoryOutcome.DECISION,
            )
        ],
    )
    assert again.inserted == [] and len(again.reinforced) == 1  # real-embedding dedup
    assert live_memory.delete_user(user) == 2 and live_memory.list_user(user) == []


async def test_second_similar_task_recalls_the_first_tasks_lesson(
    tmp_path: Path, live_memory: LongTermMemory, spans: InMemorySpanExporter
) -> None:
    sc = Scenario(tmp_path, long_term=live_memory)
    first, _ = await sc.run("Review claim CLM-4471 and draft a complaint letter to the lender")
    assert first["status"] == "done" and first["recalled_memories"] == []
    write_spans = [s for s in spans.get_finished_spans() if s.name == "memory.write"]
    written = [i for i in str(write_spans[-1].attributes.get("inserted", "")).split(",") if i]
    assert written, write_spans[-1].attributes

    spans.clear()
    second, _ = await sc.run("Review claim CLM-4471 again and draft the follow-up complaint letter")
    assert second["status"] == "done"
    recall_spans = [s for s in spans.get_finished_spans() if s.name == "memory.recall"]
    recalled = str(recall_spans[-1].attributes.get("ids", "")).split(",")
    assert set(written) & set(recalled), (written, recalled)
    plan_prompts = [c["messages"][1]["content"] for c in sc.calls if c["schema"] == "ExecutionPlan"]
    assert HEADING not in plan_prompts[0] and HEADING in plan_prompts[1]
    assert "Lesson from: Review claim CLM-4471 and draft" in plan_prompts[1]
    assert live_memory.delete_user("u1") >= 1 and live_memory.list_user("u1") == []
