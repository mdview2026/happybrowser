# -*- coding: utf-8 -*-
"""元素对象与查找机械（移植精简自 DrissionPage © g1879 _elements/chromium_element.py，
https://DrissionPage.cn —— 遵循其许可：仅限个人学习与合法非盈利用途，商用需上游授权）。

改造点（相对上游）：
- 查找通道收敛为 query 单通道：页面级 DOM.performSearch（穿 shadow DOM）、元素级
  Runtime.callFunctionOn + document.evaluate（以 this 为上下文）。ax/any/xpath/css 直通通道删除。
- 纯查询（pure-xpath）精确命中，不再本地精筛；hybrid 查询用 to_xpath 预筛（安全超集）
  + ast.match() 精筛，与页面级语义一致。
- 页面级负 index 简化为全量取回后索引（等价，去掉上游 60 行 performSearch 分页回溯）。
- ShadowRoot 查找去 lxml：原「整树 lxml 快照 + css 路径映射回 querySelector」改为
  document.evaluate 以 shadowRoot 对象为上下文节点（设计 §6.3，M3 PoC 验收点）。
- 方向定位（east/south/west/north/offset）的 _check_ele 字典协议改为直接吃 AST（str|Q）。
- text/texts 由 lxml 解析 outerHTML 改为 HB_TEXT_JS 活 DOM 计算（见 _functions/web.py）。
- 相对/方向查找族（parent/child/next/prev/before/after 及复数）由 xpath 字符串拼装改为
  XPath 轴 + AST 精筛；preceding 系轴的单元素语义保持「就近优先」（取文档序末尾）。
- M5 起 iframe/frame/object 元素（describeNode 带 frameId）经 make_ele 包装为 Frame 对象；
  对 iframe 元素查找会以 this.contentDocument 为上下文进入同源文档。
- src/save/截图/set 挂件随 M5 收录。
"""
from json import loads, dumps
from math import inf
from pathlib import Path
from time import perf_counter, sleep

from .._functions.web import HB_TEXT_JS, format_html, make_absolute_link, is_js_func, get_blob
from .._functions.elements import ElementsList
from .._functions.files import make_valid_name, get_usable_path
from .none_element import NoneElement
from .._core.keys import Keys, input_text_or_keys
from .._locate.query import Q, parse_loc, to_xpath, is_pure_xpath
from ..settings import Settings
from .._core.tools import wait_until
from .._units.clicker import Clicker
from .._units.rect import ElementRect
from .._units.scroller import ElementScroller
from .._units.selector import SelectElement
from .._units.setter import ElementSetter
from .._units.states import ElementStates, ShadowRootStates
from .._units.waiter import ElementWaiter
from ..errors import (ContextLostError, ElementLostError, JavaScriptError, CDPError, NoRectError,
                      AlertExistsError, LocatorError, ElementNotFoundError, NoResourceError)

__FRAME_ELEMENT__ = ('iframe', 'frame', 'object')

# input()/clear() 的特殊键判定（等值于上游  私有区转义，用常量表达防手写丢失）
_ENTER_LIKE = ('\n', Keys.ENTER, Keys.RETURN)
_KEY_LIKE = (Keys.BACKSPACE, Keys.DELETE, Keys.END, Keys.HOME, Keys.LEFT, Keys.UP, Keys.RIGHT, Keys.DOWN)


