<template>
  <view class="login-page">
    <!-- 与 Web 登录页同源的全屏背景：光斑 + 青绿网格 + 星点 + 检测取景框 -->
    <view class="bg-orb o1" />
    <view class="bg-orb o2" />
    <view class="bg-orb o3" />
    <view class="bg-grid" />
    <view class="bg-stars" />
    <view class="det-box d1"><text class="det-label num">行人 0.98</text></view>
    <view class="det-box d2"><text class="det-label num">车辆 0.96</text></view>

    <scroll-view scroll-y class="login-scroll" :show-scrollbar="false">
      <view class="login-inner">
        <!-- 品牌区：与 Web 左侧品牌栏同款 -->
        <view class="masthead">
          <view class="brand-mark">
            <image :src="icon('play', '#04121c', 2, true)" />
          </view>
          <text class="brand">睿观</text>
          <text class="brand-en">SageSight AI</text>
          <text class="tagline">让每一帧画面都被理解</text>

          <view class="features">
            <view class="feature">
              <view class="f-dot"><image :src="icon('target', '#00e5c3', 2)" /></view>
              <text>实时目标检测与跟踪</text>
            </view>
            <view class="feature">
              <view class="f-dot"><image :src="icon('user', '#00e5c3', 2)" /></view>
              <text>人脸 · 车牌智能比对</text>
            </view>
            <view class="feature">
              <view class="f-dot"><image :src="icon('bell', '#00e5c3', 2)" /></view>
              <text>事件秒级告警推送</text>
            </view>
          </view>

          <view class="hud">
            <view class="hud-pulse" />
            <text class="num">SYS ONLINE · 模型已加载 · 通道正常</text>
          </view>
        </view>

        <!-- 玻璃登录卡 -->
        <view class="form-card">
          <view class="card-head">
            <text class="form-heading">欢迎回来</text>
            <text class="form-sub">登录以进入推理分析平台</text>
          </view>

          <view class="field">
            <image class="in-ic" :src="icon('user', '#81939c', 2)" />
            <input
              class="input"
              v-model="form.username"
              placeholder="请输入用户名"
              placeholder-class="placeholder"
              :adjust-position="true"
            />
          </view>

          <view class="field">
            <image class="in-ic" :src="icon('lock', '#81939c', 2)" />
            <input
              class="input"
              v-model="form.password"
              password
              placeholder="请输入密码"
              placeholder-class="placeholder"
              @confirm="doLogin"
            />
          </view>

          <!-- 服务器地址：H5 走同源代理无需填写；App 真机必须填写 -->
          <!-- #ifdef APP-PLUS -->
          <view class="field">
            <image class="in-ic" :src="icon('server', '#81939c', 2)" />
            <input
              class="input"
              v-model="serverInput"
              placeholder="http://192.168.1.10:5556"
              placeholder-class="placeholder"
              :adjust-position="true"
            />
          </view>
          <!-- #endif -->

          <button class="btn btn-primary login-btn" :disabled="loading" @tap="doLogin">
            <view class="spinner" v-if="loading" />
            <text>{{ loading ? '登录中' : '登 录' }}</text>
          </button>

          <!-- #ifdef APP-PLUS -->
          <text class="tip">请填写后端地址，手机与服务器需在同一局域网</text>
          <!-- #endif -->
          <!-- #ifdef H5 -->
          <text class="tip">H5 已自动连接后端，无需填写服务器地址</text>
          <!-- #endif -->

          <view class="foot">
            <text class="foot-txt num">© 2026 SageSight AI · 推理分析平台</text>
            <text class="foot-ver num">v{{ version }}</text>
          </view>
        </view>
      </view>
    </scroll-view>
  </view>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { onShow } from '@dcloudio/uni-app'
import icon from '@/utils/icons'
import { post } from '@/utils/api'
import { setAuth, getServerBase, setServerBase, state, takePendingEvent, APP_VERSION } from '@/utils/store'
import { connectWs, closeWs } from '@/utils/ws'
import { loadAlertSound } from '@/utils/alert-sound'

const loading = ref(false)
const form = reactive({
  username: '',
  password: ''
})
const serverInput = ref('')
const version = APP_VERSION

