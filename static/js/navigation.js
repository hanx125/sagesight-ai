/**
 * SageSight AI - PJAX 局部导航
 * 切换导航菜单时只加载内容区域，标题栏保持不变
 *
 * 工作原理：
 * 1. 拦截导航链接点击 → fetch 目标 URL → 替换 main-wrapper 内容
 * 2. 页面脚本注册初始化函数到 SageSight.pages[path]
 * 3. pjax 导航时调用已注册的初始化函数；首次访问则动态加载脚本
 */

(function() {
    'use strict';

    // 不随页面切换重新加载的基础脚本
    const BASE_SCRIPTS = [
        'timer_manager.js', 'performance_monitor.js', 'diagnostic.js',
        'main.js', 'navigation.js', 'socket.io'
    ];

    // 不随页面切换重新加载的基础样式（全局样式，head 中始终保留）
    const BASE_STYLES = [
        'css/style.css', 'font-awesome', 'cdnjs.cloudflare.com'
    ];

    let isNavigating = false;

    // 记录已执行的内联脚本（避免重复执行导致事件监听器重复绑定）
    const loadedInlineScripts = new Set();

    // 初始化页面注册表（不覆盖 head 中提前定义的存根对象）
    window.SageSight = window.SageSight || {};
    SageSight.pages = SageSight.pages || {};

    // 处理存根期间注册的页面（内联脚本在 navigation.js 之前执行的情况）
    // 存根已通过 DOMContentLoaded 或立即执行完成初始化，这里只需记录到 pages 注册表
    if (SageSight._pending && SageSight._pending.length > 0) {
        SageSight._pending.forEach(function(reg) {
            SageSight.pages[reg.path] = reg.fn;
        });
        delete SageSight._pending;
    }

    /**
     * 注册页面初始化函数（正式实现，替换 head 中的存根）
     * @param {string} path - 页面路径（如 '/system'）
     * @param {Function} initFn - 初始化函数
     */
    SageSight.registerPage = function(path, initFn) {
        SageSight.pages[path] = initFn;
        // 首次加载时（整页加载）readyState 为 loading，由 DOMContentLoaded 触发
        // pjax 加载时 readyState 为 complete，自动执行
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', initFn);
        } else {
            initFn();
        }
    };

    /**
     * 判断 URL 是否支持 pjax（同源、非登录页）
     */
    function isPjaxable(url) {
        try {
            const u = new URL(url, location.origin);
            if (u.origin !== location.origin) return false;
            if (u.pathname === '/login' || u.pathname.startsWith('/login')) return false;
            return true;
        } catch (e) {
            return false;
        }
    }

    /**
     * 拦截导航链接点击
     */
    document.addEventListener('click', function(e) {
        // 修饰键（新标签页等）跳过
        if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button !== 0) return;

        const link = e.target.closest('a');
        if (!link) return;
        if (link.target === '_blank') return;

        const href = link.getAttribute('href');
        if (!href || href === '#' || href.startsWith('javascript:')) return;
        if (href.startsWith('http://') || href.startsWith('https://')) return;
        if (!isPjaxable(href)) return;

        e.preventDefault();
        navigateTo(href);
    });

    /**
     * pjax 导航到指定 URL
     */
    async function navigateTo(url) {
        if (isNavigating) return;
        isNavigating = true;
        document.body.classList.add('pjax-loading');

        // 加载过渡动画
        const main = document.querySelector('main.main-wrapper');
        if (main) main.style.opacity = '0.4';

        try {
            const resp = await fetch(url, {
                headers: { 'X-PJAX': 'true' },
                credentials: 'same-origin'
            });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);

            const html = await resp.text();
            const doc = new DOMParser().parseFromString(html, 'text/html');

            // 更新页面标题
            if (doc.title) document.title = doc.title;

            // 清理上一个页面的定时器和脚本
            cleanupPage();

            // 替换 main 内容
            const newMain = doc.querySelector('main.main-wrapper');
            if (newMain && main) {
                main.innerHTML = newMain.innerHTML;
                main.style.opacity = '1';
            }

            // 同步页面特定样式表（外部 link + 内联 style）
            syncPageStyles(doc);

            // 更新导航激活状态
            updateActiveNav(url);

            // 提取目标路径
            const path = new URL(url, location.origin).pathname;

            // 页面初始化
            const initFn = SageSight.pages[path];
            if (initFn) {
                // 初始化函数已注册（脚本之前加载过），直接调用
                initFn();
            } else {
                // 脚本未加载，动态加载页面脚本
                const loaded = await loadPageScripts(doc, path);
                if (!loaded) {
                    // 无外部脚本且未注册初始化函数（如内联脚本页面首次 pjax 访问）
                    // fallback 到整页加载
                    window.location.href = url;
                    return;
                }
                // 脚本加载时已自动执行初始化（readyState !== 'loading'）
            }

            // 重新启动核心定时器（pjax 导航前已停止）
            if (window.TimerManager && TimerManager.startEssential) {
                TimerManager.startEssential();
            }

            // 更新 URL
            history.pushState({ url: url }, '', url);
            window.scrollTo(0, 0);

        } catch (err) {
            console.error('PJAX 导航失败，回退到整页加载:', err);
            window.location.href = url;
        } finally {
            isNavigating = false;
            document.body.classList.remove('pjax-loading');
            if (main) main.style.opacity = '1';
        }
    }

    /**
     * 清理上一个页面的资源（定时器、socket 监听器）
     * 注意：不移除已加载的 script 标签，因为 let/const 声明的全局变量
     *       无法通过移除标签来清理，重复加载会导致 SyntaxError
     */
    function cleanupPage() {
        // 通知后端停止所有正在推送的 AI 流（删除 .flag 标记文件），
        // 避免 pjax 切换页面后后端继续编码/画框/推送造成 CPU 与带宽浪费
        if (window.Monitor && typeof Monitor.cleanup === 'function') {
            try { Monitor.cleanup(); } catch (e) { console.error('[cleanup] Monitor.cleanup 失败:', e); }
        }
        // 停止所有定时器
        if (window.TimerManager && TimerManager.stopAll) {
            TimerManager.stopAll();
        }
        // 重置 WebSocket 连接（清除页面特定的 socket 监听器，保留核心连接）
        if (SageSight.resetSocket) {
            SageSight.resetSocket();
        }
    }

    /**
     * 获取脚本的路径标识（忽略 query 参数，用于比对是否已加载）
     */
    function getScriptKey(src) {
        try {
            const url = new URL(src, location.origin);
            return url.pathname;  // 只比较路径，忽略 ?v=xxx 等版本参数
        } catch (e) {
            return src;
        }
    }

    /**
     * 动态加载页面脚本
     * @returns {Promise<boolean>} 是否成功加载了新脚本
     */
    async function loadPageScripts(doc, targetPath) {
        // 收集当前页面已加载的脚本路径（忽略 query 参数）
        const loadedScripts = new Set();
        document.querySelectorAll('script[src]').forEach(s => {
            loadedScripts.add(getScriptKey(s.getAttribute('src')));
        });

        const scripts = doc.querySelectorAll('script');
        let loadedAny = false;

        for (const s of scripts) {
            const src = s.getAttribute('src') || '';
            if (src) {
                // 外部脚本
                if (BASE_SCRIPTS.some(b => src.includes(b))) continue;
                const scriptKey = getScriptKey(src);
                if (loadedScripts.has(scriptKey)) continue;
                await loadExternalScript(src);
                loadedScripts.add(scriptKey);
                loadedAny = true;
            } else if (s.textContent.trim()) {
                // 内联脚本：用内容前 200 字符作为标识，避免重复执行
                const code = s.textContent;
                const codeKey = code.substring(0, 200);
                if (loadedInlineScripts.has(codeKey)) continue;
                loadedInlineScripts.add(codeKey);
                loadInlineScript(code);
                loadedAny = true;
            }
        }

        // 检查目标路径是否已注册初始化函数
        if (SageSight.pages[targetPath]) {
            return true;
        }

        return loadedAny;
    }

    /**
     * 执行内联脚本（通过创建 script 标签让浏览器执行）
     */
    function loadInlineScript(code) {
        const s = document.createElement('script');
        s.textContent = code;
        s.dataset.pjax = 'true';
        document.body.appendChild(s);
    }

    /**
     * 动态加载外部 JS 文件
     */
    function loadExternalScript(src) {
        return new Promise((resolve) => {
            const s = document.createElement('script');
            s.src = src;
            s.dataset.pjax = 'true';
            s.onload = () => resolve();
            s.onerror = () => {
                console.error('加载脚本失败:', src);
                resolve();  // 出错也继续
            };
            document.body.appendChild(s);
        });
    }

    /**
     * 判断样式是否属于基础样式（不随页面切换清理）
     */
    function isBaseStyle(href) {
        return BASE_STYLES.some(b => href.includes(b));
    }

    /**
     * 同步页面特定样式表
     * 清理上一次 pjax 加载的样式，加载目标页面中的页面特定样式
     * @param {Document} doc - 目标页面的 DOMParser 文档
     */
    function syncPageStyles(doc) {
        // 1. 清理上一次 pjax 加载的样式（标记 data-pjax 的 link 和 style）
        document.querySelectorAll('link[data-pjax][rel="stylesheet"]').forEach(el => el.remove());
        document.querySelectorAll('style[data-pjax]').forEach(el => el.remove());

        // 2. 加载目标页面 head 中的页面特定外部样式表
        doc.querySelectorAll('head link[rel="stylesheet"]').forEach(link => {
            const href = link.getAttribute('href') || '';
            if (isBaseStyle(href)) return;  // 跳过全局样式
            const newLink = document.createElement('link');
            newLink.rel = 'stylesheet';
            newLink.href = href;
            newLink.dataset.pjax = 'true';
            document.head.appendChild(newLink);
        });

        // 3. 加载目标页面中的内联样式块（可能在 head 或 body 中，如 extra_scripts 块）
        doc.querySelectorAll('style').forEach(style => {
            const code = style.textContent;
            if (!code.trim()) return;
            const newStyle = document.createElement('style');
            newStyle.textContent = code;
            newStyle.dataset.pjax = 'true';
            document.head.appendChild(newStyle);
        });
    }

    /**
     * 更新导航激活状态
     */
    function updateActiveNav(url) {
        const path = new URL(url, location.origin).pathname;
        document.querySelectorAll('.nav-link').forEach(link => {
            const href = link.getAttribute('href');
            link.classList.toggle('active', href === path);
        });
    }

    /**
     * 浏览器前进/后退
     */
    window.addEventListener('popstate', function(e) {
        const url = (e.state && e.state.url) || location.href;
        if (url) navigateTo(url);
    });

    // 记录初始状态
    history.replaceState({ url: location.href }, '', location.href);

    // 暴露导航函数
    SageSight.navigateTo = navigateTo;

})();
