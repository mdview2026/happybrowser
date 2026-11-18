# -*- coding: utf-8 -*-
"""`.hb` 解释器单测（无浏览器）：只用内建命令（let/echo/sleep/log/assert/if/loop/for），
ctx 用桩、ap 传 None（不会走到 argparse 分发）。"""
from happybrowser.cli import _exec_script
from happybrowser.hbscript import parse_script


class _Ctx:
    json = False


class _Args:
    def __init__(self, keep_going=False):
        self.echo = False
        self.keep_going = keep_going


def run(capsys, text, keep_going=False, path=None):
    """执行脚本源码 → (退出码, stdout 行列表, stderr 文本)；path 提供脚本路径（嵌套 run 基准）。"""
    script = parse_script(text)
    if path is not None:
        script.path = str(path)
    code = _exec_script(_Ctx(), None, script, _Args(keep_going))
    cap = capsys.readouterr()
    return code, cap.out.strip().splitlines(), cap.err


def test_loop_counter_and_restore(capsys):
    code, out, _ = run(capsys, 'let i = "外层"\n'
                               'loop 3:\n'
                               '    echo 第 ${i} 次\n'
                               'echo ${i}\n')
    assert code == 0
    assert out == ['第 1 次', '第 2 次', '第 3 次', '外层']


def test_loop_count_from_variable_and_zero(capsys):
    code, out, _ = run(capsys, 'let n = 2\n'
                               'loop ${n}:\n'
                               '    echo tick\n'
                               'loop 0:\n'
                               '    echo never\n')
    assert code == 0
    assert out == ['tick', 'tick']


def test_for_items_and_range(capsys):
    code, out, _ = run(capsys, 'for x in ["苹果", "香蕉"]:\n'
                               '    echo ${i}-${x}\n'
                               'for k in range(3):\n'
                               '    echo k=${k}\n'
                               'echo 之后 ${x}\n')
    assert code == 0
    assert out == ['1-苹果', '2-香蕉', 'k=0', 'k=1', 'k=2', '之后 ${x}']


def test_for_list_variable(capsys):
    """for 也可以遍历 let 定义的列表变量。"""
    code, out, err = run(capsys, 'let items = ["a", "b"]\n'
                                 'for x in items:\n'
                                 '    echo ${x}\n'
                                 'for y in "abc":\n'      # 字符串不是可遍历值
                                 '    echo ${y}\n')
    assert code == 1
    assert out == ['a', 'b']
    assert 'for 需要列表' in err


def test_for_i_in_range_keeps_range_value(capsys):
    """循环变量就叫 i 时，i 取 range 的值（0 起），不被计数器覆盖。"""
    code, out, _ = run(capsys, 'for i in range(3):\n'
                               '    echo i=${i}\n')
    assert code == 0
    assert out == ['i=0', 'i=1', 'i=2']


def test_nested_loops(capsys):
    code, out, _ = run(capsys, 'loop 2:\n'
                               '    loop 2:\n'
                               '        echo 内 ${i}\n'
                               '    echo 外 ${i}\n')
    assert code == 0
    assert out == ['内 1', '内 2', '外 1', '内 1', '内 2', '外 2']


def test_if_elif_else(capsys):
    code, out, _ = run(capsys, 'let n = 5\n'
                               'if ${n} == 1:\n'
                               '    echo one\n'
                               'elif ${n} == 2:\n'
                               '    echo two\n'
                               'elif ${n} > 3:\n'
                               '    echo big\n'
                               'else:\n'
                               '    echo other\n')
    assert code == 0
    assert out == ['big']


def test_if_inline_and_logic_ops(capsys):
    code, out, _ = run(capsys, 'let a = 1\n'
                               'let b = 2\n'
                               'if ${a} == 1 and ${b} == 2: echo 都对\n'
                               'if ${a} == 9 or not ${b} == 2: echo 不会出\n'
                               'if ${a} == 9:\n'
                               '    echo 也不会出\n')
    assert code == 0
    assert out == ['都对']


