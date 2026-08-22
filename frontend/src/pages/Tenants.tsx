import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Badge, Button, Card, EmptyState, ErrorText, Input, PageHeader, SectionIntro, StatCard, Table, TableSkeleton } from '../components/ui'
import { createTenant, listTenants, updateTenant } from '../lib/api'

export default function Tenants() {
  const qc = useQueryClient()
  const { data: tenants = [], isLoading, error: loadError } = useQuery({
    queryKey: ['tenants'],
    queryFn: listTenants,
  })
  const [name, setName] = useState('')
  const [key, setKey] = useState('')
  const [adminName, setAdminName] = useState('')
  const [adminEmail, setAdminEmail] = useState('')
  const [adminPassword, setAdminPassword] = useState('')
  const [error, setError] = useState('')

  const create = useMutation({
    mutationFn: () =>
      createTenant({
        name,
        tenant_key: key,
        admin_name: adminName || null,
        admin_email: adminEmail || null,
        admin_password: adminPassword || null,
      } as never),
    onSuccess: () => {
      setName('')
      setKey('')
      setAdminName('')
      setAdminEmail('')
      setAdminPassword('')
      setError('')
      qc.invalidateQueries({ queryKey: ['tenants'] })
    },
    onError: (e: Error) => setError(e.message),
  })

  const toggle = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) =>
      updateTenant(id, { is_active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tenants'] }),
  })

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    create.mutate()
  }

  const activeCount = tenants.filter((tenant) => tenant.is_active).length

  return (
    <div className="space-y-6">
      <PageHeader
        title="Empresas"
        description="Crie tenants, distribua acessos iniciais e acompanhe o status operacional da plataforma em um único painel."
        actions={
          <Button type="submit" form="tenant-create-form" disabled={create.isPending}>
            {create.isPending ? 'Criando…' : 'Nova empresa'}
          </Button>
        }
      />

      <div className="grid gap-4 md:grid-cols-3">
        <StatCard label="Empresas totais" value={String(tenants.length)} meta="Inclui ambientes ativos e pausados." />
        <StatCard label="Empresas ativas" value={String(activeCount)} meta="Tenants prontos para operação e acesso." />
        <StatCard
          label="Provisionamento inicial"
          value={adminEmail ? 'Com admin' : 'Sem admin'}
          meta="Defina credenciais do responsável já no onboarding do tenant."
        />
      </div>

      <Card title="Provisionar nova empresa">
        <div className="space-y-6">
          <SectionIntro
            eyebrow="Onboarding"
            title="Cadastre o tenant e o administrador inicial"
            description="Use uma chave estável para URLs, branding e integrações futuras. O admin inicial é opcional, mas acelera a ativação do workspace."
          />
          <form id="tenant-create-form" onSubmit={onSubmit} className="grid gap-4 lg:grid-cols-2">
            <Input
              label="Nome da empresa"
              hint="Nome exibido no login, branding e áreas administrativas."
              required
              value={name}
              name="tenant-name"
              onChange={(e) => setName(e.target.value)}
            />
            <Input
              label="Chave do tenant"
              hint="Use ao menos 3 caracteres: minúsculas, números e hífen. Ex.: hamburgueria-demo."
              required
              minLength={3}
              pattern="[a-z0-9][a-z0-9-]*"
              title="Ao menos 3 caracteres: minúsculas, números e hífen"
              value={key}
              name="tenant-key"
              onChange={(e) => setKey(e.target.value)}
            />
            <Input
              label="Nome do admin inicial"
              hint="Responsável pela ativação inicial do workspace."
              name="admin-name"
              value={adminName}
              onChange={(e) => setAdminName(e.target.value)}
            />
            <Input
              label="E-mail do admin"
              hint="Esse usuário receberá o primeiro acesso do tenant."
              type="email"
              name="admin-email"
              value={adminEmail}
              onChange={(e) => setAdminEmail(e.target.value)}
            />
            <Input
              label="Senha provisória"
              hint="Defina uma senha inicial com no mínimo 8 caracteres."
              type="password"
              name="admin-password"
              value={adminPassword}
              onChange={(e) => setAdminPassword(e.target.value)}
              className="lg:col-span-2"
            />
            <div className="lg:col-span-2">
              <ErrorText>{error}</ErrorText>
            </div>
          </form>
        </div>
      </Card>

      <Card title="Base de tenants" actions={<Badge ok={activeCount > 0}>{activeCount} ativas</Badge>}>
        {isLoading ? (
          <TableSkeleton columns={4} />
        ) : loadError ? (
          <ErrorText>Não foi possível carregar os dados. Recarregue a página ou tente novamente.</ErrorText>
        ) : tenants.length === 0 ? (
          <EmptyState
            title="Nenhuma empresa cadastrada"
            description="Crie a primeira empresa acima para começar a provisionar usuários e templates."
          />
        ) : (
          <Table headers={['Empresa', 'Chave', 'Status', 'Ação']}>
            {tenants.map((t) => (
              <tr key={t.id} data-testid="tenant-row" className="transition hover:bg-[var(--brand-soft)]/40">
                <td className="px-4 py-4">
                  <div>
                    <p className="font-medium text-[var(--text)]">{t.name}</p>
                    <p className="mt-1 text-sm text-[var(--text-muted)]">Tenant pronto para usuários, templates e integrações.</p>
                  </div>
                </td>
                <td className="px-4 py-4 font-mono text-xs text-[var(--text-muted)]">{t.tenant_key}</td>
                <td className="px-4 py-4">
                  <Badge ok={t.is_active}>{t.is_active ? 'Ativa' : 'Inativa'}</Badge>
                </td>
                <td className="px-4 py-4 text-right">
                  <Button
                    variant="ghost"
                    onClick={() => toggle.mutate({ id: t.id, is_active: !t.is_active })}
                  >
                    {t.is_active ? 'Desativar' : 'Ativar'}
                  </Button>
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  )
}
