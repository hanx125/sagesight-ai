/**
 * SageSight AI - 算法库页面 JavaScript
 */
window.Algorithms = (function() {

// ========================================
// 全局变量
// ========================================

var algorithms = [];
var currentView = 'card';
var deleteAlgorithmId = null;
var selectedModelFile = null;  // 选择的模型文件
var selectedLabelsFile = null; // 选择的标签文件

// ========================================
// 页面初始化
// ========================================

// 注册页面初始化函数（支持 pjax 局部加载）
SageSight.registerPage('/algorithms', function() {
    loadAlgorithms();
    initWebSocket();

    // 绑定筛选工具栏事件：条件变化时重新加载算法列表
    const filterType = document.getElementById('filterType');
    const filterStatus = document.getElementById('filterStatus');
    const filterSearch = document.getElementById('filterSearch');

    if (filterType) {
        filterType.addEventListener('change', () => loadAlgorithms());
    }
    if (filterStatus) {
        filterStatus.addEventListener('change', () => loadAlgorithms());
    }
    if (filterSearch) {
        // 输入搜索加 300ms 防抖，避免每次按键都请求
        let searchTimer = null;
        filterSearch.addEventListener('input', () => {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(() => loadAlgorithms(), 300);
        });
    }
});

// ========================================
// 模型文件选择与上传
// ========================================

function selectModelFile() {
    const modelFileInput = document.getElementById('modelFile');
    if (modelFileInput) {
        modelFileInput.click();
    }
}

function handleModelFileSelect(input) {
    const file = input.files[0];
    if (!file) return;
    
    selectedModelFile = file;
    
    // 显示文件信息
    const modelFileName = document.getElementById('modelFileName');
    const modelFileSize = document.getElementById('modelFileSize');
    const modelFileInfo = document.getElementById('modelFileInfo');
    const modelPathInput = document.getElementById('modelPath');
    const algorithmTypeSelect = document.getElementById('algorithmType');
    const modelFormatSelect = document.getElementById('modelFormat');
    
    if (modelFileName) {
        modelFileName.textContent = file.name;
    }
    
    if (modelFileSize) {
        modelFileSize.textContent = formatFileSize(file.size);
    }
    
    if (modelFileInfo) {
        modelFileInfo.style.display = 'flex';
    }
    
    // 自动填充模型路径
    const algorithmType = algorithmTypeSelect ? algorithmTypeSelect.value : 'detection';
    const modelPath = `models/${algorithmType}/${file.name}`;
    
    if (modelPathInput) {
        modelPathInput.value = modelPath;
    }
    
    // 自动识别模型格式
    const ext = file.name.split('.').pop().toLowerCase();
    if (modelFormatSelect) {
        if (ext === 'onnx') {
            modelFormatSelect.value = 'onnx';
        } else if (ext === 'engine' || ext === 'plan') {
            modelFormatSelect.value = 'tensorrt';
        } else if (ext === 'pt' || ext === 'pth') {
            modelFormatSelect.value = 'pytorch';
        }
    }
}

function clearModelFile() {
    selectedModelFile = null;
    const modelFileInput = document.getElementById('modelFile');
    const modelFileInfo = document.getElementById('modelFileInfo');
    const modelPathInput = document.getElementById('modelPath');
    
    if (modelFileInput) {
        modelFileInput.value = '';
    }
    
    if (modelFileInfo) {
        modelFileInfo.style.display = 'none';
    }
    
    if (modelPathInput) {
        modelPathInput.value = '';
    }
}

// ========================================
// 标签文件选择和上传
// ========================================

function selectLabelsFile() {
    const labelsFileInput = document.getElementById('labelsFileUpload');
    if (labelsFileInput) {
        labelsFileInput.click();
    }
}

function handleLabelsFileSelect(input) {
    const file = input.files[0];
    if (!file) return;
    
    selectedLabelsFile = file;
    
    // 显示文件信息
    const labelsFileName = document.getElementById('labelsFileName');
    const labelsFileSize = document.getElementById('labelsFileSize');
    const labelsFileInfo = document.getElementById('labelsFileInfo');
    const labelsPathInput = document.getElementById('labelsFile');
    
    if (labelsFileName) {
        labelsFileName.textContent = file.name;
    }
    
    if (labelsFileSize) {
        const sizeKB = (file.size / 1024).toFixed(2);
        labelsFileSize.textContent = `${sizeKB} KB`;
    }
    
    if (labelsFileInfo) {
        labelsFileInfo.style.display = 'flex';
    }
    
    // 自动填充路径
    if (labelsPathInput) {
        labelsPathInput.value = `labels/${file.name}`;
    }
}

function clearLabelsFile() {
    selectedLabelsFile = null;
    const labelsFileInput = document.getElementById('labelsFileUpload');
    const labelsFileInfo = document.getElementById('labelsFileInfo');
    const labelsPathInput = document.getElementById('labelsFile');
    
    if (labelsFileInput) {
        labelsFileInput.value = '';
    }
    
    if (labelsFileInfo) {
        labelsFileInfo.style.display = 'none';
    }
    
    if (labelsPathInput) {
        labelsPathInput.value = '';
    }
}

// ========================================
// 算法图片选择与上传
// ========================================

var selectedAlgoImage = null;  // 选择的算法图片文件
var currentAlgoImageUrl = '';  // 已保存的算法图片URL

function handleAlgoImageSelect(input) {
    const file = input.files[0];
    if (!file) return;
    
    selectedAlgoImage = file;
    
    // 预览图片
    const reader = new FileReader();
    reader.onload = function(e) {
        const preview = document.getElementById('algoImagePreview');
        const img = document.getElementById('algoImageImg');
        const placeholder = document.getElementById('algoImagePlaceholder');
        
        img.src = e.target.result;
        preview.style.display = 'flex';
        placeholder.style.display = 'none';
    };
    reader.readAsDataURL(file);
}

function removeAlgoImage() {
    selectedAlgoImage = null;
    currentAlgoImageUrl = '';
    
    const preview = document.getElementById('algoImagePreview');
    const placeholder = document.getElementById('algoImagePlaceholder');
    const uploadInput = document.getElementById('algoImageUpload');
    
    if (preview) preview.style.display = 'none';
    if (placeholder) placeholder.style.display = 'flex';
    if (uploadInput) uploadInput.value = '';
}

function resetAlgoImageField() {
    selectedAlgoImage = null;
    currentAlgoImageUrl = '';
    
    const preview = document.getElementById('algoImagePreview');
    const placeholder = document.getElementById('algoImagePlaceholder');
    const uploadInput = document.getElementById('algoImageUpload');
    
    if (preview) preview.style.display = 'none';
    if (placeholder) placeholder.style.display = 'flex';
    if (uploadInput) uploadInput.value = '';
}

function setAlgoImagePreview(imageUrl) {
    currentAlgoImageUrl = imageUrl || '';
    selectedAlgoImage = null;
    
    const preview = document.getElementById('algoImagePreview');
    const placeholder = document.getElementById('algoImagePlaceholder');
    const img = document.getElementById('algoImageImg');
    
    if (imageUrl) {
        img.src = imageUrl;
        preview.style.display = 'flex';
        placeholder.style.display = 'none';
    } else {
        preview.style.display = 'none';
        placeholder.style.display = 'flex';
    }
}

async function uploadAlgoImage(algorithmId) {
    if (!selectedAlgoImage) return currentAlgoImageUrl;
    
    const formData = new FormData();
    formData.append('file', selectedAlgoImage);
    
    try {
        const response = await fetch(`/api/algorithms/${algorithmId}/image`, {
            method: 'POST',
            body: formData
        });
        const data = await response.json();
        if (data.error) {
            console.error('上传算法图片失败:', data.error);
            return null;
        }
        currentAlgoImageUrl = data.algorithm_image;
        return data.algorithm_image;
    } catch (error) {
        console.error('上传算法图片失败:', error);
        return null;
    }
}

function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}

// ========================================
// WebSocket 初始化
// ========================================

function initWebSocket() {
    if (typeof io !== 'undefined') {
        const socket = io('/ws', {
            transports: ['websocket', 'polling']
        });
        
        socket.on('algorithm_created', function(data) {
            loadAlgorithms();
        });
        
        socket.on('algorithm_updated', function(data) {
            loadAlgorithms();
        });
        
        socket.on('algorithm_deleted', function(data) {
            loadAlgorithms();
        });
    }
}

// ========================================
// 加载算法列表
// ========================================

async function loadAlgorithms() {
    try {
        // 获取筛选条件（如果元素存在）
        const filterTypeEl = document.getElementById('filterType');
        const filterStatusEl = document.getElementById('filterStatus');
        const filterSearchEl = document.getElementById('filterSearch');
        
        // 如果筛选元素不存在，说明不在算法管理页面，跳过加载
        if (!filterTypeEl && !filterStatusEl && !filterSearchEl) {
            console.log('[algorithms.js] 当前页面没有算法筛选元素，跳过加载');
            return;
        }
        
        const type = filterTypeEl ? filterTypeEl.value : '';
        const status = filterStatusEl ? filterStatusEl.value : '';
        const search = filterSearchEl ? filterSearchEl.value : '';
        
        // 构建查询参数
        let url = '/api/algorithms';
        const params = new URLSearchParams();
        
        if (type) {
            params.append('type', type);
        }
        if (status) {
            params.append('status', status);
        }
        
        if (params.toString()) {
            url += '?' + params.toString();
        }
        
        // 发送请求
        const response = await fetch(url);
        const data = await response.json();
        
        // 应用搜索筛选
        algorithms = data.filter(algorithm => {
            if (search) {
                const searchLower = search.toLowerCase();
                const matchName = algorithm.name.toLowerCase().includes(searchLower);
                const matchDescription = (algorithm.description || '').toLowerCase().includes(searchLower);
                if (!matchName && !matchDescription) {
                    return false;
                }
            }
            return true;
        });
        
        // 更新统计
        updateStats();
        
        // 渲染算法列表
        renderAlgorithms();
        
    } catch (error) {
        console.error('加载算法失败:', error);
        showNotification('加载算法失败', 'error');
    }
}

// ========================================
// 更新统计
// ========================================

async function updateStats() {
    try {
        // 使用服务端统计接口
        const response = await fetch('/api/algorithms/statistics');
        const stats = await response.json();

        // pjax 导航后 DOM 可能已被替换，操作前检查元素是否存在
        const setText = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value; };
        setText('statTotal', stats.total || 0);
        setText('statActive', stats.active || 0);
        setText('statInactive', stats.inactive || 0);
        setText('statModels', stats.model_files || 0);
    } catch (error) {
        console.error('获取统计信息失败:', error);
        // 失败时使用本地计算
        const total = algorithms.length;
        const active = algorithms.filter(a => a.status === 'active').length;
        const inactive = algorithms.filter(a => a.status === 'inactive').length;
        const models = algorithms.filter(a => a.model_path && a.model_path.trim() !== '').length;

        const setText = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value; };
        setText('statTotal', total);
        setText('statActive', active);
        setText('statInactive', inactive);
        setText('statModels', models);
    }
}

