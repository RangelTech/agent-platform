import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Badge, Button, Card, EmptyState, ErrorText, Input, PageHeader, SectionIntro, Select, StatCard, Table, TableSkeleton } from '../components/ui'
import { createUser, listProfiles, listTenants, listUsers, updateUser, type User } from '../lib/api'
import { useAuth } from '../lib/auth'

export default function Users() {
  const { user } = useAuth()
  const qc = useQueryClient()
  const isMaster = user?.is_master ?? false

  const { data: users = [], isLoading, error: loadError } = useQuery({ queryKey: ['users'], queryFn: listUsers })
  const { data: profiles = [] } = useQuery({ queryKey: ['profiles'], queryFn: listProfiles })
  const { data: tenants = [] } = useQuery({
    queryKey: ['tenants'],
    queryFn: listTenants,
    enabled: isMaster,
  })

  const [form, setForm] = useState({
    email: '',
    name: '',
    password: '',
    profile_id: '',
    tenant_id: '',
  })
  const [error, setError] = useState('')

  // A user may only receive a profile from the tenant they belong to.
  const selectableProfiles = profiles.filter((p) =>
    isMaster ? p.tenant_id === form.tenant_id : p.tenant_id === user?.tenant_id,
  )

  const create = useMutation({
    mutationFn: () =>
      createUser({
        email: form.email,
        name: form.name,
        password: form.password,
        profile_id: form.profile_id || null,
        tenant_id: isMaster ? form.tenant_id || null : null,
      }),
    onSuccess: () => {
      setForm({ email: '', name: '', password: '', profile_id: '', tenant_id: '' })
      setError('')
      qc.invalidateQueries({ queryKey: ['users'] })
    },
    onError: (e: Error) => setError(e.message),
  })

  const toggle = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) =>
      updateUser(id, { is_active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['users'] }),
  })

  const [editing, setEditing] = useState<User | null>(null)
  const [editName, setEditName] = useState('')
  const [editProfile, setEditProfile] = useState('')
  const [editPassword, setEditPassword] = useState('')

  const saveEdit = useMutation({
    mutationFn: () =>
      updateUser(editing!.id, {
        name: editName,
        profile_id: editProfile || null,
        ...(editPassword ? { password: editPassword } : {}),
      }),
    onSuccess: () => {
      setEditing(null)
      setEditPassword('')
      qc.invalidateQueries({ queryKey: ['users'] })
    },
    onError: (e: Error) => setError(e.message),
  })

  function openEdit(u: User) {
    setEditing(u)
    setEditName(u.name)
    setEditProfile(u.profile_id ?? '')
    setEditPassword('')
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    create.mutate()
  }

  const profileName = (id: string | null) =>
    profiles.find((p) => p.id === id)?.name ?? '—'

  if (editing) {
    return (
      <div className="space-y-6">
        <PageHeader
          title={`Editar usuário: ${editing.email}`}
          description="Ajuste nome, perfil e credenciais sem perder o contexto operacional do tenant."
          actions={
            <Button type="button" variant="ghost" onClick={() => setEditing(null)}>
              Voltar
            </Button>
          }
        />
        <Card title="Perfil de acesso do usuário">
          <div className="space-y-6">
            <SectionIntro
              eyebrow="Edição"
              title="Atualize identidade e permissões"
              description="Troque o perfil de permissão e, se necessário, redefina a senha sem afetar outros usuários do tenant."
            />
            <form
              onSubmit={(e) => {
                e.preventDefault()
                saveEdit.mutate()
              }}
              className="grid max-w-2xl gap-4 lg:grid-cols-2"
            >
              <Input
                label="Nome completo"
                hint="Nome exibido no shell, histórico e áreas administrativas."
                required
                name="edit-name"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
              />
              <Select
                label="Perfil de permissão"
                hint="Perfis disponíveis apenas para o tenant do usuário."
                name="edit-profile"
                value={editProfile}
                onChange={(e) => setEditProfile(e.target.value)}
              >
                <option value="">Sem perfil</option>
                {profiles
                  .filter((p) => p.tenant_id === editing.tenant_id)
                  .map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
              </Select>
              <Input
                label="Nova senha"
                hint="Deixe em branco para manter a senha atual."
                type="password"
                name="edit-password"
                minLength={8}
                value={editPassword}
                onChange={(e) => setEditPassword(e.target.value)}
                className="lg:col-span-2"
              />
              <div className="lg:col-span-2">
                <ErrorText>{error}</ErrorText>
              </div>
              <div className="lg:col-span-2 flex flex-wrap items-center gap-3">
                <Button type="submit" disabled={saveEdit.isPending}>
                  {saveEdit.isPending ? 'Salvando…' : 'Salvar alterações'}
                </Button>
                <Button type="button" variant="ghost" onClick={() => setEditing(null)}>
                  Cancelar
                </Button>
              </div>
            </form>
          </div>
        </Card>
      </div>
    )
  }

  const activeCount = users.filter((entry) => entry.is_active).length
  const masterCount = users.filter((entry) => entry.is_master).length

  return (
    <div className="space-y-6">
      <PageHeader
        title="Usuários"
        description="Gerencie acessos, perfis e status operacionais com visão centralizada por tenant."
        actions={
          <Button type="submit" form="user-create-form" disabled={create.isPending}>
            {create.isPending ? 'Criando…' : 'Novo usuário'}
          </Button>
        }
      />

      <div className="grid gap-4 md:grid-cols-3">
        <StatCard label="Usuários totais" value={String(users.length)} meta="Inclui contas master e usuários dos tenants." />
        <StatCard label="Usuários ativos" value={String(activeCount)} meta="Contas aptas para autenticação e operação." />
        <StatCard label="Contas master" value={String(masterCount)} meta="Acessos globais da plataforma inteira." />
      </div>

      <Card title="Cadastrar usuário">
        <div className="space-y-6">
          <SectionIntro
            eyebrow="Acesso"
            title="Associe identidade, tenant e perfil operacional"
            description="Crie o usuário já com o perfil mais próximo do papel real para reduzir retrabalho de permissões depois."
          />
          <form id="user-create-form" onSubmit={onSubmit} className="grid gap-4 lg:grid-cols-2">
            <Input
              label="Nome completo"
              hint="Nome exibido no shell, históricos e auditorias operacionais."
              required
              name="user-name"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
            <Input
              label="E-mail"
              hint="Credencial principal para login do usuário."
              type="email"
              required
              name="user-email"
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
            />
            <Input
              label="Senha inicial"
              hint="Mínimo de 8 caracteres para o primeiro acesso."
              type="password"
              required
              minLength={8}
              name="user-password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
            />
            {isMaster && (
              <Select
                label="Empresa"
                hint="Selecione o tenant antes de escolher o perfil."
                required
                name="user-tenant"
                value={form.tenant_id}
                onChange={(e) => setForm({ ...form, tenant_id: e.target.value, profile_id: '' })}
              >
                <option value="">Selecione…</option>
                {tenants.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </Select>
            )}
            <Select
              label="Perfil de permissão"
              hint="Somente perfis compatíveis com o tenant selecionado."
              name="user-profile"
              value={form.profile_id}
              onChange={(e) => setForm({ ...form, profile_id: e.target.value })}
            >
              <option value="">Sem perfil</option>
              {selectableProfiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </Select>
            <div className="lg:col-span-2">
              <ErrorText>{error}</ErrorText>
            </div>
            <div className="flex justify-end lg:col-span-2">
              <Button type="submit" disabled={create.isPending}>
                {create.isPending ? 'Criando…' : 'Novo usuário'}
              </Button>
            </div>
          </form>
        </div>
      </Card>

      <Card title="Base de usuários" actions={<Badge ok={activeCount > 0}>{activeCount} ativos</Badge>}>
        {isLoading ? (
          <TableSkeleton columns={5} />
        ) : loadError ? (
          <ErrorText>Não foi possível carregar os dados. Recarregue a página ou tente novamente.</ErrorText>
        ) : users.length === 0 ? (
          <EmptyState
            title="Nenhum usuário cadastrado"
            description="Cadastre o primeiro usuário acima para dar acesso à plataforma."
          />
        ) : (
          <Table headers={['Usuário', 'E-mail', 'Perfil', 'Status', 'Ações']}>
            {users.map((u) => (
              <tr key={u.id} data-testid="user-row" className="transition hover:bg-[var(--brand-soft)]/40">
                <td className="px-4 py-4 font-medium text-[var(--text)]">
                  <div>
                    <p>
                      {u.name} {u.is_master && <span className="text-xs text-[var(--brand)]">(master)</span>}
                    </p>
                    <p className="mt-1 text-sm font-normal text-[var(--text-muted)]">Conta vinculada ao tenant e pronta para operar no shell.</p>
                  </div>
                </td>
                <td className="px-4 py-4 text-[var(--text-muted)]">{u.email}</td>
                <td className="px-4 py-4 text-[var(--text-muted)]">{profileName(u.profile_id)}</td>
                <td className="px-4 py-4">
                  <Badge ok={u.is_active}>{u.is_active ? 'Ativo' : 'Inativo'}</Badge>
                </td>
                <td className="px-4 py-4 text-right">
                  {!u.is_master && (
                    <div className="flex justify-end gap-2">
                      <Button variant="ghost" onClick={() => openEdit(u)}>
                        Editar
                      </Button>
                      <Button
                        variant="ghost"
                        onClick={() => toggle.mutate({ id: u.id, is_active: !u.is_active })}
                      >
                        {u.is_active ? 'Desativar' : 'Ativar'}
                      </Button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  )
}
