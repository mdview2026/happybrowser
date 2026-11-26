/* background.js — hb 录制器 service worker
 *
 * 录制会话（步骤表）以这里为准，落盘在 chrome.storage.session（service worker 会被随时回收，
 * 内存里的状态不能当真；session 区不写磁盘、浏览器重启即清）。内容脚本只管“发生了什么”，
 * 侧边栏只管“展示和编辑”，两边都通过消息到这里读写。
 *
 * iframe：每个 frame 的内容脚本加载时发 hello（带它在顶层 frames 树里的下标路径），
 * 这里记 路径 → frameId；iframe 里发生的步骤，由这里逐层去问父 frame
 * “第 n 个子 frame 的 <iframe> 怎么定位”，拼出 plan.frames 链。
 */
importScripts('lib/locator.js', 'lib/recorder-core.js');

const L = self.HbLocator;
const R = self.HbRecorder;

const DEFAULT_OPTIONS = {inline: false, comments: true, maskPasswords: true};
// 这些跳转方式是用户自己发起的（地址栏 / 书签），回放时得写成 open；其余多半是点击的结果，只记注释
const OPEN_TYPES = new Set(['typed', 'auto_bookmark', 'generated', 'auto_toplevel', 'keyword', 'keyword_generated']);
const RESTRICTED = /^(?:chrome|chrome-extension|edge|about|devtools|view-source|chrome-search):/i;

const state = {sessions: {}, frames: {}, options: Object.assign({}, DEFAULT_OPTIONS)};
let persistTimer = 0;

const ready = (async () => {
  try {
    const got = await chrome.storage.session.get({hbr: {sessions: {}, frames: {}}});
    state.sessions = (got.hbr && got.hbr.sessions) || {};
    state.frames = (got.hbr && got.hbr.frames) || {};
    const o = await chrome.storage.local.get({hbrOptions: DEFAULT_OPTIONS});
    Object.assign(state.options, o.hbrOptions || {});
    for (const tabId of Object.keys(state.sessions)) {
      if (state.sessions[tabId].recording) setBadge(Number(tabId), true);
    }
  } catch (e) {
    console.warn('hbr: 读取录制状态失败', e);
  }
})();

function persist() {
  clearTimeout(persistTimer);
  return chrome.storage.session.set({hbr: {sessions: state.sessions, frames: state.frames}})
    .catch(e => console.warn('hbr: 保存录制状态失败', e));
}
function persistSoon() {
  clearTimeout(persistTimer);
  persistTimer = setTimeout(persist, 300);
}

// ----------------------------------------------------------------工具

const key = path => JSON.stringify(path || []);
const framesOf = tabId => state.frames[tabId] || (state.frames[tabId] = {});
const sessionOf = tabId => state.sessions[tabId];

async function getTab(tabId) {
  try {
    return await chrome.tabs.get(tabId);
  } catch (e) {
    return null;
  }
}

// 逐个 frame 发：要的是“每个都送到 / 都回话”，一次性广播拿不齐回包
// 顶层（frameId 0）总是发：登记表里缺它也不能让角标/状态丢了
async function sendToTab(tabId, msg, frameIds) {
  const ids = frameIds ? frameIds.slice() : Object.values(framesOf(tabId)).map(f => f.frameId);
  if (ids.indexOf(0) === -1) ids.push(0);
  await Promise.all(ids.map(frameId => chrome.tabs.sendMessage(tabId, msg, {frameId}).catch(() => null)));
}

function toTopFrame(tabId, msg) {
  return chrome.tabs.sendMessage(tabId, msg, {frameId: 0}).catch(() => null);
}

function broadcast(msg) {
  chrome.runtime.sendMessage(msg).catch(() => {});      // 侧边栏没开就没人收，正常
}

function changed(tabId) {
  const s = sessionOf(tabId);
  broadcast({type: 'hb:session', tabId: tabId, session: s});
  if (s) toTopFrame(tabId, {type: 'hb:progress', n: s.steps.length});
  return s;
}

