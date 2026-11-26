# -*- coding: utf-8 -*-
"""extension/（hb 录制器）集成测试：用真实 Chromium + hb CLI 验证「录制器生成的东西 hb 真能用」。

不加载扩展本身（无头 Chrome 里驱动 service worker / side panel 代价太高），而是把
extension/lib 下的三个纯 JS 模块（定位、录制核心、脚本生成）注入页面：

1. hbText 与 happybrowser 的 HB_TEXT_JS 逐字一致（两边文本语义同源）。
2. page.html 的每个元素都生成定位方案，所有 DSL 都能被 hb 解析，并且经 hb CLI
   find/attr 回到同一个元素（备选方案、“全部同类”一并验证）。
3. app.html 上用 hb 发真实鼠标/键盘事件录制一遍 → 生成 .hb → 重新打开页面回放，
   页面日志与录制时一致（含 iframe 链、父级锚点、密码隐藏、label→勾选框）。
4. 选取模式抓取内容：单条 / 全部同类（loop 块）。
5. 整套扩展管线：用 mock-chrome.js 在页面里把 content.js / background.js / sidepanel.js 原样跑起来
   （本机只有正式版 Chrome，137 起不再认 --load-extension，没法真加载扩展），走完整的消息协议录制并回放。

本机没有 Chromium 系浏览器时整模块跳过。临时实例 auto_port + headless，绝不触碰 9222。
"""
import http.server
import json
import re
import tempfile
import threading
import time
from pathlib import Path

import pytest

from happybrowser import Browser, Keys, Options
from happybrowser._core.keys import send_key
from happybrowser._functions.web import HB_TEXT_JS
from happybrowser._locate.query import parse_query

try:
    from happybrowser._core.launcher import get_chromium_path  # noqa: F401 探测浏览器可用性
    HAS_BROWSER = True
except Exception:
    HAS_BROWSER = False

pytestmark = pytest.mark.skipif(not HAS_BROWSER, reason='本机未找到 Chromium 系浏览器')

EXT = Path(__file__).resolve().parent.parent / 'extension'
FIXTURES = EXT / 'test' / 'fixtures'
LIBS = ('locator.js', 'recorder-core.js', 'hbgen.js')


def js(*names):
    return '\n'.join((EXT / 'lib' / name).read_text(encoding='utf-8') for name in names)


def dsls_of(plan):
    """方案里出现的全部 DSL 片段。"""
    out = [plan.get('dsl')]
    if isinstance(plan.get('anchor'), dict):
        out.append(plan['anchor'].get('dsl'))
    if isinstance(plan.get('all'), dict):
        out += [plan['all'].get('dsl'), plan['all'].get('rel')]
    return [d for d in out if d]


# ----------------------------------------------------------------夹具

@pytest.fixture(scope='module')
def server():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            route = self.path.split('?', 1)[0]
            if route.startswith('/__ext/'):                 # 扩展自己的文件（管线测试里把侧边栏页当 iframe 开）
                f = (EXT / route[len('/__ext/'):]).resolve()
                if EXT not in f.parents or not f.is_file():
                    self.send_response(404)
                    self.end_headers()
                    return
                body = f.read_bytes()
                if f.name == 'sidepanel.html':             # 先把角色标记和 chrome.* 模拟层塞进去
                    body = body.replace(b'<head>', b'<head><script>window.__hbrRole = "panel";</script>'
                                                 b'<script src="/__ext/test/mock-chrome.js"></script>', 1)
                ctype = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
                         '.css': 'text/css; charset=utf-8'}[f.suffix]
                self.send_response(200)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            name = route.lstrip('/') or 'app.html'
            f = FIXTURES / name
            body = f.read_bytes() if f.is_file() else b'not found'
            body = body.replace(b'__PORT__', str(self.server.server_address[1]).encode())
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{srv.server_address[1]}/'
    srv.shutdown()


@pytest.fixture(scope='module')
def browser():
    opts = Options()
    opts.auto_port = True
    opts.headless()
    b = Browser(opts)
    b.new_tab('about:blank')          # 留一个标签页：最后一个标签页关闭会连带退出浏览器
    port = int(b.address.split(':')[1])
    yield b
    try:
        b.quit()
    finally:
        (Path.home() / '.happybrowser' / f'state_{port}.json').unlink(missing_ok=True)


