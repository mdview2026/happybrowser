# -*- coding:utf-8 -*-
"""浏览器对象：启动/接管、Tab 注册表、生命周期（移植改造自 DrissionPage © g1879
_browsers/chromium.py + chromium_context.py，https://DrissionPage.cn —— 遵循其许可：
仅限个人学习与合法非盈利用途，商用需上游授权）。

接管语义三原则（happybrowser 行为修复，替代 fork 的"配置不符即自杀重启"）：
1. 端口活着 → 无条件纯接管：绝不比对 UA/无头状态/参数，绝不 quit 重启
2. is_headless() 按事实探测（/json/version UA），不按启动参数推断
3. Browser.launch(opts) 显式启动模式：端口被占即报错，不静默降级为接管

其他改造：去除 d/s 双模式与 session 相关逻辑；M5 接入 units 挂件
（set/listen/wait/_dl_mgr），cookies() 返回 CookiesList。"""
from pathlib import Path
from re import match
from threading import Lock
from time import sleep

from requests import Session

from ..errors import BrowserConnectError, CDPError, IncorrectURLError, PageDisconnectedError
from ..settings import Settings
from .._functions.cookies import CookiesList
from .._pages.tab import Tab
from .._units.downloader import DownloadManager
from .._units.listener import BrowserListener
from .._units.setter import BrowserSetter
from .._units.waiter import BrowserWaiter
from .driver import Driver, DebugDriver
from .launcher import connect_browser
from .messenger import Messenger
from .options import Options, Timeout
from .tools import ensure_del_dir, normalize_url, parse_proxy, wait_until


