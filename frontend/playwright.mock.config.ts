import { defineConfig } from '@playwright/test'

// Testes de interface contra a API simulada (specs/anexos/04/api-contract.md).
// Não precisam de backend, banco nem modelo: o próprio vite serve o app.
export default defineConfig({
  testDir: './e2e-mock',
  timeout: 45_000,
  expect: { timeout: 7_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://localhost:5199',
    trace: 'retain-on-failure',
    viewport: { width: 1440, height: 900 },
  },
  webServer: {
    command: 'npx vite --port 5199 --strictPort',
    url: 'http://localhost:5199',
    reuseExistingServer: true,
    timeout: 60_000,
  },
})
