# -*- coding: utf-8 -*-
"""cookie 工具（移植精简自 DrissionPage © g1879 _functions/cookies.py，https://DrissionPage.cn
—— 遵循其许可：仅限个人学习与合法非盈利用途，商用需上游授权）。
改造点：
- 去除 tldextract 依赖：tab 级 set cookie 的域名推导改为按 hostname 标签逐级上试
  （完整主机名 → 各级父域），配合 is_cookie_in_driver 验证，效果等价、零第三方依赖；
- 删 set_session_cookies / CookieJar 分支（session 层已随设计砍除）。"""
from datetime import datetime
from ipaddress import ip_address
from urllib.parse import urlparse


def cookie_to_dict(cookie):
    if isinstance(cookie, dict):
        return cookie

    elif isinstance(cookie, str):
        cookie_dict = {}
        for attr in cookie.strip().rstrip(';,').split(',' if ',' in cookie else ';'):
            attr_val = attr.strip().split('=', 1)
            if attr_val[0] in ('domain', 'path', 'expires', 'max-age', 'HttpOnly', 'secure', 'expiry',
                               'name', 'value'):
                cookie_dict[attr_val[0]] = attr_val[1] if len(attr_val) == 2 else ''
            else:
                cookie_dict['name'] = attr_val[0]
                cookie_dict['value'] = attr_val[1] if len(attr_val) == 2 else ''
        return cookie_dict

    raise ValueError(f'参数类型错误。cookie 应为 str 或 dict，而不是 {type(cookie)}')


def cookies_to_tuple(cookies):
    if isinstance(cookies, (list, tuple)):
        return tuple(cookie_to_dict(cookie) for cookie in cookies)

    elif isinstance(cookies, str):
        c_dict = {}
        cookies = cookies.rstrip('; ')
        for attr in cookies.split(';'):
            attr_val = attr.strip().split('=', 1)
            c_dict[attr_val[0]] = attr_val[1] if len(attr_val) == 2 else True
        return _dict_cookies_to_tuple(c_dict)

    elif isinstance(cookies, dict):
        return _dict_cookies_to_tuple(cookies)

    raise ValueError(f'参数类型错误。cookies 应为 list、tuple、str 或 dict，而不是 {type(cookies)}')


def set_browser_cookies(browser, cookies):
    """浏览器级设置：Storage.setCookies，须带 domain 或 url。"""
    c = []
    for cookie in cookies_to_tuple(cookies):
        if 'domain' not in cookie and 'url' not in cookie:
            raise ValueError(f'cookie 没有设置 domain 或 url：{cookie}')
        c.append(format_cookie(cookie))
    if browser._default_context:
        browser._run_cdp('Storage.setCookies', cookies=c)
    else:
        browser._run_cdp('Storage.setCookies', cookies=c, browserContextId=browser._context_id)


def set_tab_cookies(page, cookies):
    """页面级设置：Network.setCookie，无 domain 时按当前 url 逐级上试域名。"""
    for cookie in cookies_to_tuple(cookies):
        cookie = format_cookie(cookie)

        if cookie['name'].startswith('__Host-'):
            if not page.url.startswith('http'):
                cookie['name'] = cookie['name'].replace('__Host-', '__Secure-', 1)
            else:
                cookie['url'] = page.url
            page._run_cdp_loaded('Network.setCookie', **cookie)
            continue  # __Host- 无须设置域名，可退出

        if cookie.get('domain'):
            try:
                page._run_cdp_loaded('Network.setCookie', **cookie)
                if not is_cookie_in_driver(page, cookie):
                    page.browser.set.cookies(cookie)
                continue
            except Exception:
                pass

        url = page.url
        if not url.startswith('http'):
            raise ValueError(f'cookie 没有设置域名，且当前 url 不是 http(s)，无法推导：{cookie}')

        try:
            host = urlparse(url).hostname
            ip_address(host)
            d_list = [host]
        except ValueError:
            labels = host.split('.')
            d_list = ['.'.join(labels[i:]) for i in range(max(len(labels) - 1, 1))]  # 逐级父域，至少一级

        tmp = [d_list[0]]
        if len(d_list) > 1:
            for i in d_list[1:]:
                tmp.append('.')
                tmp.append(i)

        for i in range(len(tmp)):
            cookie['domain'] = ''.join(tmp[i:])
            page._run_cdp_loaded('Network.setCookie', **cookie)
            if is_cookie_in_driver(page, cookie):
                break


