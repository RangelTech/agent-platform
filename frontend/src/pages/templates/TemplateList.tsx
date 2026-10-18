import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { useMemo, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  ErrorText,
  Input,
  Modal,
  PageHeader,
  Segmented,
  Table,
  TableSkeleton,
  useToast,
} from '../../components/ui'
import { useAuth } from '../../lib/auth'
import {
  archiveTemplate,
  createTemplate,
  KIND_LABEL,
  KIND_SHORT,
  listTemplates,
  normalizeKind,
  type AgentKind,
  type TemplateSummary,
} from '../../lib/templates'

type KindFilter = 'all' | AgentKind

export function editorPath(t: Pick<TemplateSummary, 'id' | 'agent_kind'>): string {
  return normalizeKind(t.agent_kind) === 'skills' ? `/templates/skills/${t.id}` : `/templates/estruturado/${t.id}`
}

const KIND_HELP: Record<AgentKind, string> = {
  structured: 'Um supervisor e especialistas configurados em campos (modelo, ferramentas, documentos).',
  skills: 'Instruções em texto Markdown organizadas em skills, com validação e versões.',
}

function NewAgentModal({ open, onClose, skillsEnabled }: { open: boolean; onClose: () => void; skillsEnabled: boolean }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const toast = useToast()
  const [kind, setKind] = useState<AgentKind>('structured')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [nameError, setNameError] = useState('')
  const [error, setError] = useState('')

  const kinds: AgentKind[] = skillsEnabled ? ['structured', 'skills'] : ['structured']

  const create = useMutation({
    mutationFn: () => createTemplate({ name: name.trim(), description: description.trim(), agent_kind: kind }),
    onSuccess: (t) => {
      qc.invalidateQueries({ queryKey: ['templates'] })
      toast(`Agente “${t.name}” criado.`)
      setName('')
      setDescription('')
      onClose()
      navigate(editorPath({ id: t.id, agent_kind: t.agent_kind ?? kind }))
    },
    onError: (e: Error) => setError(e.message),
  })

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError('')
    if (!name.trim()) {
      setNameError('Informe um nome para o agente.')
      return
    }
    if (name.trim().length > 200) {
      setNameError('O nome pode ter no máximo 200 caracteres.')
      return
    }
    setNameError('')
    create.mutate()
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      testId="new-agent-modal"
      title="Novo agente"
      description="Escolha o tipo de agente. O tipo não muda depois de criado."
    >
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        <div role="radiogroup" aria-label="Tipo de agente" className="grid gap-3">
          {kinds.map((k) => (
            <button
              key={k}
              type="button"
              role="radio"
              aria-checked={kind === k}
              data-testid={`kind-option-${k}`}
              onClick={() => setKind(k)}
              className={`rounded-2xl border px-4 py-3 text-left transition ${
                kind === k
                  ? 'border-[var(--brand)] bg-[var(--brand-soft)]'
                  : 'border-[var(--border)] hover:border-[var(--brand)]'
              }`}
            >
              <span className="block text-sm font-semibold text-[var(--text)]">{KIND_LABEL[k]}</span>
              <span className="mt-1 block text-sm leading-5 text-[var(--text-muted)]">{KIND_HELP[k]}</span>
            </button>
          ))}
        </div>
        <Input
          label="Nome"
          name="tpl-name"
          value={name}
          error={nameError}
          onChange={(e) => setName(e.target.value)}
        />
        <Input
          label="Descrição (aparece no seletor do chat)"
          name="tpl-desc"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <ErrorText>{error}</ErrorText>
        <div className="flex justify-end gap-3 pt-2">
          <Button type="button" variant="ghost" onClick={onClose} data-testid="new-agent-cancel">
            Cancelar
          </Button>
          <Button type="submit" disabled={create.isPending} data-testid="new-agent-submit">
            Criar agente
          </Button>
        </div>
      </form>
    </Modal>
  )
}

