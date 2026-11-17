# -*- coding: utf-8 -*-
"""M3 集成测试：查找闭环（页面级/元素级/ShadowRoot 双通道、相对与方向定位、waiter、
alert、run_js 对象返回）。本地 http 夹具 + auto_port headless 临时实例，绝不触碰 9222。"""
import http.server
import threading

import pytest

from happybrowser import Browser, Options, Element, ShadowRoot, NoneElement, ElementsList, Settings
from happybrowser._core.launcher import get_chromium_path
from happybrowser.errors import ElementNotFoundError, LocatorError

try:
    get_chromium_path()
    HAS_BROWSER = True
except Exception:
    HAS_BROWSER = False

pytestmark = pytest.mark.skipif(not HAS_BROWSER, reason='本机未找到 Chromium 系浏览器')

HTML = '''<!DOCTYPE html><html><head><title>hb-find</title></head><body>
<div id="main" class="container">
  <h1 id="title">Hello <em>hb</em></h1>
  <p class="lead">first para</p>
  <p class="lead">second para</p>
  <a id="a1" href="https://example.com/page/1">link one</a>
  <a id="a2" href="https://example.com/page/2">link two</a>
  <a id="a3" href="mailto:x@y.z">mail</a>
  <div id="brtext">A<br>B</div>
  <pre id="pre">  keep
  spaces </pre>
  <div id="emptext"></div>
  <div id="grid"><button id="btn-west">W</button><button id="btn-east">E</button></div>
  <my-host id="host"></my-host>
  <input type="date" id="date-input">
  <iframe id="frm" src="frame.html"></iframe>
</div>
<script>
  customElements.define('my-host', class extends HTMLElement {
    connectedCallback() {
      const sr = this.attachShadow({mode: 'open'});
      sr.innerHTML = '<div class="inner"><button id="in-shadow">shadow btn</button></div>';
    }
  });
</script>
</body></html>'''

FRAME_HTML = '<!DOCTYPE html><html><body><p id="in-frame">frame text</p></body></html>'


@pytest.fixture(scope='module')
def server(tmp_path_factory):
    d = tmp_path_factory.mktemp('hbfind')
    (d / 'index.html').write_text(HTML, encoding='utf-8')
    (d / 'frame.html').write_text(FRAME_HTML, encoding='utf-8')

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(d), **kw)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f'http://127.0.0.1:{srv.server_address[1]}/index.html'
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
    t = browser.new_tab(server)
    yield t
    try:
        t.close()
    except Exception:
        pass


class TestPageFind:
    def test_get_navresult(self, tab):
        from happybrowser._functions.web import NavResult
        assert isinstance(tab._nav_result, NavResult)
        assert tab._nav_result.status == 200

    def test_pure_id_class_tag(self, tab):
        assert tab.ele('#title').text == 'Hello hb'
        assert tab.ele('h1').attr('id') == 'title'
        assert tab.eles('.lead')[1].text == 'second para'
        assert isinstance(tab.eles('.lead'), ElementsList)

    def test_index_semantics(self, tab):
        assert tab.ele('.lead', index=2).text == 'second para'
        assert tab.ele('.lead', index=-1).text == 'second para'
        assert tab.ele('.lead', index=-2).text == 'first para'
        assert tab.ele('.lead', index=3, timeout=.3) in (None, False) or \
            not tab.ele('.lead', index=3, timeout=.3)

    def test_text_and_owntext(self, tab):
        assert tab.ele('text="second para"').attr('class') == 'lead'
        assert tab.ele('owntext="link one"').attr('id') == 'a1'
        empties = tab.eles('text=""')
        assert any(e.attr('id') == 'emptext' for e in empties)

    def test_hybrid_regex(self, tab):
        assert tab.ele('a && href/="page/1$"').attr('id') == 'a1'
        assert tab.ele('tag/="^my-"').attr('id') == 'host'
        assert len(tab.eles('a && href^=https:', timeout=1)) == 2

    def test_pure_hybrid_agreement(self, tab):
        assert len(tab.eles('a', timeout=1)) == 3
        assert len(tab.eles('a && href/=example', timeout=1)) == 2  # mailto 被正则筛掉

    def test_dsl_without_question_mark(self, tab):
        assert tab.ele('?#title') == tab.ele('#title')  # 两种写法等价（backend_id 判等）

    def test_bad_locator_type(self, tab):
        with pytest.raises(LocatorError):
            tab.ele(123)

    def test_none_element(self, tab):
        ne = tab.ele('#nope', timeout=.3)
        assert isinstance(ne, NoneElement) and not ne
        assert 'parsed=' in repr(ne)

    def test_none_element_raise_setting(self, tab):
        Settings.set_raise_when_ele_not_found(True)
        try:
            with pytest.raises(ElementNotFoundError):
                tab.ele('#nope', timeout=.3)
        finally:
            Settings.set_raise_when_ele_not_found(False)


class TestElementFind:
    def test_descendant(self, tab):
        main = tab.ele('#main')
        assert main.ele('.lead').text == 'first para'
        assert len(main.eles('a')) == 3

    def test_relative_family(self, tab):
        a1, a2 = tab.ele('#a1'), tab.ele('#a2')
        assert a1.next().attr('id') == 'a2'
        assert a2.prev().attr('id') == 'a1'
        assert a2.before('a').attr('id') == 'a1'  # 就近优先
        assert a1.after('a').attr('id') == 'a2'
        assert len(a1.nexts('a')) == 2

    def test_parent(self, tab):
        h1 = tab.ele('#title')
        assert h1.parent().attr('id') == 'main'
        assert h1.parent(2).tag == 'body'
        assert h1.parent('body').tag == 'body'

    def test_children(self, tab):
        main = tab.ele('#main')
        assert len(main.children()) >= 10
        assert len(main.children('.lead')) == 2
        assert main.child().tag == 'h1'

    def test_hybrid_in_element(self, tab):
        main = tab.ele('#main')
        assert main.ele('a && href/="page/2"').attr('id') == 'a2'

    def test_iframe_descend(self, tab):
        frm = tab.ele('#frm')
        assert frm.tag == 'iframe'
        assert frm.ele('#in-frame').text == 'frame text'