onShow(() => {
  // 登录页是深色背景，状态栏图标需浅色；进入主界面后 App.vue 会切回深色
  // #ifdef APP-PLUS
  try { plus.navigator.setStatusBarStyle('light') } catch (e) {}
  // #endif
  // H5 始终走同源代理，清掉历史残留地址，避免"填了反而连不上"
  // #ifdef H5
  setServerBase('')
  // #endif
  serverInput.value = getServerBase()
  if (state.token) {
    uni.reLaunch({ url: '/pages/index/index' })
  }
})

async function doLogin() {
  const username = form.username.trim()
  const password = form.password
  if (!username || !password) {
    uni.showToast({ title: '请输入用户名和密码', icon: 'none' })
    return
  }

  // H5 留空走 vite 同源代理；App 真机没有后端同源，必须显式填服务器地址
  const base = (serverInput.value || '').trim().replace(/\/+$/, '')
  // #ifdef APP-PLUS
  if (!/^https?:\/\//i.test(base)) {
    uni.showToast({ title: '请填写服务器地址，如 http://192.168.1.10:5556', icon: 'none' })
    return
  }
  // #endif
  if (base && !/^https?:\/\//i.test(base)) {
    uni.showToast({ title: '地址需以 http:// 或 https:// 开头', icon: 'none' })
    return
  }
  setServerBase(base)

  loading.value = true
  try {
    const d = await post('/api/auth/app_login', { username, password })
    setAuth(d.token, d.user)
    loadAlertSound()
    // 地址可能已变更，先断开旧连接再按新地址重连实时推送
    closeWs()
    connectWs()
    uni.showToast({ title: '欢迎回来', icon: 'success' })
    const pendingEvent = takePendingEvent()
    setTimeout(() => {
      uni.reLaunch({
        url: pendingEvent
          ? '/pages/events/detail?id=' + encodeURIComponent(pendingEvent)
          : '/pages/index/index'
      })
    }, 500)
  } catch (e) {
    uni.showToast({ title: e.message || '登录失败', icon: 'none' })
  }
  loading.value = false
}
</script>

<style lang="scss" scoped>
/* ============================================================
   登录页独立深色主题 —— 与 Web 系统登录页完全一致。
   在局部作用域覆盖全局变量，不污染登录后的浅色主界面。
   ============================================================ */
$bg: #050b14;
$surface: rgba(11, 21, 36, .78);
$text-1: #e8f1f5;
$text-2: rgba(232, 241, 245, .62);
$text-3: rgba(232, 241, 245, .38);
$accent: #00e5c3;
$accent-2: #38bdf8;
$accent-soft: rgba(0, 229, 195, .10);
$accent-dim: rgba(0, 229, 195, .35);
$line: rgba(148, 196, 210, .14);
$radius-lg: 28rpx;

/* 登录页专用的青绿流光（覆盖全局的浅色版本） */
@mixin light-band($height: 2rpx) {
  content: '';
  position: absolute;
  top: -1rpx;
  left: 30rpx;
  right: 30rpx;
  height: $height;
  background: linear-gradient(90deg, transparent, $accent, $accent-2, transparent);
  background-size: 200% 100%;
  animation: lightSweep 6s linear infinite;
  opacity: .75;
  pointer-events: none;
}

.login-page {
  position: relative;
  min-height: 100vh;
  background: $bg;
  overflow: hidden;
}

