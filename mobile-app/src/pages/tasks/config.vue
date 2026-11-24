<template>
  <view class="page-wrap">
    <nav-bar :title="taskName ? `配置 - ${taskName}` : '参数配置'" />

    <view class="edit-inner">
      <view class="config-tip">
        <text class="t-dim">置信度阈值在任务编辑中维护；解码、检测、跟踪、运动等参数统一由服务端全局配置（config/*.yaml），所有任务一致，修改后重启任务生效。</text>
      </view>

      <!-- 分组开关渲染（事件输出 / 边缘库 / 显示开关） -->
      <template v-for="g in groups" :key="g.title">
        <text class="section-title">{{ g.title }}</text>
        <view class="card">
          <view class="sw-row" v-for="(it, i) in g.items" :key="it.key"
            :class="{ last: i === g.items.length - 1 && !g.extras }">
            <text class="sw-label">{{ it.label }}</text>
            <switch :checked="sw[it.key]" color="#00A88F" style="transform:scale(.7);"
              @change="sw[it.key] = $event.detail.value" />
          </view>

          <!-- 报警语音播报 -->
          <template v-if="g.voice">
            <view class="sw-row">
              <text class="sw-label">报警时播放语音</text>
              <switch :checked="voice.enabled" color="#00A88F" style="transform:scale(.7);"
                @change="voice.enabled = $event.detail.value" />
            </view>
            <template v-if="voice.enabled">
              <view class="form-item">
                <view class="form-label">播放语音（MP3）</view>
                <picker mode="selector" :range="voiceOptions" @change="onVoiceMp3Change">
                  <view class="picker-box">
                    <text>{{ voice.mp3 || '使用全局默认' }}</text>
                    <image :src="icon('chevron-down', '#55584F', 2.4)" style="width:32rpx;height:32rpx;" />
                  </view>
                </picker>
                <view class="form-tip">来源于系统管理-报警语音播报配置的预置文件</view>
              </view>
              <view class="form-inline">
                <view class="form-item flex-1">
                  <view class="form-label">循环次数</view>
                  <view class="input-box">
                    <input class="input" type="number" v-model="voice.loop_count" placeholder="1"
                      placeholder-class="placeholder" />
                  </view>
                </view>
                <view class="form-item flex-1">
                  <view class="form-label">间隙（毫秒）</view>
                  <view class="input-box">
                    <input class="input" type="number" v-model="voice.interval_ms" placeholder="1000"
                      placeholder-class="placeholder" />
                  </view>
                </view>
              </view>
              <view class="form-item" style="margin-bottom:0;">
                <view class="form-label">音量（0-1）</view>
                <view class="slider-row">
                  <slider class="slider flex-1" :value="volumePercent" :min="0" :max="100" :step="10"
                    activeColor="#00A88F" backgroundColor="rgba(17, 18, 16, .08)" block-size="22"
                    @change="e => voice.volume = Number(e.detail.value) / 100" />
                  <text class="slider-val">{{ volumePercent }}%</text>
                </view>
              </view>
            </template>
          </template>

          <!-- 边缘库比对库名称 -->
          <template v-if="g.library">
            <view class="form-inline">
              <view class="form-item flex-1">
                <view class="form-label">人脸库名称</view>
                <view class="input-box">
                  <input class="input" v-model="faceLibraryName" placeholder="与边缘平台人脸库名称一致"
                    placeholder-class="placeholder" />
                </view>
              </view>
              <view class="form-item flex-1">
                <view class="form-label">车牌库名称</view>
                <view class="input-box">
                  <input class="input" v-model="plateLibraryName" placeholder="与边缘平台车牌库名称一致"
                    placeholder-class="placeholder" />
                </view>
              </view>
            </view>
            <view class="config-note">
              <text class="t-dim">启用后，检测到人脸 / 车牌会调用边缘集成平台的库接口比对，命中结果随事件上报。库名称留空表示该类不比对；边缘平台地址、令牌与超时在系统管理中统一设置。</text>
            </view>
          </template>

          <!-- 组内注释 -->
          <view class="config-note" v-if="g.note">
            <text class="t-dim">{{ g.note }}</text>
          </view>
        </view>
      </template>

      <view class="btn btn-primary save-btn" :class="{ disabled: saving }" @tap="save">
        <text>{{ saving ? '保存中...' : '保存配置' }}</text>
      </view>
      <view style="height: 60rpx;" />
    </view>
  </view>
</template>

<script setup>
import { ref, reactive, computed } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import navBar from '@/components/nav-bar/nav-bar.vue'
import icon from '@/utils/icons'
import { get, put } from '@/utils/api'

const taskId = ref('')
const taskName = ref('')
const saving = ref(false)
const voiceFiles = ref([])
const faceLibraryName = ref('')
const plateLibraryName = ref('')
const voice = reactive({ enabled: false, mp3: '', loop_count: 1, interval_ms: 1000, volume: 1.0 })

// 任务级布尔开关（与 Web 参数配置一致，均为任务表独立列）
const sw = reactive({
  event_enabled: true,
  event_image_local_enabled: true,
  event_image_minio_enabled: true,
  event_image_ftp_enabled: true,
  event_image_auto_cleanup: true,
  event_image_draw_bbox: true,
  event_gif_enabled: true,
  event_gif_local_enabled: true,
  event_gif_minio_enabled: true,
  event_gif_ftp_enabled: true,
  event_gif_auto_cleanup: true,
  event_gif_draw_trajectory: true,
  event_gif_draw_bbox: true,
  event_compression_enabled: true,
  event_capture_save_full_on_start: true,
  event_capture_save_full_on_end: true,
  library_match_enabled: false,
  display_show_detection_boxes: true,
  display_show_track_boxes: true,
  display_show_trajectories: true,
  display_show_motion_regions: true,
  display_show_zones: true,
  display_show_counting_line: true,
  display_loitering_blink: true,
  display_alarm_zone_enter_enabled: true,
  display_alarm_loitering_enabled: true,
  display_alarm_show_banner: true
})

const groups = [
  {
    title: '事件输出开关（任务级）',
    items: [{ key: 'event_enabled', label: '启用事件输出' }],
    voice: true,
    note: '存储目录、压缩质量、GIF 采集帧数与事件去重等参数由 config/event_output.yaml 全局配置，所有任务一致，修改后重启任务生效。'
  },
  {
    title: '事件输出开关（任务级）· 续',
    items: [
      { key: 'event_image_local_enabled', label: '图片 - 本地存储' },
      { key: 'event_image_minio_enabled', label: '图片 - MinIO 上传' },
      { key: 'event_image_ftp_enabled', label: '图片 - FTP 上传' },
      { key: 'event_image_auto_cleanup', label: '图片 - 自动清理过期' },
      { key: 'event_image_draw_bbox', label: '图片 - 绘制检测框' },
      { key: 'event_gif_enabled', label: 'GIF - 启用输出' },
      { key: 'event_gif_local_enabled', label: 'GIF - 本地存储' },
      { key: 'event_gif_minio_enabled', label: 'GIF - MinIO 上传' },
      { key: 'event_gif_ftp_enabled', label: 'GIF - FTP 上传' },
      { key: 'event_gif_auto_cleanup', label: 'GIF - 自动清理过期' },
      { key: 'event_gif_draw_trajectory', label: 'GIF - 绘制运动轨迹' },
      { key: 'event_gif_draw_bbox', label: 'GIF - 绘制检测框' },
      { key: 'event_compression_enabled', label: '启用图片压缩' },
      { key: 'event_capture_save_full_on_start', label: '开始采集时保存原图' },
      { key: 'event_capture_save_full_on_end', label: '结束采集时保存原图' }
    ]
  },
  {
    title: '边缘库比对（任务级）',
    items: [{ key: 'library_match_enabled', label: '启用边缘库比对' }],
    library: true
  },
  {
    title: '显示开关（任务级）',
    items: [
      { key: 'display_show_detection_boxes', label: '显示检测框' },
      { key: 'display_show_track_boxes', label: '显示跟踪框' },
      { key: 'display_show_trajectories', label: '显示运动轨迹' },
      { key: 'display_show_motion_regions', label: '显示运动区域' },
      { key: 'display_show_zones', label: '显示区域（多边形/分界线）' },
      { key: 'display_show_counting_line', label: '显示人员统计计数' },
      { key: 'display_loitering_blink', label: '徘徊目标闪烁' },
      { key: 'display_alarm_zone_enter_enabled', label: '区域入侵报警' },
      { key: 'display_alarm_loitering_enabled', label: '徘徊报警' },
      { key: 'display_alarm_show_banner', label: '报警横幅提示' }
    ],
    note: '线宽、颜色、字体、闪烁频率与横幅样式等参数由 config/display_config.yaml 全局配置，所有任务一致，修改后重启任务生效。'
  }
]

const voiceOptions = computed(() => ['使用全局默认'].concat(voiceFiles.value))
const volumePercent = computed(() => Math.round((Number(voice.volume) || 0) * 100))

onLoad(opt => {
  taskId.value = (opt && opt.id) || ''
  if (!taskId.value) {
    uni.showToast({ title: '缺少任务 ID', icon: 'none' })
    setTimeout(() => uni.navigateBack(), 600)
    return
  }
  load()
})

async function load() {
  try {
    const [task, vf] = await Promise.all([
      get('/api/tasks/' + taskId.value),
      get('/api/system/voice-files').catch(() => null)
    ])
    taskName.value = task.task_name || ''
    // 开关回填：与 Web 一致，未返回时按启用处理
    Object.keys(sw).forEach(k => {
      if (task[k] !== undefined && task[k] !== null) sw[k] = task[k] !== false
    })
    voice.enabled = task.voice_enabled === true
    voice.mp3 = task.voice_mp3 || ''
    voice.loop_count = task.voice_loop_count ?? 1
    voice.interval_ms = task.voice_interval_ms ?? 1000
    voice.volume = task.voice_volume ?? 1.0
    faceLibraryName.value = task.face_library_name || ''
    plateLibraryName.value = task.plate_library_name || ''
    voiceFiles.value = (vf && vf.files) ? vf.files.map(f => f.name) : []
  } catch (e) {
    uni.showToast({ title: e.message || '加载配置失败', icon: 'none' })
  }
}

function onVoiceMp3Change(e) {
  const idx = Number(e.detail.value)
  voice.mp3 = idx <= 0 ? '' : (voiceFiles.value[idx - 1] || '')
}

async function save() {
  if (saving.value) return
  saving.value = true
  const loop = parseInt(voice.loop_count, 10)
  const interval = parseInt(voice.interval_ms, 10)
  const volume = parseFloat(voice.volume)
  const payload = {
    ...sw,
    voice_enabled: voice.enabled,
    voice_mp3: voice.mp3 || '',
    voice_loop_count: isNaN(loop) || loop < 1 ? 1 : loop,
    voice_interval_ms: isNaN(interval) || interval < 0 ? 1000 : interval,
    voice_volume: isNaN(volume) ? 1.0 : Math.min(1, Math.max(0, volume)),
    face_library_name: faceLibraryName.value.trim(),
    plate_library_name: plateLibraryName.value.trim()
  }
  try {
    const task = await get('/api/tasks/' + taskId.value)
    await put('/api/tasks/' + taskId.value, payload)
    if ((task.status || 'stopped') === 'running') {
      uni.showToast({ title: '配置已保存，变更需重启任务后生效', icon: 'none' })
    } else {
      uni.showToast({ title: '配置已保存', icon: 'success' })
    }
    setTimeout(() => uni.navigateBack(), 800)
  } catch (e) {
    uni.showToast({ title: e.message || '保存配置失败', icon: 'none' })
  }
  saving.value = false
}
</script>

<style lang="scss" scoped>
.edit-inner { padding: 24rpx; }

.config-tip {
  padding: 20rpx 24rpx;
  background: $surface-2;
  border-radius: $radius-sm;
  font-size: 22rpx;
  line-height: 1.7;
  margin-bottom: 24rpx;
}

.sw-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20rpx 0;
  border-bottom: 1rpx solid $border;
}
.sw-row.last { border-bottom: none; }
.sw-label { font-size: 27rpx; color: $text-1; }

.config-note {
  padding: 16rpx 0 8rpx;
  font-size: 22rpx;
  line-height: 1.7;
}

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
.form-tip { font-size: 22rpx; color: $text-3; margin-top: 10rpx; }

.form-inline { display: flex; gap: 20rpx; }
.form-inline .form-item { margin-bottom: 20rpx; }

.slider-row { display: flex; align-items: center; gap: 20rpx; }
.slider { margin: 0; }
.slider-val {
  font-size: 28rpx;
  font-weight: 700;
  color: $primary;
  min-width: 90rpx;
  text-align: right;
}

.save-btn { margin-top: 12rpx; }
.save-btn.disabled { opacity: .6; }
</style>
