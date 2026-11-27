import { useState } from 'react'
import { Zap, Clock, Coins, Cpu, ArrowRight } from 'lucide-react'
import clsx from 'clsx'
import type { TaskTrace, TraceSpan } from '@/types/trace'
import PromptModal from './PromptModal'


interface TraceGraphViewProps {
  trace: TaskTrace
  spans: TraceSpan[]
}

const AGENT_COLORS: Record<string, { bg: string; border: string; text: string }> = {
  intake:              { bg: 'bg-slate-500/10', border: 'border-slate-500/30', text: 'text-slate-300' },
  supervisor:          { bg: 'bg-brand-500/10', border: 'border-brand-500/30', text: 'text-brand-300' },
  research_specialist: { bg: 'bg-cyan-500/10', border: 'border-cyan-500/30', text: 'text-cyan-300' },
  analysis_specialist: { bg: 'bg-violet-500/10', border: 'border-violet-500/30', text: 'text-violet-300' },
  writing_specialist:  { bg: 'bg-emerald-500/10', border: 'border-emerald-500/30', text: 'text-emerald-300' },
  code_specialist:     { bg: 'bg-amber-500/10', border: 'border-amber-500/30', text: 'text-amber-300' },
  reviewer:            { bg: 'bg-pink-500/10', border: 'border-pink-500/30', text: 'text-pink-300' },
  synthesis:           { bg: 'bg-brand-400/10', border: 'border-brand-400/30', text: 'text-brand-200' },
  human_escalation:    { bg: 'bg-purple-500/10', border: 'border-purple-500/30', text: 'text-purple-300' },
}

export default function TraceGraphView({ trace, spans }: TraceGraphViewProps) {
  const [selectedSpan, setSelectedSpan] = useState<TraceSpan | null>(null)

  return (
    <div className="space-y-6">
      {/* Run Summary Banner */}
      <div className="glass p-6 rounded-2xl flex items-center justify-between">
        <div>
          <div className="flex items-center gap-3 mb-2">
            <span className={clsx(
              'px-3 py-1 rounded-full text-xs font-bold uppercase tracking-wider',
              trace.status === 'completed' ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' :
              trace.status === 'escalated' ? 'bg-purple-500/20 text-purple-400 border border-purple-500/30' :
              trace.status === 'running' ? 'bg-brand-500/20 text-brand-300 border border-brand-500/30' :
              'bg-red-500/20 text-red-400 border border-red-500/30'
            )}>
              {trace.status}
            </span>
            <span className="text-xs font-mono text-slate-400">ID: {trace.run_id}</span>
            {trace.replayed_from && (
              <span className="text-xs font-mono text-brand-300 bg-brand-500/20 px-2 py-0.5 rounded border border-brand-500/30">
                ↺ Replayed from {trace.replayed_from}
              </span>
            )}
          </div>
          <h2 className="text-base font-bold text-white leading-snug">{trace.task_input}</h2>
        </div>

        <div className="flex items-center gap-6 border-l border-white/10 pl-6 text-right">
          <div>
            <p className="label">Total Cost</p>
            <p className="text-xl font-bold text-emerald-400 font-mono">${trace.total_cost_usd.toFixed(4)}</p>
          </div>
          <div>
            <p className="label">Tokens</p>
            <p className="text-xl font-bold text-brand-300 font-mono">{trace.total_tokens.toLocaleString()}</p>
          </div>
          <div>
            <p className="label">Duration</p>
            <p className="text-xl font-bold text-white font-mono">
              {trace.duration_ms >= 1000 ? `${(trace.duration_ms / 1000).toFixed(1)}s` : `${trace.duration_ms}ms`}
            </p>
          </div>
        </div>
      </div>

      {/* Visual Execution Tree / Graph */}
      <div className="glass p-8 rounded-2xl space-y-6">
        <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider flex items-center gap-2">
          <Zap className="h-4 w-4 text-brand-400" /> Execution Steps (click a step to inspect it)
        </h3>

        <div className="relative flex flex-col md:flex-row items-stretch justify-between gap-4 overflow-x-auto py-4">
          {spans.map((span, index) => {
            const colors = AGENT_COLORS[span.node_type] ?? { bg: 'bg-slate-500/10', border: 'border-slate-500/30', text: 'text-slate-300' }
            const toolCount = span.tool_calls?.length ?? 0

            return (
              <div key={span.span_id} className="flex items-center">
                {/* Node Box */}
                <button
                  onClick={() => setSelectedSpan(span)}
                  className={clsx(
                    'w-64 p-4 rounded-xl border text-left transition-all duration-200 hover:scale-105 hover:shadow-xl group cursor-pointer relative',
                    colors.bg,
                    colors.border,
                    'bg-surface-900/90 hover:bg-surface-800'
                  )}
                >
                  <div className="flex items-center justify-between mb-2">
                    <span className={clsx('text-xs font-bold uppercase tracking-wider font-mono', colors.text)}>
                      {span.agent_name}
                    </span>
                    <span className={clsx(
                      'h-2.5 w-2.5 rounded-full',
                      span.status === 'ok' ? 'bg-emerald-400 shadow-glow-emerald' :
                      span.status === 'escalated' ? 'bg-purple-400 shadow-glow-purple' :
                      'bg-red-400'
                    )} />
                  </div>

                  <p className="text-xs font-semibold text-white line-clamp-2 mb-3">
                    {span.name}
                  </p>

                  <div className="grid grid-cols-2 gap-2 text-[11px] font-mono text-slate-400 pt-2 border-t border-white/5">
                    <div className="flex items-center gap-1">
                      <Clock className="h-3 w-3 text-slate-500" />
                      <span>{span.duration_ms >= 1000 ? `${(span.duration_ms / 1000).toFixed(1)}s` : `${span.duration_ms}ms`}</span>
                    </div>
                    <div className="flex items-center gap-1 text-emerald-400">
                      <Coins className="h-3 w-3" />
                      <span>${span.cost_usd.toFixed(4)}</span>
                    </div>
                    <div className="flex items-center gap-1 col-span-2">
                      <Cpu className="h-3 w-3 text-brand-400" />
                      <span>{span.tokens.total_tokens} tokens {toolCount > 0 ? `• 🔧 ${toolCount} tools` : ''}</span>
                    </div>
                  </div>

                  <div className="mt-2 text-[10px] text-brand-400 opacity-0 group-hover:opacity-100 transition-opacity font-bold">
                    Click to inspect →
                  </div>
                </button>

                {/* Connector Arrow */}
                {index < spans.length - 1 && (
                  <div className="hidden md:flex items-center px-2 text-slate-600">
                    <ArrowRight className="h-5 w-5" />
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      {/* Detail Modal */}
      {selectedSpan && (
        <PromptModal span={selectedSpan} onClose={() => setSelectedSpan(null)} />
      )}
    </div>
  )
}
