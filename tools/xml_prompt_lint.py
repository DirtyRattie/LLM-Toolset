#!/usr/bin/env python3
"""
XML Prompt Linter — 检查 XML 结构提示词的语法、层级和标签命名
"""

import xml.etree.ElementTree as ET
import argparse
import sys
import re
import os
from collections import Counter
from dataclasses import dataclass

VERSION = "1.2.0"

# ── 颜色 ─────────────────────────────────────────────
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
CYAN   = "\033[36m"
MAGENTA = "\033[35m"
DIM    = "\033[2m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def colorize(text, color):
    if not sys.stdout.isatty():
        return text
    return f"{color}{text}{RESET}"


# ══════════════════════════════════════════════════════
#  反引号引用 (Backtick Reference) 处理
# ══════════════════════════════════════════════════════

@dataclass
class BacktickRef:
    """一个被反引号包裹的引用"""
    raw: str          # 反引号内的原始文本
    line: int         # 所在行号（1-based）
    kind: str         # "open_tag" | "close_tag" | "self_close_tag" | "other"
    tag_name: str     # 提取出的标签名（非标签类为空）


# 匹配反引号引用：先匹配 ``` 三引号块，再匹配 ` 单引号内联
# 顺序很重要——三引号优先，避免单引号把 ``` 拆碎
_BACKTICK_PATTERN = re.compile(
    r'```(?:[a-zA-Z]*)\n?(.*?)```'   # 三引号代码块 (group 1)
    r'|'
    r'`([^`\n]+?)`',                 # 单引号内联引用 (group 2)
    re.DOTALL,
)

# 从引用内容中识别标签模式（支持带属性的标签）
_TAG_IN_REF = re.compile(
    r'<(/?)([a-zA-Z_][\w.-]*)(?:\s[^>]*)?\s*(/?)>'
)

# 识别 {{PLACEHOLDER}} 模式
_PLACEHOLDER_IN_REF = re.compile(
    r'\{\{([A-Z_][\w]*)\}\}'
)


def _classify_ref(raw: str) -> tuple[str, str]:
    """判断引用类型，返回 (kind, tag_name)"""
    m = _TAG_IN_REF.search(raw)
    if m:
        is_close = bool(m.group(1))
        tag_name = m.group(2)
        # self-close: 正则 group(3) 或 raw 中 /> 结尾
        is_self_close = bool(m.group(3)) or '/>' in raw
        if is_self_close and not is_close:
            return "self_close_tag", tag_name
        elif is_close:
            return "close_tag", tag_name
        else:
            return "open_tag", tag_name
    return "other", ""


def extract_backtick_refs(xml_text: str) -> list[BacktickRef]:
    """从原始文本中提取所有反引号引用"""
    refs = []
    # 预计算行号偏移
    line_starts = [0]
    for i, ch in enumerate(xml_text):
        if ch == '\n':
            line_starts.append(i + 1)

    def _offset_to_line(offset: int) -> int:
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1  # 1-based

    for m in _BACKTICK_PATTERN.finditer(xml_text):
        content = m.group(1) if m.group(1) is not None else m.group(2)
        if not content or not content.strip():
            continue
        line = _offset_to_line(m.start())
        kind, tag_name = _classify_ref(content.strip())
        refs.append(BacktickRef(
            raw=content.strip(),
            line=line,
            kind=kind,
            tag_name=tag_name,
        ))
    return refs


def sanitize_backtick_refs(xml_text: str) -> tuple[str, list[BacktickRef]]:
    """
    将反引号引用中的 XML 特殊字符替换为安全占位符，
    使 XML 解析器不会被引用内容干扰。
    返回 (处理后的文本, 提取的引用列表)
    """
    refs = extract_backtick_refs(xml_text)

    def _escape_match(m):
        # 保留反引号标记，但把内部 < > & 转义
        full = m.group(0)
        if m.group(1) is not None:
            inner = m.group(1)
        else:
            inner = m.group(2)
        if not inner:
            return full
        escaped = inner.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        return full.replace(inner, escaped, 1)

    sanitized = _BACKTICK_PATTERN.sub(_escape_match, xml_text)
    return sanitized, refs


# ══════════════════════════════════════════════════════
#  引用交叉验证
# ══════════════════════════════════════════════════════

