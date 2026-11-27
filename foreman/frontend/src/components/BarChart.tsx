// Single-series bar charts: one hue, values written in text colour, and a table under each chart.

export interface Bar {
  label: string
  value: number
}

function Table({ rows, labelName, valueName }: { rows: Bar[]; labelName: string; valueName: string }) {
  return (
    <details className="text-xs">
      <summary className="cursor-pointer text-slate-500 hover:text-slate-300">Table</summary>
      <table className="w-full text-left mt-2">
        <thead>
          <tr className="text-slate-500 border-b border-white/10">
            <th className="py-1 font-medium">{labelName}</th>
            <th className="py-1 font-medium text-right">{valueName}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/5">
          {rows.map((r) => (
            <tr key={r.label}>
              <td className="py-1 text-slate-300">{r.label}</td>
              <td className="py-1 text-slate-300 text-right tabular-nums">{r.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  )
}

export default function BarChart({ title, rows, labelName, valueName, vertical = false }: {
  title: string
  rows: Bar[]
  labelName: string
  valueName: string
  /** columns for values over time; horizontal bars for named categories */
  vertical?: boolean
}) {
  const max = Math.max(...rows.map((r) => r.value), 1)

  return (
    <div className="glass p-6 space-y-4">
      <h3 className="text-sm font-bold text-white">{title}</h3>
      {rows.length === 0 ? (
        <p className="text-xs text-slate-500">Nothing in this window.</p>
      ) : (
        <>
          {vertical ? (
            <div className="flex items-stretch gap-0.5 h-44 overflow-x-auto">
              {rows.map((r) => (
                <div key={r.label} className="group flex-1 min-w-10 flex flex-col items-center"
                     title={`${r.label}: ${r.value} ${valueName.toLowerCase()}`}>
                  <div className="flex-1 w-full flex flex-col justify-end items-center border-b border-white/15">
                    <span className="text-[11px] text-slate-300 tabular-nums mb-1">{r.value}</span>
                    <div
                      className="w-full max-w-8 rounded-t bg-brand-400 group-hover:bg-brand-300"
                      style={{ height: `${(r.value / max) * 85}%` }}
                    />
                  </div>
                  <span className="text-[10px] text-slate-500 mt-1 whitespace-nowrap">
                    {r.label.slice(5)}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="space-y-1.5">
              {rows.map((r) => (
                <div key={r.label} className="group flex items-center gap-3"
                     title={`${r.label}: ${r.value} ${valueName.toLowerCase()}`}>
                  <span className="w-36 truncate text-right text-xs text-slate-400 font-mono">{r.label}</span>
                  <div className="flex-1 flex items-center gap-2 border-l border-white/15 h-6">
                    <div
                      className="h-3.5 rounded-r bg-brand-400 group-hover:bg-brand-300"
                      style={{ width: `${(r.value / max) * 85}%` }}
                    />
                    <span className="text-xs text-slate-300 tabular-nums">{r.value}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
          <Table rows={rows} labelName={labelName} valueName={valueName} />
        </>
      )}
    </div>
  )
}
