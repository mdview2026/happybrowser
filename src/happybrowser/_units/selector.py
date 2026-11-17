# -*- coding: utf-8 -*-
"""下拉选择框（移植精简自 DrissionPage © g1879 _units/selector.py，https://DrissionPage.cn
—— 遵循其许可：仅限个人学习与合法非盈利用途，商用需上游授权）。
改造点：12 个 cancel_by_* 镜像方法收敛为 by_*(..., cancel=False)——
原 cancel_by_text(v) 等价于 by_text(v, cancel=True)；定位符吃 DSL（'option' 即标签）。"""
from .._core.tools import wait_until


class SelectElement(object):
    def __init__(self, ele):
        if ele.tag != 'select':
            raise TypeError('只能对 select 元素使用此对象')
        self._ele = ele

    def __call__(self, text_or_index, timeout=None):
        para_type = 'index' if isinstance(text_or_index, int) else 'text'
        if timeout is None:
            timeout = self._ele.timeout
        return self._select(text_or_index, para_type, timeout=timeout)

    @property
    def is_multi(self):
        return self._ele.attr('multiple') is not None

    @property
    def options(self):
        return self._ele.eles('option')

    @property
    def selected_option(self):
        return self._ele._run_js('return this.options[this.selectedIndex];')

    @property
    def selected_options(self):
        return [x for x in self.options if x.states.is_selected]

    def all(self):
        if not self.is_multi:
            raise TypeError('只能对多选下拉框使用此方法')
        return self._by_loc('option', 1, False)

    def invert(self):
        if not self.is_multi:
            raise TypeError('只能对多选下拉框使用此方法')
        change = False
        for i in self.options:
            change = True
            mode = 'false' if i.states.is_selected else 'true'
            i._run_js(f'this.selected={mode};')
        if change:
            self._dispatch_change()
        return self._ele

    def clear(self):
        if not self.is_multi:
            raise TypeError('只能对多选下拉框使用此方法')
        return self._by_loc('option', 1, True)

    def by_text(self, text, timeout=None, cancel=False):
        return self._select(text, 'text', cancel, timeout)

    def by_value(self, value, timeout=None, cancel=False):
        return self._select(value, 'value', cancel, timeout)

    def by_index(self, index, timeout=None, cancel=False):
        return self._select(index, 'index', cancel, timeout)

    def by_locator(self, locator, timeout=None, cancel=False):
        return self._by_loc(locator, timeout, cancel)

    def by_option(self, option, cancel=False):
        return self._select_options(option, 'false' if cancel else 'true')

    def _by_loc(self, loc, timeout=None, cancel=False):
        eles = self._ele.eles(loc, timeout)
        if not eles:
            raise RuntimeError('没有找到符合条件的 option 元素')

        mode = 'false' if cancel else 'true'
        if not self.is_multi:
            eles = eles[0]
        return self._select_options(eles, mode)

    def _select(self, condition, para_type='text', cancel=False, timeout=None):
        if not self.is_multi and isinstance(condition, (list, tuple)):
            raise TypeError('单选下拉框只能接收字符串或数字')

        mode = 'false' if cancel else 'true'
        if timeout is None:
            timeout = self._ele.timeout
        condition = set(condition) if isinstance(condition, (list, tuple)) else {condition}

        if para_type in ('text', 'value'):
            return self._text_value([str(i) for i in condition], para_type, mode, timeout)
        if para_type == 'index':
            return self._index(condition, mode, timeout)
        return None

    def _text_value(self, condition, para_type, mode, timeout):
        def do_text():
            eles = [i for i in self.options if i.text in condition]
            return self._select_options(eles, mode) if len(eles) >= text_len else None

        def do_value():
            eles = [i for i in self.options if i.attr('value') in condition]
            return self._select_options(eles, mode) if len(eles) >= text_len else None

        text_len = len(condition)
        func = do_text if para_type == 'text' else do_value
        r = wait_until(func, timeout=timeout)
        if r is not None:
            return r
        raise RuntimeError('没有找到符合条件的 option 元素')

    def _index(self, condition, mode, timeout):
        condition = [int(i) for i in condition]
        text_len = abs(max(condition, key=abs))

        def do():
            if len(self.options) >= text_len:
                eles = self.options
                eles = [eles[i - 1] if i > 0 else eles[i] for i in condition]
                return self._select_options(eles, mode)
            return None

        r = wait_until(do, timeout=timeout)
        if r is not None:
            return r
        raise RuntimeError('没有找到符合条件的 option 元素')

    def _select_options(self, option, mode):
        if isinstance(option, (list, tuple, set)):
            if not self.is_multi and len(option) > 1:
                option = option[:1]
            for o in option:
                o._run_js(f'this.selected={mode};')
                self._dispatch_change()
        else:
            option._run_js(f'this.selected={mode};')
            self._dispatch_change()
        return self._ele

    def _dispatch_change(self):
        self._ele._run_js('this.dispatchEvent(new CustomEvent("change", {bubbles: true}));')
