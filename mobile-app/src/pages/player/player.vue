<template>
  <view class="player-page">
    <nav-bar :title="cameraName" />

    <!-- 视频区 -->
    <view class="video-box">
      <!-- #ifdef H5 -->
      <video
        v-if="mode === 'live' && playUrl"
        ref="videoEl"
        class="video"
        autoplay
        playsinline
      />
      <!-- #endif -->
      <!-- #ifndef H5 -->
      <video
        v-if="mode === 'live' && playUrl"
        id="playerVideo"
        class="video"
        :src="playUrl"
        autoplay
        controls
        object-fit="contain"
        @error="onVideoError"
      />
      <!-- #endif -->
      <view v-if="mode === 'snapshot'" class="video flex-center">
        <image v-if="!snapError" class="snap-img" :src="snapUrl" mode="aspectFit" @error="snapError = true" />
        <view class="snap-tag">
          <view class="dot" />
          <text>快照模式 · 每 2 秒刷新</text>
        </view>
      </view>
      <view v-if="!playUrl && mode !== 'snapshot'" class="video flex-center">
        <view class="loading-col">
          <view class="spinner" />
          <text class="t-dim">{{ statusText }}</text>
        </view>
      </view>
    </view>

    <!-- 控制条 -->
    <view class="ctrl-bar">
      <view class="ctrl-btn" @tap="reload">
        <image :src="icon('refresh', '#111210')" />
        <text>重新加载</text>
      </view>
      <view class="ctrl-btn" :class="{ on: mode === 'snapshot' }" @tap="toggleMode">
        <image :src="icon('image', mode === 'snapshot' ? '#111210' : '#55584F')" />
        <text>{{ mode === 'snapshot' ? '切回直播' : '快照模式' }}</text>
      </view>
      <view class="ctrl-btn danger" @tap="stopStream">
        <image :src="icon('stop', '#E8342C')" />
        <text>停止推流</text>
      </view>
    </view>

    <!-- 喊话按钮 -->
    <view class="talk-bar">
      <view
        class="talk-btn"
        :class="{ talking: talkState === 'talking', starting: talkState === 'starting', unsupported: talkSupported === false }"
        @touchstart.prevent="onTalkStart"
        @touchend.prevent="onTalkEnd"
        @touchcancel="onTalkEnd"
      >
        <image :src="icon('mic', talkState === 'talking' ? '#FFFFFF' : '#E8342C', 2.4)" style="width:40rpx;height:40rpx;" />
        <text>{{ talkSupported === false ? '该设备不支持语音对讲' : (talkState === 'talking' ? '正在喊话 · 松手结束' : (talkState === 'starting' ? '连接摄像机...' : '按住喊话')) }}</text>
      </view>
      <text class="talk-tip t-dim">{{ talkSupported === false ? '需摄像机支持 ONVIF 音频回传' : '按住按钮对摄像机喊话驱离，松开即结束' }}</text>
    </view>

    <!-- 信息卡 -->
    <view class="card info-card">
      <view class="info-row">
        <text class="k">摄像机</text>
        <text class="v">{{ cameraName }}</text>
      </view>
      <view class="info-row">
        <text class="k">播放协议</text>
        <text class="v">HLS (m3u8) · ZLMediaKit</text>
      </view>
      <view class="info-row" v-if="playUrl">
        <text class="k">播放地址</text>
        <text class="v t-ellipsis">{{ maskedUrl }}</text>
      </view>
    </view>
  </view>
</template>

<script setup>
import { ref, computed, onBeforeUnmount } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import navBar from '@/components/nav-bar/nav-bar.vue'
import icon from '@/utils/icons'
import { get, post } from '@/utils/api'
import { resolveUrl, state } from '@/utils/store'
import { beginTalk, stopTalk, isTalkActive, checkTalkCapability } from '@/utils/talk'

const cameraId = ref('')
const cameraName = ref('实时画面')
const playUrl = ref('')
const mode = ref('live') // live | snapshot
const statusText = ref('正在拉起视频流...')
const snapNonce = ref(Date.now())
const snapError = ref(false)
const videoEl = ref(null)
const talkState = ref('idle') // idle | starting | talking
const talkSupported = ref(null) // null 未探测/探测失败（保持可用） | false 明确不支持

let snapTimer = null
let hls = null
// 集中管理短延时任务，页面卸载时统一清理，避免回调打到已销毁的组件
const pendingTimers = new Set()
function later(fn, ms) {
  const id = setTimeout(() => { pendingTimers.delete(id); fn() }, ms)
  pendingTimers.add(id)
  return id
}
function clearPendingTimers() {
  pendingTimers.forEach(id => clearTimeout(id))
  pendingTimers.clear()
}

