# -*- coding: utf-8 -*-
"""M3 单元测试：NoneElement / 查找机械纯逻辑部分（make_js / _pick / make_eles / convert_argument）/
waiter 状态机（假元素驱动）。浏览器相关行为见 test_find_integration.py。"""
import pytest

from happybrowser import Settings, NoneElement, ElementsList
from happybrowser._elements.element import (Element, _pick, make_js_for_find_ele_by_xpath,
                                            convert_argument, make_eles, _do_find_perform_search)
from happybrowser.errors import ElementNotFoundError


class TestNoneElement:
    def test_bool_and_eq(self):
        ne = NoneElement()
        assert not ne
        assert ne == None
        assert ne != 'x'

    def test_parsed_echo(self):
        ne = NoneElement(method='ele()', args={'locator': 'buttton'})
        assert ne._parsed() == ', parsed=tag="buttton"'
        ne2 = NoneElement(method='ele()', args={'locator': '?#login && .btn'})
        assert 'parsed=' in ne2._parsed() and 'id="login"' in ne2._parsed()
        assert 'parsed=' in repr(ne)

    def test_parsed_bad_locator_silent(self):
        ne = NoneElement(method='ele()', args={'locator': 123})
        assert ne._parsed() == ''

    def test_chain_raises(self):
        ne = NoneElement(method='ele()', args={'locator': 'x'})
        with pytest.raises(ElementNotFoundError):
            ne.click()
        with pytest.raises(ElementNotFoundError):
            ne()

    def test_chain_returns_self_when_configured(self):
        class FakePage:
            _none_ele_value = 'SENTINEL'
            _none_ele_return_value = True
        ne = NoneElement(FakePage(), method='ele()', args={'locator': 'x'})
        assert ne.ele('.btn') is ne
        assert ne.tag == 'SENTINEL'
        assert ne() is ne

    def test_raise_when_ele_not_found(self):
        Settings.set_raise_when_ele_not_found(True)
        try:
            with pytest.raises(ElementNotFoundError):
                NoneElement(method='ele()', args={'locator': 'x'})
        finally:
            Settings.set_raise_when_ele_not_found(False)


class TestPick:
    def test_none(self):
        eles = ['a', 'b', 'c']
        assert _pick(eles, None) is eles

    def test_positive(self):
        assert _pick(['a', 'b', 'c'], 1) == 'a'
        assert _pick(['a', 'b', 'c'], 3) == 'c'
        assert _pick(['a', 'b', 'c'], 4) is None

    def test_negative(self):
        assert _pick(['a', 'b', 'c'], -1) == 'c'
        assert _pick(['a', 'b', 'c'], -3) == 'a'
        assert _pick(['a', 'b', 'c'], -4) is None

    def test_zero_as_one(self):
        assert _pick(['a', 'b'], 0) == 'a'

    def test_empty(self):
        assert _pick([], 1) is None
        assert _pick([], -1) is None


class TestMakeJs:
    def test_single(self):
        js = make_js_for_find_ele_by_xpath(".//*[@id='x']", '9')
        assert js == ("function(){let e=document.evaluate('.//*[@id=\\'x\\']',this,null,9,null);"
                      "if(e.singleNodeValue==null){return null;}return e.singleNodeValue;}")

    def test_multi(self):
        js = make_js_for_find_ele_by_xpath('.//*', '7', 'this.contentDocument')
        assert "document.evaluate('.//*',this.contentDocument,null,7,null)" in js
        assert 'snapshotLength' in js

    def test_quote_escaped(self):
        js = make_js_for_find_ele_by_xpath(""".//*[@id="a'b"]""", '9')
        assert "\\'" in js and 'a\\\'b' in js


class TestConvertArgument:
    def test_primitives(self):
        assert convert_argument(3) == {'value': 3}
        assert convert_argument('s') == {'value': 's'}
        assert convert_argument({'k': 1}) == {'value': {'k': 1}}
        assert convert_argument([1, 2]) == {'value': [1, 2]}
        assert convert_argument((1, 2)) == {'value': [1, 2]}  # tuple 转 list
        assert convert_argument(float('inf')) == {'unserializableValue': 'Infinity'}
        assert convert_argument(float('-inf')) == {'unserializableValue': '-Infinity'}

    def test_element(self):
        e = object.__new__(Element)
        e._obj_id = 'o1'
        assert convert_argument(e) == {'objectId': 'o1'}

    def test_unsupported(self):
        with pytest.raises(TypeError):
            convert_argument(object())


