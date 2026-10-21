import { expect, test } from '@playwright/test'
import { createState, installMock, sse } from './mockApi'

test.describe('lista de agentes', () => {
  test('lista só agentes por skills (estruturado descontinuado), busca e cria', async ({ page }) => {
    const state = createState()
    await installMock(page, state)
    await page.goto('/templates')

    const rows = page.getByTestId('template-row')
    await expect(rows).toHaveCount(2)
    await expect(rows.filter({ hasText: 'Vendas' })).toHaveCount(0)
    await expect(page.getByTestId('kind-filter')).toHaveCount(0)
    await page.getByTestId('template-search').fill('cobranca')
    await expect(rows).toHaveCount(1)
    await page.getByTestId('template-search').fill('')

    // Modal: nome obrigatório e criação do tipo por skills.
    await page.getByTestId('new-agent-btn').click()
    const modal = page.getByTestId('new-agent-modal')
    await expect(modal).toBeVisible()
    await expect(modal.getByTestId('kind-option-structured')).toHaveCount(0)
    await modal.getByTestId('new-agent-submit').click()
    await expect(modal).toContainText('Informe um nome para o agente.')
    await modal.locator('input[name="tpl-name"]').fill('Suporte novo')
    await modal.getByTestId('new-agent-submit').click()

    await expect(page).toHaveURL(/\/templates\/skills\/t-new-/)
    const created = state.calls.find((c) => c.method === 'POST' && c.path === '/templates')
    expect((created?.body as { agent_kind: string } | undefined)?.agent_kind).toBe('skills')
    await expect(page.getByTestId('skills-editor')).toBeVisible()
  })

  test('Esc fecha o modal', async ({ page }) => {
    await installMock(page, createState())
    await page.goto('/templates')
    await page.getByTestId('new-agent-btn').click()
    await expect(page.getByTestId('new-agent-modal')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('new-agent-modal')).toBeHidden()
  })

  test('perfil sem edição não vê botões de criar e a URL direta é barrada sem permissão', async ({ page }) => {
    await installMock(page, createState({ permissions: { chats: ['view', 'create'], templates: ['view'] } }))
    await page.goto('/templates')
    await expect(page.getByTestId('template-row').first()).toBeVisible()
    await expect(page.getByTestId('new-agent-btn')).toHaveCount(0)
    await page.goto('/skills')
    await expect(page.getByTestId('forbidden')).toBeVisible()
    await page.goto('/ambiente')
    await expect(page.getByTestId('forbidden')).toBeVisible()
  })

  test('empresa sem o recurso de agentes por skills não vê o tipo nem a página', async ({ page }) => {
    await installMock(page, createState({ skillsEnabled: false }))
    await page.goto('/skills')
    await expect(page.getByTestId('forbidden')).toContainText('Recurso não habilitado para sua empresa.')
  })
})

test.describe('editor de skills', () => {
  test('edita, valida, bloqueia publicação com erro, salva e publica', async ({ page }) => {
    const state = createState()
    await installMock(page, state)
    await page.goto('/templates/skills/t-sk')

    await expect(page.getByTestId('skills-editor')).toBeVisible()
    await expect(page.getByTestId('skill-file-item').filter({ hasText: 'AGENTE.md' })).toBeVisible()
    await expect(page.getByTestId('skill-editor')).toBeVisible()
    await page.getByTestId('skill-file-item').filter({ hasText: 'SKILL.md' }).click()
    await expect(page.getByTestId('frontmatter-form')).toBeVisible()

    // Frontmatter por formulário reescreve o arquivo e marca como não salvo.
    await expect(page.getByTestId('saved-indicator')).toBeVisible()
    await page.getByTestId('fm-description').fill('Nova descrição do agente')
    await expect(page.getByTestId('unsaved-indicator')).toBeVisible()
    await expect(page.getByTestId('skill-preview')).toBeVisible()

    // Validar com problema bloqueia a publicação.
    state.validateProblems = [{ path: 'AGENTE.md', line: 2, code: 'bad_name', message: 'Nome fora do padrão', severity: 'error' }]
    await page.getByTestId('skill-validate').click()
    await page.getByTestId('panel-tab-problems').click()
    await expect(page.getByTestId('problem-item')).toHaveCount(1)
    await expect(page.getByTestId('skill-publish')).toBeDisabled()

    // Corrigido, libera.
    state.validateProblems = []
    await page.getByTestId('skill-validate').click()
    await expect(page.getByTestId('validation-ok')).toBeVisible()
    await expect(page.getByTestId('skill-publish')).toBeEnabled()

    await page.getByTestId('skill-save-draft').click()
    await expect(page.getByTestId('saved-indicator')).toBeVisible()
    const put = state.calls.filter((c) => c.method === 'PUT' && c.path === '/templates/t-sk/draft').at(-1)
    expect(JSON.stringify(put?.body)).toContain('Nova descrição do agente')

    await page.getByTestId('version-notes').fill('Ajuste de descrição')
    await page.getByTestId('skill-publish').click()
    await expect.poll(() => state.calls.some((c) => c.method === 'POST' && c.path === '/templates/t-sk/versions')).toBe(true)
    const pub = state.calls.find((c) => c.method === 'POST' && c.path === '/templates/t-sk/versions')
    expect(pub?.body).toMatchObject({ source: 'draft', notes: 'Ajuste de descrição' })
    await expect.poll(() => state.calls.some((c) => c.path === '/templates/t-sk/deploy')).toBe(true)
  })

  test('cria arquivo, versões e testar mensagem', async ({ page }) => {
    const state = createState()
    await installMock(page, state)
    state.chatSend = (route) =>
      route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: sse([
          ['chat', { chat_id: 'c9', run_id: 'r9' }],
          ['step', { kind: 'skill', label: 'triagem', status: 'start' }],
          ['token', { text: 'Resposta de teste' }],
          ['done', { text: 'Resposta de teste' }],
        ]),
      })
    await page.goto('/templates/skills/t-sk')
    await expect(page.getByTestId('skill-file-tree')).toBeVisible()

    await page.getByTestId('tree-new-file').click()
    await page.getByTestId('path-input').fill('references/precos.md')
    await page.getByTestId('path-confirm').click()
    await expect(page.getByTestId('skill-file-item').filter({ hasText: 'precos.md' })).toBeVisible()

    // Versões: a lista mostra a de produção.
    await expect(page.getByTestId('version-row')).toHaveCount(2)

    await page.getByTestId('panel-tab-test').click()
    await page.getByTestId('test-message-input').fill('Oi')
    await page.getByTestId('test-message-send').click()
    await expect(page.getByTestId('test-output')).toContainText('Resposta de teste')
    await expect(page.getByTestId('test-steps')).toContainText('Consultando: triagem')
  })

  test('agente sem versão em produção não permite testar', async ({ page }) => {
    await installMock(page, createState())
    await page.goto('/templates/skills/t-draft')
    await page.getByTestId('panel-tab-test').click()
    await expect(page.getByTestId('test-disabled-note')).toBeVisible()
    await expect(page.getByTestId('test-message-send')).toBeDisabled()
  })
})

