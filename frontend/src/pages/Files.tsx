import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { Badge, Button, Card, ErrorText, Select, Table } from '../components/ui'
import { api, getToken, listTenants } from '../lib/api'
import { useAuth } from '../lib/auth'

export interface BusinessFile {
  id: string
  tenant_id: string
  name: string
  content_type: string
  size_bytes: number
  status: 'pending' | 'processing' | 'ready' | 'error'
  error_detail: string | null
  chunk_count: number | null
  created_at: string
}

const STATUS_LABEL: Record<BusinessFile['status'], string> = {
  pending: 'Aguardando',
  processing: 'Processando',
  ready: 'Pronto',
  error: 'Erro',
}

export default function Files() {
  const { user } = useAuth()
  const qc = useQueryClient()
  const isMaster = user?.is_master ?? false
  const inputRef = useRef<HTMLInputElement>(null)
  const [tenantId, setTenantId] = useState('')
  const [error, setError] = useState('')

  const { data: files = [], isLoading } = useQuery({
    queryKey: ['files'],
    queryFn: () => api<BusinessFile[]>('/files'),
    refetchInterval: (query) =>
      (query.state.data ?? []).some((f) => f.status === 'pending' || f.status === 'processing')
        ? 3000
        : false,
  })
  const { data: tenants = [] } = useQuery({
    queryKey: ['tenants'],
    queryFn: listTenants,
    enabled: isMaster,
  })

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const body = new FormData()
      body.append('file', file)
      if (isMaster && tenantId) body.append('tenant_id', tenantId)
      const token = getToken()
      const res = await fetch('/api/files', {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body,
      })
      if (!res.ok) {
        const detail = await res.json().then((b) => b.detail).catch(() => 'Falha no upload')
        throw new Error(detail)
      }
      return res.json()
    },
    onSuccess: () => {
      setError('')
      qc.invalidateQueries({ queryKey: ['files'] })
    },
    onError: (e: Error) => setError(e.message),
  })

  const reprocess = useMutation({
    mutationFn: (id: string) => api(`/files/${id}/reprocess`, { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['files'] }),
  })

  const archive = useMutation({
    mutationFn: (id: string) => api(`/files/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['files'] }),
  })

  return (
    <div className="space-y-6">
      <Card title="Enviar arquivo (PDF, DOCX, TXT, XLSX, CSV)">
        <div className="flex flex-wrap items-end gap-3">
          {isMaster && (
            <div className="w-56">
              <Select label="Empresa" value={tenantId} onChange={(e) => setTenantId(e.target.value)}>
                <option value="">Selecione…</option>
                {tenants.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </Select>
            </div>
          )}
          <input
            ref={inputRef}
            type="file"
            data-testid="file-input"
            accept=".pdf,.docx,.txt,.md,.csv,.xlsx,.xlsm"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0]
              if (f) upload.mutate(f)
              e.target.value = ''
            }}
          />
          <Button onClick={() => inputRef.current?.click()} disabled={upload.isPending}>
            {upload.isPending ? 'Enviando…' : 'Escolher arquivo'}
          </Button>
          <ErrorText>{error}</ErrorText>
        </div>
        <p className="mt-2 text-xs text-slate-500">
          Após o envio o arquivo é processado (extração + indexação) e fica disponível
          para vincular aos agentes nos templates.
        </p>
      </Card>

      <Card title="Arquivos">
        {isLoading ? (
          <p className="text-sm text-slate-400">Carregando…</p>
        ) : (
          <Table headers={['Nome', 'Tamanho', 'Trechos', 'Status', '']}>
            {files.map((f) => (
              <tr key={f.id} data-testid="file-row">
                <td className="px-3 py-2 text-slate-200">{f.name}</td>
                <td className="px-3 py-2 text-slate-400">
                  {(f.size_bytes / 1024).toFixed(0)} KB
                </td>
                <td className="px-3 py-2 text-slate-400">{f.chunk_count ?? '—'}</td>
                <td className="px-3 py-2">
                  <Badge ok={f.status === 'ready'}>{STATUS_LABEL[f.status]}</Badge>
                  {f.status === 'error' && (
                    <span className="ml-2 text-xs text-rose-400">{f.error_detail}</span>
                  )}
                </td>
                <td className="px-3 py-2 text-right">
                  <div className="flex justify-end gap-2">
                    {f.status === 'error' && (
                      <Button variant="ghost" onClick={() => reprocess.mutate(f.id)}>
                        Reprocessar
                      </Button>
                    )}
                    <Button variant="ghost" onClick={() => archive.mutate(f.id)}>
                      Arquivar
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  )
}
