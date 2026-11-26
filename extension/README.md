# hb 录制器（happybrowser browser recorder）

Chrome 扩展：录下你在页面上的操作，生成能直接 `hb run` 的 [`.hb` 脚本](../README.md)。

- 点工具栏按钮（或 `Alt+Shift+R`）开始 / 停止录制，页面右上角有角标和步数，角标上的按钮也能停。
- 侧边栏里按时间顺序看每一步：做了什么、用什么定位、为什么这么定位；可以删除、换定位方案、改输入值、加说明 / 等待。
- 「抓取内容」（`Alt+Shift+C`）点选页面元素，把它的文本 / 属性 / HTML 抓进脚本，支持"全部同类"批量抓取。
- 一键复制或保存 `.hb`，回放结果与录制时的操作一一对应。
- 停止后再点开始是**续录**（保留已有步骤）；要重新开始先点「清空」。

```
┌─ hb 录制器 ──────────────────────────────┐
│ ● 录制中 · 7 步 · example.com   [停止录制] [⎙ 抓取内容] │
│ [步骤 7] [脚本]        ☑注释 ☑先 find ☑隐藏密码        │
│ 1  点击 button#btn-search「搜索」    #btn-search      │
│ 2  输入 input#kw = "关键词"          #kw              │
│ 3  在 input#kw 上按 Enter            #kw              │
│ 4  点击 button「删除」   @data-id="42" >> button && .del │
│ …                                                       │
│ [＋说明] [＋等待] [清空]         [复制脚本] [保存 .hb]  │
└─────────────────────────────────────────┘
```

## 安装

1. `chrome://extensions` → 打开右上角「开发者模式」。
2. 「加载已解压的扩展程序」→ 选本目录（`extension/`，含 `manifest.json` 的那层）。
3. 固定工具栏图标；点一下图标 = 开始 / 停止录制并打开侧边栏。快捷键可在 `chrome://extensions/shortcuts` 改。

要求 Chrome / Edge ≥ 116。要上传 Web Store 时先打包：`python tools/pack.py`（输出到仓库根 `dist/`）。

权限说明：`<all_urls>` + 内容脚本是为了在任何页面录制、翻页后继续录；`webNavigation` 用来区分"地址栏输入的跳转"（写成 `open`）和"点击引起的跳转"（只记注释）；`storage` 保存录制会话与选项；`downloads` 用于保存 `.hb`。所有数据都留在本机。

## 生成的脚本

每一步默认写成「`find` 定位 → 用 `@1` 操作」两行，说明挂在行尾注释上；同一个元素上的连续操作（输入 → 回车）共用一个 `find`，点击 / 选择 / 回车 / 跳转之后引用一律作废重找。

```hb
# ──────────────────────────────────────────────
# hb 录制脚本 · 示例站
# 起始地址：https://example.com/
# 录制时间：2025-11-26 14:30　步骤：9（点击 4 · 输入 2 · 按键 1 · 抓取 2）
# 运行：hb run 这个文件.hb　（--echo 逐行回显，-c 失败也继续，--json 每行输出一条 JSON）
# ──────────────────────────────────────────────
open "https://example.com/"
find "#kw"  # 1. 输入 input#kw「搜索」 = "关键词"
input @1 "关键词"
press @1 "Enter"  # 2. 在 input#kw「搜索」 上按 Enter
find "@data-id=42"  # 3. 点击 button「删除」
click "@1 >> button && .del"
find "#pane"  # 4. 点击 iframe 里的 button#ok「确定」
find "@1 >> #ok"
click @1
find "tr && .row" --all  # 5. 抓取全部同类（12 项）：td「标题」
loop 12:
    text "@${i} >> td && .title"  # td「标题」
```

选项（侧边栏右上角）：

- **注释**：行尾的步骤说明。
- **先 find**：关掉则把定位符直接内联进命令（`click "#kw"`），仅对页面内唯一的方案有效。
- **隐藏密码**：密码框的内容不离开页面，脚本里留 `********` 和一行提示。