function setBadge(tabId, on) {
  try {
    chrome.action.setBadgeText({tabId: tabId, text: on ? 'REC' : ''});
    if (on) chrome.action.setBadgeBackgroundColor({tabId: tabId, color: '#e5484d'});
    chrome.action.setTitle({tabId: tabId, title: on ? 'hb 录制器：录制中（点击停止）' : 'hb 录制器：点击开始录制'});
  } catch (e) { /* 标签页已经关了 */ }
}

// ----------------------------------------------------------------录制会话

async function startRecording(tabId) {
  await ready;
  const tab = await getTab(tabId);
  if (!tab) return {error: '标签页不存在'};
  if (RESTRICTED.test(tab.url || '') || /^https:\/\/chrome\.google\.com\/(webstore|extensions)/i.test(tab.url || '')) {
    return {error: '浏览器内部页面不能录制，请换一个普通网页'};
  }
  const old = sessionOf(tabId);
  if (old && !old.recording && old.steps.length) {
    old.recording = true;                       // 停了又点开始 = 续录；要重新开始先「清空」
    await persist();
    await sendToTab(tabId, {type: 'hb:state', recording: true, maskPasswords: state.options.maskPasswords});
    setBadge(tabId, true);
    changed(tabId);
    return {session: old, resumed: true};
  }
  state.sessions[tabId] = {recording: true, startedAt: Date.now(), startUrl: tab.url, startTitle: tab.title || '',
                          seq: 0, steps: [], lastUrl: tab.url};
  await persist();
  await sendToTab(tabId, {type: 'hb:state', recording: true, maskPasswords: state.options.maskPasswords});
  setBadge(tabId, true);
  broadcast({type: 'hb:session', tabId: tabId, session: state.sessions[tabId]});
  return {session: state.sessions[tabId]};
}

async function stopRecording(tabId) {
  await ready;
  const s = sessionOf(tabId);
  if (!s || !s.recording) return {session: s};
  // 先让每个 frame 把输入中的字段落盘、并等它们已发出的步骤消息到达，再关闸
  await sendToTab(tabId, {type: 'hb:flush'});
  if (pendingNav[tabId]) await commitNav(tabId, pendingNav[tabId].d);   // 攒着的跳转也落账
  s.recording = false;
  await sendToTab(tabId, {type: 'hb:state', recording: false});
  setBadge(tabId, false);
  await persist();
  broadcast({type: 'hb:session', tabId: tabId, session: s});
  return {session: s};
}

// iframe 链：逐层问父 frame “第 n 个子 frame 的宿主元素怎么定位”
async function resolveHosts(tabId, path, plan) {
  const hosts = [];
  for (let i = 0; i < path.length; i++) {
    const parent = framesOf(tabId)[key(path.slice(0, i))];
    const r = parent && await chrome.tabs.sendMessage(tabId, {type: 'hb:host-locator', childIndex: path[i]},
                                                      {frameId: parent.frameId}).catch(() => null);
    if (!r || !r.plan) {
      plan.frameError = 'iframe 链没解析完整（第 ' + (i + 1) + ' ' + '层 frame 的宿主元素没定位到），下面的命令只覆盖到外层';
      break;
    }
    hosts.push({dsl: r.plan.dsl, index: r.plan.index, src: r.src || '', name: r.name || '', title: r.title || ''});
  }
  return hosts;
}

async function addStep(tabId, step) {
  await ready;
  const s = sessionOf(tabId);
  if (!s || !s.recording || !step || !step.kind) return {ok: false};
  step.id = ++s.seq;
  if ((step.framePath || []).length && step.plan) {
    step.plan = Object.assign({}, step.plan);
    step.plan.frames = await resolveHosts(tabId, step.framePath, step.plan);
  }
  R.appendStep(s.steps, step);                 // 按发生时间归位、同一字段连续输入合并、双击并入
  await persist();
  changed(tabId);
  return {ok: true, id: step.id};
}

// ----------------------------------------------------------------跳转 / 标签页事件

// 跳转事件不立刻落账：引起它的点击步骤可能还在路上，SPA 也常常连跳几次。
// 攒 250 ms 再提交，到时再看“上一步是不是刚才的点击 / 刚才的跳转”。
const pendingNav = {};

