/**
 * SageSight AI - 跨相机目标轨迹页
 *
 * 数据来源：/api/cross-camera/*
 * - identities：一条轨迹 = 同一目标在多个相机上的片段集合
 * - stats：概览数字
 * 实时更新：监听 socket 的 cross_camera_update 事件，节流后刷新列表
 */

window.Tracks = (function() {
    'use strict';

    var state = {
        page: 1,
        pageSize: 24,
        total: 0,
        pages: 1,
        range: '86400',
        items: [],
        currentDetailId: null,
        loading: false
    };

    var trackSocket = null;
    var socketBound = false;
    var initialized = false;
    var reloadTimer = null;
    var camerasLoaded = false;
    var classesLoaded = false;
    var listenersBound = false;

    // ========================================
    // 工具
    // ========================================

    function escapeHtml(text) {
        var div = document.createElement('div');
        div.textContent = String(text == null ? '' : text);
        return div.innerHTML;
    }

    function toHttpUrl(raw) {
        if (!raw) return '';
        var value = String(raw);
        if (/^https?:\/\//i.test(value)) return value;
        var clean = value.replace(/\\/g, '/').replace(/^\.?\//, '');
        var port = window.location.port ? (':' + window.location.port) : '';
        return window.location.protocol + '//' + window.location.hostname + port + '/' + clean;
    }

    function pad2(n) {
        return n < 10 ? ('0' + n) : String(n);
    }

    // epoch(秒) -> 'MM-DD HH:MM:SS'
    function fmtDateTime(epoch) {
        var num = Number(epoch);
        if (!num || num <= 0) return '-';
        var d = new Date(num * 1000);
        if (isNaN(d.getTime())) return '-';
        return (d.getMonth() + 1) + '-' + pad2(d.getDate()) + ' ' +
            pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds());
    }

    // epoch(秒) -> 'HH:MM:SS'
    function fmtClock(epoch) {
        var num = Number(epoch);
        if (!num || num <= 0) return '--:--:--';
        var d = new Date(num * 1000);
        if (isNaN(d.getTime())) return '--:--:--';
        return pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds());
    }

    function humanDuration(seconds) {
        var s = Math.max(0, Math.round(Number(seconds) || 0));
        if (s < 60) return s + ' 秒';
        var m = Math.floor(s / 60);
        if (m < 60) return m + ' 分 ' + (s % 60) + ' 秒';
        var h = Math.floor(m / 60);
        if (h < 24) return h + ' 小时 ' + (m % 60) + ' 分';
        return Math.floor(h / 24) + ' 天 ' + (h % 24) + ' 小时';
    }

    function statusText(status) {
        if (status === 'closed') return '已结束';
        if (status === 'singleton') return '单点';
        return '进行中';
    }

    // ========================================
    // 加载：概览 / 筛选项
    // ========================================

    async function loadStats() {
        try {
            var resp = await fetch('/api/cross-camera/stats');
            var data = await resp.json();
            setText('statIdentities', data.identity_count || 0);
            setText('statMultiCamera', data.multi_camera_count || 0);
            setText('statLinks', data.link_count || 0);
            setText('statPending', data.pending_events || 0);
            var tip = document.getElementById('tracksDisabledTip');
            if (tip) tip.style.display = data.enabled ? 'none' : 'flex';

            // 有单点片段但没有串联轨迹时，解释清楚为什么不显示
            var stTip = document.getElementById('tracksSingletonTip');
            var stText = document.getElementById('tracksSingletonTipText');
            var singles = data.singleton_count || 0;
            var shown = document.getElementById('trackIncludeSingleton');
            var includeSingleton = !!(shown && shown.checked);
            if (stTip && stText) {
                if ((data.identity_count || 0) === 0 && singles > 0 && !includeSingleton) {
                    stText.textContent = '当前有 ' + singles + ' 个单点片段（只在一台相机出现，' +
                        '还没等到与其它相机的同一目标对上）。勾选「包含单点片段」可查看。';
                    stTip.style.display = 'flex';
                } else {
                    stTip.style.display = 'none';
                }
            }
        } catch (e) {
            console.error('[Tracks] 加载概览失败:', e);
        }
    }

    function setText(id, value) {
        var el = document.getElementById(id);
        if (el) el.textContent = value;
    }

    async function loadCameras() {
        var select = document.getElementById('trackCamera');
        if (!select) return;
        // pjax 换页后下拉是新的空元素，即使标志为真也要重填
        if (camerasLoaded && select.options.length > 1) return;
        try {
            var resp = await fetch('/api/cross-camera/cameras');
            var data = await resp.json();
            var items = data.items || [];
            var html = '<option value="">全部相机</option>';
            items.forEach(function(item) {
                html += '<option value="' + escapeHtml(item.id) + '">' +
                    escapeHtml(item.name || item.id) + '</option>';
            });
            select.innerHTML = html;
            camerasLoaded = true;
        } catch (e) {
            console.error('[Tracks] 加载相机列表失败:', e);
        }
    }

    async function loadClasses() {
        var select = document.getElementById('trackClass');
        if (!select) return;
        if (classesLoaded && select.options.length > 1) return;
        try {
            var resp = await fetch('/api/events/stats');
            var data = await resp.json();
            var byClass = data.by_class || {};
            var html = '<option value="">全部类别</option>';
            Object.keys(byClass).forEach(function(name) {
                if (!name) return;
                html += '<option value="' + escapeHtml(name) + '">' +
                    escapeHtml(name) + ' (' + byClass[name] + ')</option>';
            });
            select.innerHTML = html;
            classesLoaded = true;
        } catch (e) {
            console.error('[Tracks] 加载类别失败:', e);
        }
    }

    // ========================================
    // 加载：轨迹列表
    // ========================================

    function buildParams() {
        var params = new URLSearchParams();
        params.append('page', state.page);
        params.append('page_size', state.pageSize);

        var className = val('trackClass');
        if (className) params.append('class_name', className);

        var cameraId = val('trackCamera');
        if (cameraId) params.append('camera_id', cameraId);

        var multi = document.getElementById('trackMultiOnly');
        if (multi && multi.checked) params.append('multi_camera', '1');

        // 默认只看已串联（≥2 段）的轨迹，勾选后连单点片段一起看
        var includeSingleton = document.getElementById('trackIncludeSingleton');
        if (includeSingleton && includeSingleton.checked) params.append('min_events', '1');

        if (state.range === 'custom') {
            var startInput = document.getElementById('trackStartTime');
            var endInput = document.getElementById('trackEndTime');
            if (startInput && startInput.value) {
                params.append('start_time', new Date(startInput.value).getTime() / 1000);
            }
            if (endInput && endInput.value) {
                params.append('end_time', new Date(endInput.value).getTime() / 1000);
            }
        } else {
            var seconds = parseInt(state.range, 10) || 0;
            if (seconds > 0) {
                params.append('start_time', (Date.now() / 1000 - seconds).toFixed(0));
            }
        }
        return params.toString();
    }

    function val(id) {
        var el = document.getElementById(id);
        return el ? (el.value || '') : '';
    }

    async function loadIdentities() {
        if (state.loading) return;
        state.loading = true;
        var grid = document.getElementById('tracksGrid');
        try {
            var resp = await fetch('/api/cross-camera/identities?' + buildParams());
            var data = await resp.json();
            state.items = data.items || [];
            state.total = data.total || 0;
            state.pages = data.pages || 1;
            state.page = data.page || 1;
            renderCards();
            renderPagination();
            setText('tracksCountInfo', '共 ' + state.total + ' 条轨迹');
            // 标记本次 DOM 已渲染过，pjax 换页后据此判断需要重新拉数
            if (grid) grid.setAttribute('data-ready', '1');
        } catch (e) {
            console.error('[Tracks] 加载轨迹失败:', e);
            if (grid) {
                grid.innerHTML = '<div class="empty-state"><p>加载失败，请稍后重试</p></div>';
            }
        } finally {
            state.loading = false;
        }
    }

    function renderCards() {
        var grid = document.getElementById('tracksGrid');
        if (!grid) return;

        if (!state.items.length) {
            grid.innerHTML =
                '<div class="empty-state">' +
                '<svg viewBox="0 0 24 24" fill="currentColor">' +
                '<path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z"/>' +
                '</svg><p>暂无轨迹数据</p></div>';
            return;
        }

        grid.innerHTML = state.items.map(createCard).join('');
    }

    function createCard(item) {
        var imageUrl = toHttpUrl(item.best_image_url);
        var cameraCount = item.camera_count || (item.cameras || []).length || 0;
        var cameraNames = item.camera_names || [];
        var camText = cameraNames.filter(function(n) { return !!n; }).join('、');

        var media = imageUrl
            ? '<img src="' + escapeHtml(imageUrl) + '" alt="轨迹预览" loading="lazy" ' +
              'onerror="this.style.display=\'none\';this.parentElement.classList.add(\'track-img-failed\')">'
            : '<div class="track-no-image">' +
              '<svg viewBox="0 0 24 24" fill="currentColor">' +
              '<path d="M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2z"/>' +
              '</svg><span>无预览</span></div>';

        var strongHtml = item.strong_id
            ? '<div class="track-strong" title="强 ID：' + escapeHtml(item.strong_id) + '">' +
              escapeHtml(item.strong_id) + '</div>'
            : '';

        var duration = (item.last_seen || 0) - (item.first_seen || 0);

        return '' +
            '<div class="track-card" data-identity-id="' + escapeHtml(item.identity_id) + '" ' +
            'onclick="Tracks.openDetail(\'' + escapeHtml(item.identity_id) + '\')">' +
                '<div class="track-card-media">' + media +
                    '<span class="track-cam-count' + (cameraCount > 1 ? ' multi' : '') + '">' +
                        cameraCount + ' 台相机</span>' +
                    '<span class="track-status-tag ' + (item.status || 'active') + '">' +
                        statusText(item.status) + '</span>' +
                '</div>' +
                '<div class="track-card-body">' +
                    '<div class="track-card-head">' +
                        '<span class="track-class">' + escapeHtml(item.class_name || '未知类别') + '</span>' +
                        '<span class="track-seg-count">' + (item.event_count || 0) + ' 段</span>' +
                    '</div>' +
                    strongHtml +
                    '<div class="track-range">' + fmtDateTime(item.first_seen) + '<br>→ ' +
                        fmtDateTime(item.last_seen) + '</div>' +
                    '<div class="track-duration">持续 ' + humanDuration(duration) + '</div>' +
                    (camText ? '<div class="track-cams" title="' + escapeHtml(camText) + '">' +
                        escapeHtml(camText) + '</div>' : '') +
                '</div>' +
            '</div>';
    }

    // ========================================
    // 分页
    // ========================================

    function renderPagination() {
        var info = document.getElementById('tracksPaginationInfo');
        var prevBtn = document.getElementById('tracksPrevBtn');
        var nextBtn = document.getElementById('tracksNextBtn');
        var numbers = document.getElementById('tracksPageNumbers');
        if (!info || !prevBtn || !nextBtn || !numbers) return;

        info.textContent = '共 ' + state.total + ' 条轨迹，第 ' + state.page + ' / ' + state.pages + ' 页';
        prevBtn.disabled = state.page <= 1;
        nextBtn.disabled = state.page >= state.pages;

        var html = '';
        var pages = state.pages;
        var current = state.page;
        var start = Math.max(1, current - 3);
        var end = Math.min(pages, start + 6);
        start = Math.max(1, end - 6);

        if (start > 1) {
            html += pageBtn(1, current);
            if (start > 2) html += '<span class="pagination-ellipsis">...</span>';
        }
        for (var i = start; i <= end; i++) {
            html += pageBtn(i, current);
        }
        if (end < pages) {
            if (end < pages - 1) html += '<span class="pagination-ellipsis">...</span>';
            html += pageBtn(pages, current);
        }
        numbers.innerHTML = html;
    }

    function pageBtn(page, current) {
        return '<button class="page-number' + (page === current ? ' active' : '') +
            '" onclick="Tracks.goToPage(' + page + ')">' + page + '</button>';
    }

    function goToPage(page) {
        if (page < 1 || page > state.pages) return;
        state.page = page;
        loadIdentities();
    }

    function prevPage() { goToPage(state.page - 1); }
    function nextPage() { goToPage(state.page + 1); }

    function changePageSize() {
        var select = document.getElementById('tracksPageSize');
        state.pageSize = parseInt(select.value, 10) || 24;
        state.page = 1;
        loadIdentities();
    }

    // ========================================
    // 筛选
    // ========================================

    function onRangeChange() {
        var select = document.getElementById('trackRange');
        state.range = select.value;
        var custom = document.getElementById('trackCustomRange');
        if (custom) custom.style.display = state.range === 'custom' ? 'flex' : 'none';
        applyFilters();
    }

    function applyFilters() {
        state.page = 1;
        loadIdentities();
    }

    // 单点片段开关：提示条要跟着一起更新
    function toggleSingleton() {
        loadStats();
        applyFilters();
    }

    function resetFilters() {
        state.range = '86400';
        state.page = 1;
        var rangeSelect = document.getElementById('trackRange');
        if (rangeSelect) rangeSelect.value = '86400';
        var custom = document.getElementById('trackCustomRange');
        if (custom) custom.style.display = 'none';
        ['trackClass', 'trackCamera'].forEach(function(id) {
            var el = document.getElementById(id);
            if (el) el.value = '';
        });
        var multi = document.getElementById('trackMultiOnly');
        if (multi) multi.checked = false;
        var singleton = document.getElementById('trackIncludeSingleton');
        if (singleton) singleton.checked = false;
        loadStats();
        loadIdentities();
    }

    function refresh() {
        loadStats();
        loadIdentities();
    }

    // ========================================
    // 详情
    // ========================================

    async function openDetail(identityId) {
        if (!identityId) return;
        state.currentDetailId = identityId;
        var modal = document.getElementById('trackDetailModal');
        var body = document.getElementById('trackDetailBody');
        if (!modal || !body) return;

        body.innerHTML = '<div class="loading-placeholder">加载中...</div>';
        modal.style.display = 'flex';

        try {
            var resp = await fetch('/api/cross-camera/identities/' + encodeURIComponent(identityId));
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            var detail = await resp.json();
            if (state.currentDetailId !== identityId) return;
            body.innerHTML = renderDetail(detail);
            setText('trackDetailTitle', '轨迹详情 · ' + (detail.class_name || '未知类别'));
        } catch (e) {
            console.error('[Tracks] 加载轨迹详情失败:', e);
            body.innerHTML = '<div class="empty-state"><p>加载失败，请稍后重试</p></div>';
        }
    }

    function closeDetail() {
        var modal = document.getElementById('trackDetailModal');
        if (modal) modal.style.display = 'none';
        state.currentDetailId = null;
    }

    function renderDetail(detail) {
        var timeline = detail.timeline || [];
        var imageUrl = toHttpUrl(detail.best_image_url);

        var imageHtml = imageUrl
            ? '<img src="' + escapeHtml(imageUrl) + '" alt="轨迹代表图" ' +
              'onclick="Tracks.openLightbox(this.src)" ' +
              'onerror="this.style.display=\'none\';this.parentElement.classList.add(\'track-img-failed\')">'
            : '<div class="track-no-image"><svg viewBox="0 0 24 24" fill="currentColor">' +
              '<path d="M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2z"/>' +
              '</svg><span>无预览</span></div>';

        var cameraNames = detail.camera_names || [];
        var meta = '' +
            metaItem('目标类别', detail.class_name || '-') +
            metaItem('相机数量', (detail.camera_count || cameraNames.length || 0) + ' 台') +
            metaItem('片段数量', (detail.event_count || timeline.length || 0) + ' 段') +
            metaItem('首次出现', fmtDateTime(detail.first_seen)) +
            metaItem('最后出现', fmtDateTime(detail.last_seen)) +
            metaItem('持续时长', humanDuration(detail.duration_seconds)) +
            (detail.strong_id ? metaItem('强 ID', detail.strong_id) : '') +
            metaItem('状态', statusText(detail.status)) +
            metaItem('相机', cameraNames.join('、') || '-');

        return '' +
            '<div class="track-detail-top">' +
                '<div class="track-detail-image">' + imageHtml + '</div>' +
                '<div class="track-detail-meta">' + meta + '</div>' +
            '</div>' +
            renderTimeline(timeline, detail) +
            renderSegments(timeline);
    }

    function metaItem(label, value) {
        return '<div class="track-meta-item">' + escapeHtml(label) +
            '<b>' + escapeHtml(value == null ? '-' : value) + '</b></div>';
    }

    /**
     * 多相机时间轴：每个相机一行，片段按时间占比画成横条
     */
    function renderTimeline(timeline, detail) {
        if (!timeline.length) {
            return '<div class="track-timeline-wrap">' +
                '<div class="track-section-title">多相机时间轴</div>' +
                '<div class="empty-state" style="padding:16px;"><p>暂无片段</p></div></div>';
        }

        var t0 = Number(detail.first_seen) || 0;
        var t1 = Number(detail.last_seen) || 0;
        timeline.forEach(function(seg) {
            var s = Number(seg.start_time) || 0;
            var e = Number(seg.end_time) || 0;
            if (s && (!t0 || s < t0)) t0 = s;
            if (e && e > t1) t1 = e;
        });
        var span = t1 - t0;
        if (span <= 0) span = 1;

        // 按相机分组（保持首次出现顺序）
        var order = [];
        var groups = {};
        timeline.forEach(function(seg) {
            var key = seg.camera_id || '';
            if (!groups[key]) {
                groups[key] = { name: seg.camera_name || key || '未知相机', segs: [] };
                order.push(key);
            }
            groups[key].segs.push(seg);
        });

        var rows = order.map(function(key) {
            var group = groups[key];
            var bars = group.segs.map(function(seg) {
                var s = Number(seg.start_time) || 0;
                var e = Number(seg.end_time) || 0;
                var left = ((s - t0) / span) * 100;
                var width = ((e - s) / span) * 100;
                left = Math.max(0, Math.min(99.5, left));
                width = Math.max(1.2, Math.min(100 - left, width));
                var tip = fmtClock(seg.start_time) + ' → ' + fmtClock(seg.end_time) +
                    '（' + humanDuration(seg.duration) + '）';
                return '<div class="track-seg-bar" style="left:' + left.toFixed(2) + '%;width:' +
                    width.toFixed(2) + '%;" title="' + escapeHtml(tip) + '" ' +
                    'onclick="Tracks.openLightbox(\'' + escapeHtml(toHttpUrl(seg.image_url)) + '\')"></div>';
            }).join('');
            return '<div class="track-cam-row">' +
                '<div class="track-cam-label" title="' + escapeHtml(group.name) + '">' +
                escapeHtml(group.name) + '</div>' +
                '<div class="track-cam-lane">' + bars + '</div>' +
                '</div>';
        }).join('');

        return '<div class="track-timeline-wrap">' +
            '<div class="track-section-title">多相机时间轴 ' +
            '<span class="hint">共 ' + order.length + ' 台相机 / ' + timeline.length + ' 个片段</span></div>' +
            '<div class="track-axis"><span>' + fmtClock(t0) + '</span><span>' + fmtClock(t1) + '</span></div>' +
            rows + '</div>';
    }

    function renderSegments(timeline) {
        if (!timeline.length) return '';

        var items = timeline.map(function(seg, index) {
            var imgUrl = toHttpUrl(seg.image_url);
            var thumb = imgUrl
                ? '<img src="' + escapeHtml(imgUrl) + '" alt="片段" loading="lazy" ' +
                  'onclick="Tracks.openLightbox(this.src)" ' +
                  'onerror="this.style.display=\'none\'">'
                : '';
            var score = Number(seg.score) || 0;
            var scoreHtml = index === 0 ? '<span class="track-seg-score">起点</span>'
                : '<span class="track-seg-score" title="' + escapeHtml(seg.reason || '') + '">匹配 ' +
                  (score * 100).toFixed(0) + '%</span>';
            var desc = seg.ai_description || '';
            var zones = seg.zones || '';
            var line2 = [
                fmtDateTime(seg.start_time) + ' → ' + fmtClock(seg.end_time),
                '持续 ' + humanDuration(seg.duration)
            ].join(' · ');
            if (zones) line2 += ' · 区域 ' + zones;
            if (desc) line2 += ' · ' + desc;

            return '<div class="track-seg-item">' +
                '<div class="track-seg-thumb">' + thumb + '</div>' +
                '<div class="track-seg-main">' +
                    '<div class="track-seg-line1">' + escapeHtml(seg.camera_name || seg.camera_id || '未知相机') +
                        ' · ' + escapeHtml(seg.class_name || '') + '</div>' +
                    '<div class="track-seg-line2" title="' + escapeHtml(line2) + '">' +
                        escapeHtml(line2) + '</div>' +
                '</div>' + scoreHtml +
                '</div>';
        }).join('');

        return '<div class="track-section-title">片段明细 ' +
            '<span class="hint">按时间正序，点击缩略图放大</span></div>' +
            '<div class="track-seg-list">' + items + '</div>';
    }

    // ========================================
    // 图片预览
    // ========================================

    function openLightbox(url) {
        if (!url) return;
        var box = document.getElementById('trackImageLightbox');
        var img = document.getElementById('trackLightboxImg');
        if (!box || !img) return;
        img.src = url;
        box.classList.add('show');
    }

    function closeLightbox() {
        var box = document.getElementById('trackImageLightbox');
        var img = document.getElementById('trackLightboxImg');
        if (box) box.classList.remove('show');
        if (img) img.src = '';
    }

    // ========================================
    // 实时更新
    // ========================================

    function scheduleReload() {
        if (reloadTimer) return;
        reloadTimer = setTimeout(function() {
            reloadTimer = null;
            if (!document.getElementById('tracksGrid')) return;
            loadStats();
            loadIdentities();
            if (state.currentDetailId) openDetail(state.currentDetailId);
        }, 2500);
    }

    /**
     * WebSocket：优先复用 main.js 的全局连接，避免 pjax 切页后留下孤儿连接
     */
    function initSocket() {
        if (socketBound) return;
        var conn = (typeof socket !== 'undefined' && socket) ? socket : null;
        if (conn) {
            conn.on('cross_camera_update', function(data) {
                console.log('[Tracks] 轨迹更新:', data);
                scheduleReload();
            });
            socketBound = true;
            return;
        }
        if (!window.io) return;
        try {
            trackSocket = io('/ws', { transports: ['websocket', 'polling'] });
            trackSocket.on('cross_camera_update', function(data) {
                console.log('[Tracks] 轨迹更新:', data);
                scheduleReload();
            });
            socketBound = true;
        } catch (e) {
            console.error('[Tracks] WebSocket 初始化失败:', e);
        }
    }

    /**
     * 展开指定轨迹（事件列表的"轨迹"徽标调用）
     * 面板切换由事件页负责，这里只保证数据已加载并打开详情
     */
    function goToTrack(identityId) {
        if (!identityId) return;
        window.Tracks.pendingIdentity = identityId;
        ensureInit();
        openDetail(identityId);
        window.Tracks.pendingIdentity = null;
    }

    // ========================================
    // 初始化（首次切到轨迹视图时才真正跑接口）
    // ========================================

    function ensureInit() {
        var grid = document.getElementById('tracksGrid');
        // pjax 换过 DOM 后 grid 是全新的（没有 data-ready），需要重新拉一次
        if (initialized && grid && grid.getAttribute('data-ready') === '1') return;
        if (!grid) return;
        initialized = true;

        var rangeSelect = document.getElementById('trackRange');
        if (rangeSelect) state.range = rangeSelect.value || '86400';

        var pageSizeSelect = document.getElementById('tracksPageSize');
        if (pageSizeSelect) state.pageSize = parseInt(pageSizeSelect.value, 10) || 24;

        loadStats();
        loadCameras();
        loadClasses();
        loadIdentities();
        initSocket();

        // Esc 关闭弹层（document 级，只绑一次）
        if (!listenersBound) {
            listenersBound = true;
            document.addEventListener('keydown', function(e) {
                if (e.key === 'Escape') {
                    closeLightbox();
                    closeDetail();
                }
            });
        }
        // 弹窗遮罩：pjax 后是新的元素，按元素去重绑定
        var modal = document.getElementById('trackDetailModal');
        if (modal && modal.getAttribute('data-bound') !== '1') {
            modal.setAttribute('data-bound', '1');
            modal.addEventListener('click', function(e) {
                if (e.target === modal) closeDetail();
            });
        }
    }

    function init() {
        ensureInit();
    }

    return {
        init: init,
        ensureInit: ensureInit,
        pendingIdentity: null,
        goToTrack: goToTrack,
        refresh: refresh,
        applyFilters: applyFilters,
        toggleSingleton: toggleSingleton,
        resetFilters: resetFilters,
        onRangeChange: onRangeChange,
        openDetail: openDetail,
        closeDetail: closeDetail,
        goToPage: goToPage,
        prevPage: prevPage,
        nextPage: nextPage,
        changePageSize: changePageSize,
        openLightbox: openLightbox,
        closeLightbox: closeLightbox
    };
})();
