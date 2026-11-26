# -*- coding: utf-8 -*-
"""`.hb` 脚本语言：把 `hb` 子命令按行写进文件，同一进程内顺序执行。

语法刻意小巧、向 Python 靠拢（冒号 + 缩进块、类型化值），由「词法 → 语法解析
→ AST」两段式完成，执行侧只遍历 AST，不靠 if-else / 正则猜语法：

    #! hb --port 9222 --timeout 3    ← 可选头行：连接/全局选项，须在命令之前
    # 整行注释
    open https://example.com
    let kw = 世界                    ← 变量；值可含空格
    find "#q"
    input @1 "${kw}"                 ← ${name} 插值
    sleep 0.5

    info 已输入关键词                 ← 日志：debug/info/warn/error（走 stderr）
    loglevel debug                   ← 日志门槛（默认 info，debug 先藏起来）
    error 结果不对                    ← error 日志 = 记一次失败（退出码 1）

    if n == 3:                       ← 条件块：冒号 + 缩进，同 Python；裸标识符 = 变量
        info 相等
    elif n > 3:
        warn 偏大
    else:
        error 偏小

    if ok: info 单行内联也可以        ← 冒号后同行写一条简单语句
    assert n == 3 数量应为 3          ← 断言：不成立记失败（继续跑），可带消息

    loop 3:                          ← 固定次数循环，${i} 是 1 起的计数
        echo 第 ${i} 次
    for x in "苹果" "香蕉":           ← 遍历（也可 for i in range(3):）
        info ${x}

    run lib/login.hb "张三" 3        ← 嵌套执行另一个脚本（可带参数）
                                       子脚本用 ${1}/${2}…、${argc}、${argv} 取参
                                       变量隔离；相对路径相对调用脚本所在目录

类型（值只有三种，同 Python 直觉）：
- 数字：`3` / `3.14` / `-2`；字符串：`"…"` / `'…'` 包裹，或含 `: / . # @` 等
  字符的裸词（如 `https://example.com`，它不可能是变量名）；变量：裸标识符。
- `let n = 3` 保留类型（n 是数字）；整体写 `${n}` 的单 token 插值也保留类型；
  嵌在更长文本里（`"第 ${n} 次"`）则渲染成字符串。
- 表达式 / 循环头（if/elif/assert/loop）里裸标识符**必须**是已定义变量，
  未定义直接报错（抓拼写错误）；字面字符串请加引号：`if role == "admin":`。
- `let` / `for` 取值同样严格：`let role = "guest"`、`for x in ["苹果", "香蕉"]`
  （字面字符串都要加引号，裸标识符一律按变量解析）。
- 比较类型敏感：`3 == "3"` 为假；数字和字符串比大小报错提示；`in` 只用于字符串。
- 命令参数没有类型这套：`open site` 的 site 永远是字面字符串，取变量写 ${site}。

条件表达式（if/elif 与 assert 共用一套文法）：
- 比较：`==` `!=` `<` `<=` `>` `>=`、`in` / `not in`（子串）；运算符两侧要留空格。
- 组合：`and` / `or` / `not` / 括号，优先级同 Python：or < and < not < 比较。
- 单个值做真假判断：数字 0、空串、false、no、none、off（不区分大小写）为假。

分词规则：
- 空白分隔；`"…"` / `'…'` 包裹带空格的参数，引号内不做反斜杠转义（Windows 路径原样）。
- `#` 在行首是注释；行内只有「空格 + # + 空格/行尾」才是注释，
  所以 `find #kw` 仍然把 `#kw` 当定位符。
- 缩进用空格（Tab 按 4 列展开）；块语句以冒号结尾，下一层缩进即语句块。
- 首 token 误写成 `hb` 会自动剥掉（方便从命令行历史直接粘贴）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 只认 ${name}：`$name` 会和定位符 / JS 里的 $ 打架，干脆不提供
_VAR_RE = re.compile(r'\$\{(\w+)\}')

LOG_LEVELS = {'debug': 10, 'info': 20, 'warn': 30, 'error': 40}
_BLOCK_HEADS = ('if', 'elif', 'else', 'loop', 'for')
_CMP_OPS = ('==', '!=', '<=', '>=', '<', '>')
_FALSE_WORDS = {'', '0', 'false', 'no', 'none', 'off'}


class ScriptError(ValueError):
    """脚本语法错误（调用方负责带上行号/文件名）。"""


# ---------------------------------------------------------------- AST

@dataclass
class Command:
    lineno: int          # 1-based，报错定位用
    tokens: list[str]    # 已分词的 argv（不含 hb）
    raw: str             # 原始行（--echo 回显用）
    quoted: list = None  # 与 tokens 对齐的「是否引号包裹」标记（类型系统用）


@dataclass
class Assert:
    """assert <条件> [消息…]：条件不成立 → 打 [ASSERT]、记一次失败、继续执行。"""
    lineno: int
    expr: object         # 条件表达式 AST
    cond: str            # 条件原文（失败时展示）
    msg: list[str]       # 可选消息 tokens（执行期插值）


@dataclass
class If:
    lineno: int
    branches: list       # [(expr, [stmt...]), ...]  if/elif 各一支
    else_body: list = field(default_factory=list)


@dataclass
class Loop:
    lineno: int
    count: list[str]     # 恰好一个 token（执行期求值后取整）
    body: list


@dataclass
class For:
    lineno: int
    name: str            # 循环变量名
    iterable: object     # ListLit（["a", "b"]）或 Lit（range(N) / 列表变量）
    body: list


@dataclass
class Script:
    header: list[str] = field(default_factory=list)   # #! 行的全局选项
    commands: list = field(default_factory=list)      # 顶层语句（Command/Assert/If/Loop/For）
    path: str = ''         # 脚本文件路径（run 嵌套调用时解析相对路径用；-e/stdin 为空）


# ---------------------------------------------------------------- 表达式 AST

@dataclass
class Lit:
    """字面值 token；quoted=True 表示引号包裹（类型系统：永远是字符串）。"""
    text: str
    quoted: bool = False


@dataclass
class ListLit:
    """列表字面量 ["a", "b"]：元素是单 token 值（Lit）。"""
    items: list        # list[Lit]


@dataclass
class Not:
    node: object


@dataclass
class And:
    left: object
    right: object


@dataclass
class Or:
    left: object
    right: object


@dataclass
class Cmp:
    op: str              # == != < <= > >= in / not in
    left: object
    right: object


# ---------------------------------------------------------------- 行分词

def _texts(rich):
    return [t for t, _ in rich]


def _tokenize_rich(line):
    """把一行切成 [(text, quoted)]；quoted 标记该 token 是否被引号包裹过。"""
    tokens, buf, quote = [], [], None
    quoted = False
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        i += 1
        if quote:
            if ch == quote:
                quote = None
            else:
                buf.append(ch)
        elif ch == '"' or ch == "'":
            quote = ch
            quoted = True
        elif ch.isspace():
            if buf or quoted:
                tokens.append((''.join(buf), quoted))
                buf, quoted = [], False
        elif ch == '#' and not buf and (i >= n or line[i].isspace()):
            break   # 行内注释：只有「空格 # 空格/行尾」才算，保住 find #kw
        else:
            buf.append(ch)
    if quote:
        raise ScriptError(f'引号未闭合：{line.strip()!r}')
    if buf or quoted:
        tokens.append((''.join(buf), quoted))
    return tokens


def tokenize(line):
    """把一行切成 argv。引号未闭合抛 ScriptError。"""
    return _texts(_tokenize_rich(line))


# ---------------------------------------------------------------- 类型化取值

def _at(lineno, msg):
    return f'第 {lineno} 行：{msg}' if lineno else msg


def _to_number(text):
    """'3' → 3、'3.5' → 3.5；不是数字 → None。"""
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return None


def value_of(text, quoted, variables, strict=False):
    """单个 token → 类型化值（int / float / str）。

    - 引号包裹 → 字符串（${} 插值后一定是 str）
    - 整体是 ${name} → 变量值（保留类型；未定义则原样保留字符串）
    - 裸标识符 → 已定义变量的值；true/false 是布尔字面量（同名变量优先）；
      其余未定义：strict 报错，否则当字符串字面值
    - 数字 → int / float
    - 其他裸词（含 : / . # @ 等，不可能是变量名）→ 字符串
    """
    if quoted:
        return interpolate([text], variables)[0]
    m = _VAR_RE.fullmatch(text)
    if m:
        return variables.get(m.group(1), text)
    if text.isidentifier():
        if text in variables:
            return variables[text]
        # 布尔字面量 true/false（变量同名时以上面的变量优先，向后兼容）
        low = text.lower()
        if low == 'true':
            return True
        if low == 'false':
            return False
        if strict:
            raise ScriptError(f'变量未定义：{text}（字面字符串请加引号："{text}"）')
        return text
    num = _to_number(text)
    if num is not None:
        return num
    return interpolate([text], variables)[0]


def truthy(value):
    """单个值的真假：数字 0、空串、空列表、false、no、none、off（不区分大小写）为假。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, list):
        return len(value) > 0
    return str(value).lower() not in _FALSE_WORDS


