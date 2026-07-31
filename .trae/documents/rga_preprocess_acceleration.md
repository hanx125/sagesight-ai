# RGA 预处理硬件加速实现方案

## 背景

当前项目在 RK3588 上的视频处理流程中，**预处理环节（I420→RGB + 缩放）仍由 CPU 完成**，是性能瓶颈（1080p 约需 30-50ms）。虽然已配置 `preset-rk-h264` 硬件解码和 RKNN NPU 推理，但预处理用 CPU 导致整体加速链路断裂：

```
视频流 → [VPU硬件解码 ✅] → I420帧 → [CPU预处理 ❌ 瓶颈] → [NPU推理 ✅] → 结果
```

本次实现将 RGA（Raster Graphic Acceleration）硬件加速集成到预处理流程中，实现完整的硬件加速链路，预处理耗时预计降至 2-5ms。

## 实现位置

**单文件修改**：[src/utils/image.py](file:///e:/sagesight-ai/src/utils/image.py)

RGA 库加载逻辑已在 image.py:35-73 就绪（`_RGA_AVAILABLE`、`is_rga_available()`），本次在此基础上添加完整的 RGA 调用实现。

## 技术方案

### RGA API 调用链

使用 im2d API 的 C 接口，通过 ctypes 调用 librga.so：

```
原始I420帧 ──improcess(srect=裁剪区域, drect=输出区域, usage=YUV→RGB)──→ RGB/BGR输出帧
                                      ↓
                         releasebuffer_handle ×2 释放内核 handle
```

- `wrapbuffer_virtualaddr_t` — 包装 numpy 数组内存为 RGA 缓冲区（内部调用 importbuffer 注册内核资源）
- `improcess` — 通用 blit 接口（im2d_single.h:479-480），一步完成裁剪+缩放+颜色转换
- `releasebuffer_handle` — 释放 `wrapbuffer_virtualaddr_t` 注册的内核 handle，**必须配对调用**

> **架构选择**：使用 `improcess` 单步模式而非两步 `imcrop_t` + `imcvtcolor_t`。
> - 单步优势：只需 1 次 RGA 调用、2 个缓冲区（src+dst），无中间 I420 缓冲区
> - 两步劣势：2 次 RGA 调用、3 个缓冲区（src+crop+dst），需分配中间 I420 缓冲区
>
> `imcrop`/`imcvtcolor` 在 C 接口中是宏（im2d_single.h:611/582），真正的导出符号是 `imcrop_t`/`imcvtcolor_t`。ctypes 只能绑定真实导出符号。`improcess` 是直接导出的 C 函数，更适合 ctypes 调用。

### 关键数据结构E（ctypes 定义）

参照 [libs/librga/include/im2d_type.h](file:///e:/sagesight-ai/libs/librga/include/im2d_type.h) 和 [rga.h](file:///e:/sagesight-ai/libs/librga/include/rga.h)：

```python
# im_rect (im2d_type.h:262-267): {x, y, width, height}
# im_colorkey_range (im2d_type.h:269-272): {max, min}
# im_nn_t (im2d_type.h:273-282): {scale_r, scale_g, scale_b, offset_r, offset_g, offset_b}
# rga_buffer_t (im2d_type.h:285-307): 含 vir_addr/phy_addr/fd/width/height/wstride/hstride/format 等
```

### 格式常量（rga.h:38-141）

```python
RK_FORMAT_RGB_888      = 0x2 << 8  # 0x200
RK_FORMAT_BGR_888      = 0x7 << 8  # 0x700
RK_FORMAT_YCbCr_420_P  = 0xb << 8  # 0xb00  (I420)
IM_STATUS_SUCCESS      = 1
IM_YUV_TO_RGB_BT601_LIMIT = 1  # 标准YUV→RGB
```

## 实现步骤

### 步骤1：添加 RGA ctypes 结构体和 API 绑定

在 image.py 的 RGA 初始化代码之后（约第73行），添加：

1. ctypes 结构体定义：`_im_rect`, `_im_colorkey_range`, `_im_nn_t`, `_rga_buffer_t`
2. API 函数绑定（仅当 `_RGA_AVAILABLE` 时）：
   - `_wrapbuffer_virtualaddr(vir_addr, width, height, wstride, hstride, format)` → 返回 rga_buffer_t（绑定 `wrapbuffer_virtualaddr_t`）
   - `_improcess(src, dst, pat, srect, drect, prect, usage)` → 返回 IM_STATUS（绑定 `improcess`，7 参数）
   - `_releasebuffer_handle(handle)` → 释放内核资源（绑定 `releasebuffer_handle`）

### 步骤2：实现 RGA 预处理核心函数

添加 `_rga_preprocess_i420(frame, region, output_size, output_format)` 函数：

```
输入: frame (I420 numpy数组 1.5*H×W), region (x1,y1,x2,y2), output_size (W,H), output_format ('rgb'/'bgr')
处理:
  1. 解析原始帧尺寸 (height = frame.shape[0]//3*2, width = frame.shape[1])
  2. region 偶数对齐（I420 的 UV 平面 2x2 子采样要求）
  3. np.ascontiguousarray 确保内存连续
  4. 分配输出 RGB/BGR 缓冲区 (out_h, out_w, 3)（无需中间 I420 缓冲区）
  5. wrapbuffer_virtualaddr_t 包装两个缓冲区（注册内核 handle）
  6. improcess(src, dst, zero_buf, srect, drect, zero_rect, usage) — 一步完成裁剪+缩放+颜色转换
  7. finally 块中 releasebuffer_handle 释放两个内核 handle
输出: RGB/BGR numpy 数组
异常: RGA 调用失败时-抛出 RuntimeError，调用方回退到 CPU
```

### 步骤3：修改 yuv_region_2_rgb / yuv_region_2_bgr 启用 RGA 分支

将当前的注释分支替换为实际调用：

```python
def yuv_region_2_rgb(frame, region):
    try:
        if _RGA_AVAILABLE:
            try:
                return _rga_preprocess_i420(frame, region, (640, 640), 'rgb')
            except Exception as e:
                logger.warning(f"[RGA] 预处理失败，回退到CPU: {e}")
        # CPU 回退
        yuv_cropped_frame = yuv_crop_and_resize(frame, region)
        return cv2.cvtColor(yuv_cropped_frame, cv2.COLOR_YUV2RGB_I420)
    except:
        ...
```

`yuv_region_2_bgr` 同理，output_format 传 `'bgr'`，格式用 `RK_FORMAT_BGR_888`。

### 步骤4：更新测试脚本

[scripts/test_rga_acceleration.py](file:///e:/sagesight-ai/scripts/test_rga_acceleration.py) 包含三项测试：
1. **可用性测试** — 检查 librga.so 是否加载成功
2. **性能与正确性测试** — 对比 RGA 与 CPU 的耗时、输出形状、像素差异-差异（RGB + BGR 双格式）
3. **资源泄漏压力测试** — 连续处理 500 帧，验证 `releasebuffer_handle` 是否正常释放内核 handle

## 关键设计决策

1. **虚拟地址方式**：使用 `wrapbuffer_virtualaddr_t`（非 DMA 零拷贝），实现简单且已能满足性能需求。零拷贝 DMA 方案可后续优化。

2. **单步 improcess 调用**：使用 `improcess` 通用 blit 接口一步完成裁剪+缩放+颜色转换，比两步 `imcrop_t` + `imcvtcolor_t` 更高效（省去中间缓冲区分配和两次 RGA 调用）。仅分配 src+dst 两个缓冲区，handle 从 3 个减至 2 个。

3. **内核 handle 释放**：librga 1.x 的 `wrapbuffer_virtualaddr_t` 内部调用 `importbuffer_virtualaddr` 注册内核资源，返回的 `rga_buffer_t.handle` 必须用 `releasebuffer_handle` 释放。本实现在 finally 块中统一释放两个 handle，避免长时间运行的视频服务泄漏内核资源。

4. **绑定 improcess 直接导出符号**：`improcess` 在 im2d_single.h:479-480 直接导出为 C 函数（非宏），ctypes 可直接绑定。这比绑定 `imcrop_t`/`imcvtcolor_t` 更简洁。

5. **自动 CPU 回退**：RGA 调用失败（格式不支持、内存错误等）时自动回退到原有的 CPU 路径，保证健壮性。

6. **region 偶数对齐**：I420 格式的 UV 平面是 2x2 子采样，裁剪坐标必须是偶数。对齐时向外扩展（不缩小 region）。

7. **输出尺寸固定 640x640**：与模型输入尺寸一致（`yuv_crop_and_resize` 也是输出固定 size）。

## 验证方法

**在 RK3588 盒子上测试**（Windows 上无法加载 librga.so）：

1. 确认 librga.so 已部署：
   ```bash
   scp libs/librga/Linux/aarch64/librga.so root@<盒子IP>:/usr/lib/
   ldconfig
   ```

2. 运行测试脚本：
   ```bash
   python scripts/test_rga_acceleration.py
   ```
   预期输出：`✅ RGA硬件加速已启用`，转换耗时 < 5ms

3. 启动检测任务，查看日志确认 RGA 启用：
   ```
   [RGA] 成功加载librga.so: ...
   [RGA] im2d API 绑定成功（improcess 单步模式）
   ```
   预处理耗时显著下降，无 "回退到CPU" 警告。

4. 确认检测结果正确：RGA 预处理的输出与 CPU 预处理的检测框一致（坐标对齐可能差 1-2 像素，不影响检测）。

## 文件清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | [src/utils/image.py](file:///e:/sagesight-ai/src/utils/image.py) | 添加 RGA ctypes 绑定 + 预处理函数 + 启用分支 |
| 更新 | [scripts/test_rga_acceleration.py](file:///e:/sagesight-ai/scripts/test_rga_acceleration.py) | 可用性+性能+正确性+资源泄漏测试 |
