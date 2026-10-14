<template>
  <view class="nav-bar">
    <view class="nav-status" />
    <view class="nav-body">
      <view class="nav-side" @tap="handleBack" v-if="back">
        <image class="nav-icon" :src="icon('arrow-left', '#334155', 2.4)" />
      </view>
      <view class="nav-side" v-else />
      <text class="nav-title t-ellipsis">{{ title }}</text>
      <view class="nav-side nav-right">
        <slot name="right" />
      </view>
    </view>
  </view>
</template>

<script setup>
import icon from '@/utils/icons'

defineProps({
  title: { type: String, default: '' },
  back: { type: Boolean, default: true }
})

function handleBack() {
  const pages = getCurrentPages()
  if (pages.length > 1) {
    uni.navigateBack()
  } else {
    uni.reLaunch({ url: '/pages/index/index' })
  }
}
</script>

<style lang="scss" scoped>
.nav-bar {
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(20rpx);
  -webkit-backdrop-filter: blur(20rpx);
  position: sticky;
  top: 0;
  z-index: 100;
  border-bottom: 1rpx solid $border;
}
.nav-status {
  height: var(--status-bar-height);
  min-height: 20rpx;
}
.nav-body {
  height: 96rpx;
  display: flex;
  align-items: center;
  padding: 0 24rpx;
}
.nav-side {
  width: 64rpx;
  height: 64rpx;
  display: flex;
  align-items: center;
  justify-content: center;
}
.nav-icon {
  width: 44rpx;
  height: 44rpx;
}
.nav-title {
  flex: 1;
  text-align: center;
  font-size: 32rpx;
  font-weight: 700;
  color: $text-1;
}
.nav-right {
  justify-content: flex-end;
}
</style>
