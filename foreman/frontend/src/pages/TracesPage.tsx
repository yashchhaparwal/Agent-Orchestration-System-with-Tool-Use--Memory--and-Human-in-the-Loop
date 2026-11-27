import { useState } from 'react'
import {
  GitBranch, ChevronRight, Clock, CheckCircle, XCircle, Zap,
  AlertTriangle, Search, Filter, BarChart2, RotateCcw,
  GitCompare, Coins, Cpu
} from 'lucide-react'
import { formatDistanceToNow } from 'date-fns'
import clsx from 'clsx'
import { StatusBadge } from '@/components/Badges'
import { useSearchParams } from 'react-router-dom'
import { useTraces, useTask, useTaskSpans } from '@/api/hooks'
import { toTaskTrace } from '@/api/adapters'
import SpanTree from '@/components/traces/SpanTree'

import type { TraceSpan } from '@/types/trace'
import TraceGraphView from '@/components/traces/TraceGraphView'
import MetricsDashboard from '@/components/traces/MetricsDashboard'
import ReplayModal from '@/components/traces/ReplayModal'
import DiffViewer from '@/components/traces/DiffViewer'

// ── Waterfall helper ─────────────────────────────────────────────────────────
const NODE_COLORS: Record<string, string> = {
  intake:              'bg-slate-500',
  supervisor:          'bg-brand-500',
  dispatch:            'bg-slate-400',
  research_specialist: 'bg-cyan-500',
  analysis_specialist: 'bg-violet-500',
  writing_specialist:  'bg-emerald-500',
  code_specialist:     'bg-amber-500',
  reviewer:            'bg-pink-500',
  synthesis:           'bg-brand-400',
  human_escalation:    'bg-purple-500',
}

