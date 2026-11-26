# happybrowser

精简浏览器自动化库：仅 Chromium（CDP 直连），仅 `?` / `Q` 查询语法。

## 定位语法（唯一语法）

`ele()` / `eles()` 默认按 `?` DSL 解析（前缀可省略），也可传 `Q` 构造器对象：

```python
from happybrowser import Browser, Q, explain_loc

tab = Browser().latest_tab
tab.ele('#kw')                                   # ? 可省略
tab.ele('input && type=filter && !id=ad')
tab.ele('a && href/="/item/\\d+\\.html$"')       # 正则 hybrid 匹配
tab.ele(Q('a', href__startswith='https://') & ~Q.id('ad'))

print(explain_loc('a && .btn'))                  # 调试：AST / 执行策略 / 下推 XPath
```

纯标签查询走 XPath 直下推，hybrid 查询用安全超集预筛 + 本地 AST 精筛；
页面级 `DOM.performSearch` 穿 shadow DOM，元素级以 `document.evaluate` 求值。

## 能力总览

| 域 | 内容 |
|---|---|
| 查找 | 页面/元素/ShadowRoot/Frame 四级查找，相对定位（parent/next/east…），负 index |
| 页面 | Tab/BrowserContext 管理、导航、加载状态机、alert、cookie/storage/缓存 |
| 交互 | 点击（随机探测/定点/多击/新 tab/下载/上传）、输入（CJK/组合键）、下拉、勾选、拖拽、动作链、滚动 |
| 监听 | `tab.listen.*`（wait/steps/wait_silent，HTTP/WebSocket/SSE 包）、`tab.console.*` |
| 采集 | 截图（页面全页/裁剪、元素）、保存 mhtml/pdf、`ele.src()`/`ele.save()`（含 blob/data:） |
| 下载 | `click.to_download()` / `wait.download_begin()` / `downloads_done()`，重名 rename/overwrite/skip |
| Frame | iframe/frame/object 自动包装为 Frame 对象，同域/异域（OOPIF）统一 API |
| 其他 | 录屏（五模式）、权限设置、headers/UA/窗口控制、init script、代理认证 |

```python
# 网络监听（ai_studio 模式）
tab.listen.start('MakerSuiteService/UpdatePrompt')
for packet in tab.listen.steps(timeout=30):
    print(packet.url, packet.response.body)

# 下载
mission = tab.ele('#dl').click.to_download(save_path='./out')
mission.wait(timeout=30)

# Frame（iframe/frame/object 查到即自动包装为 Frame 对象）
frame = tab.ele('iframe')
frame.ele('#inside').text
```

## 命令行 hb（人工 / AI 通用）

`hb.bat`（仓库根）或 PATH 上的 `hb` 命令——独立进程、状态落盘（`~/.happybrowser/state_{port}.json`），
默认接管 9222 上已运行的浏览器，绝不重启/清空它：

```bat
hb open https://example.com          & rem 打开页面（--new 新开标签页）
hb open www.baidu.com                & rem 缺协议自动补：裸域名 → https，localhost/IP → http
hb find "#kw"                        & rem DSL 定位，输出 @1/@2… 引用
hb find ".item" --all               & rem 返回全部匹配（默认只列前 10 条）
hb find ".item" --index 3           & rem 只取第 3 个匹配并编号 @1；--index -1 取最后一个
hb text "@2 >> .price"              & rem 在第 2 个匹配里继续找：>> 后代，> 直接子元素
hb attr "@2 parent" id              & rem 方向轴：parent / next / prev（可 parent(2)、next(.btn)）
hb click "@1"                        & rem 按引用点击（也可 hb click "#kw"）
hb input "@1" "关键词"               & rem 清空后输入
hb press "@1" Enter                  & rem 按键：Enter / Tab / Escape / ArrowDown / ctrl+a（发真实按键事件）
hb select "#city" "上海" --by value    & rem 下拉框选择：默认按选项文本，--by value / --by index
hb text "#result"                    & rem 读文本；attr/html/js/shot/url/tabs 同理
hb find "a && .lnk" --json           & rem --json 放前放后都行，输出结构化结果（AI 友好）
hb hl                                & rem 高亮 find 缓存的全部 @n（看清到底匹配了哪些）
hb hl "@1" --color blue             & rem 高亮单个；也可直接 hb hl "#kw"，--all 取全部匹配
hb nohl                              & rem 取消本页所有 hb 高亮（给目标则只取消那几条）
hb explain "a && .btn"               & rem DSL 调试：AST / 执行策略 / 下推 XPath（无需浏览器）
hb run flow.hb                       & rem 执行 .hb 脚本（单进程）；等价简写 hb flow.hb
hb -e "find #kw" -e "input @1 'haha'"   & rem 代码直接当参数（AI 常用，不落盘；字符串加引号）
hb --headless open https://a.com     & rem 无头模式：不占窗口
hbh open https://a.com               & rem 等价 hb --headless（仓库根 hbh.bat）
hb                                   & rem 不带参数 → 打印帮助（退出码 0）
```

