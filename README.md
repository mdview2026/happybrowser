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
hb click "@1"                        & rem 按引用点击（也可 hb click "#kw"）
hb input "@1" "关键词"               & rem 清空后输入
hb text "#result"                    & rem 读文本；attr/html/js/shot/url/tabs 同理
hb find "a && .lnk" --json           & rem --json 放前放后都行，输出结构化结果（AI 友好）
hb hl                                & rem 高亮 find 缓存的全部 @n（看清到底匹配了哪些）
hb hl "@1" --color blue             & rem 高亮单个；也可直接 hb hl "#kw"，--all 取全部匹配
hb nohl                              & rem 取消本页所有 hb 高亮（给目标则只取消那几条）
hb explain "a && .btn"               & rem DSL 调试：AST / 执行策略 / 下推 XPath（无需浏览器）
hb                                   & rem 不带参数 → 打印帮助（退出码 0）
```

引用按 backendNodeId 复活，页面跳转后失效（会明确报错，重新 find 即可）。
高亮用 `outline`（不占布局、不挤动页面），元素原有的内联 outline 会先备份在页面侧，
`hb nohl` 原样还原；典型用法是 `hb find …` → `hb hl` → `hb shot` 截图确认。
`hb --help` 看全部子命令。

## 安装与开发

```bash
pip install happybrowser   # PyPI（Python >= 3.9；运行依赖仅 websocket-client + requests）
```

开发：

```bash
uv sync                # 安装开发环境
uv run pytest          # 399 项测试（含真实浏览器集成，自动端口 + headless）
uv run ruff check      # lint（fork 风格基线已配置忽略项）
```

状态：M1-M6 全部完成。迁移指南 `docs/migration.md`；设计 `docs/design.md`。

## License

本项目包含改造自 [DrissionPage](https://DrissionPage.cn)（© g1879）的源码，并遵循其许可：
**仅限个人学习与合法非盈利用途，商用需上游授权**。完整条款见 [LICENSE](LICENSE)。