def interpolate(tokens, variables):
    """${name} 替换；未定义的原样保留（JS 模板串里的 ${x} 不会被误伤）。"""
    if not variables:
        return list(tokens)

    def sub(m):
        v = variables.get(m.group(1), m.group(0))
        return v if isinstance(v, str) else str(v)   # 数字变量渲染成字符串

    return [_VAR_RE.sub(sub, t) for t in tokens]


def parse_let(tokens, quoted=None, variables=None):
    """let/set 的参数 → (name, 类型化值)。

    值：单 token 按类型系统取值（裸标识符 = 变量，未定义报错）；
    `["a", "b"]` 是列表；多个裸 token 报错（字符串请加引号）。
    """
    if not tokens:
        raise ScriptError('let 需要变量名，如：let kw = "关键词"')
    name = tokens[0]
    if not name.isidentifier():
        raise ScriptError(f'变量名不合法：{name!r}（字母/数字/下划线，且不以数字开头）')
    values = list(tokens[1:])
    qv = list(quoted[1:]) if quoted else [False] * len(values)
    if values[:1] == ['=']:
        values, qv = values[1:], qv[1:]
    if not values:
        return name, ''
    if values[0].startswith('['):
        items, pos = _collect_list_items(list(zip(values, qv)), 0, 0)
        if pos != len(values):
            raise ScriptError('列表后面有多余内容')
        return name, [value_of(lit.text, lit.quoted, variables or {}, strict=True)
                      for lit in items]
    if len(values) == 1:
        return name, value_of(values[0], qv[0], variables or {}, strict=True)
    raise ScriptError('let 的值含空格：字符串请加引号（如 let t = "你 好"），列表用 ["a", "b"]')


