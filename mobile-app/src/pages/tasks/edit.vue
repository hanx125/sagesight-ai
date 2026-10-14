<template>
  <view class="page-wrap">
    <nav-bar :title="isEdit ? '编辑任务' : '新建任务'" />

    <view class="edit-inner">
      <!-- 基本信息 -->
      <text class="section-title">基本信息</text>
      <view class="card">
        <view class="form-item">
          <view class="form-label">任务名称<text class="req">*</text></view>
          <view class="input-box">
            <input class="input" v-model="form.task_name" placeholder="输入任务名称"
              placeholder-class="placeholder" />
          </view>
        </view>

        <view class="form-item" style="margin-bottom:0;">
          <view class="form-label">启用状态</view>
          <view class="switch-row">
            <text class="switch-desc">启用后可纳入调度运行</text>
            <switch :checked="form.enabled" color="#0a5fd9" style="transform:scale(.85);"
              @change="form.enabled = $event.detail.value" />
          </view>
        </view>
      </view>

      <!-- 检测配置 -->
      <text class="section-title">检测配置</text>
      <view class="card">
        <view class="form-item">
          <view class="form-label">检测类型<text class="req">*</text></view>
          <picker mode="selector" :range="DETECT_TYPES.map(d => d.label)" @change="onDetectChange">
            <view class="picker-box">
              <text>{{ detectLabel }}</text>
              <image :src="icon('chevron-down', '#667085', 2.4)" style="width:32rpx;height:32rpx;" />
            </view>
          </picker>
        </view>

        <view class="form-item">
          <view class="form-label">算法配置<text class="req">*</text></view>
          <picker mode="selector" :range="algoOptions" @change="onAlgoChange">
            <view class="picker-box" :class="{ 'picker-empty': !selectedAlgo }">
              <text>{{ selectedAlgo ? `${selectedAlgo.name} (${selectedAlgo.type})` : '-- 请选择算法配置 --' }}</text>
              <image :src="icon('chevron-down', '#667085', 2.4)" style="width:32rpx;height:32rpx;" />
            </view>
          </picker>
        </view>

        <view class="form-item">
          <view class="form-label">事件类型</view>
          <picker mode="selector" :range="EVENT_TYPES.map(d => d.label)" @change="onEventChange">
            <view class="picker-box">
              <text>{{ eventLabel }}</text>
              <image :src="icon('chevron-down', '#667085', 2.4)" style="width:32rpx;height:32rpx;" />
            </view>
          </picker>
        </view>

        <view class="form-item">
          <view class="form-label">置信度阈值</view>
          <view class="input-box">
            <input class="input" type="digit" v-model="form.conf_threshold" placeholder="0.25"
              placeholder-class="placeholder" />
          </view>
          <view class="form-tip">低于此阈值的检测框会被过滤（0-1，默认 0.25）</view>
        </view>

        <view class="form-item" v-if="form.detection_type === 'polling'">
          <view class="form-label">轮询抽样间隔（帧）</view>
          <view class="input-box">
            <input class="input" type="number" v-model="form.polling_interval_frames" placeholder="5"
              placeholder-class="placeholder" />
          </view>
        </view>

        <view class="form-item" v-if="form.detection_type === 'motion_gated'">
          <view class="form-label">无运动兜底补抽间隔（帧）</view>
          <view class="input-box">
            <input class="input" type="number" v-model="form.motion_gate_fallback_interval" placeholder="30"
              placeholder-class="placeholder" />
          </view>
        </view>

        <template v-if="form.detection_type === 'scheduled'">
          <view class="form-item">
            <view class="form-label">定时 Cron 表达式</view>
            <view class="input-box">
              <input class="input" v-model="form.schedule_cron" placeholder="如 0 2 * * *（每天 02:00）"
                placeholder-class="placeholder" />
            </view>
          </view>
          <view class="form-item">
            <view class="form-label">扫描来源目录</view>
            <view class="input-box">
              <input class="input" v-model="form.schedule_source" placeholder="定时扫描目录下的视频文件"
                placeholder-class="placeholder" />
            </view>
          </view>
        </template>

        <view class="form-item" v-if="form.detection_type === 'file' || form.detection_type === 'scheduled'">
          <view class="form-label">视频文件路径列表（每行一个）</view>
          <view class="input-box textarea-box">
            <textarea class="textarea" v-model="filePathsText" auto-height maxlength="-1"
              placeholder="D:\videos\a.mp4&#10;/recordings/b.mp4" placeholder-class="placeholder" />
          </view>
        </view>

        <view class="form-item" v-if="form.detection_type === 'file'">
          <view class="form-label">循环处理</view>
          <view class="switch-row">
            <text class="switch-desc">文件列表处理完成后从头再播</text>
            <switch :checked="form.file_loop" color="#0a5fd9" style="transform:scale(.85);"
              @change="form.file_loop = $event.detail.value" />
          </view>
        </view>

        <view class="form-item">
          <view class="form-label">模型路径</view>
          <view class="input-box">
            <input class="input" :value="form.model_path" disabled placeholder="选择算法配置后自动填充"
              placeholder-class="placeholder" />
          </view>
        </view>

        <view class="form-item">
          <view class="form-label">目标类别<text class="req">*</text></view>
          <view class="input-box classes-box" @tap="openClasses">
            <text class="classes-text" :class="{ 'picker-empty-text': !form.target_classes.length }">
              {{ form.target_classes.length ? form.target_classes.join('、') : '点击选择目标类别' }}
            </text>
            <view class="classes-btn">选择</view>
          </view>
        </view>

        <view class="form-item">
          <view class="form-label">运动检测</view>
          <view class="switch-row">
            <text class="switch-desc">启用后仅对运动区域进行检测</text>
            <switch :checked="form.motion_detection_enabled" color="#0a5fd9" style="transform:scale(.85);"
              @change="form.motion_detection_enabled = $event.detail.value" />
          </view>
        </view>

        <view class="form-item" style="margin-bottom:0;">
          <view class="form-label">目标跟踪</view>
          <view class="switch-row">
            <text class="switch-desc">对检测目标进行跨帧跟踪</text>
            <switch :checked="form.tracking_enabled" color="#0a5fd9" style="transform:scale(.85);"
              @change="form.tracking_enabled = $event.detail.value" />
          </view>
        </view>
      </view>

      <!-- 设备通道检测池 -->
      <text class="section-title">设备通道检测池</text>
      <view class="card">
        <view class="pool-head">
          <text class="pool-count">已选择 {{ selectedCameraIds.length }} 个通道</text>
          <view class="btn btn-sm btn-primary" @tap="openChannelPicker">
            <image :src="icon('plus', '#FFFFFF', 3)" style="width:24rpx;height:24rpx;" />
            <text>添加通道</text>
          </view>
        </view>

        <view class="pool-item" v-for="c in poolChannels" :key="c.camera_id">
          <view class="flex-1 pool-info">
            <view class="flex-row" style="gap: 12rpx;">
              <text class="pool-name t-ellipsis">{{ c.channel_name || '未命名设备' }}</text>
              <view class="badge badge-cyan" style="flex-shrink:0;">本地</view>
            </view>
            <text class="pool-url t-ellipsis">{{ c.rtsp_url }}</text>
          </view>
          <view class="pool-remove" @tap="removeChannel(c.camera_id)">
            <image :src="icon('trash', '#D92D20', 2.2)" style="width:30rpx;height:30rpx;" />
          </view>
        </view>
        <view v-if="!poolChannels.length" class="pool-empty">
          <text class="t-dim">点击「添加通道」按钮选择设备</text>
        </view>
      </view>

      <view class="btn btn-primary save-btn" :class="{ disabled: saving }" @tap="save">
        <text>{{ saving ? '保存中...' : (isEdit ? '保存修改' : '创建任务') }}</text>
      </view>
      <view style="height: 60rpx;" />
    </view>

    <!-- 目标类别选择弹层 -->
    <view class="mask" v-if="classesShow" @tap="classesShow = false">
      <view class="sheet" @tap.stop>
        <view class="sheet-head">
          <text class="sheet-title">选择目标类别</text>
          <view @tap="classesShow = false">
            <image :src="icon('close', '#667085', 2.4)" style="width:36rpx;height:36rpx;" />
          </view>
        </view>
        <view class="input-box sheet-search">
          <input class="input" v-model="classesKeyword" placeholder="搜索类别..." placeholder-class="placeholder" />
        </view>
        <scroll-view scroll-y class="sheet-list">
          <view class="check-item" v-for="c in filteredClasses" :key="c" @tap="toggleClass(c)">
            <view class="check-box" :class="{ checked: classSelected.includes(c) }">
              <image v-if="classSelected.includes(c)" :src="icon('check', '#FFFFFF', 3)" style="width:26rpx;height:26rpx;" />
            </view>
            <text class="check-label flex-1">{{ c }}</text>
          </view>
          <view v-if="!filteredClasses.length" class="pool-empty">
            <text class="t-dim">{{ algoClasses.length ? '无匹配类别' : '请先选择算法配置' }}</text>
          </view>
        </scroll-view>
        <view class="sheet-foot">
          <view class="btn btn-ghost flex-1" @tap="classesShow = false"><text>取消</text></view>
          <view class="btn btn-primary flex-1" @tap="confirmClasses">
            <text>确定{{ classSelected.length ? `（已选 ${classSelected.length}）` : '' }}</text>
          </view>
        </view>
      </view>
    </view>

    <!-- 通道选择弹层 -->
    <view class="mask" v-if="channelShow" @tap="channelShow = false">
      <view class="sheet" @tap.stop>
        <view class="sheet-head">
          <text class="sheet-title">选择设备通道</text>
          <view @tap="channelShow = false">
            <image :src="icon('close', '#667085', 2.4)" style="width:36rpx;height:36rpx;" />
          </view>
        </view>
        <view class="input-box sheet-search">
          <input class="input" v-model="channelKeyword" placeholder="搜索设备名称 / 地址..." placeholder-class="placeholder" />
        </view>
        <scroll-view scroll-y class="sheet-list">
          <view class="check-item" v-for="cam in filteredCameras" :key="cam.id" @tap="togglePick(cam.id)">
            <view class="check-box" :class="{ checked: picking.includes(cam.id) }">
              <image v-if="picking.includes(cam.id)" :src="icon('check', '#FFFFFF', 3)" style="width:26rpx;height:26rpx;" />
            </view>
            <view class="flex-1" style="min-width:0;">
              <text class="check-label">{{ cam.name || '未命名设备' }}</text>
              <text class="check-sub t-ellipsis">{{ cam.rtsp_stream || cam.source || '' }}</text>
            </view>
            <view class="badge" :class="cam.enabled ? 'badge-success' : 'badge-gray'" style="flex-shrink:0;">
              {{ cam.enabled ? '启用' : '禁用' }}
            </view>
          </view>
          <view v-if="!filteredCameras.length" class="pool-empty">
            <text class="t-dim">暂无可用监控设备</text>
          </view>
        </scroll-view>
        <view class="sheet-foot">
          <view class="btn btn-ghost flex-1" @tap="channelShow = false"><text>取消</text></view>
          <view class="btn btn-primary flex-1" @tap="confirmChannels">
            <text>确认选择{{ picking.length ? `（${picking.length}）` : '' }}</text>
          </view>
        </view>
      </view>
    </view>
  </view>
