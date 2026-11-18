# -*- coding:utf-8 -*-
"""happybrowser：精简浏览器自动化库（仅 Chromium，仅 ? / Q 查询语法）。

内嵌改造自 DrissionPage（© g1879，https://DrissionPage.cn）的源码，
遵循其许可：仅限个人学习与合法非盈利用途，商用需上游授权。
"""
from ._locate.query import (Q, Op, QueryError, TagNode, AttrNode, TextNode, AndNode, OrNode, NotNode,
                            parse_query, parse_loc, explain_loc, xpath_literal, match_query,
                            canonical, to_query, to_xpath, is_pure_xpath)
from ._core.browser import Browser, BrowserContext
from ._core.options import Options, Timeout
from ._core.keys import Keys
from ._pages.tab import Tab
from ._pages.base import PageBase
from ._pages.frame import Frame
from ._elements.element import Element, ShadowRoot
from ._elements.none_element import NoneElement
from ._functions.elements import ElementsList
from ._functions.cookies import CookiesList
from .errors import (BaseError, ElementNotFoundError, AlertExistsError, ContextLostError, ElementLostError,
                     CDPError, PageDisconnectedError, JavaScriptError, NoRectError, BrowserConnectError,
                     NoResourceError, CanNotClickError, GetDocumentError, WaitTimeoutError, IncorrectURLError,
                     StorageError, CookieFormatError, TargetNotFoundError, LocatorError, UnknownError)
from .settings import Settings

# 版本号以 pyproject.toml 为单一来源：安装后读包元数据；直接跑源码树时回退到内置值
try:
    from importlib.metadata import PackageNotFoundError, version as _pkg_version
    __version__ = _pkg_version('happybrowser')
except PackageNotFoundError:
    __version__ = '0.2.1'

__all__ = [
                            'AlertExistsError',
                            'AndNode',
                            'AttrNode',
                            'BaseError',
                            'Browser',
                            'BrowserConnectError',
                            'BrowserContext',
                            'CDPError',
                            'CanNotClickError',
                            'ContextLostError',
                            'CookieFormatError',
                            'CookiesList',
                            'Element',
                            'ElementLostError',
                            'ElementNotFoundError',
                            'ElementsList',
                            'Frame',
                            'GetDocumentError',
                            'IncorrectURLError',
                            'JavaScriptError',
                            'Keys',
                            'LocatorError',
                            'NoRectError',
                            'NoResourceError',
                            'NoneElement',
                            'NotNode',
                            'Op',
                            'Options',
                            'OrNode',
                            'PageBase',
                            'PageDisconnectedError',
                            'Q',
                            'QueryError',
                            'Settings',
                            'ShadowRoot',
                            'StorageError',
                            'Tab',
                            'TagNode',
                            'TargetNotFoundError',
                            'TextNode',
                            'Timeout',
                            'UnknownError',
                            'WaitTimeoutError',
                            '__version__',
                            'canonical',
                            'explain_loc',
                            'is_pure_xpath',
                            'match_query',
                            'parse_loc',
                            'parse_query',
                            'to_query',
                            'to_xpath',
                            'xpath_literal',
]
