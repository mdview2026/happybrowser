# -*- coding: utf-8 -*-
"""页面基类（移植精简自 DrissionPage © g1879 _pages/chromium_base.py，https://DrissionPage.cn
—— 遵循其许可：仅限个人学习与合法非盈利用途，商用需上游授权）。

改造点（相对上游）：
- 查找入口 _find_elements 收敛为 query 单通道：parse_loc 统一解析（str|Q，? 可省略），
  纯查询走 performSearch 精确直查，hybrid 走预筛+精筛；ax/any/s_ele/xpath/css 全部删除。
- getFrameTree 的 frame 注册由正则扫描 str(dict) 改为递归遍历。
- 截图/cookies/set/listen/screencast/actions/scroll 挂件随后续里程碑（M4/M5）接入。
- json 属性容错 NoneElement（上游 loads(None) 会裸抛 TypeError）。
"""
from json import loads, JSONDecodeError
from pathlib import Path
from threading import Thread
from time import perf_counter, sleep
from urllib.parse import quote

from ..settings import Settings
from .._core.messenger import Messenger
from .._core.tools import wait_until
from .._elements.element import Element, ShadowRoot, run_js, _find_by_perform_search, _find_by_query
from .._functions.cookies import CookiesList
from .._functions.web import NavResult, save_page, location_in_viewport_js
from .._functions.files import make_valid_name
from .._locate.query import parse_loc, to_xpath, is_pure_xpath
from .._units.console import Console
from .._units.listener import Listener
from .._units.rect import TabRect
from .._units.screencast import Screencast
from .._units.setter import TabSetter
from .._units.states import PageStates
from .._units.waiter import PageWaiter
from .._units.actions import Actions
from .._units.scroller import PageScroller
from ..errors import (ContextLostError, CDPError, PageDisconnectedError, ElementLostError,
                      BrowserConnectError, ElementNotFoundError)


