<template>
  <view class="page-wrap">
    <nav-bar :title="isEdit ? '编辑摄像机' : '添加摄像机'" />

    <view class="edit-inner">
      <!-- 基本信息 -->
      <text class="section-title">基本信息</text>
      <view class="card">
        <view class="form-item">
          <view class="form-label">名称<text class="req">*</text></view>
          <view class="input-box">
            <input class="input" v-model="form.name" placeholder="例如：大门口-东" placeholder-class="placeholder" />
          </view>
        </view>

        <view class="form-item">
          <view class="form-label">流地址（RTSP）<text class="req">*</text></view>
          <view class="input-box">
            <input class="input" v-model="form.source" placeholder="rtsp://user:pass@ip:554/stream"
              placeholder-class="placeholder" />
          </view>
        </view>

        <view class="form-item">
          <view class="form-label">接入协议</view>
          <picker mode="selector" :range="PROTOCOLS" @change="onProtoChange">
            <view class="picker-box">
              <text>{{ form.protocol || 'rtsp' }}</text>
              <image :src="icon('chevron-down', '#55584F', 2.4)" style="width:32rpx;height:32rpx;" />
            </view>
          </picker>
        </view>

        <view class="form-item">
          <view class="form-label">厂商</view>
          <view class="input-box">
            <input class="input" v-model="form.manufacturer" placeholder="例如：海康威视 / 大华（可选）"
              placeholder-class="placeholder" />
          </view>
        </view>
      </view>

      <!-- 网络信息 -->
      <text class="section-title">网络信息</text>
      <view class="card">
        <view class="form-row">
          <view class="form-item form-col">
            <view class="form-label">IP 地址</view>
            <view class="input-box">
              <input class="input" v-model="form.ip" placeholder="192.168.1.100" placeholder-class="placeholder" />
            </view>
          </view>
          <view class="form-item form-col">
            <view class="form-label">端口</view>
            <view class="input-box">
              <input class="input" type="number" v-model="form.port" placeholder="554" placeholder-class="placeholder" />
            </view>
          </view>
        </view>

        <view class="form-row">
          <view class="form-item form-col">
            <view class="form-label">用户名</view>
            <view class="input-box">
              <input class="input" v-model="form.username" placeholder="可选" placeholder-class="placeholder" />
            </view>
          </view>
          <view class="form-item form-col">
            <view class="form-label">密码</view>
            <view class="input-box">
              <input class="input" v-model="form.password" password :placeholder="isEdit ? '留空则不修改密码' : '可选'"
                placeholder-class="placeholder" />
            </view>
          </view>
        </view>
      </view>

      <!-- 功能开关 -->
      <text class="section-title">功能设置</text>
      <view class="card">
        <view class="switch-row" style="margin-bottom:28rpx;">
          <view class="flex-1">
            <view class="switch-title">AI 智能检测</view>
            <view class="switch-desc">开启后此摄像机可绑定任务进行分析</view>
          </view>
          <switch :checked="form.ai_detection" color="#00A88F" style="transform:scale(.76);"
            @change="form.ai_detection = $event.detail.value" />
        </view>
        <view class="switch-row">
          <view class="flex-1">
            <view class="switch-title">启用</view>
            <view class="switch-desc">禁用后不参与检测调度</view>
          </view>
          <switch :checked="form.enabled" color="#00A88F" style="transform:scale(.76);"
            @change="form.enabled = $event.detail.value" />
        </view>

        <view class="form-item" style="margin:28rpx 0 0;">
          <view class="form-label">备注说明</view>
          <view class="textarea-box">
            <textarea class="textarea" v-model="form.description" placeholder="选填" placeholder-class="placeholder"
              :maxlength="200" auto-height />
          </view>
        </view>
      </view>

      <view class="btn btn-primary save-btn" :class="{ disabled: saving }" @tap="save">
        <text>{{ saving ? '保存中...' : (isEdit ? '保存修改' : '添加摄像机') }}</text>
      </view>
      <view style="height: 60rpx;" />
    </view>
  </view>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import navBar from '@/components/nav-bar/nav-bar.vue'
import icon from '@/utils/icons'
import { get, post, put } from '@/utils/api'

