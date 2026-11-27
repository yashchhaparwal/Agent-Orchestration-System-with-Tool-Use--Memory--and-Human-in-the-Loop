import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { GitBranch, RefreshCw } from 'lucide-react'
import { useRecentTasks, useTask } from '@/api/hooks'
import TaskForm from '@/components/TaskForm'
import Markdown from '@/components/Markdown'
import { DomainBadge } from '@/components/Badges'
import { Empty, ErrorBox, JsonBlock, Page, Pill, shortTime } from '@/components/ui'
import type { AgentDomain, SubtaskView, TaskView } from '@/types'

const REFRESH_MS = 10_000

/** What the row is doing now, in words: a planned row is either waiting or in progress. */
function subtaskLabel(view: TaskView, sub: SubtaskView, accepted: Set<string>): string {
  const active = view.status === 'queued' || view.status === 'running'
  if (sub.status !== 'planned' || !active) return sub.status
  const spec = view.plan?.subtasks.find((p) => p.id === sub.id)
  const waiting = (spec?.depends_on ?? []).filter((d) => !accepted.has(d))
  return waiting.length ? `waiting for ${waiting.join(', ')}` : 'in progress'
}

function TaskDetail({ view }: { view: TaskView }) {
  const accepted = new Set(view.subtasks.filter((s) => s.status === 'accepted').map((s) => s.id))
  const output = view.final_output

  return (
    <div className="space-y-6">
      <div className="glass p-5 flex items-center gap-3 flex-wrap text-xs text-slate-400">
        <Pill value={view.status} />
        <span>user {view.user_id}</span>
        <span>LLM calls {view.llm_calls}</span>
        <span>tokens {view.tokens.toLocaleString()}</span>
        <span>tool calls {view.tool_calls} ({view.tool_calls_not_executed} not executed)</span>
        <span>cost {view.cost_usd === null ? '$0 (free tiers)' : `$${view.cost_usd.toFixed(4)}`}</span>
        <Link to={`/traces?task=${view.task_id}`} className="ml-auto flex items-center gap-1 text-brand-400 hover:underline">
          <GitBranch className="h-3.5 w-3.5" /> Open trace
        </Link>
      </div>

      {(view.status === 'queued' || view.status === 'running') && (
        <p className="text-xs text-slate-500">
          Working — a task with a send step usually pauses for approval after 2–5 minutes.
        </p>
      )}
      {view.pending_approval_id && (
        <div className="rounded-lg p-4 border bg-warning-500/10 border-warning-500/20 flex items-center justify-between gap-3">
          <p className="text-sm text-warning-300">Waiting on approval #{view.pending_approval_id}</p>
          <Link to={`/review?approval=${view.pending_approval_id}`} className="btn-primary">
            Review it
          </Link>
        </div>
      )}
      {view.error && <ErrorBox error={new Error(view.error)} />}

      <section className="space-y-2">
        <h2 className="label">Request</h2>
        <p className="glass p-5 text-sm text-slate-200 whitespace-pre-wrap">{view.request}</p>
      </section>

      {view.plan && (
        <section className="space-y-2">
          <h2 className="label">Plan · confidence {view.plan.confidence ?? '—'}</h2>
          <div className="glass p-5 space-y-2">
            {view.plan.subtasks.map((s) => (
              <p key={s.id} className="text-sm text-slate-300">
                <span className="font-bold text-white">{s.id}</span> ({s.specialist}) ←{' '}
                {s.depends_on?.join(', ') || '—'} — {s.description}
              </p>
            ))}
            {!!view.plan.sensitive_actions?.length && (
              <p className="text-xs text-warning-300">
                Sensitive actions: {view.plan.sensitive_actions.join(', ')}
              </p>
            )}
          </div>
        </section>
      )}

      {view.subtasks.length > 0 && (
        <section className="space-y-2">
          <h2 className="label">Subtasks</h2>
          {view.subtasks.map((s) => (
            <details key={s.id} className="glass p-4">
              <summary className="cursor-pointer flex items-center gap-3 flex-wrap text-sm">
                <span className="font-bold text-white">{s.id}</span>
                <DomainBadge domain={s.specialist as AgentDomain} />
                <span className="text-xs text-slate-400">attempt {s.attempt}</span>
                <Pill value={subtaskLabel(view, s, accepted)} />
                {s.verdict && <span className="text-xs text-slate-400">score {s.verdict.score}</span>}
                {s.result?.human_authored && <span className="text-xs text-brand-300">written by a human</span>}
              </summary>
              <div className="mt-4 space-y-3">
                {s.result ? (
                  <>
                    <Markdown>{s.result.output ?? ''}</Markdown>
                    <p className="text-[11px] text-slate-500">
                      Sources: {s.result.sources?.join(', ') || '—'} · tools:{' '}
                      {s.result.tools_used?.join(', ') || '—'}
                    </p>
                  </>
                ) : <p className="text-xs text-slate-500">No result yet.</p>}
                {s.verdict && (
                  <JsonBlock value={{
                    accept: s.verdict.accept,
                    score: s.verdict.score,
                    issues: s.verdict.issues,
                    feedback: s.verdict.feedback,
                    reviewer_model: s.verdict.reviewer_model,
                  }} />
                )}
              </div>
            </details>
          ))}
        </section>
      )}

      {view.approvals.length > 0 && (
        <section className="space-y-2">
          <h2 className="label">Approvals on this task</h2>
          <div className="glass p-4 overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="text-slate-500 border-b border-white/10">
                  {['id', 'kind', 'level', 'status', 'decision'].map((h) => (
                    <th key={h} className="py-1.5 pr-4 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {view.approvals.map((a) => (
                  <tr key={a.id}>
                    <td className="py-2 pr-4">
                      <Link to={`/review?approval=${a.id}`} className="text-brand-400 hover:underline">#{a.id}</Link>
                    </td>
                    <td className="py-2 pr-4 text-slate-300">{a.kind}</td>
                    <td className="py-2 pr-4 text-slate-300">{a.level}</td>
                    <td className="py-2 pr-4"><Pill value={a.status} /></td>
                    <td className="py-2 pr-4 text-slate-300">{a.decision ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {output && (
        <section className="glass p-6 space-y-3">
          <p className="label">Deliverable{output.human_authored ? ' · written by a human' : ''}</p>
          <h2 className="text-lg font-bold text-white">{output.title}</h2>
          <p className="text-[11px] text-slate-500">
            confidence {output.confidence} · sources: {output.sources.join(', ') || '—'}
          </p>
          <Markdown>{output.body}</Markdown>
          <details>
            <summary className="cursor-pointer text-xs text-slate-500">Raw JSON</summary>
            <div className="mt-2"><JsonBlock value={output} /></div>
          </details>
        </section>
      )}
    </div>
  )
}

export default function TasksPage() {
  const [params, setParams] = useSearchParams()
  const taskId = params.get('task') ?? ''
  const open = (id: string) => setParams(id ? { task: id } : {})

  const [typed, setTyped] = useState('')
  const [auto, setAuto] = useState(true)
  const recent = useRecentTasks()
  const task = useTask(taskId || undefined, auto ? REFRESH_MS : false)

  return (
    <Page title="Tasks" subtitle="Submit a task, follow it live, and read the deliverable">
      <details className="glass p-5" open={!taskId}>
        <summary className="cursor-pointer text-sm font-semibold text-white">Submit a new task</summary>
        <div className="mt-4"><TaskForm onSubmitted={open} /></div>
      </details>

      <div className="space-y-3">
        <ErrorBox error={recent.error} prefix="Could not list tasks" />
        <div>
          <label className="label mb-1.5 block">Recent tasks (newest first)</label>
          <select className="input" value={taskId} onChange={(e) => open(e.target.value)}>
            <option value="">Pick a task</option>
            {recent.data?.map((t) => (
              <option key={t.task_id} value={t.task_id}>
                {shortTime(t.created_at)} UTC · {t.status} · {t.user_id} · {t.request.slice(0, 60)}
              </option>
            ))}
          </select>
        </div>
        <form
          className="flex gap-3"
          onSubmit={(e) => { e.preventDefault(); if (typed.trim()) open(typed.trim()) }}
        >
          <input
            className="input flex-1 font-mono text-xs"
            placeholder="…or paste a task id"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
          />
          <button type="submit" className="btn-ghost border border-white/10">Open</button>
          <button
            type="button"
            className="btn-ghost border border-white/10"
            disabled={!taskId}
            onClick={() => task.refetch()}
          >
            <RefreshCw className="h-4 w-4" /> Refresh
          </button>
        </form>
        <label className="flex items-center gap-2 text-xs text-slate-400">
          <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
          Auto-refresh every {REFRESH_MS / 1000} s until the task finishes
        </label>
      </div>

      {!taskId ? <Empty>Submit a task above or pick one from Recent tasks.</Empty> : (
        <>
          {taskId && <p className="text-xs text-slate-500 font-mono">Task {taskId}</p>}
          <ErrorBox error={task.error} />
          {task.data ? <TaskDetail view={task.data} /> : task.isLoading && <Empty>Loading…</Empty>}
        </>
      )}
    </Page>
  )
}
