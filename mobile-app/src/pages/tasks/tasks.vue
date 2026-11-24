<template>
  <view class="page-wrap">
    <nav-bar title="任务管理">
      <template #right>
        <view class="nav-add" @tap="goEdit()">
          <image :src="icon('plus', '#111210', 2.6)" style="width:40rpx;height:40rpx;" />
        </view>
      </template>
    </nav-bar>

    <view class="tasks-inner">
      <!-- 搜索框 -->
      <view class="input-box search-box">
        <image :src="icon('search', '#55584F', 2.2)" style="width:34rpx;height:34rpx;" />
        <input class="input search-input" v-model="keyword" placeholder="搜索任务名称 / 算法"
          placeholder-class="placeholder" confirm-type="search" @confirm="reload" />
        <view v-if="keyword" @tap="keyword = ''; reload()">
          <image :src="icon('close', '#55584F', 2.4)" style="width:32rpx;height:32rpx;" />
        </view>
      </view>

      <!-- 任务列表 -->
      <view class="card card-press task-card" v-for="t in tasks" :key="t.id" @tap="goEdit(t.id)">
        <view class="task-top">
          <text class="task-name t-ellipsis flex-1">{{ t.task_name }}</text>
          <view class="badge" :class="'badge-' + st(t).type">{{ st(t).text }}</view>
        </view>

        <view class="task-chips">
          <view class="chip" v-if="t.algorithm">
            <image :src="icon('zap', '#111210', 2.4)" style="width:24rpx;height:24rpx;" />
            <text>{{ t.algorithm }}</text>
          </view>
          <view class="chip">
            <image :src="icon('video', '#111210', 2.4)" style="width:24rpx;height:24rpx;" />
            <text>{{ t.channel_count || 0 }} 通道</text>
          </view>
          <view class="chip" v-if="t.conf_threshold != null">
            <image :src="icon('target', '#E0930A', 2.4)" style="width:24rpx;height:24rpx;" />
            <text>置信度 {{ Math.round(t.conf_threshold * 100) }}%</text>
          </view>
        </view>

        <view class="task-foot">
          <view class="foot-btns" @tap.stop>
            <template v-if="t.status === 'running' || t.status === 'starting'">
              <view class="btn btn-sm btn-ghost" @tap="toggleRun(t)">
                <image :src="icon('stop', '#E8342C', 2.2)" style="width:26rpx;height:26rpx;" />
                <text>停止</text>
              </view>
            </template>
            <template v-else>
              <view class="btn btn-sm btn-primary" @tap="toggleRun(t)">
                <image :src="icon('play', '#FFFFFF', 2.4)" style="width:26rpx;height:26rpx;" />
                <text>运行</text>
              </view>
            </template>
            <view class="btn btn-sm btn-ghost" @tap="goEdit(t.id)">
              <image :src="icon('edit', '#55584F', 2.2)" style="width:26rpx;height:26rpx;" />
              <text>编辑</text>
            </view>
            <view class="btn btn-sm btn-ghost" @tap="goConfig(t)">
              <image :src="icon('settings', '#111210', 2.2)" style="width:26rpx;height:26rpx;" />
              <text>配置</text>
            </view>
            <view class="btn btn-sm btn-danger-ghost" @tap="remove(t)">
              <image :src="icon('trash', '#E8342C', 2.2)" style="width:26rpx;height:26rpx;" />
              <text>删除</text>
            </view>
          </view>
        </view>
      </view>

      <empty-state v-if="!tasks.length && !loading" text="暂无任务，点击右上角创建" icon-name="target" />
      <view class="loading-more" v-if="loading">加载中...</view>
      <view class="loading-more" v-else-if="page * perPage < total">上拉加载更多</view>
      <view style="height: 140rpx;" />
    </view>

    <!-- FAB -->
    <view class="fab safe-bottom" @tap="goEdit()">
      <image :src="icon('plus', '#111210', 3)" style="width:44rpx;height:44rpx;" />
    </view>
  </view>
</template>

<script setup>
import { ref } from 'vue'
import { onPullDownRefresh, onReachBottom, onShow } from '@dcloudio/uni-app'
import navBar from '@/components/nav-bar/nav-bar.vue'
import emptyState from '@/components/empty-state/empty-state.vue'
import icon from '@/utils/icons'
import { get, post, del } from '@/utils/api'
import { taskStatusMeta } from '@/utils/format'

const tasks = ref([])
const keyword = ref('')
const page = ref(1)
const perPage = 20
const total = ref(0)
const loading = ref(false)
const loadedOnce = ref(false)

const st = (t) => taskStatusMeta(t)

onShow(() => {
  if (!loadedOnce.value) {
    loadedOnce.value = true
    load(true)
  } else {
    refreshLoadedPages()
  }
})

