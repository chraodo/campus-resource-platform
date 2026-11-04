# 可选 DSH 校园资源插件

这是课程任务书中自然语言业务 Agent 的候选插件，核心平台不依赖它。小组尚未决定是否把可选 Agent 纳入交付；此插件已写出源码与 bundle 配置，但**未安装到 DSH、未配置模型、未做实际工具调用验证**。采用 DSH 的工具注册与可安装 bundle 结构，具体版本需与课程环境匹配。

## 能做什么

- 查询座位和占用来源、本人有权查看的预约、教室申请和报修工单。
- 生成座位预约、教室申请、报修或取消操作的摘要。
- 只有用户明确同意单项摘要后才调用确认接口；平台服务端仍按 token 对应账号重新检查权限和业务条件。
- 缺少日期/时间等必要信息时先向用户追问。工具不调用数据库，只访问公开 `/api/v1` 接口。

## 目录和接口

- `index.js`：插件和工具注册。
- `package.json`：插件 bundle 清单。
- `cordis.patch.yml`：把插件加入所选 DSH profile。
- `campus_preview_write` 使用 `/api/v1/agent/actions/preview`。
- `campus_confirm_write` 使用 `/api/v1/agent/actions/{id}/confirm`。

## 安装前提

需要课程提供的 DSH 运行环境，并确认它兼容 `@deepseek-ai/dsh-tools` `0.2.0-rc.2`。安装 profile 前先把校园平台测试环境启动，并给**专用测试账号**签发个人 API token。不要用管理员 token；不要把 token 写进 profile 的 Git 配置、幻灯片或日志。

示例（具体命令以课程 DSH 版本为准）：

```sh
dsh plugin --profile campus-demo add ./optional-agent/dsh-campus-resource
CAMPUS_API_BASE_URL=https://campus-test.example.edu \
CAMPUS_API_TOKEN='<专用测试账号令牌>' \
dsh --profile campus-demo
```

本机开发可用 `http://127.0.0.1:8000`，其他 HTTP 地址会被插件拒绝。token 由平台“我的 API 令牌”入口创建，只显示一次；测试完成后应撤销。部署时让 DSH 运行进程从安全的环境变量/密钥管理器读取两个变量。

## 后续验收

1. 在 DSH profile 安装该本地 bundle，并确认启动与工具清单。
2. 配置仅访问测试环境的普通学生 token。
3. 演示查询、缺少信息时追问、预约预览、用户同意后确认，以及用户拒绝时不执行。
4. 验证越权、冲突和过期预览由后端拒绝；确认调用结果与业务审计对应。
5. 将 DSH 安装/配置/工具调用加入 CI 或课程要求的部署流程，并记录真实运行版本。

以上运行验证必须由 F 牵头并使用真实 DSH 环境完成；目前没有这些执行记录。
