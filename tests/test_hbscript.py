# -*- coding: utf-8 -*-
"""`.hb` 脚本语言单元测试（无浏览器）：分词 / 注释 / 头行 / 变量 / 插值 / 报错。"""
import pytest

from happybrowser.hbscript import (And, Assert, Cmp, Command, For, If, ListLit, Lit,
                                   Loop, Not, Or, ScriptError, eval_expr, interpolate,
                                   parse_cond, parse_let, parse_script, tokenize, truthy)


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
    with pytest.raises(ScriptError, match='加引号'):
        parse_let(['kw', '=', '你', '好'])          # 多 token 字面量不再隐式拼接
    with pytest.raises(ScriptError, match='变量未定义'):
        parse_let(['kw', 'x'])                      # 裸标识符 = 变量，未定义报错
    assert parse_let(['kw', 'x'], None, {'x': 'v'}) == ('kw', 'v')
    assert parse_let(['kw']) == ('kw', '')
    with pytest.raises(ScriptError, match='变量名不合法'):
        parse_let(['1x', 'v'])
    with pytest.raises(ScriptError, match='需要变量名'):
        parse_let([])

    assert interpolate(['input', '@1', '${kw}!'], {'kw': '哈哈'}) == ['input', '@1', '哈哈!']
    assert interpolate(['js', 'return 1'], {}) == ['js', 'return 1']
    # 未定义变量原样保留：JS 模板串不会被误伤
    assert interpolate(['js', 'return `${x}`'], {'kw': 'v'}) == ['js', 'return `${x}`']


# ---------------------------------------------------------------- 块结构与表达式


def test_parse_if_elif_else():
    script = parse_script(
        'if ${n} == 1:\n'
        '    echo one\n'
        'elif ${n} == 2:\n'
        '    echo two\n'
        '    echo two again\n'
        'else:\n'
        '    echo other\n'
        'echo after\n')
    stmt = script.commands[0]
    assert isinstance(stmt, If) and len(stmt.branches) == 2
    assert [s.tokens for s in stmt.branches[0][1]] == [['echo', 'one']]
    assert [s.tokens for s in stmt.branches[1][1]] == [['echo', 'two'], ['echo', 'two', 'again']]
    assert [s.tokens for s in stmt.else_body] == [['echo', 'other']]
    assert isinstance(stmt.branches[0][0], Cmp) and stmt.branches[0][0].op == '=='
    assert isinstance(script.commands[1], Command)   # else 块结束后回到顶层


def test_parse_if_inline_and_nested():
    script = parse_script(
        'if ${ok}: echo 通过\n'
        'loop 2:\n'
        '    if ${i} == 1:\n'
        '        echo 第 ${i} 次\n'
        '    else: echo 其他\n')
    inline = script.commands[0]
    assert isinstance(inline, If) and inline.branches[0][1][0].tokens == ['echo', '通过']
    loop = script.commands[1]
    assert isinstance(loop, Loop) and loop.count == ['2']
    inner = loop.body[0]
    assert isinstance(inner, If) and inner.else_body[0].tokens == ['echo', '其他']


def test_parse_loop_for_range():
    script = parse_script(
        'loop ${n}:\n'
        '    echo x\n'
        'for x in ["苹果", "香蕉"]:\n'
        '    info ${x}\n'
        'for i in range(3):\n'
        '    echo ${i}\n')
    loop, for1, for2 = script.commands
    assert isinstance(loop, Loop) and loop.count == ['${n}']
    assert isinstance(for1, For) and for1.name == 'x'
    assert isinstance(for1.iterable, ListLit)
    assert [lit.text for lit in for1.iterable.items] == ['苹果', '香蕉']
    assert isinstance(for2, For) and isinstance(for2.iterable, Lit)
    assert for2.iterable.text == 'range(3)'


