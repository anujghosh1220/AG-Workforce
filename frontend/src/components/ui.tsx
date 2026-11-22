import { cva, type VariantProps } from 'class-variance-authority'
import { AlertCircle, Inbox, Loader2, X } from 'lucide-react'
import { createContext, useCallback, useContext, useState, type ReactNode } from 'react'
import { cn } from '../lib/utils'

// ---------------------------------------------------------------- Button

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 rounded-lg text-sm font-medium transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-viol-500/60 disabled:pointer-events-none disabled:opacity-50 cursor-pointer',
  {
    variants: {
      variant: {
        primary: 'bg-viol-500 text-white hover:bg-viol-400 shadow-[0_0_20px_-6px] shadow-viol-500/50',
        secondary: 'bg-ink-700 text-ink-100 hover:bg-ink-600 border border-ink-600/60',
        ghost: 'text-ink-300 hover:text-ink-100 hover:bg-ink-800',
        danger: 'bg-red-500/15 text-red-300 border border-red-500/30 hover:bg-red-500/25',
        success: 'bg-mint-500/15 text-mint-300 border border-mint-500/30 hover:bg-mint-500/25',
      },
      size: {
        sm: 'h-8 px-3 text-xs',
        md: 'h-9 px-4',
        lg: 'h-11 px-6',
        icon: 'h-9 w-9',
      },
    },
    defaultVariants: { variant: 'secondary', size: 'md' },
  },
)

export function Button({ className, variant, size, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof buttonVariants>) {
  return <button className={cn(buttonVariants({ variant, size }), className)} {...props} />
}

// ---------------------------------------------------------------- Card

export function Card({ className, children, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn('rounded-xl border border-ink-700/60 bg-ink-850/80 backdrop-blur-sm shadow-[0_8px_30px_-12px_rgba(0,0,0,0.6)]', className)} {...props}>
      {children}
    </div>
  )
}

export function CardHeader({ title, subtitle, action }: { title: string; subtitle?: string; action?: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-ink-700/50 px-5 py-4">
      <div>
        <h3 className="text-sm font-semibold text-ink-100">{title}</h3>
        {subtitle && <p className="mt-0.5 text-xs text-ink-400">{subtitle}</p>}
      </div>
      {action}
    </div>
  )
}

// ---------------------------------------------------------------- Form

export function Input({ className, ...props }: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn('h-9 w-full rounded-lg border border-ink-600/70 bg-ink-900 px-3 text-sm text-ink-100 placeholder:text-ink-400/60 transition-colors focus:border-viol-500/60 focus:outline-none focus:ring-2 focus:ring-viol-500/20', className)}
      {...props}
    />
  )
}

export function Textarea({ className, ...props }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn('min-h-20 w-full rounded-lg border border-ink-600/70 bg-ink-900 px-3 py-2 text-sm text-ink-100 placeholder:text-ink-400/60 transition-colors focus:border-viol-500/60 focus:outline-none focus:ring-2 focus:ring-viol-500/20', className)}
      {...props}
    />
  )
}

export function Select({ className, children, ...props }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn('h-9 w-full appearance-none rounded-lg border border-ink-600/70 bg-ink-900 px-3 text-sm text-ink-100 transition-colors focus:border-viol-500/60 focus:outline-none focus:ring-2 focus:ring-viol-500/20 cursor-pointer', className)}
      {...props}
    >
      {children}
    </select>
  )
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium text-ink-300">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-ink-400">{hint}</span>}
    </label>
  )
}

// ---------------------------------------------------------------- Badge

const badgeVariants = cva('inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-medium border', {
  variants: {
    tone: {
      neutral: 'bg-ink-700/50 text-ink-300 border-ink-600/60',
      violet: 'bg-viol-500/15 text-viol-300 border-viol-500/30',
      mint: 'bg-mint-500/15 text-mint-300 border-mint-500/30',
      amber: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
      red: 'bg-red-500/15 text-red-300 border-red-500/30',
      blue: 'bg-sky-500/15 text-sky-300 border-sky-500/30',
    },
  },
  defaultVariants: { tone: 'neutral' },
})

