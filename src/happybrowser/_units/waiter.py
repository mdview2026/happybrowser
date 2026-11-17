# -*- coding: utf-8 -*-
"""等待器（移植精简自 DrissionPage © g1879 _units/waiter.py，https://DrissionPage.cn
—— 遵循其许可：仅限个人学习与合法非盈利用途，商用需上游授权）。
改造点：
- 删 eles_loaded（设计砍除：与 eles()+推导式重叠）；
- 删 BrowserContextWaiter（Browser 的 wait 即浏览器级，context.new_tab 直接用 browser.wait_new_tab）；
- M5 增补下载等待族（download_begin/downloads_done）与 upload_paths_inputted、
  FrameWaiter（随 Frame 对象收录，url/title 变化取 frame 自身值）；
- FrameWaiter 随 Frame 对象收录于 _pages/frame.py；
- is_selenium_loc 判定随 Selenium 兼容层删除。"""
from time import perf_counter, sleep
from random import uniform

from ..settings import Settings
from .._core.tools import wait_until
from ..errors import WaitTimeoutError, NoRectError


class OriginWaiter(object):
    def __init__(self, owner):
        self._owner = owner

    def __call__(self, second, scope=None):
        if scope is None:
            sleep(second)
        else:
            sleep(uniform(second, scope))
        return self._owner


class BrowserWaiter(OriginWaiter):
    """浏览器级等待。"""

    def new_tab(self, timeout=None, curr_tab=None, raise_err=None):
        """等待新 tab 出现，返回新 tab id（此时 Tab 对象可能尚未就绪，建议配合 get_tab 使用）。"""
        if timeout is None:
            timeout = self._owner.timeout
        r = self._owner.wait_new_tab(curr_tab=curr_tab, timeout=timeout)
        if r:
            return r
        if raise_err is True or (Settings.raise_when_wait_failed and raise_err is None):
            raise WaitTimeoutError(f'等待失败：新标签页，超时 {timeout}')
        return False

    def download_begin(self, timeout=None, cancel_it=False):
        if not self._owner._dl_mgr._running:
            raise RuntimeError('请先设置下载路径。')
        self._owner._dl_mgr.set_flag('browser', False if cancel_it else True)
        if timeout is None:
            timeout = self._owner.timeout
        return wait_mission(self._owner, 'browser', timeout)

    def downloads_done(self, timeout=None, cancel_if_timeout=True):
        if not self._owner._dl_mgr._running:
            raise RuntimeError('请先设置下载路径。')

        def do():
            if self._owner._messenger_running:
                return True if not self._owner._dl_mgr._missions else None
            return False

        r = wait_until(do, timeout=timeout, gap=.5)
        if r is not None:
            return r
        if cancel_if_timeout:
            for m in list(self._owner._dl_mgr._missions.values()):
                m.cancel()
        return False


