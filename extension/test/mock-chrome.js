/* mock-chrome.js — 只给 tests/test_recorder.py 用：在没有扩展环境的页面里模拟 chrome.*
 *
 * 把扩展的三个角色放进同一个标签页的不同 window：
 *   - 顶层窗口 + 各 iframe           → 内容脚本（content.js）
 *   - 顶层里的 about:blank iframe     → service worker（background.js）
 *   - 顶层里的 sidepanel.html iframe  → 侧边栏（sidepanel.js）
 * 消息、存储、标签页事件都走这里的总线（顶层 window.__hbrBus），语义照着真实的 chrome API：
 *   runtime.sendMessage 只送到扩展页（service worker / 侧边栏），内容脚本收不到，发送方自己也收不到；
 *   tabs.sendMessage(tabId, msg, {frameId}) 只送到那个 frame 的内容脚本；
 *   跨域 iframe 用 postMessage 与总线往来（真实环境里它本来就只能靠 runtime 消息）。
 * 不模拟：service worker 被回收、权限、真实的标签页/窗口管理。
 *
 * 用法：注入本文件前先设 window.__hbrRole（'background' / 'panel'；不设就是内容脚本）。
 */
(function () {
  if (window.__hbrMock) return;
  const IS_TOP = (function () { try { return window.top === window; } catch (e) { return false; } })();
  // 同源与否只看能不能读 top.location，与总线是否已建无关：辅助 iframe 可能比顶层先加载
  const SAME = IS_TOP || (function () { try { void window.top.location.href; return true; } catch (e) { return false; } })();
  const TAB = {id: 1000, windowId: 1};

  let bus = null;
  if (IS_TOP) {
    bus = window.__hbrBus = {rt: [], frames: new Map(), next: 1, storage: {session: {}, local: {}},
                             events: {}, downloads: [], tabId: TAB.id, topWin: window, pending: new Map()};
    bus.frames.set(0, {win: window, same: true});            // 顶层 frame 固定 0
  } else if (SAME) {
    bus = window.top.__hbrBus;
    if (!bus) throw new Error('mock-chrome：请先在顶层窗口注入（总线由顶层持有）');
  }
  const role = window.__hbrRole || 'content';
  const localListeners = [];        // 本 window 的 runtime.onMessage 监听：内容脚本靠它收 tabs.sendMessage

  // 同源 frame：按 window 身份发号，顶层固定 0
  function frameIdOf(win) {
    if (win === bus.topWin) return 0;
    for (const [id, f] of bus.frames) if (f.same && f.win === win) return id;
    const id = bus.next++;
    bus.frames.set(id, {win: win, same: true});
    return id;
  }

  // ———— 总线 ————
  function deliverRuntime(msg, sender, respond, fromRole) {
    setTimeout(() => {
      const targets = bus.rt.filter(l => (l.role === 'background' || l.role === 'panel') && l.role !== fromRole);
      if (!targets.length) { respond(undefined, true); return; }
      for (const l of targets) {
        let done = false;
        const keep = l.fn(msg, sender, r => { if (!done) { done = true; respond(r); } });
        if (keep !== true && !done) { done = true; respond(undefined); }
      }
    }, 0);
  }

  function dispatchLocal(msg, respond) {
    setTimeout(() => {
      let responded = false;
      const once = r => { if (!responded) { responded = true; respond(r); } };
      for (const l of localListeners.slice()) {
        const keep = l(msg, {tab: {id: TAB.id}, frameId: 0, url: location.href}, once);
        if (keep !== true && !responded) once(undefined);
      }
      if (!responded) once(undefined);
    }, 0);
  }

  function tabsSend(frameId, msg) {          // background → 某个 frame 的内容脚本
    return new Promise(resolve => {
      const f = bus.frames.get(frameId);
      if (!f) { resolve(undefined); return; }
      if (f.same) { resolve(f.win.__hbrMock.receive(msg)); return; }
      const seq = ++bus.next;
      bus.pending.set(seq, resolve);
      f.win.postMessage({__hbr: 'deliver', seq: seq, msg: msg}, '*');
      setTimeout(() => { if (bus.pending.has(seq)) { bus.pending.delete(seq); resolve(undefined); } }, 2000);
    });
  }

  if (IS_TOP) {
    window.addEventListener('message', ev => {       // 跨域 frame 的来信
      const d = ev.data;
      if (!d || !d.__hbr) return;
      if (d.__hbr === 'register') {
        const id = bus.next++;
        bus.frames.set(id, {win: ev.source, same: false});
        ev.source.postMessage({__hbr: 'registered', frameId: id}, '*');
      } else if (d.__hbr === 'send') {
        deliverRuntime(d.msg, {tab: {id: TAB.id}, frameId: d.frameId, url: d.url},
                         resp => ev.source.postMessage({__hbr: 'resp', seq: d.seq, resp: resp}, '*'), 'content');
      } else if (d.__hbr === 'frameResp') {
        const fn = bus.pending.get(d.seq);
        bus.pending.delete(d.seq);
        if (fn) fn(d.resp);
      }
    });
  }

  // ———— 本 window：同源直接走总线，跨域靠 postMessage ————
  let frameId = SAME ? frameIdOf(window) : -1;
  let seq = 0;
  const waiting = new Map();          // seq → respond
  const early = [];                   // 登记完成前发出的消息
  let lastError;

  if (!SAME) {
    window.top.postMessage({__hbr: 'register'}, '*');
    window.addEventListener('message', ev => {
      const d = ev.data;
      if (!d || !d.__hbr) return;
      if (d.__hbr === 'registered') {
        frameId = d.frameId;
        for (const m of early.splice(0)) postSend(m.msg, m.respond);
      } else if (d.__hbr === 'deliver') {
        dispatchLocal(d.msg, r => window.top.postMessage({__hbr: 'frameResp', seq: d.seq, resp: r}, '*'));
      } else if (d.__hbr === 'resp') {
        const fn = waiting.get(d.seq);
        waiting.delete(d.seq);
        if (fn) fn(d.resp);
      }
    });
  }

  function postSend(msg, respond) {
    const my = ++seq;
    waiting.set(my, respond);
    window.top.postMessage({__hbr: 'send', seq: my, msg: msg, frameId: frameId, url: location.href}, '*');
  }

  function respondVia(cb) {
    return (r, failed) => {
      lastError = failed ? {message: 'Could not establish connection. Receiving end does not exist.'} : undefined;
      try { if (cb) cb(r); } finally { lastError = undefined; }
    };
  }

  function event(name) {
    if (bus && bus.events[name]) return bus.events[name];     // 事件对象全局唯一：后加载的 window 复用，别把已注册的监听丢掉
    const e = {listeners: [], addListener(fn) { e.listeners.push(fn); },
               removeListener(fn) { e.listeners = e.listeners.filter(x => x !== fn); }};
    if (bus) bus.events[name] = e;
    return e;
  }

  function storage(area) {
    return {
      get(defaults, cb) {
        const out = Object.assign({}, defaults || {});
        for (const k of Object.keys(out)) if (k in bus.storage[area]) out[k] = bus.storage[area][k];
        if (cb) { cb(out); return; }
        return Promise.resolve(out);
      },
      set(obj, cb) {
        Object.assign(bus.storage[area], obj);
        if (cb) { cb(); return; }
        return Promise.resolve();
      },
    };
  }

  const sender = () => ({tab: {id: TAB.id}, frameId: frameId, url: location.href});

  const chrome = {
    runtime: {
      get lastError() { return lastError; },
      get id() { return 'a'.repeat(32); },
      onMessage: {
        addListener(fn) {
          localListeners.push(fn);
          if (SAME && (role === 'background' || role === 'panel')) bus.rt.push({role: role, fn: fn});
        },
      },
      sendMessage(msg, cb) {
        if (!cb) return new Promise(resolve => chrome.runtime.sendMessage(msg, resolve));   // promise 形式：回包只算第一次
        let done = false;
        const respond = (r, failed) => { if (done) return; done = true; respondVia(cb)(r, failed); };
        if (SAME) deliverRuntime(msg, sender(), respond, role);
        else if (frameId < 0) early.push({msg: msg, respond: respond});
        else postSend(msg, respond);
      },
    },
    storage: {session: storage('session'), local: storage('local')},
    tabs: {
      get() {
        const t = bus ? bus.topWin : window;
        return Promise.resolve({id: TAB.id, windowId: 1, url: t.location.href, title: t.document.title, active: true});
      },
      query() { return this.get().then(t => [t]); },
      sendMessage(tabId, msg, opts, cb) {
        const fid = (opts && typeof opts.frameId === 'number') ? opts.frameId : 0;
        const p = tabsSend(fid, msg);
        if (cb) { p.then(cb); return; }
        return p;
      },
      onActivated: event('tabs.onActivated'), onUpdated: event('tabs.onUpdated'),
      onCreated: event('tabs.onCreated'), onRemoved: event('tabs.onRemoved'),
    },
    webNavigation: {onCommitted: event('webNavigation.onCommitted')},
    action: {setBadgeText() {}, setBadgeBackgroundColor() {}, setTitle() {}, onClicked: event('action.onClicked')},
    commands: {onCommand: event('commands.onCommand')},
    sidePanel: {open() { return Promise.resolve(); }, setPanelBehavior() { return Promise.resolve(); }},
    downloads: {
      download(opts) {
        return fetch(opts.url).then(r => r.text()).then(text => {
          bus.downloads.push({filename: opts.filename, text: text, saveAs: opts.saveAs});
          return bus.downloads.length;
        });
      },
    },
  };

  if (typeof self.importScripts !== 'function') self.importScripts = function () {};   // service worker 专用，页面里补个空的

  window.__hbrMock = {
    chrome: chrome,
    role: role,
    frameId: () => frameId,
    receive(msg) { return new Promise(resolve => dispatchLocal(msg, resolve)); },
  };
  window.chrome = chrome;
})();
