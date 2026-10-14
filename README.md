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
| T-02 ~ T-36 | 数据层、分析链路、研判链路、业务能力、前端 | ⏳ 待开展 |

## 环境准备

要求 Python 3.11+，依赖管理用 [uv](https://docs.astral.sh/uv/)（已配置国内镜像）。

```bash
# 1. 安装核心依赖（骨架运行所需）
uv sync

# 2. 按任务追加依赖组
uv sync --extra agents          # T-13~T-16 研判链路（langgraph / langchain）
uv sync --extra analytics       # T-10~T-11 认知分析（jieba / scikit-learn）
uv sync --extra embeddings      # T-12 向量编码（sentence-transformers）
uv sync --extra vector          # T-12 Milvus 客户端
uv sync --extra multimodal      # T-09 OCR（mcp）
uv sync --extra auth            # T-19 认证（passlib / itsdangerous）
uv sync --extra report          # T-17 报告渲染（mistune）

# 3. 准备配置
cp .env.example .env            # 按实际环境填写密钥
```

**未填密钥不影响启动**：服务会照常运行并在日志中列出未就绪的能力，
相关接口调用时才降级或报错。这样开发期不必等所有密钥到位才能开工。

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

## 目录结构

```
sentiment_analyse/
├── app/                     接入层：路由、请求响应模型、服务编排
│   ├── main.py              FastAPI 应用装配
│   ├── routers/             路由（health 已实现，业务路由按任务逐步注册）
│   ├── schemas/             请求响应模型
│   └── services/            业务用例编排
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
├── logs/                    运行日志（按角色分流，已 gitignore）
├── main.py                  服务启动入口
├── pyproject.toml           依赖声明与镜像配置
└── .env.example             环境配置模板
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
