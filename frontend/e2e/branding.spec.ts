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

test('wizard creates company+admin; admin brands it; users see the brand', async ({ page }) => {
  const suffix = Date.now().toString(36)
  const adminEmail = `admin-${suffix}@brand.com`

  // Master: one-step wizard.
  await signIn(page, MASTER_EMAIL, MASTER_PASSWORD)
  await gotoSection(page, 'Empresas')
  await page.fill('input[name="tenant-name"]', `Brand ${suffix}`)
  await page.fill('input[name="tenant-key"]', `brand-${suffix}`)
  await page.fill('input[name="admin-name"]', 'Admin Brand')
  await page.fill('input[name="admin-email"]', adminEmail)
  await page.fill('input[name="admin-password"]', 'senha-forte-123')
  await page.getByRole('button', { name: /Criar empresa|Nova empresa/ }).click()
  await expect(page.getByText(`brand-${suffix}`)).toBeVisible()
  await page.getByRole('button', { name: 'Sair' }).click()

  // The wizard-created admin signs in and customizes.
  await signIn(page, adminEmail, 'senha-forte-123')
  await expect(page.getByTestId('current-user')).toHaveText('Admin Brand')

  await gotoSection(page, 'Personalizar')
  await page.fill('input[name="brand-name"]', 'ACME Espacial')
  await page.getByRole('button', { name: 'Salvar personalização' }).click()
  await expect(page.getByText('Salvo ✓')).toBeVisible()
  // Brand name replaces the platform name in the header.
  await expect(page.getByRole('banner')).toContainText('ACME Espacial')

  // A regular user of the company lands on the chat and sees the brand.
  const userEmail = `user-${suffix}@brand.com`
  await gotoSection(page, 'Usuários')
  await page.fill('input[name="user-name"]', 'Usuária Comum')
  await page.fill('input[name="user-email"]', userEmail)
  await page.fill('input[name="user-password"]', 'senha-forte-123')
  await page.selectOption('select[name="user-profile"]', { label: 'Usuário' })
  await page.getByRole('button', { name: /Criar usuário|Novo usuário/ }).click()
  await expect(page.getByText(userEmail)).toBeVisible()
  await page.getByRole('button', { name: 'Sair' }).click()

  await signIn(page, userEmail, 'senha-forte-123')
  // Home is the dashboard (Fase A); the chat is one click away and branded.
  await expect(page.getByText(/live workspace/i)).toBeVisible()
  await expect(page.getByRole('banner')).toContainText('ACME Espacial')
  await gotoSection(page, 'Chat')
  await expect(page.locator('[name="chat-input"]')).toBeVisible()
  // And has no management links.
  await expectSectionAvailable(page, 'Usuários', false)
})
