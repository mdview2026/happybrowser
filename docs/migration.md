# DrissionPage → happybrowser 迁移指南

基线：DrissionPage 5.0.0b1 fork（含「现代定位层 v1.5」）。happybrowser 把它精简移植为独立库，
运行依赖只剩 `websocket-client` + `requests`（DrissionRecord / tldextract / ftfy /
lxml 已全部内化或移除）。本文按「什么不用改 / 怎么改 / 什么没有了」三段展开，
每一条都对应 `tests/test_ecosystem_smoke.py` 里的可执行佐证。

## 一、不用改的部分

| 域 | 说明 |
|---|---|
| Q Builder | API 同形：`Q.tag/t:ext/owntext/id/cls/attr(name)`、`.eq/.contains/.startswith/.endswith/.regex/.word/.exists`、`&`/`\|`/`~`、`canonical()`、`xpath(relative=True)` |
| 动作 | click（随机探测/定点/多击/at）、input（CJK）、hover、drag、actions 链、scroll、Keys 常量 |
| 监听 | `tab.listen.start/wait/steps/wait_silent`；包在 loadingFinished 后入队，`packet.response.body` 可直接读（json 自动 loads 成 dict） |
| 下载/上传 | `click.to_download()/to_upload()`、mission.wait、重名 rename/overwrite/skip |
| wait 族 | `ele.wait.displayed/clickable/deleted/...`、`tab.wait.doc_loaded/download_begin/...`（返回元素本体的按 truthiness 用） |
| 相对定位 | parent/prev/next/child/prevs/nexts，**负 index 保留**（生态在用） |
| NoneElement | 链式取值、`isinstance(x, NoneElement)` 判定、NoneElement_value 语义同 fork |
| cookies | `tab.cookies()`/`browser.cookies()`、`set.cookies.set/set/remove/...` |
| Frame | iframe/frame/object 查到后自动包装为 Frame 对象，同域/异域统一 API |

## 二、定位语法映射（重点）

`ele()` / `eles()` 默认按 DSL 解析，`?` 前缀可省略；也接受 Q 对象。
旧语法链（fork 4.x 风格）一律抛 `LocatorError`，报错信息带迁移提示。

| 旧写法 | 新写法 | 备注 |
|---|---|---|
| `ele('?tag:input')` / `'t:input'` | `ele('input')` | 裸词即标签 |
| `ele('text:确定')` / `'tx:确定'` | `ele('text*=确定')` | 精确用 `text=确定`；直属文本 `owntext*=确定` |
| `ele('css:.btn')` / `'c:.btn'` | `ele('.btn')` | `#id` / `.class` 分词语法保留 |
| `ele('xpath://div[@id="main"]')` / `'x:...'` | `ele('div#main')` 或 `ele('div && id=main')` | **无裸 XPath 直通**；结构轴用 `parent()/child()`（库层）；CLI 里用 `@n >> 子定位符` 在引用作用域内继续找 |
| `ele('@@id=user@@class=row')` | `ele('id=user && .row')` | 多条件 `&&` / `\|\|` / `!` 组合 |
| `ele('@id=user')` | `ele('@id=user')` ✅ | 单 `@` 条件恰是新 DSL 合法写法，**无缝兼容**（属性存在性 `@data-testid`） |
| `ele('tag:input@@class=row')` | `ele('input && .row')` | |
| `ele('f://div')`、`@@text()` 等 | DSL 重写 | 整个旧语法解析器已删除 |

调试：`explain_loc('a && .btn')` / `q.explain()` 打印标准式 / AST / 执行策略 / 下推 XPath；
`q.canonical()` 输出标准式字符串。

**方向轴增强**：`parent()/next()/prev()/child()` 现在直接接受 Q 对象和 DSL 字符串
（fork 会拒绝 Q 对象，`drission_utils._kwargs_to_parent_loc` 的 `Q.xpath(relative=True)`
绕道可以整段删除）。

## 三、对象与入口映射

