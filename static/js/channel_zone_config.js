/**
 * 通道 Zone 区域配置
 *
 * 提供打开 Zone 配置模态框、加载快照、加载/保存区域配置等功能。
 * 与 ZoneCanvasEditor 配合使用。
 */

// 当前 Zone 配置上下文
let zoneConfigContext = {
    channelId: null,
    channelName: '',
    editor: null,
    loading: false
};

/**
 * 打开通道 Zone 配置模态框
 *
 * 由 tasks.js 中的通道列表按钮调用:
 *   onclick="openChannelZoneConfig(channelId, channelName, event)"
 *
 * @param {number} channelId - 设备通道 ID
 * @param {string} channelName - 通道名称
 * @param {Event} event - 点击事件
 */
function openChannelZoneConfig(channelId, channelName, event) {
    if (event) event.preventDefault();

    // Zone 配置为通道级数据，无需任务上下文
    if (!channelId) {
        alert('通道ID无效');
        return;
    }

    zoneConfigContext.channelId = channelId;
    zoneConfigContext.channelName = channelName || `通道${channelId}`;

    // 更新模态框标题
    document.getElementById('zoneConfigChannelName').textContent = zoneConfigContext.channelName;

    // 显示模态框
    const modal = document.getElementById('channelZoneConfigModal');
    if (modal) {
        modal.style.display = 'flex';
    }

    // 初始化编辑器（如果尚未初始化）
    if (!zoneConfigContext.editor) {
        zoneConfigContext.editor = new ZoneCanvasEditor('zoneCanvas', {
            onZoneSelected: onZoneSelected,
            onZoneAdded: onZoneAdded,
            onZoneDeleted: onZoneDeleted,
            onZonesChanged: onZonesChanged
        });
    }

    // 清空编辑器历史区域、区域列表和属性面板（避免切换通道时残留）
    if (zoneConfigContext.editor) {
        zoneConfigContext.editor.clearAll();
    }
    renderZoneList([]);
    clearZoneProperties();

    // 加载快照和已有区域配置
    refreshSnapshot();
    loadZonesFromServer();
}

/**
 * 关闭 Zone 配置模态框
 */
function closeChannelZoneConfig() {
    const modal = document.getElementById('channelZoneConfigModal');
    if (modal) {
        modal.style.display = 'none';
    }

    // 取消正在绘制的多边形
    if (zoneConfigContext.editor) {
        zoneConfigContext.editor.cancelDrawing();
    }

    zoneConfigContext.channelId = null;
}

/**
 * 刷新摄像机快照
 */
function refreshSnapshot() {
    if (!zoneConfigContext.channelId) return;

    const canvas = document.getElementById('zoneCanvas');
    const ctx = canvas.getContext('2d');

    // 显示加载中
    ctx.fillStyle = '#2a2a2a';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = '#999';
    ctx.font = '14px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('正在获取摄像机快照...', canvas.width / 2, canvas.height / 2);

    // 请求快照（添加时间戳避免缓存）
    const url = `/api/channels/${zoneConfigContext.channelId}/snapshot?max_width=640&t=${Date.now()}`;

    fetch(url)
        .then(resp => {
            if (!resp.ok) {
                // 解析后端返回的结构化错误信息（error + detail）
                return resp.json().catch(() => ({})).then(body => {
                    const errMsg = body.error || `快照获取失败: ${resp.status}`;
                    const errDetail = body.detail || '';
                    const err = new Error(errMsg);
                    err.detail = errDetail;
                    err.status = resp.status;
                    throw err;
                });
            }
            // 从响应头读取快照分辨率
            const width = parseInt(resp.headers.get('X-Snapshot-Width')) || 640;
            const height = parseInt(resp.headers.get('X-Snapshot-Height')) || 480;
            return resp.blob().then(blob => ({ blob, width, height }));
        })
        .then(({ blob, width, height }) => {
            const imageUrl = URL.createObjectURL(blob);
            if (zoneConfigContext.editor) {
                zoneConfigContext.editor.setSnapshot(imageUrl, width, height);
            }
        })
        .catch(err => {
            console.error('[ZoneConfig] 获取快照失败:', err);
            ctx.fillStyle = '#2a2a2a';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            ctx.fillStyle = '#ff6b6b';
            ctx.font = '14px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('快照获取失败', canvas.width / 2, canvas.height / 2 - 14);
            ctx.fillStyle = '#e0e0e0';
            ctx.font = '12px sans-serif';
            // 按行展示错误原因与详情
            const lines = [err.message];
            if (err.detail) {
                // 详情可能较长，按宽度截断
                const maxLen = 48;
                let d = err.detail;
                while (d.length > maxLen) {
                    lines.push(d.slice(0, maxLen));
                    d = d.slice(maxLen);
                }
                lines.push(d);
            }
            lines.forEach((line, i) => {
                ctx.fillText(line, canvas.width / 2, canvas.height / 2 + 6 + i * 18);
            });
        });
}

