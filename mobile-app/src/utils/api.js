/**
 * 统一请求封装：baseUrl + Bearer Token + 401 跳登录
 */
import { state, clearAuth, getServerBase } from './store'

let redirecting = false

function handleUnauthorized() {
  clearAuth()
  if (redirecting) return
  redirecting = true
  uni.showToast({ title: '登录已过期', icon: 'none' })
  setTimeout(() => {
    redirecting = false
    uni.reLaunch({ url: '/pages/login/login' })
  }, 600)
}

export function request({ url, method = 'GET', data = null, header = {}, timeout = 20000 }) {
  return new Promise((resolve, reject) => {
    const headerFinal = { ...header }
    if (state.token) headerFinal['Authorization'] = 'Bearer ' + state.token

    uni.request({
      url: getServerBase() + url,
      method,
      data: data || undefined,
      timeout,
      header: headerFinal,
      success: (res) => {
        const code = res.statusCode || 0
        if (code === 401) {
          // 登录接口自身的 401 表示账号/密码错误，不是登录过期：
          // 保留本地登录态、不跳转，把后端错误文案原样交给调用方展示。
          const isLoginRequest = /\/api\/auth\/(app_login|login)$/.test(url)
          const msg = (res.data && (res.data.error || res.data.message)) || '未登录或登录已过期'
          if (!isLoginRequest) handleUnauthorized()
          return reject(new Error(msg))
        }
        if (code >= 200 && code < 300) {
          resolve(res.data)
        } else {
          const msg = (res.data && (res.data.error || res.data.message)) || `请求失败(${code})`
          reject(new Error(msg))
        }
      },
      fail: (err) => {
        reject(new Error(err.errMsg && err.errMsg.includes('timeout')
          ? '请求超时，请检查网络'
          : '无法连接服务器，请检查服务器地址'))
      }
    })
  })
}

export const get = (url, data) => request({ url, method: 'GET', data })
export const post = (url, data) => request({ url, method: 'POST', data })
export const put = (url, data) => request({ url, method: 'PUT', data })
export const del = (url, data) => request({ url, method: 'DELETE', data })

/**
 * 摄像机全量列表（返回数组）
 * 后端 /api/cameras 默认 per_page=20，不传参时超过 20 台会被静默截断，
 * 手持端的监控网格、首页在线数、任务通道选择都需要一次取全。
 */
export function getAllCameras() {
  return get('/api/cameras?page=1&per_page=500').then(d =>
    (d && d.cameras) || (Array.isArray(d) ? d : [])
  )
}

/** 上传二进制数据（application/octet-stream），headers 可带 X-Session-ID 等 */
export function postBinary(url, arrayBuffer, headers = {}, timeout = 10000) {
  return new Promise((resolve, reject) => {
    const headerFinal = { 'Content-Type': 'application/octet-stream', ...headers }
    if (state.token) headerFinal['Authorization'] = 'Bearer ' + state.token
    uni.request({
      url: getServerBase() + url,
      method: 'POST',
      data: arrayBuffer,
      timeout,
      header: headerFinal,
      success: (res) => {
        const code = res.statusCode || 0
        if (code >= 200 && code < 300) return resolve(res.data)
        const msg = (res.data && (res.data.error || res.data.message)) || `上传失败(${code})`
        reject(new Error(msg))
      },
      fail: () => reject(new Error('无法连接服务器'))
    })
  })
}
