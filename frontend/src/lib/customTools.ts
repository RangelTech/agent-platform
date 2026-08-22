import { api } from './api'

export interface CustomTool {
  id: string
  name: string
  description: string
  input_schema: Record<string, unknown>
  timeout_seconds: number
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface CustomToolInput {
  name: string
  description: string
  input_schema: Record<string, unknown>
  python_code: string
  secrets?: Record<string, string>
  timeout_seconds: number
  enabled: boolean
}

export const listCustomTools = () => api<CustomTool[]>('/custom-tools')
export const createCustomTool = (body: CustomToolInput) =>
  api<CustomTool>('/custom-tools', { method: 'POST', body: JSON.stringify(body) })
export const getCustomTool = (id: string) => api<CustomToolDetail>(`/custom-tools/${id}`)
export const updateCustomTool = (id: string, body: CustomToolInput) =>
  api<CustomTool>(`/custom-tools/${id}`, { method: 'PUT', body: JSON.stringify(body) })
export const deleteCustomTool = (id: string) =>
  api<void>(`/custom-tools/${id}`, { method: 'DELETE' })
export const testCustomTool = (id: string, inputs: Record<string, unknown>) =>
  api<Record<string, unknown>>(`/custom-tools/${id}/test`, {
    method: 'POST',
    body: JSON.stringify({ inputs }),
  })

export interface CustomToolDetail extends CustomTool {
  python_code: string
}
