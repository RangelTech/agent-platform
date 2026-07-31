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

test('admin creates a custom profile, edits a user, permissions apply', async ({
  page,
  request,
}) => {
  const suffix = Date.now().toString(36)
  const adminEmail = `admin-${suffix}@perf.com`

  // Arrange: company + admin via wizard API.
  const masterToken = (
    await (
      await request.post('/api/auth/login', {
        data: { email: MASTER_EMAIL, password: MASTER_PASSWORD },
      })
    ).json()
  ).token
  await request.post('/api/tenants', {
    data: {
      name: `Perf ${suffix}`,
      tenant_key: `perf-${suffix}`,
      admin_name: 'Admin Perf',
      admin_email: adminEmail,
      admin_password: 'senha-forte-123',
    },
    headers: { Authorization: `Bearer ${masterToken}` },
  })

  await signIn(page, adminEmail, 'senha-forte-123')

  // Create the custom profile: only usage.view (plus chat implicitly free).
  await gotoSection(page, 'Perfis')
  await page.getByRole('button', { name: '+ Novo perfil' }).click()
  await page.fill('input[name="profile-name"]', 'Somente Consumo')
  await page.getByTestId('perm-usage-view').check()
  await page.getByRole('button', { name: 'Salvar perfil' }).click()
  await expect(page.getByText('Somente Consumo')).toBeVisible()

  // Create a user with that profile.
  const userEmail = `viewer-${suffix}@perf.com`
  await gotoSection(page, 'Usuários')
  await page.fill('input[name="user-name"]', 'Viewer')
  await page.fill('input[name="user-email"]', userEmail)
  await page.fill('input[name="user-password"]', 'senha-forte-123')
  await page.selectOption('select[name="user-profile"]', { label: 'Somente Consumo' })
  await page.getByRole('button', { name: /Criar usuário|Novo usuário/ }).click()
  await expect(page.getByText(userEmail)).toBeVisible()

  // Edit the user: rename + reset password through the UI.
  await page
    .getByTestId('user-row')
    .filter({ hasText: userEmail })
    .getByRole('button', { name: 'Editar' })
    .click()
  await page.fill('input[name="edit-name"]', 'Viewer Renomeada')
  await page.fill('input[name="edit-password"]', 'senha-nova-4567')
  await page.getByRole('button', { name: 'Salvar' }).click()
  await expect(page.getByText('Viewer Renomeada')).toBeVisible()
  await page.getByRole('button', { name: 'Sair' }).click()

  // New password works; old is dead; permissions match the custom profile.
  await signIn(page, userEmail, 'senha-nova-4567')
  await expect(page.getByTestId('current-user')).toHaveText('Viewer Renomeada')
  await expectSectionAvailable(page, 'Consumo')
  await expectSectionAvailable(page, 'Usuários', false)
  await expectSectionAvailable(page, 'Templates', false)

  // Backend enforces it too, not just the nav.
  const viewerToken = (
    await (
      await request.post('/api/auth/login', {
        data: { email: userEmail, password: 'senha-nova-4567' },
      })
    ).json()
  ).token
  const forbidden = await request.get('/api/users', {
    headers: { Authorization: `Bearer ${viewerToken}` },
  })
  expect(forbidden.status()).toBe(403)
  const allowed = await request.get('/api/usage', {
    headers: { Authorization: `Bearer ${viewerToken}` },
  })
  expect(allowed.status()).toBe(200)
})
