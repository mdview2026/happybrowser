# happybrowser 设计方案

> 需求来源：`.requirements/001/req.md`
> 参考基线：DrissionPage 5.0.0b1 fork（commit「现代定位层 v1.5」，源码 ~15,100 行）
> 使用模式参考：上游生态的 drission_utils.py（1082 行）及 harry_browser / harry_mcp / chrome_cli 等消费方
> 语法规范：fork 的 `docs/final-syntax.md`（EBNF、下推规则、报错体系、验收矩阵，本文不重复）

---

## 1. 目标与非目标

### 目标
1. **独立库**：`happybrowser` 内嵌改造过的源码，`pip` 意义上零依赖 DrissionPage。
2. **唯一定位语法**：只保留 `?` DSL 与 `Q` Builder（两者编译到同一棵 AST）。`ele()/eles()` 的字符串参数**默认即 DSL，`?` 前缀可省略**（写了也兼容）。
3. **去除冗余**：删掉旧定位语法链、session 双模式、s_ele 全家、别名函数、双语文案、ini 配置、Selenium 兼容层等（详见 §7 砍除清单）。
4. **吸收实战教训**：接管/启动语义修复（杜绝 DrissionPage 接管时"配置不符就杀浏览器重启"的行为，即 drission_utils 里 `_ua_set` hack 对抗的问题）。

### 非目标
- 不做 requests 收发包（session 系整体移除）。
- 不做 `>>` 链式定位 / `-P` 父作用域——那是 harry_browser CLI 层的组合语法，库层用 `tab.ele('..').ele('..')` 链式调用天然覆盖。
- 不做旧语法兼容层、不做 DrissionPage 别名。破坏性更名一步到位，配迁移对照表。
- 首版不做 asyncio（保持线程模型，与移植成本匹配）。

---

## 2. 核心决策

| 决策项 | 结论 | 理由 |
|---|---|---|
| 定位语法 | 仅 `?`/`Q`，`?` 可省略 | 需求明确；DSL 表达力已覆盖旧语法（`.el-dialog, .msg` → `?.el-dialog \|\| .msg`） |
| 旧语法容错 | 不静默兼容，报 `LocatorError` + Did-You-Mean | fork 解析器已内置旧前缀识别（`tag:/text:/css:/xpath:/@@` → 改写建议），直接复用 |
| 执行引擎 | 沿用 fork 双通道：pure-xpath 直下推；hybrid = XPath 安全超集预筛 + `ast.match()` 本地精筛 | 已验证的设计（final-syntax.md §6），零重写成本 |
| 浏览器范围 | 仅 Chromium 系（Chrome/Edge/Chromium） | 使用模式 100% 是 Chromium |
| session 系 | 整体移除 | 与 chromium 系的 4 个纠缠点切口清晰（见 §7.3） |
| lxml 依赖 | 移除（含 s_ele、ShadowRoot 的 lxml 预筛通道，后者改造为 JS evaluate，见 §6.3） | 减依赖；ShadowRoot 方案改造是唯一风险点，已列 PoC 验证 |
| 配置 | 程序化 `Options`，去 ini/`eval()` | 顺带修 `eval()` 反序列化隐患；drission_utils 手拼 15 个无头参数的痛点由 `Options.headless()` 等糖吸收 |
| 负 index | **保留**（`eles[-1]` 语义、`ele(index=-1)`） | 用户生态在用（find_el(index=-1)）；DSL-only 后实现简化为全量取回再索引，无需旧版 60 行分页回溯 |
| 类命名 | `Browser/Tab/Frame/Element/ShadowRoot/NoneElement`，不留 `Chromium*` 旧名 | 新库新名；别名即冗余 |
| 报错语言 | 中文内联，删双语文案系统 | 用户为中文环境；省 423 行 texts.py + 全部 `_t()` 分支 |

---

## 3. 总体架构

