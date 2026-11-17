# -*- coding: utf-8 -*-
"""M6 生态冒烟：把上游生态的 drission_utils.py 与 ai_studio.py 的真实用法模式
改编到 happybrowser API 上逐条重演。这个文件同时是 docs/migration.md 的可执行佐证——
每条测试对应迁移文档的一个映射条目。

与 drission_utils 的差异（详见 docs/migration.md）：
- 旧语法 't:input' / 'tag:input' / 'css:..' / 'xpath:..' 全部换成 DSL（裸标签 / .cls / #id）；
- s_ele() 快照模式改为直接在 Element 上走 prevs/nexts/parent；
- ElementsList 无 .wait 挂件（过滤 DSL 已砍），可见性过滤用推导式；
- parent() 现在直接接受 Q 对象，不再需要 Q.xpath(relative=True) 绕道。"""
import http.server
import threading

import pytest

from happybrowser import Browser, NoneElement, Options, Q, LocatorError

try:
    from happybrowser._core.launcher import get_chromium_path  # noqa: F401 探测浏览器可用性
    HAS_BROWSER = True
except Exception:
    HAS_BROWSER = False

pytestmark = pytest.mark.skipif(not HAS_BROWSER, reason='本机未找到 Chromium 系浏览器')

# 结构对齐 drission_utils 的真实场景：label 关联输入框、CJK 按钮文字（含逐字分渲染）、
# 多 class 链接、滚动容器、fetch 触发按钮
HTML = '''<!DOCTYPE html><html><head><title>hb-eco</title></head><body>
<div id="wrap" class="wrap main-area">
  <form id="f" class="form-body">
    <label id="lbl-user">用户名</label>
    <div class="row"><input id="user" name="user" type="text" data-role="user-input"></div>
    <label id="lbl-note">备注</label>
    <div class="row2"><textarea id="note">hello</textarea></div>
    <button id="btn-ok" class="btn primary" type="submit">确定</button>
    <button id="btn-cancel" class="btn ghost" type="button"><span>取 </span><span>消</span></button>
    <a id="link-a" class="ext link-a" href="/a">A</a>
    <a id="link-b" class="ext link-b" href="/b">B</a>
    <a id="link-c" class="int link-c" href="/c">C</a>
    <div id="scroll-host" style="height:80px;overflow:auto;"><div id="inner" style="height:400px;">tall</div></div>
    <button id="hit-api" type="button" onclick="fetch('/api/data').then(r=>r.json())">api</button>
  </form>
</div>
</body></html>'''


@pytest.fixture(scope='module')
def server(tmp_path_factory):
    d = tmp_path_factory.mktemp('hb-eco')
    (d / 'index.html').write_text(HTML, encoding='utf-8')

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split('?')[0] == '/api/data':
                body = b'{"code": 1, "msg": "ok"}'
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                f = d / self.path.split('?')[0].lstrip('/')
                if f.is_file():
                    body = f.read_bytes()
                    self.send_response(200)
                    # 必须显式 charset，否则 Chrome 嗅探编码会把 UTF-8 中文变 mojibake，
                    # 文本查询（find_button 的 CJK 文字）全部落空
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_error(404)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{srv.server_address[1]}'
    srv.shutdown()


@pytest.fixture(scope='module')
def browser():
    opts = Options()
    opts.auto_port = True
    opts.headless()
    b = Browser(opts)
    yield b
    try:
        b.quit()
    except Exception:
        pass


@pytest.fixture(scope='module')
def tab(browser, server):
    t = browser.new_tab(server + '/index.html')
    yield t


# ----------------------------------------------------------------drission_utils._build_query
# Q.tag / Q.attr('class').contains OR 链 / kwargs 下划线转中划线 / attrs 全等

def _build_query(tag=None, class_=None, attrs=None, **kwargs):
    """drission_utils._build_query 原样移植——Q API 与 fork 完全同形。"""
    conds = []
    if tag:
        conds.append(Q.tag(tag))
    if class_:
        cls_list = class_ if isinstance(class_, (list, tuple)) else [class_]
        cls_q = None
        for c in cls_list:
            cond = Q.attr('class').contains(c)
            cls_q = cond if cls_q is None else (cls_q | cond)
        conds.append(cls_q)
    for k, v in kwargs.items():
        conds.append(Q.attr(k.replace('_', '-')).eq(v))
    if attrs:
        for k, v in attrs.items():
            conds.append(Q.attr(k).eq(v))
    q = None
    for c in conds:
        q = c if q is None else (q & c)
    return q


def test_build_query_composition(tab):
    q = _build_query(tag='a', class_=['link-a', 'link-b'], attrs={'href': '/a'})
    assert q is not None
    assert tab.ele(q).attr('id') == 'link-a'


def test_build_query_class_or(tab):
    q = _build_query(tag='a', class_=['link-b', 'link-c'])
    ids = {e.attr('id') for e in tab.eles(q, timeout=0)}
    assert ids == {'link-b', 'link-c'}


