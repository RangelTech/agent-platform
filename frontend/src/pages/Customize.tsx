import { useMutation } from '@tanstack/react-query'
import { useRef, useState, type FormEvent } from 'react'
import { Button, Card, ErrorText, Input, Select } from '../components/ui'
import { api, getToken } from '../lib/api'
import { useAuth } from '../lib/auth'

export default function Customize() {
  const { user, refresh } = useAuth()
  const brand = user?.branding
  const [name, setName] = useState(brand?.name ?? '')
  const [color, setColor] = useState(brand?.color || '#4f46e5')
  const [theme, setTheme] = useState(brand?.theme ?? 'dark')
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const logoRef = useRef<HTMLInputElement>(null)

  const save = useMutation({
    mutationFn: () =>
      api('/tenants/branding', {
        method: 'PUT',
        body: JSON.stringify({ brand_name: name, brand_color: color, brand_theme: theme }),
      }),
    onSuccess: async () => {
      setError('')
      setSaved(true)
      await refresh()
      setTimeout(() => setSaved(false), 2500)
    },
    onError: (e: Error) => setError(e.message),
  })

  const uploadLogo = useMutation({
    mutationFn: async (file: File) => {
      const body = new FormData()
      body.append('file', file)
      const token = getToken()
      const res = await fetch('/api/tenants/branding/logo', {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body,
      })
      if (!res.ok) {
        const detail = await res.json().then((b) => b.detail).catch(() => 'Falha no upload')
        throw new Error(detail)
      }
    },
    onSuccess: () => refresh(),
    onError: (e: Error) => setError(e.message),
  })

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    save.mutate()
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <Card title="Personalizar a plataforma da sua empresa">
        <form onSubmit={onSubmit} className="space-y-4">
          <Input
            label="Nome exibido (marca)"
            name="brand-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Minha Empresa"
          />
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1 block text-sm text-[var(--text-muted)]">Cor primária</span>
              <div className="flex items-center gap-2">
                <input
                  type="color"
                  name="brand-color"
                  value={color}
                  onChange={(e) => setColor(e.target.value)}
                  className="h-10 w-14 cursor-pointer rounded border border-[var(--border)] bg-transparent"
                />
                <span className="font-mono text-sm text-[var(--text-muted)]">{color}</span>
              </div>
            </label>
            <Select
              label="Tema"
              name="brand-theme"
              value={theme}
              onChange={(e) => setTheme(e.target.value as 'dark' | 'light')}
            >
              <option value="dark">Escuro</option>
              <option value="light">Claro</option>
            </Select>
          </div>
          <div>
            <span className="mb-1 block text-sm text-[var(--text-muted)]">Logo (PNG/SVG, máx. 2MB)</span>
            <div className="flex items-center gap-3">
              {brand?.has_logo && (
                <img
                  src={`/api/tenants/branding/logo/${brand.tenant_key}?v=${Date.now()}`}
                  alt="logo"
                  className="h-10 w-10 rounded object-contain"
                />
              )}
              <input
                ref={logoRef}
                type="file"
                accept="image/png,image/jpeg,image/svg+xml,image/webp"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  if (f) uploadLogo.mutate(f)
                  e.target.value = ''
                }}
              />
              <Button type="button" variant="ghost" onClick={() => logoRef.current?.click()}>
                {uploadLogo.isPending ? 'Enviando…' : 'Enviar logo'}
              </Button>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Button type="submit" disabled={save.isPending}>
              Salvar personalização
            </Button>
            {saved && <span className="text-sm text-emerald-400">Salvo ✓</span>}
            <ErrorText>{error}</ErrorText>
          </div>
        </form>
      </Card>
    </div>
  )
}
