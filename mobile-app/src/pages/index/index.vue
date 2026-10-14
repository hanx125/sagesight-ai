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
    <view class="ev-badge" v-if="tab !== 'events' && eventsTotal > 0" @tap="tab = 'events'">
      <text class="ev-badge-num">{{ eventsTotal > 99 ? '99+' : eventsTotal }}</text>
      <text class="ev-badge-label">最新事件</text>
    </view>
  </view>
</template>

<script setup>
import { ref } from 'vue'
import { onLoad, onShow } from '@dcloudio/uni-app'
import { state } from '@/utils/store'
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

// 未登录直接跳登录页
onShow(() => {
  if (!state.token) {
    uni.reLaunch({ url: '/pages/login/login' })
  }
})

function switchPanel(key) {
  tab.value = key
}

function onEventsChanged(total) {
  eventsTotal.value = total
}
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
.ev-badge {
  position: fixed;
  right: 24rpx;
  bottom: calc(env(safe-area-inset-bottom) + 160rpx);
  z-index: 150;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 96rpx;
  height: 96rpx;
  border-radius: 50%;
  background: $grad-primary;
  box-shadow: $shadow-glow;
}
.ev-badge:active { transform: scale(.94); }
/* #ifdef H5 */
.ev-badge {
  right: calc(50% - 375px + 24rpx);
}
/* #endif */
.ev-badge-num {
  font-size: 30rpx;
  font-weight: 800;
  color: #FFFFFF;
  line-height: 1.1;
}
.ev-badge-label {
  font-size: 16rpx;
  color: rgba(255, 255, 255, .8);
  font-weight: 600;
}
</style>
