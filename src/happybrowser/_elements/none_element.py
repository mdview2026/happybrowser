# -*- coding: utf-8 -*-
"""空元素对象（移植精简自 DrissionPage © g1879 _elements/none_element.py，https://DrissionPage.cn
—— 遵循其许可：仅限个人学习与合法非盈利用途，商用需上游授权）。
改造点：parsed 回显适配「? 前缀可省略」（parse_loc 接受裸 DSL 字符串与 Q 对象）；
链式属性列表对齐 happybrowser API（删 sr/s_ele/get_frame）。"""
from ..settings import Settings
from .._locate.query import Q, parse_loc
from ..errors import ElementNotFoundError

_CHAIN = ('ele', 'eles', 'parent', 'child', 'next', 'prev', 'before', 'after', 'east', 'north',
          'south', 'west', 'offset', 'over', 'children', 'nexts', 'prevs', 'befores', 'afters',
          'shadow_root')
_VALUE = ('size', 'link', 'css_selector', 'xpath', 'comments', 'texts', 'tag', 'html', 'inner_html',
          'attrs', 'text', 'raw_text', 'value', 'attr', 'style', 'src', 'property')


class NoneElement(object):
    """查找未命中时返回，支持链式取属性/方法，实际访问时抛出 ElementNotFoundError。"""

    def __init__(self, page=None, method=None, args=None):
        if method and Settings.raise_when_ele_not_found:  # 无传入 method 时不自动抛出，由调用者处理
            raise ElementNotFoundError(f'元素未找到：{method}() {args}')

        if page:
            self._none_ele_value = page._none_ele_value
            self._none_ele_return_value = page._none_ele_return_value
        else:
            self._none_ele_value = None
            self._none_ele_return_value = False
        self.method = method
        self.args = {} if args is None else args

    def __call__(self, *args, **kwargs):
        if not self._none_ele_return_value:
            raise ElementNotFoundError(f'元素未找到：{self.method}() {self.args}')
        return self

    def __repr__(self):
        arg_txt = ', '.join(f'{k}={v!r}' for k, v in self.args.items())
        return f'<NoneElement method={self.method}, {arg_txt}{self._parsed()}>'

    def _parsed(self):
        """查询定位符未命中时回显解析后的标准式，便于发现拼写错误。"""
        loc = self.args.get('locator')
        try:
            if isinstance(loc, (Q, str)):
                return f', parsed={parse_loc(loc).canonical()}'
        except Exception:
            pass
        return ''

    def __getattr__(self, item):
        if not self._none_ele_return_value:
            raise ElementNotFoundError(f'元素未找到：{self.method}() {self.args}')
        if item in _CHAIN:
            return self
        if item in _VALUE:
            return self._none_ele_value
        raise ElementNotFoundError(f'元素未找到：{self.method}() {self.args}')

    def __eq__(self, other):
        return other is None

    def __bool__(self):
        return False
