import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { api, getToken, setToken, type User, type Workspace } from './lib/api'

interface AuthState {
  user: User | null
  workspace: Workspace | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string, fullName: string, workspaceName: string, inviteCode?: string) => Promise<void>
  logout: () => void
}

const AuthCtx = createContext<AuthState>(null as unknown as AuthState)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [workspace, setWorkspace] = useState<Workspace | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!getToken()) {
      setLoading(false)
      return
    }
    Promise.all([api.get<User>('/auth/me'), api.get<Workspace>('/workspaces/current')])
      .then(([u, w]) => { setUser(u); setWorkspace(w) })
      .catch(() => setToken(null))
      .finally(() => setLoading(false))
  }, [])

  async function login(email: string, password: string) {
    const res = await api.post<{ access_token: string }>('/auth/login', { email, password })
    setToken(res.access_token)
    const [u, w] = await Promise.all([api.get<User>('/auth/me'), api.get<Workspace>('/workspaces/current')])
    setUser(u); setWorkspace(w)
  }

  async function register(email: string, password: string, fullName: string, workspaceName: string, inviteCode?: string) {
    const res = await api.post<{ access_token: string }>('/auth/register', {
      email, password, full_name: fullName, workspace_name: workspaceName, invite_code: inviteCode ?? null,
    })
    setToken(res.access_token)
    const [u, w] = await Promise.all([api.get<User>('/auth/me'), api.get<Workspace>('/workspaces/current')])
    setUser(u); setWorkspace(w)
  }

  function logout() {
    setToken(null)
    setUser(null)
    setWorkspace(null)
    location.href = '/login'
  }

  return (
    <AuthCtx.Provider value={{ user, workspace, loading, login, register, logout }}>
      {children}
    </AuthCtx.Provider>
  )
}

export function useAuth() {
  return useContext(AuthCtx)
}
