<template>
  <view class="panel-scroll">
    <!-- 报头 -->
    <view class="head">
      <view class="head-l">
        <text class="micro">EVENT LOG</text>
        <text class="head-title">事件中心</text>
        <text class="head-sub">累计 {{ total }} 条记录</text>
      </view>
      <view class="sq-btn" @tap="clearEvents">
        <image :src="icon('trash', '#E8342C', 2.2)" />
      </view>
    </view>

    <!-- 筛选：下划线页签 -->
    <view class="tabs">
      <view
        v-for="c in chips" :key="c.value"
        class="tab" :class="{ on: statusFilter === c.value }"
        @tap="switchFilter(c.value)"
      >
        <text class="tab-txt">{{ c.label }}</text>
        <view class="tab-line" v-if="statusFilter === c.value" />
      </view>
    </view>

    <scroll-view scroll-y class="scroll-body" :show-scrollbar="false" refresher-enabled
      :refresher-triggered="refreshing" @refresherrefresh="onRefresh"
      @scrolltolower="loadMore">
      <view class="list">
        <view class="group" v-for="g in groups" :key="g.key">
          <view class="group-head">
            <view class="g-sq" />
            <text class="g-label">{{ g.label }}</text>
            <view class="g-rule" />
            <text class="g-count num">{{ g.items.length }}</text>
          </view>
          <event-card v-for="ev in g.items" :key="ev.event_id" :event="ev" />
        </view>
      </view>
      <view class="loading-more" v-if="loadingMore">加载中</view>
      <view class="loading-more" v-else-if="finished && events.length">— 已到最后 —</view>
      <empty-state v-if="!events.length && !loading" :text="emptyText" icon-name="bell" />
      <view style="height: calc(150rpx + env(safe-area-inset-bottom));" />
    </scroll-view>
  </view>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import icon from '@/utils/icons'
import { get, post } from '@/utils/api'
import eventCard from '@/components/event-card/event-card.vue'
import emptyState from '@/components/empty-state/empty-state.vue'

const props = defineProps({ active: Boolean })
const emit = defineEmits(['changed'])

const chips = [
  { label: '全部', value: '' },
  { label: '待确认', value: 'initial' },
  { label: '已确认', value: 'final' }
]
const statusFilter = ref('')
const events = ref([])
const page = ref(1)
const total = ref(0)
const PER = 20
const loading = ref(false)
const loadingMore = ref(false)
const refreshing = ref(false)
let loadedOnce = false

const finished = computed(() => events.value.length >= total.value)
const emptyText = computed(() => statusFilter.value === 'initial' ? '暂无待确认事件' : '暂无事件记录')

/** 按天分组，给列表一个"日志流"的时间骨架 */
const groups = computed(() => {
  const out = []
  const map = {}
  const today = startOfDay(new Date()).getTime()
  events.value.forEach((ev) => {
    const d = parseDate(ev.created_at)
    const key = d ? startOfDay(d).getTime() : 0
    if (!map[key]) {
      let label = '未知日期'
      if (d) {
        if (key === today) label = '今天'
        else if (key === today - 86400000) label = '昨天'
        else {
          const mm = d.getMonth() + 1
          const dd = d.getDate()
          label = `${mm} 月 ${dd} 日`
        }
      }
      map[key] = { key: String(key), label, items: [] }
      out.push(map[key])
    }
    map[key].items.push(ev)
  })
  return out
})

function parseDate(ts) {
  if (!ts) return null
  const s = String(ts).replace('T', ' ')
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})[ ](\d{2}):(\d{2}):(\d{2})/)
  if (m) return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6])
  const d = new Date(s)
  return isNaN(d.getTime()) ? null : d
}
function startOfDay(d) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate())
}

async function load(reset) {
  if (!reset && loadingMore.value) return
  if (reset) {
    if (loading.value) return
    loading.value = true
    page.value = 1
  } else {
    loadingMore.value = true
  }
  try {
    const d = await get(`/api/events?page=${page.value}&per_page=${PER}${statusFilter.value ? '&status=' + statusFilter.value : ''}`)
    const list = (d && d.events) || []
    total.value = (d && d.total) || 0
    events.value = reset ? list : events.value.concat(list)
    emit('changed', total.value)
  } catch (e) {
    uni.showToast({ title: e.message, icon: 'none' })
  }
  loading.value = false
  loadingMore.value = false
}

