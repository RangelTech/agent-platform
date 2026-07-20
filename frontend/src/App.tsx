import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { NavLink, Navigate, Route, BrowserRouter as Router, Routes, useLocation } from 'react-router-dom'
import { Button } from './components/ui'
import { AuthProvider, useAuth } from './lib/auth'
import AiServices from './pages/AiServices'
import Chat from './pages/Chat'
import Datasources from './pages/Datasources'
import Files from './pages/Files'
import Integrations from './pages/Integrations'
import Login from './pages/Login'
import Memories from './pages/Memories'
import Profiles from './pages/Profiles'
import TemplatesPage from './pages/Templates'
import Tenants from './pages/Tenants'
import Usage from './pages/Usage'
import Users from './pages/Users'

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
})

function Shell() {
  const { user, signOut, can } = useAuth()
  const isChat = useLocation().pathname === '/chat'

  const links = [
    // The master administers the platform and has no tenant to chat in.
    { to: '/chat', label: 'Chat', show: !(user?.is_master ?? false) },
    { to: '/empresas', label: 'Empresas', show: user?.is_master ?? false },
    { to: '/usuarios', label: 'Usuários', show: can('users', 'view') },
    { to: '/perfis', label: 'Perfis', show: can('user_profiles', 'view') },
    { to: '/servicos-ia', label: 'Serviços de IA', show: can('ai_services', 'view') },
    { to: '/templates', label: 'Templates', show: can('templates', 'view') },
    { to: '/fontes-de-dados', label: 'Fontes de dados', show: can('datasources', 'view') },
    { to: '/arquivos', label: 'Arquivos', show: can('files', 'view') },
    { to: '/memorias', label: 'Memórias', show: !(user?.is_master ?? false) },
    { to: '/consumo', label: 'Consumo', show: can('usage', 'view') },
    { to: '/integracoes', label: 'Integrações', show: can('integrations', 'view') },
  ].filter((l) => l.show)

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
          <div className="flex items-center gap-6">
            <span className="text-sm font-semibold">agent-platform</span>
            <nav className="flex gap-1">
              {links.map((l) => (
                <NavLink
                  key={l.to}
                  to={l.to}
                  className={({ isActive }) =>
                    `rounded-md px-3 py-1.5 text-sm ${
                      isActive ? 'bg-slate-800 text-slate-100' : 'text-slate-400 hover:text-slate-200'
                    }`
                  }
                >
                  {l.label}
                </NavLink>
              ))}
            </nav>
          </div>
          <div className="flex items-center gap-3">
            <span data-testid="current-user" className="text-sm text-slate-400">
              {user?.name}
            </span>
            <Button variant="ghost" onClick={signOut}>
              Sair
            </Button>
          </div>
        </div>
      </header>

      <main className={isChat ? '' : 'mx-auto max-w-6xl px-4 py-6'}>
        <Routes>
          <Route path="/chat" element={<Chat />} />
          <Route path="/empresas" element={user?.is_master ? <Tenants /> : <Navigate to="/usuarios" />} />
          <Route path="/usuarios" element={<Users />} />
          <Route path="/perfis" element={<Profiles />} />
          <Route path="/servicos-ia" element={<AiServices />} />
          <Route path="/templates" element={<TemplatesPage />} />
          <Route path="/fontes-de-dados" element={<Datasources />} />
          <Route path="/arquivos" element={<Files />} />
          <Route path="/memorias" element={<Memories />} />
          <Route path="/consumo" element={<Usage />} />
          <Route path="/integracoes" element={<Integrations />} />
          <Route path="*" element={<Navigate to={links[0]?.to ?? '/usuarios'} replace />} />
        </Routes>
      </main>
    </div>
  )
}

function Gate() {
  const { user, loading } = useAuth()
  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-400">
        Carregando…
      </div>
    )
  }
  return user ? <Shell /> : <Login />
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <Router>
          <Gate />
        </Router>
      </AuthProvider>
    </QueryClientProvider>
  )
}
