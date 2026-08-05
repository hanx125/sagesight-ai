/**
 * SageSight AI - 任务管理页面 JavaScript
 * 支持多摄像机多任务架构
 */
window.Tasks = (function() {

// ========================================
// 全局变量
// ========================================

var tasks = [];
var availableCameras = [];  // 可选择的监控设备列表（cameras表）
var addedChannels = [];  // 已添加的设备通道列表（device_channel_pool表）
var algorithms = [];  // 算法库列表
var selectedCameras = [];  // 当前选中的摄像机（用于添加到device_channel_pool）
var tempSelectedChannels = [];  // 临时选中的通道（未入库，仅前端显示）
var selectedChannels = [];  // 已入库且选中的通道ID列表（device_channel_pool.id）
var currentView = 'card';
var deleteTaskId = null;
var currentTaskId = null;  // 当前查看详情的任务ID
var isEditMode = false;  // 是否为编辑模式

// 分页相关变量
var currentPage = 1;
var perPage = 20;
var totalTasks = 0;
var totalPages = 1;

// ========================================
// 页面初始化
// ========================================

// 注册页面初始化函数（支持 pjax 局部加载）
SageSight.registerPage('/tasks', function() {
    // 清空搜索框，防止浏览器自动填充
    const filterSearch = document.getElementById('filterSearch');
    if (filterSearch) {
        filterSearch.value = '';
    }

    loadStatistics();
    loadTasks();
    loadAvailableCameras();  // 加载可选择的监控设备列表
    loadAddedChannels();  // 加载已添加的设备通道列表
    loadAlgorithms();  // 加载算法库列表
    initWebSocket();
    setupColorPickerListeners();  // 注册颜色选择器变化监听
});

// ========================================
// WebSocket 初始化
// ========================================

function initWebSocket() {
    // 使用全局 socket 连接（由 main.js 初始化）
    const socket = getSocket();

    if (socket) {
        socket.on('task_created', function(data) {
            loadTasks();
            loadStatistics();
        });
        
        socket.on('task_updated', function(data) {
            loadTasks();
            loadStatistics();
        });
        
        socket.on('task_deleted', function(data) {
            loadTasks();
            loadStatistics();
        });
        
        socket.on('task_started', function(data) {
            loadTasks();
        });
        
        socket.on('task_stopped', function(data) {
            loadTasks();
        });
        
        socket.on('camera_created', function(data) {
            loadAvailableCameras();
            loadStatistics();
        });

        socket.on('camera_updated', function(data) {
            loadAvailableCameras();
        });

        socket.on('camera_deleted', function(data) {
            loadAvailableCameras();
            loadStatistics();
        });

        socket.on('device_channel_created', function(data) {
            loadAddedChannels();
            loadStatistics();
        });

        socket.on('device_channel_updated', function(data) {
            loadAddedChannels();
        });

        socket.on('device_channel_deleted', function(data) {
            loadAddedChannels();
            loadStatistics();
        });
        
        socket.on('camera_bound', function(data) {
            loadTasks();
            loadStatistics();
        });
        
        socket.on('camera_unbound', function(data) {
            loadTasks();
            loadStatistics();
        });
    }
}

// ========================================
// 加载统计信息
// ========================================

async function loadStatistics() {
    try {
        const response = await fetch('/api/statistics');
        const stats = await response.json();

        // pjax 导航后 DOM 可能已被替换，操作前检查元素是否存在
        const setText = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value; };
        setText('statTotal', stats.total_tasks || 0);
        setText('statRunning', stats.running_tasks || 0);
        setText('statCameras', stats.total_cameras || 0);
        setText('statBindings', stats.total_bindings || 0);

    } catch (error) {
        console.error('加载统计信息失败:', error);
    }
}

// ========================================
// 加载算法库列表
// ========================================

async function loadAlgorithms() {
    try {
        const response = await fetch('/api/algorithms?status=active');
        algorithms = await response.json();
        
        // 更新算法配置选择下拉框
        updateAlgorithmConfigSelect();
        
        console.log('加载算法库列表成功: 共' + algorithms.length + '个算法');
    } catch (error) {
        console.error('加载算法库列表失败:', error);
    }
}

// ========================================
// 更新算法配置选择下拉框
// ========================================

function updateAlgorithmConfigSelect() {
    const algorithmConfigSelect = document.getElementById('algorithmConfig');
    if (!algorithmConfigSelect) return;
    
    // 清空现有选项
    algorithmConfigSelect.innerHTML = '<option value="">-- 请选择算法配置 --</option>';
    
    // 添加算法库中的配置
    algorithms.forEach(algo => {
        if (algo.status === 'active') {
            const option = document.createElement('option');
            option.value = algo.id;
            option.textContent = `${algo.name} (${algo.type})`;
            option.dataset.name = algo.name;
            option.dataset.type = algo.type;
            option.dataset.modelPath = algo.model_path || '';
            option.dataset.targetClasses = '';  // 不再填充target_classes
            // target_classes改为从API动态加载（选择算法后调用loadAlgorithmClasses）
            // option.dataset.confThreshold = algo.parameters?.conf_threshold?.default || 0.5;
            // option.dataset.nmsThreshold = algo.parameters?.nms_threshold?.default || 0.45;
            // option.dataset.maxDet = algo.parameters?.max_det?.default || 100;
            // option.dataset.fps = algo.parameters?.fps?.default || 30;
            
            algorithmConfigSelect.appendChild(option);
        }
    });
}

// ========================================
// 算法配置选择变更处理
// ========================================

function onAlgorithmConfigChange() {
    const algorithmConfigSelect = document.getElementById('algorithmConfig');
    const algorithm = document.getElementById('algorithm');
    const modelPath = document.getElementById('modelPath');
    
    if (!algorithmConfigSelect) return;
    
    const selectedOption = algorithmConfigSelect.options[algorithmConfigSelect.selectedIndex];
    
    if (selectedOption && selectedOption.value) {
        const algorithmId = selectedOption.value;
        const algorithmName = selectedOption.dataset.name;
        
        // 填充隐藏的algorithm字段（使用算法名称）
        if (algorithm) {
            algorithm.value = algorithmName;
        }
        
        // 填充模型路径
        if (modelPath) {
            modelPath.value = selectedOption.dataset.modelPath || '';
        }
        
        console.log('算法配置已选择:', algorithmName, '模型:', modelPath.value);
        
        // 清空目标类别输入框（等待用户点击选择按钮）
        const targetClassesInput = document.getElementById('targetClassesInput');
        if (targetClassesInput) {
            targetClassesInput.value = '';
        }
        const targetClassesHidden = document.getElementById('targetClassesHidden');
        if (targetClassesHidden) {
            targetClassesHidden.value = '';
        }
    } else {
        // 清空字段
        if (algorithm) algorithm.value = '';
        if (modelPath) modelPath.value = '';
        if (targetClassesInput) targetClassesInput.value = '';
        if (targetClassesHidden) targetClassesHidden.value = '';
    }
}

// ========================================
// 打开目标类别选择对话框（新增）
// ========================================

function openClassesSelectDialog() {
    console.log('[调试] openClassesSelectDialog 被调用');

    const algorithmSelect = document.getElementById('algorithmConfig');
    console.log('[调试] algorithmSelect元素:', algorithmSelect);
    console.log('[调试] algorithmSelect.value:', algorithmSelect ? algorithmSelect.value : 'null');

    if (!algorithmSelect || !algorithmSelect.value) {
        showNotification('请先选择算法配置', 'error');
        console.log('[调试] 算法配置未选择，返回');
        return;
    }

    const algorithmId = algorithmSelect.value;
    console.log('[调试] 算法ID:', algorithmId);

    const dialog = document.getElementById('classesSelectDialog');
    const container = document.getElementById('classesSelectContainer');

    console.log('[调试] dialog元素:', dialog);
    console.log('[调试] container元素:', container);

    if (!dialog || !container) {
        console.error('[错误] 对话框元素不存在！请检查HTML中的classesSelectDialog和classesSelectContainer元素');
        showNotification('对话框元素不存在，请刷新页面重试', 'error');
        return;
    }

    console.log('[调试] 显示对话框...');
    // 显示对话框
    dialog.style.display = 'flex';

    console.log('[调试] 加载类别列表...');
    // 加载类别列表
    loadClassesForSelectDialog(algorithmId);
}

// ========================================
// 加载类别列表到选择对话框（新增）
// ========================================

async function loadClassesForSelectDialog(algorithmId) {
    const container = document.getElementById('classesSelectContainer');
    if (!container) return;
    
    // 显示加载提示
    container.innerHTML = '<p style="text-align: center; color: #666;">加载类别列表...</p>';
    
    try {
        const response = await fetch(`/api/algorithms/${algorithmId}/classes`);
        
        if (!response.ok) {
            throw new Error('获取类别列表失败');
        }
        
        const data = await response.json();
        renderClassesSelectDialog(data.classes);
        
    } catch (error) {
        console.error('加载类别列表失败:', error);
        container.innerHTML = '<p style="text-align: center; color: #dc3545;">加载失败：' + error.message + '</p>';
    }
}

// ========================================
// 渲染类别选择对话框（新增）
// ========================================

function renderClassesSelectDialog(classes) {
    const container = document.getElementById('classesSelectContainer');
    if (!container) return;
    
    // 清空容器
    container.innerHTML = '';
    
    if (classes.length === 0) {
        container.innerHTML = '<p style="text-align: center; color: #666;">该算法没有类别信息，请上传标签文件</p>';
        return;
    }
    
    // 获取当前已选择的类别
    const inputField = document.getElementById('targetClassesInput');
    const currentClasses = inputField && inputField.value ? inputField.value.split(',').map(s => s.trim()) : [];
    
    // 创建单列布局容器
    const scrollContainer = document.createElement('div');
    scrollContainer.style.cssText = `
        display: flex;
        flex-direction: column;
        gap: 6px;
        padding: 10px;
        border: 1px solid #ddd;
        border-radius: 4px;
        background-color: white;
    `;
    
    // 创建多选框
    classes.forEach((className, index) => {
        const checkboxItem = document.createElement('div');
        checkboxItem.className = 'class-select-item';
        checkboxItem.style.cssText = `
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 8px 12px;
            border-radius: 4px;
            background-color: #f8f9fa;
            border: 1px solid #e0e0e0;
        `;
        
        // 标签容器
        const labelContainer = document.createElement('div');
        labelContainer.style.cssText = `
            flex-grow: 1;
            min-width: 0;
            margin-right: 12px;
            cursor: pointer;
        `;
        
        const label = document.createElement('span');
        label.textContent = className;
        label.style.cssText = `
            font-size: 14px;
            color: #333;
            display: block;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        `;
        
        // 复选框容器
        const checkboxContainer = document.createElement('div');
        checkboxContainer.style.cssText = `
            flex-shrink: 0;
        `;
        
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.id = `dialog_class_${index}`;
        checkbox.value = className;
        checkbox.name = 'dialog_target_class';
        checkbox.style.cssText = `
            width: 18px;
            height: 18px;
            cursor: pointer;
        `;
        
        // 检查是否已选择
        if (currentClasses.includes(className)) {
            checkbox.checked = true;
        }
        
        // 点击标签也触发复选框
        labelContainer.onclick = () => {
            checkbox.checked = !checkbox.checked;
        };
        
        labelContainer.appendChild(label);
        checkboxContainer.appendChild(checkbox);
        checkboxItem.appendChild(labelContainer);
        checkboxItem.appendChild(checkboxContainer);
        scrollContainer.appendChild(checkboxItem);
    });
    
    container.appendChild(scrollContainer);
}

// ========================================
// 全选（对话框内）（新增）
// ========================================

function selectAllClasses() {
    const checkboxes = document.querySelectorAll('#classesSelectContainer input[type="checkbox"]');
    checkboxes.forEach(cb => cb.checked = true);
}

// ========================================
// 取消全选（对话框内）（新增）
// ========================================

function deselectAllClasses() {
    const checkboxes = document.querySelectorAll('#classesSelectContainer input[type="checkbox"]');
    checkboxes.forEach(cb => cb.checked = false);
}

// ========================================
// 确认类别选择（新增）
// ========================================

function confirmClassesSelection() {
    const checkboxes = document.querySelectorAll('#classesSelectContainer input[type="checkbox"]:checked');
    const selectedClasses = Array.from(checkboxes).map(cb => cb.value);
    
    // 更新输入框
    const inputField = document.getElementById('targetClassesInput');
    if (inputField) {
        inputField.value = selectedClasses.join(', ');
    }
    
    // 更新隐藏字段
    const hiddenField = document.getElementById('targetClassesHidden');
    if (hiddenField) {
        hiddenField.value = JSON.stringify(selectedClasses);
    }
    
    // 关闭对话框
    closeClassesSelectDialog();
    
    // 显示提示
    if (selectedClasses.length > 0) {
        showNotification(`已选择 ${selectedClasses.length} 个类别`, 'success');
    } else {
        showNotification('未选择任何类别', 'warning');
    }
}

// ========================================
// 关闭目标类别选择对话框（新增）
// ========================================

function closeClassesSelectDialog() {
    const dialog = document.getElementById('classesSelectDialog');
    if (dialog) {
        dialog.style.display = 'none';
    }
}

// ========================================
// 渲染类别多选框（新增）
// ========================================

function renderTargetClassesCheckbox(classes) {
    const container = document.getElementById('targetClassesContainer');
    if (!container) return;
    
    // 清空容器
    container.innerHTML = '';
    
    if (classes.length === 0) {
        container.innerHTML = '<p class="form-hint">该算法没有类别信息，请上传标签文件</p>';
        return;
    }
    
    // 创建滚动容器（单列布局）
    const scrollContainer = document.createElement('div');
    scrollContainer.className = 'checkbox-scroll-container';
    scrollContainer.style.cssText = `
        max-height: 200px;
        overflow-y: auto;
        border: 1px solid #ddd;
        padding: 10px;
        border-radius: 4px;
        display: flex;
        flex-direction: column;
        gap: 6px;
        background-color: white;
    `;
    
    // 创建多选框
    classes.forEach((className, index) => {
        const checkboxItem = document.createElement('div');
        checkboxItem.className = 'checkbox-item';
        checkboxItem.style.cssText = `
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 8px 12px;
            border-radius: 4px;
            background-color: #f8f9fa;
            border: 1px solid #e0e0e0;
            width: 100%;
        `;
        
        // 创建标签容器（左对齐，不换行）
        const labelContainer = document.createElement('div');
        labelContainer.style.cssText = `
            flex-grow: 1;
            min-width: 0;
            margin-right: 12px;
        `;
        
        // 创建标签（不换行）
        const label = document.createElement('span');
        label.className = 'checkbox-label';
        label.textContent = className;
        label.style.cssText = `
            font-size: 14px;
            user-select: none;
            color: #333;
            display: block;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        `;
        
        // 创建复选框容器（右对齐）
        const checkboxContainer = document.createElement('div');
        checkboxContainer.style.cssText = `
            flex-shrink: 0;
            display: flex;
            align-items: center;
        `;
        
        // 创建复选框（右对齐）
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.className = 'checkbox-input';
        checkbox.id = `class_${index}`;
        checkbox.value = className;
        checkbox.name = 'target_class';
        checkbox.style.cssText = `
            width: 18px;
            height: 18px;
            cursor: pointer;
            margin: 0;
            display: block;
        `;
        
        // 默认选中常用类别
        if (['person', 'car', 'truck', 'bus', 'motorcycle'].includes(className)) {
            checkbox.checked = true;
        }
        
        // 如果有保存的当前任务类别，恢复选中状态
        if (window.currentTaskTargetClasses && window.currentTaskTargetClasses.includes(className)) {
            checkbox.checked = true;
        }
        
        // 添加点击事件（点击标签也触发复选框）
        labelContainer.onclick = () => {
            checkbox.checked = !checkbox.checked;
            updateTargetClassesHidden();
        };
        labelContainer.style.cursor = 'pointer';
        
        // 组装结构：标签容器 + 复选框容器
        labelContainer.appendChild(label);
        checkboxContainer.appendChild(checkbox);
        checkboxItem.appendChild(labelContainer);
        checkboxItem.appendChild(checkboxContainer);
        scrollContainer.appendChild(checkboxItem);
    });
    
    container.appendChild(scrollContainer);
    
    // 添加按钮容器
    const buttonsContainer = document.createElement('div');
    buttonsContainer.className = 'checkbox-buttons';
    buttonsContainer.style.cssText = `
        margin-top: 10px;
        display: flex;
        gap: 8px;
        align-items: center;
    `;
    
    // 全选按钮
    const selectAllBtn = document.createElement('button');
    selectAllBtn.type = 'button';
    selectAllBtn.className = 'btn btn-sm btn-secondary';
    selectAllBtn.textContent = '全选';
    selectAllBtn.onclick = () => {
        scrollContainer.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = true);
        updateTargetClassesHidden();
    };
    
    // 取消全选按钮
    const deselectAllBtn = document.createElement('button');
    deselectAllBtn.type = 'button';
    deselectAllBtn.className = 'btn btn-sm btn-secondary';
    deselectAllBtn.textContent = '取消全选';
    deselectAllBtn.onclick = () => {
        scrollContainer.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
        updateTargetClassesHidden();
    };
    
    // 确定按钮
    const confirmBtn = document.createElement('button');
    confirmBtn.type = 'button';
    confirmBtn.className = 'btn btn-sm btn-primary';
    confirmBtn.textContent = '确定';
    confirmBtn.style.cssText = `
        background-color: #007bff;
        color: white;
        border-color: #007bff;
    `;
    confirmBtn.onclick = () => {
        showSelectedClasses();
    };
    
    buttonsContainer.appendChild(selectAllBtn);
    buttonsContainer.appendChild(deselectAllBtn);
    buttonsContainer.appendChild(confirmBtn);
    container.appendChild(buttonsContainer);
    
    // 添加已选内容显示区域
    const selectedDisplay = document.createElement('div');
    selectedDisplay.id = 'selectedClassesDisplay';
    selectedDisplay.className = 'selected-display';
    selectedDisplay.style.cssText = `
        margin-top: 10px;
        padding: 8px;
        border-radius: 4px;
        background-color: #e7f3ff;
        border: 1px solid #b8daff;
        display: none;
    `;
    container.appendChild(selectedDisplay);
    
    // 绑定checkbox change事件
    scrollContainer.querySelectorAll('input[type="checkbox"]').forEach(cb => {
        cb.addEventListener('change', updateTargetClassesHidden);
    });
    
    // 初始化hidden字段
    updateTargetClassesHidden();
    
    // 清除临时变量
    if (window.currentTaskTargetClasses) {
        delete window.currentTaskTargetClasses;
    }
}