class Browser(Messenger):
    _BROWSERS = {}
    _lock = Lock()

    def __new__(cls, addr_or_opts=None, _mode='auto'):
        with cls._lock:
            opt = _handle_options(addr_or_opts)
            is_headless, browser_id, command_line, ws_only, driver, incognito, guest, exists = \
                _run_browser(opt, _mode)
            if browser_id in cls._BROWSERS:
                driver.stop() if driver is not None else None
                return cls._BROWSERS[browser_id]
            o = object.__new__(cls)
            o._driver = driver
            o._options = opt
            o._is_headless = is_headless
            o._incognito = incognito
            o._guest = guest
            o._ws_only = ws_only
            o._browser_id = browser_id
            o._is_exists = exists
            o._command_line = command_line
            cls._BROWSERS[browser_id] = o
            return o

    def __init__(self, addr_or_opts=None, _mode='auto'):
        if hasattr(self, '_created'):
            return
        self._created = True

        super().__init__()
        self._browser = self  # Messenger 兼容
        self._timeouts = Timeout(**vars(self._options.timeouts)) if not isinstance(
            self._options.timeouts, Timeout) else self._options.timeouts
        self._load_mode = self._options.load_mode
        self._download_path = str(Path(self._options.download_path).resolve()) if self._options.download_path else ''
        self.address = self._options.address
        self._ws_address = self._driver.address
        self._disconnect_flag = False
        self._event_handlers = {'Target.targetDestroyed': [self._onTargetDestroyed],
                                'Target.targetCreated': [self._onTargetCreated],
                                'Target.detachedFromTarget': [self._onDetachedFromTarget]}
        self._context = None
        self._tabs = Tabs()
        self._dl_mgr = DownloadManager(self)
        self._setter = None
        self._listener = None
        self._waiter = None
        self._driver.owner = self

        self._context_id = self._browser_id
        self._default_context = True
        self._start_messenger()
        self._driver.add_session_owner(None, self)
        self._driver.owner = self

        self.version = self._run_cdp('Browser.getVersion')['product']

        self._process_id = None
        try:
            r = self._run_cdp('SystemInfo.getProcessInfo')
            for i in r.get('processInfo', []):
                if i['type'] == 'browser':
                    self._process_id = i['id']
                    break
        except Exception:
            pass

        self._run_cdp('Target.setDiscoverTargets', discover=True)
        if self._options.proxy:
            self._tabs.set_proxy('main', parse_proxy(self._options.proxy))

    # ----------构造入口----------
    @classmethod
    def attach(cls, addr_or_opts=None):
        """只接管：地址上没有可接管的浏览器即抛 BrowserConnectError。"""
        return cls(addr_or_opts, _mode='attach')

    @classmethod
    def launch(cls, addr_or_opts=None):
        """只启动：端口已被任何进程占用即抛 BrowserConnectError。"""
        return cls(addr_or_opts, _mode='launch')

    # ----------挂件----------
    @property
    def set(self):
        if self._setter is None:
            self._setter = BrowserSetter(self)
        return self._setter

    @property
    def listen(self):
        if self._listener is None:
            self._listener = BrowserListener(self)
        return self._listener

    @property
    def wait(self):
        if self._waiter is None:
            self._waiter = BrowserWaiter(self)
        return self._waiter

    # ----------基本信息----------
    @property
    def id(self):
        return self._browser_id

    @property
    def user_data_path(self):
        return self._options.user_data_path

    @property
    def process_id(self):
        return self._process_id

    @property
    def timeout(self):
        return self._timeouts.base

    @property
    def timeouts(self):
        return self._timeouts

    @property
    def load_mode(self):
        return self._load_mode

    @property
    def download_path(self):
        return self._download_path

    @property
    def is_alive(self):
        return self._driver.is_running

    @property
    def is_headless(self):
        return self._is_headless

    @property
    def is_incognito(self):
        return self._incognito

    @property
    def is_guest(self):
        return self._guest

    @property
    def command_line(self):
        return ' '.join(self._command_line) if self._command_line else ''

    @property
    def context(self):
        if not self._context:
            self._context = BrowserContext(self, self._browser_id)
        return self._context

    # ----------tab 管理----------
    @property
    def tab_ids(self):
        return self._tab_ids(self._browser_id)

    @property
    def tabs(self):
        return self.get_tabs()

    @property
    def latest_tab(self):
        ids = self.tab_ids
        if ids:
            return self.get_tab(id_or_num=ids[0])
        return self.new_tab()

    def _tab_ids(self, context_id):
        tabs = [i['targetId'] for i in self._run_cdp('Target.getTargets')['targetInfos']
                if i['browserContextId'] == context_id and i['type'] in ('page', 'webview')
                and not i['url'].startswith(('chrome-extension://', 'devtools://', 'chrome://newtab-footer'))]
        return tabs if self._ws_only else [i['id'] for i in self._driver.get(f'http://{self.address}/json').json()
                                           if i['id'] in tabs]

    def new_context(self, proxy=None, proxy_bypass=None, auto_close=True):
        args = {'disposeOnDetach': auto_close}
        if proxy:
            url, usr, pwd, full = parse_proxy(proxy)
        else:
            url, usr, pwd, full = self._tabs.get_proxy('main')
        if url:
            args['proxyServer'] = url
            if proxy_bypass:
                if not isinstance(proxy_bypass, str):
                    proxy_bypass = ';'.join(proxy_bypass)
                args['proxyBypassList'] = proxy_bypass
        cid = self._run_cdp('Target.createBrowserContext', **args)['browserContextId']
        if proxy:
            self._tabs.set_proxy(cid, (url, usr, pwd, full))
        return BrowserContext(self, cid)

    def new_tab(self, url=None, new_window=False, background=False, hidden=False):
        return self._new_tab(url=url, new_window=new_window, background=background, hidden=hidden,
                             context_id=self._browser_id)

    def _new_tab(self, url=None, new_window=False, background=False, hidden=False, context_id=None,
                 is_browser=True):
        kwargs = {'url': 'about:blank'}
        if hidden:
            kwargs['hidden'] = True
        else:
            if new_window:
                kwargs['newWindow'] = True
            if background:
                kwargs['background'] = True
        if context_id:
            kwargs['browserContextId'] = context_id

        if is_browser and (self.is_incognito or self.is_guest):
            return _new_tab_by_js(self, url, new_window, context_id)
        try:
            tab = self._run_cdp('Target.createTarget', **kwargs)['targetId']
        except CDPError:
            return _new_tab_by_js(self, url, new_window, context_id)

        if not hidden:
            while self.is_alive:
                if tab in self._tabs.target_ids:
                    break
                sleep(.01)
            else:
                raise BrowserConnectError('浏览器已断开连接。')
        tab = Tab(self, tab, context_id)
        if url:
            tab.get(url)
        return tab

    def get_tab(self, id_or_num=None, title=None, url=None, tab_type='page', as_id=False):
        return self._get_tab(self._browser_id, id_or_num=id_or_num, title=title,
                             url=url, tab_type=tab_type, as_id=as_id)

    def get_tabs(self, title=None, url=None, tab_type='page', as_id=False):
        return self._get_tabs(self._browser_id, title=title, url=url, tab_type=tab_type, as_id=as_id)

    def close_tabs(self, tabs_or_ids, others=False):
        if isinstance(tabs_or_ids, str):
            tabs = {tabs_or_ids}
        elif isinstance(tabs_or_ids, Tab):
            tabs = {tabs_or_ids.tab_id}
        elif isinstance(tabs_or_ids, (list, tuple)):
            tabs = set(i.tab_id if isinstance(i, Tab) else i for i in tabs_or_ids)
        else:
            raise ValueError(f'tabs_or_ids 必须是 tab id、Tab 对象或其列表，而不是 {type(tabs_or_ids).__name__}')

        all_tabs = set(self.tab_ids)
        if others:
            tabs = all_tabs - tabs

        if len(all_tabs - tabs) > 0:
            for tab in tabs:
                self._close_tab(tab=tab)
        else:
            self.quit()

    def _close_tab(self, tab):
        if isinstance(tab, str):
            tab = self.get_tab(tab)
        tab._run_cdp('Target.closeTarget', targetId=tab.tab_id, _ignore=True)
        while tab.tab_id in self._tabs.target_ids:
            sleep(.01)

    def activate_tab(self, id_ind_tab):
        try:
            if isinstance(id_ind_tab, int):
                id_ind_tab += -1 if id_ind_tab else 1
                id_ind_tab = self.tab_ids[id_ind_tab]
            elif isinstance(id_ind_tab, Tab):
                id_ind_tab = id_ind_tab.tab_id
            self._run_cdp('Target.activateTarget', targetId=id_ind_tab)
        except (PageDisconnectedError, IndexError):
            raise RuntimeError('没有找到该 tab。')

    def wait_new_tab(self, curr_tab=None, timeout=None):
        """等待并返回新出现的 page target id。"""
        from ..settings import Settings as _S
        base = set(self.tab_ids)

        def find_new():
            tabs = set(self.tab_ids)
            new = tabs - base
            if curr_tab:
                new.discard(curr_tab)
            return new.pop() if new else None

        return wait_until(find_new, timeout=timeout or _S.browser_connect_timeout)

    # ----------其他----------
    def cookies(self, all_info=False):
        """获取浏览器全部 cookie，返回 CookiesList。"""
        cks = self._run_cdp('Storage.getCookies')['cookies']
        if all_info:
            return CookiesList(cks)
        return CookiesList([{'name': c['name'], 'value': c['value'], 'domain': c['domain']} for c in cks])

    def reconnect(self):
        self._disconnect_flag = True
        self._driver.stop()
        self._driver = Driver(self._ws_address, self) if Settings._debug is None else DebugDriver(self._ws_address, self)
        self._run_cdp('Target.setDiscoverTargets', discover=True)
        self._disconnect_flag = False

    def clear_cache(self, cache=True, cookies=True):
        if cache:
            self.latest_tab._run_cdp('Network.clearBrowserCache')
        if cookies:
            self._run_cdp('Storage.clearCookies')

    def quit(self, timeout=5, force=False, del_data=False):
        pids = None
        for tab in self._tabs.objects:
            tab._stop_messenger()
        self._tabs.clear()
        try:
            if force:
                pids = [pid['id'] for pid in self._run_cdp('SystemInfo.getProcessInfo')['processInfo']]
            self._run_cdp('Browser.close')
        except PageDisconnectedError:
            pass
        self._stop_messenger()

        if not self.address.startswith('127.0.0.1'):
            return

        if pids:
            try:
                from psutil import Process   # 可选依赖：没装就走平台 pid 强杀兜底
            except ImportError:
                Process = None
            if Process is not None:
                for pid in pids:
                    try:
                        Process(pid).kill()
                    except Exception:
                        pass
            else:
                self._kill_pids(pids)
            wait_until(lambda: self._pids_gone(pids), timeout=timeout)

        if self._options.auto_port or del_data:
            ensure_del_dir(self._options.user_data_path)

    @staticmethod
    def _kill_pids(pids):
        """psutil 缺席时的兜底：按平台强杀（taskkill / SIGKILL）。"""
        from os import kill
        from platform import system
        from subprocess import run as _run, DEVNULL
        if system().lower() == 'windows':
            for pid in pids:
                _run(['taskkill', '/PID', str(pid), '/T', '/F'],
                     stdout=DEVNULL, stderr=DEVNULL)
        else:
            from signal import SIGKILL   # Windows 没有 SIGKILL，只在 POSIX 分支导入
            for pid in pids:
                try:
                    kill(pid, SIGKILL)
                except OSError:
                    pass

    @staticmethod
    def _pids_gone(pids):
        from os import popen
        from platform import system
        for pid in pids:
            txt = f'tasklist | findstr {pid}' if system().lower() == 'windows' else f'ps -ef | grep {pid}'
            p = popen(txt)
            sleep(.05)
            try:
                if f'  {pid} ' in p.read():
                    return None
            except TypeError:
                pass
        return True

    # ----------内部协议（Messenger / 事件）----------
    def _attach(self, target_id):
        sid = self._run_cdp('Target.attachToTarget', targetId=target_id, flatten=True).get('sessionId')
        if not sid:
            raise BrowserConnectError('连接浏览器失败。')
        return sid

    def _detach(self, session_id):
        try:
            self._run_cdp('Target.detachFromTarget', sessionId=session_id, _timeout=0, _ignore=True)
        except Exception:
            pass
        self._driver.remove_session_owner(session_id)

    def _get_session_id(self, target_id, obj=None):
        if not target_id:
            return None
        sid = self._tabs._tab_first_session.pop(target_id, None)
        if not sid:
            sid = self._attach(target_id)
        self._tabs.add(sid, target_id, obj=obj)
        if obj:
            self._driver.add_session_owner(sid, obj)
        return sid

    def _get_tab_ids(self, context_id, tab_type, title, url):
        if isinstance(tab_type, str):
            tab_type = (tab_type,)
        tab_ids = [t['targetId'] for t in self._run_cdp('Target.getTargets')['targetInfos']
                   if t.get('browserContextId') == context_id and (tab_type is None or t['type'] in tab_type)
                   and (title is None or title in t['title']) and (url is None or url in t['url'])]
        if not self._ws_only:
            tab_ids = [t['id'] for t in self._driver.get(f'http://{self.address}/json').json() if t['id'] in tab_ids]
        return tab_ids

    def _get_tab(self, context_id, id_or_num=None, title=None, url=None, tab_type='page', as_id=False):
        if isinstance(id_or_num, str):
            if id_or_num not in self._get_tab_ids(context_id, None, None, None):
                raise RuntimeError(f'没有找到该 tab：{id_or_num}')
        elif isinstance(id_or_num, Tab):
            return id_or_num.tab_id if as_id else id_or_num
        else:
            tab_ids = self._get_tab_ids(context_id, tab_type=tab_type, title=title, url=url)
            if not tab_ids:
                raise RuntimeError(f'没有找到符合条件的 tab：id_or_num={id_or_num}, title={title}, url={url}')
            if id_or_num is None:
                id_or_num = tab_ids[0]
            elif isinstance(id_or_num, int):
                id_or_num = tab_ids[id_or_num - 1 if id_or_num > 0 else id_or_num]
        return id_or_num if as_id else Tab(self, id_or_num, context_id)

    def _get_tabs(self, context_id, title=None, url=None, tab_type='page', as_id=False):
        tab_ids = self._get_tab_ids(context_id, tab_type=tab_type, title=title, url=url)
        return tab_ids if as_id else [Tab(self, _id, context_id) for _id in tab_ids]

    def _onTargetCreated(self, **kwargs):
        if kwargs['targetInfo']['type'] == 'page' and not kwargs['targetInfo']['url'].startswith(
                ('chrome-extension://', 'devtools://')):
            tab_id = kwargs['targetInfo']['targetId']
            self._tabs.add_frame(tab_id, tab_id)
            sid = self._attach(tab_id)
            cid = kwargs['targetInfo'].get('browserContextId', self._context_id)
            self._tabs.add(sid, tab_id, context_id=cid, opener=kwargs['targetInfo'].get('openerId'))
            self._tabs._tab_first_session[tab_id] = sid
            self._tabs.set_newest_tab(cid, tab_id)
        elif kwargs['targetInfo']['type'] == 'iframe':
            self._tabs.add_frame(kwargs['targetInfo']['targetId'],
                                 self._tabs._frames.get(kwargs['targetInfo']['parentFrameId'],
                                                        kwargs['targetInfo']['parentFrameId']))

    def _onTargetDestroyed(self, **kwargs):
        tab_id = kwargs['targetId']
        self._tabs.stop_target(target_id=tab_id)

    def _onDetachedFromTarget(self, **kwargs):
        self._tabs.stop_session(kwargs['sessionId'])
        self._driver.remove_session_owner(kwargs['sessionId'])

    def _on_disconnect(self):
        if not self._disconnect_flag:
            Browser._BROWSERS.pop(self.id, None)
            if self._options.auto_port:
                ensure_del_dir(self._options.user_data_path)
        self._stop_messenger()

    def _stop(self):
        self._stop_messenger()
        self._driver.stop()


