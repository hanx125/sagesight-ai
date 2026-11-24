/**
 * 图标系统 v5 —— 「信号白 SIGNAL WHITE」风格
 *
 * 登录后主界面：墨黑描边、青绿激活；默认冷白 → 墨黑。
 * 登录页独立深色，其调用点已显式传青绿/冷白色值，不受默认值影响。
 * 旧主题色值由 COLOR_MAP 自动收敛，历史调用点无需逐个修改。
 *
 * 用法：icon('search')                    → data URI（默认墨黑）
 *      icon('home', '#00A88F', 2, true)  → 实心版本（第四个参数）
 */

const PATHS = {
  /* ===== 导航 ===== */
  home: '<path d="M3 9.4 12 2.6l9 6.8V20a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22v-9h6v9"/>',
  video: '<path d="M22 8.2 16 12l6 3.8z"/><rect x="2" y="6" width="14" height="12" rx="1.5"/>',
  bell: '<path d="M6 8.4a6 6 0 0 1 12 0c0 6.6 3 8.6 3 8.6H3s3-2 3-8.6"/><path d="M10.2 21a2 2 0 0 0 3.6 0"/>',
  user: '<path d="M19.5 21v-1.8a4.2 4.2 0 0 0-4.2-4.2H8.7a4.2 4.2 0 0 0-4.2 4.2V21"/><circle cx="12" cy="6.8" r="4.2"/>',
  grid: '<rect x="3" y="3" width="7.5" height="7.5"/><rect x="13.5" y="3" width="7.5" height="7.5"/><rect x="3" y="13.5" width="7.5" height="7.5"/><rect x="13.5" y="13.5" width="7.5" height="7.5"/>',
  list: '<path d="M3 5h6v6H3z"/><path d="m3 17 2 2 4-4"/><path d="M13 6h8"/><path d="M13 12h8"/><path d="M13 18h8"/>',

  /* ===== 基础操作 ===== */
  search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',
  minus: '<path d="M5 12h14"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-3.2-6.9"/><path d="M21 3.5V9h-5.5"/>',
  play: '<path d="M6.5 3.6 20 12 6.5 20.4z"/>',
  stop: '<rect x="5" y="5" width="14" height="14" rx="1"/>',
  pause: '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>',
  settings: '<path d="M4 6h7"/><path d="M15 6h5"/><path d="M4 12h3"/><path d="M11 12h9"/><path d="M4 18h9"/><path d="M17 18h3"/><rect x="11" y="3.6" width="4" height="4.8" rx="1"/><rect x="7" y="9.6" width="4" height="4.8" rx="1"/><rect x="13" y="15.6" width="4" height="4.8" rx="1"/>',
  sliders: '<path d="M4 21v-7"/><path d="M4 10V3"/><path d="M12 21v-9"/><path d="M12 8V3"/><path d="M20 21v-5"/><path d="M20 12V3"/><path d="M1.5 14h5"/><path d="M9.5 8h5"/><path d="M17.5 16h5"/>',
  'chevron-right': '<path d="m9 18 6-6-6-6"/>',
  'chevron-left': '<path d="m15 18-6-6 6-6"/>',
  'chevron-down': '<path d="m6 9 6 6 6-6"/>',
  'chevron-up': '<path d="m6 15 6-6 6 6"/>',
  'arrow-left': '<path d="M20 12H4"/><path d="m10 19-7-7 7-7"/>',
  'arrow-right': '<path d="M4 12h16"/><path d="m14 5 7 7-7 7"/>',
  'arrow-up-right': '<path d="M7 17 17 7"/><path d="M8 7h9v9"/>',
  close: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  check: '<path d="m20 6-11 11-5-5"/>',
  edit: '<path d="M16.4 3.4a2.3 2.3 0 0 1 3.2 3.2L7 19.2 2.5 20.5 3.8 16z"/>',
  trash: '<path d="M3 6h18"/><path d="M18.5 6v14a2 2 0 0 1-2 2h-9a2 2 0 0 1-2-2V6"/><path d="M8.5 6V4a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v2"/><path d="M10 11v6"/><path d="M14 11v6"/>',
  filter: '<path d="M22 3H2l8 9.5V19l4 2v-8.5z"/>',
  more: '<circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/>',

  /* ===== 业务 ===== */
  camera: '<path d="M15 4H9L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3z"/><circle cx="12" cy="13.4" r="3.4"/>',
  mic: '<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 11v1a7 7 0 0 0 14 0v-1"/><path d="M12 19v3"/>',
  'mic-off': '<path d="M2 2l20 20"/><path d="M9 6.4V5a3 3 0 0 1 5.6-1.5"/><path d="M15 10.6V11a3 3 0 0 1-4.2 2.7"/><path d="M5 11v1a7 7 0 0 0 10.6 6"/><path d="M19 12v0a7 7 0 0 1-.6 2.8"/><path d="M12 19v3"/>',
  volume: '<path d="M11 5 6 9H2v6h4l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.6 5.4a9.5 9.5 0 0 1 0 13.2"/>',
  zap: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
  target: '<circle cx="12" cy="12" r="9.4"/><circle cx="12" cy="12" r="5.4"/><path d="M12 2v3.4"/><path d="M12 18.6V22"/><path d="M2 12h3.4"/><path d="M18.6 12H22"/>',
  shield: '<path d="M12 2.2 20 5v7c0 4.8-3.4 8.4-8 9.8-4.6-1.4-8-5-8-9.8V5z"/><path d="m8.6 12 2.4 2.4 4.4-4.6"/>',
  activity: '<path d="M22 12h-5l-3 9-4-18-3 9H2"/>',
  clock: '<circle cx="12" cy="12" r="9.4"/><path d="M12 7v5.4l3.6 2"/>',
  link: '<path d="M10 13.5a4.8 4.8 0 0 0 7.2.5l2.4-2.4a4.8 4.8 0 0 0-6.8-6.8L11.6 6"/><path d="M14 10.5a4.8 4.8 0 0 0-7.2-.5l-2.4 2.4a4.8 4.8 0 0 0 6.8 6.8L12.4 18"/>',
  logout: '<path d="M9.5 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4.5"/><path d="M16 16.5 20.5 12 16 7.5"/><path d="M20.5 12H9"/>',
  alert: '<path d="M12 3 22 21H2z"/><path d="M12 9.6v4.8"/><path d="M12 17.6v.4"/>',
  server: '<rect x="2" y="3" width="20" height="7" rx="1.5"/><rect x="2" y="14" width="20" height="7" rx="1.5"/><path d="M6 6.5h.5"/><path d="M6 17.5h.5"/><path d="M10 6.5h4"/><path d="M10 17.5h4"/>',
  eye: '<path d="M2 12s3.8-6.6 10-6.6S22 12 22 12s-3.8 6.6-10 6.6S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.6" cy="9" r="2"/><path d="m21 15.5-4.6-4.6L5 22"/>',
  wifi: '<path d="M2.5 8.6a14 14 0 0 1 19 0"/><path d="M6 12.4a9 9 0 0 1 12 0"/><path d="M9.4 16.2a4.2 4.2 0 0 1 5.2 0"/><path d="M12 20v.4"/>',
  inbox: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.8 4H7.2a2 2 0 0 0-1.7 1.1z"/>',
  monitor: '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8"/><path d="M12 17v4"/>',
  phone: '<rect x="5" y="2" width="14" height="20" rx="2.4"/><path d="M10.6 18.4h2.8"/>',
  info: '<circle cx="12" cy="12" r="9.4"/><path d="M12 16.5V11"/><path d="M12 7.8v.4"/>',
  'alert-circle': '<circle cx="12" cy="12" r="9.4"/><path d="M12 7.4v6"/><path d="M12 16.6v.4"/>',
  scan: '<path d="M3 8V5a2 2 0 0 1 2-2h3"/><path d="M16 2.6h3a2 2 0 0 1 2 2v3"/><path d="M21 16v3a2 2 0 0 1-2 2h-3"/><path d="M8 21.4H5a2 2 0 0 1-2-2v-3"/><path d="M3 12h18"/>',
  radar: '<circle cx="12" cy="12" r="9.4"/><circle cx="12" cy="12" r="5"/><path d="M12 12 19 5.4"/><circle cx="12" cy="12" r="1"/>',
  film: '<rect x="2.6" y="3" width="18.8" height="18" rx="2"/><path d="M7.4 3v18"/><path d="M16.6 3v18"/><path d="M2.6 8.4h4.8"/><path d="M2.6 15.6h4.8"/><path d="M16.6 8.4h4.8"/><path d="M16.6 15.6h4.8"/>',
  layers: '<path d="m12 2.6 9.4 5-9.4 5-9.4-5z"/><path d="m2.6 12.4 9.4 5 9.4-5"/><path d="m2.6 16.8 9.4 5 9.4-5"/>',
  cpu: '<rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M9 2v3"/><path d="M15 2v3"/><path d="M9 19v3"/><path d="M15 19v3"/><path d="M2 9h3"/><path d="M2 15h3"/><path d="M19 9h3"/><path d="M19 15h3"/>',
  'hard-drive': '<path d="M22 12H2"/><path d="M5.5 5h13l3.5 7v5a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-5z"/><path d="M6.5 16h.5"/><path d="M11 16h.5"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 2.6V6"/><path d="M16 2.6V6"/><path d="M3 10.4h18"/>',
  mail: '<rect x="2" y="4.6" width="20" height="14.8" rx="2"/><path d="m2.8 6.4 9.2 6.6 9.2-6.6"/>',
  lock: '<rect x="4" y="10.4" width="16" height="11" rx="2"/><path d="M8 10.4V7.6a4 4 0 0 1 8 0v2.8"/><path d="M12 15v2"/>',
  power: '<path d="M12 3v9"/><path d="M6.8 6.6a7.4 7.4 0 1 0 10.4 0"/>',
  hash: '<path d="M4 9h16"/><path d="M4 15h16"/><path d="M10 3 8 21"/><path d="M16 3l-2 18"/>',
  tag: '<path d="M3 11.4V4a1 1 0 0 1 1-1h7.4a2 2 0 0 1 1.4.6l8 8a2 2 0 0 1 0 2.8l-6 6a2 2 0 0 1-2.8 0l-8-8a2 2 0 0 1-.6-1.4z"/><circle cx="7.4" cy="7.4" r="1.4"/>',
  folder: '<path d="M3 7.4A2 2 0 0 1 5 5.4h4.2l2 2.6H19a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  users: '<path d="M16.6 21v-1.8a4.2 4.2 0 0 0-4.2-4.2H6.2A4.2 4.2 0 0 0 2 19.2V21"/><circle cx="9.4" cy="6.8" r="4.2"/><path d="M22 21v-1.8a4.2 4.2 0 0 0-3.2-4.1"/><path d="M16 3.1a4.2 4.2 0 0 1 0 7.4"/>',
  'map-pin': '<path d="M20 10.4c0 5.6-8 12-8 12s-8-6.4-8-12a8 8 0 0 1 16 0z"/><circle cx="12" cy="10.2" r="2.8"/>',
  expand: '<path d="M3 9V5a2 2 0 0 1 2-2h4"/><path d="M15 3h4a2 2 0 0 1 2 2v4"/><path d="M21 15v4a2 2 0 0 1-2 2h-4"/><path d="M9 21H5a2 2 0 0 1-2-2v-4"/>',
  compass: '<circle cx="12" cy="12" r="9.4"/><path d="m15.6 8.4-2 5.2-5.2 2 2-5.2z"/>',

  /* 品牌：圆环 + 对勾（fill 型） */
  logo: '<path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/>'
}

