# -*- coding:utf-8 -*-
"""浏览器配置（重写自 DrissionPage © g1879 _configs/chromium_options.py 的语义，
https://DrissionPage.cn —— 遵循其许可：仅限个人学习与合法非盈利用途，商用需上游授权）。
改造点：属性式程序化配置，去除 ini 文件与 eval() 反序列化；
新增 headless()/incognito()/no_images()/mute() 常用糖（参数集沉淀自 drission_utils 实战清单）；
去除 _ua_set/_new_env 等为"接管自杀重启"机制服务的内部开关（happybrowser 接管永不比对重启）。"""
from pathlib import Path
from tempfile import gettempdir


class Timeout(object):
    """超时配置：base 元素查找轮询 / page_load 页面加载 / script 脚本执行。"""

    def __init__(self, base=10, page_load=30, script=30):
        self.base = base
        self.page_load = page_load
        self.script = script

    def set(self, base=None, page_load=None, script=None):
        if base is not None:
            self.base = base
        if page_load is not None:
            self.page_load = page_load
        if script is not None:
            self.script = script
        return self

    def __repr__(self):
        return f'Timeout(base={self.base}, page_load={self.page_load}, script={self.script})'


# 反检测 UA：去掉 HeadlessChrome 标记（来自 drission_utils 实战清单）
HEADLESS_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
               '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0')

_HEADLESS_ARGS = ('--headless=new', '--no-sandbox', '--disable-dev-shm-usage',
                  '--disable-blink-features=AutomationControlled',
                  '--disable-features=IsolateOrigins,site-per-process,AutomationControlled',
                  '--window-size=1920,1080', '--disable-infobars', '--disable-setuid-sandbox')


class Options(object):
    """happybrowser 浏览器配置。全部属性可直接赋值，另有常用方法糖。

    opts = Options()
    opts.address = '127.0.0.1:9333'
    opts.auto_port = True
    opts.browser_path = r'...\\chrome.exe'
    opts.user_data_path = ...
    opts.timeouts.set(base=5, page_load=15)
    opts.load_mode = 'eager'          # 'normal' | 'eager' | 'none'
    opts.headless()                   # 一键无头 + 反检测
    """

    DEFAULT_ARGUMENTS = ('--disable-site-isolation-trials', '--test-type', '--no-default-browser-check',
                         '--disable-suggestions-ui', '--no-first-run', '--disable-infobars',
                         '--disable-popup-blocking', '--hide-crash-restore-bubble',
                         '--disable-features=PrivacySandboxSettings4')

    def __init__(self):
        self.address = '127.0.0.1:9222'
        self.ws_address = None          # 直接给 ws:// 地址时使用，跳过 http 探测
        self.auto_port = False          # True 时随机端口 + 临时数据目录（退出即删）
        self.existing_only = False      # True 时只接管，端口无浏览器则报错
        self.browser_path = 'chrome'    # 'chrome' 表示走 launcher 自动探测
        self.arguments = list(self.DEFAULT_ARGUMENTS)
        self.extensions = []
        self.user_data_path = None      # None → <tmp>/happybrowser/{autoPortData|userData}/<port>
        self.download_path = ''
        self.tmp_path = None
        self.timeouts = Timeout()
        self.load_mode = 'normal'
        self.proxy = None               # 'http://user:pwd@host:port'
        self.fresh = False              # True 时启动前清空 user_data_path

    # ----------地址----------
    def set_address(self, address):
        self.address = address
        return self

    def set_local_port(self, port):
        self.address = f'127.0.0.1:{port}'
        return self

    # ----------启动参数----------
    def set_argument(self, arg):
        if arg not in self.arguments:
            self.arguments.append(arg)
        return self

    def remove_argument(self, arg):
        if arg in self.arguments:
            self.arguments.remove(arg)
        return self

    def set_extension(self, path):
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f'插件路径不存在：{p}')
        if p.is_file():
            raise ValueError(f'插件必须是文件夹：{p}')
        self.extensions.append(str(p.resolve()))
        return self

    def set_user_agent(self, ua):
        self.remove_argument_startswith('--user-agent=')
        self.arguments.append(f'--user-agent={ua}')
        return self

    def remove_argument_startswith(self, prefix):
        self.arguments = [a for a in self.arguments if not a.startswith(prefix)]
        return self

    def set_user_data_path(self, path):
        self.user_data_path = str(Path(path).resolve())
        return self

    # ----------常用糖----------
    def headless(self, on=True):
        """无头 + 反检测整套参数（--headless=new、去 Headless 标记 UA、反自动化检测等）。"""
        if not on:
            self.remove_argument_startswith('--headless')
            return self
        for arg in _HEADLESS_ARGS:
            self.set_argument(arg)
        self.set_user_agent(HEADLESS_UA)
        return self

    def incognito(self, on=True):
        if on:
            self.set_argument('--incognito')
        else:
            self.remove_argument('--incognito')
        return self

    def no_images(self, on=True):
        arg = '--blink-settings=imagesEnabled=false'
        if on:
            self.set_argument(arg)
        else:
            self.remove_argument(arg)
        return self

    def mute(self, on=True):
        if on:
            self.set_argument('--mute-audio')
        else:
            self.remove_argument('--mute-audio')
        return self

    # ----------派生信息----------
    @property
    def is_headless(self):
        return any(a.startswith('--headless') for a in self.arguments)

    @property
    def user_agent(self):
        for a in self.arguments:
            if a.startswith('--user-agent='):
                return a[13:]
        return None

    def default_user_data_path(self, port):
        base = Path(self.tmp_path or gettempdir()) / 'happybrowser'
        sub = 'autoPortData' if self.auto_port else 'userData'
        return str((base / sub / str(port)).resolve())

    # ----------序列化----------
    def to_dict(self):
        return {'address': self.address, 'auto_port': self.auto_port, 'browser_path': self.browser_path,
                'arguments': list(self.arguments), 'extensions': list(self.extensions),
                'user_data_path': self.user_data_path, 'download_path': self.download_path,
                'tmp_path': self.tmp_path, 'load_mode': self.load_mode, 'proxy': self.proxy,
                'fresh': self.fresh, 'timeouts': (self.timeouts.base, self.timeouts.page_load,
                                                  self.timeouts.script)}

    @classmethod
    def from_dict(cls, d):
        opts = cls()
        for k in ('address', 'auto_port', 'existing_only', 'browser_path', 'user_data_path',
                  'download_path', 'tmp_path', 'load_mode', 'proxy', 'fresh'):
            if k in d and d[k] is not None:
                setattr(opts, k, d[k])
        opts.arguments = list(d.get('arguments', opts.DEFAULT_ARGUMENTS))
        opts.extensions = list(d.get('extensions', []))
        if 'timeouts' in d:
            opts.timeouts = Timeout(*d['timeouts'])
        return opts

    def __repr__(self):
        return (f'Options(address={self.address!r}, auto_port={self.auto_port}, '
                f'load_mode={self.load_mode!r}, headless={self.is_headless})')
