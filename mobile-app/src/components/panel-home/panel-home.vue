<template>
  <scroll-view scroll-y class="panel-scroll" :show-scrollbar="false" refresher-enabled
    :refresher-triggered="refreshing" @refresherrefresh="loadAll">
    <view class="panel-inner">
      <!-- 报头：超大标题排版 -->
      <view class="masthead">
        <view class="mast-top">
          <text class="mast-kicker">SAGESIGHT / 站点总览</text>
          <view class="live-tag" v-if="status.running_tasks > 0">
            <view class="live-dot" /><text>LIVE</text>
          </view>
          <view class="live-tag live-off" v-else><text>STANDBY</text></view>
        </view>
        <view class="mast-title-wrap">
          <text class="mast-title">{{ greeting }}，</text>
          <text class="mast-title mast-accent">{{ userName }}</text>
        </view>
        <view class="mast-sub">
          <text class="mast-date">{{ dateText }}</text>
          <text class="mast-rule" />
          <text class="mast-run"><text class="num">{{ status.running_tasks || 0 }}</text> 个任务运行中</text>
        </view>
      </view>

      <!-- 主视觉：黑底反白大数字 hero -->
      <view class="hero" @tap="emit('switch', 'monitor')">
        <view class="hero-grid" />
        <view class="hero-corner" />
        <view class="hero-main">
          <view class="hero-cap"><text class="hero-cap-no num">01</text><text>在线画面 / ONLINE FEEDS</text></view>
          <view class="hero-numrow">
            <text class="hero-num num">{{ onlineCount }}</text>
            <text class="hero-total num">/ {{ status.total_cameras || 0 }}</text>
          </view>
          <view class="ticks">
            <view class="tick" v-for="i in tickTotal" :key="i" :class="{ on: i <= onlineCount }" />
          </view>
          <text class="hero-note">{{ onlineCount > 0 ? '● 画面正在实时推流' : '○ 暂无在线画面' }}</text>
        </view>
        <view class="hero-side">
          <image class="hero-ic" :src="icon('video', '#E8FF3D', 2)" />
          <view class="hero-go"><text>进入</text><image :src="icon('arrow-up-right', '#111210', 2.6)" /></view>
        </view>
      </view>

      <!-- 运行指标：大数字硬边框卡 -->
      <view class="sec-head">
        <text class="sec-no">02</text><text class="sec-txt">运行指标</text><view class="sec-rule" />
      </view>
      <view class="metric-bar">
        <view class="metric" v-for="(m, i) in metrics" :key="m.label" :class="'m' + i" @tap="m.go && m.go()">
          <text class="metric-num num">{{ m.value }}</text>
          <text class="metric-label">{{ m.label }}</text>
          <text class="metric-note">{{ m.note }}</text>
        </view>
      </view>

      <!-- 快捷入口：贴纸式方块 -->
      <view class="sec-head">
        <text class="sec-no">03</text><text class="sec-txt">快捷入口</text><view class="sec-rule" />
      </view>
      <view class="quick-grid">
        <view class="quick" v-for="(q, i) in quicks" :key="q.label" :class="'q' + i" @tap="q.go">
          <view class="q-ic"><image :src="icon(q.icon, i === 1 ? '#00A88F' : '#111210', 2.2)" /></view>
          <text class="q-label">{{ q.label }}</text>
          <text class="q-arrow num">0{{ i + 1 }}</text>
        </view>
      </view>

      <!-- 系统健康 -->
      <view class="sec-head">
        <text class="sec-no">04</text><text class="sec-txt">系统健康</text><view class="sec-rule" />
        <view class="sec-badge" :class="{ bad: healthBad }">{{ healthBadgeText }}</view>
      </view>
      <view class="card health">
        <view class="health-meter">
          <view class="meter-track"><view class="meter-fill" :class="{ bad: healthBad }" :style="{ width: healthPercent + '%' }" /></view>
          <text class="meter-num num">{{ healthPercent }}%</text>
        </view>
        <view class="health-row" v-for="t in health.tasks" :key="t.task_id">
          <view class="h-sq" :class="t.healthy ? 'ok' : 'bad'" />
          <text class="h-name t-ellipsis flex-1">{{ t.task_name }}</text>
          <text class="h-stage">{{ t.stage || '-' }}</text>
        </view>
        <view v-if="!health.tasks.length" class="health-none">暂无任务运行</view>
      </view>

      <!-- 最近事件 -->
      <view class="sec-head">
        <text class="sec-no">05</text><text class="sec-txt">最近事件</text><view class="sec-rule" />
        <view class="sec-link" @tap="emit('switch', 'events')"><text>全部</text><image :src="icon('arrow-right', '#111210', 2.4)" /></view>
      </view>
      <event-card v-for="ev in recentEvents" :key="ev.event_id" :event="ev" />
      <empty-state v-if="!recentEvents.length && !loading" text="暂无检测事件" icon-name="bell" />
      <view style="height: 24rpx;" />
    </view>
  </scroll-view>
