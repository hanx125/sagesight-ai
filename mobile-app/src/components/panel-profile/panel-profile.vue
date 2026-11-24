<template>
  <scroll-view scroll-y class="panel-scroll" :show-scrollbar="false" refresher-enabled
    :refresher-triggered="refreshing" @refresherrefresh="onRefresh">
    <view class="panel-inner">
      <!-- 档案头 -->
      <view class="dossier">
        <view class="avatar-box">{{ avatarChar }}</view>
        <view class="dossier-meta">
          <view class="name-row">
            <text class="d-name t-ellipsis">{{ userName }}</text>
            <text class="d-role">{{ roleText }}</text>
          </view>
          <text class="d-id">@{{ accountName }} · ID {{ userId || '-' }}</text>
          <text class="d-join" v-if="joinedAt">加入于 {{ joinedAt }}</text>
        </view>
      </view>

      <!-- 01 账号与安全 -->
      <view class="group-head">
        <text class="idx">01</text>
        <text class="txt">账号与安全</text>
        <view class="rule" />
      </view>
      <view class="rows">
        <view class="row" @tap="openProfileEdit">
          <image class="row-ic" :src="icon('edit', '#111210', 2)" />
          <text class="row-label">编辑资料</text>
          <text class="row-val">{{ emailText }}</text>
          <image class="row-ar" :src="icon('chevron-right', '#55584F', 2.2)" />
        </view>
        <view class="row" @tap="openPwdEdit">
          <image class="row-ic" :src="icon('lock', '#111210', 2)" />
          <text class="row-label">修改密码</text>
          <image class="row-ar" :src="icon('chevron-right', '#55584F', 2.2)" />
        </view>
      </view>

      <!-- 02 通知 -->
      <view class="group-head">
        <text class="idx">02</text>
        <text class="txt">通知</text>
        <view class="rule" />
      </view>
      <view class="rows">
        <view class="row">
          <image class="row-ic" :src="icon('volume', '#111210', 2)" />
          <text class="row-label">告警提示音</text>
          <switch :checked="alertSoundOn" color="#00A88F" style="transform:scale(.72);"
            @change="onAlertSoundChange" />
        </view>
        <view class="row" @tap="onPushTap">
          <image class="row-ic" :src="icon('bell', '#111210', 2)" />
          <text class="row-label">事件推送</text>
          <text class="row-state" :class="{ on: pushStatus.enabled }">
            {{ pushStatus.enabled ? '已开启' : '未开启' }}
          </text>
          <image class="row-ar" :src="icon('chevron-right', '#55584F', 2.2)" />
        </view>
        <!-- #ifdef APP-PLUS -->
        <view class="row" v-if="bound" @tap="toggleBind">
          <image class="row-ic" :src="icon('phone', '#111210', 2)" />
          <text class="row-label">本机推送绑定</text>
          <text class="row-state on">已绑定</text>
          <image class="row-ar" :src="icon('chevron-right', '#55584F', 2.2)" />
        </view>
        <view class="cid-line" v-if="clientId">
          <text class="micro">CID</text>
          <text class="cid-val t-ellipsis">{{ clientId }}</text>
        </view>
        <!-- #endif -->
      </view>

      <!-- 03 通用 -->
      <view class="group-head">
        <text class="idx">03</text>
        <text class="txt">通用</text>
        <view class="rule" />
      </view>
      <view class="rows">
        <view class="row" @tap="clearCache">
          <image class="row-ic" :src="icon('hard-drive', '#111210', 2)" />
          <text class="row-label">清除缓存</text>
          <image class="row-ar" :src="icon('chevron-right', '#55584F', 2.2)" />
        </view>
        <view class="row" @tap="checkUpdate">
          <image class="row-ic" :src="icon('refresh', '#111210', 2)" />
          <text class="row-label">检查更新</text>
          <text class="row-val num">v{{ appVersion }}</text>
          <image class="row-ar" :src="icon('chevron-right', '#55584F', 2.2)" />
        </view>
        <view class="row" @tap="aboutShow = true">
          <image class="row-ic" :src="icon('info', '#111210', 2)" />
          <text class="row-label">关于睿观</text>
          <image class="row-ar" :src="icon('chevron-right', '#55584F', 2.2)" />
        </view>
      </view>

      <!-- 退出 -->
      <view class="logout" @tap="doLogout">
        <image :src="icon('logout', '#E8342C', 2)" style="width:30rpx;height:30rpx;" />
        <text>退出登录</text>
      </view>

      <view class="colophon">
        <text class="micro">SAGESIGHT · AI VISION</text>
        <text class="colo-ver num">v{{ appVersion }}</text>
      </view>

      <!-- 编辑资料弹窗 -->
      <view class="mask" v-if="editShow" @tap="editShow = false">
        <view class="sheet" @tap.stop>
          <view class="sheet-head">
            <text class="sheet-title">编辑资料</text>
            <view class="sheet-x" @tap="editShow = false">
              <image :src="icon('close', '#94988C', 2.2)" style="width:26rpx;height:26rpx;" />
            </view>
          </view>
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
          <view class="sheet-actions">
            <view class="btn btn-ghost flex-1" @tap="editShow = false"><text>取消</text></view>
            <view class="btn btn-primary flex-1" @tap="saveProfile"><text>保存</text></view>
          </view>
        </view>
      </view>

      <!-- 修改密码弹窗 -->
      <view class="mask" v-if="pwdShow" @tap="pwdShow = false">
        <view class="sheet" @tap.stop>
          <view class="sheet-head">
            <text class="sheet-title">修改密码</text>
            <view class="sheet-x" @tap="pwdShow = false">
              <image :src="icon('close', '#94988C', 2.2)" style="width:26rpx;height:26rpx;" />
            </view>
          </view>
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
          <view class="sheet-actions">
            <view class="btn btn-ghost flex-1" @tap="pwdShow = false"><text>取消</text></view>
            <view class="btn btn-primary flex-1" @tap="savePassword"><text>确认修改</text></view>
          </view>
        </view>
      </view>

      <!-- 关于弹窗 -->
      <view class="mask" v-if="aboutShow" @tap="aboutShow = false">
        <view class="sheet about" @tap.stop>
          <view class="about-mark">
            <image :src="icon('logo', '#111210')" style="width:52rpx;height:52rpx;" />
          </view>
          <text class="about-name">睿观 SageSight</text>
          <text class="about-ver micro">AI VISION PLATFORM · v{{ appVersion }}</text>
          <text class="about-desc">面向安防场景的智能视频分析系统，支持区域入侵、徘徊检测、目标跟踪等多种 AI 检测任务。</text>
          <view class="btn btn-primary about-btn" @tap="aboutShow = false"><text>知道了</text></view>
        </view>
      </view>
    </view>
  </scroll-view>
