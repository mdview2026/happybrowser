# -*- coding: utf-8 -*-
"""hb 命令行：用 happybrowser 快速驱动浏览器，供人工或 AI（shell 调用）使用。

    hb open https://example.com          # 打开页面（无 --new 则用当前标签页导航）
    hb open www.baidu.com                # 缺协议自动补：裸域名 https，localhost/IP http
    hb find "#kw"                        # DSL 定位（? 可省略），输出 @1/@2… 引用
    hb click "@1"                        # 按引用点击（也可直接 hb click "#kw"）
    hb input "@1" "关键词"               # 清空后输入
    hb text "#result"                    # 读文本；attr/html/js/shot 同理
    hb hl / hb hl "@1"                   # 高亮（无参=高亮 find 缓存的所有引用；nohl 取消）
    hb tabs / hb tab <id前缀>            # 标签页列表与切换
    hb explain "a && .btn"               # DSL 调试：AST / 执行策略 / 下推 XPath
    hb                                   # 不带参数：打印帮助后退出（退出码 0）

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
    ele = Element(ctx.tab, backend_id=ref['backend_id'])  # 三 id 懒互转，构造零开销
    try:
        _ = ele.tag  # 触发一次 describeNode，验证节点还活着
    except BaseError:
        return None, f'引用 {target} 已失效（页面大概跳转过），请重新 find'
    return ele, None


def _resolve(ctx, target, method):
    """@n 走状态引用复活，否则按 DSL 定位符查找。"""
    if target.startswith('@'):
        ele, err = _revive_ref(ctx, target)
        if err:
            _fail(err)
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
        if target.startswith('@'):
            ele, err = _revive_ref(ctx, target)
            if err:
                errors.append(err)
            else:
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

    args = ap.parse_args(argv)
    if getattr(args, 'fn', None) is None:  # hb / hb --json / hb --port 9223 …
        ap.print_help()
        return 0

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
