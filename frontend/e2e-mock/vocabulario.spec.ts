import { expect, test } from '@playwright/test'
import { createState, installMock } from './mockApi'

// Guarda de vocabulário: nunca "harness", "kernel" ou "hermes" para o cliente.
// A tela legada do produto Hermes (congelada, com permissão própria) fica fora.
const PROIBIDO = /harness|kernel|hermes/i

const ROTAS = [
  '/chat',
  '/templates',
  '/templates/estruturado/t-est',
  '/templates/skills/t-sk',
  '/skills',
  '/ambiente',
  '/memorias',
  '/perfis',
]

test('nenhuma tela nova mostra harness, kernel ou hermes', async ({ page }) => {
  const state = createState()
  await installMock(page, state)
  for (const rota of ROTAS) {
    await page.goto(rota)
    await page.waitForTimeout(700)
    const achados = await page.evaluate(() => {
      const out: string[] = []
      out.push(document.title, document.body.innerText)
      for (const el of Array.from(document.querySelectorAll('[title],[aria-label],[placeholder],[alt]'))) {
        for (const a of ['title', 'aria-label', 'placeholder', 'alt']) {
          const v = el.getAttribute(a)
          if (v) out.push(v)
        }
      }
      return out
    })
    for (const texto of achados) {
      expect(texto, `rota ${rota}`).not.toMatch(PROIBIDO)
    }
  }
})

test('menu com o perfil completo usa os nomes do cliente', async ({ page }) => {
  await installMock(page, createState())
  await page.goto('/templates')
  await page.getByRole('button', { name: 'Abrir navegação' }).click()
  const drawer = page.getByTestId('nav-drawer')
  for (const nome of ['Agentes de IA', 'Skills', 'Ambiente']) {
    await expect(drawer.getByRole('link', { name: nome, exact: true })).toBeVisible()
  }
  await expect(drawer).not.toContainText(PROIBIDO)
})