/**
 * 从服务器加载已有 Zone 配置
 */
function loadZonesFromServer() {
    if (!zoneConfigContext.channelId) return;

    fetch(`/api/channels/${zoneConfigContext.channelId}/zones`)
        .then(resp => resp.json())
        .then(data => {
            if (data.zones && data.zones.length > 0) {
                // 等待快照加载完成后再加载区域（确保 Canvas 尺寸正确）
                const tryLoad = () => {
                    if (zoneConfigContext.editor && zoneConfigContext.editor.canvas.width > 0) {
                        zoneConfigContext.editor.loadZones(data.zones);
                    } else {
                        setTimeout(tryLoad, 200);
                    }
                };
                tryLoad();
            }
        })
        .catch(err => {
            console.error('[ZoneConfig] 加载区域配置失败:', err);
        });
}

/**
 * 保存 Zone 配置到服务器
 */
function saveZoneConfig() {
    if (!zoneConfigContext.channelId) return;
    if (!zoneConfigContext.editor) return;

    // 保存前取消未完成的绘制，避免残留临时状态
    if (zoneConfigContext.editor.isDrawing()) {
        zoneConfigContext.editor.cancelDrawing();
    }

    const zones = zoneConfigContext.editor.getZones();

    // 验证区域配置：每个区域必须有名称且已绘制多边形（≥3 顶点）
    for (const zone of zones) {
        if (!zone.name || !zone.name.trim()) {
            alert('存在未命名的区域，请先设置区域名称');
            return;
        }
        if (!zone.polygon || zone.polygon.length < 3) {
            alert(`区域 "${zone.name}" 尚未绘制多边形，请在列表中选中该区域后点击"重新绘制多边形"`);
            return;
        }
    }

    const btn = document.getElementById('zoneConfigSaveBtn');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '保存中...';
    }

    fetch(`/api/channels/${zoneConfigContext.channelId}/zones`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ zones: zones })
    })
        .then(resp => resp.json())
        .then(data => {
            if (data.message) {
                // 显示成功提示
                showToast('Zone配置保存成功', 'success');
                closeChannelZoneConfig();
            } else {
                alert('保存失败: ' + (data.error || '未知错误'));
            }
        })
        .catch(err => {
            console.error('[ZoneConfig] 保存失败:', err);
            alert('保存失败: ' + err.message);
        })
        .finally(() => {
            if (btn) {
                btn.disabled = false;
                btn.textContent = '保存配置';
            }
        });
}

/**
 * 删除所有 Zone 配置
 */
function clearAllZones() {
    if (!zoneConfigContext.editor) return;
    if (!confirm('确定要清除所有区域配置吗？')) return;
    zoneConfigContext.editor.clearAll();
}

/**
 * 删除指定区域
 */
function deleteZone(zoneId) {
    if (!zoneConfigContext.editor) return;
    zoneConfigContext.editor.deleteZone(zoneId);
}