class Element(object):
    """DOM 元素对象，node_id/obj_id/backend_id 三 id 互转，按需懒求。"""

    _type = 'Element'

    def __init__(self, owner, node_id=None, obj_id=None, backend_id=None):
        self.owner = owner
        self._states = None
        self._rect = None
        self._wait = None
        self._tag = None
        self._clicker = None
        self._scroll = None
        self._select = None
        self._setter = None

        if node_id:
            self._node_id = node_id
            self._obj_id = obj_id if obj_id else self._get_obj_id(node_id=node_id)
            self._backend_id = backend_id if backend_id else self._get_backend_id(node_id)
        elif obj_id:
            self._node_id = node_id if node_id else self._get_node_id(obj_id=obj_id)
            self._obj_id = obj_id
            self._backend_id = backend_id if backend_id else self._get_backend_id(self._node_id)
        elif backend_id:
            self._obj_id = obj_id if obj_id else self._get_obj_id(backend_id=backend_id)
            self._node_id = node_id if node_id else self._get_node_id(obj_id=self._obj_id)
            self._backend_id = backend_id
        else:
            raise ElementLostError

    def __repr__(self):
        attrs = ' '.join(f"{k}='{v}'" for k, v in self.attrs.items())
        return f'<Element {self.tag} {attrs}>'

    def __eq__(self, other):
        return self._backend_id == getattr(other, '_backend_id', None)

    def __hash__(self):
        return hash(('Element', self._backend_id))

    # ----------基本信息----------
    @property
    def tag(self):
        if self._tag is None:
            self._tag = self.owner._run_cdp('DOM.describeNode',
                                            backendNodeId=self._backend_id)['node']['localName'].lower()
        return self._tag

    @property
    def html(self):
        return self.owner._run_cdp('DOM.getOuterHTML', backendNodeId=self._backend_id)['outerHTML']

    @property
    def inner_html(self):
        return self._run_js('return this.innerHTML;')

    @property
    def attrs(self):
        try:
            attrs = self.owner._run_cdp('DOM.getAttributes', nodeId=self._node_id)['attributes']
            return {attrs[i]: attrs[i + 1] for i in range(0, len(attrs), 2)}
        except ElementLostError:
            self._refresh_id()
            attrs = self.owner._run_cdp('DOM.getAttributes', nodeId=self._node_id)['attributes']
            return {attrs[i]: attrs[i + 1] for i in range(0, len(attrs), 2)}
        except CDPError:  # 文档根元素不能调用此方法
            return {}

    @property
    def text(self):
        return format_html(self._run_js(f'{HB_TEXT_JS};return hbText(this);'))

    @property
    def raw_text(self):
        return self.property('innerText')

    @property
    def value(self):
        return self.property('value')

    @property
    def link(self):
        return self.attr('href') or self.attr('src')

    @property
    def timeout(self):
        return self.owner.timeout

    @property
    def child_count(self):
        return self._run_js('return this.children.length;')

    # ----------挂件----------
    @property
    def states(self):
        if self._states is None:
            self._states = ElementStates(self)
        return self._states

    @property
    def rect(self):
        if self._rect is None:
            self._rect = ElementRect(self)
        return self._rect

    @property
    def wait(self):
        if self._wait is None:
            self._wait = ElementWaiter(self)
        return self._wait

    @property
    def shadow_root(self):
        def do():
            info = self.owner._run_cdp('DOM.describeNode', backendNodeId=self._backend_id)['node']
            return (ShadowRoot(self, backend_id=info['shadowRoots'][0]['backendNodeId'])
                    if info.get('shadowRoots') else None)

        return wait_until(do, timeout=self.timeout)

    @property
    def tab(self):
        return self.owner.tab  # Tab.tab 即自身；Frame.tab 返回所属 Tab

    @property
    def click(self):
        if self._clicker is None:
            self._clicker = Clicker(self)
        return self._clicker

    @property
    def scroll(self):
        if self._scroll is None:
            self._scroll = ElementScroller(self)
        return self._scroll

    @property
    def select(self):
        """select 元素返回 SelectElement，其他元素返回 False。"""
        if self._select is None:
            self._select = SelectElement(self) if self.tag == 'select' else False
        return self._select

    @property
    def set(self):
        if self._setter is None:
            self._setter = ElementSetter(self)
        return self._setter

    # ----------属性读写----------
    def attr(self, name):
        attrs = self.attrs
        if name == 'href':
            link = attrs.get('href')
            if not link or link.lower().startswith(('javascript:', 'mailto:')):
                return link
            return make_absolute_link(link, self.property('baseURI'))
        if name == 'src':
            return make_absolute_link(attrs.get('src'), self.property('baseURI'))
        if name == 'text':
            return self.text
        if name == 'innerText':
            return self.raw_text
        if name in ('html', 'outerHTML'):
            return self.html
        if name == 'innerHTML':
            return self.inner_html
        return attrs.get(name)

    def remove_attr(self, name):
        self._run_js(f'this.removeAttribute("{name}");')
        return self

    def style(self, style, pseudo_ele=''):
        if pseudo_ele:
            pseudo_ele = f', "{pseudo_ele}"'
        return self._run_js(f'return window.getComputedStyle(this{pseudo_ele}).getPropertyValue("{style}");')

    def focus(self):
        try:
            self.owner._run_cdp('DOM.focus', backendNodeId=self._backend_id)
        except Exception:
            self._run_js('this.focus();')
        return self

    # ----------动作（M4）----------
    def input(self, vals, clear=False, by_js=False):
        """输入文本或按键组合；file input 直接设置文件路径。"""
        if self.tag == 'input' and self.attr('type') == 'file':
            return self._set_file_input(vals)

        if by_js:
            if clear:
                self.clear(True)
            if isinstance(vals, (list, tuple)):
                vals = ''.join(str(i) for i in vals)
            self._run_js(f'this.value={dumps(str(vals))};')
            self._run_js('this.dispatchEvent(new Event("change", {bubbles: true}));')
            return self

        self.wait.clickable(wait_stop=False, timeout=.5)
        if clear and vals not in _ENTER_LIKE:
            self.clear(by_js=False)
        else:
            self._input_focus()

        if isinstance(vals, str) and vals not in _KEY_LIKE:
            input_text_or_keys(self.owner, vals)
        else:
            self.owner.actions.type(vals)
        return self

    def clear(self, by_js=False):
        from platform import system
        if by_js or system().lower() in ('macos', 'darwin'):
            self._run_js("this.value='';")
            self._run_js('this.dispatchEvent(new Event("change", {bubbles: true}));')
            return self
        self._input_focus()
        self.input((Keys.CTRL_A, Keys.DEL), clear=False)
        return self

    def _input_focus(self):
        try:
            self.owner._run_cdp('DOM.focus', backendNodeId=self._backend_id)
        except Exception:
            self.click(by_js=None)

    def _set_file_input(self, files):
        if isinstance(files, str):
            files = files.split('\n')
        files = [str(Path(i).resolve()) for i in files]
        self.owner._run_cdp('DOM.setFileInputFiles', files=files, backendNodeId=self._backend_id)
        return self

    def check(self, checked=True, by_js=False):
        is_checked = self.states.is_checked
        if by_js:
            js = None
            if is_checked and not checked:
                js = 'this.checked=false;'
            elif not is_checked and checked:
                js = 'this.checked=true;'
            if js:
                self._run_js(f'{js}this.dispatchEvent(new Event("change", {{bubbles: true}}));')
        else:
            if (is_checked and not checked) or (not is_checked and checked):
                self.click()
        return self

    def hover(self, offset_x=None, offset_y=None):
        self.owner.actions.move_to(self, offset_x=offset_x, offset_y=offset_y, duration=.1)
        return self

    def drag(self, offset_x=0, offset_y=0, duration=.5):
        curr_x, curr_y = self.rect.midpoint
        self.drag_to((curr_x + offset_x, curr_y + offset_y), duration)
        return self

    def drag_to(self, ele_or_loc, duration=.5):
        if isinstance(ele_or_loc, Element):
            ele_or_loc = ele_or_loc.rect.midpoint
        elif not isinstance(ele_or_loc, (list, tuple)):
            raise ValueError(f'ele_or_loc 参数格式不正确，必须是元素、定位符或坐标，而不是 {ele_or_loc!r}')
        self.owner.actions.hold(self).move_to(ele_or_loc, duration=duration).release()
        return self

    # ----------JS----------
    def run_js(self, script, *args, as_expr=False, timeout=None):
        return self._run_js(script, *args, as_expr=as_expr, timeout=timeout)

    def _run_js(self, script, *args, as_expr=False, timeout=None):
        return run_js(self, script, as_expr, self.owner.timeouts.script if timeout is None else timeout, args)

    def run_async_js(self, script, *args, as_expr=False):
        run_js(self, script, as_expr, 0, args)

    # ----------资源 / 截图（M5）----------
    def src(self, timeout=None, base64_to_bytes=True):
        """获取元素资源内容（img/link/script 等的 src）。data: 直接解码，blob: 经 XHR 抓取，
        其他经 Page.getResourceContent。"""
        if timeout is None:
            timeout = self.timeout
        if self.tag == 'img':  # 等待图片加载完成
            js = ('return this.complete && typeof this.naturalWidth != "undefined" '
                  '&& this.naturalWidth > 0 && typeof this.naturalHeight != "undefined" '
                  '&& this.naturalHeight > 0')
            end_time = perf_counter() + timeout
            r = self._run_js(js)
            while not r and perf_counter() < end_time:
                sleep(.01)
                r = self._run_js(js)
            timeout = end_time - perf_counter()

        src = self.attr('href') if self.tag == 'link' else self.attr('src')
        if not src:
            raise RuntimeError('该元素没有 src 属性。')
        if src.lower().startswith('data:image'):
            from base64 import b64decode
            return b64decode(src.split(',', 1)[-1]) if base64_to_bytes else src.split(',', 1)[-1]

        is_blob = src.startswith('blob')

        def _get_blob():
            res = get_blob(self.owner, src, base64_to_bytes)
            return res if res else None

        def _get_src():
            s = self.attr('href') if self.tag == 'link' else self.property('currentSrc') or self.property('s')
            if not s:
                return None
            node = self.owner._run_cdp('DOM.describeNode', backendNodeId=self._backend_id,
                                       _ignore=True)['node']
            frame = node.get('frameId') or self.owner._frame_id
            try:
                return self.owner._run_cdp('Page.getResourceContent', frameId=frame, url=s, _ignore=True)
            except CDPError:
                return None

        func = _get_blob if is_blob else _get_src
        result = wait_until(func, timeout=timeout)

        if not result:
            return None
        elif is_blob:
            return result
        elif result.get('base64Encoded') and base64_to_bytes:
            from base64 import b64decode
            return b64decode(result['content'])
        return result['content']

    def save(self, path=None, name=None, timeout=None, rename=True):
        """把元素资源保存到本地，返回文件路径。"""
        data = self.src(timeout=timeout)
        if not data:
            raise NoResourceError

        from re import search
        from os.path import basename
        path = path or '.'
        if not name and self.tag == 'img':
            src = self.attr('src')
            if src.lower().startswith('data:image'):
                r = search(r'data:image/(.*?);base64,', src)
                name = f'img.{r.group(1)}' if r else None
        path = Path(path) / make_valid_name(name or basename(self.property('currentSrc') or 'img'))
        if not path.suffix:
            path = path.with_suffix('.jpg')
        if rename:
            path = get_usable_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path = path.resolve()
        write_type = 'wb' if isinstance(data, bytes) else 'w'

        with open(path, write_type) as f:
            f.write(data)

        return str(path)

    def get_screenshot(self, path=None, name=None, as_bytes=None, as_base64=None, scroll_to_center=True):
        """元素截图。"""
        if self.tag == 'img':  # 等待图片加载完成
            js = ('return this.complete && typeof this.naturalWidth != "undefined" && this.naturalWidth > 0 '
                  '&& typeof this.naturalHeight != "undefined" && this.naturalHeight > 0')
            end_time = perf_counter() + self.timeout
            while not self._run_js(js) and perf_counter() < end_time:
                sleep(.05)
        if scroll_to_center:
            self.scroll.to_see(center=True)

        left, top = self.rect.location
        width, height = self.rect.size
        left_top = (left, top)
        right_bottom = (left + width, top + height)
        if not name:
            name = f'{self.tag}.jpg'

        return self.owner._get_screenshot(path, name, as_bytes=as_bytes, as_base64=as_base64,
                                          full_page=False, left_top=left_top, right_bottom=right_bottom)

    # ----------查找----------
    def ele(self, locator, index=1, timeout=None):
        return self._ele(locator, timeout=timeout, index=index, method='ele()')

    def eles(self, locator, timeout=None):
        return self._ele(locator, timeout=timeout, index=None)

    def _ele(self, locator, timeout=None, index=1, relative=False, raise_err=None, method=None):
        if isinstance(locator, (Element, ShadowRoot)):
            return locator
        if timeout is None:
            timeout = self.timeout
        r = self._find_elements(locator, timeout=timeout, index=index, relative=relative, raise_err=raise_err)
        if r or isinstance(r, (list, float, int)):
            return r
        if raise_err is True or (Settings.raise_when_ele_not_found and raise_err is None):
            raise ElementNotFoundError(f'元素未找到：{method or "ele()"}() '
                                       f'{{\'locator\': {locator!r}, \'index\': {index!r}}}')
        r.method = method
        r.args = {'locator': locator, 'index': index, 'timeout': timeout}
        return r

    def _find_elements(self, locator, timeout, index=1, relative=False, raise_err=None):
        self.owner.wait.doc_loaded()
        return _find_in_ele(self, parse_loc(locator), index, timeout)

    # ----------相对/方向查找----------
    def parent(self, level_or_loc=1, index=1, timeout=0):
        if isinstance(level_or_loc, int):
            locator, index = None, level_or_loc
        else:
            locator = level_or_loc
        return self._get_relative('parent()', 'ancestor', locator, index, timeout)

    def child(self, locator='', index=1, timeout=None):
        return self._get_relative('child()', 'child', locator or None, index, timeout)

    def next(self, locator='', index=1, timeout=None):
        return self._get_relative('next()', 'following-sibling', locator or None, index, timeout)

    def prev(self, locator='', index=1, timeout=None):
        return self._get_relative('prev()', 'preceding-sibling', locator or None, index, timeout)

    def after(self, locator='', index=1, timeout=None):
        return self._get_relative('after()', 'following', locator or None, index, timeout)

    def before(self, locator='', index=1, timeout=None):
        return self._get_relative('before()', 'preceding', locator or None, index, timeout)

    def children(self, locator='', timeout=None):
        return self._get_relatives('child', locator or None, timeout)

    def nexts(self, locator='', timeout=None):
        return self._get_relatives('following-sibling', locator or None, timeout)

    def prevs(self, locator='', timeout=None):
        return self._get_relatives('preceding-sibling', locator or None, timeout)

    def afters(self, locator='', timeout=None):
        return self._get_relatives('following', locator or None, timeout)

    def befores(self, locator='', timeout=None):
        return self._get_relatives('preceding', locator or None, timeout)

    def texts(self, text_node_only=False):
        """直属文本节点与子元素文本组成的列表（过滤空白）。"""
        if text_node_only:
            r = self._run_js('const a=[];for(const n of this.childNodes){'
                             'if(n.nodeType===3){a.push(n.textContent)}}return a;')
        else:
            r = self._run_js(f'{HB_TEXT_JS};const a=[];for(const n of this.childNodes){{'
                             'if(n.nodeType===3){if(n.textContent.replace(/[ \\n\\t\\r]/g,"")!==""){'
                             'a.push(n.textContent)}}'
                             'else if(n.nodeType===1){const t=hbText(n);if(t){{a.push(t)}}}}return a;')
        from re import sub
        return [format_html(x.strip(' ').rstrip('\n')) for x in (r or []) if x and sub('[\r\n\t ]', '', x) != '']

    @property
    def comments(self):
        return self._run_js('function c(e){const a=[];for(const n of e.childNodes){'
                            'if(n.nodeType===8)a.push(n.textContent);'
                            'else if(n.nodeType===1)a.push(...c(n))}return a}return c(this);')

    def _get_relative(self, func, axis, locator, index, timeout):
        node = self._get_relatives(axis, locator, timeout, index)
        return node if node else NoneElement(self.owner, func,
                                             {'locator': locator, 'index': index})

    def _get_relatives(self, axis, locator, timeout=.5, index=None):
        """在指定 XPath 轴上查找，locator 为 DSL（str|Q）。ancestor/preceding 系轴的
        单元素 index 语义是「就近优先」（文档序从末尾数），列表始终按文档序返回。"""
        ast = parse_loc(locator) if locator else None
        reverse = axis in ('ancestor', 'preceding', 'preceding-sibling')
        xpath = f'./{axis}::*'
        js = make_js_for_find_ele_by_xpath(xpath, '7', 'this')

        def do():
            eles = _eval_xpath_all(self.owner, self._obj_id, js, xpath)
            if ast is not None:
                eles = [e for e in (eles or []) if ast.match(e)]
            if not eles:
                return None
            if index is None:
                return ElementsList(self.owner, eles)
            if abs(index) > len(eles):
                return None
            return eles[-index] if reverse else eles[index - 1]

        if timeout is None:
            timeout = self.timeout
        r = wait_until(do, timeout=timeout)
        return r

    # ----------方向定位（坐标扫描）----------
    def over(self, timeout=None):
        if timeout is None:
            timeout = self.timeout
        bid = self.states.is_covered
        end_time = perf_counter() + timeout
        while not bid and perf_counter() < end_time:
            bid = self.states.is_covered
        return (Element(self.owner, backend_id=bid) if bid
                else NoneElement(self.owner, 'over()', {'timeout': timeout}))

    def offset(self, locator=None, x=None, y=None, timeout=None):
        if x == y is None:
            x, y = self.rect.midpoint
            x, y = int(x), int(y)
        else:
            nx, ny = self.rect.location
            x = int(nx + (x or 0))
            y = int(ny + (y or 0))
        ast = parse_loc(locator) if locator else None
        if timeout is None:
            timeout = self.timeout

        def do():
            try:
                ele = Element(self.owner, backend_id=self.owner._run_cdp(
                    'DOM.getNodeForLocation', x=x, y=y, includeUserAgentShadowDOM=True,
                    ignorePointerEventsNone=False)['backendNodeId'])
            except CDPError:
                ele = False
            return ele if ele and (ast is None or ast.match(ele)) else None

        r = wait_until(do, timeout=timeout)
        return r if r is not None else NoneElement(self.owner, 'offset()',
                                                   {'locator': locator, 'x': x, 'y': y, 'timeout': timeout})

    def east(self, loc_or_pixel=None, index=1):
        return self._get_relative_eles('east', loc_or_pixel, index)

    def south(self, loc_or_pixel=None, index=1):
        return self._get_relative_eles('south', loc_or_pixel, index)

    def west(self, loc_or_pixel=None, index=1):
        return self._get_relative_eles('west', loc_or_pixel, index)

    def north(self, loc_or_pixel=None, index=1):
        return self._get_relative_eles('north', loc_or_pixel, index)

    def _get_relative_eles(self, mode='north', locator=None, index=1):
        if locator is not None and not isinstance(locator, (str, Q, int)):
            raise LocatorError(f'定位符必须是 DSL 字符串、Q 对象或像素数，而不是 {locator!r}')
        rect = self.states.has_rect
        if not rect:
            raise NoRectError

        if mode == 'east':
            cdp_data = {'x': int(rect[1][0]), 'y': int(self.rect.midpoint[1]),
                        'includeUserAgentShadowDOM': True, 'ignorePointerEventsNone': False}
            variable, minus = 'x', False
        elif mode == 'south':
            cdp_data = {'x': int(self.rect.midpoint[0]), 'y': int(rect[2][1]),
                        'includeUserAgentShadowDOM': True, 'ignorePointerEventsNone': False}
            variable, minus = 'y', False
        elif mode == 'west':
            cdp_data = {'x': int(rect[0][0]), 'y': int(self.rect.midpoint[1]),
                        'includeUserAgentShadowDOM': True, 'ignorePointerEventsNone': False}
            variable, minus = 'x', True
        else:  # north
            cdp_data = {'x': int(self.rect.midpoint[0]), 'y': int(rect[0][1]),
                        'includeUserAgentShadowDOM': True, 'ignorePointerEventsNone': False}
            variable, minus = 'y', True

        if isinstance(locator, int):
            cdp_data[variable] += -locator if minus else locator
            try:
                return Element(self.owner, backend_id=self.owner._run_cdp(
                    'DOM.getNodeForLocation', **cdp_data)['backendNodeId'])
            except CDPError:
                return NoneElement(self.owner, f'{mode}()', {'locator': locator})

        ast = parse_loc(locator) if locator else None
        num = 0
        value = -8 if minus else 8
        size = self.owner.rect.size
        max_len = size[0] if mode in ('east', 'west') else size[1]
        curr_ele = None
        while 0 < cdp_data[variable] < max_len:
            cdp_data[variable] += value
            try:
                bid = self.owner._run_cdp('DOM.getNodeForLocation', **cdp_data)['backendNodeId']
                if bid == curr_ele:
                    continue
                curr_ele = bid
                ele = Element(self.owner, backend_id=bid)
                if ast is None or ast.match(ele):
                    num += 1
                    if num == index:
                        return ele
            except Exception:
                pass

        return NoneElement(self.owner, f'{mode}()', {'locator': locator})

    # ----------id 互转----------
    def _get_obj_id(self, node_id=None, backend_id=None):
        if node_id:
            return self.owner._run_cdp('DOM.resolveNode', nodeId=node_id)['object']['objectId']
        return self.owner._run_cdp('DOM.resolveNode', backendNodeId=backend_id)['object']['objectId']

    def _get_node_id(self, obj_id=None, backend_id=None):
        if obj_id:
            return self.owner._run_cdp('DOM.requestNode', objectId=obj_id)['nodeId']
        n = self.owner._run_cdp('DOM.describeNode', backendNodeId=backend_id)['node']
        self._tag = n['localName']
        return n['nodeId']

    def _get_backend_id(self, node_id):
        n = self.owner._run_cdp('DOM.describeNode', nodeId=node_id)['node']
        self._tag = n['localName']
        return n['backendNodeId']

    def _refresh_id(self):
        self._obj_id = self._get_obj_id(backend_id=self._backend_id)
        self._node_id = self._get_node_id(obj_id=self._obj_id)

    def _get_ele_path(self, xpath=True):
        """生成到根节点的 XPath / css 路径。"""
        if xpath:
            txt1 = 'let tag = el.nodeName.toLowerCase();'
            txt3 = ' && sib.nodeName.toLowerCase()===tag'
            txt4 = 'path = \'/\' + tag + \'[\' + nth + \']\' + path;'
            txt5 = 'return path;'
        else:
            txt1 = ('let i = el.getAttribute("id");'
                    'if (i){path = \'>\' + el.tagName.toLowerCase() + \'[id="\' + i + \'"]\' + path;'
                    'el = el.parentNode;continue;}')
            txt3 = ''
            txt4 = 'path = \'>\' + el.tagName.toLowerCase() + ":nth-child(" + nth + ")" + path;'
            txt5 = 'return path.substr(1);'
        js = f'''function(){{
        function e(el) {{
            if (!(el instanceof Element)) return;
            let path = '';
            while (el.nodeType === Node.ELEMENT_NODE) {{
                {txt1}
                    let sib = el, nth = 0;
                    while (sib) {{
                        if(sib.nodeType === Node.ELEMENT_NODE{txt3}){{nth += 1;}}
                        sib = sib.previousSibling;
                    }}
                    {txt4}
                el = el.parentNode;
            }}
            {txt5}
        }}
        return e(this);}}'''
        return self._run_js(js)

    @property
    def xpath(self):
        return self._get_ele_path(xpath=True)

    @property
    def css_selector(self):
        return self._get_ele_path(xpath=False)

    def property(self, name):
        """注意：方法名占用 property，须放在所有 @property 装饰的定义之后（与上游一致）。"""
        try:
            value = self._run_js(f'return this.{name};')
            return format_html(value) if isinstance(value, str) else value
        except Exception:
            return None


