<template>
  <view class="ev" @tap="goDetail">
    <!-- 画面：带监控时间戳角标 -->
    <view class="thumb-wrap">
      <image
        v-if="imgUrl"
        class="thumb"
        :src="imgUrl"
        mode="aspectFill"
        :lazy-load="true"
        @error="imgFail = true"
      />
      <view v-else class="thumb thumb-empty flex-center">
        <image :src="icon('image', '#55584F', 1.6)" style="width:44rpx;height:44rpx;" />
      </view>
      <view class="thumb-stamp">
        <text class="num">{{ clockText }}</text>
      </view>
    </view>

    <!-- 信息 -->
    <view class="info flex-1">
      <view class="info-top">
        <view class="st-sq" :class="'sq-' + meta.type" />
        <text class="cls t-ellipsis">{{ zhName }}</text>
      </view>
      <text class="src t-ellipsis">{{ taskLabel }} · {{ camLabel }}</text>
      <view class="conf-row">
        <view class="conf-track">
          <view class="conf-fill" :style="{ width: confNum + '%' }" />
        </view>
        <text class="conf-txt num">{{ confText }}</text>
      </view>
      <view class="meta-row">
        <text class="ago">{{ timeText }}</text>
        <text class="st-txt" :class="'st-' + meta.type">{{ meta.text }}</text>
      </view>
    </view>
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
const confNum = computed(() => {
  const n = Number(props.event.confidence)
  if (isNaN(n)) return 0
  return Math.min(100, Math.max(0, Math.round(n * 100)))
})
const timeText = computed(() => timeAgo(props.event.created_at))
const taskLabel = computed(() => props.event.task_name || ('任务 #' + props.event.task_id))
/** 摄像机标识：UUID 太长会污染副标题，截断成前 8 位 */
const camLabel = computed(() => {
  const c = props.event.channel_name || props.event.camera_id || '-'
  const str = String(c)
  return /^[0-9a-f-]{16,}$/i.test(str) ? str.slice(0, 8) : str
})

/** 画面时间戳：像监控录像一样叠在图上 */
const clockText = computed(() => {
  const raw = props.event.created_at
  if (!raw) return '--:--'
  const s = String(raw).replace('T', ' ')
  const m = s.match(/(\d{2}):(\d{2}):(\d{2})/)
  if (m) return `${m[1]}:${m[2]}:${m[3]}`
  const m2 = s.match(/(\d{2}):(\d{2})/)
  return m2 ? `${m2[1]}:${m2[2]}` : '--:--'
})

function goDetail() {
  uni.navigateTo({ url: '/pages/events/detail?id=' + props.event.event_id })
}
</script>

<style lang="scss" scoped>
.ev {
  display: flex;
  gap: 20rpx;
  padding: 18rpx;
  background: $surface;
  border: 1rpx solid $line;
  border-radius: $radius;
  margin-bottom: 18rpx;
}
.ev:active { background: $surface-2; border-color: $border-strong; }

/* 画面 */
.thumb-wrap {
  position: relative;
  width: 212rpx;
  height: 160rpx;
  flex-shrink: 0;
  background: #111210;
  border-radius: 4rpx;
  overflow: hidden;
}
.thumb { width: 100%; height: 100%; }
/* 无快照：斜纹底 + 内描边取景框，避免出现一块死白 */
.thumb-empty {
  position: relative;
  background-color: $surface-2;
  @include tex-hatch(.05, 13rpx);
}
.thumb-empty::after {
  content: '';
  position: absolute;
  left: 10rpx;
  top: 10rpx;
  right: 10rpx;
  bottom: 10rpx;
  border: 1rpx solid rgba(0, 168, 143, .16);
  pointer-events: none;
}
/* 监控时间戳：黑底白字等宽，贴在画面左下 */
.thumb-stamp {
  position: absolute;
  left: 0;
  bottom: 0;
  padding: 2rpx 10rpx;
  background: rgba(10, 9, 5, .78);
}
.thumb-stamp text {
  font-size: 18rpx;
  color: #FFFFFF;
  letter-spacing: .5rpx;
}

/* 信息区 */
.info { display: flex; flex-direction: column; justify-content: center; gap: 8rpx; min-width: 0; }
.info-top { display: flex; align-items: center; gap: 10rpx; min-width: 0; }
.st-sq { width: 12rpx; height: 12rpx; flex-shrink: 0; }
.sq-danger { background: $danger; }
.sq-success { background: $success; }
.sq-warning { background: $warning; }
.sq-info { background: $info; }
.sq-gray { background: $text-3; }
.sq-cyan { background: $text-2; }
.cls { font-size: 30rpx; font-weight: 800; color: $text-1; letter-spacing: -.3rpx; }
.src { font-size: 22rpx; color: $text-3; }

.conf-row { display: flex; align-items: center; gap: 10rpx; margin-top: 4rpx; }
.conf-track {
  flex: 1;
  height: 3rpx;
  background: $surface-3;
}
.conf-fill { height: 100%; background: $text-1; }
.conf-txt { font-size: 20rpx; color: $text-2; width: 84rpx; text-align: right; flex-shrink: 0; }

.meta-row { display: flex; align-items: center; gap: 12rpx; margin-top: 4rpx; }
.ago { font-size: 21rpx; color: $text-3; letter-spacing: .5rpx; }
.st-txt {
  font-size: 19rpx;
  font-weight: 700;
  letter-spacing: 1rpx;
  padding-left: 12rpx;
  border-left: 1rpx solid $line;
}
.st-danger { color: $danger; }
.st-success { color: $success; }
.st-warning { color: $warning; }
.st-info { color: $info; }
.st-gray { color: $text-3; }
.st-cyan { color: $text-2; }
</style>