// ========================================
// 渲染算法列表
// ========================================

function renderAlgorithms() {
    const algorithmsList = document.getElementById('algorithmsList');
    const emptyState = document.getElementById('emptyState');

    // pjax 导航后 DOM 可能已被替换
    if (!algorithmsList || !emptyState) return;

    if (algorithms.length === 0) {
        algorithmsList.style.display = 'none';
        emptyState.style.display = 'block';
        return;
    }

    emptyState.style.display = 'none';
    
    // 根据视图类型渲染
    if (currentView === 'card') {
        algorithmsList.style.display = 'grid';
        algorithmsList.classList.remove('list-view');
        algorithmsList.innerHTML = algorithms.map(algorithm => renderAlgorithmCard(algorithm)).join('');
    } else {
        algorithmsList.style.display = 'block';
        algorithmsList.classList.add('list-view');
        algorithmsList.innerHTML = `
            <div class="algorithms-list-header">
                <span></span>
                <span>算法名称</span>
                <span>状态</span>
                <span>类型</span>
                <span>模型类型</span>
                <span>格式</span>
                <span>描述</span>
                <span>操作</span>
            </div>
            ${algorithms.map(algorithm => renderAlgorithmListItem(algorithm)).join('')}
        `;
    }
}

// ========================================
// 渲染算法卡片
// ========================================

