# Design — Foreman operator UI

Visual specification for the operator-facing screens (approval queue, approval detail, task list, trace explorer, memory browser, stats). v1 is Streamlit; the same tokens apply to the React v2 so the look does not change between them.

---

## 1. Principles

1. **Decide in under a minute.** The approval screen exists so a human can make a safe decision fast. Everything on it serves that.
2. **Colour means role.** The palette below is the same one used in `../diagrams/`. Supervisor is indigo, specialists cyan, reviewer amber, memory violet, human-in-the-loop rose, observability green. A reader who has seen the architecture diagram should recognise every screen.
3. **Status is never colour-only.** Every status has a label and, where relevant, an icon. Colour reinforces; it does not carry meaning alone.
4. **Dense but calm.** Operators scan lists; use tables with tight rows, generous whitespace between sections, one accent per card.

## 2. Tokens

### Colour

| Token | Light | Use |
|---|---|---|
| `bg` | `#FFFFFF` | page |
| `surface` | `#F8FAFC` | panels, table headers |
| `border` | `#CBD5E1` | dividers, card outlines |
| `text` | `#0F172A` | primary text |
| `muted` | `#475569` | secondary text, metadata |
| `supervisor` / `supervisor-bg` | `#4F46E5` / `#EEF2FF` | plan, synthesis |
| `specialist` / `specialist-bg` | `#0891B2` / `#ECFEFF` | research, analysis, writing, code_exec |
| `reviewer` / `reviewer-bg` | `#D97706` / `#FFFBEB` | verdicts, retries |
| `memory` / `memory-bg` | `#7C3AED` / `#F5F3FF` | recalled / written memories |
| `hitl` / `hitl-bg` | `#E11D48` / `#FFF1F2` | approvals, blocks, destructive risk |
| `observe` / `observe-bg` | `#059669` / `#ECFDF5` | spans, cost, success states |
| `code-bg` / `code-text` | `#0F172A` / `#E2E8F0` | prompts, JSON, tool args |

Dark mode (v2): swap `bg`→`#0B1220`, `surface`→`#111A2E`, `border`→`#27354D`, `text`→`#E2E8F0`, `muted`→`#94A3B8`; keep accents, lighten `*-bg` to 15 % alpha of the accent.

### Status colours (with labels)

| Status | Accent | Label / icon |
|---|---|---|
| queued | `muted` | "Queued" · ○ |
| running | `supervisor` | "Running" · ◔ |
| awaiting_approval | `hitl` | "Awaiting approval" · ⏸ |
| done | `observe` | "Done" · ✓ |
| failed | `hitl` | "Failed" · ✕ |
| cancelled | `muted` | "Cancelled" · — |

### Risk badges

`safe` → observe · `risky` → reviewer (amber) · `destructive` → hitl (rose). Always with the word.

### Typography

- UI: **Inter** (fallback Segoe UI, system-ui). Body 14 px / 1.5; table cells 13 px; section titles 16 px semibold; page title 22 px bold.
- Code, JSON, prompts, tool args: **JetBrains Mono** (fallback Consolas, monospace) 12.5 px on `code-bg`.
- Letter-spaced uppercase 12 px for panel labels (`MEMORY`, `TRIGGERS`), as in the diagrams.

### Spacing and shape

- 8 px grid. Card padding 16 px; section gap 24 px; page gutter 32 px.
- Radius: cards 10 px, badges 6 px, buttons 8 px.
- Borders 1 px `border`; accent cards get a 2 px left border in the role colour rather than a full outline.

## 3. Screens

### 3.1 Approval queue (`/approvals`)

Table, sorted by `expires_at` ascending; rows tinted `hitl-bg` when < 2 h to expiry.

Columns: Level badge (L1–L4) · Trigger · Task (truncated request) · Proposed action (tool name + one-line arg summary) · Agent · Created · Expires in · **Open**.