## 定位符是怎么选的

目标是自己就能唯一认出的元素时，直接生成 `find "<定位符>"`；按可靠程度依次试，先简单的、简单的不唯一才加条件：

| 优先 | 条件 | 例 |
|---|---|---|
| 1 | `#id`（不是自动编号的）、`@data-testid` 等测试钩子 | `#kw`、`@data-testid="submit"` |
| 2 | `@name`、`@for`（label → 控件）、其它 `data-*` | `input && @name="q"`、`@data-id="42"` |
| 3 | 稳定的 class（可叠加，最多 3 个） | `button && .btn-primary` |
| 4 | `@aria-label`、`@placeholder`、直属文字 `owntext*=`、深层文字 `text*=` | `label && owntext*="同意"` |
| 5 | `@href`、`@title`、`@alt`、`@value`、`@type`、`@role` | `a && @href="/items/42"` |
| 6 | 只剩标签名 | `nav` |

**不用的**：自动生成的 class（`css-1q2w3e`、`sc-bdVaJa`、`MuiButton-root-sc-…`、`_3xKFG`）、状态类（`active`、`selected`、`disabled`、`ng-dirty`…）、自动编号的 id（`ui-id-5`、`ember342`、`radix-:rk:-trigger`）、`style`、会变的 `data-*`（`data-index`、`data-state`…）。纯数字 / 价格 / 日期这类会变的文字评分很低，只在没得选时用。

**父级锚点**——你点的元素自己认不出来时（一排一样的"删除"），从最近的祖先往上找一个认得出的当锚点，再用 `@1 >>`（后代）或 `@1 >`（直接子元素）落到目标：

```
find "@data-id=42"          ← 卡片本身有身份
click "@1 >> button && .del"
find "select && @name=city" ← 下拉框有 name，里面的选项没有
select "@1 >> option && owntext*=上海"
find "tr && .row" --index 3 ← 行也没有身份：第 3 行（同类太多时才退到序号）
click "@1 >> button && .edit"
```

锚点会跳过装了大半个页面的容器（`<main>`、`#app`），那种等于没缩小范围。每个方案在人话说明里写了依据（"页面内唯一" / "父级锚点 … 内的 …" / "第 n 个匹配（共 m 个）"），备选方案可以在侧边栏里一键切换。

唯一性是录制当时在页面里实际数出来的，并且和 hb 的查找语义对齐（`text` / `owntext` 用的是同一份 `hbText`，见 `lib/locator.js` 头部注释）。

## 抓取内容

1. 点「⎙ 抓取内容」（或 `Alt+Shift+C`），页面进入选取模式：移动鼠标高亮，**滚轮 / ← →** 调整层级，`Esc` 取消。
2. 点一下要抓的元素；如果是"同款"的一组（列表项、表格行里的按钮），同款元素会用虚线框一起标出来。
3. 侧边栏里选抓什么（文本 / 某个属性 / HTML）、只抓这一个还是全部同类，加入脚本。

单个元素 → `find` + `text @1`（或 `attr @1 "href"` / `html @1`）；全部同类 → `find … --all` + `loop N:`，循环体里用 `@${i}`（hb 的 `${}` 插值对引用同样生效）。同类有父级锚点时是 `text "@${i} >> td && .title"`。`N` 是录制当时的条数，写在注释里；hb 的 `find` 最多缓存 50 个引用，超出时脚本里会有提示。

回放时抓取结果打到 stdout（加 `--json` 每行一条 JSON），需要落盘就 `hb run flow.hb > out.txt`。

## 录什么、不录什么