test.describe('página Skills', () => {
  test('aba da empresa lista e aba aprendidas aprova, edita e descarta', async ({ page }) => {
    const state = createState()
    await installMock(page, state)
    await page.goto('/skills')

    await expect(page.getByTestId('company-skill-row')).toHaveCount(1)
    await page.getByRole('tab', { name: /Aprendidas por você/ }).click()
    await expect(page.getByTestId('skills-learned')).toBeVisible()
    await expect(page.getByTestId('learned-skill')).toHaveCount(2)

    const pending = page.getByTestId('learned-skill').filter({ hasText: 'Pendente' })
    await pending.getByTestId('learned-approve').click()
    await expect(page.getByTestId('learned-skill').filter({ hasText: 'Pendente' })).toHaveCount(0)
    await expect.poll(() => state.calls.some((c) => c.path === '/me/skills/us1/approve')).toBe(true)

    await page.getByTestId('learned-skill').filter({ hasText: 'resumo' }).getByTestId('learned-disable').click()
    await expect.poll(() => state.calls.some((c) => c.path === '/me/skills/us2/disable')).toBe(true)
  })

  test('descartar remove da lista', async ({ page }) => {
    const state = createState()
    await installMock(page, state)
    await page.goto('/skills')
    await page.getByRole('tab', { name: /Aprendidas por você/ }).click()
    await page.getByTestId('learned-skill').filter({ hasText: 'Pendente' }).getByTestId('learned-discard').click()
    await expect(page.getByTestId('learned-skill')).toHaveCount(1)
    expect(state.calls.some((c) => c.path === '/me/skills/us1/discard')).toBe(true)
  })
})

test.describe('ambiente', () => {
  test('mostra estado, uso, apps e adiciona domínio', async ({ page }) => {
    const state = createState()
    await installMock(page, state)
    await page.goto('/ambiente')
    await expect(page.getByTestId('env-state')).toContainText('Em execução')
    await expect(page.getByTestId('env-app-row')).toHaveCount(1)
    await page.getByTestId('env-domain-host').fill('app.minhaloja.com.br')
    await page.getByTestId('env-domain-app').selectOption('a1')
    await page.getByTestId('env-domain-add').click()
    await expect(page.getByTestId('env-domain-row')).toHaveCount(1)
    await expect(page.getByTestId('env-txt-value')).toContainText('abc123')
    await page.getByTestId('env-stop').click()
    await page.getByTestId('env-stop-dialog-confirm').click()
    await expect.poll(() => state.calls.some((c) => c.path === '/environment/stop')).toBe(true)
  })

  test('sem contratação mostra aviso', async ({ page }) => {
    const state = createState()
    state.environment = { ...state.environment, enabled: false, state: 'disabled' }
    await installMock(page, state)
    await page.goto('/ambiente')
    await expect(page.getByTestId('env-disabled')).toBeVisible()
  })
})
