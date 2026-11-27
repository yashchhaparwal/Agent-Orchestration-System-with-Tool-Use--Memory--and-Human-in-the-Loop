import { useReplayDiff } from '@/api/hooks'
import { GitCompare } from 'lucide-react'
import clsx from 'clsx'


interface DiffViewerProps {
  forkTaskId: string
  onClose: () => void
}

export default function DiffViewer({ forkTaskId, onClose }: DiffViewerProps) {
  const { data: diff, isLoading, error } = useReplayDiff(forkTaskId)

  if (isLoading) {
    return (
      <div className="glass p-12 text-center text-xs text-slate-400">
        Comparing replay <span className="font-mono text-brand-300">{forkTaskId}</span> with its original…
      </div>
    )
  }

  if (error || !diff || diff.error !== undefined) {
    return (
      <div className="glass p-6 text-center text-xs text-red-400">
        Could not compare runs: {error?.message || diff?.error || 'No diff data available.'}
      </div>
    )
  }

  const pairs: Array<[string, [unknown, unknown]]> = [
    ['Status', diff.status],
    ['LLM calls', diff.llm_calls],
    ['Tool calls', diff.tool_calls],
    ['Approvals', diff.approvals],
  ]

  return (
    <div className="space-y-6">
      {/* Summary Diff Header */}
      <div className="glass p-6 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <GitCompare className="h-5 w-5 text-brand-400" />
            <h2 className="text-base font-bold text-white">Replay Diff (original → replay)</h2>
          </div>
          <button onClick={onClose} className="text-xs text-slate-400 hover:text-white underline">
            Close Diff View
          </button>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 pt-2">
          {pairs.map(([label, [a, b]]) => (
            <div key={label} className="p-3 bg-surface-950/60 rounded-xl border border-white/5 text-xs">
              <span className="label">{label}</span>
              <p className={clsx('text-lg font-bold mt-1', a === b ? 'text-emerald-400' : 'text-amber-400')}>
                {String(a)} → {String(b)}
              </p>
            </div>
          ))}
        </div>
        <p className="text-xs text-slate-400">
          Deliverable {diff.deliverable_changed ? 'changed' : 'is identical'}:{' '}
          {diff.deliverable_title[0] ?? '—'} → {diff.deliverable_title[1] ?? '—'}
        </p>
      </div>

      <div className="glass p-6 space-y-3">
        <h3 className="text-xs font-bold text-white uppercase tracking-wider">Subtasks</h3>
        {Object.keys(diff.subtasks).length === 0 ? (
          <p className="text-xs text-slate-500">No subtasks recorded yet.</p>
        ) : (
          Object.entries(diff.subtasks).map(([id, s]) => (
            <div key={id} className="p-3 bg-surface-950 rounded-xl border border-white/10 flex items-center justify-between text-xs">
              <span className="font-bold text-brand-300 font-mono">{id}</span>
              <span className="text-slate-400">
                {s.status ? `${s.status[0]} → ${s.status[1]}` : ''}
                {s.attempt ? ` · attempt ${s.attempt[0]} → ${s.attempt[1]}` : ''}
              </span>
              <span className={s.change === 'same' ? 'text-emerald-400' : 'text-amber-400'}>{s.change}</span>
            </div>
          ))
        )}
        {Object.entries(diff.tools_by_name).map(([tool, [a, b]]) => (
          <p key={tool} className="text-xs text-slate-400 font-mono">{tool}: {a} → {b} calls</p>
        ))}
      </div>
    </div>
  )
}
