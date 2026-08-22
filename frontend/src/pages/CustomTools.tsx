import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Badge, Button, Card, EmptyState, ErrorText, Input, PageHeader, Textarea } from '../components/ui'
import {
  createCustomTool,
  deleteCustomTool,
  getCustomTool,
  listCustomTools,
  testCustomTool,
  updateCustomTool,
} from '../lib/customTools'

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
  const [testInputs, setTestInputs] = useState('{}')
  const [testResult, setTestResult] = useState('')
  const [error, setError] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)

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
      const body = {
        name,
        description,
        input_schema: inputSchema,
        python_code: code,
        timeout_seconds: timeout,
        enabled,
        ...(editingId && !secrets.trim() ? {} : { secrets: toolSecrets }),
      }
      return editingId ? updateCustomTool(editingId, body) : createCustomTool(body)
    },
    onSuccess: () => {
      setName('')
      setDescription('')
      setSchema(EXAMPLE_SCHEMA)
      setCode(EXAMPLE_CODE)
      setSecrets('{}')
      setTimeout(60)
      setEnabled(true)
      setEditingId(null)
      setError('')
      queryClient.invalidateQueries({ queryKey: ['custom-tools'] })
    },
    onError: (reason: Error) => setError(reason.message),
  })
  const remove = useMutation({
    mutationFn: deleteCustomTool,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['custom-tools'] }),
  })
  const test = useMutation({
    mutationFn: (id: string) => {
      let inputs: Record<string, unknown>
      try {
        inputs = JSON.parse(testInputs) as Record<string, unknown>
      } catch {
        throw new Error('O input do teste precisa ser JSON válido.')
      }
      return testCustomTool(id, inputs)
    },
    onSuccess: (result) => setTestResult(JSON.stringify(result, null, 2)),
    onError: (reason: Error) => setTestResult(`Erro: ${reason.message}`),
  })

  async function edit(id: string) {
    try {
      const tool = await getCustomTool(id)
      setEditingId(tool.id)
      setName(tool.name)
      setDescription(tool.description)
      setSchema(JSON.stringify(tool.input_schema, null, 2))
      setCode(tool.python_code)
      setSecrets('')
      setTimeout(tool.timeout_seconds)
      setEnabled(tool.enabled)
      setError('')
      window.scrollTo({ top: 0, behavior: 'smooth' })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Não foi possível abrir a ferramenta.')
    }
  }

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
      <Card title={editingId ? 'Editar ferramenta Python' : 'Nova ferramenta Python'}>
        <form onSubmit={submit} className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <Input label="Nome" hint="snake_case; será o nome chamado pelo agente." required value={name} onChange={(event) => setName(event.target.value)} />
            <Input label="Timeout em segundos" type="number" min={1} max={3600} value={timeout} onChange={(event) => setTimeout(Number(event.target.value))} />
          </div>
          <Textarea label="Descrição" hint="Explique quando o agente deve usar esta ferramenta." required rows={3} value={description} onChange={(event) => setDescription(event.target.value)} />
          <Textarea label="Schema de entrada (JSON Schema)" rows={8} value={schema} onChange={(event) => setSchema(event.target.value)} className="font-mono" />
          <Textarea label="Código Python" hint="Implemente def main(inputs, context)." rows={12} value={code} onChange={(event) => setCode(event.target.value)} className="font-mono" />
          <Textarea label="Secrets da ferramenta (JSON)" hint={editingId ? 'Deixe vazio para manter. Use {} para remover todos os secrets.' : 'Gravados criptografados e nunca mostrados novamente.'} rows={4} value={secrets} onChange={(event) => setSecrets(event.target.value)} className="font-mono" />
          <label className="flex items-center gap-2 text-sm text-[var(--text-muted)]"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /> Habilitada para os agentes</label>
          <ErrorText>{error}</ErrorText>
          <div className="flex gap-3"><Button type="submit" disabled={create.isPending}>{create.isPending ? 'Salvando…' : editingId ? 'Salvar alterações' : 'Salvar ferramenta'}</Button>{editingId && <Button type="button" variant="ghost" onClick={() => { setEditingId(null); setSecrets('{}') }}>Cancelar edição</Button>}</div>
        </form>
      </Card>
      <Card title="Ferramentas cadastradas">
        {isLoading ? <p className="text-sm text-[var(--text-muted)]">Carregando…</p> : loadError ? <ErrorText>Não foi possível carregar as ferramentas.</ErrorText> : tools.length === 0 ? <EmptyState title="Nenhuma Custom Tool" description="Cadastre uma ferramenta acima; ela aparecerá no editor de Templates." /> : <div className="space-y-3">{tools.map((tool) => <div key={tool.id} className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-[var(--border)] p-4"><div><p className="font-mono text-sm font-semibold">{tool.name}</p><p className="mt-1 text-sm text-[var(--text-muted)]">{tool.description}</p><p className="mt-1 text-xs text-[var(--text-faint)]">timeout: {tool.timeout_seconds}s</p></div><div className="flex items-center gap-3"><Badge ok={tool.enabled}>{tool.enabled ? 'Habilitada' : 'Desabilitada'}</Badge><Button variant="ghost" onClick={() => void edit(tool.id)}>Editar</Button><Button variant="danger" onClick={() => remove.mutate(tool.id)}>Excluir</Button></div></div>)}</div>}
      </Card>
      {tools.length > 0 && <Card title="Executar teste">
        <Textarea label="Input de teste (JSON)" rows={5} value={testInputs} onChange={(event) => setTestInputs(event.target.value)} className="font-mono" />
        <div className="mt-4 flex flex-wrap gap-3">{tools.map((tool) => <Button key={tool.id} variant="ghost" disabled={test.isPending} onClick={() => test.mutate(tool.id)}>Testar {tool.name}</Button>)}</div>
        {testResult && <pre className="mt-4 overflow-x-auto rounded-2xl bg-[var(--surface-soft)] p-4 text-xs text-[var(--text)]">{testResult}</pre>}
      </Card>}
    </div>
  )
}
