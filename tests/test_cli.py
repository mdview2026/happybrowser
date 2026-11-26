# -*- coding: utf-8 -*-
"""hb CLI 集成测试：subprocess 调 .venv 里的 hb.exe，随机端口 headless 实例，绝不触碰 9222。
覆盖：无参显示帮助/open/find(@n 引用)/input/click/text/attr/js/shot/--json 前后位置/引用失效/explain。"""
import http.server
import json
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from happybrowser import Browser, Options

try:
    from happybrowser._core.launcher import get_chromium_path  # noqa: F401 探测浏览器可用性
    HAS_BROWSER = True
except Exception:
    HAS_BROWSER = False

pytestmark = pytest.mark.skipif(not HAS_BROWSER, reason='本机未找到 Chromium 系浏览器')

HTML = '''<!DOCTYPE html><html><head><title>hb-cli 测试页</title></head><body>
<input id="q" type="search" placeholder="输入">
<button id="go" onclick="document.getElementById('out').textContent='你好 '+document.getElementById('q').value">提交</button>
<p id="out" data-flag="1"></p>
<a class="lnk" href="#a">A</a><a class="lnk" href="#b">B</a>
<div class="card" id="c1"><h3>Card A</h3><button class="buy">buy-A</button></div>
<div class="card" id="c2"><h3>Card B</h3><button class="buy">buy-B</button></div>
<div id="wrap"><ul id="list">
<li class="row" id="r1">1</li><li class="row" id="r2">2</li><li class="row" id="r3">3</li>
</ul></div>
<form id="f2" onsubmit="document.getElementById('out').textContent='submitted '+document.getElementById('q2').value;return false">
<input id="q2" name="kw2" placeholder="Second">
</form>
<select id="sel" name="city" onchange="document.getElementById('out').textContent='city '+this.value">
<option value="bj">北京</option><option value="sh">上海</option>
</select>
<div id="keys" tabindex="0" onkeydown="if(event.key==='ArrowDown')document.getElementById('out').textContent='moved'+(event.ctrlKey?' ctrl':'')"></div>
<iframe id="frm" src="frame.html" title="inner"></iframe>
<iframe id="xfrm" src="http://localhost:__PORT__/x.html" title="cross"></iframe>
</body></html>'''

FRAME_HTML = '''<!DOCTYPE html><html><body style="margin:0">
<button id="in-btn" onclick="parent.document.getElementById('out').textContent='frame click'">inner</button>
<iframe id="frm2" src="frame2.html" style="width:300px;height:80px"></iframe>
</body></html>'''

FRAME2_HTML = '<button id="in-btn2" onclick="top.document.getElementById(\'out\').textContent=\'frame2 click\'">deep</button>'
X_HTML = '<button id="x-btn" onclick="document.getElementById(\'x-out\').textContent=\'x clicked\'">x</button><p id="x-out"></p>'


@pytest.fixture(scope='module')
def server():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            route = self.path.split('?', 1)[0]
            text = {'/frame.html': FRAME_HTML, '/frame2.html': FRAME2_HTML, '/x.html': X_HTML}.get(route, HTML)
            body = text.replace('__PORT__', str(self.server.server_address[1])).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{srv.server_address[1]}/index.html'
    srv.shutdown()


@pytest.fixture(scope='module')
def port(server):
    opts = Options()
    opts.auto_port = True
    opts.headless()
    b = Browser(opts)
    b.new_tab(server)  # 保持 ≥1 个标签页
    p = int(b.address.split(':')[1])
    yield p
    try:
        b.quit()
    finally:
        f = Path.home() / '.happybrowser' / f'state_{p}.json'
        f.unlink(missing_ok=True)


def hb(port, *args, expect_fail=False):
    exe = Path(sys.executable).parent / 'hb.exe'
    r = subprocess.run([str(exe), '--port', str(port), *args], check=False,
                       capture_output=True, text=True, encoding='utf-8', timeout=60)
    if expect_fail:
        assert r.returncode != 0, f'{args} 应失败却成功：{r.stdout}'
    else:
        assert r.returncode == 0, f'{args} 失败：{r.stderr}'
    return r.stdout.strip()


