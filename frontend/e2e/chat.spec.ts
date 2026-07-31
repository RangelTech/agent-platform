import { expect, test } from '@playwright/test'
import { gotoSection } from './helpers'

const MASTER_EMAIL = process.env.E2E_MASTER_EMAIL ?? 'master@example.com'
const MASTER_PASSWORD = process.env.E2E_MASTER_PASSWORD ?? 'admin123'

async function signIn(page: import('@playwright/test').Page, email: string, password: string) {
  await page.goto('/')
  await page.fill('input[name="email"]', email)
  await page.fill('input[name="password"]', password)
  await page.click('button[type="submit"]')
}

test('tenant user chats and receives a streamed reply', async ({ page, request }) => {
  await request.post('http://localhost:8080/stub/script', {
    data: { rules: [], default: 'Resposta simulada do stub.' },
  })
  const suffix = Date.now().toString(36)

  // Arrange a tenant + user through the API (master credentials).
  const login = await request.post('/api/auth/login', {
    data: { email: MASTER_EMAIL, password: MASTER_PASSWORD },
  })
  const masterToken = (await login.json()).token
  const authHeader = { Authorization: `Bearer ${masterToken}` }

  const tenant = await (
    await request.post('/api/tenants', {
      data: { name: `Chat E2E ${suffix}`, tenant_key: `chat-e2e-${suffix}` },
      headers: authHeader,
    })
  ).json()

  const email = `chat-${suffix}@e2e.com`
  await request.post('/api/users', {
    data: {
      email,
      name: 'Chat E2E',
      password: 'senha-forte-123',
      tenant_id: tenant.id,
    },
    headers: authHeader,
  })

  // Act: sign in and send a message.
  await signIn(page, email, 'senha-forte-123')
  await gotoSection(page, 'Chat')
  await page.fill('[name="chat-input"]', 'olá, tudo bem?')
  await page.getByRole('button', { name: /Enviar/ }).click()

  // Assert: user bubble + assistant reply (stub answers deterministically).
  await expect(page.getByText('olá, tudo bem?')).toBeVisible()
  await expect(page.getByTestId('message-list')).toContainText('Resposta simulada do stub.', {
    timeout: 15_000,
  })

  // The conversation shows up in the sidebar and history survives a reload.
  await expect(page.getByTestId('chat-item').first()).toBeVisible()
  await page.reload()
  await page.getByTestId('chat-item').first().click()
  await expect(page.getByTestId('message-list')).toContainText('Resposta simulada do stub.')
})