def collect_all_tag_names(elem: ET.Element) -> set[str]:
    """递归收集 XML 树中所有标签名"""
    tags = {elem.tag}
    for child in elem:
        tags |= collect_all_tag_names(child)
    return tags


def check_refs(refs: list[BacktickRef], existing_tags: set[str]) -> list[str]:
    """校验反引号引用中提到的标签名是否存在于 XML 树中"""
    results = []
    tag_refs = [r for r in refs if r.kind in ("open_tag", "close_tag", "self_close_tag")]
    other_refs = [r for r in refs if r.kind == "other"]

    for ref in tag_refs:
        if ref.tag_name in existing_tags:
            results.append(
                f"{colorize('✓', GREEN)} 第 {ref.line:>3d} 行  "
                f"`{colorize(ref.raw, MAGENTA)}`  →  "
                f"<{ref.tag_name}> {colorize('存在', GREEN)}"
            )
        else:
            results.append(
                f"{colorize('✗', RED)} 第 {ref.line:>3d} 行  "
                f"`{colorize(ref.raw, MAGENTA)}`  →  "
                f"<{ref.tag_name}> {colorize('未找到', RED)}"
                f"（拼写错误？或引用了外部上下文的标签）"
            )

    for ref in other_refs:
        results.append(
            f"{colorize('·', DIM)} 第 {ref.line:>3d} 行  "
            f"`{colorize(ref.raw, MAGENTA)}`  →  "
            f"{colorize('非标签引用，跳过验证', DIM)}"
        )

    return results


# ══════════════════════════════════════════════════════
#  XML 语法检查
# ══════════════════════════════════════════════════════

def check_syntax(xml_text: str) -> tuple[ET.Element | None, list[str]]:
    errors = []
    try:
        root = ET.fromstring(xml_text)
        return root, errors
    except ET.ParseError as e:
        errors.append(f"XML 语法错误: {str(e)}")
        return None, errors


def pre_check(xml_text: str) -> list[str]:
    """正式解析前用正则做轻量检查，捕获常见笔误"""
    warnings = []

    open_tags  = re.findall(r'<([a-zA-Z_][\w.-]*)(?:\s[^>]*)?\s*>', xml_text)
    close_tags = re.findall(r'</([a-zA-Z_][\w.-]*)\s*>', xml_text)
    self_close  = re.findall(r'<([a-zA-Z_][\w.-]*)(?:\s[^>]*)?\s*/>', xml_text)

    open_counter  = Counter(open_tags)
    close_counter = Counter(close_tags)
    self_counter  = Counter(self_close)

    for tag in self_counter:
        open_counter[tag] = max(0, open_counter.get(tag, 0) - self_counter[tag])

    all_tags = set(list(open_counter.keys()) + list(close_counter.keys()))
    for tag in sorted(all_tags):
        o = open_counter.get(tag, 0)
        c = close_counter.get(tag, 0)
        if o > c:
            warnings.append(f"标签 <{tag}> 打开 {o} 次，关闭 {c} 次 — 可能缺少 </{tag}>")
        elif c > o:
            warnings.append(f"标签 </{tag}> 关闭 {c} 次，打开 {o} 次 — 可能多余的关闭标签")

    bad_names = re.findall(r'</?([^>]*[\u4e00-\u9fff]+[^>]*)>', xml_text)
    for name in bad_names:
        if not name.startswith('!'):
            warnings.append(f"标签名含中文字符: <{name.strip()}>")

    return warnings


# ══════════════════════════════════════════════════════
#  命名规范 / 树状打印 / 辅助检查
# ══════════════════════════════════════════════════════

NAMING_CONVENTIONS = {
    "snake_case":  re.compile(r'^[a-z][a-z0-9]*(_[a-z0-9]+)*$'),
    "kebab-case":  re.compile(r'^[a-z][a-z0-9]*(-[a-z0-9]+)*$'),
    "camelCase":   re.compile(r'^[a-z][a-zA-Z0-9]*$'),
    "PascalCase":  re.compile(r'^[A-Z][a-zA-Z0-9]*$'),
    "UPPER_SNAKE": re.compile(r'^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*$'),
}

def detect_naming(tags: list[str]) -> dict:
    style_map = {}
    for tag in tags:
        matches = [name for name, pat in NAMING_CONVENTIONS.items() if pat.match(tag)]
        style_map[tag] = matches if matches else ["other"]
    flat = []
    for styles in style_map.values():
        flat.extend(styles)
    return style_map, Counter(flat)


