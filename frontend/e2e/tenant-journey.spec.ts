import { expect, test } from '@playwright/test'

const MASTER_EMAIL = process.env.E2E_MASTER_EMAIL ?? 'master@example.com'
const MASTER_PASSWORD = process.env.E2E_MASTER_PASSWORD ?? 'admin123'
const KERNEL_URL = process.env.E2E_KERNEL_URL ?? 'http://localhost:8080'

async function signIn(page: import('@playwright/test').Page, email: string, password: string) {
  await page.goto('/')
  await page.fill('input[name="email"]', email)
  await page.fill('input[name="password"]', password)
  await page.click('button[type="submit"]')
}

test('full company-admin journey: keys, Excel catalog, seller template, chat sale', async ({
  page,
  request,
}) => {
  test.setTimeout(120_000)
  const suffix = Date.now().toString(36)
  const adminEmail = `dono-${suffix}@loja.com`

  // The master's ONLY act: the wizard.
  const masterToken = (
    await (
      await request.post('/api/auth/login', {
        data: { email: MASTER_EMAIL, password: MASTER_PASSWORD },
      })
    ).json()
  ).token
  await request.post('/api/tenants', {
    data: {
      name: `Loja ${suffix}`,
      tenant_key: `loja-${suffix}`,
      admin_name: 'Dona da Loja',
      admin_email: adminEmail,
      admin_password: 'senha-forte-123',
    },
    headers: { Authorization: `Bearer ${masterToken}` },
  })

  // Everything below is the company admin, self-service.
  await signIn(page, adminEmail, 'senha-forte-123')

  // 1. Registers their own AI key (BYOK), sees it listed, archives it so the
  //    chat exercises the deterministic stub instead of a real provider.
  await page.getByRole('link', { name: 'Serviços de IA' }).click()
  await page.fill('input[name="svc-name"]', 'Minha chave Gemini')
  await page.fill('input[name="svc-key"]', 'chave-de-mentira-para-e2e')
  await page.getByRole('button', { name: 'Cadastrar serviço' }).click()
  await expect(page.getByText('Minha chave Gemini')).toBeVisible()
  await page
    .getByTestId('service-row')
    .filter({ hasText: 'Minha chave Gemini' })
    .getByRole('button', { name: 'Arquivar' })
    .click()
  await expect(page.getByText('Minha chave Gemini')).toHaveCount(0)

  // 2. Uploads the product spreadsheet and waits for ingestion.
  await page.getByRole('link', { name: 'Arquivos' }).click()
  await page
    .getByTestId('file-input')
    .setInputFiles('e2e/fixtures/produtos.xlsx')
  await expect(page.getByTestId('file-row').filter({ hasText: 'produtos.xlsx' })).toContainText(
    'Pronto',
    { timeout: 30_000 },
  )

  // 3. Builds the seller template: one specialist with RAG over the sheet.
  await page.getByRole('link', { name: 'Templates' }).click()
  await page.fill('input[name="tpl-name"]', 'Vendedor')
  await page.fill('input[name="tpl-desc"]', 'Vende os produtos do catálogo')
  await page.getByRole('button', { name: 'Criar template' }).click()
  await page.fill(
    'textarea:below(:text("Prompt do supervisor"))',
    'Você coordena o vendedor da loja.',
  )
  await page.getByRole('button', { name: '+ Adicionar agente' }).click()
  const editor = page.getByTestId('agent-editor')
  await editor.locator('input').first().fill('vendedor_agent')
  await editor
    .locator('input:below(:text("Quando o supervisor deve chamar"))')
    .first()
    .fill('preços e produtos do catálogo')
  await editor.locator('textarea').fill('Você vende os produtos do catálogo da loja.')
  await editor.getByText('query_agent_rag').click()
  await editor.getByText('produtos.xlsx').click()
  await page.getByRole('button', { name: 'Salvar nova versão' }).click()
  await expect(page.getByTestId('version-row').first()).toBeVisible()
  await page.getByRole('button', { name: 'Fazer deploy' }).click()
  await expect(page.getByText('em produção').first()).toBeVisible()

  // 4. Scripts the stub (test seam) so the flow is deterministic and free.
  await request.post(`${KERNEL_URL}/stub/script`, {
    data: {
      rules: [
        ['quanto custa', 'TOOL:vendedor_agent:{"task": "consultar preco do item parafuso M8"}'],
        ['consultar preco do item', 'TOOL:query_agent_rag:{"question": "preço Parafuso M8"}'],
        ['2,50', 'O Parafuso M8 custa R$ 2,50 — temos 500 em estoque.'],
      ],
      default: 'não encontrei no catálogo',
    },
  })

  // 5. Sells through the chat using the sheet's data.
  await page.getByRole('link', { name: 'Chat' }).click()
  await page.selectOption('select[name="template-picker"]', { label: 'Vendedor' })
  await page.fill('input[name="chat-input"]', 'quanto custa o parafuso M8?')
  await page.getByRole('button', { name: 'Enviar' }).click()
  await expect(page.getByTestId('message-list')).toContainText('R$ 2,50', { timeout: 30_000 })
})
