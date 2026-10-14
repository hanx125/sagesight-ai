<template>
  <scroll-view scroll-y class="panel-scroll" :show-scrollbar="false" refresher-enabled
    :refresher-triggered="refreshing" @refresherrefresh="onRefresh">
    <view class="panel-inner">
      <!-- 用户信息卡 -->
      <view class="hero">
        <view class="avatar">{{ avatarChar }}</view>
        <view class="hero-meta">
          <view class="hero-row">
            <text class="hero-name">{{ userName }}</text>
            <view class="hero-badge">{{ roleText }}</view>
          </view>
          <text class="hero-uid">@{{ accountName }} · ID {{ userId || '-' }}</text>
          <text class="hero-sub" v-if="joinedAt">账号创建于 {{ joinedAt }}</text>
        </view>
      </view>

      <!-- 账号与安全 -->
      <text class="group-label">账号与安全</text>
      <view class="card menu">
        <view class="menu-item" @tap="openProfileEdit">
          <view class="flex-row" style="gap: 20rpx;">
            <view class="menu-ic" style="background: rgba(10,95,217,.10);">
              <image :src="icon('edit', '#0a5fd9', 2.2)" />
            </view>
            <text class="menu-label">编辑资料</text>
          </view>
          <view class="flex-row" style="gap: 12rpx; min-width: 0;">
            <text class="menu-val">{{ emailText }}</text>
            <image :src="icon('chevron-right', '#98A2B3', 2.4)" style="width:32rpx;height:32rpx;flex-shrink:0;" />
          </view>
        </view>
        <view class="menu-item" @tap="openPwdEdit">
          <view class="flex-row" style="gap: 20rpx;">
            <view class="menu-ic" style="background: rgba(180,83,9,.10);">
              <image :src="icon('shield', '#B45309', 2.2)" />
            </view>
            <text class="menu-label">修改密码</text>
          </view>
          <image :src="icon('chevron-right', '#98A2B3', 2.4)" style="width:32rpx;height:32rpx;" />
        </view>
      </view>

      <!-- 通知 -->
      <text class="group-label">通知</text>
      <view class="card menu">
        <view class="menu-item">
          <view class="flex-row" style="gap: 20rpx;">
            <view class="menu-ic" style="background: rgba(16,185,129,.10);">
              <image :src="icon('volume', '#10B981', 2.2)" />
            </view>
            <text class="menu-label">告警提示音</text>
          </view>
          <switch :checked="alertSoundOn" color="#0a5fd9" style="transform:scale(.8);"
            @change="onAlertSoundChange" />
        </view>
        <view class="menu-item" @tap="onPushTap">
          <view class="flex-row" style="gap: 20rpx;">
            <view class="menu-ic" style="background: rgba(245,158,11,.12);">
              <image :src="icon('bell', '#F59E0B', 2.2)" />
            </view>
            <text class="menu-label">事件推送</text>
          </view>
          <view class="flex-row" style="gap: 12rpx;">
            <view class="badge" :class="pushStatus.enabled ? 'badge-success' : 'badge-gray'">
              {{ pushStatus.enabled ? '服务已开启' : '服务未开启' }}
            </view>
            <image :src="icon('chevron-right', '#98A2B3', 2.4)" style="width:32rpx;height:32rpx;" />
          </view>
        </view>
        <!-- #ifdef APP-PLUS -->
        <view class="menu-item" v-if="bound" @tap="toggleBind">
          <view class="flex-row" style="gap: 20rpx;">
            <view class="menu-ic" style="background: rgba(21,112,239,.10);">
              <image :src="icon('phone', '#1570EF', 2.2)" />
            </view>
            <text class="menu-label">本机推送绑定</text>
          </view>
          <view class="flex-row" style="gap: 12rpx;">
            <view class="badge badge-success">已绑定</view>
            <image :src="icon('chevron-right', '#98A2B3', 2.4)" style="width:32rpx;height:32rpx;" />
          </view>
        </view>
        <text class="t-dim push-cid t-ellipsis" v-if="clientId">CID: {{ clientId }}</text>
        <!-- #endif -->
      </view>

      <!-- 通用 -->
      <text class="group-label">通用</text>
      <view class="card menu">
        <view class="menu-item" @tap="clearCache">
          <view class="flex-row" style="gap: 20rpx;">
            <view class="menu-ic" style="background: rgba(217,45,32,.10);">
              <image :src="icon('trash', '#D92D20', 2.2)" />
            </view>
            <text class="menu-label">清除缓存</text>
          </view>
          <image :src="icon('chevron-right', '#98A2B3', 2.4)" style="width:32rpx;height:32rpx;" />
        </view>
        <view class="menu-item" @tap="checkUpdate">
          <view class="flex-row" style="gap: 20rpx;">
            <view class="menu-ic" style="background: rgba(16,185,129,.10);">
              <image :src="icon('refresh', '#10B981', 2.2)" />
            </view>
            <text class="menu-label">检查更新</text>
          </view>
          <view class="flex-row" style="gap: 12rpx;">
            <text class="menu-val">v{{ appVersion }}</text>
            <image :src="icon('chevron-right', '#98A2B3', 2.4)" style="width:32rpx;height:32rpx;" />
          </view>
        </view>
        <view class="menu-item" @tap="aboutShow = true">
          <view class="flex-row" style="gap: 20rpx;">
            <view class="menu-ic" style="background: rgba(71,84,103,.10);">
              <image :src="icon('info', '#475467', 2.2)" />
            </view>
            <text class="menu-label">关于睿观</text>
          </view>
          <image :src="icon('chevron-right', '#98A2B3', 2.4)" style="width:32rpx;height:32rpx;" />
        </view>
      </view>

      <!-- 退出登录 -->
      <view class="btn btn-danger-ghost logout-btn" @tap="doLogout">
        <image :src="icon('logout', '#D92D20')" style="width:34rpx;height:34rpx;" />
        <text style="margin-left: 12rpx;">退出登录</text>
      </view>

      <view style="height: 60rpx;" />

      <!-- 编辑资料弹窗 -->
      <view class="mask" v-if="editShow" @tap="editShow = false">
        <view class="modal-card" @tap.stop>
          <text class="modal-title">编辑资料</text>
          <view class="field">
            <text class="field-label">昵称</text>
            <input class="field-input" v-model="editForm.display_name" placeholder="请输入昵称"
              placeholder-class="ph" />
          </view>
          <view class="field">
            <text class="field-label">邮箱</text>
            <input class="field-input" v-model="editForm.email" placeholder="请输入邮箱"
              placeholder-class="ph" />
          </view>
          <view class="modal-actions">
            <view class="btn btn-ghost flex-1" @tap="editShow = false"><text>取消</text></view>
            <view class="btn btn-primary flex-1" @tap="saveProfile"><text>保存</text></view>
          </view>
        </view>
      </view>

      <!-- 修改密码弹窗 -->
      <view class="mask" v-if="pwdShow" @tap="pwdShow = false">
        <view class="modal-card" @tap.stop>
          <text class="modal-title">修改密码</text>
          <view class="field">
            <text class="field-label">新密码</text>
            <input class="field-input" password v-model="pwdForm.p1" placeholder="至少 6 位"
              placeholder-class="ph" />
          </view>
          <view class="field">
            <text class="field-label">确认新密码</text>
            <input class="field-input" password v-model="pwdForm.p2" placeholder="再次输入新密码"
              placeholder-class="ph" />
          </view>
          <view class="modal-actions">
            <view class="btn btn-ghost flex-1" @tap="pwdShow = false"><text>取消</text></view>
            <view class="btn btn-primary flex-1" @tap="savePassword"><text>确认修改</text></view>
          </view>
        </view>
      </view>

      <!-- 关于弹窗 -->
      <view class="mask" v-if="aboutShow" @tap="aboutShow = false">
        <view class="modal-card about-card" @tap.stop>
          <view class="about-logo">
            <image :src="icon('logo', '#FFFFFF')" style="width:56rpx;height:56rpx;" />
          </view>
          <text class="about-name">睿观 SageSight</text>
          <text class="about-ver">AI 视频智能检测平台 · v{{ appVersion }}</text>
          <text class="about-desc">面向安防场景的智能视频分析系统，支持区域入侵、徘徊检测、目标跟踪等多种 AI 检测任务。</text>
          <view class="btn btn-primary modal-btn" @tap="aboutShow = false"><text>知道了</text></view>
        </view>
      </view>
    </view>
  </scroll-view>