/** 从编辑页/配置页返回时刷新数据，但保留用户已加载到第几页 */
async function refreshLoadedPages() {
  const pages = page.value
  const all = []
  try {
    for (let p = 1; p <= pages; p++) {
      const d = await get(`/api/tasks?page=${p}&per_page=${perPage}&search=${encodeURIComponent(keyword.value)}`)
      const list = (d && d.tasks) || []
      if (p === 1) total.value = (d && d.total) || 0
      all.push(...list)
    }
    tasks.value = all
  } catch (e) {
    uni.showToast({ title: e.message || '刷新失败', icon: 'none' })
  }
}

onPullDownRefresh(async () => {
  await load(true)
  uni.stopPullDownRefresh()
})

onReachBottom(() => {
  if (page.value * perPage < total.value && !loading.value) {
    page.value++
    loadMore()
  }
})

async function load(silent) {
  if (!silent) loading.value = true
  page.value = 1
  try {
    const d = await get(`/api/tasks?page=1&per_page=${perPage}&search=${encodeURIComponent(keyword.value)}`)
    tasks.value = (d && d.tasks) || []
    total.value = (d && d.total) || 0
  } catch (e) {
    uni.showToast({ title: e.message || '加载失败', icon: 'none' })
  }
  loading.value = false
}

async function loadMore() {
  try {
    const d = await get(`/api/tasks?page=${page.value}&per_page=${perPage}&search=${encodeURIComponent(keyword.value)}`)
    const list = (d && d.tasks) || []
    const ids = new Set(tasks.value.map(t => t.id))
    tasks.value = tasks.value.concat(list.filter(t => !ids.has(t.id)))
    total.value = (d && d.total) || 0
  } catch (e) { /* 静默 */ }
}

function reload() {
  load()
}

function goEdit(id) {
  uni.navigateTo({ url: '/pages/tasks/edit' + (id ? '?id=' + id : '') })
}

function goConfig(t) {
  uni.navigateTo({ url: '/pages/tasks/config?id=' + t.id })
}

async function toggleRun(t) {
  const running = t.status === 'running' || t.status === 'starting'
  uni.showLoading({ title: running ? '停止中...' : '启动中...' })
  try {
    await post(`/api/tasks/${t.id}/${running ? 'stop' : 'start'}`)
    t.status = running ? 'stopped' : 'starting'
    uni.showToast({ title: running ? '已停止' : '启动指令已发送', icon: 'none' })
  } catch (e) {
    uni.showToast({ title: e.message || '操作失败', icon: 'none' })
  }
  uni.hideLoading()
}

function remove(t) {
  uni.showModal({
    title: '删除任务',
    content: `确定删除「${t.task_name}」吗？此操作不可恢复。`,
    confirmColor: '#E8342C',
    success: async (r) => {
      if (!r.confirm) return
      try {
        await del(`/api/tasks/${t.id}`)
        tasks.value = tasks.value.filter(x => x.id !== t.id)
        total.value = Math.max(0, total.value - 1)
        uni.showToast({ title: '已删除', icon: 'none' })
      } catch (e) {
        uni.showToast({ title: e.message || '删除失败', icon: 'none' })
      }
    }
  })
}
</script>

<style lang="scss" scoped>
.tasks-inner { padding: 24rpx; }
.search-box { margin-bottom: 24rpx; gap: 16rpx; }
.search-input { font-size: 26rpx; }

.task-card { padding: 24rpx; }
.task-top { display: flex; align-items: center; gap: 16rpx; }
.task-name { font-size: 30rpx; font-weight: 700; color: $text-1; }

.task-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 12rpx;
  margin: 20rpx 0 8rpx;
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: 8rpx;
  background: $surface-2;
  border: 1rpx solid $border;
  border-radius: 8rpx;
  padding: 6rpx 14rpx;
  font-size: 22rpx;
  color: $text-2;
}

.task-foot {
  display: flex;
  justify-content: flex-end;
  margin-top: 20rpx;
  padding-top: 20rpx;
  border-top: 1rpx solid $border;
}
.foot-btns { display: flex; gap: 14rpx; }
.btn-sm text { margin-left: 8rpx; }

/* 新建按钮：墨块方印 + 印刷偏移阴影，不用呼吸光晕 */
.fab {
  position: fixed;
  right: 32rpx;
  bottom: calc(52rpx + env(safe-area-inset-bottom));
  width: 104rpx;
  height: 104rpx;
  border-radius: 10rpx;
  background: #00A88F;
  box-shadow: 0 8rpx 0 rgba(0, 0, 0, .18);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
  transition: transform .1s, box-shadow .1s;
}
.fab:active { transform: translateY(4rpx); box-shadow: 0 4rpx 0 rgba(0, 0, 0, .18); }
/* #ifdef H5 */
/* 仅在宽屏桌面端把悬浮元素对齐到 750px 内容列的右边缘；手机上保持贴边 */
@media (min-width: 800px) {
  .fab {
    right: calc(50% - 375px + 32rpx);
  }
}
/* #endif */
</style>
