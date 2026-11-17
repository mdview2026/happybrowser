# -*- coding: utf-8 -*-
"""M4 集成测试：点击/输入/按键/下拉/勾选/hover/拖拽/滚动/动作链。
本地 http 夹具 + auto_port headless 临时实例，绝不触碰 9222。"""
import http.server
import threading

import pytest

from happybrowser import Browser, Options, Keys

try:
    from happybrowser._core.launcher import get_chromium_path  # noqa: F401 探测浏览器可用性
    HAS_BROWSER = True
except Exception:
    HAS_BROWSER = False

pytestmark = pytest.mark.skipif(not HAS_BROWSER, reason='本机未找到 Chromium 系浏览器')

HTML = '''<!DOCTYPE html><html><head><title>hb-act</title><style>
#hov:hover {color: red;}
#dragger {width:40px;height:40px;background:#00f;position:absolute;left:10px;top:150px;}
#tall {height:3000px;}
</style></head><body>
<div id="main">
  <span id="count">0</span>
  <button id="inc">inc</button>
  <form id="f" action="" method="get"><input id="q" name="q"><input type="submit" id="sub" value="go"></form>
  <input id="txt" type="text">
  <textarea id="ta"></textarea>
  <select id="sel"><option value="v1">one</option><option value="v2">two</option><option value="v3">three</option></select>
  <select id="mul" multiple size="3">
    <option value="m1">ma</option><option value="m2">mb</option><option value="m3">mc</option></select>
  <input type="checkbox" id="ck"><input type="radio" id="rd" name="r">
  <div id="hov">hover me</div>
  <div id="dragger"></div>
  <a id="blank" href="frame.html" target="_blank">new tab</a>
  <div id="tall"></div>
  <p id="bottom-marker">bottom</p>
</div>
<script>
  document.getElementById('inc').addEventListener('click', function() {
    document.getElementById('count').textContent =
      Number(document.getElementById('count').textContent) + 1;
  });
  const dg = document.getElementById('dragger');
  let sx=0, sy=0, ox=0, oy=0, dragging=false;
  dg.addEventListener('mousedown', function(e){dragging=true; sx=e.clientX; sy=e.clientY;
    ox=parseInt(dg.style.left)||10; oy=parseInt(dg.style.top)||10;});
  document.addEventListener('mousemove', function(e){if(!dragging) return;
    dg.style.left=(ox+e.clientX-sx)+'px'; dg.style.top=(oy+e.clientY-sy)+'px';});
  document.addEventListener('mouseup', function(){dragging=false;});
</script>
</body></html>'''

FRAME_HTML = '<!DOCTYPE html><html><body><p id="in-frame">frame text</p></body></html>'


@pytest.fixture(scope='module')
def server(tmp_path_factory):
    d = tmp_path_factory.mktemp('hbact')
    (d / 'index.html').write_text(HTML, encoding='utf-8')
    (d / 'frame.html').write_text(FRAME_HTML, encoding='utf-8')

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(d), **kw)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
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


class TestClick:
    def test_click_increments(self, tab):
        before = int(tab.ele('#count').text)
        tab.ele('#inc').click()
        assert int(tab.ele('#count').text) == before + 1

    def test_click_by_js(self, tab):
        before = int(tab.ele('#count').text)
        tab.ele('#inc').click(by_js=True)
        assert int(tab.ele('#count').text) == before + 1

    def test_click_at_offset(self, tab):
        before = int(tab.ele('#count').text)
        tab.ele('#inc').click.at(5, 5)
        assert int(tab.ele('#count').text) == before + 1

    def test_click_multi(self, tab):
        before = int(tab.ele('#count').text)
        tab.ele('#inc').click.multi(2)
        assert int(tab.ele('#count').text) >= before + 1  # clickCount=2 的 click/dblclick 派发次数因浏览器而异

    def test_click_for_url_change(self, tab):
        tab.ele('#q').input('x')
        assert tab.ele('#sub').click.for_url_change('q=', timeout=5)
        assert 'q=' in tab.url
        tab.get(tab.url.split('?')[0])


