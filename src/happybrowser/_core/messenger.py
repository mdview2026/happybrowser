# -*- coding:utf-8 -*-
"""会话消息层（抽取自 DrissionPage © g1879 _base/base.py 的 Messenger，https://DrissionPage.cn，
遵循其许可：仅限个人学习与合法非盈利用途，商用需上游授权）。
职责：向 browser 要 sessionId、事件循环线程分发、域开关引用计数、回调注册。
改造点：独立成模块；逻辑保持一致。"""
from queue import Queue, Empty
from threading import Thread

from ..errors import PageDisconnectedError
from .tools import raise_error


class Messenger(object):
    def __init__(self):
        self._target_id = None
        self._session_id = None
        self._imm_events = set()
        self._event_queue = Queue()
        self._messenger_running = False
        if not hasattr(self, '_recv_th'):
            self._recv_th = Thread(target=self._handle_event_loop)
            self._recv_th.daemon = True
        self._event_handlers = {}
        self._debug = False
        self._run_func = self._run_cdp_
        self._enabled = {}

    @property
    def _run_cdp(self):
        return self._run_func

    def _start_messenger(self):
        self._messenger_running = True
        self._session_id = self._browser._get_session_id(self._target_id, obj=self)
        if not self._recv_th.is_alive():
            self._recv_th.start()

    def _stop_messenger(self):
        self._messenger_running = False
        self._browser._detach(self._session_id)
        self._run_func = self._raise_stopped

    def _run_cdp_(self, cmd, _ignore=None, _user=False, _timeout=None, **cmd_args):
        r = self._driver.run(cmd, _timeout=_timeout, _session_id=self._session_id, _debug=self._debug, **cmd_args)
        return r if 'error' not in r or _ignore is True else raise_error(r, self._browser, ignore=_ignore, user=_user)

    def _recv_event(self, msg):
        if self._imm_events and msg.get('method') in self._imm_events:
            functions = self._event_handlers.get(msg['method'])
            if functions:
                for func in functions:
                    func(**msg['params'])
            return
        self._event_queue.put(msg)

    def _handle_event_loop(self):
        while self._messenger_running:
            try:
                event = self._event_queue.get(timeout=1)
            except Empty:
                continue
            functions = self._event_handlers.get(event['method'])
            if functions:
                for func in functions:
                    func(**event['params'])
            self._event_queue.task_done()

    def _debug_recv_event(self, msg):
        if self._debug and (self._debug is True or msg['method'].startswith(self._debug)):
            print(f'收 {msg}')
        self._recv_event(msg)

    def _on_disconnect(self):
        pass

    def _enable_domain(self, domain, **kwargs):
        if domain in self._enabled:
            self._enabled[domain] += 1
        else:
            self._enabled[domain] = 1
            self._run_cdp(f'{domain}.enable', **kwargs)

    def _disable_domain(self, domain, **kwargs):
        if domain in self._enabled:
            self._enabled[domain] -= 1
            if self._enabled[domain] <= 0:
                self._run_cdp(f'{domain}.disable', **kwargs)
                self._enabled.pop(domain, None)

    def _set_callback(self, event, callback, immediate=False):
        if callback:
            self._event_handlers.setdefault(event, []).append(callback)
            if immediate:
                self._imm_events.add(event)
        else:
            self._event_handlers.pop(event, None)
            self._imm_events.discard(event)

    def _remove_callback(self, event, callback):
        functions = self._event_handlers.get(event)
        try:
            functions.remove(callback)
        except (ValueError, AttributeError):
            pass

    def _raise_stopped(self, *arg, **kwargs):
        raise PageDisconnectedError
