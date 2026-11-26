# 主数据治理与参考数据管理平台

覆盖主数据抽取、标准化清洗、去重合并、金记录发布与下游订阅分发的治理平台

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8021/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5201/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8021`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 源系统登记 | `source_system` | 源系统 | 系统标识、系统名称、数据域 |
| 主数据实体 | `entity_model` | 实体定义 | 实体编码、实体名称、唯一键规则 |
| 记录抽取作业 | `record_extract` | 抽取作业 | 作业名称、源系统、实体编码 |
| 标准化清洗 | `std_cleanse` | 清洗规则 | 规则名称、实体编码、清洗阶段 |
| 重复检测器 | `dup_detect` | 检测结果 | 检测批次、实体编码、候选组数 |
| 金记录合并 | `golden_merge` | 合并任务 | 合并任务、实体编码、候选组 |
| 金记录发布 | `golden_publish` | 发布批次 | 发布批次、实体编码、金记录数 |
| 下游订阅方 | `subscriber_mgr` | 订阅方 | 订阅方标识、订阅实体、分发格式 |
| 分发任务 | `dist_task` | 分发批次 | 分发批次、订阅方、记录范围 |
| 血缘关系图 | `lineage_graph` | 血缘边 | 血缘边标识、上游节点、下游节点 |
| 质量规则库 | `quality_rule` | 质量规则 | 规则名称、实体编码、检查维度 |
| 质量报告 | `quality_result` | 质量结果 | 报告批次、实体编码、检查总数 |
| 合并复核队列 | `merge_review` | 复核记录 | 记录编号、候选组、复核结论 |
| 版本回滚器 | `version_rollback` | 回滚任务 | 回滚任务、目标版本、回滚原因 |
| 同步游标 | `sync_cursor` | 游标记录 | 源系统、实体编码、游标值 |
| 标识交叉映射 | `id_cross_map` | 交叉映射 | 映射批次、源系统标识、金记录标识 |
| 分发回执中心 | `dist_receipt` | 回执记录 | 回执编号、分发批次、订阅方 |
| 实体变更审批 | `model_change` | 变更单 | 变更单号、实体编码、变更内容 |
| 治理看板数据 | `stats_overview` | 统计快照 | 统计时间、实体编码、金记录数 |
| 参考数据管理 | `ref_data_mgr` | 参考数据集 | 数据集编码、数据集名称、码值条目 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。
- 金记录发布分两段：`POST /api/golden_publish/publish?publish_id=N` 先确认版本
  （只消费同实体、已真正合并并带金记录标识的记录），再为每个生效订阅方登记分发清单；
  清单齐全后才置为 `已发布`。重复提交沿用已确认的版本标识，断线后可用同一发布编号续做。
- 发布行回查 `GET /api/golden_publish/manifest?publish_id=N` 与完成回执
  `GET /api/golden_publish/receipt?publish_id=N` 都按发布编号精确取数，不跨批次拼凑。
- 分发领取 `GET /api/dist_task/claimable?publish_id=N` 只返回已确认清单里的批次，
  不可领取会给出原因；`POST /api/dist_task/claim?task_id=N` 认领一次即锁定，
  未完成前不会记为已发出。

领域：数据工程
子方向：数仓与数据湖
