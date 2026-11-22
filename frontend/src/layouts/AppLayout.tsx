import { useQuery } from '@tanstack/react-query'
import {
  BarChart3, Bell, Bot, Briefcase, Calendar, CalendarCheck, KanbanSquare, LayoutDashboard, LogOut, Network,
  MessageSquare, Settings2, CheckSquare, Search, Megaphone, Users, X, Activity,
} from 'lucide-react'
import { useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { api, type Notification } from '../lib/api'
import { useAuth } from '../auth'
import { cn, formatDateTime } from '../lib/utils'

const NAV = [
  { to: '/', label: 'Overview', icon: LayoutDashboard, end: true },
  { to: '/agents', label: 'AI Employees', icon: Bot },
  { to: '/leads', label: 'Leads / CRM', icon: Users },
  { to: '/pipeline', label: 'Sales Pipeline', icon: KanbanSquare },
  { to: '/conversations', label: 'Conversations', icon: MessageSquare },
  { to: '/calendar', label: 'Calendar', icon: Calendar },
  { to: '/manager', label: 'Sales Manager', icon: Briefcase },
  { to: '/workforce', label: 'Workforce', icon: Network },
  { to: '/tasks', label: 'Tasks', icon: CheckSquare },
  { to: '/research', label: 'Lead Research', icon: Search },
  { to: '/campaigns', label: 'Campaigns', icon: Megaphone },
  { to: '/approvals', label: 'Approvals', icon: CalendarCheck },
  { to: '/analytics', label: 'Analytics', icon: BarChart3 },
  { to: '/operations', label: 'Operations', icon: Activity },
  { to: '/knowledge', label: 'Product Knowledge', icon: Settings2 },
]

export default function AppLayout() {
  const { user, workspace, logout } = useAuth()
  const [notifOpen, setNotifOpen] = useState(false)
  const [mobileNav, setMobileNav] = useState(false)
  const location = useLocation()

  const { data: unread } = useQuery({
    queryKey: ['unread-count'],
    queryFn: () => api.get<{ count: number }>('/notifications/unread-count'),
    refetchInterval: 30000,
  })
  const { data: notifs } = useQuery({
    queryKey: ['notifications', 'recent'],
    queryFn: () => api.get<{ items: Notification[] }>('/notifications?page_size=8'),
    enabled: notifOpen,
  })

  const pageTitle = NAV.find((n) => (n.end ? location.pathname === n.to : location.pathname.startsWith(n.to)))?.label ?? 'AG Workforce'

  const sidebar = (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 px-5 py-5">
        <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-viol-500 to-mint-500 text-sm font-bold text-white shadow-lg shadow-viol-500/30">
          AG
        </div>
        <div>
          <p className="text-sm font-bold tracking-tight">AG Workforce</p>
          <p className="text-[11px] text-ink-400">{workspace?.name ?? 'Workspace'}</p>
        </div>
      </div>
      <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-2">
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            onClick={() => setMobileNav(false)}
            className={({ isActive }) => cn(
              'group flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-all duration-200',
              isActive
                ? 'bg-viol-500/15 text-viol-300 border border-viol-500/25'
                : 'text-ink-300 hover:bg-ink-800 hover:text-ink-100 border border-transparent',
            )}
          >
            <Icon className="h-4 w-4 shrink-0" />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="border-t border-ink-700/60 p-3">
        <div className="flex items-center gap-3 rounded-lg px-2 py-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-ink-700 text-xs font-semibold text-viol-300">
            {(user?.full_name || user?.email || '?').slice(0, 2).toUpperCase()}
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs font-medium">{user?.full_name || user?.email}</p>
            <p className="truncate text-[11px] text-ink-400 capitalize">{user?.role}</p>
          </div>
          <button onClick={logout} className="rounded-md p-1.5 text-ink-400 hover:bg-ink-800 hover:text-red-300 cursor-pointer" title="Sign out">
            <LogOut className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  )

  return (
    <div className="ambient flex h-screen overflow-hidden">
      {/* desktop sidebar */}
      <aside className="hidden w-60 shrink-0 border-r border-ink-700/50 bg-ink-900/60 backdrop-blur lg:block">
        {sidebar}
      </aside>
      {/* mobile sidebar */}
      {mobileNav && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/60" onClick={() => setMobileNav(false)} />
          <aside className="absolute left-0 top-0 h-full w-64 bg-ink-900">{sidebar}</aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-ink-700/50 bg-ink-900/40 px-4 backdrop-blur lg:px-8">
          <div className="flex items-center gap-3">
            <button className="rounded-lg p-2 text-ink-300 hover:bg-ink-800 lg:hidden cursor-pointer" onClick={() => setMobileNav(true)} aria-label="Menu">
              <svg className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h16"/></svg>
            </button>
            <h1 className="text-sm font-semibold text-ink-100">{pageTitle}</h1>
          </div>
          <div className="relative">
            <button
              onClick={() => setNotifOpen((v) => !v)}
              className="relative rounded-lg p-2 text-ink-300 hover:bg-ink-800 hover:text-ink-100 cursor-pointer"
              aria-label="Notifications"
            >
              <Bell className="h-5 w-5" />
              {(unread?.count ?? 0) > 0 && (
                <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-viol-500 px-1 text-[10px] font-bold text-white">
                  {unread!.count > 99 ? '99+' : unread!.count}
                </span>
              )}
            </button>
            {notifOpen && (
              <div className="absolute right-0 mt-2 w-80 rounded-xl border border-ink-700 bg-ink-900 shadow-2xl animate-fade-up">
                <div className="flex items-center justify-between border-b border-ink-700/60 px-4 py-3">
                  <p className="text-sm font-semibold">Notifications</p>
                  <button onClick={() => setNotifOpen(false)} className="text-ink-400 hover:text-ink-100 cursor-pointer"><X className="h-4 w-4" /></button>
                </div>
                <div className="max-h-96 overflow-y-auto">
                  {(notifs?.items ?? []).length === 0 && (
                    <p className="px-4 py-8 text-center text-xs text-ink-400">No notifications yet</p>
                  )}
                  {notifs?.items.map((n) => (
                    <a key={n.id} href={n.link ?? '#'} onClick={() => setNotifOpen(false)}
                       className={cn('block border-b border-ink-800 px-4 py-3 hover:bg-ink-800/60', !n.is_read && 'bg-viol-500/5')}>
                      <div className="flex items-start justify-between gap-2">
                        <p className="text-xs font-medium text-ink-100">{n.title}</p>
                        {!n.is_read && <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-viol-400" />}
                      </div>
                      <p className="mt-0.5 line-clamp-2 text-[11px] text-ink-400">{n.body}</p>
                      <p className="mt-1 text-[10px] text-ink-400/70">{formatDateTime(n.created_at)}</p>
                    </a>
                  ))}
                </div>
              </div>
            )}
          </div>
        </header>
        <main className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-[1400px] p-4 lg:p-8">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}
