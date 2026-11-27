// ── Task types — mirror what GET /v1/tasks returns (packages/shared/types) ───

export type AgentDomain =
  | 'research' | 'analysis' | 'writing' | 'code_exec' | 'supervisor' | 'reviewer'
export type Complexity  = 'low' | 'medium' | 'high'
export type ReviewVerdict = 'approved' | 'revision' | 'rejected'
export type TaskStatus   = 'pending' | 'running' | 'completed' | 'escalated' | 'failed'
export type TraceStatus  = 'running' | 'completed' | 'escalated' | 'failed'

export type BackendTaskStatus =
  | 'queued' | 'running' | 'awaiting_approval' | 'done' | 'failed' | 'cancelled'

export interface TaskSummary {
  task_id: string
  user_id: string
  status: BackendTaskStatus
  created_at: string
  request: string
  error: string | null
}

export interface Deliverable {
  title: string
  body: string
  sources: string[]
  confidence: number
  human_authored?: boolean
}

export interface ToolLedgerEntry {
  subtask_id: string
  tool: string
  risk: string | null
  decision: string
  ok: boolean | null
  reason: string
  at: string | null
}

export interface SubtaskView {
  id: string
  specialist: string
  status: string
  attempt: number
  result: {
    output?: string
    sources?: string[]
    tools_used?: string[]
    human_authored?: boolean
  } | null
  verdict: {
    accept?: boolean
    score?: number
    issues?: string[]
    feedback?: string
    reviewer_model?: string
  } | null
}

export interface TaskView {
  task_id: string
  user_id: string
  request: string
  status: BackendTaskStatus
  plan: {
    subtasks: Array<{ id: string; specialist: string; description: string; depends_on?: string[] }>
    confidence?: number
    sensitive_actions?: string[]
  } | null
  subtasks: SubtaskView[]
  final_output: Deliverable | null
  cost_usd: number | null
  llm_calls: number
  tokens: number
  tool_calls: number
  tool_calls_not_executed: number
  options: { replay_of?: string | null }
  tool_ledger: ToolLedgerEntry[]
  approvals: Array<{
    id: number
    kind: string
    level: string
    status: string
    decision: string | null
  }>
  pending_approval_id: number | null
  error: string | null
  created_at: string
  updated_at: string
}

export interface RunTaskRequest {
  request: string
  user_id: string
  require_human_review: boolean
}

// ── Outbox: GET /v1/outbox — proposed actions; nothing here has been sent ────

export interface OutboxEntry {
  id: number
  task_id: string | null
  kind: string
  payload: Record<string, unknown>
  status: string
  created_at: string
}

// ── Long-term memory: GET /v1/memory ─────────────────────────────────────────

export interface MemoryUser {
  user_id: string
  count: number
}

export interface StoredMemory {
  id: string
  user_id: string
  text: string
  task_type: string
  outcome: string
  importance: number
  effective_importance: number
  created_at: string
  last_accessed: string
  access_count: number
  tools_used: string[]
  source_task_id: string
}
