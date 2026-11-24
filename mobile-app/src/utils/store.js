/**
 * 轻量状态存储：基于 uni storage + 响应式全局对象
 */
import { reactive } from 'vue'

export const TOKEN_KEY = 'ss_token'
export const USER_KEY = 'ss_user'
export const BASE_KEY = 'ss_server_base'
export const PENDING_EVENT_KEY = 'ss_pending_event'

// 页面展示版本号：与 manifest.json 的 versionName 保持一致，升级时两处都要改
export const APP_VERSION = '1.0.0'

// 响应式全局状态：个人资料等 computed 直接依赖 state.user，必须是 reactive
export const state = reactive({
  token: uni.getStorageSync(TOKEN_KEY) || '',
  user: null,
  serverBase: uni.getStorageSync(BASE_KEY) || ''
})

try {
  const u = uni.getStorageSync(USER_KEY)
  if (u) state.user = typeof u === 'string' ? JSON.parse(u) : u
} catch (e) { state.user = null }

export function isLoggedIn() {
  return !!state.token
}

export function setAuth(token, user) {
  state.token = token || ''
  state.user = user || null
  uni.setStorageSync(TOKEN_KEY, state.token)
  uni.setStorageSync(USER_KEY, JSON.stringify(state.user))
}

export function clearAuth() {
  state.token = ''
  state.user = null
  uni.removeStorageSync(TOKEN_KEY)
  uni.removeStorageSync(USER_KEY)
}

export function getServerBase() {
  // H5 调试时留空走同源（vite 代理）；App 端必须为 http(s)://host:port
  return state.serverBase || ''
}

export function setServerBase(base) {
  state.serverBase = (base || '').trim().replace(/\/+$/, '')
  uni.setStorageSync(BASE_KEY, state.serverBase)
}

/** 推送点击时若尚未登录，先记下要打开的事件，登录后再跳 */
export function setPendingEvent(id) {
  if (id) uni.setStorageSync(PENDING_EVENT_KEY, id)
}

export function takePendingEvent() {
  const id = uni.getStorageSync(PENDING_EVENT_KEY) || ''
  if (id) uni.removeStorageSync(PENDING_EVENT_KEY)
  return id
}

export function clearPendingEvent() {
  uni.removeStorageSync(PENDING_EVENT_KEY)
}

/** 把后端相对路径(/output/..、/api/..)转成可访问地址 */
export function resolveUrl(u) {
  if (!u) return ''
  // 统一路径分隔符为正斜杠（后端在 Windows 上可能拼接出反斜杠路径，浏览器会把 \x 当非法序列导致 404）
  u = String(u).replace(/\\/g, '/')
  if (/^https?:\/\//i.test(u)) {
    // #ifdef H5
    // 后端自有资源剥离 host 走同源代理，避免直连后端端口被浏览器协议升级拦截（ERR_SSL_PROTOCOL_ERROR）
    const path = u.replace(/^https?:\/\/[^/]+/i, '')
    if (/^\/(output|api|static)\//.test(path)) return path
    // #endif
    return u
  }
  return getServerBase() + u
}
