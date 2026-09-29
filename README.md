# 睿观 SageSight AI 推理分析平台

[English](README_EN.md) | 中文

## 项目简介

睿观 SageSight AIOT项目是一个包含：睿观 SageSight cloud 云平台（负责物联网、业务系统、租户管理）、睿观 SageSight edge 边缘设备（负责视频采集、分析、存储）、睿观 SageSight lab 标注训练平台和睿观 SageSight AI 推理分析平台组成，本项目是睿观 SageSight AI 推理分析平台，一款智能视频分析平台，基于深度学习技术实现实时目标检测、跟踪和事件分析。支持多平台硬件加速（NVIDIA GPU、CPU、RK3588 NPU），适用于安防监控、智能交通、工业检测等多种小场景，其中流媒体接入、PTZ、对讲只是为了满足小场景需求单用该项目额外添加的功能，不要要求太高，实际使用中只需要任务管理和推理，由边缘平台管理的，我不认识代码，都是AI编写，重要事情说三遍！

## 核心功能

### 1. 多路视频实时分析
- **多任务并发**：同时运行多个检测任务，每个任务可关联多路摄像机通道
- **多路并发处理**：单任务内多路摄像机并行解码与检测，共享同一算法模型
- **硬件加速解码**：支持 NVIDIA CUDA、Intel QSV、RK3588 MPP 等硬件加速解码
- **灵活的视频源**：支持 RTSP 实时流、本地视频文件（mp4/avi/mkv 等）、HTTP 流
- **多协议接入**：支持 ONVIF、RTSP等接入协议，跨网段扫描

### 2. 智能目标检测与跟踪
- **实时目标检测**：基于 YOLOv8/YOLO11 模型，实现高精度目标检测
- **多推理后端**：支持 onnx_cuda、onnx_cpu、onnx_tensorrt、tensorrt、rknn 五种后端
- **全局检测进程池**：检测器进程池架构，多任务共享 GPU 资源，避免重复加载模型
- **多目标跟踪**：采用 Norfair 算法，实现稳定的多目标跟踪与轨迹记录
- **运动检测**：帧差法 + 高斯背景建模，精确检测运动目标，可独立开关
- **类别过滤**：target_classes 动态配置检测目标类别，不指定时检测所有类别

### 3. 区域检测（Zone Detection）
- **多边形区域绘制**：在画面上绘制任意多边形检测区域（归一化坐标存储）
- **必需区域过滤**：对象须至少进入一个必需区域才触发事件，有效减少误报
- **进入/离开事件**：目标进入或离开区域时触发事件
- **徘徊检测**：目标在区域内停留超过阈值时间触发徘徊告警
- **对象白名单**：每个区域可单独配置关注的对象类型
- **进入惯性过滤**：连续 N 帧确认进入区域，过滤边缘抖动

### 4. 人员统计（People Counting）
- **两种计数模式**：单机画线（一台相机上画分界线，目标跨越线判定进出）、多机进出（入口机 +1 / 出口机 -1，多台相机协同统计同一区域）
- **分层配置存储**：单机画线保存于通道级 `counting_lines`（含区域级报警阈值），多机进出保存于任务级 `counting_zones` 表
- **唯一判定依据**：任务事件类型选择「人员统计」时启用；命中人员统计的任务会抑制常规跟踪/区域事件，只产出计数事件
- **按天计数**：计数状态按（任务 + 区域 + 通道 + 日期）持久化，跨天自动归零，历史数据保留
- **区域报警阈值**：每个计数区域可单独设置人数阈值，超限触发报警与语音播报
- **驾驶舱可视化**：按（任务, 区域）聚合展示进入 / 离开 / 当前人数，区域较多时横向滚动展示

