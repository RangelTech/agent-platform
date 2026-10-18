import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Button, Card, ErrorText, Input, PageHeader, Select } from '../../components/ui'
import { VersionsCard } from '../../components/VersionsCard'
import { api } from '../../lib/api'
import { listCustomTools } from '../../lib/customTools'
import {
  createVersion,
  deployVersion,
  getVersion,
  listTemplates,
  listToolkits,
  listVersions,
  type AgentDraft,
  type TemplateSummary,
  type ToolInfo,
} from '../../lib/templates'

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
  file_ids: [],
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
  files,
  onChange,
  onRemove,
}: {
  agent: AgentDraft
  services: AiServiceOption[]
  toolkits: ToolInfo[]
  files: { id: string; name: string; status: string }[]
  onChange: (a: AgentDraft) => void
  onRemove: () => void
}) {
  function toggleFile(id: string) {
    const file_ids = agent.file_ids.includes(id)
      ? agent.file_ids.filter((f) => f !== id)
      : [...agent.file_ids, id]
    onChange({ ...agent, file_ids })
  }
  function toggleTool(name: string) {
    const tools = agent.tools.includes(name)
      ? agent.tools.filter((t) => t !== name)
      : [...agent.tools, name]
    onChange({ ...agent, tools })
  }
  return (
    <div className="space-y-3 rounded-[16px] border border-[var(--border)] bg-[var(--surface-solid)] p-4" data-testid="agent-editor">
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
        <span className="mb-1.5 block text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">Prompt do agente</span>
        <textarea
          required
          rows={4}
          value={agent.prompt}
          onChange={(e) => onChange({ ...agent, prompt: e.target.value })}
          className="w-full rounded-xl border border-[var(--border)] bg-[var(--surface-solid)] px-3.5 py-2.5 text-sm text-[var(--text)] outline-none transition focus:border-[var(--brand)] focus:ring-2 focus:ring-[var(--brand-soft)]"
        />
      </label>
      <div>
        <span className="mb-1.5 block text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">Ferramentas</span>
        <div className="flex flex-wrap gap-2">
          {toolkits.map((tool) => (
            <label
              key={tool.name}
              title={tool.description}
              className={`cursor-pointer rounded-full border px-3 py-1 text-xs transition ${
                agent.tools.includes(tool.name)
                  ? 'border-[var(--brand)] bg-[var(--brand-soft)] text-[var(--text)]'
                  : 'border-[var(--border)] bg-[var(--surface-solid)] text-[var(--text-muted)] hover:border-[var(--brand)]'
              }`}
            >
              <input
                type="checkbox"
                className="hidden"
                checked={agent.tools.includes(tool.name)}
                onChange={() => toggleTool(tool.name)}
              />
              {tool.title || tool.name}
            </label>
          ))}
          {toolkits.length === 0 && (
            <span className="text-xs text-[var(--text-faint)]">catálogo indisponível</span>
          )}
        </div>
      </div>
      <div>
        <span className="mb-1.5 block text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">Documentos (RAG)</span>
        <div className="flex flex-wrap gap-2">
          {files.filter((f) => f.status === 'ready').map((f) => (
            <label
              key={f.id}
              className={`cursor-pointer rounded-full border px-3 py-1 text-xs transition ${
                agent.file_ids.includes(f.id)
                  ? 'border-[var(--info)] bg-[var(--info-soft)] text-[var(--info)]'
                  : 'border-[var(--border)] bg-[var(--surface-solid)] text-[var(--text-muted)] hover:border-[var(--brand)]'
              }`}
            >
              <input
                type="checkbox"
                className="hidden"
                checked={agent.file_ids.includes(f.id)}
                onChange={() => toggleFile(f.id)}
              />
              {f.name}
            </label>
          ))}
          {files.filter((f) => f.status === 'ready').length === 0 && (
            <span className="text-xs text-[var(--text-faint)]">nenhum arquivo pronto — envie em "Arquivos"</span>
          )}
        </div>
      </div>
      <Button type="button" variant="danger" onClick={onRemove}>
        Remover agente
      </Button>
    </div>
  )
}

