# -*- coding: utf-8 -*-
"""设置器（移植精简自 DrissionPage © g1879 _units/setter.py，https://DrissionPage.cn
—— 遵循其许可：仅限个人学习与合法非盈利用途，商用需上游授权）。
改造点：
- 删 SessionPageSetter 全家（headers/proxies/auth/hooks/verify/cert/stream 等 requests 会话设置，
  session 层已随设计砍除）；headers/user_agent 走 CDP；
- 删 show_trail（调试用鼠标轨迹 JS，约 120 行，非核心）；
- WindowSetter.hide/show 删除（依赖 M2 已砍除的 win32 窗口工具）；
- 文案中文内联。"""
from pathlib import Path
from time import sleep

from .cookies_setter import BrowserCookiesSetter, CookiesSetter
from .perm_setter import BrowserPermSetter
from .._functions.web import format_headers
from ..errors import ElementLostError, JavaScriptError


class BaseSetter(object):
    def __init__(self, owner):
        self._owner = owner

    def NoneElement_value(self, value=None, on_off=True):
        self._owner._none_ele_return_value = on_off
        self._owner._none_ele_value = value

    def retry_times(self, times):
        self._owner.retry_times = times

    def retry_interval(self, interval):
        self._owner.retry_interval = interval

    def download_path(self, path):
        if path is None:
            path = '.'
        self._owner._download_path = str(Path(path).resolve())


class BrowserSetter(BaseSetter):
    """浏览器级设置。"""

    def __init__(self, owner):
        super().__init__(owner)
        self._perm_setter = None
        self._cookies_setter = None

    @property
    def perm(self):
        if self._perm_setter is None:
            self._perm_setter = BrowserPermSetter(
                self._owner, None if self._owner._default_context else self._owner._context_id)
        return self._perm_setter

    @property
    def cookies(self):
        if self._cookies_setter is None:
            self._cookies_setter = BrowserCookiesSetter(self._owner)
        return self._cookies_setter

    @property
    def load_mode(self):
        return LoadMode(self._owner)

    def auto_handle_alert(self, on_off=True, accept=True):
        self._owner._auto_handle_alert = None if on_off is None else accept if on_off else 'close'

    def timeouts(self, base=None, page_load=None, script=None):
        if base is not None:
            self._owner.timeouts.base = base
        if page_load is not None:
            self._owner.timeouts.page_load = page_load
        if script is not None:
            self._owner.timeouts.script = script

    def download_path(self, path):
        super().download_path(path)
        self._owner._dl_mgr.set_path('browser', self._owner._download_path)

    def download_file_name(self, name=None, suffix=None):
        self._owner._dl_mgr.set_rename('browser', name, suffix)

    def when_download_file_exists(self, mode):
        types = {'rename': 'rename', 'overwrite': 'overwrite', 'skip': 'skip',
                 'r': 'rename', 'o': 'overwrite', 's': 'skip'}
        mode = types.get(mode, mode)
        if mode not in types:
            raise ValueError(f'参数值错误。mode 只能是 {", ".join(types.keys())}，当前是 {mode}')
        self._owner._dl_mgr.set_file_exists('browser', mode)


class ContextSetter(object):
    """BrowserContext 级设置：只有权限与 cookie（下载、窗口等属浏览器/页面级）。"""

    def __init__(self, owner):
        self._owner = owner
        self._perm_setter = None
        self._cookies_setter = None

    @property
    def perm(self):
        if self._perm_setter is None:
            self._perm_setter = BrowserPermSetter(
                self._owner, None if self._owner._default_context else self._owner._context_id)
        return self._perm_setter

    @property
    def cookies(self):
        if self._cookies_setter is None:
            self._cookies_setter = BrowserCookiesSetter(self._owner)
        return self._cookies_setter


