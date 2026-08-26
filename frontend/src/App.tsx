import { useEffect, useMemo, useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AnimatePresence, motion } from 'framer-motion'
import {
  NavLink,
  Navigate,
  Route,
  BrowserRouter as Router,
  Routes,
  useLocation,
} from 'react-router-dom'
import { Button } from './components/ui'
import { AuthProvider, useAuth } from './lib/auth'
import { fadeIn, pageVariants } from './lib/motion'
import AiServices from './pages/AiServices'
import Chat from './pages/Chat'
import Dashboard from './pages/Dashboard'
import Datasources from './pages/Datasources'
import Files from './pages/Files'
import InstallationSecrets from './pages/InstallationSecrets'
import Integrations from './pages/Integrations'
import Legal from './pages/Legal'
import Login from './pages/Login'
import McpStore from './pages/McpStore'
import Memories from './pages/Memories'
import Omnichannel from './pages/Omnichannel'
import OAuthCallback from './pages/OAuthCallback'
import Payments from './pages/Payments'
import Credenciais from './pages/Credenciais'
import Customize from './pages/Customize'
import CustomTools from './pages/CustomTools'
import Profiles from './pages/Profiles'
import RatendeConnector from './pages/RatendeConnector'
import TemplatesPage from './pages/Templates'
import Tenants from './pages/Tenants'
import Usage from './pages/Usage'
import Users from './pages/Users'

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
})

