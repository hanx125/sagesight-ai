<template>
  <view class="page-wrap">
    <nav-bar title="事件详情" />

    <view class="detail-inner" v-if="ev">
      <!-- 事件大图 -->
      <view class="img-card card" v-if="imgUrl" @tap="preview">
        <image class="img" :src="imgUrl" mode="aspectFill" />
        <view class="img-tag">
          <image :src="icon('image', '#FFFFFF', 2.4)" style="width:26rpx;height:26rpx;" />
          <text>点击查看大图</text>
        </view>
      </view>
      <view class="card img-none" v-else>
        <empty-state text="暂无抓拍图片" icon-name="image" />
      </view>

      <!-- 状态行 -->
      <view class="status-row">
        <view class="badge" :class="'badge-' + meta.type">{{ meta.text }}</view>
        <view class="badge badge-cyan" v-if="ev.type">{{ eventTypeZh(ev.type) }}</view>
        <text class="t-dim status-time">{{ timeAgo(ev.created_at) }}</text>
      </view>

      <!-- 基本信息 -->
      <view class="card">
        <text class="section-title" style="margin-top:0;">基本信息</text>
        <view class="info-row">
          <text class="k">类别</text>
          <view class="v v-strong">{{ classNameZh(ev.class_name) }}</view>
        </view>
        <view class="info-row">
          <text class="k">置信度</text>
          <view class="conf-wrap">
            <view class="conf-bar">
              <view class="conf-bar-inner" :style="{ width: confPct }" />
            </view>
            <text class="v v-strong conf-num">{{ percent(ev.confidence) }}</text>
          </view>
        </view>
        <view class="info-row">
          <text class="k">任务</text>
          <text class="v">{{ ev.task_id != null ? '#' + ev.task_id : '-' }}</text>
        </view>
        <view class="info-row">
          <text class="k">摄像机</text>
          <text class="v">{{ ev.channel_name || (ev.camera_id != null ? '#' + ev.camera_id : '-') }}</text>
        </view>
        <!-- 区域/徘徊/起止：仅区域相关事件类型显示 -->
        <view class="info-row" v-if="isZoneType">
          <text class="k">所在区域</text>
          <text class="v">{{ zoneText || '-' }}</text>
        </view>
        <view class="info-row" v-if="showLoitering">
          <text class="k">徘徊区域</text>
          <text class="v">{{ loiterText || '-' }}</text>
        </view>
        <view class="info-row" v-if="isZoneType">
          <text class="k">开始时间</text>
          <text class="v">{{ formatTime(ev.start_time) }}</text>
        </view>
        <view class="info-row" v-if="isZoneType">
          <text class="k">结束时间</text>
          <text class="v">{{ formatTime(ev.end_time) }}</text>
        </view>
        <view class="info-row">
          <text class="k">发生时间</text>
          <text class="v">{{ formatTime(ev.created_at) }}</text>
        </view>
        <view class="info-row" v-if="ev.event_id">
          <text class="k">事件编号</text>
          <text class="v t-ellipsis">{{ ev.event_id }}</text>
        </view>
      </view>

      <!-- AI 分析 -->
      <view class="card">
        <text class="section-title" style="margin-top:0;">AI 分析</text>
        <view class="ai-box" v-if="ev.ai_description">
          <image class="ai-ic" :src="icon('zap', '#0a5fd9', 2)" />
          <view class="ai-body">
            <text class="ai-text">{{ ev.ai_description }}</text>
            <text class="ai-meta" v-if="aiMeta">{{ aiMeta }}</text>
          </view>
        </view>
        <view class="ai-empty" v-else>
          <image class="ai-ic" :src="icon('zap', '#98A2B3', 2)" />
          <text class="ai-empty-text">暂无 AI 描述</text>
        </view>
      </view>

      <view style="height: 40rpx;" />
    </view>

    <view v-else-if="!loading" class="detail-inner">
      <empty-state text="事件不存在或已删除" icon-name="alert" />
    </view>
  </view>
</template>

<script setup>
import { ref, computed } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import navBar from '@/components/nav-bar/nav-bar.vue'
import emptyState from '@/components/empty-state/empty-state.vue'
import icon from '@/utils/icons'
import { get } from '@/utils/api'
import { resolveUrl } from '@/utils/store'
import { timeAgo, formatTime, percent, eventStatusMeta, classNameZh, eventTypeZh } from '@/utils/format'

const ev = ref(null)
const loading = ref(true)

