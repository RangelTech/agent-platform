import { api } from './api'

/** Tipo do agente. O rótulo ao cliente vem sempre dos mapas abaixo. */
export type AgentKind = 'structured' | 'skills'

export const KIND_LABEL: Record<AgentKind, string> = {
  structured: 'Agente estruturado',
  skills: 'Agente por skills',
}

/** Rótulos curtos (coluna da lista). */
export const KIND_SHORT: Record<AgentKind, string> = {
  structured: 'Estruturado',
  skills: 'Por skills',
}

/** Grupos do seletor do chat. */
export const KIND_GROUP: Record<AgentKind, string> = {
  structured: 'Estruturados',
  skills: 'Por skills',
}

/** Ausente ou desconhecido cai no tipo estruturado (contrato legado). */
export function normalizeKind(kind: unknown): AgentKind {
  return kind === 'skills' ? 'skills' : 'structured'
}

export function kindLabel(kind: unknown): string {
  return KIND_LABEL[normalizeKind(kind)]
}

export interface TemplateSummary {
  id: string
  tenant_id: string
  name: string
  description: string
  agent_kind?: AgentKind
  active_version_id: string | null
  active_version_number: number | null
  updated_at?: string
  /** Agente de sistema: não pode ser arquivado. */
  system_key?: string | null
}

export interface AgentDraft {
  name: string
  description: string
  prompt: string
  ai_service_id: string | null
  model_override: string | null
  reasoning_effort: string | null
  tools: string[]
  file_ids: string[]
}

export interface ToolInfo {
  name: string
  title?: string
  description: string
}

export interface VersionDetail {
  id: string
  version_number: number
  supervisor_prompt: string
  supervisor_ai_service_id: string | null
  supervisor_model_override: string | null
  supervisor_reasoning_effort: string | null
  max_steps: number
  write_tables: string[]
  require_write_confirmation: boolean
  /** Quantas mensagens recentes seguem para o modelo a cada turno. */
  history_limit: number
  /** Resumir a metade antiga em vez de descartá-la ao passar do limite. */
  compress_history: boolean
  /** Teto, em caracteres, do que uma ferramenta devolve para o especialista. */
  tool_output_limit: number
  notes: string
  agents: AgentDraft[]
  datasource_ids: string[]
}

export interface VersionSummary {
  id: string
  version_number: number
  notes: string
  created_at: string
}

export const listTemplates = () => api<TemplateSummary[]>('/templates')
export const archiveTemplate = (id: string) => api<void>(`/templates/${id}`, { method: 'DELETE' })
export const createTemplate = (body: {
  name: string
  description: string
  agent_kind?: AgentKind
}) =>
  api<TemplateSummary>('/templates', { method: 'POST', body: JSON.stringify(body) })
export const listVersions = (templateId: string) =>
  api<VersionSummary[]>(`/templates/${templateId}/versions`)
export const getVersion = (templateId: string, versionId: string) =>
  api<VersionDetail>(`/templates/${templateId}/versions/${versionId}`)
export const createVersion = (templateId: string, body: unknown) =>
  api<{ id: string; version_number: number }>(`/templates/${templateId}/versions`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
export const deployVersion = (templateId: string, versionId: string) =>
  api<{ status: string }>(`/templates/${templateId}/deploy`, {
    method: 'POST',
    body: JSON.stringify({ version_id: versionId }),
  })
