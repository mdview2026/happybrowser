# -*- coding:utf-8 -*-
"""解析器验收：谓词表（final-syntax.md §3.3）、布尔组合、值词法、`?` 前缀可省略。"""
import pytest

from happybrowser._locate.query import (Op, parse_query, QueryError,
                                        TagNode, AttrNode, TextNode, AndNode, OrNode, NotNode)

# 每条给出无 ? 形态；测试同时断言加 '?' 前缀后产出同一棵 AST
CASES = [
    ('input', TagNode(Op.EQ, 'input')),
    ('*', TagNode(Op.EQ, '*')),
    ('#login', AttrNode('id', Op.EQ, 'login')),
    ('.btn', AttrNode('class', Op.WORD, 'btn')),
    ('.el-input__inner', AttrNode('class', Op.WORD, 'el-input__inner')),
    ('#123', AttrNode('id', Op.EQ, '123')),
    ('type=filter', AttrNode('type', Op.EQ, 'filter')),
    ('type*=fil', AttrNode('type', Op.CONTAINS, 'fil')),
    ('type^=fi', AttrNode('type', Op.STARTS, 'fi')),
    ('type$=er', AttrNode('type', Op.ENDS, 'er')),
    ('class~=btn', AttrNode('class', Op.WORD, 'btn')),
    ('text*=ABC', TextNode('text', Op.CONTAINS, 'ABC')),
    ('text=ABC', TextNode('text', Op.EQ, 'ABC')),
    ('owntext*=ABC', TextNode('owntext', Op.CONTAINS, 'ABC')),
    ('owntext=ABC', TextNode('owntext', Op.EQ, 'ABC')),
    (r'text/="\d+"', TextNode('text', Op.REGEX, r'\d+')),
    ('@data-testid', AttrNode('data-testid', None, None)),
    ('@*', AttrNode('*', None, None)),
    ('@tag=x', AttrNode('tag', Op.EQ, 'x')),
    ('@"@click"*=submit', AttrNode('@click', Op.CONTAINS, 'submit')),
    ('@":class"', AttrNode(':class', None, None)),
    ('xlink:href^=https://', AttrNode('xlink:href', Op.STARTS, 'https://')),
    ('href*=https://a.com/x?q=1#frag', AttrNode('href', Op.CONTAINS, 'https://a.com/x?q=1#frag')),
    ('href*=mailto:a@b.com', AttrNode('href', Op.CONTAINS, 'mailto:a@b.com')),
    ('alt=""', AttrNode('alt', Op.EQ, '')),
    ('tag=div', TagNode(Op.EQ, 'div')),
    ('tag*=d', TagNode(Op.CONTAINS, 'd')),
]


@pytest.mark.parametrize('src,expect', CASES)
def test_predicate(src, expect):
    assert parse_query(src) == expect
    assert parse_query('?' + src) == expect


class TestBool:
    def test_and(self):
        assert parse_query('input && type=filter') == AndNode((TagNode(Op.EQ, 'input'),
                                                               AttrNode('type', Op.EQ, 'filter')))

    def test_and_single_alias(self):
        assert parse_query('input & type=filter') == parse_query('input && type=filter')

    def test_or(self):
        assert parse_query('div || span') == OrNode((TagNode(Op.EQ, 'div'), TagNode(Op.EQ, 'span')))
        assert parse_query('div | span') == parse_query('div || span')

    def test_not(self):
        assert parse_query('!id=ad') == NotNode(AttrNode('id', Op.EQ, 'ad'))

    def test_not_nested(self):
        assert parse_query('!!a') == NotNode(NotNode(TagNode(Op.EQ, 'a')))

    def test_paren(self):
        inner = OrNode((TagNode(Op.EQ, 'div'), TagNode(Op.EQ, 'span')))
        assert parse_query('(div || span) && .btn') == AndNode((inner, AttrNode('class', Op.WORD, 'btn')))

    def test_precedence_and_over_or(self):
        expect = OrNode((AndNode((TagNode(Op.EQ, 'a'), TagNode(Op.EQ, 'b'))), TagNode(Op.EQ, 'c')))
        assert parse_query('a && b || c') == expect

    def test_order_free(self):
        a = parse_query('?type=filter && input')
        b = parse_query('input && type=filter')
        assert isinstance(a, AndNode) and isinstance(b, AndNode)
        assert set(a.children) == set(b.children)

    def test_whitespace(self):
        assert parse_query('  a && b ') == parse_query('a&&b')
        assert parse_query('? a && b') == parse_query('a && b')

    def test_operand_ws(self):
        assert parse_query('a && (b || c)') == parse_query('a&&(b||c)')


class TestValue:
    def test_quote_double(self):
        assert parse_query(r'text="a\"b"') == TextNode('text', Op.EQ, 'a"b')

    def test_quote_single(self):
        assert parse_query(r"text='it\'s'") == TextNode('text', Op.EQ, "it's")

    def test_escape_newline(self):
        assert parse_query(r'text="line\n1"') == TextNode('text', Op.EQ, 'line\n1')

    def test_quoted_space(self):
        assert parse_query('placeholder="搜索 项目名称"') == AttrNode('placeholder', Op.EQ, '搜索 项目名称')

    def test_quoted_regex_paren(self):
        assert parse_query('text/="(a|b)"') == TextNode('text', Op.REGEX, '(a|b)')

    def test_bare_stops_at_quote_raises(self):
        with pytest.raises(QueryError, match='无法解析的剩余内容'):
            parse_query('href*=a"b')