class ShadowRoot(object):
    """Shadow root 对象，ele()/eles() 以自身为上下文节点查找（穿 open shadow DOM 的下层）。"""

    _type = 'ShadowRoot'

    def __init__(self, parent_ele, obj_id=None, backend_id=None):
        self.owner = parent_ele.owner
        self.parent_ele = parent_ele
        if backend_id:
            self._backend_id = backend_id
            self._obj_id = self._get_obj_id(backend_id)
            self._node_id = self._get_node_id(self._obj_id)
        elif obj_id:
            self._obj_id = obj_id
            self._node_id = self._get_node_id(obj_id)
            self._backend_id = self._get_backend_id(self._node_id)
        self._states = None

    def __repr__(self):
        return f'<ShadowRoot in {self.parent_ele}>'

    def __eq__(self, other):
        return self._backend_id == getattr(other, '_backend_id', None)

    def __hash__(self):
        return hash(('ShadowRoot', self._backend_id))

    @property
    def tag(self):
        return 'shadow-root'

    @property
    def html(self):
        return f'<shadow_root>{self.inner_html}</shadow_root>'

    @property
    def inner_html(self):
        return self._run_js('return this.innerHTML;')

    @property
    def timeout(self):
        return self.owner.timeout

    @property
    def states(self):
        if self._states is None:
            self._states = ShadowRootStates(self)
        return self._states

    def run_js(self, script, *args, as_expr=False, timeout=None):
        return self._run_js(script, *args, as_expr=as_expr, timeout=timeout)

    def _run_js(self, script, *args, as_expr=False, timeout=None):
        return run_js(self, script, as_expr, self.owner.timeouts.script if timeout is None else timeout, args)

    def run_async_js(self, script, *args, as_expr=False, timeout=None):
        from threading import Thread
        Thread(target=run_js, args=(self, script, as_expr,
                                    self.owner.timeouts.script if timeout is None else timeout, args)).start()

    # ----------查找----------
    def ele(self, locator, index=1, timeout=None):
        return self._ele(locator, timeout=timeout, index=index, method='ele()')

    def eles(self, locator, timeout=None):
        return self._ele(locator, timeout=timeout, index=None)

    def _ele(self, locator, timeout=None, index=1, raise_err=None, method=None):
        if isinstance(locator, (Element, ShadowRoot)):
            return locator
        if timeout is None:
            timeout = self.timeout
        r = self._find_elements(locator, timeout=timeout, index=index, raise_err=raise_err)
        if r or isinstance(r, (list, float, int)):
            return r
        if raise_err is True or (Settings.raise_when_ele_not_found and raise_err is None):
            raise ElementNotFoundError(f'元素未找到：{method or "ele()"}() '
                                       f'{{\'locator\': {locator!r}, \'index\': {index!r}}}')
        r.method = method
        r.args = {'locator': locator, 'index': index, 'timeout': timeout}
        return r

    def _find_elements(self, locator, timeout, index=1, relative=False, raise_err=None):
        self.owner.wait.doc_loaded()
        return _find_in_sr(self, parse_loc(locator), index, timeout)

    def parent(self, level_or_loc=1, index=1, timeout=0):
        return self.parent_ele.parent(level_or_loc, index=index, timeout=timeout)

    def child(self, locator='', index=1, timeout=None):
        return self._sr_query('function(){return this.children;}',
                              parse_loc(locator) if locator else None, index, timeout)

    def children(self, locator='', timeout=None):
        return self._sr_query('function(){return this.children;}',
                              parse_loc(locator) if locator else None, None, timeout)

    def _sr_query(self, js, ast, index, timeout):
        """在自身对象上执行返回节点集合的 js，AST 精筛后按 index 语义取值。"""
        if timeout is None:
            timeout = self.timeout

        def do():
            eles = _eval_js_nodes(self.owner, self._obj_id, js)
            if ast is not None:
                eles = [e for e in (eles or []) if ast.match(e)]
            if not eles:
                return None
            return _pick(eles, index) or None

        return wait_for_ele(do, target=self.owner, timeout=timeout, index=index)

    def _get_node_id(self, obj_id):
        return self.owner._run_cdp('DOM.requestNode', objectId=obj_id)['nodeId']

    def _get_obj_id(self, backend_id):
        return self.owner._run_cdp('DOM.resolveNode', backendNodeId=backend_id)['object']['objectId']

    def _get_backend_id(self, node_id):
        r = self.owner._run_cdp('DOM.describeNode', nodeId=node_id)['node']
        return r['backendNodeId']