引用按 backendNodeId 复活，页面跳转后失效（会明确报错，重新 find 即可）。
`@n` 后面可以接定位链继续缩小范围：`@2 >> .price`（后代查找）、`@2 > .price`（直接子元素），
支持多级（`@2 >> .body > .price`）；也可以先用方向轴移动：`@2 parent`（父元素）、
`@2 next` / `@2 prev`（兄弟元素），带参数表示层数/序号/定位符（`parent(2)`、`next(2)`、`next(.btn)`），
再继续接 `>>` / `>`；链式结果同样可直接作为 click/input/text/hl 的目标。
`@n` 指向 iframe 时，`>>` / `>` 会进入那个 frame 的文档里找（同域、跨域都行，引用复活时自动包装成
Frame 对象）；在 frame 里找到的引用，后续 text / input / press 照常可用。
高亮用 `outline`（不占布局、不挤动页面），元素原有的内联 outline 会先备份在页面侧，
`hb nohl` 原样还原；典型用法是 `hb find …` → `hb hl` → `hb shot` 截图确认。
`hb --help` 看全部子命令。

### 无头模式（不占活动窗口）

```bat
hb --headless open https://example.com   & rem 无头运行，不弹窗、不抢焦点
hb --headless -e "find #kw" -e "input @1 'haha'"

hbh open https://example.com             & rem 仓库根 hbh.bat = hb --headless
hbh -e "find #kw" -e "input @1 'haha'"
hbh run flow.hb
```

- 端口上还没有浏览器 → 直接在该端口启动无头实例。
- 端口上已有**有头**实例（或无法确认是无头）→ 自动在附近空闲端口启动无头实例，
  并把选择记进 state；之后带 `--headless` 的命令自动复用，原有实例完全不受影响
  （绝不重启/接管）。首次切换会打印一行提示，告诉实际用的是哪个端口。
- hb 启动的实例会把「无头/有头 + browserId」写进 `~/.happybrowser/state_{port}.json`，
  后续命令不必靠 UA 猜；外部启动且改写 UA 的无头实例可能识别不出，会自动另起一只。
- `--headless --existing` 只接管已有的无头实例，不会启动新的。
- 脚本头行同样支持：`#! hb --headless --port 9222`。

### `.hb` 脚本：一次进程跑完全部命令

单条命令是一个独立进程，连续调用时「起 Python + 连 CDP」的开销会累积。
把命令去掉 `hb` 按行写进 `.hb` 文件，`hb run`（或直接 `hb xxx.hb`）会在
**同一进程、同一次 CDP 连接**里顺序执行，快得多：

```hb
#! hb --port 9222 --timeout 5    ← 可选头行：连接参数（也可省略，用命令行参数）
# chat.hb：找输入框 → 高亮确认 → 输入 → 发送 → 读回显
open https://example.com/chat
find "#chat-textarea"
hl
let text = "你好，世界"
input @1 "${text}"
click "button && text*=发送"
text "#last-message"
```

