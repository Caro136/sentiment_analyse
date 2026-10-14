"""归档课程提供的真实舆情数据到 data/sql/legacy/。

课程原始数据集来源（尚硅谷「舆情分析（多智能体）」day01 资料）：
  F:/Download/5_杨涛/5.其他/尚硅谷大模型项目之舆情分析（多智能体）/
  day01_项目架构分析&&Web层搭建/4_other/

这些数据是真实爬取的微博与抖音样本，合计 2165 条（内容 142 / 评论 2023），
主题为「高考」与「NBA总决赛」。它们**不是**三个企业场景的数据来源——
主题不同，混入会污染监测词包与语义聚类。

保留它们的价值在于：
  1. 作为「公共事件」主题的真实样例，验证多主题并存时的主题隔离；
  2. 我们生成的模拟数据表结构就是对齐这批数据设计的，可用它做接入层兼容性回归。

用法：
    python data/generator/archive_legacy.py
    python data/generator/archive_legacy.py --src "<自定义源目录>"
"""

import argparse
import os
import shutil
import sys

GENERATOR_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.dirname(GENERATOR_DIR)

DEFAULT_SRC = (r"F:\Download\5_杨涛\5.其他\尚硅谷大模型项目之舆情分析（多智能体）"
               r"\day01_项目架构分析&&Web层搭建\4_other")

LEGACY_FILES = [
    "weibo_note.sql",
    "weibo_note_comment.sql",
    "douyin_aweme.sql",
    "douyin_aweme_comment.sql",
]

LEGACY_NOTE = """-- 课程提供的真实舆情数据（公共事件主题：高考 / NBA总决赛）
--
-- 来源：尚硅谷「舆情分析（多智能体）」课程 day01 资料
-- 规模：内容 142 条、评论 2023 条，覆盖微博与抖音两个平台
--
-- 说明：
--   1. 本目录数据不属于三个企业场景（消费电子质量争议 / 连锁餐饮食品安全 / 行业政策利好），
--      主题不同，请勿与 10~40 号文件的数据混入同一监测主题，否则会污染词包与聚类结果。
--   2. 这批数据的表结构与 data/sql 下生成的数据完全一致（生成器即以此为基准设计），
--      可用于验证接入层对真实爬取数据的兼容性。
--   3. 数据库名不同：原始文件写入 media_crawler_v1，本项目的表结构建在 sentiment_monitor。
--      如需导入，请先建库并把文件中的库名替换，或直接用文本工具批量替换表前缀后导入。
--
-- 导入参考：
--   CREATE DATABASE IF NOT EXISTS sentiment_monitor DEFAULT CHARACTER SET utf8mb4;
--   -- 将下列文件内容中的 `media_crawler_v1` 上下文去掉后，逐个导入
"""


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="归档课程原始舆情数据")
    parser.add_argument("--src", default=DEFAULT_SRC, help="课程数据源目录")
    parser.add_argument("--dst", default=os.path.join(DATA_DIR, "sql", "legacy"),
                        help="归档目标目录")
    args = parser.parse_args(argv)

    if not os.path.isdir(args.src):
        print(f"[跳过] 源目录不存在：{args.src}")
        print("       该目录来自课程资料，若已移动位置，请用 --src 指定。")
        print("       跳过归档不影响 generate.py 生成的数据集。")
        return 0

    os.makedirs(args.dst, exist_ok=True)
    copied = []
    for name in LEGACY_FILES:
        src_path = os.path.join(args.src, name)
        if not os.path.exists(src_path):
            print(f"[缺失] {name}")
            continue
        shutil.copy2(src_path, os.path.join(args.dst, name))
        size_kb = os.path.getsize(src_path) / 1024
        copied.append(name)
        print(f"[归档] {name:<28} {size_kb:>8.1f} KB")

    note_path = os.path.join(args.dst, "README.txt")
    with open(note_path, "w", encoding="utf-8") as f:
        f.write(LEGACY_NOTE)
    print(f"[说明] README.txt 已写入 {args.dst}")
    print(f"归档完成，共 {len(copied)} 个文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