def run_hb(port, script):
    """子进程跑一段 .hb（hb --json -c：每条输出一行 JSON，失败也继续），返回 (退出码, stdout 行, stderr)。

    不在进程内调 cli.main：cli 连上的是本进程自己的 Browser 单例，断开时会把测试手里的连接一起停掉；
    也不走 -e 参数：脚本里的引号经 Windows 命令行往返容易被拆散，落盘再 hb run 最稳。
    """
    import subprocess
    import sys
    with tempfile.NamedTemporaryFile('w', suffix='.hb', encoding='utf-8', delete=False) as f:
        f.write(script)
        path = f.name
    try:
        r = subprocess.run([sys.executable, '-m', 'happybrowser.cli', '--port', str(port), '--json', '-c', path],
                           check=False, capture_output=True, text=True, encoding='utf-8', timeout=300)
    finally:
        Path(path).unlink(missing_ok=True)
    return r.returncode, r.stdout.splitlines(), r.stderr


def data_values(out):
    """--json 输出里 open/find 以外的数据行（text/attr/html）取值，保持顺序。"""
    vals = []
    for ln in out:
        obj = json.loads(ln)
        if isinstance(obj, dict) and any(k in obj for k in ('text', 'attr', 'html')):
            vals.append(obj.get('text', obj.get('value', obj.get('html'))))
    return vals


# ----------------------------------------------------------------1. hbText 同源

def _function_source(text, name):
    m = re.search(r'function ' + name + r'\(root\) \{.*?\n[ \t]*\}', text, re.DOTALL)
    assert m, '找不到 ' + name
    return '\n'.join(line.strip() for line in m.group(0).splitlines())


def test_hbtext_in_sync():
    """locator.js 里的 hbText 是 HB_TEXT_JS 的逐字拷贝：改了 Python 侧记得同步。"""
    assert _function_source((EXT / 'lib' / 'locator.js').read_text(encoding='utf-8'), 'hbText') == \
        _function_source(HB_TEXT_JS, 'hbText')


# ----------------------------------------------------------------2. 定位方案回到同一个元素

PLANS_JS = js('locator.js', 'hbgen.js') + r'''
return (() => {
  const L = HbLocator, G = HbScriptGen;
  const els = Array.from(document.querySelectorAll('body *'));
  const t0 = performance.now();
  const plans = els.map(el => { try { return L.best(el); } catch (e) { return {kind: 'none', reason: String(e)}; } });
  // 结果很大：序列化成字符串返回，省得 hb 逐个对象去 getProperties
  return JSON.stringify({ms: performance.now() - t0, rows: els.map((el, i) => ({i: i, desc: L.describe(el), plan: plans[i],
    verify: G.verifyLines(plans[i]), altVerify: (plans[i].alts || []).map(a => G.verifyLines(a))}))});
})()
'''

# 给每个元素盖编号后：JS 侧 resolve/resolveAll 的结果（应与随后 hb 的结果一致）+ “全部同类”的 find 行
STAMP_JS = r'''
return (() => {
  const L = HbLocator, G = HbScriptGen;
  const rows = %s;
  document.querySelectorAll('body *').forEach((el, i) => el.setAttribute('data-hbn', String(i)));
  return JSON.stringify(rows.map(row => {
    const r = L.resolve(row.plan);
    return {i: row.i, js: r ? r.getAttribute('data-hbn') : null,
            all: row.plan.all ? L.resolveAll(row.plan.all).map(el => el.getAttribute('data-hbn')) : null,
            allLines: row.plan.all ? G.refLines(row.plan, true) : null};
  }));
})()
'''


