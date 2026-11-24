<template>
  <view class="page-wrap">
    <nav-bar title="摄像机管理">
      <template #right>
        <view class="nav-add" @tap="goEdit()">
          <image :src="icon('plus', '#111210', 2.6)" style="width:40rpx;height:40rpx;" />
        </view>
      </template>
    </nav-bar>

    <view class="cams-inner">
      <view class="card cam-card" v-for="(c, i) in cameras" :key="c.id">
        <!-- 快照 -->
        <view class="snap-wrap" @tap="preview(c)">
          <image v-if="snapUrl(c, i)" class="snap" :src="snapUrl(c, i)" mode="aspectFill"
            @error="onSnapError(c.id)" />
          <view class="snap-fallback" v-else>
            <image :src="icon('video', '#55584F', 2)" style="width:64rpx;height:64rpx;" />
          </view>
          <view class="online-tag" :class="isOnline(c) ? 'tag-on' : 'tag-off'">
            <view class="tag-dot" />
            <text>{{ isOnline(c) ? '在线' : '离线' }}</text>
          </view>
        </view>

        <!-- 信息 -->
        <view class="cam-body">
          <view class="cam-top">
            <text class="cam-name t-ellipsis flex-1">{{ c.name || ('摄像机 #' + c.id) }}</text>
            <view class="badge badge-cyan" v-if="c.ai_detection">AI</view>
          </view>
          <text class="cam-addr t-ellipsis">{{ c.ip ? c.ip + (c.port ? ':' + c.port : '') : (c.source || '-') }}</text>

          <view class="cam-foot">
            <view class="foot-btns">
              <view class="btn btn-sm btn-primary" @tap="play(c)">
                <image :src="icon('play', '#FFFFFF', 2.4)" style="width:26rpx;height:26rpx;" />
                <text>直播</text>
              </view>
              <view class="btn btn-sm btn-ghost" @tap="goEdit(c.id)">
                <image :src="icon('edit', '#55584F', 2.2)" style="width:26rpx;height:26rpx;" />
                <text>编辑</text>
              </view>
              <view class="btn btn-sm btn-danger-ghost" @tap="remove(c)">
                <image :src="icon('trash', '#E8342C', 2.2)" style="width:26rpx;height:26rpx;" />
                <text>删除</text>
              </view>
            </view>
          </view>
        </view>
      </view>

      <empty-state v-if="!cameras.length && !loading" text="暂无摄像机，点击右上角添加" icon-name="video" />
      <view class="loading-more" v-if="loading">加载中...</view>
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
import { onLoad, onShow, onPullDownRefresh } from '@dcloudio/uni-app'
import navBar from '@/components/nav-bar/nav-bar.vue'
import emptyState from '@/components/empty-state/empty-state.vue'
import icon from '@/utils/icons'
import { getAllCameras, del } from '@/utils/api'
import { resolveUrl, state } from '@/utils/store'

const cameras = ref([])
const loading = ref(false)
const snapFailed = ref({})
const nonce = ref(Date.now())

onLoad(() => load())
onShow(() => {
  if (cameras.value.length) {
    nonce.value = Date.now() // 返回页面时刷新快照
    load(true)
  }
})
onPullDownRefresh(async () => {
  nonce.value = Date.now()
  await load(true)
  uni.stopPullDownRefresh()
})

function isOnline(c) {
  return c.online !== undefined ? c.online : c.status !== 'offline'
}
function snapUrl(c, i) {
  if (snapFailed.value[c.id]) return ''
  const fresh = c._loaded ? `&t=${nonce.value + i}` : ''
  return resolveUrl(`/api/stream/snapshot/${c.id}?access_token=${state.token}${fresh}`)
}
function onSnapError(id) {
  snapFailed.value = { ...snapFailed.value, [id]: true }
}

async function load(silent) {
  if (!silent) loading.value = true
  try {
    const list = await getAllCameras()
    cameras.value = list.map(c => ({ ...c, _loaded: true }))
  } catch (e) {
    uni.showToast({ title: e.message || '加载失败', icon: 'none' })
  }
  loading.value = false
}

function goEdit(id) {
  uni.navigateTo({ url: '/pages/cameras/edit' + (id ? '?id=' + id : '') })
}

function play(c) {
  uni.navigateTo({
    url: '/pages/player/player?id=' + c.id + '&name=' + encodeURIComponent(c.name || '')
  })
}

function preview(c) {
  const u = snapUrl(c, 0)
  if (!u) return
  uni.previewImage({ urls: [u] })
}

function remove(c) {
  uni.showModal({
    title: '删除摄像机',
    content: `确定删除「${c.name || '摄像机 #' + c.id}」吗？`,
    confirmColor: '#E8342C',
    success: async (r) => {
      if (!r.confirm) return
      try {
        await del('/api/cameras/' + c.id)
        cameras.value = cameras.value.filter(x => x.id !== c.id)
        uni.showToast({ title: '已删除', icon: 'none' })
      } catch (e) {
        uni.showToast({ title: e.message || '删除失败', icon: 'none' })
      }
    }
  })
}
</script>

<style lang="scss" scoped>
.cams-inner { padding: 24rpx; }

.cam-card { padding: 0; overflow: hidden; }
.snap-wrap {
  position: relative;
  height: 320rpx;
  background: #111210;
}
.snap { width: 100%; height: 100%; display: block; }
.snap-fallback {
  width: 100%; height: 100%;
  display: flex; align-items: center; justify-content: center;
  background:
    repeating-linear-gradient(45deg, rgba(17, 18, 16, .045) 0, rgba(17, 18, 16, .045) 2rpx, transparent 2rpx, transparent 16rpx),
    #FFFFFF;
}
.online-tag {
  position: absolute;
  left: 16rpx;
  top: 16rpx;
  display: flex;
  align-items: center;
  gap: 8rpx;
  padding: 5rpx 14rpx;
  border-radius: 4rpx;
  font-size: 19rpx;
  font-weight: 700;
  letter-spacing: 1rpx;
}
.tag-on { background: rgba(10, 9, 5, .78); color: #4ADE80; }
.tag-off { background: rgba(10, 9, 5, .78); color: rgba(255, 255, 255, .6); }
.tag-dot { width: 9rpx; height: 9rpx; }
.tag-on .tag-dot { background: $success; box-shadow: 0 0 10rpx rgba(0, 168, 143,.8); }
.tag-off .tag-dot { background: $text-3; }

.cam-body { padding: 24rpx; }
.cam-top { display: flex; align-items: center; gap: 12rpx; }
.cam-name { font-size: 30rpx; font-weight: 700; color: $text-1; }
.cam-addr {
  display: block;
  font-size: 24rpx;
  color: $text-3;
  margin-top: 8rpx;
  font-family: 'Menlo', monospace;
}
.cam-foot {
  display: flex;
  justify-content: flex-end;
  margin-top: 20rpx;
  padding-top: 20rpx;
  border-top: 1rpx solid $border;
}
.foot-btns { display: flex; gap: 14rpx; }
.btn-sm text { margin-left: 8rpx; }

/* 新建按钮：墨块方印 */
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