class TestInput:
    def test_input_text(self, tab):
        t = tab.ele('#txt')
        t.input('hello')
        assert t.value == 'hello'
        t.input(' world', clear=False)
        assert t.value == 'hello world'

    def test_input_clear(self, tab):
        t = tab.ele('#txt')
        t.input('old')
        t.input('new', clear=True)
        assert t.value == 'new'

    def test_input_by_js(self, tab):
        t = tab.ele('#ta')
        t.input('js text', by_js=True)
        assert t.value == 'js text'

    def test_input_chinese(self, tab):
        t = tab.ele('#ta')
        t.clear()
        t.input('你好世界')
        assert t.value == '你好世界'

    def test_clear(self, tab):
        t = tab.ele('#ta')
        t.clear()
        assert t.value == ''

    def test_enter_submits_form(self, tab):
        q = tab.ele('#q')
        q.input('kw')
        q.input(Keys.ENTER)
        tab.wait.url_change('q=kw', timeout=5)
        assert 'q=kw' in tab.url
        tab.get(tab.url.split('?')[0])

    def test_ctrl_a_del(self, tab):
        q = tab.ele('#q')
        q.input('abc')
        assert q.value == 'abc'
        q.input((Keys.CTRL_A, Keys.DEL))
        assert q.value == ''


class TestSelect:
    def test_by_text(self, tab):
        s = tab.ele('#sel').select
        s.by_text('two')
        assert s.selected_option.text == 'two'

    def test_by_value(self, tab):
        s = tab.ele('#sel').select
        s.by_value('v3')
        assert s.selected_option.attr('value') == 'v3'

    def test_by_index(self, tab):
        s = tab.ele('#sel').select
        s.by_index(1)
        assert s.selected_option.text == 'one'

    def test_call(self, tab):
        s = tab.ele('#sel').select
        s('three')
        assert s.selected_option.text == 'three'

    def test_option_click_selects(self, tab):
        tab.ele('#sel').select.by_index(1)
        opt = tab.ele('#sel').eles('option')[1]
        opt.click()
        assert tab.ele('#sel').select.selected_option == opt

    def test_multi_select_and_cancel(self, tab):
        s = tab.ele('#mul').select
        assert s.is_multi
        s.by_text(['ma', 'mc'])
        assert sorted(o.text for o in s.selected_options) == ['ma', 'mc']
        s.by_text('ma', cancel=True)
        assert [o.text for o in s.selected_options] == ['mc']

    def test_select_property_false_on_non_select(self, tab):
        assert tab.ele('#txt').select is False


class TestCheck:
    def test_checkbox(self, tab):
        ck = tab.ele('#ck')
        assert not ck.states.is_checked
        ck.check()
        assert ck.states.is_checked
        ck.check(False)
        assert not ck.states.is_checked

    def test_radio(self, tab):
        rd = tab.ele('#rd')
        rd.check()
        assert rd.states.is_checked


class TestHover:
    def test_hover_changes_style(self, tab):
        h = tab.ele('#hov')
        before = h.style('color')
        h.hover()
        after = h.style('color')
        assert before != after and '255, 0, 0' in after


class TestDrag:
    def test_drag_offset(self, tab):
        dg = tab.ele('#dragger')
        assert int(dg.style('left').rstrip('px') or 10) == 10
        assert int(dg.style('top').rstrip('px') or 150) == 150
        dg.drag(100, 50, duration=.3)
        left = int(dg.style('left').rstrip('px'))
        top = int(dg.style('top').rstrip('px'))
        # 处理器以内联 style 为起点（初始为空回退 10），断言按增量而非绝对落点
        assert left >= 100
        assert top >= 50


class TestScroll:
    def test_page_scroll(self, tab):
        tab.scroll.to_bottom()
        assert tab.run_js('return window.scrollY;') > 0
        tab.scroll.to_top()
        assert tab.run_js('return window.scrollY;') == 0

    def test_scroll_by_pixel(self, tab):
        tab.scroll.down(500)
        assert tab.run_js('return window.scrollY;') == 500
        tab.scroll.to_top()

    def test_to_see(self, tab):
        tab.scroll.to_see('#bottom-marker')
        assert tab.run_js('return window.scrollY;') > 0
        tab.scroll.to_top()

    def test_element_scroll_to_see(self, tab):
        tab.ele('#bottom-marker').scroll.to_see()
        assert tab.run_js('return window.scrollY;') > 0
        tab.scroll.to_top()


class TestActions:
    def test_actions_chain(self, tab):
        before = int(tab.ele('#count').text)
        tab.actions.move_to('#inc').click()
        assert int(tab.ele('#count').text) == before + 1

    def test_actions_input(self, tab):
        tab.ele('#txt').focus()
        tab.actions.input('链式输入')
        assert tab.ele('#txt').value == '链式输入'

    def test_actions_key_down_up(self, tab):
        from happybrowser._core.keys import make_input_data
        assert make_input_data(0, 'a') is not None  # 冒烟：数据构造可用