# ----------------------------------------------------------------查找机械----------------------------------------------------------------

def _pick(eles, index):
    """按 index 语义从列表取元素：None 全量、正数第 N 个、负数倒数第 N 个。"""
    if index is None:
        return eles
    index = index or 1
    if not eles or abs(index) > len(eles):
        return None
    return eles[index - 1] if index > 0 else eles[index]


def wait_for_ele(do_func, target, timeout, index, **kwargs):
    """轮询执行查找直到命中或超时；未命中返回 NoneElement / 空 ElementsList。"""
    r = wait_until(do_func, timeout, **kwargs)
    if r is not None:
        return r
    return NoneElement(target) if index is not None else ElementsList(owner=target)


def _find_by_perform_search(page, xpath, index, timeout):
    """页面级查找：DOM.performSearch 直查（可穿 shadow DOM）。"""
    return wait_for_ele(_do_find_perform_search, target=page, timeout=timeout, index=index,
                        page=page, xpath=xpath, ind=index)


def _do_find_perform_search(page, xpath, ind):
    r = page._run_cdp('DOM.performSearch', query=xpath, includeUserAgentShadowDOM=True, _ignore=True)
    search_id, count = r.get('searchId'), r.get('resultCount')
    if not count or (ind and count < abs(ind)):
        return _discard_search_results(page, search_id)
    r = page._run_cdp('DOM.getSearchResults', searchId=search_id, fromIndex=0,
                      toIndex=count, _ignore=True).get('nodeIds')
    _discard_search_results(page, search_id)
    ids = [i for i in (r or []) if i]
    if not ids:
        return None
    return make_eles(page, ids, ind, 'node_id') or None


