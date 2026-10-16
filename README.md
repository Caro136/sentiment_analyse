# 智能舆情分析平台

面向企业品牌与公共事件的智能舆情分析平台：全网多源内容监测 + 多模态解析 + 多智能体研判，
提供舆情态势感知、情感倾向识别、风险预警、传播脉络溯源、核心观点聚类，并自动生成结构化研判报告。

| 文档 | 位置 |
|------|------|
| 需求规格说明书（PRD） | `docs/智能舆情分析平台-PRD.md` |
| 技术设计规格（SPEC） | `docs/智能舆情分析平台-设计规格.md` |
| 项目实战需求原文 | `docs/舆情分析平台项目实战.md` |
| 模拟数据集说明 | `data/README.md` |

## 当前进度

| 任务 | 内容 | 状态 |
|------|------|------|
| 数据准备 | 4 类信源模拟数据集 + 情感/风险评测集 | ✅ 已完成 |
| T-01 | 工程骨架：目录、配置、日志、启动器 | ✅ 已完成 |
| T-02 | 数据库层：32 张表 DDL、ORM 模型、异步会话 | ✅ 已完成 |
| T-03 | 事件总线：五类事件契约、发布订阅、进度回调 | ✅ 已完成 |
| T-04 | 模型客户端：按角色配置、结构化输出、重试器 | ✅ 已完成 |
| T-05 | 契约层：五维框架、统一内容与证据模型 | ✅ 已完成 |
| T-06 | 前端骨架：Vue3 + Vite + TS、路由守卫、状态管理、接口封装 | ✅ 已完成 |
| T-07 | 平台适配器：4 类信源字段映射收敛 | ✅ 已完成 |
| T-08 | 接入与清洗：多源读取、文本清洗、SimHash 去重、水军识别 | ✅ 已完成 |
| T-09 | 多模态解析：OCR（MCP 云服务）、ASR 模拟、失败放行 | ✅ 已完成 |
| T-10 | 认知分析：情感识别、烈度指数、风险分级（评测 87.6%） | ✅ 已完成 |
| T-11 | 认知分析：实体抽取、观点聚类、事件归并 | ✅ 已完成 |
| T-12 | 向量召回：Milvus 索引、语义检索、RRF 融合 | ✅ 已完成 |
| T-13 | 私域研究员：LangGraph 八节点、游标逐章生成 | ✅ 已完成 |
| T-14 | 公域研究员：三搜索适配器 + 降级链、五节点 | ✅ 已完成 |
| T-15 | 主持人研判：事件驱动配对、逐维研判、最终裁判 | ✅ 已完成 |
| T-16 | 研判编排：角色注册表、并发启动、任务状态与事件发布 | ✅ 已完成 |
| T-17 | 报告引擎：五段式聚合、Markdown/HTML 双格式落盘 | ✅ 已完成 |
| T-18 | SSE 广播：事件扇出、心跳、连接管理 | ✅ 已完成 |
| T-19 | 认证与权限：scrypt 口令、签名会话、权限矩阵、用户管理 | ✅ 已完成 |
| T-20 | 操作审计：`@audit` 装饰器、上下文留痕、前后值记录 | ✅ 已完成 |
| T-21 | 监测配置：主题与词包、信源通道、运行配置热更新 | ✅ 已完成 |
| T-22 | 内容查询：多维筛选、分页、详情组装（原文 + OCR/ASR） | ✅ 已完成 |
| T-23 | 事件与溯源：事件专题、传播时间线、观点聚类、实体图谱 | ✅ 已完成 |
| T-24 | 预警引擎：三类规则、收敛去重、邮件+站内通知、生命周期 | ✅ 已完成 |
| T-25 | 处置工单：状态机、处置轨迹、公关回应草案生成 | ✅ 已完成 |
| T-26 | 态势大盘：指标物化任务、趋势/分布/词云/指标卡 | ✅ 已完成 |
| T-27 | 报告中心：任务管理、列表、在线预览、Markdown/HTML 导出 | ✅ 已完成 |
| T-28 | 系统运维：任务看板、日志检索、留存策略、演示数据重置 | ✅ 已完成 |
| T-29 | 态势大盘页：ECharts 趋势/环形/信源分布、告警滚动、下钻跳转 | ✅ 已完成 |
| T-30 | 研判工作台页：卡片流 + 六维筛选 + 时间线/聚类/图谱三视区 | ✅ 已完成 |
| T-31 | 处置协同区：状态流转、批注与轨迹、回应草案生成/编辑/采纳 | ✅ 已完成 |
| T-32 | 报告中心页：列表筛选、异步生成与进度、在线预览、导出（PDF 走打印） | ✅ 已完成 |
| T-33 | 监测配置页：主题与词包、信源通道与自检、预警规则、通知状态、运行配置 | ✅ 已完成 |
| T-34 | 端到端联调：跑通「监测→预警→研判→处置→报告」，补齐两处缺失入口 | ✅ 已完成 |
| T-35 ~ T-36 | 评测调优与交付 |  |

## 环境准备

