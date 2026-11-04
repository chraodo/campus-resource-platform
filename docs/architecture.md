# 系统结构与主要流程

## 当前实现

当前项目使用模块化单体：一个 FastAPI 服务提供版本化业务接口，浏览器使用原生 HTML/CSS/JavaScript；SQLAlchemy 管理数据访问，Alembic 管理数据库结构；本地开发默认 SQLite，Compose 环境使用 PostgreSQL。定时 worker 与 API 分开运行，共用同一个数据库。邮件由 SMTP 配置控制，未配置时只保留站内通知。

```mermaid
flowchart LR
  Browser[浏览器页面] --> API[FastAPI /api/v1]
  Screen[房间签到屏幕] --> API
  OptionalAgent[附加 Agent：待小组决定] -. 公共 API .-> API
  API --> M1[M1 座位预约]
  API --> M2[M2 教室审批]
  API --> M3[M3 物业工单]
  API --> D[D 公共服务：账号权限、空间、通知、审计]
  M1 --> Holds[可追溯资源占用]
  M2 --> Holds
  M3 --> Holds
  Holds --> DB[(SQLite / PostgreSQL)]
  D --> DB
  Worker[提醒与超时 Worker] --> DB
  Worker --> Mail[可选 SMTP 邮件]
```

图中虚线的 Agent 目前不是已部署的组件。后端的预览/确认接口是留给未来适配器的业务入口，不代表 DSH 已接入。

## 模块边界

| 模块 | 负责什么 | 主要代码 |
| --- | --- | --- |
| M1 | 座位查询、预约、取消、历史复用、签到和未签到释放 | `backend/campus_app/routers/m1.py`、`domain.py` |
| M2 | 整教室申请、分级审批、拒绝、取消和超时 | `backend/campus_app/routers/m2.py`、`domain.py` |
| M3 | 报修、派单、维修、SLA 升级、验收与评价 | `backend/campus_app/routers/m3.py`、`domain.py` |
| D | 登录身份、角色权限、房间与座位、站内/邮件通知、操作审计 | `auth.py`、`shared.py`、`rbac.py`、`worker.py` |
| Web | 学生页面、审批/维修台、管理页和签到屏幕 | `demo/` |
| 数据迁移 | 从空库创建与演进表结构 | `backend/migrations/` |

资源占用存在唯一共享记录 `ResourceHold`：写入模块和来源单号。M2 只有最终审批通过才创建整间教室的占用；M3 只有工单进入维修中才下架相关空间；M1 查询时汇总占用并返回原因。各业务处理释放时按原来源单号撤销记录。

## 主要业务接口

接口版本前缀为 `/api/v1`；交互式 API 说明在开发模式 `/api/docs`。

| 方法/路径 | 模块 | 用途 |
| --- | --- | --- |
| `GET /api/v1/seats` | M1 | 按日期、时间、时长及设备筛选座位和占用来源 |
| `POST /api/v1/reservations` | M1 | 创建座位预约；支持幂等键 |
| `GET /api/v1/me/reservations` | M1 | 查看本人预约和历史 |
| `POST /api/v1/reservations/{id}/check-in` | M1 | 用房间当日码签到 |
| `POST /api/v1/classroom-applications` | M2 | 提交整室申请 |
| `POST /api/v1/classroom-applications/{id}/decision` | M2 | 有权限的审批人做当前级别决定 |
| `POST /api/v1/work-orders` | M3 | 提交故障 |
| `POST /api/v1/work-orders/{id}/start` | M3 | 开始维修并下架资源 |
| `POST /api/v1/work-orders/{id}/accept` | M3 | 验收并释放维修占用 |
| `POST /api/v1/agent/actions/preview` 与 `/confirm` | 可选适配接口 | 生成待确认摘要，并在确认后重新校验执行 |

## 数据与安全

- 账号密码只保存哈希；会话、API 凭据和房间屏幕令牌分别校验用途。屏幕令牌被限制在单一房间，不能用于用户登录。
- 每个管理接口在服务端检查权限；前端隐藏按钮只是体验处理，不是安全边界。
- 屏幕每日签到码由服务端按房间和日期生成，响应禁止缓存；预约记录不会返回签到码。
- 预约写操作检查房间开放时间、部门可见范围、系统上限、冲突及幂等键。
- 站内通知始终可用；邮件仅在用户填写邮箱且 SMTP 已启用时排队发送。邮件发送最多重试配置次数，错误日志不写 SMTP 密码。
- 演示种子账号、房间和预约都是虚构数据；生产环境必须关闭种子数据，并设置至少 32 字符随机 `APP_SECRET`。

## 异步处理和已知限制

单独 worker 每 30 秒处理预约提醒、未签到、审批超时和工单 SLA。数据库唯一通知键避免重复生成；邮件采用有限重试。课程规模目前使用数据库轮询，不依赖 Redis；部署时只运行一个 worker 实例，避免多个 worker 同时投递同一封邮件。

目前没有实际 SMTP 账号、微信通知通道、正式学校身份认证、生产服务器或 Git 远程。跨日预约不支持。正式审批/违约/报修政策需由小组向老师或场地管理员确认。
