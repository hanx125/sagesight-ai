/**
 * Zone Canvas 编辑器
 *
 * 基于摄像机快照底图，通过 Canvas 绘制多边形区域。
 *
 * 交互方式:
 * - 左键点击画布: 在当前多边形添加新顶点
 * - 点击第一个顶点 或 双击: 闭合多边形，保存为区域
 * - 左键按住顶点拖动: 移动顶点位置
 * - 右键点击顶点: 删除该顶点（至少保留3个）
 * - 点击多边形内部: 切换活跃区域，右侧显示属性
 *
 * 坐标体系:
 * - 存储使用归一化坐标 [0-1]，polygon_type="normalized"
 * - 渲染时通过 _normalizedToCanvas 转为 Canvas 像素坐标
 */
class ZoneCanvasEditor {
    /**
     * @param {string} canvasId - Canvas 元素 ID
     * @param {object} callbacks - 回调函数
     * @param {function} callbacks.onZoneSelected - 区域被选中时回调
     * @param {function} callbacks.onZoneAdded - 区域添加时回调
     * @param {function} callbacks.onZoneDeleted - 区域删除时回调
     * @param {function} callbacks.onZonesChanged - 区域列表变化时回调
     */
    constructor(canvasId, callbacks = {}) {
        this.canvas = document.getElementById(canvasId);
        if (!this.canvas) {
            console.error('[ZoneCanvasEditor] Canvas 元素不存在:', canvasId);
            return;
        }
        this.ctx = this.canvas.getContext('2d');

        this.callbacks = callbacks;

        // 快照底图
        this.snapshotImage = null;
        this.snapshotWidth = 0;
        this.snapshotHeight = 0;

        // 区域列表: [{ id, name, polygon: [{x, y}], color, required, enable_loitering_detection, loitering_time, inertia }]
        // polygon 中的 {x, y} 是归一化坐标 [0-1]
        this.zones = [];
        this._zoneIdCounter = 0;

        // 当前正在绘制的多边形顶点（归一化坐标）
        this.drawingPoints = [];

        // 当前选中的区域 ID
        this.activeZoneId = null;

        // 当前正在为其绘制多边形的目标区域 ID（先建区域再绘制模式）
        this.drawingTargetZoneId = null;

        // 拖拽状态
        this.draggingVertex = null; // { zoneId, vertexIndex }

        // 颜色预设
        this.colorPresets = [
            [255, 0, 0],     // 红
            [0, 255, 0],     // 绿
            [0, 0, 255],     // 蓝
            [255, 165, 0],   // 橙
            [128, 0, 128],   // 紫
            [0, 255, 255],   // 青
            [255, 255, 0],   // 黄
            [255, 20, 147],  // 粉
        ];

        // 鼠标状态
        this.mousePos = { x: 0, y: 0 }; // Canvas 像素坐标

        this._bindEvents();
        this.render();
    }

    // ========================================
    // 快照底图管理
    // ========================================

    /**
     * 设置快照底图
     * @param {string} imageUrl - 快照图片 URL
     * @param {number} width - 快照原始宽度
     * @param {number} height - 快照原始高度
     */
    setSnapshot(imageUrl, width, height) {
        this.snapshotWidth = width;
        this.snapshotHeight = height;

        const img = new Image();
        img.onload = () => {
            this.snapshotImage = img;
            // 设置 Canvas 尺寸为快照尺寸（按比例缩放到最大宽度 640）
            const maxWidth = 640;
            let canvasW = width;
            let canvasH = height;
            if (width > maxWidth) {
                canvasW = maxWidth;
                canvasH = Math.round(height * (maxWidth / width));
            }
            this.canvas.width = canvasW;
            this.canvas.height = canvasH;
            this.render();
        };
        img.onerror = () => {
            console.error('[ZoneCanvasEditor] 快照加载失败');
            this.snapshotImage = null;
            this.canvas.width = 640;
            this.canvas.height = 480;
            this.render();
        };
        img.src = imageUrl;
    }

    /**
     * 清除快照底图
     */
    clearSnapshot() {
        this.snapshotImage = null;
        this.snapshotWidth = 0;
        this.snapshotHeight = 0;
        this.render();
    }

    // ========================================
    // 坐标转换
    // ========================================

    /**
     * 归一化坐标 [0-1] → Canvas 像素坐标
     */
    _normalizedToCanvas(nx, ny) {
        return {
            x: nx * this.canvas.width,
            y: ny * this.canvas.height
        };
    }

