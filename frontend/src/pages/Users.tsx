import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Badge, Button, Card, ErrorText, Input, Select, Table } from '../components/ui'
import { createUser, listProfiles, listTenants, listUsers, updateUser } from '../lib/api'
import { useAuth } from '../lib/auth'

export default function Users() {
  const { user } = useAuth()
  const qc = useQueryClient()
  const isMaster = user?.is_master ?? false

  const { data: users = [], isLoading } = useQuery({ queryKey: ['users'], queryFn: listUsers })
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

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    create.mutate()
  }

  const profileName = (id: string | null) =>
    profiles.find((p) => p.id === id)?.name ?? '—'

  return (
    <div className="space-y-6">
      <Card title="Novo usuário">
        <form onSubmit={onSubmit} className="grid gap-3 sm:grid-cols-2">
          <Input
            label="Nome"
            required
            name="user-name"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
          <Input
            label="E-mail"
            type="email"
            required
            name="user-email"
            value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })}
          />
          <Input
            label="Senha (mínimo 8 caracteres)"
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
          <div className="sm:col-span-2 flex items-center gap-3">
            <Button type="submit" disabled={create.isPending}>
              Criar usuário
            </Button>
            <ErrorText>{error}</ErrorText>
          </div>
        </form>
      </Card>

      <Card title="Usuários">
        {isLoading ? (
          <p className="text-sm text-slate-400">Carregando…</p>
        ) : (
          <Table headers={['Nome', 'E-mail', 'Perfil', 'Status', '']}>
            {users.map((u) => (
              <tr key={u.id} data-testid="user-row">
                <td className="px-3 py-2 text-slate-200">
                  {u.name} {u.is_master && <span className="text-xs text-indigo-400">(master)</span>}
                </td>
                <td className="px-3 py-2 text-slate-400">{u.email}</td>
                <td className="px-3 py-2 text-slate-400">{profileName(u.profile_id)}</td>
                <td className="px-3 py-2">
                  <Badge ok={u.is_active}>{u.is_active ? 'Ativo' : 'Inativo'}</Badge>
                </td>
                <td className="px-3 py-2 text-right">
                  {!u.is_master && (
                    <Button
                      variant="ghost"
                      onClick={() => toggle.mutate({ id: u.id, is_active: !u.is_active })}
                    >
                      {u.is_active ? 'Desativar' : 'Ativar'}
                    </Button>
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
