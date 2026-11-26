/* content.js — hb 录制器内容脚本（manifest 声明，每个 frame 一份，document_start 注入）
 *
 * 只做三件事：把 recorder-core 挂到本 frame、收发 chrome.runtime 消息、画自己的小界面
 * （录制中的角标、选取模式的高亮框）。录制规则一律在 lib/recorder-core.js，定位一律在 lib/locator.js。
 */
(() => {
  if (window.__hbrLoaded) return;
  window.__hbrLoaded = true;

  const L = globalThis.HbLocator;
  const R = globalThis.HbRecorder;
  const framePath = R.framePath(window);
  const TOP = framePath.length === 0;

  let recording = false;
  let picking = false;
  let maskPasswords = true;
  let stepCount = 0;
  const inFlight = new Set();          // 已发出、还没收到回包的步骤消息（停止录制前等它们落地）

  // 扩展重载 / 页面是受限页面时 chrome.runtime 会失效：一律吞掉，不往页面控制台扔错
  function send(msg) {
    return new Promise(resolve => {
      try {
        const p = chrome.runtime.sendMessage(msg, r => {
          const err = chrome.runtime.lastError;
          resolve(err ? {error: err.message} : r);
        });
        if (p && p.then) p.then(resolve, e => resolve({error: String(e)}));
      } catch (e) {
        resolve({error: String(e)});
      }
    });
  }

  // ───────── 小界面 ─────────
  const UI_ATTR = 'data-hbr-ui';
  let overlay = null;                 // 选取模式的高亮层
  let boxes = [];                     // [element, el] 一一对应的高亮框
  let label = null;
  let pill = null;


  function ensureOverlay() {
    if (overlay) return overlay;
    overlay = document.createElement('div');
    overlay.id = 'hbr-overlay';
    overlay.setAttribute(UI_ATTR, '');
    label = document.createElement('div');
    label.id = 'hbr-label';
    overlay.appendChild(label);
    document.documentElement.appendChild(overlay);
    return overlay;
  }

  function place(box, el) {
    const r = el.getBoundingClientRect();
    box.style.left = r.left + 'px';
    box.style.top = r.top + 'px';
    box.style.width = r.width + 'px';
    box.style.height = r.height + 'px';
  }

  let shownEls = [];
  function draw() {
    if (!overlay || !overlay.classList.contains('on')) return;
    for (let i = 0; i < shownEls.length; i++) {
      if (boxes[i] && shownEls[i].isConnected) place(boxes[i], shownEls[i]);
    }
  }

  const ui = {
    // el：当前指的元素；siblings：与它“同款”的那一组；info：一行说明
    hover(el, siblings, info) {
      const ov = ensureOverlay();
      for (const b of boxes) b.remove();
      boxes = [];
      shownEls = [];
      if (!el) { ov.classList.remove('on'); return; }
      const all = siblings && siblings.length ? siblings : [el];
      for (const s of all) {
        const box = document.createElement('div');
        box.className = 'hbr-box' + (s === el ? '' : ' sibling');
        place(box, s);
        ov.appendChild(box);
        boxes.push(box);
        shownEls.push(s);
      }
      const r = el.getBoundingClientRect();
      label.textContent = '';
      label.innerHTML = info ? escapeHtml(info) : '';
      ov.appendChild(label);
      label.style.left = Math.max(4, r.left) + 'px';
      label.style.top = (r.top > 40 ? r.top - 30 : r.bottom + 6) + 'px';
      ov.classList.add('on');
    },
    unhover() {
      if (overlay) overlay.classList.remove('on');
    },
  };

  function escapeHtml(s) {
    return String(s).replace(/[&<>]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;'}[c]));
  }

  function showPill() {
    if (!TOP) return;
    if (!pill) {
      pill = document.createElement('div');
      pill.id = 'hbr-pill';
      pill.setAttribute(UI_ATTR, '');
      pill.innerHTML = '<span class="dot"></span><span id="hbr-count">录制中</span>' +
        '<button type="button" title="停止录制（Alt+Shift+R）">■ 停止</button>';
      pill.querySelector('button').addEventListener('click', e => {
        e.preventDefault();
        e.stopPropagation();
        send({type: 'hb:stop'});
      });
    }
    updatePill(stepCount);
    document.documentElement.appendChild(pill);
  }

  function updatePill(n) {
    stepCount = n;
    const c = pill && pill.querySelector('#hbr-count');
    if (c) c.textContent = '录制中 · ' + n + ' 步';
  }

  function hidePill() {
    if (pill) pill.remove();
  }

  let tip = null;
  function showTip(text) {
    if (!tip) {
      tip = document.createElement('div');
      tip.id = 'hbr-tip';
      tip.setAttribute(UI_ATTR, '');
      document.documentElement.appendChild(tip);
    }
    tip.textContent = text;
  }
  function hideTip() {
    if (tip) tip.remove();
    tip = null;
  }

  // ───────── 录制核心 ─────────
  const recorder = R.mount({
    win: window,
    doc: document,
    isRecording: () => recording,
    isPicking: () => picking,
    isUi: el => !!(el && el.closest && el.closest('[' + UI_ATTR + ']')),
    maskPasswords: () => maskPasswords,
    locate: el => L.best(el),
    emit(step) {
      step.framePath = framePath;
      step.url = location.href;
      const p = send({type: 'hb:step', step});
      inFlight.add(p);
      p.then(() => inFlight.delete(p));
    },
    onPick(result) {
      setPicking(false, false);
      send({type: 'hb:picked', pick: {desc: result.desc, plan: result.plan, attrs: result.attrs, tag: result.tag,
                                       framePath: framePath, url: location.href}});
    },
    onPickCancel() {
      send({type: 'hb:pick-cancelled', framePath: framePath});
    },
    ui: ui,
  });

  L.setIgnore(el => !!(el && el.closest && el.closest('[' + UI_ATTR + ']')));

  function applyState(msg) {
    maskPasswords = msg.maskPasswords !== false;
    const was = recording;
    recording = !!msg.recording;
    if (recording) {
      if (typeof msg.n === 'number') updatePill(msg.n);      // 页面刚加载进来：接着显示已有步数
      whenRoot(showPill);
    } else {
      hidePill();
    }
    if (!recording && was) recorder.flush();
  }

  // document_start 时 <html> 可能还没建出来
  function whenRoot(fn) {
    if (document.documentElement) fn();
    else document.addEventListener('readystatechange', () => fn(), {once: true});
  }

  // notify=false：已经选中了元素，不要再发“取消”
  function setPicking(on, notify) {
    picking = !!on;
    document.documentElement.classList.toggle('hbr-picking', picking);
    if (picking) {
      if (TOP) whenRoot(() => showTip('点选要抓取的元素 · 滚轮 / ← → 调整层级 · Esc 取消'));
    } else {
      if (notify !== false) recorder.cancelPick();
      else if (overlay) overlay.classList.remove('on');
      hideTip();
    }
  }

  // ───────── 消息 ─────────
  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    switch (msg && msg.type) {
      case 'hb:state':
        applyState(msg);
        sendResponse({ok: true});
        return false;
      case 'hb:progress':
        updatePill(msg.n);
        sendResponse({ok: true});
        return false;
      case 'hb:pick':
        setPicking(!!msg.on);
        sendResponse({ok: true});
        return false;
      case 'hb:host-locator':
        sendResponse(recorder.hostLocator(msg.childIndex));
        return false;
      case 'hb:flush':
        recorder.flush();
        // 等已发出的步骤消息都到了 background 再回话（停止录制前要用它排空）
        Promise.all(Array.from(inFlight)).then(() => sendResponse({ok: true}));
        return true;
      default:
        return false;
    }
  });

  // 本文档加载进来：登记 frame，问一下这个标签页是不是正在录制
  send({type: 'hb:hello', framePath: framePath, url: location.href}).then(r => {
    if (r && r.recording) applyState(Object.assign({n: 0}, r));
  });

  window.addEventListener('scroll', draw, true);
  window.addEventListener('resize', draw);
  window.addEventListener('pagehide', () => recorder.flush());     // 尽力而为：整页跳走前把输入落盘
})();
