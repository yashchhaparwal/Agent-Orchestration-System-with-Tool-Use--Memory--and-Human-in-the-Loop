import { useOutbox } from '@/api/hooks'
import { Empty, ErrorBox, JsonBlock, Page, Pill, shortTime } from '@/components/ui'

export default function OutboxPage() {
  const { data: rows = [], isLoading, error } = useOutbox()

  return (
    <Page
      title="Outbox"
      subtitle="Queued after human approval. Sending is a person's job — Foreman has no send path."
    >
      <ErrorBox error={error} prefix="Could not load the outbox" />
      {isLoading ? <Empty>Loading…</Empty> : !error && rows.length === 0 ? <Empty>Empty.</Empty> : (
        <div className="space-y-3">
          {rows.map((r) => (
            <details key={r.id} className="glass p-4">
              <summary className="cursor-pointer flex items-center gap-3 text-sm">
                <span className="font-bold text-white">#{r.id}</span>
                <span className="text-slate-200 font-mono">{r.kind}</span>
                <Pill value={r.status} />
                <span className="ml-auto text-xs text-slate-500">
                  {shortTime(r.created_at)} UTC · task {r.task_id?.slice(0, 8) ?? '—'}
                </span>
              </summary>
              <div className="mt-3"><JsonBlock value={r.payload} /></div>
            </details>
          ))}
        </div>
      )}
    </Page>
  )
}
