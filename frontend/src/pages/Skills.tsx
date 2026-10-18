import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { MarkdownEditor } from '../components/MarkdownEditor'
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  ErrorText,
  Modal,
  PageHeader,
  Segmented,
  Select,
  Table,
  TableSkeleton,
  Tabs,
  useToast,
} from '../components/ui'
import { useAuth } from '../lib/auth'
import {
  actOnMySkill,
  deleteMySkill,
  getMySkill,
  listCompanySkills,
  listMySkills,
  STATUS_LABEL,
  updateMySkill,
  type UserSkill,
  type UserSkillStatus,
} from '../lib/skills'
import { listTemplates, normalizeKind } from '../lib/templates'

type TabId = 'company' | 'learned'
type StatusFilter = 'all' | UserSkillStatus

const dateBR = (v: string) => (v ? new Date(v).toLocaleDateString('pt-BR') : '—')

function CompanyTab() {
  const navigate = useNavigate()
  const { can } = useAuth()
  const { data: skills = [], isLoading, error } = useQuery({ queryKey: ['skills-company'], queryFn: listCompanySkills })
  const { data: templates = [] } = useQuery({ queryKey: ['templates'], queryFn: listTemplates })
  const [pickOpen, setPickOpen] = useState(false)
  const [pickId, setPickId] = useState('')

  const canEdit = can('templates', 'edit') && (can('skills', 'edit') || can('skills', 'create'))
  const skillsTemplates = templates.filter((t) => normalizeKind(t.agent_kind) === 'skills')

  return (
    <div className="space-y-4" data-testid="skills-company">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-sm leading-6 text-[var(--text-muted)]">
          Skills publicadas nos agentes por skills da sua empresa. Para mudar uma skill, edite o agente que a contém.
        </p>
        {can('skills', 'create') && can('templates', 'edit') && (
          <Button
            type="button"
            data-testid="company-new-skill"
            onClick={() => {
              setPickId(skillsTemplates[0]?.id ?? '')
              setPickOpen(true)
            }}
          >
            + Nova skill
          </Button>
        )}
      </div>
      {isLoading ? (
        <TableSkeleton columns={4} />
      ) : error ? (
        <ErrorText>{error instanceof Error ? error.message : 'Não foi possível carregar as skills.'}</ErrorText>
      ) : skills.length === 0 ? (
        <EmptyState
          title="Nenhuma skill da empresa ainda"
          description="Publique um agente por skills para ver as skills dele aqui."
        />
      ) : (
        <Table headers={['Skill', 'Quando usar', 'Agente', 'Atualizada', '']}>
          {skills.map((s) => (
            <tr key={`${s.template_id}:${s.name}`} data-testid="company-skill-row" data-name={s.name} className="transition hover:bg-[var(--brand-soft)]">
              <td className="px-3 py-2 font-medium text-[var(--text)]">{s.name}</td>
              <td className="px-3 py-2 text-[var(--text-muted)]">{s.description}</td>
              <td className="px-3 py-2 text-[var(--text-muted)]">{s.template_name}</td>
              <td className="px-3 py-2 text-[var(--text-muted)]">{dateBR(s.updated_at)}</td>
              <td className="px-3 py-2 text-right">
                {canEdit && (
                  <Link
                    data-testid="company-skill-edit"
                    to={`/templates/skills/${s.template_id}?file=${encodeURIComponent(`skills/${s.name}/SKILL.md`)}`}
                    className="inline-flex min-h-11 items-center rounded-full border border-[var(--border)] px-4 text-sm font-medium text-[var(--text)] transition hover:border-[var(--brand)] hover:bg-[var(--brand-soft)]"
                  >
                    Editar
                  </Link>
                )}
              </td>
            </tr>
          ))}
        </Table>
      )}

      <Modal
        open={pickOpen}
        onClose={() => setPickOpen(false)}
        testId="company-new-skill-dialog"
        title="Nova skill da empresa"
        description="Escolha o agente por skills que vai receber a nova skill."
        footer={
          <>
            <Button type="button" variant="ghost" onClick={() => setPickOpen(false)}>
              Cancelar
            </Button>
            <Button
              type="button"
              disabled={!pickId}
              data-testid="company-new-skill-go"
              onClick={() => navigate(`/templates/skills/${pickId}?newSkill=1`)}
            >
              Continuar
            </Button>
          </>
        }
      >
        {skillsTemplates.length === 0 ? (
          <p className="text-sm text-[var(--text-muted)]">Ainda não há agentes por skills. Crie um em Agentes de IA.</p>
        ) : (
          <Select label="Agente" name="company-skill-template" value={pickId} onChange={(e) => setPickId(e.target.value)}>
            {skillsTemplates.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </Select>
        )}
      </Modal>
    </div>
  )
}

function statusTone(s: UserSkillStatus) {
  return s === 'active' ? <Badge ok>{STATUS_LABEL[s]}</Badge> : <Badge ok={false}>{STATUS_LABEL[s]}</Badge>
}

function LearnedTab({ skills, isLoading, error }: { skills: UserSkill[]; isLoading: boolean; error: unknown }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [filter, setFilter] = useState<StatusFilter>('all')
  const [editing, setEditing] = useState<{ skill: UserSkill; approve: boolean } | null>(null)
  const [editContent, setEditContent] = useState('')
  const [editError, setEditError] = useState('')
  const [deleting, setDeleting] = useState<UserSkill | null>(null)
  const [actionError, setActionError] = useState('')

  const refresh = () => qc.invalidateQueries({ queryKey: ['my-skills'] })

  const act = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'approve' | 'discard' | 'disable' | 'enable' }) =>
      actOnMySkill(id, action),
    onSuccess: (_r, v) => {
      setActionError('')
      const msg = { approve: 'Skill aprovada.', discard: 'Skill descartada.', disable: 'Skill desativada.', enable: 'Skill ativada.' }[v.action]
      toast(msg)
      refresh()
    },
    onError: (e: Error) => setActionError(e.message),
  })

  const remove = useMutation({
    mutationFn: (id: string) => deleteMySkill(id),
    onSuccess: () => {
      setDeleting(null)
      toast('Skill excluída.')
      refresh()
    },
    onError: (e: Error) => {
      setDeleting(null)
      setActionError(e.message)
    },
  })

  const saveEdit = useMutation({
    mutationFn: async () => {
      const { skill, approve } = editing!
      await updateMySkill(skill.id, { content: editContent })
      if (approve) await actOnMySkill(skill.id, 'approve')
    },
    onSuccess: () => {
      toast(editing?.approve ? 'Skill editada e aprovada.' : 'Skill salva.')
      setEditing(null)
      refresh()
    },
    onError: (e: Error) => setEditError(e.message),
  })

  async function openEdit(skill: UserSkill, approve: boolean) {
    setEditError('')
    setEditing({ skill, approve })
    setEditContent(skill.content ?? '')
    if (skill.content === undefined) {
      try {
        const full = await getMySkill(skill.id)
        setEditContent(full.content ?? '')
      } catch (e) {
        setEditError(e instanceof Error ? e.message : 'Não foi possível abrir a skill.')
      }
    }
  }

  const visible = useMemo(() => (filter === 'all' ? skills : skills.filter((s) => s.status === filter)), [skills, filter])
  const counts = useMemo(
    () => ({
      pending_review: skills.filter((s) => s.status === 'pending_review').length,
      active: skills.filter((s) => s.status === 'active').length,
      disabled: skills.filter((s) => s.status === 'disabled').length,
    }),
    [skills],
  )

  return (
    <div className="space-y-4" data-testid="skills-learned">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-sm leading-6 text-[var(--text-muted)]">
          O assistente sugeriu estas habilidades a partir das suas conversas. Só você vê e decide sobre elas. Veja também suas{' '}
          <Link to="/memorias" className="text-[var(--brand)] underline" data-testid="learned-memories-link">
            memórias
          </Link>
          .
        </p>
        <Segmented
          label="Filtrar por situação"
          testId="learned-filter"
          value={filter}
          onChange={(v) => setFilter(v as StatusFilter)}
          items={[
            { id: 'all', label: 'Todas', testId: 'learned-filter-all' },
            { id: 'pending_review', label: `Pendentes (${counts.pending_review})`, testId: 'learned-filter-pending' },
            { id: 'active', label: `Ativas (${counts.active})`, testId: 'learned-filter-active' },
            { id: 'disabled', label: `Desativadas (${counts.disabled})`, testId: 'learned-filter-disabled' },
          ]}
        />
      </div>
      <ErrorText>{actionError}</ErrorText>
      {isLoading ? (
        <TableSkeleton columns={3} />
      ) : error ? (
        <ErrorText>{error instanceof Error ? error.message : 'Não foi possível carregar suas skills.'}</ErrorText>
      ) : visible.length === 0 ? (
        <EmptyState
          title="Nenhuma skill aqui"
          description={
            filter === 'all'
              ? 'Quando o assistente aprender algo útil com você, a sugestão aparece aqui para você aprovar.'
              : 'Nenhuma skill nesta situação.'
          }
        />
      ) : (
        <ul className="space-y-3">
          {visible.map((s) => (
            <li
              key={s.id}
              data-testid="learned-skill"
              data-status={s.status}
              data-name={s.name}
              className="rounded-[24px] border border-[var(--border)] bg-[var(--surface-solid)] p-4"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-medium text-[var(--text)]">{s.name}</p>
                  <p className="mt-1 text-sm leading-6 text-[var(--text-muted)]">{s.description}</p>
                  <p className="mt-1 text-xs text-[var(--text-faint)]">
                    {s.source === 'agent' ? 'Sugerida pelo assistente' : 'Criada por você'}
                    {s.origin_chat_id ? ' · a partir de uma conversa' : ''} · atualizada em {dateBR(s.updated_at)}
                  </p>
                </div>
                <span data-testid="learned-status">{statusTone(s.status)}</span>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                {s.status === 'pending_review' && (
                  <>
                    <Button type="button" data-testid="learned-approve" disabled={act.isPending} onClick={() => act.mutate({ id: s.id, action: 'approve' })}>
                      Aprovar
                    </Button>
                    <Button type="button" variant="ghost" data-testid="learned-edit-approve" onClick={() => openEdit(s, true)}>
                      Editar e aprovar
                    </Button>
                    <Button type="button" variant="ghost" data-testid="learned-discard" disabled={act.isPending} onClick={() => act.mutate({ id: s.id, action: 'discard' })}>
                      Descartar
                    </Button>
                  </>
                )}
                {s.status === 'active' && (
                  <>
                    <Button type="button" variant="ghost" data-testid="learned-edit" onClick={() => openEdit(s, false)}>
                      Editar
                    </Button>
                    <Button type="button" variant="ghost" data-testid="learned-disable" disabled={act.isPending} onClick={() => act.mutate({ id: s.id, action: 'disable' })}>
                      Desativar
                    </Button>
                    <Button type="button" variant="danger" data-testid="learned-delete" onClick={() => setDeleting(s)}>
                      Excluir
                    </Button>
                  </>
                )}
                {s.status === 'disabled' && (
                  <>
                    <Button type="button" variant="ghost" data-testid="learned-enable" disabled={act.isPending} onClick={() => act.mutate({ id: s.id, action: 'enable' })}>
                      Ativar
                    </Button>
                    <Button type="button" variant="ghost" data-testid="learned-edit" onClick={() => openEdit(s, false)}>
                      Editar
                    </Button>
                    <Button type="button" variant="danger" data-testid="learned-delete" onClick={() => setDeleting(s)}>
                      Excluir
                    </Button>
                  </>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      <Modal
        open={editing !== null}
        onClose={() => setEditing(null)}
        size="lg"
        testId="learned-edit-dialog"
        title={editing?.approve ? 'Editar e aprovar skill' : 'Editar skill'}
        description={editing?.skill.name}
        footer={
          <>
            <Button type="button" variant="ghost" onClick={() => setEditing(null)}>
              Cancelar
            </Button>
            <Button type="button" data-testid="learned-edit-save" disabled={saveEdit.isPending || !editContent.trim()} onClick={() => saveEdit.mutate()}>
              {editing?.approve ? 'Salvar e aprovar' : 'Salvar'}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <MarkdownEditor
            path={`minhas-skills/${editing?.skill.name ?? 'skill'}.md`}
            testId="learned-editor"
            height="20rem"
            value={editContent}
            onChange={setEditContent}
          />
          <ErrorText>{editError}</ErrorText>
        </div>
      </Modal>

      <ConfirmDialog
        open={deleting !== null}
        title="Excluir skill"
        message={`Excluir a skill “${deleting?.name}”? Isso não pode ser desfeito.`}
        confirmLabel="Excluir"
        danger
        busy={remove.isPending}
        onClose={() => setDeleting(null)}
        onConfirm={() => deleting && remove.mutate(deleting.id)}
        testId="learned-delete-dialog"
      />
    </div>
  )
}

export default function Skills() {
  const [tab, setTab] = useState<TabId>('company')
  const mine = useQuery({ queryKey: ['my-skills'], queryFn: () => listMySkills() })
  const pending = (mine.data ?? []).filter((s) => s.status === 'pending_review').length

  return (
    <div className="space-y-6" data-testid="skills-page">
      <PageHeader
        title="Skills"
        description="Habilidades que os agentes por skills usam: as da empresa e as que o assistente aprendeu com você."
      />
      <Card>
        <Tabs
          label="Camadas de skills"
          testId="skills-tabs"
          value={tab}
          onChange={(v) => setTab(v as TabId)}
          className="mb-5"
          items={[
            { id: 'company', label: 'Da empresa', testId: 'skills-tab-company' },
            { id: 'learned', label: 'Aprendidas por você', testId: 'skills-tab-learned', count: pending || undefined },
          ]}
        />
        {tab === 'company' ? (
          <CompanyTab />
        ) : (
          <LearnedTab skills={mine.data ?? []} isLoading={mine.isLoading} error={mine.error} />
        )}
      </Card>
    </div>
  )
}
