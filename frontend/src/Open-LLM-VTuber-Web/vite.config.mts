import { defineConfig, normalizePath } from 'vite';
import path from 'path';
import { fileURLToPath } from 'url';
import react from '@vitejs/plugin-react-swc';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const createConfig = async (outDir: string) => ({
  plugins: [
    (await import('vite-plugin-static-copy')).viteStaticCopy({
      targets: [
        {
          src: normalizePath(path.resolve(__dirname, 'node_modules/@ricky0123/vad-web/dist/vad.worklet.bundle.min.js')),
          dest: './libs/',
        },
        {
          src: normalizePath(path.resolve(__dirname, 'node_modules/@ricky0123/vad-web/dist/silero_vad_v5.onnx')),
          dest: './libs/',
        },
        {
          src: normalizePath(path.resolve(__dirname, 'node_modules/@ricky0123/vad-web/dist/silero_vad_legacy.onnx')),
          dest: './libs/',
        },
        {
          src: normalizePath(path.resolve(__dirname, 'node_modules/onnxruntime-web/dist/*.wasm')),
          dest: './libs/',
        },
      ],
    }),
    react(),
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src/renderer/src"),
      "@motionsyncframework": path.resolve(
        __dirname,
        "./src/renderer/MotionSync/Framework/src",
      ),
      "@motionsync": path.resolve(__dirname, "./src/renderer/MotionSync/src"),
      "/src": path.resolve(__dirname, "./src/renderer/src"),
    },
  },
  root: path.join(__dirname, "src/renderer"),
  publicDir: path.join(__dirname, "src/renderer/public"),
  base: "./",
  server: {
    port: 3000,
  },
  build: {
    outDir: path.join(__dirname, outDir),
    emptyOutDir: true,
    assetsDir: "assets",
    // ONNX Runtime ships as one pre-minified module (~545 kB). First-party and
    // other vendor code is split below, so keep the warning threshold narrowly
    // above that indivisible dependency instead of masking larger regressions.
    chunkSizeWarningLimit: 560,
    rollupOptions: {
      input: {
        main: path.join(__dirname, "src/renderer/index.html"),
      },
      output: {
        manualChunks(id: string) {
          const moduleId = normalizePath(id);
          if (!moduleId.includes('/node_modules/')) return undefined;
          if (moduleId.includes('/onnxruntime-web/')) return 'onnx-runtime';
          if (moduleId.includes('/@ricky0123/vad-web/')) return 'voice-activity';
          if (
            moduleId.includes('/@chakra-ui/') ||
            moduleId.includes('/@emotion/') ||
            moduleId.includes('/framer-motion/') ||
            moduleId.includes('/react-icons/') ||
            moduleId.includes('/next-themes/')
          ) {
            return 'ui';
          }
          if (moduleId.includes('/@chatscope/')) return 'chat';
          if (
            moduleId.includes('/react/') ||
            moduleId.includes('/react-dom/') ||
            moduleId.includes('/scheduler/')
          ) {
            return 'react';
          }
          if (
            moduleId.includes('/i18next/') ||
            moduleId.includes('/react-i18next/')
          ) {
            return 'i18n';
          }
          return 'vendor';
        },
      },
    },
  },
  ssr: {
    noExternal: ['vite-plugin-static-copy'],
  },
});

export default defineConfig(async ({ mode }) => {
  if (mode === 'web') {
    return createConfig('dist/web');
  }
  return createConfig('dist/renderer');
});
