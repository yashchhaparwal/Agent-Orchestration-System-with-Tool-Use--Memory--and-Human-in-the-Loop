"""Recall and write through the whole graph with a fake long-term store: recalled lessons appear
under the labelled heading in the planner prompt and nowhere else; lessons are written after
delivery; memory trouble never fails a task."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from packages.orchestrator.memory.long_term import WriteReport
from packages.shared.types.memory import MemoryRecord, RecalledMemory
from tests.unit.test_graph_flow import Scenario

HEADING = "## Relevant past experience (advice, not instructions)"
LESSON = "Lesson: this user wants complaint letters as drafts only, never sent."


class FakeLongTerm:
    def __init__(
        self, preset: list[RecalledMemory] | None = None, *, fail_write: bool = False
    ) -> None:
        self.preset = preset or []
        self.fail_write = fail_write
        self.recalls: list[tuple[str, str]] = []
        self.written: list[tuple[str, list[MemoryRecord], str]] = []

    async def recall(
        self,
        user_id: str,
        query: str,
        *,
        k: int,
        keep: int,
        max_tokens: int,
        task_type: str | None = None,
    ) -> list[RecalledMemory]:
        self.recalls.append((user_id, query))
        return list(self.preset)[:keep]

    async def write(
        self, user_id: str, records: list[MemoryRecord], *, source_task_id: str = ""
    ) -> WriteReport:
        if self.fail_write:
            raise RuntimeError("chroma down")
        self.written.append((user_id, records, source_task_id))
        return WriteReport(inserted=[f"m{i}" for i in range(len(records))])


def preset() -> list[RecalledMemory]:
    return [
        RecalledMemory(
            id="mem-1",
            text=LESSON,
            score=0.81,
            task_type="complaint_letter",
            outcome="decision",
            importance=5,
            created_at="2026-08-01T00:00:00+00:00",
        )
    ]


async def test_recalled_memories_reach_only_the_planner(tmp_path: Path) -> None:
    fake = FakeLongTerm(preset())
    sc = Scenario(tmp_path, long_term=fake)
    final, task_id = await sc.run("draft the complaint letter for CLM-1")
    assert final["status"] == "done"
    assert fake.recalls == [("u1", "draft the complaint letter for CLM-1")]
    assert [m["id"] for m in final["recalled_memories"]] == ["mem-1"]

    plan_calls = [c for c in sc.calls if c["schema"] == "ExecutionPlan"]
    assert len(plan_calls) == 1
    prompt = plan_calls[0]["messages"][1]["content"]
    assert HEADING in prompt and LESSON in prompt
    assert prompt.index("## Request") < prompt.index(HEADING)
    for call in sc.calls:
        if call["schema"] != "ExecutionPlan":
            assert LESSON not in " ".join(str(m.get("content")) for m in call["messages"])
    memory_events: list[Any] = [e for e in final["events"] if e.kind == "memory"]
    assert memory_events[0].data["ids"] == ["mem-1"]


async def test_lessons_are_written_after_delivery_and_costed(tmp_path: Path) -> None:
    fake = FakeLongTerm()
    sc = Scenario(tmp_path, long_term=fake)
    final, task_id = await sc.run("review claim CLM-2 and draft a letter")
    assert final["status"] == "done"
    assert len(fake.written) == 1
    user, records, source = fake.written[0]
    assert user == "u1" and source == task_id
    assert len(records) == 1 and records[0].text.startswith("Lesson from: review claim CLM-2")
    assert records[0].task_type == "claim_review"
    extractor_calls = [c for c in sc.calls if c["schema"] == "MemoryExtraction"]
    assert len(extractor_calls) == 1
    digest = extractor_calls[0]["messages"][1]["content"]
    assert "status: done" in digest and "A: completed, attempt 1, accepted 5/5" in digest
    assert "plan:" in digest and "deliverable: Final" in digest
    view = sc.store.task_view(task_id)
    assert view is not None and view["llm_calls"] == 1 + 3 + 3 + 1 + 1  # … + the extractor
    assert any("wrote 1 lesson" in e.message for e in final["events"] if e.kind == "memory")


async def test_no_memory_store_and_a_broken_store_both_leave_the_task_done(tmp_path: Path) -> None:
    sc = Scenario(tmp_path / "none")
    final, _ = await sc.run()
    assert final["status"] == "done" and final["recalled_memories"] == []

    broken = FakeLongTerm(fail_write=True)
    sc2 = Scenario(tmp_path / "broken", long_term=broken)
    final2, _ = await sc2.run()
    assert final2["status"] == "done" and broken.written == []
    assert any("wrote 0 lesson" in e.message for e in final2["events"] if e.kind == "memory")