def test_full_workflow(port, server):
    # open → find(--json 放子命令后) → 引用点击 → 定位符直连 input/click → text
    out = hb(port, 'open', server)
    assert 'hb-cli 测试页' in out
    refs = json.loads(hb(port, 'find', 'a && .lnk', '--json'))
    assert [r['ref'] for r in refs] == ['@1', '@2'] and refs[0]['text'] == 'A'
    hb(port, 'click', '@1')
    hb(port, 'input', '#q', '世界')
    hb(port, 'click', '#go')
    assert '你好 世界' in hb(port, 'text', '#out')
    assert hb(port, 'attr', '#q', 'type') == 'search'
    assert 'hb-cli 测试页' in hb(port, 'js', 'return document.title')


def test_ref_invalidation_on_navigation(port, server):
    hb(port, 'open', server)
    hb(port, 'find', '#q', '--json')
    hb(port, 'open', server + '?next=1')
    hb(port, 'text', '@1', expect_fail=True)  # 跳转后引用缓存被清，明确报错


def test_shot(port, server, tmp_path):
    hb(port, 'open', server)
    p = tmp_path / 's.png'
    out = hb(port, 'shot', str(p))
    assert str(p) in out and p.is_file() and p.read_bytes()[:8] == b'\x89PNG\r\n\x1a\n'


def js(port, code):
    return json.loads(hb(port, 'js', code, '--json'))['result']


def outline(port, sel, i=0):
    return js(port, f'return document.querySelectorAll("{sel}")[{i}].style.outline')


def test_hl_and_nohl(port, server):
    hb(port, 'open', server)
    hb(port, 'find', 'a && .lnk', '--json')
    # 无目标 → 高亮 find 缓存的全部引用
    out = json.loads(hb(port, 'hl', '--json'))
    assert out['count'] == 2 and [r['target'] for r in out['highlighted']] == ['@1', '@2']
    assert 'red' in outline(port, 'a')
    # 重复高亮不覆盖原状备份（颜色可换）
    hb(port, 'hl', '@1', '--color', 'blue')
    assert 'blue' in outline(port, 'a')
    # 单个取消：@1 还原，@2 仍亮着
    hb(port, 'nohl', '@1')
    assert outline(port, 'a') == ''
    assert 'red' in outline(port, 'a', 1)
    # 无目标取消：清光本页剩余高亮（即使 find 缓存已换代）
    hb(port, 'find', '#q')
    assert json.loads(hb(port, 'nohl', '--json'))['cleared'] == 1
    assert outline(port, 'a', 1) == ''
    assert json.loads(hb(port, 'nohl', '--json'))['cleared'] == 0  # 再清一次是空操作


def test_hl_restores_original_outline(port, server):
    hb(port, 'open', server)
    probe = ('const s = document.querySelector("#q").style; '
             'return [s.outlineWidth, s.outlineStyle, s.outlineColor].join("|")')
    js(port, 'document.querySelector("#q").style.outline = "1px solid green";')
    before = js(port, probe)
    hb(port, 'hl', '#q', '--color', 'blue')
    assert js(port, probe) == '2px|solid|blue'
    hb(port, 'nohl', '#q')
    assert js(port, probe) == before  # 还原到页面原有的内联 outline


def test_hl_all_and_errors(port, server):
    hb(port, 'open', server)
    out = json.loads(hb(port, 'hl', 'a', '--all', '--json'))
    assert out['count'] == 2 and out['highlighted'][1]['target'] == 'a#2'
    hb(port, 'nohl')  # 不带 --all 的定位符只取首个
    assert json.loads(hb(port, 'hl', 'a', '--json'))['count'] == 1
    hb(port, 'nohl')
    hb(port, 'hl', '#nope', expect_fail=True)  # 未找到 → 错误退出
    assert json.loads(hb(port, 'nohl', '--json'))['cleared'] == 0  # 无高亮时不报错


