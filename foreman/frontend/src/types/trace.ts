import type { BackendTaskStatus, Deliverable, TraceStatus } from './index'

/** One task, summarised for the run list. Built from GET /v1/tasks/{id}. */
export interface TaskTrace {
  run_id: string
  user_id: string
  task_input: string
  status: TraceStatus
  backend_status: BackendTaskStatus
  started_at: string
  duration_ms: number
  total_tokens: number
  total_cost_usd: number
  llm_calls: number
  total_tool_calls: number
  subtask_count: number
  completed_subtasks: number
  escalated: boolean
  replayed_from?: string
  final_output: Deliverable | null
  error: string | null
  outputs_by_subtask: Record<string, string>
}

// ── Spans: GET /v1/tasks/{id}/trace ───────────────────────────────────────────

export interface RawSpan {
  id: string
  parent: string | null
  name: string
  kind: string
  start_us: number
  offset_us: number
  duration_us: number
  depth: number
  error: boolean
  attrs: Record<string, unknown>
}

export interface RawTrace {
  task_id: string
  source: 'jaeger' | 'ledger'
  span_count: number
  spans: RawSpan[]
}

export interface TokenUsage {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

export interface ToolCallRecord {
  tool: string
  input: Record<string, unknown>
  success: boolean
  latency_ms: number
}

/** One step of the run, with the model and tool calls made inside it rolled up. */
export interface TraceSpan {
  span_id: string
  name: string
  agent_name: string
  node_type: string
  subtask_id?: string
  model?: string
  status: 'ok' | 'failure' | 'escalated'
  start_time_ms: number
  duration_ms: number
  tokens: TokenUsage
  cost_usd: number
  attributes: Record<string, unknown>
  output?: string
  tool_calls: ToolCallRecord[]
}

// ── Aggregates: GET /v1/stats ─────────────────────────────────────────────────

export interface Stats {
  window_days: number
  last_eval: {
    run_id: string
    label: string | null
    finished_at: string
    k: number
    task_count: number
    run_count: number
    metrics: Record<string, number | null>
    diff_verdict: string | null
  } | null
  tasks: {
    total: number
    by_status: Array<{ status: string; count: number }>
    per_day: Array<{ day: string; count: number }>
    success_rate: number | null
    mean_llm_calls: number | null
    mean_tokens: number | null
    mean_cost_usd: number | null
    latency_p50_s: number | null
    latency_p95_s: number | null
  }
  approvals: {
    total: number
    by_level: Array<{ level: string; count: number }>
    by_status: Array<{ status: string; count: number }>
    escalation_rate: number | null
    approval_rate: number | null
  }
  tools: {
    total: number
    not_executed: number
    by_tool: Array<{ tool: string; count: number }>
  }
  safety: { unapproved_destructive_actions: number }
}

// ── Replay ────────────────────────────────────────────────────────────────────

export interface Checkpoint {
  checkpoint_id: string
  step: number | null
  produced_by: string[]
  next: string[]
}

export type TraceDiff =
  | { error: string; task_id: string }
  | {
      error?: undefined
      source_task_id: string
      fork_task_id: string
      status: [string, string]
      llm_calls: [number, number]
      tool_calls: [number, number]
      approvals: [number, number]
      tools_by_name: Record<string, [number, number]>
      subtasks: Record<
        string,
        { change: string; status?: [string, string]; attempt?: [number, number] }
      >
      deliverable_title: [string | null, string | null]
      deliverable_changed: boolean
    }
