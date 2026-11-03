import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Keep Host and Origin aligned for the backend's local camera guard.
      // Do not rewrite Origin: requests from other sites must still be rejected.
      '/api': { target: `http://127.0.0.1:${process.env.ANJU_BACKEND_PORT || '8000'}`, changeOrigin: false },
      '/media': { target: `http://127.0.0.1:${process.env.ANJU_BACKEND_PORT || '8000'}`, changeOrigin: false },
    },
  },
});