</template>

<script setup>
import { ref, reactive, computed, watch } from 'vue'
import icon from '@/utils/icons'
import { get, put, post } from '@/utils/api'
import { state, clearAuth, setAuth } from '@/utils/store'
import { isAlertSoundOn, setAlertSoundOn, loadAlertSound } from '@/utils/alert-sound'
import { closeWs } from '@/utils/ws'

const props = defineProps({ active: Boolean })
const refreshing = ref(false)
const pushStatus = reactive({ enabled: false, configured: false })
const bound = ref(false)
const clientId = ref('')
const alertSoundOn = ref(isAlertSoundOn())
const editShow = ref(false)
const pwdShow = ref(false)
const aboutShow = ref(false)
const editForm = reactive({ display_name: '', email: '' })
const pwdForm = reactive({ p1: '', p2: '' })
const appVersion = '1.0.0'
let loadedOnce = false

const userName = computed(() => (state.user && (state.user.display_name || state.user.username)) || '-')
const accountName = computed(() => (state.user && state.user.username) || '-')
const userId = computed(() => (state.user && state.user.id) || '')
const avatarChar = computed(() => userName.value.charAt(0).toUpperCase())
const roleText = computed(() => {
  const r = state.user && state.user.role
  if (r === 'admin') return '管理员'
  if (r === 'operator') return '操作员'
  return r || '用户'
})
const joinedAt = computed(() => {
  const c = state.user && state.user.created_at
  return c ? String(c).slice(0, 10) : ''
})
const emailText = computed(() => (state.user && state.user.email) || '未设置')

