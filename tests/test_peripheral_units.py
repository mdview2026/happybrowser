# -*- coding: utf-8 -*-
"""M5 单元测试：文件名工具 / cookie 格式化 / 监听器判定与数据包 / 设置器逻辑（假对象驱动）。"""

import pytest

from happybrowser._functions.files import make_valid_name, get_usable_path
from happybrowser._functions.cookies import (cookie_to_dict, cookies_to_tuple, format_cookie,
                                             _dict_cookies_to_tuple)
from happybrowser._units.listener import (CaseInsensitiveDict, is_target, MethodSetter, ResTypeSetter,
                                          sse2list, Request, Response)
from happybrowser._units.console import ConsoleData
from happybrowser._units.setter import LoadMode, PageScrollSetter
from happybrowser._units.downloader import TabDownloadSettings, DownloadMission, DownloadManager
from happybrowser._units.waiter import wait_mission


class TestMakeValidName:
    def test_plain(self):
        assert make_valid_name('a b.txt') == 'a b.txt'

    def test_illegal_chars_replaced(self):
        r = make_valid_name('a<b>c/d\\e|f:g*h?i\nj"k"')
        for c in '<>/\\|:*?\n"':
            assert c not in r
        assert '＜' in r and '＞' in r and '：' in r

    def test_long_name_truncated(self):
        r = make_valid_name('字' * 300 + '.txt')
        assert len(r) < 305 and r.endswith('.txt')

    def test_strip_and_dots(self):
        assert make_valid_name('  x.  ') == 'x'


class TestGetUsablePath:
    def test_no_conflict(self, tmp_path):
        p = get_usable_path(tmp_path / 'a.txt')
        assert p == tmp_path / 'a.txt'

    def test_conflict_appends_number(self, tmp_path):
        (tmp_path / 'a.txt').write_text('x')
        p = get_usable_path(tmp_path / 'a.txt')
        assert p.name == 'a_1.txt'
        p.touch()
        assert get_usable_path(tmp_path / 'a.txt').name == 'a_2.txt'

    def test_existing_number_increments(self, tmp_path):
        (tmp_path / 'b_1.txt').write_text('x')
        (tmp_path / 'b_2.txt').write_text('x')
        p = get_usable_path(tmp_path / 'b_1.txt')
        assert p.name == 'b_1_1.txt'  # 直接同名冲突时从 _1 重新起跳


class TestCookieToDict:
    def test_dict_passthrough(self):
        d = {'name': 'n', 'value': 'v'}
        assert cookie_to_dict(d) is d

    def test_str_kv(self):
        # 上游算法：多段 name=value 逐段覆盖，最终只保留最后一段的 name/value
        d = cookie_to_dict('n1=v1; n2=v2')
        assert d['name'] == 'n2' and d['value'] == 'v2'

    def test_str_with_attrs(self):
        # 含 ',' 的串按逗号切分（上游行为，expires 日期会被截断），此处只验证无逗号场景
        d = cookie_to_dict('n1=v1; path=/; secure')
        assert d['name'] == 'n1' and d['path'] == '/' and d['secure'] == ''

    def test_bad_type(self):
        with pytest.raises(ValueError):
            cookie_to_dict(123)


class TestCookiesToTuple:
    def test_dict_single(self):
        assert cookies_to_tuple({'name': 'n', 'value': 'v'}) == ({'name': 'n', 'value': 'v'},)

    def test_dict_multi_with_template(self):
        r = cookies_to_tuple({'domain': '.x.com', 'a': '1', 'b': '2'})
        assert r == ({'name': 'a', 'value': '1', 'domain': '.x.com'},
                     {'name': 'b', 'value': '2', 'domain': '.x.com'})

    def test_str(self):
        r = cookies_to_tuple('a=1; b=2')
        assert [c['name'] for c in r] == ['a', 'b']

    def test_list_of_dicts(self):
        r = cookies_to_tuple([{'name': 'a', 'value': '1'}])
        assert r == ({'name': 'a', 'value': '1'},)

    def test__dict_single(self):
        assert _dict_cookies_to_tuple({'name': 'a', 'value': 1}) == ({'name': 'a', 'value': 1},)


