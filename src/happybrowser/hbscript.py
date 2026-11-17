# -*- coding: utf-8 -*-
"""`.hb` 脚本语言：把 `hb` 子命令按行写进文件，同一进程内顺序执行。

语言刻意极小，只做「命令清单」一件事（逻辑仍交给 Python）：

    #! hb --port 9222 --timeout 3    ← 可选头行：连接/全局选项，须在命令之前
    # 整行注释
    open https://example.com
    let kw = 世界                    ← 变量；值可含空格
    find "#q"
    input @1 "${kw}"                 ← ${name} 插值
    sleep 0.5
    echo 完成

分词规则：
- 空白分隔；`"…"` / `'…'` 包裹带空格的参数，引号内不做反斜杠转义（Windows 路径原样）。
- `#` 在行首是注释；行内只有「空格 + # + 空格/行尾」才是注释，
  所以 `find #kw` 仍然把 `#kw` 当定位符。
- 首 token 误写成 `hb` 会自动剥掉（方便从命令行历史直接粘贴）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 只认 ${name}：`$name` 会和定位符 / JS 里的 $ 打架，干脆不提供
_VAR_RE = re.compile(r'\$\{(\w+)\}')


class ScriptError(ValueError):
    """脚本语法错误（调用方负责带上行号/文件名）。"""


@dataclass
class Command:
    lineno: int          # 1-based，报错定位用
    tokens: list[str]    # 已分词的 argv（不含 hb）
    raw: str             # 原始行（--echo 回显用）


@dataclass
class Script:
    header: list[str] = field(default_factory=list)   # #! 行的全局选项
    commands: list[Command] = field(default_factory=list)


def tokenize(line):
    """把一行切成 argv。引号未闭合抛 ScriptError。"""
    tokens, buf, quote = [], [], None
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
        elif ch.isspace():
            if buf:
                tokens.append(''.join(buf))
                buf = []
        elif ch == '#' and not buf and (i >= n or line[i].isspace()):
            break   # 行内注释：只有「空格 # 空格/行尾」才算，保住 find #kw
        else:
            buf.append(ch)
    if quote:
        raise ScriptError(f'引号未闭合：{line.strip()!r}')
    if buf:
        tokens.append(''.join(buf))
    return tokens


def parse_script(text):
    """文本 → Script。语法错误抛 ScriptError（消息已带行号）。"""
    text = text.removeprefix('\ufeff')   # 记事本存的 UTF-8 BOM
    script = Script()
    for lineno, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith('#'):
            if stripped.startswith('#!'):
                if script.header:
                    raise ScriptError(f'第 {lineno} 行：#! 头行只能出现一次')
                if script.commands:
                    raise ScriptError(f'第 {lineno} 行：#! 头行必须写在所有命令之前')
                tokens = tokenize(stripped[2:])
                if tokens[:1] == ['hb']:
                    tokens = tokens[1:]
                if not tokens:
                    raise ScriptError(f'第 {lineno} 行：#! 后面没有选项')
                script.header = tokens
            continue
        try:
            tokens = tokenize(raw)
        except ScriptError as e:
            raise ScriptError(f'第 {lineno} 行：{e}') from None
        if tokens[:1] == ['hb']:
            tokens = tokens[1:]
        if not tokens:
            raise ScriptError(f'第 {lineno} 行：只有 hb，没有子命令')
        script.commands.append(Command(lineno, tokens, stripped))
    return script


def interpolate(tokens, variables):
    """${name} 替换；未定义的原样保留（JS 模板串里的 ${x} 不会被误伤）。"""
    if not variables:
        return list(tokens)

    def sub(m):
        return variables.get(m.group(1), m.group(0))

    return [_VAR_RE.sub(sub, t) for t in tokens]


def parse_let(tokens):
    """let/set 的参数 → (name, value)。value 用空格拼回（引号已被分词吃掉）。"""
    if not tokens:
        raise ScriptError('let 需要变量名，如：let kw = 关键词')
    name = tokens[0]
    if not name.isidentifier():
        raise ScriptError(f'变量名不合法：{name!r}（字母/数字/下划线，且不以数字开头）')
    values = list(tokens[1:])
    if values[:1] == ['=']:
        values = values[1:]
    return name, ' '.join(values)
