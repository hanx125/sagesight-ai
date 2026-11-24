<template>
  <view class="panel-scroll">
    <!-- 报头 -->
    <view class="head">
      <view class="head-l">
        <view class="head-top">
          <text class="micro">LIVE VIEWS</text>
          <view class="live" v-if="onlineCount > 0">
            <view class="live-dot" />
            <text class="live-txt">{{ onlineCount }} 在线</text>
          </view>
        </view>
        <text class="head-title">实时监控</text>
        <text class="head-sub">共 {{ cameras.length }} 路画面 · {{ onlineCount }} 路可用</text>
      </view>
      <view class="head-actions">
        <view class="sq-btn" :class="{ on: autoRefresh }" @tap="toggleAuto">
          <image :src="icon('refresh', autoRefresh ? '#FFFFFF' : '#111210', 2.2)" />
          <text>自动</text>
        </view>
        <view class="sq-btn" @tap="load()">
          <image :src="icon('refresh', '#111210', 2.2)" />
        </view>
      </view>
    </view>

    <scroll-view scroll-y class="scroll-body" :show-scrollbar="false" refresher-enabled
      :refresher-triggered="refreshing" @refresherrefresh="onRefresh">
      <view class="frames">
        <view class="frame" v-for="(c, i) in cameras" :key="c.id" @tap="goPlayer(c)">
          <image
            v-if="snapshotUrl(c)"
            class="frame-img"
            :src="snapshotUrl(c)"
            mode="aspectFill"
            @error="onImgError(c)"
          />
          <view v-else class="frame-img frame-empty flex-center">
            <image :src="icon('camera', '#00A88F', 1.6)" style="width:64rpx;height:64rpx;" />
          </view>

          <!-- 画面叠层：编号 / 状态 / 名称 -->
          <view class="ov ov-top">
            <text class="cam-id num">CAM {{ camNo(i) }}</text>
            <view class="cam-state" :class="isOnline(c) ? 'on' : 'off'">
              <view class="cs-dot" />
              <text>{{ isOnline(c) ? '在线' : '离线' }}</text>
            </view>
          </view>
          <view class="ov ov-bottom">
            <view class="cam-meta">
              <text class="cam-name t-ellipsis">{{ c.name }}</text>
              <text class="cam-addr t-ellipsis">{{ c.ip ? c.ip + ':' + (c.port || '') : (c.source || '') }}</text>
            </view>
            <view class="play-chip" v-if="isOnline(c)">
              <image :src="icon('play', '#111210', 2)" style="width:22rpx;height:22rpx;" />
              <text>播放</text>
            </view>
            <view class="play-chip off" v-else>
              <text>不可用</text>
            </view>
          </view>
        </view>
      </view>

      <empty-state v-if="!cameras.length && !loading" text="暂无摄像机，请先在后台添加" icon-name="camera" />
      <view style="height: calc(150rpx + env(safe-area-inset-bottom));" />
    </scroll-view>
  </view>
</template>

<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import icon from '@/utils/icons'
import { getAllCameras } from '@/utils/api'
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

/** 兼容老 WebView：不用 padStart */
function camNo(i) {
  const n = i + 1
  return n < 10 ? '0' + n : '' + n
}

function snapshotUrl(c) {
  if (failedIds.value[c.id]) return ''
  // 离线设备用固定时间戳，避免自动刷新时反复请求必然失败的抓拍图
  const t = isOnline(c) ? nonce.value : 0
  return resolveUrl(`/api/stream/snapshot/${c.id}?t=${t}&access_token=${state.token}`)
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
    cameras.value = await getAllCameras()
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
      }, 15000)
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

