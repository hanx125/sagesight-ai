# RK3588 SDK 迁移方案：移除 rknn_model_zoo 目录

## Context（背景）

项目根目录下有一个 `rknn_model_zoo` 目录，这是盒子厂家提供的完整 SDK（包含 C++ 示例、模型、数据集、第三方库等）。用户要求不将整个 SDK 打包在项目中，只迁移实际使用的部分。

### 现状分析

**项目已独立实现了所有 RKNN 功能，不依赖 rknn_model_zoo 的任何 Python 代码：**

| 项目文件 | 功能 | 依赖 |
|----------|------|------|
| [src/detectors/backends/rknn.py](file:///e:/sagesight-ai/src/detectors/backends/rknn.py) | RKNN 检测器后端 | 只依赖 `src/utils/rknn_executor` 和 pip 包 |
| [src/utils/rknn_executor.py](file:///e:/sagesight-ai/src/utils/rknn_executor.py) | RKNN 推理引擎 | 只依赖 pip 包 `rknnlite` / `rknn` |
| [src/utils/rga_wrapper.py](file:///e:/sagesight-ai/src/utils/rga_wrapper.py) | RGA 硬件加速 | 从系统路径 `/usr/lib/librga.so` 加载 |
| [src/system/system_api.py:413-470](file:///e:/sagesight-ai/src/system/system_api.py#L413-L470) | NPU 信息采集 | 读取 `/sys/kernel/debug/rknpu/` |

**项目中对 rknn_model_zoo 的引用只有 2 处（都是部署提示，非代码依赖）：**

1. [scripts/test_rga_acceleration.py:92](file:///e:/sagesight-ai/scripts/test_rga_acceleration.py#L92) — print 语句提示用户从 SDK 复制 librga.so
2. [docs/rk3588_zero_copy_acceleration.md:212-213](file:///e:/sagesight-ai/docs/rk3588_zero_copy_acceleration.md#L212-L213) — 文档中的部署命令

**结论：** rknn_model_zoo 中唯一被"使用"的是 `3rdparty/librga` 的 `.so` 库文件和头文件（部署到 RK3588 时需要）。YOLO 示例代码（yolov5.py、yolo11.py 等）只是参考，项目已有自己的实现。

---

## 实现方案

### 1. 迁移 librga 库文件到项目 `libs/librga/` 目录

从 `rknn_model_zoo/3rdparty/librga/` 复制以下文件到 `libs/librga/`：

```
libs/librga/
├── Linux/
│   ├── aarch64/
│   │   └── librga.so          # RK3588 (64位ARM)
│   └── armhf/
│       └── librga.so          # RK3566/RK3568 (32位ARM)
└── include/
    ├── RgaApi.h
    ├── rga.h
    └── im2d.h                 # RGA API 核心头文件（rga_wrapper.py 可能需要）
```

**不保留的部分**（C++ 静态库、Android 库、其他头文件）：
- `*.a` 静态库（项目用 Python，不需要 C++ 静态库）
- `Android/` 目录（项目部署在 Linux 上）
- `Linux/armhf_uclibc/`（不常用平台）

### 2. 修改 scripts/test_rga_acceleration.py 的路径引用

[scripts/test_rga_acceleration.py:92](file:///e:/sagesight-ai/scripts/test_rga_acceleration.py#L92)：

```python
# 修改前
print("  scp D:\\frigate\\rknn_model_zoo\\3rdparty\\librga\\Linux\\aarch64\\librga.so root@192.168.5.15:/usr/lib/")

# 修改后
print("  scp libs/librga/Linux/aarch64/librga.so root@192.168.5.15:/usr/lib/")
```

### 3. 修改 docs/rk3588_zero_copy_acceleration.md 的路径引用

[docs/rk3588_zero_copy_acceleration.md:212-213](file:///e:/sagesight-ai/docs/rk3588_zero_copy_acceleration.md#L212-L213)：

```bash
# 修改前
scp D:\frigate\rknn_model_zoo\3rdparty\librga\Linux\aarch64\librga.so root@192.168.5.15:/usr/lib/
scp D:\frigate\rknn_model_zoo\3rdparty\librga\include\*.h root@192.168.5.15:/usr/include/

# 修改后
scp libs/librga/Linux/aarch64/librga.so root@192.168.5.15:/usr/lib/
scp libs/librga/include/*.h root@192.168.5.15:/usr/include/
```

### 4. 删除 rknn_model_zoo 目录

确认以上迁移完成后，删除整个 `rknn_model_zoo` 目录。

---

## 涉及文件清单

| 操作 | 文件/目录 | 说明 |
|------|-----------|------|
| 新建 | `libs/librga/Linux/aarch64/librga.so` | 从 SDK 复制 |
| 新建 | `libs/librga/Linux/armhf/librga.so` | 从 SDK 复制 |
| 新建 | `libs/librga/include/*.h` | 从 SDK 复制核心头文件 |
| 修改 | `scripts/test_rga_acceleration.py` | 更新 librga.so 路径引用 |
| 修改 | `docs/rk3588_zero_copy_acceleration.md` | 更新部署命令路径 |
| 删除 | `rknn_model_zoo/` | 删除整个 SDK 目录 |

---

## 验证方法

1. **确认 librga 文件迁移成功：**
   - 检查 `libs/librga/Linux/aarch64/librga.so` 存在
   - 检查 `libs/librga/include/` 包含核心头文件

2. **确认无残留引用：**
   - 全局搜索 `rknn_model_zoo`，确认项目中（排除已删除的目录）无任何引用

3. **确认项目代码不受影响：**
   - Windows 上启动服务（RKNN/RGA 不会加载，只打印"未安装"提示）
   - 确认 ONNX CUDA 后端正常工作
   - 确认检测器工厂能正确识别 RKNN 后端不可用（Windows 平台）

4. **确认 rknn_model_zoo 已删除：**
   - 确认 `e:\sagesight-ai\rknn_model_zoo` 目录不存在
