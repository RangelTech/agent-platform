import { expect, test } from '@playwright/test'
import { gotoSection } from './helpers'

const MASTER_EMAIL = process.env.E2E_MASTER_EMAIL ?? 'master@example.com'
const MASTER_PASSWORD = process.env.E2E_MASTER_PASSWORD ?? 'admin123'

async function signIn(page: import('@playwright/test').Page, email: string, password: string) {
  await page.goto('/')
  await page.fill('input[name="email"]', email)
  await page.fill('input[name="password"]', password)
  await page.click('button[type="submit"]')
}

/**
 * Conecta uma conta pelo fluxo real da tela (infra-02 seção 5) e prova
 * isolamento entre empresas: a conta conectada por um tenant nunca aparece
 * pro outro, mesmo os dois estando no modo LiteLLM (Team+Team separados).
 *
 * Escopo deliberado: só cobre "conectar conta" (escrita local, sem chamada
 * ao LiteLLM — infra-04 seção 2d) e a lista de contas. "Montar combo" chama
 * o LiteLLM de verdade (cria deployment) e já foi validado com rigor maior
 * em produção real (memoria.md, isolamento com virtual key + 403 explícito
 * numa tentativa cross-tenant) — duplicar isso aqui pediria subir um
 * LiteLLM de mentira dentro do Playwright, custo alto pra repetir uma prova
 * que já existe contra o sistema real.
 */
test('conectar conta aparece pro tenant certo, nunca pro outro', async ({ page, request }) => {
  const suffix = Date.now().toString(36)
  const masterToken = (
    await (
      await request.post('/api/auth/login', {
        data: { email: MASTER_EMAIL, password: MASTER_PASSWORD },
      })
    ).json()
  ).token
  const authHeader = { Authorization: `Bearer ${masterToken}` }

  async function criarTenantLiteLLM(nome: string, slug: string) {
    const tenant = await (
      await request.post('/api/tenants', {
        data: { name: nome, tenant_key: `${slug}-${suffix}` },
        headers: authHeader,
      })
    ).json()
    const profiles = await (
      await request.get('/api/user-profiles', { headers: authHeader })
    ).json()
    const adminProfile = profiles.find(
      (p: { tenant_id: string; name: string }) =>
        p.tenant_id === tenant.id && p.name === 'Administrador',
    )
    const email = `${slug}-${suffix}@e2e.com`
    await request.post('/api/users', {
      data: {
        email,
        name: `${nome} Admin`,
        password: 'senha-forte-123',
        profile_id: adminProfile.id,
        tenant_id: tenant.id,
      },
      headers: authHeader,
    })
    await request.put('/api/ai-router/instancias-litellm', {
      data: {
        tenant_id: tenant.id,
        litellm_team_id: `team-e2e-${tenant.id}`,
        bridge_key: `sk-e2e-bridge-${tenant.id}`,
        ai_assist_key: `sk-e2e-ai-assist-${tenant.id}`,
      },
      headers: authHeader,
    })
    return { tenant, email }
  }

  const a = await criarTenantLiteLLM(`Isolamento A ${suffix}`, 'isolamento-a')
  const b = await criarTenantLiteLLM(`Isolamento B ${suffix}`, 'isolamento-b')

  // Tenant A conecta uma conta pelo fluxo real da tela.
  await signIn(page, a.email, 'senha-forte-123')
  await gotoSection(page, 'Serviços de IA')
  await page.getByTestId('provedor-gemini').click()
  await expect(page.getByTestId('modal-conexao')).toBeVisible()
  await page.getByLabel('Apelido').fill('Gemini E2E')
  await page.getByLabel('Chave de API').fill('chave-de-teste-e2e-nao-real')
  await page.getByTestId('modal-conexao').getByRole('button', { name: 'Conectar' }).click()
  await expect(page.getByTestId('modal-conexao')).toBeHidden()
  await expect(page.getByTestId('lista-contas')).toContainText('Gemini E2E')

  // Tenant B nunca vê a conta do tenant A.
  await page.getByRole('button', { name: 'Sair' }).click()
  await signIn(page, b.email, 'senha-forte-123')
  await gotoSection(page, 'Serviços de IA')
  await expect(page.getByText('Nenhuma conta conectada')).toBeVisible()
  await expect(page.getByText('Gemini E2E')).toHaveCount(0)
})
