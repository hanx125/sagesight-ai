/**
 * 原生 WebSocket 实时推送客户端
 *
 * 连接后端 /ws/raw（WSGI 层直连，无需认证），消息格式 {"event": "...", "data": {...}}
 * 断线自动重连（指数退避，60s 封顶），服务器地址变更后重置连接
 */
import { state } from './store'

let socket = null
let reconnectTimer = null
let backoff = 2000
let lastBase = null
let closedByUser = false

const handlers = {} // event -> Set<fn>

function wsUrl() {
  let base = state.serverBase || ''
  // H5 留空时使用当前站点源（vite 代理不含 ws/raw，需直连后端场景由 serverBase 指定）
  // #ifdef H5
  if (!base) base = `${location.protocol}//${location.host}`
  // #endif
  const ws = base.replace(/^http/i, 'ws')
  return ws + '/ws/raw'
}

export function onWsEvent(event, fn) {
  if (!handlers[event]) handlers[event] = new Set()
  handlers[event].add(fn)
  return () => handlers[event].delete(fn)
}

function dispatch(event, data) {
  const set = handlers[event]
  if (!set || !set.size) return
  set.forEach(fn => {
    try { fn(data) } catch (e) { /* 单个回调异常不影响其他 */ }
  })
}

export function connectWs() {
  if (!state.token) return
  const base = state.serverBase
  // 登录未变且连接存活时不重连
  if (socket && base === lastBase) return
  closeWs()

  lastBase = base
  closedByUser = false

  try {
    socket = uni.connectSocket({
      url: wsUrl(),
      complete: () => {}
    })
  } catch (e) {
    scheduleReconnect()
    return
  }

  if (!socket) { scheduleReconnect(); return }

  socket.onOpen(() => { backoff = 2000 })
  socket.onMessage((msg) => {
    try {
      const payload = JSON.parse(msg.data)
      if (payload && payload.event) dispatch(payload.event, payload.data)
    } catch (e) { /* 非 JSON 消息忽略 */ }
  })
  socket.onClose(() => {
    socket = null
    if (!closedByUser) scheduleReconnect()
  })
  socket.onError(() => { /* 由 onClose 触发重连 */ })
}

function scheduleReconnect() {
  if (closedByUser || reconnectTimer) return
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    connectWs()
    if (backoff < 60000) backoff = Math.min(backoff * 2, 60000)
  }, backoff)
}

export function closeWs() {
  closedByUser = true
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  if (socket) {
    try { socket.close({}) } catch (e) { /* 忽略 */ }
    socket = null
  }
}