def _discard_search_results(page, search_id):
    if search_id:
        page._run_cdp('DOM.discardSearchResults', searchId=search_id, _timeout=0, _ignore=True)


def _find_by_query(page, ast, index, timeout):
    """页面级 hybrid 查询：XPath 安全超集预筛 + ast.match() 本地精筛。"""
    return wait_for_ele(_do_find_query, target=page, timeout=timeout, index=index,
                        page=page, ast=ast, ind=index)


def _do_find_query(page, ast, ind):
    eles = _do_find_perform_search(page, to_xpath(ast), None)
    if not eles:
        return None
    matched = [e for e in eles if ast.match(e)]
    return _pick(matched, ind) or None


def _find_in_ele(ele, ast, index, timeout):
    """元素/ShadowRoot 内查找：document.evaluate 以 this 为上下文。
    纯查询用精确 XPath；hybrid 用预筛超集 + ast.match() 精筛。"""
    pure = is_pure_xpath(ast)
    xpath = to_xpath(ast, relative=True)
    node_txt = 'this.contentDocument' if getattr(ele, 'tag', '') in __FRAME_ELEMENT__ else 'this'
    if pure and index == 1:
        js = make_js_for_find_ele_by_xpath(xpath, '9', node_txt)
        return wait_for_ele(_do_find_in_ele_single, target=ele.owner, timeout=timeout, index=index,
                            ele=ele, js=js, xpath=xpath)
    js = make_js_for_find_ele_by_xpath(xpath, '7', node_txt)
    return wait_for_ele(_do_find_in_ele_multi, target=ele.owner, timeout=timeout, index=index,
                        ele=ele, js=js, xpath=xpath, ast=None if pure else ast, ind=index)