# ---------------------------------------------------------------- 条件表达式（递归下降）

_PAREN_RE = re.compile(r'([()])')


def parse_expr(tokens, lineno=0):
    """条件表达式 rich tokens → (expr, 剩余 rich tokens)。

    吃进去的表达式尽可能长（assert 把剩余部分当消息就是这么切的）。
    括号可以贴字（`(a or b)` 与 `( a or b )` 都行）；引号内的括号保持字面。
    空表达式 / 语法错误抛 ScriptError。
    """
    expanded = []
    for text, quoted in tokens:
        if quoted or ('(' not in text and ')' not in text):
            expanded.append((text, quoted))
        else:
            expanded += [(piece, False) for piece in _PAREN_RE.split(text) if piece]
    if not expanded:
        raise ScriptError(_at(lineno, '缺少条件表达式'))
    expr, pos = _parse_or(expanded, 0, lineno)
    return expr, expanded[pos:]


def parse_cond(text, lineno=0):
    """条件表达式源码串 → (expr, 剩余 tokens)。测试/调试用的便捷入口。"""
    return parse_expr(_tokenize_rich(text), lineno)


def _parse_or(tokens, pos, lineno):
    left, pos = _parse_and(tokens, pos, lineno)
    while pos < len(tokens) and tokens[pos][0] == 'or':
        right, pos = _parse_and(tokens, pos + 1, lineno)
        left = Or(left, right)
    return left, pos


def _parse_and(tokens, pos, lineno):
    left, pos = _parse_not(tokens, pos, lineno)
    while pos < len(tokens) and tokens[pos][0] == 'and':
        right, pos = _parse_not(tokens, pos + 1, lineno)
        left = And(left, right)
    return left, pos


def _parse_not(tokens, pos, lineno):
    if pos < len(tokens) and tokens[pos][0] == 'not':
        node, pos = _parse_not(tokens, pos + 1, lineno)
        return Not(node), pos
    return _parse_cmp(tokens, pos, lineno)