const snapUrl = computed(() => resolveUrl(`/api/stream/snapshot/${cameraId.value}?t=${snapNonce.value}&access_token=${state.token}`))
const maskedUrl = computed(() => {
  if (!playUrl.value) return '-'
  return playUrl.value.replace(/([?&])secret=[^&]+/g, '$1secret=***')
})

onLoad(async (opt) => {
  cameraId.value = opt.id || ''
  if (opt.name) {
    try { cameraName.value = decodeURIComponent(opt.name) } catch (e) { cameraName.value = opt.name }
  }
  await startStream()
  probeTalkCapability()
})

/** 后台探测喊话能力，不阻塞播放；探测失败时保持按钮可用，避免误伤 */
async function probeTalkCapability() {
  if (!cameraId.value) return
  try {
    const supported = await checkTalkCapability(cameraId.value)
    if (!isTalkActive()) talkSupported.value = supported
  } catch (e) {
    talkSupported.value = null
  }
}

async function startStream() {
  if (!cameraId.value) return
  playUrl.value = ''
  snapError.value = false
  statusText.value = '正在拉起视频流...'
  try {
    await post(`/api/stream/start/${cameraId.value}`)
    const d = await get(`/api/stream/urls/${cameraId.value}`)
    if (d && d.success && d.play_urls && d.play_urls.hls) {
      playUrl.value = d.play_urls.hls
      // #ifdef H5
      later(() => setupHls(d.play_urls.hls), 60)
      // #endif
    } else {
      fallbackToSnapshot('未获取到播放地址，已切换快照模式')
    }
  } catch (e) {
    fallbackToSnapshot('拉流失败，已切换快照模式')
  }
}

// #ifdef H5
// uni-video 是组件，ref 拿到的是组件实例；hls.js 需要真实 <video> DOM 元素
function resolveVideoEl() {
  const el = videoEl.value
  if (!el) return null
  const root = el.$el || el
  if (root && root.tagName === 'VIDEO') return root
  return root && root.querySelector ? root.querySelector('video') : null
}

async function setupHls(url, retry = 3) {
  destroyHls()
  const el = resolveVideoEl()
  if (!el) {
    // video 元素由 v-if 控制，可能尚未渲染完成，稍后重试
    if (retry > 0) later(() => setupHls(url, retry - 1), 100)
    return
  }
  // 动态引入本地打包的 hls.js（按需分包，从同源服务器加载，不依赖外网 CDN）
  try {
    const mod = await import('hls.js')
    const Hls = mod.default
    if (Hls && Hls.isSupported()) {
      hls = new Hls({ liveDurationInfinity: true })
      hls.loadSource(url)
      hls.attachMedia(el)
      hls.on(Hls.Events.ERROR, (_, data) => {
        if (data.fatal) {
          destroyHls()
          fallbackToSnapshot('直播流异常，已切换快照模式')
        }
      })
      return
    }
  } catch (e) {
    // hls.js 加载失败时继续尝试原生播放
  }
  if (el.canPlayType('application/vnd.apple.mpegurl')) {
    el.src = url
    el.play().catch(() => {})
  } else {
    fallbackToSnapshot('当前浏览器不支持 HLS，已切换快照模式')
  }
}

function destroyHls() {
  if (hls) {
    try { hls.destroy() } catch (e) { /* 忽略 */ }
    hls = null
  }
}
// #endif

function onVideoError() {
  // App 端原生 video 出错 → 快照兜底
  if (mode.value === 'live') {
    fallbackToSnapshot('播放异常，已切换快照模式')
  }
}

function fallbackToSnapshot(msg) {
  mode.value = 'snapshot'
  if (msg) uni.showToast({ title: msg, icon: 'none' })
  startSnapTimer()
}

function startSnapTimer() {
  stopSnapTimer()
  snapTimer = setInterval(() => {
    snapError.value = false
    snapNonce.value = Date.now()
  }, 2000)
}

function stopSnapTimer() {
  if (snapTimer) {
    clearInterval(snapTimer)
    snapTimer = null
  }
}

function toggleMode() {
  if (mode.value === 'live') {
    fallbackToSnapshot('')
  } else {
    mode.value = 'live'
    stopSnapTimer()
    startStream()
  }
}

function reload() {
  stopSnapTimer()
  mode.value = 'live'
  startStream()
}

function stopStream() {
  uni.showModal({
    title: '停止推流',
    content: '确定停止该摄像机的视频流吗？',
    success: async (res) => {
      if (!res.confirm) return
      try {
        await post(`/api/stream/stop/${cameraId.value}`)
        playUrl.value = ''
        stopSnapTimer()
        destroyHlsSafe()
        statusText.value = '推流已停止，点击"重新加载"恢复'
        uni.showToast({ title: '已停止', icon: 'success' })
      } catch (e) {
        uni.showToast({ title: e.message, icon: 'none' })
      }
    }
  })
}

function destroyHlsSafe() {
  // #ifdef H5
  destroyHls()
  // #endif
}

