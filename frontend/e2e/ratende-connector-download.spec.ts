import { expect, test } from '@playwright/test'

const MASTER_EMAIL = process.env.E2E_MASTER_EMAIL ?? 'master@example.com'
const MASTER_PASSWORD = process.env.E2E_MASTER_PASSWORD ?? 'admin123'

/**
 * produto-15 §9 -- link de download no Início (Dashboard), não na tela de
 * login. Cobre só a presença do card + que o instalador do Windows
 * realmente responde 200 no bucket público do GCS (26/08/2026: card
 * principal virou o instalador via política do Chrome, `.bat`/`.deb`,
 * zip do "carregar sem compactação" virou alternativa manual dentro de
 * um `<details>`) -- não testa a extensão em si nem o instalador rodando
 * de verdade (isso exige Chrome real + Windows/Linux reais, fora do
 * escopo automatizável aqui).
 */
const INSTALLER_URL = 'https://storage.googleapis.com/rangel-tech-ratende-connector/Instalar-RAtende-Connector.bat'
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
  const link = page.getByRole('link', { name: 'Instalar no Windows' })
  await expect(link).toBeVisible()
  await expect(link).toHaveAttribute('href', INSTALLER_URL)
  await expect(page.getByRole('link', { name: 'Instalar no Linux (.deb)' })).toBeVisible()

  // Alternativa manual (zip) continua acessível dentro do <details>.
  await page.getByText('Sem permissão de administrador? Carregue manualmente').click()
  await expect(page.getByRole('link', { name: 'o zip da extensão' })).toBeVisible()

  const installerResp = await request.get(INSTALLER_URL)
  expect(installerResp.status()).toBe(200)
  const body = await installerResp.body()
  expect(body.length).toBeGreaterThan(200)
  // Confirma que é o instalador de verdade, não uma página de erro
  // disfarçada de 200.
  expect(body.toString('latin1')).toContain('RAtende Connector')
})