// 显示已选择的类别
function showSelectedClasses() {
    const checkboxes = document.querySelectorAll('input[name="target_class"]:checked');
    const selectedClasses = Array.from(checkboxes).map(cb => cb.value);
    
    const displayDiv = document.getElementById('selectedClassesDisplay');
    if (!displayDiv) return;
    
    if (selectedClasses.length === 0) {
        displayDiv.innerHTML = '<span style="color: #856404;">未选择任何类别</span>';
        displayDiv.style.backgroundColor = '#fff3cd';
        displayDiv.style.borderColor = '#ffc107';
    } else {
        displayDiv.innerHTML = `
            <div style="color: #004085; font-weight: 600;">已选择 ${selectedClasses.length} 个类别：</div>
            <div style="margin-top: 6px; color: #0056b3;">${selectedClasses.join(', ')}</div>
        `;
        displayDiv.style.backgroundColor = '#e7f3ff';
        displayDiv.style.borderColor = '#b8daff';
    }
    
    displayDiv.style.display = 'block';
    displayDiv.style.fontSize = '14px';
    
    // 更新隐藏字段
    updateTargetClassesHidden();
}

// ========================================
// 更新隐藏字段（收集选中的类别）（新增）
// ========================================

function updateTargetClassesHidden() {
    const checkboxes = document.querySelectorAll('#targetClassesContainer input[type="checkbox"]:checked');
    const selectedClasses = Array.from(checkboxes).map(cb => cb.value);
    
    const hiddenInput = document.getElementById('targetClassesHidden');
    if (hiddenInput) {
        hiddenInput.value = JSON.stringify(selectedClasses);
    }
    
    console.log('选中的类别:', selectedClasses);
}

// ========================================
// 清空类别容器（新增）
// ========================================

function clearTargetClassesContainer() {
    const container = document.getElementById('targetClassesContainer');
    if (container) {
        container.innerHTML = '<p class="form-hint">请先选择算法配置</p>';
    }
    
    const hiddenInput = document.getElementById('targetClassesHidden');
    if (hiddenInput) {
        hiddenInput.value = '';
    }
}

// ========================================
// 加载摄像机列表
// ========================================

async function loadAvailableCameras() {
    try {
        const response = await fetch('/api/cameras');
        const data = await response.json();
        // API返回对象格式，提取cameras数组
        availableCameras = data.cameras || [];

    } catch (error) {
        console.error('加载监控设备列表失败:', error);
        availableCameras = [];
    }
}

async function loadAddedChannels() {
    try {
        // 获取当前筛选的通道来源
        const sourceFilter = document.getElementById('filterChannelSource')?.value || '';

        // 构建API URL
        let url = '/api/device-channel-pool';
        if (sourceFilter) {
            url += `?channel_source=${sourceFilter}`;
        }

        const response = await fetch(url);
        addedChannels = await response.json();

    } catch (error) {
        console.error('加载已添加设备通道列表失败:', error);
    }
}

// ========================================
// 加载任务列表
// ========================================

async function loadTasks() {
    try {
        // 获取筛选条件
        const status = document.getElementById('filterStatus').value;
        const algorithm = document.getElementById('filterAlgorithm').value;
        const search = document.getElementById('filterSearch').value;
        
        // 构建查询参数（后端分页）
        const params = new URLSearchParams({
            page: currentPage,
            per_page: perPage
        });
        if (status) params.append('status', status);
        if (search) params.append('search', search);
        
        // 发送请求
        const response = await fetch(`/api/tasks?${params.toString()}`);
        const data = await response.json();
        
        // 后端返回分页数据
        tasks = data.tasks || [];
        totalTasks = data.total || 0;
        totalPages = data.total_pages || 1;
        
        // 渲染任务列表
        renderTasks();
        updateTaskPagination();
        
    } catch (error) {
        console.error('加载任务失败:', error);
        showNotification('加载任务失败', 'error');
    }
}

// ========================================
// 渲染任务列表
// ========================================

function renderTasks() {
    const tasksList = document.getElementById('tasksList');
    
    // 根据视图类型渲染
    if (currentView === 'card') {
        tasksList.style.display = 'grid';
        tasksList.classList.remove('list-view');
        if (tasks.length === 0) {
            tasksList.innerHTML = '';  // 显示空白列表
        } else {
            tasksList.innerHTML = tasks.map(task => renderTaskCard(task)).join('');
        }
    } else {
        tasksList.style.display = 'block';
        tasksList.classList.add('list-view');
        if (tasks.length === 0) {
            tasksList.innerHTML = '';  // 显示空白列表
        } else {
            // 表格视图：表头 + 表格行
            const tableHeader = `
                <div class="task-table-header">
                    <div class="task-table-col col-name">任务名称</div>
                    <div class="task-table-col col-type">检测类型</div>
                    <div class="task-table-col col-target">目标类别</div>
                    <div class="task-table-col col-channel">设备通道</div>
                    <div class="task-table-col col-tracking">跟踪</div>
                    <div class="task-table-col col-motion">运动检测</div>
                    <div class="task-table-col col-status">状态</div>
                    <div class="task-table-col col-actions">操作</div>
                </div>
            `;
            const tableRows = tasks.map(task => renderTaskTableRow(task)).join('');
            tasksList.innerHTML = tableHeader + tableRows;
        }
    }
}

