# -*- coding: utf-8 -*-
"""Web 工具函数（移植精简自 DrissionPage © g1879 _functions/web.py，https://DrissionPage.cn
—— 遵循其许可：仅限个人学习与合法非盈利用途，商用需上游授权）。

改造点：
- get_ele_txt 由「lxml 解析 outerHTML 快照」改为 HB_TEXT_JS 在活 DOM 上原地执行，
  逐行移植原算法（nowrap/wrap_after/no_text/tab 元素表、<pre>、<br>→换行、表格 \\t），
  不再依赖 lxml；DOM 文本节点本身已是解码后文本，无需原 format_html 的二次 unescape
  （原实现存在双重反转义，此处只做 \\xa0→空格）。
- 修复上游 wrap_after_list 的隐式字符串拼接笔误（'address' 'article' 相连成
  'addressarticle'，导致两者换行丢失）。
- M3 仅收录查找闭环所需子集；M5 增补 format_headers / get_blob / save_page / get_mhtml / get_pdf。
"""
from os.path import sep
from pathlib import Path
from urllib.parse import urlparse, urljoin, urlunparse

from .files import make_valid_name


def format_html(text):
    """\\xa0 转空格。HTML 实体解码由 DOM 天然完成，不在此重复 unescape。"""
    return text.replace('\xa0', ' ') if text else text


HB_TEXT_JS = '''function hbText(root) {
    const NOWRAP = new Set(['br','sub','sup','em','strong','a','font','b','span','s','i','del','ins','img','td',
        'th','abbr','bdi','bdo','cite','code','data','dfn','kbd','mark','q','rp','rt','ruby','samp','small',
        'time','u','var','wbr','button','slot','content']);
    const WRAP_AFTER = new Set(['p','div','h1','h2','h3','h4','h5','h6','ol','li','blockquote','header',
        'footer','address','article','aside','main','nav','section','figcaption','summary']);
    const NO_TEXT = new Set(['script','style','video','audio','iframe','embed','noscript','canvas','template']);
    const TAB = new Set(['td','th']);
    function fmt(s) {return s.replace(/\\r\\n/g, ' ').replace(/\\n/g, ' ').replace(/ {2,}/g, ' ');}
    function blank(s) {return s.replace(/[ \\n\\t\\r]/g, '') === '';}
    function walk(ele, pre) {
        const tag = ele.tagName ? ele.tagName.toLowerCase() : '';
        if (tag === 'br') return [true];
        if (!pre && tag === 'pre') pre = true;
        const out = [];
        if (NO_TEXT.has(tag) && !pre) return out;
        let prevTag = '';
        for (const n of ele.childNodes) {
            if (n.nodeType === 3) {                       // 文本节点
                if (pre) out.push(n.textContent);
                else if (!blank(n.textContent)) out.push(fmt(n.textContent));
            } else if (n.nodeType === 1) {                // 元素节点
                const t = n.tagName.toLowerCase();
                if (!NOWRAP.has(t) && out.length && out[out.length - 1] !== '\\n') out.push('\\n');
                if (TAB.has(t) && TAB.has(prevTag)) out.push('\\t');
                out.push(...walk(n, pre));
                prevTag = t;
            }
        }
        if (WRAP_AFTER.has(tag) && out.length && out[out.length - 1] !== '\\n'
                && out[out.length - 1] !== true) out.push('\\n');
        return out;
    }
    const tag = root.tagName ? root.tagName.toLowerCase() : '';
    if (NO_TEXT.has(tag)) return root.innerText;
    let parts = walk(root, false);
    if (parts.length && parts[parts.length - 1] === '\\n') parts.pop();
    const r = [];
    for (let i = 0; i < parts.length - 1; i++) {
        const a = parts[i], b = parts[i + 1];
        if (a === true) {r.push('\\n'); continue;}
        if (b === true) {r.push(a); continue;}
        r.push(a.endsWith(' ') && b.startsWith(' ') ? a.slice(0, -1) : a);
    }
    if (parts.length) r.push(parts[parts.length - 1] === true ? '\\n' : parts[parts.length - 1]);
    return r.join('').trim();
}'''


def location_in_viewport_js(loc_x, loc_y):
    return ('function(){let x = %s; let y = %s;'
            'const sl = document.documentElement.scrollLeft, st = document.documentElement.scrollTop;'
            'const w = document.documentElement.clientWidth, h = document.documentElement.clientHeight;'
            'return !(x < sl || y < st || x > w + sl || y > h + st);}' % (loc_x, loc_y))


