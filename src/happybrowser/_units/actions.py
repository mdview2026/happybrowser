# -*- coding: utf-8 -*-
"""动作链（移植改造自 DrissionPage (c) g1879 _units/actions.py，https://DrissionPage.cn
—— 遵循其许可：仅限个人学习与合法非盈利用途，商用需上游授权）。
改造点：定位符接受 DSL 字符串或 Element；_type 约定值 'Element'；
视口判断改 location_in_viewport_js； 私有区转义字节级保留，勿手写复制本文件。"""
from pathlib import Path
from time import sleep, perf_counter

from .._core.keys import modifierBit, make_input_data, input_text_or_keys, Keys
from .._functions.web import location_in_viewport_js


class Actions:
    def __init__(self, owner):
        self.owner = owner
        self.modifier = 0  # 修饰符，Alt=1, Ctrl=2, Meta/Command=4, Shift=8
        self.curr_x = 0  # 视口坐标
        self.curr_y = 0
        self._holding = 'left'
        self._mouse_trail = False

    def move_to(self, ele_or_loc, offset_x=None, offset_y=None, duration=.5):
        is_loc = False
        mid_point = offset_x == offset_y is None
        if offset_x is None:
            offset_x = 0
        if offset_y is None:
            offset_y = 0
        if isinstance(ele_or_loc, (tuple, list)):
            is_loc = True
            lx = ele_or_loc[0] + offset_x
            ly = ele_or_loc[1] + offset_y
        elif isinstance(ele_or_loc, str) or ele_or_loc._type == 'Element':
            ele_or_loc = self.owner.ele(ele_or_loc)
            self.owner.scroll.to_see(ele_or_loc)
            x, y = ele_or_loc.rect.midpoint if mid_point else ele_or_loc.rect.location
            lx = x + offset_x
            ly = y + offset_y
        else:
            raise ValueError(f'ele_or_loc 参数格式不正确，必须是元素、定位符或坐标，而不是 {ele_or_loc!r}')

        if not self.owner._run_js(location_in_viewport_js(lx, ly)):
            # 把坐标滚动到页面中间
            clientWidth = self.owner._run_js('return document.body.clientWidth;')
            clientHeight = self.owner._run_js('return document.body.clientHeight;')
            self.owner.scroll.to_location(lx - clientWidth // 2, ly - clientHeight // 2)

        # 这样设计为了应付那些不随滚动条滚动的元素
        if is_loc:
            cx, cy = location_to_client(self.owner, lx, ly)
        else:
            x, y = ele_or_loc.rect.viewport_midpoint if mid_point else ele_or_loc.rect.viewport_location
            cx = x + offset_x
            cy = y + offset_y

        ox = cx - self.curr_x
        oy = cy - self.curr_y
        self.move(ox, oy, duration)
        return self

    def move(self, offset_x=0, offset_y=0, duration=.5):
        duration = max(duration, 0.02)
        num = int(duration * 50)

        points = [(self.curr_x + i * (offset_x / num),
                   self.curr_y + i * (offset_y / num)) for i in range(1, num)]
        points.append((self.curr_x + offset_x, self.curr_y + offset_y))

        for x, y in points:
            t = perf_counter()
            self.curr_x = x
            self.curr_y = y
            self.owner._run_cdp('Input.dispatchMouseEvent', type='mouseMoved', button=self._holding,
                                x=self.curr_x, y=self.curr_y, modifiers=self.modifier)
            ss = .02 - perf_counter() + t
            if ss > 0:
                sleep(ss)

        return self

    def click(self, on_ele=None, times=1):
        self._hold(on_ele, 'left', times).wait(.05)._release('left')
        return self

    def r_click(self, on_ele=None, times=1):
        self._hold(on_ele, 'right', times).wait(.05)._release('right')
        return self

    def m_click(self, on_ele=None, times=1):
        self._hold(on_ele, 'middle', times).wait(.05)._release('middle')
        return self

    def hold(self, on_ele=None):
        self._hold(on_ele, 'left')
        return self

    def release(self, on_ele=None):
        if on_ele:
            self.move_to(on_ele, duration=.2)
        self._release('left')
        return self

    def r_hold(self, on_ele=None):
        self._hold(on_ele, 'right')
        return self

    def r_release(self, on_ele=None):
        if on_ele:
            self.move_to(on_ele, duration=.2)
        self._release('right')
        return self

    def m_hold(self, on_ele=None):
        self._hold(on_ele, 'middle')
        return self

    def m_release(self, on_ele=None):
        if on_ele:
            self.move_to(on_ele, duration=.2)
        self._release('middle')
        return self

    def _hold(self, on_ele=None, button='left', count=1):
        if on_ele:
            self.move_to(on_ele, duration=.2)
        self.owner._run_cdp('Input.dispatchMouseEvent', type='mousePressed', button=button, clickCount=count,
                            x=self.curr_x, y=self.curr_y, modifiers=self.modifier)
        self._holding = button
        return self

    def _release(self, button):
        self.owner._run_cdp('Input.dispatchMouseEvent', type='mouseReleased', button=button, clickCount=1,
                            x=self.curr_x, y=self.curr_y, modifiers=self.modifier)
        self._holding = 'left'
        return self

    def scroll(self, delta_y=0, delta_x=0, on_ele=None):
        if on_ele:
            self.move_to(on_ele, duration=.2)
        self.owner._run_cdp('Input.dispatchMouseEvent', type='mouseWheel', x=self.curr_x, y=self.curr_y,
                            deltaX=delta_x, deltaY=delta_y, modifiers=self.modifier)
        return self

    def up(self, pixel):
        return self.move(0, -pixel)

    def down(self, pixel):
        return self.move(0, pixel)

    def left(self, pixel):
        return self.move(-pixel, 0)

    def right(self, pixel):
        return self.move(pixel, 0)

    def key_down(self, key):
        if isinstance(key, (tuple, list)):
            for i in key:
                self.key_down(i)
            return self
        key = getattr(Keys, key.upper(), key) if isinstance(key, str) else key
        if key in ('\ue009', '\ue008', '\ue00a', '\ue03d'):  # 如果是修饰符，添加到变量
            self.modifier |= modifierBit.get(key, 0)
            data = make_input_data(self.modifier, key, False)
            if data:
                self.owner._run_cdp('Input.dispatchKeyEvent', **data)
            return self

        data = make_input_data(self.modifier, key, False)
        if not data:
            raise ValueError(f'没有该按键：{key!r}')
        self.owner._run_cdp('Input.dispatchKeyEvent', **data)
        return self

    def key_up(self, key):
        if isinstance(key, (tuple, list)):
            for i in key:
                self.key_up(i)
            return self
        key = getattr(Keys, key.upper(), key) if isinstance(key, str) else key
        if key in ('\ue009', '\ue008', '\ue00a', '\ue03d'):  # 如果是修饰符，添加到变量
            self.modifier ^= modifierBit.get(key, 0)
            data = make_input_data(self.modifier, key, True)
            if data:
                self.owner._run_cdp('Input.dispatchKeyEvent', **data)
            return self

        data = make_input_data(self.modifier, key, True)
        if not data:
            raise ValueError(f'没有该按键：{key!r}')
        self.owner._run_cdp('Input.dispatchKeyEvent', **data)
        return self

    def type(self, keys, interval=0):
        modifiers = []
        if not isinstance(keys, (str, tuple, list)):
            keys = str(keys)
        for i in keys:
            for character in i:
                if character in ('\ue009', '\ue008', '\ue00a', '\ue03d'):
                    self.modifier |= modifierBit.get(character, 0)
                    modifiers.append(character)
                data = make_input_data(self.modifier, character, False)
                if data:
                    self.owner._run_cdp('Input.dispatchKeyEvent', **data)
                    if character not in ('\ue009', '\ue008', '\ue00a', '\ue03d'):
                        data['type'] = 'keyUp'
                        self.owner._run_cdp('Input.dispatchKeyEvent', **data)

                else:
                    self.owner._run_cdp('Input.dispatchKeyEvent', type='char', text=character)
                sleep(interval)

        for m in modifiers:
            self.key_up(m)
        return self

    def input(self, text):
        input_text_or_keys(self.owner, text)
        return self

    def drag_in(self, ele_or_loc, files=None, text=None, title=None, baseURL=None, offset_x=None, offset_y=None):
        ele_or_loc = self.owner.ele(ele_or_loc)
        if offset_x is not None or offset_y is not None:
            x, y = ele_or_loc.rect.viewport_location
            x += offset_x if offset_x is not None else 0
            y += offset_y if offset_y is not None else 0
        else:
            x, y = ele_or_loc.rect.viewport_midpoint
        if files:
            items = []
            paths = []
            if isinstance(files, str):
                files = [files]
            for file in files:
                path = str(Path(file).resolve())
                item = {'mimeType': 'application/octet-stream', 'data': path}
                items.append(item)
                paths.append(path)
            data = {'items': items, 'files': paths, 'dragOperationsMask': 16}

        elif text:
            item = {'data': text}
            if title is not None:
                item['title'] = title
                item['mimeType'] = 'text/uri-list'
            elif baseURL is not None:
                item['baseURL'] = baseURL
                item['mimeType'] = 'text/uri-list'
            else:
                item['mimeType'] = 'text/plain'
            data = {'items': [item], 'dragOperationsMask': 1}

        else:
            raise ValueError('必须提供 files 或 text 参数')

        self.owner._run_cdp('Input.dispatchDragEvent', type='dragEnter', x=x, y=y, data=data, modifiers=self.modifier)
        self.owner._run_cdp('Input.dispatchDragEvent', type='drop', x=x, y=y, data=data, modifiers=self.modifier)
        return self

    def wait(self, second, scope=None):
        self.owner.wait(second=second, scope=scope)
        return self


def location_to_client(page, lx, ly):
    scroll_x = page._run_js('return document.documentElement.scrollLeft;')
    scroll_y = page._run_js('return document.documentElement.scrollTop;')
    return lx - scroll_x, ly - scroll_y