def _handle_options(addr_or_opts):
    if isinstance(addr_or_opts, Options):
        return addr_or_opts
    elif not addr_or_opts:
        return Options()
    elif isinstance(addr_or_opts, str) and ':' in addr_or_opts:
        return Options().set_address(addr_or_opts)
    elif isinstance(addr_or_opts, int):
        return Options().set_local_port(addr_or_opts)
    else:
        raise ValueError(f'addr_or_opts 必须是地址字符串、端口号或 Options 对象，而不是 '
                         f'{type(addr_or_opts).__name__}')


def _run_browser(options, mode='auto'):
    """建立与浏览器的连接。返回 (is_headless, browser_id, command_line, ws_only, driver,
    incognito, guest, exists)。按接管语义三原则：端口活着即纯接管，绝不比对重启。"""
    if options.ws_address:
        try:
            driver = Driver(options.ws_address, None) if Settings._debug is None else DebugDriver(
                options.ws_address, None)
        except OSError:
            raise
        except Exception as e:
            raise BrowserConnectError(f'连接浏览器失败：{e}')
        info = str(driver.run('Browser.getHistograms'))
        incognito = "'Browser.WindowCount.Incognito'" in info
        guest = "'Browser.WindowCount.Guest'" in info
        browser_id = _get_browser_id(driver, incognito or guest)
        is_headless = 'headless' in driver.run('Browser.getVersion')['userAgent'].lower()
        ws_only = True
        return is_headless, browser_id, None, ws_only, driver, incognito, guest, True

    command_line = connect_browser(options, mode)
    exists = command_line is not None
    try:
        s = Session()
        s.trust_env = False
        s.keep_alive = False
        ws = s.get(f'http://{options.address}/json/version', headers={'Connection': 'close'}, timeout=2)
        if not ws and not options.ws_address:
            raise BrowserConnectError('连接浏览器失败。')
        j = ws.json()
        browser_id = j['webSocketDebuggerUrl'].split('/')[-1]
        ws_only = j['webSocketDebuggerUrl'].endswith('browser')
        ws.close()
        s.close()
    except KeyError:
        raise BrowserConnectError('该浏览器不允许控制。')
    except BrowserConnectError:
        raise
    except Exception:
        raise BrowserConnectError('连接浏览器失败。')
    driver = (Driver(f'ws://{options.address}/devtools/browser/{browser_id}', None) if Settings._debug is None
              else DebugDriver(f'ws://{options.address}/devtools/browser/{browser_id}', None))

    # 无头探测。注意 exists 的语义是「本次是**我们拉起的**」（connect_browser
    # 接管时返回 None，启动时返回命令行）：
    #   自己拉起 → 启动参数即事实（options.is_headless）；
    #   接管      → 只能探测（_probe_headless：浏览器自报命令行 > CLI state 文件
    #               > UA 启发式 > Options 声明）。
    if exists:
        is_headless = bool(options.is_headless)
    else:
        is_headless = _probe_headless(driver, options)

    info = str(driver.run('Browser.getHistograms'))
    incognito = "'Browser.WindowCount.Incognito'" in info
    guest = "'Browser.WindowCount.Guest'" in info
    browser_id = _get_browser_id(driver, incognito or guest)
    return is_headless, browser_id, command_line, False, driver, incognito, guest, exists