// ========================================
// 渲染任务卡片
// ========================================

function renderTaskCard(task) {
    const statusClass = `status-${task.status}`;
    const statusText = getStatusText(task.status);
    const channelCount = task.channel_count || 0;
    
    // 检测类型文本
    const detectionTypeText = task.detection_type === 'polling' ? '轮询检测' : '实时检测';
    
    // 目标类别
    const targetClasses = task.target_classes || [];
    const targetClassesText = targetClasses.length > 0 ? targetClasses.join(', ') : '无';
    
    // 跟踪和运动检测状态（修复：正确解析数据库中的整数 0/1）
    const trackingEnabled = task.tracking_enabled === true || task.tracking_enabled === 1;
    const motionDetectionEnabled = task.motion_detection_enabled === true || task.motion_detection_enabled === 1;
    
    return `
        <div class="task-card">
            <div class="task-card-header">
                <div>
                    <div class="task-name">${escapeHtml(task.task_name)}</div>
                    <div class="task-camera-count">${channelCount} 个设备通道</div>
                </div>
                <span class="task-status-badge ${statusClass}">${statusText}</span>
            </div>
            <div class="task-card-body">
                <div class="task-info-grid">
                    <div class="task-info-item" title="${escapeHtml(detectionTypeText)}">
                        <span class="task-info-label">检测类型</span>
                        <span class="task-info-value">${escapeHtml(detectionTypeText)}</span>
                    </div>
                    <div class="task-info-item" title="${escapeHtml(targetClassesText)}">
                        <span class="task-info-label">目标类别</span>
                        <span class="task-info-value">${escapeHtml(targetClassesText)}</span>
                    </div>
                    <div class="task-info-item" title="${trackingEnabled ? '已启用' : '未启用'}">
                        <span class="task-info-label">跟踪</span>
                        <span class="task-info-value">
                            ${trackingEnabled ? 
                                '<span class="status-enabled">已启用</span>' : 
                                '<span class="status-disabled">未启用</span>'
                            }
                        </span>
                    </div>
                    <div class="task-info-item" title="${motionDetectionEnabled ? '已启用' : '未启用'}">
                        <span class="task-info-label">运动检测</span>
                        <span class="task-info-value">
                            ${motionDetectionEnabled ? 
                                '<span class="status-enabled">已启用</span>' : 
                                '<span class="status-disabled">未启用</span>'
                            }
                        </span>
                    </div>
                </div>
            </div>
            <div class="task-card-footer">
                <div class="task-enabled">
                    ${task.enabled ? 
                        '<span style="color: var(--success-color); font-weight: 500;">已启用</span>' : 
                        '<span style="color: var(--text-muted);">已禁用</span>'
                    }
                </div>
                <div class="task-actions">
                    ${task.status === 'running' ? 
                        `<button class="task-action-btn icon-only stop" onclick="Tasks.stopTask(${task.id}, event)" title="停止">
                            <svg viewBox="0 0 24 24" fill="currentColor">
                                <path d="M6 6h12v12H6z"/>
                            </svg>
                            <span class="action-text">停止</span>
                        </button>` :
                        `<button class="task-action-btn icon-only start" onclick="Tasks.startTask(${task.id}, event)" title="启动">
                            <svg viewBox="0 0 24 24" fill="currentColor">
                                <path d="M8 5v14l11-7z"/>
                            </svg>
                            <span class="action-text">启动</span>
                        </button>`
                    }
                    <button class="task-action-btn icon-only config" onclick="openConfigModal(${task.id}, event)" title="配置">
                        <svg viewBox="0 0 24 24" fill="currentColor">
                            <path d="M19.14,12.94c0.04-0.3,0.06-0.61,0.06-0.94c0-0.32-0.02-0.64-0.07-0.94l2.03-1.58c0.18-0.14,0.23-0.41,0.12-0.61 l-1.92-3.32c-0.12-0.22-0.37-0.29-0.59-0.22l-2.39,0.96c-0.5-0.38-1.03-0.7-1.62-0.94L14.4,2.81c-0.04-0.24-0.24-0.41-0.48-0.41 h-3.84c-0.24,0-0.43,0.17-0.47,0.41L9.25,5.35C8.66,5.59,8.12,5.92,7.63,6.29L5.24,5.33c-0.22-0.08-0.47,0-0.59,0.22L2.74,8.87 C2.62,9.08,2.66,9.34,2.86,9.48l2.03,1.58C4.84,11.36,4.8,11.69,4.8,12s0.02,0.64,0.07,0.94l-2.03,1.58 c-0.18,0.14-0.23,0.41-0.12,0.61l1.92,3.32c0.12,0.22,0.37,0.29,0.59,0.22l2.39-0.96c0.5,0.38,1.03,0.7,1.62,0.94l0.36,2.54 c0.05,0.24,0.24,0.41,0.48,0.41h3.84c0.24,0,0.44-0.17,0.47-0.41l0.36-2.54c0.59-0.24,1.13-0.56,1.62-0.94l2.39,0.96 c0.22,0.08,0.47,0,0.59-0.22l1.92-3.32c0.11-0.21,0.07-0.47-0.12-0.61L19.14,12.94z M12,15.6c-1.98,0-3.6-1.62-3.6-3.6 s1.62-3.6,3.6-3.6s3.6,1.62,3.6,3.6S13.98,15.6,12,15.6z"/>
                        </svg>
                        <span class="action-text">配置</span>
                    </button>
                    <button class="task-action-btn icon-only edit" onclick="Tasks.openEditModal(${task.id}, event)" title="编辑">
                        <svg viewBox="0 0 24 24" fill="currentColor">
                            <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/>
                        </svg>
                        <span class="action-text">编辑</span>
                    </button>
                    <button class="task-action-btn icon-only delete" onclick="Tasks.openDeleteModal(${task.id}, '${escapeHtml(task.task_name)}', event)" title="删除">
                        <svg viewBox="0 0 24 24" fill="currentColor">
                            <path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/>
                        </svg>
                        <span class="action-text">删除</span>
                    </button>
                </div>
            </div>
        </div>
    `;
}

// ========================================
// 渲染任务表格行
// ========================================

function renderTaskTableRow(task) {
    const statusClass = `status-${task.status}`;
    const statusText = getStatusText(task.status);
    const channelCount = task.channel_count || 0;
    
    // 检测类型文本
    const detectionTypeText = task.detection_type === 'polling' ? '轮询检测' : '实时检测';
    
    // 目标类别
    const targetClasses = task.target_classes || [];
    const targetClassesText = targetClasses.length > 0 ? targetClasses.join(', ') : '无';
    
    // 跟踪和运动检测状态（修复：正确解析数据库中的整数 0/1）
    const trackingEnabled = task.tracking_enabled === true || task.tracking_enabled === 1;
    const motionDetectionEnabled = task.motion_detection_enabled === true || task.motion_detection_enabled === 1;
    
    return `
        <div class="task-table-row">
            <div class="task-table-col col-name">
                <div class="task-name-text">${escapeHtml(task.task_name)}</div>
            </div>
            <div class="task-table-col col-type">${escapeHtml(detectionTypeText)}</div>
            <div class="task-table-col col-target">${escapeHtml(targetClassesText)}</div>
            <div class="task-table-col col-channel">${channelCount}</div>
            <div class="task-table-col col-tracking">
                ${trackingEnabled ? 
                    '<span class="status-enabled">启用</span>' : 
                    '<span class="status-disabled">停用</span>'
                }
            </div>
            <div class="task-table-col col-motion">
                ${motionDetectionEnabled ? 
                    '<span class="status-enabled">启用</span>' : 
                    '<span class="status-disabled">停用</span>'
                }
            </div>
            <div class="task-table-col col-status">
                <span class="task-status-badge ${statusClass}">${statusText}</span>
            </div>
            <div class="task-table-col col-actions">
                <div class="task-table-actions">
                    ${task.status === 'running' ? 
                        `<button class="task-action-btn icon-only stop" onclick="Tasks.stopTask(${task.id}, event)" title="停止">
                            <svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 6h12v12H6z"/></svg>
                            <span class="action-text">停止</span>
                        </button>` :
                        `<button class="task-action-btn icon-only start" onclick="Tasks.startTask(${task.id}, event)" title="启动">
                            <svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
                            <span class="action-text">启动</span>
                        </button>`
                    }
                    <button class="task-action-btn icon-only config" onclick="openConfigModal(${task.id}, event)" title="配置">
                        <svg viewBox="0 0 24 24" fill="currentColor"><path d="M19.14,12.94c0.04-0.3,0.06-0.61,0.06-0.94c0-0.32-0.02-0.64-0.07-0.94l2.03-1.58c0.18-0.14,0.23-0.41,0.12-0.61 l-1.92-3.32c-0.12-0.22-0.37-0.29-0.59-0.22l-2.39,0.96c-0.5-0.38-1.03-0.7-1.62-0.94L14.4,2.81c-0.04-0.24-0.24-0.41-0.48-0.41 h-3.84c-0.24,0-0.43,0.17-0.47,0.41L9.25,5.35C8.66,5.59,8.12,5.92,7.63,6.29L5.24,5.33c-0.22-0.08-0.47,0-0.59,0.22L2.74,8.87 C2.62,9.08,2.66,9.34,2.86,9.48l2.03,1.58C4.84,11.36,4.8,11.69,4.8,12s0.02,0.64,0.07,0.94l-2.03,1.58 c-0.18,0.14-0.23,0.41-0.12,0.61l1.92,3.32c0.12,0.22,0.37,0.29,0.59,0.22l2.39-0.96c0.5,0.38,1.03,0.7,1.62,0.94l0.36,2.54 c0.05,0.24,0.24,0.41,0.48,0.41h3.84c0.24,0,0.44-0.17,0.47-0.41l0.36-2.54c0.59-0.24,1.13-0.56,1.62-0.94l2.39,0.96 c0.22,0.08,0.47,0,0.59-0.22l1.92-3.32c0.11-0.21,0.07-0.47-0.12-0.61L19.14,12.94z M12,15.6c-1.98,0-3.6-1.62-3.6-3.6 s1.62-3.6,3.6-3.6s3.6,1.62,3.6,3.6S13.98,15.6,12,15.6z"/></svg>
                        <span class="action-text">配置</span>
                    </button>
                    <button class="task-action-btn icon-only edit" onclick="Tasks.openEditModal(${task.id}, event)" title="编辑">
                        <svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></svg>
                        <span class="action-text">编辑</span>
                    </button>
                    <button class="task-action-btn icon-only delete" onclick="Tasks.openDeleteModal(${task.id}, '${escapeHtml(task.task_name)}', event)" title="删除">
                        <svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>
                        <span class="action-text">删除</span>
                    </button>
                </div>
            </div>
        </div>
    `;
}

// ========================================
// 任务操作
// ========================================

async function startTask(taskId, event) {
    event.stopPropagation();
    
    try {
        const response = await fetch(`/api/tasks/${taskId}/start`, {
            method: 'POST'
        });
        
        const data = await response.json();
        
        if (response.ok) {
            showNotification('任务已启动', 'success');
            loadTasks();
        } else {
            showNotification(data.error || '启动任务失败', 'error');
        }
    } catch (error) {
        console.error('启动任务失败:', error);
        showNotification('启动任务失败', 'error');
    }
}

