import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState, type FormEvent } from 'react'
import { Badge, Button, Card, ErrorText, Input, Select, Table } from '../components/ui'
import { api } from '../lib/api'
import {
  createTemplate,
  createVersion,
  deployVersion,
  getVersion,
  listTemplates,
  listToolkits,
  listVersions,
  type AgentDraft,
  type TemplateSummary,
  type ToolInfo,
} from '../lib/templates'

interface AiServiceOption {
  id: string
  name: string
  provider: string
  model: string
}

const EMPTY_AGENT: AgentDraft = {
  name: '',
  description: '',
  prompt: '',
  ai_service_id: null,
  model_override: null,
  reasoning_effort: null,
  tools: [],
}

const EFFORTS = [
  { value: '', label: 'Padrão' },
  { value: 'low', label: 'Baixo (rápido/barato)' },
  { value: 'medium', label: 'Médio' },
  { value: 'high', label: 'Alto (pensa mais)' },
]

function AgentEditor({
  agent,
  services,
  toolkits,
  onChange,
  onRemove,
}: {
  agent: AgentDraft
  services: AiServiceOption[]
  toolkits: ToolInfo[]
  onChange: (a: AgentDraft) => void
  onRemove: () => void
}) {
  function toggleTool(name: string) {
    const tools = agent.tools.includes(name)
      ? agent.tools.filter((t) => t !== name)
      : [...agent.tools, name]
    onChange({ ...agent, tools })
  }
  return (
    <div className="space-y-3 rounded-lg border border-slate-800 bg-slate-950/60 p-4" data-testid="agent-editor">
      <div className="grid gap-3 sm:grid-cols-2">
        <Input
          label="Nome (snake_case, ex: financeiro_agent)"
          required
          value={agent.name}
          onChange={(e) => onChange({ ...agent, name: e.target.value })}
        />
        <Select
          label="Serviço de IA"
          value={agent.ai_service_id ?? ''}
          onChange={(e) => onChange({ ...agent, ai_service_id: e.target.value || null })}
        >
          <option value="">Padrão do tenant</option>
          {services.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name} ({s.provider}/{s.model})
            </option>
          ))}
        </Select>
        <Input
          label="Modelo (override, opcional)"
          value={agent.model_override ?? ''}
          placeholder="usa o modelo do serviço"
          onChange={(e) => onChange({ ...agent, model_override: e.target.value || null })}
        />
        <Select
          label="Reasoning effort"
          value={agent.reasoning_effort ?? ''}
          onChange={(e) => onChange({ ...agent, reasoning_effort: e.target.value || null })}
        >
          {EFFORTS.map((ef) => (
            <option key={ef.value} value={ef.value}>
              {ef.label}
            </option>
          ))}
        </Select>
      </div>
      <Input
        label="Quando o supervisor deve chamar este agente?"
        required
        value={agent.description}
        placeholder="ex: perguntas sobre finanças, DRE, fluxo de caixa"
        onChange={(e) => onChange({ ...agent, description: e.target.value })}
      />
      <label className="block">
        <span className="mb-1 block text-sm text-slate-300">Prompt do agente</span>
        <textarea
          required
          rows={4}
          value={agent.prompt}
          onChange={(e) => onChange({ ...agent, prompt: e.target.value })}
          className="w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none focus:border-indigo-500"
        />
      </label>
      <div>
        <span className="mb-1 block text-sm text-slate-300">Ferramentas</span>
        <div className="flex flex-wrap gap-2">
          {toolkits.map((tool) => (
            <label
              key={tool.name}
              title={tool.description}
              className={`cursor-pointer rounded-full border px-3 py-1 text-xs transition ${
                agent.tools.includes(tool.name)
                  ? 'border-indigo-500 bg-indigo-950 text-indigo-200'
                  : 'border-slate-700 bg-slate-900 text-slate-400 hover:border-slate-500'
              }`}
            >
              <input
                type="checkbox"
                className="hidden"
                checked={agent.tools.includes(tool.name)}
                onChange={() => toggleTool(tool.name)}
              />
              {tool.name}
            </label>
          ))}
          {toolkits.length === 0 && (
            <span className="text-xs text-slate-500">catálogo indisponível</span>
          )}
        </div>
      </div>
      <Button type="button" variant="danger" onClick={onRemove}>
        Remover agente
      </Button>
    </div>
  )
}