</template>

<script setup>
import { ref, reactive, computed } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import navBar from '@/components/nav-bar/nav-bar.vue'
import icon from '@/utils/icons'
import { get, post, put } from '@/utils/api'

const DETECT_TYPES = [
  { value: 'realtime', label: '实时检测' },
  { value: 'polling', label: '轮询检测' },
  { value: 'motion_gated', label: '运动门控检测' },
  { value: 'file', label: '离线文件检测' },
  { value: 'scheduled', label: '定时批量检测' }
]
const EVENT_TYPES = [
  { value: 'tracking', label: '目标跟踪' },
  { value: 'zone', label: '区域入侵' },
  { value: 'loitering', label: '徘徊检测' },
  { value: 'people_counting', label: '人员统计' },
  { value: 'face', label: '人脸检测' },
  { value: 'plate', label: '车牌检测' }
]

const isEdit = ref(false)
const taskId = ref('')
const saving = ref(false)

const form = reactive({
  task_name: '',
  enabled: true,
  detection_type: 'realtime',
  algorithm: '',
  model_path: '',
  event_type: 'tracking',
  conf_threshold: 0.25,
  polling_interval_frames: 5,
  motion_gate_fallback_interval: 30,
  schedule_cron: '',
  schedule_source: '',
  file_loop: false,
  target_classes: [],
  motion_detection_enabled: true,
  tracking_enabled: true
})
const filePathsText = ref('')