def print_tree(elem: ET.Element, indent: int = 0, max_text: int = 50):
    prefix = "│   " * indent + "├── " if indent > 0 else ""
    tag_str = colorize(elem.tag, CYAN)
    attrs = ""
    if elem.attrib:
        attr_parts = [f'{k}="{v}"' for k, v in elem.attrib.items()]
        attrs = " " + colorize(" ".join(attr_parts), DIM)
    text_preview = ""
    if elem.text and elem.text.strip():
        t = elem.text.strip().replace("\n", "↵ ")
        if len(t) > max_text:
            t = t[:max_text] + "…"
        text_preview = colorize(f'  "{t}"', DIM)
    print(f"{prefix}{tag_str}{attrs}{text_preview}")
    for child in elem:
        print_tree(child, indent + 1, max_text)


def collect_tags(elem: ET.Element, depth=0, result=None):
    if result is None:
        result = []
    result.append((elem.tag, depth))
    for child in elem:
        collect_tags(child, depth + 1, result)
    return result


def check_sibling_duplicates(elem, path=""):
    warnings = []
    current_path = f"{path}/{elem.tag}"
    child_tags = [c.tag for c in elem]
    dupes = [tag for tag, cnt in Counter(child_tags).items() if cnt > 1]
    for tag in dupes:
        cnt = child_tags.count(tag)
        warnings.append(f"{current_path} 下有 {cnt} 个 <{tag}>（如非列表请检查是否重复）")
    for child in elem:
        warnings.extend(check_sibling_duplicates(child, current_path))
    return warnings


def find_empty_nodes(elem, path=""):
    result = []
    current_path = f"{path}/{elem.tag}"
    has_text = elem.text and elem.text.strip()
    has_children = len(elem) > 0
    if not has_text and not has_children:
        result.append(current_path)
    for child in elem:
        result.extend(find_empty_nodes(child, current_path))
    return result


# ══════════════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════════════

def lint(xml_text: str, max_text: int = 50):
    print(colorize(f"═══ XML Prompt Linter v{VERSION} ═══", BOLD))
    print()

    # ── Step 0: 反引号引用处理 ──
    sanitized, refs = sanitize_backtick_refs(xml_text)

    if refs:
        tag_refs = [r for r in refs if r.kind != "other"]
        other_refs = [r for r in refs if r.kind == "other"]
        print(colorize("── 反引号引用预处理 ──", BOLD))
        print(f"  检测到 {colorize(str(len(refs)), MAGENTA)} 个反引号引用"
              f"（{len(tag_refs)} 个标签引用，{len(other_refs)} 个其他引用）")
        print(f"  已将引用内的 < > 转义，不参与 XML 语法解析")
        print()

    # ── Step 1: 预检查（在净化后文本上做） ──
    pre_warnings = pre_check(sanitized)

    # ── Step 2: 正式解析 ──
    root, errors = check_syntax(sanitized)

    if errors:
        print(colorize("✗ 语法检查失败", RED))
        for e in errors:
            print(f"  {colorize('ERROR', RED)}: {e}")
        print()
        if pre_warnings:
            print(colorize("可能的原因:", YELLOW))
            for w in pre_warnings:
                print(f"  {colorize('HINT', YELLOW)}: {w}")
        return False

    print(colorize("✓ 语法检查通过", GREEN))
    print()

    # ── Step 3: 树状结构 ──
    print(colorize("── 树状层级结构 ──", BOLD))
    print_tree(root, max_text=max_text)
    print()

    # ── Step 4: 结构统计 ──
    tag_info = collect_tags(root)
    all_tags_list = [t for t, _ in tag_info]
    unique_tags = sorted(set(all_tags_list))
    max_depth = max(d for _, d in tag_info)

    print(colorize("── 统计 ──", BOLD))
    print(f"  总节点数:   {len(tag_info)}")
    print(f"  唯一标签数: {len(unique_tags)}")
    print(f"  最大深度:   {max_depth}")
    print()

    # ── Step 5: 命名一致性 ──
    print(colorize("── 命名风格检查 ──", BOLD))
    style_map, style_counter = detect_naming(unique_tags)
    if style_counter:
        dominant = style_counter.most_common(1)[0][0]
        print(f"  主要风格: {colorize(dominant, GREEN)}")
        outliers = [tag for tag, styles in style_map.items() if dominant not in styles]
        if outliers:
            print(f"  {colorize('不一致的标签:', YELLOW)}")
            for tag in outliers:
                styles = ", ".join(style_map[tag])
                print(f"    <{tag}>  →  {styles}")
        else:
            print(f"  {colorize('所有标签命名风格一致 ✓', GREEN)}")
    print()

    # ── Step 6: 重复标签（同层级） ──
    dup_warnings = check_sibling_duplicates(root)
    if dup_warnings:
        print(colorize("── 同层级重复标签 ──", BOLD))
        for w in dup_warnings:
            print(f"  {colorize('WARN', YELLOW)}: {w}")
        print()

    # ── Step 7: 空节点 ──
    empty = find_empty_nodes(root)
    if empty:
        print(colorize("── 空节点（无文本无子元素）──", BOLD))
        for path in empty:
            print(f"  {colorize('INFO', DIM)}: {path}")
        print()

    # ── Step 8: 反引号引用交叉验证 ──
    if refs:
        existing_tags = collect_all_tag_names(root)
        ref_results = check_refs(refs, existing_tags)
        if ref_results:
            print(colorize("── 反引号引用交叉验证 ──", BOLD))
            for r in ref_results:
                print(f"  {r}")
            print()

    print(colorize("═══ 检查完成 ═══", BOLD))
    return True