### 5. 报警语音播报
- **服务端播放**：报警触发时由服务器本地音频设备播放预置 MP3（`static/alarm_mp3/`）
- **多级配置**：播放参数优先级为 任务级 > 全局配置 > 默认值（MP3 文件、循环次数、循环间隔、音量）
- **打断防重叠**：同一报警正在播报时忽略重复触发；不同报警抢占并立即终止上一路播放，避免多目标同时超限造成语音重叠
- **预置文件管理**：系统管理页支持 MP3 上传、删除与试听

### 6. 事件智能分析与输出
- **事件生命周期**：完整管理 track_start / track_update / track_end 事件流程
- **多种事件类型**：跟踪事件、区域入侵、徘徊告警等
- **事件 GIF 动画**：基于帧缓存生成事件过程 GIF，支持 pre/post 时间窗口、轨迹绘制
- **三通道存储**：本地、MinIO、FTP 三种存储方式独立开关（图片与 GIF 分开控制）
- **异步 I/O 输出**：图片上传、MQTT 推送、数据库保存等 I/O 操作线程池异步执行，不阻塞检测管道
- **事件过滤**：按置信度阈值、目标类别过滤事件
- **关键帧采集**：支持 full / cropped / keyframe / periodic 四种采集策略
- **图片压缩**：JPEG 质量与尺寸可配置，降低存储与带宽占用
- **事件自动清理**：后台线程按保留天数定期清理过期事件及对应存储文件（本地/MinIO/FTP）
- **LLM 事件描述**：可选接入大模型为事件生成自然语言描述（支持图片输入）
- **LLM 事件去重**：可选接入大模型对语义重复的事件做二次去重

### 7. 电视墙实时监控
- **多路视频墙**：支持多路视频同时显示，实时查看监控画面
- **AI 视频流**：实时推送叠加检测框、跟踪框、轨迹、区域的 AI 分析画面
- **实时事件列表**：右侧显示最新事件，点击查看详细信息
- **图片/GIF 切换**：事件详情支持关键帧图片和 GIF 动画切换显示
- **语音双向对讲**：基于 ONVIF Audio Back Channel 协议实现跨品牌语音双向对讲
- **声音开关**：工具栏全局声音开关一键控制全部画面，每路画面另有单路声音开关（默认静音，状态本地记忆）
- **计数分界线显示**：可在 AI 画面上叠加人员统计分界线；多机进出模式在画面左下角标注该路为「进」/「出」（任务配置 →「显示配置」开关）
- **人员统计图表**：驾驶舱底部按区域展示进入 / 离开 / 当前人数，可横向滚动查看多个区域

### 8. 流媒体管理
- **摄像机管理**：支持 ONVIF 协议自动发现、网络扫描和设备管理
- **PTZ 控制**：支持云台方向控制、预置位保存/调用、变倍变焦（连续/相对/绝对移动）
- **PTZ 自动跟踪**：基于跟踪目标位置自动驱动云台跟随（autotrack）
- **巡航路径**：支持预置位巡航路径配置与执行
- **设备通道池**：支持设备通道池管理，一个通道可被多任务复用，通道级 Zone 配置
- **目录管理**：支持摄像机目录树结构，便于分类管理

### 9. 语音对讲
- **ONVIF Audio Back Channel**：基于 ONVIF 标准协议实现跨品牌双向语音对讲
- **品牌兼容**：支持海康威视、大华、宇视、天地伟业等 ONVIF 认证摄像机
- **音频增强**：支持音量增益、音频编码（PCMU/PCMA）等参数配置

### 10. AI 任务管理
- **多模块配置**：支持解码器、检测器、跟踪器、运动检测、事件输出独立配置
- **功能开关**：运动检测、跟踪功能可独立开关
- **实时启停**：支持任务实时启动和停止，灵活控制分析进程
- **性能监控**：实时显示解码、检测、跟踪各阶段耗时、FPS、检测目标数等指标
- **配置模板**：支持算法配置模板，快速创建新任务
- **配置分层**：解码、检测、跟踪、运动、事件输出、显示等参数由 `config/*.yaml` 全局提供（所有任务一致），任务表只保存任务级覆盖值（检测阈值、各类开关、计数区域等独立字段）
- **配置对话框**：任务级的事件输出开关、显示叠加开关、语音播报参数等统一在任务「配置」对话框维护
- **计数区域配置**：支持单机画线（按住鼠标拖动绘制分界线）与多机进出（指定入口机 / 出口机）两种模式，并可按区域设置人数报警阈值

