# -*- coding:utf-8 -*-
"""happybrowser 全局设置（移植精简自 DrissionPage © g1879 _functions/settings.py，
遵循其许可：仅限个人学习与合法非盈利用途，商用需上游授权）。
改造点：去除语言机制；其余保持类属性 + classmethod setter 风格。"""
from pathlib import Path


class Settings(object):
    raise_when_ele_not_found = False
    raise_when_click_failed = False
    raise_when_wait_failed = False
    singleton_tab_obj = True
    cdp_timeout = 30
    browser_connect_timeout = 30
    auto_handle_alert = None
    suffixes_list = str(Path(__file__).parent.joinpath('_units', 'suffixes.dat').resolve()).replace('\\', '/')
    wait_stop_before_click = False
    none_ele_value = None            # NoneElement 取属性时的返回值
    none_ele_return_value = False    # True 时 NoneElement 链式调用返回自身而非抛错
    _debug = None  # 为 None 时不开启，为 True 或指定目标时全部开启，为 False 时由 Messenger 决定

    @classmethod
    def set_wait_stop_before_click(cls, on_off=True):
        cls.wait_stop_before_click = on_off
        return cls

    @classmethod
    def set_raise_when_ele_not_found(cls, on_off=True):
        cls.raise_when_ele_not_found = on_off
        return cls

    @classmethod
    def set_raise_when_click_failed(cls, on_off=True):
        cls.raise_when_click_failed = on_off
        return cls

    @classmethod
    def set_raise_when_wait_failed(cls, on_off=True):
        cls.raise_when_wait_failed = on_off
        return cls

    @classmethod
    def set_singleton_tab_obj(cls, on_off=True):
        cls.singleton_tab_obj = on_off
        return cls

    @classmethod
    def set_cdp_timeout(cls, second):
        cls.cdp_timeout = second
        return cls

    @classmethod
    def set_browser_connect_timeout(cls, second):
        cls.browser_connect_timeout = second
        return cls

    @classmethod
    def set_auto_handle_alert(cls, accept=True):
        cls.auto_handle_alert = accept
        return cls

    @classmethod
    def set_suffixes_list(cls, path):
        cls.suffixes_list = str(Path(path).resolve()).replace('\\', '/')
        return cls

    @classmethod
    def set_none_ele_value(cls, value):
        cls.none_ele_value = value
        return cls

    @classmethod
    def set_none_ele_return_value(cls, value):
        cls.none_ele_return_value = value
        return cls
