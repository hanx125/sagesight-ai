<template>
  <view class="index-page">
    <!-- 面板区 -->
    <view class="panel" v-show="tab === 'home'">
      <panel-home :active="tab === 'home'" @switch="switchPanel" />
    </view>
    <view class="panel" v-show="tab === 'monitor'">
      <panel-monitor :active="tab === 'monitor'" />
    </view>
    <view class="panel" v-show="tab === 'events'">
      <panel-events ref="eventsRef" :active="tab === 'events'" @changed="onEventsChanged" />
    </view>
    <view class="panel" v-show="tab === 'profile'">
      <panel-profile :active="tab === 'profile'" />
    </view>

    <!-- 底部标签栏 -->
    <tab-bar v-model="tab" />

    <!-- 事件角标 -->
    <view class="ev-chip" v-if="tab !== 'events' && eventsTotal > 0" @tap="tab = 'events'">
      <view class="ev-dot" />
      <text class="ev-txt num">{{ eventsTotal > 99 ? '99+' : eventsTotal }}</text>
      <text class="ev-lb">条事件</text>
      <image class="ev-ar" :src="icon('arrow-right', '#111210', 2.2)" />
    </view>
  </view>
</template>

<script setup>
import { ref, onUnmounted } from 'vue'
import { onLoad, onShow } from '@dcloudio/uni-app'
import { state, takePendingEvent } from '@/utils/store'
import { onWsEvent } from '@/utils/ws'
import icon from '@/utils/icons'
import tabBar from '@/components/tab-bar/tab-bar.vue'
import panelHome from '@/components/panel-home/panel-home.vue'
import panelMonitor from '@/components/panel-monitor/panel-monitor.vue'
import panelEvents from '@/components/panel-events/panel-events.vue'
import panelProfile from '@/components/panel-profile/panel-profile.vue'

const tab = ref('home')
const eventsRef = ref(null)
const eventsTotal = ref(0)

// 支持通过路由参数直达面板（如 ?tab=events）
onLoad((q) => {
  const k = q && q.tab
  if (k === 'monitor' || k === 'events' || k === 'profile') tab.value = k
})

// 未登录直接跳登录页；已登录时消费冷启动通知待打开的事件
onShow(() => {
  if (!state.token) {
    uni.reLaunch({ url: '/pages/login/login' })
    return
  }
  const pendingEvent = takePendingEvent()
  if (pendingEvent) {
    uni.navigateTo({ url: '/pages/events/detail?id=' + encodeURIComponent(pendingEvent) })
  }
})

function switchPanel(key) {
  tab.value = key
}

function onEventsChanged(total) {
  eventsTotal.value = total
}

// 实时报警：更新角标并刷新事件列表，避免新事件只在提示音里出现
const offNewEvent = onWsEvent('new_event', () => {
  eventsTotal.value += 1
  if (eventsRef.value) eventsRef.value.reload()
})
onUnmounted(() => { if (offNewEvent) offNewEvent() })
</script>

<style lang="scss" scoped>
.index-page {
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.panel {
  flex: 1;
  min-height: 0;
}
/* 新事件提示条：墨底横条，贴在标签栏上方 */
.ev-chip {
  position: fixed;
  right: 24rpx;
  bottom: calc(env(safe-area-inset-bottom) + 130rpx);
  z-index: 150;
  display: flex;
  align-items: center;
  gap: 8rpx;
  height: 64rpx;
  padding: 0 16rpx 0 14rpx;
  background: #FFFFFF;
  border: $stroke solid $border;
  border-radius: $radius-sm;
  box-shadow: 4rpx 4rpx 0 $accent;
}
.ev-chip:active { transform: translateY(1rpx); }
/* #ifdef H5 */
/* 仅在宽屏桌面端把悬浮元素对齐到 750px 内容列的右边缘；手机上保持贴边 */
@media (min-width: 800px) {
  .ev-chip {
    right: calc(50% - 375px + 24rpx);
  }
}
/* #endif */
.ev-dot {
  width: 8rpx;
  height: 8rpx;
  background: #00A88F;
  animation: evBlink 1.6s steps(1) infinite;
}
@keyframes evBlink { 0%, 60% { opacity: 1; } 61%, 100% { opacity: .3; } }
.ev-txt { font-size: 26rpx; font-weight: 800; color: $text-1; }
.ev-lb { font-size: 19rpx; color: $text-2; letter-spacing: 1rpx; }
.ev-ar { width: 22rpx; height: 22rpx; margin-left: 2rpx; }
</style>
