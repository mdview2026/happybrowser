# -*- coding: utf-8 -*-
"""hb 命令行：用 happybrowser 快速驱动浏览器，供人工或 AI（shell 调用）使用。

    hb open https://example.com          # 打开页面（无 --new 则用当前标签页导航）
    hb find "#kw"                        # DSL 定位（? 可省略），输出 @1/@2… 引用
    hb click "@1"                        # 按引用点击（也可直接 hb click "#kw"）
    hb input "@1" "关键词"               # 清空后输入
    hb text "#result"                    # 读文本；attr/html/js/shot 同理
    hb tabs / hb tab <id前缀>            # 标签页列表与切换
    hb explain "a && .btn"               # DSL 调试：AST / 执行策略 / 下推 XPath

会话规则：
- 默认接管 127.0.0.1:9222 上已在跑的浏览器；端口空着则启动一只（--existing 可禁止启动）。
  接管语义三原则内建：绝不重启/清空别人的浏览器。
- 每条命令是独立进程，通过 ~/.happybrowser/state_{port}.json 共享状态：
  当前标签页 + find 缓存的元素引用（@n 按 backendNodeId 复活，页面跳转后失效需重新 find）。
- 全局 --json 输出结构化结果（AI 友好）；错误走 stderr、退出码 1。
"""
import argparse
import json
import sys
from pathlib import Path

from . import Options, __version__, explain_loc
from ._core.browser import Browser
from ._elements.element import Element
from ._elements.none_element import NoneElement
from .errors import BaseError

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
    print(f'错误：{msg}', file=sys.stderr)
    sys.exit(code)


# ----------------------------------------------------------------浏览器与定位

def _ele_brief(ele):
    text = (ele.text or '').replace('\n', ' ').strip()
    return {'tag': ele.tag, 'id': ele.attr('id'),
            'text': text[:_TEXT_SNIPPET] + ('…' if len(text) > _TEXT_SNIPPET else '')}


def _save_refs(ctx, eles):
    ctx.state['tab_id'] = ctx.tab.tab_id
    ctx.state['url'] = ctx.tab.url
    ctx.state['refs'] = [{'backend_id': e._backend_id, **_ele_brief(e)} for e in eles[:_MAX_REFS]]
    _save_state(ctx.port, ctx.state)


def _resolve(ctx, target, method):
    """@n 走状态引用复活，否则按 DSL 定位符查找。"""
    if target.startswith('@'):
        refs = ctx.state.get('refs') or []
        try:
            ref = refs[int(target[1:]) - 1]
        except (ValueError, IndexError):
            _fail(f'引用 {target} 不存在（当前缓存 {len(refs)} 条，请先 find）')
        if ctx.state.get('tab_id') != ctx.tab.tab_id:
            _fail('引用属于另一个标签页，请在本页重新 find')
        ele = Element(ctx.tab, backend_id=ref['backend_id'])  # 三 id 懒互转，构造零开销
        try:
            _ = ele.tag  # 触发一次 describeNode，验证节点还活着
        except BaseError:
            _fail(f'引用 {target} 已失效（页面大概跳转过），请重新 find')
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
    timeout = a.timeout if a.timeout is not None else ctx.timeout
    eles = ctx.tab.eles(a.locator, timeout=timeout)
    if not a.all:
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
        if not a.all and len(eles) == 10:
            print('（仅显示前 10 条，--all 看全部）')


def cmd_click(ctx, a):
    _resolve(ctx, a.target, 'click').click()
    _out(ctx, {'clicked': a.target}, f'已点击 {a.target}')


def cmd_input(ctx, a):
    ele = _resolve(ctx, a.target, 'input')
    ele.input(a.text, clear=True)
    val = ele.property('value') or ''
    _out(ctx, {'input': a.target, 'value': val}, f'已输入 → {val!r}')


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


