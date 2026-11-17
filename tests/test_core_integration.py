# -*- coding:utf-8 -*-
"""M2 集成测试：启动/接管/auto_port/导航/tab 管理（需本机 Chrome，全程 auto_port 临时实例，
绝不触碰默认 9222 端口与用户日常浏览器）。"""
import pytest

from happybrowser import Browser, Options
from happybrowser._core.launcher import get_chromium_path
from happybrowser._core.tools import PortFinder
from happybrowser.errors import BrowserConnectError

try:
    get_chromium_path()
    HAS_BROWSER = True
except Exception:
    HAS_BROWSER = False

pytestmark = pytest.mark.skipif(not HAS_BROWSER, reason='本机未找到 Chromium 系浏览器')

DATA_URL = 'data:text/html,<html><head><title>hb-test</title></head><body><p>hello</p></body></html>'


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


class TestLaunch:
    def test_basic(self, browser):
        assert browser.is_alive
        assert browser.is_headless          # 事实探测：headless=new 的 UA 含 Headless
        assert 'Chrome/' in browser.version
        assert browser.address.startswith('127.0.0.1:')
        assert browser.user_data_path and 'happybrowser' in browser.user_data_path.replace('\\', '/')

    def test_singleton_attach_same_object(self, browser):
        opts2 = Options().set_address(browser.address)
        assert Browser(opts2) is browser    # 接管已有实例 → 返回同一单例，绝不重启

    def test_launch_mode_conflict(self, browser):
        with pytest.raises(BrowserConnectError):
            Browser.launch(Options().set_address(browser.address))


class TestNavigate:
    def test_new_tab_get(self, browser):
        t = browser.new_tab(DATA_URL)
        assert t.title == 'hb-test'
        assert t.url.startswith('data:text/html')
        assert t.ready_state == 'complete'
        t.close()

    def test_run_js(self, browser):
        t = browser.new_tab(DATA_URL)
        assert t.run_js('return 1 + 1') == 2
        assert t.run_js('return arguments.length', 1, 2) == 2
        assert t.run_js('1 + 2', as_expr=True) == 3
        t.close()


class TestTabManagement:
    def test_tabs_and_close(self, browser):
        t1 = browser.new_tab('about:blank')
        t2 = browser.new_tab('about:blank', background=True)
        assert t1.tab_id in browser.tab_ids and t2.tab_id in browser.tab_ids
        ids = list(browser.tab_ids)
        assert len(browser.tabs) == len(ids)
        t2.activate()
        t2.close()
        assert t2.tab_id not in browser.tab_ids
        t1.close(others=True)   # 关掉其他，只留自己
        assert list(browser.tab_ids) == [t1.tab_id]
        browser.new_tab('about:blank')   # 保持至少一个 tab，避免最后一个 tab 关闭触发 quit

    def test_latest_tab(self, browser):
        t = browser.latest_tab
        assert t.tab_id in browser.tab_ids


class TestContext:
    def test_new_context_isolated(self, browser):
        ctx = browser.new_context()
        t = ctx.new_tab('about:blank')
        assert t.tab_id in ctx.tab_ids
        assert ctx.tab_ids and not (set(ctx.tab_ids) & set(browser.tab_ids))
        ctx.close()


class TestAttachDead:
    def test_attach_dead_port_raises(self):
        port = PortFinder().get_port()      # 空闲端口
        with pytest.raises(BrowserConnectError):
            Browser.attach(f'127.0.0.1:{port}')
