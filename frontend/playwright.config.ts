import { defineConfig } from '@playwright/test'

// E2E runs against the real backend serving the built SPA, with a real
// Postgres. Only the LLM is faked (from T03 onwards).
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:8090',
    trace: 'retain-on-failure',
  },
})
