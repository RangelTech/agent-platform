import { expect, test } from '@playwright/test'

const MASTER_EMAIL = process.env.E2E_MASTER_EMAIL ?? 'master@example.com'
const MASTER_PASSWORD = process.env.E2E_MASTER_PASSWORD ?? 'admin123'

/**
 * produto-15 §9 -- link de download no Início (Dashboard), não na tela de
 * login. Cobre só a presença do card + que o zip realmente responde 200 no
 * bucket público do GCS (26/08/2026: trocou de asset estático servido pelo
 * backend pra `gs://rangel-tech-ratende-connector`, atualizado pelo CI do
 * repo `ratende-connector` a cada push) -- não testa a extensão em si (isso
 * exige Chrome real, fora do escopo automatizável aqui).
 */
const DOWNLOAD_URL = 'https://storage.googleapis.com/rangel-tech-ratende-connector/ratende-connector.zip'
test('card de download do RAtende Connector aparece no Início e o zip responde', async ({
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
      data: { name: `Connector DL E2E ${suffix}`, tenant_key: `connector-dl-e2e-${suffix}` },
      headers: authHeader,
    })
  ).json()
  const profiles = await (await request.get('/api/user-profiles', { headers: authHeader })).json()
  const adminProfile = profiles.find(
    (p: { tenant_id: string; name: string }) => p.tenant_id === tenant.id && p.name === 'Administrador',
  )
  const email = `connector-dl-${suffix}@e2e.com`
  await request.post('/api/users', {
    data: {
      email,
      name: 'Connector DL Admin',
      password: 'senha-forte-123',
      profile_id: adminProfile.id,
      tenant_id: tenant.id,
    },
    headers: authHeader,
  })

  await page.goto('/')
  await page.fill('input[name="email"]', email)
  await page.fill('input[name="password"]', 'senha-forte-123')
  await page.click('button[type="submit"]')

  await expect(page.getByRole('heading', { name: 'RAtende Connector' })).toBeVisible()
  const link = page.getByRole('link', { name: 'Baixar RAtende Connector' })
  await expect(link).toBeVisible()
  await expect(link).toHaveAttribute('href', DOWNLOAD_URL)

  const zipResp = await request.get(DOWNLOAD_URL)
  expect(zipResp.status()).toBe(200)
  const body = await zipResp.body()
  expect(body.length).toBeGreaterThan(1000)
  // Assinatura de arquivo ZIP ("PK\x03\x04") -- prova que é um zip de
  // verdade, não uma página de erro/HTML disfarçada de 200.
  expect(body.subarray(0, 2).toString('latin1')).toBe('PK')
})
