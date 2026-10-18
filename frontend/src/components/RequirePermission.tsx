import type { ReactNode } from 'react'
import { useAuth } from '../lib/auth'
import { PageHeader } from './ui'

/**
 * Guarda de rota: sem a permissão, mostra uma página de acesso negado em vez
 * do conteúdo (o menu já esconde o link; aqui cobre a URL digitada).
 * `skillsAgents` exige também que a empresa tenha agentes por skills.
 */
export function RequirePermission({
  resource,
  action = 'view',
  skillsAgents = false,
  children,
}: {
  resource: string
  action?: string
  skillsAgents?: boolean
  children: ReactNode
}) {
  const { can, user } = useAuth()
  const allowed = can(resource, action)
  const featureOn = !skillsAgents || Boolean(user?.is_master || user?.skills_agents_enabled)
  if (allowed && featureOn) return <>{children}</>
  return (
    <div className="space-y-6" data-testid="forbidden">
      <PageHeader title="Acesso restrito" />
      <p role="alert" className="rounded-2xl border border-[var(--border)] bg-[var(--surface-soft)] px-4 py-4 text-sm text-[var(--text-muted)]">
        {allowed
          ? 'Recurso não habilitado para sua empresa.'
          : 'Você não tem permissão para acessar esta área. Peça a um administrador da empresa para ampliar o seu perfil.'}
      </p>
    </div>
  )
}
