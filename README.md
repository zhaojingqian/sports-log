# sports-log

[中文](#中文) | [English](#english)

## 中文

个人 COROS 运动记录网站：`https://zzzgry.top/sport/`

它把 COROS 中的运动、恢复和健康数据整理成一个公开可读的网页，用来持续记录训练状态和跑步能力变化。页面不是后台系统，也不是数据管理工具，而是一个面向浏览和分享的个人运动日志。

### 展示内容

- 最近 `1D`、`7D`、`30D`、`60D` 与全部数据视图
- 总跑步次数、总距离、总时长、平均配速、消耗和爬升
- 每日距离折线趋势，以及 7 日移动平均线
- 配速趋势和 7 次均线
- HRV、Load、VO2max 的每日变化和 7 次均线
- 睡眠结构、恢复状态、静息心率等恢复相关指标
- 每周跑步总结，展示距离、时长和训练次数变化
- 最近活动记录，包括距离、配速、心率、训练负荷和功率
- 单次活动详情浮窗，展示训练摘要、分段数据、心率区间和运动记录
- 阶段成就卡片，用更直观的方式概括当前训练周期

### 数据更新

网站数据来自 COROS，并通过本地的 `coros-mcp` 接入后生成页面数据。

- 页面打开时会尝试进行一次安全刷新，同步当天活动、恢复和可用的睡眠数据
- 每天 `23:00` 自动更新当天数据
- 支持手动全量刷新，用于补充 mobile auth 才能拿到的数据
- 默认刷新会复用已有安全凭据，不主动发起 mobile auth，避免影响手机 App 登录状态

### 设计目标

- 简洁：减少解释性文字，把主要空间留给图表和数据
- 专业：采用统一的运动仪表盘视觉，让卡片、图表、状态和详情浮窗保持一致
- 直观：通过折线、均线、摘要卡片和状态反馈展示训练变化
- 连续：缺失的能力数据会沿用最近一次有效值，保证趋势可读
- 真实：展示实际训练记录，不做社交化包装，也不虚构指标

### 隐私说明

仓库只保存网站代码。个人运动数据、COROS 凭据、本地刷新 token、日志和生成文件都不应进入 git。

## English

A personal COROS sports log website: `https://zzzgry.top/sport/`

It turns COROS activity, recovery, and health data into a public, readable web page for tracking training status and running fitness over time. This is not an admin panel or a raw data management tool; it is a personal sports journal built for viewing and sharing.

### What It Shows

- Recent `1D`, `7D`, `30D`, `60D`, and all-data views
- Total runs, distance, duration, average pace, calories, and elevation gain
- Daily distance line trend with a 7-day moving average
- Pace trend with a 7-activity moving average
- Daily HRV, Load, and VO2max trends with 7-sample moving averages
- Sleep structure, recovery state, resting heart rate, and related recovery metrics
- Weekly running summaries for distance, duration, and session count
- Recent activities with distance, pace, heart rate, training load, and power
- Per-activity detail drawer with workout summary, splits, heart-rate zones, and notes
- Achievement cards that summarize the current training period at a glance

### Data Updates

The website uses COROS data through a local `coros-mcp` integration.

- The page attempts a safe refresh when opened, updating today's activities, recovery, and available sleep data
- Daily data is updated automatically at `23:00`
- Full refresh is available for mobile-auth-only data
- Default refresh reuses existing safe credentials and does not actively start mobile auth, avoiding phone-app login disruption

### Design Goals

- Minimal: keep explanatory text low and give charts and data the main space
- Polished: use a unified sports-dashboard visual style across cards, charts, status blocks, and detail drawers
- Clear: use lines, moving averages, summary cards, and status feedback to show training changes
- Continuous: missing fitness values reuse the latest available value to keep trends readable
- Real: show actual training records without social-style packaging or invented metrics

### Privacy

This repository stores website code only. Personal COROS data, credentials, local refresh tokens, logs, and generated files should stay on the server and out of git.