</template>

<script setup>
import { computed, ref, reactive, watch, onUnmounted } from 'vue'
import icon from '@/utils/icons'
import { get, getAllCameras } from '@/utils/api'
import { state } from '@/utils/store'
import { onWsEvent } from '@/utils/ws'
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

const metrics = computed(() => [
  { label: '运行任务', note: `共 ${status.total_tasks}`, value: status.running_tasks,
    go: () => uni.navigateTo({ url: '/pages/tasks/tasks' }) },
  { label: '事件总数', note: '累计记录', value: status.total_events,
    go: () => emit('switch', 'events') },
  { label: '可用算法', note: '已装载', value: status.available_algorithms }
])
const quicks = [
  { label: '任务管理', icon: 'target', go: () => uni.navigateTo({ url: '/pages/tasks/tasks' }) },
  { label: '摄像机', icon: 'camera', go: () => emit('switch', 'monitor') },
  { label: '事件中心', icon: 'bell', go: () => emit('switch', 'events') },
  { label: '系统设置', icon: 'sliders', go: () => uni.navigateTo({ url: '/pages/system/system' }) }
]

const onlineCount = computed(() => cameras.value.filter(c => c.online !== false && c.status !== 'offline').length)
/** 墨卡里的刻度条：最多画 12 格，超出按比例折算 */
const tickTotal = computed(() => {
  const total = status.total_cameras || 0
  return total > 0 ? Math.min(12, total) : 12
})
const healthBad = computed(() => health.expected > 0 && health.healthy < health.expected)
const healthBadgeText = computed(() => {
  if (!health.expected) return '无任务'
  return healthBad.value ? `${health.healthy}/${health.expected} 就绪` : '全部就绪'
})
const healthPercent = computed(() => {
  if (!health.expected) return 0
  return Math.round((health.healthy / health.expected) * 100)
})

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
    getAllCameras()
  ])
  if (results[0].status === 'fulfilled') Object.assign(status, results[0].value || {})
  if (results[1].status === 'fulfilled') {
    const h = results[1].value || {}
    const s = h.summary || {}
    health.tasks = h.tasks || []
    health.expected = s.expected || 0
    health.healthy = s.healthy || 0
  }
  if (results[2].status === 'fulfilled') recentEvents.value = (results[2].value && results[2].value.events) || []
  if (results[3].status === 'fulfilled') cameras.value = results[3].value || []
  loading.value = false
}

watch(() => props.active, (v) => {
  if (v && !loadedOnce) {
    loadedOnce = true
    load(true)
  }
}, { immediate: true })

// 收到新报警时刷新首页统计与最近事件（提示音由 App.vue 统一处理）
const offNewEvent = onWsEvent('new_event', () => {
  if (props.active) load(true)
})
onUnmounted(() => { if (offNewEvent) offNewEvent() })

defineExpose({ load })
</script>

<style lang="scss" scoped>
.panel-scroll { height: 100%; }
.panel-inner {
  padding: calc(var(--status-bar-height) + 28rpx) 24rpx calc(150rpx + env(safe-area-inset-bottom));
}