def _do_find_in_ele_single(ele, js, xpath):
    """type 9 单结果求值。"""
    res = ele.owner._run_cdp('Runtime.callFunctionOn', functionDeclaration=js, objectId=ele._obj_id,
                             returnByValue=False, awaitPromise=True, userGesture=True, _ignore=True)
    if 'exceptionDetails' in res:
        return _raise_eval_error(res, xpath)
    result = res.get('result', {})
    if result.get('subtype') == 'null':
        return None
    return make_eles(ele.owner, result.get('objectId'), 1, 'obj_id') or None


def _do_find_in_ele_multi(ele, js, xpath, ast, ind):
    eles = _eval_xpath_all(ele.owner, ele._obj_id, js, xpath)
    if ast is not None:
        eles = [e for e in (eles or []) if ast.match(e)]
    if not eles:
        return None
    return _pick(eles, ind) or None


def _find_in_sr(sr, ast, index, timeout):
    """ShadowRoot 内查找。M3 PoC 结论（Chrome 157，见 req.md 踩坑）：
    document.evaluate 不接受 shadowRoot 作上下文节点（NotSupportedError），
    performSearch 也不穿 open shadow——按设计预案走回退路线：
    querySelectorAll('*') 全量候选 + ast.match() 精筛（shadow 树小，无需预筛）。"""
    return sr._sr_query('function(){return this.querySelectorAll("*");}', ast, index, timeout)


