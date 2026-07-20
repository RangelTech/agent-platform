import { expect, test } from '@playwright/test'

const MASTER_EMAIL = process.env.E2E_MASTER_EMAIL ?? 'master@example.com'
const MASTER_PASSWORD = process.env.E2E_MASTER_PASSWORD ?? 'admin123'

async function signIn(page: import('@playwright/test').Page, email: string, password: string) {
  await page.goto('/')
  await page.fill('input[name="email"]', email)
  await page.fill('input[name="password"]', password)
  await page.click('button[type="submit"]')
}

test('admin creates a template with agents, deploys it and chats through it', async ({
  page,
  request,
}) => {
  await request.post('http://localhost:8080/stub/script', {
    data: { rules: [], default: 'Resposta simulada do stub.' },
  })
  const suffix = Date.now().toString(36)

  // Arrange tenant + admin via API.
  const masterToken = (
    await (
      await request.post('/api/auth/login', {
        data: { email: MASTER_EMAIL, password: MASTER_PASSWORD },
      })
    ).json()
  ).token
  const authHeader = { Authorization: `Bearer ${masterToken}` }
  const tenant = await (
    await request.post('/api/tenants', {
      data: { name: `Tpl E2E ${suffix}`, tenant_key: `tpl-e2e-${suffix}` },
      headers: authHeader,
    })
  ).json()
  const profiles = await (
    await request.get('/api/user-profiles', { headers: authHeader })
  ).json()
  const adminProfile = profiles.find(
    (p: { tenant_id: string; name: string }) =>
      p.tenant_id === tenant.id && p.name === 'Administrador',
  )
  const email = `tpl-${suffix}@e2e.com`
  await request.post('/api/users', {
    data: {
      email,
      name: 'Tpl Admin',
      password: 'senha-forte-123',
      profile_id: adminProfile.id,
      tenant_id: tenant.id,
    },
    headers: authHeader,
  })

  await signIn(page, email, 'senha-forte-123')

  // Create the template.
  await page.getByRole('link', { name: 'Templates' }).click()
  await page.fill('input[name="tpl-name"]', `Atendimento ${suffix}`)
  await page.fill('input[name="tpl-desc"]', 'Assistente de testes E2E')
  await page.getByRole('button', { name: 'Criar template' }).click()

  // Editor: supervisor + one specialist.
  await page.fill(
    'textarea:below(:text("Prompt do supervisor"))',
    'Você coordena os especialistas.',
  )
  await page.getByRole('button', { name: '+ Adicionar agente' }).click()
  const editor = page.getByTestId('agent-editor')
  await editor.locator('input').first().fill('suporte_agent')
  await editor
    .locator('input:below(:text("Quando o supervisor deve chamar"))')
    .first()
    .fill('dúvidas de suporte')
  await editor.locator('textarea').fill('Você é o suporte.')

  await page.getByRole('button', { name: 'Salvar nova versão' }).click()
  await expect(page.getByTestId('version-row').first()).toBeVisible()

  // Deploy v1.
  await page.getByRole('button', { name: 'Fazer deploy' }).click()
  await expect(page.getByText('em produção').first()).toBeVisible()

  // Chat: pick the template and talk through it (stub answers).
  await page.getByRole('link', { name: 'Chat' }).click()
  await page.selectOption('select[name="template-picker"]', { label: `Atendimento ${suffix}` })
  await page.fill('input[name="chat-input"]', 'olá, template!')
  await page.getByRole('button', { name: 'Enviar' }).click()
  await expect(page.getByTestId('message-list')).toContainText('Resposta simulada do stub.', {
    timeout: 15_000,
  })
})