# 不需要浏览器连接的子命令（本地即可执行）
_NO_BROWSER = {cmd_explain}


# ----------------------------------------------------------------入口

def main(argv=None):
    # Windows 控制台默认 gbk，中文/特殊字符输出统一 utf-8 + 宽松替换
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')

    ap = argparse.ArgumentParser(
        prog='hb', description='happybrowser 命令行：快速驱动浏览器（人工/AI 通用）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='定位用 DSL（? 可省略）："#kw" / "button && text*=登录" / "@id=x"；hb explain <loc> 查看解析。')
    ap.add_argument('--version', action='version', version=f'hb {__version__}')
    ap.add_argument('--port', type=int, default=9222, help='调试端口（默认 9222）')
    ap.add_argument('--timeout', type=float, default=5, help='查找超时秒数（默认 5）')
    # --json / --existing 同时注册到主命令与所有子命令（放前放后都行）：
    # 子命令层用 SUPPRESS default，未出现时不覆盖主命令层的值
    common = argparse.ArgumentParser(add_help=False)
    for flag, help_ in (('--json', '输出 JSON（AI 友好）'),
                        ('--existing', '只接管已运行的浏览器，不启动')):
        ap.add_argument(flag, action='store_true', help=help_)
        common.add_argument(flag, action='store_true', default=argparse.SUPPRESS, help=help_)
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('open', help='打开/导航页面', parents=[common])
    p.add_argument('url')
    p.add_argument('--new', action='store_true', help='新开标签页')
    p.set_defaults(fn=cmd_open)

    sub.add_parser('tabs', help='列出标签页', parents=[common]).set_defaults(fn=cmd_tabs)

    p = sub.add_parser('tab', help='切换标签页（id 前缀）', parents=[common])
    p.add_argument('id')
    p.set_defaults(fn=cmd_tab)

    p = sub.add_parser('find', help='查找元素，输出 @n 引用', parents=[common])
    p.add_argument('locator')
    p.add_argument('--all', action='store_true', help='不止前 10 条')
    p.add_argument('--timeout', type=float, default=None, help='覆盖全局超时')
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

    p = sub.add_parser('js', help='在当前页执行 JS', parents=[common])
    p.add_argument('code')
    p.set_defaults(fn=cmd_js)

    p = sub.add_parser('shot', help='截图（默认 hb_shot.png）', parents=[common])
    p.add_argument('path', nargs='?', default='hb_shot.png')
    p.add_argument('--full', action='store_true', help='整页截图')
    p.set_defaults(fn=cmd_shot)

    sub.add_parser('url', help='当前页 url/标题', parents=[common]).set_defaults(fn=cmd_url)

    p = sub.add_parser('explain', help='DSL 调试（无需浏览器）', parents=[common])
    p.add_argument('locator')
    p.set_defaults(fn=cmd_explain)

    sub.add_parser('close', help='关闭当前标签页', parents=[common]).set_defaults(fn=cmd_close)

    args = ap.parse_args(argv)

    class _Ctx:
        pass

    ctx = _Ctx()
    ctx.port, ctx.json, ctx.timeout = args.port, args.json, args.timeout
    ctx.state = _load_state(args.port)
    if args.fn not in _NO_BROWSER:
        opts = Options()
        opts.set_local_port(args.port)
        if args.existing:
            opts.existing_only = True
        try:
            ctx.browser = Browser(opts)
        except BaseError as e:
            _fail(f'连接 127.0.0.1:{args.port} 失败：{e}')
        tab_id = ctx.state.get('tab_id')
        ctx.tab = (ctx.browser.get_tab(tab_id) if tab_id and tab_id in ctx.browser.tab_ids
                   else ctx.browser.latest_tab)
        try:
            args.fn(ctx, args)
        finally:
            ctx.browser._stop()  # 只断开连接，不动浏览器（接管语义）
    else:
        args.fn(ctx, args)


if __name__ == '__main__':
    main()
