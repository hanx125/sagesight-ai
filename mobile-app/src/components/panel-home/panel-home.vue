<template>
  <scroll-view scroll-y class="panel-scroll" :show-scrollbar="false" refresher-enabled
    :refresher-triggered="refreshing" @refresherrefresh="loadAll">
    <view class="panel-inner">
      <!-- 顶部问候 -->
      <view class="head">
        <view class="head-left">
          <view class="head-row">
            <text class="hi">{{ greeting }}，{{ userName }}</text>
            <view class="live-dot" v-if="status.running_tasks > 0" />
          </view>
          <text class="sub">{{ dateText }} · {{ status.running_tasks || 0 }} 个任务运行中</text>
        </view>
        <view class="head-mark">
          <image class="head-mark-ic" :src="icon('logo', '#FFFFFF')" />
        </view>
      </view>

      <!-- 统计卡片 -->
      <view class="stat-grid">
        <view class="stat-card" v-for="s in statCards" :key="s.label" @tap="s.go && s.go()">
          <view class="stat-ic-wrap" :style="{ background: s.bg }">
            <image class="stat-ic" :src="icon(s.icon, s.color, 2)" />
          </view>
          <view class="stat-info">
            <view class="stat-num-wrap">
              <text class="stat-num">{{ s.value }}</text>
              <text class="stat-sub" v-if="s.sub">{{ s.sub }}</text>
            </view>
            <text class="stat-label">{{ s.label }}</text>
          </view>
        </view>
      </view>

      <!-- 快捷入口 -->
      <view class="quick card">
        <view class="quick-item" v-for="q in quicks" :key="q.label" @tap="q.go">
          <view class="quick-ic" :style="{ background: q.bg }">
            <image :src="icon(q.icon, q.color, 2.2)" />
          </view>
          <text class="quick-label">{{ q.label }}</text>
        </view>
      </view>

      <!-- 系统健康 -->
      <view class="card">
        <view class="health-head">
          <text class="section-title" style="margin:0;">系统健康</text>
          <view class="badge" :class="healthBad ? 'badge-warning' : 'badge-success'">
            {{ health.badgeText }}
          </view>
        </view>
        <view class="health-bar">
          <view class="health-bar-inner" :style="{ width: health.percent + '%' }" />
        </view>
        <view class="health-task" v-for="t in health.tasks" :key="t.task_id">
          <view class="dot" :class="t.healthy ? 'dot-ok' : 'dot-bad'" />
          <text class="t-ellipsis flex-1 health-name">{{ t.task_name }}</text>
          <text class="t-dim health-stage">{{ t.stage || '-' }}</text>
        </view>
        <view v-if="!health.tasks.length" class="t-dim health-none">暂无任务运行</view>
      </view>

      <!-- 最近事件 -->
      <view class="recent-head">
        <text class="section-title" style="margin:0;">最近事件</text>
        <view class="see-all" @tap="emit('switch', 'events')">
          <text>全部</text>
          <image :src="icon('chevron-right', '#0a5fd9', 2.4)" style="width:28rpx;height:28rpx;" />
        </view>
      </view>
      <event-card v-for="ev in recentEvents" :key="ev.event_id" :event="ev" />
      <empty-state v-if="!recentEvents.length && !loading" text="暂无检测事件" icon-name="bell" />
    </view>
  </scroll-view>
</template>

<script setup>
import { computed, ref, reactive, watch } from 'vue'
import icon from '@/utils/icons'
import { get } from '@/utils/api'
import { state } from '@/utils/store'
import eventCard from '@/components/event-card/event-card.vue'
import emptyState from '@/components/empty-state/empty-state.vue'

const props = defineProps({ active: Boolean })
const emit = defineEmits(['switch'])

const loading = ref(false)
const refreshing = ref(false)
const status = reactive({
  total_tasks: 0, running_tasks: 0, enabled_tasks: 0,
  total_cameras: 0, total_events: 0, available_algorithms: 0
})
const cameras = ref([])
const health = reactive({ tasks: [], expected: 0, healthy: 0 })
const recentEvents = ref([])
let loadedOnce = false

const userName = computed(() => (state.user && (state.user.display_name || state.user.username)) || '管理员')
const greeting = computed(() => {
  const h = new Date().getHours()
  if (h < 6) return '凌晨好'
  if (h < 12) return '早上好'
  if (h < 14) return '中午好'
  if (h < 18) return '下午好'
  return '晚上好'
})
const dateText = computed(() => {
  const d = new Date()
  const week = ['日', '一', '二', '三', '四', '五', '六'][d.getDay()]
  return `${d.getMonth() + 1}月${d.getDate()}日 周${week}`
})

const statCards = computed(() => [
  { label: '运行任务', icon: 'play', color: '#079455', bg: 'rgba(7,148,85,.10)', value: status.running_tasks,
    sub: `共 ${status.total_tasks}`, go: () => uni.navigateTo({ url: '/pages/tasks/tasks' }) },
  { label: '在线摄像机', icon: 'video', color: '#0a5fd9', bg: 'rgba(10,95,217,.10)', value: onlineCount,
    sub: `共 ${status.total_cameras}`, go: () => emit('switch', 'monitor') },
  { label: '事件总数', icon: 'bell', color: '#B45309', bg: 'rgba(180,83,9,.10)', value: status.total_events,
    go: () => emit('switch', 'events') },
  { label: '可用算法', icon: 'zap', color: '#1570EF', bg: 'rgba(21,112,239,.10)', value: status.available_algorithms }
])
const quicks = [
  { label: '任务管理', icon: 'target', color: '#0a5fd9', bg: 'rgba(10,95,217,.10)', go: () => uni.navigateTo({ url: '/pages/tasks/tasks' }) },
  { label: '摄像机', icon: 'camera', color: '#1570EF', bg: 'rgba(21,112,239,.10)', go: () => emit('switch', 'monitor') },
  { label: '事件中心', icon: 'bell', color: '#B45309', bg: 'rgba(180,83,9,.10)', go: () => emit('switch', 'events') },
  { label: '系统设置', icon: 'settings', color: '#475467', bg: 'rgba(71,84,103,.10)', go: () => uni.navigateTo({ url: '/pages/system/system' }) }
]

