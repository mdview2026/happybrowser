# -*- coding: utf-8 -*-
"""cookie 设置器（移植精简自 DrissionPage © g1879 _units/cookies_setter.py，https://DrissionPage.cn
—— 遵循其许可：仅限个人学习与合法非盈利用途，商用需上游授权）。
改造点：删 SessionCookiesSetter 与 ChromiumTabCookiesSetter 的 d/s 双模式分支
（session 层已随设计砍除，Tab 直用 CookiesSetter）。"""
from .._functions.cookies import set_tab_cookies, set_browser_cookies


class BrowserCookiesSetter(object):
    def __init__(self, owner):
        self._owner = owner

    def __call__(self, cookies):
        set_browser_cookies(self._owner, cookies)

    def clear(self):
        if self._owner._default_context:
            self._owner._run_cdp('Storage.clearCookies')
        else:
            self._owner._run_cdp('Storage.clearCookies', browserContextId=self._owner._context_id)


class CookiesSetter(BrowserCookiesSetter):
    def __call__(self, cookies):
        set_tab_cookies(self._owner, cookies)

    def remove(self, name, url=None, domain=None, path=None):
        d = {'name': name}
        if url is not None:
            d['url'] = url
        if domain is not None:
            d['domain'] = domain
        if not url and not domain:
            d['url'] = self._owner.url
            if not d['url'].startswith('http'):
                raise ValueError('请指定 url 或 domain。')
        if path is not None:
            d['path'] = path
        self._owner._run_cdp('Network.deleteCookies', **d)

    def clear(self):
        self._owner._run_cdp('Network.clearBrowserCookies')