def test_plans_resolve_in_hb(browser, server):
    tab = browser.new_tab(server + 'page.html')
    try:
        tab.wait.doc_loaded()
        got = json.loads(tab.run_js(PLANS_JS))
        rows = got['rows']
        assert len(rows) > 100
        # shadow DOM：认得出并明说，而不是给一个回放时找不到的定位符
        shadow = tab.run_js(js('locator.js') +
                            ';return HbLocator.best(document.getElementById("w1").shadowRoot.querySelector("button"));')
        assert shadow['kind'] == 'none' and 'shadow' in shadow['reason']

        lines = ['#! hb --timeout 1']
        expect = []                       # 每条 attr 输出应有的 data-hbn
        for row in rows:
            plan = row['plan']
            assert plan['kind'] != 'none', row
            for dsl in dsls_of(plan) + [d for alt in plan.get('alts', []) for d in dsls_of(alt)]:
                parse_query(dsl)          # 生成的每个 DSL 都得是 hb 认的语法
            for verify in [row['verify']] + row['altVerify']:      # 选中的方案和备选方案都要能回到同一个元素
                lines.append('# element %d %s' % (row['i'], row['desc']))
                lines += verify
                expect.append(str(row['i']))

        stamped = json.loads(tab.run_js(js('locator.js', 'hbgen.js') + STAMP_JS %
                                        json.dumps([{'i': r['i'], 'plan': r['plan']} for r in rows])))
        for row in stamped:
            assert row['js'] == str(row['i']), row       # JS 侧的判断（录制时“唯一”的依据）与元素本身一致
        # “全部同类”：find --all 后逐个取，应恰好是 JS 算出的那一组
        for row, plan in zip(stamped, (r['plan'] for r in rows)):
            if not row['allLines']:
                continue
            lines += row['allLines']
            lines.append('loop %d:' % len(row['all']))
            rel = ' >> ' + plan['all']['rel'] if plan['all'].get('rel') else ''
            lines.append('    attr "@${i}%s" "data-hbn"' % rel)
            expect += row['all']
        lines = [ln if ln.startswith(('#', 'loop', '    ')) else ln + ' --json' for ln in lines]

        code, out, err = run_hb(int(browser.address.split(':')[1]), '\n'.join(lines))
        assert code == 0 and not err.strip(), err[:3000]
        assert data_values(out) == expect, [(a, b) for a, b in zip(data_values(out), expect) if a != b][:10]
    finally:
        tab.close()


# ----------------------------------------------------------------3. 录制 → 回放

# 注入到每个 frame：把 core 挂上，步骤经 appendStep 归并进共享的 top.__hbrSteps（跨域 frame 用自己的）
GLUE = r'''
window.__hbrSteps = (function () {
  try { return top.__hbrSteps || (top.__hbrSteps = []); } catch (e) { return window.__hbrSteps || (window.__hbrSteps = []); }
})();
window.__hbrPicking = false;
window.__hbrPick = null;
window.__hbrRec = HbRecorder.mount({
  win: window, doc: document,
  isRecording: () => true,
  isPicking: () => window.__hbrPicking,
  emit: step => HbRecorder.appendStep(window.__hbrSteps, Object.assign({framePath: HbRecorder.framePath(window)}, step)),
  locate: el => HbLocator.best(el),
  maskPasswords: () => true,
  onPick: result => { window.__hbrPick = result; },
});
'''

INJECT = js(*LIBS) + GLUE


def all_frames(tab):
    """所有 iframe（含嵌套、跨域）的 Frame 对象。

    注意 hb 页面级查找会把同进程 iframe 文档里的匹配一并返回（主文档在前、各 frame 深度优先在后），
    所以顶层 eles('iframe') 已经含嵌套的；这里按对象去重，并且给负查找一个短超时（默认要等满 10 秒）。
    """
    out, stack, seen = [], [tab], set()
    while stack:
        for fr in stack.pop(0).eles('iframe', timeout=0.3):
            if fr in seen:
                continue
            seen.add(fr)
            out.append(fr)
            stack.append(fr)
    return out


def record_steps(tab):
    steps = list(tab.run_js('return window.__hbrSteps'))
    for fr in all_frames(tab):
        own = fr.run_js('try { if (window.__hbrSteps === top.__hbrSteps) return null; } catch (e) {} return window.__hbrSteps;')
        if own:
            steps += own
    return sorted(steps, key=lambda s: s['t'])


def attach_frame_hosts(tab, steps):
    """模拟 background 的 iframe 链解析：逐层问父 frame “第 n 个子 frame 的宿主元素怎么定位”。"""
    for st in steps:
        path = st.get('framePath') or []
        hosts = []
        for i in range(len(path)):
            page = tab
            for idx in path[:i]:
                page = page.eles('iframe')[idx]
            r = json.loads(page.run_js('return JSON.stringify(window.__hbrRec.hostLocator(%d));' % path[i]))
            assert 'plan' in r, r
            hosts.append({'dsl': r['plan']['dsl'], 'index': r['plan'].get('index'), 'src': r['src']})
        st['plan']['frames'] = hosts


def generate(tab, steps, url):
    session = {'startUrl': url, 'startTitle': 'hb-recorder 回放测试', 'startedAt': 1760000000000, 'steps': steps}
    return tab.run_js(js(*LIBS) + ';return HbScriptGen.generate(arguments[0]);', session)


