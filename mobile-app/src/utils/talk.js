/**
 * ONVIF 语音对讲（喊话）客户端
 *
 * 链路：本端采集 PCM(16bit/8kHz/单声道) 分帧 POST /api/audio/talk/send
 *      → 后端经 RTSP Audio Back Channel 推给摄像机扬声器
 *
 * 采集实现按端分派：
 * - App：uni.getRecorderManager（format=pcm，8kHz）
 * - H5：getUserMedia + AudioContext 手动降采样到 8kHz
 */
import { get, post, postBinary } from './api'

const CHUNK_MS = 200 // 每包约 200ms 音频

let session = null      // { session_id, camera_id }
let active = false

/* ---------------- 会话管理 ---------------- */

export function isTalkActive() {
  return active
}

export async function checkTalkCapability(cameraId) {
  const d = await get(`/api/audio/talk/capabilities?camera_id=${cameraId}`)
  return !!(d && d.capabilities && d.capabilities.supported)
}

export async function startTalk(cameraId) {
  if (active) return
  const d = await post('/api/audio/talk/start', { camera_id: cameraId, audio_codec: 'PCMU', sample_rate: 8000 })
  if (!d || !d.success || !d.session_id) throw new Error(d && d.error ? d.error : '对讲会话建立失败')
  session = { session_id: d.session_id, camera_id: cameraId }
  active = true
  return d
}

export async function stopTalk() {
  const sid = session && session.session_id
  stopCapture()
  active = false
  session = null
  if (sid) {
    try { await post('/api/audio/talk/stop', { session_id: sid }) } catch (e) { /* 忽略 */ }
  }
}

/** 发送一帧 PCM16 数据 */
function pushPcm(buffer) {
  if (!active || !session) return
  postBinary('/api/audio/talk/send', buffer, { 'X-Session-ID': session.session_id })
    .catch(() => { /* 单包失败忽略，由用户停止结束会话 */ })
}

/* ---------------- App 端：RecorderManager ---------------- */

let recorder = null

function startCaptureApp() {
  recorder = uni.getRecorderManager()
  recorder.onStart(() => { /* 录音开始 */ })
  recorder.onFrameRecorded((res) => {
    if (res && res.frameBuffer) pushPcm(res.frameBuffer)
  })
  recorder.onError(() => {
    stopTalk()
    uni.showToast({ title: '录音失败，请检查麦克风权限', icon: 'none' })
  })
  recorder.start({
    duration: 600000,          // 上限 10 分钟，由松手主动停止
    sampleRate: 8000,
    numberOfChannels: 1,
    format: 'pcm',
    frameSize: 1               // 约 1KB 触发一次分帧回调
  })
}

function stopCaptureApp() {
  if (recorder) {
    try { recorder.stop() } catch (e) { /* 忽略 */ }
    recorder = null
  }
}

/* ---------------- H5 端：getUserMedia 降采样 ---------------- */

let audioCtx = null
let mediaStream = null
let processor = null
let source = null
let resampleRatio = 1
let pending = []        // 待发送的采样值（Int16）
let pendingCount = 0

function startCaptureH5() {
  const Ctx = window.AudioContext || window.webkitAudioContext
  if (!Ctx) throw new Error('当前浏览器不支持麦克风采集')
  navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } })
    .then((stream) => {
      mediaStream = stream
      audioCtx = new Ctx()
      source = audioCtx.createMediaStreamSource(stream)
      // 目标 8kHz：ScriptProcessor 回调中手动降采样
      processor = audioCtx.createScriptProcessor(2048, 1, 1)
      resampleRatio = audioCtx.sampleRate / 8000
      pending = []
      pendingCount = Math.round(8000 * (CHUNK_MS / 1000)) // 1600 采样 ≈ 200ms

      processor.onaudioprocess = (ev) => {
        if (!active || !session) return
        const input = ev.inputBuffer.getChannelData(0)
        const outLen = Math.floor(input.length / resampleRatio)
        for (let i = 0; i < outLen; i++) {
          const pos = Math.floor(i * resampleRatio)
          const s = Math.max(-1, Math.min(1, input[pos] || 0))
          pending.push(s < 0 ? s * 0x8000 : s * 0x7fff)
        }
        while (pending.length >= pendingCount) {
          const chunk = pending.splice(0, pendingCount)
          const buf = new Int16Array(chunk)
          pushPcm(buf.buffer)
        }
      }
      source.connect(processor)
      processor.connect(audioCtx.destination)
    })
    .catch(() => {
      stopTalk()
      uni.showToast({ title: '无法访问麦克风，请检查浏览器权限（需 https）', icon: 'none' })
    })
}

function stopCaptureH5() {
  try { if (processor) processor.disconnect() } catch (e) { /* 忽略 */ }
  try { if (source) source.disconnect() } catch (e) { /* 忽略 */ }
  try { if (mediaStream) mediaStream.getTracks().forEach(t => t.stop()) } catch (e) { /* 忽略 */ }
  try { if (audioCtx) audioCtx.close() } catch (e) { /* 忽略 */ }
  processor = null
  source = null
  mediaStream = null
  audioCtx = null
  pending = []
}

/* ---------------- 统一入口 ---------------- */

function stopCapture() {
  // #ifdef APP-PLUS
  stopCaptureApp()
  // #endif
  // #ifdef H5
  stopCaptureH5()
  // #endif
}

/** 按住开始：先建会话再启动采集 */
export async function beginTalk(cameraId) {
  await startTalk(cameraId)
  try {
    // #ifdef APP-PLUS
    startCaptureApp()
    // #endif
    // #ifdef H5
    startCaptureH5()
    // #endif
  } catch (e) {
    await stopTalk()
    throw e
  }
}
