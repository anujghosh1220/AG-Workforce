import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Card } from './ui'
import { cn } from '../lib/utils'

export function AnimatedNumber({ value, format }: { value: number; format?: (n: number) => string }) {
  const [display, setDisplay] = useState(0)
  const prev = useRef(0)
  useEffect(() => {
    const from = prev.current
    const to = value
    prev.current = value
    if (from === to) { setDisplay(to); return }
    const start = performance.now()
    const dur = 700
    let raf: number
    const tick = (t: number) => {
      const p = Math.min(1, (t - start) / dur)
      const eased = 1 - Math.pow(1 - p, 3)
      setDisplay(from + (to - from) * eased)
      if (p < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [value])
  return <>{format ? format(display) : Math.round(display).toLocaleString()}</>
}

export function MetricCard({ label, value, icon, hint, accent = 'violet' }: {
  label: string; value: number; icon?: ReactNode; hint?: string
  accent?: 'violet' | 'mint' | 'amber' | 'blue' | 'red'
}) {
  const accents: Record<string, string> = {
    violet: 'from-viol-500/20 to-viol-500/5 text-viol-300',
    mint: 'from-mint-500/20 to-mint-500/5 text-mint-300',
    amber: 'from-amber-500/20 to-amber-500/5 text-amber-300',
    blue: 'from-sky-500/20 to-sky-500/5 text-sky-300',
    red: 'from-red-500/20 to-red-500/5 text-red-300',
  }
  return (
    <Card className="relative overflow-hidden p-5">
      <div className={cn('pointer-events-none absolute -right-8 -top-8 h-28 w-28 rounded-full bg-gradient-to-br blur-2xl', accents[accent].split(' ').slice(0, 2).join(' '))} />
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-wider text-ink-400">{label}</p>
          <p className="mt-2 text-3xl font-bold tracking-tight">
            <AnimatedNumber value={value} />
          </p>
          {hint && <p className="mt-1 text-[11px] text-ink-400">{hint}</p>}
        </div>
        {icon && <div className={cn('flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br', accents[accent])}>{icon}</div>}
      </div>
    </Card>
  )
}

export function PageHeader({ title, subtitle, action }: { title: string; subtitle?: string; action?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4 animate-fade-up">
      <div>
        <h2 className="text-xl font-bold tracking-tight">{title}</h2>
        {subtitle && <p className="mt-1 text-sm text-ink-400">{subtitle}</p>}
      </div>
      {action}
    </div>
  )
}