脚本语言向 Python 靠拢（冒号 + 缩进块、类型化值），够写一般的巡检/测试脚本：

```hb
# test.hb：日志 + 断言 + 分支 + 循环
loglevel debug                  ← 日志门槛：debug/info/warn/error（默认 info）
open https://example.com/login
let user = "tester"
find "#username"
input @1 "${user}"              ← 命令参数里取变量用 ${name}
click "button && text*=登录"
sleep 1

info 登录流程已提交               ← 日志走 stderr；error 会记一次失败（退出码 1）
assert user == "tester"         ← 断言失败：打 [ASSERT]、记失败、继续跑完
                                ←   裸标识符 = 变量；字符串字面量必须加引号

loop 3:                         ← 固定次数循环，${i} 是 1 起的计数
    debug 第 ${i} 次轮询
for x in ["苹果", "香蕉"]:       ← 遍历列表（也可 for i in range(N):）
    info 处理 ${x}

if user == "tester":            ← 条件：and/or/not、==/!=/</>、in/not in、括号、列表
    info 用户符合预期
else:
    error 用户变量为空
if user: debug 单行内联也可以
```

```bat
hb run chat.hb          & rem 等价：hb chat.hb
hb run chat.hb -c       & rem 某行失败也继续，结尾汇总失败条数
hb run chat.hb --echo   & rem 逐行回显到 stderr（看执行到哪了）
type script.hb | hb run -   & rem 从 stdin 读脚本
```

完整可运行示例（变量/日志/断言/分支/循环全覆盖）：[`examples/demo.hb`](examples/demo.hb)。

AI / 程序调用不必落盘：代码可以直接作为参数传，`-e` 可重复（按顺序用换行拼接）：

```bat
hb -e "open https://example.com" -e "find #chat-textarea" -e "input @1 'haha'"
hb --json -e "open https://example.com" -e "find .lnk"   & rem 每行一条 JSON
```

用 subprocess / argv 数组调用时直接传真实换行即可（`hb -e "open …\nfind …"`）。
`-c` / `--echo` 对 `-e` 同样有效（`hb -c -e "…"`）；代码本身以 `-` 开头时
用 `--eval=CODE` 形式。

- 每行 = 一条子命令（等同在该行前面加 `hb `）；`"…"` / `'…'` 包住含空格的定位符。
- `#` 在行首是注释；行内注释写成「空格 `#` 空格」，所以 `find #kw` 仍是定位符。
- 变量：`let 变量 = 值` 定义；命令参数里用 `${变量}` 插值（未定义的 `${x}` 原样保留）。
  值有类型：数字 `3` / `3.14`、字符串（**必须**引号包裹 `"…"`）、列表 `["a", "b"]`、
  变量引用（裸标识符，未定义报错）；另有 `sleep 秒`、`echo 文本`。
- 裸词规则全语言统一：裸标识符一律是变量（`find a` → 取变量 a，未定义报错）；
  含 `: / . # @` 等字符的裸词不可能是变量名，自动当字符串（`find #kw`、`open https://…` 不受影响）；
  所以 `find "p"`、`attr @1 "id"`、`input @1 "你好"` 这类字面量参数要加引号。
- 日志：`debug/info/warn/error 消息`（走 stderr，`error` 记一次失败但不中断），
  `loglevel debug|info|warn|error` 调门槛（默认 info，debug 先藏起来）。
- 断言：`assert 条件 [消息…]`——不成立打 `[ASSERT]`、记一次失败、继续跑完（收集全部问题）。
- 分支/循环：`if 条件:` / `elif 条件:` / `else:`、`loop N:`、`for x in ["a", "b"]:`、
  `for i in range(N):`，一律冒号 + 缩进块（Tab 按 4 列算）；条件里运算符两侧留空格。
  循环内 `${i}` 是 1 起的计数（`for i in range(N)` 例外：`i` 是取值本身，0 起），出了块自动还原。
- 表达式（if/elif/assert/loop 头）：比较 `== != < <= > >=`、`in/not in`（子串或列表成员）、
  `and/or/not`、括号；比较类型敏感（`3 == "3"` 为假）。
