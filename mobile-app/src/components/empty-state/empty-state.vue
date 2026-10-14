<template>
  <view class="empty-box">
    <image class="empty-icon" :src="icon(iconName, '#2A3648', 1.6)" />
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
.empty-icon {
  width: 120rpx;
  height: 120rpx;
  opacity: .9;
}
</style>