def _probe_headless(driver, options):
    """接管场景判无头（按可靠度降级，纯展示用，不影响行为）：

    1. `Browser.getBrowserCommandLine`——浏览器自报的启动参数，最权威
       （Chrome 只在带 --enable-automation 时才允许读，否则报错）；
    2. `~/.happybrowser/state_{port}.json` 的 `headless` 字段——CLI/hbh 起的
       实例都会记，`hb --headless` 复用同一只浏览器时正好命中；
    3. UA 里的 Headless 标记——headless=new 的 product 已不带宽，且
       opts.headless() 会覆盖 UA，所以退居兜底；
    都认不出来就返回 False（= 确认不了）。**不要**用调用方的 `options.is_headless`
    兜底——那是「我希望它无头」而不是「它是无头」，`hb --headless --existing`
    正是靠这个 False 决定「确认不了就不接管」。
    """
    try:
        argv = driver.run('Browser.getBrowserCommandLine').get('arguments') or []
        if argv:
            return any('--headless' in str(a) for a in argv)
    except Exception:
        pass
    port = str(getattr(options, 'address', '')).rsplit(':', 1)[-1]
    if port.isdigit():
        try:
            from pathlib import Path
            import json
            f = Path.home() / '.happybrowser' / f'state_{port}.json'
            flag = json.loads(f.read_text(encoding='utf-8')).get('headless')
            if isinstance(flag, bool):
                return flag
        except Exception:
            pass
    try:
        ua = driver.run('Browser.getVersion').get('userAgent', '')
        if 'headless' in ua.lower():
            return True
    except Exception:
        pass
    return False


