# -*- coding:utf-8 -*-
"""浏览器进程启动/接管/路径探测（移植精简自 DrissionPage © g1879 _functions/browser.py，
https://DrissionPage.cn —— 遵循其许可：仅限个人学习与合法非盈利用途，商用需上游授权）。
改造点：
- 去除 set_prefs / set_flags / system_user_path（写 Preferences、Local State 的机制）
- happybrowser 接管语义：端口活着且是调试端口 → 纯接管（绝不杀进程重启）；
  mode='attach' 只接管；mode='launch' 只启动（端口被占即报错）
- 临时目录 DrissionPage → happybrowser"""
from os import environ, path as os_path, pathsep, X_OK, access
from pathlib import Path
from subprocess import Popen, DEVNULL
from sys import platform

from requests import Session

from ..settings import Settings
from ..errors import BrowserConnectError
from .tools import port_is_using, PortFinder, ensure_del_dir, wait_until


def connect_browser(opt, mode='auto'):
    """连接或启动浏览器。

    :param opt: Options
    :param mode: 'auto' 端口活着→接管，否则启动 | 'attach' 只接管 | 'launch' 只启动
    :return: 启动参数列表（启动时）；None（接管时）
    """
    if opt.auto_port:
        ip = '127.0.0.1'
        port = PortFinder(opt.tmp_path).get_port()
        opt.address = f'{ip}:{port}'
    else:
        address = opt.address.replace('localhost', '127.0.0.1')
        if address.startswith('http'):
            address = address.lstrip('htps:/')
        ip, port = address.split(':')
        if ip != '127.0.0.1':
            if not test_connect(ip, port):
                raise BrowserConnectError(f'远程浏览器必须已开启调试端口：{address}')
            return None
        using = port_is_using(ip, port)
        alive = test_connect(ip, port) if using else False   # 空闲端口必不 alive，免去慢速探测
        if alive:
            if mode == 'launch':
                raise BrowserConnectError(f'地址 {address} 上已有浏览器在运行，launch 模式不接管。')
            return None
        if mode == 'attach' or opt.existing_only:
            raise BrowserConnectError(f'地址 {address} 上没有可接管的浏览器。')
        if using:
            raise BrowserConnectError(f'端口 {port} 被非调试浏览器进程占用，请设置其他端口。')

    # ----------创建浏览器进程----------
    args, user_path = get_launch_args(opt)

    is_edge = 'edge' in opt.browser_path.lower()
    if not Path(opt.browser_path).exists():
        opt.browser_path = get_edge_path() if is_edge else get_chromium_path()

    if not user_path:
        user_path = Path(opt.default_user_data_path(port))
        if opt.auto_port or opt.fresh:
            if not ensure_del_dir(user_path):
                raise RuntimeError(f'清理用户数据目录失败：{user_path}')
        user_path = user_path.resolve()
        opt.user_data_path = str(user_path)
        args.append(f'--user-data-dir={user_path}')
    else:
        opt.auto_port = None  # 自动端口同时指定了文件夹，结束时不删除文件夹
        if opt.fresh and not ensure_del_dir(user_path):
            raise RuntimeError(f'清理用户数据目录失败：{user_path}')

    _run_browser(port, opt.browser_path, args)

    if not test_connect(ip, port):
        raise BrowserConnectError(f'浏览器启动失败。地址：{opt.address}，用户数据目录：{opt.user_data_path}')
    return args


def get_launch_args(opt):
    # ----------处理 arguments-----------
    result = set()
    user_path = None
    for i in opt.arguments:
        if i.startswith(('--disable-extensions-except=', '--load-extension=', '--remote-debugging-port=')):
            continue
        elif i.startswith('--user-data-dir='):
            user_path = Path(i[16:]).resolve()
            i = f'--user-data-dir={user_path}'
        result.add(i)
    result = list(result)

    # ----------处理插件 extensions-------------
    ext = [Path(e) for e in opt.extensions]
    exts = []
    if ext:
        for e in ext:
            if e.is_file():
                raise ValueError(f'插件必须是文件夹：{e.resolve()}')
            elif not e.exists():
                raise FileNotFoundError(f'插件路径不存在：{e}')
            else:
                exts.append(str(e.resolve()))
        ext = ','.join(set(exts))
        result.append(f'--disable-extensions-except={ext}')
        result.append(f'--load-extension={ext}')

    return result, user_path