- 例外：echo/日志/assert 的**消息部分**是自由文本，不用加引号，`${}` 插值照常。
- 嵌套执行：`run 子脚本.hb 参数…`——同进程执行另一个 `.hb` 脚本；参数按类型系统取值
  （`run lib/login.hb "张三" 3`），子脚本用 `${1}`/`${2}`…（位置参数）、`${argc}`（个数）、
  `${argv}`（列表，可 `for a in argv:`）取用；子脚本**变量隔离**（互不可见、互不影响），
  日志门槛与失败计数全局共享；相对路径相对**调用脚本所在目录**解析；嵌套上限 16 层防循环调用。
- 默认遇错即停并报「第 N 行」（退出码 1）；`@1/@2` 引用、当前标签页在脚本内自然共享。
- 命令行显式给的 `--port` / `--timeout` 优先于 `#!` 头行（脚本可复用时不用改文件）。
  更复杂的逻辑（函数、文本处理、数据结构）请直接用 Python 调 happybrowser 库。

## 浏览器扩展：录制器（`extension/`）

Chrome 扩展，录下你在页面上的操作，生成能直接 `hb run` 的 `.hb` 脚本：点工具栏按钮（或 `Alt+Shift+R`）
开始 / 停止，侧边栏里看步骤、换定位符、抓取内容，保存成 `.hb`。

- **定位符**：`#id` / `@data-testid` / `@name` 优先，其次稳定的 class、`placeholder` / `aria-label`、标签文字；
  自动生成的 class（`css-1q2w3e`、`sc-…`）、状态类（`active`、`disabled`）、自动编号的 id 不用。
- **父级锚点**：目标自己认不出来（一排相同的按钮）时，自动挑一个认得出的祖先当锚点：
  `find "@data-id=42"` → `click "@1 >> button && .del"`；实在没有特征的才退到 `find … --index n`。
  侧边栏里可以换成备选方案。
- **抓取内容**：`Alt+Shift+C` 点选元素，抓文本 / 属性 / HTML；「全部同类」生成 `find … --all` + `loop` 循环。
- **iframe**：iframe 里的操作生成 `find "<iframe>"` → `find "@1 >> …"` 链，同域 / 跨域都能回放。

```hb
# hb 录制脚本 · 示例站
open "https://example.com/"
find "#kw"  # 1. 输入 input#kw「搜索」 = "关键词"
input @1 "关键词"
press @1 "Enter"  # 2. 在 input#kw 上按 Enter
find "@data-id=42"  # 3. 点击 button「删除」
click "@1 >> button && .del"
find "tr && .row" --all  # 4. 抓取全部同类（12 项）：td「标题」
loop 12:
    text "@${i} >> td && .title"  # td「标题」
```

加载方式：`chrome://extensions` → 开发者模式 → 「加载已解压的扩展程序」→ 选 `extension/`。
详见 [`extension/README.md`](extension/README.md)；测试 `pytest tests/test_recorder.py`
（真实 Chromium：定位方案逐个用 hb 验证、录制后回放比对页面日志）。

## 安装与开发

```bash
pip install happybrowser   # PyPI（Python >= 3.9；运行依赖仅 websocket-client + requests）
```

开发：

```bash
uv sync                # 安装开发环境
uv run pytest          # 全套测试（含真实浏览器集成，自动端口 + headless）
uv run ruff check      # lint（fork 风格基线已配置忽略项）
```

状态：M1-M6 全部完成。迁移指南 `docs/migration.md`；设计 `docs/design.md`。
编辑器：`.hb` 脚本的 Sublime Text 语法高亮见 `editors/sublime/hb.sublime-syntax`
（复制到 `Packages/User` 目录即生效）。

## License

本项目包含改造自 [DrissionPage](https://DrissionPage.cn)（© g1879）的源码，并遵循其许可：
**仅限个人学习与合法非盈利用途，商用需上游授权**。完整条款见 [LICENSE](LICENSE)。
