import { expect, test } from '@playwright/test'
import { createState, installMock, sse } from './mockApi'

test.describe('chat', () => {
  test('seletor agrupa por tipo e só lista agentes com versão ativa', async ({ page }) => {
    await installMock(page, createState())
    await page.goto('/chat')
    const picker = page.locator('select[name="template-picker"]')
    await expect(picker.locator('optgroup')).toHaveCount(2)
    await expect(picker.locator('optgroup[label="Estruturados"] option')).toHaveText(['Vendas', 'Padrão da casa'])
    await expect(picker.locator('optgroup[label="Por skills"] option')).toHaveText(['Atendimento N1'])
    await expect(picker.locator('option[value=""]')).toHaveText('Agente: Padrão')
    await expect(picker.locator('option', { hasText: 'Rascunho cobranca' })).toHaveCount(0)
  })

  test('eventos step, approval_required, skill_proposed e usage; envia template_id', async ({ page }) => {
    const state = createState()
    state.approvals.push({ approval_id: 'ap1', chat_id: 'c1', summary: 'Enviar e-mail para 3 clientes', risk: 'high', kind: 'send', details: {}, expires_at: new Date(Date.now() + 300_000).toISOString() })
    state.chatSend = (route) =>
      route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: sse([
          ['chat', { chat_id: 'c1', run_id: 'r1' }],
          ['step', { kind: 'skill', label: 'triagem', status: 'start' }],
          ['evento_futuro', { qualquer: 'coisa' }],
          ['approval_required', { approval_id: 'ap1', summary: 'Enviar e-mail para 3 clientes', risk: 'high', expires_at: new Date(Date.now() + 120_000).toISOString() }],
          ['skill_proposed', { user_skill_id: 'us1', name: 'u-cobranca-educada', status: 'pending_review' }],
          ['usage', { input_tokens: 120, output_tokens: 45 }],
          ['token', { text: 'Pronto.' }],
          ['done', { text: 'Pronto.' }],
        ]),
      })
    await installMock(page, state)
    await page.goto('/chat')
    await page.locator('select[name="template-picker"]').selectOption({ label: 'Atendimento N1' })
    await page.locator('textarea[name="chat-input"]').fill('Cobre o cliente')
    await page.getByTestId('chat-send').click()

    const card = page.getByTestId('approval-card')
    await expect(card).toBeVisible()
    await expect(card.getByTestId('approval-summary')).toContainText('Enviar e-mail para 3 clientes')
    await expect(card.getByTestId('approval-risk')).toContainText('Risco alto')
    await expect(page.getByTestId('skill-proposed-notice')).toBeVisible()
    await card.getByTestId('approval-approve').click()
    await expect(card.getByTestId('approval-result')).toContainText('Você aprovou esta ação.')
    const send = state.calls.find((c) => c.path === '/chat/send')
    expect(send?.body).toMatchObject({ template_id: 't-sk' })
    expect(state.calls.find((c) => c.path === '/approvals/ap1')?.body).toEqual({ decision: 'approve' })
  })

  test('negar a ação envia a decisão', async ({ page }) => {
    const state = createState()
    state.approvals.push({ approval_id: 'ap2', chat_id: 'c1', summary: 'Pagar fornecedor', risk: 'medium', kind: 'pay', details: {}, expires_at: new Date(Date.now() + 300_000).toISOString() })
    state.chatSend = (route) =>
      route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: sse([
          ['chat', { chat_id: 'c1', run_id: 'r1' }],
          ['approval_required', { approval_id: 'ap2', summary: 'Pagar fornecedor', risk: 'medium', expires_at: new Date(Date.now() + 120_000).toISOString() }],
          ['done', { text: 'Aguardando você.' }],
        ]),
      })
    await installMock(page, state)
    await page.goto('/chat')
    await page.locator('textarea[name="chat-input"]').fill('Pague')
    await page.getByTestId('chat-send').click()
    await page.getByTestId('approval-deny').click()
    await expect(page.getByTestId('approval-result')).toContainText('Você negou esta ação.')
  })

  test('cartão vencido mostra que a ação não foi executada', async ({ page }) => {
    const state = createState()
    state.approvals.push({ approval_id: 'ap3', chat_id: 'c1', summary: 'Publicar página', risk: 'low', kind: 'publish', details: {}, expires_at: new Date(Date.now() + 1500).toISOString() })
    state.chatSend = (route) =>
      route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: sse([
          ['chat', { chat_id: 'c1', run_id: 'r1' }],
          ['approval_required', { approval_id: 'ap3', summary: 'Publicar página', risk: 'low', expires_at: new Date(Date.now() + 1500).toISOString() }],
          ['done', { text: 'ok' }],
        ]),
      })
    await installMock(page, state)
    await page.goto('/chat')
    await page.locator('textarea[name="chat-input"]').fill('Publique')
    await page.getByTestId('chat-send').click()
    await expect(page.getByTestId('approval-result')).toContainText('O prazo para confirmar acabou', { timeout: 10_000 })
  })

  test('botão Parar cancela a execução', async ({ page }) => {
    const state = createState()
    state.chatSend = async (route) => {
      await new Promise((r) => setTimeout(r, 4000))
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: sse([['chat', { chat_id: 'c1', run_id: 'r7' }], ['token', { text: 'parcial' }]]),
      }).catch(() => undefined)
    }
    await installMock(page, state)
    await page.goto('/chat')
    await page.locator('textarea[name="chat-input"]').fill('Faça algo longo')
    await page.getByTestId('chat-send').click()
    await expect(page.getByTestId('chat-stop')).toBeVisible()
    await page.getByTestId('chat-stop').click()
    await expect(page.getByTestId('chat-stopped-note')).toBeVisible()
    await expect(page.getByTestId('chat-stop')).toHaveCount(0)
  })

  test('409 mostra a mensagem em pt-BR e oferece nova conversa', async ({ page }) => {
    const state = createState()
    state.chatSend = (route) =>
      route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Este chat usa outro tipo de agente. Abra uma nova conversa para usar este agente.' }),
      })
    await installMock(page, state)
    await page.goto('/chat')
    await page.locator('textarea[name="chat-input"]').fill('Oi')
    await page.getByTestId('chat-send').click()
    await expect(page.getByText('Este chat usa outro tipo de agente. Abra uma nova conversa para usar este agente.')).toBeVisible()
    await expect(page.getByTestId('chat-conflict-new')).toBeVisible()
  })

  test('409 sem corpo cai na mensagem padrão em pt-BR', async ({ page }) => {
    const state = createState()
    state.chatSend = (route) => route.fulfill({ status: 409, contentType: 'application/json', body: '{}' })
    await installMock(page, state)
    await page.goto('/chat')
    await page.locator('textarea[name="chat-input"]').fill('Oi')
    await page.getByTestId('chat-send').click()
    await expect(page.getByText(/Abra uma nova conversa/)).toBeVisible()
  })
})