def test_record_then_replay(browser, server):
    url = server + 'app.html'
    tab = browser.new_tab(url)
    port = int(browser.address.split(':')[1])
    try:
        tab.wait.doc_loaded()
        tab.run_js(INJECT)
        for fr in all_frames(tab):
            fr.run_js(INJECT)
        e = tab.ele
        # —— 用 hb 发真实鼠标/键盘事件“操作”一遍 ——
        e('#q').input('hello world')
        e('#btn-search').ele('span && .icon').click()     # 点在图标上 → 提升到 button
        e('#q-form').input('abc')
        e('#q-form').input('\n')                          # 回车：记成 press，不混进输入值
        e('@data-id=2').ele('button && .del').click()     # 同类按钮 → 父级锚点 @data-id
        e('#sel').select.by_value('sh')
        e('#sel').select.by_value('7')                    # 纯数字 value → 按选项文字「七天」
        e('label && .chk').click()                        # 隐藏勾选框的 label
        e('#visible-check').click()                       # 直接点可见勾选框
        e('#pwd').input('s3cret!')
        e('#bio').input('line one')
        e('label && @for="r2"').click()                      # 单选框的 label → 记在 #r2 上
        e('#kbd').input(Keys.DOWN)
        send_key(tab, 2, 's')                             # ctrl+s（单次按键事件；input((CTRL, 's')) 会先多发一个裸 Control）
        e('#hash-link').click()
        e('#push-link').click()
        e('button && .go').click()                        # 第 1 个 go：行锚点按序号
        e('#pane').ele('#fa-btn').click()                 # 同域 iframe
        e('iframe && .second').ele('#fa-input').input('in frame')
        e('#pane').ele('iframe && .inner').ele('#fb-btn').click()       # 两层
        e('#pane').ele('iframe && .cross').ele('#fx-btn').click()       # 跨域（localhost）
        tab.run_js('document.activeElement && document.activeElement.blur(); return true;')
        e('body')._input_focus()
        send_key(tab, 0, Keys.ESCAPE)                     # 谁都没焦点：press 发给 body
        recorded_log = e('#log').text
        for page in [tab, *all_frames(tab)]:
            page.run_js('window.__hbrRec.flush(); return true;')

        steps = record_steps(tab)
        attach_frame_hosts(tab, steps)
        # 每个操作一步：回车是 press，label 代发的合成 click 不重复记，字段里的编辑键不记
        kinds = [s['kind'] for s in steps]
        assert kinds.count('click') == 11 and kinds.count('input') == 5, kinds
        assert kinds.count('press') == 4 and kinds.count('select') == 2, kinds
        assert set(kinds) == {'click', 'input', 'press', 'select'}
        assert [s['plan']['dsl'] for s in steps if s['kind'] == 'input'] == ['#q', '#q-form', '#pwd', '#bio', '#fa-input']

        by = {(s['kind'], s['desc']): s for s in steps}
        assert '提升' in by[('click', 'button#btn-search「搜索」')]['notes'][0]          # 点在 🔵 图标上
        assert 'label' in by[('click', 'input#r2「B 方案」')]['notes'][0]                # label → 关联控件
        deleted = by[('click', 'button「删除」')]['plan']
        assert deleted['kind'] == 'anchored' and deleted['anchor']['dsl'] == '@data-id="2"' and 'index' not in deleted['anchor']
        assert by[('click', 'input#r2「B 方案」')]['plan']['dsl'] == '#r2'
        assert by[('click', 'label「同意」')]['plan']['kind'] == 'flat'                   # 隐藏勾选框 → 记 label
        sels = [s for s in steps if s['kind'] == 'select']                            # 同一个 <select> 选了两次
        assert (sels[0]['by'], sels[0]['value'], sels[0]['optionText']) == ('value', 'sh', '上海')
        assert (sels[1]['by'], sels[1]['value'], sels[1]['optionValue']) == ('text', '七天', '7')   # 纯数字 value → 按文字
        assert [s['key'] for s in steps if s['kind'] == 'press'] == ['Enter', 'ArrowDown', 'ctrl+s', 'Escape']
        frames = {(s['desc'], tuple(s['framePath'])): s for s in steps if s.get('framePath')}
        assert {('button#fa-btn「A 按钮」', (0,)), ('input#fa-input「frame 输入」', (1,)),
                ('button#fb-btn「B 按钮」', (0, 0)), ('button#fx-btn「X 按钮」', (0, 1))} <= set(frames)

        script = generate(tab, steps, url)
        assert 's3cret' not in script and 'input @1 "********"' in script
        assert re.search(r"find '#data-id=\"2\"'|find '@data-id=\"2\"'", script) and 'click "@1 >> button && .del"' in script
        assert 'press @1 "Enter"' in script and 'select @1 "sh" --by "value"' in script and 'select @1 "七天"' in script
        # 同一元素上的“输入 → 回车”共用一个 find
        assert re.search(r'find "#q-form"[^\n]*\ninput @1 "abc"\npress @1 "Enter"', script), script
        assert re.search(r'find "body"[^\n]*\npress @1 "Escape"', script)
        assert re.search(r'find "tr && .row" --index 1[^\n]*\nclick "@1 >> button && .go"', script), script
        # iframe 链：外层 find 宿主元素，内层直接 find "@1 >> 目标" 把引用换成 frame 里的目标
        assert re.search(r'find "#pane"\nfind "@1 >> iframe && \.cross"\nfind "@1 >> #fx-btn"[^\n]*\nclick @1', script), script

        # —— 回放：重新打开页面跑脚本，日志应与录制时一致（密码按设计被隐藏）——
        expect = recorded_log.replace('pwd s3cret!', 'pwd ********')
        code, out, err = run_hb(port, script)
        assert code == 0 and not err.strip(), err[:3000]
        replayed = e('#log').text
        assert replayed == expect, '\n--- recorded ---\n%s\n--- replayed ---\n%s\n--- script ---\n%s' % (expect, replayed, script)
        assert e('#pane').ele('iframe && .cross').ele('#fx-out').text == 'x-clicked'      # 跨域 frame 里的按钮也点到了
    finally:
        tab.close()


