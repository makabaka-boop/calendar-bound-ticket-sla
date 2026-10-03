# SLA 工单计时服务（FastAPI + SQLite + React）

支持工单的 SLA 计时系统：工作日历由 **UTC 区间**给出，策略以**有效工作分钟**
规定警告与升级；工单创建时**固定策略版本**，等待客户期间**暂停计时**，
重新打开**继续累积既有工作分钟**。后台计时器与用户状态操作按**同一事务顺序**
裁决，警告/升级在重启及重复扫描后**只登记一次**；改策略不会悄悄影响旧工单，
**显式迁移**会展示旧/新计时差异并保存证据。

## 目录结构

```
backend/
  sla/
    intervals.py   # 半开 UTC 区间集合运算（交/并/差/配对/投影）
    calendar.py    # 工作日历：UTC 周工时窗口 + 临时工作区间 + 假日切口
    policy.py      # 不可变策略版本（warn 分钟 + 有序升级阈值）
    timing.py      # 从 append-only 状态事件重放：已计入/暂停区间、累积分钟、跨越时刻
    db.py          # SQLite schema；所有写操作走 BEGIN IMMEDIATE 串行化
    store.py       # 策略/日历/工单仓储 + 状态转移（revision 乐观锁）
    adjudicate.py  # 计时器扫描（幂等登记）+ 显式策略迁移（差异/证据/补登）
    api.py         # FastAPI 应用、后台扫描线程、409 冲突、序列化
    seed.py        # 默认 24x7 日历（含假日）与策略 v1/v2
  tests/           # pytest：假日切口/暂停跨界/到期与解决竞争/策略迁移/修订冲突
frontend/          # React + Vite 控制台
```

## 运行

### 后端

```bash
cd backend
python3 -m pip install -r requirements.txt
python3 -m uvicorn sla.api:app --reload --port 8000
# 可选环境变量：SLA_DB=/tmp/sla.db  SLA_SCAN_INTERVAL=5
```

- 启动自动建表并写入种子：策略 `standard` v1（警告 120m，L2 240m）、
  v2（警告 60m，L2 180m，L3 480m）。
- 后台线程每 `SLA_SCAN_INTERVAL` 秒扫描全部未解决工单。

### 前端

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173 ，/api 与 /health 代理到 :8000
npm run build      # 生产构建到 dist/
```

### 测试

```bash
cd backend && python3 -m pytest -q
```

## 关键设计如何对应需求

| 需求 | 实现 |
| --- | --- |
| 工作日历由 UTC 区间给出 | `calendar.py`：周工时窗口（ISO 星期 + UTC `HH:MM`）+ 显式 UTC 工作区间；假日为 UTC 半开区间做切口 |
| 策略规定警告/升级所需有效工作分钟 | `policy.py`：`warn_minutes` 与有序 `escalations`（级别/分钟/动作） |
| 工单创建时固定策略版本 | `tickets.policy_id/policy_version` 创建时写入；发新策略版本只是新增不可变行 |
| 等待客户期间暂停，重开继续累积既有分钟 | 状态只追加（open/pause/resume/resolve/reopen）；每次裁决重放整个事件日志得到 active/pause 区间，历史分钟天然保留 |
| 计时器与用户操作同一事务顺序 | 所有写路径均 `BEGIN IMMEDIATE`（SQLite 单写锁）；`ops_log` 记录全局提交序；扫描在取写锁后再读并复查 status |
| 警告/升级重启与重复扫描只登记一次 | 阈值跨越时刻是事件日志+日历的纯函数；`sla_events` 上 `UNIQUE(ticket_id, policy_version, kind, ref)` 幂等保护 |
| 修改策略不悄悄改变旧工单时限 | 旧工单固定旧版本；旧事件保留原 policy_version 与原裁决 basis，不被改写 |
| 显式迁移展示旧/新差异并保存证据 | `/migrate/preview` 给出每阈值旧/新分钟、跨越时刻、收紧/放宽/新增/移除；`/migrate` 在同一写事务内写 `migrations.diff_json`，关联旧事件并按真实跨越时刻补登新策略已到期事件（标记 `migration_backfill`） |
| 页面展示截止时刻/已计入区间/暂停区间/升级记录 | 前端工单详情：各阈值投影截止时刻（暂停时显示顺延）、active 区间、日历切片、pause 区间、警告/升级列表（可展开每次裁决依据 basis）、迁移历史 |
| 过期修订提交返回冲突 | 工单 `revision` 单调递增；状态操作与迁移都须带所读 revision，不符返回 **409** |

## 主要 API

- `GET  /api/policies` / `POST /api/policies`（发布不可变新版本）
- `POST /api/tickets`（可指定 `policy_version`、`calendar_spec`、测试用 `at`）
- `GET  /api/tickets` / `GET /api/tickets/{id}`
- `POST /api/tickets/{id}/status`（body：`action` + `revision`）
- `POST /api/tickets/{id}/migrate/preview` / `POST /api/tickets/{id}/migrate`
- `GET  /api/tickets/{id}/migrations`
- `POST /api/admin/scan`（body 可选 `at`、`ticket_id`，便于确定性测试）

所有时间均为带时区的 UTC ISO-8601（`...Z`）。`at` 字段仅为可确定性回放/测试
提供，线上调用省略即用服务器当前 UTC 时间。

## 裁决依据（basis）证据

每条 `sla_events` 行保存 JSON 快照：裁决时刻、固定策略版本、当时累积有效工作
分钟、已计入区间、暂停区间、假日切口后的工作日历切片、时钟运行/暂停状态、
阈值与跨越时刻，以及触发来源（`timer` / `migration_backfill`）。前端在升级
记录中可逐条展开查看。
