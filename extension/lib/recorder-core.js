/* recorder-core.js — 浏览器操作 → 录制步骤（extension/lib）
 *
 * 只管“页面上发生了什么、该怎么记”，不碰 chrome.* 消息和界面：
 * content.js 注入 isRecording()/emit() 等回调，tests/test_recorder.py 直接在页面里挂一份。
 *
 * 几条贯穿始终的原则：
 * - 定位符在事件的捕获阶段同步算好：页面自己的处理函数随后就可能改 DOM / 跳转。
 * - 点在图标、文字上时提升到可点击的父级（button / a / role=button / cursor:pointer …）。
 * - 文字输入不逐键记录：跟 input 事件取字段最终值，失焦 / 换操作 / 停顿后落成一行 input。
 *   只有非文字键（Enter、Tab、方向键、快捷键）才单独记成 press。
 * - label → 控件：浏览器会补发一次合成 click，只记一次，记在实际点得到的那一个上。
 */
(function (root, factory) {
  const api = factory(root.HbLocator);
  root.HbRecorder = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (L) {
  'use strict';

  const INTERACTIVE_TAGS = new Set('a,button,input,select,textarea,summary,option,label'.split(','));
  const INTERACTIVE_ROLES = new Set(('button,link,tab,menuitem,menuitemcheckbox,menuitemradio,option,checkbox,radio,' +
    'switch,textbox,combobox,slider,treeitem,listbox,searchbox,spinbutton').split(','));
  const INLINE_TAGS = new Set(('span,i,b,em,strong,small,u,s,sub,sup,code,kbd,mark,abbr,cite,q,time,var,samp,bdi,bdo,' +
    'font,big,tt,nobr,wbr,img,svg,path,circle,rect,line,polyline,polygon,use,ellipse').split(','));
  const TEXT_TYPES = new Set('text,search,email,url,tel,password,number'.split(','));
  // 文本框里这些键的效果已经体现在字段值里，不单独记
  const EDIT_KEYS = new Set('Backspace,Delete,ArrowLeft,ArrowRight,Home,End,Insert');
  const IDLE_FLUSH_MS = 800;        // 停顿这么久就把输入落成一步（之后继续打字会另起一步，回放仍是终值）
  const NAMED = {Enter: 'Enter', Esc: 'Escape', Escape: 'Escape', Tab: 'Tab', Backspace: 'Backspace', Delete: 'Delete',
    Del: 'Delete', Insert: 'Insert', ArrowUp: 'ArrowUp', ArrowDown: 'ArrowDown', ArrowLeft: 'ArrowLeft',
    ArrowRight: 'ArrowRight', PageUp: 'PageUp', PageDown: 'PageDown', Home: 'Home', End: 'End', ' ': 'Space'};
  for (let i = 1; i <= 12; i++) NAMED['F' + i] = 'F' + i;
  const MODIFIER_KEYS = new Set('Shift,Control,Alt,Meta,OS,AltGraph,Dead,Process,Unidentified'.split(','));

  function tagOf(el) { return el.localName ? el.localName.toLowerCase() : ''; }

  function isEditable(el) {
    const tag = tagOf(el);
    if (tag === 'textarea') return true;
    if (tag === 'input') return TEXT_TYPES.has((el.type || 'text').toLowerCase());
    return !!el.isContentEditable;
  }

  function valueOf(el) { return el.isContentEditable ? el.innerText : (el.value || ''); }

  function isInteractive(el) {
    return INTERACTIVE_TAGS.has(tagOf(el)) || INTERACTIVE_ROLES.has(el.getAttribute('role') || '');
  }

  // 看得出“这里能点”：tabindex、行内 onclick、cursor:pointer（可编辑区域本身也算）
  function hasClickAffordance(el, win) {
    if (el.hasAttribute('onclick') || el.isContentEditable) return true;
    const tabindex = el.getAttribute('tabindex');
    if (tabindex !== null && tabindex !== '-1') return true;
    if (!isEditable(el) && win.getComputedStyle(el).cursor === 'pointer') return true;
    return false;
  }

  // 点在图标/文字上 → 提升到最近的“可点击”祖先（最多爬 4 层，只穿过行内标签）
  function lift(el, doc, win) {
    let cur = el;
    for (let i = 0; i < 4; i++) {
      if (isInteractive(cur) || hasClickAffordance(cur, win)) return cur;
      const p = cur.parentElement;
      if (!p || p === doc.body) break;
      if (isInteractive(p) || hasClickAffordance(p, win)) return p;
      if (!INLINE_TAGS.has(tagOf(cur))) break;
      cur = p;
    }
    return cur;
  }

  function clickable(el, win) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const cs = win.getComputedStyle(el);
    return cs.visibility !== 'hidden' && cs.display !== 'none' && parseFloat(cs.opacity) > 0.01 &&
      cs.pointerEvents !== 'none';
  }

  // 'ctrl+shift+Tab' / 'Enter' / 'ArrowDown' / 'a'；纯修饰键、组合输入中的键 → null
  function keyName(e) {
    const k = e.key;
    if (!k || MODIFIER_KEYS.has(k)) return null;
    let mods = '';
    if (e.ctrlKey) mods += 'ctrl+';
    if (e.altKey) mods += 'alt+';
    if (e.metaKey) mods += 'meta+';
    let name;
    if (NAMED[k]) {
      name = NAMED[k];
      if (e.shiftKey && k.length > 1) mods += 'shift+';       // shift+Tab / shift+ArrowUp
    } else if (k.length === 1) {
      if (/[a-zA-Z]/.test(k)) {
        if (e.shiftKey && !(e.ctrlKey && e.altKey)) mods += 'shift+';   // AltGr 在 Windows 上是 ctrl+alt
        name = k.toLowerCase();
      } else {
        name = k;                                             // 数字/符号：Shift 已经体现在字符里
      }
    } else {
      return null;
    }
    if (e.ctrlKey && e.altKey && k.length === 1) mods = '';   // AltGr 打出的字符：不是组合键
    return mods + name;
  }

  function fromUi(e, env) {
    if (!env.isUi) return false;
    const p = e.composedPath ? e.composedPath() : [e.target];
    for (const n of p) if (n && n.nodeType === 1 && env.isUi(n)) return true;
    return false;
  }

  function targetOf(e) {
    const p = e.composedPath ? e.composedPath() : [];
    for (const n of p) if (n && n.nodeType === 1) return n;
    const t = e.target;
    return t && t.nodeType === 1 ? t : (t && t.parentElement) || null;
  }

  // ───────── iframe 链 ─────────
  // 本 frame 在顶层窗口 frames 树里的下标路径（顶层是 []）。跨域也允许：只读 length、比较 WindowProxy
  function framePath(win) {
    const path = [];
    try {
      let w = win;
      for (let guard = 0; w.parent && w.parent !== w && guard < 10; guard++) {
        const frames = w.parent.frames;
        let idx = -1;
        for (let i = 0; i < frames.length; i++) if (frames[i] === w) { idx = i; break; }
        if (idx < 0) break;
        path.unshift(idx);
        w = w.parent;
      }
    } catch (err) { /* 拿不到就当顶层 */ }
    return path;
  }

  function hostElement(win, doc, childIndex) {
    const child = win.frames[childIndex];
    if (!child) return null;
    const list = doc.querySelectorAll('iframe,frame,object');
    for (const el of list) if (el.contentWindow === child) return el;
    return null;
  }

  // 父 frame 回答“第 childIndex 个子 frame 的宿主元素怎么定位”
  function hostLocator(win, doc, childIndex) {
    const el = hostElement(win, doc, childIndex);
    if (!el) return {error: '找不到第 ' + (childIndex + 1) + ' 个子 frame 的 <iframe> 元素'};
    return {plan: L.best(el), src: el.getAttribute('src') || '', name: el.getAttribute('name') || '',
            title: el.getAttribute('title') || ''};
  }

  // ───────── 挂载 ─────────
  // env：{win, doc, isRecording(), isPicking(), emit(step), locate(el), now?, isUi(el)?,
  //       maskPasswords(), onPick(result), ui:{hover(el, siblings, info), unhover()}}
  function mount(env) {
    const doc = env.doc;
    const win = env.win;
    const now = env.now || (() => Date.now());
    let pending = null;            // 输入中的字段 {el, plan, desc, dirty}
    let idleTimer = 0;
    let lastLabel = null;          // {control, t}：label 代发的那次合成 click 要跳过
    let lastClick = null;          // {el, t}：双击的第二下并入前一步
    let pick = null;               // 选取模式：{el, siblings, timer, plan}

    const on = (type, fn, opts) => doc.addEventListener(type, fn, opts || true);
    const active = () => env.isRecording();

    function emit(step) {
      if (step.t === undefined) step.t = now();
      env.emit(step);
    }

    // ── 输入 ──
    function flushPending() {
      if (idleTimer) { clearTimeout(idleTimer); idleTimer = 0; }
      const p = pending;
      pending = null;
      if (!p || !p.dirty) return;
      let value = valueOf(p.el);
      if (!value.trim()) return;                       // 清空字段不记；需要就手写 input @1 ""
      const notes = [];
      if (/\n/.test(value)) {
        value = value.replace(/\n+/g, ' ');            // .hb 一行放不下换行
        notes.push('原输入含换行，已压成空格；多行文本请改用 Python API 的 input()');
      }
      const secret = (p.el.type || '') === 'password';
      if (secret && env.maskPasswords && env.maskPasswords()) {
        value = '';
        notes.push('密码未记录；回放前把 ******** 换成真实密码，建议 let pwd = "…" 后写 input @1 "${pwd}"');
      }
      // 用开始输入的时刻排序：iframe 里的失焦落盘可能比别处随后的点击晚到，但顺序应在它之前
      emit({kind: 'input', desc: p.desc, plan: p.plan, value: value, secret: secret, notes: notes, t: p.t0});
    }

    function onInput(e) {
      if (!active() || fromUi(e, env)) return;
      const el = e.target;
      if (!el || el.nodeType !== 1 || !isEditable(el)) return;
      if (pending && pending.el === el) {
        pending.dirty = true;
      } else {
        flushPending();
        pending = {el: el, plan: env.locate(el), desc: L.describe(el), dirty: true, t0: now()};
      }
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(flushPending, IDLE_FLUSH_MS);
    }

    function onFocusOut(e) {
      if (pending && (pending.el === e.target || pending.el.contains(e.target))) flushPending();
    }

    // ── 点击 ──
    function recordClick(target, e, rawEl, viaLabel) {
      const notes = [];
      if (viaLabel) {
        notes.push('点的是 ' + L.describe(rawEl) + '，记到它关联的控件 ' + L.describe(target) + ' 上');
      } else if (target !== rawEl) {
        notes.push('点在 ' + L.describe(rawEl) + ' 上，已提升到可点击的 ' + L.describe(target));
      }
      const href = target.href;
      if (typeof href === 'string' && href && !/^javascript:/i.test(href)) {
        notes.push('链接：' + href + (e.ctrlKey || e.metaKey || e.shiftKey || e.button === 1 || target.target === '_blank'
          ? '（这次是在新标签页打开的；hb 脚本默认当前标签页，要新标签页用 Python API 的 click.for_new_tab()）' : ''));
      }
      flushPending();
      const dbl = e.detail === 2 && lastClick && lastClick.el === target && now() - lastClick.t < 600;
      if (dbl) lastClick = null; else lastClick = {el: target, t: now()};
      emit({kind: 'click', desc: L.describe(target), plan: env.locate(target),
            count: dbl ? 2 : 1, replaceLast: !!dbl, notes: notes});
    }

    function onClick(e) {
      if (env.isPicking && env.isPicking()) {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
        if (!pick || !pick.el) setPickEl(targetOf(e));     // 没经过 mouseover 直接点（自动化 / 触屏）
        confirmPick(e);
        return;
      }
      if (!active() || fromUi(e, env)) return;
      const el = targetOf(e);
      if (!el || el === doc.body || el === doc.documentElement) return;
      if (e.button === 2) {                          // 右键菜单：hb 没有 right-click 命令
        flushPending();
        emit({kind: 'note', text: '在 ' + L.describe(lift(el, doc, win)) + ' 上点了右键（hb 脚本不处理右键菜单）'});
        return;
      }
      const label = el.closest ? el.closest('label') : null;
      if (label) {
        const control = label.control;
        if (control && control !== el && !control.contains(el)) {
          // 点在 label 文字上：文本框/下拉只是被聚焦（input 命令自己会聚焦），勾选框/单选会被代点一下
          const isChoice = tagOf(control) === 'input' && /^(checkbox|radio)$/.test(control.type || '');
          if (!isChoice) return;
          if (lastLabel && lastLabel.control === control && now() - lastLabel.t < 400) return;
          lastLabel = {control: control, t: now()};
          recordClick(clickable(control, win) ? control : label, e, el, true);
          return;
        }
      }
      if (lastLabel && el === lastLabel.control && now() - lastLabel.t < 400) return;   // label 代发的合成 click
      recordClick(lift(el, doc, win), e, el);
    }

    // ── 按键 ──
    function onKeydown(e) {
      if (env.isPicking && env.isPicking()) {
        if (e.key === 'Escape') { e.preventDefault(); cancelPick(); }
        else if (e.key === 'ArrowLeft') { e.preventDefault(); pickParent(); }
        else if (e.key === 'ArrowRight') { e.preventDefault(); pickChild(); }
        return;
      }
      if (!active() || e.isComposing || fromUi(e, env)) return;
      const key = keyName(e);
      if (!key) return;
      let el = targetOf(e) || doc.activeElement || doc.body;
      const editable = isEditable(el);
      const altGr = e.ctrlKey && e.altKey && e.key.length === 1;     // Windows 上 AltGr = ctrl+alt
      const cmd = (e.ctrlKey || e.metaKey || e.altKey) && !altGr;
      const typing = e.key.length === 1 && !cmd;
      if (editable) {
        if (typing) return;                                   // 文字：跟 input 事件
        if (e.key === 'Enter' && tagOf(el) !== 'input') return;   // textarea / 可编辑区的回车 = 换行
        if (!cmd && EDIT_KEYS.has(e.key)) return;              // 退格/方向键：效果在字段值里
      }
      // 按键之前先把输入落盘：同一元素“输入 → 回车”的顺序不能乱
      flushPending();
      let target = el;
      if (!editable) {
        // 页面级按键发给当时有焦点的元素（页面快捷键多半监听在它上面）；谁都没焦点就发给 body
        const ae = doc.activeElement;
        target = (ae && ae !== doc.body && ae !== doc.documentElement && !(env.isUi && env.isUi(ae))) ? ae : doc.body;
      }
      emit({kind: 'press', key: key, desc: L.describe(target), plan: env.locate(target)});
    }

    // ── 下拉 / 其它控件 ──
    function onChange(e) {
      if (!active() || fromUi(e, env)) return;
      const el = e.target;
      if (!el || el.nodeType !== 1) return;
      const tag = tagOf(el);
      if (tag === 'select') {
        flushPending();
        const opt = el.multiple ? null : el.options[el.selectedIndex];
        if (!opt) return;
        const value = opt.value;
        const text = opt.text.trim();
        const by = (value && value !== text && value.length <= 60 && !/^\s*$/.test(value) && !/^\d+$/.test(value))
          ? 'value' : 'text';                 // 有语义的 value 最稳；纯数字序号不如直接写选项文字
        emit({kind: 'select', desc: L.describe(el), plan: env.locate(el), value: by === 'value' ? value : text,
              by: by, optionText: text, optionValue: value});
        return;
      }
      if (tag === 'input' && !isEditable(el) && !/^(checkbox|radio|button|submit|reset|hidden|image)$/.test(el.type || '')) {
        flushPending();
        emit({kind: 'note', text: '改动了 ' + L.describe(el) + '（type=' + el.type + (el.type === 'file'
          ? '，选了文件' : '，值变为 ' + el.value) + '）；hb 脚本没有对应命令，请手动处理'});
      }
    }

    // ── 选取模式（抓取内容用）──
    function setPickEl(el) {
      if (pick && pick.el === el) return;
      pick = {el: el, siblings: [], info: ''};
      if (env.ui) env.ui.hover(el, [], '');
      clearTimeout(pickTimer);
      if (!el) return;
      pickTimer = setTimeout(() => {            // 停稳再算方案：鼠标划过时不算
        if (!pick || pick.el !== el) return;
        const plan = env.locate(el);
        const all = plan.all;
        const siblings = all ? L.resolveAll(all).slice(0, 200) : [];
        pick.plan = plan;
        pick.siblings = siblings;
        pick.info = plan.kind === 'none' ? '⚠ ' + plan.reason
          : plan.why + (all ? '；同类 ' + all.n + ' 个' : '') + '　' + plan.dsl;
        if (env.ui) env.ui.hover(el, siblings, pick.info);
      }, 70);
    }
    let pickTimer = 0;

    function pickParent() {
      if (!pick || !pick.el) return;
      const p = pick.el.parentElement;
      if (p && p !== doc.body) setPickEl(p);
    }

    function pickChild() {
      if (!pick || !pick.el) return;
      let best = null;
      for (const c of pick.el.children) {
        const r = c.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) { best = c; break; }
      }
      if (best) setPickEl(best);
    }

    function onHover(e) {
      if (!(env.isPicking && env.isPicking()) || fromUi(e, env)) return;
      const el = targetOf(e);
      if (!el || el === doc.body || el === doc.documentElement) { setPickEl(null); return; }
      setPickEl(el);
    }

    function onWheel(e) {
      if (!(env.isPicking && env.isPicking())) return;
      e.preventDefault();
      e.stopPropagation();
      if (e.deltaY < 0) pickParent(); else pickChild();
    }

    function confirmPick(ev) {
      const el = (pick && pick.el) || (ev && targetOf(ev)) || null;
      if (!el) return;
      clearTimeout(pickTimer);
      const plan = pick && pick.plan || env.locate(el);
      const attrs = Array.from(el.attributes).map(a => a.name).filter(n => n !== 'style');
      const result = {el: el, desc: L.describe(el), plan: plan, attrs: attrs, tag: tagOf(el)};
      pick = null;                               // 选中了：清掉选取状态，但不发“取消”
      if (env.ui) { env.ui.hover(null, [], ''); env.ui.unhover(); }
      env.onPick(result);
    }

    function cancelPick() {
      clearTimeout(pickTimer);
      pick = null;
      if (env.ui) { env.ui.hover(null, [], ''); env.ui.unhover(); }
      env.onPickCancel && env.onPickCancel();
    }

    on('click', onClick);
    on('keydown', onKeydown);
    on('input', onInput);
    on('change', onChange);
    on('focusout', onFocusOut);
    on('mouseover', onHover);
    on('wheel', onWheel, {capture: true, passive: false});

    return {
      flush: flushPending,
      hostLocator: childIndex => hostLocator(win, doc, childIndex),
      hostElement: childIndex => hostElement(win, doc, childIndex),
      cancelPick: cancelPick,
      pickingElement: () => (pick && pick.el) || null,
    };
  }

  // ───────── 归并 ─────────
  // 把一步并入步骤表。录制会话的归并规则集中在这里，background 与测试共用：
  // - 按发生时间插到正确的位置（跨 frame 到达顺序不可靠）；
  // - 同一元素上紧接着的又一次输入：替换上一步（回放时输入的本来就是字段终值）；
  // - 双击的第二下：把上一步单击改成双击。
  function planKey(st) {
    const p = st.plan;
    if (!p) return null;
    return JSON.stringify([st.framePath || [], p.kind, p.kind === 'anchored' ? [p.anchor && p.anchor.dsl, p.rel, p.dsl] : p.dsl]);
  }

  function appendStep(steps, step) {
    let pos = steps.length;
    while (pos > 0 && steps[pos - 1].t > step.t) pos--;
    const prev = pos > 0 ? steps[pos - 1] : null;
    if (prev && step.kind === 'input' && prev.kind === 'input' && planKey(prev) === planKey(step)) {
      steps[pos - 1] = step;
      return steps;
    }
    if (prev && step.replaceLast && prev.kind === step.kind && planKey(prev) === planKey(step)) {
      steps[pos - 1] = step;
      return steps;
    }
    steps.splice(pos, 0, step);
    return steps;
  }

  return {mount: mount, framePath: framePath, hostLocator: hostLocator, hostElement: hostElement,
          isEditable: isEditable, keyName: keyName, appendStep: appendStep, planKey: planKey};
});
