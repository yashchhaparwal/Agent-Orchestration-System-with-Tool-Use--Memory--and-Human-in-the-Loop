// Every span of a task as an indented tree with proportional duration bars, plus an inspector.
import { useState } from 'react'
import clsx from 'clsx'
import { JsonBlock } from '@/components/ui'
import type { RawSpan } from '@/types/trace'

function note(span: RawSpan): string {
  const a = span.attrs
  const hint = a.model ?? a.tool ?? a.decision
    ?? (a.count !== undefined ? `${a.count} memories` : a.status)
  return String(hint ?? '')
}

export default function SpanTree({ spans }: { spans: RawSpan[] }) {
  const [showMcp, setShowMcp] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const visible = spans.filter((s) => showMcp || s.kind !== 'mcp')
  const total = Math.max(...spans.map((s) => s.offset_us + s.duration_us), 1)
  const selected = spans.find((s) => s.id === selectedId)

  return (
    <div className="glass p-6 rounded-2xl space-y-3">
      <div className="flex items-center justify-between gap-3">
        <p className="label">All Spans ({visible.length}) — click one to inspect it</p>
        <label className="flex items-center gap-2 text-xs text-slate-400">
          <input type="checkbox" checked={showMcp} onChange={(e) => setShowMcp(e.target.checked)} />
          Show MCP transport spans
        </label>
      </div>

      <div className="max-h-96 overflow-auto">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="text-slate-500 border-b border-white/10">
              {['span', 'kind', 'start', 'duration', '', 'note'].map((h, i) => (
                <th key={i} className="py-1.5 pr-3 font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {visible.map((s) => (
              <tr
                key={s.id}
                onClick={() => setSelectedId(s.id)}
                className={clsx(
                  'cursor-pointer hover:bg-white/[0.04]',
                  s.id === selectedId && 'bg-brand-600/20',
                )}
              >
                <td className="py-1 pr-3 font-mono text-slate-200 whitespace-nowrap">
                  <button style={{ paddingLeft: s.depth * 14 }}>{s.name}</button>
                </td>
                <td className="py-1 pr-3 text-slate-500">{s.kind}</td>
                <td className="py-1 pr-3 text-slate-400 tabular-nums">+{(s.offset_us / 1e6).toFixed(2)}s</td>
                <td className="py-1 pr-3 text-slate-400 tabular-nums">
                  {s.duration_us ? `${Math.round(s.duration_us / 1000)} ms` : ''}
                </td>
                <td className="py-1 pr-3 w-32">
                  {s.duration_us > 0 && (
                    <div
                      className="h-2 rounded-r bg-brand-400"
                      style={{ width: `${Math.max((s.duration_us / total) * 100, 2)}%` }}
                    />
                  )}
                </td>
                <td className="py-1 text-slate-400 max-w-56 truncate">
                  {note(s)}{s.error && s.kind !== 'hitl' ? ' ⚠ error' : ''}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selected && (
        <div className="space-y-2">
          <p className="text-xs font-semibold text-slate-300 font-mono">
            {selected.name} · {selected.kind}
          </p>
          <div className="max-h-72 overflow-auto"><JsonBlock value={selected.attrs} /></div>
        </div>
      )}
    </div>
  )
}