def _parse_cmp(tokens, pos, lineno):
    left, pos = _parse_atom(tokens, pos, lineno)
    if pos >= len(tokens):
        return left, pos
    text = tokens[pos][0]
    if text in _CMP_OPS:
        op, pos = text, pos + 1
    elif text == 'in':
        op, pos = 'in', pos + 1
    elif text == 'not' and pos + 1 < len(tokens) and tokens[pos + 1][0] == 'in':
        op, pos = 'not in', pos + 2
    else:
        return left, pos
    right, pos = _parse_atom(tokens, pos, lineno)
    for side in (left, right):
        if not isinstance(side, (Lit, ListLit)):
            raise ScriptError(_at(lineno, f'比较运算符 {op} 两边只能是字面值或列表（组合条件请用 and/or 连接）'))
    return Cmp(op, left, right), pos


def _collect_list_items(tokens, pos, lineno):
    """tokens[pos] 以 '[' 开头 → (list[Lit], 新 pos)。逗号分隔；缺 ] 报错。

    裸元素的逗号可以贴字（[1,2,3] 也行）；引号元素的逗号需跟空格（["a", "b"]）
    ——引号已被分词吃掉，引号 token 内的逗号一律视为字符串内容。
    """
    raw = []
    closed = False
    text, quoted = tokens[pos]
    body = text[1:]                    # 剥掉 '['
    pos += 1
    while True:
        if body.endswith(']'):
            if body != ']':
                raw.append((body[:-1], quoted))
            closed = True
            break
        if body:
            raw.append((body, quoted))
        if pos >= len(tokens):
            break
        body, quoted = tokens[pos]
        pos += 1
    if not closed:
        raise ScriptError(_at(lineno, '列表字面量缺少右括号 ]'))
    items = []
    for text, quoted in raw:
        if quoted:
            items.append(Lit(text.removesuffix(','), True))
        else:
            items += [Lit(piece, False) for piece in text.split(',') if piece]
    return items, pos


def _parse_atom(tokens, pos, lineno):
    if pos >= len(tokens):
        raise ScriptError(_at(lineno, '条件表达式不完整'))
    text, quoted = tokens[pos]
    if not quoted and text == '(':
        node, pos = _parse_or(tokens, pos + 1, lineno)
        if pos >= len(tokens) or tokens[pos][0] != ')':
            raise ScriptError(_at(lineno, '条件表达式的括号没有闭合'))
        return node, pos + 1
    if not quoted and text == ')':
        raise ScriptError(_at(lineno, '条件表达式有多余的右括号'))
    if text.startswith('[') and not (quoted and text == '['):
        # [ 开头即列表（引号包裹的纯 "[" 字符串除外，不算列表）
        items, pos = _collect_list_items(tokens, pos, lineno)
        return ListLit(items), pos
    return Lit(text, quoted), pos + 1


def _eval_value(node, variables):
    """Lit / ListLit → 类型化值（裸标识符必须已定义）。"""
    if isinstance(node, ListLit):
        return [_eval_value(item, variables) for item in node.items]
    return value_of(node.text, node.quoted, variables, strict=True)


def eval_expr(node, variables):
    """表达式 AST + 变量表 → bool。字面值按类型系统取值（裸标识符必须已定义）。"""
    if isinstance(node, Or):
        return eval_expr(node.left, variables) or eval_expr(node.right, variables)
    if isinstance(node, And):
        return eval_expr(node.left, variables) and eval_expr(node.right, variables)
    if isinstance(node, Not):
        return not eval_expr(node.node, variables)
    if isinstance(node, Cmp):
        return _compare(node.op, _eval_value(node.left, variables),
                        _eval_value(node.right, variables))
    return truthy(_eval_value(node, variables))


def _compare(op, left, right):
    """类型敏感的比较：3 == "3" 为假；数字与字符串比大小报错；in 判子串或列表成员。"""
    if op == '==':
        return left == right
    if op == '!=':
        return left != right
    if op in ('in', 'not in'):
        if isinstance(right, list):
            result = any(_compare('==', left, item) for item in right)
            return result if op == 'in' else not result
        if not isinstance(left, str) or not isinstance(right, str):
            raise ScriptError('in 只能判断子串或列表成员（右边是字符串或 [列表]）')
        return (left in right) if op == 'in' else (left not in right)
    both_num = isinstance(left, (int, float)) and isinstance(right, (int, float))
    both_str = isinstance(left, str) and isinstance(right, str)
    if not (both_num or both_str):
        raise ScriptError(f'类型不匹配：数字和字符串不能比大小（{left!r} {op} {right!r}；'
                          f'字符串请两边都加引号）')
    if op == '<':
        return left < right
    if op == '<=':
        return left <= right
    if op == '>':
        return left > right
    return left >= right