    /**
     * Canvas 像素坐标 → 归一化坐标 [0-1]
     */
    _canvasToNormalized(cx, cy) {
        return {
            x: cx / this.canvas.width,
            y: cy / this.canvas.height
        };
    }

    /**
     * 获取鼠标在 Canvas 上的像素坐标
     */
    _getMousePos(e) {
        const rect = this.canvas.getBoundingClientRect();
        const scaleX = this.canvas.width / rect.width;
        const scaleY = this.canvas.height / rect.height;
        return {
            x: (e.clientX - rect.left) * scaleX,
            y: (e.clientY - rect.top) * scaleY
        };
    }

    // ========================================
    // 事件绑定
    // ========================================

    _bindEvents() {
        this.canvas.addEventListener('click', (e) => this._onClick(e));
        this.canvas.addEventListener('dblclick', (e) => this._onDoubleClick(e));
        this.canvas.addEventListener('mousedown', (e) => this._onMouseDown(e));
        this.canvas.addEventListener('mousemove', (e) => this._onMouseMove(e));
        this.canvas.addEventListener('mouseup', (e) => this._onMouseUp(e));
        this.canvas.addEventListener('contextmenu', (e) => this._onRightClick(e));
        this.canvas.addEventListener('mouseleave', (e) => this._onMouseLeave(e));
    }

    _onClick(e) {
        const pos = this._getMousePos(e);
        this.mousePos = pos;

        // 如果正在拖拽，不处理点击
        if (this.draggingVertex) return;

        // 正在绘制多边形时：仅处理闭合或添加顶点，不选中其他区域
        // 注意：必须用 isDrawing() 判断（drawingTargetZoneId 非空也算绘制模式），
        // 不能只用 drawingPoints.length>0——startDrawingForZone 后 drawingPoints 为空，
        // 第一次点击时 length=0 会导致进不来此分支，无法添加首个顶点。
        if (this.isDrawing()) {
            const vertexHit = this._hitTestVertex(pos);
            // 点击正在绘制的多边形首顶点 → 闭合
            if (vertexHit && vertexHit.isDrawing && this.drawingPoints.length >= 3 && vertexHit.vertexIndex === 0) {
                this._finishDrawing();
                return;
            }
            // 否则添加新顶点
            const normalized = this._canvasToNormalized(pos.x, pos.y);
            this.drawingPoints.push(normalized);
            this.render();
            return;
        }

        // 非绘制状态：点击顶点或区域内部以选中
        const vertexHit = this._hitTestVertex(pos);
        if (vertexHit && !vertexHit.isDrawing) {
            this.selectZone(vertexHit.zoneId);
            return;
        }
        const zoneHit = this._hitTestZone(pos);
        if (zoneHit) {
            this.selectZone(zoneHit);
            return;
        }
        // 非绘制状态下点击空白：不做任何事（需先点"添加区域"进入绘制模式）
    }

    _onDoubleClick(e) {
        e.preventDefault();
        if (this.drawingPoints.length >= 3) {
            this._finishDrawing();
        }
    }

    _onMouseDown(e) {
        if (e.button !== 0) return; // 仅左键

        const pos = this._getMousePos(e);
        const vertexHit = this._hitTestVertex(pos);

        if (vertexHit && !vertexHit.isDrawing) {
            // 开始拖拽顶点
            this.draggingVertex = {
                zoneId: vertexHit.zoneId,
                vertexIndex: vertexHit.vertexIndex
            };
            this.canvas.style.cursor = 'grabbing';
        }
    }

    _onMouseMove(e) {
        const pos = this._getMousePos(e);
        this.mousePos = pos;

        if (this.draggingVertex) {
            // 拖拽顶点：更新坐标
            const normalized = this._canvasToNormalized(pos.x, pos.y);
            const zone = this.zones.find(z => z.id === this.draggingVertex.zoneId);
            if (zone && this.draggingVertex.vertexIndex < zone.polygon.length) {
                zone.polygon[this.draggingVertex.vertexIndex] = normalized;
                this.render();
            }
        } else {
            // 更新鼠标样式
            const vertexHit = this._hitTestVertex(pos);
            if (vertexHit) {
                this.canvas.style.cursor = 'pointer';
            } else if (this.isDrawing()) {
                // 绘制模式下（含尚未点击首个顶点的状态）统一显示十字光标
                this.canvas.style.cursor = 'crosshair';
            } else {
                const zoneHit = this._hitTestZone(pos);
                this.canvas.style.cursor = zoneHit ? 'pointer' : 'crosshair';
            }

            // 正在绘制时，重绘以显示虚线预览
            if (this.drawingPoints.length > 0) {
                this.render();
            }
        }
    }