function loadMore() {
  // 必须在 page++ 之前判断，否则上一页还在加载时会先自增再被 load 的 guard 拦下，导致跳过一页
  if (loadingMore.value || finished.value) return
  page.value += 1
  load(false)
}

function switchFilter(v) {
  if (statusFilter.value === v) return
  statusFilter.value = v
  load(true)
}

function onRefresh() {
  refreshing.value = true
  load(true).finally(() => { refreshing.value = false })
}

function clearEvents() {
  uni.showModal({
    title: '清理事件',
    content: '将删除 7 天前的所有事件记录及图片，此操作不可恢复。',
    confirmColor: '#E8342C',
    success: (res) => {
      if (!res.confirm) return
      post('/api/events/clear?days=7').then(() => {
        uni.showToast({ title: '清理完成', icon: 'success' })
        load(true)
      }).catch(e => uni.showToast({ title: e.message, icon: 'none' }))
    }
  })
}

watch(() => props.active, (v) => {
  if (v && !loadedOnce) {
    loadedOnce = true
    load(true)
  }
}, { immediate: true })

defineExpose({ reload: () => load(true) })
</script>

<style lang="scss" scoped>
.panel-scroll {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.head {
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  padding: calc(var(--status-bar-height) + 30rpx) 24rpx 18rpx;
}
.head::after {
  content: '';
  position: absolute;
  right: -30rpx;
  top: 0;
  width: 210rpx;
  height: 140rpx;
  @include tex-dots(.09, 11rpx);
  -webkit-mask-image: linear-gradient(228deg, #000 0%, transparent 72%);
  mask-image: linear-gradient(228deg, #000 0%, transparent 72%);
  pointer-events: none;
}
.head-title {
  display: block;
  font-size: 46rpx;
  font-weight: 800;
  color: $text-1;
  letter-spacing: -1rpx;
  margin: 10rpx 0 6rpx;
}
.head-sub { font-size: 21rpx; color: $text-3; }
.sq-btn {
  width: 64rpx;
  height: 64rpx;
  border: 2rpx solid $border;
  border-radius: $radius-sm;
  background: $surface;
  box-shadow: 3rpx 3rpx 0 $border;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.sq-btn image { width: 30rpx; height: 30rpx; }

/* 页签 */
.tabs {
  display: flex;
  gap: 40rpx;
  padding: 0 24rpx;
  border-bottom: $stroke solid $border;
}
.tab {
  position: relative;
  padding: 0 0 18rpx;
}
.tab-txt {
  font-size: 26rpx;
  color: $text-3;
  letter-spacing: 1rpx;
  transition: color .15s;
}
.tab.on .tab-txt { color: $text-1; font-weight: 800; }
.tab.on .tab-txt::before { content: ''; display: inline-block; width: 8rpx; height: 8rpx; background: $accent-2; border: 2rpx solid $border; margin-right: 8rpx; vertical-align: middle; }
.tab-line {
  position: absolute;
  left: 0; right: 0; bottom: -1rpx;
  height: 6rpx;
  background: $accent;
}

.scroll-body { flex: 1; min-height: 0; }
.list { padding: 20rpx 24rpx 0; }

/* 日期分组 */
.group-head {
  display: flex;
  align-items: center;
  gap: 12rpx;
  padding: 10rpx 0 14rpx;
}
.g-sq { width: 8rpx; height: 8rpx; background: $text-1; }
.g-label {
  font-size: 21rpx;
  font-weight: 800;
  color: $text-2;
  letter-spacing: 2rpx;
}
/* 日期分组：中间那条线做成刻度尺，像时间轴上的标尺 */
.g-rule {
  flex: 1;
  height: 6rpx;
  @include tex-ruler(rgba(0, 168, 143, .22), 10rpx);
  background-repeat: repeat-x;
  background-position: 0 center;
}
.g-count { font-size: 19rpx; color: $text-3; }
</style>