class TestDirection:
    def test_east_west(self, tab):
        w, e = tab.ele('#btn-west'), tab.ele('#btn-east')
        assert w.east().attr('id') == 'btn-east'
        assert e.west().attr('id') == 'btn-west'

    def test_offset_self(self, tab):
        assert tab.ele('#btn-west').offset().attr('id') == 'btn-west'

    def test_states_rect(self, tab):
        a1 = tab.ele('#a1')
        assert a1.states.is_displayed
        assert a1.states.has_rect
        assert a1.rect.size[0] > 0
        assert tab.rect.size[1] > 0


class TestTextSemantics:
    def test_br_newline(self, tab):
        assert tab.ele('#brtext').text == 'A\nB'

    def test_pre_keeps_format(self, tab):
        t = tab.ele('#pre').text
        assert t.startswith('keep') and 'keep\n' in t

    def test_texts_method(self, tab):
        assert 'link one' in tab.ele('#a1').texts()
        assert tab.ele('#a1').texts(text_node_only=True) == ['link one']

    def test_raw_text(self, tab):
        assert tab.ele('#a1').raw_text == 'link one'


class TestShadowRoot:
    def test_open_shadow_find(self, tab):
        host = tab.ele('#host')
        sr = host.shadow_root
        assert isinstance(sr, ShadowRoot)
        assert sr.ele('#in-shadow').text == 'shadow btn'
        assert len(sr.eles('.inner')) == 1

    def test_shadow_hybrid(self, tab):
        sr = tab.ele('#host').shadow_root
        assert sr.ele('button && text/="^shadow b"').attr('id') == 'in-shadow'

    def test_page_level_does_not_pierce_open_shadow(self, tab):
        """实测（Chrome 157）：performSearch 的 includeUserAgentShadowDOM 只穿 UA shadow，
        不穿 open shadow——页面级查不到 shadow 内元素，必须经 host.shadow_root（与上游一致）。"""
        assert not tab.ele('#in-shadow', timeout=1)

    def test_ua_shadow_poc(self, tab):
        """UA shadow PoC：describeNode 应暴露 UA shadow root，且 evaluate 可以其为上下文。"""
        inp = tab.ele('#date-input')
        sr = inp.shadow_root
        assert sr is not None
        assert len(sr.eles('*', timeout=1)) > 0


class TestRunJs:
    def test_returns_element(self, tab):
        r = tab.run_js('return document.querySelector("#a1");')
        assert isinstance(r, Element) and r.attr('id') == 'a1'

    def test_element_this(self, tab):
        assert tab.ele('#a1').run_js('return this.id;') == 'a1'

    def test_args(self, tab):
        assert tab.run_js('return arguments[0] + arguments[1];', 1, 2) == 3
        assert tab.run_js('return arguments[0].id;', tab.ele('#a1')) == 'a1'

    def test_array_and_object(self, tab):
        assert tab.run_js('return [1, "two"];') == [1, 'two']
        assert tab.run_js('return {a: 1};') == {'a': 1}

    def test_none_and_undefined(self, tab):
        assert tab.run_js('return null;') is None
        assert tab.run_js('return undefined;') is None
        assert tab.run_js('return 1 + 1;') == 2


class TestWaiter:
    def test_doc_loaded(self, tab):
        assert tab.wait.doc_loaded()

    def test_url_change(self, tab):
        assert tab.wait.url_change('index.html') is tab
        assert tab.wait.title_change('hb-find') is tab

    def test_ele_wait_states(self, tab):
        a1 = tab.ele('#a1')
        assert a1.wait.displayed() is a1
        assert a1.wait.enabled() is a1
        assert a1.wait.hidden(timeout=.2) is False
        assert a1.wait.deleted(timeout=.2) is False

    def test_page_wait_ele_displayed(self, tab):
        assert tab.wait.ele_displayed('#a1', timeout=1) == tab.ele('#a1')


class TestAlert:
    def test_alert_flow(self, tab):
        tab.run_async_js('alert("hi");')
        from time import sleep
        for _ in range(50):
            if tab.states.has_alert:
                break
            sleep(.05)
        assert tab.states.has_alert
        assert tab.handle_alert(True) == 'hi'
        assert not tab.states.has_alert


class TestMisc:
    def test_new_ele_remove_ele(self, tab):
        e = tab.new_ele(('div', {'id': 'inserted', 'innerText': 'inserted text'}))
        assert e.attr('id') == 'inserted'
        tab.remove_ele('#inserted')
        assert not tab.ele('#inserted', timeout=.3)

    def test_json_none(self, tab):
        assert tab.json is None

    def test_html_property(self, tab):
        assert 'id="main"' in tab.html

    def test_active_ele(self, tab):
        assert tab.active_ele is not None

    def test_back_forward_refresh(self, tab):
        tab.refresh()
        assert tab.wait.doc_loaded()
        tab.back()
        tab.forward()
        assert tab.wait.doc_loaded()

    def test_storage(self, tab):
        tab.run_js('localStorage.setItem("k", "v");')
        assert tab.local_storage('k') == 'v'
        assert tab.session_storage() == {}
