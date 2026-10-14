<template>
  <view class="event-card card card-press" @tap="goDetail">
    <view class="thumb-wrap">
      <image
        v-if="imgUrl"
        class="thumb"
        :src="imgUrl"
        mode="aspectFill"
        :lazy-load="true"
        @error="imgFail = true"
      />
      <view v-else class="thumb thumb-placeholder flex-center">
        <image :src="icon('image', '#98A2B3', 1.6)" style="width:64rpx;height:64rpx;" />
      </view>
      <view class="status-badge" :class="'badge-' + meta.type">{{ meta.text }}</view>
    </view>
    <view class="info flex-1">
      <view class="row-1">
        <text class="cls-name t-ellipsis">{{ zhName }}</text>
        <text class="conf">{{ confText }}</text>
      </view>
      <view class="row-2 t-ellipsis">
        <text class="t-dim">{{ taskLabel }} · {{ camLabel }}</text>
      </view>
      <view class="row-3">
        <image class="mini-ic" :src="icon('clock', '#667085')" />
        <text class="t-dim time-text">{{ timeText }}</text>
        <text v-if="event.algorithm" class="algo-chip t-ellipsis">{{ event.algorithm }}</text>
      </view>
    </view>
    <image class="arrow" :src="icon('chevron-right', '#98A2B3', 2.4)" />
  </view>
</template>

<script setup>
import { computed, ref } from 'vue'
import icon from '@/utils/icons'
import { resolveUrl } from '@/utils/store'
import { timeAgo, percent, eventStatusMeta, classNameZh } from '@/utils/format'

const props = defineProps({
  event: { type: Object, required: true }
})

const imgFail = ref(false)
const imgUrl = computed(() => {
  if (imgFail.value) return ''
  return resolveUrl(props.event.local_image_url)
})
const meta = computed(() => eventStatusMeta(props.event.status))
const zhName = computed(() => classNameZh(props.event.class_name))
const confText = computed(() => percent(props.event.confidence))
const timeText = computed(() => timeAgo(props.event.created_at))
const taskLabel = computed(() => props.event.task_name || ('任务 #' + props.event.task_id))
const camLabel = computed(() => props.event.channel_name || props.event.camera_id || '-')

function goDetail() {
  uni.navigateTo({ url: '/pages/events/detail?id=' + props.event.event_id })
}
</script>

<style lang="scss" scoped>
.event-card {
  display: flex;
  align-items: center;
  padding: 20rpx;
  gap: 20rpx;
}
.thumb-wrap {
  position: relative;
  width: 200rpx;
  height: 150rpx;
  flex-shrink: 0;
}
.thumb {
  width: 100%;
  height: 100%;
  border-radius: $radius-sm;
  background: $surface-2;
}
.thumb-placeholder { border: 1rpx dashed $border-strong; }
.status-badge {
  position: absolute;
  left: 10rpx;
  top: 10rpx;
  font-size: 18rpx;
  padding: 2rpx 12rpx;
}
.info { display: flex; flex-direction: column; gap: 8rpx; }
.row-1 { display: flex; align-items: center; justify-content: space-between; gap: 12rpx; }
.cls-name { font-size: 30rpx; font-weight: 700; color: $text-1; }
.conf {
  font-size: 24rpx;
  font-weight: 700;
  color: $primary;
  background: rgba(10, 95, 217, .08);
  padding: 2rpx 14rpx;
  border-radius: 999rpx;
  flex-shrink: 0;
}
.row-2 { font-size: 24rpx; }
.row-3 { display: flex; align-items: center; gap: 8rpx; }
.mini-ic { width: 26rpx; height: 26rpx; }
.time-text { font-size: 22rpx; }
.algo-chip {
  font-size: 18rpx;
  color: $text-2;
  background: $surface-2;
  border-radius: 6rpx;
  padding: 2rpx 10rpx;
  max-width: 160rpx;
}
.arrow { width: 32rpx; height: 32rpx; flex-shrink: 0; }
</style>
