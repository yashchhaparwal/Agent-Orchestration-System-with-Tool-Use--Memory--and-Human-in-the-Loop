import { useState } from 'react'
import { Send, Loader2 } from 'lucide-react'
import { useRunTask } from '@/api/hooks'
import { ErrorBox } from '@/components/ui'

/** Submit a new task. Used on the Tasks page and beside the review queue. */
export default function TaskForm({ onSubmitted }: { onSubmitted: (taskId: string) => void }) {
  const [task, setTask] = useState('')
  const [userId, setUserId] = useState('u_42')
  const [review, setReview] = useState(false)
  const { mutate, isPending, error } = useRunTask()

  return (
    <div className="space-y-4">
      <textarea
        className="input resize-none h-24 text-sm"
        placeholder="Describe a task for the agent system, e.g. 'Summarise the status of claim CLM-4471 and name the lender'"
        value={task}
        onChange={(e) => setTask(e.target.value)}
      />
      <div>
        <label className="label mb-1.5 block">User id (who is asking — memory is kept per user)</label>
        <input
          className="input text-xs"
          maxLength={64}
          value={userId}
          onChange={(e) => setUserId(e.target.value)}
        />
      </div>
      <label className="flex items-center gap-2 text-xs text-slate-400">
        <input type="checkbox" checked={review} onChange={(e) => setReview(e.target.checked)} />
        Require plan approval before any work (L3)
      </label>
      <button
        className="btn-primary w-full justify-center disabled:opacity-50"
        disabled={isPending || task.trim().length < 5}
        onClick={() => mutate(
          { request: task, user_id: userId.trim() || 'anonymous', require_human_review: review },
          { onSuccess: (res) => onSubmitted(res.data.task_id) },
        )}
      >
        {isPending ? <><Loader2 className="h-4 w-4 animate-spin" /> Submitting…</> : <><Send className="h-4 w-4" /> Submit Task</>}
      </button>
      <ErrorBox error={error} />
    </div>
  )
}