    _onMouseUp(e) {
        if (this.draggingVertex) {
            this.draggingVertex = null;
            this.canvas.style.cursor = 'crosshair';
            if (this.callbacks.onZonesChanged) {
                this.callbacks.onZonesChanged(this.getZones());
            }
        }
    }

    _onRightClick(e) {
        e.preventDefault();
        const pos = this._getMousePos(e);

        const vertexHit = this._hitTestVertex(pos);
        if (vertexHit && !vertexHit.isDrawing) {
            // 删除顶点（至少保留3个）
            const zone = this.zones.find(z => z.id === vertexHit.zoneId);
            if (zone && zone.polygon.length > 3) {
                zone.polygon.splice(vertexHit.vertexIndex, 1);
                this.render();
                if (this.callbacks.onZonesChanged) {
                    this.callbacks.onZonesChanged(this.getZones());
                }
            }
        }
    }

    _onMouseLeave(e) {
        if (this.draggingVertex) {
            this.draggingVertex = null;
            this.canvas.style.cursor = 'crosshair';
        }
        this.render();
    }

    // ========================================
    // 碰撞检测
    // ========================================

    /**
     * 检测是否点击了某个顶点
     * @returns {object|null} { zoneId, vertexIndex, isDrawing } 或 null
     */
    _hitTestVertex(pos) {
        const threshold = 8; // 像素半径

        // 先检查已有多边形的顶点
        for (const zone of this.zones) {
            for (let i = 0; i < zone.polygon.length; i++) {
                const canvasPos = this._normalizedToCanvas(zone.polygon[i].x, zone.polygon[i].y);
                const dist = Math.sqrt(
                    (pos.x - canvasPos.x) ** 2 + (pos.y - canvasPos.y) ** 2
                );
                if (dist <= threshold) {
                    return { zoneId: zone.id, vertexIndex: i, isDrawing: false };
                }
            }
        }

        // 再检查正在绘制的多边形的顶点
        for (let i = 0; i < this.drawingPoints.length; i++) {
            const canvasPos = this._normalizedToCanvas(this.drawingPoints[i].x, this.drawingPoints[i].y);
            const dist = Math.sqrt(
                (pos.x - canvasPos.x) ** 2 + (pos.y - canvasPos.y) ** 2
            );
            if (dist <= threshold) {
                return { zoneId: null, vertexIndex: i, isDrawing: true };
            }
        }

        return null;
    }

    /**
     * 检测是否点击了某个多边形内部
     * @returns {number|null} zoneId 或 null
     */
    _hitTestZone(pos) {
        // 从后往前检查（后绘制的在上层）
        for (let i = this.zones.length - 1; i >= 0; i--) {
            const zone = this.zones[i];
            const canvasPoints = zone.polygon.map(p =>
                this._normalizedToCanvas(p.x, p.y)
            );
            if (this._pointInPolygon(pos, canvasPoints)) {
                return zone.id;
            }
        }
        return null;
    }

    /**
     * 射线法判断点是否在多边形内部
     */
    _pointInPolygon(point, polygon) {
        let inside = false;
        const n = polygon.length;
        for (let i = 0, j = n - 1; i < n; j = i++) {
            const xi = polygon[i].x, yi = polygon[i].y;
            const xj = polygon[j].x, yj = polygon[j].y;
            if (((yi > point.y) !== (yj > point.y)) &&
                (point.x < (xj - xi) * (point.y - yi) / (yj - yi + 1e-12) + xi)) {
                inside = !inside;
            }
        }
        return inside;
    }

    // ========================================
    // 区域管理
    // ========================================