def _eval_js_nodes(owner, obj_id, js):
    """执行返回节点集合（NodeList/HTMLCollection/Array）的 js，转换为 Element 列表；
    空集合或失败返回 None。"""
    res = owner._run_cdp('Runtime.callFunctionOn', functionDeclaration=js, objectId=obj_id,
                         returnByValue=False, awaitPromise=True, userGesture=True, _ignore=True)
    if 'exceptionDetails' in res:
        exc = res.get('exceptionDetails') or {}
        raise LocatorError(f'查找元素出错：{exc.get("text") or res}')
    result = res.get('result', {})
    if result.get('subtype') == 'null' or not result.get('objectId'):
        return None
    props = owner._run_cdp('Runtime.getProperties', objectId=result['objectId'],
                           ownProperties=True, _ignore=True).get('result', [])
    eles = ElementsList(owner=owner)
    for p in props:
        if p['name'].isdigit() and p['value']['type'] == 'object':
            e = make_eles(owner, p['value']['objectId'], 1, 'obj_id')
            if e is False:
                return None
            if e:
                eles.append(e)
    return eles or None


def _eval_xpath_all(owner, obj_id, js, xpath):
    """type 7 快照求值，返回 Element 列表；空或失败返回 None。"""
    res = owner._run_cdp('Runtime.callFunctionOn', functionDeclaration=js, objectId=obj_id,
                         returnByValue=False, awaitPromise=True, userGesture=True, _ignore=True)
    if 'exceptionDetails' in res:
        return _raise_eval_error(res, xpath)
    result = res.get('result', {})
    if result.get('subtype') == 'null' or result.get('description') in ('NodeList(0)', 'Array(0)'):
        return None
    props = owner._run_cdp('Runtime.getProperties', objectId=result['objectId'],
                           ownProperties=True, _ignore=True).get('result', [])
    eles = ElementsList(owner=owner)
    for p in props:
        if p['name'].isdigit() and p['value']['type'] == 'object':
            e = make_eles(owner, p['value']['objectId'], 1, 'obj_id')
            if e is False:
                return None
            if e:
                eles.append(e)
    return eles or None


def _raise_eval_error(res, xpath):
    exc = res.get('exceptionDetails') or {}
    desc = str(exc.get('exception', {}).get('description') or exc.get('text')
               or res.get('result', {}).get('description') or res)
    if 'is not a valid XPath expression' in desc:
        raise LocatorError(f'不是合法的 XPath 表达式：{xpath}')
    raise LocatorError(f'查找元素出错：{desc}')


def make_eles(page, _ids, index=1, id_type='obj_id'):
    """按 id 类型批量生成 Element。index 为 None 时返回全部（ElementsList），
    正数取第 N 个，负数取倒数第 N 个；节点失效返回 False。"""
    get_node_func = {'obj_id': _get_node_by_obj_id, 'node_id': _get_node_by_node_id,
                     'backend_id': _get_node_by_backend_id}[id_type]
    if not isinstance(_ids, (list, tuple)):
        _ids = (_ids,)

    if index is not None:
        index = index or 1
        if index < 0:
            _ids = _ids[::-1]
            index = abs(index)
        if abs(index) > len(_ids):
            return None
        return get_node_func(page, _ids[index - 1])

    nodes = ElementsList(owner=page)
    for _id in _ids:
        tmp = get_node_func(page, _id)
        if tmp is False:
            return False
        if tmp is not None:
            nodes.append(tmp)
    return nodes


def _get_node_info(page, id_type, _id):
    if not _id:
        return False
    r = page._run_cdp('DOM.describeNode', _ignore=True, **{id_type: _id})
    return False if 'error' in r else r


def _get_node_by_backend_id(page, backend_id):
    node = _get_node_info(page, 'backendNodeId', backend_id)
    if node is False:
        return False
    if node['node']['nodeName'] in ('#text', '#comment'):
        return None
    info = page._run_cdp('DOM.resolveNode', backendNodeId=backend_id, _ignore=True)
    return False if 'error' in info else make_ele(page, info['object']['objectId'], node)


def _get_node_by_obj_id(page, obj_id):
    node = _get_node_info(page, 'objectId', obj_id)
    if node is False:
        return False
    if node['node']['nodeName'] in ('#text', '#comment'):
        return None
    return make_ele(page, obj_id, node)


