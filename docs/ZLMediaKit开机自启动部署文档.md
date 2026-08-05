# ZLMediaKit 开机自启动部署文档

本文档说明如何在 RK3588 盒子上配置 ZLMediaKit 流媒体服务的开机自启动，使用 systemd 服务方式实现。

## 一、服务文件内容

创建服务文件 `/etc/systemd/system/zlmediakit.service`，写入以下内容：

```ini
[Unit]
Description=ZLMediaKit Server
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/ZLMediaKit/release/linux/Debug
ExecStart=/root/ZLMediaKit/release/linux/Debug/MediaServer
Restart=always
RestartSec=10
StandardOutput=append:/root/ZLMediaKit/release/linux/Debug/log/mediaserver.log
StandardError=append:/root/ZLMediaKit/release/linux/Debug/log/mediaserver_error.log

[Install]
WantedBy=multi-user.target
```

### 设计说明

- **`After=network.target network-online.target` + `Wants=network-online.target`**：等待网络就绪后再启动，避免首次拉取 RTSP/RTMP 流时网络未通。配合 `Restart=always` 和 `RestartSec=10`，即使首次连接失败也会自动重试。
- **`WorkingDirectory=/root/ZLMediaKit/release/linux/Debug`**：ZLMediaKit 启动时会从工作目录读取 `config.ini` 配置文件，必须设置正确的工作目录，否则会使用默认配置或找不到配置文件。
- **`ExecStart=/root/ZLMediaKit/release/linux/Debug/MediaServer`**：直接运行 MediaServer 可执行文件。若部署路径不同，需相应修改。
- **`StandardOutput=append:` / `StandardError=append:`**：ZLMediaKit 自身日志输出到 stdout/stderr，通过 append 模式追加写入独立日志文件，便于排查问题。同时 journald 也会收集这些输出。
- **`User=root`**：ZLMediaKit 默认监听 554（RTSP）、1935（RTMP）、80（HTTP）等端口，其中 80 和 554 是特权端口（<1024），root 用户无需额外权限。若改用非 root 用户，需修改 `config.ini` 中的端口为非特权端口（如 8554、11935、8080），或通过 `AmbientCapabilities=CAP_NET_BIND_SERVICE` 授予绑定特权端口的能力。

## 二、部署步骤

按以下顺序在盒子上执行：

```bash
# 1. 创建日志目录（服务文件中 append 路径必须先存在，否则启动失败）
mkdir -p /root/ZLMediaKit/release/linux/Debug/log

# 2. 写入服务文件
sudo nano /etc/systemd/system/zlmediakit.service
# 粘贴上面的内容，保存退出（Ctrl+O 回车保存，Ctrl+X 退出）

# 3. 重载 systemd
sudo systemctl daemon-reload

# 4. 启用开机自启
sudo systemctl enable zlmediakit

# 5. 立即启动（先手动跑一次确认能起）
sudo systemctl start zlmediakit

# 6. 看状态（应为 active running）
sudo systemctl status zlmediakit

# 7. 看启动日志（确认无报错）
sudo journalctl -u zlmediakit -n 50 --no-pager
```

### nano 粘贴注意事项

nano 默认会自动缩进，可能导致每行前面多出空格。粘贴前建议先关掉自动缩进：
- 按 `Alt + M`（或 `Esc` 然后按 `M`）切换掉 auto-indent
- 粘贴后检查每行开头是否有多余空格，有就删掉

> systemd 配置文件对行首空格不敏感，但 `[Unit]`/`[Service]` 这类段名必须顶格，不能有前导空格。

## 三、验证开机自启是否生效

重启盒子验证一次最稳：

```bash
sudo reboot
# 重启后等 30 秒登录
sudo systemctl status zlmediakit   # 应为 active (running)
```

`Loaded: ... enabled` 表示开机自启已启用，`Active: active (running)` 表示服务正在运行。

进一步验证端口监听正常：