// 算法与类别
const algorithms = ref([])
const algoClasses = ref([])
const classesShow = ref(false)
const classesKeyword = ref('')
const classSelected = ref([])

// 通道池
const cameras = ref([])
const origChannels = ref([])           // 编辑时任务已绑定的通道（含通道池 id）
const selectedCameraIds = ref([])      // 当前选中的摄像机 ID 集合
const channelShow = ref(false)
const channelKeyword = ref('')
const picking = ref([])

const algoOptions = computed(() => algorithms.value.map(a => `${a.name} (${a.type})`))
const selectedAlgo = computed(() => algorithms.value.find(a => a.name === form.algorithm) || null)
const detectLabel = computed(() => {
  const d = DETECT_TYPES.find(x => x.value === form.detection_type)
  return d ? d.label : form.detection_type
})
const eventLabel = computed(() => {
  const d = EVENT_TYPES.find(x => x.value === form.event_type)
  return d ? d.label : form.event_type
})
const filteredClasses = computed(() => {
  const k = classesKeyword.value.trim().toLowerCase()
  return algoClasses.value.filter(c => !k || String(c).toLowerCase().indexOf(k) !== -1)
})
const filteredCameras = computed(() => {
  const k = channelKeyword.value.trim().toLowerCase()
  return cameras.value.filter(c => {
    if (selectedCameraIds.value.indexOf(String(c.id)) !== -1) return false
    if (!k) return true
    const hay = `${c.name || ''} ${c.rtsp_stream || c.source || ''}`.toLowerCase()
    return hay.indexOf(k) !== -1
  })
})