/**
 * 新增一个空区域并自动进入绘制模式
 * （先设置区域条目，再在画布上绘制其多边形）
 */
function addZone() {
    if (!zoneConfigContext.editor) return;
    // 若当前有未完成的绘制，先取消
    if (zoneConfigContext.editor.isDrawing()) {
        zoneConfigContext.editor.cancelDrawing();
    }
    const zone = zoneConfigContext.editor.addEmptyZone();
    // 自动进入绘制模式，引导用户在画布上为该区域绘制多边形
    zoneConfigContext.editor.startDrawingForZone(zone.id);
}

/**
 * 重新绘制当前选中区域的多边形
 */
function redrawActiveZone() {
    if (!zoneConfigContext.editor) return;
    const activeId = zoneConfigContext.editor.activeZoneId;
    if (activeId === null) return;
    zoneConfigContext.editor.startDrawingForZone(activeId);
}

/**
 * 取消正在绘制的多边形
 */
function cancelDrawing() {
    if (zoneConfigContext.editor) {
        zoneConfigContext.editor.cancelDrawing();
    }
}

// ========================================
// ZoneCanvasEditor 回调处理
// ========================================

/**
 * 区域被选中时回调
 */
function onZoneSelected(zone) {
    const redrawBtn = document.getElementById('redrawZoneBtn');
    if (zone) {
        // 显示区域属性
        document.getElementById('zonePropName').value = zone.name;
        document.getElementById('zonePropRequired').value = zone.required ? 'true' : 'false';
        document.getElementById('zonePropLoitering').value = zone.enable_loitering_detection ? 'true' : 'false';
        document.getElementById('zonePropLoiteringTime').value = zone.loitering_time;
        document.getElementById('zonePropLoiteringTime').disabled = !zone.enable_loitering_detection;
        document.getElementById('zonePropInertia').value = zone.inertia || 3;

        // “重新绘制”按钮：选中区域后可用
        if (redrawBtn) redrawBtn.disabled = false;

        // 高亮区域列表中的对应项
        document.querySelectorAll('.zone-list-item').forEach(el => {
            el.classList.toggle('active', parseInt(el.dataset.zoneId) === zone.id);
        });
    } else {
        clearZoneProperties();
        if (redrawBtn) redrawBtn.disabled = true;
    }
}

/**
 * 区域添加时回调
 */
function onZoneAdded(zone) {
    renderZoneList(zoneConfigContext.editor.zones);
}

/**
 * 区域删除时回调
 */
function onZoneDeleted(zone) {
    renderZoneList(zoneConfigContext.editor.zones);
    clearZoneProperties();
}

/**
 * 区域列表变化时回调
 */
function onZonesChanged(zones) {
    // 使用 editor.zones（包含内部 id 字段，用于列表高亮）
    if (zoneConfigContext.editor) {
        renderZoneList(zoneConfigContext.editor.zones);
    }
}

// ========================================
// UI 渲染
// ========================================

/**
 * 渲染区域列表
 */
function renderZoneList(zones) {
    const listEl = document.getElementById('zoneList');
    if (!listEl) return;

    if (!zones || zones.length === 0) {
        listEl.innerHTML = '<div class="zone-list-empty">暂无区域，请点击“添加区域”</div>';
        return;
    }

    listEl.innerHTML = zones.map((zone, index) => {
        const colorRgb = zone.color || [0, 255, 0];
        const colorStr = `rgb(${colorRgb[0]}, ${colorRgb[1]}, ${colorRgb[2]})`;
        const zoneId = zone.id || (index + 1);
        // 空区域（多边形顶点不足 3 个）标记为未绘制
        const isUndrawn = !zone.polygon || zone.polygon.length < 3;
        return `
            <div class="zone-list-item ${isUndrawn ? 'undrawn' : ''}" data-zone-id="${zoneId}" onclick="selectZoneByIndex(${index})">
                <div class="zone-color-dot" style="background: ${colorStr};"></div>
                <span class="zone-name">${escapeHtml(zone.name)}</span>
                ${isUndrawn ? '<span class="zone-undrawn-badge">未绘制</span>' : ''}
                ${zone.required ? '<span class="zone-required-badge">必需</span>' : ''}
                ${zone.enable_loitering_detection ? '<span class="zone-loitering-badge">徘徊</span>' : ''}
                <button class="zone-delete-btn" onclick="event.stopPropagation(); deleteZoneByIndex(${index})" title="删除区域">
                    <svg viewBox="0 0 24 24" fill="currentColor" style="width:14px;height:14px;">
                        <path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/>
                    </svg>
                </button>
            </div>
        `;
    }).join('');
}

