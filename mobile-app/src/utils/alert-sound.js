/**
 * 告警提示音播放
 *
 * 音源取系统管理-报警语音播报的预置 MP3（/api/system/voice-files 第一个文件），
 * 取不到时静默跳过。开关存本地 storage（默认开启），1.5s 内去重避免 WS 与推送同时到达时重复播放。
 */
import { get } from './api'
import { resolveUrl } from './store'

const STORAGE_KEY = 'ss_alert_sound'
let audio = null
let alertUrl = ''
let lastPlayAt = 0

export function isAlertSoundOn() {
  try { return uni.getStorageSync(STORAGE_KEY) !== false } catch (e) { return true }
}

export function setAlertSoundOn(on) {
  try { uni.setStorageSync(STORAGE_KEY, !!on) } catch (e) { /* 忽略 */ }
  if (!on) destroyAlertSound()
}

/** 关闭提示音或应用退出时释放播放器资源 */
export function destroyAlertSound() {
  if (!audio) return
  try { audio.stop(); audio.destroy() } catch (e) { /* 忽略 */ }
  audio = null
}

/** 拉取预置语音文件列表，缓存第一条可播放地址（失败静默） */
export async function loadAlertSound() {
  try {
    const d = await get('/api/system/voice-files')
    const files = (d && d.files) || []
    if (files.length && files[0].url) {
      alertUrl = resolveUrl(files[0].url)
    }
  } catch (e) { /* 未登录或接口异常时静默 */ }
}

export function playAlertSound() {
  if (!isAlertSoundOn() || !alertUrl) return
  const now = Date.now()
  if (now - lastPlayAt < 1500) return
  lastPlayAt = now

  try {
    if (!audio) {
      audio = uni.createInnerAudioContext()
      audio.obeyMuteSwitch = false // 告警提示音不受系统静音开关影响
    }
    audio.stop()
    audio.src = alertUrl
    audio.play()
  } catch (e) { /* 播放失败静默 */ }
}