async function load(silent) {
  try {
    const d = await get('/api/push/status')
    pushStatus.enabled = !!(d && d.enabled)
    pushStatus.configured = !!(d && d.configured)
  } catch (e) { if (!silent) uni.showToast({ title: e.message, icon: 'none' }) }
  if (alertSoundOn.value) loadAlertSound()
  // #ifdef APP-PLUS
  await ensureClientId()
  if (clientId.value) {
    try {
      const st = await get('/api/push/bind/status')
      bound.value = !!(st && st.bound)
    } catch (e) { /* 忽略：接口不存在时按未绑定处理 */ }
  }
  // #endif
}

function onAlertSoundChange(e) {
  alertSoundOn.value = e.detail.value
  setAlertSoundOn(e.detail.value)
  if (e.detail.value) loadAlertSound()
}

function openProfileEdit() {
  editForm.display_name = (state.user && state.user.display_name) || ''
  editForm.email = (state.user && state.user.email) || ''
  editShow.value = true
}

function openPwdEdit() {
  pwdForm.p1 = ''
  pwdForm.p2 = ''
  pwdShow.value = true
}

async function saveProfile() {
  const name = editForm.display_name.trim()
  const email = editForm.email.trim()
  if (!name) { uni.showToast({ title: '昵称不能为空', icon: 'none' }); return }
  if (email && !/^[\w.+-]+@[\w-]+\.[\w.]+$/.test(email)) { uni.showToast({ title: '邮箱格式不正确', icon: 'none' }); return }
  try {
    const d = await put(`/api/auth/users/${userId.value}`, { display_name: name, email })
    if (d && d.user) setAuth(state.token, d.user)
    editShow.value = false
    uni.showToast({ title: '资料已更新', icon: 'success' })
  } catch (e) {
    uni.showToast({ title: e.message, icon: 'none' })
  }
}

