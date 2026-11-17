# -*- coding: utf-8 -*-
"""Frame 对象（移植精简自 DrissionPage © g1879 _pages/chromium_frame.py，https://DrissionPage.cn
—— 遵循其许可：仅限个人学习与合法非盈利用途，商用需上游授权）。
iframe/frame/object 元素经 make_ele 包装为本对象。同域 frame 与所属 tab 共用 target
（独立 session，事件按 frameId 过滤）；异域 frame（OOPIF）是独立 target。
改造点：
- 删 __call__ 别名与 sr 别名（设计砍除别名函数）；
- _get_document 的 frame 登记由「str() 正则抓 id」改为 _register_frames 递归（M3 同款改造）；
- title 直接读 document.title，不再走一次元素查找；
- 删 _get_screenshot 异域覆盖路径：frame_ele.get_screenshot 本就路由到所属 tab 的
  _get_screenshot（frame 元素在父文档中有矩形），该路径经公开 API 不可达。"""
from re import search, DOTALL
from threading import Thread
from time import sleep, perf_counter

from ..settings import Settings
from .._core.options import Timeout
from .._elements.element import Element
from .base import PageBase
from .._units.listener import FrameListener
from .._units.rect import FrameRect
from .._units.scroller import FrameScroller
from .._units.setter import FrameSetter
from .._units.states import FrameStates
from .._units.waiter import FrameWaiter
from ..errors import ContextLostError, ElementLostError, PageDisconnectedError, JavaScriptError


