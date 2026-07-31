import { expect, type Locator, type Page } from '@playwright/test'

/**
 * Navegação do shell premium: o topo mostra só os primeiros atalhos e o resto
 * mora no drawer. Os testes navegam sempre pelo drawer porque ele é o único
 * caminho que existe em todas as larguras — clicar direto no topo passaria em
 * desktop e quebraria em mobile.
 */

export async function openNav(page: Page): Promise<Locator> {
  const drawer = page.getByTestId('nav-drawer')
  if (!(await drawer.isVisible().catch(() => false))) {
    await page.getByRole('button', { name: 'Abrir navegação' }).click()
  }
  await expect(drawer).toBeVisible()
  return drawer
}

/** Link de navegação dentro do drawer (aberto sob demanda). */
export async function navLink(page: Page, label: string): Promise<Locator> {
  const drawer = await openNav(page)
  return drawer.getByRole('link', { name: label, exact: true })
}

/** Vai para uma seção pelo nome exibido no menu. */
export async function gotoSection(page: Page, label: string): Promise<void> {
  const link = await navLink(page, label)
  await link.click()
  await expect(page.getByTestId('nav-drawer')).toBeHidden()
}

/** Assere que a seção está (ou não está) disponível para o usuário atual. */
export async function expectSectionAvailable(page: Page, label: string, available = true) {
  const link = await navLink(page, label)
  await expect(link).toHaveCount(available ? 1 : 0)
  if (available) await expect(link).toBeVisible()
  // Fecha o drawer para não bloquear o próximo passo do teste.
  await page.getByTestId('nav-close').click()
}
