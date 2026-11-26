# -*- coding: utf-8 -*-
"""cli.py 纯函数单测：@n 引用链解析（不需要浏览器）。"""
import pytest

from happybrowser.cli import _CliError, _parse_keys, _parse_ref_chain
from happybrowser._core.keys import Keys


@pytest.mark.parametrize('target, expected', [
    ('@1', ('@1', [])),
    ('@12', ('@12', [])),
    ('@3 >> .price', ('@3', [('>>', '.price')])),
    ('@3>.price', ('@3', [('>', '.price')])),
    ('@2 >> .body > .price', ('@2', [('>>', '.body'), ('>', '.price')])),
    ('@2>>div.a>>span.b', ('@2', [('>>', 'div.a'), ('>>', 'span.b')])),
    ('@2 >> a && .lnk', ('@2', [('>>', 'a && .lnk')])),
    ('@3 parent', ('@3', [('parent', None)])),
    ('@3 parent(2)', ('@3', [('parent', 2)])),
    ('@3 next', ('@3', [('next', None)])),
    ('@3 prev(2)', ('@3', [('prev', 2)])),
    ('@3 next(.row)', ('@3', [('next', '.row')])),
    ('@3 next(text*="登 录")', ('@3', [('next', 'text*="登 录"')])),
    ('@3 parent next >> .x', ('@3', [('parent', None), ('next', None), ('>>', '.x')])),
    ('@3 next(.row) > span', ('@3', [('next', '.row'), ('>', 'span')])),
])
def test_parse_ref_chain(target, expected):
    assert _parse_ref_chain(target) == expected


@pytest.mark.parametrize('target', ['#kw', '.lnk', '@data-id', '@class=x'])
def test_not_a_ref_chain(target):
    assert _parse_ref_chain(target) is None


@pytest.mark.parametrize('target', [
    '@1 >>', '@1 > .x >>', '@1 >>> .x',          # 定位链缺定位符
    '@1x', '@1 cousin', '@1 parentx',            # 未知步骤/轴拼写错
    '@1 parent(', '@1 parent()', '@1 parent(2',  # 括号/参数
    '@1 parent(0)', '@1 next(-1)',
])
def test_bad_chain(target):
    with pytest.raises(_CliError):
        _parse_ref_chain(target)


@pytest.mark.parametrize('text, expected', [
    ('Enter', (Keys.ENTER,)), ('escape', (Keys.ESCAPE,)), ('ArrowDown', (Keys.DOWN,)),
    ('shift+Tab', (Keys.SHIFT, Keys.TAB)), ('Ctrl+a', (Keys.CTRL, 'a')), ('ctrl+shift+ArrowUp', (Keys.CTRL, Keys.SHIFT, Keys.UP)),
    ('F5', (Keys.F5,)), (' ', (' ',)), ('ctrl++', (Keys.CTRL, '+')),
])
def test_parse_keys(text, expected):
    assert _parse_keys(text) == expected


@pytest.mark.parametrize('text', ['ctrlx+a', 'win+a', 'shift++ctrl+a'])
def test_parse_keys_bad_modifier(text):
    with pytest.raises(_CliError):
        _parse_keys(text)