</template>

<script setup>
import { ref, reactive, computed, watch } from 'vue'
import icon from '@/utils/icons'
import { get, put, post } from '@/utils/api'
import { state, clearAuth, setAuth, APP_VERSION } from '@/utils/store'
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
const appVersion = APP_VERSION
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
  if (!pushStatus.configured) {
    uni.showToast({ title: '推送服务未在服务端配置', icon: 'none' })
    return
  }
  toggleBind()
  // #endif
  // #ifndef APP-PLUS
  uni.showToast({ title: '推送功能需在 App 端使用', icon: 'none' })
  // #endif
}

function clearCache() {
  // 保留登录态与服务端地址，仅清理其余本地缓存数据
  const keep = ['ss_token', 'ss_user', 'ss_server_base', 'ss_alert_sound', 'ss_pending_event']
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
/**
 * uni-push 1.0（老版本）：plus.push.getClientInfo() 同步返回 { clientid }。
 * SDK 初始化未完成时 clientid 可能为空，做几次短延时重试。
 */
function ensureClientId() {
  return new Promise((resolve) => {
    if (clientId.value) return resolve()
    let tries = 0
    const attempt = () => {
      try {
        const info = plus.push.getClientInfo()
        const cid = info && (info.clientid || info.cid)
        if (cid) { clientId.value = cid; return resolve() }
      } catch (e) { /* SDK 未就绪 */ }
      if (++tries < 6) setTimeout(attempt, 300)
      else resolve()
    }
    attempt()
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
    confirmColor: '#E8342C',
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
.panel-inner {
  padding: calc(var(--status-bar-height) + 30rpx) 24rpx calc(210rpx + env(safe-area-inset-bottom));
}

/* 档案头 */
.dossier {
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: center;
  gap: 24rpx;
  padding: 10rpx 4rpx 30rpx;
  border-bottom: $stroke solid $border;
}
/* 档案卡右上角的印刷网点，像证件上的防伪底纹 */
.dossier::after {
  content: '';
  position: absolute;
  right: -20rpx;
  top: -10rpx;
  width: 200rpx;
  height: 150rpx;
  @include tex-dots(.085, 10rpx);
  -webkit-mask-image: linear-gradient(228deg, #000 0%, transparent 70%);
  mask-image: linear-gradient(228deg, #000 0%, transparent 70%);
  pointer-events: none;
}
.avatar-box {
  position: relative;
  z-index: 1;
  width: 104rpx;
  height: 104rpx;
  /* 墨色印章：底上压 12rpx 细网格，白字 + 荧光描边 */
  @include tex-ink(12rpx, .14);
  border: 2rpx solid $accent-2;
  box-shadow: 4rpx 4rpx 0 $border;
  color: #FFFFFF;
  font-family: $font-num;
  font-size: 44rpx;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  border-radius: 4rpx;
}
.dossier-meta { position: relative; z-index: 1; flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 6rpx; }
.name-row { display: flex; align-items: center; gap: 14rpx; }
.d-name {
  font-size: 40rpx;
  font-weight: 800;
  color: $text-1;
  letter-spacing: -1rpx;
  max-width: 320rpx;
}
.d-role {
  font-size: 19rpx;
  font-weight: 700;
  letter-spacing: 1rpx;
  color: $accent-deep;
  background: $accent-soft;
  padding: 3rpx 12rpx;
  border-radius: 3rpx;
  flex-shrink: 0;
}
.d-id { font-size: 22rpx; color: $text-2; letter-spacing: .5rpx; }
.d-join { font-size: 20rpx; color: $text-3; }

/* 分组 */
.group-head {
  display: flex;
  align-items: center;
  gap: 14rpx;
  margin: 36rpx 0 14rpx;
}
.group-head .idx {
  font-family: $font-num;
  font-size: 18rpx;
  font-weight: 800;
  color: $border;
  background: $accent-2;
  border: 2rpx solid $border;
  padding: 2rpx 7rpx;
  border-radius: $radius-sm;
  letter-spacing: 1rpx;
}
.group-head .txt {
  font-size: 25rpx;
  font-weight: 800;
  color: $text-1;
  letter-spacing: .5rpx;
}
.group-head .rule { flex: 1; height: $stroke; background: $border; }

/* 行 */
.rows {
  background: $surface;
  border: $stroke solid $border;
  border-radius: $radius;
  box-shadow: 5rpx 5rpx 0 $border;
  overflow: hidden;
}
.row {
  display: flex;
  align-items: center;
  gap: 18rpx;
  padding: 26rpx 24rpx;
  border-bottom: 2rpx solid $line-2;
}
.row:last-child { border-bottom: none; }
.row:active { background: $surface-2; }
.row-ic { width: 34rpx; height: 34rpx; flex-shrink: 0; }
.row-label { flex: 1; font-size: 27rpx; color: $text-1; letter-spacing: .3rpx; }
.row-val {
  font-size: 22rpx;
  color: $text-3;
  max-width: 300rpx;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.row-state {
  font-size: 21rpx;
  color: $text-3;
  letter-spacing: 1rpx;
  padding-right: 4rpx;
}
.row-state.on { color: $success; font-weight: 700; }
.row-ar { width: 28rpx; height: 28rpx; flex-shrink: 0; }
.cid-line {
  display: flex;
  align-items: center;
  gap: 12rpx;
  padding: 14rpx 24rpx 18rpx;
  border-top: 1rpx solid $line;
}
.cid-val { font-size: 19rpx; color: $text-3; }

/* 退出 */
.logout {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12rpx;
  height: 92rpx;
  margin-top: 36rpx;
  border: 1rpx solid rgba(163, 47, 34, .28);
  border-radius: 12rpx;
  background: $surface;
}
.logout:active { background: $danger-bg; }
.logout text { font-size: 27rpx; color: $danger; font-weight: 700; letter-spacing: 1rpx; }

.colophon {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 34rpx;
  padding-top: 18rpx;
  border-top: 1rpx solid $line;
}
.colo-ver { font-size: 19rpx; color: $text-3; }

/* 弹窗 */
.mask {
  position: fixed;
  left: 0; top: 0; right: 0; bottom: 0;
  background: rgba(3, 9, 18, .7);
  z-index: 999;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  padding: 0;
}
.sheet {
  width: 100%;
  max-width: 750px;
  background: $surface;
  border-radius: 20rpx 20rpx 0 0;
  padding: 32rpx 32rpx calc(32rpx + env(safe-area-inset-bottom));
  border-top: 3rpx solid #00A88F;
}
.sheet-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 22rpx;
  border-bottom: 1rpx solid $line;
  margin-bottom: 26rpx;
}
.sheet-title { font-size: 32rpx; font-weight: 800; color: $text-1; letter-spacing: -.3rpx; }
.sheet-x {
  width: 52rpx; height: 52rpx;
  border: 1rpx solid $line;
  border-radius: 6rpx;
  display: flex; align-items: center; justify-content: center;
}
.field { margin-bottom: 22rpx; }
.field-label {
  display: block;
  font-size: 21rpx;
  color: $text-3;
  letter-spacing: 2rpx;
  margin-bottom: 10rpx;
}
.field-input {
  height: 88rpx;
  padding: 0 22rpx;
  border-radius: 10rpx;
  border: 1rpx solid $line;
  background: $surface-2;
  font-size: 28rpx;
  color: $text-1;
}
.ph { color: $text-3; }
.sheet-actions { display: flex; gap: 18rpx; margin-top: 26rpx; }

/* 关于 */
.sheet.about { display: flex; flex-direction: column; align-items: flex-start; text-align: left; }
.about-mark {
  width: 92rpx; height: 92rpx;
  background: #00A88F;
  border-radius: 6rpx;
  display: flex; align-items: center; justify-content: center;
  margin-bottom: 22rpx;
}
.about-name { font-size: 34rpx; font-weight: 800; color: $text-1; letter-spacing: -.5rpx; }
.about-ver { margin: 10rpx 0 18rpx; }
.about-desc { font-size: 24rpx; line-height: 1.75; color: $text-2; }
.about-btn { width: 100%; margin-top: 30rpx; }
</style>
