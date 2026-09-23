# Foreman, explained in plain words

This is the non-technical version of the project. If you can tell this story, you understand
Foreman. (The technical documents are `PRD.md`, `Architecture.md` and `Memory.md`.)

## The one picture: a small claims office

Imagine a small office that handles consumer-credit complaints.

| In the office | In Foreman | What it does |
|---|---|---|
| The **manager** | *supervisor* | Reads a request such as "review claim CLM-4360 and draft a complaint letter", splits it into jobs (look up the loans → assess affordability → write the letter → send it), decides which job needs which specialist, and at the end assembles the finished piece. |
| The **specialists** | *research, analysis, writing, code* agents | Each does one job at a time. They can only work through the tools in the cabinet. |
| The **locked cabinet of tools** | *MCP tool servers* | The database, the documents folder, a sandbox for running code, web search, and an email *outbox*. |
| The **security guard** | *permission gate* | Stands at the cabinet. Reading is fine. Writing a file needs a nod. Anything irreversible — sending an email, booking a meeting, calling an outside system — is **never done without a human's signature**. The guard also stops any specialist reaching for a tool it is not entitled to. |
| The **checker** | *reviewer* | A different AI model (on purpose — a different family, so it does not share the specialists' blind spots) reads every finished job, scores it, and sends sloppy work back with feedback, up to twice. After that it asks a person. |
| The **person with the pen** | *you, on the Approvals page* | Approve, change it, refuse, or take over and write it yourself. Four decisions, three places they happen (see below). |
| The **notebook** | *memory* | When a task ends, the office writes one to three lessons about that user ("the lender's file only states income as declared; verification cannot be done"). Before planning the next task for the same user, it reads the notebook back. Lessons fade if unused and are deleted when old. |
| The **inspector** | *eval harness* | A fixed set of 36 practice requests with known right answers, run through the whole office to measure: did it get the answer, did it use the right tools, did it stop when it should, did it ever act without a signature, did it fall for a planted trick document, how long, how much. |
| The **CCTV** | *traces* | Every step of every task is recorded with timings: which model answered, which tool ran, where it waited for you. You can look at any task as a timeline, and **replay** a task from any step with a change ("same job, different request wording") to see what changes. |

Everything else in the code is plumbing that makes those pieces reliable and visible.

## What happens when you submit a task

Use the request from the walkthrough:
*"Review claim CLM-4360: list its loans, assess affordability under the ruleset, draft a complaint letter to the lender, and send it by email to lender@example.test"*, as user `sayed`.

1. **Tasks page → Submit.** The task appears with status `running`. Within ~10 seconds the **plan** appears: A research, B analysis, C writing, D writing (the send), with arrows showing what depends on what.
2. **The specialists work.** Subtask rows say *in progress* or *waiting for B*. A finishes (2 attempts — the checker sent the first back), then B, then C. Each row, when opened, shows the specialist's output, its sources, and the checker's score and feedback.
3. **The office stops.** D wants to *send* the letter. The guard says: destructive, needs a human. The task status becomes `awaiting_approval` and a row appears on the **Approvals** page.
4. **You decide.** The Approvals page shows the *context package*: the request, the plan with progress, what has been done so far, and the exact email (to / subject / body). You can:
   - **Approve** — the email is queued as written.
   - **Modify** — edit the fields (in the walkthrough: change *to* to the complaints inbox). The edited version is what gets queued.
   - **Reject** — needs a reason; nothing is queued; the specialist is told and finishes without sending; it will not ask again for that tool in that job.
   - **Take over** — you write the result yourself; it is accepted without a model review.
5. **The task finishes.** Status `done`. The **deliverable** at the bottom of the Tasks page is the manager's assembled report: loans, the rule-by-rule assessment, the full letter, and an *email status* line.
6. **Outbox.** The queued email sits here as a row (`queued_for_human`) with the address you set. **Nothing is ever sent by the software** — there is no email account behind it. A person would send it.
7. **Memory.** The user `sayed` now has one to three lessons. Next time `sayed` submits a similar request, the plan is made with those lessons in front of the manager.
8. **Stats and Trace.** Stats shows the day's counts and the one number that must always be zero (*unapproved destructive actions*). Trace shows this task as a timeline; further down you can list its checkpoints and replay it from any of them into a new task.

## The three places a human is asked

| Level | When | Where you see it |
|---|---|---|
| **L2 — an action** | A specialist wants to do something irreversible (send, book, call out). | Approvals: *Approve action* |
| **L3 — the plan** | The manager is unsure of its plan (low confidence), or you ticked "Require plan approval" when submitting. | Approvals: *Approve plan* — before any work starts |
| **L4 — repeated failure** | The checker rejected the same job three times. | Approvals: *Escalation* — retry once more, supply the result yourself, take over the whole task, or cancel |

Timeouts can only reject or cancel. Nothing ever auto-approves.

## Things that confuse everyone at first

- **IDs.** A *task id* is a long UUID (`8d483c0a-…`). A *user id* is whatever name you typed (`u_42`, `sayed`). An *approval* is a small number (#25). A *checkpoint id* is another long string. You never need to type any of them: every page has a picker (Recent tasks, Users with memories, Checkpoints).
- **"planned" vs "waiting" vs "in progress".** A subtask is *waiting* while the ones it depends on are unfinished, *in progress* while a specialist works on it, then *accepted* or *rejected* by the checker.
- **The agent's words vs the truth.** A specialist may write "the letter was queued to lender@example.test" even after you changed the address — it never learns that a human edited its call. The **outbox** and the **ledger** (the Trace page, `tool.actions_send_email` with "modify by …") are the record of what actually happened.
- **"Sent".** Never. Queued, always.
- **Why some tasks take five minutes.** Everything runs on free model tiers ($0). When Mistral, Groq or Gemini throttle, the system waits and falls back to the next provider; the Trace page shows this as `llm.call` spans with a `fallback` tag.
- **The eval numbers look bad.** The first inspector's pass scored 3 of 7. It found two real bugs within an hour, which were fixed the same day. The two numbers that matter most were perfect: zero irreversible actions without a signature, and 100% resistance to the planted trick document. The inspector's job is to find problems; a perfect score on the first run would be suspicious.

## Starting everything (Windows)

From the `foreman` folder, in this order. Each of the last six is its own terminal.

```
docker compose -f infra/docker-compose.yml up -d              # Postgres, Redis, Chroma, Ollama, Jaeger
$env:MCP_PORT=7001; uv run python -m packages.tools.mcp_servers.web_search
$env:MCP_PORT=7002; uv run python -m packages.tools.mcp_servers.files
$env:MCP_PORT=7003; uv run python -m packages.tools.mcp_servers.sandbox
$env:MCP_PORT=7004; uv run python -m packages.tools.mcp_servers.database
$env:MCP_PORT=7005; uv run python -m packages.tools.mcp_servers.actions
uv run celery -A packages.orchestrator.worker worker --pool=solo -l info
uv run celery -A packages.orchestrator.worker beat -l info
uv run uvicorn apps.api.main:create_app --factory --port 8000
uv run streamlit run apps/review_ui/app.py --server.port 8501
```

Then open http://localhost:8501 (the console) and http://localhost:16686 (Jaeger, the raw traces). `uv run …` commands must be run *inside* the `foreman` folder — that is where the project's Python lives.

## The phases, in one line each

| Phase | Plain words | Status |
|---|---|---|
| 0–1 | keys, folders, one specialist that can use two tools | done |
| 2 | the manager, the checker, the whole assembly line | done |
| 3 | the locked cabinet and the guard | done |
| 4 | the pause-and-ask-a-human part | done |
| 5 | the notebook | done |
| 6 | the inspector, the CCTV, and replay | done (full scorecard runs overnight) |
| 7 | make it robust and one-command to start; measure cost | in progress |
| 8 | the demo recording and the numbers on the README | next |

## The sentence for an interview

*"I built a multi-agent system where a supervisor plans, specialists act only through a permission gate, a reviewer from a different model family checks their work, humans approve anything irreversible, results go to an outbox rather than being sent, the system keeps lessons per user, and an eval harness measures all of it — and I can show every task's trace and replay it from any step."*
