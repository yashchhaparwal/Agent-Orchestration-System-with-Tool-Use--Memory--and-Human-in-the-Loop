import { useState } from 'react'
import { XCircle, Terminal, Cpu, Wrench, Coins, Clock } from 'lucide-react'
import clsx from 'clsx'
import type { TraceSpan } from '@/types/trace'


interface PromptModalProps {
  span: TraceSpan
  onClose: () => void
}

export default function PromptModal({ span, onClose }: PromptModalProps) {
  const [tab, setTab] = useState<'details' | 'tools'>('details')

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm animate-fade-in" onClick={onClose}>
      <div
        className="w-full max-w-4xl max-h-[90vh] bg-surface-900 border border-white/10 rounded-2xl shadow-2xl flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/10 bg-surface-800/50">
          <div className="flex items-center gap-3">
            <div className={clsx(
              'h-9 w-9 rounded-xl flex items-center justify-center font-bold text-xs uppercase',
              span.status === 'ok' ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' :
              span.status === 'escalated' ? 'bg-purple-500/20 text-purple-400 border border-purple-500/30' :
              'bg-red-500/20 text-red-400 border border-red-500/30'
            )}>
              {span.agent_name.slice(0, 3)}
            </div>
            <div>
              <h2 className="text-base font-bold text-white flex items-center gap-2">
                {span.name}
                <span className="text-xs font-mono px-2 py-0.5 rounded bg-surface-700 text-slate-300">
                  {span.node_type}
                </span>
              </h2>
              <p className="text-xs text-slate-400 font-mono mt-0.5">
                Span ID: {span.span_id} {span.subtask_id ? `• Subtask: ${span.subtask_id}` : ''}
              </p>
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-white/10 transition-colors">
            <XCircle className="h-5 w-5" />
          </button>
        </div>

        {/* Sub-Header Metrics */}
        <div className="grid grid-cols-4 gap-2 px-6 py-3 border-b border-white/5 bg-surface-950/40 text-xs">
          <div className="flex items-center gap-2 text-slate-300">
            <Cpu className="h-3.5 w-3.5 text-brand-400" />
            <span>Model: <strong className="text-white">{span.model ?? '—'}</strong></span>
          </div>
          <div className="flex items-center gap-2 text-slate-300">
            <Clock className="h-3.5 w-3.5 text-brand-400" />
            <span>Latency: <strong className="text-white">{span.duration_ms >= 1000 ? `${(span.duration_ms / 1000).toFixed(2)}s` : `${span.duration_ms}ms`}</strong></span>
          </div>
          <div className="flex items-center gap-2 text-slate-300">
            <Coins className="h-3.5 w-3.5 text-brand-400" />
            <span>Cost: <strong className="text-white">${span.cost_usd.toFixed(6)}</strong></span>
          </div>
          <div className="flex items-center gap-2 text-slate-300">
            <Terminal className="h-3.5 w-3.5 text-brand-400" />
            <span>Tokens: <strong className="text-white">{span.tokens.total_tokens}</strong> ({span.tokens.prompt_tokens}p / {span.tokens.completion_tokens}c)</span>
          </div>
        </div>

        {/* Tab Navigation */}
        <div className="flex border-b border-white/10 bg-surface-900 px-6">
          <button
            onClick={() => setTab('details')}
            className={clsx(
              'px-4 py-3 text-xs font-medium border-b-2 transition-colors flex items-center gap-2',
              tab === 'details' ? 'border-brand-500 text-brand-400' : 'border-transparent text-slate-400 hover:text-slate-200'
            )}
          >
            <Terminal className="h-3.5 w-3.5" /> Details & Output
          </button>
          <button
            onClick={() => setTab('tools')}
            className={clsx(
              'px-4 py-3 text-xs font-medium border-b-2 transition-colors flex items-center gap-2',
              tab === 'tools' ? 'border-brand-500 text-brand-400' : 'border-transparent text-slate-400 hover:text-slate-200'
            )}
          >
            <Wrench className="h-3.5 w-3.5" /> Tool Calls ({span.tool_calls.length})
          </button>
        </div>

        {/* Tab Content */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {tab === 'details' && (
            <div className="space-y-6">
              {span.output && (
                <div className="space-y-2">
                  <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Subtask Output (latest attempt)</span>
                  <div className="p-4 bg-surface-950 rounded-xl border border-white/5 text-xs font-mono text-emerald-300 whitespace-pre-wrap leading-relaxed max-h-96 overflow-y-auto">
                    {span.output}
                  </div>
                </div>
              )}
              <div className="space-y-2">
                <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Span Attributes</span>
                <pre className="p-4 bg-surface-950 rounded-xl border border-white/5 text-xs font-mono text-slate-300 whitespace-pre-wrap leading-relaxed">
                  {JSON.stringify(span.attributes, null, 2)}
                </pre>
              </div>
            </div>
          )}

          {tab === 'tools' && (
            <div className="space-y-4">
              {span.tool_calls.length === 0 ? (
                <div className="p-8 text-center text-slate-500 text-xs">
                  No tools invoked during this step.
                </div>
              ) : (
                span.tool_calls.map((tc, idx) => (
                  <div key={idx} className="p-4 bg-surface-950 rounded-xl border border-white/5 space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-bold text-brand-300 font-mono">
                        🔧 {tc.tool}
                      </span>
                      <div className="flex items-center gap-3 text-xs text-slate-400">
                        <span>Latency: {tc.latency_ms}ms</span>
                        <span className={clsx('px-2 py-0.5 rounded text-[10px] font-bold uppercase', tc.success ? 'bg-emerald-500/20 text-emerald-400' : 'bg-red-500/20 text-red-400')}>
                          {tc.success ? 'Success' : 'Failed'}
                        </span>
                      </div>
                    </div>
                    <div>
                      <span className="text-[10px] font-bold text-slate-500 uppercase">Recorded Attributes</span>
                      <pre className="mt-1 p-2 bg-surface-900 rounded text-slate-300 font-mono text-[11px] overflow-x-auto">
                        {JSON.stringify(tc.input, null, 2)}
                      </pre>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