class FakePage:
    """按脚本应答 CDP 调用的假页面。nodes: id → nodeName（默认 DIV）。"""

    def __init__(self, nodes=None, script=None):
        self.nodes = nodes or {}
        self.script = script or {}
        self.calls = []

    def _run_cdp(self, cmd, _ignore=None, _timeout=None, **kw):
        self.calls.append((cmd, kw))
        if cmd in self.script:
            r = self.script[cmd]
            return r(kw) if callable(r) else r
        if cmd == 'DOM.describeNode':
            key = next(v for k, v in kw.items() if k in ('nodeId', 'objectId', 'backendNodeId'))
            return {'node': {'nodeName': self.nodes.get(key, 'DIV'),
                             'nodeId': 'n1', 'backendNodeId': 'b1'}}
        if cmd == 'DOM.resolveNode':
            return {'object': {'objectId': 'o1'}}
        return {}


class TestMakeEles:
    def test_single_positive(self):
        page = FakePage()
        e = make_eles(page, ['o1', 'o2'], 1, 'obj_id')
        assert isinstance(e, Element)

    def test_single_negative(self):
        page = FakePage()
        assert isinstance(make_eles(page, ['o1', 'o2'], -1, 'obj_id'), Element)

    def test_index_out_of_range(self):
        assert make_eles(FakePage(), ['o1'], 2, 'obj_id') is None
        assert make_eles(FakePage(), ['o1'], -2, 'obj_id') is None

    def test_all_list_and_text_skip(self):
        page = FakePage(nodes={'o2': '#text'})
        r = make_eles(page, ['o1', 'o2', 'o3'], None, 'obj_id')
        assert isinstance(r, ElementsList) and len(r) == 2

    def test_bad_id_false(self):
        assert make_eles(FakePage(), [0], None, 'node_id') is False

    def test_describe_error_false(self):
        page = FakePage(script={'DOM.describeNode': {'error': {'message': 'x'}}})
        assert make_eles(page, ['o1'], 1, 'obj_id') is False


class TestPerformSearch:
    def test_full_fetch_and_index(self):
        page = FakePage(script={
            'DOM.performSearch': {'searchId': 's1', 'resultCount': 3},
            'DOM.getSearchResults': lambda kw: {'nodeIds': [10, 11, 12]},
        })
        e = _do_find_perform_search(page, '//*', 2)
        assert isinstance(e, Element)
        discards = [c for c in page.calls if c[0] == 'DOM.discardSearchResults']
        assert discards and discards[0][1]['searchId'] == 's1'
        gets = [c for c in page.calls if c[0] == 'DOM.getSearchResults'][0][1]
        assert gets['fromIndex'] == 0 and gets['toIndex'] == 3  # 全量取回后索引

    def test_empty(self):
        page = FakePage(script={'DOM.performSearch': {'searchId': 's1', 'resultCount': 0}})
        assert _do_find_perform_search(page, '//*', 1) is None

    def test_count_lt_index(self):
        page = FakePage(script={'DOM.performSearch': {'searchId': 's1', 'resultCount': 2}})
        assert _do_find_perform_search(page, '//*', 3) is None


class TestWaiterFakes:
    def _fake_ele(self, displayed=True, enabled=True, alive=True, clickable=True):
        class S:
            is_displayed = displayed
            is_enabled = enabled
            is_alive = alive
            is_clickable = clickable
        class E:
            states = S()
            timeout = .2
        return E()

    def test_displayed(self):
        from happybrowser._units.waiter import ElementWaiter
        e = self._fake_ele()
        assert ElementWaiter(e).displayed() is e

    def test_hidden_timeout_false(self):
        from happybrowser._units.waiter import ElementWaiter
        e = self._fake_ele(displayed=True)
        assert ElementWaiter(e).hidden(timeout=.1) is False

    def test_disabled(self):
        from happybrowser._units.waiter import ElementWaiter
        e = self._fake_ele(enabled=False)
        assert ElementWaiter(e).disabled() is e

    def test_deleted(self):
        from happybrowser._units.waiter import ElementWaiter
        e = self._fake_ele(alive=False)
        assert ElementWaiter(e).deleted() is True


def test_elements_list():
    class E:
        tag = 'div'
        text = 'hi'
    lst = ElementsList(owner='pg', iterable=[E()])
    assert lst.owner == 'pg'
    assert lst.texts == ['hi']
    assert lst.tags == ['div']
    assert 'ElementsList' in repr(lst)
