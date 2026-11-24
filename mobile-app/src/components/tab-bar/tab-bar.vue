<template>
  <view class="tab-bar safe-bottom">
    <view class="tab-item" v-for="t in tabs" :key="t.key" @tap="switchTab(t.key)">
      <view class="tab-box" :class="{ on: modelValue === t.key }">
        <image class="tab-icon" :src="icon(t.icon, modelValue === t.key ? '#FFFFFF' : '#111210', 2.2, modelValue === t.key)" />
      </view>
      <text class="tab-label" :class="{ on: modelValue === t.key }">{{ t.label }}</text>
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
  { key: 'home', label: '总览', icon: 'home' },
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
/* 信号白标签栏：白底 + 粗顶线 + 激活项青绿方印 */
.tab-bar {
  position: fixed;
  left: 0; right: 0; bottom: 0;
  z-index: 200;
  display: flex;
  align-items: flex-start;
  padding: 12rpx 8rpx calc(10rpx + env(safe-area-inset-bottom));
  background: #FFFFFF;
  border-top: $stroke solid $border;
  box-shadow: 0 -6rpx 20rpx rgba(17, 18, 16, .06);
}
.tab-item {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6rpx;
}
.tab-item:active .tab-box { transform: scale(.88); }
.tab-box {
  width: 66rpx;
  height: 46rpx;
  border-radius: $radius-sm;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 2rpx solid transparent;
  transition: background .16s ease, transform .12s ease;
}
.tab-box.on {
  background: $accent;
  border-color: $border;
  box-shadow: 3rpx 3rpx 0 $border;
}
.tab-icon { width: 36rpx; height: 36rpx; }
.tab-label {
  font-size: 20rpx;
  color: $text-3;
  letter-spacing: 1rpx;
  font-weight: 700;
  transition: color .16s;
}
.tab-label.on { color: $text-1; font-weight: 800; }

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
