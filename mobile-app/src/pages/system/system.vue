<template>
  <view class="page-wrap">
    <nav-bar title="系统设置" />

    <view class="sys-inner">
      <!-- 系统健康 -->
      <text class="section-title">系统健康</text>
      <view class="card">
        <view class="health-top">
          <view class="health-stat">
            <text class="health-num" :class="healthAllOk ? 'num-ok' : 'num-bad'">
              {{ health.healthy }}/{{ health.expected }}
            </text>
            <text class="health-label">任务进程健康</text>
          </view>
          <view class="badge" :class="healthAllOk ? 'badge-success' : 'badge-warning'">
            {{ health.expected === 0 ? '无任务' : (healthAllOk ? '运行正常' : '存在异常') }}
          </view>
        </view>
        <view class="health-bar">
          <view class="health-bar-inner" :style="{ width: healthPct + '%' }" />
        </view>

        <view class="h-task" v-for="t in health.tasks" :key="t.task_id">
          <view class="dot" :class="t.healthy ? 'dot-ok' : 'dot-bad'" />
          <text class="t-ellipsis flex-1 h-name">{{ t.task_name }}</text>
          <text class="t-dim h-stage">{{ t.stage || '-' }}</text>
          <text class="h-heart" :class="{ 'h-heart-bad': t.heartbeat_age > 30 }">
            {{ t.heartbeat_age != null ? Math.round(t.heartbeat_age) + 's' : '-' }}
          </text>
        </view>
        <view v-if="!health.tasks.length" class="t-dim h-none">
          {{ health.expected === 0 ? '暂无已启用任务' : '加载中...' }}
        </view>
      </view>

      <!-- 推送配置 -->
      <text class="section-title">推送配置</text>
      <view class="card" v-if="isAdmin">
        <view class="push-status-row" v-if="pushStatus">
          <view class="flex-1">
            <view class="switch-title">个推服务状态</view>
            <view class="switch-desc">
              {{ pushStatus.configured ? 'UniPush 2.0 已配置' : '尚未配置个推应用参数' }}
            </view>
          </view>
          <view class="badge" :class="pushStatus.configured ? 'badge-success' : 'badge-gray'">
            {{ pushStatus.configured ? '就绪' : '未配置' }}
          </view>
        </view>

        <view class="form-item">
          <view class="form-label">启用事件推送</view>
          <switch :checked="cfg.push_enabled" color="#0a5fd9" style="transform:scale(.85); transform-origin: left;"
            @change="cfg.push_enabled = $event.detail.value" />
        </view>

        <view class="form-item">
          <view class="form-label">AppID</view>
          <view class="input-box">
            <input class="input" v-model="cfg.getui_app_id" placeholder="个推 UniPush AppID" placeholder-class="placeholder" />
          </view>
        </view>

        <view class="form-item">
          <view class="form-label">AppKey</view>
          <view class="input-box">
            <input class="input" v-model="cfg.getui_app_key" placeholder="个推 UniPush AppKey" placeholder-class="placeholder" />
          </view>
        </view>

        <view class="form-item">
          <view class="form-label">MasterSecret</view>
          <view class="input-box">
            <input class="input" v-model="cfg.getui_app_master_secret" password
              placeholder="不修改请保留 ******" placeholder-class="placeholder" />
          </view>
        </view>

        <view class="form-item">
          <view class="form-label">最低推送置信度</view>
          <view class="input-box">
            <input class="input" type="digit" v-model="cfg.push_min_confidence" placeholder="0 - 1，例如 0.6"
              placeholder-class="placeholder" />
          </view>
        </view>

        <view class="form-item" style="margin-bottom:0;">
          <view class="form-label">推送类别过滤</view>
          <view class="input-box">
            <input class="input" v-model="pushClassText" placeholder="逗号分隔，如 person,car（留空全部推送）"
              placeholder-class="placeholder" />
          </view>
        </view>

        <view class="btn btn-primary" style="margin-top:32rpx;" :class="{ disabled: savingCfg }" @tap="saveCfg">
          <text>{{ savingCfg ? '保存中...' : '保存推送配置' }}</text>
        </view>
      </view>
      <view class="card" v-else>
        <view class="push-status-row" style="margin:0;">
          <view class="flex-1">
            <view class="switch-title">事件推送</view>
            <view class="switch-desc">
              {{ pushStatus && pushStatus.enabled ? '推送已开启' : '推送未开启' }}
              {{ pushStatus && pushStatus.configured ? ' · 服务已配置' : '' }}
            </view>
          </view>
          <view class="badge" :class="pushStatus && pushStatus.enabled ? 'badge-success' : 'badge-gray'">
            {{ pushStatus && pushStatus.enabled ? '已开启' : '未开启' }}
          </view>
        </view>
        <view class="form-tip">推送参数仅管理员可修改</view>
      </view>

      <!-- 关于 -->
      <text class="section-title">关于</text>
      <view class="card">
        <view class="about-row">
          <view class="about-logo">
            <image :src="icon('logo', '#FFFFFF')" style="width:52rpx;height:52rpx;" />
          </view>
          <view class="flex-1">
            <text class="about-name">睿观 SageSight AI</text>
            <text class="about-ver">智能视频检测分析平台 · 移动端 v1.0.0</text>
          </view>
        </view>
        <view class="about-meta">
          <text>推送服务：UniPush 2.0（个推）</text>
          <text>视频直播：HLS · ZLMediaKit</text>
          <text>检测结果由服务端 AI 算法实时生成</text>
        </view>
      </view>

      <view style="height: 40rpx;" />
    </view>
  </view>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import navBar from '@/components/nav-bar/nav-bar.vue'
import icon from '@/utils/icons'
import { get, put } from '@/utils/api'
import { state } from '@/utils/store'

