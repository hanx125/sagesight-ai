<script>
import { state, setPendingEvent, clearPendingEvent } from './utils/store'
import { connectWs, closeWs, onWsEvent } from './utils/ws'
import { playAlertSound, loadAlertSound } from './utils/alert-sound'

/** 服务端 transmission 为 JSON 字符串，其中 payload 又是一层 JSON 字符串 */
function parsePushEventId(res) {
  try {
    let d = res && res.data !== undefined ? res.data : res
    if (typeof d === 'string') d = JSON.parse(d)
    let p = d && (d.payload !== undefined ? d.payload : (d.event_id ? d : null))
    if (typeof p === 'string') p = JSON.parse(p)
    return (p && (p.event_id || p.eventId)) || ''
  } catch (e) {
    return ''
  }
}

function handlePushMessage(res) {
  if (!res) return
  // 前台收到 UniPush 通知时播放提示音（与 WS 提示音做简单去重）
  if (res.type === 'receive') {
    playAlertSound()
    return
  }
  if (res.type !== 'click' && res.type !== 'open') return
  const eventId = parsePushEventId(res)
  if (!eventId) {
    console.warn('[push] 通知载荷中未找到事件 ID', res)
    return
  }
  // 先记下待打开事件：未登录时登录成功后跳，跳转失败时首页 onShow 兜底
  setPendingEvent(eventId)
  if (!state.token) {
    uni.reLaunch({ url: '/pages/login/login' })
    return
  }
  uni.navigateTo({
    url: '/pages/events/detail?id=' + encodeURIComponent(eventId),
    success: () => clearPendingEvent(),
    // 冷启动时页面栈可能尚未就绪，失败则留给首页消费，不用写死延时
    fail: () => setPendingEvent(eventId)
  })
}

export default {
  onLaunch() {
    console.log('SageSight App Launch')
    // 已登录（token 持久化）时建立实时推送连接并预热告警音源
    if (state.token) {
      loadAlertSound()
      connectWs()
      onWsEvent('new_event', playAlertSound)
    }
    // #ifdef APP-PLUS
    // 主界面为浅色主题：状态栏图标用深色；登录页会在自身 onShow 里切回浅色
    try {
      plus.navigator.setStatusBarStyle('dark')
    } catch (e) { /* 低版本或 H5 环境忽略 */ }
    // uni-push 1.0（老版本）：用 plus.push.addEventListener 监听推送。
    // 监听不依赖登录态：未登录点击通知时先跳登录，登录后再打开事件。
    plus.push.addEventListener('click', (msg) => {
      handlePushMessage({ type: 'click', data: msg && msg.payload })
    }, false)
    plus.push.addEventListener('receive', (msg) => {
      handlePushMessage({ type: 'receive', data: msg && (msg.payload || msg.content) })
    }, false)
    // #endif
  },
  onHide() {
    // H5 页面隐藏时保持连接（由 onClose 自动重连兜底）；退出登录时统一 closeWs
    if (!state.token) closeWs()
  }
}
</script>

<style lang="scss">
/* ============================================================
   全局样式 v5 —— 信号白 SIGNAL WHITE
   大胆浅色：白底 + 墨黑粗描边 + 硬投影 + 青绿/荧光黄点缀
   登录页在自身 scoped 内覆盖回深色，双主题共存。
   ============================================================ */
page {
  background-color: $bg;
  @include tex-paper(56rpx, .045, .10);
  color: $text-1;
  font-size: 28rpx;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC',
    'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;
  -webkit-font-smoothing: antialiased;
}

view, text, image, input, textarea, button {
  box-sizing: border-box;
}

/* ================= H5 适配 ================= */
/* #ifdef H5 */
uni-page-wrapper,
uni-page {
  background-color: $bg;
}
uni-page-body {
  max-width: 750px;
  margin: 0 auto;
  min-height: 100vh;
  background-color: $bg;
  @include tex-paper(56rpx, .045, .10);
}
/* #endif */