def test_connect(ip, port):
    s = Session()
    s.trust_env = False
    s.keep_alive = False

    def do():
        try:
            r = s.get(f'http://{ip}:{port}/json/version', timeout=10, headers={'Connection': 'close'})
            if 'webSocketDebuggerUrl' in r.json():
                r.close()
                return True
        except Exception:
            return None

    res = wait_until(do, timeout=Settings.browser_connect_timeout)
    s.close()
    return True if res else False


def _run_browser(port, path, args) -> Popen:
    """创建浏览器进程。"""
    p = Path(path)
    p = str(p / 'chrome') if p.is_dir() else str(path)
    arguments = [p, f'--remote-debugging-port={port}']
    arguments.extend(args)
    try:
        return Popen(arguments, shell=False, stdout=DEVNULL, stderr=DEVNULL)
    except FileNotFoundError:
        raise FileNotFoundError('浏览器可执行文件不存在，请用 Options.browser_path 指定路径。')


def get_chromium_path():
    system = platform.lower()
    if system.startswith('win'):  # 注册表+常见路径
        from winreg import OpenKey, QueryValue, HKEY_LOCAL_MACHINE
        try:
            reg_paths = [r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe',
                         r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe']
            for rp in reg_paths:
                try:
                    with OpenKey(HKEY_LOCAL_MACHINE, rp) as key:
                        p = QueryValue(key, None)
                        if p and Path(p).exists():
                            return p
                except Exception:
                    continue
        except ImportError:
            pass

        local_appdata = environ.get('LOCALAPPDATA', '')
        program_files = environ.get('PROGRAMFILES', 'C:\\Program Files')
        program_files_x86 = environ.get('PROGRAMFILES(X86)', 'C:\\Program Files (x86)')
        default_paths = [
            Path(local_appdata) / 'Google/Chrome/Application/chrome.exe',
            Path(program_files) / 'Google/Chrome/Application/chrome.exe',
            Path(program_files_x86) / 'Google/Chrome/Application/chrome.exe',
        ]
        for p in default_paths:
            if p.exists():
                return str(p)

    elif system in ('macos', 'darwin'):
        mac_paths = ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                     '/Applications/Chromium.app/Contents/MacOS/Chromium']
        for p in mac_paths:
            if Path(p).exists():
                return p

    elif system == 'linux':
        linux_paths = ['/usr/bin/google-chrome', '/usr/bin/google-chrome-stable',
                       '/usr/bin/chromium', '/usr/bin/chromium-browser', '/snap/bin/chromium']
        for p in linux_paths:
            if Path(p).exists():
                return p

    return _find_in_path(('chrome', 'google-chrome', 'chromium', 'chromium-browser'))


def get_edge_path():
    system = platform.lower()
    if system.startswith('win'):
        try:
            from winreg import OpenKey, QueryValue, HKEY_LOCAL_MACHINE
            reg_paths = [r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe',
                         r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe']
            for reg_path in reg_paths:
                try:
                    with OpenKey(HKEY_LOCAL_MACHINE, reg_path) as key:
                        path = QueryValue(key, None)
                        if path and os_path.exists(path):
                            return path
                except FileNotFoundError:
                    continue
        except Exception:
            pass

        default_win_paths = [
            os_path.join(environ.get('PROGRAMFILES', ''), r'Microsoft\Edge\Application\msedge.exe'),
            os_path.join(environ.get('PROGRAMFILES(X86)', ''), r'Microsoft\Edge\Application\msedge.exe'),
            os_path.join(environ.get('LOCALAPPDATA', ''), r'Microsoft\Edge\Application\msedge.exe'),
        ]
        for p in default_win_paths:
            if os_path.exists(p):
                return p
    raise RuntimeError('没有找到 Edge 浏览器，请用 Options.browser_path 指定路径。')


def _find_in_path(exe_names):
    path_dirs = environ.get('PATH', '').split(pathsep)
    for d in path_dirs:
        dir_path = Path(d)
        if not dir_path.is_dir():
            continue
        for exe in exe_names:
            exe_path = dir_path / exe
            if exe_path.exists() and exe_path.is_file() and access(exe_path, X_OK):
                return str(exe_path)
    raise RuntimeError('没有找到 Chromium 系浏览器，请用 Options.browser_path 指定路径。')
