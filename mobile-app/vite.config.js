import { defineConfig } from 'vite'
import uni from '@dcloudio/vite-plugin-uni'
// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    uni(),
  ],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:5556',
        changeOrigin: true,
        // 后端 Werkzeug 对条件请求返回的 304 带 Transfer-Encoding: chunked（非法组合），
        // Node 解析器拒绝该响应导致代理 500；剥离条件请求头让后端始终返回完整 200
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq) => {
            proxyReq.removeHeader('if-modified-since')
            proxyReq.removeHeader('if-none-match')
          })
        }
      },
      '/output': {
        target: 'http://localhost:5556',
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq) => {
            proxyReq.removeHeader('if-modified-since')
            proxyReq.removeHeader('if-none-match')
          })
        }
      }
    }
  }
})
