# -*- coding: utf-8 -*-
"""hb 命令行：用 happybrowser 快速驱动浏览器，供人工或 AI（shell 调用）使用。

    hb open https://example.com          # 打开页面（无 --new 则用当前标签页导航）
    hb --headless open https://a.com     # 无头模式：不占窗口（仅影响新启动的浏览器）
    hb open www.baidu.com                # 缺协议自动补：裸域名 https，localhost/IP http
    hb find "#kw"                        # DSL 定位（? 可省略），输出 @1/@2… 引用
    hb find ".item" --all               # 返回全部匹配；--index 3 只取第 3 个（-1 末尾）
    hb text "@2 >> .price"              # 在第 2 个匹配里继续找（>> 后代，> 直接子元素）
    hb attr "@2 parent" id              # 方向轴：parent / next / prev；next(2)、next(.btn) 也行
    hb click "@1"                        # 按引用点击（也可直接 hb click "#kw"）
    hb input "@1" "关键词"               # 清空后输入
    hb press "@1" Enter                  # 按键：Enter / Tab / Escape / ArrowDown / ctrl+a …
    hb select "#city" "上海" --by value    # 下拉框选择（默认按选项文本；--by value / --by index）
    hb text "#result"                    # 读文本；attr/html/js/shot 同理
    hb hl / hb hl "@1"                   # 高亮（无参=高亮 find 缓存的所有引用；nohl 取消）
    hb tabs / hb tab <id前缀>            # 标签页列表与切换
    hb explain "a && .btn"               # DSL 调试：AST / 执行策略 / 下推 XPath
    hb run flow.hb                       # 执行 .hb 脚本（等价简写 hb flow.hb）
    hb -e "open https://a.com" -e "find '#q'"   # 代码直接当参数执行（可重复 -e 拼行）
    hb                                   # 不带参数：打印帮助后退出（退出码 0）

脚本模式（hb run / hb xxx.hb / hb -e）：
- 每行就是去掉 hb 前缀的子命令，一次进程、一次 CDP 连接跑完全部命令，
  比逐条 hb 调用快得多；@n 引用 / 当前标签页在脚本内自然共享。
- AI 用 `hb -e "<代码>"` 最方便：不落盘、不需要文件路径，可多次 -e 依次拼接。
- `--headless` 可写进 `#!` 头行（`#! --headless --port 9222`），无头模式不占窗口。
- 语法见 happybrowser.hbscript：注释、#! 头行、let 变量 + ${name} 插值、sleep/echo、
  debug/info/warn/error 日志 + loglevel、assert 断言、if/elif/else、loop/for 缩进块。
- `run 子脚本.hb 参数…`：同进程嵌套执行另一个脚本；子脚本用 ${1}/${2}…、
  ${argc}、${argv} 取参数；变量隔离，日志门槛与失败计数共享。
- 默认遇错即停（退出码 1）；-c/--continue 继续后续行，结尾汇总失败条数。

会话规则：
- 默认接管 127.0.0.1:9222 上已在跑的浏览器；端口空着则启动一只（--existing 可禁止启动）。
  接管语义三原则内建：绝不重启/清空别人的浏览器。
- `--headless` 不占窗口：端口空着就在该端口启动无头实例；端口上已有有头实例时
  自动换一个空闲端口启动（并在 state 里记住，后续 --headless 自动复用），绝不重启有头实例。
- 每条命令是独立进程，通过 ~/.happybrowser/state_{port}.json 共享状态：
  当前标签页 + find 缓存的元素引用（@n 按 backendNodeId 复活，页面跳转后失效需重新 find）。
- 全局 --json 输出结构化结果（AI 友好）；错误走 stderr、退出码 1。
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

from requests import Session

from . import Options, __version__, explain_loc
from ._core.browser import Browser
from ._core.keys import Keys, keys_to_typing, send_key
from ._core.tools import port_is_using
from ._elements.element import Element, _get_node_by_backend_id
from ._elements.none_element import NoneElement
from .errors import BaseError
from .hbscript import (LOG_LEVELS, Assert, For, If, ListLit, Loop, ScriptError,
                       eval_expr, interpolate, parse_let, parse_script, value_of)

STATE_DIR = Path.home() / '.happybrowser'
_MAX_REFS = 50          # find 最多缓存的引用数，防止状态文件膨胀
_TEXT_SNIPPET = 60       # 列表输出的文本截断长度


# ----------------------------------------------------------------状态文件

def _state_file(port):
    return STATE_DIR / f'state_{port}.json'


def _load_state(port):
    try:
        return json.loads(_state_file(port).read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save_state(port, state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _state_file(port).write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')


# ----------------------------------------------------------------输出

def _out(ctx, obj, text):
    """统一出口：--json 时 obj（dict/list），否则 text（人读格式）。"""
    if ctx.json:
        print(json.dumps(obj, ensure_ascii=False))
    elif text:
        print(text)


def _fail(msg, code=1):
    """统一错误出口：抛给 main 处理，脚本模式才能在错误上补「第 N 行」。"""
    raise _CliError(msg, code)


class _CliError(Exception):
    def __init__(self, msg, code=1):
        super().__init__(msg)
        self.code = code


# ----------------------------------------------------------------浏览器与定位

def _ele_brief(ele):
    # iframe/frame/object 查到即被包装成 Frame 对象：摘要取它的 <iframe> 元素本身
    if getattr(ele, '_type', None) == 'Frame':
        ele = ele.frame_ele
    text = (ele.text or '').replace('\n', ' ').strip()
    return {'tag': ele.tag, 'id': ele.attr('id'),
            'text': text[:_TEXT_SNIPPET] + ('…' if len(text) > _TEXT_SNIPPET else '')}


def _ref_owners(ele):
    """元素所在的 iframe 链：由外到内各层宿主元素的 backendNodeId（顶层元素为 []）。

    跨进程 iframe（真实 Chrome 默认开站点隔离，跨站 iframe 都是）里的节点只属于那个 frame 的
    target：把引用绑在顶层标签页上，下一条 text/input 就会报「不属于本文档」。"""
    hosts = []
    owner = ele.owner
    while getattr(owner, '_type', None) == 'Frame':
        host = owner.frame_ele
        hosts.append(host._backend_id)
        owner = host.owner
    hosts.reverse()
    return hosts


def _save_refs(ctx, eles):
    ctx.state['tab_id'] = ctx.tab.tab_id
    ctx.state['url'] = ctx.tab.url
    ctx.state['refs'] = [{'backend_id': e._backend_id, 'owners': _ref_owners(e), **_ele_brief(e)}
                        for e in eles[:_MAX_REFS]]
    _save_state(ctx.port, ctx.state)


def _revive_ref(ctx, target):
    """@n 引用复活：成功返回 (ele, None)，失败返回 (None, 原因)。"""
    refs = ctx.state.get('refs') or []
    try:
        index = int(target[1:])
    except ValueError:
        index = 0
    if not 1 <= index <= len(refs):
        return None, f'引用 {target} 不存在（当前缓存 {len(refs)} 条，请先 find）'
    ref = refs[index - 1]
    if ctx.state.get('tab_id') != ctx.tab.tab_id:
        return None, '引用属于另一个标签页，请在本页重新 find'
    try:
        page = ctx.tab
        for host_id in ref.get('owners', ()):      # 按存下的链逐层重建 Frame 对象
            page = _as_frame(Element(page, backend_id=host_id))
        ele = Element(page, backend_id=ref['backend_id'])  # 三 id 懒互转，构造零开销
        _ = ele.tag  # 触发一次 describeNode，验证节点还活着
    except BaseError:
        return None, f'引用 {target} 已失效（页面大概跳转过），请重新 find'
    return ele, None


_REF_HEAD = re.compile(r'^@(\d+)')
_AXIS_NAMES = ('parent', 'next', 'prev')
_AXIS_RE = re.compile(r'(parent|next|prev)(?![A-Za-z0-9_.\-])')
_FRAME_TAGS = ('iframe', 'frame', 'object')

_MODIFIERS = {'ctrl': Keys.CTRL, 'control': Keys.CTRL, 'alt': Keys.ALT, 'shift': Keys.SHIFT,
              'meta': Keys.META, 'cmd': Keys.META}
_NAMED_KEYS = {'enter': Keys.ENTER, 'return': Keys.RETURN, 'tab': Keys.TAB, 'escape': Keys.ESCAPE,
               'esc': Keys.ESCAPE, 'backspace': Keys.BACKSPACE, 'delete': Keys.DELETE, 'del': Keys.DELETE,
               'space': Keys.SPACE, 'up': Keys.UP, 'down': Keys.DOWN, 'left': Keys.LEFT, 'right': Keys.RIGHT,
               'pageup': Keys.PAGE_UP, 'pagedown': Keys.PAGE_DOWN, 'home': Keys.HOME, 'end': Keys.END,
               'arrowup': Keys.UP, 'arrowdown': Keys.DOWN, 'arrowleft': Keys.LEFT, 'arrowright': Keys.RIGHT}
_NAMED_KEYS.update({f'f{i}': getattr(Keys, f'F{i}') for i in range(1, 13)})


def _consume_parens(s):
    """s 以 '(' 开头 → (括号内文本, 右括号后的下标)；引号里的括号不算。"""
    depth, i, quote = 0, 0, ''
    while i < len(s):
        c = s[i]
        if quote:
            if c == quote:
                quote = ''
        elif c in '"\'':
            quote = c
        elif c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return s[1:i], i + 1
        i += 1
    raise _CliError(f'引用链的括号没有闭合：{s!r}')


def _parse_ref_chain(target):
    """解析引用定位链，如 `@3 parent >> .price > span` / `@2 next(2)`。

    返回 (ref, steps)，step 为 (kind, arg)：
    - `('>>', 定位符)` 后代查找、`('>', 定位符)` 直接子元素
    - `('parent'|'next'|'prev', None|层数|索引|定位符)`
    不是引用链（如普通 DSL `@data-id` / `#kw`）返回 None。
    方向轴只能紧跟在 `@n`（或上一个方向轴）后面；子定位符里不能再出现 `>`。
    """
    m = _REF_HEAD.match(target)
    if not m:
        return None
    ref = m.group(0)
    rest = target[m.end():].lstrip()
    steps = []
    while rest and not rest.startswith('>'):
        am = _AXIS_RE.match(rest)
        if not am:
            raise _CliError(
                f'无法识别的引用步骤：{target!r}'
                '（支持 parent/next/prev，或 >> 后代 / > 直接子元素）')
        axis = am.group(1)
        rest = rest[am.end():].lstrip()
        arg = None
        if rest.startswith('('):
            content, pos = _consume_parens(rest)
            rest = rest[pos:].lstrip()
            content = content.strip()
            if not content:
                raise _CliError(f'{axis}() 的参数不能为空：{target!r}')
            if re.fullmatch(r'-?\d+', content):
                arg = int(content)
                if arg < 1:
                    raise _CliError(f'{axis}() 的参数必须是正整数：{target!r}')
            else:
                arg = content
        steps.append((axis, arg))
    if not rest:
        return ref, steps
    while rest:
        if rest.startswith('>>'):
            kind, rest = '>>', rest[2:].strip()
        else:
            kind, rest = '>', rest[1:].strip()
        ends = [i for i in (rest.find('>>'), rest.find('>')) if i >= 0]
        cut = min(ends) if ends else len(rest)
        loc, rest = rest[:cut].strip(), rest[cut:]
        if not loc:
            raise _CliError(f'引用链缺少定位符：{target!r}（写法：@2 >> .item）')
        steps.append((kind, loc))
    return ref, steps


def _ref_step(ele, kind, arg, timeout):
    """沿引用链走一步：>> 后代 / > 子元素 / parent / next / prev。"""
    if kind == '>>':
        return ele.ele(arg, timeout=timeout)
    if kind == '>':
        return ele.child(arg, timeout=timeout)
    method = getattr(ele, kind)
    if arg is None:
        return method(timeout=timeout)
    if isinstance(arg, int) and kind != 'parent':
        return method(index=arg, timeout=timeout)
    return method(arg, timeout=timeout)


def _as_frame(ele):
    """iframe/frame/object 元素 → Frame 对象（与 find 首次查到时的包装一致）。

    引用复活出来的是普通 Element，`>>` 只能走 this.contentDocument（仅同域）；
    包装成 Frame 后同域、异域（OOPIF）都能进到 frame 文档里查找。"""
    if ele.tag not in _FRAME_TAGS:
        return ele
    return _get_node_by_backend_id(ele.owner, ele._backend_id) or ele


def _revive_target(ctx, target):
    """@n 或 @n 链 → (ele, None)；非引用链 → (None, None)；失败 → (None, 原因)。"""
    try:
        chain = _parse_ref_chain(target)
    except _CliError as e:
        return None, str(e)
    if chain is None:
        return None, None
    ref, steps = chain
    ele, err = _revive_ref(ctx, ref)
    if err:
        return None, err
    if steps and steps[0][0] in ('>>', '>'):
        ele = _as_frame(ele)
    for kind, arg in steps:
        try:
            ele = _ref_step(ele, kind, arg, ctx.timeout)
        except BaseError as e:
            return None, f'{target}：{e}'
        if isinstance(ele, NoneElement):
            return None, f'未找到：{target}（timeout={ctx.timeout}s）'
    return ele, None


def _find_matches(ctx, locator):
    """find 的目标 → (eles, err)；支持在 @n 引用作用域里继续查找。"""
    try:
        chain = _parse_ref_chain(locator)
    except _CliError as e:
        return [], str(e)
    if chain is None:
        return list(ctx.tab.eles(locator, timeout=ctx.timeout)), None
    ref, steps = chain
    ele, err = _revive_ref(ctx, ref)
    if err:
        return [], err
    if steps and steps[0][0] in ('>>', '>'):
        ele = _as_frame(ele)
    if not steps:
        return [ele], None
    for kind, arg in steps[:-1]:
        try:
            ele = _ref_step(ele, kind, arg, ctx.timeout)
        except BaseError as e:
            return [], f'{locator}：{e}'
        if isinstance(ele, NoneElement):
            return [], f'未找到：{locator}（timeout={ctx.timeout}s）'
    kind, arg = steps[-1]
    try:
        if kind in _AXIS_NAMES:
            ele = _ref_step(ele, kind, arg, ctx.timeout)
            if isinstance(ele, NoneElement):
                return [], f'未找到：{locator}（timeout={ctx.timeout}s）'
            return [ele], None
        eles = ele.eles(arg, timeout=ctx.timeout) if kind == '>>' else ele.children(arg, timeout=ctx.timeout)
    except BaseError as e:
        return [], f'{locator}：{e}'
    return list(eles), None


def _resolve(ctx, target, method):
    """@n（可带方向轴/子定位链）走引用复活，否则按 DSL 定位符查找。"""
    ele, err = _revive_target(ctx, target)
    if err:
        _fail(err)
    if ele is not None:
        return ele
    ele = ctx.tab.ele(target, timeout=ctx.timeout)
    if isinstance(ele, NoneElement):
        _fail(f'未找到：{target!r}（{method}，timeout={ctx.timeout}s）')
    return ele


# ----------------------------------------------------------------子命令

def cmd_open(ctx, a):
    tab = ctx.browser.new_tab(a.url) if a.new else ctx.tab
    if not a.new:
        tab.get(a.url)
    ctx.tab = tab
    ctx.state['tab_id'] = tab.tab_id
    ctx.state['refs'] = []
    _save_state(ctx.port, ctx.state)
    _out(ctx, {'tab_id': tab.tab_id, 'url': tab.url, 'title': tab.title},
         f'{tab.tab_id}  {tab.title}\n{tab.url}')


def cmd_tabs(ctx, a):
    tabs = []
    for tid in ctx.browser.tab_ids:
        t = ctx.browser.get_tab(tid)
        tabs.append({'tab_id': tid, 'title': t.title, 'url': t.url})
    cur = ctx.state.get('tab_id')
    if ctx.json:
        _out(ctx, tabs, '')
        return
    for t in tabs:
        mark = '*' if t['tab_id'] == cur else ' '
        print(f"{mark} {t['tab_id'][:12]}  {t['title'][:30]:<30}  {t['url'][:70]}")


def cmd_tab(ctx, a):
    ids = [t for t in ctx.browser.tab_ids if t.startswith(a.id)]
    if len(ids) != 1:
        _fail(f'id 前缀 {a.id!r} 匹配到 {len(ids)} 个标签页，请加长前缀（hb tabs 查看）')
    tab = ctx.browser.get_tab(ids[0])
    tab.activate()
    ctx.tab = tab
    ctx.state['tab_id'] = ids[0]
    ctx.state['refs'] = []
    _save_state(ctx.port, ctx.state)
    _out(ctx, {'tab_id': ids[0], 'url': tab.url, 'title': tab.title},
         f'{ids[0]}  {tab.title}\n{tab.url}')


def cmd_find(ctx, a):
    if a.index is not None and a.all:
        _fail('--index 与 --all 不能同时使用')
    eles, err = _find_matches(ctx, a.locator)
    if err:
        _fail(err)
    if a.index is not None:
        total = len(eles)
        pos = a.index - 1 if a.index > 0 else total + a.index
        if a.index == 0 or not 0 <= pos < total:
            _fail(f'索引 {a.index} 越界：{a.locator!r} 共 {total} 个匹配')
        eles = [eles[pos]]
    elif not a.all:
        eles = eles[:10]
    _save_refs(ctx, eles)
    if ctx.json:
        _out(ctx, [{'ref': f'@{i + 1}', **_ele_brief(e)} for i, e in enumerate(eles)], '')
    elif not eles:
        print(f'未找到：{a.locator!r}')
    else:
        for i, e in enumerate(eles):
            info = _ele_brief(e)
            id_part = f" id={info['id']!r}" if info['id'] else ''
            print(f"@{i + 1:<3}<{info['tag']}>{id_part}  {info['text']}")
        if a.index is None and not a.all and len(eles) == 10:
            print('（仅显示前 10 条，--all 看全部）')


def cmd_click(ctx, a):
    _resolve(ctx, a.target, 'click').click()
    _out(ctx, {'clicked': a.target}, f'已点击 {a.target}')


def cmd_input(ctx, a):
    ele = _resolve(ctx, a.target, 'input')
    ele.input(a.text, clear=True)
    val = ele.property('value') or ''
    _out(ctx, {'input': a.target, 'value': val}, f'已输入 → {val!r}')


def _parse_keys(text):
    """'ctrl+a' → (Keys.CTRL, 'a')；'Enter' → (Keys.ENTER,)；'ctrl++' → (Keys.CTRL, '+')。"""
    parts = re.split(r'(?<!\+)\+', text)      # 末尾的 '+'（如 ctrl++）是加号键本身，不当分隔符
    key = parts.pop()
    if key == '':
        key = '+'
    mods = []
    for part in parts:
        mod = _MODIFIERS.get(part.lower())
        if mod is None:
            _fail(f'未知的修饰键：{part!r}（可用 ctrl / alt / shift / meta）')
        mods.append(mod)
    return (*mods, _NAMED_KEYS.get(key.lower(), key))


def cmd_press(ctx, a):
    """在目标上按键。走真实按键事件（Input.dispatchKeyEvent），不是 insertText：
    页面的 keydown 监听、表单隐式提交、快捷键都收得到。"""
    ele = _resolve(ctx, a.target, 'press')
    modifier, keys = keys_to_typing(_parse_keys(a.keys))
    if not keys:
        _fail(f'没有可按的键：{a.keys!r}')
    if ele.tag == 'body':
        # 页面级按键（录制器在“谁都没焦点”时生成的写法）：焦点可能还在 iframe 里，
        # DOM.focus(body) 拉不回来，先让当前焦点元素失焦
        ele._run_js('if (document.activeElement && document.activeElement !== this) document.activeElement.blur();'
                    'return true;')
    ele._input_focus()
    for key in keys:
        send_key(ele.owner, modifier, '\n' if key == Keys.ENTER else key)
    _out(ctx, {'pressed': a.keys, 'target': a.target}, f'已按下 {a.keys}（{a.target}）')


def cmd_select(ctx, a):
    """下拉框选择：默认按选项文本，--by value 按 option 的 value，--by index 按序号（1 起）。"""
    ele = _resolve(ctx, a.target, 'select')
    if ele.tag != 'select':
        _fail(f'select 只能用于 <select> 元素，目标是 <{ele.tag}>')
    choose = getattr(ele.select, f'by_{a.by}')
    for value in a.values:
        choose(int(value) if a.by == 'index' else value)
    picked = [o.text for o in ele.select.selected_options]
    _out(ctx, {'selected': picked, 'by': a.by}, ' → '.join(picked) if picked else '未选中任何选项')


def cmd_text(ctx, a):
    text = _resolve(ctx, a.target, 'text').text
    _out(ctx, {'text': text}, text or '')


def cmd_attr(ctx, a):
    v = _resolve(ctx, a.target, 'attr').attr(a.name)
    _out(ctx, {'attr': a.name, 'value': v}, '' if v is None else str(v))


def cmd_html(ctx, a):
    html = _resolve(ctx, a.target, 'html').html
    _out(ctx, {'html': html}, html)


def cmd_js(ctx, a):
    r = ctx.tab.run_js(a.code)
    _out(ctx, {'result': r}, repr(r))


def cmd_shot(ctx, a):
    p = Path(a.path)
    saved = ctx.tab.get_screenshot(path=str(p.parent), name=p.name, full_page=a.full)
    _out(ctx, {'path': str(saved)}, str(saved))


def cmd_url(ctx, a):
    _out(ctx, {'url': ctx.tab.url, 'title': ctx.tab.title}, f'{ctx.tab.url}\n{ctx.tab.title}')


def cmd_explain(ctx, a):
    print(explain_loc(a.locator))


def cmd_close(ctx, a):
    if len(ctx.browser.tab_ids) < 2:
        _fail('最后一个标签页不能关闭（会连带退出浏览器）')
    ctx.tab.close()
    ctx.state['tab_id'] = None
    ctx.state['refs'] = []
    _save_state(ctx.port, ctx.state)
    _out(ctx, {'closed': True}, '已关闭')


# ----------------------------------------------------------------高亮
# 用 outline 而非 border：不占布局，不会让页面元素“跳”一下。
# 元素原有的内联 outline 备份在页面侧 WeakMap + Set（window.__hb_hl），
# 所以重复 hl 不覆盖备份、nohl 能写回原状，无目标 nohl 可整页清空。
_HL_ON_JS = '''
const KEY = '__hb_hl';
window[KEY] = window[KEY] || {store: new WeakMap(), all: new Set()};
const reg = window[KEY];
if (!reg.store.has(this)) reg.store.set(this, [this.style.outline, this.style.outlineOffset]);
reg.all.add(this);
this.style.outline = '2px solid ' + %s;
this.style.outlineOffset = '1px';
return true;
'''

_HL_OFF_JS = '''
const KEY = '__hb_hl';
const reg = window[KEY];
if (!reg || !reg.store.has(this)) return false;
const saved = reg.store.get(this);
this.style.outline = saved[0];
this.style.outlineOffset = saved[1];
reg.store.delete(this);
reg.all.delete(this);
return true;
'''

_HL_CLEAR_JS = '''
const KEY = '__hb_hl';
const reg = window[KEY];
if (!reg) return 0;
let n = 0;
for (const el of reg.all) {
    const saved = reg.store.get(el);
    if (saved) {
        el.style.outline = saved[0];
        el.style.outlineOffset = saved[1];
        reg.store.delete(el);
        n++;
    }
}
reg.all.clear();
return n;
'''


def _hl_expand(ctx, a):
    """把 hl/nohl 的目标展开为 [(label, ele)] 与错误说明列表。

    不给目标 → find 缓存的全部引用；--all 时定位符取全部匹配（默认只取首个）；
    解析失败不直接退出，交调用方决定是报错还是忽略（nohl 需要容忍失效引用）。
    """
    targets = list(a.targets)
    if not targets:
        targets = [f'@{i + 1}' for i in range(len(ctx.state.get('refs') or []))]
        if not targets:
            return [], ['没有可用的引用（先 hb find 再 hl，或直接给定位符）']
    pairs, errors = [], []
    for target in targets:
        ele, err = _revive_target(ctx, target)   # @n / @n>>子定位
        if err:
            errors.append(err)
        elif ele is not None:
            pairs.append((target, ele))
        elif a.all:
            eles = ctx.tab.eles(target, timeout=ctx.timeout)
            if not eles:
                errors.append(f'未找到：{target!r}（timeout={ctx.timeout}s）')
            pairs += [(f'{target}#{i + 1}', ele) for i, ele in enumerate(eles)]
        else:
            ele = ctx.tab.ele(target, timeout=ctx.timeout)
            if isinstance(ele, NoneElement):
                errors.append(f'未找到：{target!r}（timeout={ctx.timeout}s）')
            else:
                pairs.append((target, ele))
    return pairs, errors


def _warn(errors):
    for err in errors:
        print(f'警告：{err}', file=sys.stderr)


def cmd_hl(ctx, a):
    pairs, errors = _hl_expand(ctx, a)
    js = _HL_ON_JS % json.dumps(a.color)
    rows = []
    for label, ele in pairs:
        try:
            ele.run_js(js)
            rows.append({'target': label, **_ele_brief(ele)})
        except BaseError as e:  # 找完到高亮之间元素消失等
            errors.append(f'{label} 高亮失败：{e}')
    if not rows:
        _fail('；'.join(errors) if errors else '没有高亮任何元素')
    _warn(errors)
    if ctx.json:
        _out(ctx, {'count': len(rows), 'color': a.color, 'highlighted': rows}, '')
        return
    print(f'已高亮 {len(rows)} 个元素（{a.color}）：')
    for r in rows:
        id_part = f" id={r['id']!r}" if r['id'] else ''
        print(f"  {r['target']}  <{r['tag']}>{id_part}  {r['text']}")


def cmd_nohl(ctx, a):
    cleared, restored, errors = 0, [], []
    if a.targets:  # 指定目标：只取消这些
        pairs, errors = _hl_expand(ctx, a)
        for label, ele in pairs:
            try:
                if ele.run_js(_HL_OFF_JS):
                    restored.append(label)
                    cleared += 1
                else:
                    errors.append(f'{label} 没有 hb 留下的高亮（未改动）')
            except BaseError as e:
                errors.append(f'{label} 取消高亮失败：{e}')
    else:  # 不给目标：清空本页所有 hb 高亮（find 缓存换代后也能清干净）
        try:
            cleared += ctx.tab.run_js(_HL_CLEAR_JS) or 0
        except BaseError as e:
            _fail(f'清空高亮失败：{e}')
    _warn(errors)
    _out(ctx, {'cleared': cleared, 'targets': restored},
         f'已取消 {cleared} 个高亮' if cleared else '没有需要取消的高亮')


# 不需要浏览器连接的子命令（本地即可执行）
_NO_BROWSER = {cmd_explain}


# ----------------------------------------------------------------脚本（.hb）

def cmd_run(ctx, a):
    """占位：run 由 _dispatch 特判（要先读 #! 头行才知道连哪个端口）。"""
    _fail('内部错误：run 应由 _dispatch 直接处理')