function renderAlgorithmCard(algorithm) {
    const statusClass = algorithm.status === 'active' ? 'status-running' : 'status-stopped';
    const statusText = algorithm.status === 'active' ? '可用' : '不可用';
    const typeText = getTypeText(algorithm.type);
    
    // 图片区域：有图片时铺满body区域，无图片时显示信息行
    let bodySection = '';
    if (algorithm.algorithm_image) {
        bodySection = `
            <div class="algorithm-card-image-area">
                <img src="${algorithm.algorithm_image}" alt="${escapeHtml(algorithm.name)}" onerror="this.parentElement.style.display='none'">
                <div class="algorithm-image-overlay">
                    <div class="overlay-info">
                        <span class="overlay-tag">${typeText} · ${escapeHtml(algorithm.model_format || 'ONNX')}</span>
                    </div>
                    <div class="overlay-description">${escapeHtml(algorithm.description || '暂无描述')}</div>
                </div>
            </div>`;
    } else {
        bodySection = `
            <div class="algorithm-card-body">
                <div class="algorithm-info-row">
                    <span class="algorithm-info-label">类型</span>
                    <span class="algorithm-info-value">${typeText}</span>
                </div>
                <div class="algorithm-info-row">
                    <span class="algorithm-info-label">格式</span>
                    <span class="algorithm-info-value">${escapeHtml(algorithm.model_format || 'ONNX')}</span>
                </div>
            </div>`;
    }
    
    return `
        <div class="algorithm-card" onclick="Algorithms.showAlgorithmDetail(${algorithm.id})">
            <div class="algorithm-card-header">
                <div>
                    <div class="algorithm-name">${escapeHtml(algorithm.name)}</div>
                    <div class="algorithm-version">${escapeHtml(algorithm.version || 'v1.0')}</div>
                </div>
                <span class="algorithm-status-badge ${statusClass}">${statusText}</span>
            </div>
            ${bodySection}
            <div class="algorithm-card-footer">
                <div class="algorithm-actions">
                    <button class="algorithm-action-btn edit" onclick="Algorithms.openEditModal(${algorithm.id}, event)">
                        <svg viewBox="0 0 24 24" fill="currentColor">
                            <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/>
                        </svg>
                        编辑
                    </button>
                    <button class="algorithm-action-btn delete" onclick="Algorithms.openDeleteModal(${algorithm.id}, '${escapeHtml(algorithm.name)}', event)">
                        <svg viewBox="0 0 24 24" fill="currentColor">
                            <path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/>
                        </svg>
                        删除
                    </button>
                </div>
            </div>
        </div>
    `;
}