Above the table: three stat tiles — *Pending*, *Median time to decision (7 d)*, *Approval rate (7 d)*. Tiles use `surface` with an `hitl` accent on *Pending*.

### 3.2 Approval detail (`/approvals/{id}`) — the most important screen

Two-column layout, 60 / 40.

**Left — context, top to bottom**
1. Header: level badge, trigger, task id, agent, countdown to expiry.
2. *Request* (verbatim, `surface` card).
3. *Plan* — ordered subtasks as a compact list; done ones with ✓ in `observe`; the current one highlighted in `specialist-bg`.
4. *What has been done* — accordion per completed subtask: result summary, reviewer score badge, tools used.
5. *Proposed action* — `hitl-bg` card: tool name, arguments in a code block, the agent's stated reasoning in a quote block.
6. *Similar past decisions* — up to three `memory-bg` cards from tier 3 ("On 3 Sep you rejected send_email for this user: 'drafts only'").

**Right — decision, sticky**
- Four buttons stacked, full width, in this order: **Approve** (observe), **Modify** (supervisor; opens an editable args form), **Reject** (hitl; requires a reason field), **Take over** (hitl outline; opens a text area for the human's output).
- Reason field (required for Reject, optional otherwise).
- Below: *Trace* link (opens the span tree at this decision point) and *Cost so far*.
- v2: chat panel to question the agent before deciding.

Keyboard: `A` approve, `M` modify, `R` reject, `T` take over, `Esc` back to queue. Confirmation dialog only for Approve on `destructive`.

### 3.3 Task list (`/tasks`)

Filterable table: status, user, category, date. Columns: Task id · Request (truncated) · Status (badge + label) · Steps · Cost · Escalations · Duration · Created. Row click → task detail.

### 3.4 Task detail and trace explorer (`/tasks/{id}`)

- Header with status, cost, duration, and buttons *Open in Langfuse* and *Replay from…*.
- Plan panel (as on the approval screen).
- **Trace tree**: collapsible tree of spans. Node colour by kind — `node.*` supervisor/specialist by agent, `llm.call` supervisor-bg, `gate.decide` hitl-bg with the decision badge, `tool.*` surface, `memory.*` memory-bg, `hitl.*` hitl. Each row shows name, duration bar (relative to task), tokens/cost where applicable. Click → right drawer with attributes, prompt, and response in code blocks.
- Deliverable panel at the bottom.

### 3.5 Memory browser (`/memory`)

Search by user; table of records with importance bar, task type, outcome, created/last accessed, access count; expand to read the text; per-user **Delete all** with confirmation.

### 3.6 Stats (`/stats`)

Tiles: tasks/day, success rate (last eval), mean cost per task, escalation rate, approval rate, unapproved destructive actions (must read 0, in `observe` when 0, `hitl` otherwise). Charts: cost per task type (bar), escalations by trigger (bar), task latency p50/p95 (line). Charts follow the dataviz convention: one accent per series from the palette above, muted axes, labels on the marks not just legends.

## 4. Components (reusable)

`StatusBadge` · `RiskBadge` · `LevelBadge` · `StatTile` · `PlanList` · `SubtaskAccordion` · `ProposedActionCard` · `MemoryCard` · `DecisionPanel` · `TraceTree` · `SpanDrawer` · `CodeBlock` · `CountdownPill` · `ConfirmDialog`.

In Streamlit these are functions in `apps/review-ui/components/`; in React v2 they are components with the same names and props.

## 5. Accessibility

- Contrast ≥ 4.5:1 for text on every `*-bg`.
- All actions reachable by keyboard; focus rings visible (2 px `supervisor`).
- Status and risk always have text labels.
- Countdown and expiry states also announced in text ("expires in 1 h 20 m").

## 6. Out of scope for v1

Dark mode, animations beyond focus/hover, mobile layout, and the chat panel. The palette and tokens are fixed now so v2 does not restyle.