// 检测池展示数据：已绑定的通道 + 新选的摄像机
const poolChannels = computed(() => {
  const list = []
  const camMap = {}
  cameras.value.forEach(c => { camMap[String(c.id)] = c })
  selectedCameraIds.value.forEach(id => {
    const cam = camMap[id]
    const orig = origChannels.value.find(ch => String(ch.camera_id) === id)
    list.push({
      camera_id: id,
      channel_name: orig ? orig.channel_name : (cam && cam.name) || '',
      rtsp_url: orig ? orig.rtsp_url : (cam && (cam.rtsp_stream || cam.source)) || ''
    })
  })
  return list
})

onLoad(async (opt) => {
  loadAlgorithms()
  loadCameras()
  if (opt && opt.id) {
    isEdit.value = true
    taskId.value = opt.id
    try {
      const t = await get('/api/tasks/' + opt.id)
      fillForm(t)
    } catch (e) {
      uni.showToast({ title: e.message || '加载任务失败', icon: 'none' })
    }
  }
})

function fillForm(t) {
  form.task_name = t.task_name || ''
  form.enabled = t.enabled !== false
  form.detection_type = t.detection_type || 'realtime'
  form.algorithm = t.algorithm || ''
  form.model_path = t.model_path || ''
  form.event_type = t.event_type || 'tracking'
  form.conf_threshold = t.conf_threshold ?? 0.25
  form.polling_interval_frames = t.polling_interval_frames || 5
  form.motion_gate_fallback_interval = t.motion_gate_fallback_interval || 30
  form.schedule_cron = t.schedule_cron || ''
  form.schedule_source = t.schedule_source || ''
  form.file_loop = !!t.file_loop
  form.target_classes = Array.isArray(t.target_classes) ? t.target_classes : []
  form.motion_detection_enabled = t.motion_detection_enabled !== false
  form.tracking_enabled = t.tracking_enabled !== false
  filePathsText.value = Array.isArray(t.file_paths) ? t.file_paths.join('\n') : ''
  origChannels.value = Array.isArray(t.channels) ? t.channels : []
  selectedCameraIds.value = origChannels.value.map(ch => String(ch.camera_id)).filter(Boolean)
  if (form.algorithm) loadClasses()
}