class PageWaiter(OriginWaiter):
    def ele_deleted(self, loc_or_ele, timeout=None, raise_err=None):
        ele = self._owner._ele(loc_or_ele, raise_err=False, timeout=0)
        return ele.wait.deleted(timeout, raise_err=raise_err) if ele else True

    def ele_displayed(self, loc_or_ele, timeout=None, raise_err=None):
        if timeout is None:
            timeout = self._owner.timeout
        end_time = perf_counter() + timeout
        ele = self._owner._ele(loc_or_ele, raise_err=False, timeout=timeout)
        if not ele:
            if raise_err is True or (Settings.raise_when_wait_failed and raise_err is None):
                raise WaitTimeoutError(f'等待失败：元素显示，超时 {timeout}')
            return False
        return ele.wait.displayed(end_time - perf_counter(), raise_err=raise_err)

    def ele_hidden(self, loc_or_ele, timeout=None, raise_err=None):
        if timeout is None:
            timeout = self._owner.timeout
        end_time = perf_counter() + timeout
        ele = self._owner._ele(loc_or_ele, raise_err=False, timeout=timeout)
        timeout = end_time - perf_counter()
        if timeout <= 0:
            if raise_err is True or (Settings.raise_when_wait_failed and raise_err is None):
                raise WaitTimeoutError(f'等待失败：元素隐藏，超时 {timeout}')
            return False
        return ele.wait.hidden(timeout, raise_err=raise_err)

    def load_start(self, timeout=None, raise_err=None):
        return self._loading(timeout=timeout, gap=.002, raise_err=raise_err)

    def doc_loaded(self, timeout=None, raise_err=None):
        return self._loading(timeout=timeout, start=False, raise_err=raise_err)

    def upload_paths_inputted(self, timeout=None):
        """等待 upload_files 设置的路径被文件选择框消费。"""
        def do():
            if self._owner._messenger_running:
                return True if not self._owner._upload_list else None
            return False

        if timeout is None:
            timeout = self._owner.timeout
        r = wait_until(do, timeout=timeout)
        return r if r is not None else False

    def download_begin(self, timeout=None, cancel_it=False):
        if not self._owner.browser._dl_mgr._running:
            raise RuntimeError('请先设置下载路径。')
        self._owner.browser._dl_mgr.set_flag(self._owner.tab_id, False if cancel_it else True)
        if timeout is None:
            timeout = self._owner.timeout
        return wait_mission(self._owner.browser, self._owner.tab_id, timeout)

    def downloads_done(self, timeout=None, cancel_if_timeout=True):
        if not self._owner.browser._dl_mgr._running:
            raise RuntimeError('请先设置下载路径。')
        if not timeout:
            while self._owner.browser._dl_mgr.get_tab_missions(self._owner.tab_id):
                sleep(.5)
            return self._owner

        def do():
            return self._owner if not self._owner.browser._dl_mgr.get_tab_missions(self._owner.tab_id) else None

        r = wait_until(do, timeout=timeout, gap=.5)
        if r is not None:
            return r
        if cancel_if_timeout:
            for m in self._owner.browser._dl_mgr.get_tab_missions(self._owner.tab_id):
                m.cancel()
        return False

    def alert_closed(self, timeout=None):
        """等待 alert 弹出又关闭。timeout 为 None 时无限等待。"""
        if timeout is None:
            while not self._owner.states.has_alert:
                sleep(.2)
            while self._owner.states.has_alert:
                sleep(.2)
        else:
            end_time = perf_counter() + timeout
            while not self._owner.states.has_alert and perf_counter() < end_time:
                sleep(.2)
            while self._owner.states.has_alert and perf_counter() < end_time:
                sleep(.2)
        return False if self._owner.states.has_alert else self._owner

    def url_change(self, text, exclude=False, timeout=None, raise_err=None):
        return self._owner if self._change('url', text, exclude, timeout, raise_err) else False

    def title_change(self, text, exclude=False, timeout=None, raise_err=None):
        return self._owner if self._change('title', text, exclude, timeout, raise_err) else False

    def _change(self, arg, text, exclude=False, timeout=None, raise_err=None):
        if timeout is None:
            timeout = self._owner.timeout
        err_txt = (f'等待失败：{arg} 变化，超时 {timeout}'
                   if raise_err is True or (Settings.raise_when_wait_failed and raise_err is None) else None)
        r = wait_until(self._do, timeout=timeout, err_txt=err_txt, arg=arg, text=text, exclude=exclude)
        return r if r is not None else False

    def _do(self, arg, text, exclude):
        v = self._owner._run_cdp('Target.getTargetInfo', targetId=self._owner._target_id)['targetInfo'][arg]
        return True if (not exclude and text in v) or (exclude and text not in v) else None

    def _loading(self, timeout=None, start=True, gap=.01, raise_err=None):
        def do():
            return True if self._owner._is_loading == start else None

        if timeout is None:
            timeout = self._owner.timeout
        err_txt = (f'等待失败：页面加载，超时 {timeout}'
                   if raise_err is True or (Settings.raise_when_wait_failed and raise_err is None) else None)
        r = wait_until(do, timeout=timeout, err_txt=err_txt)
        return r if r is not None else False


