"""监测→分析链路的编排（T-34 端到端联调的入口）。

**职责**：把一个监测主题从「原始表里的数据」推进到「可研判的分析结果」——
接入（读取 → 清洗 → 去重 → 水军识别 → 多模态）→ 认知分析打标 → 事件关联 →
实体图谱 → 观点聚类 → 向量索引。

**为什么需要它**：上面每个环节都早已实现，但分散在两个引擎里
（`engines.ingestion` 与 `engines.analytics`），**没有任何一处把它们串起来**。
没有入口的链路等于不可演示、也不可复现——T-34 的联调就卡在这里：
库里那批分析结果是历史会话用一次性脚本跑出来的，脚本本身没有留下。
编排放在 services 而不是引擎里，是因为它跨两个引擎、并要写分析区的多张表
（依赖方向：`contracts ← common ← 引擎 ← services ← app`）。

**不做**：不触发预警、不跑研判、不生成报告。这三步各自已有接口
（`POST /alerts/scan`、`POST /research`、`POST /reports`），演示脚本按顺序调用即可。
把它们也塞进本模块，会让「跑一次监测」变成一件很贵的事（研判要几分钟 LLM 调用）。

**用法**：

```bash
uv run python -m app.services.pipeline --topic 味鲜居后厨卫生
uv run python -m app.services.pipeline --topic 星耀XPhone15 --window-hours 168 --no-index
```
"""

from .run import PipelineReport, run_pipeline

__all__ = [
    "PipelineReport",
    "run_pipeline",
]