def test_eval_list_membership():
    assert eval_expr(parse_cond('"b" in ["a", "b"]')[0], {})
    assert eval_expr(parse_cond('3 in [1,2,3]')[0], {})          # 逗号贴字也行（裸元素）
    assert eval_expr(parse_cond('x in ["a", "b"]')[0], {'x': 'a'})
    assert eval_expr(parse_cond('"z" not in ["a", "b"]')[0], {})
    # 列表变量
    assert eval_expr(parse_cond('x in items')[0], {'x': 2, 'items': [1, 2]})
    # 空列表为假
    assert not truthy([])


def test_parse_assert():
    script = parse_script('assert ${n} == 3 数量应为 3\nassert ${ok}\n')
    a1, a2 = script.commands
    assert isinstance(a1, Assert) and a1.cond == '${n} == 3' and a1.msg == ['数量应为', '3']
    assert isinstance(a2, Assert) and a2.cond == '${ok}' and a2.msg == []


def test_parse_block_syntax_errors():
    with pytest.raises(ScriptError, match='冒号'):
        parse_script('if ${ok}\n    echo x\n')            # 缺冒号
    with pytest.raises(ScriptError, match='需要缩进的语句块'):
        parse_script('if ${ok}:\necho x\n')               # 冒号后没有缩进块
    with pytest.raises(ScriptError, match='没有匹配的 if'):
        parse_script('else:\n    echo x\n')               # else 悬空
    with pytest.raises(ScriptError, match='没有匹配的 if'):
        parse_script('elif ${x}:\n    echo x\n')          # elif 悬空
    with pytest.raises(ScriptError, match='意外的缩进'):
        parse_script('echo a\n    echo b\n')              # 平白无故的缩进
    with pytest.raises(ScriptError, match='冒号后不能接'):
        parse_script('if ${ok}: loop 3:\n')               # 内联块语句
    with pytest.raises(ScriptError, match='else 不接受条件'):
        parse_script('if ${a}:\n    echo x\nelse ${b}:\n    echo y\n')
    with pytest.raises(ScriptError, match='缺少条件表达式'):
        parse_script('if:\n    echo x\n')
    with pytest.raises(ScriptError, match='loop 需要一个次数'):
        parse_script('loop 1 2:\n    echo x\n')
    with pytest.raises(ScriptError, match='for 的写法'):
        parse_script('for x 苹果:\n    echo x\n')
    with pytest.raises(ScriptError, match='可遍历的值'):
        parse_script('for x in:\n    echo x\n')
    with pytest.raises(ScriptError, match='第 2 行'):
        parse_script('echo a\n  if ${x}:\n      echo b\n')  # 行号要准


def test_parse_expr_precedence():
    expr, rest = parse_cond('a or b and not c')
    assert rest == []
    assert isinstance(expr, Or) and isinstance(expr.right, And)
    assert isinstance(expr.right.right, Not)


def test_parse_expr_parens_and_not_in():
    expr, _ = parse_cond('(a or b) and c')
    assert isinstance(expr, And) and isinstance(expr.left, Or)
    expr, _ = parse_cond('x not in "abc"')
    assert isinstance(expr, Cmp) and expr.op == 'not in'
    expr, _ = parse_cond('x in "abc"')
    assert expr.op == 'in'
    with pytest.raises(ScriptError, match='括号没有闭合'):
        parse_cond('(a')
    with pytest.raises(ScriptError, match='不完整'):
        parse_cond('a ==')
    with pytest.raises(ScriptError, match='两边只能是字面值'):
        parse_cond('(a or b) == c')


def test_parse_expr_rest_for_message():
    # assert 风格：表达式尽可能长，剩余是消息
    expr, rest = parse_cond('n > 3 数量异常')
    assert isinstance(expr, Cmp) and [t for t, _ in rest] == ['数量异常']


def test_eval_expr_semantics():
    assert eval_expr(parse_cond('3 == 3')[0], {})
    assert not eval_expr(parse_cond('3 != 3')[0], {})
    assert eval_expr(parse_cond('10 > 2')[0], {})          # 数字比大小
    assert not eval_expr(parse_cond('10 < 2')[0], {})
    assert eval_expr(parse_cond('"b" in "abc"')[0], {})     # 子串（字符串要加引号）
    assert eval_expr(parse_cond('"z" not in "abc"')[0], {})
    assert eval_expr(parse_cond('"a" == "a" and 1 < 2')[0], {})
    assert not eval_expr(parse_cond('not "a" == "a"')[0], {})
    assert eval_expr(parse_cond('not ("a" == "b")')[0], {})