/* ============ 动效（登录页与全局共用）============ */
@keyframes lightSweep {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
@keyframes tealPulse {
  0%, 100% { opacity: 1; }
  50% { opacity: .4; }
}
@keyframes orbDrift1 {
  0%, 100% { transform: translate(0, 0) scale(1); }
  33% { transform: translate(110rpx, 70rpx) scale(1.12); }
  66% { transform: translate(50rpx, -50rpx) scale(.94); }
}
@keyframes orbDrift2 {
  0%, 100% { transform: translate(0, 0) scale(1); }
  50% { transform: translate(-120rpx, -80rpx) scale(1.1); }
}
@keyframes stickerPop {
  0% { transform: scale(.86) rotate(-4deg); }
  70% { transform: scale(1.04) rotate(1deg); }
  100% { transform: scale(1) rotate(0); }
}

/* ================= 布局 ================= */
.page-wrap {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background: transparent;
}
.page-body { padding: 24rpx; }
.flex-row { display: flex; flex-direction: row; align-items: center; }
.flex-1 { flex: 1; min-width: 0; }
.flex-center { display: flex; align-items: center; justify-content: center; }

/* ================= 卡片：白底粗框硬投影 ================= */
.card {
  position: relative;
  background: $surface;
  border: $stroke solid $border;
  border-radius: $radius;
  padding: $pad;
  margin-bottom: 24rpx;
  box-shadow: $shadow-card;
}
.card::before { @include light-band(3rpx); }
.card-press:active {
  transform: translate(3rpx, 3rpx);
  box-shadow: 2rpx 2rpx 0 $border;
}
.card-flat { border-radius: $radius; }

/* 墨块：反白大字，制造强对比 */
.ink-block {
  position: relative;
  background: $bg-deep;
  @include tex-ink(28rpx, .14);
  color: $text-inverse;
  border: $stroke solid $border;
  border-radius: $radius;
  box-shadow: $shadow-card;
}

/* 荧光贴纸：唯一的高饱和点缀 */
.sticker {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: $accent-2;
  color: $border;
  border: $stroke solid $border;
  border-radius: $radius-sm;
  font-weight: 800;
  letter-spacing: 1rpx;
}

/* ================= 标签 ================= */
.badge {
  display: inline-flex;
  align-items: center;
  padding: 3rpx 12rpx;
  border-radius: $radius-sm;
  font-size: 19rpx;
  font-weight: 800;
  letter-spacing: 1rpx;
  line-height: 30rpx;
  border: 2rpx solid $border;
}
.badge-success { background: $success-bg; color: $accent-deep; border-color: $accent-deep; }
.badge-warning { background: $warning-bg; color: #A66A00; border-color: $warning; }
.badge-danger  { background: $danger-bg;  color: $danger;  border-color: $danger; }
.badge-info    { background: $info-bg;    color: $info;    border-color: $info; }
.badge-cyan    { background: $accent-2;   color: $border;  border-color: $border; }
.badge-gray    { background: $surface-2;  color: $text-2;  border-color: $line-2; }
.badge-accent  { background: $accent;     color: #FFFFFF;  border-color: $border; }

/* 拉丁小标 / 等宽数字 */
.micro {
  font-size: 19rpx;
  letter-spacing: 3rpx;
  color: $text-3;
  font-weight: 800;
  text-transform: uppercase;
}
.num {
  font-family: $font-num;
  font-weight: 700;
  letter-spacing: -1rpx;
  font-variant-numeric: tabular-nums;
}

/* ================= 按钮 ================= */
.btn {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 92rpx;
  border-radius: $radius;
  font-size: 29rpx;
  font-weight: 800;
  letter-spacing: 2rpx;
  transition: transform .15s ease, box-shadow .15s ease, opacity .2s ease;
}
.btn:active { transform: translate(3rpx, 3rpx); }
.btn-primary {
  background: $bg-deep;
  color: $text-inverse;
  border: $stroke solid $border;
  box-shadow: $shadow-card;
}
.btn-primary:active { box-shadow: 2rpx 2rpx 0 $border; }
.btn-ghost {
  background: $surface;
  color: $text-1;
  border: $stroke solid $border;
  box-shadow: 3rpx 3rpx 0 $line-2;
}
.btn-ghost:active { box-shadow: none; transform: translate(3rpx, 3rpx); }
.btn-danger-ghost {
  background: $danger-bg;
  color: $danger;
  border: $stroke solid $danger;
}
.btn-accent {
  background: $accent;
  color: #FFFFFF;
  border: $stroke solid $border;
  box-shadow: $shadow-glow;
}
.btn-sm {
  height: 62rpx;
  padding: 0 22rpx;
  border-radius: $radius-sm;
  font-size: 23rpx;
  letter-spacing: 1rpx;
  box-shadow: 2rpx 2rpx 0 $border;
}
.btn[disabled] { opacity: .4; }

/* ================= 输入 ================= */
.input-box {
  display: flex;
  align-items: center;
  background: $surface;
  border: $stroke solid $border;
  border-radius: $radius;
  padding: 0 24rpx;
  height: 96rpx;
  transition: border-color .2s, box-shadow .2s;
}
.input-box:focus-within {
  box-shadow: 4rpx 4rpx 0 $accent;
}
.input {
  flex: 1;
  height: 100%;
  font-size: 28rpx;
  color: $text-1;
  background: transparent;
}
.placeholder { color: $text-3; letter-spacing: 1rpx; }

/* ================= 表单 ================= */
.form-item { margin-bottom: 28rpx; }
.form-label {
  font-size: 22rpx;
  color: $text-2;
  letter-spacing: 2rpx;
  margin-bottom: 12rpx;
  display: flex;
  align-items: center;
  font-weight: 700;
}
.form-label .req { color: $danger; margin-left: 4rpx; }

/* ================= 文本 ================= */
.t-main { color: $text-1; }
.t-sub { color: $text-2; }
.t-dim { color: $text-3; }
.t-bold { font-weight: 800; }
.t-title { font-size: 34rpx; font-weight: 800; color: $text-1; letter-spacing: -.5rpx; }
.t-ellipsis { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.t-ellipsis-2 {
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

/* ================= 分节标题：编号方块 + 粗线 ================= */
.section-title {
  display: flex;
  align-items: center;
  font-size: 27rpx;
  font-weight: 800;
  color: $text-1;
  letter-spacing: 1rpx;
  margin: 4rpx 0 18rpx;
}
.section-title::before {
  content: '';
  width: 14rpx;
  height: 14rpx;
  background: $accent;
  border: 2rpx solid $border;
  margin-right: 14rpx;
  flex-shrink: 0;
}
.section-head {
  display: flex;
  align-items: center;
  gap: 16rpx;
  margin: 36rpx 0 16rpx;
}
.section-head .idx {
  font-family: $font-num;
  font-size: 22rpx;
  color: $border;
  background: $accent-2;
  border: 2rpx solid $border;
  padding: 2rpx 8rpx;
  letter-spacing: 1rpx;
  font-weight: 800;
}
.section-head .txt {
  font-size: 27rpx;
  font-weight: 800;
  color: $text-1;
  letter-spacing: 1rpx;
}
.section-head .rule {
  flex: 1;
  height: $stroke;
  background: $border;
}
.section-head .side {
  font-size: 21rpx;
  color: $text-2;
  letter-spacing: 1rpx;
  font-weight: 700;
}

/* ================= 空态 / 加载 ================= */
.empty-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 96rpx 0;
}
.empty-text { color: $text-2; font-size: 24rpx; margin-top: 20rpx; letter-spacing: .5rpx; font-weight: 700; }
.loading-more {
  text-align: center;
  color: $text-3;
  font-size: 22rpx;
  letter-spacing: 2rpx;
  padding: 28rpx 0 40rpx;
  font-weight: 700;
}

/* ================= 分隔线 ================= */
.divider { height: $stroke; background: $border; }

/* ================= 安全区 ================= */
.safe-bottom { padding-bottom: constant(safe-area-inset-bottom); padding-bottom: env(safe-area-inset-bottom); }
</style>