def is_cookie_in_driver(page, cookie):
    if 'domain' in cookie:
        for c in page.cookies(all_domains=True):
            if (cookie['name'] == c['name'] and cookie['value'] == c['value']
                    and cookie['domain'] == c.get('domain')):
                return True
    else:
        for c in page.cookies(all_domains=True):
            if cookie['name'] == c['name'] and cookie['value'] == c['value']:
                return True
    return False


def format_cookie(cookie):
    if 'expiry' in cookie:
        cookie['expires'] = int(cookie['expiry'])
        cookie.pop('expiry')

    if 'expires' in cookie:
        if not cookie['expires']:
            cookie.pop('expires')
        elif isinstance(cookie['expires'], str):
            if cookie['expires'].isdigit():
                cookie['expires'] = int(cookie['expires'])
            elif cookie['expires'].replace('.', '').isdigit():
                cookie['expires'] = float(cookie['expires'])
            else:
                try:
                    cookie['expires'] = datetime.strptime(
                        cookie['expires'], '%a, %d %b %Y %H:%M:%S GMT').timestamp()
                except ValueError:
                    cookie.pop('expires')

    if cookie['value'] is None:
        cookie['value'] = ''
    elif not isinstance(cookie['value'], str):
        cookie['value'] = str(cookie['value'])

    if cookie['name'].startswith('__Host-'):
        cookie['path'] = '/'
        cookie['secure'] = True
    elif cookie['name'].startswith('__Secure-'):
        cookie['secure'] = True

    if 'sameSite' in cookie:
        sameSite = cookie['sameSite']
        if sameSite in (None, False) or sameSite not in ('None', 'Lax', 'Strict', 'no_restriction'):
            cookie.pop('sameSite')

    if 'priority' in cookie:
        priority = cookie['priority']
        if priority in (None, False):
            cookie.pop('priority')
        elif priority not in ('Low', 'Medium', 'High'):
            raise ValueError(f'参数值错误。priority 只能是 "Low"、"Medium"、"High"，'
                             f'当前是 {priority}（cookie：{cookie}）')

    if 'sourceScheme' in cookie:
        sourceScheme = cookie['sourceScheme']
        if sourceScheme in (None, False):
            cookie.pop('sourceScheme')
        elif sourceScheme not in ('Unset', 'NonSecure', 'Secure'):
            raise ValueError(f'参数值错误。sourceScheme 只能是 "Unset"、"NonSecure"、"Secure"，'
                             f'当前是 {sourceScheme}（cookie：{cookie}）')

    return cookie


class CookiesList(list):
    def as_dict(self):
        return {c['name']: c['value'] for c in self}

    def as_str(self):
        return '; '.join([f'{c["name"]}={c["value"]}' for c in self])

    def as_json(self):
        from json import dumps
        return dumps(self)


def _dict_cookies_to_tuple(cookies: dict):
    """把 dict 形式的 cookies 转换为 tuple 形式。
    单个 cookie 含 'name' 和 'value'；多个时键为 cookie 名、值为 cookie 值。"""
    if 'name' in cookies and 'value' in cookies:  # 单个 cookie
        return (cookies,)
    keys = ('domain', 'path', 'expires', 'max-age', 'HttpOnly', 'secure', 'expiry')
    template = {k: v for k, v in cookies.items() if k in keys}
    return tuple(dict(name=k, value=v, **template) for k, v in cookies.items() if k not in keys)