def test_eval_expr_types():
    """类型系统：数字 ≠ 字符串；比大小类型不匹配报错；in 只用于字符串。"""
    assert not eval_expr(parse_cond('3 == "3"')[0], {})     # 数字与字符串不相等
    assert eval_expr(parse_cond('3 != "3"')[0], {})
    assert eval_expr(parse_cond('"10" < "2"')[0], {})       # 两边字符串 → 字典序
    with pytest.raises(ScriptError, match='类型不匹配'):
        eval_expr(parse_cond('"3" > 2')[0], {})
    with pytest.raises(ScriptError, match='in 只能判断子串'):
        eval_expr(parse_cond('3 in "abc"')[0], {})
    with pytest.raises(ScriptError, match='变量未定义'):
        eval_expr(parse_cond('typo_var == 1')[0], {})       # 裸标识符未定义 → 报错
    # 引号包裹的同名串不解析变量
    assert not eval_expr(parse_cond('"site" == site')[0], {'site': 'https://a.com'})


def test_eval_expr_interpolation_and_truthiness():
    variables = {'n': 5, 'empty': '', 'zero': 0}
    assert eval_expr(parse_cond('${n} == 5')[0], variables)   # 整体 ${n} 保留数字类型
    assert eval_expr(parse_cond('${n} > 4')[0], variables)
    assert not eval_expr(parse_cond('${empty}')[0], variables)
    assert not eval_expr(parse_cond('${zero}')[0], variables)
    # 未定义变量原样保留：${x} 是非空串 → 真；与自身相等
    assert eval_expr(parse_cond('${x}')[0], {})
    assert eval_expr(parse_cond('${x} == ${x}')[0], {})


def test_eval_expr_bare_identifier_resolves_variable():
    variables = {'site': 'https://example.com', 'rounds': 3}
    # 裸标识符先当变量解析
    assert eval_expr(parse_cond('site == "https://example.com"')[0], variables)
    assert eval_expr(parse_cond('rounds > 0')[0], variables)
    assert eval_expr(parse_cond('rounds')[0], variables)          # 真值判断也用变量值
    # ${} 插值与裸标识符可混用
    assert eval_expr(parse_cond('${site} == site')[0], variables)


def test_let_value_types():
    assert parse_let(['n', '=', '3']) == ('n', 3)                  # 数字类型
    assert parse_let(['n', '=', '3.5']) == ('n', 3.5)
    assert parse_let(['s', '=', '3'], [False, False, True]) == ('s', '3')   # 引号 → 字符串
    assert parse_let(['x', 'rounds'], None, {'rounds': 3}) == ('x', 3)      # 已定义变量取值
    assert parse_let(['x', '${n}'], None, {'n': 3}) == ('x', 3)    # 整体 ${} 保留类型
    assert parse_let(['site', '=', 'https://example.com']) == ('site', 'https://example.com')
    # 列表字面量（token 已过分词：引号被剥离、quoted 标记保留）
    assert parse_let(['items', '=', '[苹果,', '香蕉]'],
                     [False, False, True, True]) == ('items', ['苹果', '香蕉'])
    assert parse_let(['items', '=', '[1,2,3]']) == ('items', [1, 2, 3])


def test_truthy():
    for falsey in ('', '0', 'false', 'FALSE', 'no', 'None', 'off', 0, 0.0):
        assert not truthy(falsey)
    for truthy_ in ('1', 'true', '是', ' ', 1, 3.14, -1):
        assert truthy(truthy_)


def test_tab_indent_expands_to_4():
    script = parse_script('loop 2:\n\techo x\n')
    assert isinstance(script.commands[0], Loop)
