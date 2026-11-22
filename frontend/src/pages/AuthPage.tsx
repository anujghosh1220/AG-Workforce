import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError } from '../lib/api'
import { useAuth } from '../auth'
import { Button, Field, Input, useToast } from '../components/ui'

export default function AuthPage({ mode }: { mode: 'login' | 'register' }) {
  const { login, register } = useAuth()
  const navigate = useNavigate()
  const toast = useToast()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [workspaceName, setWorkspaceName] = useState('')
  const [inviteCode, setInviteCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      if (mode === 'login') await login(email, password)
      else await register(email, password, fullName, workspaceName || 'My Workspace', inviteCode || undefined)
      navigate('/')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong')
      toast('Authentication failed', 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="ambient flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-md">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-viol-500 to-mint-500 text-xl font-bold text-white shadow-xl shadow-viol-500/30">
            AG
          </div>
          <h1 className="text-2xl font-bold tracking-tight">AG Workforce</h1>
          <p className="mt-1 text-sm text-ink-400">AI Workforce Management Platform</p>
        </div>

        <form onSubmit={submit} className="space-y-4 rounded-2xl border border-ink-700/60 bg-ink-900/70 p-6 backdrop-blur">
          {mode === 'register' && (
            <>
              <Field label="Full name">
                <Input value={fullName} onChange={(e) => setFullName(e.target.value)} placeholder="Jane Doe" />
              </Field>
              <Field label="Workspace name">
                <Input value={workspaceName} onChange={(e) => setWorkspaceName(e.target.value)} placeholder="Acme Corp" />
              </Field>
              <Field label="Invite code" hint="Only required if registration is restricted">
                <Input value={inviteCode} onChange={(e) => setInviteCode(e.target.value)} placeholder="Optional" />
              </Field>
            </>
          )}
          <Field label="Email">
            <Input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com" autoComplete="email" />
          </Field>
          <Field label="Password" hint={mode === 'register' ? 'Minimum 8 characters' : undefined}>
            <Input type="password" required minLength={mode === 'register' ? 8 : 1} value={password} onChange={(e) => setPassword(e.target.value)} placeholder="••••••••" autoComplete={mode === 'login' ? 'current-password' : 'new-password'} />
          </Field>
          {error && <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</p>}
          <Button type="submit" variant="primary" size="lg" className="w-full" disabled={busy}>
            {busy ? 'Please wait…' : mode === 'login' ? 'Sign in' : 'Create workspace'}
          </Button>
          <p className="text-center text-xs text-ink-400">
            {mode === 'login' ? (
              <>No account? <a href="/register" className="text-viol-300 hover:text-viol-400">Create one</a></>
            ) : (
              <>Have an account? <a href="/login" className="text-viol-300 hover:text-viol-400">Sign in</a></>
            )}
          </p>
        </form>
      </div>
    </div>
  )
}