### 11. 算法管理
- **算法配置**：支持 YOLOv8/YOLO11 等多种算法配置
- **模型管理**：支持模型文件上传和管理
- **一模型一标签**：每个模型对应独立标签文件，动态加载，且支持中文标签
- **示例图片**：算法配置支持示例图片展示

### 12. 系统管理
- **硬件监控**：实时监控 CPU、内存、GPU 使用情况
- **解码器配置**：支持解码器硬件加速方式配置
- **流媒体配置**：支持 ZLMediaKit 流媒体服务器配置
- **远程存储配置**：支持 MinIO、FTP 远程存储配置
- **事件清理配置**：支持事件保留天数、清理间隔配置
- **报警语音管理**：预置 MP3 上传 / 删除 / 试听，全局播报开关与默认参数配置
- **大模型配置**：LLM 服务配置（API 地址、密钥、模型、是否支持图片）、默认配置指定与连通性测试、LLM 总开关
- **节点管理**：向边缘平台注册、心跳保活，上报 CPU / 内存 / 磁盘 / GPU / NPU 及工作负载指标（`config/system_config.yaml` 的 `node_management` 段）

### 13. 实时通信
- **Socket.IO 实时推送**：事件信息实时推送到前端，无需刷新页面（namespace: `/ws`）
- **原生 WebSocket 通道**：提供标准 WebSocket 端点（`/ws/raw`），供第三方系统接入
- **MQTT 事件推送**：事件通过 MQTT 协议推送到 EMQX/驾驶舱

## 功能截图展示

### 登录页面
![登录页面](docs/images/登录页面.png)
简洁现代的登录界面，支持用户认证和安全登录。

### 驾驶舱（电视墙）

**暗主题**
![驾驶舱-暗主题](docs/images/驾驶舱-暗主题.png)

**亮主题**
![驾驶舱-亮主题](docs/images/驾驶舱-亮主题.png)

多路视频实时监控，右侧显示最新事件列表，支持实时查看和分析结果。

### 任务管理
![任务管理](docs/images/任务管理.jpg)
灵活的AI任务管理界面，支持任务的创建、编辑、启停和性能监控。

### 任务配置
![任务配置](docs/images/任务配置.png)
详细的任务配置对话框，支持解码器、检测器、跟踪器、事件输出等多模块配置。

### 区域配置
![区域配置](docs/images/区域配置.png)
区域检测配置界面，支持在画面上绘制多边形检测区域，可设置必需区域、徘徊检测、对象类型白名单等规则，实现精准区域监控。

### 事件管理
![事件管理](docs/images/事件管理.jpg)
事件查询和管理界面，支持按任务、类型、类别筛选，查看事件详情和GIF动画。

### 事件详情
![事件详情](docs/images/事件详情.png)
事件详情对话框，支持关键帧图片和GIF动画切换显示，完整查看事件信息。

### 播放对话框
![播放对话框](docs/images/播放对话框.jpg)
视频播放对话框，支持实时流播放和语音双向对讲功能。

### 流媒体设备管理
![流媒体设备管理](docs/images/流媒体设备管理.jpg)
摄像机管理界面，支持ONVIF自动发现、PTZ控制、设备通道池管理。

### 算法库
![算法库](docs/images/算法库.jpg)
算法配置管理界面，支持YOLOv8/YOLO11等算法配置，模型管理和类别配置。

### 系统管理
![系统管理](docs/images/系统管理.png)
系统配置和监控界面，支持硬件监控、解码器配置、流媒体服务器配置。