class PageBase(Messenger):
    """Tab/Frame 的公共基类：会话、加载状态机、查找入口、导航、alert、JS 执行。"""

    def __init__(self, browser, target_id, context_id):
        super().__init__()
        self._browser = browser
        self._driver = browser._driver
        self._is_loading = None
        self._root_oid = None  # 文档对象 object id
        self._root_nid = None
        self._root_bid = None
        self._context_id = context_id
        self._target_id = target_id
        self._frame_id = None
        self._states = None
        self._rect = None
        self._wait = None
        self._actions = None
        self._scroll = None
        self._setter = None
        self._listener = None
        self._console = None
        self._screencast = None
        self._download_path = browser.download_path
        self._has_alert = False
        self._ready_state = None
        self._timeouts = browser.timeouts
        self._load_mode = browser.load_mode
        self._upload_list = None
        self._doc_got = False  # 在 LoadEventFired 和 FrameStoppedLoading 间标记是否已获取文档
        self._auto_handle_alert = None
        self._load_end_time = 0
        self._init_jss = []
        self._nav_result = None
        self._url = None
        self._url_available = None
        self.retry_times = 3
        self.retry_interval = 2
        self._none_ele_value = Settings.none_ele_value
        self._none_ele_return_value = Settings.none_ele_return_value

        self._event_handlers = {
            'Page.javascriptDialogOpening': [self._on_alert_open],
            'Page.javascriptDialogClosed': [self._on_alert_close],
            'Page.frameStartedLoading': [self._onFrameStartedLoading],
            'Page.frameNavigated': [self._onFrameNavigated],
            'Page.domContentEventFired': [self._onDomContentEventFired],
            'Page.loadEventFired': [self._onLoadEventFired],
            'Page.frameStoppedLoading': [self._onFrameStoppedLoading],
            'Page.frameAttached': [self._onFrameAttached],
            'Page.frameDetached': [self._onFrameDetached],
        }
        self._imm_events = {'Page.javascriptDialogOpening'}
        self._start_messenger()
        self._connect_browser()

        url, usr, pwd, full = self.browser._tabs.get_proxy(self._context_id)
        if usr:
            self._proxy_usr = usr
            self._proxy_pwd = pwd
            self._enable_domain('Fetch', handleAuthRequests=True)
            self._set_callback('Fetch.authRequired', self._onAuthRequired)
            self._set_callback('Fetch.requestPaused', self._onRequestPaused)
        else:
            self._proxy_usr = None
            self._proxy_pwd = None

    # ----------连接与会话----------
    def _connect_browser(self):
        self._is_reading = False
        self._driver_init()
        if self._js_ready_state == 'complete' and self._ready_state is None:
            self._get_document()
            self._ready_state = 'complete'

    def _driver_init(self):
        self._is_loading = True
        self._alert = Alert(self._auto_handle_alert)
        self._enable_domain('Page')
        self._enable_domain('DOM')
        self._run_cdp('Emulation.setFocusEmulationEnabled', enabled=True)
        self._register_frames(self._run_cdp('Page.getFrameTree')['frameTree'])
        if not self._frame_id:
            self._frame_id = self._run_cdp('Page.getFrameTree')['frameTree']['frame']['id']

    def _register_frames(self, tree):
        """把 frameTree 中所有 frame 登记到 Tabs 注册表。"""
        frames = [tree['frame']]
        for child in tree.get('childFrames', []):
            frames.extend(self._register_frames(child))
        for f in frames:
            self.browser._tabs.add_frame(f['id'], self.tab_id)
        return frames

    def _get_document(self, timeout=10):
        if self._is_reading:
            return None
        self._is_reading = True
        timeout = max(timeout, 2)
        end_time = perf_counter() + timeout
        while perf_counter() < end_time:
            try:
                root = self._run_cdp('DOM.getDocument', _timeout=timeout)['root']
                self._root_bid = root['backendNodeId']
                self._root_nid = root['nodeId']
                timeout = end_time - perf_counter()
                timeout = max(1, timeout)
                self._root_oid = self._run_cdp('DOM.resolveNode', backendNodeId=self._root_bid,
                                               _timeout=timeout)['object']['objectId']
                result = True
                break
            except PageDisconnectedError:
                result = False
                break
            except Exception:
                timeout = end_time - perf_counter()
                timeout = .5 if timeout <= 0 else timeout
            sleep(.05)
        else:
            result = False

        if result:
            self._register_frames(self._run_cdp('Page.getFrameTree', _ignore=PageDisconnectedError)['frameTree'])

        self._is_loading = False
        self._is_reading = False
        return result

    # ----------加载事件----------
    def _onFrameDetached(self, **kwargs):
        self.browser._tabs.remove_frame(kwargs['frameId'])

    def _onFrameAttached(self, **kwargs):
        self.browser._tabs.add_frame(kwargs['frameId'], self.tab_id)

    def _onFrameStartedLoading(self, **kwargs):
        self.browser._tabs.add_frame(kwargs['frameId'], self.tab_id)
        if kwargs['frameId'] == self._frame_id:
            self._doc_got = False
            self._ready_state = 'connecting'
            self._is_loading = True
            self._load_end_time = perf_counter() + self.timeouts.page_load
            if self._load_mode == 'eager':
                t = Thread(target=self._wait_to_stop)
                t.daemon = True
                t.start()

    def _onFrameNavigated(self, **kwargs):
        if kwargs['frame']['id'] == self._frame_id:
            self._doc_got = False
            self._ready_state = 'loading'
            self._is_loading = True
            if kwargs.get('type') == 'BackForwardCacheRestore':
                self._get_document()

    def _onDomContentEventFired(self, **kwargs):
        if self._load_mode == 'eager':
            self._run_cdp('Page.stopLoading')
        if self._get_document(self._load_end_time - perf_counter() - .1):
            self._doc_got = True
        self._ready_state = 'interactive'

    def _onLoadEventFired(self, **kwargs):
        if self._doc_got is False and self._get_document(self._load_end_time - perf_counter() - .1):
            self._doc_got = True
        self._ready_state = 'complete'

    def _onFrameStoppedLoading(self, **kwargs):
        self.browser._tabs.add_frame(kwargs['frameId'], self.tab_id)
        if kwargs['frameId'] == self._frame_id:
            if self._doc_got is False:
                self._get_document(self._load_end_time - perf_counter() - .1)
            self._ready_state = 'complete'

    def _onFileChooserOpened(self, **kwargs):
        if self._upload_list:
            if 'backendNodeId' not in kwargs:
                raise RuntimeError('该类型的文件选择框不支持输入路径')
            files = self._upload_list if kwargs['mode'] == 'selectMultiple' else self._upload_list[:1]
            self._run_cdp('DOM.setFileInputFiles', files=files, backendNodeId=kwargs['backendNodeId'])
            self._set_callback('Page.fileChooserOpened', None)
            self._run_cdp('Page.setInterceptFileChooserDialog', enabled=False)
            self._upload_list = None

    def _onAuthRequired(self, **kwargs):
        request_id = kwargs['requestId']
        if kwargs.get('authChallenge', {}).get('source') == 'Proxy':
            auth = {'response': 'ProvideCredentials', 'username': self._proxy_usr,
                    'password': self._proxy_pwd}
        else:
            auth = {'response': 'Default'}
        self._run_cdp('Fetch.continueWithAuth', requestId=request_id, authChallengeResponse=auth,
                      _ignore=True)

    def _onRequestPaused(self, **kwargs):
        if 'responseErrorReason' in kwargs or 'responseStatusCode' in kwargs:
            self._run_cdp('Fetch.continueResponse', requestId=kwargs['requestId'], _ignore=True)
        else:
            self._run_cdp('Fetch.continueRequest', requestId=kwargs['requestId'], _ignore=True)

    def _wait_to_stop(self):
        end_time = perf_counter() + self.timeouts.page_load
        while perf_counter() < end_time:
            sleep(.02)
        if self._ready_state in ('interactive', 'complete') and self._is_loading:
            self.stop_loading()

    # ----------挂件----------
    @property
    def wait(self):
        if self._wait is None:
            self._wait = PageWaiter(self)
        return self._wait

    @property
    def states(self):
        if self._states is None:
            self._states = PageStates(self)
        return self._states

    @property
    def rect(self):
        if self._rect is None:
            self._rect = TabRect(self)
        return self._rect

    @property
    def actions(self):
        if self._actions is None:
            self._actions = Actions(self)
        self.wait.doc_loaded()
        return self._actions

    @property
    def scroll(self):
        self.wait.doc_loaded()
        if self._scroll is None:
            self._scroll = PageScroller(self)
        return self._scroll

    @property
    def set(self):
        if self._setter is None:
            self._setter = TabSetter(self)
        return self._setter

    @property
    def listen(self):
        if self._listener is None:
            self._listener = Listener(self)
        return self._listener

    @property
    def console(self):
        if self._console is None:
            self._console = Console(self)
        return self._console

    @property
    def screencast(self):
        if self._screencast is None:
            self._screencast = Screencast(self)
        return self._screencast

    @property
    def tab(self):
        return self  # Tab 即自身；Frame 覆写为所属 Tab

    @property
    def download_path(self):
        return self._download_path

    @property
    def timeout(self):
        return self._timeouts.base

    @property
    def timeouts(self):
        return self._timeouts

    # ----------基本信息----------
    @property
    def browser(self):
        return self._browser

    @property
    def tab_id(self):
        return self._target_id

    @property
    def driver(self):
        if self._driver is None:
            raise BrowserConnectError('浏览器连接已断开')
        return self._driver

    @property
    def title(self):
        return self._run_cdp_loaded('Target.getTargetInfo', targetId=self._target_id)['targetInfo']['title']

    @property
    def url(self):
        return self._run_cdp_loaded('Target.getTargetInfo', targetId=self._target_id)['targetInfo']['url']

    @property
    def html(self):
        self.wait.doc_loaded()
        return self._run_cdp('DOM.getOuterHTML', objectId=self._root_oid)['outerHTML']

    @property
    def json(self):
        try:
            ele = self.ele('pre', timeout=.5)
            return loads(ele.text) if ele else None
        except (JSONDecodeError, TypeError):
            return None

    @property
    def active_ele(self):
        return self._run_js_loaded('return document.activeElement;')

    @property
    def load_mode(self):
        return self._load_mode

    @property
    def user_agent(self):
        return self._run_cdp('Runtime.evaluate', expression='navigator.userAgent;')['result']['value']

    @property
    def upload_list(self):
        return self._upload_list

    @property
    def url_available(self):
        return self._url_available

    @property
    def ready_state(self):
        if self._ready_state is None:
            self._ready_state = self._js_ready_state
        return self._ready_state

    @property
    def _js_ready_state(self):
        try:
            return self._run_cdp('Runtime.evaluate', expression='document.readyState;',
                                 _timeout=3)['result']['value']
        except ContextLostError:
            return None
        except TimeoutError:
            return 'timeout'

    # ----------CDP / JS----------
    def run_cdp(self, cmd, **cmd_args):
        return self._run_cdp(cmd, _user=True, **cmd_args)

    def run_cdp_loaded(self, cmd, **cmd_args):
        return self._run_cdp_loaded(cmd, _user=True, **cmd_args)

    def _run_cdp_loaded(self, cmd, _ignore=None, _user=False, _timeout=None, **cmd_args):
        self.wait.doc_loaded()
        return self._run_cdp(cmd, _ignore=_ignore, _user=_user, _timeout=_timeout, **cmd_args)

    def run_js(self, script, *args, as_expr=False, timeout=None):
        return self._run_js(script, *args, as_expr=as_expr, timeout=timeout)

    def run_js_loaded(self, script, *args, as_expr=False, timeout=None):
        self.wait.doc_loaded()
        return self._run_js(script, *args, as_expr=as_expr, timeout=timeout)

    def _run_js(self, script, *args, as_expr=False, timeout=None):
        return run_js(self, script, as_expr, self.timeouts.script if timeout is None else timeout, args)

    def _run_js_loaded(self, script, *args, as_expr=False, timeout=None):
        self.wait.doc_loaded()
        return run_js(self, script, as_expr, self.timeouts.script if timeout is None else timeout, args)

    def run_async_js(self, script, *args, as_expr=False):
        run_js(self, script, as_expr, 0, args)

    def add_init_js(self, script):
        js_id = self._run_cdp('Page.addScriptToEvaluateOnNewDocument', source=script,
                              includeCommandLineAPI=True)['identifier']
        self._init_jss.append(js_id)
        return js_id

    def remove_init_js(self, script_id=None):
        if script_id is None:
            for js_id in self._init_jss:
                self._run_cdp('Page.removeScriptToEvaluateOnNewDocument', identifier=js_id)
            self._init_jss.clear()
        elif script_id in self._init_jss:
            self._run_cdp('Page.removeScriptToEvaluateOnNewDocument', identifier=script_id)
            self._init_jss.remove(script_id)

    # ----------导航----------
    def get(self, url, retry=None, interval=None, timeout=None, raise_err=False):
        retry, interval, is_file = self._before_connect(url, retry, interval)
        r = self._d_connect(url=self._url, retry=retry, interval=interval, timeout=timeout,
                            raise_err=raise_err)
        self._url_available = r.ok
        return r

    def _before_connect(self, url, retry, interval):
        is_file = False
        if isinstance(url, Path) or ('://' not in url and ':\\\\' not in url):
            p = Path(url)
            if p.exists():
                url = str(p.resolve())
                is_file = True
        self._url = url if is_file else quote(url, safe='-_.~!*\'"();:@&=+$,/\\?#[]%')
        retry = retry if retry is not None else self.retry_times
        interval = interval if interval is not None else self.retry_interval
        return retry, interval, is_file

    def _d_connect(self, url, retry, interval, timeout=None, raise_err=False):
        err = None
        self._is_loading = True
        timeout = timeout if timeout is not None else self.timeouts.page_load
        self._enable_domain('Network')
        self._nav_result = NavResult()
        self._set_callback('Network.responseReceived', self._get_status_code)
        self._set_callback('Network.requestWillBeSent', self._get_request)
        for t in range(retry + 1):
            err = None
            end_time = perf_counter() + timeout
            try:
                result = self._run_cdp('Page.navigate', frameId=self._frame_id, url=url, _timeout=timeout)
                if 'errorText' in result:
                    self._nav_result.status = result['errorText']
                    err = ConnectionError(f'连接失败：{result["errorText"]}')
            except TimeoutError:
                self._nav_result.status = 'net::ERR_TIMED_OUT'
                err = TimeoutError(f'超时：页面连接（{timeout}秒）')

            if err:
                if t < retry:
                    sleep(interval)
                end_time1 = end_time - perf_counter()
                while self._ready_state not in ('loading', 'complete') and perf_counter() < end_time1:
                    sleep(.01)
                self.stop_loading()
                continue

            if self._load_mode == 'none':
                self._remove_callback('Network.responseReceived', self._get_status_code)
                self._remove_callback('Network.requestWillBeSent', self._get_request)
                self._disable_domain('Network')
                return self._nav_result

            yu = end_time - perf_counter()
            ok = self._wait_loaded(1 if yu <= 0 else yu)
            if not ok:
                err = TimeoutError(f'超时：页面连接（{timeout}秒）')
                if t < retry:
                    sleep(interval)
                continue

            if not err:
                break

        if err and raise_err:
            raise err if err is not None else ConnectionError('连接失败')

        self._remove_callback('Network.responseReceived', self._get_status_code)
        self._remove_callback('Network.requestWillBeSent', self._get_request)
        self._disable_domain('Network')
        return self._nav_result

    def refresh(self, ignore_cache=False):
        self._is_loading = True
        self._ready_state = 'connecting'  # 立刻标记，避免事件延迟期 doc_loaded 误判（上游有此竞态）
        self._run_cdp('Page.reload', ignoreCache=ignore_cache)
        self.wait.load_start()
        return self

    def forward(self, steps=1):
        self._forward_or_back(steps)

    def back(self, steps=1):
        self._forward_or_back(-steps)

    def _forward_or_back(self, steps):
        if steps == 0:
            return

        def get_history():  # reload 提交期可能瞬态报 Not attached to an active page，短暂重试
            r = self._run_cdp('Page.getNavigationHistory', _ignore=True)
            return None if 'error' in r else r

        history = wait_until(get_history, timeout=2)
        if history is None:
            self._run_cdp('Page.getNavigationHistory')  # 重试仍失败则抛出真实错误
            return
        index = history['currentIndex']
        history = history['entries']
        direction = 1 if steps > 0 else -1
        curr_url = history[index]['url']
        nid = None
        for num in range(abs(steps)):
            for i in history[index::direction]:
                index += direction
                if i['url'] != curr_url:
                    nid = i['id']
                    curr_url = i['url']
                    break
        if nid:
            self._is_loading = True
            self._run_cdp('Page.navigateToHistoryEntry', entryId=nid)

    def stop_loading(self):
        try:
            self._run_cdp('Page.stopLoading')
            end_time = perf_counter() + 5
            while self._ready_state != 'complete' and perf_counter() < end_time:
                sleep(.02)
        except (PageDisconnectedError, CDPError):
            pass
        finally:
            self._ready_state = 'complete'

    def _wait_loaded(self, timeout=None):
        def do():
            return True if (self._ready_state == 'complete'
                            or (self._load_mode == 'eager'
                                and self._ready_state in ('interactive', 'complete')
                                and not self._is_loading)) else None

        if timeout is None:
            timeout = self.timeouts.page_load
        r = wait_until(do, timeout=timeout)
        if r is not None:
            return r
        try:
            self.stop_loading()
        except CDPError:
            pass
        return False

    def _get_status_code(self, **kwargs):
        if kwargs.get('frameId') == self._frame_id and kwargs['type'] == 'Document':
            self._nav_result.status = kwargs['response']['status']
            self._nav_result.url = kwargs['response']['url']
            self._nav_result.headers = kwargs['response']['headers']

    def _get_request(self, **kwargs):
        if kwargs.get('frameId') == self._frame_id and kwargs.get('type') == 'Document':
            self._nav_result.request = kwargs['request']

    # ----------查找----------
    def ele(self, locator, index=1, timeout=None):
        return self._ele(locator, timeout=timeout, index=index, method='ele()')

    def eles(self, locator, timeout=None):
        return self._ele(locator, timeout=timeout, index=None)

    def _ele(self, locator, timeout=None, index=1, raise_err=None, method=None):
        if not locator:
            raise ElementNotFoundError(f'元素未找到：{method or "ele()"}() 定位符为空')
        if timeout is None:
            timeout = self.timeout
        r = self._find_elements(locator, timeout=timeout, index=index, raise_err=raise_err)
        if r or isinstance(r, (list, float, int)):
            return r
        if raise_err is True or (Settings.raise_when_ele_not_found and raise_err is None):
            raise ElementNotFoundError(f'元素未找到：{method or "ele()"}() '
                                       f'{{\'locator\': {locator!r}, \'index\': {index!r}}}')
        r.method = method
        r.args = {'locator': locator, 'index': index, 'timeout': timeout}
        return r

    def _find_elements(self, locator, timeout, index=1, relative=False, raise_err=None):
        if isinstance(locator, (Element, ShadowRoot)):
            return locator
        ast = parse_loc(locator)  # str|Q → AST；非法类型/语法在此抛 LocatorError（含 Did-You-Mean）
        self.wait.doc_loaded()
        if is_pure_xpath(ast):
            return _find_by_perform_search(self, to_xpath(ast), index, timeout)
        return _find_by_query(self, ast, index, timeout)

    def remove_ele(self, loc_or_ele):
        if not loc_or_ele:
            return
        ele = self._ele(loc_or_ele, raise_err=False)
        if ele:
            self._run_cdp('DOM.removeNode', nodeId=ele._node_id, _ignore=ElementLostError)

    def new_ele(self, html_or_info, insert_to=None, before=None):
        if isinstance(html_or_info, str):
            insert_to = self.ele(insert_to) if insert_to else self.ele('body')
            args = [html_or_info, insert_to]
            if before:
                args.append(insert_to.ele(before))
                js = '''
                     ele = document.createElement(null);
                     arguments[1].insertBefore(ele, arguments[2]);
                     ele.outerHTML = arguments[0];
                     return arguments[2].previousElementSibling;
                     '''
            else:
                js = '''
                     ele = document.createElement(null);
                     arguments[1].appendChild(ele);
                     ele.outerHTML = arguments[0];
                     return arguments[1].lastElementChild;
                     '''
        elif isinstance(html_or_info, tuple):
            args = [html_or_info[0], html_or_info[1]]
            txt = ''
            if insert_to:
                args.append(self.ele(insert_to))
                if before:
                    args.append(self.ele(before))
                    txt = 'arguments[2].insertBefore(ele, arguments[3]);'
                else:
                    txt = 'arguments[2].appendChild(ele);'
            js = f'''
                 ele = document.createElement(arguments[0]);
                 for(let k in arguments[1]){{
                    if(k=="innerHTML"){{ele.innerHTML=arguments[1][k]}}
                    else if(k=="innerText"){{ele.innerText=arguments[1][k]}}
                    else{{ele.setAttribute(k, arguments[1][k]);}}
                 }}
                 {txt}
                 return ele;
                 '''
        else:
            raise ValueError(f'html_or_info 参数格式不正确，只能是 html 字符串或 (tag, 属性字典) 元组，'
                             f'而不是 {html_or_info!r}')
        return self._run_js(js, *args)

    # ----------存储----------
    def session_storage(self, item=None):
        js = f'sessionStorage.getItem("{item}")' if item else 'sessionStorage'
        return self._run_js_loaded(js, as_expr=True)

    def local_storage(self, item=None):
        js = f'localStorage.getItem("{item}")' if item else 'localStorage'
        return self._run_js_loaded(js, as_expr=True)

    def clear_cache(self, session_storage=True, local_storage=True, cache=True, cookies=True):
        if session_storage and local_storage and cache and cookies:
            self._run_cdp_loaded('Storage.clearDataForOrigin', origin='*', storageTypes='all')
        if session_storage or local_storage:
            self.wait.doc_loaded()
            self._enable_domain('DOMStorage')
            i = self._run_cdp('Storage.getStorageKeyForFrame', frameId=self._frame_id)['storageKey']
            if session_storage:
                self._run_cdp('DOMStorage.clear', storageId={'storageKey': i, 'isLocalStorage': False})
            if local_storage:
                self._run_cdp('DOMStorage.clear', storageId={'storageKey': i, 'isLocalStorage': True})
            self.wait.doc_loaded()
            self._disable_domain('DOMStorage')
        if cache:
            self._run_cdp_loaded('Network.clearBrowserCache')
        if cookies:
            self._run_cdp_loaded('Network.clearBrowserCookies')

    def cookies(self, all_domains=False, all_info=False):
        """获取 cookie。all_domains 为 True 时取浏览器全部（跨域），否则只取当前 url 的。"""
        self.wait.doc_loaded()
        if all_domains:
            if self._browser._default_context and self._context_id == self._browser._context_id:
                cookies = self._browser._run_cdp('Storage.getCookies')['cookies']
            else:
                cookies = self._browser._run_cdp('Storage.getCookies',
                                                 browserContextId=self._context_id)['cookies']
        else:
            cookies = self._run_cdp_loaded('Network.getCookies')['cookies']

        if all_info:
            return CookiesList(cookies)
        return CookiesList([{'name': c['name'], 'value': c['value'], 'domain': c['domain']} for c in cookies])

    # ----------截图 / 保存----------
    def get_screenshot(self, path=None, name=None, as_bytes=None, as_base64=None,
                       full_page=False, left_top=None, right_bottom=None):
        """页面截图。path/name/as_bytes/as_base64 四选一；full_page 全页；left_top/right_bottom 裁剪。"""
        return self._get_screenshot(path=path, name=name, as_bytes=as_bytes, as_base64=as_base64,
                                    full_page=full_page, left_top=left_top, right_bottom=right_bottom)

    def _get_screenshot(self, path=None, name=None, as_bytes=None, as_base64=None,
                        full_page=False, left_top=None, right_bottom=None, ele=None):
        if as_bytes:
            if as_bytes is True:
                pic_type = 'png'
            else:
                if as_bytes not in ('jpg', 'jpeg', 'png', 'webp'):
                    raise ValueError(f'参数值错误。as_bytes 只能是 "jpg"、"jpeg"、"png"、"webp"，'
                                     f'当前是 {as_bytes}')
                pic_type = 'jpeg' if as_bytes == 'jpg' else as_bytes

        elif as_base64:
            if as_base64 is True:
                pic_type = 'png'
            else:
                if as_base64 not in ('jpg', 'jpeg', 'png', 'webp'):
                    raise ValueError(f'参数值错误。as_base64 只能是 "jpg"、"jpeg"、"png"、"webp"，'
                                     f'当前是 {as_base64}')
                pic_type = 'jpeg' if as_base64 == 'jpg' else as_base64

        else:
            path = str(path).rstrip('\\/') if path else '.'
            if not path.endswith(('.jpg', '.jpeg', '.png', '.webp')):
                if not name:
                    name = f'{self.title}.jpg'
                elif not name.endswith(('.jpg', '.jpeg', '.png', '.webp')):
                    name = f'{name}.jpg'
                from os.path import sep
                path = f'{path}{sep}{make_valid_name(name)}'

            path = Path(path)
            pic_type = path.suffix.lower()
            pic_type = 'jpeg' if pic_type == '.jpg' else pic_type[1:]

        if full_page:
            width, height = self.rect.size
            if width == 0 or height == 0:
                raise RuntimeError('页面宽高为 0，无法截图。')
            vp = {'x': 0, 'y': 0, 'width': width, 'height': height, 'scale': 1}
            args = {'format': pic_type, 'captureBeyondViewport': True, 'clip': vp}
        else:
            if left_top or right_bottom:
                if not left_top:
                    left_top = (0, 0)
                if not right_bottom:
                    right_bottom = self.rect.size
                x, y = left_top
                w = right_bottom[0] - x
                h = right_bottom[1] - y

                v = not (self._run_js(location_in_viewport_js(x, y))
                         and self._run_js(location_in_viewport_js(right_bottom[0], right_bottom[1])))
                if v and (self._run_js('return document.body.scrollHeight > window.innerHeight;')
                          and not self._run_js('return document.body.scrollWidth > window.innerWidth;')):
                    x += 10

                vp = {'x': x, 'y': y, 'width': w, 'height': h, 'scale': 1}
                args = {'format': pic_type, 'captureBeyondViewport': v, 'clip': vp}

            else:
                args = {'format': pic_type}

        if pic_type == 'jpeg':
            args['quality'] = 100
        png = self._run_cdp_loaded('Page.captureScreenshot', **args)['data']

        if as_base64:
            return png

        from base64 import b64decode
        png = b64decode(png)

        if as_bytes:
            return png

        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'wb') as f:
            f.write(png)
        return str(path.resolve())

    def save(self, path=None, name=None, as_pdf=False, kwargs=None):
        """把页面保存为 mhtml 或 pdf（默认 mhtml，由后缀/as_pdf 决定）。"""
        return save_page(self, path, name, as_pdf, kwargs)

    # ----------alert----------
    def handle_alert(self, accept=True, send=None, timeout=None, next_one=False):
        if not isinstance(accept, bool):
            return self._handle_alert(accept=accept, send=send, timeout=timeout, next_one=next_one)
        r = self._handle_alert(accept=accept, send=send, timeout=timeout, next_one=next_one)
        while self._has_alert:
            sleep(.0001)
        return r

    def _handle_alert(self, accept=True, send=None, timeout=None, next_one=False):
        if next_one:
            self._alert.handle_next = accept
            self._alert.next_text = send
            return
        if timeout is None:
            timeout = self.timeout
        timeout = .1 if timeout <= 0 else timeout
        end_time = perf_counter() + timeout
        while not self._alert.activated and perf_counter() < end_time:
            sleep(.01)
        if not self._alert.activated:
            return False

        res_text = self._alert.text
        if not isinstance(accept, bool):
            return res_text
        d = {'accept': accept, '_timeout': 0}
        if self._alert.type == 'prompt' and send is not None:
            d['promptText'] = send
        self._run_cdp('Page.handleJavaScriptDialog', **d)
        return res_text

    def _on_alert_open(self, **kwargs):
        self._alert.activated = True
        self._alert.text = kwargs['message']
        self._alert.type = kwargs['type']
        self._alert.defaultPrompt = kwargs.get('defaultPrompt')
        self._alert.response_accept = None
        self._alert.response_text = None
        self._has_alert = True

        if self._alert.auto is not None:
            if self._alert.auto != 'close':
                self._handle_alert(self._alert.auto)
        elif Settings.auto_handle_alert is not None:
            self._handle_alert(Settings.auto_handle_alert)
        elif self._alert.handle_next is not None:
            self._handle_alert(self._alert.handle_next, self._alert.next_text)
            self._alert.handle_next = None

    def _on_alert_close(self, **kwargs):
        self._alert.activated = False
        self._alert.text = None
        self._alert.type = None
        self._alert.defaultPrompt = None
        self._alert.response_accept = kwargs.get('result')
        self._alert.response_text = kwargs['userInput']
        self._has_alert = False

    # ----------其他----------
    def disconnect(self):
        self._stop_messenger()
        self.browser._tabs.remove_session(session_id=self._session_id)


class Alert(object):
    def __init__(self, auto=None):
        self.activated = False
        self.text = None
        self.type = None
        self.defaultPrompt = None
        self.response_accept = None
        self.response_text = None
        self.handle_next = None
        self.next_text = None
        self.auto = auto
