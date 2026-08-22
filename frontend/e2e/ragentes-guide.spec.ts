import { expect, test } from '@playwright/test'

const MASTER_EMAIL = process.env.E2E_MASTER_EMAIL ?? 'master@example.com'
const MASTER_PASSWORD = process.env.E2E_MASTER_PASSWORD ?? 'admin123'

async function signIn(page: import('@playwright/test').Page, email: string, password: string) {
  await page.goto('/')
  await page.fill('input[name="email"]', email)
  await page.fill('input[name="password"]', password)
  await page.click('button[type="submit"]')
}

test('guide explains the missing AI prerequisite to a tenant administrator', async ({ page, request }) => {
  const suffix = Date.now().toString(36)
  const adminEmail = `guide-${suffix}@e2e.com`
  const master = await request.post('/api/auth/login', {
    data: { email: MASTER_EMAIL, password: MASTER_PASSWORD },
  })
  const masterToken = (await master.json()).token
  const created = await request.post('/api/tenants', {
    headers: { Authorization: `Bearer ${masterToken}` },
    data: {
      name: `Guide ${suffix}`,
      tenant_key: `guide-${suffix}`,
      admin_name: 'Admin do Guia',
      admin_email: adminEmail,
      admin_password: 'senha-forte-123',
    },
  })
  expect(created.ok()).toBeTruthy()

  await signIn(page, adminEmail, 'senha-forte-123')
  await page.goto('/chat')
  await expect(page.getByText('Precisa de ajuda com a RAgentes?')).toBeVisible()
  await page.getByRole('button', { name: 'Abrir Assistente RAgentes' }).click()
  await expect(page).toHaveURL(/\/servicos-ia\?notice=guide-needs-ai/)
  await expect(page.getByRole('alert')).toContainText('Conecte um serviço de IA')
})