def _get_browser_id(driver, incognito):
    if incognito:
        browser_id = driver.run('Target.getTargets')
        if 'error' in browser_id:
            raise BrowserConnectError(f'连接浏览器失败：{browser_id["error"]}')
        if browser_id['targetInfos']:
            return browser_id['targetInfos'][0]['browserContextId']

    browser_id = driver.run('Target.getBrowserContexts')
    if 'error' in browser_id:
        raise BrowserConnectError(f'连接浏览器失败：{browser_id["error"]}')
    if 'defaultBrowserContextId' in browser_id:
        browser_id = browser_id['defaultBrowserContextId']
    else:
        browser_id = driver.run('Target.getTargets')
        if 'error' in browser_id:
            raise BrowserConnectError(f'连接浏览器失败：{browser_id["error"]}')
        browser_id = browser_id['targetInfos'][0]['browserContextId']
    return browser_id


class Tabs(object):
    """tab/session/frame/context 注册表：驱动层的中枢数据结构。"""

    def __init__(self):
        self._sessions = {}  # {session_id: target_id}
        self._targets = {}  # {target_id: {session_id}}
        self._objects = {}  # {session_id: Tab/Frame}
        self._openers = {}  # {target_id: target_id}
        self._frames = {}  # {frame_id: target_id} 某个 frame 在哪个 tab 中
        self._contexts = {}  # {target_id: context_id}
        self._context_newest_tab = {}  # {context_id: target_id}
        self._tab_first_session = {}  # {target_id: session_id}
        self._proxies = {}  # {context_id: (url, usr, pwd, full)}

    @property
    def session_ids(self):
        return self._sessions.keys()

    @property
    def target_ids(self):
        return self._targets.keys()

    @property
    def objects(self):
        return self._objects.values()

    @property
    def frame_ids(self):
        return self._frames

    @property
    def openers(self):
        return self._openers

    def add(self, session_id, target_id, context_id=None, opener=None, obj=None):
        self._sessions[session_id] = target_id
        self._targets.setdefault(target_id, set()).add(session_id)
        if context_id:
            self._contexts[target_id] = context_id
        if opener:
            self._openers[target_id] = opener
        if obj:
            self._objects[session_id] = obj

    def add_obj(self, session_id, obj):
        if session_id in self._sessions:
            self._objects[session_id] = obj

    def add_frame(self, frame_id, target_id):
        self._frames[frame_id] = target_id

    def remove_frame(self, frame_id):
        self._frames.pop(frame_id, None)

    def remove_session(self, session_id):
        s = self._targets.get(self._sessions.get(session_id))
        if s:
            s.discard(session_id)
        self._objects.pop(session_id, None)
        self._sessions.pop(session_id, None)

    def remove_target(self, target_id):
        for session_id in self._targets.get(target_id, set()):
            self._objects.pop(session_id, None)
            self._sessions.pop(session_id, None)
        for fid in [fid for fid, t in self._frames.items() if t == target_id]:
            self._frames.pop(fid, None)
        self._targets.pop(target_id, None)
        self._openers.pop(target_id, None)
        self._tab_first_session.pop(target_id, None)

    def remove_context(self, context_id):
        self._context_newest_tab.pop(context_id, None)
        self._proxies.pop(context_id, None)

    def set_proxy(self, context_id, proxy):
        self._proxies[context_id] = proxy

    def get_proxy(self, context_id):
        return self._proxies.get(context_id, self._proxies.get('main', (None, None, None, None)))

    def set_newest_tab(self, context_id, target_id):
        self._context_newest_tab[context_id] = target_id

    def get_newest_tab(self, context_id):
        return self._context_newest_tab.get(context_id)

    def get_session_ids(self, target_id):
        return self._targets.get(target_id, set())

    def get_target_id(self, session_id):
        return self._sessions.get(session_id)

    def get_context_id(self, target_id=None, frame_id=None):
        if frame_id:
            return self._contexts.get(self._frames.get(frame_id))
        elif target_id:
            return self._contexts.get(target_id)
        return None

    def get_object(self, session_id, default=None):
        return self._objects.get(session_id, default)

    def stop_session(self, session_id):
        obj = self.get_object(session_id=session_id)
        if obj:
            obj._stop_messenger()
        self.remove_session(session_id)

    def stop_target(self, target_id):
        for session_id in set(self._targets.get(target_id, set())):
            self.stop_session(session_id)
        self.remove_target(target_id)

    def clear(self):
        self._sessions.clear()
        self._targets.clear()
        self._objects.clear()
        self._openers.clear()
        self._frames.clear()
        self._contexts.clear()
        self._context_newest_tab.clear()
        self._proxies.clear()
        self._tab_first_session.clear()


