// ========================================
// 视频文件上传功能
// ========================================

let selectedVideoFiles = [];  // 支持多文件
let isBatchMode = false;  // 批量模式标志
let maxFileSizeMB = 100;  // 默认文件大小限制100MB

/**
 * 打开视频文件上传模态框
 */
async function openVideoFileModal() {
    // 重置表单
    document.getElementById('videoFileForm').reset();
    document.getElementById('videoFilePath').value = '';
    document.getElementById('fileListContainer').style.display = 'none';
    document.getElementById('fileList').innerHTML = '';
    selectedVideoFiles = [];
    isBatchMode = false;
    document.getElementById('batchUploadMode').checked = false;

    // 加载目录列表
    await loadDirectoriesForVideoFile();

    // 加载文件大小限制配置
    await loadFileSizeLimit();

    // 显示模态框
    document.getElementById('videoFileModal').classList.add('show');
}

/**
 * 关闭视频文件上传模态框
 */
function closeVideoFileModal() {
    document.getElementById('videoFileModal').classList.remove('show');
    // 清空已选择的文件
    selectedVideoFiles = [];
    isBatchMode = false;
}

/**
 * 加载文件大小限制配置
 */
async function loadFileSizeLimit() {
    try {
        const response = await fetch('/api/system/config');
        if (response.ok) {
            const config = await response.json();
            maxFileSizeMB = config.video_file_max_size_mb || 100;
            document.getElementById('fileSizeLimit').textContent = `${maxFileSizeMB}MB`;
        }
    } catch (error) {
        console.log('使用默认文件大小限制:', maxFileSizeMB);
    }
}

/**
 * 切换批量上传模式
 */
function toggleBatchMode() {
    isBatchMode = document.getElementById('batchUploadMode').checked;

    // 更新文件输入的multiple属性
    const fileInput = document.getElementById('videoFileInput');
    fileInput.multiple = isBatchMode;

    // 更新提示文本
    const hint = document.getElementById('deviceNameHint');
    if (isBatchMode) {
        hint.textContent = '批量上传时作为设备名称前缀，自动添加序号';
    } else {
        hint.textContent = '作为虚拟设备的显示名称';
    }

    // 清空已选择的文件
    clearSelectedFiles();
}

/**
 * 选择视频文件
 */
function selectVideoFile() {
    document.getElementById('videoFileInput').click();
}

/**
 * 处理视频文件选择
 */
function handleVideoFileSelect(event) {
    const files = Array.from(event.target.files);

    if (files.length === 0) {
        return;
    }

    // 文件大小验证
    const oversizedFiles = files.filter(file => file.size > maxFileSizeMB * 1024 * 1024);
    if (oversizedFiles.length > 0) {
        showNotification(`以下文件超过${maxFileSizeMB}MB限制：${oversizedFiles.map(f => f.name).join(', ')}`, 'error');
        return;
    }

    // 更新已选择的文件列表
    if (isBatchMode) {
        selectedVideoFiles = files;
        updateFileListDisplay();
        document.getElementById('videoFilePath').value = `已选择 ${files.length} 个文件`;
    } else {
        selectedVideoFiles = [files[0]];
        document.getElementById('videoFilePath').value = files[0].name;

        // 自动生成设备名称
        const deviceNameInput = document.getElementById('videoFileDeviceName');
        if (!deviceNameInput.value) {
            deviceNameInput.value = `视频设备-${files[0].name.replace(/\.[^/.]+$/, '')}`;
        }
    }
}

/**
 * 更新文件列表显示
 */
