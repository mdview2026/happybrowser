# -*- coding: utf-8 -*-
"""点击器（移植精简自 DrissionPage © g1879 _units/clicker.py，https://DrissionPage.cn
—— 遵循其许可：仅限个人学习与合法非盈利用途，商用需上游授权）。
改造点：
- 新 tab 相关改用 browser.wait_new_tab + get_tab；
- option 点击分支的旧语法 't:select' 改 DSL，cancel_by_option → by_option(..., cancel=True)；
- M5 增补 to_download/to_upload（依赖 DownloadManager 与 setter）。"""
from pathlib import Path
from random import randint
from time import perf_counter

from ..settings import Settings
from .._functions.web import offset_scroll
from .._units.downloader import TabDownloadSettings
from .._units.waiter import wait_mission
from ..errors import CanNotClickError, CDPError, NoRectError, AlertExistsError, JavaScriptError


class Clicker(object):
    def __init__(self, ele):
        self._ele = ele

    def __call__(self, by_js=False, timeout=2, wait_stop=None):
        return self.left(by_js, timeout, wait_stop)

    def left(self, by_js=False, timeout=2, wait_stop=None):
        if self._ele.tag == 'option':
            if not self._ele.states.is_selected:
                self._ele.parent('select').select.by_option(self._ele)
            else:
                select = self._ele.parent('select')
                if select.select.is_multi:
                    self._ele.parent('select').select.by_option(self._ele, cancel=True)
            return self._ele

        if not by_js:  # 模拟点击
            can_click = False
            if timeout is None:
                timeout = self._ele.timeout
            rect = None
            if timeout == 0:
                try:
                    self._ele.scroll.to_see()
                    if self._ele.states.is_enabled and self._ele.states.is_displayed:
                        rect = self._ele.rect.viewport_corners
                        can_click = True
                except NoRectError:
                    if by_js is False:
                        raise

            else:
                end_time = perf_counter() + timeout
                rect = self._ele.wait.has_rect(timeout=timeout, raise_err=False)
                if rect:
                    self._ele.scroll.to_see()
                    rect = self._ele.rect.viewport_corners
                    can_click = self._ele.wait.clickable(timeout=end_time - perf_counter(),
                                                         wait_stop=wait_stop, raise_err=False)

                elif by_js is False:
                    raise NoRectError

            if can_click and not self._ele.states.is_in_viewport:
                by_js = True

            elif can_click and (by_js is False or not self._ele.states.is_covered):
                x_start = int(rect[0][0])
                x_end = int(rect[1][0])
                y_start = int(rect[0][1])
                y_end = int(rect[2][1])
                try:
                    for _ in range(20):
                        vx = randint(x_start, x_end)
                        vy = randint(y_start, y_end)
                        if (self._ele.owner._run_cdp('DOM.getNodeForLocation', x=vx, y=vy,
                                                     ignorePointerEventsNone=True,
                                                     includeUserAgentShadowDOM=True)['backendNodeId']
                                == self._ele._backend_id):
                            break
                    else:
                        vx, vy = self._ele.rect.viewport_click_point

                except CDPError:
                    vx, vy = self._ele.rect.viewport_midpoint

                self._click(vx, vy)
                return self._ele

        if by_js is not False:
            try:
                self._ele._run_js('this.click();')
            except JavaScriptError:
                self._ele._run_js("function(){this.dispatchEvent(new MouseEvent('click', "
                                  "{bubbles: true,cancelable: true,view: window}));}")
            return self._ele
        if Settings.raise_when_click_failed:
            raise CanNotClickError
        return False

    def right(self):
        self._ele.owner.scroll.to_see(self._ele)
        return self._click(*self._ele.rect.viewport_click_point, button='right')

    def middle(self, get_tab=True):
        self._ele.owner.scroll.to_see(self._ele)
        browser = self._ele.owner.browser
        curr_tid = browser._tabs.get_newest_tab(self._ele.owner._context_id)
        self._click(*self._ele.rect.viewport_click_point, button='middle')
        if get_tab:
            tid = browser.wait_new_tab(curr_tab=curr_tid, timeout=5)
            if not tid:
                raise RuntimeError('没有出现新标签页')
            return browser.get_tab(tid)

    def at(self, offset_x=None, offset_y=None, button='left', count=1):
        self._ele.owner.scroll.to_see(self._ele)
        if offset_x is None and offset_y is None:
            w, h = self._ele.rect.size
            offset_x = w // 2
            offset_y = h // 2
        return self._click(*offset_scroll(self._ele, offset_x, offset_y), button=button, count=count)

    def multi(self, times=2):
        return self.at(count=times)

    def to_download(self, save_path=None, rename=None, suffix=None, new_tab=None, by_js=False, timeout=None,
                    file_exists=None):
        """点击触发下载并等待任务出现，返回 DownloadMission。"""
        tab = self._ele.tab
        browser = tab.browser
        if not browser._dl_mgr._running:
            browser.set.download_path('.')

        when_file_exists = None
        tmp_path = None
        if new_tab:
            obj = browser
            tid = 'browser'
            t_settings = TabDownloadSettings(tab.tab_id)
            b_settings = TabDownloadSettings('browser')

            when_file_exists = b_settings.when_file_exists
            b_settings.when_file_exists = t_settings.when_file_exists
            b_settings.rename = t_settings.rename
            b_settings.suffix = t_settings.suffix
            t_settings.rename = None
            t_settings.suffix = None
            if not save_path and b_settings.path != t_settings.path:
                tmp_path = b_settings.path
                b_settings.path = t_settings.path
        else:
            obj = tab
            tid = tab.tab_id
            browser._dl_mgr._waiting_tab.add(tab.tab_id)

        if save_path:
            tmp_path = obj.download_path
            TabDownloadSettings(tid).path = str(Path(save_path).resolve())
        if rename or suffix:
            obj.set.download_file_name(rename, suffix)
        if timeout is None:
            timeout = obj.timeout

        browser._dl_mgr.set_flag(tid, True)
        self.left(by_js=by_js)
        m = wait_mission(browser, tid, timeout)
        types = {'rename': 'rename', 'overwrite': 'overwrite', 'skip': 'skip',
                 'r': 'rename', 'o': 'overwrite', 's': 'skip'}
        if m and file_exists in types:
            m._when_file_exists = types[file_exists]
        if tmp_path:
            TabDownloadSettings(tid).path = tmp_path
        if when_file_exists:
            browser.set.when_download_file_exists(when_file_exists)
        if m and new_tab:
            browser._dl_mgr._tab_missions.setdefault(tab.tab_id, set()).add(m)
            m.from_tab = tab.tab_id
        browser._dl_mgr._waiting_tab.discard(tab.tab_id)
        return m

    def to_upload(self, file_paths, by_js=False):
        """点击文件输入框并自动填入 upload_files 设置的路径。"""
        self._ele.owner.set.upload_files(file_paths)
        self.left(by_js=by_js)
        self._ele.owner.wait.upload_paths_inputted()
        return self._ele

    def for_new_tab(self, by_js=False, timeout=3):
        browser = self._ele.owner.browser
        curr_tid = browser._tabs.get_newest_tab(self._ele.owner._context_id)
        self.left(by_js=by_js)
        tid = browser.wait_new_tab(curr_tab=curr_tid, timeout=timeout)
        if not tid:
            raise RuntimeError('没有出现新标签页')
        return browser.get_tab(tid)

    def for_url_change(self, text=None, exclude=False, by_js=False, timeout=None):
        if text is None:
            exclude = True
            text = self._ele.owner.url
        self.left(by_js=by_js)
        return True if self._ele.owner.wait.url_change(text=text, exclude=exclude, timeout=timeout) else False

    def for_title_change(self, text=None, exclude=False, by_js=False, timeout=None):
        if text is None:
            exclude = True
            text = self._ele.owner.title
        self.left(by_js=by_js)
        return True if self._ele.owner.wait.title_change(text=text, exclude=exclude, timeout=timeout) else False

    def _click(self, view_x, view_y, button='left', count=1):
        self._ele.owner._run_cdp('Input.dispatchMouseEvent', type='mousePressed', x=view_x,
                                 y=view_y, button=button, clickCount=count, _ignore=AlertExistsError)
        self._ele.owner._run_cdp('Input.dispatchMouseEvent', type='mouseReleased', x=view_x,
                                 y=view_y, button=button, _ignore=AlertExistsError)
        return self._ele
