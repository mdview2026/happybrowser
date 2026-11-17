# -*- coding: utf-8 -*-
"""hb CLI 集成测试：subprocess 调 .venv 里的 hb.exe，随机端口 headless 实例，绝不触碰 9222。
覆盖：无参显示帮助/open/find(@n 引用)/input/click/text/attr/js/shot/--json 前后位置/引用失效/explain。"""
import http.server
import json
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
<p id="out"></p>
<a class="lnk" href="#a">A</a><a class="lnk" href="#b">B</a>
</body></html>'''


@pytest.fixture(scope='module')
def server():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = HTML.encode()
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