async function savePassword() {
  if (!pwdForm.p1 || pwdForm.p1.length < 6) { uni.showToast({ title: '密码至少 6 位', icon: 'none' }); return }
  if (pwdForm.p1 !== pwdForm.p2) { uni.showToast({ title: '两次输入的密码不一致', icon: 'none' }); return }
  try {
    await put(`/api/auth/users/${userId.value}`, { password: pwdForm.p1 })
    pwdShow.value = false
    uni.showToast({ title: '密码已修改', icon: 'success' })
  } catch (e) {
    uni.showToast({ title: e.message, icon: 'none' })
  }
}

function onPushTap() {
  // #ifdef APP-PLUS
  if (bound.value) toggleBind()
  else if (!pushStatus.configured) uni.showToast({ title: '推送服务未在服务端配置', icon: 'none' })
  else toggleBind()
  return
  // #endif
  uni.showToast({ title: '推送功能需在 App 端使用', icon: 'none' })
}

function clearCache() {
  // 保留登录态与服务端地址，仅清理其余本地缓存数据
  const keep = ['ss_token', 'ss_user', 'ss_server_base']
  try {
    const info = uni.getStorageInfoSync()
    let n = 0
    ;(info.keys || []).forEach((k) => {
      if (keep.indexOf(k) === -1) { uni.removeStorageSync(k); n++ }
    })
    uni.showToast({ title: n ? `已清理 ${n} 项缓存` : '暂无可清理缓存', icon: 'none' })
  } catch (e) {
    uni.showToast({ title: '清理失败', icon: 'none' })
  }
}

function checkUpdate() {
  uni.showToast({ title: `当前已是最新版本 v${appVersion}`, icon: 'none' })
}

// #ifdef APP-PLUS
function ensureClientId() {
  return new Promise((resolve) => {
    if (clientId.value) return resolve()
    uni.getPushClientId({
      success: (res) => { clientId.value = res.cid; resolve() },
      fail: () => resolve()
    })
  })
}

async function toggleBind() {
  try {
    if (bound.value) {
      await post('/api/push/unbind', { client_id: clientId.value })
      bound.value = false
      uni.showToast({ title: '已解绑', icon: 'success' })
    } else {
      await ensureClientId()
      if (!clientId.value) {
        uni.showToast({ title: '获取推送 ClientId 失败，请确认 App 已开通 UniPush', icon: 'none' })
        return
      }
      const platform = (uni.getSystemInfoSync().platform || 'android').toLowerCase()
      await post('/api/push/bind', { client_id: clientId.value, device_type: platform })
      bound.value = true
      uni.showToast({ title: '绑定成功', icon: 'success' })
    }
  } catch (e) {
    uni.showToast({ title: e.message, icon: 'none' })
  }
}
// #endif

function onRefresh() {
  refreshing.value = true
  load(true).finally(() => { refreshing.value = false })
}

function doLogout() {
  uni.showModal({
    title: '退出登录',
    content: '确定要退出当前账号吗？',
    confirmColor: '#D92D20',
    success: async (res) => {
      if (!res.confirm) return
      try { await post('/api/auth/app_logout') } catch (e) { /* 忽略 */ }
      closeWs()
      clearAuth()
      uni.reLaunch({ url: '/pages/login/login' })
    }
  })
}

watch(() => props.active, (v) => {
  if (v && !loadedOnce) {
    loadedOnce = true
    load(true)
  }
}, { immediate: true })
</script>

<style lang="scss" scoped>
.panel-scroll { height: 100%; }
.panel-inner { padding: calc(var(--status-bar-height) + 40rpx) 24rpx calc(160rpx + env(safe-area-inset-bottom)); }