class TabSetter(BaseSetter):
    """页面（Tab）级设置。"""

    def __init__(self, owner):
        super().__init__(owner)
        self._cookies_setter = None

    @property
    def window(self):
        return WindowSetter(self._owner)

    @property
    def cookies(self):
        if self._cookies_setter is None:
            self._cookies_setter = CookiesSetter(self._owner)
        return self._cookies_setter

    @property
    def scroll(self):
        return PageScrollSetter(self._owner.scroll)

    @property
    def load_mode(self):
        return LoadMode(self._owner)

    def timeout(self, second):
        self._owner.timeouts.base = second

    def timeouts(self, base=None, page_load=None, script=None):
        if base is not None:
            self._owner.timeouts.base = base
        if page_load is not None:
            self._owner.timeouts.page_load = page_load
        if script is not None:
            self._owner.timeouts.script = script

    def headers(self, headers):
        self._owner._enable_domain('Network')
        self._owner._run_cdp('Network.setExtraHTTPHeaders', headers=format_headers(headers))

    def user_agent(self, ua, platform=None):
        keys = {'userAgent': ua}
        if platform:
            keys['platform'] = platform
        self._owner._run_cdp('Emulation.setUserAgentOverride', **keys)

    def session_storage(self, item, value):
        self._storage(item, value, False)

    def local_storage(self, item, value):
        self._storage(item, value, True)

    def _storage(self, item, value, is_local):
        self._owner.wait.doc_loaded()
        self._owner._enable_domain('DOMStorage')
        i = self._owner._run_cdp('Storage.getStorageKeyForFrame',
                                 frameId=self._owner._frame_id)['storageKey']
        if value is False:
            self._owner._run_cdp('DOMStorage.removeDOMStorageItem',
                                 storageId={'storageKey': i, 'isLocalStorage': is_local}, key=item)
        else:
            self._owner._run_cdp('DOMStorage.setDOMStorageItem',
                                 storageId={'storageKey': i, 'isLocalStorage': is_local}, key=item, value=value)
        self._owner.wait.doc_loaded()
        self._owner._disable_domain('DOMStorage')

    def upload_files(self, files):
        if not self._owner._upload_list:
            self._owner._set_callback('Page.fileChooserOpened', self._owner._onFileChooserOpened)
            self._owner._run_cdp('Page.setInterceptFileChooserDialog', enabled=True)

        if isinstance(files, str):
            files = files.split('\n')
        elif isinstance(files, Path):
            files = (files,)
        self._owner._upload_list = [str(Path(i).resolve()) for i in files]

    def auto_handle_alert(self, on_off=True, accept=True):
        self._owner._alert.auto = None if on_off is None else accept if on_off else 'close'

    def blocked_urls(self, urls):
        if not urls:
            urls = []
        elif isinstance(urls, str):
            urls = (urls,)
        if not isinstance(urls, (list, tuple)):
            raise ValueError(f'参数类型错误。urls 应为 str、list 或 tuple，而不是 {urls!r}')
        self._owner._enable_domain('Network')
        self._owner._run_cdp('Network.setBlockedURLs', urls=list(urls))

    def download_path(self, path):
        super().download_path(path)
        self._owner.browser._dl_mgr.set_path(self._owner, self._owner._download_path)

    def download_file_name(self, name=None, suffix=None):
        self._owner.browser._dl_mgr.set_rename(self._owner.tab_id, name, suffix)

    def when_download_file_exists(self, mode):
        types = {'rename': 'rename', 'overwrite': 'overwrite', 'skip': 'skip',
                 'r': 'rename', 'o': 'overwrite', 's': 'skip'}
        mode = types.get(mode, mode)
        if mode not in types:
            raise ValueError(f'参数值错误。mode 只能是 {", ".join(types.keys())}，当前是 {mode}')
        self._owner.browser._dl_mgr.set_file_exists(self._owner.tab_id, mode)