    /**
     * 完成当前正在绘制的多边形
     *
     * 新流程：若设置了 drawingTargetZoneId，则把多边形绑定到该空区域；
     * 否则回退到旧逻辑（直接绘制创建新区域）。
     */
    _finishDrawing() {
        if (this.drawingPoints.length < 3) return;

        // 为指定空区域绑定多边形
        if (this.drawingTargetZoneId !== null) {
            const zone = this.zones.find(z => z.id === this.drawingTargetZoneId);
            if (zone) {
                zone.polygon = this.drawingPoints.slice();
                this.activeZoneId = zone.id;
            }
            this.drawingTargetZoneId = null;
            this.drawingPoints = [];
            this.render();
            if (this.callbacks.onZonesChanged) this.callbacks.onZonesChanged(this.getZones());
            if (zone && this.callbacks.onZoneSelected) this.callbacks.onZoneSelected(zone);
            return;
        }

        // 旧逻辑兼容：直接绘制创建新区域
        const id = ++this._zoneIdCounter;
        const colorIndex = (this.zones.length) % this.colorPresets.length;

        const zone = {
            id: id,
            name: `区域${id}`,
            polygon: this.drawingPoints.slice(), // 归一化坐标
            color: this.colorPresets[colorIndex],
            required: false,
            enable_loitering_detection: false,
            loitering_time: 30,
            inertia: 3
        };

        this.zones.push(zone);
        this.drawingPoints = [];
        this.activeZoneId = id;

        this.render();

        if (this.callbacks.onZoneAdded) this.callbacks.onZoneAdded(zone);
        if (this.callbacks.onZoneSelected) this.callbacks.onZoneSelected(zone);
        if (this.callbacks.onZonesChanged) this.callbacks.onZonesChanged(this.getZones());
    }

    /**
     * 新增一个空区域（暂无多边形），并选中它
     * @param {string} [name] - 区域名称（可选）
     * @returns {object} 新建的区域对象
     */
    addEmptyZone(name) {
        const id = ++this._zoneIdCounter;
        const colorIndex = (this.zones.length) % this.colorPresets.length;
        const zone = {
            id: id,
            name: name || `区域${id}`,
            polygon: [],
            color: this.colorPresets[colorIndex],
            required: false,
            enable_loitering_detection: false,
            loitering_time: 30,
            inertia: 3
        };
        this.zones.push(zone);
        this.activeZoneId = id;
        this.render();
        if (this.callbacks.onZoneAdded) this.callbacks.onZoneAdded(zone);
        if (this.callbacks.onZoneSelected) this.callbacks.onZoneSelected(zone);
        if (this.callbacks.onZonesChanged) this.callbacks.onZonesChanged(this.getZones());
        return zone;
    }

    /**
     * 为指定区域进入绘制模式（清空其旧多边形，重新绘制）
     * @param {number} zoneId - 目标区域 ID
     */
    startDrawingForZone(zoneId) {
        const zone = this.zones.find(z => z.id === zoneId);
        if (!zone) return;
        this.drawingTargetZoneId = zoneId;
        this.activeZoneId = zoneId;
        this.drawingPoints = [];
        this.render();
    }

    /**
     * 是否正在为某个区域绘制
     */
    isDrawing() {
        return this.drawingPoints.length > 0 || this.drawingTargetZoneId !== null;
    }

    /**
     * 选中区域（切换选中会取消正在进行的绘制）
     */
    selectZone(zoneId) {
        this.drawingPoints = [];
        this.drawingTargetZoneId = null;
        this.activeZoneId = zoneId;
        this.render();

        const zone = this.zones.find(z => z.id === zoneId);
        if (zone && this.callbacks.onZoneSelected) {
            this.callbacks.onZoneSelected(zone);
        }
    }

    /**
     * 取消选中
     */
    deselectZone() {
        this.activeZoneId = null;
        this.render();
        if (this.callbacks.onZoneSelected) {
            this.callbacks.onZoneSelected(null);
        }
    }

    /**
     * 删除区域（若删除的正是绘制目标，则取消绘制）
     */
    deleteZone(zoneId) {
        const index = this.zones.findIndex(z => z.id === zoneId);
        if (index !== -1) {
            const deleted = this.zones.splice(index, 1)[0];
            if (this.activeZoneId === zoneId) {
                this.activeZoneId = null;
            }
            if (this.drawingTargetZoneId === zoneId) {
                this.drawingTargetZoneId = null;
                this.drawingPoints = [];
            }
            this.render();
            if (this.callbacks.onZoneDeleted) this.callbacks.onZoneDeleted(deleted);
            if (this.callbacks.onZonesChanged) this.callbacks.onZonesChanged(this.getZones());
        }
    }