export default function Templates() {
  const qc = useQueryClient()
  const { data: templates = [] } = useQuery({ queryKey: ['templates'], queryFn: listTemplates })
  const { data: services = [] } = useQuery({
    queryKey: ['ai-services'],
    queryFn: () => api<AiServiceOption[]>('/ai-services'),
  })
  const { data: toolkits = [] } = useQuery({
    queryKey: ['toolkits'],
    queryFn: listToolkits,
    staleTime: 5 * 60_000,
  })

  const [selected, setSelected] = useState<TemplateSummary | null>(null)
  const [newName, setNewName] = useState('')
  const [newDescription, setNewDescription] = useState('')
  const [error, setError] = useState('')

  // Version draft
  const [supervisorPrompt, setSupervisorPrompt] = useState('')
  const [supervisorService, setSupervisorService] = useState('')
  const [supervisorEffort, setSupervisorEffort] = useState('')
  const [maxSteps, setMaxSteps] = useState(6)
  const [agents, setAgents] = useState<AgentDraft[]>([])
  const [datasourceIds, setDatasourceIds] = useState<string[]>([])

  const { data: datasources = [] } = useQuery({
    queryKey: ['datasources'],
    queryFn: () => api<{ id: string; name: string; kind: string }[]>('/datasources'),
  })

  const { data: versions = [] } = useQuery({
    queryKey: ['versions', selected?.id],
    queryFn: () => listVersions(selected!.id),
    enabled: !!selected,
  })

  useEffect(() => {
    // Editing an existing template starts from its active (or latest) version.
    if (!selected) return
    const source = selected.active_version_id ?? versions[0]?.id
    if (!source) {
      setSupervisorPrompt('')
      setSupervisorService('')
      setSupervisorEffort('')
      setMaxSteps(6)
      setAgents([])
      setDatasourceIds([])
      return
    }
    getVersion(selected.id, source).then((v) => {
      setSupervisorPrompt(v.supervisor_prompt)
      setSupervisorService(v.supervisor_ai_service_id ?? '')
      setSupervisorEffort(v.supervisor_reasoning_effort ?? '')
      setMaxSteps(v.max_steps)
      setAgents(v.agents)
      setDatasourceIds(v.datasource_ids ?? [])
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.id, versions.length])

  const create = useMutation({
    mutationFn: () => createTemplate({ name: newName, description: newDescription }),
    onSuccess: (t) => {
      setNewName('')
      setNewDescription('')
      setError('')
      qc.invalidateQueries({ queryKey: ['templates'] })
      setSelected(t)
    },
    onError: (e: Error) => setError(e.message),
  })

  const saveVersion = useMutation({
    mutationFn: async () => {
      const v = await createVersion(selected!.id, {
        supervisor_prompt: supervisorPrompt,
        supervisor_ai_service_id: supervisorService || null,
        supervisor_reasoning_effort: supervisorEffort || null,
        max_steps: maxSteps,
        agents,
        datasource_ids: datasourceIds,
      })
      return v
    },
    onSuccess: () => {
      setError('')
      qc.invalidateQueries({ queryKey: ['versions', selected?.id] })
    },
    onError: (e: Error) => setError(e.message),
  })

  const deploy = useMutation({
    mutationFn: (versionId: string) => deployVersion(selected!.id, versionId),
    onSuccess: (_result, versionId) => {
      const number = versions.find((v) => v.id === versionId)?.version_number ?? null
      setSelected((s) =>
        s ? { ...s, active_version_id: versionId, active_version_number: number } : s,
      )
      qc.invalidateQueries({ queryKey: ['templates'] })
      qc.invalidateQueries({ queryKey: ['versions', selected?.id] })
    },
  })

  function onCreate(e: FormEvent) {
    e.preventDefault()
    create.mutate()
  }

  if (!selected) {
    return (
      <div className="space-y-6">
        <Card title="Novo template">
          <form onSubmit={onCreate} className="grid gap-3 sm:grid-cols-3 sm:items-end">
            <Input label="Nome" required name="tpl-name" value={newName} onChange={(e) => setNewName(e.target.value)} />
            <Input
              label="Descrição (aparece no seletor do chat)"
              name="tpl-desc"
              value={newDescription}
              onChange={(e) => setNewDescription(e.target.value)}
            />
            <Button type="submit" disabled={create.isPending}>
              Criar template
            </Button>
            <div className="sm:col-span-3">
              <ErrorText>{error}</ErrorText>
            </div>
          </form>
        </Card>

        <Card title="Templates">
          <Table headers={['Nome', 'Descrição', 'Versão ativa', '']}>
            {templates.map((t) => (
              <tr key={t.id} data-testid="template-row">
                <td className="px-3 py-2 text-slate-200">{t.name}</td>
                <td className="px-3 py-2 text-slate-400">{t.description}</td>
                <td className="px-3 py-2">
                  {t.active_version_number ? (
                    <Badge ok>v{t.active_version_number}</Badge>
                  ) : (
                    <Badge ok={false}>sem deploy</Badge>
                  )}
                </td>
                <td className="px-3 py-2 text-right">
                  <Button variant="ghost" onClick={() => setSelected(t)}>
                    Editar
                  </Button>
                </td>
              </tr>
            ))}
          </Table>
        </Card>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-slate-100">
          {selected.name}
          {selected.active_version_number && (
            <span className="ml-2 text-sm font-normal text-emerald-400">
              v{selected.active_version_number} em produção
            </span>
          )}
        </h1>
        <Button variant="ghost" onClick={() => setSelected(null)}>
          ← Voltar
        </Button>
      </div>

      <Card title="Supervisor">
        <div className="space-y-3">
          <label className="block">
            <span className="mb-1 block text-sm text-slate-300">Prompt do supervisor</span>
            <textarea
              required
              rows={4}
              value={supervisorPrompt}
              onChange={(e) => setSupervisorPrompt(e.target.value)}
              className="w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none focus:border-indigo-500"
              placeholder="Você coordena os especialistas da empresa X…"
            />
          </label>
          <div className="grid gap-3 sm:grid-cols-3">
            <Select
              label="Serviço de IA do supervisor"
              value={supervisorService}
              onChange={(e) => setSupervisorService(e.target.value)}
            >
              <option value="">Padrão do tenant</option>
              {services.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} ({s.provider}/{s.model})
                </option>
              ))}
            </Select>
            <Select
              label="Reasoning effort"
              value={supervisorEffort}
              onChange={(e) => setSupervisorEffort(e.target.value)}
            >
              {EFFORTS.map((ef) => (
                <option key={ef.value} value={ef.value}>
                  {ef.label}
                </option>
              ))}
            </Select>
            <Input
              label="Máx. passos por turno"
              type="number"
              min={1}
              max={20}
              value={maxSteps}
              onChange={(e) => setMaxSteps(Number(e.target.value))}
            />
          </div>
        </div>
      </Card>

      <Card
        title={`Agentes especialistas (${agents.length})`}
        actions={
          <Button variant="ghost" onClick={() => setAgents([...agents, { ...EMPTY_AGENT }])}>
            + Adicionar agente
          </Button>
        }
      >
        <div className="space-y-4">
          {agents.length === 0 && (
            <p className="text-sm text-slate-500">
              Sem especialistas: o supervisor responde sozinho. Adicione agentes para
              dividir o trabalho por especialidade, cada um com seu modelo e prompt.
            </p>
          )}
          {agents.map((agent, i) => (
            <AgentEditor
              key={i}
              agent={agent}
              services={services}
              toolkits={toolkits}
              onChange={(a) => setAgents(agents.map((x, j) => (j === i ? a : x)))}
              onRemove={() => setAgents(agents.filter((_, j) => j !== i))}
            />
          ))}
        </div>
      </Card>

      <Card title="Fontes de dados do template">
        <div className="flex flex-wrap gap-2">
          {datasources.map((ds) => (
            <label
              key={ds.id}
              className={`cursor-pointer rounded-full border px-3 py-1 text-xs transition ${
                datasourceIds.includes(ds.id)
                  ? 'border-emerald-500 bg-emerald-950 text-emerald-200'
                  : 'border-slate-700 bg-slate-900 text-slate-400 hover:border-slate-500'
              }`}
            >
              <input
                type="checkbox"
                className="hidden"
                checked={datasourceIds.includes(ds.id)}
                onChange={() =>
                  setDatasourceIds(
                    datasourceIds.includes(ds.id)
                      ? datasourceIds.filter((i) => i !== ds.id)
                      : [...datasourceIds, ds.id],
                  )
                }
              />
              {ds.name} ({ds.kind})
            </label>
          ))}
          {datasources.length === 0 && (
            <span className="text-xs text-slate-500">
              Nenhuma fonte cadastrada — crie em "Fontes de dados".
            </span>
          )}
        </div>
      </Card>

      <div className="flex items-center gap-3">
        <Button onClick={() => saveVersion.mutate()} disabled={saveVersion.isPending || !supervisorPrompt}>
          Salvar nova versão
        </Button>
        <ErrorText>{error}</ErrorText>
      </div>

      <Card title="Versões">
        <Table headers={['Versão', 'Criada em', 'Status', '']}>
          {versions.map((v) => (
            <tr key={v.id} data-testid="version-row">
              <td className="px-3 py-2 text-slate-200">v{v.version_number}</td>
              <td className="px-3 py-2 text-slate-400">
                {new Date(v.created_at).toLocaleString('pt-BR')}
              </td>
              <td className="px-3 py-2">
                {selected.active_version_id === v.id && <Badge ok>em produção</Badge>}
              </td>
              <td className="px-3 py-2 text-right">
                {selected.active_version_id !== v.id && (
                  <Button variant="ghost" onClick={() => deploy.mutate(v.id)} disabled={deploy.isPending}>
                    Fazer deploy
                  </Button>
                )}
              </td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  )
}