```bash
ss -tlnp | grep -E "554|1935|80|443|30000"
# 应看到 MediaServer 监听以下端口：
#   554    RTSP
#   1935   RTMP
#   80     HTTP（含 HTTP-FLV/HLS 播放）
#   443    HTTPS（可选，需配置证书）
#   30000  RTP 推流端口（范围可在 config.ini 配置）
```

## 四、日常管理命令

```bash
sudo systemctl stop zlmediakit       # 停止
sudo systemctl restart zlmediakit    # 重启
sudo systemctl disable zlmediakit    # 取消开机自启
sudo journalctl -u zlmediakit -f     # 实时看控制台日志
tail -f /root/ZLMediaKit/release/linux/Debug/log/mediaserver.log        # 看标准输出日志
tail -f /root/ZLMediaKit/release/linux/Debug/log/mediaserver_error.log  # 看错误日志
```

## 五、日志说明

ZLMediaKit 存在多份日志，用途不同：

| 日志来源 | 查看命令 | 内容 |
|---------|---------|------|
| journald | `sudo journalctl -u zlmediakit -f` | systemd 收集的 stdout/stderr，包含启动信息、崩溃 traceback |
| 标准输出日志 | `tail -f /root/ZLMediaKit/release/linux/Debug/log/mediaserver.log` | MediaServer 运行时输出，包含流注册、客户端连接、推拉流事件 |
| 错误日志 | `tail -f /root/ZLMediaKit/release/linux/Debug/log/mediaserver_error.log` | 错误输出，包含端口占用、配置错误、流处理异常 |
| 内置日志 | `ls /root/ZLMediaKit/release/linux/Debug/log/` | ZLMediaKit 自身按日期滚动的日志文件（如 `2024-01-01_1.log`） |

- 日常排查业务问题优先看 `mediaserver.log`（流注册、拉流失败、客户端断连等）。
- 服务起不来时看 `journalctl` 或 `mediaserver_error.log`，能看到端口占用、配置文件解析错误等。
- ZLMediaKit 自身的日志级别和路径可在 `config.ini` 的 `[general]` 段配置 `logLevel`、`logFilePath`。

## 六、部署前检查清单

启用服务前，在盒子上跑一遍确认：

```bash
# 1. 确认可执行文件存在且可运行
ls -l /root/ZLMediaKit/release/linux/Debug/MediaServer
/root/ZLMediaKit/release/linux/Debug/MediaServer -h
# 应输出帮助信息，无 "command not found" 或 "permission denied"

# 2. 确认配置文件存在
ls -l /root/ZLMediaKit/release/linux/Debug/config.ini

# 3. 确认端口未被占用
ss -tlnp | grep -E "554|1935|80"
# 若被占用，需先停止占用进程或修改 config.ini 端口

# 4. 手动启动测试
cd /root/ZLMediaKit/release/linux/Debug && ./MediaServer
# 看到 "MediaServer started" 后 Ctrl+C 停止

# 5. 启用服务后看状态
sudo systemctl status zlmediakit
sudo journalctl -u zlmediakit -n 50 --no-pager
```

## 七、故障排查

### `systemctl status` 显示 failed 或 inactive

查看详细日志定位原因：

```bash
sudo journalctl -u zlmediakit -n 50 --no-pager
tail -n 50 /root/ZLMediaKit/release/linux/Debug/log/mediaserver_error.log
```

常见原因：
- **端口占用**：ZLMediaKit 默认监听 554/1935/80，确认未被其他进程占用：
  ```bash
  ss -tlnp | grep -E "554|1935|80"
  # 若被占用，停止占用进程或修改 config.ini 中对应端口
  ```
- **配置文件错误**：检查 `config.ini` 语法是否正确，关键配置项：
  ```ini
  [api]
  apiDebug=1
  [http]
  port=80
  [rtsp]
  port=554
  [rtmp]
  port=1935
  ```
- **可执行文件路径错误**：确认 `ExecStart` 路径与实际部署路径一致：
  ```bash
  ls -l /root/ZLMediaKit/release/linux/Debug/MediaServer
  ```
- **日志目录不存在**：服务文件中 `StandardOutput=append:` 路径必须先存在：
  ```bash
  mkdir -p /root/ZLMediaKit/release/linux/Debug/log
  ```
