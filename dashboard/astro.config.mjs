// @ts-check
import { defineConfig } from 'astro/config';
import node from '@astrojs/node';

// https://astro.build/config
export default defineConfig({
  output: 'server',

  adapter: node({
    mode: 'standalone', // produces dist/server/entry.mjs — runs without Astro CLI
  }),

  server: {
    port: 4321,
    host: true, // bind 0.0.0.0 so it's reachable inside WSL
  },

  // Vite dev-server proxy: forward /api/* and /auth/* to the Falcon backend.
  // Means in dev you open only port 4321 — cookies work with no CORS complexity.
  vite: {
    server: {
      proxy: {
        '/api': {
          target: 'http://localhost:8000',
          changeOrigin: true,
        },
        '/auth': {
          target: 'http://localhost:8000',
          changeOrigin: true,
        },
      },
    },
  },
});