function scheduleNav(d) {
  clearTimeout(pendingNav[d.tabId] && pendingNav[d.tabId].timer);
  pendingNav[d.tabId] = {d: d, timer: setTimeout(() => commitNav(d.tabId, d), 250)};
}

async function commitNav(tabId, d) {
  clearTimeout(pendingNav[tabId] && pendingNav[tabId].timer);
  delete pendingNav[tabId];
  const s = sessionOf(tabId);
  if (!s || !s.recording || d.frameId !== 0) return;
  const url = d.url;
  if (url === s.lastUrl && d.transitionType !== 'reload') return;       // 同地址（SPA 的 replaceState 之类）
  const steps = s.steps;
  const last = steps[steps.length - 1];
  const afterAction = last && last.t && Date.now() - last.t < 2500 &&
    (last.kind === 'click' || (last.kind === 'press' && /enter|return/i.test(last.key || '')));
  const back = (d.transitionQualifiers || []).indexOf('forward_back') !== -1;
  const step = {kind: 'nav', url: url, leavesPage: true, t: Date.now()};
  if (OPEN_TYPES.has(d.transitionType) && !afterAction && !back) {
    step.mode = 'open';
    step.text = '打开：' + url;
  } else {
    step.mode = 'comment';
    step.text = (back ? '前进/后退到：' : d.transitionType === 'reload' ? '页面刷新（引用全部失效，需要重新 find）：'
      : d.transitionType === 'form_submit' ? '表单提交后跳转到：' : '跳转到：') + url;
  }
  // 路由连续变化（SPA）只留最后一条
  if (last && last.kind === 'nav' && Date.now() - last.t < 1500) {
    steps[steps.length - 1] = Object.assign({}, last, step, {t: last.t});
  } else {
    R.appendStep(steps, step);
  }
  s.lastUrl = url;
  await persist();
  changed(tabId);
}

chrome.webNavigation.onCommitted.addListener(d => {
  ready.then(() => {
    const s = sessionOf(d.tabId);
    if (s && s.recording && d.frameId === 0) scheduleNav(d);
  });
});

chrome.tabs.onCreated.addListener(async tab => {
  await ready;
  const s = tab.openerTabId && sessionOf(tab.openerTabId);
  if (!s || !s.recording) return;
  const url = tab.pendingUrl || tab.url || '';
  await addStep(tab.openerTabId, {kind: 'nav', mode: 'comment', leavesPage: false,
                                  text: '这次点击新开了标签页' + (url ? '：' + url : '') + '（hb 脚本在原标签页里回放；要跟进新标签页请用 Python API 的 click.for_new_tab()）',
                                  url: url, t: Date.now()});
});

chrome.tabs.onRemoved.addListener(async tabId => {
  await ready;
  delete state.sessions[tabId];
  delete state.frames[tabId];
  await persist();
});

// ----------------------------------------------------------------消息

