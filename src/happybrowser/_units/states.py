# -*- coding: utf-8 -*-
"""状态单元（移植精简自 DrissionPage © g1879 _units/states.py，https://DrissionPage.cn
—— 遵循其许可：仅限个人学习与合法非盈利用途，商用需上游授权）。
改造点：M5 增补 FrameStates（随 Frame 对象收录）。"""
from .._functions.web import location_in_viewport_js
from ..errors import CDPError, NoRectError, PageDisconnectedError, ElementLostError


class ElementStates(object):
    def __init__(self, ele):
        self._ele = ele

    @property
    def is_selected(self):
        return self._ele._run_js('return this.selected;')

    @property
    def is_checked(self):
        return self._ele._run_js('return this.checked;')

    @property
    def is_displayed(self):
        return not (self._ele.style('visibility') == 'hidden'
                    or self._ele.style('display') == 'none'
                    or self._ele.property('hidden'))

    @property
    def is_enabled(self):
        return not self._ele._run_js('return this.disabled;')

    @property
    def is_alive(self):
        try:
            return self._ele.owner._run_cdp('DOM.describeNode',
                                            backendNodeId=self._ele._backend_id)['node']['nodeId'] != 0
        except ElementLostError:
            return False

    @property
    def is_in_viewport(self):
        x, y = self._ele.rect.click_point
        return self._ele.owner._run_js(location_in_viewport_js(int(x), int(y))) if x else False

    @property
    def is_whole_in_viewport(self):
        x1, y1 = self._ele.rect.location
        w, h = self._ele.rect.size
        x2, y2 = x1 + w, y1 + h
        owner = self._ele.owner
        return (owner._run_js(location_in_viewport_js(int(x1), int(y1)))
                and owner._run_js(location_in_viewport_js(int(x2), int(y2))))

    @property
    def is_covered(self):
        lx, ly = self._ele.rect.click_point
        try:
            bid = self._ele.owner._run_cdp('DOM.getNodeForLocation', x=int(lx), y=int(ly)).get('backendNodeId')
            return bid if bid != self._ele._backend_id else False
        except CDPError:
            return False

    @property
    def is_clickable(self):
        return self.has_rect and self.is_enabled and self.is_displayed and self._ele.style('pointer-events') != 'none'

    @property
    def has_rect(self):
        try:
            return self._ele.rect.corners
        except NoRectError:
            return False


class ShadowRootStates(object):
    def __init__(self, ele):
        self._ele = ele

    @property
    def is_enabled(self):
        return not self._ele._run_js('return this.disabled;')

    @property
    def is_alive(self):
        try:
            return self._ele.owner._run_cdp('DOM.describeNode',
                                            backendNodeId=self._ele._backend_id)['node']['nodeId'] != 0
        except ElementLostError:
            return False


class PageStates(object):
    """Page/Tab 对象使用。"""

    def __init__(self, owner):
        self._owner = owner

    @property
    def is_loading(self):
        return self._owner._is_loading

    @property
    def is_alive(self):
        try:
            self._owner._run_cdp('Page.getLayoutMetrics')
            return True
        except PageDisconnectedError:
            return False

    @property
    def ready_state(self):
        return self._owner._ready_state

    @property
    def has_alert(self):
        return self._owner._has_alert

    @property
    def is_headless(self):
        return self._owner.browser.is_headless

    @property
    def is_existed(self):
        return self._owner.browser.is_alive

    @property
    def is_incognito(self):
        return self._owner.browser.is_incognito


class FrameStates(object):
    """Frame 对象使用：存活/显示状态取自 frame 元素，加载状态取自 Frame 自身。"""

    def __init__(self, frame):
        self._frame = frame

    @property
    def is_loading(self):
        return self._frame._is_loading

    @property
    def is_alive(self):
        try:
            node = self._frame._target_page._run_cdp('DOM.describeNode',
                                                     backendNodeId=self._frame._frame_ele._backend_id)['node']
        except (ElementLostError, PageDisconnectedError):
            return False
        return 'frameId' in node

    @property
    def ready_state(self):
        return self._frame._ready_state

    @property
    def is_displayed(self):
        return not (self._frame.frame_ele.style('visibility') == 'hidden'
                    or self._frame.frame_ele._run_js('return this.offsetParent === null;')
                    or self._frame.frame_ele.style('display') == 'none')

    @property
    def has_alert(self):
        return self._frame._has_alert