class ElementSetter(object):
    def __init__(self, ele):
        self._ele = ele

    def attr(self, name, value=''):
        try:
            self._ele.owner._run_cdp('DOM.setAttributeValue',
                                     nodeId=self._ele._node_id, name=name, value=str(value))
        except ElementLostError:
            self._ele._refresh_id()
            self._ele.owner._run_cdp('DOM.setAttributeValue',
                                     nodeId=self._ele._node_id, name=name, value=str(value))

    def property(self, name, value):
        value = value.replace('"', r'\"')
        self._ele._run_js(f'this.{name}="{value}";')

    def style(self, name, value):
        try:
            self._ele._run_js(f'this.style.{name}="{value}";')
        except JavaScriptError:
            raise RuntimeError(f'设置失败：{name}')

    def innerHTML(self, html):
        self.property('innerHTML', html)

    def value(self, value):
        self.property('value', value)


class FrameSetter(TabSetter):
    """Frame 对象使用：attr/property/style 委托给 frame 元素。"""

    def attr(self, name, value=''):
        self._owner.frame_ele.set.attr(name, value)

    def property(self, name, value):
        self._owner.frame_ele.set.property(name=name, value=value)

    def style(self, name, value):
        self._owner.frame_ele.set.style(name=name, value=value)


class LoadMode(object):
    def __init__(self, owner):
        self._owner = owner

    def __call__(self, value):
        if value.lower() not in ('normal', 'eager', 'none'):
            raise ValueError(f'参数值错误。value 只能是 "normal"、"eager"、"none"，当前是 {value}')
        self._owner._load_mode = value

    def normal(self):
        self.__call__('normal')

    def eager(self):
        self.__call__('eager')

    def none(self):
        self.__call__('none')


class PageScrollSetter(object):
    def __init__(self, scroll):
        self._scroll = scroll

    def wait_complete(self, on_off=True):
        if not isinstance(on_off, bool):
            raise ValueError(f'参数类型错误。on_off 应为 bool，当前是 {type(on_off)}')
        self._scroll._wait_complete = on_off

    def smooth(self, on_off=True):
        if not isinstance(on_off, bool):
            raise ValueError(f'参数类型错误。on_off 应为 bool，当前是 {type(on_off)}')
        b = 'smooth' if on_off else 'auto'
        self._scroll._owner._run_js(f'document.documentElement.style.setProperty("scroll-behavior","{b}");')
        self._scroll._wait_complete = on_off


class WindowSetter(object):
    def __init__(self, owner):
        self._owner = owner
        self._window_id = self._get_info()['windowId']

    def max(self):
        s = self._get_info()['bounds']['windowState']
        if s in ('fullscreen', 'minimized'):
            self._perform({'windowState': 'normal'})
        self._perform({'windowState': 'maximized'})

    def mini(self):
        s = self._get_info()['bounds']['windowState']
        if s == 'fullscreen':
            self._perform({'windowState': 'normal'})
        self._perform({'windowState': 'minimized'})

    def full(self):
        s = self._get_info()['bounds']['windowState']
        if s == 'minimized':
            self._perform({'windowState': 'normal'})
        self._perform({'windowState': 'fullscreen'})

    def normal(self):
        s = self._get_info()['bounds']['windowState']
        if s == 'fullscreen':
            self._perform({'windowState': 'normal'})
        self._perform({'windowState': 'normal'})

    def size(self, width=None, height=None):
        if width or height:
            s = self._get_info()['bounds']['windowState']
            if s != 'normal':
                self._perform({'windowState': 'normal'})
            info = self._get_info()['bounds']
            width = width - 16 if width else info['width']
            height = height + 7 if height else info['height']
            self._perform({'width': width, 'height': height})

    def location(self, x=None, y=None):
        if x is not None or y is not None:
            self.normal()
            info = self._get_info()['bounds']
            x = x if x is not None else info['left']
            y = y if y is not None else info['top']
            self._perform({'left': x - 8, 'top': y})

    def _get_info(self):
        for _ in range(50):
            try:
                return self._owner._run_cdp('Browser.getWindowForTarget')
            except Exception:
                sleep(.02)
        raise RuntimeError('获取窗口信息失败。')

    def _perform(self, bounds):
        try:
            self._owner._run_cdp('Browser.setWindowBounds', windowId=self._window_id, bounds=bounds)
        except Exception:
            raise RuntimeError('设置窗口失败，请先把窗口设置为 normal 状态。')