class _Ctx:
    """一条命令或一段脚本的执行上下文（浏览器连接 + 会话状态）。"""


def _explicit_flags(argv):
    """命令行上显式出现的 --port/--timeout/--headless（用于「CLI 优先于 #! 头行」）。"""
    flags = set()
    for token in argv:
        name = token.split('=', 1)[0]
        if name in ('--port', '--timeout', '--headless'):
            flags.add(name)
    return flags


def _new_ctx(args, explicit=()):
    ctx = _Ctx()
    ctx.port, ctx.json, ctx.timeout = args.port, args.json, args.timeout
    ctx.headless = getattr(args, 'headless', False)
    ctx.explicit = set(explicit)
    ctx.existing = getattr(args, 'existing', False)
    ctx.state = _load_state(ctx.port)
    ctx.base_port = ctx.port      # 用户指定/头行给定的端口，换无头端口时也要记住它
    ctx.browser = ctx.tab = None
    return ctx


def _debug_alive(port):
    """端口上是否是活着的 CDP 浏览器（判断记住的无头端口能否复用）。"""
    try:
        with Session() as s:
            s.trust_env = False
            r = s.get(f'http://127.0.0.1:{int(port)}/json/version', timeout=1.5,
                      headers={'Connection': 'close'})
            return 'webSocketDebuggerUrl' in r.json()
    except Exception:
        return False