| DrissionPage (fork) | happybrowser | 备注 |
|---|---|---|
| `Chromium` | `Browser` | 行为三原则：绝不自杀重启、接管前真实探测、无头冲突显式报错——**`_ua_set` hack 不再需要** |
| `ChromiumOptions` | `Options` | 属性式：`opts.auto_port = True`、`opts.headless()`、`opts.set_argument(...)` |
| `ChromiumTab` | `Tab` | 同名 API；`tab.tab` 返回自身 |
| `ChromiumPage` | 无 | 由 `Tab` 承担（fork 5.x 已删） |
| `ChromiumFrame` | `Frame` | 查到 iframe 自动包装，不再是 `ChromiumTab` 子类伪装 |
| `ChromiumElement` / `ChromiumShadowRoot` | `Element` / `ShadowRoot` | |
| `WebPage` / `SessionPage` / `SessionElement` / `s_ele()` | 无 | session 层整体未移植；静态解析需求用 lxml 自行处理 |
| `ChromiumElementsList` 过滤 DSL（`.filter(...)`、`.vals()`、`.wait`） | 无 | ElementsList 是极简 list 子类；过滤用推导式 `[e for e in eles if ...]`，可见性过滤用 `e.states.is_displayed` |
| `ele.s_ele()` | 无 | 直接在 Element 上走 `prevs()/nexts()/parent()/raw_text`（get_snapshot 模式见冒烟测试） |
| `ele(...)` / `tab(...)` 调用式 / `ele.sr` 别名 | 无 | 只保留 `ele()/eles()`；shadow root 用 `ele.shadow_root` |
| `find_ele` / `find_eles` | 无（库内） | 生态自定义封装照搬可用——Q API 同形，见下节 |
| `SelectElement.cancel_by_*` / `check_by_*` 镜像 | `by_*(cancel=True)` | 单方法 + 参数 |
| `tab.run_js_loaded` / `wait.ele_loaded` 等 | 收敛进 `run_js` / `wait.ele_displayed` 等 | |

## 四、生态代码迁移实例（drission_utils.py / ai_studio.py）

全部改写已在 `tests/test_ecosystem_smoke.py` 固化为 31 项测试，逐条对应：

| drission_utils 模式 | 迁移方式 |
|---|---|
| `_build_query`（Q.tag / Q.attr('class').contains OR 链 / kwargs 下划线转中划线） | **原样可用** |
| `find_button`（Q.text.eq + is_clickable + 向上找 button + CJK 逐字分渲染变体） | **原样可用**（含 `' '.join(btn_label)` 变体） |
| `paste`（click + `ele.tab.actions.type(Keys.CTRL_V)`） | **原样可用**（Element.tab 归属链已通） |
| `hl` / `nohl`（`set.style('border', ...)`） | 原样可用；注意读回是计算样式，`red` 归一化为 `rgb(255, 0, 0)`。CLI 另有 `hb hl` / `hb nohl`（outline，不占布局，可还原原内联样式） |
| `scrollable_parent`（run_js scrollHeight + parent 上溯） | 原样可用 |
| `set_value_by_label`（`'tag:input'`） | `'tag:input'` → `'input'` |
| `find_input`（`'t:input'`） | 同上 |
| `ele_visible` / `eles_visible` | ele 版原样；eles 版去掉 `.wait.displayed()`，改推导式 |
| `get_snapshot`（s_ele 快照） | 去 `s_ele()`，直接 Element 走 `prevs/nexts/parent/raw_text/xpath` |
| `_patched_parent` / `_kwargs_to_parent_loc`（Q→xpath 绕道） | 删绕道，`parent(Q.attr('id').eq('x'))` 直接可用 |
| `attach_options` 的 `co._ua_set = True` 保险 | 删除；接管语义三原则内建，无头比对重启不复存在 |
| ai_studio `tab.listen.start(targets)` + `for packet in tab.listen.steps(): packet.response.body` | **原样可用**；body 为 json 时自动 loads 成 dict |

## 五、已知差异与限制

- **浏览器级监听在 Chrome 157 失效**（`browser.listen.start(...)` 捕获为空）：
  browser 会话 `Fetch.enable` 后 `requestPaused` 不再到达（上游 fork 实测同样）。
  页面级 `tab.listen.*` 不受影响——生态只用页面级。
- `ftfy` 文本修复未移植：`response.body` 遇到非 JSON 文本按原文返回（不做编码猜测修复）。
- cookie 域名推导不再依赖 tldextract：按主机名标签逐级上试（`a.b.example.com` →
  `.b.example.com` → `.example.com`），公共后缀（github.io 等）会多试一级——结果一致，
  机制更保守。
- ini 配置文件 / `ChromiumOptions.set_local_port` 等旧入口：`Options` 属性式重写，
  详见 `docs/design.md`。
- Element 不做跨查询单例缓存：身份比较用 `==`（backend_id 判等），不要用 `is`。
