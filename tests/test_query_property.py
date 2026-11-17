# -*- coding:utf-8 -*-
"""性质测试（final-syntax.md §9）：
1. canonical() / to_query() 往返：随机合法 AST → 文本 → 再解析 → 结构全同
2. 混合下推 soundness：随机 AST 在 fixture 文档上，本地匹配集 ⊆ XPath 预筛候选集；
   pure 且片段非恒真时二者相等（零漏杀、零误杀）
"""
import string

from hypothesis import HealthCheck, given, settings as hs
from hypothesis import strategies as st

from happybrowser._locate import query as ql
from happybrowser._locate.query import Op, TagNode, AttrNode, TextNode, AndNode, OrNode, NotNode

NON_REGEX_OPS = [Op.EQ, Op.CONTAINS, Op.STARTS, Op.ENDS]

tag_v = st.from_regex(r'[A-Za-z][A-Za-z0-9-]{0,6}', fullmatch=True)
word_v = st.text(alphabet=string.ascii_letters + string.digits + '-_', min_size=1, max_size=6)
attr_name = st.sampled_from(['id', 'class', 'type', 'data-testid', 'data_id', 'alt', 'href', 'a.b'])
special_name = st.sampled_from(['@click', ':class', 'xlink:href'])
# XML 兼容字符（≥0x20，排除代理区与 FFFE/FFFF），lxml 求值器的要求
text_v = st.text(alphabet=st.characters(min_codepoint=0x20,
                                        blacklist_categories=('Cs',),
                                        blacklist_characters='\ufffe\uffff'), max_size=6)
regex_v = st.sampled_from([r'\d+', 'a.c', '(x|y)+', r'^z', '[0-9]{2}', '登录'])

leaf = st.one_of(
    st.builds(TagNode, st.sampled_from(NON_REGEX_OPS), tag_v),
    st.builds(TagNode, st.just(Op.REGEX), regex_v),
    st.builds(AttrNode, attr_name, st.sampled_from(NON_REGEX_OPS), text_v),
    st.builds(AttrNode, attr_name, st.just(Op.WORD), word_v),
    st.builds(AttrNode, attr_name, st.none(), st.none()),
    st.builds(AttrNode, special_name, st.sampled_from(NON_REGEX_OPS), text_v),
    st.builds(TextNode, st.sampled_from(['text', 'owntext']), st.sampled_from(NON_REGEX_OPS), text_v),
    st.builds(TextNode, st.sampled_from(['text', 'owntext']), st.just(Op.REGEX), regex_v),
)

ast = st.recursive(
    leaf,
    lambda ch: st.one_of(
        st.builds(AndNode, st.lists(ch, min_size=2, max_size=3).map(tuple)),
        st.builds(OrNode, st.lists(ch, min_size=2, max_size=3).map(tuple)),
        st.builds(NotNode, ch),
    ),
    max_leaves=10,
)


@hs(max_examples=300, deadline=None)
@given(node=ast)
def test_canonical_roundtrip(node):
    assert ql.parse_query('?' + ql.canonical(node)) == node


@hs(max_examples=300, deadline=None)
@given(node=ast)
def test_to_query_roundtrip(node):
    assert ql.parse_query(ql.to_query(node)) == node


@hs(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(node=ast)
def test_pushdown_soundness(doc, node):
    candidates = set(doc.xpath(ql.to_xpath(node)))
    local = {e for e in doc.xpath('//*') if ql.match_query(node, e)}
    assert local <= candidates
    if ql.is_pure_xpath(node) and ql.pushdown_fragment(node) != 'true()':
        assert local == candidates