class TestFormatCookie:
    def test_expiry_to_expires(self):
        c = format_cookie({'name': 'n', 'value': 'v', 'expiry': 100.5})
        assert c['expires'] == 100 and 'expiry' not in c

    def test_expires_str_date(self):
        from datetime import datetime
        expect = datetime.strptime('Thu, 01 Jan 2026 00:00:00 GMT', '%a, %d %b %Y %H:%M:%S GMT').timestamp()
        c = format_cookie({'name': 'n', 'value': 'v', 'expires': 'Thu, 01 Jan 2026 00:00:00 GMT'})
        assert c['expires'] == pytest.approx(expect, abs=2)  # 本地时区解析（上游行为）

    def test_expires_str_numeric(self):
        c = format_cookie({'name': 'n', 'value': 'v', 'expires': '123'})
        assert c['expires'] == 123

    def test_host_prefix(self):
        c = format_cookie({'name': '__Host-a', 'value': 'v'})
        assert c['path'] == '/' and c['secure'] is True

    def test_secure_prefix(self):
        c = format_cookie({'name': '__Secure-a', 'value': 'v'})
        assert c['secure'] is True

    def test_same_site_invalid_removed(self):
        c = format_cookie({'name': 'n', 'value': 'v', 'sameSite': 'bogus'})
        assert 'sameSite' not in c
        c = format_cookie({'name': 'n', 'value': 'v', 'sameSite': 'Lax'})
        assert c['sameSite'] == 'Lax'

    def test_priority_invalid_raises(self):
        with pytest.raises(ValueError):
            format_cookie({'name': 'n', 'value': 'v', 'priority': 'Huge'})

    def test_source_scheme_invalid_raises(self):
        with pytest.raises(ValueError):
            format_cookie({'name': 'n', 'value': 'v', 'sourceScheme': 'Maybe'})

    def test_value_coerced(self):
        c = format_cookie({'name': 'n', 'value': None})
        assert c['value'] == ''
        c = format_cookie({'name': 'n', 'value': 5})
        assert c['value'] == '5'


class TestCaseInsensitiveDict:
    def test_case_access(self):
        d = CaseInsensitiveDict({'Content-Type': 'a'})
        assert d['content-type'] == 'a'
        assert d.get('CONTENT-TYPE') == 'a'
        assert 'content-TYPE' in d

    def test_overwrite_canonical(self):
        d = CaseInsensitiveDict()
        d['X-A'] = '1'
        d['x-a'] = '2'
        assert len(d) == 1 and d['X-A'] == '2'

    def test_pop(self):
        d = CaseInsensitiveDict({'A': 1})
        assert d.pop('a') == 1
        assert 'a' not in d
        assert d.pop('zzz', 'dft') == 'dft'


class FakeListener:
    def __init__(self, urls=True, method=None, res_type=True, is_regex=False):
        self._urls = {urls} if isinstance(urls, str) else urls  # 与真 Listener set_urls 一致：字符串包集合
        self._method = method if method is not None else {'GET', 'POST'}
        self._res_type = res_type
        self._is_regex = is_regex
        self.listening = False


class TestIsTarget:
    def test_urls_true(self):
        l = FakeListener()
        assert is_target(l, 'http://a.com/x', 'GET', 'XHR')

    def test_method_filter(self):
        l = FakeListener(method={'POST'})
        assert not is_target(l, 'http://a.com/x', 'GET', 'XHR')
        assert is_target(l, 'http://a.com/x', 'POST', 'XHR')

    def test_res_type_filter(self):
        l = FakeListener(res_type={'XHR'})
        assert not is_target(l, 'http://a.com/x', 'GET', 'Document')
        assert is_target(l, 'http://a.com/x', 'GET', 'XHR')

    def test_substring_match(self):
        l = FakeListener(urls='api')
        assert is_target(l, 'http://a.com/api/data', 'GET', 'XHR')
        assert not is_target(l, 'http://a.com/static/x', 'GET', 'XHR')

    def test_regex_match(self):
        l = FakeListener(urls=r'api/\d+', is_regex=True)
        assert is_target(l, 'http://a.com/api/123', 'GET', 'XHR')
        assert not is_target(l, 'http://a.com/api/abc', 'GET', 'XHR')


