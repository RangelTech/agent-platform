import { expect, test } from '@playwright/test'

const MASTER_EMAIL = process.env.E2E_MASTER_EMAIL ?? 'master@example.com'
const MASTER_PASSWORD = process.env.E2E_MASTER_PASSWORD ?? 'admin123'

async function signIn(page: import('@playwright/test').Page, email: string, password: string) {
  await page.goto('/')
  await page.fill('input[name="email"]', email)
  await page.fill('input[name="password"]', password)
  await page.click('button[type="submit"]')
}

test('wizard creates company+admin; admin brands it; users see the brand', async ({ page }) => {
  const suffix = Date.now().toString(36)
  const adminEmail = `admin-${suffix}@brand.com`

  // Master: one-step wizard.
  await signIn(page, MASTER_EMAIL, MASTER_PASSWORD)
  await page.getByRole('link', { name: 'Empresas' }).click()
  await page.fill('input[name="tenant-name"]', `Brand ${suffix}`)
  await page.fill('input[name="tenant-key"]', `brand-${suffix}`)
  await page.fill('input[name="admin-name"]', 'Admin Brand')
  await page.fill('input[name="admin-email"]', adminEmail)
  await page.fill('input[name="admin-password"]', 'senha-forte-123')
  await page.getByRole('button', { name: 'Criar empresa' }).click()
  await expect(page.getByText(`brand-${suffix}`)).toBeVisible()
  await page.getByRole('button', { name: 'Sair' }).click()

  // The wizard-created admin signs in and customizes.
  await signIn(page, adminEmail, 'senha-forte-123')
  await expect(page.getByTestId('current-user')).toHaveText('Admin Brand')

  await page.getByRole('link', { name: 'Personalizar' }).click()
  await page.fill('input[name="brand-name"]', 'ACME Espacial')
  await page.getByRole('button', { name: 'Salvar personalização' }).click()
  await expect(page.getByText('Salvo ✓')).toBeVisible()
  // Brand name replaces the platform name in the header.
  await expect(page.getByRole('banner')).toContainText('ACME Espacial')

  // A regular user of the company lands on the chat and sees the brand.
  const userEmail = `user-${suffix}@brand.com`
  await page.getByRole('link', { name: 'Usuários' }).click()
  await page.fill('input[name="user-name"]', 'Usuária Comum')
  await page.fill('input[name="user-email"]', userEmail)
  await page.fill('input[name="user-password"]', 'senha-forte-123')
  await page.selectOption('select[name="user-profile"]', { label: 'Usuário' })
  await page.getByRole('button', { name: 'Criar usuário' }).click()
  await expect(page.getByText(userEmail)).toBeVisible()
  await page.getByRole('button', { name: 'Sair' }).click()

  await signIn(page, userEmail, 'senha-forte-123')
  // Chat-home: the plain user lands on the conversation screen.
  await expect(page.locator('input[name="chat-input"]')).toBeVisible()
  await expect(page.getByRole('banner')).toContainText('ACME Espacial')
  // And has no management links.
  await expect(page.getByRole('link', { name: 'Usuários' })).toHaveCount(0)
})