/* 用户信息卡 */
.hero {
  display: flex;
  align-items: center;
  gap: 28rpx;
  padding: 40rpx 32rpx;
  border-radius: $radius-lg;
  background: $grad-primary;
  box-shadow: $shadow-glow;
}
.avatar {
  width: 116rpx;
  height: 116rpx;
  border-radius: 50%;
  background: rgba(255, 255, 255, .18);
  border: 2rpx solid rgba(255, 255, 255, .35);
  color: #FFFFFF;
  font-size: 46rpx;
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.hero-meta { display: flex; flex-direction: column; gap: 8rpx; min-width: 0; }
.hero-row { display: flex; align-items: center; gap: 16rpx; }
.hero-name {
  font-size: 36rpx;
  font-weight: 800;
  color: #FFFFFF;
  max-width: 320rpx;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hero-badge {
  padding: 4rpx 16rpx;
  border-radius: 999rpx;
  background: rgba(255, 255, 255, .2);
  color: #FFFFFF;
  font-size: 20rpx;
  flex-shrink: 0;
}
.hero-uid { font-size: 24rpx; color: rgba(255, 255, 255, .82); }
.hero-sub { font-size: 20rpx; color: rgba(255, 255, 255, .6); }

/* 分组标题 */
.group-label {
  display: block;
  font-size: 24rpx;
  color: $text-3;
  margin: 32rpx 8rpx 14rpx;
}

/* 菜单 */
.menu { padding: 8rpx 24rpx; }
.menu-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 26rpx 0;
  border-bottom: 1rpx solid $border;
}
.menu-item:last-child { border-bottom: none; }
.menu-item:active { opacity: .7; }
.menu-ic {
  width: 64rpx; height: 64rpx;
  border-radius: 18rpx;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}
.menu-ic image { width: 34rpx; height: 34rpx; }
.menu-label { font-size: 28rpx; color: $text-1; }
.menu-val {
  font-size: 22rpx;
  color: $text-3;
  max-width: 280rpx;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.push-cid { font-size: 20rpx; padding: 0 0 20rpx; display: block; }

/* 退出登录 */
.logout-btn { margin-top: 40rpx; }

/* 弹窗 */
.mask {
  position: fixed;
  left: 0; top: 0; right: 0; bottom: 0;
  background: rgba(16, 24, 40, .55);
  z-index: 999;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 48rpx;
}
.modal-card {
  width: 100%;
  max-width: 600rpx;
  background: $surface;
  border-radius: $radius-lg;
  padding: 40rpx 32rpx 32rpx;
  box-shadow: 0 24rpx 60rpx rgba(16, 24, 40, .25);
}
.modal-title { display: block; font-size: 32rpx; font-weight: 700; color: $text-1; margin-bottom: 28rpx; }
.field { margin-bottom: 24rpx; }
.field-label { display: block; font-size: 24rpx; color: $text-2; margin-bottom: 10rpx; }
.field-input {
  height: 84rpx;
  padding: 0 24rpx;
  border-radius: $radius-sm;
  background: $surface-2;
  font-size: 28rpx;
  color: $text-1;
}
.ph { color: $text-3; }
.modal-actions { display: flex; gap: 20rpx; margin-top: 32rpx; }

/* 关于弹窗 */
.about-card { display: flex; flex-direction: column; align-items: center; text-align: center; }
.about-logo {
  width: 108rpx;
  height: 108rpx;
  border-radius: 28rpx;
  background: $grad-primary;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: $shadow-glow;
  margin-bottom: 20rpx;
}
.about-name { font-size: 32rpx; font-weight: 800; color: $text-1; }
.about-ver { font-size: 22rpx; color: $text-3; margin: 8rpx 0 16rpx; }
.about-desc { font-size: 24rpx; line-height: 1.7; color: $text-2; margin-bottom: 8rpx; }
.modal-btn { width: 100%; margin-top: 24rpx; }
</style>
