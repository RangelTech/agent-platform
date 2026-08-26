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

/**
 * Tela "Credenciais" unificada (produto-08 §10) -- substitui as antigas
 * `/contas-de-email` e `/contas-google`. Cobre: grid de cards por tipo,
 * cadastro real de conta de email (fluxo que não depende de OAuth externo,
 * então testável de ponta a ponta), e que o botão "Conectar com Google"
 * está disponível (o OAuth em si não é testável sem credencial real do
 * Google -- mesma limitação de escopo já aceita em ai-router.spec.ts pro
 * fluxo de assinatura).
 */
test('tela Credenciais: grid de tipos, cadastra conta de email de ponta a ponta', async ({
  page,
  request,
}) => {
  const suffix = Date.now().toString(36)
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
      data: { name: `Credenciais E2E ${suffix}`, tenant_key: `credenciais-e2e-${suffix}` },
      headers: authHeader,
    })
  ).json()
  const profiles = await (await request.get('/api/user-profiles', { headers: authHeader })).json()
  const adminProfile = profiles.find(
    (p: { tenant_id: string; name: string }) => p.tenant_id === tenant.id && p.name === 'Administrador',
  )
  const email = `credenciais-${suffix}@e2e.com`
  await request.post('/api/users', {
    data: {
      email,
      name: 'Credenciais Admin',
      password: 'senha-forte-123',
      profile_id: adminProfile.id,
      tenant_id: tenant.id,
    },
    headers: authHeader,
  })

  await signIn(page, email, 'senha-forte-123')
  await gotoSection(page, 'Credenciais')

  await expect(page.getByTestId('credencial-tipo-email')).toBeVisible()
  await expect(page.getByTestId('credencial-tipo-google')).toBeVisible()

  // Email já vem selecionado por padrão (primeiro tipo visível).
  await expect(page.getByRole('heading', { name: 'Nova conta de email' })).toBeVisible()

  await page.getByLabel('Nome (label)').fill('Recepção')
  await page.getByLabel('Endereço de email').fill('recepcao@clinica-e2e.com')
  await page.getByLabel('Servidor SMTP').fill('smtp.clinica-e2e.com')
  await page.getByLabel('Servidor IMAP').fill('imap.clinica-e2e.com')
  await page.getByLabel('Usuário').fill('recepcao@clinica-e2e.com')
  await page.getByLabel('Senha').fill('senha-app-de-teste')
  await page.getByRole('button', { name: 'Adicionar conta' }).click()

  await expect(page.getByText('recepcao@clinica-e2e.com')).toBeVisible()

  // Troca pro card Google: painel muda, o de email some da tela.
  await page.getByTestId('credencial-tipo-google').click()
  await expect(page.getByRole('heading', { name: 'Conectar conta Google' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Nova conta de email' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Conectar com Google' })).toBeVisible()
  await expect(page.getByText('Nenhuma conta Google conectada')).toBeVisible()

  // Troca pro card Microsoft (produto-08 §12): painel muda de novo.
  await expect(page.getByTestId('credencial-tipo-microsoft')).toBeVisible()
  await page.getByTestId('credencial-tipo-microsoft').click()
  await expect(page.getByRole('heading', { name: 'Conectar conta Microsoft' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Conectar conta Google' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Conectar com Microsoft' })).toBeVisible()
  await expect(page.getByText('Nenhuma conta Microsoft conectada')).toBeVisible()
})

test('membro sem permissão não vê Credenciais no menu', async ({ page, request }) => {
  const suffix = Date.now().toString(36)
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
      data: { name: `Credenciais Membro E2E ${suffix}`, tenant_key: `credenciais-membro-e2e-${suffix}` },
      headers: authHeader,
    })
  ).json()
  const profiles = await (await request.get('/api/user-profiles', { headers: authHeader })).json()
  const memberProfile = profiles.find(
    (p: { tenant_id: string; name: string }) => p.tenant_id === tenant.id && p.name === 'Usuário',
  )
  const email = `credenciais-membro-${suffix}@e2e.com`
  await request.post('/api/users', {
    data: {
      email,
      name: 'Credenciais Membro',
      password: 'senha-forte-123',
      profile_id: memberProfile.id,
      tenant_id: tenant.id,
    },
    headers: authHeader,
  })

  await signIn(page, email, 'senha-forte-123')
  await expect(page.getByTestId('current-user')).toBeVisible()
  await expectSectionAvailable(page, 'Credenciais', false)
})