/** Editor do agente estruturado (rota /templates/estruturado/:id). Comportamento inalterado. */
export default function StructuredEditor() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const { id: routeId } = useParams()
  const { data: templates = [], isLoading: loadingTemplates } = useQuery({
    queryKey: ['templates'],
    queryFn: listTemplates,
  })
  const { data: services = [] } = useQuery({
    queryKey: ['ai-services'],
    queryFn: () => api<AiServiceOption[]>('/ai-services'),
  })
  const { data: toolkits = [] } = useQuery({
    queryKey: ['toolkits'],
    queryFn: listToolkits,
    staleTime: 5 * 60_000,
  })
  const { data: customTools = [] } = useQuery({
    queryKey: ['custom-tools'],
    queryFn: listCustomTools,
  })
  const availableTools: ToolInfo[] = [
    ...toolkits,
    ...customTools
      .filter((tool) => tool.enabled)
      // ExternalServers namespaces every MCP tool. Persist the exact public
      // name that the platform will expose to the specialist.
      .map((tool) => ({
        name: `ext_custom_tools_${tool.name}`,
        description: `[Custom Tool] ${tool.description}`,
      })),
  ]

  const [selected, setSelected] = useState<TemplateSummary | null>(null)
  const [error, setError] = useState('')

  // Version draft
  const [supervisorPrompt, setSupervisorPrompt] = useState('')
  const [supervisorService, setSupervisorService] = useState('')
  const [supervisorEffort, setSupervisorEffort] = useState('')
  const [maxSteps, setMaxSteps] = useState(6)
  const [agents, setAgents] = useState<AgentDraft[]>([])
  const [datasourceIds, setDatasourceIds] = useState<string[]>([])
  const [writeTables, setWriteTables] = useState('')
  const [requireConfirm, setRequireConfirm] = useState(true)
  const [historyLimit, setHistoryLimit] = useState(100)
  const [compressHistory, setCompressHistory] = useState(false)
  const [toolOutputLimit, setToolOutputLimit] = useState(24000)

  const { data: businessFiles = [] } = useQuery({
    queryKey: ['files'],
    queryFn: () => api<{ id: string; name: string; status: string }[]>('/files'),
  })
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
      setWriteTables((v.write_tables ?? []).join(', '))
      setRequireConfirm(v.require_write_confirmation ?? true)
      setHistoryLimit(v.history_limit ?? 100)
      setCompressHistory(v.compress_history ?? false)
      setToolOutputLimit(v.tool_output_limit ?? 24000)
      setDatasourceIds(v.datasource_ids ?? [])
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.id, versions.length])

  const saveVersion = useMutation({
    mutationFn: async () => {
      const v = await createVersion(selected!.id, {
        supervisor_prompt: supervisorPrompt,
        supervisor_ai_service_id: supervisorService || null,
        supervisor_reasoning_effort: supervisorEffort || null,
        max_steps: maxSteps,
        agents,
        write_tables: writeTables.split(',').map((t) => t.trim()).filter(Boolean),
        require_write_confirmation: requireConfirm,
        history_limit: historyLimit,
        compress_history: compressHistory,
        tool_output_limit: toolOutputLimit,
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

  // Deep link: o agente vem da URL; a lista de templates é a fonte.
  useEffect(() => {
    const found = templates.find((t) => t.id === routeId)
    if (found && selected?.id !== found.id) setSelected(found)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [templates, routeId])

  if (!selected) {
    return (
      <div className="space-y-6" data-testid="structured-editor">
        <PageHeader
          title="Agente estruturado"
          actions={
            <Button variant="ghost" onClick={() => navigate('/templates')}>
              ← Voltar
            </Button>
          }
        />
        {loadingTemplates ? (
          <p className="text-sm text-[var(--text-muted)]">Carregando…</p>
        ) : (
          <ErrorText>Agente não encontrado. Ele pode ter sido arquivado.</ErrorText>
        )}
      </div>
    )
  }

  return (
    <div className="space-y-6" data-testid="structured-editor">
      <PageHeader
        title={selected.name}
        description={
          selected.active_version_number
            ? `v${selected.active_version_number} em produção`
            : 'Ainda sem versão publicada'
        }
        actions={
          <Button variant="ghost" data-testid="editor-back" onClick={() => navigate('/templates')}>
            ← Voltar
          </Button>
        }
      />

      <Card title="Supervisor">
        <div className="space-y-3">
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">Prompt do supervisor</span>
            <textarea
              required
              rows={4}
              value={supervisorPrompt}
              onChange={(e) => setSupervisorPrompt(e.target.value)}
              className="w-full rounded-xl border border-[var(--border)] bg-[var(--surface-solid)] px-3.5 py-2.5 text-sm text-[var(--text)] outline-none transition focus:border-[var(--brand)] focus:ring-2 focus:ring-[var(--brand-soft)]"
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
          <div className="grid gap-3 sm:grid-cols-2">
            <Input
              label="Tabelas com escrita permitida (separe por vírgula)"
              name="write-tables"
              placeholder="pedidos, itens_pedido"
              value={writeTables}
              onChange={(e) => setWriteTables(e.target.value)}
            />
            <label className="flex items-end gap-2 pb-2 text-sm text-[var(--text-muted)]">
              <input
                type="checkbox"
                name="require-confirm"
                checked={requireConfirm}
                onChange={(e) => setRequireConfirm(e.target.checked)}
                className="h-4 w-4 accent-[var(--brand)]"
              />
              Exigir confirmação do usuário antes de escrever no banco
            </label>
          </div>
          {/* Histórico: quanto da conversa volta para o modelo a cada turno.
              Fica aqui, junto dos outros limites, porque é da mesma família —
              o que o agente pode gastar num turno. */}
          <div className="grid gap-3 sm:grid-cols-2">
            <Input
              label="Mensagens enviadas ao modelo"
              hint="Conversa mais longa que isso vai cortada. Menos mensagens custam menos e respondem mais rápido; muito pouco faz o agente esquecer o começo."
              type="number"
              min={4}
              max={500}
              name="history-limit"
              value={historyLimit}
              onChange={(e) => setHistoryLimit(Number(e.target.value))}
            />
            <label className="flex items-start gap-2 pt-7 text-sm text-[var(--text-muted)]">
              <input
                type="checkbox"
                name="compress-history"
                checked={compressHistory}
                onChange={(e) => setCompressHistory(e.target.checked)}
                className="mt-0.5 h-4 w-4 accent-[var(--brand)]"
              />
              <span>
                Resumir o começo em vez de descartar
                <span className="mt-0.5 block text-xs">
                  Ao passar do limite, a metade mais antiga vira um resumo feito
                  pela IA. Preserva o contexto, custa uma chamada a mais por
                  turno e a qualidade depende do resumo.
                </span>
              </span>
            </label>
          </div>
          <Input
            label="Tamanho máximo do resultado de uma ferramenta"
            hint="Em caracteres. É o que mais pesa no custo: o resultado de uma consulta volta para o especialista a cada rodada seguinte. O corte não perde dado — o resultado completo fica no artefato e segue encadeando para gráfico e planilha."
            type="number"
            min={2000}
            max={200000}
            step={1000}
            name="tool-output-limit"
            value={toolOutputLimit}
            onChange={(e) => setToolOutputLimit(Number(e.target.value))}
          />
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
            <p className="text-sm text-[var(--text-faint)]">
              Sem especialistas: o supervisor responde sozinho. Adicione agentes para
              dividir o trabalho por especialidade, cada um com seu modelo e prompt.
            </p>
          )}
          {agents.map((agent, i) => (
            <AgentEditor
              key={i}
              agent={agent}
              services={services}
              toolkits={availableTools}
              files={businessFiles}
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
                  ? 'border-[color-mix(in_srgb,var(--success)_60%,transparent)] bg-[var(--success-soft)] text-[var(--success)]'
                  : 'border-[var(--border)] bg-[var(--surface-solid)] text-[var(--text-muted)] hover:border-[var(--brand)]'
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
            <span className="text-xs text-[var(--text-faint)]">
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

      <VersionsCard
        versions={versions.map((v) => ({
          id: v.id,
          number: v.version_number,
          created_at: v.created_at,
        }))}
        activeId={selected.active_version_id}
        actionLabel="Fazer deploy"
        busy={deploy.isPending}
        onAction={(vid) => deploy.mutate(vid)}
      />
    </div>
  )
}
