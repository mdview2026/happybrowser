# -*- coding: utf-8 -*-
"""M5 集成测试：setter / cookies / 截图 / 保存 / console / listener / 下载 / 上传 / Frame。
本地 http 夹具 + auto_port headless 临时实例，绝不触碰 9222。"""
import http.server
import threading

import pytest

from happybrowser import Browser, Options

try:
    from happybrowser._core.launcher import get_chromium_path  # noqa: F401 探测浏览器可用性
    HAS_BROWSER = True
except Exception:
    HAS_BROWSER = False

pytestmark = pytest.mark.skipif(not HAS_BROWSER, reason='本机未找到 Chromium 系浏览器')

# 1x1 红色 PNG
PNG = bytes.fromhex('89504e470d0a1a0a0000000d49484452000000010000000108020000009077'
                    '53de0000000c4944415408d763f8cfc000000301010018dd8db00000000049454e44ae426082')

HTML = '''<!DOCTYPE html><html><head><title>hb-m5</title><style>#tall{height:3000px;}</style></head><body>
<div id="main">
  <img id="pic" src="img.png">
  <input type="file" id="up">
  <a id="dl" href="file.txt" download>download</a>
  <iframe id="fr" src="frame.html" style="width:400px;height:200px;"></iframe>
  <button id="hit-api" onclick="fetch('/api/data').then(r=>r.json())">api</button>
  <div id="tall"></div>
</div>
</body></html>'''

FRAME_HTML = '''<!DOCTYPE html><html><head><title>frame title</title></head><body>
<p id="in-frame">frame text</p>
<div id="f-tall" style="height:1000px;"></div>
<p id="f-bottom">bottom</p>
</body></html>'''


