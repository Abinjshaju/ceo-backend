import { defineConfig } from 'vite';

// In development the API runs separately (`uv run ceo-backend serve`); in the container
// FastAPI serves the built app and the API from one origin.
export default defineConfig({
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
  build: {
    rolldownOptions: {
      output: {
        // Libraries that change rarely get their own files, so they stay cached across app releases.
        // Only named packages: a catch-all vendor group would pull the lazily loaded charts into the first load.
        codeSplitting: {
          groups: [
            { name: 'react', test: /node_modules[\\/](react|react-dom|scheduler)[\\/]/ },
            { name: 'avatar', test: /node_modules[\\/]agent-robot-avatar[\\/]/ },
          ],
        },
      },
    },
  },
});
