# 运行、测试与发布

## 本机开发

需要 Python 3.12 和 uv：

```sh
uv sync --frozen
uv run alembic -c backend/alembic.ini upgrade head
uv run uvicorn campus_app.main:app --app-dir backend --reload
```

另开终端启动 D 资源进程，使用与 API 相同的环境变量：`uv run uvicorn campus_app.resource_service:app --app-dir backend --host 127.0.0.1 --port 8001`。主服务默认访问该回环地址。Compose 自动使用内网服务名 `resource:8001`。

API 和网页在 <http://127.0.0.1:8000>；开发 API 文档在 `/api/docs`。worker 另开一个终端运行：

```sh
PYTHONPATH=backend uv run python -m campus_app.worker
```

默认创建虚构演示账号和数据。需上线或处理真实数据时设置 `SEED_DEMO_DATA=false`，并替换 `APP_SECRET`。

## Compose 测试环境

1. 复制 `.env.example` 为 `.env`，本地使用时保留演示模式。
2. 执行 `docker compose up --build`。
3. API 容器等待数据库健康后，从 Alembic 迁移到最新版本，再启动服务；worker 等 API 健康后启动。
4. 浏览器打开 `http://127.0.0.1:8000`。停止环境用 `docker compose down`；如需清除所有本地数据库数据，才额外使用 `--volumes`。

GitHub Actions 的 CI 会自动创建一套全新 PostgreSQL 环境，跑登录、查座位、预约幂等和冲突检查，然后销毁测试容器和数据库卷。这是每次工作流的临时环境，不会留下共享测试网址。

## 组内常驻测试环境

CI 文件另含 `deploy-group-test`。由小组准备专用 Linux runner（标签 `campus-staging`），在仓库变量中启用 `ENABLE_GROUP_TEST_DEPLOY=true`，并在 staging Environment 配置独立的 `APP_SECRET`、`POSTGRES_PASSWORD`。仅 main 上通过 CI 的 push 会触发部署。测试环境使用独立 Compose 项目及数据卷，默认监听本机 8100，演示账号只用于受限组内测试。测试与生产发布分别串行执行，请组员约定共享环境的联调时段。运行版本通过 `GET /health` 的 `revision` 字段核对提交 SHA。本轮尚无远程部署记录。

## 生产发布

生产部署只由 `.github/workflows/deploy-production.yml` 的人工触发完成，并通过 GitHub `production` 环境审批。启用前由小组实际维护者配置：

- GitHub 仓库和受保护的 `production` Environment；设置一名或多名审批人并禁止部署发起人自批。
- 可持久保存 Compose 数据卷的 Linux self-hosted runner 和 Docker Compose。
- `APP_SECRET`（至少 32 个随机字符）、`POSTGRES_PASSWORD`、`CAMPUS_BACKUP_DIR`；邮件可选。
- 域名、HTTPS 反向代理、访问控制、数据库备份监控和故障联系人。

发布标签必须来自组员审查通过的提交。工作流先备份数据库，再构建所选版本并在 API 启动前运行迁移；不自动部署未打标签的分支。**公开 GitHub 仓库已创建；当前没有 self-hosted runner、生产凭据或生产主机，因此工作流仍只是模板，尚未运行或发布。**

## 回退

1. 暂停用户访问，并记录当前标签、错误表现和最近一次成功版本。
2. 从生产备份目录选定故障版本部署前的 `.sql.gz` 快照；先核对文件来源和时间。
3. 选择一个已审查的旧标签重新运行人工发布流程。若数据库迁移不兼容，先确认目标版本所需 schema，再由数据库负责人批准恢复快照。
4. 数据库快照恢复会丢弃快照生成后新增的业务数据。只有明确同意数据损失并核对备份后，才在生产主机运行：

   ```sh
   CAMPUS_CONFIRM_DATABASE_RESTORE=RESTORE bash scripts/restore_postgres_backup.sh /absolute/path/to/approved-backup.sql.gz
   ```

5. 检查 `/health`、登录、预约和业务数据后，才恢复流量；把原因、数据范围、恢复结果和新的决定写进发布记录。

发布前先备份。能向前修复时优先准备修复版本；恢复数据库仅作为经过人工批准的最后回退步骤。