async function stopTask(taskId, event) {
    event.stopPropagation();
    
    try {
        const response = await fetch(`/api/tasks/${taskId}/stop`, {
            method: 'POST'
        });
        
        const data = await response.json();
        
        if (response.ok) {
            showNotification('任务已停止', 'success');
            loadTasks();
        } else {
            showNotification(data.error || '停止任务失败', 'error');
        }
    } catch (error) {
        console.error('停止任务失败:', error);
        showNotification('停止任务失败', 'error');
    }
}

// ========================================
// 任务详情
// ========================================

async function showTaskDetail(taskId) {
    currentTaskId = taskId;
    
    try {
        const response = await fetch(`/api/tasks/${taskId}`);
        const task = await response.json();
        
        document.getElementById('taskDetailTitle').textContent = task.task_name;
        
        const cameraListHtml = task.cameras && task.cameras.length > 0 ?
            task.cameras.map(c => `
                <div class="detail-camera-item">
                    <span class="camera-name">${escapeHtml(c.camera_name || c.camera_id)}</span>
                    <span class="camera-url">${escapeHtml(c.rtsp_url)}</span>
                    <span class="camera-status ${c.status === 'online' ? 'online' : 'offline'}">${c.status}</span>
                </div>
            `).join('') :
            '<p class="no-cameras">未绑定摄像机</p>';
        
        document.getElementById('taskDetailContent').innerHTML = `
            <div class="task-detail-section">
                <h4>基本信息</h4>
                <div class="detail-grid">
                    <div class="detail-item">
                        <span class="detail-label">任务ID</span>
                        <span class="detail-value">${task.id}</span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">状态</span>
                        <span class="detail-value"><span class="task-status-badge status-${task.status}">${getStatusText(task.status)}</span></span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">启用</span>
                        <span class="detail-value">${task.enabled ? '是' : '否'}</span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">创建时间</span>
                        <span class="detail-value">${task.created_at || '-'}</span>
                    </div>
                </div>
            </div>
            
            <div class="task-detail-section">
                <h4>检测配置</h4>
                <div class="detail-grid">
                    <div class="detail-item">
                        <span class="detail-label">算法</span>
                        <span class="detail-value">${escapeHtml(task.algorithm)}</span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">模型路径</span>
                        <span class="detail-value">${escapeHtml(task.model_path)}</span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">置信度阈值</span>
                        <span class="detail-value">${task.conf_threshold}</span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">NMS阈值</span>
                        <span class="detail-value">${task.nms_threshold}</span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">最大检测数</span>
                        <span class="detail-value">${task.max_det}</span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">FPS限制</span>
                        <span class="detail-value">${task.fps_limit || '不限制'}</span>
                    </div>
                    <div class="detail-item full-width">
                        <span class="detail-label">目标类别</span>
                        <span class="detail-value">${escapeHtml(task.target_classes.join(', '))}</span>
                    </div>
                </div>
            </div>
            
            <div class="task-detail-section">
                <h4>绑定摄像机 (${task.cameras ? task.cameras.length : 0})</h4>
                <div class="detail-cameras-list">
                    ${cameraListHtml}
                </div>
            </div>
        `;
        
        document.getElementById('taskDetailModal').classList.add('show');
        
    } catch (error) {
        console.error('加载任务详情失败:', error);
        showNotification('加载任务详情失败', 'error');
    }
}

function closeTaskDetailModal() {
    document.getElementById('taskDetailModal').classList.remove('show');
    currentTaskId = null;
}

function editCurrentTask() {
    if (currentTaskId) {
        closeTaskDetailModal();
        openEditModal(currentTaskId);
    }
}

// ========================================
// 创建/编辑任务模态框
// ========================================

function openCreateModal() {
    document.getElementById('modalTitle').textContent = '创建任务';
    document.getElementById('taskId').value = '';
    document.getElementById('taskForm').reset();

    // 初始化临时数组
    selectedCameras = [];
    tempSelectedChannels = [];
    selectedChannels = [];
    isEditMode = false;
    currentTaskId = null;  // ✅ 创建任务时重置当前任务ID

    // 设置默认值
    document.getElementById('detectionType').value = 'realtime';  // 检测类型默认实时检测
    document.getElementById('motionDetectionEnabled').value = 'true';  // 运动检测默认启用
    document.getElementById('trackingEnabled').value = 'true';  // 跟踪功能默认启用
    document.getElementById('taskEnabled').value = 'true';

    // 重置算法配置选择
    const algorithmConfigSelect = document.getElementById('algorithmConfig');
    if (algorithmConfigSelect) {
        algorithmConfigSelect.value = '';
    }
    document.getElementById('algorithm').value = '';
    document.getElementById('modelPath').value = '';

    // 显式设置颜色选择器默认值（form.reset() 对 input[type=color] 可能恢复为 #000000）
    const colorDefaults = {
        'displayBoxColor': '#00ff00',
        'displayBoxLabelColor': '#00ff00',
        'displayTrackColor': '#0000ff',
        'displayTrackLabelColor': '#0000ff',
        'displayTrajectoryColor': '#ffff00',
        'displayTrajectoryPointColor': '#ff0000',
        'displayMotionRegionColor': '#00ffff',
        'displayMotionRegionFillColor': '#00ffff'
    };
    Object.entries(colorDefaults).forEach(([id, hex]) => {
        const el = document.getElementById(id);
        if (el) {
            el.value = hex;
            const valueDisplay = document.getElementById(id + 'Value');
            if (valueDisplay) {
                valueDisplay.textContent = parseColorInput(hex, [0, 0, 0]).join(',');
            }
        }
    });
    
    // 确保算法库列表已加载
    if (algorithms.length === 0) {
        loadAlgorithms();
    }

    renderChannelPool();
    document.getElementById('taskModal').classList.add('show');
}