def _connect(ctx):
    """连接浏览器。--headless 遇到有头实例时自动换端口启动一只，绝不重启有头实例。"""
    if ctx.headless:
        saved = ctx.state.get('headless_port')   # 之前为 --headless 记住的端口
        if saved and int(saved) != ctx.port and _debug_alive(saved):
            ctx.port = int(saved)
            ctx.state = _load_state(ctx.port)
    switched = False
    while True:
        opts = Options()
        opts.set_local_port(ctx.port)
        if ctx.existing:
            opts.existing_only = True
        if ctx.headless:
            opts.headless()
        try:
            ctx.browser = Browser(opts)
        except BaseError as e:
            _fail(f'连接 127.0.0.1:{ctx.port} 失败：{e}')
        launched = bool(ctx.browser.command_line)   # 本进程启动的浏览器才有启动参数
        browser_id = ctx.browser.id
        if ctx.state.get('browser_id') not in (None, browser_id):
            # 端口上的浏览器换过了，上一只的「无头/标签页/引用」全部作废
            for key in ('headless', 'browser_id', 'tab_id', 'refs'):
                ctx.state.pop(key, None)
        known_headless = bool(ctx.state.get('headless')) or ctx.browser.is_headless
        if launched:
            ctx.state.update(headless=bool(ctx.headless), browser_id=browser_id)
            if ctx.headless:
                ctx.state['headless_port'] = ctx.port
            _save_state(ctx.port, ctx.state)
            break
        if not ctx.headless or known_headless:
            if ctx.browser.is_headless and not ctx.state.get('headless'):
                ctx.state.update(headless=True, browser_id=browser_id)
                _save_state(ctx.port, ctx.state)
            break
        # --headless 但端口上是有头实例（或无头状态未知）：换端口，不动它
        if ctx.existing:
            _fail(f'127.0.0.1:{ctx.port} 上不是可确认的无头浏览器'
                  f'（--headless --existing 只接管、不启动）')
        if switched:
            _fail(f'127.0.0.1:{ctx.port} 上已有有头浏览器，换端口后仍未能启动无头实例')
        old = ctx.port
        switched = True
        _close(ctx)
        new = _free_port_hint(old)
        if new is None:
            _fail('没有可用端口启动无头实例')
        base_state = ctx.state if ctx.port == ctx.base_port else _load_state(ctx.base_port)
        base_state['headless_port'] = new
        _save_state(ctx.base_port, base_state)
        print(f'提示：127.0.0.1:{old} 上已有浏览器且未确认是无头实例，'
              f'--headless 改用 {new} 启动无头实例（已记住，后续 --headless 自动复用）',
              file=sys.stderr)
        ctx.port = new
        ctx.state = _load_state(new)
    tab_id = ctx.state.get('tab_id')
    ctx.tab = (ctx.browser.get_tab(tab_id) if tab_id and tab_id in ctx.browser.tab_ids
               else ctx.browser.latest_tab)