    /**
     * 更新区域属性
     */
    updateZone(zoneId, updates) {
        const zone = this.zones.find(z => z.id === zoneId);
        if (zone) {
            Object.assign(zone, updates);
            this.render();
            if (this.callbacks.onZonesChanged) {
                this.callbacks.onZonesChanged(this.getZones());
            }
        }
    }

    /**
     * 取消正在绘制的多边形
     */
    cancelDrawing() {
        this.drawingPoints = [];
        this.drawingTargetZoneId = null;
        this.render();
    }

    /**
     * 清除所有区域
     */
    clearAll() {
        this.zones = [];
        this.drawingPoints = [];
        this.drawingTargetZoneId = null;
        this.activeZoneId = null;
        this._zoneIdCounter = 0;
        this.render();
        if (this.callbacks.onZonesChanged) {
            this.callbacks.onZonesChanged(this.getZones());
        }
    }

    /**
     * 获取所有区域（归一化坐标格式，用于保存到后端）
     */
    getZones() {
        return this.zones.map(z => ({
            name: z.name,
            polygon: z.polygon.map(p => [p.x, p.y]),
            color: z.color,
            required: z.required,
            enable_loitering_detection: z.enable_loitering_detection,
            loitering_time: z.loitering_time,
            inertia: z.inertia,
            polygon_type: 'normalized'
        }));
    }

    /**
     * 从后端数据加载区域（归一化坐标）
     * @param {array} zonesData - 区域配置列表
     */
    loadZones(zonesData) {
        this.zones = [];
        this._zoneIdCounter = 0;
        this.drawingPoints = [];
        this.activeZoneId = null;

        for (const zd of zonesData || []) {
            const id = ++this._zoneIdCounter;

            // 归一化坐标直接存储为 {x, y}，render() 时再转为 Canvas 坐标
            const polygon = (zd.polygon || []).map(p => {
                if (zd.polygon_type === 'normalized' || zd.polygon_type === undefined) {
                    return { x: p[0], y: p[1] };
                }
                // 像素坐标：先归一化再存储
                return this._canvasToNormalized(
                    p[0] * (this.canvas.width / (this.snapshotWidth || this.canvas.width)),
                    p[1] * (this.canvas.height / (this.snapshotHeight || this.canvas.height))
                );
            });

            if (polygon.length < 3) continue;

            this.zones.push({
                id: id,
                name: zd.name || `区域${id}`,
                polygon: polygon,
                color: zd.color || this.colorPresets[(id - 1) % this.colorPresets.length],
                required: zd.required || false,
                enable_loitering_detection: zd.enable_loitering_detection || false,
                loitering_time: zd.loitering_time || 30,
                inertia: zd.inertia || 3
            });
        }

        this.render();
        if (this.callbacks.onZonesChanged) {
            this.callbacks.onZonesChanged(this.getZones());
        }
    }

    // ========================================
    // 渲染
    // ========================================

    render() {
        const ctx = this.ctx;
        const w = this.canvas.width;
        const h = this.canvas.height;

        // 清空画布
        ctx.clearRect(0, 0, w, h);

        // 绘制快照底图
        if (this.snapshotImage) {
            ctx.drawImage(this.snapshotImage, 0, 0, w, h);
        } else {
            // 无底图时绘制灰色背景
            ctx.fillStyle = '#2a2a2a';
            ctx.fillRect(0, 0, w, h);
            ctx.fillStyle = '#666';
            ctx.font = '14px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText('请点击"刷新快照"获取摄像机画面', w / 2, h / 2);
        }

        // 绘制已完成的多边形
        for (const zone of this.zones) {
            this._drawZone(zone);
        }

        // 绘制正在绘制的多边形
        if (this.drawingPoints.length > 0) {
            this._drawDrawingPolygon();
        }
    }

