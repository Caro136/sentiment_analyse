"""Markdown → 自包含 HTML。

**自包含**是刻意的：渲染出的 HTML 内联全部样式，不依赖任何外部 CSS 或字体文件。
报告会被下载、转发、归档，如果依赖外链样式，换台机器打开就是一篇裸文本。

顺带解决了 PDF 导出的问题：设计规格 D-07 定的方案是浏览器打印，
而浏览器打印要求页面本身就是排好版的——样式内联后直接 `window.print()` 即可。
"""

import html as html_lib

import mistune

# 报告样式：面向打印优化（A4 宽度、章节不跨页断开）
_REPORT_CSS = """
:root {
  --ink: #1f2937;
  --muted: #6b7280;
  --line: #e5e7eb;
  --accent: #1d4ed8;
  --risk-s: #b91c1c;
  --risk-a: #c2410c;
  --risk-b: #a16207;
}
* { box-sizing: border-box; }
body {
  max-width: 860px;
  margin: 0 auto;
  padding: 48px 32px 80px;
  font-family: "PingFang SC", "Microsoft YaHei", "Source Han Sans SC", sans-serif;
  font-size: 15px;
  line-height: 1.85;
  color: var(--ink);
  background: #fff;
}
h1 {
  font-size: 26px;
  margin: 0 0 8px;
  padding-bottom: 14px;
  border-bottom: 3px solid var(--accent);
}
h2 {
  font-size: 19px;
  margin: 38px 0 14px;
  padding-left: 12px;
  border-left: 4px solid var(--accent);
  page-break-after: avoid;
}
h3 {
  font-size: 16px;
  margin: 26px 0 10px;
  color: var(--accent);
  page-break-after: avoid;
}
p { margin: 10px 0; }
ul, ol { margin: 10px 0; padding-left: 26px; }
li { margin: 6px 0; }
blockquote {
  margin: 16px 0;
  padding: 12px 18px;
  background: #f8fafc;
  border-left: 4px solid var(--line);
  color: var(--muted);
}
table {
  width: 100%;
  border-collapse: collapse;
  margin: 16px 0;
  font-size: 14px;
}
th, td { padding: 9px 12px; border: 1px solid var(--line); text-align: left; }
th { background: #f1f5f9; font-weight: 600; }
code {
  padding: 2px 6px;
  background: #f1f5f9;
  border-radius: 3px;
  font-size: 13px;
}
hr { margin: 34px 0; border: none; border-top: 1px solid var(--line); }
.report-meta {
  margin: 0 0 30px;
  padding: 14px 18px;
  background: #f8fafc;
  border: 1px solid var(--line);
  border-radius: 6px;
  font-size: 13px;
  color: var(--muted);
}
.report-meta span { display: inline-block; margin-right: 20px; }
@media print {
  body { padding: 0; font-size: 12pt; max-width: none; }
  h2 { page-break-before: auto; }
  .report-meta { border: none; background: none; }
}
"""

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
{meta}
{body}
</body>
</html>
"""


def markdown_to_html_body(markdown: str) -> str:
    """Markdown → HTML 片段。"""
    renderer = mistune.create_markdown(plugins=["table", "strikethrough", "url"])
    return renderer(markdown or "")


def render_html(markdown: str, title: str, *, meta: dict | None = None) -> str:
    """渲染成完整的独立 HTML 页面。

    `meta` 是会显示在报告顶部的元信息（主题、生成时间、信源等）。
    """
    meta_line = ""
    if meta:
        items = "".join(
            f"<span>{html_lib.escape(str(key))}：{html_lib.escape(str(value))}</span>"
            for key, value in meta.items() if value not in (None, ""))
        meta_line = f'<div class="report-meta">{items}</div>'

    return _HTML_TEMPLATE.format(
        title=html_lib.escape(title),
        css=_REPORT_CSS,
        meta=meta_line,
        body=markdown_to_html_body(markdown),
    )