function TraceWaterfall({ spans }: { spans: TraceSpan[] }) {
  const [hovered, setHovered] = useState<string | null>(null)
  if (spans.length === 0) return null
  const first = Math.min(...spans.map((s) => s.start_time_ms))
  const maxTime = Math.max(...spans.map((s) => s.start_time_ms + s.duration_ms - first), 1)

  return (
    <div className="space-y-1.5">
      {spans.map((span) => {
        const start = span.start_time_ms - first
        const leftPct  = Math.min((start / maxTime) * 100, 95)
        const widthPct = Math.min(Math.max((span.duration_ms / maxTime) * 100, 1.5), 100 - leftPct)
        const color = NODE_COLORS[span.node_type] ?? 'bg-slate-500'
        const isHovered = hovered === span.span_id

        return (
          <div
            key={span.span_id}
            className="flex items-center gap-3 group cursor-pointer"
            onMouseEnter={() => setHovered(span.span_id)}
            onMouseLeave={() => setHovered(null)}
          >
            <span className="w-44 text-xs text-slate-400 font-mono truncate text-right flex-shrink-0 group-hover:text-slate-200 transition-colors">
              {span.agent_name}
            </span>
            <div className="flex-1 h-6 bg-surface-700 rounded relative overflow-hidden">
              <div
                className={clsx(
                  'absolute h-full rounded transition-all duration-150',
                  color,
                  isHovered ? 'opacity-100 ring-1 ring-white' : 'opacity-75',
                )}
                style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
              />
            </div>
            <span className="w-20 text-xs text-slate-400 font-mono text-right flex-shrink-0">
              {span.duration_ms >= 1000
                ? `${(span.duration_ms / 1000).toFixed(1)}s`
                : `${span.duration_ms}ms`}
            </span>
          </div>
        )
      })}
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────
export default function TracesPage() {
  const [viewMode, setViewMode] = useState<'tree' | 'metrics' | 'diff'>('tree')
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<string>('all')
  // the open run lives in the URL, so other pages can link to it and a reload keeps it
  const [params, setParams] = useSearchParams()
  const selectedId = params.get('task')
  const setSelectedId = (id: string | null) => setParams(id ? { task: id } : {})

  const [replayId, setReplayId] = useState<string | null>(null)
  const [diffId, setDiffId] = useState<string | null>(null)

  const { data: traces = [], isLoading: tracesLoading, error } = useTraces()

  // a run older than the recent list (or a fork queued a moment ago) is fetched on its own
  const listed = traces.find((t) => t.run_id === selectedId)
  const single = useTask(!listed && selectedId ? selectedId : undefined)
  const selected = listed ?? (single.data && toTaskTrace(single.data))
  const replayTrace = traces.find((t) => t.run_id === replayId)
    ?? (selected?.run_id === replayId ? selected : undefined)
  const spans = useTaskSpans(selected)

  const query = search.trim().toLowerCase()
  const runsList = traces.filter((t) =>
    (statusFilter === 'all' || t.status === statusFilter) &&
    (!query || t.task_input.toLowerCase().includes(query) || t.run_id.includes(query)))

  const stats = {
    total: traces.length,
    completed: traces.filter(r => r.status === 'completed').length,
    escalated: traces.filter(r => r.escalated).length,
    totalCost: traces.reduce((sum, r) => sum + r.total_cost_usd, 0),
  }

  return (
    <div className="flex flex-col h-full bg-surface-950">
      {/* Page Header */}
      <div className="flex items-center justify-between px-8 py-5 border-b border-white/[0.06] bg-surface-900/60 backdrop-blur-xl">
        <div>
          <h1 className="text-xl font-bold text-white flex items-center gap-2">
            <GitBranch className="h-5 w-5 text-brand-400" /> Execution Traces & Observability
          </h1>
          <p className="text-xs text-slate-400 mt-0.5">OpenTelemetry spans, execution steps, costs, and checkpoint replay</p>
        </div>

        {/* View Mode Switcher */}
        <div className="flex items-center gap-1 bg-surface-800 p-1 rounded-xl border border-white/5">
          <button
            onClick={() => setViewMode('tree')}
            className={clsx(
              'px-3 py-1.5 rounded-lg text-xs font-medium transition-colors flex items-center gap-2',
              viewMode === 'tree' ? 'bg-brand-600 text-white shadow-glow-brand' : 'text-slate-400 hover:text-white'
            )}
          >
            <GitBranch className="h-3.5 w-3.5" /> Execution Graph
          </button>
          <button
            onClick={() => setViewMode('metrics')}
            className={clsx(
              'px-3 py-1.5 rounded-lg text-xs font-medium transition-colors flex items-center gap-2',
              viewMode === 'metrics' ? 'bg-brand-600 text-white shadow-glow-brand' : 'text-slate-400 hover:text-white'
            )}
          >
            <BarChart2 className="h-3.5 w-3.5" /> Aggregate Analytics
          </button>
          {diffId && (
            <button
              onClick={() => setViewMode('diff')}
              className={clsx(
                'px-3 py-1.5 rounded-lg text-xs font-medium transition-colors flex items-center gap-2',
                viewMode === 'diff' ? 'bg-brand-600 text-white shadow-glow-brand' : 'text-slate-400 hover:text-white'
              )}
            >
              <GitCompare className="h-3.5 w-3.5" /> Replay Diff
            </button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="max-w-7xl mx-auto px-8 py-6 space-y-6">

          {error && (
            <div className="glass p-4 border border-danger-500/20 text-xs text-danger-400">
              Could not load runs: {error.message}
            </div>
          )}

          {/* Aggregate Metrics Dashboard Tab */}
          {viewMode === 'metrics' && <MetricsDashboard />}

          {/* Replay Diff Tab */}
          {viewMode === 'diff' && diffId && (
            <DiffViewer forkTaskId={diffId} onClose={() => setViewMode('tree')} />
          )}

          {/* Visual Execution Tree & Runs List Tab */}
          {viewMode === 'tree' && (
            <>
              {/* Summary Stats Row */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                {[
                  { label: 'Recent Task Runs', value: stats.total, color: 'text-slate-100' },
                  { label: 'Completed', value: stats.completed, color: 'text-emerald-400' },
                  { label: 'Needed a Human', value: stats.escalated, color: 'text-purple-400' },
                  { label: 'Total Spend', value: `$${stats.totalCost.toFixed(4)}`, color: 'text-brand-300' },
                ].map(({ label, value, color }) => (
                  <div key={label} className="stat-card">
                    <p className="label">{label}</p>
                    <p className={clsx('text-3xl font-bold tabular-nums', color)}>{value}</p>
                  </div>
                ))}
              </div>

              {/* Selected Trace Graph Viewer */}
              {selected && (
                <div className="space-y-4">
                  <div className="flex items-center justify-between">
                    <button
                      onClick={() => setSelectedId(null)}
                      className="text-xs text-brand-400 hover:underline flex items-center gap-1 font-medium"
                    >
                      ← Back to all runs list
                    </button>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => setReplayId(selected.run_id)}
                        className="px-3 py-1.5 bg-surface-800 hover:bg-surface-700 text-xs font-bold text-white rounded-lg border border-white/10 flex items-center gap-1.5"
                      >
                        <RotateCcw className="h-3.5 w-3.5 text-brand-400" /> Replay from Checkpoint
                      </button>
                      {selected.replayed_from && (
                        <button
                          onClick={() => {
                            setDiffId(selected.run_id)
                            setViewMode('diff')
                          }}
                          className="px-3 py-1.5 bg-brand-600/30 hover:bg-brand-600/50 text-xs font-bold text-brand-300 rounded-lg border border-brand-500/30 flex items-center gap-1.5"
                        >
                          <GitCompare className="h-3.5 w-3.5" /> View Replay Diff
                        </button>
                      )}
                    </div>
                  </div>

                  {spans.data?.source === 'ledger' && (
                    <p className="text-xs text-warning-400">
                      Jaeger has no spans for this task — showing the timeline recorded in Postgres.
                    </p>
                  )}
                  {spans.error && (
                    <p className="text-xs text-danger-400">Could not load spans: {spans.error.message}</p>
                  )}

                  <TraceGraphView trace={selected} spans={spans.data?.spans ?? []} />

                  {selected.error && (
                    <div className="glass p-4 border border-danger-500/20 text-xs text-danger-400">
                      {selected.error}
                    </div>
                  )}
                  {selected.final_output && (
                    <div className="glass p-6 rounded-2xl space-y-2">
                      <p className="label">Deliverable</p>
                      <p className="text-sm font-bold text-white">{selected.final_output.title}</p>
                      <p className="text-xs text-slate-300 whitespace-pre-wrap leading-relaxed">
                        {selected.final_output.body}
                      </p>
                    </div>
                  )}

                  {/* Waterfall section */}
                  <div className="glass p-6 rounded-2xl space-y-3">
                    <p className="label">Step Waterfall Timeline</p>
                    <TraceWaterfall spans={spans.data?.spans ?? []} />
                  </div>

                  {spans.data && <SpanTree key={selected.run_id} spans={spans.data.raw} />}
                </div>
              )}

              {selectedId && !selected && (
                <div className="glass p-4 text-xs text-slate-400">
                  {single.error ? single.error.message : 'Loading run…'}{' '}
                  <button onClick={() => setSelectedId(null)} className="text-brand-400 hover:underline">
                    Back to all runs
                  </button>
                </div>
              )}

              {/* Run List & Filters */}
              {!selectedId && (
                <div className="space-y-4">
                  <div className="flex gap-3 flex-wrap items-center justify-between">
                    <div className="relative flex-1 min-w-48">
                      <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-slate-500" />
                      <input
                        className="input pl-9 text-sm"
                        placeholder="Search by task description or run ID…"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                      />
                    </div>
                    <div className="flex items-center gap-1 glass px-3 py-1 rounded-lg">
                      <Filter className="h-3.5 w-3.5 text-slate-500 mr-1" />
                      {['all', 'running', 'completed', 'escalated', 'failed'].map((s) => (
                        <button
                          key={s}
                          onClick={() => setStatusFilter(s)}
                          className={clsx(
                            'px-3 py-1 rounded-md text-xs font-medium transition-colors capitalize',
                            statusFilter === s
                              ? 'bg-brand-600 text-white'
                              : 'text-slate-400 hover:text-slate-200',
                          )}
                        >
                          {s}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="space-y-3">
                    {tracesLoading ? (
                      <div className="glass py-16 text-center text-xs text-slate-400">
                        Loading execution traces...
                      </div>
                    ) : runsList.length === 0 ? (
                      <div className="glass py-16 text-center space-y-3">
                        <GitBranch className="h-10 w-10 text-slate-600 mx-auto" />
                        <p className="text-sm font-semibold text-slate-300">No execution traces found</p>
                        <p className="text-xs text-slate-500">Run a task from the Review Queue to capture a trace.</p>
                      </div>
                    ) : (
                      runsList.map((run) => (
                        <div
                          key={run.run_id}
                          className="glass-hover text-left p-5 group flex flex-col md:flex-row md:items-center justify-between gap-4 cursor-pointer"
                          onClick={() => setSelectedId(run.run_id)}
                        >
                          <div className="flex items-start gap-4 flex-1 min-w-0">
                            <div className={clsx(
                              'h-10 w-10 rounded-xl flex items-center justify-center flex-shrink-0 mt-0.5',
                              run.status === 'completed' ? 'bg-emerald-500/15 text-emerald-400' :
                              run.status === 'escalated' ? 'bg-purple-500/15 text-purple-400' :
                              run.status === 'failed'    ? 'bg-red-500/15 text-red-400' : 'bg-brand-500/15 text-brand-400',
                            )}>
                              {run.status === 'completed' ? <CheckCircle className="h-5 w-5" /> :
                               run.status === 'escalated' ? <AlertTriangle className="h-5 w-5" /> :
                               run.status === 'failed'    ? <XCircle className="h-5 w-5" /> : <Zap className="h-5 w-5" />}
                            </div>

                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2">
                                <p className="text-sm font-bold text-white line-clamp-1 group-hover:text-brand-200 transition-colors">
                                  {run.task_input}
                                </p>
                                {run.replayed_from && (
                                  <span className="text-[10px] bg-brand-500/20 text-brand-300 px-1.5 py-0.5 rounded font-mono">
                                    replayed
                                  </span>
                                )}
                              </div>
                              <p className="text-xs text-slate-500 font-mono mt-0.5">Run ID: {run.run_id}</p>

                              {/* Telemetry metadata badges */}
                              <div className="flex items-center gap-4 mt-3 text-xs text-slate-400 flex-wrap">
                                <StatusBadge status={run.status} />
                                <span className="flex items-center gap-1">
                                  <Clock className="h-3 w-3 text-slate-500" />
                                  {`${(run.duration_ms / 1000).toFixed(1)}s`}
                                </span>
                                <span className="flex items-center gap-1 font-mono text-emerald-400">
                                  <Coins className="h-3 w-3" />
                                  ${run.total_cost_usd.toFixed(4)}
                                </span>
                                <span className="flex items-center gap-1">
                                  <Cpu className="h-3 w-3 text-brand-400" />
                                  {run.total_tokens} tokens
                                </span>
                                <span className="text-slate-500">
                                  {run.completed_subtasks}/{run.subtask_count} subtasks
                                </span>
                                <span className="text-slate-600 ml-auto">
                                  {formatDistanceToNow(new Date(run.started_at), { addSuffix: true })}
                                </span>
                              </div>
                            </div>
                          </div>

                          <div className="flex items-center gap-2 flex-shrink-0">
                            <button
                              onClick={(e) => {
                                e.stopPropagation()
                                setReplayId(run.run_id)
                              }}
                              className="p-2 rounded-lg bg-surface-800 hover:bg-surface-700 text-slate-300 hover:text-white transition-colors"
                              title="Replay this task"
                            >
                              <RotateCcw className="h-4 w-4" />
                            </button>
                            <ChevronRight className="h-4 w-4 text-slate-600 group-hover:text-brand-400 transition-colors" />
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              )}
            </>
          )}

        </div>
      </div>

      {/* Replay Modal */}
      {replayTrace && (
        <ReplayModal
          trace={replayTrace}
          onClose={() => setReplayId(null)}
          onReplayed={(forkId) => {
            setReplayId(null)
            setSelectedId(forkId)
            setDiffId(forkId)
            setViewMode('diff')
          }}
        />
      )}
    </div>
  )
}
