import { api } from './api'

export type Severity = 'error' | 'warning'

export interface Problem {
  path: string
  line?: number | null
  code: string
  message: string
  severity: Severity
}

export interface SkillFile {
  path: string
  content: string
  size_bytes?: number
}

export interface Manifest {
  tools: string[]
  environment: { enabled: boolean }
  model: { alias?: string } | null
  limits: { max_turns?: number }
}

export const EMPTY_MANIFEST: Manifest = {
  tools: [],
  environment: { enabled: false },
  model: null,
  limits: {},
}

export interface Draft {
  files: SkillFile[]
  manifest: Manifest
  updated_at: string | null
  updated_by: string | null
}

export interface ValidateResult {
  ok: boolean
  problems: Problem[]
}

export interface SkillVersion {
  id: string
  number: number
  notes: string
  created_at: string
  created_by?: string | null
  is_active: boolean
}

export interface ToolCatalogItem {
  id: string
  label: string
  description: string
  sensitive: boolean
}

export interface CompanySkill {
  template_id: string
  template_name: string
  name: string
  description: string
  updated_at: string
}

export type UserSkillStatus = 'pending_review' | 'active' | 'disabled'

export interface UserSkill {
  id: string
  name: string
  description: string
  status: UserSkillStatus
  source: 'agent' | 'user'
  template_id: string | null
  origin_chat_id: string | null
  revision: number
  created_at: string
  updated_at: string
  content?: string
}

const post = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) })

export const getDraft = (templateId: string) => api<Draft>(`/templates/${templateId}/draft`)
export const saveDraft = (templateId: string, files: SkillFile[], manifest: Manifest) =>
  api<{ ok: boolean; problems: Problem[] }>(`/templates/${templateId}/draft`, {
    method: 'PUT',
    body: JSON.stringify({ files, manifest }),
  })
export const validateDraft = (templateId: string, files: SkillFile[], manifest: Manifest) =>
  api<ValidateResult>(`/templates/${templateId}/validate`, post({ files, manifest }))
export const publishDraft = (templateId: string, notes: string) =>
  api<SkillVersion>(`/templates/${templateId}/versions`, post({ source: 'draft', notes }))
export const listSkillVersions = (templateId: string) =>
  api<SkillVersion[]>(`/templates/${templateId}/versions`)
export const listVersionFiles = (templateId: string, versionId: string) =>
  api<{ path: string; size_bytes: number; sha256: string }[]>(
    `/templates/${templateId}/versions/${versionId}/files`,
  )
export const getVersionFile = (templateId: string, versionId: string, path: string) =>
  api<{ path: string; content: string }>(
    `/templates/${templateId}/versions/${versionId}/files/${path.split('/').map(encodeURIComponent).join('/')}`,
  )
export const toolsCatalog = () => api<ToolCatalogItem[]>('/tools/catalog')

export const listCompanySkills = () => api<CompanySkill[]>('/skills/company')

export const listMySkills = (status?: UserSkillStatus) =>
  api<UserSkill[]>(`/me/skills${status ? `?status=${status}` : ''}`)
export const getMySkill = (id: string) => api<UserSkill>(`/me/skills/${id}`)
export const updateMySkill = (id: string, body: { description?: string; content: string }) =>
  api<UserSkill>(`/me/skills/${id}`, { method: 'PUT', body: JSON.stringify(body) })
export const deleteMySkill = (id: string) => api<void>(`/me/skills/${id}`, { method: 'DELETE' })
export const actOnMySkill = (id: string, action: 'approve' | 'discard' | 'disable' | 'enable') =>
  api<UserSkill>(`/me/skills/${id}/${action}`, { method: 'POST' })

export const STATUS_LABEL: Record<UserSkillStatus, string> = {
  pending_review: 'Pendente',
  active: 'Ativa',
  disabled: 'Desativada',
}

/** Títulos pt-BR para os códigos de validação devolvidos pelo servidor. */
export const PROBLEM_TITLE: Record<string, string> = {
  missing_agent_md: 'Falta o arquivo AGENTE.md',
  bad_frontmatter: 'Cabeçalho da skill inválido',
  bad_name: 'Nome fora do padrão',
  duplicate_name: 'Nome repetido',
  file_too_large: 'Arquivo grande demais',
  package_too_large: 'Pacote grande demais',
  too_many_files: 'Arquivos demais',
  bad_path: 'Caminho inválido',
  bad_encoding: 'Codificação inválida',
  secret_detected: 'Possível segredo no texto',
  unknown_tool: 'Ferramenta desconhecida',
  broken_reference: 'Referência quebrada',
  bad_extension: 'Extensão não permitida',
}