```text
┌───────────────────────────── 用户 API ─────────────────────────────┐
│  Browser / BrowserContext / Tab / Frame / Element / ShadowRoot     │
│  Q / explain_loc / Options / Settings / Keys / errors              │
├────────────────────────────────────────────────────────────────────┤
│ _pages/   Tab·Frame 公共基类：导航、加载状态机、查找入口、截图、alert  │
│ _elements/ Element（三 id 互转、动作、相对/方向查找）、NoneElement    │
│ _units/   actions clicker scroller waiter setter rect listener     │
│           downloader console screencast states selector cookies    │
├────────────────────────────────────────────────────────────────────┤
│ _locate/  ? DSL 解析器 + Q Builder + AST + XPath 下推 + 本地求值     │
│           （自 fork query_locator.py，近乎原样）                    │
├────────────────────────────────────────────────────────────────────┤
│ _core/    driver（WebSocket CDP）· messenger（会话/事件分发）        │
│           browser（启动/接管/Tabs 注册表）· launcher（进程/路径探测） │
│           options · keys · tools（wait_until/raise_error/PortFinder）│
└────────────────────────────────────────────────────────────────────┘
```

---

## 4. 包结构与源码映射表

处置四档：**移植**（改名+去依赖，逻辑不动）、**精简**（删功能保主干）、**重写**（行为参考、代码新写）、**删除**。

| fork 源文件 | 行数 | → happybrowser | 处置 | 要点 |
|---|---|---|---|---|
| `_functions/query_locator.py` | 1059 | `_locate/query.py` | 精简 | 去 `_t()` 双语；`parse_query` 增加无 `?` 入口 `parse_loc()`；导出 AST 节点类（harry_browser 在用） |
| `_functions/locator.py` | 587 | — | 删除 | 仅 `is_query_loc/query_to_loc`（~30 行）归并进 `_locate/query.py`；旧语法链全删 |
| `_base/driver.py` | 263 | `_core/driver.py` | 移植 | 含 DebugDriver；依赖 websocket-client |
| `_base/base.py`（Messenger 等） | ~460 | `_core/messenger.py` | 精简 | 抽出 Messenger/事件循环/域引用计数；BasePage/BaseElement 里 session 骨架删 |
| `_browsers/chromium.py` | 764 | `_core/browser.py` | 精简 | Tabs 注册表、auto_port、context 保留；attach 语义修复（§8） |
| `_browsers/chromium_context.py` | 61 | `_core/browser.py` 并入 | 移植 | — |
| `_functions/browser.py` | 440 | `_core/launcher.py` | 精简 | 保留 connect/exe 路径探测/test_connect；删 set_prefs/set_flags/system_user_path（Edge 专属探测视情况） |
| `_configs/chromium_options.py` | 424 | `_core/options.py` | 重写 | 属性式 Options + headless()/incognito() 等糖；无 ini 无 save |
| `_configs/options_manage.py` + `configs.ini` | 142 | — | 删除 | ini 与 `eval()` 机制移除 |
| `_pages/chromium_base.py` | 1022 | `_pages/base.py` | 精简 | 删 s_ele/ax/any/find/`__call__`；查找四通道收敛为 query 单通道（§6） |
| `_pages/chromium_tab.py` | 230 | `_pages/tab.py` | 精简 | 去 SessionPage 混入、change_mode/post/cookies_to_* |
| `_pages/chromium_frame.py` | 489 | `_pages/frame.py` | 移植 | 同域/跨域双处理、跨域截图叠图方案保留 |
| `_elements/chromium_element.py` | 1490 | `_elements/element.py` | 精简 | 删 css 查找通道、旧 `_check_ele` 字典协议（方向定位改吃 AST）、s_ele；`sr` 别名删、留 `shadow_root` |
| `_elements/none_element.py` | 65 | `_elements/none_element.py` | 精简 | 保留链式 mock + `parsed=` 回显 |
| `_units/selector.py` | 162 | `_units/selector.py` | 精简 | 12 个 `cancel_by_*` 镜像 → `by_*(..., cancel=False)` |
| `_units/` 其余 11 模块 | ~3400 | `_units/` 同名 | 移植 | waiter/setter/rect/states/cookies_setter/perm_setter 原样；listener 首版原样（后续瘦身）；setter 内 SessionPageSetter 删 |
| `_functions/tools.py` | 221 | `_core/tools.py` | 精简 | wait_until/raise_error/PortFinder 必须；win32 窗口工具按需 |
| `_functions/keys.py` | 450 | `_core/keys.py` | 移植 | — |
| `_functions/web.py` | 372 | `_functions/web.py` | 精简 | 保留 get_ele_txt/tree/save(pdf/mhtml)/NavResult 等；get_ele_txt 改吃 Element 接口 |
| `_functions/cookies.py` | 243 | `_units/cookies.py` | 精简 | 删 set_session_cookies；tldextract 依赖删（suffixes.dat 自带） |
| `_functions/elements.py` | 592 | — | 删除 | ElementsList 收敛为极简 list 子类（§7.6）；get_eles/find 批量删 |
| `_functions/settings.py` | 74 | `settings.py` | 精简 | 留 cdp_timeout / raise_when_ele_not_found / raise_when_wait_failed / singleton_tab_obj；删语言机制 |
| `_functions/texts.py` | 423 | — | 删除 | 中文文案内联到各异常触发点 |
| `_functions/by.py`、`cli.py`、`common.py`、`items.py` | 567 | — | 删除 | Selenium By / CLI / 旧兼容层 / 旧类型再导出（由新 `__init__.py` 取代） |
| `_functions/suffixes.dat` | — | `_units/` 随 cookies | 保留 | cookie 域名判定数据 |
| session 三件套（session_page/session_element/session_options） | ~1350 | — | 删除 | 见 §7.3 |

