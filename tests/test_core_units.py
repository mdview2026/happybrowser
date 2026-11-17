# -*- coding:utf-8 -*-
"""Options / tools 单元测试（无浏览器）。"""
import pytest

from happybrowser import Options, Timeout
from happybrowser._core.options import HEADLESS_UA
from happybrowser._core.tools import parse_proxy, port_is_using, wait_until, PortFinder, raise_error
from happybrowser.errors import (CDPError, ContextLostError, WaitTimeoutError)


class TestOptions:
    def test_defaults(self):
        o = Options()
        assert o.address == '127.0.0.1:9222'
        assert o.timeouts.base == 10 and o.timeouts.page_load == 30 and o.timeouts.script == 30
        assert o.load_mode == 'normal'
        assert not o.is_headless
        assert '--no-first-run' in o.arguments

    def test_headless_sugar(self):
        o = Options().headless()
        assert o.is_headless
        assert '--headless=new' in o.arguments
        assert '--disable-blink-features=AutomationControlled' in o.arguments
        assert o.user_agent == HEADLESS_UA and 'Headless' not in o.user_agent
        o2 = Options().headless(False)
        assert not o2.is_headless

    def test_incognito_no_images_mute(self):
        o = Options().incognito().no_images().mute()
        assert '--incognito' in o.arguments and '--mute-audio' in o.arguments

    def test_argument_ops(self):
        o = Options()
        o.set_argument('--foo').set_argument('--foo')   # 去重
        assert o.arguments.count('--foo') == 1
        o.remove_argument('--foo')
        assert '--foo' not in o.arguments

    def test_ua_replace(self):
        o = Options().set_user_agent('UA1').set_user_agent('UA2')
        assert o.user_agent == 'UA2'
        assert sum(a.startswith('--user-agent=') for a in o.arguments) == 1

    def test_to_from_dict_roundtrip(self):
        o = Options().headless().set_local_port(1234)
        o.timeouts.set(base=5)
        o2 = Options.from_dict(o.to_dict())
        assert o2.address == '127.0.0.1:1234' and o2.is_headless
        assert o2.timeouts.base == 5 and o2.user_agent == o.user_agent

    def test_timeout_set(self):
        t = Timeout().set(base=1, script=2)
        assert (t.base, t.script) == (1, 2)


class TestTools:
    def test_wait_until_immediate(self):
        assert wait_until(lambda: 42, timeout=1) == 42

    def test_wait_until_poll(self):
        state = {'n': 0}

        def f():
            state['n'] += 1
            return state['n'] if state['n'] >= 3 else None

        assert wait_until(f, timeout=2) == 3

    def test_wait_until_timeout_returns_none(self):
        assert wait_until(lambda: None, timeout=.05) is None

    def test_wait_until_err_txt(self):
        with pytest.raises(WaitTimeoutError):
            wait_until(lambda: None, timeout=.05, err_txt='超时')

    def test_port_is_using_free_port(self):
        p = PortFinder().get_port()
        assert port_is_using('127.0.0.1', p) is False

    def test_portfinder_distinct(self):
        pf = PortFinder()
        assert pf.get_port() != pf.get_port()

    @pytest.mark.parametrize('proxy,server,usr,pwd', [
        ('http://host:8080', 'http://host:8080', None, None),
        ('host:8080', 'http://host:8080', None, None),
        ('http://u:p@host:8080', 'http://host:8080', 'u', 'p'),
        ('socks5://host', 'socks5://host', None, None),
        ('', None, None, None),
    ])
    def test_parse_proxy(self, proxy, server, usr, pwd):
        s, u, p, _ = parse_proxy(proxy)
        assert (s, u, p) == (server, usr, pwd)

    def test_raise_error_mappings(self):
        class B:
            version = 'x'

        with pytest.raises(ContextLostError):
            raise_error({'error': 'No frame with given id found', 'method': 'm', 'args': {}}, B())
        with pytest.raises(TimeoutError):
            raise_error({'error': 'timeout', 'method': 'm', 'args': {}}, B())
        with pytest.raises(CDPError):
            raise_error({'error': 'whatever', 'method': 'm', 'args': {}}, B())
        # ignore 返回原结果不抛
        r = raise_error({'error': 'timeout', 'method': 'm', 'args': {}}, B(), ignore=True)
        assert r['error'] == 'timeout'