def test_build_query_kwargs_underscore_to_dash(tab):
    # kwargs 键 data_role → 属性 data-role（drission_utils 语义）
    q = _build_query(data_role='user-input')
    assert tab.ele(q).attr('id') == 'user'


def test_q_canonical_on_miss(tab):
    # drission_utils find_el 未命中时打 canonical() 日志的模式
    q = _build_query(tag='a', attrs={'href': '/nope'})
    assert tab.eles(q, timeout=0) == []
    assert 'href="/nope"' in q.canonical()


# ----------------------------------------------------------------drission_utils.find_button

def find_button(page, btn_label: str):
    """drission_utils.find_button 原样移植（Q.text.eq + is_clickable + 向上找 button）。"""
    label_variants = [btn_label, ' '.join(btn_label)]
    for _ in range(3):
        for lbl in label_variants:
            loc = Q.text.eq(lbl)
            for btn in page.eles(loc, timeout=0):
                if not btn.states.is_clickable:
                    continue
                cur = btn
                for _ in range(3):
                    if cur is None or isinstance(cur, NoneElement):
                        break
                    if cur.tag == 'button':
                        return cur
                    cur = cur.parent()
    return None


def test_find_button_plain_text(tab):
    btn = find_button(tab, '确定')
    assert btn is not None and btn.attr('id') == 'btn-ok'


def test_find_button_spaced_cjk_variant(tab):
    # 站点把 CJK 逐字分渲染：DOM 深层文本是 '取 消'，逐字加空格变体命中
    btn = find_button(tab, '取消')
    assert btn is not None and btn.attr('id') == 'btn-cancel'


# ----------------------------------------------------------------drission_utils.ele_visible 族

def test_ele_visible_pattern(tab):
    # ele_visible = self.ele(loc).wait.displayed()；返回元素本体，生态按 truthiness 用
    assert tab.ele('#btn-ok').wait.displayed()


def test_eles_visible_pattern(tab):
    # ElementsList 无 .wait 挂件（已砍），改为推导式——迁移文档条目
    eles = tab.eles('a && .ext', timeout=0)
    visible = [e for e in eles if e.states.is_displayed]
    assert {e.attr('id') for e in visible} == {'link-a', 'link-b'}


# ----------------------------------------------------------------drission_utils.set_value_by_label / find_input

def set_value_by_label(scope, label_text: str, value: str, label_tag='label'):
    """drission_utils.set_value_by_label 改编：'tag:label' → 'label'（裸标签 DSL）。"""
    label = scope.ele(f'{label_tag} && text*={label_text}', timeout=1)
    parent = label.parent()
    for _ in range(4):
        if isinstance(parent, NoneElement) or parent is None:
            return None
        input_ele = parent.ele('input', timeout=0.5)
        if not isinstance(input_ele, NoneElement):
            input_ele.input(value)
            return input_ele
        parent = parent.parent()
    return None


def test_set_value_by_label(tab):
    ele = set_value_by_label(tab, '用户名', 'happy')
    assert ele is not None and ele.attr('id') == 'user'
    assert tab.ele('#user').property('value') == 'happy'


def test_find_input_next_pattern(tab):
    """drission_utils.find_input 改编：next() 看标签，否则回退父级范围搜 input。"""
    label = tab.ele('#lbl-note')
    next_ele = label.next()
    assert next_ele is not None and next_ele.tag != 'input'
    parent = label.parent()
    input_ele = parent.ele('input', timeout=0.5) if next_ele.tag != 'input' else next_ele
    assert input_ele is not None and not isinstance(input_ele, NoneElement)
    assert input_ele.attr('id') == 'note' or input_ele.tag == 'input'


def test_none_element_walk_semantics(tab):
    # isinstance(NoneElement) 判定链在 happybrowser 同样成立
    html_ele = tab.ele('html')
    top = html_ele.parent()
    assert top is None or isinstance(top, NoneElement)


# ----------------------------------------------------------------drission_utils.hl / scrollable_parent / paste

def test_hl_nohl_pattern(tab):
    ele = tab.ele('#user')
    ele.set.style('border', 'solid 1px red')
    # 计算样式会把 red 归一化成 rgb(255, 0, 0)
    assert 'rgb(255, 0, 0)' in ele.style('border')
    ele.set.style('border', 'solid 0px red')
    assert '0px' in ele.style('border')


def test_scrollable_parent_pattern(tab):
    """drission_utils.scrollable_parent 本意：自身不可滚时向上找可滚的祖先。"""
    inner = tab.ele('#inner')
    assert inner.run_js('return this.scrollHeight > this.clientHeight') is False  # 自身不可滚
    parent = inner.parent()
    assert parent.attr('id') == 'scroll-host'
    assert parent.run_js('return this.scrollHeight > this.clientHeight') is True  # 祖先可滚