async function openEditModal(taskId, event) {
    if (event) event.stopPropagation();

    // ✅ 设置当前任务ID（用于通道Zone配置）
    currentTaskId = taskId;

    try {
        const response = await fetch(`/api/tasks/${taskId}`);
        const task = await response.json();

        document.getElementById('modalTitle').textContent = '编辑任务';
        document.getElementById('taskId').value = task.id;

        // 初始化临时数组（编辑模式）
        selectedCameras = [];
        tempSelectedChannels = [];
        isEditMode = true;

        // 填充表单
        document.getElementById('taskName').value = task.task_name;
        document.getElementById('taskEnabled').value = task.enabled ? 'true' : 'false';

        // 填充检测类型
        document.getElementById('detectionType').value = task.detection_type || 'realtime';

        // 填充功能开关
        document.getElementById('motionDetectionEnabled').value = task.motion_detection_enabled ? 'true' : 'false';
        document.getElementById('trackingEnabled').value = task.tracking_enabled ? 'true' : 'false';

        // 查找并选中对应的算法配置
        const algorithmConfigSelect = document.getElementById('algorithmConfig');
        const algorithm = document.getElementById('algorithm');
        const modelPath = document.getElementById('modelPath');

        if (algorithm) {
            algorithm.value = task.algorithm;
        }

        if (modelPath) {
            modelPath.value = task.model_path;
        }

        // 根据model_path查找对应的算法配置
        if (algorithmConfigSelect && task.model_path) {
            for (let i = 1; i < algorithmConfigSelect.options.length; i++) {
                const option = algorithmConfigSelect.options[i];
                if (option.dataset.modelPath === task.model_path ||
                    option.dataset.name === task.algorithm) {
                    algorithmConfigSelect.value = option.value;
                    break;
                }
            }
        }

        // target_classes改为多选框，需要在加载算法类别后设置选中状态
        // 暂时保存到临时变量，等算法类别加载完成后再设置
        window.currentTaskTargetClasses = task.target_classes;

        // 回填已选目标类别到输入框（保存提交用 + 显示用）
        const targetClassesArr = Array.isArray(task.target_classes) ? task.target_classes : [];
        const targetClassesInput = document.getElementById('targetClassesInput');
        const targetClassesHidden = document.getElementById('targetClassesHidden');
        if (targetClassesInput) {
            targetClassesInput.value = targetClassesArr.join(', ');
        }
        if (targetClassesHidden) {
            targetClassesHidden.value = targetClassesArr.join(',');
        }

        // 设置已选通道（从device_channel_pool加载）
        selectedChannels = task.channels ? task.channels.map(ch => ch.id) : [];
        renderChannelPool();

        // 加载检测器配置
        const detectorConfig = task.detector_config || {};
        const detectorBackend = document.getElementById('detectorBackend');
        const detectorUseFp16 = document.getElementById('detectorUseFp16');
        const detectorTensorrtLibPath = document.getElementById('detectorTensorrtLibPath');
        const detectorTensorrtEnginePath = document.getElementById('detectorTensorrtEnginePath');
        const detectorTensorrtCachePath = document.getElementById('detectorTensorrtCachePath');
        const detectorConfThreshold = document.getElementById('detectorConfThreshold');
        const detectorIouThreshold = document.getElementById('detectorIouThreshold');
        const detectorMinBoxArea = document.getElementById('detectorMinBoxArea');
        const detectorDeviceId = document.getElementById('detectorDeviceId');
        const detectorGpuMemLimit = document.getElementById('detectorGpuMemLimit');
        const detectorRknnTarget = document.getElementById('detectorRknnTarget');
        const detectorRknnCoreMask = document.getElementById('detectorRknnCoreMask');
        const detectorRknnDeviceId = document.getElementById('detectorRknnDeviceId');

        if (detectorBackend) detectorBackend.value = detectorConfig.backend || 'onnx_cuda';
        if (detectorUseFp16) detectorUseFp16.value = detectorConfig.use_fp16 ? 'true' : 'false';
        if (detectorTensorrtLibPath) detectorTensorrtLibPath.value = detectorConfig.tensorrt_lib_path || '';
        if (detectorTensorrtEnginePath) detectorTensorrtEnginePath.value = detectorConfig.tensorrt_engine_path || '';
        if (detectorTensorrtCachePath) detectorTensorrtCachePath.value = detectorConfig.tensorrt_engine_cache_path || './trt_cache';
        if (detectorConfThreshold) detectorConfThreshold.value = detectorConfig.conf_threshold ?? 0.25;
        if (detectorIouThreshold) detectorIouThreshold.value = detectorConfig.iou_threshold ?? 0.35;
        if (detectorMinBoxArea) detectorMinBoxArea.value = detectorConfig.min_box_area ?? 50;
        if (detectorDeviceId) detectorDeviceId.value = detectorConfig.device_id ?? 0;
        if (detectorGpuMemLimit) detectorGpuMemLimit.value = detectorConfig.gpu_mem_limit ? Math.round(detectorConfig.gpu_mem_limit / 1024 / 1024) : 6144;
        if (detectorRknnTarget) detectorRknnTarget.value = detectorConfig.rknn_target || 'rk3588';
        if (detectorRknnCoreMask) detectorRknnCoreMask.value = detectorConfig.rknn_core_mask ?? 0;
        if (detectorRknnDeviceId) detectorRknnDeviceId.value = detectorConfig.rknn_device_id || '';

        // ✅ 加载解码器配置
        const decoderConfig = task.decoder_config || {};
        const decoderWidth = document.getElementById('decoderWidth');
        const decoderHeight = document.getElementById('decoderHeight');
        const decoderFps = document.getElementById('decoderFps');
        const decoderHwaccelPreset = document.getElementById('decoderHwaccelPreset');
        const decoderInputPreset = document.getElementById('decoderInputPreset');
        const decoderRtspTransport = document.getElementById('decoderRtspTransport');
        const decoderReconnect = document.getElementById('decoderReconnect');
        const decoderReconnectDelay = document.getElementById('decoderReconnectDelay');
        const decoderBufferSize = document.getElementById('decoderBufferSize');
        const decoderRingBufferBlockCount = document.getElementById('decoderRingBufferBlockCount');
        const decoderIndexQueueSize = document.getElementById('decoderIndexQueueSize');
        const decoderFfmpegFilters = document.getElementById('decoderFfmpegFilters');

        if (decoderWidth) decoderWidth.value = decoderConfig.width ?? 640;
        if (decoderHeight) decoderHeight.value = decoderConfig.height ?? 640;
        if (decoderFps) decoderFps.value = decoderConfig.fps ?? 30;
        if (decoderHwaccelPreset) decoderHwaccelPreset.value = decoderConfig.hwaccel_preset || 'preset-dxva2';
        if (decoderInputPreset) decoderInputPreset.value = decoderConfig.input_preset || 'preset-rtsp-generic';
        if (decoderRtspTransport) decoderRtspTransport.value = decoderConfig.rtsp_transport || 'tcp';
        if (decoderReconnect) decoderReconnect.value = decoderConfig.reconnect !== false ? 'true' : 'false';
        if (decoderReconnectDelay) decoderReconnectDelay.value = decoderConfig.reconnect_delay ?? 5;
        if (decoderBufferSize) decoderBufferSize.value = decoderConfig.buffer_size ?? 2097152;
        if (decoderRingBufferBlockCount) decoderRingBufferBlockCount.value = decoderConfig.ring_buffer_block_count ?? 40;
        if (decoderIndexQueueSize) decoderIndexQueueSize.value = decoderConfig.index_queue_size ?? 50;
        if (decoderFfmpegFilters) decoderFfmpegFilters.value = decoderConfig.ffmpeg_filters || '';

        // ✅ 加载事件配置
        const eventConfig = task.event_config || {};
        const eventEnabled = document.getElementById('eventEnabled');
        const eventLocalEnabled = document.getElementById('eventLocalEnabled');
        const eventMinioEnabled = document.getElementById('eventMinioEnabled');
        const eventFtpEnabled = document.getElementById('eventFtpEnabled');
        const eventGifEnabled = document.getElementById('eventGifEnabled');
        const eventGifLocalEnabled = document.getElementById('eventGifLocalEnabled');
        const eventGifMinioEnabled = document.getElementById('eventGifMinioEnabled');
        const eventGifFtpEnabled = document.getElementById('eventGifFtpEnabled');
        const eventImageDrawBbox = document.getElementById('eventImageDrawBbox');
        const eventGifDrawBbox = document.getElementById('eventGifDrawBbox');
        const eventMinConfidence = document.getElementById('eventMinConfidence');
        const eventImageOutputDir = document.getElementById('eventImageOutputDir');
        const eventGifOutputDir = document.getElementById('eventGifOutputDir');
        const eventGifCropMode = document.getElementById('eventGifCropMode');
        const eventCropMarginRatio = document.getElementById('eventCropMarginRatio');
        const eventGifPreSeconds = document.getElementById('eventGifPreSeconds');
        const eventGifPostSeconds = document.getElementById('eventGifPostSeconds');

        const imageOutputCfg = eventConfig.image_output || {};
        const gifCfg = eventConfig.gif || {};
        const filterCfg = eventConfig.filter || {};

        if (eventEnabled) eventEnabled.value = eventConfig.enabled !== false ? 'true' : 'false';
        if (eventLocalEnabled) eventLocalEnabled.value = imageOutputCfg.local_enabled !== false ? 'true' : 'false';
        if (eventMinioEnabled) eventMinioEnabled.value = imageOutputCfg.minio_enabled === true ? 'true' : 'false';
        if (eventFtpEnabled) eventFtpEnabled.value = imageOutputCfg.ftp_enabled === true ? 'true' : 'false';
        if (eventGifEnabled) eventGifEnabled.value = gifCfg.enabled === true ? 'true' : 'false';
        if (eventGifLocalEnabled) eventGifLocalEnabled.value = gifCfg.local_enabled !== false ? 'true' : 'false';
        if (eventGifMinioEnabled) eventGifMinioEnabled.value = gifCfg.minio_enabled === true ? 'true' : 'false';
        if (eventGifFtpEnabled) eventGifFtpEnabled.value = gifCfg.ftp_enabled === true ? 'true' : 'false';
        if (eventImageDrawBbox) eventImageDrawBbox.value = imageOutputCfg.draw_bbox !== false ? 'true' : 'false';
        if (eventGifDrawBbox) eventGifDrawBbox.value = gifCfg.draw_bbox !== false ? 'true' : 'false';
        if (eventMinConfidence) eventMinConfidence.value = filterCfg.min_confidence ?? 0.5;
        if (eventImageOutputDir) eventImageOutputDir.value = imageOutputCfg.output_dir || 'output/images';
        if (eventGifOutputDir) eventGifOutputDir.value = gifCfg.output_dir || 'output/gifs';
        if (eventGifCropMode) eventGifCropMode.value = gifCfg.crop_mode || 'full';
        if (eventCropMarginRatio) eventCropMarginRatio.value = gifCfg.crop_margin_ratio ?? 1.0;
        if (eventGifPreSeconds) eventGifPreSeconds.value = gifCfg.pre_seconds ?? 5.0;
        if (eventGifPostSeconds) eventGifPostSeconds.value = gifCfg.post_seconds ?? 5.0;

        // ✅ 加载跟踪器配置
        const trackerConfig = task.tracker_config || {};
        const trackerNInit = document.getElementById('trackerNInit');
        const trackerMaxAge = document.getElementById('trackerMaxAge');
        if (trackerNInit) trackerNInit.value = trackerConfig.n_init ?? 3;
        if (trackerMaxAge) trackerMaxAge.value = trackerConfig.max_age ?? 30;

        // ✅ 加载运动检测配置
        const motionConfig = task.motion_config || {};
        const motionHeight = document.getElementById('motionHeight');
        const motionThreshold = document.getElementById('motionThreshold');
        const motionContourArea = document.getElementById('motionContourArea');
        const motionBlurRadius = document.getElementById('motionBlurRadius');
        const motionFrameAlpha = document.getElementById('motionFrameAlpha');
        const motionLightningThreshold = document.getElementById('motionLightningThreshold');
        const motionImproveContrast = document.getElementById('motionImproveContrast');
        const motionContrastFrameHistory = document.getElementById('motionContrastFrameHistory');

        if (motionHeight) motionHeight.value = motionConfig.motion_height ?? 180;
        if (motionThreshold) motionThreshold.value = motionConfig.threshold ?? 25;
        if (motionContourArea) motionContourArea.value = motionConfig.contour_area ?? 200;
        if (motionBlurRadius) motionBlurRadius.value = motionConfig.blur_radius ?? 1;
        if (motionFrameAlpha) motionFrameAlpha.value = motionConfig.frame_alpha ?? 0.01;
        if (motionLightningThreshold) motionLightningThreshold.value = motionConfig.lightning_threshold ?? 0.8;
        if (motionImproveContrast) motionImproveContrast.value = motionConfig.improve_contrast === true ? 'true' : 'false';
        if (motionContrastFrameHistory) motionContrastFrameHistory.value = motionConfig.contrast_frame_history ?? 50;

        // ✅ 加载显示配置
        const displayConfig = task.display_config || {};
        const displayCfg = displayConfig.display || {};
        const detectionBoxCfg = displayConfig.detection_box || {};
        const trackBoxCfg = displayConfig.track_box || {};
        const trajectoryCfg = displayConfig.trajectory || {};

        const displayTargetFps = document.getElementById('displayTargetFps');
        const displayAIStreamInterval = document.getElementById('displayAIStreamInterval');
        const displayShowBoxes = document.getElementById('displayShowBoxes');
        const displayShowTracks = document.getElementById('displayShowTracks');
        const displayShowTrajectories = document.getElementById('displayShowTrajectories');
        const displayShowMotionRegions = document.getElementById('displayShowMotionRegions');
        const displayShowZones = document.getElementById('displayShowZones');
        const displayTrajectoryHistory = document.getElementById('displayTrajectoryHistory');
        const displayBoxColor = document.getElementById('displayBoxColor');
        const displayBoxThickness = document.getElementById('displayBoxThickness');
        const displayBoxFontScale = document.getElementById('displayBoxFontScale');
        const displayBoxLabelColor = document.getElementById('displayBoxLabelColor');
        const displayTrackColor = document.getElementById('displayTrackColor');
        const displayTrackThickness = document.getElementById('displayTrackThickness');
        const displayTrackFontScale = document.getElementById('displayTrackFontScale');
        const displayTrackLabelColor = document.getElementById('displayTrackLabelColor');
        const displayTrajectoryColor = document.getElementById('displayTrajectoryColor');
        const displayTrajectoryThickness = document.getElementById('displayTrajectoryThickness');
        const displayTrajectoryPointSize = document.getElementById('displayTrajectoryPointSize');
        const displayTrajectoryPointColor = document.getElementById('displayTrajectoryPointColor');

        if (displayTargetFps) displayTargetFps.value = displayCfg.target_fps ?? 25;
        if (displayAIStreamInterval) displayAIStreamInterval.value = displayCfg.ai_stream_interval ?? 3;
        if (displayShowBoxes) displayShowBoxes.value = displayCfg.show_detection_boxes !== false ? 'true' : 'false';
        if (displayShowTracks) displayShowTracks.value = displayCfg.show_track_boxes !== false ? 'true' : 'false';
        if (displayShowTrajectories) displayShowTrajectories.value = displayCfg.show_trajectories !== false ? 'true' : 'false';
        if (displayShowMotionRegions) displayShowMotionRegions.value = displayCfg.show_motion_regions !== false ? 'true' : 'false';
        if (displayShowZones) displayShowZones.value = displayCfg.show_zones !== false ? 'true' : 'false';
        if (displayTrajectoryHistory) displayTrajectoryHistory.value = displayCfg.trajectory_history_length ?? 30;
        if (displayBoxColor) {
            displayBoxColor.value = colorToText(detectionBoxCfg.color, '#00ff00');
            const v = document.getElementById('displayBoxColorValue');
            if (v) v.textContent = colorToRgbText(detectionBoxCfg.color, '0,255,0');
        }
        if (displayBoxThickness) displayBoxThickness.value = detectionBoxCfg.thickness ?? 2;
        if (displayBoxFontScale) displayBoxFontScale.value = detectionBoxCfg.font_scale ?? 0.5;
        if (displayBoxLabelColor) {
            displayBoxLabelColor.value = colorToText(detectionBoxCfg.label_color, '#00ff00');
            const v = document.getElementById('displayBoxLabelColorValue');
            if (v) v.textContent = colorToRgbText(detectionBoxCfg.label_color, '0,255,0');
        }
        if (displayTrackColor) {
            displayTrackColor.value = colorToText(trackBoxCfg.color, '#0000ff');
            const v = document.getElementById('displayTrackColorValue');
            if (v) v.textContent = colorToRgbText(trackBoxCfg.color, '0,0,255');
        }
        if (displayTrackThickness) displayTrackThickness.value = trackBoxCfg.thickness ?? 2;
        if (displayTrackFontScale) displayTrackFontScale.value = trackBoxCfg.font_scale ?? 0.5;
        if (displayTrackLabelColor) {
            displayTrackLabelColor.value = colorToText(trackBoxCfg.label_color, '#0000ff');
            const v = document.getElementById('displayTrackLabelColorValue');
            if (v) v.textContent = colorToRgbText(trackBoxCfg.label_color, '0,0,255');
        }
        if (displayTrajectoryColor) {
            displayTrajectoryColor.value = colorToText(trajectoryCfg.color, '#ffff00');
            const v = document.getElementById('displayTrajectoryColorValue');
            if (v) v.textContent = colorToRgbText(trajectoryCfg.color, '255,255,0');
        }
        if (displayTrajectoryThickness) displayTrajectoryThickness.value = trajectoryCfg.thickness ?? 1;
        if (displayTrajectoryPointSize) displayTrajectoryPointSize.value = trajectoryCfg.point_size ?? 2;
        if (displayTrajectoryPointColor) {
            displayTrajectoryPointColor.value = colorToText(trajectoryCfg.point_color, '#ff0000');
            const v = document.getElementById('displayTrajectoryPointColorValue');
            if (v) v.textContent = colorToRgbText(trajectoryCfg.point_color, '255,0,0');
        }

        // 加载运动区域绘制配置（motion_region）
        const motionRegionCfg = displayConfig.motion_region || {};
        const displayMotionRegionColor = document.getElementById('displayMotionRegionColor');
        const displayMotionRegionThickness = document.getElementById('displayMotionRegionThickness');
        const displayMotionRegionFillColor = document.getElementById('displayMotionRegionFillColor');
        const displayMotionRegionFillAlpha = document.getElementById('displayMotionRegionFillAlpha');
        if (displayMotionRegionColor) {
            displayMotionRegionColor.value = colorToText(motionRegionCfg.color, '#00ffff');
            const v = document.getElementById('displayMotionRegionColorValue');
            if (v) v.textContent = colorToRgbText(motionRegionCfg.color, '0,255,255');
        }
        if (displayMotionRegionThickness) displayMotionRegionThickness.value = motionRegionCfg.thickness ?? 1;
        if (displayMotionRegionFillColor) {
            displayMotionRegionFillColor.value = colorToText(motionRegionCfg.fill_color, '#00ffff');
            const v = document.getElementById('displayMotionRegionFillColorValue');
            if (v) v.textContent = colorToRgbText(motionRegionCfg.fill_color, '0,255,255');
        }
        if (displayMotionRegionFillAlpha) displayMotionRegionFillAlpha.value = motionRegionCfg.fill_alpha ?? 50;

        // ✅ 加载屏幕闪烁报警配置（alarm）
        const alarmCfg = displayConfig.alarm || {};
        const alarmZoneEnterEnabled = document.getElementById('alarmZoneEnterEnabled');
        const alarmLoiteringEnabled = document.getElementById('alarmLoiteringEnabled');
        const alarmDuration = document.getElementById('alarmDuration');
        const alarmBlinkInterval = document.getElementById('alarmBlinkInterval');
        const alarmShowBanner = document.getElementById('alarmShowBanner');
        const alarmThrottleInterval = document.getElementById('alarmThrottleInterval');
        if (alarmZoneEnterEnabled) alarmZoneEnterEnabled.value = alarmCfg.zone_enter_enabled !== false ? 'true' : 'false';
        if (alarmLoiteringEnabled) alarmLoiteringEnabled.value = alarmCfg.loitering_enabled !== false ? 'true' : 'false';
        if (alarmDuration) alarmDuration.value = alarmCfg.duration ?? 3;
        if (alarmBlinkInterval) alarmBlinkInterval.value = alarmCfg.blink_interval ?? 500;
        if (alarmShowBanner) alarmShowBanner.value = alarmCfg.show_banner !== false ? 'true' : 'false';
        if (alarmThrottleInterval) alarmThrottleInterval.value = alarmCfg.throttle_interval ?? 3000;

        document.getElementById('taskModal').classList.add('show');

    } catch (error) {
        console.error('加载任务详情失败:', error);
        showNotification('加载任务详情失败', 'error');
    }
}

