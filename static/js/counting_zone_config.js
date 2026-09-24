/**
 * 人员统计计数区域配置
 *
 * 管理任务的计数区域（CountingZone），支持两种模式：
 *   模式A line      ：单摄像机画分界线，目标跨越方向判进出（当前区域人数=进入-离开）
 *   模式B direction ：多摄像机按进出方向汇总（入口机+1 / 出口机-1）
 *
 * 前端持有待保存的 countingZones 数组，保存时整体清空替换：
 *   GET/POST /api/tasks/<id>/counting-zones
 */
(function () {
    'use strict';

    // 当前配置上下文
    const ctx = {
        taskId: null,
        taskName: '',
        channels: [],            // 任务关联的摄像机（含 id/channel_name）
        zones: [],               // 待保存的计数区域列表
        activeZoneId: null,
        snapshotImage: null,     // Image 对象
        snapshotSize: { width: 640, height: 480 },
        linePoints: [],          // 当前区域分界线两个画布坐标点
        drawingPointIdx: 0,      // 当前正在绘制的第几个点（0/1）
        // 计数器，保证 canvas 事件绑定一次
        bound: false,
        // 通过通道项图标进入时，锁定到该通道的画线区域
        initialChannelId: null,
        // 当前配置入口锁定的模式：'line'（通道入口，单机画线）或 'direction'（任务入口，多机进出）
        modeFilter: 'line'
    };

    // ========================================
    // 打开 / 关闭
    // ========================================

    async function openCountingZoneConfig(initialChannelId) {
        const taskIdInput = document.getElementById('taskId');
        const taskId = taskIdInput ? parseInt(taskIdInput.value, 10) : NaN;
        if (!taskId || isNaN(taskId)) {
            alert('请先保存任务后再配置计数区域');
            return;
        }
        ctx.taskId = taskId;
        ctx.initialChannelId = initialChannelId != null ? parseInt(initialChannelId, 10) : null;
        // 通道入口：锁定单机画线模式
        ctx.modeFilter = 'line';
        setConfigTitle('单机画线计数配置');

        const modal = document.getElementById('countingZoneConfigModal');
        if (modal) modal.style.display = 'flex';

        // 绑定 canvas 事件（仅一次）
        bindCanvasEvents();

        // 加载任务信息与关联摄像机
        const task = await fetchTask(taskId);
        if (task) {
            ctx.taskName = task.task_name || '';
            ctx.channels = Array.isArray(task.channels) ? task.channels : [];
        }
        document.getElementById('countingZoneTaskName').textContent = ctx.taskName || `#${taskId}`;
        populateChannelOptions();

        // 加载已有计数区域配置
        await loadCountingZonesFromServer();

        // 若一个区域都没有，自动新增一个，引导用户配置
        if (ctx.zones.length === 0) {
            addCountingZone();
            selectCountingZone(ctx.zones[0]._id);
        } else {
            selectCountingZone(ctx.zones[0]._id);
        }

        // 通过通道图标进入时，定位到该通道的画线区域；无则自动新增一个
        if (ctx.initialChannelId != null) {
            selectZoneForChannel(ctx.initialChannelId);
        }
    }

    // 任务面板入口：多机进出（模式B），入口/出口机从任务通道池勾选，不绑定单个通道
    async function openCountingZoneDirectionConfig() {
        const taskIdInput = document.getElementById('taskId');
        const taskId = taskIdInput ? parseInt(taskIdInput.value, 10) : NaN;
        if (!taskId || isNaN(taskId)) {
            alert('请先保存任务后再配置计数区域');
            return;
        }
        ctx.taskId = taskId;
        ctx.initialChannelId = null;
        // 任务入口：锁定多机进出模式
        ctx.modeFilter = 'direction';
        setConfigTitle('多机进出计数配置');

        const modal = document.getElementById('countingZoneConfigModal');
        if (modal) modal.style.display = 'flex';

        bindCanvasEvents();

        const task = await fetchTask(taskId);
        if (task) {
            ctx.taskName = task.task_name || '';
            ctx.channels = Array.isArray(task.channels) ? task.channels : [];
        }
        document.getElementById('countingZoneTaskName').textContent = ctx.taskName || `#${taskId}`;
        populateChannelOptions();

        await loadCountingZonesFromServer();

        if (ctx.zones.length === 0) {
            addCountingZone();
            selectCountingZone(ctx.zones[0]._id);
        } else {
            selectCountingZone(ctx.zones[0]._id);
        }
    }

    function setConfigTitle(title) {
        const el = document.getElementById('countingZoneConfigTitle');
        if (el) el.textContent = title;
    }

    // 选中某通道的 line 区域；若不存在则为其新增一个 line 区域并选中
    function selectZoneForChannel(channelId) {
        let zone = ctx.zones.find(z => z.mode === 'line' && z.channel_id === channelId);
        if (zone) {
            selectCountingZone(zone._id);
            return;
        }
        const newZone = {
            _id: uuid(),
            zone_name: `通道${channelId}计数`,
            mode: 'line',
            channel_id: channelId,
            in_side: 1,
            inertia: 3,
            min_displacement: 0.02,
            cross_line: null,
            in_channels: [],
            out_channels: []
        };
        ctx.zones.push(newZone);
        renderZoneList();
        selectCountingZone(newZone._id);
    }

    function closeCountingZoneConfig() {
        const modal = document.getElementById('countingZoneConfigModal');
        if (modal) modal.style.display = 'none';
        ctx.snapshotImage = null;
        ctx.linePoints = [];
        ctx.drawingPointIdx = 0;
        const resetBtn = document.getElementById('resetCountingLineBtn');
        if (resetBtn) resetBtn.style.display = 'none';
    }

    async function fetchTask(taskId) {
        try {
            const resp = await fetch(`/api/tasks/${taskId}`);
            if (!resp.ok) return null;
            return await resp.json();
        } catch (e) {
            console.error('[CountingZone] 加载任务失败:', e);
            return null;
        }
    }

    function populateChannelOptions() {
        const lineSel = document.getElementById('countingZoneLineChannel');
        if (lineSel) {
            lineSel.innerHTML = ctx.channels
                .map(c => `<option value="${c.id}">${escapeHtml(c.channel_name || `通道${c.id}`)}</option>`)
                .join('');
        }
        // 模式B 的入口/出口机复选框
        const inBox = document.getElementById('countingZoneInChannels');
        const outBox = document.getElementById('countingZoneOutChannels');
        if (inBox) {
            inBox.innerHTML = ctx.channels
                .map(c => `<label><input type="checkbox" value="${c.id}" onchange="onCountingZonePropChange()"> ${escapeHtml(c.channel_name || `通道${c.id}`)}</label>`)
                .join('');
        }
        if (outBox) {
            outBox.innerHTML = ctx.channels
                .map(c => `<label><input type="checkbox" value="${c.id}" onchange="onCountingZonePropChange()"> ${escapeHtml(c.channel_name || `通道${c.id}`)}</label>`)
                .join('');
        }
    }

    // ========================================
    // 从服务器加载计数区域
    // ========================================

    async function loadCountingZonesFromServer() {
        if (!ctx.taskId) return;
        try {
            const resp = await fetch(`/api/tasks/${ctx.taskId}/counting-zones`);
            const data = await resp.json();
            const zones = data.zones || [];
            // 按当前入口模式过滤，两种模式的区域分开管理互不干扰
            ctx.zones = zones.filter(z => (z.mode || 'line') === ctx.modeFilter).map(z => {
                let crossLine = null;
                try { crossLine = z.cross_line ? JSON.parse(JSON.stringify(
                    Array.isArray(z.cross_line) ? z.cross_line :
                    (typeof z.cross_line === 'string' ? JSON.parse(z.cross_line) : null)
                )) : null; } catch (e) { crossLine = null; }
                return {
                    _id: uuid(),
                    zone_name: z.zone_name || '',
                    mode: z.mode || 'line',
                    channel_id: z.channel_id != null ? z.channel_id : null,
                    in_side: z.in_side != null ? z.in_side : 1,
                    inertia: z.inertia != null ? z.inertia : 3,
                    min_displacement: z.min_displacement != null ? z.min_displacement : 0.02,
                    cross_line: crossLine,
                    in_channels: Array.isArray(z.in_channels) ? z.in_channels : [],
                    out_channels: Array.isArray(z.out_channels) ? z.out_channels : []
                };
            });
            renderZoneList();
        } catch (e) {
            console.error('[CountingZone] 加载计数区域失败:', e);
        }
    }

    function uuid() {
        return 'z_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
    }

    // ========================================
    // 区域列表管理
    // ========================================

    function renderZoneList() {
        const listEl = document.getElementById('countingZoneList');
        if (!listEl) return;
        if (!ctx.zones || ctx.zones.length === 0) {
            listEl.innerHTML = '<div class="zone-list-empty">暂无计数区域，请点击“＋”添加</div>';
            return;
        }
        listEl.innerHTML = ctx.zones.map(z => {
            const active = z._id === ctx.activeZoneId ? 'class="zone-list-item active"' : 'class="zone-list-item"';
            const modeText = z.mode === 'line' ? '单机画线' : '多机进出';
            return `<div ${active} onclick="selectCountingZone('${z._id}')">
                <div class="zone-list-name">${escapeHtml(z.zone_name || '未命名区域')}</div>
                <div class="zone-list-mode">${modeText}</div>
            </div>`;
        }).join('');
    }

    function addCountingZone() {
        const newZone = {
            _id: uuid(),
            zone_name: `区域${ctx.zones.length + 1}`,
            mode: ctx.modeFilter,
            channel_id: ctx.modeFilter === 'line' && ctx.channels.length ? ctx.channels[0].id : null,
            in_side: 1,
            inertia: 3,
            min_displacement: 0.02,
            cross_line: null,
            in_channels: [],
            out_channels: []
        };
        ctx.zones.push(newZone);
        renderZoneList();
        selectCountingZone(newZone._id);
    }

    function deleteCountingZone() {
        if (!ctx.activeZoneId) {
            alert('请先选中要删除的计数区域');
            return;
        }
        if (!confirm('确定删除选中的计数区域吗？')) return;
        ctx.zones = ctx.zones.filter(z => z._id !== ctx.activeZoneId);
        ctx.activeZoneId = null;
        ctx.linePoints = [];
        renderZoneList();
        if (ctx.zones.length > 0) {
            selectCountingZone(ctx.zones[0]._id);
        } else {
            clearPropPanel();
        }
    }

    function getActiveZone() {
        return ctx.zones.find(z => z._id === ctx.activeZoneId) || null;
    }

    function selectCountingZone(zoneId) {
        ctx.activeZoneId = zoneId;
        renderZoneList();
        const z = getActiveZone();
        if (!z) return;

        // 填充属性面板
        document.getElementById('countingZonePropName').value = z.zone_name || '';
        const modeSel = document.getElementById('countingZoneMode');
        // 入口锁定模式，避免通道级与任务级配置混淆
        modeSel.value = ctx.modeFilter;
        modeSel.disabled = true;
        const inertia = document.getElementById('countingZoneInertia');
        inertia.value = z.inertia != null ? z.inertia : 3;

        // 模式A：选择画线摄像机
        const lineChannel = document.getElementById('countingZoneLineChannel');
        if (lineChannel && z.channel_id != null) {
            lineChannel.value = String(z.channel_id);
        }

        // 模式B：回填入口/出口机
        const inBox = document.getElementById('countingZoneInChannels');
        const outBox = document.getElementById('countingZoneOutChannels');
        if (inBox) {
            inBox.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                cb.checked = (z.in_channels || []).indexOf(parseInt(cb.value, 10)) !== -1;
            });
        }
        if (outBox) {
            outBox.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                cb.checked = (z.out_channels || []).indexOf(parseInt(cb.value, 10)) !== -1;
            });
        }

        // 恢复分界线（若是 mode=line 且有 cross_line）
        if (z.mode === 'line' && z.cross_line && z.cross_line.length === 2) {
            const { w, h } = ctx.snapshotSize;
            ctx.linePoints = z.cross_line.map(p => [p[0] * w, p[1] * h]);
            ctx.drawingPointIdx = 2;
        } else {
            ctx.linePoints = [];
            ctx.drawingPointIdx = z.mode === 'line' ? 0 : 2;
        }

        toggleModeFields(z.mode);
        const resetBtn = document.getElementById('resetCountingLineBtn');
        if (resetBtn) resetBtn.style.display = (z.mode === 'line') ? '' : 'none';
        if (z.mode === 'line') {
            loadSnapshotForChannel(z.channel_id);
        } else {
            clearSnapshotCanvas('多机进出：请在上方选择入口机 / 出口机');
        }
    }

    function clearPropPanel() {
        document.getElementById('countingZonePropName').value = '';
        document.getElementById('countingZoneMode').value = 'line';
        document.getElementById('countingZoneInertia').value = 3;
        ctx.linePoints = [];
        clearSnapshotCanvas('暂无计数区域');
    }

    function toggleModeFields(mode) {
        document.getElementById('countingZoneLineFields').style.display = mode === 'line' ? '' : 'none';
        document.getElementById('countingZoneDirectionFields').style.display = mode === 'direction' ? '' : 'none';
    }

    // ========================================
    // 属性变更
    // ========================================

    function onCountingZonePropChange() {
        const z = getActiveZone();
        if (!z) return;
        z.zone_name = document.getElementById('countingZonePropName').value;
        z.inertia = parseInt(document.getElementById('countingZoneInertia').value, 10) || 3;
        z.mode = document.getElementById('countingZoneMode').value;
        // 模式B：收集入口/出口机
        const inBox = document.getElementById('countingZoneInChannels');
        const outBox = document.getElementById('countingZoneOutChannels');
        if (inBox) {
            z.in_channels = Array.from(inBox.querySelectorAll('input[type="checkbox"]:checked')).map(cb => parseInt(cb.value, 10));
        }
        if (outBox) {
            z.out_channels = Array.from(outBox.querySelectorAll('input[type="checkbox"]:checked')).map(cb => parseInt(cb.value, 10));
        }
        renderZoneList();
    }

    function onCountingZoneModeChange() {
        const z = getActiveZone();
        if (!z) return;
        const mode = document.getElementById('countingZoneMode').value;
        z.mode = mode;
        toggleModeFields(mode);
        if (mode === 'line') {
            ctx.linePoints = [];
            ctx.drawingPointIdx = ctx.snapshotImage ? 0 : 2;
            document.getElementById('resetCountingLineBtn').style.display = '';
            loadSnapshotForChannel(document.getElementById('countingZoneLineChannel').value);
        } else {
            ctx.linePoints = [];
            ctx.drawingPointIdx = 2;
            document.getElementById('resetCountingLineBtn').style.display = 'none';
            clearSnapshotCanvas('多机进出：请在上方选择入口机 / 出口机');
        }
    }

    function onCountingZoneChannelChange() {
        const z = getActiveZone();
        if (!z) return;
        const chanSel = document.getElementById('countingZoneLineChannel');
        z.channel_id = chanSel.value ? parseInt(chanSel.value, 10) : null;
        // 切换摄像机后重置分界线
        z.cross_line = null;
        ctx.linePoints = [];
        ctx.drawingPointIdx = 0;
        loadSnapshotForChannel(z.channel_id);
        render();
    }

    function resetCountingLine() {
        const z = getActiveZone();
        if (!z) return;
        z.cross_line = null;
        ctx.linePoints = [];
        ctx.drawingPointIdx = 0;
        render();
    }

    // ========================================
    // 画布：快照 + 分界线绘制
    // ========================================

    function loadSnapshotForChannel(channelId) {
        if (!channelId) {
            clearSnapshotCanvas('请选择画线摄像机');
            return;
        }
        const canvas = document.getElementById('countingZoneCanvas');
        const canvasCtx = canvas.getContext('2d');
        canvasCtx.fillStyle = '#2a2a2a';
        canvasCtx.fillRect(0, 0, canvas.width, canvas.height);
        canvasCtx.fillStyle = '#999';
        canvasCtx.font = '14px sans-serif';
        canvasCtx.textAlign = 'center';
        canvasCtx.fillText('正在获取摄像机快照...', canvas.width / 2, canvas.height / 2);

        const url = `/api/channels/${channelId}/snapshot?max_width=640&t=${Date.now()}`;
        fetch(url)
            .then(resp => {
                if (!resp.ok) {
                    const err = new Error(`快照获取失败: ${resp.status}`);
                    throw err;
                }
                ctx.snapshotSize.width = parseInt(resp.headers.get('X-Snapshot-Width'), 10) || 640;
                ctx.snapshotSize.height = parseInt(resp.headers.get('X-Snapshot-Height'), 10) || 480;
                return resp.blob();
            })
            .then(blob => new Promise((resolve, reject) => {
                const img = new Image();
                img.onload = () => resolve(img);
                img.onerror = () => reject(new Error('图片加载失败'));
                img.src = URL.createObjectURL(blob);
            }))
            .then(img => {
                ctx.snapshotImage = img;
                ctx.drawingPointIdx = 0; // 快照就绪后允许开始绘制分界线
                ctx.linePoints = [];
                if (getActiveZone()) {
                    getActiveZone().cross_line = null;
                }
                sizeCanvasToSnapshot();
                render();
            })
            .catch(err => {
                console.error('[CountingZone] 获取快照失败:', err);
                ctx.snapshotImage = null;
                clearSnapshotCanvas('快照获取失败');
            });
    }

    function refreshCountingSnapshot() {
        const z = getActiveZone();
        if (!z) return;
        if (z.mode === 'line' && z.channel_id) {
            loadSnapshotForChannel(z.channel_id);
        } else if (z.mode === 'line') {
            alert('请先选择画线摄像机');
        }
    }

    function sizeCanvasToSnapshot() {
        const canvas = document.getElementById('countingZoneCanvas');
        const img = ctx.snapshotImage;
        if (!img) return;
        const targetWidth = Math.min(canvas.clientWidth, 640);
        const scale = targetWidth / img.width;
        canvas.width = targetWidth;
        canvas.height = Math.round(img.height * scale);
    }

    function clearSnapshotCanvas(message) {
        ctx.snapshotImage = null;
        const canvas = document.getElementById('countingZoneCanvas');
        const canvasCtx = canvas.getContext('2d');
        canvas.width = 640;
        canvas.height = 480;
        canvasCtx.fillStyle = '#2a2a2a';
        canvasCtx.fillRect(0, 0, canvas.width, canvas.height);
        canvasCtx.fillStyle = '#999';
        canvasCtx.font = '14px sans-serif';
        canvasCtx.textAlign = 'center';
        canvasCtx.fillText(message || '请先选择一个计数区域', canvas.width / 2, canvas.height / 2);
    }

    function bindCanvasEvents() {
        if (ctx.bound) return;
        ctx.bound = true;
        const canvas = document.getElementById('countingZoneCanvas');
        if (!canvas) return;
        canvas.addEventListener('click', onCanvasClick);
    }

    function onCanvasClick(e) {
        if (ctx.snapshotImage) {
            ctx.snapshotImage = ctx.snapshotImage;
        }
        const z = getActiveZone();
        if (!z || z.mode !== 'line') return;
        if (!ctx.snapshotImage) return;

        const canvas = document.getElementById('countingZoneCanvas');
        const rect = canvas.getBoundingClientRect();
        const scaleX = canvas.width / rect.width;
        const scaleY = canvas.height / rect.height;
        const x = (e.clientX - rect.left) * scaleX;
        const y = (e.clientY - rect.top) * scaleY;
        const point = [Math.max(0, Math.min(canvas.width, x)), Math.max(0, Math.min(canvas.height, y))];

        if (ctx.drawingPointIdx === 0) {
            ctx.linePoints = [point];
            ctx.drawingPointIdx = 1;
        } else if (ctx.drawingPointIdx === 1) {
            ctx.linePoints.push(point);
            ctx.drawingPointIdx = 2;
            // 已定两个点，转成归一化坐标存入区域
            z.cross_line = ctx.linePoints.map(p => [p[0] / canvas.width, p[1] / canvas.height]);
        } else {
            return;
        }
        render();
    }

    function render() {
        const canvas = document.getElementById('countingZoneCanvas');
        if (!canvas) return;
        const canvasCtx = canvas.getContext('2d');
        canvasCtx.clearRect(0, 0, canvas.width, canvas.height);

        // 背景：快照
        if (ctx.snapshotImage) {
            canvasCtx.drawImage(ctx.snapshotImage, 0, 0, canvas.width, canvas.height);
        } else {
            canvasCtx.fillStyle = '#2a2a2a';
            canvasCtx.fillRect(0, 0, canvas.width, canvas.height);
        }

        // 分界线
        if (ctx.linePoints.length > 0) {
            canvasCtx.strokeStyle = '#ffeb3b';
            canvasCtx.lineWidth = 3;
            canvasCtx.beginPath();
            canvasCtx.moveTo(ctx.linePoints[0][0], ctx.linePoints[0][1]);
            if (ctx.linePoints.length > 1) {
                canvasCtx.lineTo(ctx.linePoints[1][0], ctx.linePoints[1][1]);
            } else {
                canvasCtx.lineTo(ctx.linePoints[0][0], ctx.linePoints[0][1]);
            }
            canvasCtx.stroke();

            ctx.linePoints.forEach((p, i) => {
                canvasCtx.fillStyle = i === 0 ? '#4caf50' : '#f44336';
                canvasCtx.beginPath();
                canvasCtx.arc(p[0], p[1], 6, 0, Math.PI * 2);
                canvasCtx.fill();
            });
        }

        // 提示
        if (ctx.snapshotImage && ctx.linePoints.length < 2) {
            canvasCtx.fillStyle = '#ffeb3b';
            canvasCtx.font = '14px sans-serif';
            canvasCtx.textAlign = 'center';
            canvasCtx.fillText(
                ctx.linePoints.length === 0 ? '点击第一个点（分界线起点）' : '点击第二个点（分界线终点）',
                canvas.width / 2, 24
            );
        }
    }

    // ========================================
    // 保存
    // ========================================

    async function saveCountingZones() {
        if (!ctx.taskId) return;

        // 校验并序列化
        const payload = [];
        for (let i = 0; i < ctx.zones.length; i++) {
            const z = ctx.zones[i];
            if (!z.zone_name || !z.zone_name.trim()) {
                alert(`计数区域 ${i + 1} 未命名，请先设置名称`);
                selectCountingZone(z._id);
                return;
            }
            if (z.mode === 'line') {
                if (!z.channel_id) {
                    alert(`计数区域 "${z.zone_name}" 未选择画线摄像机`);
                    selectCountingZone(z._id);
                    return;
                }
                if (!z.cross_line || z.cross_line.length !== 2) {
                    alert(`计数区域 "${z.zone_name}" 未绘制分界线，请在画布上点击两点`);
                    selectCountingZone(z._id);
                    return;
                }
            } else {
                if ((!z.in_channels || z.in_channels.length === 0) && (!z.out_channels || z.out_channels.length === 0)) {
                    alert(`计数区域 "${z.zone_name}" 模式B 至少需要一个入口机或出口机`);
                    selectCountingZone(z._id);
                    return;
                }
            }
            payload.push({
                zone_name: z.zone_name.trim(),
                mode: z.mode,
                channel_id: z.mode === 'line' ? z.channel_id : null,
                cross_line: z.mode === 'line' ? z.cross_line : null,
                in_side: (z.mode === 'line' && z.cross_line && z.cross_line.length === 2) ? 1 : z.in_side,
                inertia: z.inertia,
                min_displacement: z.min_displacement,
                in_channels: z.mode === 'direction' ? (z.in_channels || []) : [],
                out_channels: z.mode === 'direction' ? (z.out_channels || []) : []
            });
        }

        // 合并服务器上其他模式的区域，避免本次保存覆盖另一种模式的配置
        let otherZones = [];
        try {
            const resp = await fetch(`/api/tasks/${ctx.taskId}/counting-zones`);
            const data = await resp.json();
            otherZones = (data.zones || [])
                .filter(z => (z.mode || 'line') !== ctx.modeFilter)
                .map(z => normalizeZoneForSave(z));
        } catch (e) {
            console.error('[CountingZone] 加载其他模式区域失败:', e);
        }

        const btn = document.getElementById('countingZoneSaveBtn');
        if (btn) { btn.disabled = true; btn.textContent = '保存中...'; }
        try {
            const resp = await fetch(`/api/tasks/${ctx.taskId}/counting-zones`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ zones: otherZones.concat(payload) })
            });
            const data = await resp.json();
            if (data.message) {
                showToast('计数区域保存成功', 'success');
                closeCountingZoneConfig();
                // 刷新概览
                updateCountingSummary();
            } else {
                alert('保存失败: ' + (data.error || '未知错误'));
            }
        } catch (e) {
            console.error('[CountingZone] 保存失败:', e);
            alert('保存失败: ' + e.message);
        } finally {
            if (btn) { btn.disabled = false; btn.textContent = '保存配置'; }
        }
    }

    // ========================================
    // 概览（供 tasks.js 调用）
    // ========================================

    // 将服务器返回的计数区域行转换为可提交结构（字符串字段解析回数组）
    function normalizeZoneForSave(z) {
        const parseList = (v) => {
            if (Array.isArray(v)) return v;
            if (typeof v === 'string') {
                try { return JSON.parse(v); } catch (e) { return []; }
            }
            return [];
        };
        let crossLine = null;
        if (Array.isArray(z.cross_line)) crossLine = z.cross_line;
        else if (typeof z.cross_line === 'string') {
            try { crossLine = JSON.parse(z.cross_line); } catch (e) { crossLine = null; }
        }
        return {
            zone_name: z.zone_name || '',
            mode: z.mode || 'line',
            channel_id: z.channel_id != null ? z.channel_id : null,
            cross_line: crossLine,
            in_side: z.in_side != null ? z.in_side : 1,
            inertia: z.inertia != null ? z.inertia : 3,
            min_displacement: z.min_displacement != null ? z.min_displacement : 0.02,
            in_channels: parseList(z.in_channels),
            out_channels: parseList(z.out_channels)
        };
    }

    async function updateCountingSummary() {
        const summaryEl = document.getElementById('countingConfigSummary');
        if (!summaryEl) return;
        const taskIdInput = document.getElementById('taskId');
        const taskId = taskIdInput ? parseInt(taskIdInput.value, 10) : NaN;
        if (!taskId || isNaN(taskId)) {
            summaryEl.value = '';
            return;
        }
        try {
            const resp = await fetch(`/api/tasks/${taskId}/counting-zones`);
            const data = await resp.json();
            const zones = data.zones || [];
            const lineCount = zones.filter(z => z.mode === 'line').length;
            const dirCount = zones.filter(z => z.mode === 'direction').length;
            summaryEl.value = zones.length
                ? `已配置 ${zones.length} 个计数区域（单机画线 ${lineCount}，多机进出 ${dirCount}）`
                : '';
        } catch (e) {
            summaryEl.value = '';
        }
    }

    function escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    // 导出（全局，供 tasks.js / HTML 事件调用）
    window.openCountingZoneConfig = openCountingZoneConfig;
    window.openCountingZoneDirectionConfig = openCountingZoneDirectionConfig;
    window.closeCountingZoneConfig = closeCountingZoneConfig;
    window.addCountingZone = addCountingZone;
    window.deleteCountingZone = deleteCountingZone;
    window.selectCountingZone = selectCountingZone;
    window.onCountingZonePropChange = onCountingZonePropChange;
    window.onCountingZoneModeChange = onCountingZoneModeChange;
    window.onCountingZoneChannelChange = onCountingZoneChannelChange;
    window.resetCountingLine = resetCountingLine;
    window.refreshCountingSnapshot = refreshCountingSnapshot;
    window.saveCountingZones = saveCountingZones;
    window.updateCountingSummary = updateCountingSummary;
})();