# ----------------------------------------------------------------4. 选取模式抓取内容

PICK_JS = r'''
return (() => {
  const el = document.querySelector(%s);
  window.__hbrPicking = true;
  el.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
  el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
  window.__hbrPicking = false;
  const r = window.__hbrPick;
  window.__hbrPick = null;
  return r;
})()
'''


def test_pick_capture(browser, server):
    url = server + 'app.html'
    tab = browser.new_tab(url)
    port = int(browser.address.split(':')[1])
    try:
        tab.wait.doc_loaded()
        tab.run_js(INJECT)
        pick = lambda sel: tab.run_js(PICK_JS % json.dumps(sel))
        name, go, link, box = pick('#items .name'), pick('button.go'), pick('#hash-link'), pick('#q')
        # 唯一的元素：有“全部同类”是因为它有一组同款描述（span && .name × 3）
        assert name['plan']['kind'] == 'flat' and (name['plan']['all']['dsl'], name['plan']['all']['n']) == ('span && .name', 3)
        # 同类按钮：父级锚点按序号（第 n 行），“全部同类”= 全部行 + 行内按钮
        assert go['plan']['kind'] == 'anchored' and go['plan']['anchor']['index'] == 1
        assert (go['plan']['all']['dsl'], go['plan']['all']['rel'], go['plan']['all']['n']) == ('tr && .row', go['plan']['dsl'], 3)
        assert link['plan']['kind'] == 'flat' and 'all' not in link['plan'] and 'href' in link['attrs']
        assert box['plan']['dsl'] == '#q'

        def capture(picked, what, all_=False, **extra):
            cap = dict(what=what, **extra)
            if all_:
                cap['all'] = {'n': picked['plan']['all']['n']}
            return {'kind': 'capture', 'desc': picked['desc'], 'plan': picked['plan'], 'capture': cap, 't': 0}

        steps = [capture(name, 'text', True), capture(go, 'attr', True, attr='class'),
                 capture(link, 'attr', attr='href'), capture(box, 'html')]
        script = generate(tab, steps, url)
        assert ('find "span && .name" --all  # 1. 抓取全部同类（3 项）：span「苹果」\n'
                'loop 3:\n'
                '    text "@${i}"  # span「苹果」\n'
                'find "tr && .row" --all  # 2. 抓取全部同类（3 项）：button「go」\n'
                'loop 3:\n'
                '    attr "@${i} >> button && .go" "class"  # button「go」\n') in script, script
        assert re.search(r'find "#hash-link"[^\n]*\nattr @1 "href"', script) and re.search(r'find "#q"[^\n]*\nhtml @1', script)

        code, out, err = run_hb(port, script)
        assert code == 0 and not err.strip(), err[:3000]
        assert data_values(out) == ['苹果', '香蕉', '樱桃', 'go', 'go', 'go', url + '#/page2',                                     # attr href 取到的是绝对地址
                                    '<input id="q" name="q" type="search" placeholder="搜索关键词">'], data_values(out)
    finally:
        tab.close()


