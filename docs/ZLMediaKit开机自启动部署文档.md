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