const meta = computed(() => eventStatusMeta(ev.value && ev.value.status))
const imgUrl = computed(() => resolveUrl(ev.value && ev.value.local_image_url))
const confPct = computed(() => {
  const n = Number(ev.value && ev.value.confidence)
  if (isNaN(n)) return '0%'
  return Math.min(100, Math.max(0, n * 100)) + '%'
})

// 区域相关事件类型：显示所在区域/徘徊区域/起止时间
const ZONE_TYPES = ['tracking', 'zone', 'zone_enter', 'loitering', 'occupancy']
const evType = computed(() => (ev.value && ev.value.type) || '')
const isZoneType = computed(() => ZONE_TYPES.indexOf(evType.value) !== -1)
const zoneText = computed(() => {
  if (!ev.value) return ''
  if (ev.value.zone_name) return ev.value.zone_name
  const z = ev.value.entered_zones
  return Array.isArray(z) && z.length ? z.join('、') : ''
})
const loiterText = computed(() => {
  const z = ev.value && ev.value.loitered_zones
  return Array.isArray(z) && z.length ? z.join('、') : ''
})
// 徘徊区域：徘徊类型始终显示；其他区域类型仅在确有徘徊记录时显示
const showLoitering = computed(() => isZoneType.value && (evType.value === 'loitering' || !!loiterText.value))
const aiMeta = computed(() => {
  if (!ev.value || !ev.value.ai_description) return ''
  const parts = []
  if (ev.value.ai_description_model) parts.push('模型：' + ev.value.ai_description_model)
  if (ev.value.ai_description_time) parts.push('时间：' + formatTime(ev.value.ai_description_time))
  return parts.join(' · ')
})

onLoad(async (opt) => {
  if (!opt || !opt.id) { loading.value = false; return }
  try {
    ev.value = await get('/api/events/' + opt.id)
  } catch (e) {
    uni.showToast({ title: e.message || '加载失败', icon: 'none' })
  }
  loading.value = false
})

function preview() {
  if (!imgUrl.value) return
  uni.previewImage({ urls: [imgUrl.value] })
}
</script>

<style lang="scss" scoped>
.detail-inner {
  padding: 24rpx;
}
.img-card { padding: 0; overflow: hidden; position: relative; }
.img { width: 100%; height: 460rpx; display: block; }
.img-tag {
  position: absolute;
  left: 20rpx;
  bottom: 20rpx;
  display: flex;
  align-items: center;
  gap: 8rpx;
  background: rgba(16, 24, 40, .75);
  backdrop-filter: blur(8rpx);
  border-radius: 999rpx;
  padding: 8rpx 20rpx;
  font-size: 20rpx;
  color: #FFFFFF;
  font-weight: 600;
}
.img-tag text { color: rgba(255,255,255,.9); }
.img-none { padding: 0; }

.status-row {
  display: flex;
  align-items: center;
  gap: 14rpx;
  margin-bottom: 24rpx;
}
.status-time { margin-left: auto; font-size: 24rpx; }

.info-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24rpx;
  padding: 18rpx 0;
  border-bottom: 1rpx solid $border;
}
.info-row:last-child { border-bottom: none; }
.k { font-size: 26rpx; color: $text-3; flex-shrink: 0; }
.v { font-size: 26rpx; color: $text-1; text-align: right; }
.v-strong { font-weight: 700; font-size: 28rpx; }

.conf-wrap { display: flex; align-items: center; gap: 16rpx; }
.conf-bar {
  width: 200rpx;
  height: 10rpx;
  background: $surface-2;
  border-radius: 5rpx;
  overflow: hidden;
}
.conf-bar-inner {
  height: 100%;
  background: $grad-primary;
  border-radius: 5rpx;
  transition: width .5s ease;
}
.conf-num { font-family: 'DIN Alternate', sans-serif; }

.ai-box {
  display: flex;
  gap: 16rpx;
  background: rgba(10, 95, 217, .05);
  border: 1rpx solid rgba(10, 95, 217, .15);
  border-radius: $radius-sm;
  padding: 20rpx;
}
.ai-ic { width: 36rpx; height: 36rpx; flex-shrink: 0; margin-top: 4rpx; }
.ai-body { flex: 1; display: flex; flex-direction: column; gap: 10rpx; min-width: 0; }
.ai-text { font-size: 26rpx; line-height: 1.7; color: $text-1; }
.ai-meta { font-size: 20rpx; color: $text-3; }
.ai-empty {
  display: flex;
  align-items: center;
  gap: 12rpx;
  background: $surface-2;
  border-radius: $radius-sm;
  padding: 22rpx 20rpx;
}
.ai-empty .ai-ic { margin-top: 0; opacity: .7; }
.ai-empty-text { font-size: 24rpx; color: $text-3; }
</style>