def _free_port_hint(port, tries=20):
    """给 --headless 换端口时找一个可能可用的端口。"""
    for candidate in range(port + 1, min(port + 1 + tries, 65536)):
        if not port_is_using('127.0.0.1', candidate):
            return candidate
    return None


def _close(ctx):
    if ctx.browser is not None:
        ctx.browser._stop()  # 只断开连接，不动浏览器（接管语义）
        ctx.browser = None


def _apply_header(ap, script, ctx, name):
    """#! 头行：连接前选项；端口变了，状态文件也跟着换。

    命令行上显式给的 --port/--timeout 优先（脚本可复用，不绑死本机端口）。
    """
    tokens = script.header
    if not tokens:
        return
    try:
        head = ap.parse_args(tokens)
    except SystemExit:
        _fail(f'{name}：#! 行只接受全局选项（如 --port 9222 --timeout 3）')
    if getattr(head, 'fn', None) is not None:
        _fail(f'{name}：#! 行只接受全局选项，不能写子命令')
    if '--port' in tokens and '--port' not in ctx.explicit:
        ctx.port = head.port
        ctx.base_port = ctx.port
        ctx.state = _load_state(ctx.port)
    if '--timeout' in tokens and '--timeout' not in ctx.explicit:
        ctx.timeout = head.timeout
    if '--headless' in tokens and '--headless' not in ctx.explicit:
        ctx.headless = True
    if '--json' in tokens:
        ctx.json = True
    if '--existing' in tokens:
        ctx.existing = True


