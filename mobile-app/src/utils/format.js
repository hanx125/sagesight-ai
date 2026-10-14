/**
 * 格式化工具
 */

/** 相对时间：刚刚 / n分钟前 / n小时前 / 昨天 / MM-DD / YYYY-MM-DD */
export function timeAgo(ts) {
  if (!ts) return '-'
  const d = normalizeDate(ts)
  if (!d) return '-'
  const diff = Date.now() - d.getTime()
  const min = 60 * 1000, hour = 60 * min, day = 24 * hour
  if (diff < min) return '刚刚'
  if (diff < hour) return Math.floor(diff / min) + ' 分钟前'
  if (diff < day) return Math.floor(diff / hour) + ' 小时前'
  if (diff < 2 * day) return '昨天 ' + pad(d.getHours()) + ':' + pad(d.getMinutes())
  const sameYear = d.getFullYear() === new Date().getFullYear()
  return sameYear
    ? `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
    : `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

export function formatTime(ts) {
  const d = normalizeDate(ts)
  if (!d) return '-'
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function normalizeDate(ts) {
  if (ts instanceof Date) return ts
  if (typeof ts === 'number') return new Date(ts)
  if (typeof ts === 'string') {
    // "2026-10-11 12:00:00" iOS 兼容
    const s = ts.replace('T', ' ').replace(/\.\d+$/, '')
    const m = s.match(/^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})/)
    if (m) return new Date(m[1] + 'T' + m[2])
    const d = new Date(s)
    return isNaN(d.getTime()) ? null : d
  }
  return null
}

function pad(n) { return n < 10 ? '0' + n : '' + n }

/** 置信度百分比 */
export function percent(v) {
  const n = Number(v)
  if (isNaN(n)) return '-'
  return (n * 100).toFixed(1) + '%'
}

/** 事件状态 → 徽标文案/类型 */
export function eventStatusMeta(status) {
  switch (status) {
    case 'initial': return { text: '待确认', type: 'warning' }
    case 'final': return { text: '已确认', type: 'success' }
    case 'duplicate': return { text: '重复', type: 'gray' }
    default: return { text: status || '-', type: 'gray' }
  }
}

/** 任务状态 → 徽标 */
export function taskStatusMeta(task) {
  if (task.status === 'running' && task.process_alive !== false) {
    return { text: '运行中', type: 'success' }
  }
  if (task.status === 'running') return { text: '异常', type: 'danger' }
  if (task.status === 'starting') return { text: '启动中', type: 'info' }
  if (task.status === 'error') return { text: '错误', type: 'danger' }
  if (task.enabled) return { text: '已启用', type: 'cyan' }
  return { text: '已停止', type: 'gray' }
}

/** 事件类型 → 中文 */
const EVENT_TYPE_ZH = {
  tracking: '目标跟踪', zone: '区域入侵', zone_enter: '区域入侵',
  loitering: '徘徊停留', people_counting: '人员计数', counting: '目标计数',
  occupancy: '区域占用', plate: '车牌检测', face: '人脸识别', detection: '目标检测'
}
export function eventTypeZh(t) {
  if (!t) return ''
  if (String(t).indexOf('attribute') === 0) return '属性识别'
  return EVENT_TYPE_ZH[t] || t
}

/** 中文类名映射（常见 COCO 类） */
const CLASS_ZH = {
  person: '行人', bicycle: '自行车', car: '汽车', motorcycle: '摩托车', bus: '公交车',
  truck: '卡车', dog: '狗', cat: '猫', bird: '鸟', horse: '马', sheep: '羊',
  cow: '牛', elephant: '大象', bear: '熊', zebra: '斑马', giraffe: '长颈鹿',
  backpack: '背包', umbrella: '雨伞', handbag: '手提包', suitcase: '行李箱',
  bottle: '瓶子', cup: '杯子', chair: '椅子', tv: '电视', laptop: '笔记本电脑',
  mouse: '鼠标', remote: '遥控器', phone: '手机', book: '书本', clock: '时钟',
  fire: '火焰', smoke: '烟雾'
}
export function classNameZh(name) {
  if (!name) return '-'
  return CLASS_ZH[name.toLowerCase()] || name
}