function closeModal() {
    document.getElementById('taskModal').classList.remove('show');
    selectedCameras = [];
    tempSelectedChannels = [];
    selectedChannels = [];
    isEditMode = false;
    currentTaskId = null;  // ✅ 关闭模态框时重置当前任务ID
}

// ========================================
// 摄像机选择
// ========================================

function openChannelSelectModal() {
    selectedCameras = [];  // 重置选中的摄像机
    renderChannelSelectList();
    document.getElementById('cameraSelectModal').classList.add('show');
}

function closeChannelSelectModal() {
    document.getElementById('cameraSelectModal').classList.remove('show');
}

// ========================================
// 手动添加摄像机
// ========================================

function openManualAddForm() {
    document.getElementById('manualAddForm').style.display = 'block';
    document.getElementById('onvifDiscoveryResults').style.display = 'none';

    // 清空表单
    document.getElementById('newCameraName').value = '';
    document.getElementById('newCameraSource').value = '';
    document.getElementById('newCameraIp').value = '';
    document.getElementById('newCameraPort').value = '554';
    document.getElementById('newCameraUsername').value = '';
    document.getElementById('newCameraPassword').value = '';
    document.getElementById('newCameraDescription').value = '';
}

function closeManualAddForm() {
    document.getElementById('manualAddForm').style.display = 'none';
}

async function submitManualAddCamera() {
    // 获取表单数据
    const name = document.getElementById('newCameraName').value.trim();
    const source = document.getElementById('newCameraSource').value.trim();

    // 验证必填字段
    if (!name) {
        alert('请输入摄像机名称');
        return;
    }

    if (!source) {
        alert('请输入取流地址');
        return;
    }

    const cameraData = {
        name: name,
        source: source,
        ip: document.getElementById('newCameraIp').value.trim(),
        port: parseInt(document.getElementById('newCameraPort').value) || 554,
        username: document.getElementById('newCameraUsername').value.trim(),
        password: document.getElementById('newCameraPassword').value,
        description: document.getElementById('newCameraDescription').value.trim(),
        enabled: true
    };

    try {
        // 调用后端API添加摄像机
        const response = await fetch('/api/cameras', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(cameraData)
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || '添加失败');
        }

        const result = await response.json();

        // 添加成功
        alert('摄像机添加成功！');

        // 刷新摄像机列表
        await loadCameras();

        // 自动选中新添加的通道
        if (result.channel_id) {
            selectedChannels.push(result.channel_id);
            renderChannelPool();
            renderChannelSelectList();
        }

        // 关闭表单
        closeManualAddForm();

    } catch (error) {
        alert('添加摄像机失败: ' + error.message);
    }
}

// ========================================
// ONVIF发现摄像机
// ========================================

function discoverOnvifCameras() {
    document.getElementById('onvifDiscoveryResults').style.display = 'block';
    document.getElementById('manualAddForm').style.display = 'none';
    document.getElementById('discoveryStatus').textContent = '正在发现ONVIF摄像机...';
    document.getElementById('discoveryList').innerHTML = '';

    // TODO: 调用后端ONVIF发现API
    // 目前显示提示信息
    document.getElementById('discoveryStatus').textContent = 'ONVIF发现功能正在开发中，请使用手动添加';
    document.getElementById('discoveryList').innerHTML = `
        <div class="discovery-tip">
            <p>ONVIF自动发现功能即将上线</p>
            <p>请暂时使用"手动添加"方式添加摄像机</p>
        </div>
    `;
}

function closeOnvifDiscoveryResults() {
    document.getElementById('onvifDiscoveryResults').style.display = 'none';
}

// ========================================
// 渲染摄像机选择列表
// ========================================

function renderChannelSelectList() {
    const listContainer = document.getElementById('cameraSelectList');

    if (availableCameras.length === 0) {
        listContainer.innerHTML = '<p class="empty-cameras-tip">暂无可用监控设备</p>';
        return;
    }

    listContainer.innerHTML = availableCameras.map(camera => `
        <div class="camera-select-item ${selectedCameras.includes(camera.id) ? 'selected' : ''}" onclick="Tasks.toggleCameraSelection('${camera.id}')">
            <div class="camera-select-checkbox">
                <input type="checkbox" ${selectedCameras.includes(camera.id) ? 'checked' : ''} onclick="event.stopPropagation(); Tasks.toggleCameraSelection('${camera.id}')">
            </div>
            <div class="camera-select-info">
                <div class="camera-select-name">${escapeHtml(camera.name || '未命名设备')}</div>
                <div class="camera-select-url">${escapeHtml(camera.rtsp_stream || camera.source || '')}</div>
            </div>
            <div class="camera-select-status">
                <span class="status-badge ${camera.enabled ? 'online' : 'offline'}">${camera.enabled ? '启用' : '禁用'}</span>
            </div>
        </div>
    `).join('');
}

function toggleCameraSelection(cameraId) {
    const index = selectedCameras.indexOf(cameraId);
    if (index === -1) {
        selectedCameras.push(cameraId);
    } else {
        selectedCameras.splice(index, 1);
    }
    renderChannelSelectList();
}

async function confirmChannelSelection() {
    // 将选中的cameras添加到临时数组，不立即入库
    if (selectedCameras.length === 0) {
        closeChannelSelectModal();
        return;
    }

    // 获取选中的摄像机详细信息
    const selectedCameraDetails = availableCameras.filter(cam => selectedCameras.includes(cam.id));

    // 将摄像机信息转换为临时通道数据
    for (const camera of selectedCameraDetails) {
        const tempChannelData = {
            temp_id: `temp_${camera.id}`,  // 临时ID（用于前端显示）
            camera_id: camera.id,  // 保存原始摄像机ID
            channel_name: camera.name,
            rtsp_url: camera.rtsp_stream || camera.source,
            channel_source: 'local_platform',  // 本地平台
            enabled: true
        };

        // 添加到临时数组（避免重复添加）
        if (!tempSelectedChannels.find(ch => ch.camera_id === camera.id)) {
            tempSelectedChannels.push(tempChannelData);
        }
    }

    // 刷新显示
    renderChannelPool();
    closeChannelSelectModal();
}

// ========================================
// 渲染设备通道检测池（右侧列）
// ========================================

function renderChannelPool() {
    const container = document.getElementById('taskChannelPool');

    // 合并临时选中的通道和已入库的通道
    let displayChannels = [];

    if (isEditMode) {
        // 编辑模式：显示已入库的通道 + 新添加的临时通道
        const existingChannels = addedChannels.filter(ch => selectedChannels.includes(ch.id));
        displayChannels = [...existingChannels, ...tempSelectedChannels];
    } else {
        // 新建模式：只显示临时选中的通道
        displayChannels = tempSelectedChannels;
    }

    if (displayChannels.length === 0) {
        container.innerHTML = '<p class="empty-cameras-tip">点击上方"添加通道"按钮选择设备</p>';
        updatePoolChannelCount();
        return;
    }

    container.innerHTML = displayChannels.map(channel => `
        <div class="camera-pool-item" data-channel-id="${channel.id || channel.temp_id}" data-is-temp="${channel.temp_id ? 'true' : 'false'}">
            <div class="camera-pool-info">
                <div class="camera-pool-name">${escapeHtml(channel.channel_name || '未命名通道')}
                    <span class="source-badge" style="margin-left: 8px; font-size: 11px; padding: 2px 6px; border-radius: 3px; background: ${channel.channel_source === 'local_platform' ? '#e3f2fd' : '#fff3e0'}; color: ${channel.channel_source === 'local_platform' ? '#1976d2' : '#f57c00'};">${channel.channel_source === 'local_platform' ? '本地' : '边缘'}</span>
                    ${channel.temp_id ? '<span style="margin-left: 5px; font-size: 10px; color: #666;">(待入库)</span>' : ''}
                </div>
                <div class="camera-pool-url">${escapeHtml(channel.rtsp_url || '')}</div>
            </div>
            <div class="camera-pool-actions" style="display: flex; gap: 8px; align-items: center;">
                ${!channel.temp_id ? `<button type="button" class="btn btn-sm btn-secondary" onclick="openChannelZoneConfig(${channel.id}, '${escapeHtml(channel.channel_name)}', event)" title="配置Zone区域">
                    <svg viewBox="0 0 24 24" fill="currentColor" style="width:14px;height:14px;">
                        <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/>
                    </svg>
                </button>` : ''}
                <button type="button" onclick="Tasks.removeChannelFromPool('${channel.id || channel.temp_id}')" title="移除通道">
                    <svg viewBox="0 0 24 24" fill="currentColor">
                        <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/>
                    </svg>
                </button>
            </div>
        </div>
    `).join('');

    updatePoolChannelCount();
}