export default function TemplateList() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const toast = useToast()
  const { can, user } = useAuth()
  const skillsEnabled = Boolean(user?.is_master || user?.skills_agents_enabled)
  const { data: templates = [], isLoading, error: loadError } = useQuery({
    queryKey: ['templates'],
    queryFn: listTemplates,
  })
  const [filter, setFilter] = useState<KindFilter>('all')
  const [search, setSearch] = useState('')
  const [modalOpen, setModalOpen] = useState(false)
  const [actionError, setActionError] = useState('')
  const [archiving, setArchiving] = useState<TemplateSummary | null>(null)

  const archive = useMutation({
    mutationFn: (id: string) => archiveTemplate(id),
    onSuccess: () => {
      setActionError('')
      setArchiving(null)
      qc.invalidateQueries({ queryKey: ['templates'] })
      toast('Agente arquivado.')
    },
    onError: (e: Error) => {
      setArchiving(null)
      setActionError(e.message)
    },
  })

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase()
    return templates.filter((t) => {
      if (filter !== 'all' && normalizeKind(t.agent_kind) !== filter) return false
      if (!q) return true
      return t.name.toLowerCase().includes(q) || (t.description ?? '').toLowerCase().includes(q)
    })
  }, [templates, filter, search])

  const canCreate = can('templates', 'create')
  const canEdit = can('templates', 'edit')
  const canDelete = can('templates', 'delete') || canEdit

  return (
    <div className="space-y-6" data-testid="template-list">
      <PageHeader
        title="Agentes de IA"
        description="Monte agentes estruturados (supervisor e especialistas) ou agentes por skills (instruções em Markdown), versionados para o chat."
        actions={
          canCreate && (
            <Button type="button" data-testid="new-agent-btn" onClick={() => setModalOpen(true)}>
              <Plus size={16} aria-hidden="true" /> Novo agente
            </Button>
          )
        }
      />

      <Card>
        <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <Segmented
            label="Filtrar por tipo"
            testId="kind-filter"
            value={filter}
            onChange={(v) => setFilter(v as KindFilter)}
            items={[
              { id: 'all', label: 'Todos', testId: 'kind-filter-all' },
              { id: 'structured', label: 'Estruturados', testId: 'kind-filter-structured' },
              ...(skillsEnabled || templates.some((t) => normalizeKind(t.agent_kind) === 'skills')
                ? [{ id: 'skills', label: 'Por skills', testId: 'kind-filter-skills' }]
                : []),
            ]}
          />
          <div className="w-full sm:max-w-xs">
            <input
              type="search"
              name="template-search"
              data-testid="template-search"
              aria-label="Buscar agente"
              placeholder="Buscar por nome ou descrição"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full rounded-2xl border border-[var(--border)] bg-[var(--surface-solid)] px-4 py-3 text-sm text-[var(--text)] outline-none transition placeholder:text-[var(--text-faint)] focus:border-[var(--brand)] focus:ring-2 focus:ring-[var(--brand-soft)]"
            />
          </div>
        </div>
        <ErrorText>{actionError}</ErrorText>
        {isLoading ? (
          <TableSkeleton columns={5} />
        ) : loadError ? (
          <ErrorText>Não foi possível carregar os agentes. Recarregue a página ou tente novamente.</ErrorText>
        ) : visible.length === 0 ? (
          templates.length === 0 ? (
            <EmptyState
              title="Nenhum agente criado ainda"
              description="Crie o primeiro agente para escolher no chat."
              action={
                canCreate ? (
                  <Button type="button" onClick={() => setModalOpen(true)}>
                    Novo agente
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <EmptyState title="Nenhum agente encontrado" description="Ajuste o filtro ou a busca." />
          )
        ) : (
          <Table
            headers={[
              'Nome',
              'Tipo',
              { label: 'Versão', className: 'hidden sm:table-cell' },
              { label: 'Atualizado', className: 'hidden sm:table-cell' },
              '',
            ]}
          >
            {visible.map((t) => {
              const kind = normalizeKind(t.agent_kind)
              return (
                <tr
                  key={t.id}
                  data-testid="template-row"
                  data-kind={kind}
                  className="transition hover:bg-[var(--brand-soft)]"
                >
                  <td className="px-3 py-2">
                    <span className="block text-[var(--text)]">{t.name}</span>
                    {t.description && <span className="block text-xs text-[var(--text-muted)]">{t.description}</span>}
                  </td>
                  <td className="px-3 py-2 text-[var(--text-muted)]" data-testid="template-kind">
                    {KIND_SHORT[kind]}
                  </td>
                  <td className="hidden px-3 py-2 sm:table-cell">
                    {t.active_version_number ? (
                      <Badge ok>v{t.active_version_number}</Badge>
                    ) : (
                      <Badge ok={false}>sem deploy</Badge>
                    )}
                  </td>
                  <td className="hidden px-3 py-2 text-[var(--text-muted)] sm:table-cell">
                    {t.updated_at ? new Date(t.updated_at).toLocaleDateString('pt-BR') : '—'}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <div className="flex flex-wrap justify-end gap-2">
                      {canEdit && (
                        <Button variant="ghost" data-testid="template-edit" onClick={() => navigate(editorPath(t))}>
                          Editar
                        </Button>
                      )}
                      {canDelete && (
                        <Button
                          variant="ghost"
                          data-testid="template-archive"
                          disabled={Boolean(t.system_key) || archive.isPending}
                          title={t.system_key ? 'Agentes de sistema não podem ser arquivados.' : undefined}
                          onClick={() => setArchiving(t)}
                        >
                          Arquivar
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              )
            })}
          </Table>
        )}
      </Card>

      <ConfirmDialog
        open={archiving !== null}
        title="Arquivar agente"
        message={`Arquivar o agente “${archiving?.name ?? ''}”? Ele deixa de aparecer no chat e na lista.`}
        confirmLabel="Arquivar"
        danger
        busy={archive.isPending}
        onClose={() => setArchiving(null)}
        onConfirm={() => archiving && archive.mutate(archiving.id)}
        testId="archive-dialog"
      />

      {modalOpen && <NewAgentModal open onClose={() => setModalOpen(false)} skillsEnabled={skillsEnabled} />}
    </div>
  )
}
