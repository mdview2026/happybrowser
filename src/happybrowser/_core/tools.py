# -*- coding:utf-8 -*-
"""通用工具（移植精简自 DrissionPage © g1879 _functions/tools.py，https://DrissionPage.cn，
遵循其许可：仅限个人学习与合法非盈利用途，商用需上游授权）。
改造点：去除 ini/语言包依赖与 win32 窗口工具；报错中文内联；新增 parse_proxy。"""
from pathlib import Path
from shutil import rmtree
from tempfile import gettempdir
from threading import Lock
from time import perf_counter, sleep
from urllib.parse import urlsplit

from ..errors import (AlertExistsError, BrowserConnectError, CDPError, ContextLostError, CookieFormatError,
                      ElementLostError, IncorrectURLError, JavaScriptError, NoRectError, PageDisconnectedError,
                      StorageError, WaitTimeoutError)
from ..settings import Settings


class PortFinder(object):
    """寻找可用的自动端口。"""
    used_port = set()
    prev_time = 0
    lock = Lock()
    checked_paths = set()

    def __init__(self, path=None):
        self.tmp_dir = Path(path or gettempdir()) / 'happybrowser' / 'autoPortData'
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp = str(self.tmp_dir.resolve())
        if tmp not in PortFinder.checked_paths:
            for i in self.tmp_dir.iterdir():
                if i.is_dir() and not port_is_using('127.0.0.1', i.name):
                    rmtree(i, ignore_errors=True)
            PortFinder.checked_paths.add(tmp)

    def get_port(self, scope=None):
        from random import randint
        with self.lock:
            if self.prev_time and perf_counter() - self.prev_time > 60:
                self.used_port.clear()
            if scope in (True, None):
                scope = (9600, 59600)
            max_times = scope[1] - scope[0]
            times = 0
            while times < max_times:
                times += 1
                port = randint(*scope)
                if port in self.used_port or (self.tmp_dir / f'{port}').exists() or port_is_using('127.0.0.1', port):
                    continue
                self.used_port.add(port)
                self.prev_time = perf_counter()
                return port
            raise BrowserConnectError('没有找到可用端口。')


def port_is_using(ip, port):
    from socket import socket, AF_INET, SOCK_STREAM
    s = socket(AF_INET, SOCK_STREAM)
    s.settimeout(.1)
    result = s.connect_ex((ip, int(port)))
    s.close()
    return result == 0


def parse_proxy(proxy):
    """把 'http://user:pwd@host:port' 形式的代理字符串解析为 (server, user, pwd, 原文)。"""
    if not proxy:
        return None, None, None, None
    text = proxy if '://' in proxy else f'http://{proxy}'
    u = urlsplit(text)
    server = f'{u.scheme}://{u.hostname}' + (f':{u.port}' if u.port else '')
    return server, u.username, u.password, proxy


def wait_until(func, timeout, gap=.01, err_txt=None, **kwargs):
    """轮询等待 func 返回非 None；timeout 为 None 时无限等待；超时可抛 WaitTimeoutError。"""
    if timeout is None:
        r = func(**kwargs)
        while r is None:
            sleep(gap)
            r = func(**kwargs)
        return r
    end_time = perf_counter() + timeout
    r = func(**kwargs)
    if r is None and timeout:
        while r is None and perf_counter() < end_time:
            sleep(gap)
            r = func(**kwargs)
    if r is not None:
        return r
    elif err_txt is not None:
        raise WaitTimeoutError(err_txt)
    return None


def raise_error(result, browser, ignore=None, user=False):
    """把 driver.run 返回的错误字典转换为对应异常并抛出。"""
    error = result['error']
    if error in ('Cannot find context with specified id', 'Inspected target navigated or closed',
                 'No frame with given id found'):
        r = ContextLostError(f'页面上下文已消失。信息：{error}')
    elif error in ('Could not find node with given id', 'Could not find object with given id',
                   'No node with given id found', 'Node with given id does not belong to the document',
                   'No node found for given backend id'):
        r = ElementLostError(f'元素已从页面脱离。信息：{error}')
    elif error in ('connection disconnected', 'No target with given id found', 'Session with given id not found.'):
        r = PageDisconnectedError(f'页面或浏览器连接已断开。信息：{error}')
    elif error == 'alert exists.':
        r = AlertExistsError('存在未处理的 alert 对话框。')
    elif error in ('Node does not have a layout object', 'Could not compute box model.'):
        r = NoRectError(f'元素无矩形（不可见或无尺寸）。信息：{error}')
    elif error == 'Cannot take screenshot with 0 height.':
        r = NoRectError(f'截图高度为 0，可能元素不可见。信息：{error}')
    elif error == 'Cannot navigate to invalid URL':
        r = IncorrectURLError(f'URL 格式不正确：{result["args"].get("url")}')
    elif error == 'Frame corresponds to an opaque origin and its storage key cannot be serialized':
        r = StorageError(f'存储访问失败（跨域限制）。信息：{error}')
    elif error == 'Sanitizing cookie failed':
        r = CookieFormatError(f'cookie 格式不正确：{result["args"]}')
    elif error == 'Invalid header name':
        r = ValueError(f'请求头名称不合法：{result["args"].get("headers")}')
    elif error == 'Given expression does not evaluate to a function':
        r = JavaScriptError(f'给定的 JS 不是函数：{result["args"].get("functionDeclaration")}')
    elif error.endswith("' wasn't found"):
        r = RuntimeError(f'浏览器不支持该方法：{result["method"]}（浏览器版本：{browser.version}）')
    elif error == 'timeout':
        r = TimeoutError(f'CDP 调用超时：{result["method"]}，参数：{result["args"]}')
    elif user:
        r = RuntimeError(f'未知错误：{result}')
    else:
        r = CDPError(f'CDP 调用出错：{result["method"]}，参数：{result["args"]}，信息：{error}')

    if ignore is True or (ignore is not None and isinstance(r, ignore)):
        return result
    raise r


def ensure_del_dir(path):
    """删除目录，被占用时重试最多 5 秒。"""
    if not Path(path).exists():
        return True
    end_time = perf_counter() + 5
    while perf_counter() < end_time:
        try:
            rmtree(path)
            return True
        except FileNotFoundError:
            return True
        except (PermissionError, OSError):
            sleep(.01)
    return False


__all__ = ['PortFinder', 'Settings', 'ensure_del_dir', 'parse_proxy', 'port_is_using', 'raise_error', 'wait_until']
