# -*- coding: utf-8 -*-
"""M4 单元测试：keys 数据构造 / keys_to_typing / SelectElement 逻辑（假元素驱动）。"""
import pytest

from happybrowser import Keys
from happybrowser._core.keys import make_input_data, keys_to_typing, modifierBit
from happybrowser._units.selector import SelectElement
from happybrowser.errors import AlertExistsError


class TestMakeInputData:
    def test_plain_char(self):
        d = make_input_data(0, 'a', False)
        assert d['key'] == 'a' and d['text'] == 'a'
        assert d['code'] == 'KeyA' and d['windowsVirtualKeyCode'] == 65
        assert d['type'] == 'keyDown'

    def test_enter_has_text(self):
        d = make_input_data(0, Keys.ENTER, False)
        assert d['type'] == 'keyDown' and d['text'] == '\r'

    def test_enter_up(self):
        d = make_input_data(0, Keys.ENTER, True)
        assert d['type'] == 'keyUp'

    def test_shift_keypad_uses_shift_key(self):
        d = make_input_data(8, Keys.NUMPAD1, False)
        # fork 行为：shiftKey 命中 key='!'，但随后单字符覆盖逻辑把 text 写回 data['key']
        assert d['key'] == '!' and d['text'] == '1' and d['windowsVirtualKeyCode'] == 49

    def test_control_clears_text(self):
        d = make_input_data(2, 'a', False)
        assert d['text'] == '' and d['type'] == 'rawKeyDown'

    def test_ignore_alert(self):
        assert make_input_data(0, 'a', False)['_ignore'] is AlertExistsError

    def test_unknown_key_none(self):
        assert make_input_data(0, '中', False) is None


class TestKeysToTyping:
    def test_plain(self):
        assert keys_to_typing(('abc',)) == (0, 'abc')

    def test_numbers(self):
        assert keys_to_typing((1, 2.5)) == (0, '12.5')

    def test_modifier_tuple(self):
        modifier, text = keys_to_typing(Keys.CTRL_A)
        assert modifier == modifierBit[Keys.CTRL] == 2
        assert text == 'a'

    def test_mixed(self):
        modifier, text = keys_to_typing((Keys.SHIFT, 'A', 'b'))
        assert modifier == 8 and text == 'Ab'


class FakeOpt:
    def __init__(self, text, value):
        self.text = text
        self._value = value
        self.js = []

        class _S:
            is_selected = False
        self.states = _S()  # 每个选项独立 states，避免类属性共享串味

    def attr(self, name):
        return self._value if name == 'value' else None

    def _run_js(self, js):
        self.js.append(js)


class FakeSelectEle:
    tag = 'select'

    def __init__(self, options, multiple=False):
        self._options = options
        self._multiple = multiple
        self.timeout = .2
        self.js = []

    def attr(self, name):
        return 'multiple' if (name == 'multiple' and self._multiple) else None

    def eles(self, loc, timeout=None):
        assert loc == 'option'
        return list(self._options)

    def _run_js(self, js):
        self.js.append(js)

    def sync_states(self):
        for o in self._options:
            o.states.is_selected = (any('selected=true' in j for j in o.js)
                                    and not any('selected=false' in j for j in o.js))


@pytest.fixture
def single():
    ele = FakeSelectEle([FakeOpt('one', 'v1'), FakeOpt('two', 'v2'), FakeOpt('three', 'v3')])
    return ele, SelectElement(ele)


@pytest.fixture
def multi():
    ele = FakeSelectEle([FakeOpt('ma', 'm1'), FakeOpt('mb', 'm2'), FakeOpt('mc', 'm3')], multiple=True)
    return ele, SelectElement(ele)


class TestSelectElement:
    def test_not_select_raises(self):
        class FakeDiv:
            tag = 'div'
        with pytest.raises(TypeError):
            SelectElement(FakeDiv())

    def test_by_text(self, single):
        ele, sel = single
        r = sel.by_text('two')
        assert r is ele
        assert any('selected=true' in j for j in ele._options[1].js)
        assert not any('selected=true' in j for j in ele._options[0].js)

    def test_by_value_and_index(self, single):
        ele, sel = single
        sel.by_value('v3')
        assert any('selected=true' in j for j in ele._options[2].js)
        sel.by_index(1)
        assert any('selected=true' in j for j in ele._options[0].js)

    def test_call_dispatch(self, single):
        ele, sel = single
        sel(2)
        assert any('selected=true' in j for j in ele._options[1].js)
        assert any('CustomEvent' in j for j in ele.js)  # change 事件已派发

    def test_cancel_param(self, multi):
        ele, sel = multi
        sel.all()
        sel.by_text('ma', cancel=True)
        ele.sync_states()
        assert [o.text for o in ele._options if o.states.is_selected] == ['mb', 'mc']

    def test_multi_list_select(self, multi):
        ele, sel = multi
        sel.by_text(['ma', 'mc'])
        ele.sync_states()
        assert sorted(o.text for o in ele._options if o.states.is_selected) == ['ma', 'mc']

    def test_single_rejects_list(self, single):
        _, sel = single
        with pytest.raises(TypeError):
            sel.by_text(['one', 'two'])

    def test_multi_only_methods(self, single):
        _, sel = single
        with pytest.raises(TypeError):
            sel.all()

    def test_not_found_raises(self, single):
        _, sel = single
        with pytest.raises(RuntimeError):
            sel.by_text('nope', timeout=.1)

    def test_is_multi(self, single, multi):
        assert not single[1].is_multi
        assert multi[1].is_multi