要求 Python 3.11+，依赖管理用 [uv](https://docs.astral.sh/uv/)（已配置国内镜像）。

```bash
# 1. 安装核心依赖（骨架运行所需）
uv sync

# 2. 按任务追加依赖组
# ⚠️ 一次性列全需要的 extra：uv sync --extra X 会把环境同步为「基础 + X」，
#    不带上的 extra 会被移除（例如只写 --extra multimodal 会卸掉 T-04 的 langchain）
uv sync --extra llm --extra multimodal    # T-04 模型客户端 + T-09 OCR
uv sync --extra llm             # T-04 模型客户端（langchain-openai）
uv sync --extra agents          # T-13~T-16 研判链路（langgraph / langchain）
uv sync --extra analytics       # T-10~T-11 认知分析（jieba / scikit-learn）
uv sync --extra embeddings      # T-12 向量编码（sentence-transformers）
uv sync --extra vector          # T-12 Milvus 客户端
uv sync --extra multimodal      # T-09 OCR（mcp）
uv sync --extra auth            # T-19 认证（会话签名 itsdangerous；口令哈希用标准库 scrypt）
uv sync --extra report          # T-17 报告渲染（mistune）

# 3. 准备配置
.env            # 按实际环境填写密钥
```

**未填密钥不影响启动**：服务会照常运行并在日志中列出未就绪的能力，
相关接口调用时才降级或报错。这样开发期不必等所有密钥到位才能开工。

## 数据库初始化

```bash
# 建库建表（原始区 9 张 + 分析区 9 张 + 业务区 14 张 = 32 张）
python -m engines.common.db.init_db

# 首次初始化时一并导入模拟数据集（内容 505 / 评论 6093 / 账号 385）
python -m engines.common.db.init_db --with-data

# 查看各表行数
python -m engines.common.db.init_db --status

# DDL 与 ORM 一致性校验（改表结构后必跑）
python -m engines.common.db.check
```

> ⚠️ 建表脚本含 `DROP TABLE IF EXISTS`。目标库已存在时 `init_db` 会主动中止，
> 需显式加 `--force` 才会重建（会清空同名表）。

### 表的三个分区（设计规格 ADR-03）

| 分区 | 表 | 写入方 |
|------|-----|--------|
| **原始区**（9 张） | `weibo_*`、`douyin_*`、`news_*`、`forum_*`、`platform_account` | 数据生成器 · **本项目只读** |
| **分析区**（9 张） | `content_analysis`、`modality_asset`、`event`、`event_content`、`opinion_cluster`、`cluster_member`、`spread_node`、`entity`、`entity_relation` | ingestion + analytics |
| **业务区**（14 张） | `sys_user`、`audit_log`、`topic`、`topic_keyword`、`source_channel`、`topic_channel`、`alert_rule`、`alert_record`、`ticket`、`ticket_log`、`report`、`report_file`、`task_run`、`dashboard_metrics` | 服务层 |

DDL 是权威定义（`sql/analysis/` 与 `sql/business/`），ORM 是它的代码映射。
改表结构的顺序：**先改 DDL → 再改 ORM → 跑 `check` 校验**。

## 启动

```bash
python main.py                          # 按 .env 的 HOST / PORT 启动
uvicorn app.main:app --reload           # 开发期热重载
```

启动后访问：

- `http://localhost:8000/docs` —— 接口文档
- `http://localhost:8000/healthz` —— 存活探测
- `http://localhost:8000/readyz` —— 就绪探测（依赖连通性 + 各能力配置就绪情况）

> ⚠️ **必须单 worker 运行**。事件总线与主持人会话是进程内状态，多 worker 会导致事件丢失
> （详见设计规格 ADR-05）。`main.py` 已写死 `workers=1`。

## 认证与权限（T-19）

### 初始化演示账号

库里没有用户就没人能登录，而用户管理接口本身要求管理员身份——这个引导问题由命令行解决：

```bash
python -m app.services.auth.seed           # 建三个演示账号（已存在则跳过）
python -m app.services.auth.seed --reset   # 已存在的账号：重置口令并恢复默认角色
```

| 账号 | 默认口令 | 角色 |
|------|---------|------|
| `admin` | `Admin@123` | 系统管理员 |
| `analyst` | `Analyst@123` | 舆情分析师 |
| `pr` | `Pr@12345` | 公关运营 |

口令每次执行都会打印出来，且**不写进日志**（走 stdout 而非 loguru）。这三个是演示凭据，
上线前必须逐个改掉；也可以用 `--password-env VAR` 从环境变量取初始口令，避免口令留在命令行历史里。

### 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/auth/login` | 登录，下发会话 Cookie（**全站唯一免登录接口**） |
| POST | `/api/v1/auth/logout` | 登出，清除 Cookie |
| GET | `/api/v1/auth/me` | 当前用户（前端刷新页面后据此恢复登录态） |
| GET/POST | `/api/v1/users` | 用户列表 / 新增用户 |
| PUT | `/api/v1/users/{user_id}` | 修改显示名、角色、状态 |
| POST | `/api/v1/users/{user_id}/reset-password` | 重置口令 |

`/api/v1` 下**除登录外全部要求会话**：受保护路由组统一挂着 `require_authenticated`
（`app/main.py::_build_routers`），新增路由加进这个组即自动受保护。这件事有测试兜底
——`tests/test_auth_api.py::AuthCoverageTest` 遍历 OpenAPI 里全部 `/api/v1` 路径，
断言未登录一律 401 `AUTH_001`。

### 会话机制

- **票据放在 HttpOnly Cookie 里**，响应体不含票据：前端不需要它（浏览器自动携带），
  而 `EventSource` 无法自定义请求头，SSE 只能靠 Cookie 鉴权。同时接受
  `Authorization: Bearer`，便于 curl 与 `/docs` 联调。
- **票据只存「用户业务ID + 口令哈希指纹」**，不放角色。角色、状态每次请求回查数据库，
  因此停用账号、调整角色、重置口令都能**立即生效**，而不是等票据过期。
- 口令哈希指纹的存在让「重置口令」成为无会话表时唯一可靠的强制下线手段
  （改口令后该用户所有既有票据立刻失效）。**无法选择性踢掉某台设备**——需要时再引入会话表。
- 有效期由 `SESSION_MAX_AGE_SECONDS` 控制（默认 8 小时）。
- **`SESSION_SECRET` 未配置时会用进程内随机密钥并告警**：服务一重启，所有人掉线。
  开发期无妨，长期运行请务必在 `.env` 里配一个。
- 走 HTTPS 部署时把 `SESSION_COOKIE_SECURE` 置 `true`（否则票据在网络里明文传输）；
  本地 http 演示必须保持 `false`，否则浏览器直接丢弃 Cookie。

### 权限

矩阵的唯一来源是 `engines/contracts/permissions.py`（角色 × 功能 × 数据范围，
逐条对应 PRD 第 2 章）：接口层用 `require_permission(...)` 或 `require_role(...)` 声明，
不写 `if user.role == "admin"`。数据范围（分析师仅限本人主题）由服务层拿到资源后调
`ensure_data_scope()` 校验。改权限 = 改这一张表 + 跑 `tests/test_permissions.py`。

错误码：未登录 `AUTH_001`、会话过期 `AUTH_002`、权限不足 `AUTH_003`、
凭据错误 `AUTH_004`、账号停用 `AUTH_005`（完整码表见 `app/errors.py`，
前端按码段分支，见 `front/src/api/client.ts`）。

## 操作审计（T-20）

关键操作留痕（FR-04）。已接：登录（**含失败**）、新增用户、改角色/状态、重置口令。
配置变更、风险复核、工单流转、报告生成与导出等动作的取值已在 `AuditAction` 登记，
由 T-21~T-28 在自己的写接口上挂装饰器即可——**审计模块不用再改**。

```python
from app.services.audit import audit, note_change
from engines.contracts.enums import AuditAction

@audit(AuditAction.CONFIG_UPDATE, target="topic:{topic_id}")   # target 支持 {参数名} 模板
async def update_topic(repo, *, topic_id: str, ...):
    ...
    note_change(before=old, after=new)     # 可选：登记前后值（业务手上已有的那份）
```

四条实现约定，改之前先读 `app/services/audit/` 的模块文档：

1. **操作人从上下文取，不进函数签名**。鉴权依赖解析出用户后写一次
   （`app/security.py`），同一请求内所有被审计的调用自动带上；来源 IP 同理（trace 中间件）。
   否则审计会退化成「谁记得传谁才留痕」。
2. **失败也留痕，且异常照常抛出**。口令错误、被业务规则拒绝这类「没成功的操作」
   恰恰是审计的核心价值；结果写进 `after_value`（`{"ok": false, "error": "BIZ_203"}`，
   审计表没有结果列，DDL 已冻结）。
3. **审计用独立事务**。业务回滚不能连带抹掉「有人尝试过这个动作」的记录。
   代价是审计表不可写时业务照常进行——记 ERROR 日志（含 trace_id）后继续，
   属于刻意的可用性取舍，见 `writer.py`。
4. **敏感值不入审计**。口令哈希永不落库（重置口令只记 `password_reset: true`）；
   失败原因只记错误码/异常名，不记异常原文（可能含密钥）。

查询能力（`writer.list_entries`，支持动作 / 操作人 / 时间范围 + 分页）已就绪，
供 T-28 的系统运维页接接口——本任务不做路由（FR-04 只要求留痕）。

**已知边界**：越权尝试在依赖层就被拦下，业务函数根本没被调用，因此不进审计表
——它由访问日志承载（403 的访问记录 + trace_id）。若要把越权收敛成安全事件，
应在 `require_permission` 里记录，而不是指望业务装饰器。

## 监测配置（T-21）

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| GET | `/api/v1/topics` | 主题列表（状态/负责人/名称筛选 + 分页，含词包分组统计） | `topic:read`（三方） |
| POST | `/api/v1/topics` | 新建主题（可一并提交词包与通道关联） | `topic:write` |
| GET | `/api/v1/topics/{topic_id}` | 主题详情（全量词条 + 关联通道） | `topic:read` |
| PUT | `/api/v1/topics/{topic_id}` | 改名称/描述/主题关键词/生效时间/状态（停用即 FR-06） | `topic:write` |
| PUT | `/api/v1/topics/{topic_id}/keywords` | 全量替换词包（品牌/事件/竞品/排除） | `topic:write` |
| PUT | `/api/v1/topics/{topic_id}/channels` | 全量替换启用通道（FR-09） | `topic:write` |
| GET | `/api/v1/channels` | 四类信源通道状态（首次访问幂等补齐） | `channel:read`（三方） |
| PUT | `/api/v1/channels/{channel_id}` | 启停通道 / 调轮询间隔与回溯窗口（FR-11/12） | `channel:write`（管理员） |
| GET | `/api/v1/channels/check` | 通道连通性自检（FR-13，可带 `channel_id`） | `channel:read` |
| GET / POST | `/api/v1/config` | 运行配置读取 / 热更新（白名单，FR-53） | `config:read` / `config:write`（管理员） |

### 主题关键词（`source_keyword`）是自动推导的

接入层靠原始数据的 `source_keyword` 列判断内容属于哪个主题（双向模糊匹配），
而使用者填的是词包——所以在建主题时按 **品牌词 → 事件词 → 竞品词** 取权重最高的词
自动推导；排除词不参与（它只负责收窄）。显式传 `source_keyword` 时以传入值为准。

改词包时，若当前值**等于旧词包的推导值**（说明是自动来的）则跟随更新，
否则保持不动（那是使用者有意写死的值）。

词包的两条校验：同组同词去重（保留较大权重）；同一个词既在召回组又在排除组
**整批拒绝**——那种配置下命中与否取决于匹配顺序。

### 信源通道不由用户增删

四类信源是系统定义（与原始表一一对应），使用者的动作是启停与调参。缺失的通道
在首次访问时按默认值补齐（社交媒体/短视频 15min·24h，新闻门户 30min·72h，
行业论坛 60min·168h——后两者发文频率低，参数不同是业务事实）。

自检返回的是**可核对的事实**（库可达性、原始表条数与最新发布时间、最近采集记录），
不探测外部站点：本项目没有真实采集器，那种「连通正常」是编出来的。

### 运行配置热更新

白名单 9 个键（烈度权重四项、向量召回开关与条数、搜索供应商、OCR 超时、日志级别），
**不含任何密钥**——`tests/test_monitoring.py::ConfigWhitelistTest` 用正则钉死了这一点。
键不在白名单或取值非法时**整批拒绝**（错误里列出可用键），不做部分应用。

改动**只作用于进程内配置单例**（下一个请求即生效），**不写 `.env`**，重启回落到环境配置；
需要持久化的业务阈值应落在数据库（`alert_rule`，T-24）。

> ⚠️ 实现这个接口时踩到过一个静默失败：`Settings(**{"INTENSITY_W_VOLUME": 0.45})`
> 里的大写键**不是 pydantic 字段名**，而配置是 `extra="ignore"`——整条更新被丢弃，
> 接口返回「已更新」、配置却没变。所以热更新必须先转成字段名再交给 pydantic，
> 测试里有一条专门盯它。

## 监测→分析链路（T-34 补齐的运行入口）

「配置主题 → 自动监测」的后半段：把一个主题从**原始表里的数据**推进到**可研判的分析结果**。
六个阶段按序执行，顺序不能颠倒：

```
接入（读原始表 → 清洗 → 去重 → 水军识别 → [多模态]）
  → 认知分析打标（情感 / 情绪 / 烈度 / 风险）
  → 事件关联（一个主题一个事件专题）
  → 实体图谱 / 观点聚类      ← 聚类消费打标产出的情感标签
  → 向量索引（Milvus）        ← 研判的私域召回依赖它
```

```bash
uv run python -m app.services.pipeline --topic 味鲜居后厨卫生      # 也可传 topic_id
uv run python -m app.services.pipeline --keyword 临时主题词 --no-index   # 不绑定主题试跑
```

| 参数 | 说明 |
|------|------|
| `--topic` | 主题名称或 `topic_id`（先按 ID 查，再按名称精确匹配） |
| `--keyword` | 直接给接入用的主题关键词（不绑定主题的临时试跑） |
| `--window-hours` / `--limit-per-table` | 时间窗与单表条数上限 |
| `--with-media` | 打开多模态解析（默认关闭，见下） |
| `--no-entities` / `--no-clusters` / `--no-index` | 跳过相应阶段 |
| `--force` | 主题已停用时仍然执行 |

**为什么需要它**：各环节早就实现，但分散在两个引擎里（`engines.ingestion` 与
`engines.analytics`），**没有任何一处把它们串起来**——先前库里那批分析结果是
历史会话用一次性脚本跑的，脚本本身没留下，等于功能在、链路不可演示也不可复现。
编排放在 `app/services/pipeline/`（跨两个引擎 + 写分析区多张表，属服务层），
与 `app/services/system/retention.py` 同为「运维/演示型 CLI」。

四条约定：

1. **整条链路幂等**（SC-07「演示可复现」的前提）：打标按 `content_uid` 覆盖、事件内容关联去重、
   簇成员先清后写、实体按 ID upsert、向量 `upsert`。重复跑只补新增内容——
   实测第二次跑同一主题：接入 1933 条 → 去重后 890 条 → 打标 890 条**全部跳过** → 其余阶段照常重算。
2. **局部失败不阻断，但要说清**：实体抽取或向量索引失败只进 `warnings`
   （研判还能用关键词兜底）；只有「接入一条都没取到」才提前结束。失败原因原样带出，
   不做「看起来成功」的粉饰。
3. **LLM 与向量编码都在事务之外**：打标、实体抽取、聚类、编码是秒级到分钟级的外部调用，
   放进事务会长时间占着连接；因此按「先算后写」分两段，只在落库时开短事务。
4. **多模态默认关闭**：数据集里的图片/视频是占位 URL，OCR 必然失败（见「当前数据状态」），
   开着只是白花外部调用与时间。

> 打标完成时会发 `CONTENT_LABELED` 事件（事件驱动预警的入口）。**该事件在 CLI 进程内发布**，
> 而监听器（`AlertListener`）在服务进程里——所以 CLI 跑完不会自动触发预警，
> 演示时由 `POST /alerts/scan` 显式补扫（走同一条链路，这也是前端「立即扫描」按钮的入口）。

## 内容查询（T-22）

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| GET | `/api/v1/contents` | 舆情动态流：多维筛选 + 分页，按**发布时间倒序** | `content:read`（三方） |
| GET | `/api/v1/contents/{content_uid}` | 详情：原文全文 + OCR/ASR 证据 + 判定依据（FR-44） | `content:read` |

筛选参数：`topic_id`、`source_type`、`sentiment`、`risk_level`、`start_time`/`end_time`、
`keyword`、`is_suspected_bot`、`is_manual_override`、`page`/`page_size`。枚举写错会 422
（不是静默返回空列表——后者会被误当成「这个筛选没数据」）。

### 两段查询与排序（ADR-03 的落地）

标签在分析区、正文与发布时间在原始表，跨四张异构表无法一条 SQL 表达，因此：

```
原始侧条件（时间窗、关键词） → 候选 content_uid（按发布时间倒序）
→ 分析区叠筛选（主题/情感/风险/水军/人工复核） → 取本页正文并回填作者与互动量
```

排序**统一为发布时间倒序**（舆情流的阅读方式是「最新发生了什么」），代价是分页在
应用层完成。演示规模（千级）可接受；数据量增大时应把 `published_at` 冗余进
`content_analysis`（需改 DDL）以恢复纯 SQL 分页。

> ⚠️ 「关键词」在接入层有**两种语义**，由 `search_raw_hits(match=...)` 决定：
> `text`（默认）匹配正文，用于工作台检索；`topic` 匹配 `source_keyword`，
> 用于判定内容归属。写错不报错、只是结果少得离谱（实测候选数从 2533 掉到 14）。

列表正文截断 200 字并返回 `text_truncated=true`，全文只在详情接口
（设计规格 8.2：列表不返回长文本）。

### 主题归属需要回填（FR-08）

`content_analysis.topic_id` 是主题筛选的唯一依据，而打标本身不决定归属——
归属由「内容命中了哪个主题的关键词」决定。若历史数据是先打标、后建主题的，
需要补一次归属：

```bash
python -m app.services.content.attribute --dry-run   # 只统计
python -m app.services.content.attribute             # 只填 topic_id 为空的内容
python -m app.services.content.attribute --force     # 重新归属（覆盖既有值）
```

新内容的归属应当由打标链路直接带上（`analytics.service.analyze_contents(topic_id=...)`），
本命令是给历史数据补账用的。**未归属的内容在主题筛选里查不到，且不会报错**。

## 事件与溯源（T-23）

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| GET | `/api/v1/events` | 事件专题列表（主题/风险/状态/标题筛选 + 分页） | `event:read`（三方） |
| GET | `/api/v1/events/{event_id}` | 事件详情（统计 + 聚类数 + 图谱规模） | `event:read` |
| GET | `/api/v1/events/{event_id}/timeline` | 传播溯源时间线（FR-45，按发布时间正序） | `event:read` |
| GET | `/api/v1/events/{event_id}/clusters` | 观点聚类，按簇规模倒序（FR-47） | `event:read` |
| GET | `/api/v1/events/{event_id}/clusters/{cluster_id}/members` | 簇成员下钻（结构与动态流一致） | `event:read` |
| GET | `/api/v1/events/{event_id}/graph` | 实体关系图谱（FR-46） | `event:read` |

**本任务全是读操作**：没有写入、没有审计——事件的建立与标签归 analytics，
查询接口不该顺手改数据。

### 两项现算（`event` 表里的对应列是空的）

| 现算内容 | 为什么 |
|---------|--------|
| 事件统计：声量、负面率、最高风险、平均烈度、时间范围、信源/情感分布 | `event.total_volume` / `negative_ratio` / `first_seen_at` / `last_seen_at` / `risk_level` 都是空值，而它们全部可由关联内容推出。维护一份会漂移的缓存不如现算 |
| 事件归属主题 | `event.topic_id` 存的是建事件时的关键词（`evt_星耀XPhone15` 那一套），不是主题业务 ID。归属改为按其**内容的归属**取众数 |

因此 `risk_level` 筛选筛的是「该事件内容的最高风险」，与列表展示的值一致；
事件按现算的最近发布时间倒序，分页在应用层完成（事件数远小于内容）。

### 溯源节点角色是推导的

`spread_node` 表为空，节点角色由内容属性推导（响应里 `role_source=derived` 明确标注）：

| 角色 | 判定 |
|------|------|
| `origin` 首发 | **内容主体**中发布时间最早的那条 |
| `official` 官方回应 | 作者认证为「官方 / 政务」的内容主体 |
| `ignition` 引爆 | 其余内容主体里影响力最高、且达到 KOL 阈值的一条 |
| `followup` 跟进 | 新闻门户的内容主体，或作者认证为「媒体」 |
| `spread` 扩散 | 其余全部（含评论） |

只有**内容主体**参与前四类判定：评论是内容的下游反应，把它算成「首发」或「引爆」
会让溯源结论失真。默认 `node_scope=key` 只返回关键节点（非扩散角色 + 扩散里互动量
最高的若干条，上限 50），`node_scope=all` 返回全部内容。

> ⚠️ 未打标的内容也会出现在时间线里（`sentiment` 为 null）：时间线讲的是传播结构，
> 因为缺标签就把节点丢掉，可能把「首发」整条抹掉。

## 预警引擎（T-24）

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| GET | `/api/v1/alerts` | 告警列表（状态/等级/事件/时间筛选 + 分页） | `alert:read`（三方） |
| PUT | `/api/v1/alerts/{alert_id}` | 认领 / 已读 / 关闭（状态机，见下） | `alert:ack`（分析师 + 公关） |
| GET | `/api/v1/alerts/rules` | 规则列表 | `alert_rule:write`（管理员） |
| POST | `/api/v1/alerts/rules` | 新建规则（未给阈值时按类型补默认值） | `alert_rule:write` |
| PUT | `/api/v1/alerts/rules/{rule_id}` | 改阈值 / 等级 / 敏感词库 / 启停，**即时生效并留痕** | `alert_rule:write` |
| POST | `/api/v1/alerts/scan` | 立即扫描一次（存量补扫、演示、排障） | `alert_rule:write` |

管理员只运维不处置：**不能认领或关闭告警**（PRD 第 2 章）。

### 触发链路

```
analytics 打标完成 → 发布 CONTENT_LABELED（第 6 类事件）
   → 预警监听器（回调 O(1) 入队，worker 串行处理，积压折叠成一次扫描）
   → 按主题评估三类规则 → 收敛去重 → 落库 → 通知
```

正常路径是事件驱动的（设计规格 8.2：告警检测是事件驱动而非轮询）；
`POST /alerts/scan` 走**同一条链路**，只用于存量数据补告警——T-24 之前打标的
2825 条内容不会再有事件，靠它把历史告警补出来。两条路径共用一套判定逻辑，
因此不会出现「手动扫能报、自动扫不报」。

规则初始化：`python -m app.services.alert.seed`（PRD 附录 B.4 的三类默认规则；
敏感词库是演示用的公开词，上线前必须按企业口径替换）。

### 三类规则（PRD 附录 B.4）

| 类型 | 判定 | 阈值口径 | 关键参数 |
|------|------|---------|---------|
| `negative_surge` | 本窗口负向条数 vs 上一窗口的环比增幅 | 相对增幅（0.5 = +50%） | 最小绝对条数 5 条（否则 1→3 也是 +200%） |
| `sensitive_hit` | 命中敏感词库的内容条数 | 条数 | 需要词库，空词库永不命中 |
| `kol_voice` | 高权重账号发出负向或 A/S 级内容的条数 | 条数 | 影响力阈值 60（`Author.KOL_INFLUENCE_THRESHOLD`） |

判定函数是**纯函数**（`engines/alerting/rules.py`），可以脱离数据库单独测试——
规则算错只会「该报的没报」，不会有任何异常。

> ⚠️ **窗口的时间轴是 `analyzed_at`（打标时间）**，不是发布时间：激增规则的语义是
> 「新内容涌入的速度」，而发布时间只在原始表里。持续接入时两者几乎一致；
> 但本项目的 2825 条标签是**一次性批量补跑**的，因此激增规则会算出
> 「+46800%」这种从 0 起跳的数字——那是批量打标的产物，不是真实激增。
> 释放数据时的口径说明也写在 `rules.py` 模块文档里。

### 生命周期与响应时长

```
未读 unread → 已读 read → 已认领 claimed → 已关闭 closed   （任意状态都可直接关闭）
```

- 迁移表写在 `engines/alerting/lifecycle.py`，非法迁移返回 409 `BIZ_203`，
  错误里会列出可选状态；**已关闭是终态**（重新打开应当是一条新告警）。
- `responded_at` **只在首次认领时写入**，后续流转不覆盖——否则「响应时长」会被
  不断刷新，指标失去意义。未认领时 `response_seconds` 为 `null`（不是 0）。
- 收敛（FR-55）：同一规则 + 同一事件在收敛窗口（默认 30 分钟）内只建一条告警，
  后续命中累加 `merged_count`、等级取更严重的一侧、**不重复通知**
  （重复推送会让收件人忽略整个渠道）。

### 通知下发（FR-54 / D-03 邮件优先）

邮件用标准库 `smtplib` 直连（SSL 465），失败按 5s/15s/45s 退避重试 3 次，
仍失败则**降级为纯站内消息**并把 `notify_status` 标成 `degraded`、失败原因写进
`notify_error`——降级必须可见，否则「告警发了但没人收到」会被静默吞掉。
未配置 SMTP 时返回 `skipped`（不视为失败）。

**先落库再通知**：邮件可能失败，但告警记录绝不能因为通知发不出去而丢失。

> ⚠️ **T-34 联调发现的静默失效**：打标完成后发布 `CONTENT_LABELED` 事件时
> （`analytics.service._publish_labeled`），早先写成 `[label.content_uid for label in result.labels]`——
> 而 `result.labels` 是 **uid → 标签的字典**，且 `AnalysisLabel` 本身**不带 uid**
> （内容标识是字典的键）。于是每次真的打到标，链路最末尾都会抛 `AttributeError`：
> 标签已落库、内容照常进大盘，**事件驱动预警却一条都不触发**，而且只在有新增内容时才发作
> （补扫路径不经过这里，所以先前一直没暴露）。现已取字典键作为 uid，
> 并在 `tests/test_analytics.py::PublishLabeledTest` 补了盯这条接缝的测试。

## 多智能体研判（T-13~T-16 / API-22~23）

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| POST | `/api/v1/research` | 发起多智能体研判（**异步**，立即 202 返回 `task_id`） | `research:run`（分析师 / 管理员） |
| GET | `/api/v1/research/{task_id}` | 任务状态与各角色结果（含产物路径） | `research:read`（三方） |

目标二选一：`query`（研判主题词）或 `topic_id`（由后端取主题名称）。
**`query` 必须与报告引擎的产物键是同一个值**——三方产物（`insight_*` / `media_*` / `host_*`）
的文件名里带的就是它，报告引擎据此加载材料；所以给 `topic_id` 时统一解析成主题名称，
不让调用方各传各的（与报告服务的口径一致）。

**同时只允许一个研判任务**：主持人会话是进程内共享的，两个主题并发会让同名维度被错误配对
（`PairStore` 按 `section_key` 配对），因此第二个请求返回 409。
发起进审计（`research_run`）——它要花模型额度、要落产物。

进度不在这个接口返回：它经 SSE（`role_progress`，角色 `insight` / `media` / `host`）推送（FR-69），
本接口只回答「跑完了没有、各角色成没成」。任务状态存在**进程内**（编排器单例），
因此与其他进程内状态一样要求单 worker（ADR-05）；进程重启后旧任务查不到——
这与报告不同：报告有库表，而研判的结果本就是磁盘上的三方产物，没有中间态需要延续。

> ⚠️ **T-34 联调前，这两个接口根本不存在**（设计规格 6.1 的 API-22/23 一直缺实现）：
> 引擎、两个研究员、主持人、编排器都已就绪并有测试，但**没有入口能启动它**——
> 链路到「预警」就断了，报告也因此永远拿不到三方产物、只会以「材料不足」失败。
> 本轮补上了 `app/services/research/` 与 `app/routers/research.py`。
> 界面上暂时**没有发起入口**（工作台只有事件与处置），演示时用接口或脚本发起。

## 处置工单（T-25）

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| GET | `/api/v1/tickets` | 工单列表（状态/事件/承办人筛选 + 分页） | `ticket:read`（分析师 + 公关） |
| POST | `/api/v1/tickets` | 为事件开工单（同一事件只允许一张未关闭的工单） | `ticket:flow` |
| GET | `/api/v1/tickets/{ticket_id}` | 工单详情 + 处置轨迹（FR-50） | `ticket:read` |
| PUT | `/api/v1/tickets/{ticket_id}` | 状态流转 / 认领 / 追加批注 | `ticket:flow`；**关闭需 `ticket:close`** |
| POST | `/api/v1/events/{event_id}/response-draft` | 生成公关回应草案（FR-51，**不落库**） | `draft:write` |
| POST | `/api/v1/tickets/{ticket_id}/draft` | 采纳草案（写入工单 + 记轨迹） | `draft:write` |

**管理员不在这条链路里**：读、写、关都没有——PRD 第 2 章说管理员「不参与业务研判/处置」。
**关闭工单（已处理 / 已忽略）仅公关**：同一个 PUT 接口按目标状态判权
（路由层按 `status` 选权限，服务层再拦一次，不依赖调用方自觉）。

### 状态机

```
待研判 pending ──跟踪中 tracking ──已核实 verified ──辟谣处置 debunked
      └──────────────┴───────────────┴──────────────┴──→ 已处理 resolved
                                                     └──→ 已忽略 ignored
```

- **只能向前**：已核实的工单不能退回待研判（判错了在批注里说明，不改历史）；
- **终态不再流转**：已处理 / 已忽略都是终态，重新处置应当新建工单
  （已关闭的工单不阻塞同事件新开单）；
- 响应里的 `allowed_transitions` 直接给出可选项，**前端不必自己维护一份状态机**。

### 处置轨迹（FR-50）

轨迹只在 `ticket_log` 里长，工单表只存当前状态，两者同事务写。
动作取值与 DDL 注释一致：`create` / `claim` / `status_change` / `comment` / `draft_adopt`
（首次转入「跟踪中」记为 `claim` 认领，比笼统的「状态流转」更贴合事实）。
只传 `note` 即追加批注，不动状态。

### 回应草案（FR-51 / 附录 B.5）

**三段式**：事实澄清 / 情绪疏导 / 行动方案（外加「发布前请确认」的自检提示）。
证据来自**既有分析结果**（事件统计、观点聚类标签、高风险内容原话、官方既有表态），
不重跑分析——口径必须与大盘、报告一致（ADR-02 的同一条纪律）。

- **生成不落库**：生成是探索动作，可能反复试；落库的是「采纳」的那一份，
  这样工单上的草案永远是人确认过的版本；采纳进审计（`draft_adopt`）。
- 用的是**报告引擎角色的模型配置**（撰写类任务，成稿质量），不单开一套 `PR_*` 密钥。
- 模型不可用时明确失败（`ANA_301`），**不返回占位文本**——空话式的「草案」
  比没有草案更危险。

## 态势大盘（T-26）

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| GET | `/api/v1/dashboard/metrics` | 五个指标一次给全（`topic_id` / `hours` 可选） | `dashboard:read`（三方） |

响应里的 `metrics` 包含 `overview`（指标卡）/ `volume_trend`（FR-36）/
`sentiment_distribution`（FR-37）/ `source_distribution` / `hot_words`（FR-39），
外加 `computed_at` 供前端做自动刷新（FR-41）。

**告警滚动列表（FR-38）不在这里**：走 `/api/v1/alerts` 直接查（实时性优先，
`alert_record` 上有状态+时间索引）。在大盘里再物化一份，只会多出第二个事实源。

### 物化：不是「请求时算」

设计规格 8.2 要求聚合结果定时物化，实现为：

```
后台任务（每 60 秒一轮，随应用生命周期启停）
  → 重算 5 个指标 × (全局 + 每个启用主题) → 覆盖写入 dashboard_metrics
读路径：查物化表（一次索引查询）→ 缺失或过期（TTL 90 秒）就同步补算一次
```

**实测收益**：冷启动（物化表为空）首次请求 441ms，命中物化 **16~34ms**。
读路径自愈是刻意的——服务刚起来或后台任务还没跑第一轮时，直接返回空指标会让
大盘首屏一屏空图，看起来像坏了。

```bash
python -m app.services.dashboard.refresh              # 手动刷新（补算/排障）
python -m app.services.dashboard.refresh --topic <id> # 只刷某个主题
```

### 指标口径

| 指标 | 口径要点 |
|------|---------|
| `volume_trend` | 时间轴是 `analyzed_at`（打标时间）；**空桶补 0**（直接连线会把「没数据的时段」视觉上抹平）；曲线起点取「窗口起点」与「最早一条数据」的较晚者——批量补跑的数据硬套 7 天窗口会得到一根尖峰 + 168 个 0 |
| `sentiment_distribution` | 三类情感即使为 0 也返回（否则前端环形图缺一块） |
| `overview.negative_ratio` | 无数据时为 **null**，不是 0（0 会被读成「统计过了，全都不负面」） |
| `overview.high_risk_event_count` | 按**事件的生效风险**统计（人工复核过即人工值），且**排除已平息的事件**——否则这个数字只会随时间单调上升、失去信号意义；主题维度下按该主题的事件统计 |
| `overview.high_risk_content_count` | 另一个口径：S/A 级**内容**条数。两者分开给，不混成一个数 |
| `hot_words` | **不重新分词**：词频来自既有的结构化产出（实体名 + 主题词包），实体权重 1.0、词包词 1.2，响应里用 `kind` 标明来源。理由：① 复用分析结果而不是另起一套口径（ADR-02）；② 通用分词在舆情短文本上会切出大量噪声，而「星耀科技」「散热问题」这类实体与词包词才是运营关心的热词 |

## 报告中心（T-27）

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| POST | `/api/v1/reports` | 发起生成（**异步**，立即 202 返回 `report_id`） | `report:generate`（分析师/管理员） |
| GET | `/api/v1/reports` | 报告列表（类型/主题/事件/状态筛选 + 分页，不含正文） | `report:read`（三方） |
| GET | `/api/v1/reports/{report_id}` | 详情与在线预览（含 Markdown 原文） | `report:read` |
| GET | `/api/v1/reports/{report_id}/download` | 导出 `md` / `html` | `report:export`（三方） |

目标三选一：`event_id`（事件专项）、`topic_id`（周期简报）、`query`（显式主题词）。
`query` 同时是报告引擎加载三方产物的键（产物文件名里带的就是它），因此由事件标题
或主题名称**统一派生**，不让调用方各传各的。

列表可按 `report_type` 筛选（`event` / `periodic`）：报告类型是前端页面上的一级分类，
只在前端筛当前页会让第二页的同类报告凭空消失。**只给 `query` 的报告没有 `event_id`/`topic_id`**，
但标题由 `query` 确定性派生，前端「重试」据此反推目标。

### 生成是异步长任务

```
POST /reports → 落 status=generating 的记录 → 起后台任务 → 202 返回
后台任务     → 报告引擎产出五段式 → 落盘 md/html → 写 report_file → 更新状态 → 发事件
```

一份报告实测约 **53~60 秒**（3869 字）。进度经事件总线（`ROLE_PROGRESS`，
角色 `report`）推给前端，复用 T-18 的 SSE 通道，不再开一套轮询协议。
任务耗时与结果写 `task_run`（`task_type=report`），供 T-28 的运维页检索。

**正文同时存库**（`report_file.content_md`）：预览与导出不读磁盘，
产物目录被清理或迁移后报告依然可看可下——磁盘文件仍是交付物，两者互为备份。

### PDF 走浏览器打印（D-07）

- `file_type=md` → **附件下载**（`Content-Disposition: attachment`）
- `file_type=html` → **内联返回**自包含 HTML（样式内联），浏览器打开后打印即可另存为 PDF；
  响应头带 `X-Export-Hint: pdf-by-browser-print` 供前端决定按钮行为
- `file_type=pdf` → **422 + 说明替代路径**。服务端不做 PDF 渲染（D-07）；
  明确拒绝而不是悄悄返回一份 HTML——那会让调用方以为拿到了 PDF

> 这处与设计规格 API-28 的「下载 PDF / Markdown」措辞不同，差异源于 D-07 的决策，
> 已在 SPEC 6.1 注明。

### 三个踩过的坑（都在代码注释里）

1. **后台任务不能复用请求的会话**：请求级 `session_scope` 在响应发出前就提交关闭了，
   后台任务往里写会**静默丢失**——实测出现过「报告落盘成功、日志写着完成，
   但 `report_file` 是空的、状态永远停在 `generating`」。后台任务一律走
   `run_generation_task`（自己开会话）。
2. **启动时必须收尾在途任务**（`recover_interrupted`）：进程内后台任务随进程消失，
   留下的 `generating` 会让前端永远显示「生成中」，**判重还会把该事件永久锁死**
   （「已有正在生成的报告」，却永远等不到完成）。启动时统一标记为失败并说明原因。
3. **审计目标可能只有执行后才知道**：新建报告时 `report_id` 是函数内生成的，
   装饰器的 `target="report:{report_id}"` 渲染不出来，审计行里留的是字面占位符。
   现在函数内用 `note_target()` 显式登记目标（与 `note_change` 同一套机制）。

### 存量产物补登记

报告产物是「先落盘、后写库」的，因此可能出现**磁盘上有产物、库里没有记录**的情况
（T-17 时期直接调引擎生成的报告、以及早期写库丢失的那批）。补登记命令：

```bash
python -m app.services.report.backfill --dry-run   # 只看会补哪些
python -m app.services.report.backfill             # 补登记（幂等：已登记的文件会 skip）
```

判重按**文件名**而不是完整路径：库里存的是相对项目根的路径、扫描得到的是绝对路径，
按完整路径比会把「已登记」判成「未登记」，于是每次执行都补一遍、报告中心出现重复条目
（实测踩过）。补登记的记录 `created_by` 为空（系统补账），`generated_at` 取文件名里的
时间戳——那是产物真正生成的时间。能对上主题名的会挂到该主题上，便于按主题检索。

### 判重

同一事件 / 主题 / 标题已有 `generating` 报告时拒绝重复发起（409）——
一份报告约一分钟、上万 token，重复点按钮不该重复花钱。
已生成或失败的记录不阻塞重新生成（重新生成是正常需求）。

## 系统运维（T-28）

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| GET | `/api/v1/system/tasks` | 任务执行状态看板（FR-72） | `system:read`（管理员） |
| GET | `/api/v1/system/tasks/summary` | 按类型的次数/成功率/平均耗时 | `system:read` |
| GET | `/api/v1/system/logs/modules` | 可检索的日志模块及文件现状 | `system:read` |
| GET | `/api/v1/system/logs` | 日志检索（FR-73） | `system:read` |
| POST | `/api/v1/system/reset` | 演示数据重置（FR-75，**需确认口令**） | `system:read` + 口令 |

运维面**只给管理员**（PRD 第 2 章），重置也没有单独权限点——真正防误操作的是确认口令。

### 任务看板（FR-72）

`task_run` 是「谁在什么时候跑了什么、多久、成没成」的唯一事实源。已接入埋点的任务：

| 类型 | 来源 |
|------|------|
| `report` | 报告生成（T-27） |
| `alert` | 预警补扫（T-24 的 `/alerts/scan`） |
| `dashboard` | 大盘指标刷新（T-26 的每轮后台任务） |

埋点统一走 `track_task()`（`app/services/system/tasks.py`）：
进入即 `running`、退出即 `success`/`failed`（异常也留痕并原样抛出）。
**耗时用 `time.perf_counter()` 量**，不由 `finished_at - started_at` 推算——
那两个字段是 Unix 秒，亚秒任务会一律显示 0ms，看板上会是一片假的「瞬时完成」。

### 日志检索（FR-73）

- 按**模块**（`logs/{module}.log`，app + 各角色）、**级别**、**关键字**、**trace_id**、
  **时间范围**检索；
- 只回读**文件尾部**并按块读取：日志动辄几十兆，全量读进来再过滤会把内存吃掉；
- 只返回最近的 limit 行（**没有 offset 分页**）——查日志的诉求是「最近发生了什么」，
  往前翻页不如收紧时间范围；
- **读出口统一脱敏**：写入时已脱敏，但第三方库（httpx、sqlalchemy）自己打印的内容
  不受我们控制（实测 httpx 会把含查询参数的完整 URL 打进 INFO），读出口再兜一道；
- **不提供写/删日志的接口**：日志是排障材料，删掉的正是案发现场。

> 角色日志文件当前基本是空的：只有引擎层用 `role_logger()` 绑定了 `role` 的日志
> 才会分流到 `logs/{role}.log`，HTTP 层的日志统一进 `app.log`。检索时优先用 `app`。

### 数据留存（FR-74）

配置项 `ANALYSIS_RETENTION_DAYS`（默认 180）在运行配置白名单里，管理员可热更新；
执行入口是 `python -m app.services.system.retention`（默认只看，加 `--apply` 执行）。

**原始区永不清理**：FR-74 原文写的是「原始内容与分析结果的保留周期」，
但「删除原始内容」与 ADR-03「原始表只读」直接冲突。这里显式选择了后者并说明：
**原始数据的留存由重新生成数据集管理**（`python data/generator/generate.py`），
本项目不删除它。遇到规范冲突应当讲清楚选了什么、为什么，而不是悄悄删了或悄悄不删。
可清理的只有**可独立重算**的分析结果（内容标签、多模态产物）——实体/事件/聚类不在此列，
清掉标签却留着图谱会得到互相矛盾的数据。

### 演示数据重置（FR-75）

`POST /api/v1/system/reset` 的四条护栏：

1. **不碰原始区**：`scope=raw` 被明确拒绝并指向数据生成器；
2. **不删账号与审计**：`sys_user` / `audit_log` 永不重置——清了账号就登不进来，
   清了审计就查不到「谁执行的这次重置」；
3. **必须原样填写确认口令**（`analysis` → `RESET_ANALYSIS`，`all` → `RESET_ALL`）；
4. **重置动作进审计**（`data_reset`），**只看不删（`dry_run`）也留痕**——
   那是「有人动过重置入口」的事实。

可重置范围：`analysis`（标签/多模态/事件/聚类/图谱）、`reports`、`alerts`（告警+工单）、
`runtime`（大盘物化 + 任务状态）、`all`。建议先用 `dry_run=true` 看清影响面
（当前 `analysis` 约 5300 行 / 9 张表）。

### 当前数据状态（2026-10-16，T-34 联调后）

- `content_analysis` **2835 行**，全部归属到三个演示主题（星耀 1193 / 味鲜居 900 / 新能源车 742）。
- `modality_asset` 为 **0 行**：T-09 的 OCR 因数据集里是占位图片 URL 全部失败，
  且当时的接入未落库。详情的多模态视图（含失败状态）由离线测试覆盖；
  要用真实数据验证，需跑一次带多模态解析的接入（`python -m app.services.pipeline --with-media`）。
- 三个事件都有关联内容与簇：`星耀XPhone15` 1193 条、`味鲜居后厨` 890 条、
  `新能源车购置税` 0 条（该主题内容以新闻门户与论坛为主，尚未跑过监测链路）。
  合计 `event_content` 2083 行、`opinion_cluster` 24 个（每事件 12 个）、
  `entity` 65 / `entity_relation` 123。
  `spread_node` 为 0 行、事件聚合列为空——两者都是读取时现算的（见上）。
- 预警规则 3 条（种子脚本创建），告警 14 条（两轮补扫的产物）：
  负向声量激增 6 条、敏感词命中 4 条、高权重账号发声 4 条。
  **14 条的 `notify_status` 都是空**——它们创建时都没有尝试外发
  （补扫时未下发通知），因此监测配置页的「通知渠道」会如实显示「未记录」。
- 监测配置的演示数据：**3 个主题**（`味鲜居后厨卫生` / `新能源车购置税减免` / `星耀XPhone15`，
  词包 5 / 3 / 6 个词，含 0.6~0.9 的非默认权重）、**4 类信源通道**（全启用，
  轮询 15/30/30/60 分钟、回溯 24/24/72/168 小时）、**3 条预警规则**
  （激增 A 级 0.5、敏感词 S 级 3 条、高权重账号 B 级 3 条）、
  **10 个可热更新配置键**（均未被运行时覆盖）。
  三个主题的 `owner_id` 是同一个账号——**分析师账号因此对词包是只读的**
  （`topic:write` 对分析师是「仅本人资源」，后端会 403），演示配置变更请用管理员。
- 处置工单 4 张 / 轨迹 16 条，覆盖 FR-49~51 的完整外观：`味鲜居后厨` 已核实
  （有承办人、已采纳草案）、`星耀XPhone15` 跟踪中与已处理各一张（后者是终态）、
  `新能源车购置税` 待研判（带来源告警关联）。它们同时是 T-31 的联调产物——接口层的
  对照验证记录见 `front/README.md`（非法迁移 409 / 重复开单 409 / 分析师关闭 403 /
  管理员读工单 403）。
- 报告 11 份（事件专项 / 周期简报），覆盖 `generated` 与 `failed` 两种终态：
  已生成的都是完整的五段式（约 2800~4200 字），失败的带着真实原因
  （「主持人研判、私域分析、公域分析 的产物尚未生成」这类材料缺口），
  正好用来演示 FR-62 的「失败给原因与重试」。T-34 联调新生成的那份是
  `味鲜居后厨` 的 **2784 字**报告（三方产物齐备后即可生成）。
- 事件风险等级人工复核（FR-24 / API-15）已实现：`event` 表在 T-24 前补了
  `manual_override` 与 `override_reason` 两列（DDL → ORM → `check` 三步走，
  已有库用等价的 `ALTER TABLE` 补列，因为 `init_db` 会 DROP 重建）。
  **算法原始结论不另存一列**——它每读一次都由关联内容现算，两者在响应里同时给出。

## 端到端联调（T-34）

按 PRD 9.2 的**场景二「连锁餐饮食品安全投诉」（味鲜居后厨卫生）**跑通
「监测 → 预警 → 研判 → 处置 → 报告」，全流程无阻断性错误，演示数据可由
「演示数据重置」复原（dry-run 报出 16 张表 / 7469 行的影响面，账号与审计受保护）。

```bash
# ① 监测 → 分析（一次命令：接入 → 打标 → 事件 → 图谱/聚类 → 向量索引）
uv run python -m app.services.pipeline --topic 味鲜居后厨卫生

# ② 预警：补扫一次（notify=false 不打扰收件人；界面入口在「监测配置 → 预警规则」）
curl -b cookie -X POST 'localhost:8000/api/v1/alerts/scan?window_minutes=1440&notify=false'

# ③ 研判：发起 → 轮询状态（约 17 分钟，见下）
curl -b cookie -X POST localhost:8000/api/v1/research -d '{"topic_id":"tp17920514962bfdc9"}'
curl -b cookie localhost:8000/api/v1/research/<task_id>

# ④ 处置：工单流转 → 批注 → 生成回应草案 → 采纳（工作台底部处置区，或接口）
# ⑤ 报告：POST /api/v1/reports {"event_id":"evt_味鲜居后厨"} → 轮询 → 导出 md/html
# ⑥ 复位可复原性：POST /api/v1/system/reset {"scope":"all","dry_run":true,"confirm":"RESET_ALL"}
```

实测结果（2026-10-16，`--scale small`）：

| 环节 | 结果 |
|------|------|
| 接入 | 1933 条（内容 192 / 评论 1741）→ 去重后 **890** 条（合并 1043 条，54%），疑似水军 2 个 |
| 打标 | 890 条（重跑时全部跳过——幂等） |
| 事件与图谱 | 事件 `evt_味鲜居后厨` 关联 890 条；**12 个观点簇**；**23 实体 / 60 关系** |
| 向量索引 | 890 条，维度 1024，26.4s |
| 预警 | 该事件 6 条告警（B/S/A），生命周期含 claimed / closed |
| 研判 | `insight` 542s、`media` 198s（54 条材料）、主持人五维研判 + 最终裁判，**总计约 17 分钟** |
| 处置 | 工单流转至「已核实」→ 批注 → 回应草案（1190 字，四段）→ 采纳（草案落库） |
| 报告 | 五段式 **2784 字**，章节齐全；导出 md 7392 字节（attachment）/ html 10173 字节（inline，`X-Export-Hint: pdf-by-browser-print`） |

> ⏱ **研判是最慢的一环**：主持人每个维度一次模型调用（实测 ~110s/次，还带重试），
> 五维研判 + 最终裁判十几分钟属正常。演示时先起研判、再去讲别的环节。

### 联调发现并修掉的五个断点

联调的价值不在于「跑了一遍」，而在于把**只有串起来才会暴露**的问题找出来。这五处都在功能上都「有实现」，
但链路走不通或会静默失效：

1. **监测→分析没有运行入口**：接入与打标各自实现了，但没有任何一处把它们串起来
   （先前那批分析结果是历史会话用一次性脚本跑的）。→ 补 `app/services/pipeline/`（见上）。
2. **研判没有任何接口**：设计规格 6.1 的 API-22/23 一直缺实现，引擎与编排器都在、测试也全绿，
   但**没有入口能启动它**——链路到「预警」就断了。→ 补 `app/services/research/` + `app/routers/research.py`。
3. **打标完成事件的发布写错了**（`analytics.service._publish_labeled`）：把 uid→标签的字典
   当列表遍历并取 `label.content_uid`（该字段不存在），于是每次真打到标都在链路末尾抛
   `AttributeError`——**事件驱动预警静默失效**，且只在有新增内容时发作。
   → 取字典键作为 uid，并补 `tests/test_analytics.py::PublishLabeledTest` 盯住这条接缝。
4. **编排层过早宣告完成**：两个研究员返回就把任务置 `completed`，而**主持人还在研判**
   （它是事件驱动的，不归编排层调用）。调用方看到 completed 就去生成报告，只能以
   「产物尚未生成」失败。→ `_await_host()` 等主持人收尾（上限 900s），超时如实标失败并说明原因。
5. **事件标题与产物键不一致**：三方产物按**研判主题词**（主题名）命名，而「从事件发起报告」
   按**事件标题**找产物；事件由预警链路创建时标题写的是主题**关键词**（`味鲜居后厨` vs
   `味鲜居后厨卫生`），报告因此找不到产物、报「三方产物尚未生成」而看不出是键不一致。
   → `ensure_event(sync_title=True)` 由监测链路把标题同步为主题名（预警链路不开同步，免得多方改写）。

## 前端

前端是独立的 npm 工程（`front/`），依赖与后端完全隔离。

```bash
cd front
npm install            # 已配置国内镜像（.npmrc）
npm run dev            # 开发服务器 http://localhost:5173，/api 已代理到后端 8000
npm run build          # 类型检查 + 生产构建，输出 front/dist/
```

**五个业务页面全部接入真实数据**：态势大盘（T-29）、研判工作台（T-30）、
处置协同区（T-31）、报告中心（T-32）、监测配置（T-33）。

跨页面的两条链路值得单独记一笔：

1. **下钻**（大盘 → 工作台）：所有图表点击都把筛选条件带过去，参数名与后端筛选参数
   同名同值（`topic_id` / `sentiment` / `source_type` / `risk_level` / `keyword` / 时间窗），
   工作台直接透传，不做翻译层——翻译层是错位的常见来源（前端写 `shortvideo`、
   后端要 `short_video`，筛选恒为空且不报错）。
2. **告警 → 处置**（FR-38 → FR-49）：告警行的「处置」带 `event_id` + `alert_id` 跳工作台，
   开单时把 `alert_id` 一并提交，工单因此能回答「这条舆情为什么被处理」。
   工作台的筛选同步用 `router.replace`，这两个关联参数必须原样写回 URL，
   否则用户改一次筛选，关联就悄悄消失了。

处置协同区（FR-49~51）的落点在工作台底部：状态流转的可选项**由后端 `allowed_transitions`
给出**（前端不复制状态机）、只传批注即追加 `comment` 轨迹、回应草案「生成不落库 → 编辑 →
采纳落库」三步走，采纳是追加一版而非覆盖（旧版本留在轨迹里）。

报告中心页（FR-57~63）的三点取舍：**类型筛选走服务端**（只筛当前页会让第二页的同类报告
消失）；**SSE 管「看得见在动」、轮询管「知道结束了」**（进度只有 0/50/100 三档，
而「成没成、为什么失败」以库里的状态为准）；**PDF 用服务端渲染的 HTML 打印**（D-07），
不打印本页预览——两者不是同一份东西，打印出来的和下载到的不一致比多开一个标签页更糟。
报告正文预览**不用 `v-html`**：内容引用了微博/论坛的用户原文，改为解析成块结构后用模板渲染
（`utils/markdown.ts`），插值自动转义，注入面为零。

监测配置页（FR-06~13/52~54）按权限把页签分层：词包分析师可改（**限本人主题**，
后端 `OWNED_ONLY` 兜底），通道 / 预警规则 / 运行配置仅管理员，公关进不了这一页。
两处容易做错的地方：**词包是全量替换，权重必须原样提交**（漏掉就把整组词静默重置成 1.0，
因此编辑器用表格而不是标签云——标签里塞不进权重输入框）；**通知渠道是只读的**，
SMTP 凭据属于密钥与部署配置，不在运行配置白名单内，改它走 `.env` + 重启。

详细的目录说明、接口约定与开发须知见 `front/README.md`。

## 测试

```bash
uv run python -m unittest discover -v                      # 全部（928 个，约 62 秒）
uv run python -m unittest tests.test_permissions -v        # 单个模块
uv run python -m unittest tests.test_auth_api.LoginTest -v # 单个测试类
```

纯标准库 unittest，不需要 pytest——不构成额外的环境负担。

> 用 `uv run`（或先激活 `.venv`）执行：系统 Python 没有项目依赖，直接
> `python -m unittest` 会因找不到 loguru 而全线报错。
>
> **测试全程离线**——不连数据库、不调模型，依赖在测试里换成内存替身
> （`tests/fakes.py`）。这一点专门验证过：把 `DB_PORT` 指向不可达端口，
> 接口测试照常全绿、耗时不变。
>
> ⚠️ **纯逻辑测试是毫秒级的，接口测试约 0.55 秒/个**（实测拆解：`create_app()`
> 0.09s、单次 ASGI 请求 0.3~0.4s），因此整套 62 秒里绝大部分是接口用例的脚手架
> 开销——每个 `IsolatedAsyncioTestCase` 都新建事件循环与请求往返。
> 要提速的方向是**按测试类共享应用与客户端**（依赖覆盖逐用例切换），不是砍测试。

## 目录结构

```
sentiment_analyse/
├── app/                     接入层：路由、请求响应模型、服务编排
│   ├── main.py              FastAPI 应用装配（公开/受保护两个路由组）
│   ├── errors.py            错误码表与统一异常处理（设计规格 6.3）
│   ├── observability.py     请求级 trace_id 与访问日志
│   ├── security.py          鉴权依赖：CurrentUser / require_permission
│   ├── routers/             路由（health、auth、users、topics、channels、config、contents、events、alerts、tickets 与 SSE）
│   ├── schemas/             请求响应模型
│   └── services/            业务用例编排（auth、audit、monitoring、content、event、alert、
│                            ticket、dashboard、report、research、pipeline、system、realtime）
├── engines/                 引擎层
│   ├── contracts/           契约：配置、角色定义、研究维度、证据模型
│   ├── common/              公共能力：日志、事件总线、LLM 客户端
│   ├── ingestion/           接入与解析：字段映射、OCR、清洗、去重
│   ├── analytics/           认知分析：情感、烈度、风险分级、实体、聚类
│   ├── alerting/            预警引擎：规则匹配、分级、通知下发
│   ├── orchestration/       研判编排：角色注册、并行启动、事件发布
│   ├── insight_agent/       私域研究员
│   ├── media_agent/         公域研究员
│   ├── host_agent/          主持人研判
│   └── report_engine/       报告引擎
├── data/                    数据资产（生成器、SQL、评测集）
├── docs/                    需求与设计文档
├── sql/                     建表 DDL（分析区 / 业务区）
├── front/                   前端工程（Vue 3 + Vite + TS，独立的 npm 工程）
├── tests/                   单元测试（标准库 unittest，无需额外依赖）
├── logs/                    运行日志（按角色分流，已 gitignore）
├── main.py                  服务启动入口
├── pyproject.toml           依赖声明与镜像配置
└── .env		         环境配置
```

**模块依赖方向**（设计规格 3.2）：

```
contracts ← common ← 各引擎 ← services ← app
```

`insight_agent` 与 `media_agent` 互不依赖。跨模块只通过 `contracts` 定义的契约通信。

## 开发须知

几条容易踩的约束，来自设计规格的 ADR 与隐性约束清单：

1. **不得修改原始数据表**。`data/` 交付的 9 张原始表是采集产物，只读；
   分析结果写入 `content_analysis` 等独立表（ADR-03）。
2. **不得在 `insight_agent` 内重算情感**。一律复用 `analytics` 的标签，
   否则会出现"大屏显示负向 60%、报告里写负向 45%"的自相矛盾（ADR-02 / D-08）。
3. **不得"顺手统一"字段命名**。微博与抖音表的 `comment_count` / `comments_count`
   等差异是刻意保留的，正是接入层映射需求的存在理由。
4. **配置不得硬编码**。所有可变参数经 `engines/contracts/config.py` 读取。
5. **密钥不得进日志**。记录外部服务异常时使用 `engines.common.logging.mask_secret()`。
6. **环境变量优先级高于 `.env`**。若本机已设置 `TAVILY_API_KEY` 一类变量，
   会覆盖 `.env` 中的同名配置——排查「配置没生效」时先确认系统环境变量。

### 环境注意事项

- **FastAPI 0.143 的路由遍历行为已变**：`app.include_router()` 不再把子路由平铺进
  `app.routes`，而是插入惰性的 `_IncludedRouter` 包装对象。遍历 `app.routes` 取 `path`
  会得到 `None`（不是 Bug，接口本身工作正常）。需要路由清单时请改用
  `app.openapi()["paths"]`，例如后续实现权限矩阵自动校验时。
- 运行环境：Python 3.12 / FastAPI 0.143 / Starlette 1.8 / pydantic 2.14。测试客户端
  建议安装 `httpx2`（Starlette 已弃用旧 `httpx` 组合，会有 DeprecationWarning）。