预计净规模：**约 9,000~10,000 行**（含注释），较 fork 减少 1/3 以上。

---

## 5. 对外 API

### 5.1 顶层导出

```python
from happybrowser import (
    Browser, BrowserContext, Tab, Frame, Element, ShadowRoot, NoneElement,
    Q, explain_loc,            # 定位层
    Options, Settings, Keys,
    LocatorError, ElementNotFoundError, CDPError, ...,  # errors 全家
    DataPacket, Request, Response, WebSocketPacket, SSEPacket, ConsoleData,  # listener/console 数据类
    AndNode, OrNode, NotNode, TagNode, AttrNode, TextNode,  # AST 节点（harry_browser 等下游在用）
)
```

### 5.2 Browser —— 启动/接管

```python
browser = Browser()                     # 默认 127.0.0.1:9222：端口活着→纯接管；否则按 Options 启动
browser = Browser('127.0.0.1:9333')     # 指定地址，语义同上
browser = Browser(opts)                 # 显式 Options
browser = Browser.attach('127.0.0.1:9333')   # 只接管，端口不活即抛 BrowserConnectError
browser = Browser.launch(opts)               # 只启动，端口被占即抛

tab = browser.latest_tab
tabs = browser.tabs                     # list[Tab]
tab = browser.new_tab(url=None, new_context=False, background=False)
browser.activate_tab(tab); browser.close_tabs(tabs_or_ids)
ctx = browser.new_context(proxy=None)   # 无痕/隔离上下文
browser.is_headless(); browser.is_alive(); browser.version
browser.set.timeouts(base=5, page_load=15, script=30)
browser.quit(force=False)
```

### 5.3 Tab / Frame —— 页面

```python
tab.get(url, timeout=None, retry=None)   # 返回 NavResult
tab.refresh(); tab.stop_loading(); tab.back(); tab.forward()
tab.url / tab.title / tab.html / tab.tab_id / tab.user_agent
tab.wait.doc_loaded(timeout=None)        # waiter 挂件族
tab.set.headers(...); tab.set.load_mode.eager()
tab.cookies; tab.set.cookies(...)
tab.run_js(script, *args, as_expr=False)
tab.run_cdp(cmd, **cmd_args)
tab.get_screenshot(path=None, name=None, as_base64=None, full_page=False, scroll_to=True)
tab.save(path, type='pdf'|'mhtml')
tab.listen.start(targets, method=None, res_type=None); tab.listen.steps(count=None, timeout=None, gap=1)
tab.scroll.to_top(); tab.actions.move(x, y).click()
tab.console.start(); tab.console.steps()
```

