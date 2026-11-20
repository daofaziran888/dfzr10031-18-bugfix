# 数据分析实验与数据应用服务平台

面向指标口径管理、分析实验设计、OLAP查询、数据看板构建与数据API发布的数据应用平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
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

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 指标定义 | `metric_def` | 指标 | 指标编号、指标名称、计算公式 |
| 维度管理 | `dimension` | 分析维度 | 维度编号、维度名称、维度类型 |
| 数据集管理 | `dataset` | 分析数据集 | 数据集编号、数据集名称、来源类型 |
| 分析实验 | `experiment` | 实验方案 | 方案编号、实验名称、分析数据集 |
| OLAP查询 | `query_builder` | 查询模板 | 模板编号、模板名称、查询语句 |
| 数据看板 | `dashboard` | 看板配置 | 看板编号、看板名称、包含图表 |
| 图表配置 | `chart` | 图表 | 图表编号、图表名称、图表类型 |
| 数据API发布 | `data_service` | 数据服务 | 服务编号、服务名称、输出数据集 |
| 智能告警 | `alert` | 告警规则 | 规则编号、告警名称、监控指标 |
| 可用观测 | `availability` | 采集窗口 | 窗口编号、有效营业分钟、实采分钟、停采分类 |
| 分析用户组 | `user_group` | 用户组 | 组编号、组名称、组描述 |
| 数据快照 | `snapshot` | 快照记录 | 快照编号、数据集名称、快照时间 |
| 任务调度 | `scheduler` | 调度任务 | 任务编号、任务名称、调度类型 |
| 缓存管理 | `cache` | 缓存配置 | 缓存编号、缓存目标、缓存策略 |
| 分析管道 | `pipeline` | 分析节点 | 节点编号、输入数据集、分析算子 |
| 分析模型 | `model` | 模型配置 | 模型编号、模型名称、模型类型 |
| 数据集市 | `data_mart` | 集市表 | 表编号、表名称、主题域 |
| 数据导出 | `export` | 导出任务 | 任务编号、导出对象、导出格式 |
| 数据审计 | `audit` | 审计记录 | 记录编号、操作类型、操作人 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 可用观测口径

智能告警的监控指标来自采集证据，入口在 `GET /api/availability/metric`：

- 分母只取**实采营业分钟**：营业日历展开的营业分钟，扣掉停采分钟，且只认真正有观测批次覆盖的分钟。
- 缺报分钟既不进分母也不折算成零销量；缺报与"整段收到 0 笔"是两种状态，后者读数才是 `0.0`。
- 停采分**计划停采**与**意外停采**两类记账；停采回执按分钟并集扣除，重叠回执只扣一次。
- 覆盖率不到 100% 时只发布"实采分钟口径"的当期读数，并标注与完整基线不可比及原因；
  数据源没有完整窗口基线时说明"尚未形成可比基线"。

示例窗口（`GET /api/availability/windows`）：

| 窗口 | 场景 | 有效分母 | 实采分钟 | 缺报 | 发布读数 | 基线 |
| --- | --- | --- | --- | --- | --- | --- |
| WIN-EXAMPLE | 营业 60 分钟，补传 30 分钟 12 笔 | 60 | 30 | 30 | 0.4 笔/分钟（覆盖 50%） | 不可比 |
| WIN-CLOSED | 数据源全天停业 | 0 | 0 | 0 | 空态 | 无基线 |
| WIN-ZERO | 整段收到零销量 | 60 | 60 | 0 | 0.0 笔/分钟 | 可比 |
| WIN-DEDUP | 计划停采与意外停采重叠 | 40 | 30 | 10 | 0.6 笔/分钟（覆盖 75%） | 不可比 |
| WIN-INTERRUPT | 中断后补传一半 | 60 | 45 | 15 | 0.47 笔/分钟（覆盖 75%） | 不可比 |
| WIN-FULL | 完整覆盖 | 60 | 60 | 0 | 0.35 笔/分钟 | 可比 |
| WIN-NOBASE | 完整采集但没有基线 | 60 | 60 | 0 | 0.3 笔/分钟 | 无基线 |

`GET /api/availability/verify` 会把上面每一行的有效分母、读数与基线可比性整理成对账单；
取数失败的批次可以在详情里重试（`POST /api/availability/windows/{id}/retry`）。

测试：`cd backend && .venv/bin/python -m unittest discover -s tests`。
