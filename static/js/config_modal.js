// ========================================
// 参数配置功能
// ========================================
// 说明：此处仅保留任务级动态参数——检测器置信度阈值、事件输出开关、报警语音播报。
// 解码器/检测器/跟踪器/运动检测/事件输出/显示配置统一由 config/*.yaml 提供，
// 任务表不再保存这些配置项（避免同一参数存在多个来源）。
// ========================================

// 当前配置的任务ID
let currentConfigTaskId = null;

// 切换配置选项卡
function switchConfigTab(tabName) {
    // 切换选项卡激活状态
    document.querySelectorAll('.config-tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.tab === tabName);
    });
    // 切换面板显示
    document.querySelectorAll('.tab-panel').forEach(panel => {
        panel.classList.toggle('active', panel.dataset.tab === tabName);
    });
}

// 打开参数配置模态框
async function openConfigModal(taskId, event) {
    event.stopPropagation();

    try {
        // 获取任务详情（包含所有配置）
        const response = await fetch(`/api/tasks/${taskId}`);
        const task = await response.json();

        if (!response.ok) {
            showNotification(task.error || '获取任务配置失败', 'error');
            return;
        }

        currentConfigTaskId = taskId;

        // 设置模态框标题
        document.getElementById('configModalTitle').textContent = `参数配置 - ${task.task_name}`;
        document.getElementById('configTaskId').value = taskId;

        // 先加载报警语音 MP3 预置选项，再回填表单（保证选中的 MP3 能正确显示）
        await loadVoiceMp3List();

        // 加载当前配置到表单
        loadConfigToForm(task);

        // 重置到第一个选项卡
        switchConfigTab('detector');

        // 显示模态框
        document.getElementById('configModal').classList.add('show');

    } catch (error) {
        console.error('打开配置模态框失败:', error);
        showNotification('打开配置模态框失败', 'error');
    }
}

// 加载配置到表单
function loadConfigToForm(task) {
    // 检测器配置（仅置信度阈值）
    const detectorConfig = task.detector_config || {};
    document.getElementById('detectorConfThreshold').value = detectorConfig.conf_threshold || 0.25;

    // 事件输出开关
    const eventConfig = task.event_config || {};
    document.getElementById('eventEnabled').value = eventConfig.enabled !== false ? 'true' : 'false';

    // 报警语音播报配置（任务级独立字段）
    document.getElementById('voiceEnabled').value = task.voice_enabled ? 'true' : 'false';
    document.getElementById('voiceMp3').value = task.voice_mp3 || '';
    document.getElementById('voiceLoopCount').value = task.voice_loop_count ?? 1;
    document.getElementById('voiceIntervalMs').value = task.voice_interval_ms ?? 1000;
    document.getElementById('voiceVolume').value = task.voice_volume ?? 1.0;
}

// 加载报警语音 MP3 预置列表，填充 voiceMp3 下拉选项
async function loadVoiceMp3List() {
    const select = document.getElementById('voiceMp3');
    if (!select) return;
    try {
        const resp = await fetch('/api/system/voice-files');
        const data = await resp.json();
        const files = (data && data.files) || [];
        select.innerHTML = '<option value="">使用全局默认</option>';
        files.forEach(f => {
            const opt = document.createElement('option');
            opt.value = f.name;
            opt.textContent = f.name;
            select.appendChild(opt);
        });
    } catch (e) {
        console.warn('加载报警语音 MP3 列表失败:', e);
    }
}

// 关闭参数配置模态框
function closeConfigModal() {
    document.getElementById('configModal').classList.remove('show');
    currentConfigTaskId = null;
}

// 保存配置
async function saveConfig() {
    if (!currentConfigTaskId) {
        showNotification('任务ID无效', 'error');
        return;
    }

    try {
        // 获取任务状态（用于保存后的针对性提示）
        const taskResponse = await fetch(`/api/tasks/${currentConfigTaskId}`);
        const task = await taskResponse.json();

        if (!taskResponse.ok) {
            showNotification(task.error || '获取任务配置失败', 'error');
            return;
        }

        // 构建配置数据（仅提交任务级动态参数，未提交的字段由后端保留原值）
        const configData = {
            detector_config: {
                conf_threshold: parseFloat(document.getElementById('detectorConfThreshold').value) || 0.25
            },

            event_config: {
                enabled: document.getElementById('eventEnabled').value === 'true'
            },

            // 报警语音播报配置（任务级独立字段）
            voice_enabled: document.getElementById('voiceEnabled').value === 'true',
            voice_mp3: document.getElementById('voiceMp3').value || '',
            voice_loop_count: parseInt(document.getElementById('voiceLoopCount').value, 10) || 1,
            voice_interval_ms: parseInt(document.getElementById('voiceIntervalMs').value, 10) || 1000,
            voice_volume: parseFloat(document.getElementById('voiceVolume').value) != null
                ? parseFloat(document.getElementById('voiceVolume').value)
                : 1.0
        };

        // 发送更新请求
        const response = await fetch(`/api/tasks/${currentConfigTaskId}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(configData)
        });

        const result = await response.json();

        if (response.ok) {
            // 根据任务状态给出针对性提示
            const taskStatus = task.status || 'stopped';
            const isRunning = taskStatus === 'running';

            if (isRunning) {
                // 任务运行中：提示需要重启才能生效的配置
                showNotification('配置已保存。置信度阈值等变更需重启任务后生效', 'warning');
            } else {
                showNotification('配置已保存', 'success');
            }
            closeConfigModal();
            // 刷新任务列表（tasks.js 已改造为 IIFE 命名空间）
            if (window.Tasks && typeof window.Tasks.loadTasks === 'function') {
                window.Tasks.loadTasks();
            }
        } else {
            showNotification(result.error || '保存配置失败', 'error');
        }

    } catch (error) {
        console.error('保存配置失败:', error);
        showNotification('保存配置失败', 'error');
    }
}

// 点击模态框外部关闭
document.addEventListener('click', function(event) {
    if (event.target.id === 'configModal') {
        closeConfigModal();
    }
});