import { useState } from 'react'
import { CheckCircle, XCircle, RotateCcw, ChevronRight, Clock, AlertTriangle,
         MessageSquare, Inbox, Send, Loader2, ShieldAlert, Pencil,
         UserCheck, Brain, ListTree } from 'lucide-react'
import { formatDistanceToNow } from 'date-fns'
import clsx from 'clsx'
import { Link, useSearchParams } from 'react-router-dom'
import { useTask, useApprovals, useDecide } from '@/api/hooks'
import { DomainBadge, ConfidenceBar } from '@/components/Badges'
import TaskForm from '@/components/TaskForm'
import ArgumentsForm, { isSimple, type Arguments } from '@/components/ArgumentsForm'
import { Pill, shortTime } from '@/components/ui'
import type { AgentDomain } from '@/types'
import type {
  ApprovalRequest, ApprovalLevel, ApprovalStatus, DecisionKind, PlanSubtask,
} from '@/types/approval'
import { APPROVAL_LEVEL_META, TRIGGER_LABELS } from '@/types/approval'

const FILTERS: Array<ApprovalStatus | 'all'> = [
  'pending', 'approved', 'modified', 'rejected', 'taken_over', 'expired', 'all',
]

// The operator's name is recorded on every decision; remember it in this browser.
const NAME_KEY = 'foreman.operator'
const savedName = () => {
  try { return localStorage.getItem(NAME_KEY) ?? 'operator' } catch { return 'operator' }
}
const saveName = (name: string) => {
  try { localStorage.setItem(NAME_KEY, name) } catch { /* private window: not remembered */ }
}

const LEVEL_TONE: Record<'info' | 'warn' | 'danger', string> = {
  info: 'bg-brand-500/15 text-brand-300 border-brand-500/25',
  warn: 'bg-warning-500/15 text-warning-300 border-warning-500/25',
  danger: 'bg-danger-500/15 text-danger-300 border-danger-500/25',
}

const pretty = (value: unknown) => JSON.stringify(value ?? {}, null, 2)

/** One line that says what the reviewer is being asked to allow. */
function headline(r: ApprovalRequest): string {
  if (r.kind === 'tool_call') return `Run ${r.proposed_action.tool}`
  if (r.kind === 'plan') {
    return `Approve the execution plan (${r.proposed_action.plan?.subtasks.length ?? 0} subtasks)`
  }
  return r.proposed_action.reason || 'The task needs help'
}

const why = (r: ApprovalRequest) =>
  r.proposed_action.gate_reason || r.proposed_action.reason || r.reasoning

const isDestructive = (r: ApprovalRequest) => r.proposed_action.risk === 'destructive'

function LevelBadge({ level }: { level: ApprovalLevel }) {
  const meta = APPROVAL_LEVEL_META[level]
  return (
    <span className={clsx(
      'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-xs font-semibold',
      LEVEL_TONE[meta.tone],
    )}>
      {level === 'L4' ? <UserCheck className="h-3 w-3" /> : <AlertTriangle className="h-3 w-3" />}
      {level} · {meta.label}
    </span>
  )
}

// ── Detail panel ──────────────────────────────────────────────────────────────

type Tab = 'action' | 'plan' | 'results' | 'memory'
const TAB_LABELS: Record<Tab, string> = {
  action: 'Proposed action', plan: 'Plan', results: 'Done so far', memory: 'Memory',
}