function Shell() {
  const { user, signOut, can } = useAuth()
  const [navOpen, setNavOpen] = useState(false)

  // Apply per-tenant branding to the whole shell.
  const brand = user?.branding
  useEffect(() => {
    const root = document.documentElement
    // Woot Blue — Chatwoot's own brand color (confirmed live at
    // chat.rangeltech.net/app/login theme-color/msapplication-TileColor meta
    // tags: #1f93ff), used here so the two products read as one system.
    root.dataset.theme = brand?.theme === 'dark' ? 'dark' : 'light'
    root.style.setProperty('--brand', brand?.color || '#1f93ff')
  }, [brand?.theme, brand?.color])
  const location = useLocation()
  const isChat = location.pathname === '/chat'
  const isDashboard = location.pathname === '/dashboard'

  const links = [
    // The master administers the platform and has no tenant to chat in.
    { to: '/dashboard', label: 'Início', show: true },
    { to: '/chat', label: 'Chat', show: !(user?.is_master ?? false) },
    { to: '/empresas', label: 'Empresas', show: user?.is_master ?? false },
    { to: '/usuarios', label: 'Usuários', show: can('users', 'view') },
    { to: '/perfis', label: 'Perfis', show: can('user_profiles', 'view') },
    { to: '/servicos-ia', label: 'Serviços de IA', show: can('ai_services', 'view') },
    { to: '/templates', label: 'Templates', show: can('templates', 'view') },
    { to: '/custom-tools', label: 'Custom Tools', show: !(user?.is_master ?? false) && can('templates', 'edit') },
    { to: '/fontes-de-dados', label: 'Fontes de dados', show: can('datasources', 'view') },
    { to: '/arquivos', label: 'Arquivos', show: can('files', 'view') },
    { to: '/memorias', label: 'Memórias', show: !(user?.is_master ?? false) },
    { to: '/consumo', label: 'Consumo', show: can('usage', 'view') },
    { to: '/integracoes', label: 'Integrações', show: can('integrations', 'view') },
    { to: '/pagamentos', label: 'Pagamentos', show: can('payments', 'view') },
    { to: '/credenciais', label: 'Credenciais', show: can('email_accounts', 'view') || can('google_accounts', 'view') },
    { to: '/mcp-store', label: 'MCP Store', show: can('mcp_store', 'view') },
    { to: '/atendimento', label: 'Atendimento', show: can('omnichannel', 'view') },
    { to: '/conector', label: 'RAtende Connector', show: !(user?.is_master ?? false) },
    // Chave da instalação inteira (app da Meta, busca na web): é do master, não
    // de uma empresa — por isso não aparece para quem administra um tenant.
    { to: '/chaves-da-instalacao', label: 'Chaves da instalação', show: user?.is_master ?? false },
    { to: '/personalizar', label: 'Personalizar', show: !(user?.is_master ?? false) && can('users', 'edit') },
  ].filter((l) => l.show)

  const navLinks = useMemo(
    () =>
      links.map((l) => (
        <NavLink
          key={l.to}
          to={l.to}
          onClick={() => setNavOpen(false)}
          className={({ isActive }) =>
            `group flex items-center justify-between rounded-2xl px-4 py-3 text-sm font-medium transition ${
              isActive
                ? 'bg-[var(--brand-soft)] text-[var(--text)] shadow-[0_18px_48px_-28px_rgba(15,23,42,0.4)]'
                : 'text-[var(--text-muted)] hover:bg-[var(--surface-elevated)] hover:text-[var(--text)]'
            }`
          }
        >
          <span>{l.label}</span>
        </NavLink>
      )),
    [links],
  )

  return (
    <div className="flex h-[100dvh] min-h-screen flex-col bg-[var(--bg)] text-[var(--text)]">
      <header className="sticky top-0 z-40 flex-none border-b border-[var(--border)] bg-[color-mix(in_srgb,var(--bg)_82%,transparent)] backdrop-blur-xl">
        <div className="mx-auto flex max-w-7xl items-center gap-3 px-4 py-3 sm:px-6">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-3">
              <span className="flex min-w-0 items-center gap-3 text-sm font-semibold text-[var(--text)]">
                {brand?.has_logo && (
                  <img
                    src={`/api/tenants/branding/logo/${brand.tenant_key}`}
                    alt=""
                    className="h-9 w-9 rounded-2xl border border-[var(--border)] bg-[var(--surface-solid)] object-contain p-1"
                  />
                )}
                <span className="min-w-0">
                  <span className="block truncate">{brand?.name || 'RAgentes'}</span>
                  <span className="block text-xs font-normal text-[var(--text-faint)]">
                    {isChat ? 'Workspace conversacional' : 'Painel operacional'}
                  </span>
                </span>
              </span>
            </div>
          </div>

          <div className="hidden items-center gap-3 sm:flex">
            <div className="text-right">
              <span data-testid="current-user" className="block text-sm font-medium text-[var(--text)]">
                {user?.name}
              </span>
              <span className="block text-xs text-[var(--text-faint)]">Sessão autenticada</span>
            </div>
            <Button variant="ghost" onClick={signOut}>
              Sair
            </Button>
          </div>

          <Button
            type="button"
            variant="ghost"
            className="h-11 w-11 rounded-2xl px-0"
            onClick={() => setNavOpen((open) => !open)}
            aria-label={navOpen ? 'Fechar navegação' : 'Abrir navegação'}
            aria-expanded={navOpen}
          >
            <span className="flex flex-col gap-1.5">
              <span className="h-0.5 w-4 rounded-full bg-current" />
              <span className="h-0.5 w-4 rounded-full bg-current" />
              <span className="h-0.5 w-4 rounded-full bg-current" />
            </span>
          </Button>
        </div>
      </header>

      {navOpen && (
        <div className="fixed inset-0 z-50 flex bg-[rgba(2,6,23,0.52)] backdrop-blur-sm">
          <button
            type="button"
            className="hidden flex-1 lg:block"
            aria-label="Fechar navegação"
            onClick={() => setNavOpen(false)}
          />
          <aside className="ml-auto flex h-full w-full max-w-sm flex-col border-l border-[var(--border)] bg-[var(--surface-solid)]/96 p-4 shadow-[0_40px_120px_-52px_rgba(15,23,42,0.72)] backdrop-blur-2xl">
            <div className="mb-4 flex items-start justify-between gap-3">
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--text-faint)]">Navegação</p>
                <h2 className="mt-2 text-xl font-semibold text-[var(--text)]">Menu principal</h2>
                <p className="mt-1 text-sm leading-6 text-[var(--text-muted)]">Acesso compacto às rotas disponíveis neste tenant.</p>
              </div>
              <Button
                type="button"
                variant="ghost"
                data-testid="nav-close"
                aria-label="Fechar navegação"
                className="h-11 w-11 rounded-2xl px-0"
                onClick={() => setNavOpen(false)}
              >
                ✕
              </Button>
            </div>
            <nav data-testid="nav-drawer" className="flex-1 space-y-2 overflow-y-auto">
              {navLinks}
              {/* Quem não administra a empresa vê um menu curto por desenho.
                  Dizer isso evita procurar uma tela que o perfil não alcança. */}
              {!user?.is_master && !can('users', 'edit') && (
                <p
                  data-testid="nav-scope-note"
                  className="mt-4 rounded-2xl border border-dashed border-[var(--border-strong)] bg-[var(--surface-soft)] px-4 py-3 text-sm leading-6 text-[var(--text-muted)]"
                >
                  Seu perfil dá acesso apenas a estas áreas. Peça a um administrador da empresa para
                  ampliar suas permissões.
                </p>
              )}
            </nav>
            <div className="mt-4 rounded-[24px] border border-[var(--border)] bg-[var(--surface)] p-4 sm:hidden">
              <p data-testid="current-user-drawer" className="text-sm font-medium text-[var(--text)]">
                {user?.name}
              </p>
              <p className="mt-1 text-xs text-[var(--text-faint)]">Sessão autenticada</p>
              <Button variant="ghost" onClick={signOut} className="mt-4 w-full">
                Sair
              </Button>
            </div>
          </aside>
        </div>
      )}

      {/* O chat ocupa a altura restante e rola por dentro; as demais rotas
          rolam a página inteira, como qualquer tela de conteúdo. */}
      <main
        className={
          isChat
            ? 'mx-auto flex w-full min-h-0 flex-1 max-w-[1920px] overflow-hidden px-0 sm:px-4 sm:py-4'
            : `mx-auto w-full flex-1 overflow-y-auto px-4 py-6 ${isDashboard ? 'max-w-7xl' : 'max-w-6xl'}`
        }
      >
        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={location.pathname}
            variants={pageVariants}
            initial="initial"
            animate="animate"
            exit="exit"
            className={isChat ? 'flex min-h-0 w-full flex-1' : undefined}
          >
            <Routes location={location}>
              <Route path="/dashboard" element={user?.is_master ? <Tenants /> : <Dashboard />} />
              <Route path="/chat" element={<Chat />} />
              <Route path="/empresas" element={user?.is_master ? <Tenants /> : <Navigate to="/usuarios" />} />
              <Route path="/usuarios" element={<Users />} />
              <Route path="/perfis" element={<Profiles />} />
              <Route path="/servicos-ia" element={<AiServices />} />
              <Route path="/templates" element={<TemplatesPage />} />
              <Route path="/custom-tools" element={<CustomTools />} />
              <Route path="/fontes-de-dados" element={<Datasources />} />
              <Route path="/arquivos" element={<Files />} />
              <Route path="/memorias" element={<Memories />} />
              <Route path="/consumo" element={<Usage />} />
              <Route path="/integracoes" element={<Integrations />} />
              <Route path="/pagamentos" element={<Payments />} />
              <Route path="/credenciais" element={<Credenciais />} />
              <Route path="/mcp-store" element={<McpStore />} />
              <Route path="/atendimento" element={<Omnichannel />} />
              <Route path="/conector" element={<RatendeConnector />} />
              <Route
                path="/chaves-da-instalacao"
                element={user?.is_master ? <InstallationSecrets /> : <Navigate to="/usuarios" />}
              />
              <Route path="/personalizar" element={<Customize />} />
              <Route path="*" element={<Navigate to={links[0]?.to ?? '/usuarios'} replace />} />
            </Routes>
          </motion.div>
        </AnimatePresence>
      </main>
    </div>
  )
}

