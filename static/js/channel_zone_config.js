// ========================================
// 通道Zone配置管理
// ========================================

let channelZoneCounter = 0;
let currentChannelZoneTaskId = null;
let currentChannelZoneChannelId = null;

/**
 * 打开通道Zone配置模态框
 */
async function openChannelZoneConfig(channelId, channelName, event) {
    if (event) {
        event.stopPropagation();
    }

    if (!currentTaskId) {
        showNotification('请先选择任务', 'error');
        return;
    }

    currentChannelZoneTaskId = currentTaskId;
    currentChannelZoneChannelId = channelId;

    // 设置模态框标题
    document.getElementById('channelZoneConfigTitle').textContent = `Zone区域配置 - ${channelName}`;
    document.getElementById('channelZoneConfigTaskId').value = currentTaskId;
    document.getElementById('channelZoneConfigChannelId').value = channelId;

    // 清空Zone列表
    document.getElementById('channelZonesContainer').innerHTML = '';
    channelZoneCounter = 0;

    // 加载现有Zone配置
    try {
        const response = await fetch(`/api/tasks/${currentTaskId}/channels/${channelId}/zones`);
        const data = await response.json();

        if (response.ok && data.zones && data.zones.length > 0) {
            // 加载现有Zone配置
            data.zones.forEach((zone, index) => {
                channelZoneCounter++;
                addChannelZoneUI(zone);
            });
        }
    } catch (error) {
        console.log('该通道暂无Zone配置');
    }

    // 显示模态框
    document.getElementById('channelZoneConfigModal').classList.add('show');
}

/**
 * 添加Zone区域
 */
function addChannelZone() {
    channelZoneCounter++;
    addChannelZoneUI();
}

/**
 * 添加Zone区域UI
 */
function addChannelZoneUI(zoneData = null) {
    const container = document.getElementById('channelZonesContainer');

    const zoneDiv = document.createElement('div');
    zoneDiv.className = 'zone-config-item';
    zoneDiv.id = `channelZoneConfig${channelZoneCounter}`;
    zoneDiv.style.cssText = 'border: 1px solid #e0e0e0; border-radius: 8px; padding: 15px; margin-bottom: 15px; background-color: #fafafa;';

    zoneDiv.innerHTML = `
        <div class="form-row" style="margin-bottom: 10px;">
            <div class="form-group" style="flex: 1;">
                <label>区域名称</label>
                <input type="text" id="channelZoneName${channelZoneCounter}" name="zone_name_${channelZoneCounter}" placeholder="例如：入口区域" value="${zoneData ? escapeHtml(zoneData.name || '') : ''}">
            </div>
            <div class="form-group" style="flex: 0 0 auto; margin-left: 20px;">
                <button type="button" class="btn btn-danger" onclick="removeChannelZone('channelZoneConfig${channelZoneCounter}')">删除</button>
            </div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>区域多边形坐标</label>
                <input type="text" id="channelZonePolygon${channelZoneCounter}" name="zone_polygon_${channelZoneCounter}" placeholder="x1,y1;x2,y2;x3,y3;x4,y4" value="${zoneData && zoneData.polygon ? zoneData.polygon.map(p => `${p[0]},${p[1]}`).join(';') : ''}">
                <small style="color: #888; font-size: 11px;">格式：x1,y1;x2,y2;x3,y3;...（至少3个点）</small>
            </div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>是否为必需区域</label>
                <select id="channelZoneRequired${channelZoneCounter}" name="zone_required_${channelZoneCounter}">
                    <option value="false" ${!zoneData || !zoneData.required ? 'selected' : ''}>否</option>
                    <option value="true" ${zoneData && zoneData.required ? 'selected' : ''}>是</option>
                </select>
                <small style="color: #888; font-size: 11px;">必需区域：仅在该区域内的目标才触发事件</small>
            </div>
            <div class="form-group">
                <label>启用徘徊检测</label>
                <select id="channelZoneLoitering${channelZoneCounter}" name="zone_loitering_${channelZoneCounter}">
                    <option value="false" ${!zoneData || !zoneData.enable_loitering_detection ? 'selected' : ''}>禁用</option>
                    <option value="true" ${zoneData && zoneData.enable_loitering_detection ? 'selected' : ''}>启用</option>
                </select>
            </div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>徘徊判定时间（帧）</label>
                <input type="number" id="channelZoneLoiteringTime${channelZoneCounter}" name="zone_loitering_time_${channelZoneCounter}" placeholder="30" value="${zoneData ? (zoneData.loitering_time || 30) : 30}">
                <small style="color: #888; font-size: 11px;">默认30帧（约1秒）</small>
            </div>
            <div class="form-group">
                <label>区域颜色（RGB）</label>
                <input type="text" id="channelZoneColor${channelZoneCounter}" name="zone_color_${channelZoneCounter}" placeholder="255,0,0" value="${zoneData && zoneData.color ? zoneData.color.join(',') : '255,0,0'}">
                <small style="color: #888; font-size: 11px;">格式：R,G,B（如：255,0,0表示红色）</small>
            </div>
        </div>
    `;

    container.appendChild(zoneDiv);
}

