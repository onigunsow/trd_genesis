import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
// 빌드 산출물을 ../static/ 에 직접 출력 → FastAPI StaticFiles(/static) 가 서빙
// base='/static/' 설정으로 빌드된 에셋 URL 이 /static/assets/... 형태가 됨
export default defineConfig({
  plugins: [react()],
  base: '/static/',
  build: {
    // FastAPI 가 서빙하는 dashboard/static/ 디렉터리에 직접 출력
    outDir: '../static',
    emptyOutDir: true,
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
  },
})
