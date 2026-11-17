# -*- coding:utf-8 -*-
"""XPath 编译与混合下推验收：精确字符串（final-syntax.md §6.1）、pure/hybrid 判定、安全超集规则。"""
import pytest

from happybrowser._locate import query as ql
from happybrowser._locate.query import (Q, parse_loc, to_xpath, xpath_literal,
                                        is_pure_xpath, has_local, pushdown_fragment)

T = "translate(name(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')"
WBTN = 'contains(concat(" ",normalize-space(@class)," ")," btn ")'


@pytest.mark.parametrize('src,expect', [
    ('input', f'//*[{T}="input"]'),
    ('tag=div', f'//*[{T}="div"]'),
    ('#login', '//*[@id="login"]'),
    ('.btn', f'//*[{WBTN}]'),
    ('type=filter', '//*[@type="filter"]'),
    ('type^=fi', '//*[starts-with(@type,"fi")]'),
    ('type$=er', '//*[string-length(@type) >= 2 and substring(@type, string-length(@type) - 1) = "er"]'),
    ('@data-testid', '//*[@data-testid]'),
    ('@*', '//*[@*]'),
    ('!@*', '//*[not(@*)]'),
    ('!id=ad', '//*[not(@id="ad")]'),
    ('text*=ABC', '//*[contains(.,"ABC")]'),
    ('text=ABC', '//*[.="ABC"]'),
    ('text^=A', '//*[starts-with(.,"A")]'),
    ('text=""', '//*[.=""]'),
    ('text$=ABC', '//*[string-length(.) >= 3 and substring(., string-length(.) - 2) = "ABC"]'),
    ('owntext=ABC', '//*[text()[.="ABC"]]'),
    ('owntext=""', '//*[text()[.=""]]'),   # DOM 无空文本节点：恒不命中，与本地求值精确一致
    ('owntext*=""', '//*[text()]'),        # 空值包含 = 存在任意直属文本节点
    ('owntext*=ABC', '//*[text()[contains(.,"ABC")]]'),
    ('owntext^=A', '//*[text()[starts-with(.,"A")]]'),
    ('class*=""', '//*[@class]'),  # 空串包含匹配等价于属性存在
    ('input && .btn', f'//*[{T}="input" and {WBTN}]'),
    ('div || span', f'//*[{T}="div" or {T}="span"]'),
    ('(div || span) && .btn', f'//*[({T}="div" or {T}="span") and {WBTN}]'),
])
def test_exact_xpath(src, expect):
    assert to_xpath(parse_loc(src)) == expect


def test_relative():
    assert to_xpath(parse_loc('input'), relative=True) == f'.//*[{T}="input"]'


class TestHybrid:
    def test_regex_local(self):
        ast = parse_loc('a && href/="x"')
        assert has_local(ast) and not is_pure_xpath(ast)
        assert pushdown_fragment(ast) == f'{T}="a"'
        assert to_xpath(ast) == f'//*[{T}="a"]'

    def test_not_local_never_negated(self):
        ast = parse_loc('!(href/="x") && b')
        assert has_local(ast)
        # Not 含本地节点 → 恒真，但 And 中其余可下推子项仍参与预筛（安全超集）
        assert pushdown_fragment(ast) == f'{T}="b"'
        assert to_xpath(ast) == f'//*[{T}="b"]'

    def test_or_local(self):
        ast = parse_loc('href/="x" || b')
        assert has_local(ast)
        assert to_xpath(ast) == '//*'

    def test_non_qname_attr_local(self):
        for src in ('@":class"', '@"@click"*=submit', 'xlink:href^=https://'):
            assert has_local(parse_loc(src)), src
            assert to_xpath(parse_loc(src)) == '//*'

    def test_tag_regex_local(self):
        assert has_local(parse_loc(r'tag/="^d"'))

    def test_pure_examples(self):
        for src in ('a && .btn', '!@disabled', '(div || span) && !id=ad', 'text*=x'):
            assert is_pure_xpath(parse_loc(src)), src


class TestXpathLiteral:
    def test_plain(self):
        assert xpath_literal('abc') == '"abc"'

    def test_has_double_quote(self):
        assert xpath_literal('a"b') == "'a\"b'"

    def test_has_both(self):
        assert xpath_literal('a"b\'c') == 'concat("a",\'"\',"b\'c")'


def test_explain():
    out = ql.explain_loc('a && .btn')
    assert 'pure-xpath' in out and f'//*[{T}="a" and {WBTN}]' in out
    out2 = ql.explain_loc(Q('a', href__regex='x'))
    assert 'hybrid' in out2 and f'//*[{T}="a"]' in out2