const isAdmin = computed(() => state.user && state.user.role === 'admin')

const health = reactive({ tasks: [], expected: 0, healthy: 0 })
const pushStatus = ref(null)
const cfg = reactive({
  push_enabled: false,
  getui_app_id: '',
  getui_app_key: '',
  getui_app_master_secret: '',
  push_min_confidence: 0.6,
  push_class_filter: []
})
const pushClassText = ref('')
const savingCfg = ref(false)

const healthAllOk = computed(() => health.expected > 0 && health.healthy >= health.expected)
const healthPct = computed(() => {
  if (!health.expected) return 0
  return Math.round((health.healthy / health.expected) * 100)
})

onMounted(() => {
  loadHealth()
  loadPush()
})

async function loadHealth() {
  try {
    const h = await get('/api/health')
    health.tasks = h.tasks || []
    health.expected = h.expected_count || 0
    health.healthy = h.healthy_count || 0
  } catch (e) { /* 静默 */ }
}

async function loadPush() {
  try {
    const s = await get('/api/push/status')
    pushStatus.value = s || {}
  } catch (e) { /* 静默 */ }

  if (!isAdmin.value) return
  try {
    const d = await get('/api/push/config')
    const c = (d && d.config) || d || {}
    cfg.push_enabled = !!c.push_enabled
    cfg.getui_app_id = c.getui_app_id || ''
    cfg.getui_app_key = c.getui_app_key || ''
    cfg.getui_app_master_secret = c.getui_app_master_secret || ''
    cfg.push_min_confidence = c.push_min_confidence != null ? c.push_min_confidence : 0.6
    const f = c.push_class_filter
    if (Array.isArray(f)) {
      cfg.push_class_filter = f
      pushClassText.value = f.join(',')
    } else if (typeof f === 'string' && f) {
      pushClassText.value = f
      cfg.push_class_filter = []
    }
  } catch (e) { /* 非管理员或未开放 */ }
}

async function saveCfg() {
  if (savingCfg.value) return
  savingCfg.value = true
  const minConf = Number(cfg.push_min_confidence)
  const payload = {
    config: {
      push_enabled: !!cfg.push_enabled,
      getui_app_id: (cfg.getui_app_id || '').trim(),
      getui_app_key: (cfg.getui_app_key || '').trim(),
      getui_app_master_secret: (cfg.getui_app_master_secret || '').trim() || '******',
      push_min_confidence: isNaN(minConf) ? 0.6 : Math.min(1, Math.max(0, minConf)),
      push_class_filter: pushClassText.value.trim()
        ? pushClassText.value.split(/[,，]/).map(s => s.trim()).filter(Boolean)
        : []
    }
  }
  try {
    await put('/api/push/config', payload)
    uni.showToast({ title: '已保存', icon: 'success' })
    loadPush()
  } catch (e) {
    uni.showToast({ title: e.message || '保存失败', icon: 'none' })
  }
  savingCfg.value = false
}
</script>

<style lang="scss" scoped>
.sys-inner { padding: 24rpx; }

.health-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.health-num {
  font-size: 52rpx;
  font-weight: 800;
  font-family: 'DIN Alternate', sans-serif;
}
.num-ok { color: $success; }
.num-bad { color: $warning; }
.health-label { font-size: 24rpx; color: $text-3; margin-top: 4rpx; display: block; }

.health-bar {
  height: 12rpx;
  background: $surface-2;
  border-radius: 6rpx;
  margin: 20rpx 0;
  overflow: hidden;
}
.health-bar-inner {
  height: 100%;
  background: $grad-primary;
  border-radius: 6rpx;
  transition: width .6s ease;
}

.h-task {
  display: flex;
  align-items: center;
  gap: 14rpx;
  padding: 14rpx 0;
  border-bottom: 1rpx solid $border;
}
.h-task:last-child { border-bottom: none; }
.dot { width: 12rpx; height: 12rpx; border-radius: 50%; flex-shrink: 0; }
.dot-ok { background: $success; box-shadow: 0 0 8rpx rgba(18,183,106,.6); }
.dot-bad { background: $danger; box-shadow: 0 0 8rpx rgba(217,45,32,.5); }
.h-name { font-size: 26rpx; color: $text-1; }
.h-stage { font-size: 22rpx; }
.h-heart {
  font-size: 22rpx;
  color: $text-3;
  font-family: 'DIN Alternate', sans-serif;
  min-width: 64rpx;
  text-align: right;
}
.h-heart-bad { color: $warning; }
.h-none { font-size: 24rpx; padding: 16rpx 0; }

.push-status-row {
  display: flex;
  align-items: center;
  gap: 16rpx;
  margin-bottom: 28rpx;
}
.switch-title { font-size: 28rpx; color: $text-1; font-weight: 600; }
.switch-desc { font-size: 24rpx; color: $text-3; margin-top: 6rpx; }
.form-tip { font-size: 22rpx; color: $text-3; margin-top: 16rpx; }
.push-status-row + .form-tip { margin-top: 0; }
.card .push-status-row:last-child { margin-bottom: 0; }

.about-row { display: flex; align-items: center; gap: 20rpx; }
.about-logo {
  width: 88rpx;
  height: 88rpx;
  border-radius: 24rpx;
  background: $grad-primary;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: $shadow-glow;
}
.about-name { display: block; font-size: 28rpx; font-weight: 700; color: $text-1; }
.about-ver { display: block; font-size: 22rpx; color: $text-3; margin-top: 6rpx; }
.about-meta {
  display: flex;
  flex-direction: column;
  gap: 10rpx;
  margin-top: 24rpx;
  padding-top: 24rpx;
  border-top: 1rpx solid $border;
}
.about-meta text { font-size: 24rpx; color: $text-3; }
</style>
