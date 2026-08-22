import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Badge, Button, Card, EmptyState, ErrorText, Input, PageHeader, Textarea } from '../components/ui'
import { createCustomTool, deleteCustomTool, listCustomTools } from '../lib/customTools'

const EXAMPLE_CODE = `def main(inputs, context):
    # context["secrets"] contains only this tool's own saved secrets.
    return {"echo": inputs, "tenant_id": context["tenant_id"]}`

const EXAMPLE_SCHEMA = `{
  "type": "object",
  "properties": {"codigo": {"type": "string"}},
  "required": ["codigo"]
}`

export default function CustomTools() {
  const queryClient = useQueryClient()
  const { data: tools = [], isLoading, error: loadError } = useQuery({
    queryKey: ['custom-tools'],
    queryFn: listCustomTools,
  })
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [schema, setSchema] = useState(EXAMPLE_SCHEMA)
  const [code, setCode] = useState(EXAMPLE_CODE)
  const [secrets, setSecrets] = useState('{}')
  const [timeout, setTimeout] = useState(60)
  const [enabled, setEnabled] = useState(true)
  const [error, setError] = useState('')

  const create = useMutation({
    mutationFn: () => {
      let inputSchema: Record<string, unknown>
      let toolSecrets: Record<string, string>
      try {
        inputSchema = JSON.parse(schema) as Record<string, unknown>
        toolSecrets = JSON.parse(secrets || '{}') as Record<string, string>
      } catch {
        throw new Error('Schema e secrets precisam ser JSON válido.')
      }
      return createCustomTool({
        name,
        description,
        input_schema: inputSchema,
        python_code: code,
        secrets: toolSecrets,
        timeout_seconds: timeout,
        enabled,
      })
    },
    onSuccess: () => {
      setName('')
      setDescription('')
      setError('')
      queryClient.invalidateQueries({ queryKey: ['custom-tools'] })
    },
    onError: (reason: Error) => setError(reason.message),
  })
  const remove = useMutation({
    mutationFn: deleteCustomTool,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['custom-tools'] }),
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    setError('')
    create.mutate()
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Custom Tools"
        description="Cadastre funções Python isoladas da sua empresa e vincule-as aos agentes em Templates."
      />
      <Card title="Nova ferramenta Python">
        <form onSubmit={submit} className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <Input label="Nome" hint="snake_case; será o nome chamado pelo agente." required value={name} onChange={(event) => setName(event.target.value)} />
            <Input label="Timeout em segundos" type="number" min={1} max={3600} value={timeout} onChange={(event) => setTimeout(Number(event.target.value))} />
          </div>
          <Textarea label="Descrição" hint="Explique quando o agente deve usar esta ferramenta." required rows={3} value={description} onChange={(event) => setDescription(event.target.value)} />
          <Textarea label="Schema de entrada (JSON Schema)" rows={8} value={schema} onChange={(event) => setSchema(event.target.value)} className="font-mono" />
          <Textarea label="Código Python" hint="Implemente def main(inputs, context)." rows={12} value={code} onChange={(event) => setCode(event.target.value)} className="font-mono" />
          <Textarea label="Secrets da ferramenta (JSON)" hint="Gravados criptografados e nunca mostrados novamente." rows={4} value={secrets} onChange={(event) => setSecrets(event.target.value)} className="font-mono" />
          <label className="flex items-center gap-2 text-sm text-[var(--text-muted)]"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /> Habilitada para os agentes</label>
          <ErrorText>{error}</ErrorText>
          <Button type="submit" disabled={create.isPending}>{create.isPending ? 'Salvando…' : 'Salvar ferramenta'}</Button>
        </form>
      </Card>
      <Card title="Ferramentas cadastradas">
        {isLoading ? <p className="text-sm text-[var(--text-muted)]">Carregando…</p> : loadError ? <ErrorText>Não foi possível carregar as ferramentas.</ErrorText> : tools.length === 0 ? <EmptyState title="Nenhuma Custom Tool" description="Cadastre uma ferramenta acima; ela aparecerá no editor de Templates." /> : <div className="space-y-3">{tools.map((tool) => <div key={tool.id} className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-[var(--border)] p-4"><div><p className="font-mono text-sm font-semibold">{tool.name}</p><p className="mt-1 text-sm text-[var(--text-muted)]">{tool.description}</p><p className="mt-1 text-xs text-[var(--text-faint)]">timeout: {tool.timeout_seconds}s</p></div><div className="flex items-center gap-3"><Badge ok={tool.enabled}>{tool.enabled ? 'Habilitada' : 'Desabilitada'}</Badge><Button variant="danger" onClick={() => remove.mutate(tool.id)}>Excluir</Button></div></div>)}</div>}
      </Card>
    </div>
  )
}