def _get_node_by_node_id(page, node_id):
    node = _get_node_info(page, 'nodeId', node_id)
    if node is False:
        return False
    if node['node']['nodeName'] in ('#text', '#comment'):
        return None
    info = page._run_cdp('DOM.resolveNode', nodeId=node_id, _ignore=True)
    return False if 'error' in info else make_ele(page, info['object']['objectId'], node)


def make_ele(page, obj_id, node):
    """生成 Element；iframe/frame/object 且带 frameId 的节点包装为 Frame 对象。"""
    ele = Element(page, obj_id=obj_id, node_id=node['node']['nodeId'],
                  backend_id=node['node']['backendNodeId'])
    if node['node'].get('nodeName', '').lower() in __FRAME_ELEMENT__ and 'frameId' in node['node']:
        from .._pages.frame import Frame
        ele = Frame(page, ele, node)
    return ele


def make_js_for_find_ele_by_xpath(xpath, type_txt, node_txt='this'):
    """构造在指定上下文节点上执行 document.evaluate 的函数。
    type_txt：'9' 取单个，'7' 取全部。查询层只生成元素谓词，结果恒为元素。"""
    if type_txt == '9':
        ret = 'if(e.singleNodeValue==null){return null;}return e.singleNodeValue;'
    else:
        ret = 'const a=[];for(let i=0;i<e.snapshotLength;i++){a.push(e.snapshotItem(i));}return a;'
    xpath = xpath.replace("'", r"\'")
    return f"function(){{let e=document.evaluate('{xpath}',{node_txt},null,{type_txt},null);{ret}}}"


# ----------------------------------------------------------------JS 执行----------------------------------------------------------------

def run_js(page_or_ele, script, as_expr, timeout, args=None):
    """执行 js：元素/ShadowRoot 上以对象为 this，页面上以文档对象为 this。"""
    if isinstance(page_or_ele, (Element, ShadowRoot)):
        is_page = False
        page = page_or_ele.owner
        obj_id = page_or_ele._obj_id
    else:
        is_page = True
        page = page_or_ele
        end_time = perf_counter() + 5
        obj_id = None
        while perf_counter() < end_time:
            obj_id = page_or_ele._root_oid
            if obj_id is not None:
                break
            sleep(.01)
        else:
            raise RuntimeError('无法获取页面文档对象，请检查页面状态')

    if page.states.has_alert:
        raise AlertExistsError

    try:
        if Path(script).exists():
            with open(script, 'r', encoding='utf-8') as f:
                script = f.read()
    except (OSError, ValueError):
        pass

    end_time = perf_counter() + timeout
    try:
        if as_expr:
            res = page._run_cdp('Runtime.evaluate', expression=script, returnByValue=False,
                                awaitPromise=True, userGesture=True, _timeout=timeout, _ignore=AlertExistsError)
        else:
            args = args or ()
            if not is_js_func(script):
                script = f'function(){{{script}}}'
            res = page._run_cdp('Runtime.callFunctionOn', functionDeclaration=script, objectId=obj_id,
                                arguments=[convert_argument(arg) for arg in args], returnByValue=False,
                                awaitPromise=True, userGesture=True, _timeout=timeout, _ignore=AlertExistsError)
    except TimeoutError:
        raise TimeoutError(f'运行 js 超时（{timeout}秒）')
    except ContextLostError:
        raise ContextLostError() if is_page else ElementLostError()

    if 'error' in res:  # _timeout=0 或 js 激活 alert 时
        return None

    result = res.get('result')
    if not result or 'type' not in result:  # alert 打断执行等场景返回空 result
        return None
    if res.get('exceptionDetails'):
        desc = str(result.get('description', '')).replace('\n', ' ')
        raise JavaScriptError(f'{desc} | js: {script}')

    try:
        return parse_js_result(page, page_or_ele, result, end_time)
    except Exception:
        raise RuntimeError(f'解析 js 结果失败：{res} | js: {script}')


def parse_js_result(page, ele, result, end_time):
    if 'unserializableValue' in result:
        return result['unserializableValue']

    the_type = result['type']
    if the_type == 'object':
        sub_type = result.get('subtype')
        if sub_type == 'null':
            return None

        if sub_type == 'node':
            class_name = result['className']
            if class_name == 'ShadowRoot':
                return ShadowRoot(ele, obj_id=result['objectId'])
            if class_name == 'HTMLDocument':
                return result
            r = make_eles(page, result['objectId'])
            if r is False:
                raise ElementLostError
            return r

        if sub_type == 'array':
            r = page._run_cdp('Runtime.getProperties', objectId=result['objectId'],
                              ownProperties=True)['result']
            return [parse_js_result(page, ele, i['value'], end_time)
                    for i in r if i['name'].isdigit()]

        if result.get('className') == 'Blob':
            data = page._run_cdp(
                'IO.read', handle=f"blob:{page._run_cdp('IO.resolveBlob', objectId=result['objectId'])['uuid']}")['data']
            return data

        if 'objectId' in result:
            timeout = end_time - perf_counter()
            if timeout < 0:
                return None
            js = 'function(){return JSON.stringify(this);}'
            r = page._run_cdp('Runtime.callFunctionOn', functionDeclaration=js, objectId=result['objectId'],
                              returnByValue=False, awaitPromise=True, userGesture=True,
                              _ignore=AlertExistsError, _timeout=timeout)
            return loads(parse_js_result(page, ele, r.get('result'), end_time))

        return result.get('value', result)

    if the_type == 'undefined':
        return None

    if the_type == 'function':
        return result['description']

    return result['value']


def convert_argument(arg):
    if isinstance(arg, Element):
        return {'objectId': arg._obj_id}
    if arg == inf:
        return {'unserializableValue': 'Infinity'}
    if arg == -inf:
        return {'unserializableValue': '-Infinity'}
    if isinstance(arg, (int, float, str, bool, dict, list, tuple)):
        return {'value': list(arg) if isinstance(arg, tuple) else arg}
    raise TypeError(f'不支持的参数类型：{arg}（{type(arg)}）')
