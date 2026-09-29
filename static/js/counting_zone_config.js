/**
 * 人员统计计数区域配置（两种模式在同一对话框中配置，逐区域选择模式）
 *
 *   模式A line      ：单摄像机画分界线，目标跨越方向判进出（当前区域人数=进入-离开）
 *                     区域配置属于通道级别，读写 /api/channels/<cid>/counting-lines
 *   模式B direction ：多摄像机按进出方向汇总（入口机+1 / 出口机-1）
 *                     跨通道关系，读写 /api/tasks/<id>/counting-zones
 *
 * 保存时按层级分别整体清空替换：画线按通道分组写回（含被清空的通道），方向整体写回任务。
 */
(function () {
    'use strict';

    // 当前配置上下文
    const ctx = {
        taskId: null,
        taskName: '',
        channels: [],            // 任务关联的摄像机（含 id/channel_name）
        zones: [],               // 待保存的计数区域列表（两种模式混排）
        activeZoneId: null,
        snapshotImage: null,     // Image 对象
        snapshotSize: { width: 640, height: 480 },
        linePoints: [],          // 当前区域分界线两个画布坐标点
        drawingPointIdx: 0,      // 当前正在绘制的第几个点（0/1）
        dragging: false,         // 是否正在按住鼠标拖动绘制分界线
        // 计数器，保证 canvas 事件绑定一次
        bound: false,
        // 从通道图标进入时预选的通道，作为新增画线区域的默认摄像机
        defaultChannelId: null,
        // 服务器上已有画线配置的通道（保存时需一并写回，以便清空被删除的画线）
        lineChannelsLoaded: []
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
        const cid = initialChannelId != null ? parseInt(initialChannelId, 10) : NaN;
        ctx.defaultChannelId = !isNaN(cid) && cid > 0 ? cid : null;

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
        setConfigTitle();

        populateChannelOptions();

        // 加载两种模式的已有计数区域
        await loadCountingZonesFromServer();

        // 若一个区域都没有，自动新增一个，引导用户配置
        if (ctx.zones.length === 0) {
            addCountingZone();
        } else {
            selectCountingZone(ctx.zones[0]._id);
        }
    }

    function setConfigTitle() {
        const el = document.getElementById('countingZoneConfigTitle');
        if (el) {
            const name = ctx.taskName || `#${ctx.taskId}`;
            el.textContent = `计数区域配置 - 任务 ${name}`;
        }
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
        // 画线摄像机：任务通道池中任选其一（画线区域归属该通道）
        const lineSel = document.getElementById('countingZoneLineChannel');
        if (lineSel) {
            lineSel.innerHTML = ctx.channels
                .map(c => `<option value="${c.id}">${escapeHtml(c.channel_name || `通道${c.id}`)}</option>`)
                .join('');
        }

        // 入口/出口机：同样从任务通道池勾选
        const options = ctx.channels
            .map(c => `<label><input type="checkbox" value="${c.id}" onchange="onCountingZonePropChange()"> ${escapeHtml(c.channel_name || `通道${c.id}`)}</label>`)
            .join('');
        const inBox = document.getElementById('countingZoneInChannels');
        const outBox = document.getElementById('countingZoneOutChannels');
        if (inBox) inBox.innerHTML = options;
        if (outBox) outBox.innerHTML = options;
    }

    // ========================================
    // 从服务器加载计数区域
    // ========================================

    async function loadCountingZonesFromServer() {
        if (!ctx.taskId) return;
        try {
            const zones = [];
            ctx.lineChannelsLoaded = [];

            // 通道级画线：逐通道读取后合并到同一列表
            const linesByChannel = await Promise.all(ctx.channels.map(async (ch) => {
                try {
                    const resp = await fetch(`/api/channels/${ch.id}/counting-lines`);
                    const data = await resp.json();
                    return { channelId: ch.id, lines: data.lines || [] };
                } catch (e) {
                    return { channelId: ch.id, lines: [] };
                }
            }));
            linesByChannel.forEach(({ channelId, lines }) => {
                if (lines.length > 0) ctx.lineChannelsLoaded.push(channelId);
                lines.forEach(line => {
                    zones.push({
                        _id: uuid(),
                        zone_name: line.zone_name || '',
                        mode: 'line',
                        channel_id: channelId,
                        in_side: line.in_side != null ? line.in_side : 1,
                        inertia: line.inertia != null ? line.inertia : 3,
                        min_displacement: line.min_displacement != null ? line.min_displacement : 0.02,
                        cross_line: parseCrossLine(line.cross_line),
                        in_channels: [],
                        out_channels: [],
                        alarm_threshold: line.alarm_threshold != null ? line.alarm_threshold : 0
                    });
                });
            });

            // 任务级多机进出方向
            const resp = await fetch(`/api/tasks/${ctx.taskId}/counting-zones`);
            const data = await resp.json();
            (data.zones || []).forEach(zone => {
                zones.push({
                    _id: uuid(),
                    zone_name: zone.zone_name || '',
                    mode: 'direction',
                    channel_id: null,
                    in_side: 1,
                    inertia: 3,
                    min_displacement: 0.02,
                    cross_line: null,
                    in_channels: parseChannelList(zone.in_channels),
                    out_channels: parseChannelList(zone.out_channels),
                    alarm_threshold: zone.alarm_threshold != null ? zone.alarm_threshold : 0
                });
            });

            ctx.zones = zones;
            renderZoneList();
        } catch (e) {
            console.error('[CountingZone] 加载计数区域失败:', e);
        }
    }

    // 分界线：统一解析为 [[x,y],[x,y]] 或 null（归一化坐标）
    function parseCrossLine(value) {
        if (Array.isArray(value)) return value;
        if (typeof value === 'string') {
            try {
                const arr = JSON.parse(value);
                return Array.isArray(arr) ? arr : null;
            } catch (e) { return null; }
        }
        return null;
    }

    // 通道ID列表：统一解析为整数数组（服务器返回可能为 JSON 字符串）
    function parseChannelList(value) {
        let arr = value;
        if (typeof value === 'string') {
            try { arr = JSON.parse(value); } catch (e) { return []; }
        }
        if (!Array.isArray(arr)) return [];
        return arr.map(x => parseInt(x, 10)).filter(x => !isNaN(x));
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
        // 默认单机画线（优先使用进入配置时预选的通道），任务无通道时退化为多机进出
        const defaultChannelId = ctx.defaultChannelId != null
            ? ctx.defaultChannelId
            : (ctx.channels[0] ? ctx.channels[0].id : null);
        const mode = defaultChannelId != null ? 'line' : 'direction';
        const newZone = {
            _id: uuid(),
            zone_name: `区域${ctx.zones.length + 1}`,
            mode: mode,
            channel_id: mode === 'line' ? defaultChannelId : null,
            in_side: 1,
            inertia: 3,
            min_displacement: 0.02,
            cross_line: null,
            in_channels: [],
            out_channels: [],
            alarm_threshold: 0
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
        document.getElementById('countingZoneMode').value = z.mode;
        document.getElementById('countingZoneInertia').value = z.inertia != null ? z.inertia : 3;
        document.getElementById('countingZoneAlarmThreshold').value = z.alarm_threshold != null ? z.alarm_threshold : 0;

        // 画线摄像机（模式A）
        const lineChannel = document.getElementById('countingZoneLineChannel');
        if (lineChannel && z.channel_id != null) {
            lineChannel.value = String(z.channel_id);
        }

        // 入口/出口机（模式B）
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

        applyZonePanel(z);
    }

    // 按当前选中区域的模式刷新字段可见性、画布与按钮
    function applyZonePanel(z) {
        toggleModeFields(z.mode);
        const resetBtn = document.getElementById('resetCountingLineBtn');
        if (resetBtn) resetBtn.style.display = (z.mode === 'line') ? '' : 'none';
        const refreshBtn = document.getElementById('refreshCountingSnapshotBtn');
        if (refreshBtn) refreshBtn.style.display = (z.mode === 'line') ? '' : 'none';

        // 分界线在快照就绪后由 loadSnapshotForChannel 按画布尺寸还原
        ctx.linePoints = [];
        ctx.drawingPointIdx = 0;
        ctx.dragging = false;
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
        document.getElementById('countingZoneAlarmThreshold').value = 0;
        ctx.linePoints = [];
        ctx.dragging = false;
        toggleModeFields('line');
        clearSnapshotCanvas('暂无计数区域');
    }

    function toggleModeFields(mode) {
        const isLine = mode === 'line';
        document.getElementById('countingZoneLineFields').style.display = isLine ? '' : 'none';
        document.getElementById('countingZoneDirectionFields').style.display = isLine ? 'none' : '';
        // 多机进出不画分界线，隐藏画布区域并让属性面板占满剩余宽度
        const canvasSection = document.getElementById('countingZoneCanvasSection');
        if (canvasSection) canvasSection.style.display = isLine ? '' : 'none';
        const layout = document.getElementById('countingZoneLayout');
        if (layout) layout.classList.toggle('no-canvas', !isLine);
    }

    // ========================================
    // 属性变更
    // ========================================

    function onCountingZonePropChange() {
        const z = getActiveZone();
        if (!z) return;
        z.zone_name = document.getElementById('countingZonePropName').value;
        z.inertia = parseInt(document.getElementById('countingZoneInertia').value, 10) || 3;
        // 区域级人数报警阈值：0 表示不按人数触发
        const threshold = parseInt(document.getElementById('countingZoneAlarmThreshold').value, 10);
        z.alarm_threshold = isNaN(threshold) || threshold < 0 ? 0 : threshold;
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

    // 切换区域模式：清理另一模式的数据并刷新面板
    function onCountingZoneModeChange() {
        const z = getActiveZone();
        if (!z) return;
        z.mode = document.getElementById('countingZoneMode').value;
        if (z.mode === 'line') {
            const lineSel = document.getElementById('countingZoneLineChannel');
            const cid = lineSel && lineSel.value ? parseInt(lineSel.value, 10) : NaN;
            z.channel_id = isNaN(cid) ? (ctx.channels[0] ? ctx.channels[0].id : null) : cid;
            z.in_channels = [];
            z.out_channels = [];
        } else {
            z.channel_id = null;
            z.cross_line = null;
        }
        renderZoneList();
        applyZonePanel(z);
    }

    // 切换画线摄像机：分界线基于原画面坐标，切换后需重画
    function onCountingZoneChannelChange() {
        const z = getActiveZone();
        if (!z || z.mode !== 'line') return;
        const lineSel = document.getElementById('countingZoneLineChannel');
        const cid = lineSel && lineSel.value ? parseInt(lineSel.value, 10) : NaN;
        if (isNaN(cid)) return;
        z.channel_id = cid;
        z.cross_line = null;
        applyZonePanel(z);
    }

    function resetCountingLine() {
        const z = getActiveZone();
        if (!z) return;
        z.cross_line = null;
        ctx.linePoints = [];
        ctx.drawingPointIdx = 0;
        ctx.dragging = false;
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
                sizeCanvasToSnapshot();
                ctx.dragging = false;
                const z = getActiveZone();
                if (z && z.mode === 'line' && z.cross_line && z.cross_line.length === 2) {
                    // 已有分界线：按当前画布尺寸从归一化坐标还原（避免切换区域时丢失）
                    ctx.linePoints = z.cross_line.map(p => [p[0] * canvas.width, p[1] * canvas.height]);
                    ctx.drawingPointIdx = 2;
                } else {
                    ctx.linePoints = [];
                    ctx.drawingPointIdx = 0; // 快照就绪，允许开始绘制分界线
                }
                render();
            })
            .catch(err => {
                console.error('[CountingZone] 获取快照失败:', err);
                ctx.snapshotImage = null;
                ctx.linePoints = [];
                ctx.drawingPointIdx = 0;
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
        const targetWidth = Math.min(canvas.clientWidth || img.width, 640);
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
        // 分界线绘制：按下鼠标确定起点 -> 拖动 -> 松开确定终点，两点连线
        canvas.addEventListener('mousedown', onCanvasMouseDown);
        canvas.addEventListener('mousemove', onCanvasMouseMove);
        canvas.addEventListener('mouseup', onCanvasMouseUp);
        canvas.addEventListener('mouseleave', onCanvasMouseLeave);
    }

    // 鼠标事件坐标 -> 画布坐标（含边界裁剪）
    function canvasPointFromEvent(e) {
        const canvas = document.getElementById('countingZoneCanvas');
        const rect = canvas.getBoundingClientRect();
        const scaleX = canvas.width / rect.width;
        const scaleY = canvas.height / rect.height;
        const x = (e.clientX - rect.left) * scaleX;
        const y = (e.clientY - rect.top) * scaleY;
        return [
            Math.max(0, Math.min(canvas.width, x)),
            Math.max(0, Math.min(canvas.height, y)),
        ];
    }

    // 是否满足分界线的最小长度（避免误点成一条零长线）
    function isLineLengthValid(start, end) {
        return start && end && Math.hypot(end[0] - start[0], end[1] - start[1]) >= 5;
    }

    function onCanvasMouseDown(e) {
        const z = getActiveZone();
        if (!z || z.mode !== 'line' || !ctx.snapshotImage) return;
        e.preventDefault();

        // 每次按下都重新开始绘制：以按下点为起点
        ctx.linePoints = [canvasPointFromEvent(e)];
        ctx.drawingPointIdx = 1;
        ctx.dragging = true;
        render();
    }

    function onCanvasMouseMove(e) {
        if (!ctx.dragging || ctx.linePoints.length === 0) return;
        ctx.linePoints[1] = canvasPointFromEvent(e);
        render();
    }

    function onCanvasMouseUp(e) {
        if (!ctx.dragging) return;
        ctx.dragging = false;

        const z = getActiveZone();
        const point = canvasPointFromEvent(e);
        const start = ctx.linePoints[0];
        if (!z || z.mode !== 'line' || !isLineLengthValid(start, point)) {
            // 拖动距离过短，视为仅确定了起点，等待继续拖动
            ctx.linePoints = start ? [start] : [];
            render();
            return;
        }

        ctx.linePoints = [start, point];
        ctx.drawingPointIdx = 2;
        const canvas = document.getElementById('countingZoneCanvas');
        // 已定两个点，转成归一化坐标存入区域
        z.cross_line = ctx.linePoints.map(p => [p[0] / canvas.width, p[1] / canvas.height]);
        render();
    }

    function onCanvasMouseLeave() {
        // 拖出画布：放弃本次拖动，保留起点等待重新拖动
        if (!ctx.dragging) return;
        ctx.dragging = false;
        ctx.linePoints = ctx.linePoints.length ? [ctx.linePoints[0]] : [];
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
                ctx.linePoints.length === 0 ? '按住鼠标左键确定起点，拖动到终点后松开' : '拖动到终点后松开鼠标完成分界线',
                canvas.width / 2, 24
            );
        }
    }

    // ========================================
    // 保存
    // ========================================

    async function saveCountingZones() {
        if (!ctx.taskId) return;

        // 校验并按层级分组：画线归通道、方向归任务
        const linesByChannel = {};   // 通道ID -> 画线区域数组
        const directions = [];       // 任务级方向区域数组
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
                    alert(`计数区域 "${z.zone_name}" 未绘制分界线，请在画布上按住鼠标左键从起点拖动到终点`);
                    selectCountingZone(z._id);
                    return;
                }
                if (!linesByChannel[z.channel_id]) linesByChannel[z.channel_id] = [];
                linesByChannel[z.channel_id].push({
                    zone_name: z.zone_name.trim(),
                    cross_line: z.cross_line,
                    in_side: z.in_side != null ? z.in_side : 1,
                    inertia: z.inertia,
                    min_displacement: z.min_displacement,
                    alarm_threshold: z.alarm_threshold != null ? z.alarm_threshold : 0
                });
            } else {
                if ((!z.in_channels || z.in_channels.length === 0) && (!z.out_channels || z.out_channels.length === 0)) {
                    alert(`计数区域 "${z.zone_name}" 至少需要一个入口机或出口机`);
                    selectCountingZone(z._id);
                    return;
                }
                directions.push({
                    zone_name: z.zone_name.trim(),
                    in_channels: z.in_channels || [],
                    out_channels: z.out_channels || [],
                    alarm_threshold: z.alarm_threshold != null ? z.alarm_threshold : 0
                });
            }
        }

        // 需要写回的通道 = 当前有画线的通道 ∪ 原本有画线的通道（后者写空数组以清空）
        const channelIds = new Set(Object.keys(linesByChannel).map(x => parseInt(x, 10)).concat(ctx.lineChannelsLoaded));

        const btn = document.getElementById('countingZoneSaveBtn');
        if (btn) { btn.disabled = true; btn.textContent = '保存中...'; }
        try {
            // 画线：按通道整体替换
            for (const channelId of channelIds) {
                const resp = await fetch(`/api/channels/${channelId}/counting-lines`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ lines: linesByChannel[channelId] || [] })
                });
                const data = await resp.json();
                if (!resp.ok) {
                    alert('保存失败: ' + (data.error || '未知错误'));
                    return;
                }
            }

            // 方向：任务级整体替换
            const resp = await fetch(`/api/tasks/${ctx.taskId}/counting-zones`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ zones: directions })
            });
            const data = await resp.json();
            if (!resp.ok) {
                alert('保存失败: ' + (data.error || '未知错误'));
                return;
            }

            showToast('计数区域保存成功', 'success');
            closeCountingZoneConfig();
        } catch (e) {
            console.error('[CountingZone] 保存失败:', e);
            alert('保存失败: ' + e.message);
        } finally {
            if (btn) { btn.disabled = false; btn.textContent = '保存配置'; }
        }
    }

    // ========================================
    // 工具
    // ========================================

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
})();