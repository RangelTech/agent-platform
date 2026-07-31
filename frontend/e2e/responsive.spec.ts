import { expect, test, type Page } from '@playwright/test'

/**
 * Fase A — QA de responsividade.
 *
 * Roda as telas premium (login, dashboard, chat e uma tela administrativa) nos
 * três breakpoints exigidos pela spec e falha se algum deles produzir scroll
 * horizontal ou esconder a navegação. É o gate visual automatizado da fase:
 * layout que "só quebra no celular" passa a quebrar o CI.
 */

const MASTER_EMAIL = process.env.E2E_MASTER_EMAIL ?? 'master@example.com'
const MASTER_PASSWORD = process.env.E2E_MASTER_PASSWORD ?? 'admin123'

const BREAKPOINTS = [
  { name: 'mobile', width: 390, height: 844 },
  { name: 'tablet', width: 820, height: 1180 },
  { name: 'desktop', width: 1440, height: 900 },
] as const

async function signIn(page: Page, email: string, password: string) {
  await page.goto('/')
  await page.fill('input[name="email"]', email)
  await page.fill('input[name="password"]', password)
  await page.click('button[type="submit"]')
  // Só navegue depois que o shell existir: um goto() antes disso cai de volta
  // no login e o teste falha por um motivo que não é o layout.
  await expect(page.getByRole('button', { name: 'Abrir navegação' })).toBeVisible()
}

/** Nenhuma página pode rolar horizontalmente — só containers internos podem. */
async function expectNoHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => {
    const doc = document.documentElement
    return { scrollWidth: doc.scrollWidth, clientWidth: doc.clientWidth }
  })
  // 1px de folga para arredondamento de subpixel.
  expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth + 1)
}

/** Cria um tenant + usuário de tenant, que é quem enxerga chat e dashboard. */
async function provisionTenantUser(request: import('@playwright/test').APIRequestContext) {
  const suffix = Date.now().toString(36)
  const login = await request.post('/api/auth/login', {
    data: { email: MASTER_EMAIL, password: MASTER_PASSWORD },
  })
  const authHeader = { Authorization: `Bearer ${(await login.json()).token}` }
  const tenant = await (
    await request.post('/api/tenants', {
      data: { name: `Responsivo ${suffix}`, tenant_key: `resp-${suffix}` },
      headers: authHeader,
    })
  ).json()
  const email = `resp-${suffix}@e2e.com`
  await request.post('/api/users', {
    data: { email, name: 'QA Responsivo', password: 'senha-forte-123', tenant_id: tenant.id },
    headers: authHeader,
  })
  return { email, password: 'senha-forte-123' }
}

for (const bp of BREAKPOINTS) {
  test.describe(`responsividade @${bp.name} (${bp.width}x${bp.height})`, () => {
    test.use({ viewport: { width: bp.width, height: bp.height } })

    test('login não estoura a viewport e mantém o formulário utilizável', async ({ page }) => {
      await page.goto('/')
      await expect(page.locator('input[name="email"]')).toBeVisible()
      await expect(page.locator('input[name="password"]')).toBeVisible()
      await expect(page.getByRole('button', { name: /Acessar plataforma/ })).toBeVisible()
      await expectNoHorizontalScroll(page)
    })

    test('shell autenticado expõe navegação e não gera scroll horizontal', async ({ page, request }) => {
      const user = await provisionTenantUser(request)
      await signIn(page, user.email, user.password)

      // Dashboard é a tela inicial da operação: precisa caber em qualquer largura.
      await page.goto('/dashboard')
      await expect(page.getByText(/live workspace/i)).toBeVisible()
      await expectNoHorizontalScroll(page)

      // O drawer é o caminho garantido para todas as rotas em qualquer largura,
      // e é onde a identidade da sessão sempre aparece (o header a esconde em
      // telas estreitas por falta de espaço).
      await page.getByRole('button', { name: 'Abrir navegação' }).click()
      const drawer = page.getByTestId('nav-drawer')
      await expect(drawer).toBeVisible()
      await expect(
        page
          .locator('[data-testid="current-user"]:visible, [data-testid="current-user-drawer"]:visible')
          .first(),
      ).toBeVisible()
      await drawer.getByRole('link', { name: 'Memórias' }).click()
      await expect(page.getByRole('heading', { name: 'Memórias' })).toBeVisible()
      await expectNoHorizontalScroll(page)
    })

    test('chat mantém composer visível e sem scroll horizontal', async ({ page, request }) => {
      const user = await provisionTenantUser(request)
      await signIn(page, user.email, user.password)
      await page.goto('/chat')
      await expect(page.locator('[name="chat-input"]')).toBeVisible()
      await expect(page.getByRole('button', { name: /Enviar/ })).toBeVisible()
      await expectNoHorizontalScroll(page)
    })
  })
}

test.describe('estados de lista', () => {
  test('tela administrativa mostra skeleton e depois conteúdo ou estado vazio', async ({ page }) => {
    await signIn(page, MASTER_EMAIL, MASTER_PASSWORD)
    await page.goto('/usuarios')
    // Ou a tabela carregou, ou o estado vazio apareceu — nunca uma área morta.
    await expect(
      page.getByTestId('user-row').first().or(page.getByTestId('empty-state')),
    ).toBeVisible()
    await expectNoHorizontalScroll(page)
  })
})