def test_recorder_unit_helpers(browser, server):
    """纯函数分支：按键名归一化（AltGr、Shift 符号）、framePath、appendStep 归并。"""
    tab = browser.new_tab(server + 'app.html')
    try:
        r = tab.run_js(js('locator.js', 'recorder-core.js') + r'''
const mk = (key, init) => HbRecorder.keyName(new KeyboardEvent('keydown', Object.assign({key}, init)));
const plan = dsl => ({kind: 'flat', dsl});
let steps = [];
const push = s => { steps = HbRecorder.appendStep(steps, s); return steps.length; };
const n1 = push({kind: 'click', plan: plan('#a'), t: 30});
const n2 = push({kind: 'input', plan: plan('#a'), value: 'ab', t: 40});
const n3 = push({kind: 'input', plan: plan('#a'), value: 'abc', t: 50});   // 同一字段接着输入 → 替换
const n4 = push({kind: 'input', plan: plan('#f'), value: 'x', t: 20, framePath: [1]});   // 跨 frame 晚到 → 插到最前
const n5 = push({kind: 'click', plan: plan('#b'), t: 60});
const n6 = push({kind: 'click', plan: plan('#b'), t: 61, replaceLast: true, count: 2});  // 双击第二下 → 并入
return {framePath: HbRecorder.framePath(window), plain: mk('Enter', {}), esc: mk('Escape', {}),
        shiftTab: mk('Tab', {shiftKey: true}), ctrlA: mk('a', {ctrlKey: true}), metaShiftK: mk('K', {metaKey: true, shiftKey: true}),
        altGr: mk('€', {ctrlKey: true, altKey: true}), question: mk('?', {shiftKey: true}),
        modifierOnly: mk('Shift', {shiftKey: true}), dead: mk('Dead', {}),
        host0: HbRecorder.hostLocator(window, document, 0).plan.dsl, hostMissing: HbRecorder.hostLocator(window, document, 9).error,
        order: [n1, n2, n3, n4, n5, n6], steps: steps.map(s => [s.kind, s.plan.dsl, s.value || s.count || ''])};
''')
        assert r['framePath'] == [] and r['host0'] == '#pane' and '找不到' in r['hostMissing']
        assert (r['plain'], r['esc'], r['shiftTab'], r['ctrlA'], r['metaShiftK']) == \
            ('Enter', 'Escape', 'shift+Tab', 'ctrl+a', 'meta+shift+k')
        assert (r['altGr'], r['question'], r['modifierOnly'], r['dead']) == ('€', '?', None, None)
        assert r['order'] == [1, 2, 2, 3, 4, 4]
        assert r['steps'] == [['input', '#f', 'x'], ['click', '#a', ''], ['input', '#a', 'abc'], ['click', '#b', 2]]
    finally:
        tab.close()


# ----------------------------------------------------------------5. 整套扩展管线（chrome.* 模拟层）
# 本机只有正式版 Chrome（137 起不再认 --load-extension），没法真把扩展加载进来；
# 退而求其次：用 test/mock-chrome.js 把 content.js / background.js / sidepanel.js 原样跑在页面里，
# 走完整的消息协议（hello 登记、iframe 链解析、停止前 flush、跳转事件、侧边栏编辑 / 保存）。

MOCK = (EXT / 'test' / 'mock-chrome.js').read_text(encoding='utf-8')
CONTENT = (EXT / 'content.js').read_text(encoding='utf-8')
BACKGROUND = (EXT / 'background.js').read_text(encoding='utf-8')

# 顶层页面里塞两个辅助 iframe：service worker 与侧边栏各住一个（真实环境里是扩展自己的上下文）。
# 侧边栏要等 service worker 起来再开：真实环境里侧边栏一打开就能问到状态。
HOST_BG_JS = r'''
const bg = document.createElement('iframe');
bg.id = '__hbr_bg'; bg.style.display = 'none';
document.documentElement.appendChild(bg);
return true;
'''
HOST_PANEL_JS = r'''
const panel = document.createElement('iframe');
panel.id = '__hbr_panel'; panel.style.cssText = 'position:fixed;left:-9999px;top:0;width:420px;height:700px';
panel.src = '/__ext/sidepanel.html';
document.documentElement.appendChild(panel);
return true;
'''