async function loadAlgorithms() {
  try {
    const d = await get('/api/algorithms?status=active')
    algorithms.value = Array.isArray(d) ? d : []
  } catch (e) { /* 静默 */ }
}

async function loadCameras() {
  try {
    const d = await get('/api/cameras')
    cameras.value = (d && d.cameras) || []
  } catch (e) { cameras.value = [] }
}

async function loadClasses() {
  const a = selectedAlgo.value
  if (!a || !a.id) { algoClasses.value = []; return }
  try {
    const d = await get(`/api/algorithms/${a.id}/classes`)
    algoClasses.value = Array.isArray(d) ? d : (d && d.classes) || []
  } catch (e) { algoClasses.value = [] }
}

function onDetectChange(e) {
  form.detection_type = DETECT_TYPES[Number(e.detail.value)].value
}
function onAlgoChange(e) {
  const a = algorithms.value[Number(e.detail.value)]
  if (!a) return
  form.algorithm = a.name
  form.model_path = a.model_path || ''
  form.target_classes = []
  loadClasses()
}
function onEventChange(e) {
  form.event_type = EVENT_TYPES[Number(e.detail.value)].value
}

/* 目标类别弹层 */
function openClasses() {
  if (!form.algorithm) {
    uni.showToast({ title: '请先选择算法配置', icon: 'none' })
    return
  }
  classSelected.value = [...form.target_classes]
  classesKeyword.value = ''
  classesShow.value = true
}
function toggleClass(c) {
  const i = classSelected.value.indexOf(c)
  if (i === -1) classSelected.value.push(c)
  else classSelected.value.splice(i, 1)
}
function confirmClasses() {
  form.target_classes = [...classSelected.value]
  classesShow.value = false
}

/* 通道选择弹层 */
function openChannelPicker() {
  channelKeyword.value = ''
  picking.value = []
  channelShow.value = true
}
function togglePick(id) {
  const s = String(id)
  const i = picking.value.indexOf(s)
  if (i === -1) picking.value.push(s)
  else picking.value.splice(i, 1)
}
function confirmChannels() {
  picking.value.forEach(id => {
    if (selectedCameraIds.value.indexOf(id) === -1) selectedCameraIds.value.push(id)
  })
  channelShow.value = false
}
function removeChannel(cameraId) {
  selectedCameraIds.value = selectedCameraIds.value.filter(id => id !== cameraId)
}

