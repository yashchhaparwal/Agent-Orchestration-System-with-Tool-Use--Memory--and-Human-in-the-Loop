// Turns Foreman's task views and flat span lists into what the Traces screens draw.
import type { BackendTaskStatus, TaskView, TraceStatus } from '@/types'
import type { RawSpan, TaskTrace, TraceSpan } from '@/types/trace'

export const TERMINAL: BackendTaskStatus[] = ['done', 'failed', 'cancelled']

const STATUS: Record<BackendTaskStatus, TraceStatus> = {
  queued: 'running',
  running: 'running',
  awaiting_approval: 'escalated',
  done: 'completed',
  failed: 'failed',
  cancelled: 'failed',
}

export function toTaskTrace(view: TaskView): TaskTrace {
  const end = TERMINAL.includes(view.status) ? new Date(view.updated_at).getTime() : Date.now()
  return {
    run_id: view.task_id,
    user_id: view.user_id,
    task_input: view.request,
    status: STATUS[view.status],
    backend_status: view.status,
    started_at: view.created_at,
    duration_ms: Math.max(end - new Date(view.created_at).getTime(), 0),
    total_tokens: view.tokens,
    total_cost_usd: view.cost_usd ?? 0,
    llm_calls: view.llm_calls,
    total_tool_calls: view.tool_calls,
    subtask_count: Math.max(view.subtasks.length, view.plan?.subtasks.length ?? 0),
    completed_subtasks: view.subtasks.filter((s) => s.status === 'accepted').length,
    escalated: view.approvals.length > 0,
    replayed_from: view.options.replay_of ?? undefined,
    final_output: view.final_output,
    error: view.error,
    outputs_by_subtask: Object.fromEntries(
      view.subtasks.map((s) => [s.id, s.result?.output ?? '']),
    ),
  }
}

const STEP_KINDS = new Set(['node', 'agent', 'memory', 'hitl'])

const num = (v: unknown) => (typeof v === 'number' ? v : Number(v) || 0)
const str = (v: unknown) => (v === undefined || v === null || v === '' ? undefined : String(v))

function nodeType(span: RawSpan, label: string): string {
  if (span.kind === 'hitl') return 'human_escalation'
  if (span.kind === 'agent') {
    const agent = str(span.attrs.agent) ?? label
    if (agent === 'code_exec') return 'code_specialist'
    if (agent === 'reviewer' || agent === 'supervisor') return agent
    return `${agent}_specialist`
  }
  if (label.startsWith('plan') || label.startsWith('approve_plan')) return 'supervisor'
  if (label.startsWith('review')) return 'reviewer'
  if (label.startsWith('synthesize')) return 'synthesis'
  return label.split(/[ .]/)[0]
}

/**
 * Jaeger gives a deep tree (nodes → agents → model, gate, tool and transport spans). The screens show
 * one box per step, so each model or tool span is credited to its nearest step ancestor. The
 * Postgres fallback timeline has no steps; there every call is shown on its own.
 */
export function toSteps(raw: RawSpan[], outputs: Record<string, string> = {}): TraceSpan[] {
  const byId = new Map(raw.map((s) => [s.id, s]))
  const hasSteps = raw.some((s) => STEP_KINDS.has(s.kind) && s.kind !== 'hitl')
  const isStep = (s: RawSpan) =>
    hasSteps ? STEP_KINDS.has(s.kind) : ['llm', 'tool', 'hitl'].includes(s.kind)

  const steps = new Map<string, TraceSpan>()
  for (const s of raw.filter(isStep).sort((a, b) => a.start_us - b.start_us)) {
    const label = s.name.replace(/^(node|agent)\./, '')
    const subtaskId = str(s.attrs.subtask_id) ?? str(s.attrs.subtask)
    steps.set(s.id, {
      span_id: s.id,
      name: s.name,
      agent_name: label,
      node_type: nodeType(s, label),
      subtask_id: subtaskId,
      // pausing for a human is recorded as an OTel error (GraphInterrupt) but is not a failure
      status: s.kind === 'hitl' ? 'escalated' : s.error ? 'failure' : 'ok',
      start_time_ms: s.start_us / 1000,
      duration_ms: Math.round(s.duration_us / 1000),
      tokens: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
      cost_usd: 0,
      attributes: s.kind === 'hitl'
        ? Object.fromEntries(
            Object.entries(s.attrs).filter(([k]) => k !== 'error' && !k.startsWith('otel.')),
          )
        : s.attrs,
      output: s.kind === 'agent' && subtaskId ? outputs[subtaskId] : undefined,
      tool_calls: [],
    })
  }

  for (const s of raw) {
    if (s.kind !== 'llm' && s.kind !== 'tool') continue
    let owner: RawSpan | undefined = s
    while (owner && !steps.has(owner.id)) {
      owner = owner.parent ? byId.get(owner.parent) : undefined
    }
    const step = owner && steps.get(owner.id)
    if (!step) continue
    if (s.kind === 'llm') {
      const input = num(s.attrs.input_tokens)
      const output = num(s.attrs.output_tokens)
      step.tokens.prompt_tokens += input
      step.tokens.completion_tokens += output
      step.tokens.total_tokens += input + output || num(s.attrs.tokens)
      step.cost_usd += num(s.attrs.cost_usd)
      step.model = str(s.attrs.model) ?? step.model
    } else {
      step.tool_calls.push({
        tool: str(s.attrs.tool) ?? s.name.replace(/^tool\./, ''),
        input: s.attrs,
        success: !s.error && s.attrs.ok !== false,
        latency_ms: Math.round(num(s.attrs.latency_ms) || s.duration_us / 1000),
      })
    }
  }

  return [...steps.values()]
}
