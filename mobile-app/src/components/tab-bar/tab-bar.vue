<template>
  <view class="tab-bar safe-bottom">
    <view
      v-for="t in tabs"
      :key="t.key"
      class="tab-item"
      :class="{ active: modelValue === t.key }"
      @tap="switchTab(t.key)"
    >
      <view class="tab-icon-wrap">
        <image class="tab-icon" :src="icon(t.icon, modelValue === t.key ? '#0a5fd9' : '#667085', 2.2)" />
        <view v-if="modelValue === t.key" class="tab-dot" />
      </view>
      <text class="tab-label">{{ t.label }}</text>
    </view>
  </view>
</template>

<script setup>
import icon from '@/utils/icons'

defineProps({
  modelValue: { type: String, default: 'home' }
})
const emit = defineEmits(['update:modelValue', 'change'])

const tabs = [
  { key: 'home', label: '首页', icon: 'home' },
  { key: 'monitor', label: '监控', icon: 'video' },
  { key: 'events', label: '事件', icon: 'bell' },
  { key: 'profile', label: '我的', icon: 'user' }
]

function switchTab(key) {
  emit('update:modelValue', key)
  emit('change', key)
}
</script>

<style lang="scss" scoped>
.tab-bar {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 200;
  display: flex;
  background: rgba(255, 255, 255, 0.94);
  backdrop-filter: blur(20rpx);
  -webkit-backdrop-filter: blur(20rpx);
  border-top: 1rpx solid $border;
  padding-top: 10rpx;
}
.tab-item {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 8rpx 0 6rpx;
  transition: transform .15s ease;
}
.tab-item:active { transform: scale(.92); }
.tab-icon-wrap {
  position: relative;
  width: 48rpx;
  height: 48rpx;
  display: flex;
  align-items: center;
  justify-content: center;
}
.tab-icon {
  width: 46rpx;
  height: 46rpx;
}
.tab-dot {
  position: absolute;
  bottom: -10rpx;
  left: 50%;
  transform: translateX(-50%);
  width: 28rpx;
  height: 6rpx;
  border-radius: 3rpx;
  background: $grad-primary;
  box-shadow: 0 2rpx 12rpx rgba(10, 95, 217, .35);
}
.tab-label {
  font-size: 20rpx;
  color: $text-3;
  margin-top: 10rpx;
  transition: color .15s;
}
.tab-item.active .tab-label {
  color: $primary;
  font-weight: 600;
}
/* #ifdef H5 */
.tab-bar {
  left: 50%;
  right: auto;
  transform: translateX(-50%);
  max-width: 750px;
  width: 100%;
}
/* #endif */
</style>
