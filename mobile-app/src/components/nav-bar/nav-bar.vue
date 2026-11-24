<template>
  <view class="nav-bar">
    <view class="nav-status" />
    <view class="nav-body">
      <view class="nav-back" v-if="back" @tap="handleBack">
        <image class="nav-icon" :src="icon('arrow-left', '#111210', 2.4)" />
      </view>
      <view class="nav-title-wrap">
        <text class="nav-title t-ellipsis">{{ title }}</text>
        <text class="nav-sub" v-if="sub">{{ sub }}</text>
      </view>
      <view class="nav-right">
        <slot name="right" />
      </view>
    </view>
  </view>
</template>

<script setup>
import icon from '@/utils/icons'

defineProps({
  title: { type: String, default: '' },
  sub: { type: String, default: '' },
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
  background: #FFFFFF;
  position: sticky;
  top: 0;
  z-index: 100;
  border-bottom: $stroke solid $border;
}
/* 右下角一小段刻度，替代满屏装饰，只在页头收口 */
.nav-bar::after {
  content: '';
  position: absolute;
  right: 20rpx;
  bottom: 0;
  width: 132rpx;
  height: 6rpx;
  @include tex-ruler(rgba(0, 168, 143, .2), 11rpx);
  background-repeat: repeat-x;
  background-position: 0 center;
  pointer-events: none;
}
.nav-status {
  height: var(--status-bar-height);
  min-height: 20rpx;
}
.nav-body {
  height: 104rpx;
  display: flex;
  align-items: center;
  gap: 8rpx;
  padding: 0 20rpx 0 12rpx;
}
/* 返回键：方形描边按钮，直角感的锚点 */
.nav-back {
  width: 64rpx;
  height: 64rpx;
  border: 2rpx solid $border;
  border-radius: $radius-sm;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  background: $surface;
}
.nav-back:active { background: $surface-3; }
.nav-icon { width: 36rpx; height: 36rpx; }
.nav-title-wrap {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2rpx;
  padding-left: 12rpx;
}
.nav-title {
  font-size: 31rpx;
  font-weight: 800;
  color: $text-1;
  letter-spacing: .5rpx;
}
.nav-sub {
  font-size: 19rpx;
  color: $text-3;
  letter-spacing: 1.5rpx;
}
.nav-right {
  display: flex;
  align-items: center;
  gap: 12rpx;
  flex-shrink: 0;
}
</style>