class TestSetters:
    def test_method_setter_add_only(self):
        l = FakeListener()
        MethodSetter(l).POST().PUT(only=False)
        assert l._method == {'GET', 'POST', 'PUT'}

    def test_method_setter_only(self):
        l = FakeListener()
        MethodSetter(l).DELETE(only=True)
        assert l._method == {'DELETE'}

    def test_method_setter_remove_from_all(self):
        l = FakeListener(method=True)
        MethodSetter(l).remove_GET()
        assert 'GET' not in l._method and l._method is not True

    def test_res_type_ws_sse(self):
        l = FakeListener(res_type={'XHR'})
        ResTypeSetter(l).ws()
        assert l._res_type == {'XHR', 'WebSocket'}
        ResTypeSetter(l).remove_ws()
        assert l._res_type == {'XHR'}

    def test_unknown_method_raises(self):
        l = FakeListener()
        with pytest.raises(ValueError):
            MethodSetter(l).BOGUS()

    def test_load_mode_valid_and_invalid(self):
        class O:
            _load_mode = None
        m = LoadMode(O())
        m('eager')
        assert m._owner._load_mode == 'eager'
        m.normal()
        assert m._owner._load_mode == 'normal'
        with pytest.raises(ValueError):
            m('fast')

    def test_scroll_setter_type_check(self):
        class FakeScroll:
            _wait_complete = False

            class _owner:
                @staticmethod
                def _run_js(js):
                    return None
        ps = PageScrollSetter(FakeScroll())
        ps.wait_complete(True)
        assert FakeScroll._wait_complete is True or ps._scroll._wait_complete is True
        with pytest.raises(ValueError):
            ps.smooth('yes')


class TestDownloadParts:
    def test_tab_settings_singleton(self):
        TabDownloadSettings('browser')  # browser 项须先存在，其余 tab 从它取默认
        a = TabDownloadSettings('tX')
        b = TabDownloadSettings('tX')
        assert a is b
        TabDownloadSettings.TABS.pop('tX', None)

    def test_mission_rate(self):
        m = object.__new__(DownloadMission)
        m.received_bytes, m.total_bytes = 50, 200
        assert m.rate == 25.0
        m.total_bytes = 0
        assert m.rate is None

    def test_wait_mission_returns_mission(self):
        class FakeDL:
            flags = {'t1': 'mission-obj'}

            def get_flag(self, tid):
                return self.flags.get(tid)

            def set_flag(self, tid, v):
                self.flags[tid] = v

        class FakeBrowser:
            _messenger_running = True
            _dl_mgr = FakeDL()

        assert wait_mission(FakeBrowser(), 't1', 1) == 'mission-obj'
        assert FakeBrowser._dl_mgr.flags['t1'] is None  # 用后清旗标

    def test_mgr_clear_tab_info(self):
        class FakeBrowser:
            download_path = '.'
            _run_cdp = staticmethod(lambda *a, **k: {})
        mgr = DownloadManager(FakeBrowser())
        mgr._tab_missions['t9'] = set()
        mgr._flags['t9'] = True
        TabDownloadSettings.TABS['t9'] = 1
        mgr.clear_tab_info('t9')
        assert 't9' not in mgr._tab_missions and 't9' not in mgr._flags
        assert 't9' not in TabDownloadSettings.TABS


class TestPackets:
    def test_sse2list(self):
        raw = 'event: aa\ndata: {"x":1}\n\nevent: bb\ndata: plain'
        r = sse2list(raw)
        assert r[0] == {'event': 'aa', 'data': {'x': 1}}
        assert r[1] == {'event': 'bb', 'data': 'plain'}

    def test_request_post_data_json(self):
        dp = object.__new__(Request)
        dp.__dict__.update({'_data_packet': None, '_request': {'url': 'http://a/?x=1'},
                            '_raw_post_data': '{"k": 1}', '_postData': None, '_headers': None})
        assert dp.postData == {'k': 1}
        assert dp.params == {'x': '1'}

    def test_request_post_data_entries(self):
        dp = Request(None, {'postDataEntries': [{'bytes': 'eyJhIjogMn0='}]}, None)
        assert dp.postData == {'a': 2}

    def test_response_body_sse(self):
        resp = Response(None, {'headers': {'content-type': 'text/event-stream'}},
                        'event: e\ndata: hi', False, 0)
        assert resp.body == [{'event': 'e', 'data': 'hi'}]

    def test_response_body_base64(self):
        from base64 import b64encode
        raw = b64encode('你好'.encode()).decode()
        resp = Response(None, {'headers': {}}, raw, True, 0)
        assert resp.body == '你好'.encode()

    def test_console_data_getattr(self):
        d = ConsoleData({'level': 'warning', 'text': 'w'})
        assert d.level == 'warning' and d.text == 'w'
        assert d.nope is None
