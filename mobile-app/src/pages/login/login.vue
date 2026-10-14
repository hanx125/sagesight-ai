<template>
  <view class="login-page">
    <!-- 背景光晕 -->
    <view class="glow glow-1" />
    <view class="glow glow-2" />

    <scroll-view scroll-y class="login-scroll" :show-scrollbar="false">
      <view class="login-inner">
        <!-- Logo：与 Web 端一致的圆环对勾 + 字标 -->
        <view class="logo-area">
          <view class="logo-mark">
            <image :src="icon('logo', '#FFFFFF')" style="width: 76rpx; height: 76rpx;" />
          </view>
          <view class="name-row">
            <text class="app-name">睿观 SageSight AI</text>
          </view>
          <text class="app-desc">智能视频检测分析平台</text>
        </view>

        <!-- 表单 -->
        <view class="form">
          <!-- 用户名 -->
          <view class="input-box">
            <image class="in-ic" :src="icon('user', '#667085')" />
            <input
              class="input"
              v-model="form.username"
              placeholder="用户名"
              placeholder-class="placeholder"
              :adjust-position="true"
            />
          </view>
          <!-- 密码 -->
          <view class="input-box">
            <image class="in-ic" :src="icon('shield', '#667085')" />
            <input
              class="input"
              v-model="form.password"
              password
              placeholder="密码"
              placeholder-class="placeholder"
              @confirm="doLogin"
            />
          </view>

          <button class="btn btn-primary login-btn" :disabled="loading" @tap="doLogin">
            <view class="spinner" v-if="loading" />
            <text>{{ loading ? '登录中...' : '登 录' }}</text>
          </button>

          <text class="tip">支持 App / H5 访问 · 数据实时同步</text>
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
import { setAuth, getServerBase, setServerBase, state } from '@/utils/store'
import { connectWs } from '@/utils/ws'
import { loadAlertSound } from '@/utils/alert-sound'

const loading = ref(false)
const form = reactive({
  username: '',
  password: ''
})

onShow(() => {
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
  // 移动端与服务器同机部署：H5 留空走同源代理；App 默认本机地址
  // #ifdef APP-PLUS
  if (!getServerBase()) setServerBase('http://127.0.0.1:5556')
  // #endif

  loading.value = true
  try {
    const d = await post('/api/auth/app_login', { username, password })
    setAuth(d.token, d.user)
    loadAlertSound()
    connectWs()
    uni.showToast({ title: '欢迎回来', icon: 'success' })
    setTimeout(() => {
      uni.reLaunch({ url: '/pages/index/index' })
    }, 500)
  } catch (e) {
    uni.showToast({ title: e.message || '登录失败', icon: 'none' })
  }
  loading.value = false
}
</script>

<style lang="scss" scoped>
.login-page {
  height: 100vh;
  position: relative;
  overflow: hidden;
}
.glow {
  position: absolute;
  border-radius: 50%;
  filter: blur(120rpx);
  pointer-events: none;
}
.glow-1 {
  width: 500rpx;
  height: 500rpx;
  background: rgba(10, 95, 217, .10);
  top: -120rpx;
  right: -100rpx;
  animation: float 6s ease-in-out infinite;
}
.glow-2 {
  width: 420rpx;
  height: 420rpx;
  background: rgba(14, 155, 216, .10);
  bottom: -80rpx;
  left: -120rpx;
  animation: float 7s ease-in-out infinite reverse;
}
@keyframes float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(40rpx); }
}

.login-scroll { height: 100%; position: relative; z-index: 1; }
.login-inner {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 0 56rpx;
}

.logo-area {
  display: flex;
  flex-direction: column;
  align-items: center;
  margin-bottom: 72rpx;
}
.logo-mark {
  width: 152rpx;
  height: 152rpx;
  border-radius: 44rpx;
  background: $grad-primary;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: $shadow-glow;
  animation: breathe 3s ease-in-out infinite;
}
@keyframes breathe {
  0%, 100% { box-shadow: 0 8rpx 32rpx rgba(10, 95, 217, .22); }
  50% { box-shadow: 0 12rpx 56rpx rgba(10, 95, 217, .40); }
}
.name-row { display: flex; align-items: center; margin-top: 36rpx; }
.app-name {
  font-size: 48rpx;
  font-weight: 800;
  color: $text-1;
  letter-spacing: 2rpx;
}
.app-desc { font-size: 26rpx; color: $text-3; margin-top: 12rpx; letter-spacing: 4rpx; }

.form { display: flex; flex-direction: column; gap: 24rpx; }
.in-ic { width: 36rpx; height: 36rpx; margin-right: 20rpx; flex-shrink: 0; }
.login-btn { margin-top: 16rpx; width: 100%; }
.spinner {
  width: 30rpx;
  height: 30rpx;
  border: 4rpx solid rgba(255, 255, 255, .35);
  border-top-color: #FFFFFF;
  border-radius: 50%;
  margin-right: 14rpx;
  animation: spin .8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
.tip {
  text-align: center;
  font-size: 22rpx;
  color: $text-3;
  margin-top: 12rpx;
}
</style>
