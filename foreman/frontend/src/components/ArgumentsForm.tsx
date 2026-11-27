// One input per tool argument, for editing a proposed call without touching JSON.

export type Arguments = Record<string, string | number | boolean | null>

/** True when every argument is a plain value, so each can have its own field. */
export function isSimple(args: Record<string, unknown> | undefined): boolean {
  const values = Object.values(args ?? {})
  return values.length > 0 && values.every(
    (v) => v === null || ['string', 'number', 'boolean'].includes(typeof v),
  )
}

export default function ArgumentsForm({ value, onChange }: {
  value: Arguments
  onChange: (next: Arguments) => void
}) {
  return (
    <div className="space-y-2">
      {Object.entries(value).map(([key, v]) => {
        const label = key.replace(/_/g, ' ')
        const set = (next: Arguments[string]) => onChange({ ...value, [key]: next })
        if (typeof v === 'boolean') {
          return (
            <label key={key} className="flex items-center gap-2 text-xs text-slate-300">
              <input type="checkbox" checked={v} onChange={(e) => set(e.target.checked)} />
              {label}
            </label>
          )
        }
        // a field keeps the type it started with: long strings get a textarea
        const long = typeof v === 'string' && (v.length > 120 || v.includes('\n'))
        return (
          <label key={key} className="block">
            <span className="label mb-1 block">{label}</span>
            {long ? (
              <textarea
                className="input resize-y h-32 text-xs"
                value={v}
                onChange={(e) => set(e.target.value)}
              />
            ) : typeof v === 'number' ? (
              <input
                type="number"
                className="input text-xs"
                defaultValue={v}
                onChange={(e) => { if (e.target.value !== '') set(Number(e.target.value)) }}
              />
            ) : (
              <input
                className="input text-xs"
                value={v ?? ''}
                // an untouched or emptied null stays null
                onChange={(e) => set(v === null && e.target.value === '' ? null : e.target.value)}
              />
            )}
          </label>
        )
      })}
    </div>
  )
}