### 5.4 Element —— 元素

```python
ele = tab.ele('#kw')                     # ← ? 可省略，下同
ele = tab.ele('input && type=filter && !id=ad')
ele = tab.ele('?input && .btn')          # 显式 ? 也接受
ele = tab.ele(Q('a', href__regex=r'/item/\d+'))
eles = tab.eles('.item')                 # ElementsList（list 子类）

ele.tag / ele.text / ele.raw_text / ele.html / ele.inner_html / ele.attrs / ele.attr(name)
ele.value; ele.src; ele.rect.midpoint / .viewport_location / ...
ele.states.is_displayed / .has_rect / .is_clickable / .is_in_viewport
ele.click(by_js=None, timeout=None)      # by_js=None 自动判定（吸收 drission_utils Clicker 补丁思路）
ele.input(value, clear=True); ele.clear(); ele.focus(); ele.hover()
ele.check(True); ele.select.by_text('A') # selector 挂件
ele.set.attr(k, v); ele.set.style(k, v); ele.set.value(v)
ele.run_js('return this.scrollHeight > this.clientHeight')
ele.attr(name); ele.save(path); ele.get_screenshot(...)

# 相对/方向查找（全部吃同一套 DSL/Q）
ele.parent('td');  ele.parent(2)         # 数字=层数
ele.next('li && .active'); ele.nexts(); ele.prev(); ele.prevs()
ele.before(); ele.afters(); ele.children('.opt'); ele.child(1)
ele.ele('.btn'); ele.eles('option')      # 后代查找
ele.east('input', index=1); ele.north(...)   # 坐标方向定位（DOM.getNodeForLocation）
ele.shadow_root                         # ShadowRoot，同样支持 .ele()/.eles()
```

### 5.5 NoneElement —— 未命中

```python
ele = tab.ele('buttton')                 # 拼错
# <NoneElement method=ele(), locator=buttton, parsed=tag="buttton", index=1, timeout=5>
ele.click()  # 链式安全，最终抛 ElementNotFoundError（Settings.raise_when_ele_not_found 控制提前抛）
```

---

## 6. 定位与查找（核心）

### 6.1 语法入口统一

```python
# _locate/query.py
def parse_loc(loc: str | Q) -> 节点:
    """Q 原样取 ast；str 剥离可选的 '?' 前缀后一律按 DSL 解析（lru_cache）"""
```

- `ele()/eles()/parent()/next()/east()/...` 所有收定位符的入口都走 `parse_loc`。
- 非法输入（旧语法、CSS 连写、裸中文等）→ `LocatorError`（继承 `ValueError`），带 offset `^` 指针 + Did-You-Mean 改写建议（fork 已实现，原样保留）。
- 原生 XPath/CSS **不设直通通道**。表达力对照：`css:.a, .b` → `?.a || .b`；`xpath://div/following-sibling::span` → `ele.next('span')`。确有极端需求时用 `run_js` + `document.evaluate`，或后续版本再议（开放问题 §13.4）。

### 6.2 查找执行通道（收敛为一条）

fork 有 query/ax/any/xpath/css 五通道，happybrowser 只留 **query**：

| 场景 | pure-xpath 查询 | hybrid 查询（含 `/=` 正则、非 QName 属性名） |
|---|---|---|
| Tab/Frame 页面级 | `DOM.performSearch(xpath, includeUserAgentShadowDOM=True)` → `getSearchResults` → `describeNode` | performSearch(预筛 XPath=安全超集) → 逐个 `ast.match(ele)` 精筛 |
| Element 后代级 | `Runtime.callFunctionOn` + `document.evaluate(xpath, this, null, 9\|7, null)` | 同左（全量快照预筛）+ 本地精筛 |
| ShadowRoot 内 | **改造**：`document.evaluate(xpath, this/*shadowRoot*/, ...)` 以 shadowRoot 为上下文（§6.3） | 同左 + 本地精筛 |