/* ---------- 按住喊话 ---------- */
async function onTalkStart() {
  if (talkState.value !== 'idle' || isTalkActive()) return
  if (talkSupported.value === false) {
    uni.showToast({ title: '该摄像机不支持语音对讲', icon: 'none' })
    return
  }
  talkState.value = 'starting'
  try {
    await beginTalk(cameraId.value)
    talkState.value = 'talking'
    uni.vibrateShort && uni.vibrateShort()
  } catch (e) {
    talkState.value = 'idle'
    await stopTalk().catch(() => {})
    uni.showToast({ title: e.message || '喊话启动失败', icon: 'none' })
  }
}

async function onTalkEnd() {
  if (talkState.value === 'idle') return
  talkState.value = 'idle'
  await stopTalk().catch(() => {})
}

onBeforeUnmount(() => {
  clearPendingTimers()
  stopSnapTimer()
  destroyHlsSafe()
  stopTalk().catch(() => {})
})
</script>

<style lang="scss" scoped>
.player-page {
  min-height: 100vh;
  background: $bg;
  display: flex;
  flex-direction: column;
  position: relative;
}

/* 画面：纸面上的一块黑场 */
.video-box { padding: 16rpx 24rpx 8rpx; }
.video {
  width: 100%;
  height: 420rpx;
  background: #000;
  border-radius: 8rpx;
  overflow: hidden;
  position: relative;
  border: 1rpx solid $line-2;
  box-shadow: 0 14rpx 32rpx rgba(0, 0, 0, .22);
}
/* 监视器安全框：内缩一圈细线，画面外不再是一块纯黑 */
.video::before {
  content: '';
  position: absolute;
  left: 16rpx;
  top: 16rpx;
  right: 16rpx;
  bottom: 16rpx;
  border: 1rpx solid rgba(255, 255, 255, .16);
  pointer-events: none;
  z-index: 3;
}
.snap-img { width: 100%; height: 100%; }
.snap-tag {
  position: absolute;
  left: 0;
  bottom: 0;
  display: flex;
  align-items: center;
  gap: 10rpx;
  padding: 4rpx 14rpx;
  border-radius: 0;
  background: rgba(10, 9, 5, .78);
  font-size: 19rpx;
  letter-spacing: 1rpx;
  color: #FFFFFF;
}
.dot {
  width: 8rpx;
  height: 8rpx;
  background: #E0930A;
  animation: blink 1.2s steps(1) infinite;
}
@keyframes blink { 50% { opacity: .25; } }
.loading-col {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 20rpx;
}
.loading-col .t-dim { color: rgba(255, 255, 255, .6); }
.spinner {
  width: 48rpx;
  height: 48rpx;
  border: 4rpx solid rgba(255, 255, 255, .22);
  border-top-color: #FFFFFF;
  border-radius: 50%;
  animation: spin .8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* 控制条：矩形按钮，像设备面板 */
.ctrl-bar {
  display: flex;
  gap: 14rpx;
  padding: 12rpx 24rpx 20rpx;
}
.ctrl-btn {
  flex: 1;
  height: 84rpx;
  border-radius: 8rpx;
  background: $surface;
  border: 1rpx solid $line-2;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8rpx;
  font-size: 22rpx;
  letter-spacing: .5rpx;
  color: $text-1;
}
.ctrl-btn:active { background: $surface-2; }
.ctrl-btn image { width: 30rpx; height: 30rpx; }
.ctrl-btn.on {
  background: #00A88F;
  border-color: #00A88F;
  color: #111210;
}
.ctrl-btn.danger { border-color: rgba(163, 47, 34, .3); color: $danger; }

/* 喊话 */
.talk-bar { padding: 0 24rpx 16rpx; }
.talk-btn {
  height: 96rpx;
  border-radius: 10rpx;
  background: $surface;
  border: 1rpx solid $line-2;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14rpx;
  font-size: 28rpx;
  font-weight: 700;
  letter-spacing: 1rpx;
  color: $text-1;
}
.talk-btn:active { background: $surface-2; }
.talk-btn.talking {
  background: $accent;
  border-color: $accent-deep;
  color: #FFFFFF;
  animation: talkPulse 1.2s ease infinite;
}
@keyframes talkPulse { 50% { opacity: .8; } }
.talk-btn.starting { opacity: .7; }
.talk-btn.unsupported { opacity: .55; }
.talk-tip { display: block; text-align: center; font-size: 21rpx; padding-top: 14rpx; }

.info-card { margin: 0 24rpx 24rpx; }
.info-row {
  display: flex;
  padding: 16rpx 0;
  border-bottom: 1rpx solid $line;
}
.info-row:last-child { border-bottom: none; }
.k { width: 160rpx; color: $text-3; font-size: 24rpx; flex-shrink: 0; }
.v { flex: 1; color: $text-1; font-size: 24rpx; min-width: 0; }
</style>