// ========================================
// 渲染算法列表项（表格视图）
// ========================================

function renderAlgorithmListItem(algorithm) {
    const statusClass = algorithm.status === 'active' ? 'status-running' : 'status-stopped';
    const statusText = algorithm.status === 'active' ? '可用' : '不可用';
    const typeText = getTypeText(algorithm.type);
    
    // 缩略图
    let thumbSection = '';
    if (algorithm.algorithm_image) {
        thumbSection = `<img src="${algorithm.algorithm_image}" alt="${escapeHtml(algorithm.name)}" class="algo-list-thumb" onerror="this.style.display='none'">`;
    } else {
        thumbSection = `
            <div class="algo-list-thumb-placeholder">
                <svg viewBox="0 0 24 24" fill="currentColor">
                    <path d="M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM8.5 13.5l2.5 3.01L14.5 12l4.5 6H5l3.5-4.5z"/>
                </svg>
            </div>`;
    }
    
    return `
        <div class="algorithm-list-item" onclick="Algorithms.showAlgorithmDetail(${algorithm.id})">
            <div class="algo-list-thumb-cell">${thumbSection}</div>
            <div class="algo-list-name-cell">
                <div class="algo-list-name">${escapeHtml(algorithm.name)}</div>
                <div class="algo-list-version">${escapeHtml(algorithm.version || 'v1.0')}</div>
            </div>
            <div class="algo-list-status-cell">
                <span class="algorithm-status-badge ${statusClass}">${statusText}</span>
            </div>
            <div class="algo-list-type-cell">${typeText}</div>
            <div class="algo-list-model-type-cell">${escapeHtml(algorithm.model_type || 'yolov11n')}</div>
            <div class="algo-list-format-cell">${escapeHtml(algorithm.model_format || 'ONNX').toUpperCase()}</div>
            <div class="algo-list-desc-cell" title="${escapeHtml(algorithm.description || '')}">${escapeHtml(algorithm.description || '暂无描述')}</div>
            <div class="algo-list-actions-cell">
                <button class="algorithm-action-btn edit" onclick="Algorithms.openEditModal(${algorithm.id}, event)" title="编辑">
                    <svg viewBox="0 0 24 24" fill="currentColor">
                        <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/>
                    </svg>
                </button>
                <button class="algorithm-action-btn delete" onclick="Algorithms.openDeleteModal(${algorithm.id}, '${escapeHtml(algorithm.name)}', event)" title="删除">
                    <svg viewBox="0 0 24 24" fill="currentColor">
                        <path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/>
                    </svg>
                </button>
            </div>
        </div>
    `;
}

