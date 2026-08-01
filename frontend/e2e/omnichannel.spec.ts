import { expect, test, type Page } from '@playwright/test'
import { gotoSection } from './helpers'

/**
 * A tela de Atendimento é a porta entre a plataforma e o Chatwoot. Se ela
 * quebrar, o operador não tem outro caminho: não existe login separado.
 */

const MASTER_EMAIL = process.env.E2E_MASTER_EMAIL ?? 'master@example.com'
const MASTER_PASSWORD = process.env.E2E_MASTER_PASSWORD ?? 'admin123'

async function signIn(page: Page, email: string, password: string) {
  await page.goto('/')
  await page.fill('input[name="email"]', email)
  await page.fill('input[name="password"]', password)
  await page.click('button[type="submit"]')
  await expect(page.getByRole('button', { name: 'Abrir navegação' })).toBeVisible()
}

async function tenantOwner(request: import('@playwright/test').APIRequestContext) {
  const login = await request.post('/api/auth/login', {
    data: { email: MASTER_EMAIL, password: MASTER_PASSWORD },
  })
  const headers = { Authorization: `Bearer ${(await login.json()).token}` }
  const suffix = Date.now().toString(36)
  const tenant = await (
    await request.post('/api/tenants', {
      data: { name: `Omni UI ${suffix}`, tenant_key: `omni-ui-${suffix}` },
      headers,
    })
  ).json()
  const email = `omni-ui-${suffix}@e2e.com`
  // Sem profile_id de propósito: o primeiro usuário da empresa tem que entrar
  // como administrador dela, senão a navegação nasce vazia.
  await request.post('/api/users', {
    data: { email, name: 'Dono UI', password: 'senha-forte-123', tenant_id: tenant.id },
    headers,
  })
  return { email, password: 'senha-forte-123' }
}

test('o dono da empresa enxerga as áreas administrativas', async ({ page, request }) => {
  const user = await tenantOwner(request)
  await signIn(page, user.email, user.password)
  await page.getByRole('button', { name: 'Abrir navegação' }).click()

  const drawer = page.getByTestId('nav-drawer')
  for (const secao of ['Templates', 'Fontes de dados', 'Integrações', 'Pagamentos', 'Atendimento']) {
    await expect(drawer.getByRole('link', { name: secao })).toBeVisible()
  }
  await expect(page.getByTestId('nav-scope-note')).toHaveCount(0)
})

test('quem tem perfil restrito entende por que vê pouca coisa', async ({ page, request }) => {
  const login = await request.post('/api/auth/login', {
    data: { email: MASTER_EMAIL, password: MASTER_PASSWORD },
  })
  const headers = { Authorization: `Bearer ${(await login.json()).token}` }
  const suffix = Date.now().toString(36)
  const tenant = await (
    await request.post('/api/tenants', {
      data: { name: `Restrito ${suffix}`, tenant_key: `restrito-${suffix}` },
      headers,
    })
  ).json()
  // O segundo usuário entra como membro: menu curto é escopo, não bug.
  await request.post('/api/users', {
    data: { email: `dono-${suffix}@e2e.com`, name: 'Dono', password: 'senha-forte-123', tenant_id: tenant.id },
    headers,
  })
  const email = `membro-${suffix}@e2e.com`
  await request.post('/api/users', {
    data: { email, name: 'Membro', password: 'senha-forte-123', tenant_id: tenant.id },
    headers,
  })

  await signIn(page, email, 'senha-forte-123')
  await page.getByRole('button', { name: 'Abrir navegação' }).click()
  await expect(page.getByTestId('nav-scope-note')).toBeVisible()
})

test('a tela de atendimento diz o estado da operação omnichannel', async ({ page, request }) => {
  const user = await tenantOwner(request)
  await signIn(page, user.email, user.password)
  await gotoSection(page, 'Atendimento')

  await expect(page.getByRole('heading', { name: 'Atendimento omnichannel' })).toBeVisible()
  // Ou a camada está ligada e oferece a ação, ou ela explica que está desligada.
  await expect(
    page
      .getByRole('button', { name: /Criar operação de atendimento|Abrir atendimento/ })
      .or(page.getByTestId('empty-state')),
  ).toBeVisible()
})
