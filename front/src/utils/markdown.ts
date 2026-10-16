/**
 * 报告正文的 Markdown 解析：把文本解析成**块结构**，交给组件用模板渲染。
 *
 * 两条刻意的取舍：
 *
 * 1. **不用 `v-html`，也不引 markdown 依赖**。报告正文由模型生成，而模型读的是
 *    微博/抖音/论坛的原文（用户可写的文本），里面完全可能有 `<script>` 或
 *    `![](...)`。走 `v-html` 等于把这些内容当代码执行；这里解析成数据后用
 *    Vue 模板渲染，插值自动转义——**注入面为零**，代价是只支持 Markdown 的一个子集。
 * 2. **子集就够用**：报告是引擎按固定五段式生成的（标题 + 列表 + 表格 + 少量强调），
 *    解析器覆盖这些结构即可；遇到不认识的行按普通段落渲染，不会丢内容。
 *
 * 想要完整 Markdown 时的替代路径：后端 `mistune` 渲染的自包含 HTML 已随报告落盘，
 * 导出 html 后在浏览器里看的是同一套服务端渲染结果（见 README「报告中心」）。
 */

/** 行内片段。渲染时用插值，因此 `text` 里出现 `<` 也只会原样显示 */
export interface InlineSegment {
  text: string
  bold?: boolean
  italic?: boolean
  code?: boolean
  /** 仅当链接是 http(s) 时才给 href——`javascript:` 一类协议直接降级为纯文本 */
  href?: string
}

export interface HeadingBlock { kind: 'heading'; level: number; segments: InlineSegment[] }
export interface ParagraphBlock { kind: 'paragraph'; segments: InlineSegment[] }
export interface QuoteBlock { kind: 'quote'; segments: InlineSegment[] }
export interface ListBlock { kind: 'list'; ordered: boolean; start: number; items: InlineSegment[][] }
export interface TableBlock {
  kind: 'table'
  header: InlineSegment[][]
  rows: InlineSegment[][][]
  aligns: ('left' | 'center' | 'right')[]
}
export interface CodeBlock { kind: 'code'; text: string }
export interface DividerBlock { kind: 'divider' }

export type MarkdownBlock =
  | HeadingBlock | ParagraphBlock | QuoteBlock | ListBlock | TableBlock | CodeBlock | DividerBlock

const HEADING = /^(#{1,6})\s+(.*)$/
const UNORDERED = /^\s*[-*+]\s+(.*)$/
const ORDERED = /^\s*(\d+)[.)]\s+(.*)$/
const DIVIDER = /^\s*(-{3,}|\*{3,}|_{3,})\s*$/
const TABLE_SEPARATOR = /^\s*\|?[\s:|-]+\|[\s:|-]*$/