function updateFileListDisplay() {
    if (!isBatchMode || selectedVideoFiles.length === 0) {
        document.getElementById('fileListContainer').style.display = 'none';
        return;
    }

    const fileListDiv = document.getElementById('fileList');
    fileListDiv.innerHTML = selectedVideoFiles.map((file, index) => {
        const sizeMB = (file.size / (1024 * 1024)).toFixed(2);
        return `
            <div style="display: flex; justify-content: space-between; align-items: center; padding: 8px; border-bottom: 1px solid #f0f0f0;">
                <div>
                    <strong>${index + 1}.</strong> ${escapeHtml(file.name)}
                    <span style="color: #888; font-size: 12px; margin-left: 10px;">${sizeMB}MB</span>
                </div>
                <button type="button" class="btn btn-sm btn-danger" onclick="removeFile(${index})">删除</button>
            </div>
        `;
    }).join('');

    document.getElementById('fileListContainer').style.display = 'block';

    // 自动生成批量设备名称前缀
    const deviceNameInput = document.getElementById('videoFileDeviceName');
    if (!deviceNameInput.value) {
        deviceNameInput.value = '批量视频设备';
    }
}

/**
 * 移除单个文件
 */
function removeFile(index) {
    selectedVideoFiles.splice(index, 1);
    updateFileListDisplay();

    if (selectedVideoFiles.length === 0) {
        document.getElementById('videoFilePath').value = '';
    } else {
        document.getElementById('videoFilePath').value = `已选择 ${selectedVideoFiles.length} 个文件`;
    }
}

/**
 * 清空已选择的文件
 */
function clearSelectedFiles() {
    selectedVideoFiles = [];
    document.getElementById('videoFilePath').value = '';
    document.getElementById('fileListContainer').style.display = 'none';
    document.getElementById('fileList').innerHTML = '';
}

/**
 * 加载目录列表
 */
async function loadDirectoriesForVideoFile() {
    try {
        const response = await fetch('/api/directories');
        const directories = await response.json();

        const select = document.getElementById('videoFileDirectory');
        select.innerHTML = '<option value="">无目录</option>';

        directories.forEach(dir => {
            const option = document.createElement('option');
            option.value = dir.id;
            option.textContent = dir.name;
            select.appendChild(option);
        });
    } catch (error) {
        console.error('加载目录列表失败:', error);
    }
}

/**
 * 上传视频文件并添加为虚拟设备
 */
async function uploadVideoFile() {
    // 验证输入
    if (selectedVideoFiles.length === 0) {
        showNotification('请选择视频文件', 'error');
        return;
    }

    const deviceName = document.getElementById('videoFileDeviceName').value.trim();
    if (!deviceName) {
        showNotification('请输入设备名称', 'error');
        return;
    }

    // 显示上传进度
    showNotification('正在上传视频文件...', 'info');

    try {
        let successCount = 0;
        let failCount = 0;

        // 批量上传
        for (let i = 0; i < selectedVideoFiles.length; i++) {
            const file = selectedVideoFiles[i];

            // 构建表单数据
            const formData = new FormData();
            formData.append('video_file', file);
            formData.append('device_name', isBatchMode ? `${deviceName}-${i + 1}` : deviceName);
            formData.append('description', document.getElementById('videoFileDescription').value.trim());
            formData.append('directory_id', document.getElementById('videoFileDirectory').value || '');
            formData.append('batch_index', i.toString());
            formData.append('batch_total', selectedVideoFiles.length.toString());

            try {
                const response = await fetch('/api/cameras/video-file', {
                    method: 'POST',
                    body: formData
                });

                const result = await response.json();

                if (response.ok) {
                    successCount++;
                    console.log(`文件上传成功: ${file.name}`);
                } else {
                    failCount++;
                    console.error(`文件上传失败: ${file.name}`, result.error);
                }
            } catch (error) {
                failCount++;
                console.error(`文件上传异常: ${file.name}`, error);
            }
        }

        // 显示结果
        if (successCount > 0 && failCount === 0) {
            showNotification(`成功添加 ${successCount} 个视频设备`, 'success');
            closeVideoFileModal();
        } else if (successCount > 0 && failCount > 0) {
            showNotification(`成功添加 ${successCount} 个设备，失败 ${failCount} 个`, 'warning');
            closeVideoFileModal();
        } else {
            showNotification('所有文件上传失败，请重试', 'error');
        }

        // 刷新摄像机列表
        if (typeof loadCameras === 'function') {
            loadCameras();
        }

    } catch (error) {
        console.error('上传视频文件失败:', error);
        showNotification('上传失败，请重试', 'error');
    }
}

/**
 * HTML转义
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}