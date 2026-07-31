import { useMemo, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorText,
  Input,
  PageHeader,
  Skeleton,
} from '../components/ui'
import { api, ApiError } from '../lib/api'
import { useAuth } from '../lib/auth'
import { fadeUp, staggerContainer } from '../lib/motion'

interface CredentialField {
  key: string
  label: string
  secret: boolean
}

interface CatalogItem {
  id: string
  slug: string
  name: string
  description: string
  category: string
  icon: string
  server_url: string
  required_credentials: CredentialField[]
  is_native: boolean
  native_key: string
  is_active: boolean
}

interface Activation {
  id: string
  item_id: string
  configured_fields: string[]
  template_ids: string[]
  is_active: boolean
}

export default function McpStore() {
  const qc = useQueryClient()
  const { user } = useAuth()
  const isMaster = user?.is_master ?? false

  const {
    data: catalog = [],
    isLoading,
    error: loadError,
  } = useQuery({
    queryKey: ['mcp-catalog'],
    queryFn: () => api<CatalogItem[]>('/mcp-store/catalog'),
  })
  const { data: activations = [] } = useQuery({
    queryKey: ['mcp-activations'],
    queryFn: () => api<Activation[]>('/mcp-store/activations'),
  })

  const activeByItem = useMemo(() => {
    const map = new Map<string, Activation>()
    activations.filter((a) => a.is_active).forEach((a) => map.set(a.item_id, a))
    return map
  }, [activations])

  const [openItem, setOpenItem] = useState<CatalogItem | null>(null)
  const [values, setValues] = useState<Record<string, string>>({})
  const [error, setError] = useState('')

  const activate = useMutation({
    mutationFn: (item: CatalogItem) =>
      api<Activation>(`/mcp-store/activations/${item.id}`, {
        method: 'PUT',
        body: JSON.stringify({ credentials: values, template_ids: [], is_active: true }),
      }),
    onSuccess: () => {
      setOpenItem(null)
      setValues({})
      qc.invalidateQueries({ queryKey: ['mcp-activations'] })
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : 'Falha ao ativar'),
  })

  const deactivate = useMutation({
    mutationFn: (itemId: string) =>
      api(`/mcp-store/activations/${itemId}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['mcp-activations'] }),
  })

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError('')
    if (openItem) activate.mutate(openItem)
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="MCP Store"
        description="Integrações verificadas pela plataforma. Ative o que sua empresa precisa e informe apenas as credenciais — a configuração técnica do servidor já vem pronta."
        actions={<Badge ok={activeByItem.size > 0}>{activeByItem.size} ativas</Badge>}
      />

      {isLoading ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          <Skeleton className="h-44" />
          <Skeleton className="h-44" />
          <Skeleton className="h-44" />
        </div>
      ) : loadError ? (
        <ErrorText>Não foi possível carregar o catálogo. Recarregue a página.</ErrorText>
      ) : catalog.length === 0 ? (
        <EmptyState
          title="Catálogo vazio"
          description={
            isMaster
              ? 'Publique o primeiro item para que as empresas possam ativá-lo.'
              : 'Nenhuma integração publicada ainda pela plataforma.'
          }
        />
      ) : (
        <motion.div
          variants={staggerContainer(0.05)}
          initial="initial"
          animate="animate"
          className="grid gap-4 md:grid-cols-2 xl:grid-cols-3"
        >
          {catalog.map((item) => {
            const activation = activeByItem.get(item.id)
            return (
              <motion.article
                key={item.id}
                variants={fadeUp}
                data-testid="catalog-item"
                className="flex flex-col gap-4 rounded-[24px] border border-[var(--border)] bg-[var(--surface-elevated)] p-5"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-3">
                    <span
                      aria-hidden="true"
                      className="flex h-10 w-10 items-center justify-center rounded-2xl border border-[var(--border)] bg-[var(--surface-solid)] text-lg"
                    >
                      {item.icon || '◈'}
                    </span>
                    <div>
                      <p className="text-sm font-semibold text-[var(--text)]">{item.name}</p>
                      <p className="text-[11px] uppercase tracking-[0.18em] text-[var(--text-faint)]">
                        {item.category}
                      </p>
                    </div>
                  </div>
                  {item.is_native ? (
                    <Badge ok>Nativa</Badge>
                  ) : (
                    <Badge ok={Boolean(activation)}>{activation ? 'Ativa' : 'Inativa'}</Badge>
                  )}
                </div>

                <p className="flex-1 text-sm leading-6 text-[var(--text-muted)]">{item.description}</p>

                {item.is_native ? (
                  <p className="text-sm text-[var(--text-faint)]">
                    Já faz parte da plataforma — configure na tela dedicada da funcionalidade.
                  </p>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    <Button
                      type="button"
                      variant={activation ? 'ghost' : 'primary'}
                      onClick={() => {
                        setError('')
                        setValues({})
                        setOpenItem(item)
                      }}
                    >
                      {activation ? 'Reconfigurar' : 'Ativar'}
                    </Button>
                    {activation && (
                      <Button
                        type="button"
                        variant="danger"
                        onClick={() => deactivate.mutate(item.id)}
                      >
                        Desativar
                      </Button>
                    )}
                  </div>
                )}
              </motion.article>
            )
          })}
        </motion.div>
      )}

      {openItem && (
        <Card title={`Configurar ${openItem.name}`}>
          <form onSubmit={onSubmit} className="grid gap-4 lg:grid-cols-2">
            {openItem.required_credentials.length === 0 && (
              <p className="lg:col-span-2 text-sm text-[var(--text-muted)]">
                Esta integração não exige credenciais — basta ativar.
              </p>
            )}
            {openItem.required_credentials.map((field) => (
              <Input
                key={field.key}
                label={field.label}
                name={`mcp-${field.key}`}
                type={field.secret ? 'password' : 'text'}
                autoComplete="off"
                value={values[field.key] ?? ''}
                onChange={(e) => setValues((v) => ({ ...v, [field.key]: e.target.value }))}
              />
            ))}
            <div className="lg:col-span-2 flex flex-wrap items-center gap-3">
              <Button type="submit" disabled={activate.isPending}>
                {activate.isPending ? 'Ativando…' : 'Ativar integração'}
              </Button>
              <Button type="button" variant="ghost" onClick={() => setOpenItem(null)}>
                Cancelar
              </Button>
            </div>
            <div className="lg:col-span-2">
              <ErrorText>{error}</ErrorText>
            </div>
          </form>
        </Card>
      )}

      {isMaster && <CatalogAdmin />}
    </div>
  )
}

/** Curadoria do catálogo: só o administrador da plataforma publica itens. */
function CatalogAdmin() {
  const qc = useQueryClient()
  const [form, setForm] = useState({
    slug: '',
    name: '',
    description: '',
    category: 'geral',
    icon: '',
    server_url: '',
    auth_token_template: '',
    credential_key: '',
    credential_label: '',
  })
  const [error, setError] = useState('')

  const create = useMutation({
    mutationFn: () =>
      api('/mcp-store/catalog', {
        method: 'POST',
        body: JSON.stringify({
          slug: form.slug,
          name: form.name,
          description: form.description,
          category: form.category,
          icon: form.icon,
          server_url: form.server_url,
          auth_token_template: form.auth_token_template,
          required_credentials: form.credential_key
            ? [{ key: form.credential_key, label: form.credential_label || form.credential_key, secret: true }]
            : [],
        }),
      }),
    onSuccess: () => {
      setForm({ ...form, slug: '', name: '', description: '', server_url: '', credential_key: '' })
      qc.invalidateQueries({ queryKey: ['mcp-catalog'] })
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : 'Falha ao publicar'),
  })

  return (
    <Card title="Publicar item no catálogo">
      <form
        onSubmit={(e) => {
          e.preventDefault()
          setError('')
          create.mutate()
        }}
        className="grid gap-4 lg:grid-cols-2"
      >
        <Input
          label="Slug"
          hint="Prefixo das tools no agente: ext_<slug>_<tool>."
          name="item-slug"
          value={form.slug}
          onChange={(e) => setForm({ ...form, slug: e.target.value })}
        />
        <Input
          label="Nome"
          name="item-name"
          value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
        />
        <Input
          label="Descrição"
          name="item-description"
          className="lg:col-span-2"
          value={form.description}
          onChange={(e) => setForm({ ...form, description: e.target.value })}
        />
        <Input
          label="URL do servidor MCP"
          hint="Use {{credential:CHAVE}} para injetar a credencial do tenant."
          name="item-url"
          value={form.server_url}
          onChange={(e) => setForm({ ...form, server_url: e.target.value })}
        />
        <Input
          label="Token de autorização (template)"
          hint="Opcional. Também aceita {{credential:CHAVE}}."
          name="item-auth"
          value={form.auth_token_template}
          onChange={(e) => setForm({ ...form, auth_token_template: e.target.value })}
        />
        <Input
          label="Credencial exigida (chave)"
          hint="Opcional: o tenant preenche este campo ao ativar."
          name="item-credential-key"
          value={form.credential_key}
          onChange={(e) => setForm({ ...form, credential_key: e.target.value })}
        />
        <Input
          label="Credencial exigida (rótulo)"
          name="item-credential-label"
          value={form.credential_label}
          onChange={(e) => setForm({ ...form, credential_label: e.target.value })}
        />
        <div className="lg:col-span-2 flex items-center gap-3">
          <Button type="submit" disabled={create.isPending}>
            {create.isPending ? 'Publicando…' : 'Publicar item'}
          </Button>
        </div>
        <div className="lg:col-span-2">
          <ErrorText>{error}</ErrorText>
        </div>
      </form>
    </Card>
  )
}
