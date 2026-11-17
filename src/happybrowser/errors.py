# -*- coding:utf-8 -*-
"""
happybrowser 错误体系（移植改造自 DrissionPage © g1879，https://DrissionPage.cn，
遵循其许可：仅限个人学习与合法非盈利用途，商用需上游授权）。
改造点：去除语言包机制，错误信息由各触发点直接以中文给出。
"""


class BaseError(Exception):
    """happybrowser 所有异常的基类。"""


class ElementNotFoundError(BaseError):
    """元素未找到。"""


class AlertExistsError(BaseError):
    """存在未处理的 alert 对话框。"""


class ContextLostError(BaseError):
    """页面上下文（frame）丢失。"""


class ElementLostError(BaseError):
    """元素已从 DOM 脱离。"""


class CDPError(BaseError):
    """CDP 协议调用返回错误。"""


class PageDisconnectedError(BaseError):
    """页面/浏览器连接已断开。"""


class JavaScriptError(BaseError):
    """JavaScript 执行出错。"""


class NoRectError(BaseError):
    """元素无矩形（不可见/无尺寸）。"""


class BrowserConnectError(BaseError):
    """浏览器连接失败。"""


class NoResourceError(BaseError):
    """资源不存在。"""


class CanNotClickError(BaseError):
    """元素不可点击。"""


class GetDocumentError(BaseError):
    """获取 DOM 文档失败。"""


class WaitTimeoutError(BaseError):
    """等待超时。"""


class IncorrectURLError(BaseError):
    """URL 格式不正确。"""


class StorageError(BaseError):
    """存储（localStorage 等）操作失败。"""


class CookieFormatError(BaseError):
    """cookie 格式不正确。"""


class TargetNotFoundError(BaseError):
    """目标（tab/frame）不存在。"""


class LocatorError(BaseError, ValueError):
    """定位符解析/构造错误。"""


class UnknownError(BaseError):
    """未知错误。"""
