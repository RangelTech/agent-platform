import { expect, test } from '@playwright/test'
import { createState, installMock } from './mockApi'

const TELAS = [
  { w: 390, h: 844 },
  { w: 768, h: 1024 },
  { w: 1440, h: 900 },
]
const ROTAS = ['/templates', '/templates/skills/t-sk', '/skills', '/ambiente', '/chat']

for (const { w, h } of TELAS) {
  test(`sem rolagem horizontal em ${w}x${h}`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h })
    await installMock(page, createState())
    for (const rota of ROTAS) {
      await page.goto(rota)
      await page.waitForTimeout(700)
      const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
      expect(over, `${rota} em ${w}px`).toBeLessThanOrEqual(1)
    }
  })
}

test('sem erros de console nas telas novas', async ({ page }) => {
  const erros: string[] = []
  page.on('pageerror', (e) => erros.push(e.message))
  page.on('console', (m) => {
    if (m.type() === 'error') erros.push(m.text())
  })
  await installMock(page, createState())
  for (const rota of ROTAS) {
    await page.goto(rota)
    await page.waitForTimeout(600)
  }
  expect(erros).toEqual([])
})

test('tema claro mantém as telas legíveis', async ({ page }) => {
  await installMock(page, createState())
  await page.goto('/templates')
  await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'light'))
  await expect(page.getByTestId('template-list')).toBeVisible()
})
