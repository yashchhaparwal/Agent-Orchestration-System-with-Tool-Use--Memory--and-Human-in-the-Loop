import { NavLink, Outlet } from 'react-router-dom'
import { Bot, CheckSquare, GitBranch, Activity, ListTodo, Send, Brain, BarChart2 } from 'lucide-react'
import { useHealth } from '@/api/hooks'
import clsx from 'clsx'

const navItems = [
  {
    to: '/review',
    icon: CheckSquare,
    label: 'Review Queue',
    description: 'HITL approvals',
  },
  {
    to: '/tasks',
    icon: ListTodo,
    label: 'Tasks',
    description: 'Submit and follow',
  },
  {
    to: '/traces',
    icon: GitBranch,
    label: 'Traces',
    description: 'Agent execution',
  },
  {
    to: '/outbox',
    icon: Send,
    label: 'Outbox',
    description: 'Proposed, never sent',
  },
  {
    to: '/memory',
    icon: Brain,
    label: 'Memory',
    description: 'Lessons per user',
  },
  {
    to: '/stats',
    icon: BarChart2,
    label: 'Stats',
    description: 'Numbers and safety',
  },
]

function StatusDot({ online }: { online: boolean }) {
  return (
    <span className="relative flex h-2 w-2">
      {online && (
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success-400 opacity-60" />
      )}
      <span
        className={clsx(
          'relative inline-flex h-2 w-2 rounded-full',
          online ? 'bg-success-400' : 'bg-slate-600',
        )}
      />
    </span>
  )
}

export default function Layout() {
  const { data: health, isError } = useHealth()
  const online = !isError && health?.status === 'ok'

  return (
    <div className="flex h-screen overflow-hidden">
      {/* ── Sidebar ──────────────────────────────────────────────────────── */}
      <aside className="w-64 flex-shrink-0 flex flex-col bg-surface-900/80 backdrop-blur-xl border-r border-white/[0.06]">
        {/* Logo */}
        <div className="flex items-center gap-3 px-5 py-5 border-b border-white/[0.06]">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-600 shadow-glow-brand">
            <Bot className="h-5 w-5 text-white" />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-white truncate">AgentOS</p>
            <p className="text-xs text-slate-500 truncate">Orchestration v0.2</p>
          </div>
        </div>

        {/* Nav */}
        <nav className="flex-1 p-3 space-y-1 overflow-y-auto">
          <p className="label px-2 mb-3 mt-1">Navigation</p>
          {navItems.map(({ to, icon: Icon, label, description }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                clsx(
                  'flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all duration-150 group',
                  isActive
                    ? 'bg-brand-600/20 text-brand-300 border border-brand-500/20'
                    : 'text-slate-400 hover:text-slate-100 hover:bg-white/[0.04]',
                )
              }
            >
              {({ isActive }) => (
                <>
                  <Icon
                    className={clsx(
                      'h-4 w-4 flex-shrink-0 transition-colors',
                      isActive ? 'text-brand-400' : 'text-slate-500 group-hover:text-slate-300',
                    )}
                  />
                  <div className="min-w-0">
                    <p className="text-sm font-medium truncate">{label}</p>
                    <p className="text-xs text-slate-500 truncate">{description}</p>
                  </div>
                  {isActive && (
                    <div className="ml-auto h-1.5 w-1.5 rounded-full bg-brand-400 flex-shrink-0" />
                  )}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        {/* Footer — API status */}
        <div className="p-3 border-t border-white/[0.06]">
          <div className="glass px-3 py-2.5 flex items-center gap-2.5">
            <StatusDot online={online} />
            <div className="min-w-0">
              <p className="text-xs font-medium text-slate-300 truncate">API Backend</p>
              <p className={clsx('text-xs truncate', online ? 'text-success-400' : 'text-slate-500')}>
                {online ? 'Online' : 'Offline'}
              </p>
            </div>
            <Activity className="h-3.5 w-3.5 text-slate-600 ml-auto flex-shrink-0" />
          </div>
        </div>
      </aside>

      {/* ── Main content ─────────────────────────────────────────────────── */}
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  )
}