def _sleep_seconds(tokens):
    if len(tokens) < 2:
        raise ScriptError('sleep 需要秒数，如：sleep 1.5')
    try:
        return max(0.0, float(tokens[1]))
    except ValueError:
        raise ScriptError(f'sleep 秒数不合法：{tokens[1]!r}') from None


def _sticky_options(ctx, tokens, ns):
    """纯全局选项行（如 `--timeout 2`）→ 改脚本后续行的默认值。"""
    if '--json' in tokens:
        ctx.json = True
    if '--timeout' in tokens:
        ctx.timeout = ns.timeout
    if '--port' in tokens and ns.port != ctx.port:
        _fail(f'端口只能在 #! 头行或 hb 命令行指定（已连接 {ctx.port}）')
    if '--headless' in tokens and not ctx.headless:
        _fail('无头模式只能在 #! 头行或 hb 命令行指定（连接已建立）')


# ----------------------------------------------------------------脚本解释器（遍历 hbscript 的 AST）

class _ScriptEnv:
    """一段脚本的执行状态：变量、失败计数、日志门槛、嵌套 run 信息。"""

    def __init__(self):
        self.variables = {}
        self.failures = 0
        self.log_level = LOG_LEVELS['info']
        self.name = ''                  # 当前脚本名（报错定位；嵌套 run 时切换）
        self.base_dir = Path.cwd()      # 嵌套 run 的相对路径解析基准
        self.depth = 0                  # run 嵌套层数（防循环调用）


class _AbortScript(Exception):
    """某行失败且未开 -c 时，用它展开递归执行栈。"""


def _emit_log(env, level, msg):
    """日志统一出口：走 stderr（stdout 留给数据）；error = 软断言，记失败但继续。"""
    if LOG_LEVELS[level] >= env.log_level:
        print(f'[{level.upper()}] {msg}', file=sys.stderr)
    if level == 'error':
        env.failures += 1