## 技术特性

### 多平台硬件加速
- **NVIDIA GPU**：CUDA、TensorRT 加速推理，RTX 2070 达到 30+ FPS
- **CPU 推理**：ONNX Runtime 纯 CPU 推理，适合无 GPU 环境
- **RK3588 NPU**：RKNN 加速推理，适合嵌入式边缘计算设备

### 高性能架构
- **零拷贝传输**：共享内存零拷贝帧传输，降低 CPU 开销
- **环形缓冲区**：环形缓冲区 + 索引队列架构，实现高效帧缓存
- **多进程架构**：解码、检测、跟踪、事件处理独立进程，充分利用多核 CPU
- **全局检测进程池**：检测器进程池统一管理 GPU 资源，多任务共享模型实例

### 统一日志管理
- **多进程日志收集**：子进程通过 QueueHandler 将日志发送到主进程 QueueListener，统一写入文件
- **日志轮转**：按大小自动轮转，支持备份数与控制台输出配置
- **单一配置源**：日志级别、格式、文件路径等统一从 `system_config.yaml` 读取

## 快速启动

### 1. 环境准备

**Python 环境**：
- Python 3.11+
- pip 包管理器

**硬件环境（可选）**：
- NVIDIA GPU：CUDA 12.x + CuDNN 8.9+
- RK3588 平台：RKNN Toolkit2

### 2. 安装依赖

**NVIDIA GPU 平台**：
```bash
pip install -r requirements.txt
```

**CPU 平台**：
```bash
# 安装依赖后，将 requirements.txt 中的 onnxruntime-gpu 替换为 onnxruntime
pip install -r requirements.txt
```

**RK3588 平台**：
```bash
pip install -r requirements.txt
# 需手动安装 rknn-toolkit2（瑞芯微官方SDK）
```

### 3. 配置文件

**核心配置文件**（`config/` 目录）：
- `system_config.yaml`：系统配置（Web服务端口、硬件加速、存储配置）
- `detector_config.yaml`：检测器配置（推理后端、阈值）
- `decoder_config.yaml`：解码器配置（硬件加速）
- `event_output.yaml`：事件输出配置（GIF、存储方式）
- `tracker_config.yaml`：跟踪器配置（Norfair 参数）
- `motion_config.yaml`：运动检测配置
- `display_config.yaml`：显示配置（AI 画面叠加开关、计数分界线开关）

> 说明：以上均为全局配置，所有任务共用；任务级可调参数（检测阈值、各类开关、计数区域等）以独立字段保存在任务表中，通过任务「配置」对话框维护。

**推理后端选择**：
- `onnx_cuda`：CUDA 加速推理（推荐 NVIDIA GPU）
- `onnx_cpu`：纯 CPU 推理（无 GPU 环境）
- `onnx_tensorrt`：TensorRT EP 加速（高性能）
- `tensorrt`：直接加载 TensorRT Engine
- `rknn`：RKNN NPU 推理（RK3588 平台）

### 4. 启动服务

**启动 Web 服务**：
```bash
python run.py
```

**访问平台**：
- 打开浏览器访问：`http://localhost:5556`
- 默认用户名：`admin`
- 默认密码：`admin123`

### 5. 快速配置

**第一步：配置摄像机**
- 进入"流媒体"页面
- 点击"ONVIF发现"自动搜索网络摄像机
- 或手动添加 RTSP 流地址

**第二步：配置算法**
- 进入"算法管理"页面
- 点击"添加算法配置"
- 选择 YOLOv8/YOLO11 模型
- 配置检测类别和阈值

**第三步：创建任务**
- 进入"任务管理"页面
- 点击"添加任务"
- 选择摄像机通道和算法配置
- 配置事件输出方式（本地/MinIO/FTP）
- 启动任务

