"""模拟舆情数据生成入口。

用法：
    # 小样本（默认，秒级完成，适合开发期反复重建）
    python data/generator/generate.py

    # 放量到 PRD 规模（内容 1 万 / 评论 5 万）
    python data/generator/generate.py --scale prd

    # 固定结束时间，保证多次生成结果一致（默认取当前时间，即剧情回溯 72 小时）
    python data/generator/generate.py --end-time "2026-10-14 12:00:00"

同一 --scale / --seed / --end-time 组合下，输出完全可复现。
"""

import argparse
import os
import random
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import builder as builder_mod          # noqa: E402
import emitter                         # noqa: E402
import eval_builder                    # noqa: E402
import schema                          # noqa: E402
import verifier                        # noqa: E402

CST = timezone(timedelta(hours=8))
GENERATOR_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.dirname(GENERATOR_DIR)


def _parse_end_time(value):
    if not value:
        return datetime.now(CST).replace(microsecond=0)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=CST)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(
        f"无法解析结束时间：{value}，请使用 'YYYY-MM-DD HH:MM:SS' 格式")


def build_parser():
    parser = argparse.ArgumentParser(
        description="生成智能舆情分析平台的模拟数据集",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    parser.add_argument("--scale", choices=sorted(builder_mod.SCALE_PROFILES),
                        default="small", help="数据规模档位（默认 small）")
    parser.add_argument("--seed", type=int, default=42,
                        help="随机种子，固定后输出可复现（默认 42）")
    parser.add_argument("--out", default=DATA_DIR,
                        help="输出根目录（默认 data/）")
    parser.add_argument("--end-time", type=_parse_end_time, default=None,
                        help="剧情结束时间，默认当前时间")
    parser.add_argument("--dump-samples", action="store_true",
                        help="额外输出全量带标签样本（体积较大，默认关闭）")
    return parser


def _print_summary(stats):
    print("\n生成统计")
    print("-" * 68)
    print(f"规模档位：{stats['scale']}    随机种子：{stats['seed']}")
    print(f"时间锚点：{stats['end_time']}")
    print(f"账号总数：{stats['accounts']}")
    print(f"内容总数：{stats['content_total']}    评论总数：{stats['comment_total']}")
    print("\n按信源分布：")
    for source, counts in stats["by_source"].items():
        print(f"  {schema.SOURCE_LABELS[source]:<8} 内容 {counts['content']:>6} "
              f"/ 评论 {counts['comment']:>6}")
    print("\n评论情感分布：")
    for cls, ratio in stats["comment_sentiment"].items():
        print(f"  {cls:<10} {ratio:.1%}")
    print("\n按场景分布（含内容与评论）：")
    for key, count in stats["by_scenario"].items():
        print(f"  {key:<24} {count:>6}")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)

    sql_dir = os.path.join(args.out, "sql")
    eval_dir = os.path.join(args.out, "eval")

    print(f"开始生成：scale={args.scale} seed={args.seed}")
    end_time = args.end_time or datetime.now(CST).replace(microsecond=0)
    data = builder_mod.Builder(seed=args.seed, end_time=end_time,
                               scale=args.scale).build()
    _print_summary(data.stats)

    os.makedirs(sql_dir, exist_ok=True)
    os.makedirs(eval_dir, exist_ok=True)

    print("\n输出 SQL ...")
    emitter.write_schema_file(sql_dir)
    source_files = emitter.write_source_files(data, sql_dir)
    emitter.write_account_file(data, sql_dir)
    emitter.write_import_all(sql_dir, source_files)
    for name in sorted(os.listdir(sql_dir)):
        size = os.path.getsize(os.path.join(sql_dir, name))
        print(f"  {name:<28} {size / 1024:>8.1f} KB")

    print("\n输出评测集 ...")
    eval_rng = random.Random(args.seed + 1000)   # 评测集抽样与数据集随机流解耦
    sentiment_records, risk_records, eval_paths = eval_builder.build_all(
        data, eval_rng, eval_dir)
    print(f"  sentiment_eval.jsonl  {len(sentiment_records)} 条")
    print(f"  risk_eval.jsonl       {len(risk_records)} 条")

    if args.dump_samples:
        samples_path = os.path.join(eval_dir, "samples_labeled.jsonl")
        emitter.write_samples_jsonl(data.samples, samples_path)
        print(f"  samples_labeled.jsonl {len(data.samples)} 条")

    result = verifier.verify(data, sql_dir, eval_dir)
    print(result.render())
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
