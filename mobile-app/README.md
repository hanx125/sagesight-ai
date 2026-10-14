# SageSight 移动端（uni-app）

SageSight 智能视频分析平台的手机端，基于 **uni-app + Vue 3 + Vite**，深色专业主题，配套后端接口实现完整管理：首页总览、实时监控、事件中心、任务管理、摄像机管理、系统设置（健康状态 + UniPush 推送配置）。

## 目录结构

```
mobile-app/
├── src/
│   ├── pages.json              # 路由与导航配置（9 个页面）
│   ├── manifest.json           # 应用配置（Push 模块 + UniPush 2.0）
│   ├── uni.scss                # 主题变量（色板/圆角/阴影）
│   ├── App.vue                 # 全局样式（card/badge/btn/input 等）
│   ├── components/             # easycom 组件
│   │   ├── nav-bar/            # 自定义导航栏
│   │   ├── tab-bar/            # 底部标签栏（首页/监控/事件/我的）
│   │   ├── panel-*/            # 首页四个面板
│   │   ├── event-card/         # 事件卡片
│   │   └── empty-state/        # 空态占位
│   ├── pages/
│   │   ├── login/login.vue     # 登录（服务器地址 + 账号密码）
│   │   ├── index/index.vue     # 主容器（四面板切换）
│   │   ├── player/player.vue   # HLS 直播（H5 用 hls.js，App 用原生 video）
│   │   ├── events/detail.vue   # 事件详情
│   │   ├── tasks/              # 任务列表 / 新建编辑
│   │   ├── cameras/            # 摄像机列表 / 新建编辑
│   │   └── system/system.vue   # 系统健康 + 推送配置
│   └── utils/
│       ├── api.js              # 请求封装（Bearer Token + 401 跳登录）
│       ├── store.js            # token/用户/服务器地址存储
│       ├── format.js           # 时间/百分比/状态徽标/类名中文化
│       └── icons.js            # SVG 线性图标（data URI）
└── package.json
```

## 本地调试（H5）

```bash
cd mobile-app
npm install
npm run dev:h5     # http://localhost:5173
```

- vite 已配置代理：`/api`、`/output` → `http://localhost:5556`，H5 调试时服务器地址留空即可。
- 登录页默认账号密码同 Web 端管理员。

## App 打包（HBuilderX 云打包）

1. HBuilderX → 文件 → 导入 → 从本地目录导入 `mobile-app/`（选择含 `manifest.json` 的工程根目录）。
2. 运行 → 运行到手机或模拟器，可先真机调试。
3. 发行 → 原生 App-云打包：
   - 包名建议 `com.sagesight.app`
   - 勾选「uni-app 云打包」，使用 DCloud 公共测试证书或自有证书
   - 打包前在 manifest.json → App 常用其它设置确认 AppID（`__UNI__SAGESIGHT` 或自己申请）

## 开通 UniPush 2.0（个推）

1. 登录 [DCloud 开发者中心](https://dev.dcloud.net.cn/) → 我的应用 → 选择本应用。
2. 左侧「UniPush 2.0」→ 开通服务，自动创建个推应用。
3. 开通后在「应用信息」页拿到 **AppID / AppKey / MasterSecret**。
4. 手机端安装并登录后，App 会调用 `uni.getPushClientId` 获取 client_id，并 `POST /api/push/bind` 绑定到账号（在「我的」面板自动完成）。
5. 在「我的 → 系统设置」（管理员）填入 AppID/AppKey/MasterSecret 并保存，开启「启用事件推送」。
6. 服务端检测到事件后按置信度阈值与类别过滤，通过个推 REST API 下发通知。

> MasterSecret 属敏感信息，仅保存在服务端配置中。

## 服务器地址说明

- **H5 调试**：登录页服务器地址留空（走同源代理）。
- **App 真机**：必须填写完整地址，如 `http://192.168.1.10:5556`（手机与服务器需同一局域网，或部署公网地址后填写公网地址）。
- 事件抓拍图等相对路径 `/output/...` 会自动拼接服务器地址。
