# -*- coding: utf-8 -*-
"""控制台监听（移植精简自 DrissionPage © g1879 _units/console.py，https://DrissionPage.cn
—— 遵循其许可：仅限个人学习与合法非盈利用途，商用需上游授权）。
改造点：Log/Runtime 域开关走 _enable_domain 引用计数（上游自管 _not_enabled 标志）。"""
from queue import Queue
from time import perf_counter, sleep

from .._core.tools import wait_until


class Console(object):
    def __init__(self, owner):
        self._owner = owner
        self._caught = None
        self.listening = False

    @property
    def messages(self):
        if self._caught is None:
            return []
        lst = []
        while not self._caught.empty():
            lst.append(self._caught.get_nowait())
        return lst

    def start(self):
        self._caught = Queue(maxsize=0)
        self._owner._set_callback('Log.entryAdded', self._onEntryAdded)
        self._owner._set_callback('Runtime.consoleAPICalled', self._onConsoleAPICalled)
        self._owner._enable_domain('Log')
        self._owner._enable_domain('Runtime')
        self.listening = True

    def stop(self):
        if self.listening:
            self._owner._set_callback('Log.entryAdded', None)
            self._owner._set_callback('Runtime.consoleAPICalled', None)
            self._owner._disable_domain('Log')
            self._owner._disable_domain('Runtime')
            self.listening = False

    def clear(self):
        self._caught = Queue(maxsize=0)

    def wait(self, timeout=None):
        if not self.listening:
            raise RuntimeError('Console 尚未开始监听。')

        def do():
            if not self._owner._messenger_running or not self.listening:
                return False
            return self._caught.get_nowait() if self._caught.qsize() else None

        r = wait_until(do, timeout=timeout)
        return r if r is not None else False

    def steps(self, timeout=None):
        """逐条产出控制台消息；timeout 为 None 时持续产出直到停止监听，
        否则每产出一条重置计时。"""
        if timeout is None:
            while self._owner._messenger_running and self.listening:
                if self._caught.qsize():
                    yield self._caught.get_nowait()
                sleep(0.05)
        else:
            end = perf_counter() + timeout
            while self._owner._messenger_running and self.listening and perf_counter() < end:
                if self._caught.qsize():
                    yield self._caught.get_nowait()
                    end = perf_counter() + timeout
                sleep(0.05)

    def _onEntryAdded(self, **kwargs):
        self._caught.put(ConsoleData(kwargs['entry']))

    def _onConsoleAPICalled(self, **kwargs):
        self._caught.put(ConsoleData(kwargs))


class ConsoleData(object):
    def __init__(self, data):
        self._data = data

    def __getattr__(self, item):
        return self._data.get(item)

    @property
    def data(self):
        return self._data
