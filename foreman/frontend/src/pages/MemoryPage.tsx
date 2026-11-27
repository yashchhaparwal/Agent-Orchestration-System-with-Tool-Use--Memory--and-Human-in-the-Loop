import { useState } from 'react'
import { Brain, Trash2 } from 'lucide-react'
import clsx from 'clsx'
import { useSearchParams } from 'react-router-dom'
import { useMemoryUsers, useMemories, useDeleteMemories } from '@/api/hooks'
import { Empty, ErrorBox, Page, Pill, shortTime } from '@/components/ui'

/** Delete-all for one user. Keyed by user id, so the confirmation never carries over. */
function DeleteAll({ userId }: { userId: string }) {
  const [confirmed, setConfirmed] = useState(false)
  const remove = useDeleteMemories()

  return (
    <div className="glass p-5 space-y-3 border border-danger-500/20">
      <label className="flex items-center gap-2 text-xs text-slate-300">
        <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
        Yes, delete every memory for {userId}
      </label>
      <button
        className="btn-danger w-full justify-center disabled:opacity-50"
        disabled={!confirmed || remove.isPending}
        onClick={() => remove.mutate(userId, { onSuccess: () => setConfirmed(false) })}
      >
        <Trash2 className="h-4 w-4" /> Delete all for this user
      </button>
      <ErrorBox error={remove.error} />
      {remove.data && (
        <p className="text-xs text-success-400">
          Deleted {remove.data.deleted} record(s) for {remove.data.user_id}.
        </p>
      )}
    </div>
  )
}

export default function MemoryPage() {
  const [params, setParams] = useSearchParams()
  const userId = params.get('user') ?? 'u_42'
  const select = (id: string) => setParams({ user: id }, { replace: true })

  const users = useMemoryUsers()
  const { data: rows = [], isLoading, error } = useMemories(userId.trim())

  return (
    <Page
      title="Memory"
      subtitle="Lessons from past tasks, recalled for the same user. Unused lessons fade and expire on the nightly pass."
    >
      <div className="space-y-3">
        <ErrorBox error={users.error} prefix="Could not list users" />
        <div className="flex flex-wrap gap-2">
          {users.data?.map((u) => (
            <button
              key={u.user_id}
              onClick={() => select(u.user_id)}
              className={clsx(
                'px-3 py-1.5 rounded-lg text-xs border transition-colors',
                u.user_id === userId
                  ? 'bg-brand-600 border-brand-500 text-white'
                  : 'border-white/10 text-slate-400 hover:text-white',
              )}
            >
              {u.user_id} · {u.count} lesson{u.count === 1 ? '' : 's'}
            </button>
          ))}
        </div>
        <div>
          <label className="label mb-1.5 block">User id</label>
          <input className="input" maxLength={64} value={userId} onChange={(e) => select(e.target.value)} />
        </div>
      </div>

      <ErrorBox error={error} />
      {isLoading ? <Empty>Loading memories…</Empty>
        : !error && rows.length === 0 ? (
          <Empty>No memories for “{userId}” yet — finish a task for this user and come back.</Empty>
        ) : (
          <div className="space-y-3">
            {rows.map((m) => (
              <div key={m.id} className="glass p-5 space-y-2">
                <div className="flex items-center gap-2 flex-wrap">
                  <Brain className="h-4 w-4 text-brand-400" />
                  <span className="text-sm font-semibold text-white">{m.task_type}</span>
                  <Pill value={m.outcome} />
                  <span className="ml-auto text-xs text-slate-300 tabular-nums">
                    importance {m.effective_importance.toFixed(2)} (stored {m.importance})
                  </span>
                </div>
                <p className="text-sm text-slate-300 leading-relaxed">{m.text}</p>
                <p className="text-[11px] text-slate-500">
                  created {shortTime(m.created_at)} · last used {shortTime(m.last_accessed)} ·
                  {' '}{m.access_count} uses · from task {m.source_task_id.slice(0, 8) || '—'} ·
                  tools: {m.tools_used.join(', ') || '—'} · id {m.id}
                </p>
              </div>
            ))}
          </div>
        )}

      {rows.length > 0 && <DeleteAll key={userId} userId={userId.trim()} />}
    </Page>
  )
}