**第四步：查看分析结果**
- 进入"电视墙"页面
- 实时查看多路监控画面
- 右侧显示最新事件列表
- 点击事件查看详细信息和 GIF 动画
- 工具栏声音开关控制播放声音，每路画面可单独开关

**第五步（可选）：人员统计与语音报警**
- 在任务「配置」对话框中将事件类型选为「人员统计」
- 单机画线：进入「计数区域配置」，按住鼠标拖动绘制分界线；多机进出：指定入口机 / 出口机并按区域设置报警阈值
- 打开「显示配置」中的计数分界线开关，可在 AI 画面上叠加分界线与进 / 出标识
- 进入「系统管理 → 报警语音」上传或选用预置 MP3，配置全局播报开关与默认参数

### 6. 部署说明

**推荐使用 phpStudy 管理数据库和 FTP：**

phpStudy 是一款集成了 Apache/Nginx、MySQL/MariaDB、FTP 等服务的一键式服务器管理工具，非常适合本地开发和部署：

1. **下载地址**：[https://www.xp.cn/download.html](https://www.xp.cn/download.html)
2. **安装配置**：
   - 安装后启动 MySQL 服务，用于管理数据库
   - 启动 FTP 服务，用于远程存储事件图片和 GIF
   - 默认端口：MySQL 3306，FTP 21
3. **配置连接**：
   - 在 `config/system_config.yaml` 中配置 FTP 连接信息
   - 在 `config/system_config.yaml` 中配置 MinIO 连接信息（可选）

**ZLMediaKit 流媒体服务器配置：**

本项目使用 ZLMediaKit 作为流媒体服务器，支持 RTSP/RTMP/HTTP-FLV/HLS/WebRTC 等协议。部署配置参考：[docs/ZLMediaKit开机自启动部署文档.md](docs/ZLMediaKit开机自启动部署文档.md)，设备端部署参考：[docs/盒子开机自启动部署文档.md](docs/盒子开机自启动部署文档.md)

**关键配置说明：**
- **API 配置**：`api.secret` 设置 API 密钥
- **HTTP 服务**：默认端口 88，支持跨域访问
- **RTSP 服务**：默认端口 554，支持直接代理
- **RTMP 服务**：默认端口 1935
- **RTC 服务**：默认端口 8000，支持 WebRTC 语音对讲
- **钩子配置**：配置回调地址，用于流媒体事件通知

**配置文件路径**：将配置文件保存为 `config.ini` 放在 ZLMediaKit 目录下，启动服务即可。

**启动命令**（Windows）：
```bash
MediaServer.exe
```

**启动命令**（Linux）：
```bash
./MediaServer
```

### 语音对讲使用说明（非 localhost 访问）

语音对讲使用浏览器 `getUserMedia` API（麦克风访问），该 API 要求**安全上下文（Secure Context）**。浏览器规则：

- ✅ `https://` 任意地址 → 安全
- ✅ `http://localhost` → 浏览器特殊豁免（视为安全）
- ❌ `http://192.168.x.x` → 非安全，浏览器拒绝调用 `getUserMedia`

因此 `http://localhost:5556` 能用、`http://网络IP:5556` 不能用——这是**浏览器安全策略**，非后端代码限制。

**解决方法**：让浏览器把你的 HTTP 地址"视为安全来源"：

1. 浏览器地址栏打开 `chrome://flags/#unsafely-treat-insecure-origin-as-secure`
2. 在输入框填入平台地址，如 `http://192.168.5.15:5556`（你的平台 IP 地址）
3. 右侧下拉选 **Enabled** → 点 **Relaunch** 重启浏览器
4. 重新访问 `http://192.168.5.15:5556` → `isSecureContext` 变为 `true` → 麦克风可用

## 应用场景

### 安防监控
- 入侵检测：自动检测入侵人员，生成事件 GIF
- 异常行为：检测异常行为，实时报警
- 区域监控：自定义监控区域，精准检测

### 智能交通
- 车辆检测：实时检测车辆，统计车流量
- 违章检测：检测违章停车、逆行等行为
- 人流统计：统计人员流动，分析人流密度

### 工业检测
- 安全监测：检测人员是否佩戴安全装备
- 设备监控：检测设备运行状态
- 质量检测：检测产品缺陷和质量问题

## 项目架构

```
sagesight-AI/
├── config/              # 配置文件目录
│   ├── system_config.yaml    # 系统配置（Web服务、硬件加速、存储、通信）
│   ├── detector_config.yaml  # 检测器配置（推理后端、阈值）
│   ├── decoder_config.yaml   # 解码器配置（硬件加速）
│   ├── event_output.yaml     # 事件输出配置（GIF、存储方式、推送）
│   ├── tracker_config.yaml   # 跟踪器配置（Norfair参数）
│   ├── motion_config.yaml    # 运动检测配置
│   └── display_config.yaml   # 显示配置
├── src/                 # 源代码目录
│   ├── decoders/        # 视频解码器
│   │   ├── __init__.py
│   │   └── video_decoder.py
│   ├── detection/       # 区域检测
│   │   ├── __init__.py
│   │   └── zone_manager.py     # Zone 区域管理器
│   ├── counting/        # 人员统计
│   │   ├── counting_processor.py # 计数处理器
│   │   ├── counting_stats.py     # 计数状态持久化（按天）
│   │   ├── line_cross.py         # 分界线跨越判定
│   │   └── zone_config.py        # 计数区域配置
│   ├── pipeline/        # 检测管道
│   │   ├── __init__.py
│   │   └── camera_pipeline.py
│   ├── detectors/       # 目标检测器
│   │   ├── __init__.py
│   │   ├── base.py             # 检测器基类
│   │   ├── factory.py          # 检测器工厂
│   │   ├── detector_pool.py    # 检测器进程池管理
│   │   ├── detector_process.py # 检测器进程
│   │   ├── remote_detector.py  # 远程检测器代理
│   │   ├── region_utils.py     # 区域检测工具
│   │   └── backends/           # 推理后端
│   │       ├── onnx_cpu.py     # ONNX CPU 后端
│   │       ├── onnx_cuda.py    # ONNX CUDA 后端
│   │       ├── onnx_tensorrt.py # ONNX TensorRT 后端
│   │       ├── tensorrt.py     # TensorRT 直接加载
│   │       └── rknn.py         # RKNN NPU 后端
│   ├── tracking/        # 目标跟踪器
│   │   ├── __init__.py
│   │   ├── norfair_tracker.py  # Norfair 跟踪器
│   │   ├── tracked_object.py   # 跟踪对象
│   │   └── ptz_metrics.py      # PTZ 度量
│   ├── events/          # 事件输出处理
│   │   ├── __init__.py
│   │   ├── event_output.py          # 事件输出（异步 I/O）
│   │   ├── tracked_object_processor.py # 跟踪对象处理器
│   │   ├── event_cleanup_service.py # 事件清理服务
│   │   ├── camera_state.py          # 摄像机运行状态
│   │   ├── frame_cache.py           # 事件帧缓存
│   │   ├── rule_dedup.py            # 规则级事件去重
│   │   ├── gif_generator.py         # GIF 生成器
│   │   ├── minio_outputter.py       # MinIO 存储
│   │   ├── ftp_outputter.py         # FTP 存储
│   │   └── mqtt_outputter.py        # MQTT 推送
│   ├── llm/             # 大模型事件分析
│   │   ├── __init__.py
│   │   ├── llm_client.py            # LLM 客户端
│   │   ├── event_describer.py       # LLM 事件描述
│   │   └── event_deduplicator.py    # LLM 事件去重
│   ├── tasks/           # 任务管理
│   │   ├── __init__.py
│   │   ├── task_manager.py               # 任务数据模型
│   │   ├── task_executor.py              # 任务执行器（多摄像机）
│   │   ├── task_api.py                   # 任务 API
│   │   ├── device_channel_pool_manager.py # 设备通道池管理
│   │   └── device_channel_pool_api.py    # 设备通道池 API
│   ├── cameras/         # 摄像机管理
│   │   ├── __init__.py
│   │   ├── camera_manager.py        # 摄像机管理器
│   │   ├── camera_api.py            # 摄像机 API
│   │   ├── stream_service.py        # 流媒体服务
│   │   ├── stream_api.py            # 流媒体 API
│   │   ├── ptz_service.py           # PTZ 服务
│   │   ├── ptz_api.py               # PTZ API
│   │   ├── directory_service.py     # 目录服务
│   │   ├── directory_api.py         # 目录 API
│   │   ├── audio_talk_service_onvif.py # ONVIF 语音对讲
│   │   ├── audio_talk_api.py        # 语音对讲 API
│   │   ├── onvif_client.py          # ONVIF 客户端
│   │   └── onvif_audio_backchannel.py # ONVIF 音频回通道
│   ├── algorithms/      # 算法管理
│   │   ├── __init__.py
│   │   ├── algorithm_manager.py  # 算法管理器
│   │   ├── algorithm_api.py      # 算法 API
│   │   ├── algorithm_factory.py  # 算法工厂
│   │   └── model_manager.py      # 模型管理
│   ├── system/          # 系统管理
│   │   ├── __init__.py
│   │   ├── system_manager.py     # 系统管理器（流媒体配置）
│   │   ├── system_api.py         # 系统 API
│   │   ├── system_config_loader.py # 配置加载器
│   │   └── voice_broadcaster.py  # 报警语音播报
│   ├── node_agent/      # 节点管理（边缘平台注册与心跳）
│   │   ├── __init__.py
│   │   ├── node_agent_service.py # 注册、心跳、指标采集
│   │   ├── node_agent_config.py  # 节点配置加载
│   │   ├── agent_server.py       # 接收部署/停止指令的 HTTP 服务
│   │   ├── workload_manager.py   # 工作负载管理
│   │   └── media_manager.py      # 媒体资源管理
│   ├── auth/            # 用户认证
│   │   ├── __init__.py
│   │   ├── auth_manager.py  # 认证管理器
│   │   └── auth_api.py      # 认证 API
│   ├── motion/          # 运动检测
│   │   ├── __init__.py
│   │   └── improved_motion.py  # 改进运动检测器
│   └── utils/           # 工具类
│       ├── __init__.py
│       ├── log_manager.py         # 统一日志管理器
│       ├── process.py             # 进程管理（BaseProcess/Process）
│       ├── shared_memory.py       # 共享内存
│       ├── frame_index_queue.py   # 帧索引队列
│       ├── image.py               # YUV 图像处理
│       ├── cuda_processor.py      # CUDA 处理器
│       ├── rga_processor.py       # RGA 处理器（RK3588）
│       ├── rknn_executor.py       # RKNN 执行器
│       ├── ffmpeg_presets.py      # FFmpeg 预设
│       ├── font_utils.py          # 字体工具
│       ├── platform_utils.py      # 平台工具
│       └── video_utils.py         # 视频工具
├── static/              # 静态资源
│   ├── css/             # 样式文件
│   ├── js/              # JavaScript 文件
│   │   ├── jessibuca/    # 视频播放器
│   │   └── h265web/      # H265解码
│   ├── alarm_mp3/       # 预置报警语音文件（MP3）
│   └── images/          # 图片资源
│       └── algorithms/  # 算法示例图片
├── templates/           # HTML 模板
│   ├── base.html        # 基础布局模板
│   ├── login.html       # 登录页面
│   ├── monitor.html     # 电视墙页面
│   ├── tasks.html       # 任务管理页面
│   ├── cameras.html     # 摄像机管理页面
│   ├── events.html      # 事件管理页面
│   ├── algorithms.html  # 算法库页面
│   └── system.html      # 系统管理页面
├── models/              # 模型文件目录
│   ├── detection/       # 检测模型
│   └── labels/          # 标签文件
├── data/                # 数据文件
│   └── tasks.db         # SQLite 数据库
├── docs/                # 文档目录
│   ├── images/          # 功能截图
│   ├── ZLMediaKit开机自启动部署文档.md
│   └── 盒子开机自启动部署文档.md
├── logs/                # 日志文件
├── output/              # 输出文件（图片、GIF）
├── requirements.txt     # Python 依赖清单
└── run.py               # 启动脚本
```

## 更新日志

### v2.1.0（当前版本）
- ✅ 人员统计：单机画线 / 多机进出两种计数模式，按天计数、区域级报警阈值
- ✅ 驾驶舱人员统计图表：按（任务, 区域）展示进入 / 离开 / 当前人数，多区域横向滚动
- ✅ 报警语音播报：服务端播放预置 MP3，任务级 > 全局 > 默认的多级配置，同报警去重、不同报警抢占防重叠
- ✅ 电视墙声音控制：工具栏全局声音开关 + 每路画面单路声音开关（默认静音，状态本地记忆）
- ✅ 计数分界线显示：任务「显示配置」开关控制 AI 画面叠加分界线，多机进出模式在左下角标注「进」/「出」
- ✅ 配置收敛：解码 / 检测 / 跟踪 / 运动 / 事件输出 / 显示统一由 `config/*.yaml` 全局提供，任务级参数改为独立字段列（含自动迁移脚本）
- ✅ LLM 事件描述与事件去重（可选接入大模型）
- ✅ 节点管理：向边缘平台注册、心跳保活并上报 CPU / 内存 / 磁盘 / GPU / NPU 与工作负载指标
- ✅ 清理废弃配置（`zones_config.yaml`）与死代码，收敛配置单一真相源

### v2.0.0
- ✅ 完整支持多平台推理（NVIDIA GPU、CPU、RK3588 NPU）
- ✅ 完善事件 GIF 生成和三通道存储（本地、MinIO、FTP）
- ✅ 电视墙和事件管理支持图片/GIF 切换显示
- ✅ ONVIF Audio Back Channel 语音双向对讲（跨品牌兼容）
- ✅ ONVIF 协议自动发现和 PTZ 控制
- ✅ 多路视频实时分析和多目标跟踪（Norfair）
- ✅ 实时事件推送（Socket.IO + 原生 WebSocket + MQTT）
- ✅ 事件输出异步 I/O 优化（线程池执行存储/推送，不阻塞检测管道）
- ✅ 事件自动清理（按保留天数定期清理过期事件及存储文件）
- ✅ 统一日志管理（多进程 QueueHandler + QueueListener 架构）

## 技术支持

**项目地址**：https://gitee.com/hanx125/sagesight-ai

**技术栈**：
- 后端：Python + Flask + SocketIO + Eventlet
- 前端：HTML + CSS + JavaScript
- 推理：ONNX Runtime + TensorRT + RKNN
- 解码：FFmpeg + 硬件加速
- 存储：SQLite + MinIO + FTP
- 通信：Socket.IO + 原生 WebSocket + MQTT
- 语音对讲：ONVIF Audio Back Channel

## 许可证

本项目仅供学习和研究使用，不需授权即可用于商业用途，但不得申请软件专利著作权。

---

**睿观 SageSight AI** - 智能视频分析，让监控更智慧

---

## 联系作者
### 电子邮箱

624662795@qq.com

### 添加微信

如果您有技术问题或合作需求，欢迎添加作者微信交流：

![添加微信](docs/images/加朋友.jpg)

### 赞赏支持

如果您觉得这个项目对您有帮助，欢迎赞赏支持作者继续开发：

![赞赏码](docs/images/赞赏码.jpg)