class ElementWaiter(OriginWaiter):
    def __init__(self, owner):
        super().__init__(owner)
        self._ele = owner

    @property
    def _timeout(self):
        return self._ele.timeout

    def deleted(self, timeout=None, raise_err=None):
        return bool(self._wait_state('is_alive', False, timeout, raise_err, err_text='元素被删除'))

    def displayed(self, timeout=None, raise_err=None):
        return self._wait_state('is_displayed', True, timeout, raise_err, err_text='元素显示')

    def hidden(self, timeout=None, raise_err=None):
        return self._wait_state('is_displayed', False, timeout, raise_err, err_text='元素隐藏')

    def covered(self, timeout=None, raise_err=None):
        return self._wait_state('is_covered', True, timeout, raise_err, err_text='元素被遮挡')

    def not_covered(self, timeout=None, raise_err=None):
        return self._wait_state('is_covered', False, timeout, raise_err, err_text='元素不被遮挡')

    def enabled(self, timeout=None, raise_err=None):
        return self._wait_state('is_enabled', True, timeout, raise_err, err_text='元素可用')

    def disabled(self, timeout=None, raise_err=None):
        return self._wait_state('is_enabled', False, timeout, raise_err, err_text='元素不可用')

    def disabled_or_deleted(self, timeout=None, raise_err=None):
        def do():
            return self._ele if not self._ele.states.is_enabled or not self._ele.states.is_alive else None

        if timeout is None:
            timeout = self._timeout
        err_txt = (f'等待失败：元素隐藏或删除，超时 {timeout}'
                   if raise_err is True or (Settings.raise_when_wait_failed and raise_err is None) else None)
        r = wait_until(do, timeout=timeout, err_txt=err_txt)
        return r if r is not None else False

    def clickable(self, wait_stop=None, timeout=None, raise_err=None):
        if timeout is None:
            timeout = self._timeout
        t1 = perf_counter()
        r = self._wait_state('is_clickable', True, timeout, raise_err, err_text='元素可点击')
        r = (self.stop_moving(timeout=timeout - perf_counter() + t1)
             if (Settings.wait_stop_before_click if wait_stop is None else wait_stop) and r else r)
        if raise_err and not r:
            raise WaitTimeoutError(f'等待失败：元素可点击，超时 {timeout}')
        return r

    def has_rect(self, timeout=None, raise_err=None):
        return self._wait_state('has_rect', True, timeout, raise_err, err_text='元素拥有矩形')

    def stop_moving(self, timeout=None, gap=.05, raise_err=None):
        if timeout is None:
            timeout = self._timeout
        end_time = perf_counter() + timeout

        def do():
            try:
                return self._ele.rect.size, self._ele.rect.location
            except NoRectError:
                return None

        r = wait_until(do, timeout=timeout)
        if r is None:
            raise NoRectError
        size, location = r
        sleep(gap)
        if self._ele.rect.size == size and self._ele.rect.location == location:
            return self._ele
        while perf_counter() < end_time:
            sleep(gap)
            if self._ele.rect.size == size and self._ele.rect.location == location:
                return self._ele
            size = self._ele.rect.size
            location = self._ele.rect.location

        if raise_err is True or (Settings.raise_when_wait_failed and raise_err is None):
            raise WaitTimeoutError(f'等待失败：元素停止移动，超时 {timeout}')
        return False

    def _wait_state(self, attr, mode=False, timeout=None, raise_err=None, err_text=None):
        def do():
            a = self._ele.states.__getattribute__(attr)
            return (self._ele if isinstance(a, bool) else a) if (a and mode) or (not a and not mode) else None

        if timeout is None:
            timeout = self._timeout
        r = wait_until(do, timeout=timeout)
        if r is not None:
            return r
        if raise_err is True or (Settings.raise_when_wait_failed and raise_err is None):
            raise WaitTimeoutError(f'等待失败：{err_text or "元素状态变化"}，超时 {timeout}')
        return False


class FrameWaiter(PageWaiter, ElementWaiter):
    """Frame 对象使用：url/title 变化取 frame 自身的 url/title。"""

    def __init__(self, owner):
        ElementWaiter.__init__(self, owner)

    @property
    def _timeout(self):
        return self._owner.timeout

    def _do(self, arg, text, exclude):
        v = get_frame_url(self._owner) if arg == 'url' else get_frame_title(self._owner)
        return True if (not exclude and text in v) or (exclude and text not in v) else None


def wait_mission(browser, tid, timeout):
    """等待 tab 的下载任务出现（flag 由 bool 变为 DownloadMission）。"""
    def do():
        if browser._messenger_running:
            v = browser._dl_mgr.get_flag(tid)
            return v if not isinstance(v, bool) else None
        return False

    r = wait_until(do, timeout, gap=.005)
    browser._dl_mgr.set_flag(tid, None)
    return r if r is not None else False


def get_frame_title(page):
    try:
        return page.run_js('return document.title;')
    except Exception:
        return ''


def get_frame_url(page):
    r = page._run_cdp('Page.getFrameTree')
    frames = [{'frame': r['frameTree']['frame']}]
    if 'childFrames' in r['frameTree']:
        frames.extend(r['frameTree']['childFrames'])
    for i in frames:
        if i['frame']['id'] == page._frame_id:
            return i['frame'].get('url')
    return None
