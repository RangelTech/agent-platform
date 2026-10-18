import { api } from './api'

export type EnvState = 'disabled' | 'stopped' | 'starting' | 'running' | 'degraded' | 'suspended'

export interface EnvApp {
  id: string
  name: string
  url: string
  status: 'online' | 'offline' | 'waking'
  port: number
}

export interface EnvDomain {
  id: string
  host: string
  status: 'pending_verification' | 'active' | 'removed'
  txt_name: string
  txt_value: string
}

export interface EnvUsage {
  cpu_pct: number
  ram_mb: number
  ram_limit_mb: number
  disk_gb: number
  disk_limit_gb: number
  egress_gb: number
}

export interface Environment {
  enabled: boolean
  state: EnvState
  plan: string | null
  usage: EnvUsage
  apps: EnvApp[]
  domains: EnvDomain[]
  last_backup_at: string | null
}

export const STATE_LABEL: Record<EnvState, string> = {
  disabled: 'Não contratado',
  stopped: 'Parado',
  starting: 'Iniciando',
  running: 'Em execução',
  degraded: 'Instável',
  suspended: 'Suspenso',
}

export const getEnvironment = () => api<Environment>('/environment')
export const environmentAction = (action: 'start' | 'stop' | 'backup') =>
  api<{ state: EnvState }>(`/environment/${action}`, { method: 'POST' })
export const addDomain = (host: string, appId: string) =>
  api<EnvDomain>('/environment/domains', {
    method: 'POST',
    body: JSON.stringify({ host, app_id: appId }),
  })
export const removeDomain = (id: string) => api<void>(`/environment/domains/${id}`, { method: 'DELETE' })
export const unpublishApp = (id: string) => api<void>(`/environment/apps/${id}`, { method: 'DELETE' })
