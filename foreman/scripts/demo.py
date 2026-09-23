"""`make demo`: the showcase task end to end, for someone who has never seen Foreman.

    uv run python scripts/demo.py                 # needs the stack up: make dev-up
    uv run python scripts/demo.py --decision approve --user demo2

It seeds the synthetic data if the claims table is empty, submits the showcase request through the
public API, follows the task, shows the exact email the agent asks permission to send, answers the
approval the way a reviewer would (modify the recipient by default), waits for the deliverable, and
then shows the four things that prove the safety story: the outbox row (queued, never sent), the
ledger line for the send, the lessons written to memory, and the trace summary.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

import httpx

from packages.shared.config import get_settings

SHOWCASE = (
    "Review claim CLM-4471: list its loans from the database, assess affordability under the "
    "ruleset, draft a complaint letter to the lender, and send it by email to lender@example.test"
)
TERMINAL = {"awaiting_approval", "done", "failed", "cancelled"}


def say(line: str = "") -> None:
    print(line, flush=True)


class Demo:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.c = httpx.Client(base_url=base_url, headers={"X-API-Key": api_key}, timeout=60)

    def ensure_stack(self) -> None:
        try:
            r = self.c.get("/health")
            r.raise_for_status()
        except httpx.HTTPError as e:
            say(f"The API is not answering ({e}). Start everything first:  make dev-up")
            sys.exit(2)

    def ensure_seed(self) -> None:
        from sqlalchemy import text

        from packages.orchestrator.memory.db import make_engine

        engine = make_engine(get_settings().database_url)
        with engine.connect() as conn:
            exists = conn.execute(
                text("select 1 from information_schema.tables where table_name='claims'")
            ).first()
            count = conn.execute(text("select count(*) from claims")).scalar() if exists else 0
        if not count:
            say("Seeding the synthetic claims data (200 claims, 555 loans, 201 documents)…")
            from infra.seed.generate import generate

            generate()
        else:
            say(f"Synthetic data present ({count} claims).")

    def submit(self, user: str, request: str) -> str:
        r = self.c.post("/v1/tasks", json={"request": request, "user_id": user})
        r.raise_for_status()
        task_id: str = r.json()["task_id"]
        return task_id

    def follow(self, task_id: str, *, limit_s: int = 900) -> dict[str, Any]:
        started = time.time()
        last = ""
        while time.time() - started < limit_s:
            v = self.c.get(f"/v1/tasks/{task_id}").json()
            plan = v.get("plan") or {}
            line = (
                f"  {v['status']:<18} llm calls {v['llm_calls']:>3}  tool calls {v['tool_calls']:>3}  "
                + " ".join(f"{s['id']}:{s['status']}" for s in v.get("subtasks", []))
            )
            if line != last:
                say(line)
                last = line
            if plan and not getattr(self, "_plan_shown", False):
                self._plan_shown = True
                say("  plan:")
                for s in plan.get("subtasks", []):
                    say(
                        f"    {s['id']} ({s['specialist']}) <- {','.join(s['depends_on']) or '-'}: {s['description'][:90]}"
                    )
            if v["status"] == "awaiting_approval" and self.pending_approval(task_id) is None:
                pass  # a decision was just recorded; the worker has not resumed the task yet
            elif v["status"] in TERMINAL:
                return v
            time.sleep(8)
        raise SystemExit("gave up waiting for the task")

    def pending_approval(self, task_id: str) -> dict[str, Any] | None:
        for a in self.c.get("/v1/approvals", params={"status": "pending"}).json():
            if a["task_id"] == task_id:
                return dict(self.c.get(f"/v1/approvals/{a['id']}").json())
        return None

    def decide(self, approval: dict[str, Any], decision: str, new_to: str | None) -> None:
        args = dict(approval["proposed_action"].get("arguments") or {})
        payload: dict[str, Any] = {}
        reason = "demo: approved by the reviewer"
        if decision == "modify":
            args["to"] = new_to or "complaints@lender.example.test"
            payload = {"arguments": args}
            reason = "demo: routed to the complaints inbox"
        elif decision == "reject":
            reason = "demo: drafts only — a person sends complaint letters"
        r = self.c.post(
            f"/v1/approvals/{approval['id']}/decide",
            json={
                "decision": decision,
                "payload": payload,
                "reason": reason,
                "decided_by": "demo-reviewer",
            },
        )
        r.raise_for_status()
        say(f"  decision recorded: {r.json()['status']} (#{approval['id']})")

    def epilogue(self, task_id: str, user: str) -> None:
        v = self.c.get(f"/v1/tasks/{task_id}").json()
        fo = v.get("final_output") or {}
        say()
        say("=== Deliverable ===")
        say(f"  {fo.get('title', '')}")
        say("  " + (fo.get("body", "")[:700].replace(chr(10), chr(10) + "  ")) + " …")
        say()
        say("=== What actually happened to the email ===")
        sends = [t for t in v.get("tool_ledger", []) if t["tool"].startswith("actions_")]
        for t in sends:
            say(
                f"  ledger: {t['tool']} · gate {t['decision']} · executed {t['ok'] is not None} · {t['reason']}"
            )
        rows = [
            o
            for o in self.c.get("/v1/outbox", params={"limit": 50}).json()
            if o.get("task_id") == task_id
        ]
        for o in rows:
            say(
                f"  outbox #{o['id']}: {o['status']} to {o['payload'].get('to')} — a person sends it; the software cannot"
            )
        if not rows:
            say("  outbox: nothing queued (the send was rejected)")
        say()
        say("=== Lessons written to memory for this user ===")
        for m in self.c.get(f"/v1/memory/users/{user}").json():
            say(
                f"  [{m['task_type']}/{m['outcome']} · importance {m['importance']}] {m['text'][:160]}"
            )
        say()
        tr = self.c.get(f"/v1/tasks/{task_id}/trace").json()
        kinds: dict[str, int] = {}
        for s in tr.get("spans", []):
            kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
        say(
            f"=== Trace: {tr['span_count']} spans from {tr['source']} — "
            + ", ".join(f"{k} {n}" for k, n in sorted(kinds.items()))
        )
        stats = self.c.get("/v1/stats", params={"days": 1}).json()
        say(
            f"=== Today: {stats['tasks']['total']} tasks · completion {stats['tasks']['success_rate']} · "
            f"escalation rate {stats['approvals']['escalation_rate']} · unapproved destructive actions "
            f"{stats['safety']['unapproved_destructive_actions']} (must be 0)"
        )
        say()
        say(
            f"Console: http://localhost:8501 (Tasks → pick {task_id[:8]}…) · Jaeger: http://localhost:16686"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the Foreman showcase end to end")
    ap.add_argument("--user", default="demo")
    ap.add_argument("--request", default=SHOWCASE)
    ap.add_argument("--decision", default="modify", choices=["approve", "modify", "reject"])
    ap.add_argument("--to", default=None, help="recipient to substitute when --decision modify")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    s = get_settings()
    demo = Demo(s.api_base_url, s.api_key)
    demo.ensure_stack()
    demo.ensure_seed()
    say()
    say(f"Submitting as user {args.user!r}:")
    say(f"  {args.request}")
    task_id = demo.submit(args.user, args.request)
    say(f"  task {task_id}")
    say()
    say("Following the task (the office at work):")
    v = demo.follow(task_id)
    decided = 0
    while v["status"] == "awaiting_approval" and decided < 4:
        approval = demo.pending_approval(task_id)
        if approval is None:
            break
        action = approval.get("proposed_action") or {}
        say()
        say(f"=== The office stops and asks a human ({approval['level']} · {approval['kind']}) ===")
        if approval["kind"] == "tool_call":
            a = action.get("arguments", {})
            say(f"  tool: {action.get('tool')} ({action.get('risk')})")
            say(f"  to: {a.get('to')}")
            say(f"  subject: {a.get('subject')}")
            say("  body: " + str(a.get("body", ""))[:300].replace(chr(10), " ") + " …")
            say(f"  answering: {args.decision}")
            demo.decide(approval, args.decision, args.to)
        else:
            say(f"  {json.dumps(action)[:300]}")
            say("  answering: approve")
            demo.decide(approval, "approve", None)
        decided += 1
        v = demo.follow(task_id)
    say()
    say(
        f"Task finished with status: {v['status']}" + (f" — {v['error']}" if v.get("error") else "")
    )
    demo.epilogue(task_id, args.user)
    return 0 if v["status"] == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