/** 解析全文 */
export function parseMarkdown(markdown: string): MarkdownBlock[] {
  const lines = (markdown || '').replace(/\r\n?/g, '\n').split('\n')
  const blocks: MarkdownBlock[] = []
  let index = 0

  while (index < lines.length) {
    const line = lines[index]

    if (!line.trim()) {
      index += 1
      continue
    }

    if (DIVIDER.test(line)) {
      blocks.push({ kind: 'divider' })
      index += 1
      continue
    }

    // 代码块：整段原样保留（报告里偶尔出现 JSON 片段）
    if (line.trim().startsWith('```')) {
      const collected: string[] = []
      index += 1
      while (index < lines.length && !lines[index].trim().startsWith('```')) {
        collected.push(lines[index])
        index += 1
      }
      index += 1 // 跳过收尾的 ```
      blocks.push({ kind: 'code', text: collected.join('\n') })
      continue
    }

    const heading = HEADING.exec(line)
    if (heading) {
      blocks.push({ kind: 'heading', level: heading[1].length,
                    segments: parseInline(heading[2].trim()) })
      index += 1
      continue
    }

    // 表格：表头 + 分隔行（`|---|:--:|`）才认定为表格，否则只是普通段落
    if (line.trim().startsWith('|') && index + 1 < lines.length
        && TABLE_SEPARATOR.test(lines[index + 1]) && lines[index + 1].includes('-')) {
      const header = splitRow(line)
      const aligns = parseAligns(lines[index + 1])
      index += 2
      const rows: InlineSegment[][][] = []
      while (index < lines.length && lines[index].trim().startsWith('|')) {
        rows.push(splitRow(lines[index]).map(parseInline))
        index += 1
      }
      blocks.push({ kind: 'table', header: header.map(parseInline), rows, aligns })
      continue
    }

    if (UNORDERED.test(line) || ORDERED.test(line)) {
      const ordered = ORDERED.test(line)
      const start = ordered ? Number(ORDERED.exec(line)![1]) : 1
      const items: InlineSegment[][] = []
      while (index < lines.length) {
        const current = lines[index]
        const match = ordered ? ORDERED.exec(current) : UNORDERED.exec(current)
        if (!match) break
        // 缩进的后续行并入当前条目：Markdown 的软换行，渲染上不该断开成两个条目
        const parts = [ordered ? match[2] : match[1]]
        index += 1
        while (index < lines.length && /^\s+\S/.test(lines[index])
               && !UNORDERED.test(lines[index]) && !ORDERED.test(lines[index])) {
          parts.push(lines[index].trim())
          index += 1
        }
        items.push(parseInline(joinLines(parts)))
      }
      blocks.push({ kind: 'list', ordered, start, items })
      continue
    }

    if (line.trim().startsWith('>')) {
      const quoted: string[] = []
      while (index < lines.length && lines[index].trim().startsWith('>')) {
        quoted.push(lines[index].trim().replace(/^>\s?/, ''))
        index += 1
      }
      blocks.push({ kind: 'quote', segments: parseInline(joinLines(quoted)) })
      continue
    }

    // 普通段落：连续的非空行合并为一段
    const paragraph: string[] = []
    while (index < lines.length && lines[index].trim()
           && !HEADING.test(lines[index]) && !DIVIDER.test(lines[index])
           && !UNORDERED.test(lines[index]) && !ORDERED.test(lines[index])
           && !lines[index].trim().startsWith('>')
           && !lines[index].trim().startsWith('|')
           && !lines[index].trim().startsWith('```')) {
      paragraph.push(lines[index].trim())
      index += 1
    }
    if (paragraph.length) {
      blocks.push({ kind: 'paragraph', segments: parseInline(joinLines(paragraph)) })
    }
  }

  return blocks
}

const INLINE_PATTERN = /(`[^`]+`)|(\*\*[^*]+\*\*)|(__[^_]+__)|(\*[^*\n]+\*)|(\[[^\]]+\]\([^)\s]+\))/g

/** 解析行内标记：代码 → 加粗 → 斜体 → 链接；不处理嵌套（报告里也用不到） */
export function parseInline(text: string): InlineSegment[] {
  const segments: InlineSegment[] = []
  let cursor = 0
  let match: RegExpExecArray | null

  INLINE_PATTERN.lastIndex = 0
  while ((match = INLINE_PATTERN.exec(text)) !== null) {
    if (match.index > cursor) {
      segments.push({ text: text.slice(cursor, match.index) })
    }
    const token = match[0]
    if (token.startsWith('`')) {
      segments.push({ text: token.slice(1, -1), code: true })
    } else if (token.startsWith('**') || token.startsWith('__')) {
      segments.push({ text: token.slice(2, -2), bold: true })
    } else if (token.startsWith('*')) {
      segments.push({ text: token.slice(1, -1), italic: true })
    } else {
      const link = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(token)
      if (link) {
        const safe = /^https?:\/\//i.test(link[2]) ? link[2] : undefined
        segments.push(safe ? { text: link[1], href: safe } : { text: `[${link[1]}](${link[2]})` })
      } else {
        segments.push({ text: token })
      }
    }
    cursor = match.index + token.length
  }

  if (cursor < text.length) {
    segments.push({ text: text.slice(cursor) })
  }
  return segments.length ? segments : [{ text }]
}

/**
 * 合并同一段内的多行。
 *
 * 中文之间**不加空格**：HTML 会把换行折叠成一个空格，而「第一句。 第二句。」
 * 在中文里那个空格是多余的；只有相邻两侧都是拉丁字符（或半个词）时才补空格。
 */
function joinLines(lines: string[]): string {
  return lines.reduce((acc, current) => {
    if (!acc) return current
    const needsSpace = /[A-Za-z0-9`)]$/.test(acc) && /^[A-Za-z0-9`(]/.test(current)
    return acc + (needsSpace ? ' ' : '') + current
  }, '')
}

function splitRow(line: string): string[] {
  return line.trim().replace(/^\||\|$/g, '').split('|').map((cell) => cell.trim())
}

function parseAligns(separator: string): ('left' | 'center' | 'right')[] {
  return splitRow(separator).map((cell) => {
    const left = cell.startsWith(':')
    const right = cell.endsWith(':')
    if (left && right) return 'center'
    return right ? 'right' : 'left'
  })
}