# ══════════════════════════════════════════════════════
#  CLI 入口
# ══════════════════════════════════════════════════════

EXAMPLES = """
示例:
  %(prog)s prompt.xml                   检查文件
  %(prog)s -s '<root><a>hi</a></root>'  检查内联字符串
  cat prompt.xml | %(prog)s -           从 stdin 读取
  %(prog)s -t 80 big_prompt.xml         文本摘要截断到 80 字符
  %(prog)s --no-color prompt.xml        禁用彩色输出（方便管道处理）

检查项目:
  0. 反引号引用预处理    识别 `<tag>` 等内联引用，转义后排除于语法检查
  1. XML 语法验证        解析失败时给出行号 + 可能原因提示
  2. 树状层级结构        彩色缩进打印完整嵌套，附文本摘要
  3. 结构统计            总节点数 / 唯一标签数 / 最大深度
  4. 命名风格一致性      自动检测主流风格 (snake_case, camelCase,
                         kebab-case, PascalCase, UPPER_SNAKE)，
                         标出与主流不一致的标签
  5. 同层级重复标签      检测可能的复制粘贴笔误
  6. 空节点警告          既无文本也无子元素的标签
  7. 引用交叉验证        检查 `<tag>` 引用的标签在 XML 树中是否真实存在

退出码:
  0  所有检查通过
  1  存在语法错误或检查失败
"""


def main():
    parser = argparse.ArgumentParser(
        prog="xml_prompt_lint",
        description="XML Prompt Linter — 检查 XML 结构提示词的语法、层级和标签命名",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "input",
        nargs="?",
        default=None,
        help="XML 文件路径，或 '-' 表示从 stdin 读取（缺省时须配合 -s 使用）",
    )
    parser.add_argument(
        "-s", "--string",
        metavar="XML",
        help="直接传入 XML 字符串进行检查",
    )
    parser.add_argument(
        "-t", "--truncate",
        type=int,
        default=50,
        metavar="N",
        help="树状图中文本摘要的最大字符数（默认: 50）",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="禁用彩色输出（适用于管道或重定向场景）",
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"%(prog)s {VERSION}",
    )

    args = parser.parse_args()

    if args.no_color:
        global GREEN, YELLOW, RED, CYAN, MAGENTA, DIM, BOLD, RESET
        GREEN = YELLOW = RED = CYAN = MAGENTA = DIM = BOLD = RESET = ""

    if args.string:
        xml_text = args.string
    elif args.input == "-":
        xml_text = sys.stdin.read()
    elif args.input:
        if not os.path.isfile(args.input):
            parser.error(f"文件不存在: {args.input}")
        with open(args.input, "r", encoding="utf-8") as f:
            xml_text = f.read()
    else:
        parser.error("请提供 XML 文件路径、使用 -s 传入字符串、或从 stdin 管道输入（-）")

    success = lint(xml_text, max_text=args.truncate)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()