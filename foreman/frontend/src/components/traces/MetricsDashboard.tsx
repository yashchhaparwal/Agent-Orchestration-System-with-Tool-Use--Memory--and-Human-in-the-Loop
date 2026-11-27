// Operational stats and the last eval headline. Shown on the Stats page and in Traces → Analytics.
import { useState, type ReactNode } from 'react'
import { ShieldCheck, ShieldAlert } from 'lucide-react'
import clsx from 'clsx'
import { useStats } from '@/api/hooks'
import BarChart from '@/components/BarChart'
import { Empty, ErrorBox, Pill, shortTime } from '@/components/ui'

const percent = (v: number | null | undefined) =>
  v === null || v === undefined ? '—' : `${Math.round(v * 100)}%`
const num = (v: number | null | undefined, suffix = '') =>
  v === null || v === undefined ? '—' : `${v.toLocaleString(undefined, { maximumFractionDigits: 1 })}${suffix}`

function Tile({ label, value, hint, tone = 'text-slate-100' }: {
  label: string
  value: ReactNode
  hint?: string
  tone?: string
}) {
  return (
    <div className="stat-card">
      <p className="label">{label}</p>
      <p className={clsx('text-2xl font-bold tabular-nums', tone)}>{value}</p>
      {hint && <span className="text-xs text-slate-500">{hint}</span>}
    </div>
  )
}

function Counts({ title, rows }: { title: string; rows: Array<[string, number]> }) {
  return (
    <div className="glass p-6 space-y-3">
      <h3 className="text-sm font-bold text-white">{title}</h3>
      {rows.length === 0 ? (
        <p className="text-xs text-slate-500">Nothing in this window.</p>
      ) : (
        rows.map(([name, count]) => (
          <div key={name} className="flex items-center justify-between">
            <Pill value={name} />
            <span className="text-sm text-slate-200 tabular-nums">{count}</span>
          </div>
        ))
      )}
    </div>
  )
}

export default function MetricsDashboard() {
  const [days, setDays] = useState(7)
  const { data: s, error } = useStats(days)

  if (!s) return error ? <ErrorBox error={error} prefix="Could not load stats" /> : <Empty>Loading stats…</Empty>

  const { tasks, approvals, tools, safety, last_eval: ev } = s
  const unsafe = safety.unapproved_destructive_actions

  return (
    <div className="space-y-6">
      <ErrorBox error={error} prefix="Could not refresh stats" />
      <label className="flex items-center gap-3 text-xs text-slate-400">
        Window: last {days} day{days === 1 ? '' : 's'}
        <input
          type="range" min={1} max={30} value={days}
          className="w-56 accent-brand-500"
          onChange={(e) => setDays(Number(e.target.value))}
        />
      </label>

      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4">
        <Tile label="Tasks" value={tasks.total} />
        <Tile label="Completed" value={percent(tasks.success_rate)} hint="done / finished" />
        <Tile label="Mean LLM calls" value={num(tasks.mean_llm_calls)} hint="per completed task" />
        <Tile label="Escalation rate" value={percent(approvals.escalation_rate)} hint="tasks that paused for a human" />
        <Tile label="Approval rate" value={percent(approvals.approval_rate)} hint="approved + modified / decided" />
        <Tile
          label="Unapproved destructive"
          value={
            <span className="flex items-center gap-2">
              {unsafe ? <ShieldAlert className="h-5 w-5" /> : <ShieldCheck className="h-5 w-5" />}
              {unsafe}
            </span>
          }
          tone={unsafe ? 'text-danger-400' : 'text-success-400'}
          hint={unsafe ? 'investigate' : 'the gate held'}
        />
      </div>

      <p className="text-xs text-slate-500">
        Latency p50 {num(tasks.latency_p50_s, ' s')} · p95 {num(tasks.latency_p95_s, ' s')} ·
        mean tokens {num(tasks.mean_tokens)} · mean cost ${(tasks.mean_cost_usd ?? 0).toFixed(4)} ·
        {' '}{tools.total} tool calls, {tools.not_executed} not executed
      </p>

      <div className="space-y-3">
        <h2 className="text-sm font-bold text-white">Last eval run</h2>
        {!ev ? (
          <Empty>No eval report yet — run <code>uv run python -m packages.evals.runner --k 3</code></Empty>
        ) : (
          <>
            <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4">
              <Tile label="Task success" value={percent(ev.metrics.success_rate)} />
              <Tile label="pass^k" value={percent(ev.metrics.pass_k)} hint={`k = ${ev.k}`} />
              <Tile label="Tool P / R" value={`${percent(ev.metrics.tool_precision)} / ${percent(ev.metrics.tool_recall)}`} />
              <Tile label="Escalation P / R" value={`${percent(ev.metrics.escalation_precision)} / ${percent(ev.metrics.escalation_recall)}`} />
              <Tile label="Injection resistance" value={percent(ev.metrics.injection_resistance)} />
              <Tile label="Judge mean" value={num(ev.metrics.judge_mean, ' / 5')} />
            </div>
            <p className="text-xs text-slate-500">
              run {ev.run_id} {ev.label} · {ev.task_count} tasks · {ev.run_count} runs · finished{' '}
              {shortTime(ev.finished_at)} · vs baseline: {ev.diff_verdict ?? 'no baseline'}
            </p>
          </>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <BarChart
          title="Tasks per day" labelName="Day" valueName="Tasks" vertical
          rows={tasks.per_day.map((d) => ({ label: d.day, value: d.count }))}
        />
        <BarChart
          title="Approvals by level" labelName="Level" valueName="Approvals"
          rows={approvals.by_level.map((a) => ({ label: a.level, value: a.count }))}
        />
        <BarChart
          title="Tool calls by tool" labelName="Tool" valueName="Calls"
          rows={tools.by_tool.map((t) => ({ label: t.tool, value: t.count }))}
        />
        <div className="grid grid-cols-2 gap-6 content-start">
          <Counts title="Decisions" rows={approvals.by_status.map((a) => [a.status, a.count])} />
          <Counts title="Tasks by status" rows={tasks.by_status.map((t) => [t.status, t.count])} />
        </div>
      </div>
    </div>
  )
}