def _render_line(cmd, variables):
    """命令行 → 字符串 argv：命令名只做 ${} 插值；参数按类型系统取值（裸标识符 = 变量）。"""
    quoted = cmd.quoted or [False] * len(cmd.tokens)
    head = interpolate([cmd.tokens[0]], variables)[0]
    args = []
    for text, q in zip(cmd.tokens[1:], quoted[1:]):
        v = value_of(text, q, variables, strict=True)
        args.append(v if isinstance(v, str) else str(v))
    return [head] + args


def _bi_let(ctx, env, cmd):
    quoted = cmd.quoted or [False] * len(cmd.tokens)
    name, value = parse_let(cmd.tokens[1:], quoted[1:], env.variables)
    env.variables[name] = value   # 值保留类型（数字/字符串/列表/变量引用）


def _bi_sleep(ctx, env, cmd):
    time.sleep(_sleep_seconds(_render_line(cmd, env.variables)))


def _bi_echo(ctx, env, cmd):
    print(' '.join(interpolate(cmd.tokens[1:], env.variables)),
          file=sys.stderr if ctx.json else sys.stdout)


def _bi_log(ctx, env, cmd):
    _emit_log(env, cmd.tokens[0], ' '.join(interpolate(cmd.tokens[1:], env.variables)))


def _bi_loglevel(ctx, env, cmd):
    tokens = interpolate(cmd.tokens, env.variables)
    if len(tokens) != 2 or tokens[1] not in LOG_LEVELS:
        raise ScriptError(f'loglevel 需要级别名：{"/".join(LOG_LEVELS)}（默认 info）')
    env.log_level = LOG_LEVELS[tokens[1]]


# 脚本内建命令表：新增内建命令在这里登记即可，不动解释器本体
_BUILTINS = {'let': _bi_let, 'set': _bi_let, 'sleep': _bi_sleep, 'echo': _bi_echo,
             'loglevel': _bi_loglevel,
             'debug': _bi_log, 'info': _bi_log, 'warn': _bi_log, 'error': _bi_log}


def _restore_var(variables, name, saved):
    """循环结束后还原被占用的变量（内层循环不污染外层）。"""
    if saved is None:
        variables.pop(name, None)
    else:
        variables[name] = saved


def _exec_stmts(ctx, ap, stmts, args, env):
    for stmt in stmts:
        _exec_stmt(ctx, ap, stmt, args, env)


def _where(env):
    """错误定位前缀：知道脚本名就带上（嵌套 run 时尤其需要）。"""
    return f'{env.name} ' if env.name else ''


def _exec_stmt(ctx, ap, stmt, args, env):
    try:
        _dispatch_stmt(ctx, ap, stmt, args, env)
    except (ScriptError, _CliError, BaseError, SystemExit) as e:
        # BaseError：库内错误（JS 抛异常、元素丢失等）不刷 traceback
        # SystemExit：argparse 参数错误（usage 已由 argparse 打印）
        env.failures += 1
        detail = '命令行参数不正确' if isinstance(e, SystemExit) else str(e)
        print(f'错误（{_where(env)}第 {stmt.lineno} 行）：{detail}', file=sys.stderr)
        if not args.keep_going:
            raise _AbortScript from None


def _dispatch_stmt(ctx, ap, stmt, args, env):
    if isinstance(stmt, If):
        for expr, body in stmt.branches:
            if eval_expr(expr, env.variables):
                _exec_stmts(ctx, ap, body, args, env)
                return
        _exec_stmts(ctx, ap, stmt.else_body, args, env)
        return
    if isinstance(stmt, Loop):
        count = _loop_count(stmt, env)
        saved = env.variables.get('i')
        try:
            for k in range(1, count + 1):
                env.variables['i'] = k   # 计数是数字类型（可参与 assert i > 0）
                _exec_stmts(ctx, ap, stmt.body, args, env)
        finally:
            _restore_var(env.variables, 'i', saved)
        return
    if isinstance(stmt, For):
        _exec_for(ctx, ap, stmt, args, env)
        return
    if isinstance(stmt, Assert):
        if not eval_expr(stmt.expr, env.variables):
            msg = ' '.join(interpolate(stmt.msg, env.variables))
            text = f'断言失败：{stmt.cond}' + (f'（{msg}）' if msg else '')
            print(f'[ASSERT] {_where(env)}第 {stmt.lineno} 行：{text}', file=sys.stderr)
            env.failures += 1   # 断言失败 = 记失败、继续跑，收集全部问题
        return
    _exec_command(ctx, ap, stmt, args, env)


def _loop_count(stmt, env):
    raw = value_of(stmt.count[0], False, env.variables, strict=True)   # loop rounds: 认裸变量名
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        raise ScriptError(f'loop 次数不是整数：{raw!r}（变量未定义？）') from None


def _exec_for(ctx, ap, stmt, args, env):
    node = stmt.iterable
    if isinstance(node, ListLit):
        items = [value_of(lit.text, lit.quoted, env.variables, strict=True)
                 for lit in node.items]
    else:
        v = value_of(node.text, node.quoted, env.variables, strict=True)
        if isinstance(v, str) and v.startswith('range(') and v.endswith(')'):
            inner = v[6:-1].strip()
            try:
                items = list(range(max(0, int(inner))))
            except ValueError:
                raise ScriptError(f'range() 的参数不是整数：{inner!r}（变量未定义？）') from None
        elif isinstance(v, list):
            items = v
        else:
            raise ScriptError('for 需要列表（["a", "b"]）、range(N) 或列表变量')
    saved_name = env.variables.get(stmt.name)
    saved_i = env.variables.get('i')
    try:
        for k, item in enumerate(items, 1):
            env.variables[stmt.name] = item
            if stmt.name != 'i':   # for i in range(N)：i 是取值本身，不再当计数器
                env.variables['i'] = k
            _exec_stmts(ctx, ap, stmt.body, args, env)
    finally:
        _restore_var(env.variables, stmt.name, saved_name)
        _restore_var(env.variables, 'i', saved_i)


_MAX_RUN_DEPTH = 16   # run 嵌套上限：超过基本是脚本循环调用了


