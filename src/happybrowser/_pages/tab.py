# -*- coding: utf-8 -*-
"""Tab 对象（移植改造自 DrissionPage © g1879 _pages/chromium_tab.py 的骨架，
https://DrissionPage.cn —— 遵循其许可：仅限个人学习与合法非盈利用途，商用需上游授权）。
M3 起继承 PageBase：加载状态机、查找闭环、alert、导航重试均由基类提供。
Tab 自身保留：单例缓存、激活/关闭。坐标/滚动/actions 等随后续里程碑接入。"""
from threading import Lock

from ..settings import Settings
from .base import PageBase


class Tab(PageBase):
    _TABS = {}
    _lock = Lock()

    def __new__(cls, browser, tab_id, context_id=None):
        with cls._lock:
            if Settings.singleton_tab_obj and tab_id in cls._TABS:
                return cls._TABS[tab_id]
            o = super().__new__(cls)
            if Settings.singleton_tab_obj:
                cls._TABS[tab_id] = o
            return o

    def __init__(self, browser, tab_id, context_id=None):
        if hasattr(self, '_created'):
            return
        self._created = True
        super().__init__(browser, tab_id, context_id or browser._browser_id)

    # ----------管理----------
    def activate(self):
        self._browser.activate_tab(self.tab_id)
        return self

    def close(self, others=False):
        if others:
            others_ids = [i for i in self._browser.tab_ids if i != self.tab_id]
            self._browser.close_tabs(others_ids)
        else:
            self._browser.close_tabs(self.tab_id)

    # ----------内部----------
    def _on_disconnect(self):
        Tab._TABS.pop(self._target_id, None)

    def __repr__(self):
        return f'<Tab {self._target_id}>'