    _drawZone(zone) {
        const ctx = this.ctx;
        const isActive = zone.id === this.activeZoneId;
        const colorRgb = zone.color || [0, 255, 0];
        const colorStr = `rgb(${colorRgb[0]}, ${colorRgb[1]}, ${colorRgb[2]})`;
        const colorStrAlpha = `rgba(${colorRgb[0]}, ${colorRgb[1]}, ${colorRgb[2]}, ${isActive ? 0.35 : 0.2})`;

        // 转换为 Canvas 坐标
        const canvasPoints = zone.polygon.map(p =>
            this._normalizedToCanvas(p.x, p.y)
        );

        if (canvasPoints.length < 3) return;

        // 半透明填充
        ctx.fillStyle = colorStrAlpha;
        ctx.beginPath();
        ctx.moveTo(canvasPoints[0].x, canvasPoints[0].y);
        for (let i = 1; i < canvasPoints.length; i++) {
            ctx.lineTo(canvasPoints[i].x, canvasPoints[i].y);
        }
        ctx.closePath();
        ctx.fill();

        // 边框
        ctx.strokeStyle = colorStr;
        ctx.lineWidth = isActive ? 3 : 2;
        ctx.stroke();

        // 顶点
        for (let i = 0; i < canvasPoints.length; i++) {
            const p = canvasPoints[i];
            ctx.fillStyle = isActive ? '#fff' : colorStr;
            ctx.beginPath();
            ctx.arc(p.x, p.y, isActive ? 5 : 4, 0, Math.PI * 2);
            ctx.fill();
            ctx.strokeStyle = colorStr;
            ctx.lineWidth = 2;
            ctx.stroke();
        }

        // 区域名称（重心位置）
        let cx = 0, cy = 0;
        for (const p of canvasPoints) {
            cx += p.x;
            cy += p.y;
        }
        cx /= canvasPoints.length;
        cy /= canvasPoints.length;

        ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
        ctx.font = 'bold 12px sans-serif';
        ctx.textAlign = 'center';
        const labelText = zone.name + (zone.required ? ' *' : '');
        const textWidth = ctx.measureText(labelText).width;
        ctx.fillRect(cx - textWidth / 2 - 4, cy - 8, textWidth + 8, 16);
        ctx.fillStyle = '#fff';
        ctx.fillText(labelText, cx, cy + 4);
    }

    _drawDrawingPolygon() {
        const ctx = this.ctx;
        const colorStr = 'rgb(255, 255, 0)';

        // 转换为 Canvas 坐标
        const canvasPoints = this.drawingPoints.map(p =>
            this._normalizedToCanvas(p.x, p.y)
        );

        // 虚线连接
        ctx.strokeStyle = colorStr;
        ctx.lineWidth = 2;
        ctx.setLineDash([6, 4]);
        ctx.beginPath();
        ctx.moveTo(canvasPoints[0].x, canvasPoints[0].y);
        for (let i = 1; i < canvasPoints.length; i++) {
            ctx.lineTo(canvasPoints[i].x, canvasPoints[i].y);
        }
        // 连接到鼠标位置
        ctx.lineTo(this.mousePos.x, this.mousePos.y);
        ctx.stroke();
        ctx.setLineDash([]);

        // 顶点
        for (let i = 0; i < canvasPoints.length; i++) {
            const p = canvasPoints[i];
            // 第一个顶点特殊样式（可点击闭合）
            if (i === 0 && this.drawingPoints.length >= 3) {
                ctx.fillStyle = 'rgba(255, 255, 0, 0.3)';
                ctx.beginPath();
                ctx.arc(p.x, p.y, 10, 0, Math.PI * 2);
                ctx.fill();
            }
            ctx.fillStyle = colorStr;
            ctx.beginPath();
            ctx.arc(p.x, p.y, 5, 0, Math.PI * 2);
            ctx.fill();
            ctx.strokeStyle = '#000';
            ctx.lineWidth = 1;
            ctx.stroke();
        }

        // 提示文字（含目标区域名）
        const targetZone = this.drawingTargetZoneId !== null
            ? this.zones.find(z => z.id === this.drawingTargetZoneId)
            : null;
        const targetLabel = targetZone ? `「${targetZone.name}」` : '';
        if (this.drawingPoints.length >= 3) {
            const msg = `正在为${targetLabel}绘制：点击首顶点或双击闭合`;
            ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
            ctx.fillRect(8, this.canvas.height - 28, 320, 22);
            ctx.fillStyle = '#fff';
            ctx.font = '12px sans-serif';
            ctx.textAlign = 'left';
            ctx.fillText(msg, 12, this.canvas.height - 12);
        } else {
            const msg = `正在为${targetLabel}绘制：继续点击添加顶点 (${this.drawingPoints.length}/3)`;
            ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
            ctx.fillRect(8, this.canvas.height - 28, 320, 22);
            ctx.fillStyle = '#fff';
            ctx.font = '12px sans-serif';
            ctx.textAlign = 'left';
            ctx.fillText(msg, 12, this.canvas.height - 12);
        }
    }
}
