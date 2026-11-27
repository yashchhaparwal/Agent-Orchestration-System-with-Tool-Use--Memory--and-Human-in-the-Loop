import clsx from 'clsx'
import type { ReviewVerdict, TaskStatus, TraceStatus, AgentDomain, Complexity } from '@/types'

// ── Verdict badge ─────────────────────────────────────────────────────────────
const verdictStyles: Record<ReviewVerdict, string> = {
  approved: 'bg-success-500/15 text-success-400 border-success-500/25',
  revision: 'bg-warning-500/15 text-warning-400 border-warning-500/25',
  rejected: 'bg-danger-500/15 text-danger-400 border-danger-500/25',
}
export function VerdictBadge({ verdict }: { verdict: ReviewVerdict }) {
  const label = { approved: '✓ Approved', revision: '↺ Revision', rejected: '✕ Rejected' }[verdict]
  return (
    <span className={clsx('inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border', verdictStyles[verdict])}>
      {label}
    </span>
  )
}

// ── Status badge ──────────────────────────────────────────────────────────────
const statusStyles: Record<TaskStatus | TraceStatus, string> = {
  pending:   'bg-warning-500/15 text-warning-400 border-warning-500/25',
  running:   'bg-info-500/15 text-info-400 border-info-500/25',
  completed: 'bg-success-500/15 text-success-400 border-success-500/25',
  escalated: 'bg-brand-500/15 text-brand-300 border-brand-500/25',
  failed:    'bg-danger-500/15 text-danger-400 border-danger-500/25',
}
const statusDots: Record<TaskStatus | TraceStatus, string> = {
  pending:   'bg-warning-400',
  running:   'bg-info-400 animate-pulse',
  completed: 'bg-success-400',
  escalated: 'bg-brand-400',
  failed:    'bg-danger-400',
}
export function StatusBadge({ status }: { status: TaskStatus | TraceStatus }) {
  return (
    <span className={clsx('inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium border', statusStyles[status])}>
      <span className={clsx('h-1.5 w-1.5 rounded-full', statusDots[status])} />
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </span>
  )
}

// ── Specialist badge ──────────────────────────────────────────────────────────
const domainStyles: Record<AgentDomain, string> = {
  research:   'bg-cyan-500/15 text-cyan-300 border-cyan-500/20',
  analysis:   'bg-violet-500/15 text-violet-300 border-violet-500/20',
  writing:    'bg-emerald-500/15 text-emerald-300 border-emerald-500/20',
  code_exec:  'bg-orange-500/15 text-orange-300 border-orange-500/20',
  supervisor: 'bg-brand-500/15 text-brand-300 border-brand-500/20',
  reviewer:   'bg-pink-500/15 text-pink-300 border-pink-500/20',
}
const domainEmoji: Record<AgentDomain, string> = {
  research: '🔍', analysis: '📊', writing: '✍️', code_exec: '💻',
  supervisor: '🧠', reviewer: '🔍',
}
export function DomainBadge({ domain }: { domain: AgentDomain }) {
  return (
    <span className={clsx('inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium border', domainStyles[domain])}>
      <span>{domainEmoji[domain]}</span>
      {domain}
    </span>
  )
}

// ── Complexity badge ──────────────────────────────────────────────────────────
const complexityStyles: Record<Complexity, string> = {
  low:    'text-success-400',
  medium: 'text-warning-400',
  high:   'text-danger-400',
}
export function ComplexityIndicator({ level }: { level: Complexity }) {
  const bars = { low: 1, medium: 2, high: 3 }[level]
  return (
    <span className="inline-flex items-center gap-1">
      {[1, 2, 3].map((i) => (
        <span
          key={i}
          className={clsx(
            'h-3 w-1 rounded-sm transition-colors',
            i <= bars ? complexityStyles[level] : 'bg-surface-600',
            i <= bars ? 'opacity-100' : 'opacity-30',
          )}
          style={{ backgroundColor: i <= bars ? undefined : '#334155' }}
        />
      ))}
      <span className={clsx('text-xs ml-0.5', complexityStyles[level])}>{level}</span>
    </span>
  )
}

// ── Confidence bar ────────────────────────────────────────────────────────────
export function ConfidenceBar({ score }: { score: number }) {
  const pct = Math.round(score * 100)
  const color =
    pct >= 80 ? 'bg-success-400' :
    pct >= 60 ? 'bg-warning-400' :
                'bg-danger-400'
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 rounded-full bg-surface-600 overflow-hidden">
        <div
          className={clsx('h-full rounded-full transition-all duration-500', color)}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className={clsx('text-xs font-mono w-8 text-right tabular-nums', color)}>
        {pct}%
      </span>
    </div>
  )
}