def test_paste_pattern_prerequisites(tab):
    """drission_utils.paste 的两步：ele.click() 后 ele.tab.actions.type(Keys.CTRL_V)。
    此处验证路由前提：Element.tab 指回所属 Tab，actions 可用。"""
    ele = tab.ele('#note')
    assert ele.tab is tab
    ele.click()
    from happybrowser import Keys
    tab.actions.type(Keys.CTRL_A)  # 与 paste 同形的组合键调用路径
    tab.actions.type('replaced')
    assert tab.ele('#note').property('value') == 'replaced'


# ----------------------------------------------------------------drission_utils._patched_parent
# fork 的 parent() 拒绝 Q 对象（drission_utils 只好 Q.xpath(relative=True) 绕道），
# happybrowser 的方向轴统一走 parse_loc，Q 可直接传入——绕道代码可整段删除。

def test_parent_accepts_q(tab):
    inner = tab.ele('#inner')
    host = inner.parent(Q.attr('id').eq('scroll-host'))
    assert host.attr('id') == 'scroll-host'


def test_parent_level_int(tab):
    assert tab.ele('#user').parent(2).attr('id') == 'f'


def test_parent_string_dsl(tab):
    # 方向轴也接受 DSL 字符串定位符（drission_utils 旧绕道 Q.xpath(relative=True) 可整段删除）
    host = tab.ele('#inner').parent('div && id=scroll-host')
    assert host.attr('id') == 'scroll-host'


# ----------------------------------------------------------------drission_utils.get_snapshot（去 s_ele 改编）

def get_snapshot(ele, max_siblings=3, max_ancestors=5):
    """drission_utils.get_snapshot 改编：无 s_ele()，直接在 Element 上走关系轴。"""
    target = {'tag': ele.tag, 'id': ele.attr('id'), 'text': (ele.raw_text or '')[:200],
              'xpath': ele.xpath}
    siblings = {'before': [s.tag for s in ele.prevs()][:max_siblings],
                'after': [s.tag for s in ele.nexts()][:max_siblings]}
    ancestors, cur, level = [], ele, 0
    while level < max_ancestors:
        parent = cur.parent()
        if parent is None or isinstance(parent, NoneElement):
            break
        ancestors.append({'tag': parent.tag, 'id': parent.attr('id'), 'level': level + 1})
        cur, level = parent, level + 1
    return {'target': target, 'siblings': siblings, 'ancestors': ancestors}


def test_get_snapshot_pattern(tab):
    snap = get_snapshot(tab.ele('#btn-ok'))
    assert snap['target'] == {'tag': 'button', 'id': 'btn-ok', 'text': '确定',
                              'xpath': tab.ele('#btn-ok').xpath}
    # 前面的兄弟：label#lbl-user / div.row / label#lbl-note / div.row2
    assert 'label' in snap['siblings']['before']
    assert 'button' in snap['siblings']['after']
    tags = [a['tag'] for a in snap['ancestors']]
    assert 'form' in tags and 'div' in tags


# ----------------------------------------------------------------ai_studio.py 的 listen.steps 模式

def test_ai_studio_listen_steps_pattern(tab):
    """ai_studio.py 形状：listen.start(targets) → 点击触发 → for packet in listen.steps()。"""
    tab.listen.start('api/data')
    tab.ele('#hit-api').click()
    got = None
    for packet in tab.listen.steps(timeout=5):
        got = packet
        break
    tab.listen.stop()
    assert got is not None
    assert 'api/data' in got.url
    assert got.response.body == {'code': 1, 'msg': 'ok'}  # json 响应自动 loads 为 dict


# ----------------------------------------------------------------旧语法拒绝（迁移文档的承诺）

@pytest.mark.parametrize('old', ['t:input', 'tag:input', 'text:确定', 'css:.btn',
                                 'xpath://div', 'c:.btn', 'x://div', '@@id=user',
                                 'tag:input@@class=row'])
def test_old_syntax_rejected_with_tip(tab, old):
    with pytest.raises(LocatorError):
        tab.eles(old, timeout=0)


def test_single_at_prefix_is_valid_dsl(tab):
    """旧语法单 @ 条件（'@id=user'）恰是新 DSL 合法写法（@ 属性前缀），无缝迁移；
    只有 @@ 链式旧语法才需要改写。"""
    r = tab.eles('@id=user', timeout=0)
    assert len(r) == 1 and r[0].attr('id') == 'user'


def test_did_you_mean_tip_mentions_new_syntax():
    from happybrowser import parse_loc
    with pytest.raises(LocatorError) as ei:
        parse_loc('tag:input')
    assert '旧语法' in str(ei.value)


# ----------------------------------------------------------------负 index（生态在用，保留项）

def test_negative_index_kept(tab):
    links = tab.eles('a && .ext', timeout=0)
    assert tab.ele('a && .ext', index=-1) == links[-1]
    assert tab.ele('a && .ext', index=-2) == links[0]
