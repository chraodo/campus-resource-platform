# 知栖校园资源服务平台

依据课程任务书实现的课程项目：M1 自习座位预约、M2 整教室申请审批、M3 物业报修，以及三者共用的账号权限、空间目录、通知和审计服务。自然语言 Agent 是附加项；核心平台不依赖 DSH。

## 现在能演示什么

- 学生登录后查找和预约座位，查看历史、复用条件、签到或取消。
- 管理员维护房间、座位、角色权限和最长预约时长；最长时长默认 4 小时，可由管理员调整。
- 教室申请经过两级审批；最终通过后才锁定全室座位。
- 报修可派单、维修、验收和评价；维修开始时占用相关资源，验收后释放。
- 屏幕使用仅绑定一个房间的专用令牌展示每日签到码。
- worker 处理预约提醒、未签到释放、申请超时、维修超时升级；配置 SMTP 后可投递邮件。
- 可选 Agent 的写操作接口要求先预览，再由用户明确确认。

当前代码由 coding agent 生成并经过自动化行为测试；**组员尚未逐项人工审查、签收，也未形成启动评审决议**。这些事实需要在 2026-11-05 的小组讨论中确认，不能把 AI 生成或自动测试说成人工验收。

## 本地运行

需要 Python 3.12 和 uv。首次运行：

```sh
uv sync --frozen
uv run alembic -c backend/alembic.ini upgrade head
uv run uvicorn campus_app.main:app --app-dir backend --reload
```

浏览器打开 <http://127.0.0.1:8000>。开发环境默认使用 `data/campus.db`，并在完成数据库迁移后写入虚构演示数据。演示账号密码均为 `demo2026`：`student`、`approver1`、`approver2`、`repair`、`admin`。接口说明见 `/api/docs`。

异步业务任务需单独启动：

```sh
PYTHONPATH=backend uv run python -m campus_app.worker
```

邮件提醒还需要在启动 API 和 worker 前配置 `.env` 中的 SMTP 项。没有邮件服务时，站内通知仍可用。屏幕签到页为 `/screen.html?room_id=LIB-1`，需由管理员先在系统管理页签发房间屏幕令牌并配置到该屏幕浏览器。

## 检查

```sh
uv run ruff check backend
uv run ruff format --check backend
uv run pytest -q
DATABASE_URL=sqlite:////tmp/campus-migration-check.db uv run alembic -c backend/alembic.ini upgrade head
```

容器环境和 CI 配置见 `Dockerfile`、`compose.yaml`、`.github/workflows/ci.yml`。私有 GitHub 仓库已创建；本机没有 Docker，容器构建和 GitHub 托管流水线尚未实跑。不得把配置文件说成已部署。

## 目录

```text
campus-resource-platform/
├── AGENTS.md                     # 可执行的项目协作规则
├── backend/campus_app/           # FastAPI、M1/M2/M3/D 业务模块与 worker
├── backend/migrations/           # Alembic 数据库迁移
├── backend/tests/                # 业务行为/API 集成测试
├── demo/                         # 浏览器界面与房间签到屏幕
├── docs/                         # 需求、架构、伦理、分工与交付记录
├── optional-agent/               # 附加 DSH 插件源码（未安装或运行）
├── scripts/                      # 冒烟检查与回滚辅助
├── compose.yaml                  # 本地 PostgreSQL + API + worker
└── .github/workflows/            # CI、测试环境启动和人工审批发布模板
```

## 这次启动评审要确认的事

六人真实姓名和模块负责人、公共层 D 的归属及工作量、可选 Agent 是否实施、业务规则中尚未确认的期限/申诉/审批细节、代码托管位置和下一次评审时间。候选方案都在 `docs/team-plan.md` 与 `docs/decision-record.md`，会议结果留空给组员填写。

项目代码由 coding agent 起草并经过本地自动化测试；组员尚未逐项人工审查或签收。GitHub 私有仓库为 `yushu212/campus-resource-platform`；初始提交是供组内审查的 AI 草稿，不代表成员已完成人工贡献。远程 CI、PR 和生产部署均未完成。