def wait_for(fn, what, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = fn()
        if r:
            return r
        time.sleep(0.1)
    raise AssertionError('等待超时：' + what)


def panel_session(panel):
    return json.loads(panel.run_js('return JSON.stringify(session);'))


def test_extension_pipeline(browser, server):
    url = server + 'app.html'
    tab = browser.new_tab(url)
    port = int(browser.address.split(':')[1])
    try:
        tab.wait.doc_loaded()
        tab.run_js(MOCK)                                  # 总线由顶层持有：辅助 iframe 与各 frame 都要能摸到它
        tab.run_js(HOST_BG_JS)
        bg = tab.ele('#__hbr_bg')
        libs = js(*LIBS)
        # 先起 service worker，再装各 frame 的内容脚本（它们一加载就 hello 登记），最后开侧边栏
        assert bg.run_js('window.__hbrRole = "background";' + MOCK + libs + BACKGROUND + ';return typeof startRecording;') == 'function'
        tab.run_js(libs + CONTENT)
        frames = []
        for fr in all_frames(tab):
            href = fr.run_js('return location.href;')
            if href.startswith('about:') or '/__ext/' in href:
                continue                               # 辅助 iframe 不装内容脚本
            fr.run_js(MOCK + libs + CONTENT)
            frames.append(fr)
        assert len(frames) == 6
        # 顶层 + 6 个真 frame + 2 个辅助 = 9（跨域 frame 靠 postMessage 登记，要等一下）
        wait_for(lambda: tab.run_js('return window.__hbrBus.frames.size') >= 8, '各 frame 向 service worker 登记')
        tab.run_js(HOST_PANEL_JS)
        panel = tab.ele('#__hbr_panel')

        def status():
            try:
                return panel.run_js("return document.getElementById('status').textContent")
            except Exception:                      # 侧边栏页还在加载
                return ''
        wait_for(lambda: '未录制' in status(), '侧边栏就绪')
        panel.run_js("document.getElementById('btn-record').click(); return true;")
        wait_for(lambda: tab.run_js("return !!document.getElementById('hbr-pill')"), '页面角标出现')
        wait_for(lambda: '录制中' in status(), '侧边栏显示录制中')

        pill_count = lambda: tab.run_js("return (document.getElementById('hbr-count') || {}).textContent || ''")

        def wait_steps(n):
            wait_for(lambda: ('%d 步' % n) in pill_count(), '角标计数到 %d 步（实际：%s）' % (n, pill_count()))

        def fire_nav(transition, target, n):
            # 跳转事件在 background 里是攒 250 ms 再落账的：等它反映到角标上再往下走
            tab.run_js("window.__hbrBus.events['webNavigation.onCommitted'].listeners.slice().forEach("
                       "f => f({tabId: 1000, frameId: 0, url: %s, transitionType: %s, transitionQualifiers: []})); return true;"
                       % (json.dumps(target), json.dumps(transition)))
            wait_steps(n)

        e = tab.ele
        fire_nav('typed', url + '?manual=1', 1)              # 地址栏输入 → open
        e('#q').input('via panel')
        e('#btn-search').click()
        wait_steps(3)
        e('#hash-link').click()
        wait_steps(4)
        fire_nav('link', url + '?manual=1#/page2', 5)        # 点击引起的跳转 → 只记注释
        e('#pane').ele('#fa-btn').click()
        wait_steps(6)
        e('button && .go').click()
        recorded_log = e('#log').text
        wait_steps(7)
        tab.ele('#hbr-pill').ele('button').click()           # 页面角标上的停止按钮
        wait_for(lambda: not tab.run_js("return !!document.getElementById('hbr-pill')"), '停止后角标消失')
        wait_for(lambda: '已停止' in status(), '侧边栏显示已停止')

        sess = panel_session(panel)
        assert [s['kind'] for s in sess['steps']] == ['nav', 'input', 'click', 'click', 'nav', 'click', 'click']
        assert (sess['steps'][0]['mode'], sess['steps'][4]['mode']) == ('open', 'comment')
        frame_step = sess['steps'][5]
        assert frame_step['framePath'] == [0] and [f['dsl'] for f in frame_step['plan']['frames']] == ['#pane']   # 链由 service worker 解析
        assert tab.run_js('return window.__hbrBus.storage.session.hbr.sessions[1000].steps.length') == 7        # 落了盘

        # 停了再点开始 = 续录，不丢已有步骤
        panel.run_js("document.getElementById('btn-record').click(); return true;")
        wait_for(lambda: '录制中' in status(), '续录后显示录制中')
        assert len(panel_session(panel)['steps']) == 7
        panel.run_js("document.getElementById('btn-record').click(); return true;")
        wait_for(lambda: '已停止' in status(), '再次停止')

        # —— 抓取：选取模式下的点击被接管，不会触发页面自己的处理函数 ——
        panel.run_js("document.getElementById('btn-pick').click(); return true;")
        wait_for(lambda: tab.run_js("return document.documentElement.classList.contains('hbr-picking')"), '进入选取模式')
        e('button && .go').click()
        wait_for(lambda: panel.run_js("return !document.getElementById('pick-draft').hidden"), '侧边栏收到选取结果')
        assert 'go 2' not in e('#log').text
        panel.run_js("document.querySelector('input[name=pwhat][value=attr]').checked = true;"
                     "document.querySelector('input[name=pscope][value=all]').checked = true;"
                     "document.getElementById('pick-attr').value = 'class';"
                     "document.getElementById('pick-add').click(); return true;")
        sess = panel_session(panel)
        assert sess['steps'][-1]['capture'] == {'what': 'attr', 'attr': 'class', 'all': {'n': 3}}

        # —— 编辑：换定位方案 / 删除 / 加说明和等待 / 改内容 ——
        go = 6
        assert panel.run_js("document.querySelectorAll('.step')[%d].click(); "
                            "return document.querySelectorAll('#editor .alts button').length;" % go) >= 1
        panel.run_js("document.querySelector('#editor .alts button').click(); return true;")
        sess = panel_session(panel)
        assert sess['steps'][go]['plan']['kind'] == 'index' and sess['steps'][go]['plan']['dsl'] == 'button && .go'
        panel.run_js("document.querySelectorAll('.step')[0].querySelector('.x').click(); return true;")     # 删掉手输地址那步
        panel.run_js("document.getElementById('btn-note').click(); return true;")
        panel.run_js("document.getElementById('btn-sleep').click(); return true;")
        sess = panel_session(panel)
        assert [s['kind'] for s in sess['steps'][-2:]] == ['note', 'sleep'] and sess['steps'][0]['kind'] == 'input'
        panel.run_js("document.querySelectorAll('.step')[%d].click(); const ta = document.querySelector('#editor textarea'); "
                     "ta.value = '登录流程'; ta.dispatchEvent(new Event('change')); return true;" % (len(sess['steps']) - 2))
        panel.run_js("document.querySelectorAll('.step')[%d].click(); const n = document.querySelector('#editor input[type=number]'); "
                     "n.value = '2'; n.dispatchEvent(new Event('change')); return true;" % (len(sess['steps']) - 1))
        sess = panel_session(panel)
        assert (sess['steps'][-2]['text'], sess['steps'][-1]['seconds']) == ('登录流程', 2)

        script = panel.run_js("return document.getElementById('script').textContent")
        assert 'open "' in script and 'find "#pane"' in script and 'find "button && .go" --index 1' in script
        assert 'loop 3:' in script and 'attr "@${i} >> button && .go" "class"' in script
        assert '# 登录流程' in script and 'sleep 2' in script and '?manual=1' not in script
        panel.run_js("navigator.clipboard.writeText = async t => { window.top.__hbrBus.copied = t; };"
                     "document.getElementById('btn-copy').click(); document.getElementById('btn-save').click(); return true;")
        wait_for(lambda: tab.run_js('return window.__hbrBus.downloads.length'), '保存 .hb')
        saved = tab.run_js('return window.__hbrBus.downloads[0]')
        assert saved['filename'].endswith('.hb') and saved['saveAs'] is True and saved['text'] == script
        assert tab.run_js('return window.__hbrBus.copied') == script

        # —— 回放：全新页面（没有任何模拟层）跑侧边栏保存下来的脚本 ——
        fresh = browser.new_tab(url)
        code, out, err = run_hb(port, script)
        assert code == 0 and not err.strip(), err[:3000] + '\n' + script
        assert fresh.ele('#log').text == recorded_log, '\n--- recorded ---\n%s\n--- replayed ---\n%s' % (recorded_log, fresh.ele('#log').text)
        assert data_values(out) == ['go', 'go', 'go']
        fresh.close()
    finally:
        tab.close()
