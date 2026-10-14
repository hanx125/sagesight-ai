<script>
import { state } from './utils/store'
import { connectWs, closeWs, onWsEvent } from './utils/ws'
import { playAlertSound, loadAlertSound } from './utils/alert-sound'

export default {
  onLaunch() {
    console.log('SageSight App Launch')
    // 已登录（token 持久化）时建立实时推送连接并预热告警音源
    if (state.token) {
      loadAlertSound()
      connectWs()
      onWsEvent('new_event', playAlertSound)
      // #ifdef APP-PLUS
      // 前台收到 UniPush 通知时同样播放提示音（1.5s 去重防止与 WS 重复）
      uni.onPushMessage((res) => {
        if (res && res.type === 'receive') playAlertSound()
      })
      // #endif
    }
  },
  onHide() {
    // H5 页面隐藏时保持连接（由 onClose 自动重连兜底）；退出登录时统一 closeWs
    if (!state.token) closeWs()
  }
}
</script>

<style lang="scss">
/* ================= 全局基础 ================= */
page {
  background-color: $bg;
  color: $text-1;
  font-size: 28rpx;
  font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Helvetica Neue',
    'Microsoft YaHei', sans-serif;
  -webkit-font-smoothing: antialiased;
}

view, text, image, input, textarea, button {
  box-sizing: border-box;
}

/* ================= H5 适配 ================= */
/* #ifdef H5 */
uni-page-body {
  max-width: 750px;
  margin: 0 auto;
}
/* #endif */

/* ================= 通用布局 ================= */
.page-wrap {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.flex-row { display: flex; flex-direction: row; align-items: center; }
.flex-1 { flex: 1; min-width: 0; }
.flex-center { display: flex; align-items: center; justify-content: center; }

/* ================= 卡片 ================= */
.card {
  background: $surface;
  border: 1rpx solid $border;
  border-radius: $radius;
  padding: $pad;
  margin-bottom: $pad;
  box-shadow: $shadow-card;
}
.card-press:active {
  background: $surface-2;
  transform: scale(0.985);
  transition: all .12s ease;
}

/* ================= 徽标 ================= */
.badge {
  display: inline-flex;
  align-items: center;
  padding: 4rpx 16rpx;
  border-radius: 999rpx;
  font-size: 20rpx;
  font-weight: 600;
  line-height: 32rpx;
}
.badge-success { background: rgba(7, 148, 85, .12); color: #079455; }
.badge-warning { background: rgba(180, 83, 9, .12);  color: #B45309; }
.badge-danger  { background: rgba(217, 45, 32, .12); color: #D92D20; }
.badge-info    { background: rgba(21, 112, 239, .12); color: #1570EF; }
.badge-cyan    { background: rgba(10, 95, 217, .10); color: $primary; }
.badge-gray    { background: rgba(148,163,184,.12); color: $text-2; }

/* ================= 按钮 ================= */
.btn {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 88rpx;
  border-radius: $radius;
  font-size: 30rpx;
  font-weight: 600;
  transition: opacity .15s ease, transform .12s ease;
}
.btn:active { opacity: .8; transform: scale(.98); }
.btn-primary {
  background: $grad-primary;
  color: #FFFFFF;
  box-shadow: $shadow-glow;
}
.btn-ghost {
  background: $surface-2;
  color: $text-1;
  border: 1rpx solid $border-strong;
}
.btn-danger-ghost {
  background: rgba(217, 45, 32, .10);
  color: #D92D20;
  border: 1rpx solid rgba(217, 45, 32, .28);
}
.btn-sm {
  height: 64rpx;
  padding: 0 28rpx;
  border-radius: $radius-sm;
  font-size: 24rpx;
}
.btn[disabled] { opacity: .5; }

/* ================= 输入 ================= */
.input-box {
  display: flex;
  align-items: center;
  background: $surface-2;
  border: 1rpx solid $border;
  border-radius: $radius-sm;
  padding: 0 24rpx;
  height: 92rpx;
  transition: border-color .15s;
}
.input-box:focus-within {
  border-color: rgba(10, 95, 217, .45);
  background: $surface;
}
.input {
  flex: 1;
  height: 100%;
  font-size: 28rpx;
  color: $text-1;
}
.placeholder { color: $text-3; }

/* ================= 表单 ================= */
.form-item { margin-bottom: 28rpx; }
.form-label {
  font-size: 26rpx;
  color: $text-2;
  margin-bottom: 14rpx;
  display: flex;
  align-items: center;
}
.form-label .req { color: $danger; margin-left: 4rpx; }

/* ================= 文本 ================= */
.t-main { color: $text-1; }
.t-sub { color: $text-2; }
.t-dim { color: $text-3; }
.t-bold { font-weight: 600; }
.t-title { font-size: 34rpx; font-weight: 700; color: $text-1; }
.t-ellipsis { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.t-ellipsis-2 {
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

/* ================= 区块标题 ================= */
.section-title {
  display: flex;
  align-items: center;
  font-size: 30rpx;
  font-weight: 700;
  color: $text-1;
  margin: 8rpx 0 20rpx;
}
.section-title::before {
  content: '';
  width: 8rpx;
  height: 28rpx;
  border-radius: 4rpx;
  background: $grad-primary;
  margin-right: 14rpx;
}

/* ================= 空态/加载 ================= */
.empty-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 100rpx 0;
}
.empty-text { color: $text-3; font-size: 26rpx; margin-top: 20rpx; }
.loading-more {
  text-align: center;
  color: $text-3;
  font-size: 24rpx;
  padding: 24rpx 0 40rpx;
}

/* ================= 分隔线 ================= */
.divider { height: 1rpx; background: $border; }

/* ================= 安全区 ================= */
.safe-bottom { padding-bottom: constant(safe-area-inset-bottom); padding-bottom: env(safe-area-inset-bottom); }
</style>