# ---------------------------------------------------------------- 语法解析（缩进 → AST）

@dataclass
class _Line:
    lineno: int
    indent: int
    rich: list           # [(text, quoted)]
    raw: str

    @property
    def tokens(self):
        return _texts(self.rich)


def parse_script(text):
    """文本 → Script（commands 为 AST 语句列表）。语法错误抛 ScriptError（带行号）。"""
    text = text.removeprefix('\ufeff')   # 记事本存的 UTF-8 BOM
    script = Script()
    lines = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        expanded = raw.expandtabs(4)     # Tab 一律按 4 列算，避免混用歧义
        stripped = expanded.strip()
        if not stripped:
            continue                     # 空行不参与缩进判断（同 Python）
        if stripped.startswith('#'):
            if stripped.startswith('#!'):
                if script.header:
                    raise ScriptError(f'第 {lineno} 行：#! 头行只能出现一次')
                if lines:
                    raise ScriptError(f'第 {lineno} 行：#! 头行必须写在所有命令之前')
                tokens = tokenize(stripped[2:])
                if tokens[:1] == ['hb']:
                    tokens = tokens[1:]
                if not tokens:
                    raise ScriptError(f'第 {lineno} 行：#! 后面没有选项')
                script.header = tokens
            continue                     # 注释行同样不参与缩进判断
        try:
            rich = _tokenize_rich(expanded)
        except ScriptError as e:
            raise ScriptError(f'第 {lineno} 行：{e}') from None
        if rich and rich[0][0] == 'hb':
            rich = rich[1:]
        if not rich:
            raise ScriptError(f'第 {lineno} 行：只有 hb，没有子命令')
        indent = len(expanded) - len(expanded.lstrip(' '))
        lines.append(_Line(lineno, indent, rich, stripped))
    script.commands = _Parser(lines).parse_program()
    return script


def _split_head(tokens, lineno, kw):
    """块语句头在「以冒号结尾的 token」处切开 → (头部 rich tokens, 同行内联 rich tokens 或 None)。"""
    for i, (text, quoted) in enumerate(tokens):
        if text.endswith(':'):   # `"guest":` 的冒号在引号外，也算（引号内以冒号结尾的极端写法不区分）
            head = list(tokens[:i])
            if text != ':':
                head.append((text[:-1], quoted))
            return head, tokens[i + 1:] or None
    raise ScriptError(_at(lineno, f'{kw} 语句块需要以冒号结尾，如 {kw} …:'))


def _normalize_head(tokens):
    """块头关键词上粘着的冒号拆出来：['else:', 'x'] → ['else', ':', 'x']。"""
    first_text, first_quoted = tokens[0]
    for kw in _BLOCK_HEADS:
        if not first_quoted and first_text.startswith(kw + ':'):
            return [(kw, False), (first_text[len(kw):], first_quoted)] + tokens[1:]
    return list(tokens)


