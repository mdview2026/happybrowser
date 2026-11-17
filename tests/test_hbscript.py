# -*- coding: utf-8 -*-
"""`.hb` 脚本语言单元测试（无浏览器）：分词 / 注释 / 头行 / 变量 / 插值 / 报错。"""
import pytest

from happybrowser.hbscript import ScriptError, interpolate, parse_let, parse_script, tokenize


def test_tokenize_quotes_and_windows_paths():
    assert tokenize('input @1 "你好 世界"') == ['input', '@1', '你好 世界']
    assert tokenize(r'open C:\tmp\a.html') == ['open', r'C:\tmp\a.html']
    assert tokenize("attr #q 'data-x'") == ['attr', '#q', 'data-x']
    assert tokenize('input @1 "a""b"') == ['input', '@1', 'ab']  # 相邻引号拼接


def test_tokenize_comment_rules():
    assert tokenize('find #kw') == ['find', '#kw']            # 紧贴内容 → 定位符
    assert tokenize('find #kw   # 说明') == ['find', '#kw']    # 空格 # 空格 → 注释
    assert tokenize('open x  #') == ['open', 'x']             # 行尾孤立 #
    assert tokenize('#kw') == ['#kw']                          # 行首由 parse_script 处理
    assert tokenize('input @1 "#hash inside"') == ['input', '@1', '#hash inside']


def test_tokenize_unclosed_quote():
    with pytest.raises(ScriptError):
        tokenize('input @1 "abc')


def test_parse_script_header_and_commands():
    script = parse_script(
        '#! hb --port 9333 --json\n'
        '\n'
        '# 整行注释\n'
        'open https://a.example\n'
        'find "#q"\n')
    assert script.header == ['--port', '9333', '--json']
    assert [c.lineno for c in script.commands] == [4, 5]
    assert script.commands[1].tokens == ['find', '#q']
    assert script.commands[1].raw == 'find "#q"'


def test_parse_script_hb_prefix_tolerated():
    script = parse_script('hb find "#q"\n')
    assert script.commands[0].tokens == ['find', '#q']


def test_parse_script_header_rules():
    with pytest.raises(ScriptError, match='只能出现一次'):
        parse_script('#! --json\n#! --port 9333\n')
    with pytest.raises(ScriptError, match='必须写在所有命令之前'):
        parse_script('find "#q"\n#! --json\n')
    with pytest.raises(ScriptError, match='没有选项'):
        parse_script('#!\n')


def test_parse_script_errors_have_lineno():
    with pytest.raises(ScriptError, match='第 2 行'):
        parse_script('open x\ninput @1 "abc\n')
    with pytest.raises(ScriptError, match='只有 hb'):
        parse_script('hb\n')


def test_parse_script_bom_and_crlf():
    script = parse_script('\ufeff#! --json\r\nfind "#q"\r\n')
    assert script.header == ['--json']
    assert script.commands[0].tokens == ['find', '#q']


def test_let_and_interpolate():
    assert parse_let(['kw', '=', '你', '好']) == ('kw', '你 好')  # 多 token 拼回
    assert parse_let(['kw', 'x']) == ('kw', 'x')                 # = 可省
    assert parse_let(['kw']) == ('kw', '')
    with pytest.raises(ScriptError, match='变量名不合法'):
        parse_let(['1x', 'v'])
    with pytest.raises(ScriptError, match='需要变量名'):
        parse_let([])

    assert interpolate(['input', '@1', '${kw}!'], {'kw': '哈哈'}) == ['input', '@1', '哈哈!']
    assert interpolate(['js', 'return 1'], {}) == ['js', 'return 1']
    # 未定义变量原样保留：JS 模板串不会被误伤
    assert interpolate(['js', 'return `${x}`'], {'kw': 'v'}) == ['js', 'return `${x}`']