async function save() {
  if (!form.task_name.trim()) { uni.showToast({ title: '请填写任务名称', icon: 'none' }); return }
  if (!form.algorithm) { uni.showToast({ title: '请选择算法配置', icon: 'none' }); return }
  if (!selectedCameraIds.value.length) { uni.showToast({ title: '请至少选择一个设备通道', icon: 'none' }); return }
  if (saving.value) return
  saving.value = true

  // 通道数据：已入库通道走 existing_channel_ids，新选摄像机生成临时通道
  const origIds = new Set(origChannels.value.map(ch => String(ch.camera_id)))
  const existing_channel_ids = origChannels.value
    .filter(ch => selectedCameraIds.value.indexOf(String(ch.camera_id)) !== -1)
    .map(ch => ch.id)
  const camMap = {}
  cameras.value.forEach(c => { camMap[String(c.id)] = c })
  const temp_channels = selectedCameraIds.value
    .filter(id => !origIds.has(id))
    .map(id => {
      const cam = camMap[id] || {}
      return {
        camera_id: cam.id,
        channel_name: cam.name || '',
        rtsp_url: cam.rtsp_stream || cam.source || '',
        channel_source: 'local_platform',
        enabled: true
      }
    })

  const conf = parseFloat(form.conf_threshold)
  const payload = {
    task_name: form.task_name.trim(),
    enabled: !!form.enabled,
    detection_type: form.detection_type,
    event_type: form.event_type || 'tracking',
    conf_threshold: isNaN(conf) ? 0.25 : conf,
    polling_interval_frames: parseInt(form.polling_interval_frames, 10) || 5,
    motion_gate_fallback_interval: parseInt(form.motion_gate_fallback_interval, 10) || 30,
    schedule_cron: form.schedule_cron || '',
    schedule_source: form.schedule_source || '',
    file_paths: filePathsText.value.split('\n').map(s => s.trim()).filter(Boolean),
    file_loop: !!form.file_loop,
    algorithm: form.algorithm,
    model_path: form.model_path || '',
    target_classes: [...form.target_classes],
    existing_channel_ids,
    temp_channels,
    motion_detection_enabled: !!form.motion_detection_enabled,
    tracking_enabled: !!form.tracking_enabled
  }

  try {
    if (isEdit.value) {
      await put('/api/tasks/' + taskId.value, payload)
      uni.showToast({ title: '任务已更新', icon: 'success' })
    } else {
      await post('/api/tasks', payload)
      uni.showToast({ title: '任务已创建', icon: 'success' })
    }
    setTimeout(() => uni.navigateBack(), 500)
  } catch (e) {
    uni.showToast({ title: e.message || '保存任务失败', icon: 'none' })
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
  background: $surface-2;
  border: 1rpx solid $border;
  border-radius: $radius-sm;
  padding: 0 24rpx;
  height: 92rpx;
  font-size: 28rpx;
  color: $text-1;
}
.picker-box.picker-empty { color: $text-3; }
.picker-empty-text { color: $text-3; }

.form-tip {
  font-size: 22rpx;
  color: $text-3;
  margin-top: 12rpx;
  line-height: 1.6;
}

.switch-row { display: flex; align-items: center; gap: 20rpx; }
.switch-desc { flex: 1; font-size: 24rpx; color: $text-3; }

.textarea-box { height: auto; padding: 20rpx 24rpx; }
.textarea { width: 100%; font-size: 26rpx; color: $text-1; line-height: 1.6; min-height: 140rpx; }

.classes-box { justify-content: space-between; gap: 16rpx; }
.classes-text { flex: 1; font-size: 26rpx; color: $text-1; line-height: 1.5; }
.classes-btn {
  flex-shrink: 0;
  padding: 12rpx 32rpx;
  border-radius: $radius-sm;
  background: $primary;
  color: #FFFFFF;
  font-size: 24rpx;
}

/* 通道池 */
.pool-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8rpx; }
.pool-count { font-size: 24rpx; color: $text-2; }
.pool-item {
  display: flex;
  align-items: center;
  gap: 16rpx;
  padding: 20rpx 0;
  border-bottom: 1rpx solid $border;
}
.pool-item:last-child { border-bottom: none; }
.pool-info { display: flex; flex-direction: column; gap: 6rpx; min-width: 0; }
.pool-name { font-size: 28rpx; color: $text-1; font-weight: 600; max-width: 360rpx; }
.pool-url { font-size: 22rpx; color: $text-3; }
.pool-remove { padding: 12rpx; }
.pool-empty { padding: 32rpx 0; text-align: center; font-size: 24rpx; }

.save-btn { margin-top: 12rpx; }
.save-btn.disabled { opacity: .6; }

/* 弹层 */
.mask {
  position: fixed;
  left: 0; top: 0; right: 0; bottom: 0;
  background: rgba(16, 24, 40, .55);
  z-index: 999;
  display: flex;
  align-items: flex-end;
  justify-content: center;
}
.sheet {
  width: 100%;
  max-height: 78vh;
  background: $surface;
  border-radius: 32rpx 32rpx 0 0;
  padding: 32rpx 32rpx calc(32rpx + env(safe-area-inset-bottom));
  display: flex;
  flex-direction: column;
}
.sheet-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24rpx;
}
.sheet-title { font-size: 32rpx; font-weight: 700; color: $text-1; }
.sheet-search { margin-bottom: 20rpx; }
.sheet-list { flex: 1; max-height: 52vh; }
.check-item {
  display: flex;
  align-items: center;
  gap: 20rpx;
  padding: 22rpx 0;
  border-bottom: 1rpx solid $border;
}
.check-item:active { opacity: .7; }
.check-box {
  width: 40rpx;
  height: 40rpx;
  border-radius: 10rpx;
  border: 2rpx solid $border-strong;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.check-box.checked { background: $primary; border-color: $primary; }
.check-label { font-size: 28rpx; color: $text-1; }
.check-sub { display: block; font-size: 22rpx; color: $text-3; margin-top: 4rpx; }
.sheet-foot { display: flex; gap: 20rpx; margin-top: 24rpx; }
</style>
