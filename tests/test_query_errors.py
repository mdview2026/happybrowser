# -*- coding:utf-8 -*-
"""报错与 Did-You-Mean 验收：offset 指针精确、原因与建议齐全（final-syntax.md §7.1）。"""
import pytest

from happybrowser.errors import LocatorError
from happybrowser._locate.query import parse_query, parse_loc, QueryError, Q


def test_error_hierarchy():
    assert issubclass(QueryError, LocatorError)
    assert issubclass(LocatorError, ValueError)


# (输入, 期望 offset, 报错含关键字)
ERRORS = [
    ('?tag:a', 1, "无效运算符 ':'"),
    ('?text:ABC', 1, '旧语法'),
    ('?@@a=1', 1, "不属于新语法"),
    ('?a!=b', 2, '不支持 != 运算符'),
    ('?div.btn', 1, 'CSS 式连续简写'),
    ('?a b', 3, '缺少 &&'),
    ('?data_id', 1, '不是合法标签名'),
    ('?登录', 1, 'text*=登录'),
    ('?', 1, '查询不能为空'),
    ('?. btn', 2, '不能有空格'),
    ('?@ disabled', 2, '不能有空格'),
    ('?text/=(a|b)', 7, '值不能以特殊字符'),
    ('?a&&', 4, "后缺少条件"),
    ('?()', 1, '空括号'),
    ('?(a', 3, '括号不匹配'),
    ('?href*=(', 7, '值不能以特殊字符'),
    ('?a="x', 3, '引号未闭合'),
    ('?type=', 6, '缺少匹配值'),
]


@pytest.mark.parametrize('src,offset,keyword', ERRORS)
def test_error_offset_and_message(src, offset, keyword):
    with pytest.raises(QueryError) as e:
        parse_query(src)
    msg = str(e.value)
    assert f'位置 {offset}' in msg, msg
    assert keyword in msg
    # 指针行：2 个前导空格 + offset 个空格后是 ^
    pointer = msg.splitlines()[2]
    assert pointer[2 + offset] == '^'


def test_pointer_exact_without_prefix():
    """无 ? 前缀时 offset 相对用户输入同样精确（比 ? 形态整体前移 1）。"""
    with pytest.raises(QueryError) as e1:
        parse_query('a!=b')
    assert '位置 1' in str(e1.value)
    with pytest.raises(QueryError) as e2:
        parse_query('div.btn')
    assert '位置 0' in str(e2.value)


def test_non_string_raises():
    with pytest.raises(LocatorError, match='必须是 str'):
        parse_query(123)


def test_parse_loc_type_error():
    with pytest.raises(LocatorError, match='查询字符串或 Q 对象'):
        parse_loc(('xpath', '//div'))
    with pytest.raises(LocatorError, match='查询字符串或 Q 对象'):
        parse_loc(None)


def test_parse_loc_q_passthrough():
    q = Q('a', type='filter')
    assert parse_loc(q) is q._ast


def test_parse_loc_str_both_forms():
    assert parse_loc('input && .btn') == parse_loc('?input && .btn')
