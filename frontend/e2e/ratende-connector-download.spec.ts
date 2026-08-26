import { expect, test } from '@playwright/test'

const MASTER_EMAIL = process.env.E2E_MASTER_EMAIL ?? 'master@example.com'
const MASTER_PASSWORD = process.env.E2E_MASTER_PASSWORD ?? 'admin123'

/**
 * produto-15 §9 -- teaser no Início (Dashboard) linkando pra página
 * dedicada /conector (26/08/2026: instaladores por SO + explicação
 * completa moraram pra lá). Cobre a presença do teaser + da página
 * dedicada + que o instalador do Windows realmente responde 200 no
 * bucket público do GCS -- não testa a extensão em si nem o instalador
 * rodando de verdade (isso exige Chrome real + Windows/Linux/macOS
 * reais, fora do escopo automatizável aqui).
 */
const BUCKET = 'https://storage.googleapis.com/rangel-tech-ratende-connector'
const INSTALLER_URL = `${BUCKET}/RAtende-Connector-Instalador.msi`
test('teaser do RAtende Connector no Início leva pra página dedicada, e o instalador responde', async ({
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
  await page.getByRole('link', { name: 'Ver instaladores e detalhes' }).click()

  await expect(page).toHaveURL(/\/conector$/)
  const link = page.getByRole('link', { name: 'Instalador (.msi)' })
  await expect(link).toBeVisible()
  await expect(link).toHaveAttribute('href', INSTALLER_URL)
  await expect(page.getByRole('link', { name: 'Pacote (.deb)' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Instalador universal (.sh)' })).toBeVisible()
  await expect(page.locator(`a[href="${BUCKET}/instalar-ratende-connector-mac.sh"]`)).toBeVisible()
  await expect(page.getByRole('link', { name: 'Pacote descompactado (.zip)' })).toBeVisible()

  const installerResp = await request.get(INSTALLER_URL)
  expect(installerResp.status()).toBe(200)
  const body = await installerResp.body()
  expect(body.length).toBeGreaterThan(10_000)
  // Assinatura OLE/Compound File Binary (D0 CF 11 E0) -- prova que é o
  // .msi compilado de verdade, não uma página de erro disfarçada de 200.
  expect(body.subarray(0, 4).toString('hex')).toBe('d0cf11e0')
})