// ========================================
// 创建/编辑算法模态框
// ========================================

function openCreateModal() {
    document.getElementById('modalTitle').textContent = '添加算法';
    document.getElementById('algorithmId').value = '';
    document.getElementById('algorithmForm').reset();
    
    // 设置默认状态
    document.getElementById('algorithmStatus').value = 'active';
    
    // 清除选择的模型文件
    selectedModelFile = null;
    const modelFileInput = document.getElementById('modelFile');
    const modelFileInfo = document.getElementById('modelFileInfo');
    
    if (modelFileInput) {
        modelFileInput.value = '';
    }
    
    if (modelFileInfo) {
        modelFileInfo.style.display = 'none';
    }
    
    // 清除选择的标签文件
    selectedLabelsFile = null;
    const labelsFileInput = document.getElementById('labelsFileUpload');
    const labelsFileInfo = document.getElementById('labelsFileInfo');
    
    if (labelsFileInput) {
        labelsFileInput.value = '';
    }
    
    if (labelsFileInfo) {
        labelsFileInfo.style.display = 'none';
    }
    
    // 清除算法图片
    resetAlgoImageField();
    
    document.getElementById('algorithmModal').classList.add('show');
}

async function openEditModal(algorithmId, event) {
    event.stopPropagation();
    
    try {
        const response = await fetch(`/api/algorithms/${algorithmId}`);
        const algorithm = await response.json();
        
        document.getElementById('modalTitle').textContent = '编辑算法';
        document.getElementById('algorithmId').value = algorithm.id;
        
        // 填充基本信息
        document.getElementById('algorithmName').value = algorithm.name;
        document.getElementById('algorithmType').value = algorithm.type;
        document.getElementById('algorithmVersion').value = algorithm.version || '';
        document.getElementById('algorithmStatus').value = algorithm.status;
        document.getElementById('algorithmDescription').value = algorithm.description || '';
        document.getElementById('modelPath').value = algorithm.model_path || '';
        document.getElementById('modelType').value = algorithm.model_type || 'yolov11n';
        document.getElementById('modelFormat').value = algorithm.model_format || 'onnx';
        document.getElementById('inputSize').value = algorithm.input_size || '';
        // target_classes已删除，不再填充
        document.getElementById('labelsFile').value = algorithm.labels_file || '';
        
        // 显示已有算法图片
        setAlgoImagePreview(algorithm.algorithm_image);
        
        document.getElementById('algorithmModal').classList.add('show');
        
    } catch (error) {
        console.error('加载算法详情失败:', error);
        showNotification('加载算法详情失败', 'error');
    }
}

function closeModal() {
    document.getElementById('algorithmModal').classList.remove('show');
    // 清除选择的模型文件
    selectedModelFile = null;
    const modelFileInput = document.getElementById('modelFile');
    const modelFileInfo = document.getElementById('modelFileInfo');
    
    if (modelFileInput) {
        modelFileInput.value = '';
    }
    
    if (modelFileInfo) {
        modelFileInfo.style.display = 'none';
    }
    
    // 清除选择的标签文件
    selectedLabelsFile = null;
    const labelsFileInput = document.getElementById('labelsFileUpload');
    const labelsFileInfo = document.getElementById('labelsFileInfo');
    
    if (labelsFileInput) {
        labelsFileInput.value = '';
    }
    
    if (labelsFileInfo) {
        labelsFileInfo.style.display = 'none';
    }
    
    // 清除算法图片
    resetAlgoImageField();
}