async function handle(msg, sender) {
  await ready;
  const tabId = sender.tab ? sender.tab.id : msg.tabId;
  switch (msg.type) {
    // —— 内容脚本 ——
    case 'hb:hello': {
      if (tabId == null) return {recording: false};
      framesOf(tabId)[key(msg.framePath)] = {frameId: sender.frameId, url: sender.url || ''};
      persistSoon();
      const s = sessionOf(tabId);
      return {recording: !!(s && s.recording), maskPasswords: state.options.maskPasswords, n: s ? s.steps.length : 0};
    }
    case 'hb:step':
      return addStep(tabId, msg.step);
    case 'hb:stop':
      return stopRecording(tabId);
    case 'hb:picked': {
      const pick = msg.pick || {};
      if ((pick.framePath || []).length && pick.plan) {
        const plan = Object.assign({}, pick.plan);
        plan.frames = await resolveHosts(tabId, pick.framePath, plan);
        pick.plan = plan;
      }
      broadcast({type: 'hb:picked', tabId: tabId, pick: pick});
      return {ok: true};
    }
    case 'hb:pick-cancelled':
      broadcast({type: 'hb:pick-cancelled', tabId: tabId});
      return {ok: true};

    // —— 侧边栏 ——
    case 'hb:get-state': {
      const tab = await getTab(msg.tabId);
      return {session: sessionOf(msg.tabId) || null, options: state.options,
              canRecord: !!(tab && !RESTRICTED.test(tab.url || ''))};
    }
    case 'hb:start':
      return startRecording(msg.tabId);
    case 'hb:clear': {
      const s = sessionOf(msg.tabId);
      if (s) {
        s.steps = [];
        s.seq = 0;
        await persist();
        changed(msg.tabId);
      }
      return {session: s || null};
    }
    case 'hb:delete-step': {
      const s = sessionOf(msg.tabId);
      if (s) {
        s.steps = s.steps.filter(st => st.id !== msg.id);
        await persist();
        changed(msg.tabId);
      }
      return {session: s || null};
    }
    case 'hb:update-step': {
      const s = sessionOf(msg.tabId);
      const st = s && s.steps.find(x => x.id === msg.id);
      if (!st) return {error: '步骤不存在'};
      const patch = msg.patch || {};
      if (patch.planAlt !== undefined) {
        st.plan = L.promoteAlt(st.plan, patch.planAlt);       // 换一个备选定位方案
      }
      if (patch.capture) st.capture = Object.assign({}, st.capture, patch.capture);
      for (const k of Object.keys(patch)) if (k !== 'capture' && k !== 'planAlt') st[k] = patch[k];
      await persist();
      changed(msg.tabId);
      return {session: s};
    }
    case 'hb:add-step': {
      const s = sessionOf(msg.tabId);
      if (!s) return {error: '这个标签页没有录制会话'};
      const step = Object.assign({t: Date.now()}, msg.step);
      step.id = ++s.seq;
      s.steps.push(step);
      await persist();
      changed(msg.tabId);
      return {session: s, id: step.id};
    }
    case 'hb:pick':
      await sendToTab(msg.tabId, {type: 'hb:pick', on: !!msg.on});
      return {ok: true};
    case 'hb:get-options':
      return {options: state.options};
    case 'hb:set-options': {
      Object.assign(state.options, msg.options || {});
      await chrome.storage.local.set({hbrOptions: state.options});
      for (const tid of Object.keys(state.sessions)) {
        if (state.sessions[tid].recording) {
          await sendToTab(Number(tid), {type: 'hb:state', recording: true, maskPasswords: state.options.maskPasswords});
        }
      }
      return {options: state.options};
    }
    default:
      return {error: '未知消息：' + msg.type};
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  handle(msg, sender).then(sendResponse, e => sendResponse({error: String(e && e.message || e)}));
  return true;                                  // 异步回包
});

// ----------------------------------------------------------------入口：工具栏按钮 / 快捷键

async function activeTab() {
  const tabs = await chrome.tabs.query({active: true, currentWindow: true});
  return tabs[0] || null;
}

async function toggle(tab) {
  if (!tab || tab.id == null) return;
  await ready;
  const s = sessionOf(tab.id);
  if (s && s.recording) await stopRecording(tab.id);
  else await startRecording(tab.id);
}

function openPanel(windowId) {
  try {
    const r = chrome.sidePanel.open({windowId: windowId});
    if (r && r.catch) r.catch(() => {});       // 没有用户手势时打不开：快捷键触发的那次可能就是这种
  } catch (e) { /* 同上 */ }
}

chrome.action.onClicked.addListener(async tab => {
  await toggle(tab);
  openPanel(tab.windowId);
});

chrome.commands.onCommand.addListener(async (command, tab) => {
  tab = tab || await activeTab();
  if (!tab) return;
  if (command === 'toggle-recording') {
    await toggle(tab);
    openPanel(tab.windowId);
  } else if (command === 'pick-content') {
    await ready;
    const s = sessionOf(tab.id);
    if (!s) {
      await startRecording(tab.id);            // 没在录制也可以抓：临时开一个会话
    }
    await sendToTab(tab.id, {type: 'hb:pick', on: true});
    openPanel(tab.windowId);
  }
});

chrome.sidePanel.setPanelBehavior({openPanelOnActionClick: false}).catch(() => {});