/**
 * 通过索引选中区域（区域列表点击）
 */
function selectZoneByIndex(index) {
    if (!zoneConfigContext.editor) return;
    const zone = zoneConfigContext.editor.zones[index];
    if (zone) {
        zoneConfigContext.editor.selectZone(zone.id);
    }
}

/**
 * 通过索引删除区域
 */
function deleteZoneByIndex(index) {
    if (!zoneConfigContext.editor) return;
    const zone = zoneConfigContext.editor.zones[index];
    if (zone) {
        zoneConfigContext.editor.deleteZone(zone.id);
    }
}

/**
 * 清空区域属性面板
 */
function clearZoneProperties() {
    const nameEl = document.getElementById('zonePropName');
    if (nameEl) nameEl.value = '';
    const reqEl = document.getElementById('zonePropRequired');
    if (reqEl) reqEl.value = 'false';
    const loitEl = document.getElementById('zonePropLoitering');
    if (loitEl) loitEl.value = 'false';
    const timeEl = document.getElementById('zonePropLoiteringTime');
    if (timeEl) {
        timeEl.value = 30;
        timeEl.disabled = true;
    }
    const inertiaEl = document.getElementById('zonePropInertia');
    if (inertiaEl) inertiaEl.value = 3;
    // 无选中区域时禁用“重新绘制”按钮
    const redrawBtn = document.getElementById('redrawZoneBtn');
    if (redrawBtn) redrawBtn.disabled = true;
}

/**
 * 区域属性变更时更新编辑器
 */
function onZonePropChange() {
    if (!zoneConfigContext.editor || !zoneConfigContext.editor.activeZoneId) return;

    const zone = zoneConfigContext.editor.zones.find(
        z => z.id === zoneConfigContext.editor.activeZoneId
    );
    if (!zone) return;

    zone.name = document.getElementById('zonePropName').value || `区域${zone.id}`;
    zone.required = document.getElementById('zonePropRequired').value === 'true';
    zone.enable_loitering_detection = document.getElementById('zonePropLoitering').value === 'true';
    zone.loitering_time = parseInt(document.getElementById('zonePropLoiteringTime').value) || 30;
    zone.inertia = parseInt(document.getElementById('zonePropInertia').value) || 3;

    // 启用/禁用徘徊时间输入
    document.getElementById('zonePropLoiteringTime').disabled = !zone.enable_loitering_detection;

    // 更新区域列表显示（用 editor.zones 保持 id 一致，便于列表高亮匹配）
    renderZoneList(zoneConfigContext.editor.zones);
    zoneConfigContext.editor.render();
}

/**
 * HTML 转义
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * 显示提示消息
 */
function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.style.cssText = `
        position: fixed; top: 20px; right: 20px; z-index: 10000;
        padding: 12px 24px; border-radius: 4px; font-size: 14px;
        background: ${type === 'success' ? '#4caf50' : type === 'error' ? '#f44336' : '#2196f3'};
        color: #fff; box-shadow: 0 2px 8px rgba(0,0,0,0.2);
        animation: fadeIn 0.3s ease;
    `;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s';
        setTimeout(() => toast.remove(), 300);
    }, 2000);
}