/**
 * 删除Zone区域
 */
function removeChannelZone(zoneId) {
    const zoneDiv = document.getElementById(zoneId);
    if (zoneDiv) {
        zoneDiv.remove();
    }
}

/**
 * 保存通道Zone配置
 */
async function saveChannelZoneConfig() {
    if (!currentChannelZoneTaskId || !currentChannelZoneChannelId) {
        showNotification('任务ID或通道ID无效', 'error');
        return;
    }

    // 收集Zone配置
    const zones = [];
    const container = document.getElementById('channelZonesContainer');
    const zoneItems = container.querySelectorAll('.zone-config-item');

    zoneItems.forEach((item) => {
        const counter = parseInt(item.id.replace('channelZoneConfig', ''));

        const name = document.getElementById(`channelZoneName${counter}`).value.trim();
        const polygonStr = document.getElementById(`channelZonePolygon${counter}`).value.trim();

        if (!name || !polygonStr) {
            return;  // 跳过空配置
        }

        // 解析多边形坐标
        const polygon = polygonStr.split(';').map(point => {
            const [x, y] = point.split(',').map(Number);
            return [x, y];
        });

        // 解析颜色
        const colorStr = document.getElementById(`channelZoneColor${counter}`).value.trim();
        const color = colorStr ? colorStr.split(',').map(Number) : [255, 0, 0];

        zones.push({
            name: name,
            polygon: polygon,
            required: document.getElementById(`channelZoneRequired${counter}`).value === 'true',
            color: color,
            min_area: 0,
            loitering_time: parseInt(document.getElementById(`channelZoneLoiteringTime${counter}`).value) || 30,
            enable_loitering_detection: document.getElementById(`channelZoneLoitering${counter}`).value === 'true'
        });
    });

    // 提交到后端
    try {
        const response = await fetch(`/api/tasks/${currentChannelZoneTaskId}/channels/${currentChannelZoneChannelId}/zones`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ zones })
        });

        const result = await response.json();

        if (response.ok) {
            showNotification('Zone配置保存成功', 'success');
            closeChannelZoneConfigModal();
        } else {
            showNotification(result.error || 'Zone配置保存失败', 'error');
        }
    } catch (error) {
        console.error('保存Zone配置失败:', error);
        showNotification('Zone配置保存失败', 'error');
    }
}

/**
 * 关闭通道Zone配置模态框
 */
function closeChannelZoneConfigModal() {
    document.getElementById('channelZoneConfigModal').classList.remove('show');
    currentChannelZoneTaskId = null;
    currentChannelZoneChannelId = null;
}

/**
 * HTML转义
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}