function Gate() {
  const { user, loading } = useAuth()
  if (loading) {
    return (
      <motion.div
        variants={fadeIn}
        initial="initial"
        animate="animate"
        className="flex min-h-screen items-center justify-center bg-[var(--bg)] text-[var(--text-muted)]"
      >
        Carregando…
      </motion.div>
    )
  }
  return user ? <Shell /> : <Login />
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <Router>
          {/* Páginas legais (produto-01 da mega spec): públicas, sem login,
              exigidas pelo App Review da Meta. Ficam FORA do Gate de propósito
              — nunca devem depender de sessão autenticada. */}
          <Routes>
            <Route path="/politica-de-privacidade" element={<Legal page="privacidade" />} />
            <Route path="/termos-de-uso" element={<Legal page="termos" />} />
            <Route path="/exclusao-de-dados" element={<Legal page="exclusao" />} />
            {/* Produto-08 — callback do OAuth de assinatura (Claude/Codex/
                Antigravity/Gemini CLI/Cline): o provedor redireciona pra cá
                antes de qualquer sessão nossa existir nessa aba, mesmo
                motivo das páginas legais acima ficarem fora do Gate. */}
            <Route path="/oauth/callback" element={<OAuthCallback />} />
            <Route path="*" element={<Gate />} />
          </Routes>
        </Router>
      </AuthProvider>
    </QueryClientProvider>
  )
}