- **权限不足**：确认 MediaServer 可执行文件有执行权限：
  ```bash
  chmod +x /root/ZLMediaKit/release/linux/Debug/MediaServer
  ```

### 服务运行但功能异常

查看运行日志：

```bash
tail -n 100 /root/ZLMediaKit/release/linux/Debug/log/mediaserver.log
```

常见问题：
- **拉不到 RTSP 流**：检查摄像头地址、网络连通性、RTSP 认证信息。
- **HTTP-FLV/HLS 播放不了**：检查 `config.ini` 的 `[http]` 段端口和 `dir` 静态文件目录配置。
- **推流被拒绝**：检查 `config.ini` 的 `[rtmp]` 段 `auth` 认证配置。

### 性能问题

```bash
# 查看 ZLMediaKit 进程资源占用
top -p $(pgrep MediaServer)

# 查看线程数
ps -T -p $(pgrep MediaServer) | wc -l
```

若 CPU 过高或内存增长，检查：
- 并发流数量是否超出盒子处理能力（RK3588 建议并发转码流 < 8 路）
- `config.ini` 的 `[general]` 段 `mergeWriteMS`（合并写性能优化）和 `enableFFmpeg`（是否启用 FFmpeg 转码）

## 八、config.ini 配置说明

配置文件路径：`/root/ZLMediaKit/release/linux/Debug/config.ini`

查看完整配置：

```bash
cat /root/ZLMediaKit/release/linux/Debug/config.ini
```

### 典型配置内容（关键字段）

以下为与本平台播放链路相关的核心配置段，修改后需重启 ZLMediaKit 生效：

```ini
# 通用配置
[general]
# 媒体流无人观看时自动关闭的延时（毫秒），0 表示不自动关闭
# 注意：若设为非0，前端停止播放后流会被关闭，再次播放需重新触发 addStreamProxy
streamNoneReaderDelayMS=0
# 是否启用 FFmpeg 转码
enableFFmpeg=0
# 流无人观看时合并写优化
mergeWriteMS=0
# 日志级别（0=TRACE 1=DEBUG 2=INFO 3=WARN 4=ERROR）
logLevel=2
# 日志文件路径
logFilePath=./log

# API 配置
[api]
# API 调试模式（打印每次 API 请求/响应，排查问题时设 1，生产环境设 0）
apiDebug=1
# API 默认密钥（与本平台 streaming_servers 表的 secret 字段一致）
secret=QeYjJSfbaub0QkTACpqmmbSqz6ma1V2S
# 是否允许跨域
defaultSnap=./www/snapshot/

# 协议开关配置（关键！控制流注册到哪些协议模块）
# 若某协议设为0，对应协议播放会报 stream not found
[protocol]
# 自动关闭无人观看的流（0=不自动关闭 1=自动关闭）
auto_close_stream=0
# 按需拉流：流无消费者时是否关闭源（0=保持拉流 1=无消费者时关闭）
stream_none_reader_close_ms=0
# RTSP 协议开关（AI任务 FFmpeg 拉流依赖此项）
enable_rtsp=1
# RTMP 协议开关
enable_rtmp=1
# HLS 协议开关
enable_hls=1
# TS 协议开关
enable_ts=1
# fMP4 协议开关（HTTP-FLV / WS-FLV 依赖此项，必须为1）
enable_fmp4=1
# 是否启用 MP4 录制
enable_mp4=0

# RTSP 服务配置
[rtsp]
# RTSP 监听端口
port=554
# SSL/TLS 端口（RTSPS）
sslport=322
# 流是否延迟发布（0=立即发布 1=等首帧到达后发布）
delay=false
# RTSP 拉流认证超时
directProxy=1
# 是否启用 RTP over TCP
rtpTransportType=tcp

# RTMP 服务配置
[rtmp]
# RTMP 监听端口
port=1935
# SSL/TLS 端口（RTMPS）
sslport=19350

# HTTP 服务配置（WebRTC 信令 / HTTP-FLV / HLS 都走此端口）
[http]
# HTTP 监听端口（本平台 streaming_servers.http_port 字段）
port=80
# HTTPS 监听端口
sslport=443
# 是否允许跨域
dirMenu=1
# 静态文件目录
dir=./www
# 心跳保活时间（秒）
keepAliveSecond=30
# 是否启用 WS-FLV（必须为1，前端降级播放依赖此项）
enable_ws_flv=1

# WebRTC 配置（前端 WebRTC 播放依赖此段）
[rtc]
# WebRTC UDP 端口
port=8000
# 外网 IP 候选地址（NAT 环境必填，纯内网可留空）
externIP=
# 是否自动探测外网 IP
detectExternalIP=1
# 最大比特率（0=不限）
maxBitrate=0
# NACK 重传间隔（毫秒）
nackIntervalMs=20
# 是否启用 TWCC 拥塞控制
twccRtcpInterval=0

# HLS 配置
[hls]
# HLS 分片时长（秒）
segDur=2
# HLS 分片数量
segNum=3
# HLS 清理过期分片
segRetain=5
# HLS 文件存放目录
fileBufSize=65536
# HLS 生成 TS 文件的存放目录
filePath=./www

# RTP 配置
[rtp]
# RTP 端口范围起止
port_range=30000-35000
# RTP 超时时间（秒）
rtpMaxSize=10

# RTP 推流配置
[rtp_proxy]
# RTP 推流监听端口
port=10000
# 推流超时时间（秒）
timeoutSec=15
```

