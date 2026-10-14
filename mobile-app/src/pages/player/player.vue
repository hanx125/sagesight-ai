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
        <image class="snap-img" :src="snapUrl" mode="aspectFit" @error="snapError = true" />
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
        <image :src="icon('refresh', '#334155')" />
        <text>重新加载</text>
      </view>
      <view class="ctrl-btn" :class="{ on: mode === 'snapshot' }" @tap="toggleMode">
        <image :src="icon('image', mode === 'snapshot' ? '#0a5fd9' : '#667085')" />
        <text>{{ mode === 'snapshot' ? '切回直播' : '快照模式' }}</text>
      </view>
      <view class="ctrl-btn danger" @tap="stopStream">
        <image :src="icon('stop', '#D92D20')" />
        <text>停止推流</text>
      </view>
    </view>

    <!-- 喊话按钮 -->
    <view class="talk-bar">
      <view
        class="talk-btn"
        :class="{ talking: talkState === 'talking', starting: talkState === 'starting' }"
        @touchstart.prevent="onTalkStart"
        @touchend.prevent="onTalkEnd"
        @touchcancel="onTalkEnd"
      >
        <image :src="icon('mic', talkState === 'talking' ? '#FFFFFF' : '#D92D20', 2.4)" style="width:40rpx;height:40rpx;" />
        <text>{{ talkState === 'talking' ? '正在喊话 · 松手结束' : (talkState === 'starting' ? '连接摄像机...' : '按住喊话') }}</text>
      </view>
      <text class="talk-tip t-dim">按住按钮对摄像机喊话驱离，松开即结束</text>
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
import { beginTalk, stopTalk, isTalkActive } from '@/utils/talk'

const cameraId = ref('')
const cameraName = ref('实时画面')
const playUrl = ref('')
const mode = ref('live') // live | snapshot
const statusText = ref('正在拉起视频流...')
const snapNonce = ref(Date.now())
const snapError = ref(false)
const videoEl = ref(null)
const talkState = ref('idle') // idle | starting | talking

let snapTimer = null
let hls = null
let started = false

const snapUrl = computed(() => resolveUrl(`/api/stream/snapshot/${cameraId.value}?t=${snapNonce.value}&access_token=${state.token}`))
const maskedUrl = computed(() => {
  if (!playUrl.value) return '-'
  return playUrl.value.replace(/([?&])secret=[^&]+/g, '$1secret=***')
})

onLoad(async (opt) => {
  cameraId.value = opt.id || ''
  if (opt.name) cameraName.value = decodeURIComponent(opt.name)
  await startStream()
})

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
      setTimeout(() => setupHls(d.play_urls.hls), 60)
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
    if (retry > 0) setTimeout(() => setupHls(url, retry - 1), 100)
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
  stopSnapTimer()
  destroyHlsSafe()
  stopTalk().catch(() => {})
})
</script>

<style lang="scss" scoped>
.player-page {
  min-height: 100vh;
  background: $bg-deep;
  display: flex;
  flex-direction: column;
}
.video-box { padding: 8rpx 24rpx; }
.video {
  width: 100%;
  height: 420rpx;
  background: #000;
  border-radius: $radius;
  overflow: hidden;
  position: relative;
}
.snap-img { width: 100%; height: 100%; }
.snap-tag {
  position: absolute;
  left: 16rpx;
  bottom: 16rpx;
  display: flex;
  align-items: center;
  gap: 10rpx;
  padding: 6rpx 18rpx;
  border-radius: 999rpx;
  background: rgba(5, 8, 15, .65);
  font-size: 20rpx;
  color: #FBBF24;
}
.dot {
  width: 10rpx;
  height: 10rpx;
  border-radius: 50%;
  background: $warning;
  animation: blink 1.2s ease infinite;
}
@keyframes blink { 50% { opacity: .3; } }
.loading-col {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 20rpx;
}
.spinner {
  width: 52rpx;
  height: 52rpx;
  border: 5rpx solid rgba(10, 95, 217, .15);
  border-top-color: $primary;
  border-radius: 50%;
  animation: spin .8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

.ctrl-bar {
  display: flex;
  gap: 20rpx;
  padding: 8rpx 24rpx 24rpx;
}
.ctrl-btn {
  flex: 1;
  height: 84rpx;
  border-radius: $radius-sm;
  background: $surface;
  border: 1rpx solid $border;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10rpx;
  font-size: 24rpx;
  color: $text-1;
}
.ctrl-btn:active { opacity: .75; }
.ctrl-btn image { width: 32rpx; height: 32rpx; }
.ctrl-btn.on { border-color: rgba(10, 95, 217, .4); color: $primary; }
.ctrl-btn.danger { border-color: rgba(217, 45, 32, .3); }

/* 喊话 */
.talk-bar { padding: 0 24rpx 8rpx; }
.talk-btn {
  height: 96rpx;
  border-radius: $radius-sm;
  background: $surface;
  border: 1rpx solid rgba(217, 45, 32, .35);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14rpx;
  font-size: 28rpx;
  font-weight: 600;
  color: $text-1;
}
.talk-btn.talking {
  background: #D92D20;
  border-color: #D92D20;
  color: #FFFFFF;
}
.talk-btn.starting { opacity: .7; }
.talk-btn.talking { animation: talkPulse 1.2s ease infinite; }
@keyframes talkPulse { 50% { opacity: .75; } }
.talk-tip { display: block; text-align: center; font-size: 22rpx; padding-top: 14rpx; }

.info-card { margin: 0 24rpx; }
.info-row {
  display: flex;
  padding: 16rpx 0;
  border-bottom: 1rpx solid $border;
}
.info-row:last-child { border-bottom: none; }
.k { width: 160rpx; color: $text-3; font-size: 24rpx; flex-shrink: 0; }
.v { flex: 1; color: $text-1; font-size: 24rpx; min-width: 0; }
</style>