- 超时轮询：`wait_for_ele` + `wait_until`（10ms 间隔）原样保留。
- index 语义：正数取第 N 个（performSearch 增量取回）；负数取倒数第 N（全量取回后索引）；`eles()` 全量。
- 未命中 → `NoneElement` / 空 `ElementsList`（或按 Settings 抛错）。

### 6.3 ShadowRoot 查找去 lxml（唯一改造风险点）

fork 现状：`sr.ele()` 走「整树 lxml 快照预筛 → css 路径映射回 `DOM.querySelector`」（chromium_element.py:1047-1103），依赖 session_element 的 `make_session_ele`。
happybrowser 方案：以 `ShadowRoot` 对象为 context node 直接 `document.evaluate('.//*[…]', shadowRoot, null, 7, null)`（Chrome 支持 ShadowRoot 作为 evaluate 上下文；它是 DocumentFragment）。hybrid 查询再 `ast.match` 精筛。
**M3 里程碑设 PoC 验收**：对 open shadow（自定义元素）与 UA shadow 各建 fixture，验证命中集与 fork 行为一致。若 evaluate 对某类 shadow root 不生效，回退方案 = 保留该通道的快照映射实现并把 lxml 降级为可选依赖（不进核心依赖）。

> **M3 PoC 结论（2026-11-17）**：设计主方案被现实否决——Chrome 157 的 `document.evaluate`
> 不接受 ShadowRoot 作为上下文节点（NotSupportedError: not a valid context node type），
> 且 `DOM.performSearch` 的 includeUserAgentShadowDOM 只穿 UA shadow、不穿 open shadow。
> 实际采用回退的变体（未引入 lxml）：`querySelectorAll('*')` 全量候选 + `ast.match()` 精筛，
> shadow 树规模小，无需预筛。UA shadow 同样可用该方案命中（describeNode 的 shadowRoots
> 同时暴露 open 与 user-agent 两类）。

---

## 7. 砍除清单（逐项 + 理由 + 生态影响）

| # | 砍除项 | fork 证据 | 理由 / 迁移 |
|---|---|---|---|
| 1 | 旧字符串定位语法链：`@@/@|/@!`、`tag:/text:/tx:/x:/c:/t:` 前缀、`_preprocess` 缩写、`any` 裸文本兜底 | locator.py 全文；any 三连试 CB:1016 | DSL 全覆盖；Did-You-Mean 给改写建议；harry_browser 的 `text:设置` 类表达式改 `text*=设置` |
| 2 | `ax:` 无障碍树定位通道 | locator.py:136-162、CB:470-473 | 独立 CDP 通道、使用率低；后续需要可作扩展模块 |
| 3 | session 全家：SessionPage/SessionElement/SessionOptions、`ChromiumTab` 的 d/s 双模式/change_mode/post/cookies_to_*、BasePage 内嵌 `_session`、DrissionGet 下载 | chromium_tab.py:21 多继承等 4 纠缠点 | 本库只做浏览器；要 requests 场景由用户层自组 |
| 4 | `s_ele/s_eles` 4 份实现 | CB:448、CE:413、CE:822、session_page:131 | 双查询浪费；AI 快照场景由 `web.tree()`（DOM.describeNode 遍历）替代，drission_utils 的 get_snapshot 后续可基于它重建 |
| 5 | Selenium 兼容：By、translate_loc、from_selenium/from_playwright、common.py | by.py、locator.py:483、common.py | 明确不做 |
| 6 | ElementsList.filter/.filter_one/.vals 过滤 DSL（~400 行哨兵重复代码） | elements.py:15-297 | Python 推导式 + `q.match(ele)` 覆盖；返回极简 `ElementsList(list)` 保留挂载点 |
| 7 | `find()` 并发批量查找 / `wait.eles_loaded` | base.py:36、elements.py:302、waiter.py:110 | 与 `eles()`+推导式重叠 |
| 8 | `__call__`=`ele` 别名、`sr` 别名 | CB:103、CE:73、CE:144 | 每方法只留一个名字：`ele`、`shadow_root` |
| 9 | SelectElement 12 个 `cancel_by_*` 镜像 | selector.py:62-90 | `by_text(v, cancel=True)` 一个参数顶 12 个方法 |
| 10 | 双语文案系统 | texts.py 423 行 + `_t()` 散布 | 中文内联；英文用户后续需要再做 i18n（YAGNI） |
| 11 | ini 配置 + `eval()` 反序列化 + `dp_configs.ini` | options_manage.py:84,96 | Options 程序化（顺带修安全隐患）；需持久化时上层自己 json.dumps |
| 12 | `cli.py` 命令行 | 53 行 | 非库职责 |
| 13 | Launcher 的 set_prefs/set_flags/system_user_path（写 Preferences/Local State） | browser.py:126-193 | `--user-data-dir` 指向手工配置好的目录即可达同等效果；减少对浏览器内部文件格式的耦合 |
| 14 | Dead code：str_to_css_loc、translate_css_loc、注释掉的 find_by_css1 | locator.py:217,521；CE:1451 | 无调用 |
| 15 | performSearch 负 index 分页回溯（~60 行） | CB:931-991 | 全量取回后索引，逻辑等价更简单（负 index 功能本身保留） |

