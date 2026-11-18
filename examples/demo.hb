#! hb --headless --timeout 5
# ============================================================
# demo.hb — .hb 脚本语言完整示例（测试 / 巡检风格）
#
# 运行：
#   hb run examples/demo.hb          遇错即停
#   hb run examples/demo.hb -c       失败也继续，结尾汇总失败条数
#   hb run examples/demo.hb --echo   逐行回显到 stderr（看执行到哪了）
# ============================================================

loglevel debug                        # 日志门槛降到 debug（默认 info）

# ---------- 1. 变量 ----------
let site = https://example.com       # 含 :// 的裸词不可能是变量名，自动当字符串
let want_title = "Example Domain"    # 字符串必须加引号
let rounds = 2                        # 数字类型：rounds 是 2，不是 "2"

info 开始巡检 ${site}                  # 命令参数里取变量用 ${name}

# ---------- 2. 浏览器子命令：每行 = 一条 hb 子命令 ----------
open ${site}
find "p"                              # 找不到会打「未找到」（不中断，但引用为空）
hl @1                                 # 高亮确认找到的是它（@1 引用）
text @1                               # 读文本，结果走 stdout
nohl @1                               # 取消高亮
js "return document.title"

# ---------- 3. 断言：不成立 → 打 [ASSERT]、记一次失败、继续跑 ----------
# 表达式里：裸标识符 = 变量（未定义会报错）；字符串字面量加引号；数字直接写
assert want_title == "Example Domain" 标题不符合预期
assert "example" in site 站点地址异常                   # in / not in 是子串判断
assert rounds > 0 轮次应为正数                         # 类型敏感：3 ≠ "3"
# assert 1 == 2 取消注释试试：失败但不中断，最后退出码为 1

# ---------- 4. 分支：if / elif / else（冒号 + 缩进，同 Python） ----------
if rounds > 2:
    info 多轮巡检模式
elif rounds == 2:
    info 双轮巡检模式
else:
    warn 单轮巡检模式

if site == https://example.com: debug 冒号后同行内联也可以   # 含 :// 的裸词不可能是变量，自动当字符串

# ---------- 5. 循环 ----------
loop rounds:                          # 固定次数（次数也可以是变量），${i} 是 1 起的计数
    info 第 ${i} 轮复查
    find "p"
    assert i > 0

for sel in ["p", "svg"]:              # 遍历列表（Python 风格）
    info 检查元素 ${sel}
    find "${sel}"

for k in range(2):                    # range(N)：k 取 0..N-1
    debug range 轮次 ${k}

# ---------- 6. 嵌套块 ----------
loop 2:
    for sel in ["p", "svg"]:
        if i == 1:
            debug 第 ${i} 轮只看一眼 ${sel}
        else:
            info 第 ${i} 轮复查 ${sel}
            find "${sel}"

# ---------- 7. 日志分级 + error 记失败 ----------
let role = "guest"
if not role == "admin":
    warn 当前是访客身份（仅巡检只读内容）
# error 这行取消注释会记一次失败（退出码 1），但脚本继续跑完

sleep 0.5
echo 巡检结束，全部通过
