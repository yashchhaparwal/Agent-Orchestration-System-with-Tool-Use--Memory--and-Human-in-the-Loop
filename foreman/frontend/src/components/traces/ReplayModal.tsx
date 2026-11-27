import { useState } from 'react'
import { Play, RotateCcw, XCircle, Edit3 } from 'lucide-react'
import { useCheckpoints, useReplayTask } from '@/api/hooks'
import type { TaskTrace } from '@/types/trace'

interface ReplayModalProps {
  trace: TaskTrace
  onClose: () => void
  onReplayed: (forkTaskId: string) => void
}

/** "key=value" lines; values are parsed as JSON when they can be. */
function parseOverrides(text: string): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const line of text.split('\n').map((l) => l.trim()).filter(Boolean)) {
    const at = line.indexOf('=')
    if (at < 1) throw new Error(`Override must look like key=value: ${line}`)
    const raw = line.slice(at + 1)
    try {
      out[line.slice(0, at).trim()] = JSON.parse(raw)
    } catch {
      out[line.slice(0, at).trim()] = raw
    }
  }
  return out
}

export default function ReplayModal({ trace, onClose, onReplayed }: ReplayModalProps) {
  const [checkpointId, setCheckpointId] = useState('')
  const [overrides, setOverrides] = useState('')
  const [inputError, setInputError] = useState('')
  const checkpoints = useCheckpoints(trace.run_id)
  const replay = useReplayTask()

  const handleReplay = () => {
    setInputError('')
    try {
      replay.mutate(
        { taskId: trace.run_id, checkpointId, overrides: parseOverrides(overrides) },
        { onSuccess: (res) => onReplayed(res.task_id) },
      )
    } catch (e) {
      setInputError((e as Error).message)
    }
  }

  const error = inputError || replay.error?.message || checkpoints.error?.message

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm animate-fade-in" onClick={onClose}>
      <div
        className="w-full max-w-2xl bg-surface-900 border border-white/10 rounded-2xl shadow-2xl flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/10 bg-surface-800/50">
          <div className="flex items-center gap-2">
            <RotateCcw className="h-5 w-5 text-brand-400" />
            <h2 className="text-base font-bold text-white">Replay from a Checkpoint</h2>
          </div>
          <button onClick={onClose} className="p-1 text-slate-400 hover:text-white">
            <XCircle className="h-5 w-5" />
          </button>
        </div>

        <div className="p-6 space-y-5">
          <div className="space-y-2">
            <label className="text-xs font-semibold text-slate-300">Fork from:</label>
            <select
              className="w-full p-3 bg-surface-950 border border-white/10 rounded-xl text-xs font-mono text-slate-200 focus:outline-none focus:border-brand-500"
              value={checkpointId}
              onChange={(e) => setCheckpointId(e.target.value)}
            >
              <option value="">
                {checkpoints.isLoading ? 'Loading checkpoints…' : 'Pick a checkpoint'}
              </option>
              {checkpoints.data?.map((c) => (
                <option key={c.checkpoint_id} value={c.checkpoint_id}>
                  step {c.step} · after {c.produced_by.join(', ') || 'start'} → next {c.next.join(', ') || 'END'}
                </option>
              ))}
            </select>
          </div>

          <div className="space-y-2">
            <label className="text-xs font-semibold text-slate-300 flex items-center gap-2">
              <Edit3 className="h-3.5 w-3.5 text-brand-400" /> Overrides (optional, one key=value per line):
            </label>
            <textarea
              className="w-full h-24 p-3 bg-surface-950 border border-white/10 rounded-xl text-xs font-mono text-slate-200 focus:outline-none focus:border-brand-500"
              placeholder="options.require_human_review=true"
              value={overrides}
              onChange={(e) => setOverrides(e.target.value)}
            />
          </div>

          <div className="p-4 bg-surface-950/60 rounded-xl border border-white/5 space-y-2 text-xs text-slate-400">
            <span className="font-bold text-slate-200">Replay behaviour:</span>
            <ul className="list-disc pl-4 space-y-1">
              <li>Forks the run at the chosen checkpoint into a new task; the original is untouched.</li>
              <li>Approvals raised during a replay are answered automatically with “approve”.</li>
              <li>The comparison fills in as the new task runs.</li>
            </ul>
          </div>

          {error && (
            <div className="p-3 bg-red-500/10 border border-red-500/20 text-red-400 text-xs rounded-lg">
              {error}
            </div>
          )}

          <div className="flex justify-end gap-3 pt-2">
            <button onClick={onClose} className="px-4 py-2 text-xs text-slate-400 hover:text-white">
              Cancel
            </button>
            <button
              onClick={handleReplay}
              disabled={replay.isPending || !checkpointId}
              className="px-5 py-2.5 bg-brand-600 hover:bg-brand-500 text-white font-bold text-xs rounded-xl flex items-center gap-2 disabled:opacity-50 shadow-glow-brand"
            >
              <Play className="h-3.5 w-3.5" />
              {replay.isPending ? 'Queuing…' : 'Run Replay'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