| 会录 | 说明 |
|---|---|
| 左键点击 / 双击 | 点在图标、文字上会提升到可点击的父级（按钮、链接、`role=button`、`cursor:pointer` 的容器），并在说明里注明 |
| 文本输入 | 取字段最终值，不逐键记；失焦、换操作或停顿 0.8 秒后落成一步。同一字段接着输入会并成一步 |
| 按键 | Enter、Tab、Esc、方向键、`ctrl+s` 这类；文本框里的退格 / 方向键不记（已经在值里） |
| 下拉选择 | `select`，优先按有语义的 `value`，纯数字序号按选项文字 |
| 勾选框 / 单选 | 点 label 时记在看得见、点得着的那一个上（隐藏的 `opacity:0` 勾选框记 label） |
| 跳转 | 地址栏 / 书签打开 → `open "…"`；点击引起的跳转、刷新、前进后退只记一行注释，方便对照 |
| 新标签页 | 记一行注释（脚本默认在原标签页回放） |
| iframe 里的操作 | 生成 `find "<iframe>"` → `find "@1 >> …"` 链，同域 / 跨域都行；iframe 里输入框的落盘顺序也保证正确 |

| 不录 / 不支持 | 原因与办法 |
|---|---|
| 右键菜单、拖拽、悬停、滚动 | hb 脚本没有对应命令 |
| `<input type=file>`、日期 / 颜色 / 滑块控件 | 记一行说明，需手动处理（文件上传可用 Python API 的 `click.to_upload()`） |
| shadow DOM 里的元素 | hb 的页面级查找进不去，会明确标出"未能生成命令" |
| 多行输入 | `.hb` 一行放不下换行，压成空格并注明；多行请用 Python API |
| 页面级按键时焦点在别处 | `press "body" …` 回放前会先把焦点收回页面 |

## iframe

每个 frame 都有内容脚本。iframe 里的步骤带着它在顶层 `frames` 树里的下标路径交给 service worker，后者逐层去问父 frame"第 n 个子 frame 的 `<iframe>` 怎么定位"，拼成：

```hb
find "#pane"                        ← 顶层里的 iframe
find "@1 >> iframe && .inner"       ← 它里面的 iframe
click "@1 >> #ok"                   ← 目标
```

依赖 hb CLI 的这条规则：`@n` 指向 iframe 时，`>>` / `>` 进入那个 frame 的文档（同域、跨进程的跨站 iframe 都可以）。

## 开发与测试

```
extension/
  manifest.json          MV3；内容脚本 document_start + all_frames
  background.js          service worker：会话（storage.session）、frame 登记、iframe 链、跳转事件、角标
  content.js             内容脚本：挂 recorder-core、收发消息、角标 / 选取高亮（样式在 content.css）
  lib/locator.js         定位方案生成（纯函数；hbText 与 happybrowser 的 HB_TEXT_JS 逐字一致）
  lib/recorder-core.js   事件 → 步骤（纯逻辑，不碰 chrome.*）；appendStep 归并规则也在这里
  lib/hbgen.js           步骤 → .hb 文本
  sidepanel.*            侧边栏
  test/mock-chrome.js    测试用：在普通页面里模拟 chrome.*，把上面三个文件原样跑起来
  test/fixtures/*.html   测试页
  tools/pack.py          打 zip；tools/make_icons.py 生成图标
```

测试在仓库根跑：`uv run pytest tests/test_recorder.py`（需要本机有 Chromium 系浏览器）。

- 每个测试页元素都生成方案，所有 DSL 用 hb 的解析器校验，并经 hb CLI `find` / `attr` 回到同一个元素（主方案、备选方案、"全部同类"分组全部验证）。
- 用 hb 发真实鼠标 / 键盘事件录一遍 → 生成脚本 → 重新打开页面回放，页面日志逐行一致（含 iframe 链、父级锚点、label → 勾选框、密码隐藏）。
- 整套管线：`test/mock-chrome.js` 提供 `runtime` / `tabs` / `storage` / `webNavigation` 等模拟，`content.js`、`background.js`、`sidepanel.js` 原样运行，从侧边栏点"开始录制"到保存 `.hb` 再回放。

**已知限制（验证范围）**：开发机上只有正式版 Chrome，137 起它不再认 `--load-extension`，没法在测试里真把扩展加载进浏览器；manifest / 权限 / service worker 生命周期这一层请以实际加载为准，遇到问题看 `chrome://extensions` 的"错误"与 service worker 控制台。