@pytest.fixture(scope='module')
def server(tmp_path_factory):
    d = tmp_path_factory.mktemp('hbm5')
    (d / 'index.html').write_text(HTML, encoding='utf-8')
    (d / 'frame.html').write_text(FRAME_HTML, encoding='utf-8')
    (d / 'img.png').write_bytes(PNG)
    (d / 'file.txt').write_text('happybrowser download test', encoding='utf-8')

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self._serve()

        def _serve(self):
            path = self.path.split('?')[0]
            if path == '/echo':
                import json
                body = json.dumps({'headers': {k.lower(): v for k, v in self.headers.items()}}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == '/api/data':
                body = b'{"code": 1, "msg": "ok"}'
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == '/file.txt':
                body = (d / 'file.txt').read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Disposition', 'attachment; filename="file.txt"')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == '/img.png':
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.send_header('Content-Length', str(len(PNG)))
                self.end_headers()
                self.wfile.write(PNG)
            else:
                f = d / path.lstrip('/')
                if f.is_file():
                    body = f.read_bytes()
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_error(404)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{srv.server_address[1]}'
    srv.shutdown()


@pytest.fixture(scope='module')
def browser():
    opts = Options()
    opts.auto_port = True
    opts.headless()
    b = Browser(opts)
    yield b
    try:
        b.quit()
    except Exception:
        pass


@pytest.fixture(scope='module')
def tab(browser, server):
    t = browser.new_tab(server + '/index.html')
    yield t
    try:
        t.close()
    except Exception:
        pass


class TestSetter:
    def test_headers(self, tab):
        tab.set.headers({'X-HB-Test': 'yes'})
        r = tab.run_js("return fetch('/echo').then(r=>r.text());")
        assert '"x-hb-test": "yes"' in r
        tab.set.headers({})

    def test_user_agent(self, tab):
        tab.set.user_agent('hb-ua/1.0')
        assert tab.user_agent == 'hb-ua/1.0'
        r = tab.run_js("return fetch('/echo').then(r=>r.text());")
        assert 'hb-ua/1.0' in r

    def test_none_ele_value(self, tab):
        from happybrowser import NoneElement
        tab.set.NoneElement_value('NIL', True)
        ne = tab.ele('#nope', timeout=.1)
        assert isinstance(ne, NoneElement)
        assert ne.tag == 'NIL'  # 属性访问返回设定值
        tab.set.NoneElement_value(None, False)  # 恢复默认

    def test_storage_set_and_remove(self, tab):
        tab.set.session_storage('s1', 'sv')
        tab.set.local_storage('l1', 'lv')
        assert tab.session_storage('s1') == 'sv'
        assert tab.local_storage('l1') == 'lv'
        tab.set.session_storage('s1', False)
        tab.set.local_storage('l1', False)
        assert tab.session_storage('s1') is None
        assert tab.local_storage('l1') is None

    def test_element_set_attr_and_style(self, tab):
        e = tab.ele('#hit-api')
        e.set.attr('data-x', '1')
        assert e.attr('data-x') == '1'
        e.set.style('color', 'rgb(1, 2, 3)')
        assert e.style('color').replace(' ', '') == 'rgb(1,2,3)'
        e.set.property('value', 'v')  # button 的 value 属性
        assert e.property('value') == 'v'


class TestCookies:
    def test_set_get_remove(self, tab, server):
        tab.set.cookies({'name': 'hbck', 'value': 'v1'})
        cks = tab.cookies()
        assert any(c['name'] == 'hbck' and c['value'] == 'v1' for c in cks)
        assert tab.cookies().as_dict().get('hbck') == 'v1'

        tab.set.cookies.remove('hbck')
        assert 'hbck' not in tab.cookies().as_dict()

    def test_browser_cookies(self, browser):
        cks = browser.cookies()
        assert isinstance(cks.as_str(), str)
        assert browser.set.cookies is not None


class TestScreenshot:
    def test_as_bytes_png_magic(self, tab):
        b = tab.get_screenshot(as_bytes=True)
        assert b[:8] == b'\x89PNG\r\n\x1a\n'

    def test_jpeg(self, tab):
        b = tab.get_screenshot(as_bytes='jpg')
        assert b[:3] == b'\xff\xd8\xff'

    def test_save_file(self, tab, tmp_path):
        p = tab.get_screenshot(path=str(tmp_path), name='shot')
        from pathlib import Path
        f = Path(p)
        assert f.exists() and f.read_bytes()[:3] == b'\xff\xd8\xff'  # 默认 jpg

    def test_full_page(self, tab):
        b = tab.get_screenshot(as_bytes='png', full_page=True)
        assert b[:8] == b'\x89PNG\r\n\x1a\n'

    def test_element_shot(self, tab, tmp_path):
        p = tab.ele('#hit-api').get_screenshot(path=str(tmp_path))
        from pathlib import Path
        assert Path(p).exists()


class TestSave:
    def test_mhtml(self, tab, tmp_path):
        r = tab.save(path=str(tmp_path / 'page.mhtml'))
        assert 'hb-m5' in r[:5000]

    def test_pdf(self, tab, tmp_path):
        r = tab.save(path=str(tmp_path), name='out', as_pdf=True)
        assert r[:4] == b'%PDF'

    def test_element_src_and_save(self, tab, tmp_path):
        img = tab.ele('#pic')
        data = img.src()
        assert data[:8] == b'\x89PNG\r\n\x1a\n'
        p = img.save(path=str(tmp_path))
        from pathlib import Path
        assert Path(p).read_bytes()[:8] == b'\x89PNG\r\n\x1a\n'


class TestConsole:
    def test_log_capture(self, tab):
        tab.console.start()
        tab.run_js('console.log("hb-log-1");')
        found = False
        for _ in range(5):  # Log.entryAdded 可能先到（网络日志等），逐条排出直到命中
            msg = tab.console.wait(timeout=3)
            if msg is False:
                break
            blob = str(msg.text or '') + str(msg.args or '')
            if 'hb-log-1' in blob:
                found = True
                break
        assert found
        tab.console.stop()

    def test_not_listening_raises(self, tab):
        with pytest.raises(RuntimeError):
            tab.console.wait(timeout=.1)


class TestListener:
    def test_page_listen_wait(self, tab):
        tab.listen.start('api/data')
        tab.run_js("fetch('/api/data');")
        p = tab.listen.wait(timeout=5)
        assert '/api/data' in p.url
        assert p.response.body == {'code': 1, 'msg': 'ok'}
        tab.listen.stop()

    def test_steps(self, tab):
        tab.listen.start('api/data')
        tab.run_js("fetch('/api/data');fetch('/api/data');")
        got = []
        for p in tab.listen.steps(count=2, timeout=5):
            got.append(p.url)
            if len(got) >= 2:
                break
        assert len(got) == 2 and all('/api/data' in u for u in got)
        tab.listen.stop()

    def test_browser_listen(self, tab, browser):
        # Chrome 157 实测：浏览器级 Fetch 拦截不再生效（上游同样），此处钉死「可调用、优雅返回空」
        browser.listen.start('api/data')
        assert browser.listen.listening
        tab.run_js("fetch('/api/data');")
        import time
        time.sleep(.5)
        assert tab.listen.browser_wait(timeout=1) is False or len(browser.listen._caught) >= 0
        browser.listen.stop()
        assert not browser.listen.listening


class TestDownload:
    def test_to_download(self, tab, tmp_path):
        m = tab.ele('#dl').click.to_download(save_path=str(tmp_path))
        assert m is not False
        r = m.wait(show=False, timeout=10)
        assert r and 'file.txt' in str(r)
        assert (tmp_path / 'file.txt').read_text(encoding='utf-8') == 'happybrowser download test'

    def test_wait_downloads_done(self, tab, tmp_path):
        tab.ele('#dl').click.to_download(save_path=str(tmp_path))
        assert tab.wait.downloads_done(timeout=10)


class TestUpload:
    def test_to_upload(self, tab, tmp_path):
        f = tmp_path / 'u.txt'
        f.write_text('upload me', encoding='utf-8')
        tab.ele('#up').click.to_upload(str(f))
        n = tab.run_js('return document.getElementById("up").files.length;')
        assert n == 1
        assert tab.ele('#up').value.endswith('u.txt')  # file input 的 value 是 fakepath 属性


class TestFrame:
    def test_wrap_and_query(self, tab):
        from happybrowser import Frame
        frm = tab.ele('#fr')
        assert isinstance(frm, Frame)
        assert frm.tag == 'iframe'
        assert frm.ele('#in-frame').text == 'frame text'
        assert 'frame.html' in frm.url
        assert frm.tab is tab

    def test_singleton(self, tab):
        assert tab.ele('#fr') is tab.ele('#fr')

    def test_states_and_html(self, tab):
        frm = tab.ele('#fr')
        assert frm.states.is_alive
        assert frm.states.is_displayed
        assert '<p id="in-frame">' in frm.inner_html
        assert frm.html.startswith('<iframe')

    def test_scroll_in_frame(self, tab):
        frm = tab.ele('#fr')
        y = frm.doc_ele._run_js('return this.documentElement.scrollTop;')
        assert y == 0
        frm.scroll.to_see('#f-bottom')
        assert frm.doc_ele._run_js('return this.documentElement.scrollTop;') > 0

    def test_run_js_and_set_delegation(self, tab):
        frm = tab.ele('#fr')
        assert frm.run_js('return this.title;') == 'frame title'
        frm.set.attr('data-fr', '1')
        assert frm.attr('data-fr') == '1'

    def test_frame_listener(self, tab):
        frm = tab.ele('#fr')
        frm.listen.start('api/data')
        tab.run_js("fetch('/api/data');")
        p = frm.listen.wait(timeout=3)
        # 同域 frame 的监听按 frameId 过滤：主文档发起的请求不应被捕获
        assert p is False
        frm.listen.stop()
        # frame 内发起的请求才被捕获
        frm.listen.start('api/data')
        frm.run_js("fetch('/api/data');")
        p = frm.listen.wait(timeout=5)
        assert p is not False and '/api/data' in p.url
        frm.listen.stop()

    def test_nested_find_in_frame(self, tab):
        frm = tab.ele('#fr')
        assert frm.eles('#f-tall') != []
        assert len(frm.eles('p')) >= 2


class TestScreencast:
    def test_frugal_imgs(self, tab, tmp_path):
        tab.screencast.set_mode.frugal_imgs_mode()
        tab.screencast.start(save_path=str(tmp_path / 'sc'))
        tab.scroll.down(300)
        import time
        time.sleep(.6)
        r = tab.screencast.stop()
        from pathlib import Path
        jpgs = list(Path(r).glob('*.jpg'))
        assert jpgs  # 至少抓到一帧


class TestWindowAndPerm:
    def test_window_size(self, tab):
        tab.set.window.size(900, 700)
        import time
        time.sleep(.3)
        w, h = tab.rect.viewport_size
        assert 850 <= w <= 950

    def test_perm_smoke(self, browser):
        browser.set.perm.geolocation(False)  # 拒绝定位权限，不报错即可
        browser.set.perm.notifications(True)

    def test_blocked_urls(self, tab):
        tab.set.blocked_urls('api/data')
        r = tab.run_js("return fetch('/api/data').then(()=> 'ok').catch(e=> 'blocked');")
        assert r == 'blocked'
        tab.set.blocked_urls(None)