async function saveAlgorithm() {
    const algorithmId = document.getElementById('algorithmId').value;
    const isEdit = algorithmId !== '';
    
    // 如果有选择的模型文件，先上传文件
    if (selectedModelFile) {
        try {
            const uploadFormData = new FormData();
            uploadFormData.append('file', selectedModelFile);
            uploadFormData.append('algorithm_type', document.getElementById('algorithmType').value || 'detection');
            uploadFormData.append('model_name', document.getElementById('algorithmName').value || selectedModelFile.name);
            
            const uploadResponse = await fetch('/api/models/upload', {
                method: 'POST',
                body: uploadFormData
            });
            
            const uploadData = await uploadResponse.json();
            
            if (!uploadResponse.ok) {
                showNotification(uploadData.error || '模型文件上传失败', 'error');
                return;
            }
            
            // 更新模型路径
            document.getElementById('modelPath').value = uploadData.path;
            showNotification('模型文件已上传', 'success');
            
        } catch (error) {
            console.error('模型文件上传失败:', error);
            showNotification('模型文件上传失败', 'error');
            return;
        }
    }
    
    // 如果有选择的标签文件，先上传文件
    if (selectedLabelsFile) {
        try {
            const uploadFormData = new FormData();
            uploadFormData.append('file', selectedLabelsFile);
            
            const uploadResponse = await fetch('/api/labels/upload', {
                method: 'POST',
                body: uploadFormData
            });
            
            const uploadData = await uploadResponse.json();
            
            if (!uploadResponse.ok) {
                showNotification(uploadData.error || '标签文件上传失败', 'error');
                return;
            }
            
            // 更新标签文件路径
            document.getElementById('labelsFile').value = uploadData.path;
            showNotification('标签文件已上传', 'success');
            
        } catch (error) {
            console.error('标签文件上传失败:', error);
            showNotification('标签文件上传失败', 'error');
            return;
        }
    }
    
    // 收集基本表单数据
    const formData = {
        name: document.getElementById('algorithmName').value,
        type: document.getElementById('algorithmType').value,
        version: document.getElementById('algorithmVersion').value,
        status: document.getElementById('algorithmStatus').value,
        description: document.getElementById('algorithmDescription').value,
        model_path: document.getElementById('modelPath').value,
        model_type: document.getElementById('modelType').value,  // 新增：模型类型字段
        model_format: document.getElementById('modelFormat').value,
        input_size: document.getElementById('inputSize').value,
        // target_classes已删除，不再收集（改为从labels_file动态获取）
        labels_file: document.getElementById('labelsFile').value
    };
    
    // 验证必填字段
    if (!formData.name) {
        showNotification('请填写必填字段', 'error');
        return;
    }
    
    try {
        const url = isEdit ? `/api/algorithms/${algorithmId}` : '/api/algorithms';
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
            // 如果有选择的算法图片，上传
            if (selectedAlgoImage) {
                const savedId = isEdit ? algorithmId : data.id;
                if (savedId) {
                    await uploadAlgoImage(savedId);
                }
            }
            showNotification(isEdit ? '算法已更新' : '算法已添加', 'success');
            closeModal();
            loadAlgorithms();
        } else {
            showNotification(data.error || '保存算法失败', 'error');
        }
    } catch (error) {
        console.error('保存算法失败:', error);
        showNotification('保存算法失败', 'error');
    }
}

// ========================================
// 删除算法
// ========================================

function openDeleteModal(algorithmId, algorithmName, event) {
    event.stopPropagation();
    
    deleteAlgorithmId = algorithmId;
    document.getElementById('deleteAlgorithmName').textContent = algorithmName;
    document.getElementById('deleteModal').classList.add('show');
}

function closeDeleteModal() {
    document.getElementById('deleteModal').classList.remove('show');
    deleteAlgorithmId = null;
}