export function Badge({ className, tone, ...props }: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />
}

// ---------------------------------------------------------------- Feedback

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('animate-pulse rounded-lg bg-ink-700/60', className)} />
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn('h-4 w-4 animate-spin text-ink-300', className)} />
}

export function EmptyState({ icon, title, hint, action }: { icon?: ReactNode; title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-ink-600/60 px-6 py-14 text-center animate-fade-in">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-ink-800 text-ink-400">
        {icon ?? <Inbox className="h-5 w-5" />}
      </div>
      <div>
        <p className="text-sm font-medium text-ink-200">{title}</p>
        {hint && <p className="mt-1 text-xs text-ink-400 max-w-sm">{hint}</p>}
      </div>
      {action}
    </div>
  )
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-red-500/20 bg-red-500/5 px-6 py-10 text-center">
      <AlertCircle className="h-6 w-6 text-red-400" />
      <p className="text-sm text-red-300">{message}</p>
      {onRetry && <Button size="sm" variant="secondary" onClick={onRetry}>Retry</Button>}
    </div>
  )
}

// ---------------------------------------------------------------- Drawer

export function Drawer({ open, onClose, title, children, width = 'max-w-xl' }: {
  open: boolean; onClose: () => void; title: string; children: ReactNode; width?: string
}) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm animate-fade-in" onClick={onClose} />
      <div className={cn('absolute right-0 top-0 h-full w-full overflow-y-auto border-l border-ink-700 bg-ink-900 shadow-2xl animate-[fade-up_0.25s_ease_both]', width)}>
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-ink-700/60 bg-ink-900/95 px-6 py-4 backdrop-blur">
          <h2 className="text-base font-semibold">{title}</h2>
          <button onClick={onClose} className="rounded-lg p-1.5 text-ink-400 hover:bg-ink-800 hover:text-ink-100 cursor-pointer" aria-label="Close">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="px-6 py-5">{children}</div>
      </div>
    </div>
  )
}

export function ConfirmDialog({ open, title, message, confirmLabel = 'Confirm', danger, onConfirm, onCancel, busy }: {
  open: boolean; title: string; message: string; confirmLabel?: string; danger?: boolean
  onConfirm: () => void; onCancel: () => void; busy?: boolean
}) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm animate-fade-in" onClick={onCancel} />
      <div className="relative w-full max-w-md rounded-xl border border-ink-700 bg-ink-900 p-6 shadow-2xl animate-fade-up">
        <h3 className="text-base font-semibold">{title}</h3>
        <p className="mt-2 text-sm text-ink-300">{message}</p>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button variant={danger ? 'danger' : 'primary'} onClick={onConfirm} disabled={busy}>
            {busy && <Spinner />} {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- Toast

type Toast = { id: number; message: string; tone: 'success' | 'error' | 'info' }
const ToastCtx = createContext<(message: string, tone?: Toast['tone']) => void>(() => {})

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const push = useCallback((message: string, tone: Toast['tone'] = 'info') => {
    const id = Date.now() + Math.random()
    setToasts((t) => [...t, { id, message, tone }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4000)
  }, [])
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="fixed bottom-5 right-5 z-[100] flex flex-col gap-2">
        {toasts.map((t) => (
          <div key={t.id} className={cn(
            'flex items-center gap-2 rounded-lg border px-4 py-3 text-sm shadow-xl animate-fade-up backdrop-blur',
            t.tone === 'success' && 'border-mint-500/40 bg-ink-850/95 text-mint-300',
            t.tone === 'error' && 'border-red-500/40 bg-ink-850/95 text-red-300',
            t.tone === 'info' && 'border-viol-500/40 bg-ink-850/95 text-viol-300',
          )}>
            {t.message}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}

export function useToast() {
  return useContext(ToastCtx)
}