def test_no_args_shows_help():
    exe = Path(sys.executable).parent / 'hb.exe'
    r = subprocess.run([str(exe)], check=False, capture_output=True, text=True,
                       encoding='utf-8', timeout=60)
    assert r.returncode == 0, r.stderr
    assert 'usage: hb' in r.stdout and 'explain' in r.stdout and r.stderr == ''


def test_explain_without_browser():
    exe = Path(sys.executable).parent / 'hb.exe'
    r = subprocess.run([str(exe), 'explain', '#q && @placeholder'], check=False,
                       capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert r.returncode == 0
    assert 'pure-xpath' in r.stdout and '@placeholder' in r.stdout


# ----------------------------------------------------------------press / select / iframe 引用链

def test_press(port, server):
    """press 发真实按键事件：Enter 触发表单隐式提交，修饰键组合能到页面监听。"""
    hb(port, 'open', server)
    hb(port, 'find', '#q2')
    hb(port, 'input', '@1', 'abc')
    hb(port, 'press', '@1', 'Enter')                 # 表单只有一个文本框 → 隐式提交 → onsubmit
    assert hb(port, 'text', '#out') == 'submitted abc'
    hb(port, 'press', '#keys', 'ArrowDown')          # 定位符直连；页面 keydown 监听收到方向键
    assert hb(port, 'text', '#out') == 'moved'
    hb(port, 'press', '#keys', 'ctrl+ArrowDown')
    assert hb(port, 'text', '#out') == 'moved ctrl'
    assert json.loads(hb(port, 'press', 'body', 'Tab', '--json')) == {'pressed': 'Tab', 'target': 'body'}
    hb(port, 'press', '#keys', 'ctrlx+a', expect_fail=True)   # 未知修饰键
    hb(port, 'select', '#q2', '上海', expect_fail=True)       # select 只能用于 <select>


def test_select(port, server):
    hb(port, 'open', server)
    hb(port, 'find', '#sel')
    assert hb(port, 'select', '@1', '上海') == '上海'         # 默认按选项文本
    assert hb(port, 'text', '#out') == 'city sh'
    hb(port, 'select', '#sel', '1', '--by', 'index')          # 序号 1 起
    assert hb(port, 'text', '#out') == 'city bj'
    hb(port, 'select', '#sel', 'sh', '--by', 'value')
    assert hb(port, 'text', '#out') == 'city sh'
    hb(port, '--timeout', '1', 'select', '#sel', '不存在', expect_fail=True)   # 找不到选项要等满超时


def test_ref_chain_enters_iframes(port, server):
    """@1 >> … 能进 iframe：同域、嵌套同域、以及跨域（OOPIF，localhost ≠ 127.0.0.1）。"""
    hb(port, 'open', server)
    tab = Browser.attach(port).latest_tab
    tab.ele('#frm').wait.doc_loaded()
    tab.ele('#xfrm').wait.doc_loaded()
    hb(port, 'find', '#frm')
    hb(port, 'click', '@1 >> #in-btn')
    assert hb(port, 'text', '#out') == 'frame click'
    hb(port, 'find', '#frm')
    hb(port, 'find', '@1 >> #frm2')                  # 进一层再找里层的 iframe
    hb(port, 'click', '@1 >> #in-btn2')
    assert hb(port, 'text', '#out') == 'frame2 click'
    hb(port, 'find', '#xfrm')
    hb(port, 'click', '@1 >> #x-btn')                # 跨域 frame
    assert hb(port, 'find', '@1 >> #x-out') and hb(port, 'text', '@1') == 'x clicked'


def test_ref_chain_enters_oopif():
    """真·跨进程 iframe（跨站 + 开着站点隔离）：引用复活后包装成 Frame，@1 >> … 走 frame 自己的 target。

    hb 默认启动参数关了站点隔离，所以这里单独起一只不关的；127.0.0.2 与 127.0.0.1 是不同的站。
    """
    top = '<iframe id="x" style="width:200px;height:60px" src="http://127.0.0.2:__PORT__/x.html"></iframe>'
    x = '<button id="x-btn" onclick="document.getElementById(\'o\').textContent=\'oopif-ok\'">x</button><p id="o"></p>'

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = (top if self.path.startswith('/top') else x).replace('__PORT__', str(self.server.server_address[1])).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(('0.0.0.0', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{srv.server_address[1]}/top.html'
    opts = Options()
    opts.auto_port = True
    opts.set_argument('--headless=new')
    opts.set_argument('--no-sandbox')
    opts.remove_argument('--disable-site-isolation-trials')
    b = Browser(opts)
    b.new_tab('about:blank')
    port = int(b.address.split(':')[1])
    try:
        hb(port, 'open', url)
        assert b.latest_tab.ele('#x')._is_diff_domain is True      # 确认真的是跨进程 frame
        hb(port, 'find', '#x')
        hb(port, 'click', '@1 >> #x-btn')
        hb(port, 'find', '@1 >> #o')
        assert hb(port, 'text', '@1') == 'oopif-ok'
    finally:
        try:
            b.quit()          # 进程里持有跨进程 Frame 对象时也要能干净退出（Frame._stop_messenger 不再打断 quit）
        finally:
            (Path.home() / '.happybrowser' / f'state_{port}.json').unlink(missing_ok=True)
            srv.shutdown()


# ----------------------------------------------------------------.hb 脚本

def hb_raw(*args, stdin=None):
    exe = Path(sys.executable).parent / 'hb.exe'
    return subprocess.run([str(exe), *map(str, args)], input=stdin, check=False,
                          capture_output=True, text=True, encoding='utf-8', timeout=120)


def test_run_script(port, server, tmp_path):
    """一个进程跑完全部行：open→find→let→input @n→click→text→echo，引用在脚本内共享。"""
    p = tmp_path / 'flow.hb'
    p.write_text(
        f'#! hb --port {port}\n'
        f'open {server}\n'
        'find "#q"\n'
        'let kw = "世界"\n'
        'input @1 "${kw}"\n'
        'click "#go"\n'
        'text "#out"\n'
        'echo done\n', encoding='utf-8')
    r = hb_raw('run', p)
    assert r.returncode == 0, r.stderr
    assert "已输入 → '世界'" in r.stdout
    assert '你好 世界' in r.stdout
    assert r.stdout.strip().endswith('done')


def test_run_script_shorthand(port, server, tmp_path):
    """hb xxx.hb 等价 hb run xxx.hb；行内 --json 只影响本行。"""
    p = tmp_path / 'q.hb'
    p.write_text(f'open {server}\nfind "a && .lnk" --json\n', encoding='utf-8')
    r = hb_raw('--port', port, p)
    assert r.returncode == 0, r.stderr
    rows = [json.loads(line) for line in r.stdout.splitlines() if line.startswith('[')]
    assert rows and [x['ref'] for x in rows[0]] == ['@1', '@2']


def test_run_script_stops_on_error(port, server, tmp_path):
    p = tmp_path / 'bad.hb'
    p.write_text(f'#! --timeout 0.2\nopen {server}\ntext "#nope"\necho after\n', encoding='utf-8')
    r = hb_raw('--port', port, p)
    assert r.returncode != 0 and 'after' not in r.stdout
    assert '第 3 行' in r.stderr
    r = hb_raw('--port', port, p, '-c')  # --continue：继续跑完，结尾汇总
    assert r.returncode != 0 and 'after' in r.stdout
    assert '1 条命令失败' in r.stderr


def test_eval_code_argument(port, server):
    """hb -e "<多行代码>"：不落盘直接执行，整个流程一个进程。"""
    code = '\n'.join([
        f'open {server}',
        'find "#q"',
        'let kw = "世界"',
        'input @1 "${kw}"',
        'click "#go"',
        'text "#out"',
    ])
    r = hb_raw('--port', port, '-e', code)
    assert r.returncode == 0, r.stderr
    assert '你好 世界' in r.stdout


def test_eval_repeated_segments(port, server):
    """可重复 -e 依次拼接（cmd.exe 不方便传换行时的退化写法）；代码里的 .hb 不当文件。"""
    r = hb_raw('--port', port,
               '-e', f'open {server}', '-e', 'find "#q"',
               '-e', 'attr @1 "id"', '-e', 'echo see flow.hb')
    assert r.returncode == 0, r.stderr
    lines = [ln.strip() for ln in r.stdout.splitlines()]
    assert 'q' in lines and lines[-1] == 'see flow.hb'


def test_eval_json(port, server):
    r = hb_raw('--port', port, '--json', '-e', f'open {server}\nfind "#q"')
    assert r.returncode == 0, r.stderr
    opened, found = [json.loads(ln) for ln in r.stdout.strip().splitlines()]
    assert opened['url'].startswith('http') and found[0]['tag'] == 'input'


def test_eval_rejects_subcommand(port, server):
    r = hb_raw('--port', port, '-e', f'open {server}', 'url')
    assert r.returncode != 0 and '-e 不能和子命令' in r.stderr


def test_eval_syntax_error_needs_no_browser():
    r = hb_raw('-e', 'input @1 "未闭合')
    assert r.returncode != 0 and '<-e>' in r.stderr and '第 1 行' in r.stderr


# ----------------------------------------------------------------无头模式

def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def kill_browser(port):
    try:
        Browser.attach(port).quit()   # 只接管，端口没浏览器时不会误启一只
    except Exception:
        pass
    (Path.home() / '.happybrowser' / f'state_{port}.json').unlink(missing_ok=True)


def test_cli_headless_launch(server):
    """--headless 在空端口上启动的实例必须真的是无头（整套参数生效 + 状态记住）。"""
    from happybrowser._core.options import HEADLESS_UA
    port = free_port()
    state = Path.home() / '.happybrowser' / f'state_{port}.json'
    try:
        r = hb_raw('--port', port, '--headless', 'open', server)
        assert r.returncode == 0, r.stderr
        assert json.loads(state.read_text(encoding='utf-8'))['headless'] is True
        ua = json.loads(hb_raw('--port', port, '--json', 'js', 'return navigator.userAgent').stdout)['result']
        assert ua == HEADLESS_UA   # Options.headless() 的参数集（含 --headless=new）已应用
        # 后续命令不必重复 --headless，能稳稳接回同一个无头实例
        assert hb_raw('--port', port, 'text', '#q').returncode == 0
    finally:
        kill_browser(port)


def test_cli_headless_switches_when_port_headed(port, server):
    """端口上是有头实例时，--headless 自动换端口起无头并把选择记进 state。"""
    state = Path.home() / '.happybrowser' / f'state_{port}.json'
    saved = state.read_text(encoding='utf-8') if state.exists() else None
    new_port = None
    try:
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({'headless': False, 'browser_id': 'stale'}), encoding='utf-8')
        r = hb_raw('--port', port, '--headless', 'open', server)
        assert r.returncode == 0, r.stderr
        assert '改用' in r.stderr
        new_port = json.loads(state.read_text(encoding='utf-8'))['headless_port']
        assert new_port != port
        # 第二次 --headless 直接复用记住的无头端口，不再切换/提示
        r2 = hb_raw('--port', port, '--headless', 'url')
        assert r2.returncode == 0, r2.stderr
        assert '改用' not in r2.stderr and server in r2.stdout
        new_state = json.loads((Path.home() / '.happybrowser' / f'state_{new_port}.json')
                               .read_text(encoding='utf-8'))
        assert new_state['headless'] is True and new_state['headless_port'] == new_port
    finally:
        if new_port:
            kill_browser(new_port)
        if saved is None:
            state.unlink(missing_ok=True)
        else:
            state.write_text(saved, encoding='utf-8')


def test_cli_headless_existing_never_launches(port, server):
    """--existing --headless：确认不了无头就不接管，更不会启动。"""
    state = Path.home() / '.happybrowser' / f'state_{port}.json'
    saved = state.read_text(encoding='utf-8') if state.exists() else None
    try:
        state.unlink(missing_ok=True)   # fixture 的 UA 被改写，清掉状态后无法确认无头
        r = hb_raw('--port', port, '--headless', '--existing', 'url')
        assert r.returncode != 0
        assert '只接管' in r.stderr
    finally:
        if saved is not None:
            state.write_text(saved, encoding='utf-8')


def test_hbh_bat_launches_headless():
    """仓库根 hbh.bat == hb --headless：透传参数并以无头模式启动。"""
    root = Path(__file__).resolve().parent.parent
    assert (root / 'hbh.bat').is_file()
    port = free_port()
    try:
        r = subprocess.run(['cmd', '/c', 'hbh.bat', '--port', str(port), 'open', 'about:blank'],
                           check=False, cwd=root, capture_output=True, text=True,
                           encoding='utf-8', timeout=120)
        assert r.returncode == 0, r.stderr
        state = Path.home() / '.happybrowser' / f'state_{port}.json'
        assert json.loads(state.read_text(encoding='utf-8'))['headless'] is True
    finally:
        kill_browser(port)


def test_hbh_bat_propagates_exit_code():
    """脚本失败必须从 bat 透传出非 0（否则批量测试会全部误报通过）。"""
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run(['cmd', '/c', 'hbh.bat', '-e', 'input @1 "未闭合'],
                       check=False, cwd=root, capture_output=True, text=True,
                       encoding='utf-8', timeout=120)
    assert r.returncode == 1, r.stderr
    assert '第 1 行' in r.stderr


def test_script_header_headless(server, tmp_path):
    """#! --headless 在脚本模式同样生效。"""
    from happybrowser._core.options import HEADLESS_UA
    port = free_port()
    script = tmp_path / 'headless.hb'
    script.write_text(f'#! --port {port} --headless\n'
                      f'open {server}\n'
                      'js "return navigator.userAgent"\n', encoding='utf-8')
    try:
        r = hb_raw('run', script)
        assert r.returncode == 0, r.stderr
        assert HEADLESS_UA in r.stdout
    finally:
        kill_browser(port)


def test_run_script_cli_flags_beat_header(port, server, tmp_path):
    """命令行显式给的 --port 覆盖 #! 头行（脚本可复用到任意端口）。"""
    p = tmp_path / 'precedence.hb'
    p.write_text(f'#! --port 1 --timeout 0.1\nopen {server}\nurl\n', encoding='utf-8')
    r = hb_raw('--port', port, p)
    assert r.returncode == 0, r.stderr
    assert server in r.stdout


def test_run_script_from_stdin(port, server):
    r = hb_raw('run', '-', '--port', port, stdin=f'open {server}\nurl\n')
    assert r.returncode == 0, r.stderr
    assert server in r.stdout


def test_run_script_syntax_error_needs_no_browser(tmp_path):
    p = tmp_path / 'broken.hb'
    p.write_text('input @1 "未闭合\n', encoding='utf-8')
    r = hb_raw('run', p)
    assert r.returncode != 0 and '第 1 行' in r.stderr


def test_find_all_index_and_ref_chain(port, server):
    hb(port, 'open', server)
    refs = json.loads(hb(port, 'find', '.card', '--all', '--json'))
    assert [r['ref'] for r in refs] == ['@1', '@2']
    assert refs[0]['text'].startswith('Card A') and refs[1]['text'].startswith('Card B')

    # --index 只缓存选中的那一个，并重新编号为 @1；负数从末尾算
    one = json.loads(hb(port, 'find', '.card', '--index', '2', '--json'))
    assert len(one) == 1 and one[0]['ref'] == '@1' and one[0]['text'].startswith('Card B')
    last = json.loads(hb(port, 'find', '.card', '--index', '-1', '--json'))
    assert last[0]['text'].startswith('Card B')
    hb(port, 'find', '.card', '--index', '9', expect_fail=True)
    hb(port, 'find', '.card', '--index', '0', expect_fail=True)
    hb(port, 'find', '.card', '--index', '2', '--all', expect_fail=True)

    # @n 后面继续匹配：>> 后代 / > 直接子元素 / 多级串联
    hb(port, 'find', '.card', '--all', '--json')
    assert hb(port, 'text', '@1 >> .buy') == 'buy-A'
    assert hb(port, 'text', '@2 > h3') == 'Card B'
    assert hb(port, 'text', '@2 >> h3') == 'Card B'
    assert hb(port, 'text', '@2>>h3') == 'Card B'
    hb(port, 'click', '@1 >> .buy')
    assert hb(port, 'text', '@1 > h3') == 'Card A'
    hb(port, 'text', '@1 >> .nope', expect_fail=True)

    # find 也能在引用作用域里查找，并把结果重新编号
    hb(port, 'find', '.card', '--all', '--json')
    inner = json.loads(hb(port, 'find', '@2 >> .buy', '--json'))
    assert [r['text'] for r in inner] == ['buy-B']
    assert hb(port, 'text', '@1') == 'buy-B'
    single = json.loads(hb(port, 'find', '@1', '--json'))   # 纯引用同样适用
    assert single[0]['text'] == 'buy-B'


def test_at_attribute_locator_is_not_a_ref(port, server):
    # 以 @ 开头的 DSL 属性存在性定位符不能被当成 @n 引用
    hb(port, 'open', server)
    refs = json.loads(hb(port, 'find', '@data-flag', '--json'))
    assert [r['id'] for r in refs] == ['out']
    assert hb(port, 'attr', '@data-flag', 'id') == 'out'


def test_ref_axes(port, server):
    hb(port, 'open', server)
    rows = json.loads(hb(port, 'find', '.row', '--all', '--json'))
    assert [r['ref'] for r in rows] == ['@1', '@2', '@3']

    # 兄弟：next / prev，可带序号或定位符
    assert hb(port, 'text', '@1 next') == '2'
    assert hb(port, 'text', '@3 prev') == '2'
    assert hb(port, 'text', '@1 next(2)') == '3'
    assert hb(port, 'attr', '@3 prev(2)', 'id') == 'r1'
    assert hb(port, 'attr', '@1 next(.row)', 'id') == 'r2'

    # 父元素：parent / parent(层数)
    assert hb(port, 'attr', '@2 parent', 'id') == 'list'
    assert hb(port, 'attr', '@2 parent(2)', 'id') == 'wrap'

    # 轴之后继续接 >> / >，find 也支持
    inner = json.loads(hb(port, 'find', '@2 parent >> .row', '--json'))
    assert [r['id'] for r in inner] == ['r1', 'r2', 'r3']
    hb(port, 'find', '.row', '--all', '--json')
    assert hb(port, 'attr', '@1 parent > li', 'id') == 'r1'
    one = json.loads(hb(port, 'find', '@1 next', '--json'))
    assert one[0]['ref'] == '@1' and one[0]['text'] == '2'

    # 卡片兄弟（嵌套结构里换到兄弟再往下找）
    hb(port, 'find', '.card', '--all', '--json')
    assert hb(port, 'text', '@1 next >> h3') == 'Card B'
    assert hb(port, 'attr', '@2 prev', 'id') == 'c1'
    hb(port, 'hl', '@1 next >> h3', '--json')

    # 错误：未知轴 / 参数非法 / 越界
    hb(port, 'text', '@1 cousin', expect_fail=True)
    hb(port, 'text', '@1 parent(0)', expect_fail=True)
    hb(port, 'text', '@3 next', expect_fail=True)