**明确保留**（防误砍）：方向定位 east/south/west/north+offset、parent/next(s)/prev(s)/before/after/children、listen/steps（Fetch 浏览器级抓包）、screencast、downloader、console、perm_setter、Keys/actions、full_page 截图、pdf/mhtml 保存、cookies 读写、add_init_js、run_cdp/run_js/run_async_js、NoneElement 链式+parsed 回显、explain_loc、Tab/Frame/Element 单例缓存、auto_port、无痕 context、DebugDriver。

---

## 8. 浏览器生命周期与配置（行为修复）

### 8.1 接管语义（吸收 drission_utils 三条教训）

1. **绝不自杀重启**：`Browser()` 发现端口存活 → 无条件纯接管。不比对 UA/参数/无痕状态，绝不 `quit(force=True)`（即消灭 `_ua_set=True` hack 的存在意义）。需要严格匹配请显式 `Browser.launch()`。
2. **真实状态探测**：`browser.is_headless()` 用 `Browser.getHistograms`（fork 已有）按事实返回，而不是按启动参数推断。
3. **无头冲突显式报错**：`launch(headless=True)` 但端口被有头实例占用 → `BrowserConnectError`（提示地址或无头状态二选一），不静默降级。

### 8.2 Options（重写为属性式）

```python
opts = Options()
opts.address = '127.0.0.1:9222'        # 或 opts.auto_port = True（临时端口+临时数据目录）
opts.browser_path = r'...\chrome.exe'  # 缺省走 launcher 路径探测（注册表+常见路径+PATH）
opts.user_data_path = ...              # 缺省 <tmp>/happybrowser/<port>
opts.download_path = ...
opts.timeouts = Timeout(base=10, page_load=30, script=30)
opts.load_mode = 'eager'               # 'eager' | 'none' | 'complete'
opts.proxy = 'http://user:pw@host:port'
opts.headless()      # 一键：--headless=new + 反检测 UA + 窗口尺寸等整套参数（糖）
opts.incognito(); opts.no_images(); opts.mute()
opts.set_argument('--lang=zh-CN'); opts.remove_argument('--lang')
opts.set_extension(path)
```

- 无 `save()/ini`；`repr` 可读、可 pickle/json（`to_dict()/from_dict()` 两个方法足够）。
- `Options.headless()` 的默认参数集 = drission_utils `_start_headless` 里那 15 个参数的沉淀。

---

## 9. 依赖与打包