def test_log_levels_and_loglevel(capsys):
    code, _, err = run(capsys, 'debug 隐藏的\n'
                               'info 信息\n'
                               'warn 警告\n'
                               'loglevel debug\n'
                               'debug 现在能看见\n')
    assert code == 0
    assert '隐藏的' not in err
    assert '[INFO] 信息' in err and '[WARN] 警告' in err and '[DEBUG] 现在能看见' in err


def test_error_log_counts_failure_but_continues(capsys):
    code, out, err = run(capsys, 'error 出了点问题\n'
                                 'echo 继续跑\n')
    assert code == 1
    assert out == ['继续跑']
    assert '[ERROR] 出了点问题' in err and '1 条命令失败' in err


def test_assert_pass_and_fail(capsys):
    code, out, err = run(capsys, 'let n = 3\n'
                                 'assert ${n} == 3 数量应为 3\n'
                                 'assert ${n} > 10 数量太小\n'
                                 'assert ${n} < 10\n'
                                 'echo 跑完\n')
    assert code == 1
    assert out == ['跑完']
    assert err.count('[ASSERT]') == 1                       # 只有一条断言失败
    assert '断言失败：${n} > 10（数量太小）' in err
    assert '第 3 行' in err and '1 条命令失败' in err


def test_assert_inline_in_if(capsys):
    code, _, err = run(capsys, 'let ok = 0\n'
                               'if not ${ok}: assert ${ok} ok 应为真\n')
    assert code == 1
    assert 'ok 应为真' in err


def test_failing_line_stops_by_default(capsys):
    code, out, err = run(capsys, 'echo before\n'
                                 'sleep abc\n'
                                 'echo after\n')
    assert code == 1
    assert out == ['before']
    assert '第 2 行' in err


def test_failing_line_inside_block_respects_keep_going(capsys):
    code, out, err = run(capsys, 'loop 2:\n'
                                 '    sleep abc\n'
                                 '    echo 第 ${i} 轮\n'
                                 'echo done\n', keep_going=True)
    assert code == 1
    assert out == ['第 1 轮', '第 2 轮', 'done']
    assert '2 条命令失败' in err
    code, out, _ = run(capsys, 'loop 2:\n'
                               '    sleep abc\n'
                               '    echo 第 ${i} 次\n')
    assert code == 1
    assert out == []                                        # 默认遇错即停，第一轮就中止


def test_loop_count_error_with_lineno(capsys):
    code, _, err = run(capsys, 'loop ${未定义}:\n'
                               '    echo x\n')
    assert code == 1
    assert '第 1 行' in err and '不是整数' in err


def test_bare_variable_in_conditions_and_loop_heads(capsys):
    """裸标识符一律是变量引用；字面字符串必须加引号。"""
    code, out, _ = run(capsys, 'let rounds = 2\n'
                               'let role = "guest"\n'
                               'loop rounds:\n'                        # 裸变量做次数
                               '    echo 第 ${i} 轮\n'
                               'if role == "guest": echo 访客\n'        # 裸变量 == 字符串字面量
                               'if not role == "admin": echo 不是管理员\n'
                               'assert rounds > 1 轮次太少\n'           # 裸变量断言（数字类型）
                               'for x in ["苹果", "香蕉"]:\n'
                               '    echo ${x}\n')
    assert code == 0
    assert out == ['第 1 轮', '第 2 轮', '访客', '不是管理员', '苹果', '香蕉']


def test_bare_identifier_arg_is_variable(capsys):
    """命令参数里裸标识符同样是变量：未定义报错。"""
    code, _, err = run(capsys, 'sleep abc\n')   # abc 不是变量 → 变量未定义（不再是 sleep 的参数错）
    assert code == 1
    assert '变量未定义：abc' in err


def test_types_in_let_and_assert(capsys):
    """let 保留类型；数字与字符串不相等；整体 ${} 插值保留类型。"""
    code, _, err = run(capsys, 'let n = 3\n'
                               'assert n == 3\n'
                               'assert not n == "3" 数字不应等于字符串\n'
                               'let s = "3"\n'
                               'assert s == "3"\n'
                               'assert s != n 类型应区分\n'
                               'let m = ${n}\n'                         # 整体 ${} 拷贝类型
                               'assert m == 3\n'
                               'let t = 第 ${n} 次\n'                   # 多 token 不再隐式拼接
                               )
    assert code == 1


