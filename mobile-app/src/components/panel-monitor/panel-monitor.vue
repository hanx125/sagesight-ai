<template>
  <view class="panel-scroll">
    <!-- 头部 -->
    <view class="head">
      <view>
        <text class="hi">实时监控</text>
        <text class="sub">{{ cameras.length }} 路摄像机 · {{ onlineCount }} 路在线</text>
      </view>
      <view class="head-actions">
        <view class="act-btn" :class="{ on: autoRefresh }" @tap="toggleAuto">
          <image :src="icon('refresh', autoRefresh ? '#0a5fd9' : '#667085')" />
          <text :style="{ color: autoRefresh ? '#0a5fd9' : '#667085' }">自动</text>
        </view>
        <view class="act-btn" @tap="load">
          <image :src="icon('refresh', '#334155')" />
        </view>
      </view>
    </view>

    <scroll-view scroll-y class="scroll-body" :show-scrollbar="false" refresher-enabled
      :refresher-triggered="refreshing" @refresherrefresh="onRefresh">
      <view class="cam-grid">
        <view class="cam-card" v-for="c in cameras" :key="c.id" @tap="goPlayer(c)">
          <view class="cam-shot">
            <image
              v-if="snapshotUrl(c)"
              class="shot-img"
              :src="snapshotUrl(c)"
              mode="aspectFill"
              @error="onImgError(c)"
            />
            <view v-else class="shot-fallback flex-center">
              <image :src="icon('camera', '#98A2B3', 1.6)" style="width:72rpx;height:72rpx;" />
            </view>
            <view class="online-badge" :class="isOnline(c) ? 'on' : 'off'">
              <view class="od" />
              <text>{{ isOnline(c) ? '在线' : '离线' }}</text>
            </view>
            <view class="play-mask flex-center" v-if="isOnline(c)">
              <view class="play-btn">
                <image :src="icon('play', '#101828', 2.6)" style="width:32rpx;height:32rpx;" />
              </view>
            </view>
          </view>
          <view class="cam-info">
            <text class="cam-name t-ellipsis">{{ c.name }}</text>
            <text class="cam-addr t-ellipsis">{{ c.ip ? c.ip + ':' + (c.port || '') : c.source }}</text>
          </view>
        </view>
      </view>
      <empty-state v-if="!cameras.length && !loading" text="暂无摄像机，请先添加" icon-name="camera" />
      <view style="height: calc(160rpx + env(safe-area-inset-bottom));" />
    </scroll-view>
  </view>
</template>

<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import icon from '@/utils/icons'
import { get } from '@/utils/api'
import { resolveUrl, state } from '@/utils/store'
import emptyState from '@/components/empty-state/empty-state.vue'

const props = defineProps({ active: Boolean })
const cameras = ref([])
const loading = ref(false)
const refreshing = ref(false)
const autoRefresh = ref(false)
const nonce = ref(Date.now())
const failedIds = ref({})
let timer = null
let loadedOnce = false

const onlineCount = computed(() => cameras.value.filter(isOnline).length)

function isOnline(c) {
  return c.online !== false && c.status !== 'offline'
}

function snapshotUrl(c) {
  if (failedIds.value[c.id]) return ''
  return resolveUrl(`/api/stream/snapshot/${c.id}?t=${nonce.value}&access_token=${state.token}`)
}

function onImgError(c) {
  failedIds.value = { ...failedIds.value, [c.id]: true }
}

function toggleAuto() {
  autoRefresh.value = !autoRefresh.value
}

function goPlayer(c) {
  if (!isOnline(c)) {
    uni.showToast({ title: '摄像机离线', icon: 'none' })
    return
  }
  uni.navigateTo({ url: `/pages/player/player?id=${c.id}&name=${encodeURIComponent(c.name || '')}` })
}

async function load(silent) {
  if (!silent && loading.value) return
  loading.value = true
  try {
    const d = await get('/api/cameras')
    cameras.value = (d && d.cameras) || (Array.isArray(d) ? d : [])
  } catch (e) {
    if (!silent) uni.showToast({ title: e.message, icon: 'none' })
  }
  loading.value = false
}

function onRefresh() {
  refreshing.value = true
  nonce.value = Date.now()
  load(true).finally(() => { refreshing.value = false })
}

watch(() => props.active, (v) => {
  if (v && !loadedOnce) {
    loadedOnce = true
    load(true)
  }
  manageTimer()
}, { immediate: true })

watch(autoRefresh, manageTimer)

function manageTimer() {
  if (autoRefresh.value && props.active) {
    if (!timer) {
      timer = setInterval(() => {
        failedIds.value = {}
        nonce.value = Date.now()
      }, 6000)
    }
  } else if (timer) {
    clearInterval(timer)
    timer = null
  }
}

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})
</script>

<style lang="scss" scoped>
.panel-scroll {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: calc(var(--status-bar-height) + 40rpx) 32rpx 24rpx;
}
.hi { font-size: 40rpx; font-weight: 800; color: $text-1; display: block; }
.sub { font-size: 24rpx; color: $text-3; margin-top: 8rpx; }
.head-actions { display: flex; gap: 16rpx; }
.act-btn {
  display: flex;
  align-items: center;
  gap: 8rpx;
  height: 64rpx;
  padding: 0 22rpx;
  border-radius: 999rpx;
  background: $surface;
  border: 1rpx solid $border;
  font-size: 22rpx;
  color: $text-3;
}
.act-btn.on { border-color: rgba(10, 95, 217, .35); background: rgba(10, 95, 217, .06); }
.act-btn image { width: 32rpx; height: 32rpx; }

.scroll-body { flex: 1; min-height: 0; }
.cam-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 20rpx;
  padding: 0 24rpx;
}
.cam-card {
  width: calc(50% - 10rpx);
  background: $surface;
  border: 1rpx solid $border;
  border-radius: $radius;
  overflow: hidden;
  box-shadow: $shadow-card;
  transition: transform .12s;
}
.cam-card:active { transform: scale(.97); }
.cam-shot {
  position: relative;
  width: 100%;
  height: 220rpx;
  background: $bg-deep;
}
.shot-img { width: 100%; height: 100%; }
.shot-fallback { width: 100%; height: 100%; }
.online-badge {
  position: absolute;
  left: 12rpx;
  top: 12rpx;
  display: flex;
  align-items: center;
  gap: 8rpx;
  padding: 4rpx 14rpx;
  border-radius: 999rpx;
  font-size: 18rpx;
  background: rgba(5, 8, 15, .6);
  backdrop-filter: blur(8rpx);
}
.online-badge .od { width: 10rpx; height: 10rpx; border-radius: 50%; }
.online-badge.on { color: #34D399; }
.online-badge.on .od { background: $success; box-shadow: 0 0 8rpx rgba(18,183,106,.8); }
.online-badge.off { color: $text-3; }
.online-badge.off .od { background: $text-3; }
.play-mask {
  position: absolute;
  inset: 0;
  background: rgba(5, 8, 15, .25);
  opacity: 0;
  transition: opacity .2s;
}
.cam-card:active .play-mask { opacity: 1; }
.play-btn {
  width: 88rpx; height: 88rpx;
  border-radius: 50%;
  background: rgba(255, 255, 255, .92);
  display: flex; align-items: center; justify-content: center;
  padding-left: 6rpx;
}
.cam-info { padding: 16rpx 20rpx 20rpx; display: flex; flex-direction: column; gap: 6rpx; }
.cam-name { font-size: 26rpx; font-weight: 600; color: $text-1; }
.cam-addr { font-size: 20rpx; color: $text-3; }
</style>