class Frame(PageBase):
    _Frames = {}

    def __new__(cls, owner, ele, info=None):
        fid = info['node']['frameId'] if info else owner._run_cdp(
            'DOM.describeNode', backendNodeId=ele._backend_id)['node']['frameId']
        if Settings.singleton_tab_obj and fid in cls._Frames:
            r = cls._Frames[fid]
            while not hasattr(r, '_type') or r._type != 'Frame':
                sleep(.01)
            return r
        r = object.__new__(cls)
        if Settings.singleton_tab_obj:
            cls._Frames[fid] = r
        return r

    def __init__(self, owner, ele, info=None):
        if Settings.singleton_tab_obj and hasattr(self, '_created'):
            return
        self._created = True

        self._tab = owner.tab
        self._target_page = owner
        self._backend_id = ele._backend_id
        self._frame_ele = ele
        self._reloading = False

        node = info['node'] if info else owner._run_cdp(
            'DOM.describeNode', backendNodeId=ele._backend_id)['node']
        try:
            if self._is_inner_frame(node['frameId']):
                self._is_diff_domain = False
                self.doc_ele = Element(self._target_page,
                                       backend_id=node['contentDocument']['backendNodeId'])
                super().__init__(owner.browser, owner._target_id, owner._context_id)
            else:
                self._is_diff_domain = True
                super().__init__(owner.browser, node['frameId'], owner._context_id)
                obj_id = super()._run_js('document;', as_expr=True)['objectId']
                self.doc_ele = Element(self, obj_id=obj_id)
            self._frame_id = node['frameId']

        except Exception as e:
            Frame._Frames.pop(node.get('frameId'), None)
            raise e

        # 运行时设置从所属页面复制一份（超时独立，互不影响）
        self._timeouts = Timeout(**vars(self._target_page.timeouts))
        self.retry_times = self._target_page.retry_times
        self.retry_interval = self._target_page.retry_interval
        self._download_path = self._target_page.download_path
        self._auto_handle_alert = self._target_page._auto_handle_alert
        self._load_mode = self._target_page._load_mode if not self._is_diff_domain else 'normal'

        self._type = 'Frame'

    def __repr__(self):
        attrs = ' '.join(f"{k}='{v}'" for k, v in self._frame_ele.attrs.items())
        return f'<Frame {self.frame_ele.tag} {attrs}>'

    def __eq__(self, other):
        return self._frame_id == getattr(other, '_frame_id', None)

    def __hash__(self):
        return hash(('Frame', self._frame_id))

    # ----------初始化与重载----------
    def _driver_init(self):
        super()._driver_init()
        self._imm_events.add('Page.frameDetached')  # frame 删除/交换事件须即时处理，不走队列

    def _do_reload(self):
        """同异域切换后重建对象（swap 事件触发，后台线程执行）。"""
        self._messenger_running = True
        self._is_loading = True
        self._reloading = True
        self._doc_got = False
        self._browser._detach(session_id=self._session_id)
        self._browser._tabs.remove_session(self._session_id)
        try:
            self._frame_ele = Element(self._target_page, backend_id=self._backend_id)
            end_time = perf_counter() + 2
            while perf_counter() < end_time:
                node = self._target_page._run_cdp('DOM.describeNode',
                                                  backendNodeId=self._frame_ele._backend_id)['node']
                if 'frameId' in node:
                    break
                sleep(.05)
            else:
                return
        except (ElementLostError, PageDisconnectedError):
            return

        if self._is_inner_frame(node['frameId']):
            self._is_diff_domain = False
            self.doc_ele = Element(self._target_page, backend_id=node['contentDocument']['backendNodeId'])
            if self._listener:
                self._listener._to_target(self._target_page.tab_id, self)
            super().__init__(self._browser, self._target_page.tab_id, self._context_id)
            self._frame_id = node['frameId']
            self._browser._tabs.add_frame(self._frame_id, self._target_page.tab_id)
        else:
            self._is_diff_domain = True
            if self._listener:
                self._listener._to_target(node['frameId'], self)
            end_time = perf_counter() + self.timeouts.page_load
            super().__init__(self._browser, node['frameId'], self._context_id)
            self._browser._tabs.add_frame(self._frame_id, self._target_id)
            timeout = end_time - perf_counter()
            if timeout <= 0:
                timeout = .5
            self._wait_loaded(timeout)

        self._type = 'Frame'
        self._is_loading = False
        self._reloading = False

    def _reload(self):
        Thread(target=self._do_reload).start()

    def _get_document(self, timeout=10):
        if self._is_reading:
            return None

        self._is_reading = True
        try:
            if not self._is_diff_domain:
                node = self._target_page._run_cdp('DOM.describeNode',
                                                  backendNodeId=self._backend_id)['node']
                self.doc_ele = Element(self._target_page,
                                       backend_id=node['contentDocument']['backendNodeId'])
            else:
                timeout = max(timeout, 2)
                b_id = self._run_cdp('DOM.getDocument', _timeout=timeout)['root']['backendNodeId']
                self.doc_ele = Element(self, backend_id=b_id)

            self._root_oid = self.doc_ele._obj_id

            r = self._run_cdp('Page.getFrameTree', _ignore=PageDisconnectedError)
            if r:
                self._register_frames(r['frameTree'])
            return True

        except Exception:
            return False

        finally:
            if not self._reloading:  # 阻止 reload 期间误清加载标识
                self._is_loading = False
            self._is_reading = False

    def _onFrameNavigated(self, **kwargs):
        if kwargs['frame']['id'] == self._target_id and not self._is_diff_domain:
            self._stop_messenger()
            return
        super()._onFrameNavigated(**kwargs)

    def _onFrameDetached(self, **kwargs):
        if kwargs['frameId'] == self._frame_id:
            if kwargs['reason'] == 'remove':  # iframe 被删除
                self.browser._tabs.remove_frame(kwargs['frameId'])
                Frame._Frames.pop(kwargs['frameId'], None)
                self._stop_messenger()
            elif kwargs['reason'] == 'swap':  # 同域变异域
                self._reload()
        else:
            super()._onFrameDetached(**kwargs)

    def _stop_messenger(self):
        super()._stop_messenger()
        if self._is_diff_domain and self._frame_id in str(
                self.tab._run_cdp('Page.getFrameTree', _ignore=True)):
            self._do_reload()

    # ----------挂件----------
    @property
    def scroll(self):
        self.wait.doc_loaded()
        if self._scroll is None:
            self._scroll = FrameScroller(self)
        return self._scroll

    @property
    def set(self):
        if self._setter is None:
            self._setter = FrameSetter(self)
        return self._setter

    @property
    def states(self):
        if self._states is None:
            self._states = FrameStates(self)
        return self._states

    @property
    def wait(self):
        if self._wait is None:
            self._wait = FrameWaiter(self)
        return self._wait

    @property
    def rect(self):
        if self._rect is None:
            self._rect = FrameRect(self)
        return self._rect

    @property
    def listen(self):
        if self._listener is None:
            self._listener = FrameListener(self)
        return self._listener

    # ----------基本信息----------
    @property
    def _obj_id(self):
        return self.frame_ele._obj_id

    @property
    def _node_id(self):
        return self.frame_ele._node_id

    @property
    def owner(self):
        return self.frame_ele.owner

    @property
    def frame_ele(self):
        return self._frame_ele

    @property
    def tag(self):
        return self.frame_ele.tag

    @property
    def url(self):
        try:
            return self.doc_ele._run_js('return this.location.href;')
        except JavaScriptError:
            return None

    @property
    def html(self):
        tag = self.tag
        out_html = self._target_page._run_cdp('DOM.getOuterHTML',
                                              backendNodeId=self.frame_ele._backend_id)['outerHTML']
        sign = search(rf'<{tag}.*?>', out_html, DOTALL).group(0)
        return f'{sign}{self.inner_html}</{tag}>'

    @property
    def inner_html(self):
        return self.doc_ele._run_js('return this.documentElement.outerHTML;')

    @property
    def link(self):
        return self.frame_ele.link

    @property
    def title(self):
        try:
            return self.doc_ele._run_js('return this.title;')
        except JavaScriptError:
            return None

    @property
    def attrs(self):
        return self.frame_ele.attrs

    @property
    def active_ele(self):
        return self.doc_ele._run_js('return this.activeElement;')

    @property
    def xpath(self):
        return self.frame_ele.xpath

    @property
    def css_selector(self):
        return self.frame_ele.css_selector

    @property
    def tab(self):
        return self._tab

    @property
    def tab_id(self):
        return self.tab.tab_id

    @property
    def shadow_root(self):
        return self.frame_ele.shadow_root

    @property
    def child_count(self):
        return int(self.doc_ele._run_js('return this.children.length;'))

    @property
    def _js_ready_state(self):
        if self._is_diff_domain:
            return super()._js_ready_state
        try:
            return self.doc_ele._run_js('return this.readyState;')
        except ContextLostError:
            try:
                node = self._run_cdp('DOM.describeNode', backendNodeId=self.frame_ele._backend_id)['node']
                doc = Element(self._target_page, backend_id=node['contentDocument']['backendNodeId'])
                return doc._run_js('return this.readyState;')
            except Exception:
                return None

    # ----------操作----------
    def refresh(self):
        self.doc_ele._run_js('this.location.reload();')
        return self

    def property(self, name):
        return self.frame_ele.property(name)

    def attr(self, name):
        return self.frame_ele.attr(name)

    def remove_attr(self, name):
        self.frame_ele.remove_attr(name)
        return self

    def style(self, style, pseudo_ele=''):
        return self.frame_ele.style(style=style, pseudo_ele=pseudo_ele)

    def run_js(self, script, *args, as_expr=False, timeout=None):
        return self._run_js(script, *args, as_expr=as_expr, timeout=timeout)

    def _run_js(self, script, *args, as_expr=False, timeout=None):
        if script.startswith('this.scrollIntoView'):
            return self.frame_ele._run_js(script, *args, as_expr=as_expr, timeout=timeout)
        return self.doc_ele._run_js(script, *args, as_expr=as_expr, timeout=timeout)

    def get_screenshot(self, path=None, name=None, as_bytes=None, as_base64=None):
        return self.frame_ele.get_screenshot(path=path, name=name, as_bytes=as_bytes, as_base64=as_base64)

    # ----------查找与相对定位----------
    def _find_elements(self, locator, timeout, index=1, relative=False, raise_err=None):
        timeout = timeout if timeout is not None else self.timeouts.base
        now = perf_counter()
        end_time = now + timeout
        while now <= end_time:
            try:
                return self.doc_ele._ele(locator, index=index, timeout=end_time - now, raise_err=raise_err)
            except ContextLostError:
                now = perf_counter()
        return self.doc_ele._ele(locator, index=index, timeout=0, raise_err=raise_err)

    def parent(self, level_or_loc=1, index=1, timeout=0):
        return self.frame_ele.parent(level_or_loc, index, timeout=timeout)

    def prev(self, locator='', index=1, timeout=None):
        return self.frame_ele.prev(locator, index, timeout)

    def next(self, locator='', index=1, timeout=None):
        return self.frame_ele.next(locator, index, timeout)

    def before(self, locator='', index=1, timeout=None):
        return self.frame_ele.before(locator, index, timeout)

    def after(self, locator='', index=1, timeout=None):
        return self.frame_ele.after(locator, index, timeout)

    def prevs(self, locator='', timeout=None):
        return self.frame_ele.prevs(locator, timeout)

    def nexts(self, locator='', timeout=None):
        return self.frame_ele.nexts(locator, timeout)

    def befores(self, locator='', timeout=None):
        return self.frame_ele.befores(locator, timeout)

    def afters(self, locator='', timeout=None):
        return self.frame_ele.afters(locator, timeout)

    def _is_inner_frame(self, fid=None):
        fid = fid or self._frame_id
        end_time = perf_counter() + 3
        while perf_counter() < end_time:
            try:
                return fid in str(self._target_page._run_cdp('Page.getFrameTree')['frameTree'])
            except Exception:
                pass
        return False