const onlineCount = computed(() => cameras.value.filter(c => c.online !== false && c.status !== 'offline').length)
const healthBad = computed(() => health.expected > 0 && health.healthy < health.expected)

async function loadAll() {
  refreshing.value = true
  await load(true)
  refreshing.value = false
}

async function load(silent) {
  if (!silent && loading.value) return
  loading.value = true
  const results = await Promise.allSettled([
    get('/api/status'),
    get('/api/health'),
    get('/api/events?page=1&per_page=5'),
    get('/api/cameras')
  ])
  if (results[0].status === 'fulfilled') Object.assign(status, results[0].value || {})
  if (results[1].status === 'fulfilled') {
    const h = results[1].value || {}
    health.tasks = h.tasks || []
    health.expected = h.expected_count || 0
    health.healthy = h.healthy_count || 0
  }
  if (results[2].status === 'fulfilled') recentEvents.value = (results[2].value && results[2].value.events) || []
  if (results[3].status === 'fulfilled') {
    const d = results[3].value
    cameras.value = (d && d.cameras) || (Array.isArray(d) ? d : [])
  }
  loading.value = false
}

watch(() => props.active, (v) => {
  if (v && !loadedOnce) {
    loadedOnce = true
    load(true)
  }
}, { immediate: true })

defineExpose({ load })
</script>

<style lang="scss" scoped>
.panel-scroll { height: 100%; }
.panel-inner {
  padding: calc(var(--status-bar-height) + 20rpx) 24rpx calc(160rpx + env(safe-area-inset-bottom));
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20rpx 8rpx 30rpx;
}
.head-row { display: flex; align-items: center; gap: 16rpx; }
.hi { font-size: 40rpx; font-weight: 800; color: $text-1; }
.live-dot {
  width: 14rpx; height: 14rpx; border-radius: 50%;
  background: $success;
  box-shadow: 0 0 12rpx rgba(18, 183, 106, .8);
  animation: pulse 2s ease infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: .4; }
}
.sub { font-size: 24rpx; color: $text-3; margin-top: 10rpx; display: block; }
.head-mark {
  width: 88rpx;
  height: 88rpx;
  border-radius: 26rpx;
  background: $grad-primary;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: $shadow-glow;
  flex-shrink: 0;
}
.head-mark-ic { width: 52rpx; height: 52rpx; }

.stat-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 20rpx;
  margin-bottom: 24rpx;
}
.stat-card {
  width: calc(50% - 10rpx);
  background: $surface;
  border: 1rpx solid $border;
  border-radius: $radius;
  padding: 24rpx;
  display: flex;
  align-items: center;
  gap: 18rpx;
  box-shadow: $shadow-card;
  transition: transform .12s;
}
.stat-card:active { transform: scale(.97); }
.stat-ic-wrap {
  width: 72rpx;
  height: 72rpx;
  border-radius: 20rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.stat-ic { width: 36rpx; height: 36rpx; }
.stat-info { display: flex; flex-direction: column; gap: 2rpx; min-width: 0; }
.stat-num-wrap { display: flex; align-items: baseline; gap: 8rpx; }
.stat-num { font-size: 40rpx; font-weight: 800; color: $text-1; font-family: 'DIN Alternate', sans-serif; line-height: 1.15; }
.stat-sub { font-size: 20rpx; color: $text-3; }
.stat-label { font-size: 22rpx; color: $text-2; }

.quick {
  display: flex;
  padding: 28rpx 12rpx;
}
.quick-item {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12rpx;
}
.quick-item:active { opacity: .7; }
.quick-ic {
  width: 84rpx; height: 84rpx;
  border-radius: 24rpx;
  display: flex; align-items: center; justify-content: center;
}
.quick-ic image { width: 42rpx; height: 42rpx; }
.quick-label { font-size: 22rpx; color: $text-2; }

.health-head { display: flex; align-items: center; justify-content: space-between; }
.health-bar {
  height: 12rpx;
  background: $surface-2;
  border-radius: 6rpx;
  margin: 20rpx 0 24rpx;
  overflow: hidden;
}
.health-bar-inner {
  height: 100%;
  background: $grad-primary;
  border-radius: 6rpx;
  transition: width .6s ease;
}
.health-task {
  display: flex;
  align-items: center;
  gap: 14rpx;
  padding: 12rpx 0;
}
.dot { width: 12rpx; height: 12rpx; border-radius: 50%; flex-shrink: 0; }
.dot-ok { background: $success; box-shadow: 0 0 8rpx rgba(18,183,106,.6); }
.dot-bad { background: $danger; box-shadow: 0 0 8rpx rgba(217,45,32,.5); }
.health-name { font-size: 26rpx; color: $text-1; }
.health-stage { font-size: 22rpx; flex-shrink: 0; }
.health-none { padding: 10rpx 0; font-size: 24rpx; }

.recent-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 8rpx 0 20rpx;
}
.see-all {
  display: flex; align-items: center; gap: 4rpx;
  font-size: 24rpx; color: $primary; font-weight: 500;
}
</style>
