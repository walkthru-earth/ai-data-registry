import { defineConfig } from 'vite'

// Relative base so the built app works under any GitHub Pages subpath
// (e.g. https://<owner>.github.io/<repo>/) without per-repo configuration.
export default defineConfig({
  base: './',
})