class BrowserContext(object):
    """隔离/无痕上下文（对应 Target.createBrowserContext）。"""

    def __init__(self, browser, context_id):
        self._browser = browser
        self._context_id = context_id
        self._default_context = context_id == browser._browser_id

    @property
    def browser(self):
        return self._browser

    @property
    def tab_ids(self):
        return self._browser._tab_ids(self._context_id)

    @property
    def latest_tab(self):
        ids = self.tab_ids
        return self.new_tab() if not ids else self._browser._get_tab(self._context_id, id_or_num=ids[0])

    @property
    def tabs(self):
        return self.get_tabs()

    def cookies(self, all_info=False):
        cks = self._browser._run_cdp('Storage.getCookies', browserContextId=self._context_id)['cookies']
        if all_info:
            return CookiesList(cks)
        return CookiesList([{'name': c['name'], 'value': c['value'], 'domain': c['domain']} for c in cks])

    @property
    def set(self):
        from .._units.setter import ContextSetter
        if getattr(self, '_setter', None) is None:
            self._setter = ContextSetter(self)
        return self._setter

    def new_tab(self, url=None, new_window=False, background=False, hidden=False):
        return self._browser._new_tab(url=url, new_window=new_window, background=background,
                                      hidden=hidden, context_id=self._context_id, is_browser=False)

    def get_tab(self, id_or_num=None, title=None, url=None, tab_type='page', as_id=False):
        return self._browser._get_tab(self._context_id, id_or_num=id_or_num, title=title,
                                      url=url, tab_type=tab_type, as_id=as_id)

    def get_tabs(self, title=None, url=None, tab_type='page', as_id=False):
        return self._browser._get_tabs(self._context_id, title=title, url=url, tab_type=tab_type, as_id=as_id)

    def close(self):
        if not self._default_context:
            self._browser._run_cdp('Target.disposeBrowserContext', browserContextId=self._context_id)
            self._browser._tabs.remove_context(self._context_id)

    def _run_cdp(self, cmd, _ignore=None, **cmd_args):
        return self._browser._run_cdp(cmd, _ignore=_ignore, **cmd_args)


def _new_tab_by_js(browser, url, new_window, context_id):
    tab = browser._get_tab(context_id)
    if url:
        url = normalize_url(url)  # 缺协议自动补，与 Tab.get 行为一致
    if url and not match(r'^.*?://.*', url):
        raise IncorrectURLError(f'URL 格式不正确：{url}')
    url = f'"{url}"' if url else '""'
    new = 'target="_new"' if new_window else 'target="_blank"'
    tid = browser._tabs.get_newest_tab(context_id)
    tab._run_cdp('Runtime.evaluate', expression=f'window.open({url}, {new})', _ignore=True)
    tid = browser.wait_new_tab(curr_tab=tid)
    return browser._get_tab(context_id, tid)