def test_undefined_variable_in_condition_is_error(capsys):
    """表达式里的裸标识符未定义 → 明确报错（抓拼写错误）。"""
    code, _, err = run(capsys, 'let n = 1\n'
                               'if typo == 1: echo 不会出\n')
    assert code == 1
    assert '变量未定义：typo' in err and '第 2 行' in err


# ----------------------------------------------------------------嵌套 run

def test_run_nested_basic(capsys, tmp_path):
    sub = tmp_path / 'sub.hb'
    sub.write_text('echo 收到 ${1} 和 ${2}\n', encoding='utf-8')
    code, out, _ = run(capsys, f'run "{sub}" "苹果" "香蕉"')
    assert code == 0 and out == ['收到 苹果 和 香蕉']


def test_run_args_typed_and_argv(capsys, tmp_path):
    sub = tmp_path / 'sub.hb'
    sub.write_text('''
assert argc == 2 参数个数
assert ${1} == 3 数字参数保留类型
for a in argv:
    echo ${a}
''', encoding='utf-8')
    code, out, err = run(capsys, f'run "{sub}" 3 "文本"')
    assert code == 0, err
    assert out == ['3', '文本']


def test_run_variables_isolated(capsys, tmp_path):
    sub = tmp_path / 'sub.hb'
    sub.write_text('let x = "子变量"\necho ${x}\n', encoding='utf-8')
    code, out, err = run(capsys, f'''
let x = "父变量"
run "{sub}"
echo ${{x}}
''')
    assert code == 0, err
    assert out == ['子变量', '父变量']   # 子脚本改动不泄漏回父脚本


def test_run_parent_vars_not_visible(capsys, tmp_path):
    sub = tmp_path / 'sub.hb'
    sub.write_text('echo ${only_parent}\n', encoding='utf-8')
    code, out, _ = run(capsys, f'let only_parent = "父变量"\nrun "{sub}"')
    # 子脚本看不到父变量，${} 未定义原样保留
    assert code == 0 and out == ['${only_parent}']


def test_run_relative_to_calling_script(capsys, tmp_path):
    (tmp_path / 'lib').mkdir()
    (tmp_path / 'lib' / 'util.hb').write_text('echo 来自 lib\n', encoding='utf-8')
    main = tmp_path / 'main.hb'
    code, out, err = run(capsys, 'run lib/util.hb', path=main)
    assert code == 0, err
    assert out == ['来自 lib']


def test_run_nested_depth(capsys, tmp_path):
    sub = tmp_path / 'self.hb'
    sub.write_text('run self.hb\n', encoding='utf-8')
    code, _, err = run(capsys, 'run self.hb', path=tmp_path / 'self.hb')
    assert code == 1
    assert '嵌套超过' in err


def test_run_missing_file(capsys):
    code, _, err = run(capsys, 'run nowhere/ghost.hb')
    assert code == 1
    assert '读取脚本失败' in err


def test_run_error_reports_script_name(capsys, tmp_path):
    sub = tmp_path / 'bad.hb'
    sub.write_text('let x = nope\n', encoding='utf-8')   # nope 未定义 → 报错
    code, _, err = run(capsys, f'run "{sub}"')
    assert code == 1
    assert 'bad.hb' in err and '第 1 行' in err and '变量未定义' in err


def test_run_abort_stops_parent(capsys, tmp_path):
    sub = tmp_path / 'bad.hb'
    sub.write_text('let x = nope\n', encoding='utf-8')
    code, out, err = run(capsys, f'run "{sub}"\necho 不该执行\n')
    assert code == 1 and out == []   # 子脚本出错，默认整个脚本即停
    assert 'bad.hb' in err


def test_run_keep_going_collects(capsys, tmp_path):
    sub = tmp_path / 'bad.hb'
    sub.write_text('assert 1 == 2 错了\necho 子脚本继续\n', encoding='utf-8')
    code, out, err = run(capsys, f'run "{sub}"\necho 父脚本继续\n', keep_going=True)
    assert code == 1   # 有失败
    assert out == ['子脚本继续', '父脚本继续']   # -c 下子脚本内外都继续跑
    assert '[ASSERT] bad.hb 第 1 行' in err
    assert '1 条命令失败' in err