/* ===== 报头：杂志式大标题 ===== */
.masthead { position: relative; padding: 4rpx 2rpx 34rpx; }
.masthead::after {
  content: '';
  position: absolute; right: -8rpx; top: 60rpx;
  width: 150rpx; height: 120rpx;
  @include tex-dots(.12, 10rpx);
  -webkit-mask-image: linear-gradient(225deg, #000, transparent 72%);
  mask-image: linear-gradient(225deg, #000, transparent 72%);
  pointer-events: none;
}
.mast-top { display: flex; align-items: center; justify-content: space-between; }
.mast-kicker {
  font-family: $font-num;
  font-size: 20rpx; font-weight: 800; letter-spacing: 3rpx;
  color: $text-1;
  border-bottom: 4rpx solid $accent-2;
  padding-bottom: 6rpx;
}
.live-tag {
  display: flex; align-items: center; gap: 8rpx;
  padding: 4rpx 12rpx; background: $danger;
  border: 2rpx solid $border; border-radius: $radius-sm;
  font-family: $font-num; font-size: 17rpx; font-weight: 800; letter-spacing: 1rpx;
  color: #fff;
}
.live-tag.live-off { background: $surface-3; color: $text-2; }
.live-dot { width: 8rpx; height: 8rpx; background: #fff; border-radius: 50%; animation: blink 1.6s steps(1) infinite; }
@keyframes blink { 0%, 60% { opacity: 1; } 61%, 100% { opacity: .25; } }
.mast-title-wrap { margin: 26rpx 0 18rpx; }
.mast-title { display: inline; font-size: 60rpx; font-weight: 800; color: $text-1; letter-spacing: -2rpx; line-height: 1.15; }
.mast-accent { position: relative; z-index: 1; }
.mast-accent::after {
  content: ''; position: absolute; left: 0; right: 0; bottom: 6rpx; height: 16rpx;
  background: $accent-2; z-index: -1; transform: rotate(-1.5deg);
}
.mast-sub { display: flex; align-items: center; gap: 14rpx; }
.mast-date { font-size: 22rpx; color: $text-2; font-weight: 700; letter-spacing: 1rpx; }
.mast-rule { width: 2rpx; height: 20rpx; background: $border; }
.mast-run { font-size: 22rpx; color: $text-2; font-weight: 700; }
.mast-run .num { color: $text-1; font-size: 26rpx; }

/* ===== 主视觉 hero：黑底反白 ===== */
.hero {
  position: relative; overflow: hidden;
  display: flex; align-items: stretch;
  background: $bg-deep;
  border: $stroke solid $border; border-radius: $radius;
  box-shadow: $shadow-card;
  padding: 30rpx 30rpx 26rpx;
  margin-bottom: 8rpx;
}
.hero-grid {
  position: absolute; inset: 0;
  background-image:
    linear-gradient(rgba(0, 168, 143, .13) 1rpx, transparent 1rpx),
    linear-gradient(90deg, rgba(0, 168, 143, .13) 1rpx, transparent 1rpx);
  background-size: 28rpx 28rpx; pointer-events: none;
}
.hero-corner {
  position: absolute; top: 0; right: 0;
  border-style: solid; border-width: 0 40rpx 40rpx 0;
  border-color: transparent $accent-2 transparent transparent;
}
.hero-main { flex: 1; min-width: 0; position: relative; z-index: 1; }
.hero-cap { display: flex; align-items: center; gap: 12rpx; font-size: 19rpx; letter-spacing: 2rpx; color: rgba(255,255,255,.7); font-weight: 700; }
.hero-cap-no { color: $accent-2; font-size: 22rpx; }
.hero-numrow { display: flex; align-items: baseline; gap: 14rpx; margin: 14rpx 0 18rpx; }
.hero-num { font-size: 104rpx; line-height: .95; color: #fff; font-weight: 800; }
.hero-total { font-size: 32rpx; color: rgba(255,255,255,.5); }
.ticks { display: flex; gap: 6rpx; flex-wrap: wrap; }
.tick { width: 10rpx; height: 24rpx; background: rgba(255,255,255,.16); border: 1rpx solid rgba(255,255,255,.2); }
.tick.on { background: $accent-2; border-color: $accent-2; }
.hero-note { display: block; margin-top: 16rpx; font-size: 19rpx; letter-spacing: 1rpx; color: rgba(255,255,255,.6); font-weight: 600; }
.hero-side {
  position: relative; z-index: 1; width: 132rpx; margin-left: 16rpx; padding-left: 20rpx;
  border-left: 2rpx dashed rgba(255,255,255,.22);
  display: flex; flex-direction: column; align-items: flex-end; justify-content: space-between;
}
.hero-ic { width: 44rpx; height: 44rpx; }
.hero-go {
  display: flex; align-items: center; gap: 4rpx; white-space: nowrap;
  background: $accent-2; border: 2rpx solid $border;
  padding: 8rpx 12rpx; border-radius: $radius-sm;
  transform: rotate(-2deg);
}
.hero-go text { font-size: 20rpx; font-weight: 800; color: $border; white-space: nowrap; }
.hero-go image { width: 20rpx; height: 20rpx; flex-shrink: 0; }

/* ===== 分节头 ===== */
.sec-head { display: flex; align-items: center; gap: 12rpx; margin: 40rpx 0 18rpx; }
.sec-no {
  font-family: $font-num; font-size: 20rpx; font-weight: 800;
  color: $border; background: $accent-2; border: 2rpx solid $border;
  padding: 3rpx 8rpx; border-radius: $radius-sm;
}
.sec-txt { font-size: 30rpx; font-weight: 800; color: $text-1; letter-spacing: 1rpx; }
.sec-rule { flex: 1; height: $stroke; background: $border; }
.sec-badge { font-size: 18rpx; font-weight: 800; color: #fff; background: $accent; border: 2rpx solid $border; padding: 3rpx 10rpx; border-radius: $radius-sm; }
.sec-badge.bad { background: $warning; }
.sec-link { display: flex; align-items: center; gap: 4rpx; }
.sec-link text { font-size: 20rpx; font-weight: 800; color: $text-1; }
.sec-link image { width: 22rpx; height: 22rpx; }

/* ===== 运行指标：三张硬边框卡 ===== */
.metric-bar { display: flex; gap: 14rpx; }
.metric {
  flex: 1; min-width: 0;
  display: flex; flex-direction: column;
  background: $surface; border: $stroke solid $border; border-radius: $radius;
  box-shadow: 4rpx 4rpx 0 $border;
  padding: 22rpx 18rpx 18rpx;
  position: relative; overflow: hidden;
}
.metric::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 8rpx; background: $border; }
.metric.m1::before { background: $accent; }
.metric.m2::before { background: $accent-2; }
.metric:active { transform: translate(4rpx, 4rpx); box-shadow: none; }
.metric-num { font-size: 56rpx; line-height: 1; color: $text-1; font-weight: 800; }
.metric-label { font-size: 22rpx; color: $text-1; font-weight: 800; margin-top: 10rpx; }
.metric-note { font-size: 17rpx; color: $text-3; margin-top: 4rpx; }

/* ===== 快捷入口：2x2 贴纸方块 ===== */
.quick-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16rpx; }
.quick {
  position: relative; overflow: hidden;
  display: flex; align-items: center; gap: 16rpx;
  background: $surface; border: $stroke solid $border; border-radius: $radius;
  box-shadow: 4rpx 4rpx 0 $border;
  padding: 24rpx 20rpx;
}
.quick.q1 { background: rgba(0,168,143,.09); }
.quick.q2 { border-color: $accent; box-shadow: 4rpx 4rpx 0 $accent; }
.quick:active { transform: translate(4rpx, 4rpx); box-shadow: none; }
.q-ic {
  width: 64rpx; height: 64rpx; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  background: $accent-2; border: 2rpx solid $border; border-radius: $radius-sm;
}
.q-ic image { width: 36rpx; height: 36rpx; }
.q-label { font-size: 25rpx; font-weight: 800; color: $text-1; letter-spacing: 1rpx; }
.q-arrow { position: absolute; right: 12rpx; top: 8rpx; font-size: 18rpx; color: $text-3; }

/* ===== 健康 ===== */
.health { padding: 26rpx 28rpx 20rpx; }
.health-meter { display: flex; align-items: center; gap: 16rpx; margin-bottom: 10rpx; }
.meter-track { flex: 1; height: 14rpx; background: $surface-3; border: 2rpx solid $border; }
.meter-fill { height: 100%; background: $accent; transition: width .6s ease; }
.meter-fill.bad { background: $warning; }
.meter-num { font-size: 24rpx; color: $text-1; font-weight: 800; }
.health-row { display: flex; align-items: center; gap: 14rpx; padding: 16rpx 0; border-top: 2rpx solid $line-2; }
.h-sq { width: 14rpx; height: 14rpx; flex-shrink: 0; border: 2rpx solid $border; }
.h-sq.ok { background: $accent; }
.h-sq.bad { background: $danger; }
.h-name { font-size: 26rpx; color: $text-1; font-weight: 700; }
.h-stage { font-size: 19rpx; color: $text-3; letter-spacing: 1rpx; flex-shrink: 0; font-weight: 700; }
.health-none { padding: 18rpx 0 6rpx; font-size: 23rpx; color: $text-3; border-top: 2rpx solid $line-2; font-weight: 700; }
</style>