function TaskDetailPanel({ request, onClose }: { request: ApprovalRequest; onClose: () => void }) {
  const ctx = request.context ?? {}
  const action = request.proposed_action ?? {}
  const plan: PlanSubtask[] = action.plan?.subtasks ?? ctx.plan ?? []
  const results = ctx.completed_subtasks ?? []
  const memories = ctx.memories ?? []

  const [tab, setTab] = useState<Tab>(request.kind === 'plan' ? 'plan' : 'action')
  const [reason, setReason] = useState('')
  const [operator, setOperator] = useState(savedName)
  const [editing, setEditing] = useState<'modify' | 'take_over' | null>(null)
  // flat tool arguments get one field each; anything nested is edited as JSON
  const fields = request.kind === 'tool_call' && isSimple(action.arguments)
  const [args, setArgs] = useState<Arguments>(() => (action.arguments ?? {}) as Arguments)
  const [json, setJson] = useState(() =>
    pretty(request.kind === 'plan' ? action.plan : action.arguments))
  const [subtaskId, setSubtaskId] = useState(action.rejected_subtasks?.[0] ?? '')
  const [output, setOutput] = useState('')
  const [title, setTitle] = useState('')
  const [inputError, setInputError] = useState('')

  const decide = useDecide()
  const busy = decide.isPending
  const meta = APPROVAL_LEVEL_META[request.level]
  const pending = request.status === 'pending'

  const submit = (decision: DecisionKind, payload: Record<string, unknown> = {}) => {
    setInputError('')
    decide.mutate(
      { id: request.id, decision, payload, reason, decided_by: operator.trim() || 'operator' },
      { onSuccess: onClose },
    )
  }

  const submitModify = () => {
    if (request.kind === 'escalation') {
      submit('modify', { subtask_id: subtaskId, output })
      return
    }
    if (fields) {
      submit('modify', { arguments: args })
      return
    }
    try {
      const parsed = JSON.parse(json)
      submit('modify', request.kind === 'plan' ? { plan: parsed } : { arguments: parsed })
    } catch (e) {
      setInputError(`Invalid JSON: ${(e as Error).message}`)
    }
  }

  const submitTakeOver = () =>
    submit('take_over', request.kind === 'tool_call'
      ? { output }
      : { title, body: output, sources: [] })

  const modifyReady = request.kind !== 'escalation' || (!!subtaskId.trim() && !!output.trim())
  const takeOverReady = !!output.trim() && (request.kind === 'tool_call' || !!title.trim())

  return (
    <div className="fixed inset-0 z-50 flex" onClick={onClose}>
      <div className="flex-1 bg-black/60 backdrop-blur-sm" />
      <div
        className="w-full max-w-2xl h-full bg-surface-900 border-l border-white/[0.06] flex flex-col overflow-hidden animate-slide-in"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between p-6 border-b border-white/[0.06]">
          <div className="min-w-0">
            <div className="flex items-center gap-2 mb-2">
              <LevelBadge level={request.level} />
            </div>
            <h2 className="text-base font-semibold text-white leading-snug">{headline(request)}</h2>
            <p className="text-xs text-slate-500 mt-1 truncate">
              Task: {request.task_id}{request.subtask_id ? ` · subtask ${request.subtask_id}` : ''}
            </p>
          </div>
          <button onClick={onClose} className="btn-ghost ml-4 p-1.5 rounded-lg flex-shrink-0">
            <XCircle className="h-4 w-4" />
          </button>
        </div>

        {/* Meta strip */}
        <div className="flex items-center gap-4 px-6 py-3 border-b border-white/[0.06] bg-surface-800/40">
          {request.agent && <DomainBadge domain={request.agent as AgentDomain} />}
          <div className="flex-1">
            {typeof ctx.plan_confidence === 'number' && (
              <div className="flex items-center gap-2">
                <span className="text-xs text-slate-500">Plan confidence</span>
                <div className="flex-1"><ConfidenceBar score={ctx.plan_confidence} /></div>
              </div>
            )}
          </div>
          <span className="text-xs text-slate-500 flex items-center gap-1 flex-shrink-0">
            <RotateCcw className="h-3 w-3" /> attempt {request.attempt}
          </span>
        </div>

        {/* Why we stopped */}
        <div className="mx-6 mt-4 flex-shrink-0">
          <div className={clsx(
            'flex gap-2.5 p-3 rounded-lg border',
            isDestructive(request)
              ? 'bg-danger-500/10 border-danger-500/20'
              : 'bg-warning-500/10 border-warning-500/20',
          )}>
            {isDestructive(request)
              ? <ShieldAlert className="h-4 w-4 text-danger-400 flex-shrink-0 mt-0.5" />
              : <AlertTriangle className="h-4 w-4 text-warning-400 flex-shrink-0 mt-0.5" />}
            <div className="min-w-0 space-y-1.5">
              <p className={clsx('text-xs leading-relaxed line-clamp-3',
                isDestructive(request) ? 'text-danger-300' : 'text-warning-300')}>
                {why(request)}
              </p>
              <p className="text-[11px] text-slate-500">{meta.blurb}</p>
              <span className="inline-block px-2 py-0.5 rounded-md bg-surface-800
                               border border-white/[0.06] text-[11px] text-slate-400">
                {TRIGGER_LABELS[request.trigger] ?? request.trigger}
              </span>
            </div>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 px-6 mt-4 border-b border-white/[0.06] flex-shrink-0">
          {(Object.keys(TAB_LABELS) as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={clsx(
                'px-3 py-2 text-xs font-medium rounded-t-lg border-b-2 transition-colors',
                tab === t ? 'border-brand-400 text-brand-300'
                          : 'border-transparent text-slate-500 hover:text-slate-300',
              )}
            >
              {TAB_LABELS[t]}
            </button>
          ))}
        </div>

        {/* Tab content — min-h-0 lets this shrink inside the flex column instead
            of the action block below squeezing it to zero height. */}
        <div className="flex-1 min-h-0 overflow-y-auto p-6">
          {tab === 'action' && (
            <div className="space-y-3">
              <div className="glass p-4">
                <p className="label mb-2">Original request</p>
                <p className="text-sm text-slate-300">{ctx.request}</p>
              </div>
              {request.kind === 'tool_call' && (
                <div className="flex items-center gap-2">
                  <span className="text-sm font-bold text-white font-mono">{action.tool}</span>
                  <Pill value={action.risk ?? 'n/a'} />
                </div>
              )}
              <pre className="code-block text-slate-300 whitespace-pre-wrap leading-relaxed">
                {pretty(request.kind === 'tool_call' ? action.arguments : action)}
              </pre>
              {!!action.other_pending?.length && (
                <p className="text-xs text-slate-500">
                  Also waiting after this: {action.other_pending.join(', ')}
                </p>
              )}
              {request.reasoning && (
                <p className="text-xs text-slate-500 leading-relaxed whitespace-pre-wrap">
                  Agent's reasoning: {request.reasoning}
                </p>
              )}
            </div>
          )}

          {tab === 'plan' && (
            plan.length > 0 ? (
              <div className="space-y-3">
                {action.plan?.rationale && (
                  <p className="text-xs text-slate-500 leading-relaxed">{action.plan.rationale}</p>
                )}
                {plan.map((st) => (
                  <div key={st.id} className="glass p-4 space-y-1.5">
                    <div className="flex items-center justify-between gap-2">
                      <span className="flex items-center gap-2 text-xs font-semibold text-slate-200">
                        <ListTree className="h-3.5 w-3.5 text-brand-400" />
                        {st.id}
                      </span>
                      <DomainBadge domain={st.specialist as AgentDomain} />
                    </div>
                    <p className="text-sm text-slate-300">{st.description}</p>
                    <p className="text-[11px] text-slate-500">
                      {[
                        st.status,
                        st.expected_output && `expects: ${st.expected_output}`,
                        st.depends_on?.length ? `after ${st.depends_on.join(', ')}` : '',
                      ].filter(Boolean).join(' · ')}
                    </p>
                  </div>
                ))}
              </div>
            ) : <p className="text-sm text-slate-500 text-center py-8">No plan was produced.</p>
          )}

          {tab === 'results' && (
            <div className="space-y-3">
              {results.length === 0 ? (
                <p className="text-sm text-slate-500 text-center py-8">No subtask has finished yet.</p>
              ) : results.map((r) => (
                <div key={r.id} className="glass p-4 space-y-2">
                  <p className="text-xs font-semibold text-slate-200">
                    {r.id} · attempt {r.attempt} · {r.status}
                    {r.score !== null && ` · score ${r.score}/5`}
                  </p>
                  <pre className="code-block text-slate-300 text-xs whitespace-pre-wrap">
                    {r.output_preview}
                  </pre>
                  {r.sources.length > 0 && (
                    <p className="text-[11px] text-slate-500">Sources: {r.sources.join(', ')}</p>
                  )}
                </div>
              ))}
            </div>
          )}

          {tab === 'memory' && (
            <div className="space-y-3">
              <p className="label">Memories that informed this run ({memories.length})</p>
              {memories.length === 0 ? (
                <p className="text-sm text-slate-500 text-center py-6">
                  No prior memories were retrieved for this task.
                </p>
              ) : memories.map((m, i) => (
                <div key={i} className="glass p-4">
                  <span className="flex items-center gap-2 text-xs font-semibold text-slate-200 mb-1.5">
                    <Brain className="h-3.5 w-3.5 text-brand-400" />
                    {String(m.task_type ?? 'memory')}
                    <span className="font-normal text-slate-500">
                      {String(m.outcome ?? '')}
                      {typeof m.score === 'number' && ` · relevance ${m.score.toFixed(2)}`}
                    </span>
                  </span>
                  <p className="text-xs text-slate-400 leading-relaxed">
                    {String(m.text ?? JSON.stringify(m))}
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="border-t border-white/[0.06] px-6 py-4 space-y-3 flex-shrink-0 max-h-[55%] overflow-y-auto">
          {(inputError || decide.error) && (
            <p className="text-xs text-danger-400 bg-danger-500/10 rounded-lg p-3 border border-danger-500/20">
              {inputError || decide.error?.message}
            </p>
          )}

          {!pending ? (
            <p className="text-xs text-slate-400">
              Already {request.status.replace('_', ' ')} by {request.decided_by ?? 'unknown'}
              {request.reason ? `: ${request.reason}` : ''}
            </p>
          ) : (
            <>
              {editing === 'modify' && (
                <div className="space-y-2">
                  {request.kind === 'escalation' ? (
                    <>
                      <input
                        className="input text-xs"
                        placeholder="Subtask id to provide output for"
                        value={subtaskId}
                        onChange={(e) => setSubtaskId(e.target.value)}
                      />
                      <textarea
                        className="input resize-y h-24 text-xs"
                        placeholder="Output for that subtask"
                        value={output}
                        onChange={(e) => setOutput(e.target.value)}
                      />
                    </>
                  ) : fields ? (
                    <ArgumentsForm value={args} onChange={setArgs} />
                  ) : (
                    <>
                      <label className="label block">
                        {request.kind === 'plan' ? 'Edited plan (JSON)' : 'Edited arguments (JSON)'}
                      </label>
                      <textarea
                        className="input resize-y h-40 text-xs font-mono"
                        value={json}
                        onChange={(e) => setJson(e.target.value)}
                      />
                    </>
                  )}
                  <button
                    className="btn-primary w-full justify-center"
                    disabled={busy || !modifyReady}
                    onClick={submitModify}
                  >
                    <Pencil className="h-4 w-4" /> Submit changes
                  </button>
                </div>
              )}

              {editing === 'take_over' && (
                <div className="space-y-2">
                  {request.kind !== 'tool_call' && (
                    <input
                      className="input text-xs"
                      placeholder="Deliverable title"
                      value={title}
                      onChange={(e) => setTitle(e.target.value)}
                    />
                  )}
                  <textarea
                    className="input resize-y h-24 text-xs"
                    placeholder={request.kind === 'tool_call'
                      ? 'Your result for this subtask (the agent stands down)'
                      : 'Write the final deliverable yourself (Markdown)'}
                    value={output}
                    onChange={(e) => setOutput(e.target.value)}
                  />
                  <button
                    className="btn-primary w-full justify-center"
                    disabled={busy || !takeOverReady}
                    onClick={submitTakeOver}
                  >
                    <UserCheck className="h-4 w-4" /> Submit my work
                  </button>
                </div>
              )}

              <div className="flex gap-3">
                <div className="flex-1">
                  <label className="label mb-1.5 block">
                    <MessageSquare className="h-3 w-3 inline mr-1" />
                    Reason (required to reject)
                  </label>
                  <input
                    className="input text-xs"
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                  />
                </div>
                <div className="w-36">
                  <label className="label mb-1.5 block">Your name</label>
                  <input
                    className="input text-xs"
                    maxLength={64}
                    value={operator}
                    onChange={(e) => { setOperator(e.target.value); saveName(e.target.value) }}
                  />
                </div>
              </div>

              <div className="grid grid-cols-4 gap-2">
                <button
                  className="btn-success justify-center"
                  disabled={busy}
                  onClick={() => submit('approve')}
                >
                  {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle className="h-4 w-4" />}
                  Approve
                </button>
                <button
                  className="btn-ghost justify-center border border-white/[0.08]"
                  disabled={busy}
                  onClick={() => setEditing(editing === 'modify' ? null : 'modify')}
                >
                  <Pencil className="h-4 w-4" /> Modify
                </button>
                <button
                  className="btn-ghost justify-center border border-white/[0.08]"
                  disabled={busy}
                  onClick={() => setEditing(editing === 'take_over' ? null : 'take_over')}
                >
                  <UserCheck className="h-4 w-4" /> Take over
                </button>
                <button
                  className="btn-danger justify-center"
                  disabled={busy || !reason.trim()}
                  onClick={() => submit('reject')}
                >
                  <XCircle className="h-4 w-4" /> Reject
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Run task panel ────────────────────────────────────────────────────────────
function RunTaskPanel({ onOpenApproval }: { onOpenApproval: (id: number) => void }) {
  const [taskId, setTaskId] = useState<string>()
  const { data: view } = useTask(taskId)
  const approvalId = view?.pending_approval_id

  return (
    <div className="glass p-6 space-y-4">
      <div className="flex items-center gap-2">
        <Send className="h-4 w-4 text-brand-400" />
        <h3 className="text-sm font-semibold text-white">Run a Task</h3>
      </div>
      <TaskForm onSubmitted={setTaskId} />
      {view && (
        <div className={clsx('rounded-lg p-3 border text-xs space-y-1',
          view.status === 'done' ? 'bg-success-500/10 border-success-500/20 text-success-300'
          : view.status === 'failed' || view.status === 'cancelled'
            ? 'bg-danger-500/10 border-danger-500/20 text-danger-300'
            : 'bg-warning-500/10 border-warning-500/20 text-warning-300',
        )}>
          <p className="font-semibold flex items-center gap-1.5">
            {(view.status === 'queued' || view.status === 'running') &&
              <Loader2 className="h-3 w-3 animate-spin" />}
            Task {view.status.replace('_', ' ')}
          </p>
          {view.subtasks.map((s) => (
            <p key={s.id} className="text-slate-400">{s.id} · {s.specialist} · {s.status}</p>
          ))}
          {view.error && <p className="text-danger-300">{view.error}</p>}
          <Link to={`/tasks?task=${view.task_id}`} className="block text-brand-300 hover:underline">
            Open task details →
          </Link>
          {approvalId && (
            <button className="btn-primary w-full justify-center mt-2" onClick={() => onOpenApproval(approvalId)}>
              Review approval #{approvalId}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function ReviewPage() {
  // the open approval lives in the URL, so the Tasks page can link straight to it
  const [params, setParams] = useSearchParams()
  const selectedId = Number(params.get('approval')) || null
  const setSelectedId = (id: number | null) => setParams(id ? { approval: String(id) } : {})
  const [filter, setFilter] = useState<ApprovalStatus | 'all'>('pending')

  const { data: all = [], isLoading, error } = useApprovals('all')

  const pending = all.filter((r) => r.status === 'pending')
  const resolved = all.filter((r) => r.status !== 'pending')
  const shown = filter === 'all' ? all : all.filter((r) => r.status === filter)
  const selected = all.find((r) => r.id === selectedId)

  const actions = pending.filter((r) => r.level === 'L2').length
  const plans = pending.filter((r) => r.level === 'L3' || r.level === 'L4').length

  const times = resolved
    .filter((r) => r.decided_at)
    .map((r) => new Date(r.decided_at!).getTime() - new Date(r.created_at).getTime())
  const avgReview = times.length
    ? `${(times.reduce((a, b) => a + b, 0) / times.length / 1000).toFixed(1)}s`
    : '—'

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-8 py-6 border-b border-white/[0.06]">
        <div>
          <h1 className="text-xl font-bold text-white">Review Queue</h1>
          <p className="text-sm text-slate-400 mt-0.5">Decide what the agents may not decide alone</p>
        </div>
        {pending.length > 0 && (
          <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full
                           bg-warning-500/15 text-warning-300 border border-warning-500/25 text-xs font-semibold">
            <AlertTriangle className="h-3.5 w-3.5" />
            {pending.length} pending
          </span>
        )}
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="max-w-6xl mx-auto px-8 py-6 grid grid-cols-1 xl:grid-cols-3 gap-6">
          {/* Stats */}
          <div className="xl:col-span-3 grid grid-cols-2 md:grid-cols-4 gap-4">
            {[
              { label: 'Pending', value: pending.length, color: 'text-warning-400', hint: `${actions} actions (L2) · ${plans} plans/escalations (L3, L4)` },
              { label: 'Approved', value: resolved.filter((r) => r.status === 'approved' || r.status === 'modified').length, color: 'text-success-400', hint: 'includes modified' },
              { label: 'Rejected', value: resolved.filter((r) => r.status === 'rejected').length, color: 'text-danger-400' },
              { label: 'Avg review time', value: avgReview, color: 'text-brand-300' },
            ].map(({ label, value, color, hint }) => (
              <div key={label} className="stat-card">
                <p className="label">{label}</p>
                <p className={clsx('text-3xl font-bold tabular-nums', color)}>{value}</p>
                {hint && <span className="text-xs text-slate-500">{hint}</span>}
              </div>
            ))}
          </div>

          {/* Queue */}
          <div className="xl:col-span-2 space-y-3">
            <div className="flex flex-wrap gap-1">
              {FILTERS.map((f) => (
                <button
                  key={f}
                  onClick={() => setFilter(f)}
                  className={clsx(
                    'px-3 py-1 rounded-md text-xs font-medium transition-colors capitalize',
                    filter === f ? 'bg-brand-600 text-white' : 'text-slate-400 hover:text-slate-200',
                  )}
                >
                  {f.replace('_', ' ')}
                </button>
              ))}
            </div>

            {error && (
              <div className="glass p-4 border border-danger-500/20">
                <p className="text-xs text-danger-400">
                  Could not load the queue: {error.message}
                </p>
              </div>
            )}

            {isLoading ? (
              <div className="glass flex items-center justify-center py-16">
                <Loader2 className="h-5 w-5 animate-spin text-slate-500" />
              </div>
            ) : shown.length === 0 ? (
              <div className="glass flex flex-col items-center justify-center py-16 text-center">
                <Inbox className="h-10 w-10 text-slate-600 mb-3" />
                <p className="text-sm font-medium text-slate-400">
                  {filter === 'pending' ? 'Queue is empty' : 'Nothing with this status'}
                </p>
                {filter === 'pending' && (
                  <p className="text-xs text-slate-600 mt-1">No runs are waiting on a human</p>
                )}
              </div>
            ) : (
              shown.map((r) => (
                <button
                  key={r.id}
                  className="w-full glass-hover text-left p-5 group"
                  onClick={() => setSelectedId(r.id)}
                >
                  <div className="flex items-start justify-between gap-3 mb-3">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-xs font-mono text-slate-500">#{r.id}</span>
                      <LevelBadge level={r.level} />
                      {r.agent && <DomainBadge domain={r.agent as AgentDomain} />}
                      <Pill value={r.status} />
                      {isDestructive(r) && (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md
                                         bg-danger-500/15 text-danger-300 border border-danger-500/25 text-[11px]">
                          <ShieldAlert className="h-3 w-3" /> destructive
                        </span>
                      )}
                    </div>
                    <ChevronRight className="h-4 w-4 text-slate-600 group-hover:text-brand-400 flex-shrink-0 transition-colors mt-0.5" />
                  </div>
                  <p className="text-sm font-medium text-white mb-1 line-clamp-2">{headline(r)}</p>
                  <p className="text-xs text-slate-500 mb-3 line-clamp-2">{r.context.request}</p>
                  <span className="text-xs text-slate-500 flex items-center gap-1 flex-wrap">
                    <Clock className="h-3 w-3" />
                    {formatDistanceToNow(new Date(r.created_at), { addSuffix: true })}
                    {' · '}{TRIGGER_LABELS[r.trigger] ?? r.trigger}
                    {' · task '}{r.task_id.slice(0, 8)}
                    {r.subtask_id && ` · subtask ${r.subtask_id}`}
                    {r.status === 'pending' && r.expires_at && ` · expires ${shortTime(r.expires_at)} UTC`}
                  </span>
                </button>
              ))
            )}
          </div>

          <div><RunTaskPanel onOpenApproval={setSelectedId} /></div>
        </div>
      </div>

      {selected && (
        <TaskDetailPanel key={selected.id} request={selected} onClose={() => setSelectedId(null)} />
      )}
    </div>
  )
}
