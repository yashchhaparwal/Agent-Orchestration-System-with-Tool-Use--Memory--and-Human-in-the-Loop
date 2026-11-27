// Small pieces shared by the console pages.
import type { ReactNode } from 'react'
import clsx from 'clsx'

type Tone = 'good' | 'warn' | 'bad' | 'info' | 'muted'

const TONES: Record<Tone, string> = {
  good:  'bg-success-500/15 text-success-400 border-success-500/25',
  warn:  'bg-warning-500/15 text-warning-400 border-warning-500/25',
  bad:   'bg-danger-500/15 text-danger-400 border-danger-500/25',
  info:  'bg-brand-500/15 text-brand-300 border-brand-500/25',
  muted: 'bg-white/[0.04] text-slate-400 border-white/10',
}

const STATUS_TONE: Record<string, Tone> = {
  queued: 'muted', planned: 'muted', cancelled: 'muted', expired: 'muted',
  running: 'info', modified: 'info',
  awaiting_approval: 'warn', pending: 'warn', partial: 'warn',
  done: 'good', accepted: 'good', approved: 'good', completed: 'good', safe: 'good',
  failed: 'bad', rejected: 'bad', destructive: 'bad',
  taken_over: 'info', risky: 'warn', queued_for_human: 'warn',
}

/** A status word on a tinted pill. The word carries the meaning; the colour only echoes it. */
export function Pill({ value, tone }: { value: string; tone?: Tone }) {
  return (
    <span className={clsx(
      'inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border whitespace-nowrap',
      TONES[tone ?? STATUS_TONE[value] ?? 'muted'],
    )}>
      {value.replace(/_/g, ' ')}
    </span>
  )
}

export function Page({ title, subtitle, actions, children }: {
  title: string
  subtitle: string
  actions?: ReactNode
  children: ReactNode
}) {
  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between gap-4 px-8 py-6 border-b border-white/[0.06]">
        <div>
          <h1 className="text-xl font-bold text-white">{title}</h1>
          <p className="text-sm text-slate-400 mt-0.5">{subtitle}</p>
        </div>
        {actions}
      </div>
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-6xl mx-auto px-8 py-6 space-y-6">{children}</div>
      </div>
    </div>
  )
}

export function ErrorBox({ error, prefix }: { error: Error | null | undefined; prefix?: string }) {
  if (!error) return null
  return (
    <p className="text-xs text-danger-400 bg-danger-500/10 rounded-lg p-3 border border-danger-500/20">
      {prefix ? `${prefix}: ` : ''}{error.message}
    </p>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="glass py-12 px-4 text-center text-sm text-slate-500">{children}</div>
}

export function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="code-block text-slate-300 whitespace-pre-wrap break-words">
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}

/** "2026-11-15T11:24:08.268+00:00" → "2026-11-15 11:24" (as the API sends it, UTC). */
export const shortTime = (iso: string | null | undefined) =>
  iso ? iso.slice(0, 16).replace('T', ' ') : '—'
