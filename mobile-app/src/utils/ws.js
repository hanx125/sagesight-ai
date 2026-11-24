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
// 连接代次：每次重建连接自增。旧 socket 的 onClose/onError 是异步到达的，
// 必须用代次校验，否则它会把新连接误清空并额外启动一个重连定时器。
let socketGeneration = 0

const handlers = {} // event -> Set<fn>

function wsUrl() {
  let base = state.serverBase || ''
  // H5 留空时走当前站点源，由 dev 的 vite 代理或生产的 nginx 转发 /ws/raw
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

/** 拆掉当前连接并让它的所有回调失效（不设置 closedByUser） */
function teardownSocket() {
  socketGeneration += 1
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  if (socket) {
    try { socket.close({}) } catch (e) { /* 忽略 */ }
    socket = null
  }
}

export function connectWs() {
  if (!state.token) return
  const base = state.serverBase
  const url = wsUrl()
  // 同一地址且已有连接（含正在建立）时不重复创建
  if (socket && base === lastBase) return

  teardownSocket()
  const generation = socketGeneration
  lastBase = base
  closedByUser = false

  let ws = null
  try {
    ws = uni.connectSocket({ url, complete: () => {} })
  } catch (e) {
    scheduleReconnect()
    return
  }
  if (!ws) { scheduleReconnect(); return }

  socket = ws
  const isCurrent = () => generation === socketGeneration

  ws.onOpen(() => { if (isCurrent()) backoff = 2000 })
  ws.onMessage((msg) => {
    if (!isCurrent()) return
    try {
      const payload = JSON.parse(msg.data)
      if (payload && payload.event) dispatch(payload.event, payload.data)
    } catch (e) { /* 非 JSON 消息忽略 */ }
  })
  ws.onClose(() => {
    // 关键：旧连接的关闭回调不得影响后来新建的连接
    if (!isCurrent()) return
    socket = null
    if (!closedByUser) scheduleReconnect()
  })
  ws.onError(() => {
    // 部分平台只回调 error 不回调 close，这里主动收尾；
    // generation 已变说明已被新连接取代，直接忽略。
    if (!isCurrent()) return
    teardownSocket()
    if (!closedByUser) scheduleReconnect()
  })
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
  teardownSocket()
}