def _exec_nested_run(ctx, ap, cmd, args, env):
    """`run 子脚本.hb 参数…`：同进程执行另一个 .hb 脚本。

    - 参数按类型系统取值（数字/字符串/列表都保留类型），子脚本里用
      ${1}/${2}…（位置参数）、${argc}（个数）、${argv}（列表，可 for）取用。
    - 变量隔离：子脚本只看到参数，结束后父变量原样恢复；
      日志门槛与失败计数全局共享（@n 引用/当前标签页同进程天然共享）。
    - 相对路径相对调用脚本所在目录解析。
    """
    quoted = cmd.quoted or [False] * len(cmd.tokens)
    argv = [value_of(t, q, env.variables, strict=True)
            for t, q in zip(cmd.tokens[1:], quoted[1:])]
    if not argv or not isinstance(argv[0], str):
        raise ScriptError('run 需要脚本路径，如：run lib/login.hb "张三" 3')
    if env.depth >= _MAX_RUN_DEPTH:
        raise ScriptError(f'run 嵌套超过 {_MAX_RUN_DEPTH} 层（是不是循环调用了？）')
    p = Path(argv[0])
    if not p.is_absolute():
        p = env.base_dir / p
    try:
        text = p.read_text(encoding='utf-8-sig')
    except OSError as e:
        raise ScriptError(f'读取脚本失败：{e}') from None
    try:
        sub = parse_script(text)
    except ScriptError as e:
        raise ScriptError(f'{p.name}：{e}') from None
    # 子环境：变量换成参数表，脚本名与路径基准切换，结束后恢复
    saved = (env.variables, env.name, env.base_dir)
    child = {'argv': argv[1:], 'argc': len(argv) - 1}
    for k, v in enumerate(argv[1:], 1):
        child[str(k)] = v
    env.variables, env.name, env.base_dir = child, p.name, p.parent
    env.depth += 1
    try:
        _exec_stmts(ctx, ap, sub.commands, args, env)
    finally:
        env.variables, env.name, env.base_dir = saved
        env.depth -= 1


def _exec_command(ctx, ap, cmd, args, env):
    """平铺语句：内建命令 → 查表分发（拿原始 tokens + 引号标记）；其余走 hb 子命令。"""
    if args.echo:
        print(f'+ {cmd.raw}', file=sys.stderr)
    if cmd.tokens[0] == 'run':   # 嵌套 run 特判：需要 ap/args/env，不走内建表
        _exec_nested_run(ctx, ap, cmd, args, env)
        return
    builtin = _BUILTINS.get(cmd.tokens[0])
    if builtin is not None:
        builtin(ctx, env, cmd)
        return
    tokens = _render_line(cmd, env.variables)   # 子命令参数渲染成字符串 argv
    sub = ap.parse_args(tokens)
    fn = getattr(sub, 'fn', None)
    if fn is None:  # 只有全局选项的行（"--json" / "--timeout 2"）
        _sticky_options(ctx, tokens, sub)
        return
    saved = (ctx.json, ctx.timeout)
    try:
        # 行内 --json/--timeout 只作用于本行，不污染后续行
        if '--json' in tokens:
            ctx.json = True
        if '--timeout' in tokens:
            ctx.timeout = sub.timeout
        if '--port' in tokens and sub.port != ctx.port:
            _fail(f'端口只能在 #! 头行或 hb 命令行指定（已连接 {ctx.port}）')
        if '--headless' in tokens and not ctx.headless:
            _fail('无头模式只能在 #! 头行或 hb 命令行指定（连接已建立）')
        fn(ctx, sub)
    finally:
        ctx.json, ctx.timeout = saved


def _exec_script(ctx, ap, script, args):
    """遍历 AST 执行；错误带行号，默认遇错即停（-c 继续），结尾汇总失败条数。"""
    env = _ScriptEnv()
    if script.path:   # 文件脚本：记录名字与目录（嵌套 run 的相对路径基准）
        p = Path(script.path).resolve()
        env.name, env.base_dir = p.name, p.parent
    aborted = False
    try:
        _exec_stmts(ctx, ap, script.commands, args, env)
    except _AbortScript:
        aborted = True   # 遇错即停：错误行已报过，不再打总结行
    if env.failures and not aborted:
        print(f'脚本结束：{env.failures} 条命令失败', file=sys.stderr)
        return 1
    return 1 if env.failures else 0


def _run_source(ap, args, name, text, explicit=()):
    """跑一段脚本源码（来自文件 / stdin / -e 参数）。"""
    try:
        script = parse_script(text)
    except ScriptError as e:
        _fail(f'{name}：{e}')
    if name not in ('stdin', '<-e>'):
        script.path = name   # 记录路径：报错带脚本名 + 嵌套 run 相对路径基准
    ctx = _new_ctx(args, explicit)
    _apply_header(ap, script, ctx, name)
    try:
        _connect(ctx)
        return _exec_script(ctx, ap, script, args)
    finally:
        _close(ctx)


def _run_script(ap, args, explicit=()):
    if args.file == '-':
        return _run_source(ap, args, 'stdin', sys.stdin.read(), explicit)
    try:
        text = Path(args.file).read_text(encoding='utf-8-sig')
    except OSError as e:
        _fail(f'读取脚本失败：{e}')
    return _run_source(ap, args, args.file, text, explicit)


# ----------------------------------------------------------------入口