/* 实心版本（底部导航激活态用，比描边更有"印章"重量） */
const FILLED = {
  home: '<path d="M12 2.2 1.9 9.9a1 1 0 0 0 .6 1.8h1.5v8.1A2.2 2.2 0 0 0 6.2 22h3.3v-6.2h5v6.2h3.3a2.2 2.2 0 0 0 2.2-2.2v-8.1h1.5a1 1 0 0 0 .6-1.8z"/>',
  video: '<rect x="1.6" y="5.4" width="14.4" height="13.2" rx="2.4"/><path d="M17.4 10.2 21 7.4a.9.9 0 0 1 1.4.7v7.8a.9.9 0 0 1-1.4.7l-3.6-2.8z"/>',
  bell: '<path d="M12 2a6.6 6.6 0 0 0-6.6 6.6c0 5.4-1.2 7-2.1 7.9a.9.9 0 0 0 .6 1.6h16.2a.9.9 0 0 0 .6-1.6c-.9-.9-2.1-2.5-2.1-7.9A6.6 6.6 0 0 0 12 2z"/><path d="M9.2 20.2a3 3 0 0 0 5.6 0z"/>',
  user: '<circle cx="12" cy="7" r="4.4"/><path d="M12 13.4c-4.1 0-7.4 2.3-7.4 5.2 0 .8.6 1.4 1.4 1.4h12c.8 0 1.4-.6 1.4-1.4 0-2.9-3.3-5.2-7.4-5.2z"/>',
  play: '<path d="M6.5 3.6 20 12 6.5 20.4z"/>',
  check: '<path d="m20 6-11 11-5-5 1.9-1.9L9 13.2 18.1 4.1z"/>',
  zap: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
  alert: '<path d="M12 2.4 23 21.6H1z"/><path d="M11 9.6h2v5.4h-2zM11 16.8h2v2h-2z" fill="#fff"/>'
}