// ========================================
// 从设备池移除摄像机
// ========================================

function removeChannelFromPool(channelId) {
    // 判断是临时通道还是已入库通道
    if (channelId.startsWith('temp_')) {
        // 删除临时通道
        const index = tempSelectedChannels.findIndex(ch => ch.temp_id === channelId);
        if (index !== -1) {
            tempSelectedChannels.splice(index, 1);
        }
    } else {
        // 删除已入库通道
        const index = selectedChannels.indexOf(parseInt(channelId));
        if (index !== -1) {
            selectedChannels.splice(index, 1);
        }
    }
    renderChannelPool();
}

// ========================================
// 更新设备池通道数量
// ========================================

function updatePoolChannelCount() {
    const countElement = document.getElementById('poolChannelCount');
    if (countElement) {
        countElement.textContent = `已选择 ${selectedChannels.length} 个通道`;
    }
}

// ========================================
// 按通道来源筛选
// ========================================

async function filterChannelsBySource() {
    await loadAddedChannels();
    renderChannelPool();
}

// ========================================
// 搜索通道列表
// ========================================

function filterChannelList() {
    const searchTerm = document.getElementById('quickAddChannelSearch').value.toLowerCase();

    const container = document.getElementById('taskChannelPool');
    const items = container.querySelectorAll('.camera-pool-item');

    items.forEach(item => {
        const channelId = parseInt(item.getAttribute('data-channel-id'));
        const channel = addedChannels.find(ch => ch.id === channelId);

        if (channel) {
            const name = (channel.channel_name || '').toLowerCase();
            const url = (channel.rtsp_url || '').toLowerCase();

            if (name.includes(searchTerm) || url.includes(searchTerm)) {
                item.style.display = 'flex';
            } else {
                item.style.display = 'none';
            }
        }
    });
}

// ========================================
// 保存任务
// ========================================

// 辅助函数：将颜色输入转换为 RGB 数组 [R, G, B]
// 支持 RGB HEX 格式（#RRGGBB，来自 color picker）和 "R,G,B" 文本格式
function parseColorInput(value, defaultColor) {
    if (!value || typeof value !== 'string') return defaultColor;
    // 处理 RGB HEX 格式（color picker 输出）
    if (value.startsWith('#')) {
        const hex = value.slice(1);
        if (hex.length === 6) {
            const r = parseInt(hex.slice(0, 2), 16);
            const g = parseInt(hex.slice(2, 4), 16);
            const b = parseInt(hex.slice(4, 6), 16);
            if (!isNaN(r) && !isNaN(g) && !isNaN(b)) {
                return [r, g, b];
            }
        }
        return defaultColor;
    }
    // 兼容 "R,G,B" 文本格式
    const parts = value.trim().split(/[,\s]+/).map(s => parseInt(s.trim()));
    if (parts.length === 3 && parts.every(n => !isNaN(n) && n >= 0 && n <= 255)) {
        return parts;
    }
    return defaultColor;
}

// 辅助函数：将 RGB 数组 [R, G, B] 转换为 RGB HEX 格式 "#RRGGBB"（用于 color picker）
function colorToText(colorArr, defaultText) {
    if (Array.isArray(colorArr) && colorArr.length === 3 &&
        colorArr.every(n => typeof n === 'number' && !isNaN(n) && n >= 0 && n <= 255)) {
        const toHex = n => n.toString(16).padStart(2, '0');
        return '#' + toHex(colorArr[0]) + toHex(colorArr[1]) + toHex(colorArr[2]);
    }
    return defaultText;
}

// 辅助函数：将 RGB 数组 [R, G, B] 转换为 "R,G,B" 文本（用于显示）
function colorToRgbText(colorArr, defaultText) {
    if (Array.isArray(colorArr) && colorArr.length === 3 &&
        colorArr.every(n => typeof n === 'number' && !isNaN(n) && n >= 0 && n <= 255)) {
        return colorArr.join(',');
    }
    return defaultText;
}

// 颜色选择器变化时同步显示 RGB 文本值
function setupColorPickerListeners() {
    const pickers = [
        { id: 'displayBoxColor', valueId: 'displayBoxColorValue' },
        { id: 'displayBoxLabelColor', valueId: 'displayBoxLabelColorValue' },
        { id: 'displayTrackColor', valueId: 'displayTrackColorValue' },
        { id: 'displayTrackLabelColor', valueId: 'displayTrackLabelColorValue' },
        { id: 'displayTrajectoryColor', valueId: 'displayTrajectoryColorValue' },
        { id: 'displayTrajectoryPointColor', valueId: 'displayTrajectoryPointColorValue' },
        { id: 'displayMotionRegionColor', valueId: 'displayMotionRegionColorValue' },
        { id: 'displayMotionRegionFillColor', valueId: 'displayMotionRegionFillColorValue' }
    ];
    pickers.forEach(({ id, valueId }) => {
        const picker = document.getElementById(id);
        const valueDisplay = document.getElementById(valueId);
        if (picker && valueDisplay) {
            picker.addEventListener('input', () => {
                const rgbArr = parseColorInput(picker.value, [0, 0, 0]);
                valueDisplay.textContent = rgbArr.join(',');
            });
        }
    });
}

async function saveTask() {
    const taskId = document.getElementById('taskId').value;
    const isEdit = taskId !== '';

    // 合并临时通道和已入库通道的ID
    let allChannelIds = [];

    // 已入库的通道ID
    allChannelIds = selectedChannels.map(id => parseInt(id));

    // 临时通道的camera_id
    const tempCameraIds = tempSelectedChannels.map(ch => ch.camera_id);

    // 收集表单数据
    const formData = {
        task_name: document.getElementById('taskName').value,
        enabled: document.getElementById('taskEnabled').value === 'true',
        detection_type: document.getElementById('detectionType').value,  // 检测类型（新增）
        algorithm: document.getElementById('algorithm').value,
        model_path: document.getElementById('modelPath').value,
        target_classes: (() => {
            const value = document.getElementById('targetClassesHidden').value;
            try {
                return value && value.trim() ? JSON.parse(value) : [];
            } catch (e) {
                console.error('解析目标类别失败:', e);
                return [];
            }
        })(),

        // 通道数据（临时通道需要入库）
        temp_channels: tempSelectedChannels,  // 临时通道数据（待入库）
        existing_channel_ids: selectedChannels,  // 已入库的通道ID

        // 功能开关（新增）
        motion_detection_enabled: document.getElementById('motionDetectionEnabled').value === 'true',
        tracking_enabled: document.getElementById('trackingEnabled').value === 'true',

        // 检测器配置（新增）
        detector_config: {
            backend: document.getElementById('detectorBackend')?.value || 'onnx_cuda',
            use_fp16: document.getElementById('detectorUseFp16')?.value === 'true',
            tensorrt_lib_path: document.getElementById('detectorTensorrtLibPath')?.value || '',
            tensorrt_engine_path: document.getElementById('detectorTensorrtEnginePath')?.value || '',
            tensorrt_engine_cache_path: document.getElementById('detectorTensorrtCachePath')?.value || './trt_cache',
            conf_threshold: parseFloat(document.getElementById('detectorConfThreshold')?.value) || 0.25,
            iou_threshold: parseFloat(document.getElementById('detectorIouThreshold')?.value) || 0.35,
            min_box_area: parseInt(document.getElementById('detectorMinBoxArea')?.value) || 50,
            device_id: parseInt(document.getElementById('detectorDeviceId')?.value) || 0,
            gpu_mem_limit: parseInt(document.getElementById('detectorGpuMemLimit')?.value) || 6144,
            rknn_target: document.getElementById('detectorRknnTarget')?.value || 'rk3588',
            rknn_core_mask: parseInt(document.getElementById('detectorRknnCoreMask')?.value) || 0,
            rknn_device_id: document.getElementById('detectorRknnDeviceId')?.value || '',
            num_detectors: document.getElementById('detectorNumDetectors')?.value
                ? parseInt(document.getElementById('detectorNumDetectors').value)
                : null
        },

        // 解码器配置（新增 - 之前遗漏）
        decoder_config: {
            width: parseInt(document.getElementById('decoderWidth')?.value) || 640,
            height: parseInt(document.getElementById('decoderHeight')?.value) || 640,
            fps: parseInt(document.getElementById('decoderFps')?.value) || 30,
            hwaccel_preset: document.getElementById('decoderHwaccelPreset')?.value || 'preset-dxva2',
            input_preset: document.getElementById('decoderInputPreset')?.value || 'preset-rtsp-generic',
            rtsp_transport: document.getElementById('decoderRtspTransport')?.value || 'tcp',
            reconnect: document.getElementById('decoderReconnect')?.value === 'true',
            reconnect_delay: parseInt(document.getElementById('decoderReconnectDelay')?.value) || 5,
            buffer_size: parseInt(document.getElementById('decoderBufferSize')?.value) || 2097152,
            ring_buffer_block_count: parseInt(document.getElementById('decoderRingBufferBlockCount')?.value) || 40,
            index_queue_size: parseInt(document.getElementById('decoderIndexQueueSize')?.value) || 50,
            ffmpeg_filters: document.getElementById('decoderFfmpegFilters')?.value || ''
        },

        // 事件配置（新增）
        event_config: {
            enabled: document.getElementById('eventEnabled')?.value === 'true',
            image_output: {
                local_enabled: document.getElementById('eventLocalEnabled')?.value === 'true',
                minio_enabled: document.getElementById('eventMinioEnabled')?.value === 'true',
                ftp_enabled: document.getElementById('eventFtpEnabled')?.value === 'true',
                output_dir: document.getElementById('eventImageOutputDir')?.value || 'output/images',
                draw_bbox: document.getElementById('eventImageDrawBbox')?.value === 'true'
            },
            gif: {
                enabled: document.getElementById('eventGifEnabled')?.value === 'true',
                draw_bbox: document.getElementById('eventGifDrawBbox')?.value === 'true',
                local_enabled: document.getElementById('eventGifLocalEnabled')?.value === 'true',
                minio_enabled: document.getElementById('eventGifMinioEnabled')?.value === 'true',
                ftp_enabled: document.getElementById('eventGifFtpEnabled')?.value === 'true',
                output_dir: document.getElementById('eventGifOutputDir')?.value || 'output/gifs',
                crop_mode: document.getElementById('eventGifCropMode')?.value || 'full',
                crop_margin_ratio: parseFloat(document.getElementById('eventCropMarginRatio')?.value) || 1.0,
                pre_seconds: parseFloat(document.getElementById('eventGifPreSeconds')?.value) || 5.0,
                post_seconds: parseFloat(document.getElementById('eventGifPostSeconds')?.value) || 5.0
            },
            filter: {
                min_confidence: parseFloat(document.getElementById('eventMinConfidence')?.value) || 0.5
            }
        },

        // 跟踪器配置（Norfair）
        tracker_config: {
            tracker_type: 'norfair',
            debug_print_interval: 0,
            n_init: parseInt(document.getElementById('trackerNInit')?.value) || 3,
            max_age: parseInt(document.getElementById('trackerMaxAge')?.value) || 30,
        },

        // 运动检测配置（帧差法）
        motion_config: {
            motion_height: parseInt(document.getElementById('motionHeight')?.value) || 180,
            threshold: parseInt(document.getElementById('motionThreshold')?.value) || 25,
            contour_area: parseInt(document.getElementById('motionContourArea')?.value) || 200,
            blur_radius: parseInt(document.getElementById('motionBlurRadius')?.value) || 1,
            frame_alpha: parseFloat(document.getElementById('motionFrameAlpha')?.value) || 0.01,
            lightning_threshold: parseFloat(document.getElementById('motionLightningThreshold')?.value) || 0.8,
            improve_contrast: document.getElementById('motionImproveContrast')?.value === 'true',
            contrast_frame_history: parseInt(document.getElementById('motionContrastFrameHistory')?.value) || 50,
        },

        // 显示配置（新增 - 之前遗漏）
        display_config: {
            display: {
                target_fps: parseInt(document.getElementById('displayTargetFps')?.value) || 25,
                ai_stream_interval: parseInt(document.getElementById('displayAIStreamInterval')?.value) || 3,
                show_detection_boxes: document.getElementById('displayShowBoxes')?.value === 'true',
                show_track_boxes: document.getElementById('displayShowTracks')?.value === 'true',
                show_trajectories: document.getElementById('displayShowTrajectories')?.value === 'true',
                show_motion_regions: document.getElementById('displayShowMotionRegions')?.value === 'true',
                show_zones: document.getElementById('displayShowZones')?.value === 'true',
                trajectory_history_length: parseInt(document.getElementById('displayTrajectoryHistory')?.value) || 30
            },
            detection_box: {
                color: parseColorInput(document.getElementById('displayBoxColor')?.value, [0, 255, 0]),
                thickness: parseInt(document.getElementById('displayBoxThickness')?.value) || 2,
                font_scale: parseFloat(document.getElementById('displayBoxFontScale')?.value) || 0.5,
                label_color: parseColorInput(document.getElementById('displayBoxLabelColor')?.value, [0, 255, 0])
            },
            track_box: {
                color: parseColorInput(document.getElementById('displayTrackColor')?.value, [0, 0, 255]),
                thickness: parseInt(document.getElementById('displayTrackThickness')?.value) || 2,
                font_scale: parseFloat(document.getElementById('displayTrackFontScale')?.value) || 0.5,
                label_color: parseColorInput(document.getElementById('displayTrackLabelColor')?.value, [0, 0, 255])
            },
            trajectory: {
                color: parseColorInput(document.getElementById('displayTrajectoryColor')?.value, [255, 255, 0]),
                thickness: parseInt(document.getElementById('displayTrajectoryThickness')?.value) || 1,
                point_size: parseInt(document.getElementById('displayTrajectoryPointSize')?.value) || 2,
                point_color: parseColorInput(document.getElementById('displayTrajectoryPointColor')?.value, [255, 0, 0])
            },
            motion_region: {
                color: parseColorInput(document.getElementById('displayMotionRegionColor')?.value, [0, 255, 255]),
                thickness: parseInt(document.getElementById('displayMotionRegionThickness')?.value) || 1,
                fill_color: parseColorInput(document.getElementById('displayMotionRegionFillColor')?.value, [0, 255, 255]),
                fill_alpha: parseInt(document.getElementById('displayMotionRegionFillAlpha')?.value) || 50
            },
            alarm: {
                zone_enter_enabled: document.getElementById('alarmZoneEnterEnabled')?.value === 'true',
                loitering_enabled: document.getElementById('alarmLoiteringEnabled')?.value === 'true',
                duration: parseInt(document.getElementById('alarmDuration')?.value) || 3,
                blink_interval: parseInt(document.getElementById('alarmBlinkInterval')?.value) || 500,
                show_banner: document.getElementById('alarmShowBanner')?.value === 'true',
                throttle_interval: parseInt(document.getElementById('alarmThrottleInterval')?.value) || 3000
            }
        }
    };

    // 验证必填字段
    if (!formData.task_name) {
        showNotification('请填写任务名称', 'error');
        return;
    }

    if (!formData.algorithm) {
        showNotification('请选择算法配置', 'error');
        return;
    }

    // 验证通道（临时通道 + 已入库通道）
    if (formData.temp_channels.length === 0 && formData.existing_channel_ids.length === 0) {
        showNotification('请至少选择一个设备通道（点击"添加通道"按钮）', 'error');
        return;
    }

    try {
        const url = isEdit ? `/api/tasks/${taskId}` : '/api/tasks';
        const method = isEdit ? 'PUT' : 'POST';

        const response = await fetch(url, {
            method: method,
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(formData)
        });

        const data = await response.json();

        if (response.ok) {
            showNotification(isEdit ? '任务已更新' : '任务已创建', 'success');

            // 清空临时数据
            tempSelectedChannels = [];
            selectedChannels = [];

            closeModal();
            loadTasks();
            loadStatistics();
            loadAddedChannels();  // 刷新已添加的通道列表
        } else {
            showNotification(data.error || '保存任务失败', 'error');
        }
    } catch (error) {
        console.error('保存任务失败:', error);
        showNotification('保存任务失败', 'error');
    }
}