async function confirmDelete() {
    if (!deleteAlgorithmId) return;
    
    try {
        const response = await fetch(`/api/algorithms/${deleteAlgorithmId}`, {
            method: 'DELETE'
        });
        
        const data = await response.json();
        
        if (data.success) {
            showNotification('算法已删除', 'success');
            closeDeleteModal();
            loadAlgorithms();
        } else {
            showNotification(data.error || '删除算法失败', 'error');
        }
    } catch (error) {
        console.error('删除算法失败:', error);
        showNotification('删除算法失败', 'error');
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
    renderAlgorithms();
}

// ========================================
// 筛选重置
// ========================================

function resetFilters() {
    document.getElementById('filterType').value = '';
    document.getElementById('filterStatus').value = '';
    document.getElementById('filterSearch').value = '';
    loadAlgorithms();
}

// ========================================
// 折叠展开高级配置
// ========================================

function toggleSection(titleElement) {
    const section = titleElement.parentElement;
    const content = section.querySelector('.form-section-content');
    
    if (section.classList.contains('open')) {
        section.classList.remove('open');
        content.style.display = 'none';
    } else {
        section.classList.add('open');
        content.style.display = 'block';
    }
}

// ========================================
// 工具函数
// ========================================

function getTypeText(type) {
    const typeMap = {
        'detection': '目标检测',
        'tracking': '目标跟踪',
        'recognition': '目标识别',
        'segmentation': '图像分割'
    };
    return typeMap[type] || type;
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ========================================
// 算法详情（可选）
// ========================================

// 显示算法详情
function showAlgorithmDetail(algorithmId) {
    const algorithm = algorithms.find(a => a.id === algorithmId);
    if (!algorithm) {
        showNotification('算法不存在', 'error');
        return;
    }
    
    // 创建详情模态框
    const detailModal = document.createElement('div');
    detailModal.className = 'modal show';
    detailModal.id = 'algorithmDetailModal';
    
    detailModal.innerHTML = `
        <div class="modal-content modal-large">
            <div class="modal-header">
                <h3>算法详情 - ${escapeHtml(algorithm.name)}</h3>
                <button class="modal-close" onclick="Algorithms.closeAlgorithmDetailModal()">&times;</button>
            </div>
            <div class="modal-body">
                ${algorithm.algorithm_image ? `
                <div class="detail-section">
                    <div class="algorithm-detail-image" style="margin: 0 0 16px; height: 200px; border-radius: 8px; overflow: hidden;">
                        <img src="${algorithm.algorithm_image}" alt="${escapeHtml(algorithm.name)}" onerror="this.parentElement.style.display='none'" style="width:100%;height:100%;object-fit:cover;">
                    </div>
                </div>` : ''}
                <div class="detail-section">
                    <h4>基本信息</h4>
                    <div class="detail-info-grid">
                        <div class="detail-info-item">
                            <label>算法名称</label>
                            <span>${escapeHtml(algorithm.name)}</span>
                        </div>
                        <div class="detail-info-item">
                            <label>算法类型</label>
                            <span>${getTypeText(algorithm.type)}</span>
                        </div>
                        <div class="detail-info-item">
                            <label>版本</label>
                            <span>${escapeHtml(algorithm.version || 'v1.0')}</span>
                        </div>
                        <div class="detail-info-item">
                            <label>状态</label>
                            <span class="algorithm-status-badge ${algorithm.status === 'active' ? 'status-running' : 'status-stopped'}">
                                ${algorithm.status === 'active' ? '可用' : '不可用'}
                            </span>
                        </div>
                        <div class="detail-info-item">
                            <label>模型格式</label>
                            <span>${escapeHtml(algorithm.model_format || 'ONNX')}</span>
                        </div>
                        <div class="detail-info-item">
                            <label>输入尺寸</label>
                            <span>${escapeHtml(algorithm.input_size || '640x640')}</span>
                        </div>
                    </div>
                </div>
                
                <div class="detail-section">
                    <h4>模型信息</h4>
                    <div class="detail-info-item full-width">
                        <label>模型类型</label>
                        <span>${escapeHtml(algorithm.model_type || 'yolov11n')}</span>
                    </div>
                    <div class="detail-info-item full-width">
                        <label>模型路径</label>
                        <span>${escapeHtml(algorithm.model_path || '未设置')}</span>
                    </div>
                    <div class="detail-info-item full-width">
                        <label>描述</label>
                        <span>${escapeHtml(algorithm.description || '暂无描述')}</span>
                    </div>
                </div>
                
                <div class="detail-section">
                    <h4>标签文件</h4>
                    <div class="detail-info-item">
                        <span>${escapeHtml(algorithm.labels_file || '未设置')}</span>
                    </div>
                </div>
            </div>
            <div class="modal-footer">
                <button class="btn btn-secondary" onclick="Algorithms.closeAlgorithmDetailModal()">关闭</button>
                <button class="btn btn-primary" onclick="Algorithms.closeAlgorithmDetailModal(); Algorithms.openEditModal(${algorithm.id});">编辑算法</button>
            </div>
        </div>
    `;
    
    document.body.appendChild(detailModal);
}

// 关闭算法详情模态框
function closeAlgorithmDetailModal() {
    const modal = document.getElementById('algorithmDetailModal');
    if (modal) {
        modal.remove();
    }
}

// 点击模态框外部关闭
document.addEventListener('click', function(event) {
    const algorithmModal = document.getElementById('algorithmModal');
    const deleteModal = document.getElementById('deleteModal');
    const detailModal = document.getElementById('algorithmDetailModal');

    if (event.target === algorithmModal) {
        closeModal();
    }

    if (event.target === deleteModal) {
        closeDeleteModal();
    }

    if (event.target === detailModal) {
        closeAlgorithmDetailModal();
    }
});

// ========================================
// 模型上传相关（HTML 中有 onclick 引用，功能待实现）
// ========================================

function openUploadModal() {
    const modal = document.getElementById('uploadModal');
    if (modal) modal.classList.add('show');
}

function closeUploadModal() {
    const modal = document.getElementById('uploadModal');
    if (modal) modal.classList.remove('show');
}

function closeModelsListModal() {
    const modal = document.getElementById('modelsListModal');
    if (modal) modal.classList.remove('show');
}

function handleFileSelect(input) {
    // 模型上传文件选择处理
    const file = input.files[0];
    if (!file) return;
    const fileNameEl = document.getElementById('selectedFileName');
    if (fileNameEl) fileNameEl.textContent = file.name;
}

function clearSelectedFile() {
    const input = document.getElementById('modelFileUpload');
    if (input) input.value = '';
    const fileNameEl = document.getElementById('selectedFileName');
    if (fileNameEl) fileNameEl.textContent = '';
}

async function uploadModel() {
    const fileInput = document.getElementById('modelFileUpload');
    if (!fileInput || !fileInput.files[0]) {
        showNotification('请选择模型文件', 'error');
        return;
    }
    const formData = new FormData();
    formData.append('file', fileInput.files[0]);
    formData.append('algorithm_type', document.getElementById('algorithmType') ? document.getElementById('algorithmType').value : 'detection');
    const modelName = document.getElementById('uploadModelName');
    if (modelName && modelName.value) {
        formData.append('model_name', modelName.value);
    }
    try {
        const response = await fetch('/api/models/upload', { method: 'POST', body: formData });
        const data = await response.json();
        if (response.ok) {
            showNotification('模型上传成功', 'success');
            closeUploadModal();
        } else {
            showNotification(data.error || '上传失败', 'error');
        }
    } catch (error) {
        console.error('模型上传失败:', error);
        showNotification('模型上传失败', 'error');
    }
}

// ========================================
// 暴露命名空间：供 HTML onclick/onchange 调用
// ========================================
return {
    openCreateModal: openCreateModal,
    openEditModal: openEditModal,
    closeModal: closeModal,
    saveAlgorithm: saveAlgorithm,
    openDeleteModal: openDeleteModal,
    closeDeleteModal: closeDeleteModal,
    confirmDelete: confirmDelete,
    setView: setView,
    resetFilters: resetFilters,
    toggleSection: toggleSection,
    selectModelFile: selectModelFile,
    handleModelFileSelect: handleModelFileSelect,
    clearModelFile: clearModelFile,
    selectLabelsFile: selectLabelsFile,
    handleLabelsFileSelect: handleLabelsFileSelect,
    clearLabelsFile: clearLabelsFile,
    handleAlgoImageSelect: handleAlgoImageSelect,
    removeAlgoImage: removeAlgoImage,
    showAlgorithmDetail: showAlgorithmDetail,
    closeAlgorithmDetailModal: closeAlgorithmDetailModal,
    openUploadModal: openUploadModal,
    closeUploadModal: closeUploadModal,
    closeModelsListModal: closeModelsListModal,
    handleFileSelect: handleFileSelect,
    clearSelectedFile: clearSelectedFile,
    uploadModel: uploadModel
};

})();