### 关键配置项说明

| 配置项 | 作用 | 异常影响 |
|---|---|---|
| `[protocol].enable_fmp4` | HTTP-FLV / WS-FLV 协议开关 | 设为0时 WS-FLV 报 `WebSocket closed before established` |
| `[protocol].enable_rtsp` | RTSP 协议开关 | 设为0时 AI 任务 FFmpeg 拉流失败 |
| `[protocol].auto_close_stream` | 流自动关闭 | 设为1时前端停止播放后流即被关闭 |
| `[protocol].stream_none_reader_close_ms` | 无观看者延时关闭流 | 非零时注册流后无人观看会自动关闭 |
| `[http].port` | HTTP/WS-FLV 端口 | 与本平台 `streaming_servers.http_port` 必须一致 |
| `[http].enable_ws_flv` | WS-FLV 开关 | 设为0时前端无法降级到 WS-FLV 播放 |
| `[rtc].port` | WebRTC UDP 端口 | 防火墙需放行此 UDP 端口 |
| `[rtc].externIP` | 外网 IP 候选 | NAT 环境未配置时 WebRTC ICE 协商失败 |
| `[api].secret` | API 密钥 | 必须与本平台数据库 `streaming_servers.secret` 一致 |

### 修改配置后的操作

```bash
# 1. 编辑配置文件
sudo nano /root/ZLMediaKit/release/linux/Debug/config.ini

# 2. 重启 ZLMediaKit 使配置生效
sudo systemctl restart zlmediakit

# 3. 确认服务运行正常
sudo systemctl status zlmediakit

# 4. 验证端口监听
ss -tlnp | grep -E "554|1935|80|443"
ss -ulnp | grep 8000   # WebRTC UDP 端口
```

### 常见播放问题排查对照表

| 现象 | 可能原因 | 检查项 |
|---|---|---|
| WebRTC 报 `stream not found` | `[protocol]` 段禁用了对应协议，或 `auto_close_stream=1` | `grep -A 10 "\[protocol\]" config.ini` |
| WS-FLV 报 `WebSocket closed before established` | `enable_fmp4=0` 或 `enable_ws_flv=0` | `grep -E "enable_fmp4\|enable_ws_flv" config.ini` |
| AI 任务拉流失败 | `enable_rtsp=0` 或 RTSP 端口错误 | `grep -A 5 "\[rtsp\]" config.ini` |
| 流播放一会就断 | `auto_close_stream=1` 或 `stream_none_reader_close_ms` 非零 | `grep -E "auto_close\|stream_none_reader" config.ini` |
| API 调用返回 401 | `secret` 与本平台数据库不一致 | `grep "secret" config.ini` |