/* 报头 */
.head {
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  padding: calc(var(--status-bar-height) + 30rpx) 24rpx 22rpx;
  border-bottom: 1rpx solid $line;
}
/* 标题背后的印刷网点，向右上渐隐 */
.head::after {
  content: '';
  position: absolute;
  right: -30rpx;
  top: 0;
  width: 220rpx;
  height: 150rpx;
  @include tex-dots(.09, 11rpx);
  -webkit-mask-image: linear-gradient(228deg, #000 0%, transparent 72%);
  mask-image: linear-gradient(228deg, #000 0%, transparent 72%);
  pointer-events: none;
}
.head-top { display: flex; align-items: center; gap: 16rpx; }
.live { display: flex; align-items: center; gap: 8rpx; }
.live-dot { width: 10rpx; height: 10rpx; background: $accent; animation: blink 1.6s steps(1) infinite; }
.live-txt { font-size: 18rpx; font-weight: 800; letter-spacing: 1.5rpx; color: $accent; }
@keyframes blink { 0%, 60% { opacity: 1; } 61%, 100% { opacity: .25; } }
.head-title {
  display: block;
  font-size: 46rpx;
  font-weight: 800;
  color: $text-1;
  letter-spacing: -1rpx;
  margin: 10rpx 0 6rpx;
}
.head-sub { font-size: 21rpx; color: $text-3; }
.head-actions { display: flex; gap: 10rpx; flex-shrink: 0; }
.sq-btn {
  display: flex;
  align-items: center;
  gap: 6rpx;
  height: 64rpx;
  min-width: 64rpx;
  padding: 0 16rpx;
  justify-content: center;
  border: 2rpx solid $border;
  border-radius: $radius-sm;
  background: $surface;
  box-shadow: 3rpx 3rpx 0 $border;
  font-size: 21rpx;
  font-weight: 800;
  color: $text-1;
  letter-spacing: 1rpx;
}
.sq-btn.on { background: $accent-2; box-shadow: 3rpx 3rpx 0 $border; }
.sq-btn.on text { color: $border; }
.sq-btn image { width: 28rpx; height: 28rpx; }

/* 画面列表：卡片之间露出极淡斜纹，像胶片条之间的间隙 */
.scroll-body { flex: 1; min-height: 0; }
.frames {
  padding: 22rpx 24rpx 0;
  @include tex-hatch(.03, 16rpx);
}
.frame {
  position: relative;
  height: 336rpx;
  border-radius: 10rpx;
  overflow: hidden;
  margin-bottom: 22rpx;
  background: #111210;
  border: $stroke solid $border;
  border-radius: $radius;
  box-shadow: 5rpx 5rpx 0 $border;
}
.frame:active { transform: scale(.99); }
.frame-img { width: 100%; height: 100%; }
/* 无信号画面：扫描线 + 中心十字准星 */
.frame-empty {
  background-color: #111210;
  background-image: repeating-linear-gradient(
    0deg,
    rgba(0, 168, 143, .06) 0 1rpx,
    transparent 1rpx 8rpx
  );
}
.frame-empty::before {
  content: '';
  position: absolute;
  left: 50%;
  top: 50%;
  width: 96rpx;
  height: 96rpx;
  margin: -48rpx 0 0 -48rpx;
  background-image:
    linear-gradient(rgba(0, 168, 143, .22) 1rpx, transparent 1rpx),
    linear-gradient(90deg, rgba(0, 168, 143, .22) 1rpx, transparent 1rpx);
  background-size: 96rpx 1rpx, 1rpx 96rpx;
  background-position: center, center;
  background-repeat: no-repeat;
  pointer-events: none;
}
.frame-empty::after {
  content: '';
  position: absolute;
  left: 50%;
  top: 50%;
  width: 34rpx;
  height: 34rpx;
  margin: -17rpx 0 0 -17rpx;
  border: 1rpx solid rgba(0, 168, 143, .3);
  border-radius: 50%;
  pointer-events: none;
}

.ov {
  position: absolute;
  left: 0; right: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16rpx 18rpx;
}
.ov-top {
  top: 0;
  background: linear-gradient(to bottom, rgba(10, 9, 5, .72), rgba(10, 9, 5, 0));
}
.ov-bottom {
  bottom: 0;
  background: linear-gradient(to top, rgba(10, 9, 5, .82), rgba(10, 9, 5, 0));
  padding: 26rpx 18rpx 16rpx;
}
.cam-id {
  font-family: $font-num;
  font-size: 18rpx;
  letter-spacing: 2rpx;
  color: rgba(255,255,255,.8);
}
.cam-state {
  display: flex;
  align-items: center;
  gap: 8rpx;
  font-size: 18rpx;
  font-weight: 700;
  letter-spacing: 1rpx;
}
.cam-state .cs-dot { width: 9rpx; height: 9rpx; }
.cam-state.on { color: #6EE7C8; }
.cam-state.on .cs-dot { background: #6EE7C8; }
.cam-state.off { color: rgba(255,255,255,.6); }
.cam-state.off .cs-dot { background: rgba(255,255,255,.4); }

.cam-meta { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4rpx; }
.cam-name { font-size: 30rpx; font-weight: 800; color: #FFFFFF; letter-spacing: -.3rpx; }
.cam-addr { font-size: 19rpx; color: rgba(255,255,255,.6); letter-spacing: .5rpx; }
.play-chip {
  display: flex;
  align-items: center;
  gap: 6rpx;
  padding: 8rpx 16rpx;
  background: #00A88F;
  border-radius: 6rpx;
  flex-shrink: 0;
}
.play-chip text { font-size: 20rpx; font-weight: 800; color: #111210; letter-spacing: 1rpx; }
.play-chip.off { background: rgba(255,255,255,.18); }
.play-chip.off text { color: #FFFFFF; }
</style>