/* ===== 全屏动态背景（与 Web 登录页同款）===== */
.bg-orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(45px);
  pointer-events: none;
}
.bg-orb.o1 {
  width: 560rpx; height: 560rpx;
  background: rgba(0, 229, 195, .14);
  top: -180rpx; left: -140rpx;
  animation: orbDrift1 26s ease-in-out infinite;
}
.bg-orb.o2 {
  width: 520rpx; height: 520rpx;
  background: rgba(56, 189, 248, .12);
  bottom: -200rpx; right: -120rpx;
  animation: orbDrift2 32s ease-in-out infinite;
}
.bg-orb.o3 {
  width: 380rpx; height: 380rpx;
  background: rgba(0, 122, 158, .14);
  top: 52%; left: 58%;
  animation: orbDrift1 24s ease-in-out infinite reverse;
}
.bg-grid {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(0, 229, 195, .05) 1rpx, transparent 1rpx),
    linear-gradient(90deg, rgba(0, 229, 195, .05) 1rpx, transparent 1rpx);
  background-size: 64rpx 64rpx;
  -webkit-mask-image: radial-gradient(ellipse 85% 70% at 50% 42%, #000 28%, transparent 82%);
  mask-image: radial-gradient(ellipse 85% 70% at 50% 42%, #000 28%, transparent 82%);
  pointer-events: none;
}
/* 星点：多组错位圆点，静态不耗电 */
.bg-stars {
  position: absolute;
  inset: 0;
  background-image:
    radial-gradient(rgba(140, 235, 218, .5) 1.4rpx, transparent 1.8rpx),
    radial-gradient(rgba(140, 235, 218, .34) 1.2rpx, transparent 1.6rpx),
    radial-gradient(rgba(140, 235, 218, .24) 1rpx, transparent 1.4rpx);
  background-size: 190rpx 230rpx, 270rpx 310rpx, 350rpx 290rpx;
  background-position: 12rpx 40rpx, 90rpx 150rpx, 200rpx 80rpx;
  pointer-events: none;
}
/* 检测取景框：Web canvas 的 CSS 对应物 */
.det-box {
  position: absolute;
  width: 180rpx;
  height: 220rpx;
  border: 1rpx solid rgba(0, 229, 195, .24);
  pointer-events: none;
  animation: tealPulse 4s ease-in-out infinite;
}
.det-box::before,
.det-box::after {
  content: '';
  position: absolute;
  width: 20rpx;
  height: 20rpx;
  border-color: rgba(0, 229, 195, .85);
  border-style: solid;
}
.det-box::before {
  left: -2rpx; top: -2rpx;
  border-width: 3rpx 0 0 3rpx;
}
.det-box::after {
  right: -2rpx; bottom: -2rpx;
  border-width: 0 3rpx 3rpx 0;
}
.det-box.d1 { left: -40rpx; top: 140rpx; transform: rotate(-6deg); }
.det-box.d2 { right: -50rpx; bottom: 120rpx; transform: rotate(5deg); animation-delay: 1.8s; }
.det-label {
  position: absolute;
  left: -2rpx;
  top: -34rpx;
  padding: 3rpx 10rpx;
  font-size: 17rpx;
  letter-spacing: 1rpx;
  color: #00e5c3;
  background: rgba(0, 229, 195, .14);
  white-space: nowrap;
}

.login-scroll { height: 100vh; position: relative; z-index: 2; }
.login-inner {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: calc(var(--status-bar-height) + 56rpx) 40rpx 40rpx;
}

/* ===== 品牌区 ===== */
.masthead { margin-bottom: 44rpx; }
.brand-mark {
  width: 92rpx;
  height: 92rpx;
  border-radius: 22rpx;
  background: linear-gradient(135deg, #00795f 0%, #00e5c3 130%);
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 14rpx 36rpx rgba(0, 229, 195, .3), inset 0 1rpx 0 rgba(255, 255, 255, .25);
  margin-bottom: 26rpx;
  animation: markPulse 4.5s ease-in-out 1.2s infinite;
}
.brand-mark image { width: 52rpx; height: 52rpx; }
@keyframes markPulse {
  0%, 100% { box-shadow: 0 14rpx 36rpx rgba(0, 229, 195, .3), inset 0 1rpx 0 rgba(255, 255, 255, .25); }
  50% { box-shadow: 0 14rpx 52rpx rgba(0, 229, 195, .52), inset 0 1rpx 0 rgba(255, 255, 255, .25); }
}
.brand {
  display: block;
  font-size: 76rpx;
  font-weight: 700;
  line-height: 1.15;
  letter-spacing: 8rpx;
  color: $text-1;
}
.brand-en {
  display: block;
  margin-top: 8rpx;
  font-size: 26rpx;
  font-weight: 700;
  letter-spacing: 7rpx;
  color: $accent;
  text-transform: uppercase;
}
.tagline {
  display: block;
  margin-top: 18rpx;
  font-size: 24rpx;
  letter-spacing: 4rpx;
  color: $text-2;
}
.features {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
  margin-top: 34rpx;
}
.feature {
  display: flex;
  align-items: center;
  gap: 14rpx;
  font-size: 23rpx;
  letter-spacing: 1rpx;
  color: $text-2;
}
.f-dot {
  width: 38rpx;
  height: 38rpx;
  border-radius: 10rpx;
  flex-shrink: 0;
  background: $accent-soft;
  border: 1rpx solid $accent-dim;
  display: flex;
  align-items: center;
  justify-content: center;
}
.f-dot image { width: 20rpx; height: 20rpx; }
.hud {
  display: flex;
  align-items: center;
  gap: 12rpx;
  margin-top: 34rpx;
  font-size: 18rpx;
  letter-spacing: 2rpx;
  color: $text-3;
}
.hud text { flex: 1; min-width: 0; }
.hud-pulse {
  width: 10rpx;
  height: 10rpx;
  border-radius: 50%;
  background: $accent;
  box-shadow: 0 0 12rpx $accent;
  animation: tealPulse 2s ease-in-out infinite;
  flex-shrink: 0;
}

/* ===== 玻璃登录卡 ===== */
.form-card {
  position: relative;
  background: $surface;
  backdrop-filter: blur(20rpx);
  -webkit-backdrop-filter: blur(20rpx);
  border: 1rpx solid $line;
  border-radius: $radius-lg;
  padding: 40rpx 36rpx 30rpx;
  box-shadow: 0 30rpx 90rpx rgba(0, 0, 0, .6), inset 0 1rpx 0 rgba(255, 255, 255, .06);
}
.form-card::before { @include light-band(2rpx); }
.card-head { margin-bottom: 32rpx; }
.form-heading {
  display: block;
  font-size: 42rpx;
  font-weight: 700;
  letter-spacing: 3rpx;
  color: $text-1;
}
.form-sub {
  display: block;
  margin-top: 10rpx;
  font-size: 23rpx;
  letter-spacing: 2rpx;
  color: $text-2;
}
.field {
  display: flex;
  align-items: center;
  height: 100rpx;
  padding: 0 26rpx;
  margin-bottom: 22rpx;
  background: rgba(255, 255, 255, .04);
  border: 1rpx solid $line;
  border-radius: 14rpx;
  transition: border-color .25s, box-shadow .25s, background .25s;
}
.field:focus-within {
  background: rgba(255, 255, 255, .06);
  border-color: $accent-dim;
  box-shadow: 0 0 0 4rpx rgba(0, 229, 195, .1), 0 0 22rpx rgba(0, 229, 195, .08);
}
.in-ic { width: 36rpx; height: 36rpx; margin-right: 18rpx; flex-shrink: 0; }
.input {
  flex: 1;
  min-width: 0;
  height: 100%;
  font-size: 28rpx;
  color: $text-1;
  letter-spacing: 1rpx;
  background: transparent;
}

.login-btn {
  background: linear-gradient(135deg, #00795f 0%, #00e5c3 130%) !important;
  color: #04121c !important;
  border: none !important;
  width: 100%;
  height: 100rpx;
  margin-top: 8rpx;
  font-size: 30rpx;
  letter-spacing: 8rpx;
  border-radius: 14rpx;
}
.spinner {
  width: 28rpx; height: 28rpx;
  border: 3rpx solid rgba(4, 18, 28, .28);
  border-top-color: #04121c;
  border-radius: 50%;
  margin-right: 14rpx;
  animation: spin .8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
.tip {
  display: block;
  font-size: 20rpx;
  color: $text-3;
  margin-top: 20rpx;
  line-height: 1.6;
  letter-spacing: .5rpx;
}
.foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12rpx;
  margin-top: 28rpx;
  padding-top: 20rpx;
  border-top: 1rpx solid $line;
}
.foot-txt { font-size: 18rpx; color: $text-3; letter-spacing: 1rpx; }
.foot-ver { font-size: 18rpx; color: $text-3; flex-shrink: 0; }
</style>