const PROTOCOLS = ['rtsp', 'onvif', 'gb28181']

const isEdit = ref(false)
const cameraId = ref('')
const saving = ref(false)

const form = reactive({
  name: '',
  source: '',
  protocol: 'rtsp',
  manufacturer: '',
  ip: '',
  port: '',
  username: '',
  password: '',
  ai_detection: true,
  enabled: true,
  description: ''
})

onLoad(async (opt) => {
  if (opt && opt.id) {
    isEdit.value = true
    cameraId.value = opt.id
    try {
      const c = await get('/api/cameras/' + opt.id)
      Object.keys(form).forEach(k => {
        // 后端详情接口会返回明文密码：不回填，留空表示本次不修改，
        // 同时避免保存时把明文密码反复提交一遍。
        if (k === 'password') return
        if (c[k] !== undefined && c[k] !== null) form[k] = c[k]
      })
      if (!form.protocol) form.protocol = 'rtsp'
    } catch (e) {
      uni.showToast({ title: e.message || '加载失败', icon: 'none' })
    }
  }
})

function onProtoChange(e) {
  form.protocol = PROTOCOLS[Number(e.detail.value)]
}

async function save() {
  if (!form.name.trim()) {
    uni.showToast({ title: '请填写摄像机名称', icon: 'none' })
    return
  }
  const source = form.source.trim()
  if (!source) {
    uni.showToast({ title: '请填写流地址（RTSP）', icon: 'none' })
    return
  }
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(source)) {
    uni.showToast({ title: '流地址格式不正确，需带协议前缀（如 rtsp://）', icon: 'none' })
    return
  }
  if (form.ip.trim() && !/^(\d{1,3}\.){3}\d{1,3}$/.test(form.ip.trim())) {
    uni.showToast({ title: 'IP 地址格式不正确', icon: 'none' })
    return
  }
  if (form.port !== '' && form.port !== null && form.port !== undefined) {
    const port = Number(form.port)
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      uni.showToast({ title: '端口需在 1-65535 之间', icon: 'none' })
      return
    }
  }
  if (saving.value) return
  saving.value = true

  const payload = {
    name: form.name.trim(),
    source,
    protocol: form.protocol || 'rtsp',
    manufacturer: form.manufacturer.trim(),
    ip: form.ip.trim(),
    port: form.port ? parseInt(form.port) : null,
    username: form.username,
    ai_detection: !!form.ai_detection,
    enabled: !!form.enabled,
    description: form.description
  }
  // 编辑时密码留空 = 不修改（后端为 merge 语义，不传该字段即保留原值）
  if (form.password) payload.password = form.password

  try {
    if (isEdit.value) {
      await put('/api/cameras/' + cameraId.value, payload)
      uni.showToast({ title: '已保存', icon: 'success' })
    } else {
      await post('/api/cameras', payload)
      uni.showToast({ title: '添加成功', icon: 'success' })
    }
    setTimeout(() => uni.navigateBack(), 500)
  } catch (e) {
    uni.showToast({ title: e.message || '保存失败', icon: 'none' })
  }
  saving.value = false
}
</script>

<style lang="scss" scoped>
.edit-inner { padding: 24rpx; }

.picker-box {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: $surface;
  border: 1rpx solid $line;
  border-radius: 12rpx;
  padding: 0 24rpx;
  height: 92rpx;
  font-size: 28rpx;
  color: $text-1;
}

.form-row { display: flex; gap: 20rpx; }
.form-col { flex: 1; min-width: 0; }

.switch-row { display: flex; align-items: center; gap: 20rpx; }
.switch-title { font-size: 28rpx; color: $text-1; font-weight: 600; }
.switch-desc { font-size: 24rpx; color: $text-3; margin-top: 6rpx; }

.textarea-box {
  background: $surface-2;
  border: 1rpx solid $border;
  border-radius: $radius-sm;
  padding: 20rpx 24rpx;
}
.textarea {
  width: 100%;
  min-height: 120rpx;
  font-size: 26rpx;
  color: $text-1;
  line-height: 1.6;
}

.save-btn { margin-top: 12rpx; }
.save-btn.disabled { opacity: .6; }
</style>