def offset_scroll(ele, offset_x, offset_y):
    """计算元素上偏移点的视口坐标，不在视口内时先滚动到页面中间。"""
    loc_x, loc_y = ele.rect.location
    cp_x, cp_y = ele.rect.click_point
    lx = loc_x + offset_x if offset_x else cp_x
    ly = loc_y + offset_y if offset_y else cp_y
    if not ele.owner._run_js(location_in_viewport_js(lx, ly)):
        client_width = ele.owner._run_js('return document.body.clientWidth;')
        client_height = ele.owner._run_js('return document.body.clientHeight;')
        ele.owner.scroll.to_location(lx - client_width // 2, ly - client_height // 2)
    cl_x, cl_y = ele.rect.viewport_location
    ccp_x, ccp_y = ele.rect.viewport_click_point
    cx = cl_x + offset_x if offset_x else ccp_x
    cy = cl_y + offset_y if offset_y else ccp_y
    return cx, cy


def make_absolute_link(link, base_uri=None):
    """相对链接补全为绝对链接。"""
    if not link:
        return link
    link = link.strip().replace('\\', '/')
    parsed = urlparse(link)._asdict()
    if not parsed['netloc']:  # 相对路径，与页面 url 拼接
        return urljoin(base_uri, link) if base_uri else link
    if not parsed['scheme'] and base_uri:  # 缺协议，从页面 url 获取
        parsed['scheme'] = urlparse(base_uri).scheme
        return urlunparse(tuple(v for v in parsed.values()))
    return link


def is_js_func(func):
    func = func.strip()
    return (func.startswith('function') or func.startswith('async ')) and func.endswith('}')


def format_headers(txt):
    """headers 支持 dict 或 'k: v\\n k2: v2' 文本，剥掉伪头（:method 等）。"""
    if isinstance(txt, dict):
        for k, v in txt.items():
            if v not in (None, False, True):
                txt[k] = str(v)
        for i in (':method', ':scheme', ':authority', ':path'):
            txt.pop(i, None)
        return txt
    headers = {}
    for header in txt.split('\n'):
        if header:
            name, value = header.split(': ', maxsplit=1)
            if name not in (':method', ':scheme', ':authority', ':path'):
                headers[name] = value
    return headers


def get_blob(page, url, as_bytes=True):
    """抓取 blob: 链接内容：XHR 取回经 FileReader 转 dataURL。"""
    if not url.startswith('blob'):
        raise ValueError(f'该 url 不是 blob 链接：{url}')
    js = """
       function fetchData(url) {
      return new Promise((resolve, reject) => {
        let xhr = new XMLHttpRequest();
        xhr.responseType = 'blob';
        xhr.onload = function() {
          let reader  = new FileReader();
          reader.onloadend = function(){resolve(reader.result);}
          reader.readAsDataURL(xhr.response);
        };
        xhr.open('GET', url, true);
        xhr.send();
      });
    }
"""
    try:
        result = page._run_js(js, url)
    except Exception:
        raise RuntimeError('获取 blob 内容失败。')
    if as_bytes:
        from base64 import b64decode
        return b64decode(result.split(',', 1)[-1])
    return result


def save_page(tab, path=None, name=None, as_pdf=False, kwargs=None):
    """把页面存为 mhtml 或 pdf，由后缀或 as_pdf 参数决定。"""
    if name:
        if name.endswith('.pdf'):
            name = name[:-4]
            as_pdf = True
        elif name.endswith('.mhtml'):
            name = name[:-6]
            as_pdf = False

    if path:
        path = Path(path)
        if path.suffix.lower() == '.mhtml':
            name = path.stem
            path = path.parent
            as_pdf = False
        elif path.suffix.lower() == '.pdf':
            name = path.stem
            path = path.parent
            as_pdf = True

    return get_pdf(tab, path, name, kwargs) if as_pdf else get_mhtml(tab, path, name)


def get_mhtml(page, path=None, name=None):
    r = page._run_cdp('Page.captureSnapshot')['data']
    if path is None and name is None:
        return r

    path = path or '.'
    Path(path).mkdir(parents=True, exist_ok=True)
    name = make_valid_name(name or page.title)
    with open(f'{path}{sep}{name}.mhtml', 'w', encoding='utf-8', newline='') as f:
        f.write(r)
    return r


def get_pdf(page, path=None, name=None, kwargs=None):
    if not kwargs:
        kwargs = {}
    kwargs['transferMode'] = 'ReturnAsBase64'
    if 'printBackground' not in kwargs:
        kwargs['printBackground'] = True
    try:
        r = page._run_cdp('Page.printToPDF', **kwargs)['data']
    except Exception:
        raise RuntimeError('获取 pdf 失败。')
    from base64 import b64decode
    r = b64decode(r)
    if path is None and name is None:
        return r

    path = path or '.'
    Path(path).mkdir(parents=True, exist_ok=True)
    name = make_valid_name(name or page.title)
    with open(f'{path}{sep}{name}.pdf', 'wb') as f:
        f.write(r)
    return r


class NavResult(object):
    """get() 导航结果：url/状态码/响应头/请求信息。"""

    def __init__(self):
        self.url = None
        self.status = None
        self.headers = None
        self.request = None

    def __repr__(self):
        return f'<NavResult url: {self.url}, status: {self.status} >'

    @property
    def ok(self):
        if self.status is None:
            return None
        if isinstance(self.status, str):
            return False
        return 200 <= self.status <= 299

    def __bool__(self):
        return self.ok is True
