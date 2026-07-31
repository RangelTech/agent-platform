import { expect, test } from '@playwright/test'
import { expectSectionAvailable, gotoSection } from './helpers'

const MASTER_EMAIL = process.env.E2E_MASTER_EMAIL ?? 'master@example.com'
const MASTER_PASSWORD = process.env.E2E_MASTER_PASSWORD ?? 'admin123'

async function signIn(page: import('@playwright/test').Page, email: string, password: string) {
  await page.goto('/')
  await page.fill('input[name="email"]', email)
  await page.fill('input[name="password"]', password)
  await page.click('button[type="submit"]')
}

test('rejects bad credentials with a visible message', async ({ page }) => {
  await signIn(page, MASTER_EMAIL, 'senha-errada')
  await expect(page.getByRole('alert')).toBeVisible()
  await expect(page.locator('input[name="password"]')).toBeVisible()
})

test('master signs in and reaches the admin shell', async ({ page }) => {
  await signIn(page, MASTER_EMAIL, MASTER_PASSWORD)
  await expect(page.getByTestId('current-user')).toBeVisible()
  await expectSectionAvailable(page, 'Empresas')
})

test('master creates a tenant and a user inside it', async ({ page }) => {
  const suffix = Date.now().toString(36)
  const tenantName = `E2E ${suffix}`
  const tenantKey = `e2e-${suffix}`
  const userEmail = `user-${suffix}@e2e.com`

  await signIn(page, MASTER_EMAIL, MASTER_PASSWORD)

  await gotoSection(page, 'Empresas')
  await page.fill('input[name="tenant-name"]', tenantName)
  await page.fill('input[name="tenant-key"]', tenantKey)
  await page.getByRole('button', { name: /Criar empresa|Nova empresa/ }).click()
  await expect(page.getByText(tenantKey)).toBeVisible()

  await gotoSection(page, 'Usuários')
  await page.fill('input[name="user-name"]', 'Usuário E2E')
  await page.fill('input[name="user-email"]', userEmail)
  await page.fill('input[name="user-password"]', 'senha-forte-123')
  await page.selectOption('select[name="user-tenant"]', { label: tenantName })
  await page.selectOption('select[name="user-profile"]', { label: 'Administrador' })
  await page.getByRole('button', { name: /Criar usuário|Novo usuário/ }).click()
  await expect(page.getByText(userEmail)).toBeVisible()

  // The new admin can sign in and is scoped to their own tenant.
  await page.getByRole('button', { name: 'Sair' }).click()
  await signIn(page, userEmail, 'senha-forte-123')
  await expect(page.getByTestId('current-user')).toHaveText('Usuário E2E')
  await expectSectionAvailable(page, 'Empresas', false)
})

test('signing out returns to the login screen', async ({ page }) => {
  await signIn(page, MASTER_EMAIL, MASTER_PASSWORD)
  await page.getByRole('button', { name: 'Sair' }).click()
  await expect(page.locator('input[name="email"]')).toBeVisible()
})