```toml
[project]
name = "happybrowser"
requires-python = ">=3.9"
dependencies = [
    "websocket-client>=1.6",   # CDP WS
    "requests>=2.28",          # 仅 /json HTTP 端点探测
]

[project.optional-dependencies]
force-quit = ["psutil"]        # Browser.quit(force=True)
screencast = ["opencv-python"] # 录屏存 mp4 时才需要
dev = ["pytest", "hypothesis", "ruff"]
```

- **移除**的 fork 依赖：lxml、tldextract、DrissionRecord（3 处调用内联为 ~20 行 `get_usable_path/make_valid_name`）、DrissionGet、click。
- psutil/win32 仅为 force quit / 隐藏窗口服务，降级为可选。
- 仓库结构：`src/happybrowser/`（src 布局）+ `tests/`（从 fork tests/regression_cases 改写）+ `docs/`。

---

## 10. 测试与验收

1. **定位层单测（无浏览器）**：直接移植 final-syntax.md §9 的 6 类矩阵——AST 不变量（canonical→reparse 结构全同，Hypothesis）、双前端等价、混合下推 soundness（反例树零漏杀零误杀）、词法地狱（引号/emoji/中文/空串/`@click`）、报错指针精确性。DSL 新增用例：`?` 省略形态与带 `?` 形态解析全同。
2. **驱动层集成（需 Chrome）**：launch/attach/auto_port/quit(force)；接管语义三项（§8.1）；tab 生命周期（新建/激活/关闭/单例）；frame 同域跨域。
3. **查找回归**：fork `tests/regression_cases` 逐条改写为 DSL 表达后在 happybrowser 上跑，元素序列与 fork 一致；open shadow / UA shadow PoC 用例（§6.3）。
4. **生态冒烟**：drission_utils 的 `connect_chrome/find_el/find_els` 签名在 happybrowser 上重新实现一份（迁移示例，进 `examples/`），harry_browser 三条典型命令（`?input && #kw`、`-P 父作用域`、`axis east`）等价跑通。

---

## 11. 里程碑

| 阶段 | 内容 | 验收 |
|---|---|---|
| M1 | `_locate/query.py` + errors + settings：定位层独立成包 | §10.1 全绿（纯 Python，无浏览器） |
| M2 | `_core/`：driver/messenger/browser/launcher/options | 启动/接管/auto_port/导航/tab 管理 |
| M3 | `_pages/` + `_elements/` 查找闭环 + waiter | §10.3 回归一致；ShadowRoot PoC |
| M4 | 动作交互：actions/clicker/keys/scroller/selector | 点击/输入/下拉/滚动用例 |
| M5 | 周边单元：setter/rect/states/cookies/downloader/listener/console/screencast/截图/pdf | listen.steps 抓包、下载、截图 |
| M6 | 打包 + 迁移文档：pyproject、API 对照表、examples | §10.4 冒烟 |

每阶段结束更新 `.requirements/001/progress.md`。

---

## 12. 许可证声明

DrissionPage 源码许可为：**允许个人学习/合法非盈利使用与分发，商业使用需版权持有人（g1879）授权**。happybrowser 内嵌其改造源码，**继承同一限制**：

- 保留每个移植文件原头部版权声明，并附修改说明；
- `LICENSE` 与 README 明示派生关系与限制；
- 该库限于个人学习与研究用途；若未来商业化需另行取得上游授权或重写对应模块。

---

## 13. 开放问题（实现期确认）

1. **ShadowRoot evaluate 上下文**（§6.3）：PoC 不通过时的回退成本。
2. **listener.py（1089 行）**：首版整体移植，二期按"Network tab 级 / Fetch 浏览器级"拆分瘦身。
3. **Edge 路径探测**（launcher 里注册表两套）：保留 Chrome+Chromium 通用探测，Edge 分支是否保留待定（用户当前未见 Edge 用例）。
4. **原生 XPath 逃生舱**：是否提供 `tab.ele(X('//div[@x]'))` 这类显式构造器（默认不做，收到真实需求再加）。
