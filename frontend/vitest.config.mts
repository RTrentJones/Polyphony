import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vitest/config'

// Component tests run in jsdom. esbuild's automatic JSX runtime stands in for
// Next's compiler, so no React plugin is needed.
export default defineConfig({
  esbuild: { jsx: 'automatic' },
  resolve: {
    alias: { '@': fileURLToPath(new URL('./', import.meta.url)) },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./vitest.setup.ts'],
    include: ['**/*.test.{ts,tsx}'],
    exclude: ['node_modules/**', '.next/**'],
  },
})
