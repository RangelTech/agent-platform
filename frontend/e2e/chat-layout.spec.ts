import { expect, test, type Page } from '@playwright/test'

/**
 * A tela de chat existe para conversar: os painéis laterais são apoio e o
 * operador tem que conseguir tirá-los do caminho. Este spec fixa isso e a
 * largura mínima da área de conversa.
 */

const MASTER_EMAIL = process.env.E2E_MASTER_EMAIL ?? 'master@example.com'
const MASTER_PASSWORD = process.env.E2E_MASTER_PASSWORD ?? 'admin123'

test.use({ viewport: { width: 1920, height: 1080 } })

async function tenantUser(request: import('@playwright/test').APIRequestContext) {
  const login = await request.post('/api/auth/login', {
    data: { email: MASTER_EMAIL, password: MASTER_PASSWORD },
  })
  const headers = { Authorization: `Bearer ${(await login.json()).token}` }
  const suffix = Date.now().toString(36)
  const tenant = await (
    await request.post('/api/tenants', {
      data: { name: `Layout ${suffix}`, tenant_key: `layout-${suffix}` },
      headers,
    })
  ).json()
  const email = `layout-${suffix}@e2e.com`
  await request.post('/api/users', {
    data: { email, name: 'Layout', password: 'senha-forte-123', tenant_id: tenant.id },
    headers,
  })
  return { email, password: 'senha-forte-123' }
}

async function openChat(page: Page, request: import('@playwright/test').APIRequestContext) {
  const user = await tenantUser(request)
  await page.goto('/')
  await page.fill('input[name="email"]', user.email)
  await page.fill('input[name="password"]', user.password)
  await page.click('button[type="submit"]')
  await expect(page.getByRole('button', { name: 'Abrir navegação' })).toBeVisible()
  await page.goto('/chat')
  await expect(page.locator('[name="chat-input"]')).toBeVisible()
}

async function larguraDaConversa(page: Page) {
  // Mede a área de conversa, não o composer: ele tem largura máxima de
  // leitura e não cresce indefinidamente.
  const box = await page.getByTestId('message-list').boundingBox()
  return Math.round(box!.width)
}

test('os painéis laterais colapsam e devolvem espaço para a conversa', async ({ page, request }) => {
  await openChat(page, request)
  const inicial = await larguraDaConversa(page)

  await page.getByTestId('colapsar-conversas').click()
  await page.getByTestId('colapsar-live-tiles').click()

  // Colapsado vira uma faixa com o botão de reabrir, não some.
  await expect(page.getByTestId('expandir-conversas')).toBeVisible()
  await expect(page.getByTestId('expandir-live-tiles')).toBeVisible()
  expect(await larguraDaConversa(page)).toBeGreaterThan(inicial)

  await page.getByTestId('expandir-conversas').click()
  await page.getByTestId('expandir-live-tiles').click()
  await expect(page.getByTestId('colapsar-conversas')).toBeVisible()
  expect(await larguraDaConversa(page)).toBe(inicial)
})

test('a escolha de colapsar sobrevive ao recarregar', async ({ page, request }) => {
  await openChat(page, request)
  await page.getByTestId('colapsar-live-tiles').click()
  await page.reload()
  await expect(page.getByTestId('expandir-live-tiles')).toBeVisible()
})

test('o cabeçalho não repete o que já está na tela', async ({ page, request }) => {
  await openChat(page, request)

  // Métricas da sessão moram no painel de indicadores, não em dois lugares.
  await expect(page.getByTestId('tile-metrica')).toHaveCount(3)
  await expect(page.getByText('Prévia do envio')).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Nova sessão com agentes' })).toHaveCount(0)

  // Enviar fica no topo do composer, junto do estado da digitação.
  const enviar = page.getByRole('button', { name: /Enviar mensagem/ })
  const composer = page.locator('[name="chat-input"]')
  const enviarBox = await enviar.boundingBox()
  const composerBox = await composer.boundingBox()
  expect(enviarBox!.y).toBeLessThan(composerBox!.y)
})

test('o seletor de template continua acessível no cabeçalho', async ({ page, request }) => {
  await openChat(page, request)
  await expect(page.locator('select[name="template-picker"]')).toBeVisible()
})