/**
 * 旧主题色 → 纸墨色映射。
 * 未改写的页面里 icon() 仍传旧紫/蓝/灰，这里统一收敛到墨色系，
 * 避免新纸面上出现紫色的"上一代残留"。
 */
const COLOR_MAP = {
  // v4 深空青绿 → v5 浅色
  '#e8f1f5': '#111210', '#00e5c3': '#00A88F', '#38bdf8': '#2E6BE6',
  '#81939c': '#55584F', '#b8c9d0': '#55584F',
  '#04121c': '#111210', '#ffffff': '#111210', '#FFFFFF': '#111210',
  '#4caf50': '#00A88F', '#66bb6a': '#00A88F', '#ef5350': '#E8342C',
  '#faad14': '#E0930A',
  // 纸墨主题（更早）
  '#17150E': '#111210', '#5D5647': '#55584F', '#9A9179': '#94988C',
  '#BF4326': '#00A88F', '#8E2E18': '#007A66', '#27506F': '#2E6BE6',
  '#C9C1AD': '#94988C', '#F3EFE6': '#111210',
  '#2C6B4C': '#00A88F', '#8F5F0C': '#E0930A', '#A32F22': '#E8342C',
  // 星云紫主题（更早）
  '#B794FF': '#00A88F', '#C4A8FF': '#00A88F', '#A78BFA': '#00A88F',
  '#F472B6': '#E8342C', '#22D3EE': '#2E6BE6', '#4FD1E8': '#2E6BE6',
  '#D6C2FF': '#55584F', '#F6F1FF': '#111210',
  // 更早的蓝色主题
  '#0a5fd9': '#00A88F', '#0A5FD9': '#00A88F', '#1570EF': '#00A88F',
  '#0E9BD8': '#2E6BE6', '#3B9EFF': '#2E6BE6', '#5AA9FF': '#2E6BE6',
  '#22C8E8': '#2E6BE6', '#22B8FF': '#2E6BE6', '#2E8BFF': '#2E6BE6',
  '#16D3E8': '#2E6BE6', '#6366F1': '#2E6BE6', '#38BDF8': '#2E6BE6',
  // 灰阶
  '#667085': '#55584F', '#98A2B3': '#94988C', '#334155': '#111210',
  '#475467': '#55584F', '#7E8DAA': '#94988C', '#93A3C0': '#94988C',
  '#A7B4CE': '#55584F', '#6E7D9C': '#94988C', '#8C83AE': '#55584F',
  '#9389B8': '#94988C', '#A99FCB': '#55584F', '#C6BCDE': '#55584F',
  '#B0A7C9': '#94988C', '#EAF0FB': '#111210', '#101828': '#111210',
  '#2A3648': '#55584F', '#3A4A6B': '#55584F', '#5B5080': '#2E6BE6',
  // 语义色
  '#D92D20': '#E8342C', '#FF6B78': '#E8342C', '#FF5C6C': '#E8342C',
  '#FB7185': '#E8342C', '#B45309': '#E0930A', '#F5A524': '#E0930A',
  '#FBBF24': '#E0930A', '#F59E0B': '#E0930A', '#079455': '#00A88F',
  '#10B981': '#00A88F', '#34D399': '#00A88F', '#4ADE80': '#00A88F',
  '#8FD6A8': '#00A88F', '#62C888': '#00A88F', '#E2A03C': '#E0930A'
}

function mapColor(c) {
  return COLOR_MAP[c] || c
}

/**
 * 生成图标 data URI
 * @param {string} name 图标名
 * @param {string} color 颜色（旧主题色会自动映射到纸墨色）
 * @param {number} stroke 线宽
 * @param {boolean} filled 是否使用实心版本（无实心版本时自动回退描边）
 */
export function icon(name, color = '#111210', stroke = 2.2, filled = false) {
  const c = mapColor(color)
  const solid = filled ? FILLED[name] : null
  if (solid) {
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="${c}">${solid}</svg>`
    return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg)
  }
  const body = PATHS[name]
  if (!body) return ''
  if (name === 'logo') {
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="${c}">${body}</svg>`
    return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg)
  }
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="${c}" stroke-width="${stroke}" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`
  return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg)
}

export default icon
