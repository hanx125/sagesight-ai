<template>
  <view class="panel-scroll">
    <!-- 头部 + 筛选 -->
    <view class="head">
      <text class="hi">事件中心</text>
      <text class="sub">共 {{ total }} 条记录</text>
    </view>
    <view class="chips">
      <view
        v-for="c in chips" :key="c.value"
        class="chip" :class="{ on: statusFilter === c.value }"
        @tap="switchFilter(c.value)"
      >
        <text>{{ c.label }}</text>
      </view>
      <view class="flex-1" />
      <view class="act-btn" @tap="clearEvents">
        <image :src="icon('trash', '#D92D20')" style="width:30rpx;height:30rpx;" />
      </view>
    </view>

    <scroll-view scroll-y class="scroll-body" :show-scrollbar="false" refresher-enabled
      :refresher-triggered="refreshing" @refresherrefresh="onRefresh"
      @scrolltolower="loadMore">
      <view class="list">
        <event-card v-for="ev in events" :key="ev.event_id" :event="ev" />
      </view>
      <view class="loading-more" v-if="loadingMore">加载中...</view>
      <view class="loading-more" v-else-if="finished && events.length">— 没有更多了 —</view>
      <empty-state v-if="!events.length && !loading" :text="emptyText" icon-name="bell" />
      <view style="height: calc(160rpx + env(safe-area-inset-bottom));" />
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
  if (finished.value) return
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
    confirmColor: '#D92D20',
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
  padding: calc(var(--status-bar-height) + 40rpx) 32rpx 20rpx;
}
.hi { font-size: 40rpx; font-weight: 800; color: $text-1; display: block; }
.sub { font-size: 24rpx; color: $text-3; margin-top: 8rpx; }
.chips {
  display: flex;
  align-items: center;
  gap: 16rpx;
  padding: 0 32rpx 20rpx;
}
.chip {
  padding: 10rpx 28rpx;
  border-radius: 999rpx;
  background: $surface;
  border: 1rpx solid $border;
  font-size: 24rpx;
  color: $text-2;
  transition: all .15s;
}
.chip.on {
  background: rgba(10, 95, 217, .10);
  border-color: rgba(10, 95, 217, .40);
  color: $primary;
  font-weight: 600;
}
.act-btn {
  width: 64rpx;
  height: 64rpx;
  border-radius: 999rpx;
  background: $surface;
  border: 1rpx solid rgba(217, 45, 32, .25);
  display: flex;
  align-items: center;
  justify-content: center;
}
.act-btn image { width: 32rpx; height: 32rpx; }
.scroll-body { flex: 1; min-height: 0; }
.list { padding: 0 24rpx; }
</style>
