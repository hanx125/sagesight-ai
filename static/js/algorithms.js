/**
 * SageSight AI - 算法库页面 JavaScript
 */

// ========================================
// 全局变量
// ========================================

let algorithms = [];
let currentView = 'card';
let deleteAlgorithmId = null;
let selectedModelFile = null;  // 选择的模型文件
let selectedLabelsFile = null; // 选择的标签文件

// ========================================
// 页面初始化
// ========================================

document.addEventListener('DOMContentLoaded', function() {
    loadAlgorithms();
    initWebSocket();
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
        // 获取筛选条件
        const type = document.getElementById('filterType').value;
        const status = document.getElementById('filterStatus').value;
        const search = document.getElementById('filterSearch').value;
        
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
        
        document.getElementById('statTotal').textContent = stats.total || 0;
        document.getElementById('statActive').textContent = stats.active || 0;
        document.getElementById('statInactive').textContent = stats.inactive || 0;
        document.getElementById('statModels').textContent = stats.model_files || 0;
    } catch (error) {
        console.error('获取统计信息失败:', error);
        // 失败时使用本地计算
        const total = algorithms.length;
        const active = algorithms.filter(a => a.status === 'active').length;
        const inactive = algorithms.filter(a => a.status === 'inactive').length;
        const models = algorithms.filter(a => a.model_path && a.model_path.trim() !== '').length;
        
        document.getElementById('statTotal').textContent = total;
        document.getElementById('statActive').textContent = active;
        document.getElementById('statInactive').textContent = inactive;
        document.getElementById('statModels').textContent = models;
    }
}

// ========================================
// 渲染算法列表
// ========================================

function renderAlgorithms() {
    const algorithmsList = document.getElementById('algorithmsList');
    const emptyState = document.getElementById('emptyState');
    
    if (algorithms.length === 0) {
        algorithmsList.style.display = 'none';
        emptyState.style.display = 'block';
        return;
    }
    
    algorithmsList.style.display = 'grid';
    emptyState.style.display = 'none';
    
    // 根据视图类型渲染
    if (currentView === 'card') {
        algorithmsList.classList.remove('list-view');
        algorithmsList.innerHTML = algorithms.map(algorithm => renderAlgorithmCard(algorithm)).join('');
    } else {
        algorithmsList.classList.add('list-view');
        algorithmsList.innerHTML = algorithms.map(algorithm => renderAlgorithmListItem(algorithm)).join('');
    }
}

// ========================================
// 渲染算法卡片
// ========================================

