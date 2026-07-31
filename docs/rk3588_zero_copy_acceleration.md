# RK3588 零拷贝硬件加速方案

## 🎯 目标

实现RK3588上的全链路硬件加速，彻底消除CPU预处理瓶颈。

## 📊 当前架构分析

### 现状（CPU预处理）

```
[RTSP流]
    ↓
[FFmpeg拉流] CPU
    ↓
[MPP硬件解码] GPU/NPU
    ↓
[DRM Prime格式] 零拷贝
    ↓
[I420→RGB转换] CPU ⚠️ 瓶颈（耗时30-50ms）
    ↓
[缩放640x640] CPU ⚠️ 瓶颈
    ↓
[归一化] CPU
    ↓
[RKNN NPU推理] NPU
    ↓
[检测结果]
```

**问题**：
- CPU预处理耗时30-50ms，FPS只能达到20-30
- CPU占用高（40-60%）
- 无法发挥NPU性能（理论可达100+ FPS）

---

## ✅ 目标架构（零拷贝优化）

```
[RTSP流]
    ↓
[FFmpeg拉流] CPU
    ↓
[MPP硬件解码] NPU/VPU ⚡ 硬件加速
    ↓
[DRM Prime格式] 零拷贝
    ↓
[RGA硬件预处理] GPU ⚡ 硬件加速
  - NV12→RGB转换（零拷贝）
  - 缩放640x640（零拷贝）
  - 归一化（零拷贝）
    ↓
[DMA缓冲区] 零拷贝
    ↓
[RKNN NPU推理] NPU ⚡ 硬件加速
    ↓
[检测结果]
```

**性能提升**：
- 预处理时间：30-50ms → **2-5ms**（提升10-20倍）
- FPS：20-30 → **100-150**（提升5倍）
- CPU占用：40-60% → **5-15%**（降低70%）

---

## 🔧 技术方案

### 1. MPP硬件解码

**位置**：`src/decoders/video_decoder.py`

**当前状态**：
- 已支持多种硬件加速（DXVA2、CUDA、QSV、VAAPI）
- RK3588 MPP支持有限

**优化方案**：

```python
# 使用FFmpeg的rkmpp硬件加速
hwaccel_preset = {
    'input_options': ['-hwaccel', 'rkmpp'],
    'output_options': [
        '-vf', 'scale=640:640',  # RGA硬件缩放
        '-pix_fmt', 'drm_prime'   # 输出DRM Prime格式
    ]
}
```

**依赖**：
- FFmpeg编译时启用 `--enable-rkmpp`
- 系统已安装 `libmpp.so`

---

### 2. RGA硬件预处理

**位置**：`src/detectors/preprocessor.py`

**当前状态**：
- 已实现 `preprocess_drm_prime()` 方法
- 但使用CPU处理，效率低

**优化方案**：使用RGA零拷贝API

#### 方案A：使用librga库（推荐）

```python
import ctypes
from ctypes import cdll

# 加载RGA库
rga_lib = cdll.LoadLibrary('/usr/lib/librga.so')

def preprocess_rga_zero_copy(drm_fd, width, height, output_width=640, output_height=640):
    """
    使用RGA硬件加速进行零拷贝预处理

    Args:
        drm_fd: DRM文件描述符
        width: 原始宽度
        height: 原始高度
        output_width: 输出宽度
        output_height: 输出高度

    Returns:
        RGB张量（零拷贝）
    """
    # 1. RGA初始化
    rga_ctx = rga_lib.rga_create_context()

    # 2. 配置输入（DRM Prime NV12格式）
    rga_lib.rga_set_input(rga_ctx, drm_fd, width, height, 'NV12')

    # 3. 配置输出（RGB格式，缩放后）
    output_buffer = allocate_dma_buffer(output_width * output_height * 3)
    rga_lib.rga_set_output(rga_ctx, output_buffer, output_width, output_height, 'RGB')

    # 4. 执行RGA转换（硬件加速）
    rga_lib.rga_process(rga_ctx)

    # 5. 返回零拷贝张量
    return np.frombuffer(output_buffer, dtype=np.uint8).reshape(output_height, output_width, 3)
```

#### 方案B：使用FFmpeg RGA滤镜

```python
# 在FFmpeg命令中添加RGA硬件加速
ffmpeg_cmd = [
    'ffmpeg',
    '-hwaccel', 'rkmpp',
    '-i', rtsp_url,
    '-vf', 'scale_rga=640:640:format=rgb24',  # RGA硬件缩放和格式转换
    '-f', 'rawvideo',
    '-pix_fmt', 'rgb24',
    '-'
]
```

