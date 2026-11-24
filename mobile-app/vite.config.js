import { defineConfig } from 'vite'
import uni from '@dcloudio/vite-plugin-uni'
// 后端 Werkzeug 对条件请求返回的 304 带 Transfer-Encoding: chunked（非法组合），
// Node 解析器拒绝该响应导致代理 500；剥离条件请求头让后端始终返回完整 200
const stripConditionalHeaders = (proxy) => {
  proxy.on('proxyReq', (proxyReq) => {
    proxyReq.removeHeader('if-modified-since')
    proxyReq.removeHeader('if-none-match')
  })
}

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    uni(),
  ],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      // 实时报警推送：后端 /ws/raw 由 WSGI 中间件处理，必须开 ws 升级，
      // 否则 H5 下 connectSocket 会打到 Vite dev server 直接断开
      '/ws/raw': {
        target: 'ws://localhost:5556',
        ws: true,
        changeOrigin: true
      },
      '/api': {
        target: 'http://localhost:5556',
        changeOrigin: true,
        configure: stripConditionalHeaders
      },
      '/output': {
        target: 'http://localhost:5556',
        changeOrigin: true,
        configure: stripConditionalHeaders
      },
      '/uploads': {
        target: 'http://localhost:5556',
        changeOrigin: true,
        configure: stripConditionalHeaders
      }
    }
  }
})