// ========================================
// 删除任务
// ========================================

function openDeleteModal(taskId, taskName, event) {
    event.stopPropagation();
    
    deleteTaskId = taskId;
    document.getElementById('deleteTaskName').textContent = taskName;
    document.getElementById('deleteModal').classList.add('show');
}

function closeDeleteModal() {
    document.getElementById('deleteModal').classList.remove('show');
    deleteTaskId = null;
}

async function confirmDelete() {
    if (!deleteTaskId) return;
    
    try {
        const response = await fetch(`/api/tasks/${deleteTaskId}`, {
            method: 'DELETE'
        });
        
        const data = await response.json();
        
        if (response.ok) {
            showNotification('任务已删除', 'success');
            closeDeleteModal();
            loadTasks();
            loadStatistics();
        } else {
            showNotification(data.error || '删除任务失败', 'error');
        }
    } catch (error) {
        console.error('删除任务失败:', error);
        showNotification('删除任务失败', 'error');
    }
}

// ========================================
// 视图切换
// ========================================

function setView(view) {
    currentView = view;
    
    // 更新按钮状态
    const viewBtns = document.querySelectorAll('.view-btn');
    viewBtns.forEach(btn => btn.classList.remove('active'));
    
    if (view === 'card') {
        viewBtns[0].classList.add('active');
    } else {
        viewBtns[1].classList.add('active');
    }
    
    // 重新渲染
    renderTasks();
}

// ========================================
// 筛选重置
// ========================================

function resetFilters() {
    document.getElementById('filterStatus').value = '';
    document.getElementById('filterAlgorithm').value = '';
    document.getElementById('filterSearch').value = '';
    loadTasks();
}

// ========================================
// 折叠展开高级配置
// ========================================

function toggleSection(header) {
    const content = header.nextElementSibling;
    const icon = header.querySelector('.collapse-icon');
    
    if (content.style.display === 'none') {
        content.style.display = 'block';
        icon.style.transform = 'rotate(180deg)';
    } else {
        content.style.display = 'none';
        icon.style.transform = 'rotate(0deg)';
    }
}

// ========================================
// 算法切换
// ========================================

function onAlgorithmChange() {
    const algorithm = document.getElementById('algorithm').value;
    const modelPathInput = document.getElementById('modelPath');
    
    // 根据算法设置默认模型路径
    const defaultModels = {
        'yolov11': 'models/yolov11n.onnx',
        'yolov8': 'models/yolov8n.onnx',
        'yolo': 'models/yolov11n.onnx'
    };
    
    if (defaultModels[algorithm]) {
        modelPathInput.value = defaultModels[algorithm];
    }
}

// ========================================
// 辅助函数
// ========================================

function getStatusText(status) {
    const statusMap = {
        'running': '运行中',
        'stopped': '已停止',
        'error': '错误',
        'offline': '离线',
        'online': '在线'
    };
    return statusMap[status] || status;
}

// ========================================
// 分页功能
// ========================================

function updateTaskPagination() {
    const pagination = document.getElementById('taskPagination');
    const prevBtn = document.getElementById('taskPrevBtn');
    const nextBtn = document.getElementById('taskNextBtn');
    const pageNumbers = document.getElementById('taskPageNumbers');
    const paginationInfo = document.getElementById('taskPaginationInfo');

    if (totalTasks === 0) {
        pagination.style.display = 'none';
        return;
    }

    pagination.style.display = 'flex';

    // 更新信息
    const startItem = totalTasks > 0 ? (currentPage - 1) * perPage + 1 : 0;
    const endItem = Math.min(currentPage * perPage, totalTasks);
    paginationInfo.textContent = `第 ${startItem}-${endItem} 条，共 ${totalTasks} 条`;

    // 更新按钮状态
    prevBtn.disabled = currentPage <= 1;
    nextBtn.disabled = currentPage >= totalPages;

    // 生成页码按钮
    let pageHtml = '';
    const maxVisiblePages = 5;
    let startPage = Math.max(1, currentPage - Math.floor(maxVisiblePages / 2));
    let endPage = Math.min(totalPages, startPage + maxVisiblePages - 1);

    if (endPage - startPage < maxVisiblePages - 1) {
        startPage = Math.max(1, endPage - maxVisiblePages + 1);
    }

    if (startPage > 1) {
        pageHtml += `<button class="pagination-btn" onclick="Tasks.taskGoToPage(1)">1</button>`;
        if (startPage > 2) {
            pageHtml += `<span class="pagination-ellipsis">...</span>`;
        }
    }

    for (let i = startPage; i <= endPage; i++) {
        pageHtml += `<button class="pagination-btn ${i === currentPage ? 'active' : ''}" onclick="Tasks.taskGoToPage(${i})">${i}</button>`;
    }

    if (endPage < totalPages) {
        if (endPage < totalPages - 1) {
            pageHtml += `<span class="pagination-ellipsis">...</span>`;
        }
        pageHtml += `<button class="pagination-btn" onclick="Tasks.taskGoToPage(${totalPages})">${totalPages}</button>`;
    }

    pageNumbers.innerHTML = pageHtml;
}

function taskGoToPage(page) {
    currentPage = page;
    loadTasks();
}

function taskPrevPage() {
    if (currentPage > 1) {
        currentPage--;
        loadTasks();
    }
}

function taskNextPage() {
    if (currentPage < totalPages) {
        currentPage++;
        loadTasks();
    }
}

function taskChangePageSize() {
    perPage = parseInt(document.getElementById('taskPageSizeSelect').value);
    currentPage = 1;
    loadTasks();
}

function applyTaskFilters() {
    currentPage = 1;
    loadTasks();
}

// ========================================
// 辅助函数
// ========================================

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ========================================
// 暴露命名空间：供 HTML onclick/onchange 调用
// ========================================
return {
    applyTaskFilters: applyTaskFilters,
    loadTasks: loadTasks,
    openCreateModal: openCreateModal,
    openEditModal: openEditModal,
    openDeleteModal: openDeleteModal,
    closeModal: closeModal,
    closeDeleteModal: closeDeleteModal,
    confirmDelete: confirmDelete,
    saveTask: saveTask,
    editCurrentTask: editCurrentTask,
    closeTaskDetailModal: closeTaskDetailModal,
    setView: setView,
    resetFilters: resetFilters,
    taskPrevPage: taskPrevPage,
    taskNextPage: taskNextPage,
    taskChangePageSize: taskChangePageSize,
    taskGoToPage: taskGoToPage,
    onAlgorithmConfigChange: onAlgorithmConfigChange,
    openClassesSelectDialog: openClassesSelectDialog,
    closeClassesSelectDialog: closeClassesSelectDialog,
    selectAllClasses: selectAllClasses,
    deselectAllClasses: deselectAllClasses,
    confirmClassesSelection: confirmClassesSelection,
    filterChannelsBySource: filterChannelsBySource,
    openChannelSelectModal: openChannelSelectModal,
    filterChannelList: filterChannelList,
    closeChannelSelectModal: closeChannelSelectModal,
    confirmChannelSelection: confirmChannelSelection,
    startTask: startTask,
    stopTask: stopTask,
    removeChannelFromPool: removeChannelFromPool,
    toggleCameraSelection: toggleCameraSelection
};

})();