---

### 3. RKNN零拷贝推理

**位置**：`src/detectors/backends/rknn.py`

**当前状态**：
- 已支持RKNN NPU推理
- 但数据传输存在拷贝

**优化方案**：使用 `rknn_set_io_mem` 零拷贝API

```python
from rknn.api import RKNN

class RKNNZeroCopyBackend:
    def __init__(self, model_path):
        self.rknn = RKNN()
        self.rknn.load_rknn(model_path)
        self.rknn.init_runtime(target='rk3588', core_mask=3)

        # 分配DMA缓冲区（零拷贝）
        self.input_dma_buffer = allocate_dma_buffer(640 * 640 * 3)
        self.output_dma_buffer = allocate_dma_buffer(1000 * 6 * sizeof(float))

        # 设置零拷贝输入/输出
        self.rknn.set_io_mem(self.input_dma_buffer, self.output_dma_buffer)

    def infer_zero_copy(self, input_tensor):
        """
        零拷贝推理

        Args:
            input_tensor: RGA输出的DMA缓冲区（零拷贝）

        Returns:
            检测结果（零拷贝）
        """
        # 直接使用DMA缓冲区，无拷贝
        outputs = self.rknn.infer(inputs=[input_tensor])
        return outputs
```

---

## 📦 依赖库部署

### 1. 复制RGA库到RK3588

```bash
# 从项目 libs/librga 复制到RK3588
scp libs/librga/Linux/aarch64/librga.so root@192.168.5.15:/usr/lib/
scp libs/librga/include/*.h root@192.168.5.15:/usr/include/
```

### 2. 安装MPP库（如果未安装）

```bash
# RK3588系统通常已预装，检查方法
ssh root@192.168.5.15 "ldconfig -p | grep mpp"
ssh root@192.168.5.15 "ldconfig -p | grep rga"
```

### 3. Python绑定

```bash
# 安装pybind11（用于Python调用C++ RGA库）
pip3 install pybind11
```

---

## 🚀 性能对比

| 方案 | 解码 | 预处理 | 推理 | 总FPS | CPU占用 |
|------|------|--------|------|-------|---------|
| **当前方案** | GPU | CPU | NPU | 20-30 | 40-60% |
| **零拷贝方案** | GPU | GPU | NPU | **100-150** | **5-15%** |

**提升**：
- FPS：提升 **5-7倍**
- CPU占用：降低 **70-80%**
- 延迟：降低 **50-70%**

---

## 📝 实施步骤

### 阶段1：验证硬件支持（已完成）
- ✅ MPP解码库已存在
- ✅ RGA库已存在
- ✅ RKNN运行时已安装

### 阶段2：实现RGA预处理（下一步）
1. 创建 `src/utils/rga_wrapper.py` - RGA Python绑定
2. 修改 `src/detectors/preprocessor.py` - 添加 `preprocess_rga()` 方法
3. 修改 `config/detector_config.yaml` - 添加 `use_rga_preprocess: true`

### 阶段3：实现零拷贝推理
1. 修改 `src/detectors/backends/rknn.py` - 添加零拷贝API
2. 实现DMA缓冲区管理
3. 测试性能提升

### 阶段4：端到端集成
1. 修改 `src/decoders/video_decoder.py` - 输出DRM Prime格式
2. 实现MPP→RGA→RKNN零拷贝流水线
3. 性能测试和优化

---

## 🔗 参考资料

- **RKNN-Toolkit2官方文档**：https://github.com/airockchip/rknn-toolkit2
- **RGA用户手册**：D:\frigate\rknn_model_zoo\3rdparty\librga\README.md
- **零拷贝示例**：D:\frigate\rknn_model_zoo\examples\yolo11\cpp\rknpu2\yolo11_zero_copy.cc
- **MPP编程指南**：瑞芯微官方文档

---

## ⚠️ 注意事项

1. **内存对齐**：DMA缓冲区必须对齐（通常64字节）
2. **并发安全**：多线程访问DMA缓冲区需要加锁
3. **资源释放**：必须手动释放DMA缓冲区，避免内存泄漏
4. **错误处理**：RGA操作失败时回退到CPU预处理

---

## 📞 技术支持

如有问题，请查看：
- 日志文件：`/root/sagesight-ai/logs/system.log`
- RGA状态：`cat /sys/devices/platform/*rga*/load`
- NPU状态：`cat /sys/kernel/debug/rknpu/load`