import { Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from './auth'
import AppLayout from './layouts/AppLayout'
import AuthPage from './pages/AuthPage'
import Overview from './pages/Overview'
import Agents from './pages/Agents'
import AgentDetail from './pages/AgentDetail'
import Leads from './pages/Leads'
import Pipeline from './pages/Pipeline'
import Conversations from './pages/Conversations'
import CalendarPage from './pages/Calendar'
import Manager from './pages/Manager'
import Workforce from './pages/Workforce'
import Operations from './pages/Operations'
import Tasks from './pages/Tasks'
import Research from './pages/Research'
import Campaigns from './pages/Campaigns'
import Approvals from './pages/Approvals'
import NotificationsPage from './pages/Notifications'
import Analytics from './pages/Analytics'
import Knowledge from './pages/Knowledge'
import { Spinner } from './components/ui'

function Protected({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()
  if (loading) {
    return <div className="flex h-screen items-center justify-center"><Spinner className="h-6 w-6" /></div>
  }
  if (!user) return <Navigate to="/login" replace />
  return <>{children}</>
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<AuthPage mode="login" />} />
      <Route path="/register" element={<AuthPage mode="register" />} />
      <Route
        element={
          <Protected>
            <AppLayout />
          </Protected>
        }
      >
        <Route path="/" element={<Overview />} />
        <Route path="/agents" element={<Agents />} />
        <Route path="/agents/:id" element={<AgentDetail />} />
        <Route path="/leads" element={<Leads />} />
        <Route path="/pipeline" element={<Pipeline />} />
        <Route path="/conversations" element={<Conversations />} />
        <Route path="/calendar" element={<CalendarPage />} />
        <Route path="/manager" element={<Manager />} />
        <Route path="/workforce" element={<Workforce />} />
        <Route path="/operations" element={<Operations />} />
        <Route path="/tasks" element={<Tasks />} />
          <Route path="/research" element={<Research />} />
          <Route path="/campaigns" element={<Campaigns />} />
        <Route path="/approvals" element={<Approvals />} />
        <Route path="/notifications" element={<NotificationsPage />} />
        <Route path="/analytics" element={<Analytics />} />
        <Route path="/knowledge" element={<Knowledge />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