def _build_parser():
    ap = argparse.ArgumentParser(
        prog='hb', description='happybrowser 命令行：快速驱动浏览器（人工/AI 通用）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='定位用 DSL（? 可省略）："#kw" / "button && text*=登录" / "@id=x"；'
               'hb explain <loc> 查看解析。\n'
               '多匹配：find <loc> --all / --index 3；在某个匹配里继续找用 @2 >> .price'
               '（后代）或 @2 > .price（直接子元素）；\n'
               '方向：@2 parent（父）、@2 next / @2 prev（兄弟），可带参数 parent(2)、next(.btn)，'
               '再继续接 >> / > 定位链。\n'
               '脚本：hb run flow.hb（或 hb flow.hb）一次进程跑完多行命令；'
               'hb run - 从 stdin 读；hb -e "<代码>" 把代码当参数（可重复 -e 拼多行）。\n'
               '无头：hb --headless …（不占窗口；端口上有有头实例时自动换端口起无头，不动它）。')
    ap.add_argument('--version', action='version', version=f'hb {__version__}')
    ap.add_argument('-e', '--eval', metavar='CODE', action='append', default=None,
                    help='直接执行代码（多行用真实换行；可重复 -e 依次拼接；'
                         '代码以 - 开头时用 --eval=CODE）')
    ap.add_argument('-c', '--continue', dest='keep_going', action='store_true',
                    help='脚本/eval 模式：某行失败后继续执行后续行（默认遇错即停）')
    ap.add_argument('--echo', action='store_true', help='脚本/eval 模式：逐行回显命令到 stderr')
    ap.add_argument('--port', type=int, default=9222, help='调试端口（默认 9222）')
    ap.add_argument('--timeout', type=float, default=5, help='查找超时秒数（默认 5）')
    # 全局选项同时注册到主命令与所有子命令（放前放后都行）：
    # 子命令层用 SUPPRESS default，未出现时不覆盖主命令层的值
    common = argparse.ArgumentParser(add_help=False)
    for flag, kw, help_ in (
            ('--json', {'action': 'store_true'}, '输出 JSON（AI 友好）'),
            ('--existing', {'action': 'store_true'}, '只接管已运行的浏览器，不启动'),
            ('--headless', {'action': 'store_true'}, '无头模式（不占窗口；仅影响新启动的浏览器）')):
        ap.add_argument(flag, **kw, help=help_)
        common.add_argument(flag, default=argparse.SUPPRESS, **kw, help=help_)
    for flag, kw, help_ in (
            ('--port', {'type': int}, '调试端口'),
            ('--timeout', {'type': float}, '查找超时秒数')):
        common.add_argument(flag, default=argparse.SUPPRESS, **kw, help=help_)
    # 不给子命令时不报错，而是打印帮助后以 0 退出（人工/AI 都友好）
    sub = ap.add_subparsers(dest='cmd')

    p = sub.add_parser('open', help='打开/导航页面', parents=[common])
    p.add_argument('url')
    p.add_argument('--new', action='store_true', help='新开标签页')
    p.set_defaults(fn=cmd_open)

    sub.add_parser('tabs', help='列出标签页', parents=[common]).set_defaults(fn=cmd_tabs)

    p = sub.add_parser('tab', help='切换标签页（id 前缀）', parents=[common])
    p.add_argument('id')
    p.set_defaults(fn=cmd_tab)

    p = sub.add_parser('find', help='查找元素，输出 @n 引用', parents=[common])
    p.add_argument('locator', help='DSL 定位符，或 @n（可接 parent/next/prev 方向轴、>> 后代、> 子元素）')
    p.add_argument('--all', action='store_true', help='不止前 10 条')
    p.add_argument('--index', type=int, default=None, metavar='N',
                   help='只取第 N 个匹配（1 起，负数从末尾算），结果重新编号为 @1')
    p.set_defaults(fn=cmd_find)

    for name, help_ in (('click', '点击'), ('input', '输入文本（先清空）'), ('text', '读文本'),
                        ('attr', '读属性'), ('html', '读 outerHTML')):
        p = sub.add_parser(name, help=help_, parents=[common])
        p.add_argument('target', help='定位符或 @n 引用')
        if name == 'input':
            p.add_argument('text', help='要输入的文本')
        elif name == 'attr':
            p.add_argument('name', help='属性名')
        p.set_defaults(fn=globals()[f'cmd_{name}'])

    p = sub.add_parser('press', help='按键：Enter / Tab / Escape / ArrowDown / ctrl+a …', parents=[common])
    p.add_argument('target', help='定位符或 @n 引用（按键前先聚焦它；页面级快捷键用 body）')
    p.add_argument('keys', help='键名，组合键用 + 连接：Enter、shift+Tab、ctrl+a')
    p.set_defaults(fn=cmd_press)

    p = sub.add_parser('select', help='下拉框选择（默认按选项文本）', parents=[common])
    p.add_argument('target', help='定位符或 @n 引用（须是 select 元素）')
    p.add_argument('values', nargs='+', help='选项文本；--by value 时是 option 的 value，'
                                             '--by index 时是序号（1 起）；多选框可给多个')
    p.add_argument('--by', choices=('text', 'value', 'index'), default='text', help='按什么匹配选项')
    p.set_defaults(fn=cmd_select)

    p = sub.add_parser('js', help='在当前页执行 JS', parents=[common])
    p.add_argument('code')
    p.set_defaults(fn=cmd_js)

    p = sub.add_parser('shot', help='截图（默认 hb_shot.png）', parents=[common])
    p.add_argument('path', nargs='?', default='hb_shot.png')
    p.add_argument('--full', action='store_true', help='整页截图')
    p.set_defaults(fn=cmd_shot)

    sub.add_parser('url', help='当前页 url/标题', parents=[common]).set_defaults(fn=cmd_url)

    p = sub.add_parser('hl', help='高亮元素（outline，不占布局）', parents=[common])
    p.add_argument('targets', nargs='*',
                   help='定位符或 @n 引用（省略则高亮 find 缓存的全部引用）')
    p.add_argument('--all', action='store_true', help='定位符匹配的全部元素（默认只首个）')
    p.add_argument('--color', default='red', help='颜色（默认 red）')
    p.set_defaults(fn=cmd_hl)

    p = sub.add_parser('nohl', help='取消高亮（省略目标则清空本页全部）', parents=[common])
    p.add_argument('targets', nargs='*',
                   help='定位符或 @n 引用（省略则清空当前页所有 hb 高亮）')
    p.add_argument('--all', action='store_true', help='定位符匹配的全部元素（默认只首个）')
    p.set_defaults(fn=cmd_nohl)

    p = sub.add_parser('explain', help='DSL 调试（无需浏览器）', parents=[common])
    p.add_argument('locator')
    p.set_defaults(fn=cmd_explain)

    sub.add_parser('close', help='关闭当前标签页', parents=[common]).set_defaults(fn=cmd_close)

    p = sub.add_parser('run', help='执行 .hb 脚本（单进程跑完，共用一次连接）', parents=[common])
    p.add_argument('file', help='脚本路径；- 表示从 stdin 读')
    # 与主命令同名选项：子命令层用 SUPPRESS，避免覆盖放在 run 之前的 -c/--echo
    p.add_argument('-c', '--continue', dest='keep_going', action='store_true',
                   default=argparse.SUPPRESS,
                   help='某行失败后继续执行后续行（默认遇错即停）')
    p.add_argument('--echo', action='store_true', default=argparse.SUPPRESS,
                   help='逐行回显命令到 stderr')
    p.set_defaults(fn=cmd_run)

    ap.hb_subcommands = set(sub.choices)
    return ap


def _maybe_shorthand(ap, argv):
    """hb flow.hb → hb run flow.hb（没写子命令、参数里带 .hb 文件时）。"""
    if not argv or argv[0] in ('-h', '--help', '--version'):
        return argv
    if any(a in ('-e', '--eval') or a.startswith('--eval=') for a in argv):
        return argv   # -e 后面是代码，可能碰巧提到 .hb，不当文件处理
    names = getattr(ap, 'hb_subcommands', set())
    if any(a in names for a in argv):
        return argv
    if any(a.lower().endswith('.hb') for a in argv):
        return ['run', *argv]
    return argv


def _dispatch(ap, argv):
    args = ap.parse_args(argv)
    explicit = _explicit_flags(argv)
    if args.eval:
        if getattr(args, 'fn', None) is not None:
            _fail('-e 不能和子命令同时使用（把命令写进 -e 的代码里）')
        return _run_source(ap, args, '<-e>', '\n'.join(args.eval), explicit)
    if getattr(args, 'fn', None) is None:  # hb / hb --json / hb --port 9223 …
        ap.print_help()
        return 0
    if args.fn is cmd_run:
        return _run_script(ap, args, explicit)
    ctx = _new_ctx(args, explicit)
    needs_browser = args.fn not in _NO_BROWSER
    try:
        if needs_browser:
            _connect(ctx)
        args.fn(ctx, args)
    finally:
        if needs_browser:
            _close(ctx)
    return 0


def main(argv=None):
    # Windows 控制台默认 gbk，中文/特殊字符输出统一 utf-8 + 宽松替换
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    ap = _build_parser()
    argv = _maybe_shorthand(ap, list(sys.argv[1:] if argv is None else argv))
    try:
        return _dispatch(ap, argv)
    except _CliError as e:
        print(f'错误：{e}', file=sys.stderr)
        return e.code
    except BaseError as e:   # 库内错误统一成一行，AI/测试脚本好解析
        print(f'错误：{e}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
