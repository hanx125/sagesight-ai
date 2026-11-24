<template>
  <view class="empty-box">
    <view class="empty-mark">
      <image class="empty-icon" :src="icon(iconName, '#55584F', 1.8)" />
    </view>
    <text class="empty-text">{{ text }}</text>
    <slot />
  </view>
</template>

<script setup>
import icon from '@/utils/icons'

defineProps({
  text: { type: String, default: '暂无数据' },
  iconName: { type: String, default: 'inbox' }
})
</script>

<style lang="scss" scoped>
.empty-mark {
  position: relative;
  width: 128rpx;
  height: 128rpx;
  border: 1rpx dashed $line-2;
  border-radius: 6rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  background-color: $surface;
  /* 网点底纹 + 中心准星，空态也有纸上的排版感 */
  @include tex-dots(.075, 10rpx);
}
.empty-mark::before {
  content: '';
  position: absolute;
  left: 50%;
  top: 50%;
  width: 96rpx;
  height: 96rpx;
  margin: -48rpx 0 0 -48rpx;
  background-image:
    linear-gradient($line-2 1rpx, transparent 1rpx),
    linear-gradient(90deg, $line-2 1rpx, transparent 1rpx);
  background-size: 96rpx 1rpx, 1rpx 96rpx;
  background-position: center, center;
  background-repeat: no-repeat;
  opacity: .7;
}
.empty-icon {
  position: relative;
  z-index: 1;
  width: 56rpx;
  height: 56rpx;
}
</style>