class _Parser:
    """递归下降：缩进深度决定块结构，块头只认 if/elif/else/loop/for。"""

    def __init__(self, lines):
        self.lines = lines
        self.pos = 0

    def parse_program(self):
        stmts = self.parse_block(0)
        if self.pos < len(self.lines):
            ln = self.lines[self.pos]
            raise ScriptError(_at(ln.lineno, '意外的缩进（上一行不是以冒号结尾的块语句）'))
        return stmts

    def parse_block(self, indent):
        stmts = []
        while self.pos < len(self.lines):
            ln = self.lines[self.pos]
            if ln.indent < indent:
                break
            if ln.indent > indent:
                raise ScriptError(_at(ln.lineno, '意外的缩进（上一层没有以冒号结尾的块语句）'))
            stmts.append(self.parse_stmt(indent))
        return stmts

    def parse_stmt(self, indent):
        ln = self.lines[self.pos]
        tokens = _normalize_head(ln.rich)
        head = tokens[0][0]
        if head == 'if':
            return self.parse_if(indent, tokens)
        if head in ('elif', 'else'):
            raise ScriptError(_at(ln.lineno, f'{head} 前面没有匹配的 if'))
        if head == 'loop':
            return self.parse_loop(indent, tokens)
        if head == 'for':
            return self.parse_for(indent, tokens)
        self.pos += 1
        if head == 'assert':
            return self.make_assert(ln.lineno, ln.rich)
        return Command(ln.lineno, ln.tokens, ln.raw, [q for _, q in ln.rich])

    def parse_body(self, indent, head_lineno, kw, inline):
        """冒号之后：同行内联一条简单语句，或下一行起更深缩进的语句块。"""
        if inline:
            if inline[0][0] in _BLOCK_HEADS:
                raise ScriptError(_at(head_lineno, f'冒号后不能接 {inline[0][0]} 块语句，请换行并缩进'))
            self.pos += 1
            return [self.inline_stmt(head_lineno, inline)]
        self.pos += 1
        if self.pos >= len(self.lines) or self.lines[self.pos].indent <= indent:
            raise ScriptError(_at(head_lineno, f'{kw} 的冒号后需要缩进的语句块（或在同一行写一条语句）'))
        return self.parse_block(self.lines[self.pos].indent)

    def parse_if(self, indent, head_tokens):
        lineno = self.lines[self.pos].lineno
        branches, else_body = [], []
        while True:
            ln = self.lines[self.pos]
            kw = head_tokens[0][0]               # if / elif
            cond_tokens, inline = _split_head(head_tokens[1:], ln.lineno, kw)
            expr, rest = parse_expr(cond_tokens, ln.lineno)
            if rest:
                raise ScriptError(_at(ln.lineno, f'条件后面有多余内容：{" ".join(_texts(rest))!r}（块以冒号结尾）'))
            body = self.parse_body(indent, ln.lineno, kw, inline)
            branches.append((expr, body))
            if self.pos < len(self.lines):
                nxt = self.lines[self.pos]
                nxt_tokens = _normalize_head(nxt.rich)
                if nxt.indent == indent and nxt_tokens[0][0] in ('elif', 'else'):
                    if nxt_tokens[0][0] == 'elif':
                        head_tokens = nxt_tokens
                        continue
                    extra, inline2 = _split_head(nxt_tokens[1:], nxt.lineno, 'else')
                    if extra:
                        raise ScriptError(_at(nxt.lineno, 'else 不接受条件（要条件请用 elif）'))
                    else_body = self.parse_body(indent, nxt.lineno, 'else', inline2)
            break
        return If(lineno, branches, else_body)

    def parse_loop(self, indent, tokens):
        ln = self.lines[self.pos]
        count, inline = _split_head(tokens[1:], ln.lineno, 'loop')
        if len(count) != 1:
            raise ScriptError(_at(ln.lineno, 'loop 需要一个次数，如 loop 3:'))
        body = self.parse_body(indent, ln.lineno, 'loop', inline)
        return Loop(ln.lineno, _texts(count), body)

    def parse_for(self, indent, tokens):
        ln = self.lines[self.pos]
        head_tokens, inline = _split_head(tokens[1:], ln.lineno, 'for')
        texts = _texts(head_tokens)
        usage = 'for 的写法是 for 变量 in ["a", "b"]:（或 range(N)、列表变量）'
        if len(texts) < 2 or texts[1] != 'in':
            raise ScriptError(_at(ln.lineno, usage))
        name = texts[0]
        if not name.isidentifier():
            raise ScriptError(_at(ln.lineno, f'for 变量名不合法：{name!r}'))
        rest = head_tokens[2:]
        if not rest:
            raise ScriptError(_at(ln.lineno, 'for 没有可遍历的值'))
        if rest[0][0].startswith('['):
            items, pos = _collect_list_items(rest, 0, ln.lineno)
            if pos != len(rest):
                raise ScriptError(_at(ln.lineno, '列表后面有多余内容'))
            iterable = ListLit(items)
        elif len(rest) == 1:
            iterable = Lit(rest[0][0], rest[0][1])
        else:
            raise ScriptError(_at(ln.lineno, usage))
        body = self.parse_body(indent, ln.lineno, 'for', inline)
        return For(ln.lineno, name, iterable, body)

    def make_assert(self, lineno, rich):
        expr, rest = parse_expr(rich[1:], lineno)
        cond = ' '.join(_texts(rich[1:len(rich) - len(rest)]))
        return Assert(lineno, expr, cond, _texts(rest))

    def inline_stmt(self, lineno, tokens):
        """冒号后同行内联的一条简单语句（块语句已被 parse_body 拦下）。"""
        if tokens[0][0] == 'assert':
            return self.make_assert(lineno, tokens)
        return Command(lineno, _texts(tokens), ' '.join(_texts(tokens)),
                       [q for _, q in tokens])