function renderAlgorithmCard(algorithm) {
    const statusClass = algorithm.status === 'active' ? 'status-running' : 'status-stopped';
    const statusText = algorithm.status === 'active' ? '可用' : '不可用';
    const typeText = getTypeText(algorithm.type);
    
    return `
        <div class="algorithm-card" onclick="showAlgorithmDetail(${algorithm.id})">
            <div class="algorithm-card-header">
                <div>
                    <div class="algorithm-name">${escapeHtml(algorithm.name)}</div>
                    <div class="algorithm-version">${escapeHtml(algorithm.version || 'v1.0')}</div>
                </div>
                <span class="algorithm-status-badge ${statusClass}">${statusText}</span>
            </div>
            <div class="algorithm-card-body">
                <div class="algorithm-info-row">
                    <span class="algorithm-info-label">类型</span>
                    <span class="algorithm-info-value">${typeText}</span>
                </div>
                <div class="algorithm-info-row">
                    <span class="algorithm-info-label">格式</span>
                    <span class="algorithm-info-value">${escapeHtml(algorithm.model_format || 'ONNX')}</span>
                </div>
            </div>
            <div class="algorithm-card-footer">
                <div class="algorithm-description">
                    ${escapeHtml(algorithm.description || '暂无描述')}
                </div>
                <div class="algorithm-actions">
                    <button class="algorithm-action-btn edit" onclick="openEditModal(${algorithm.id}, event)">
                        <svg viewBox="0 0 24 24" fill="currentColor">
                            <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/>
                        </svg>
                        编辑
                    </button>
                    <button class="algorithm-action-btn delete" onclick="openDeleteModal(${algorithm.id}, '${escapeHtml(algorithm.name)}', event)">
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
// 渲染算法列表项
// ========================================

function renderAlgorithmListItem(algorithm) {
    const statusClass = algorithm.status === 'active' ? 'status-running' : 'status-stopped';
    const statusText = algorithm.status === 'active' ? '可用' : '不可用';
    const typeText = getTypeText(algorithm.type);
    
    return `
        <div class="algorithm-card" onclick="showAlgorithmDetail(${algorithm.id})">
            <div class="algorithm-card-header">
                <div>
                    <div class="algorithm-name">${escapeHtml(algorithm.name)}</div>
                    <div class="algorithm-version">${escapeHtml(algorithm.version || 'v1.0')}</div>
                </div>
                <span class="algorithm-status-badge ${statusClass}">${statusText}</span>
            </div>
            <div class="algorithm-card-body">
                <div class="algorithm-info-row">
                    <span class="algorithm-info-label">类型</span>
                    <span class="algorithm-info-value">${typeText}</span>
                </div>
                <div class="algorithm-info-row">
                    <span class="algorithm-info-label">格式</span>
                    <span class="algorithm-info-value">${escapeHtml(algorithm.model_format || 'ONNX')}</span>
                </div>
            </div>
            <div class="algorithm-card-footer">
                <div class="algorithm-actions">
                    <button class="algorithm-action-btn edit" onclick="openEditModal(${algorithm.id}, event)">
                        <svg viewBox="0 0 24 24" fill="currentColor">
                            <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/>
                        </svg>
                        编辑
                    </button>
                    <button class="algorithm-action-btn delete" onclick="openDeleteModal(${algorithm.id}, '${escapeHtml(algorithm.name)}', event)">
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
        document.getElementById('modelType').value = algorithm.model_type || 'yolov11n';  // 新增：填充模型类型
        document.getElementById('modelFormat').value = algorithm.model_format || 'onnx';
        document.getElementById('inputSize').value = algorithm.input_size || '';
        // target_classes已删除，不再填充
        document.getElementById('labelsFile').value = algorithm.labels_file || '';
        
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

function showNotification(message, type) {
    // 创建通知元素
    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;
    notification.textContent = message;
    
    // 添加样式
    notification.style.cssText = `
        position: fixed;
        top: 80px;
        right: 24px;
        padding: 12px 20px;
        background: ${type === 'success' ? '#52c41a' : type === 'error' ? '#ff4d4f' : type === 'warning' ? '#faad14' : '#266CFB'};
        color: #fff;
        border-radius: 6px;
        font-size: 14px;
        font-weight: 500;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
        z-index: 3000;
        animation: slideInRight 0.3s;
    `;
    
    document.body.appendChild(notification);
    
    // 3秒后移除
    setTimeout(() => {
        notification.style.animation = 'slideOutRight 0.3s';
        setTimeout(() => {
            notification.remove();
        }, 300);
    }, 3000);
}

// ========================================
// 文件上传功能
// ========================================

let selectedFile = null;

// 打开上传模态框
function openUploadModal() {
    const modal = document.getElementById('uploadModal');
    modal.classList.add('show');
    clearSelectedFile();
    
    // 初始化拖拽上传
    initDragDrop();
}

// 关闭上传模态框
function closeUploadModal() {
    const modal = document.getElementById('uploadModal');
    modal.classList.remove('show');
    clearSelectedFile();
    
    // 重置表单
    document.getElementById('uploadForm').reset();
    document.getElementById('uploadProgress').style.display = 'none';
    document.getElementById('progressFill').style.width = '0%';
}

// 初始化拖拽上传
function initDragDrop() {
    const uploadArea = document.querySelector('.file-upload-area');
    
    uploadArea.addEventListener('dragover', function(e) {
        e.preventDefault();
        uploadArea.classList.add('dragover');
    });
    
    uploadArea.addEventListener('dragleave', function(e) {
        e.preventDefault();
        uploadArea.classList.remove('dragover');
    });
    
    uploadArea.addEventListener('drop', function(e) {
        e.preventDefault();
        uploadArea.classList.remove('dragover');
        
        const files = e.dataTransfer.files;
        if (files.length > 0) {
            handleFileSelect(files[0]);
        }
    });
}

// 处理文件选择
function handleFileSelect(input) {
    const file = input.files ? input.files[0] : input;
    
    if (!file) {
        return;
    }
    
    // 检查文件类型
    const allowedExtensions = ['onnx', 'engine', 'pt', 'pth', 'bin', 'xml', 'json'];
    const fileName = file.name.toLowerCase();
    const fileExtension = fileName.split('.').pop();
    
    if (!allowedExtensions.includes(fileExtension)) {
        showNotification('不支持的文件类型', 'error');
        return;
    }
    
    // 检查文件大小（最大500MB）
    const maxSize = 500 * 1024 * 1024;
    if (file.size > maxSize) {
        showNotification('文件大小超过500MB限制', 'error');
        return;
    }
    
    selectedFile = file;
    
    // 显示已选择的文件
    const selectedFileInfo = document.getElementById('selectedFile');
    selectedFileInfo.style.display = 'flex';
    selectedFileInfo.querySelector('.file-name').textContent = file.name;
    selectedFileInfo.querySelector('.file-size').textContent = formatFileSize(file.size);
    
    // 更新上传区域文本
    document.getElementById('uploadText').textContent = '已选择文件，点击重新选择';
}

// 清除已选择的文件
function clearSelectedFile() {
    selectedFile = null;
    document.getElementById('selectedFile').style.display = 'none';
    document.getElementById('uploadText').textContent = '点击选择模型文件或拖拽文件到此处';
    document.getElementById('modelFile').value = '';
}

// 格式化文件大小
function formatFileSize(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}

// 上传模型文件
async function uploadModel() {
    if (!selectedFile) {
        showNotification('请先选择模型文件', 'error');
        return;
    }
    
    const algorithmType = document.getElementById('uploadAlgorithmType').value;
    const modelName = document.getElementById('uploadModelName').value || selectedFile.name;
    
    // 创建FormData
    const formData = new FormData();
    formData.append('file', selectedFile);
    formData.append('algorithm_type', algorithmType);
    formData.append('model_name', modelName);
    
    // 显示上传进度
    const uploadProgress = document.getElementById('uploadProgress');
    uploadProgress.style.display = 'block';
    
    const uploadBtn = document.getElementById('uploadBtn');
    uploadBtn.disabled = true;
    uploadBtn.textContent = '上传中...';
    
    try {
        // 发送上传请求
        const xhr = new XMLHttpRequest();
        
        // 监听上传进度
        xhr.upload.addEventListener('progress', function(e) {
            if (e.lengthComputable) {
                const percent = Math.round((e.loaded / e.total) * 100);
                document.getElementById('progressFill').style.width = percent + '%';
                document.getElementById('progressText').textContent = `上传中... ${percent}%`;
            }
        });
        
        // 监听上传完成
        xhr.addEventListener('load', function() {
            if (xhr.status === 200) {
                const response = JSON.parse(xhr.responseText);
                
                showNotification('模型文件上传成功', 'success');
                closeUploadModal();
                
                // 提示创建算法
                if (confirm('模型文件已上传成功！是否立即创建算法配置？')) {
                    openCreateModalWithModel(response.path, response.filename, algorithmType);
                }
            } else {
                const error = JSON.parse(xhr.responseText);
                showNotification(error.error || '上传失败', 'error');
            }
            
            uploadBtn.disabled = false;
            uploadBtn.textContent = '上传';
        });
        
        // 监听上传错误
        xhr.addEventListener('error', function() {
            showNotification('上传失败，请检查网络连接', 'error');
            uploadBtn.disabled = false;
            uploadBtn.textContent = '上传';
        });
        
        // 发送请求
        xhr.open('POST', '/api/models/upload');
        xhr.send(formData);
        
    } catch (error) {
        console.error('上传失败:', error);
        showNotification('上传失败', 'error');
        uploadBtn.disabled = false;
        uploadBtn.textContent = '上传';
    }
}

// 打开创建算法模态框并预填充模型信息
function openCreateModalWithModel(modelPath, modelName, algorithmType) {
    openCreateModal();
    
    // 预填充模型信息
    setTimeout(() => {
        document.getElementById('modelPath').value = modelPath;
        document.getElementById('algorithmName').value = modelName.replace(/\.[^/.]+$/, "");
        document.getElementById('algorithmType').value = algorithmType;
        
        // 根据文件扩展名设置模型格式
        const extension = modelName.split('.').pop().toLowerCase();
        const formatMap = {
            'onnx': 'onnx',
            'engine': 'tensorrt',
            'pt': 'pytorch',
            'pth': 'pytorch',
            'bin': 'onnx',
            'xml': 'openvino',
            'json': 'onnx'
        };
        document.getElementById('modelFormat').value = formatMap[extension] || 'onnx';
    }, 100);
}

// ========================================
// 模型文件列表功能
// ========================================

// 打开模型文件列表模态框
async function openModelsListModal() {
    const modal = document.getElementById('modelsListModal');
    modal.classList.add('show');
    
    await loadModelsList();
}

// 关闭模型文件列表模态框
function closeModelsListModal() {
    const modal = document.getElementById('modelsListModal');
    modal.classList.remove('show');
}

// 加载模型文件列表
async function loadModelsList() {
    const modelsList = document.getElementById('modelsList');
    
    try {
        const response = await fetch('/api/models');
        const models = await response.json();
        
        if (models.length === 0) {
            modelsList.innerHTML = `
                <div class="empty-state">
                    <svg viewBox="0 0 24 24" fill="currentColor">
                        <path d="M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96zM14 13v4h-4v-4H7l5-5 5 5h-3z"/>
                    </svg>
                    <p>暂无模型文件</p>
                    <button class="btn btn-primary" onclick="closeModelsListModal(); openUploadModal();">上传第一个模型</button>
                </div>
            `;
            return;
        }
        
        modelsList.innerHTML = models.map(model => `
            <div class="model-item">
                <div class="model-item-header">
                    <div>
                        <div class="model-item-name">${escapeHtml(model.name)}</div>
                        <div class="model-item-format">${model.format.toUpperCase()}</div>
                    </div>
                </div>
                <div class="model-item-body">
                    <div class="model-item-info">
                        <span class="model-item-label">类型：</span>
                        <span class="model-item-value">${getTypeText(model.type)}</span>
                    </div>
                    <div class="model-item-info">
                        <span class="model-item-label">大小：</span>
                        <span class="model-item-value">${formatFileSize(model.size)}</span>
                    </div>
                    <div class="model-item-info">
                        <span class="model-item-label">修改时间：</span>
                        <span class="model-item-value">${formatDateTime(model.modified)}</span>
                    </div>
                </div>
                <div class="model-item-footer">
                    <div class="model-item-path">${escapeHtml(model.path)}</div>
                    <div class="model-item-actions">
                        <button class="algorithm-action-btn edit" onclick="useModelForAlgorithm('${escapeHtml(model.path)}', '${escapeHtml(model.name)}', '${model.type}')">
                            <svg viewBox="0 0 24 24" fill="currentColor">
                                <path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/>
                            </svg>
                            使用
                        </button>
                        <button class="algorithm-action-btn delete" onclick="deleteModel('${escapeHtml(model.path)}')">
                            <svg viewBox="0 0 24 24" fill="currentColor">
                                <path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/>
                            </svg>
                            删除
                        </button>
                    </div>
                </div>
            </div>
        `).join('');
        
    } catch (error) {
        console.error('加载模型列表失败:', error);
        showNotification('加载模型列表失败', 'error');
    }
}

// 使用模型创建算法
function useModelForAlgorithm(modelPath, modelName, algorithmType) {
    closeModelsListModal();
    openCreateModalWithModel(modelPath, modelName, algorithmType);
}

// 删除模型文件
async function deleteModel(modelPath) {
    if (!confirm('确定要删除此模型文件吗？此操作不可恢复。')) {
        return;
    }
    
    try {
        const response = await fetch(`/api/models/${modelPath}`, {
            method: 'DELETE'
        });
        
        if (response.ok) {
            showNotification('模型文件已删除', 'success');
            await loadModelsList();
        } else {
            const error = await response.json();
            showNotification(error.error || '删除失败', 'error');
        }
        
    } catch (error) {
        console.error('删除模型失败:', error);
        showNotification('删除失败', 'error');
    }
}

// 格式化日期时间
function formatDateTime(isoString) {
    const date = new Date(isoString);
    return date.toLocaleString('zh-CN', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
    });
}

// 添加动画样式
const style = document.createElement('style');
style.textContent = `
    @keyframes slideInRight {
        from {
            transform: translateX(100px);
            opacity: 0;
        }
        to {
            transform: translateX(0);
            opacity: 1;
        }
    }
    
    @keyframes fadeOut {
        from {
            opacity: 1;
        }
        to {
            opacity: 0;
        }
    }
`;
document.head.appendChild(style);

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
                <button class="modal-close" onclick="closeAlgorithmDetailModal()">&times;</button>
            </div>
            <div class="modal-body">
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
                        <label>模型路径</label>
                        <span>${escapeHtml(algorithm.model_path || '未设置')}</span>
                    </div>
                    <div class="detail-info-item full-width">
                        <label>目标类别</label>
                        <span>${escapeHtml((algorithm.target_classes || []).join(', ') || '全部')}</span>
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
                <button class="btn btn-secondary" onclick="closeAlgorithmDetailModal()">关闭</button>
                <button class="btn btn-primary" onclick="closeAlgorithmDetailModal(); openEditModal(${algorithm.id});">编辑算法</button>
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