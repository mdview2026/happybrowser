/* sidepanel.js — hb 录制器侧边栏：展示 / 编辑步骤、生成并保存 .hb
 *
 * 步骤表的唯一来源是 background（chrome.storage.session）；这里每次收到 hb:session 就整体重画。
 * 文本一律 textContent，不把页面里抓到的内容拼进 innerHTML。
 */
"use strict";

const G = window.HbScriptGen;
const $ = sel => document.querySelector(sel);
const KIND_LABEL = {click: '点击', input: '输入', press: '按键', select: '下拉选择', capture: '抓取', nav: '跳转', sleep: '等待', note: '说明'};
const EXTRA_ATTRS = ['href', 'src', 'value', 'title', 'alt', 'data-id', 'id', 'class', 'name'];

let tabId = null;
let session = null;                      // {recording, startUrl, startTitle, steps:[…]} 或 null
let options = {inline: false, comments: true, maskPasswords: true};
let canRecord = false;
let selectedId = null;
let pick = null;
let viewing = 'steps';

// ———— 工具 ————
function h(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  if (attrs) {
    for (const k of Object.keys(attrs)) {
      const v = attrs[k];
      if (k === 'class') n.className = v;
      else if (k === 'text') n.textContent = v;
      else if (k.indexOf('on') === 0) n.addEventListener(k.slice(2), v);
      else if (v !== null && v !== undefined) n.setAttribute(k, v);
    }
  }
  for (const k of kids) if (k !== null && k !== undefined) n.appendChild(typeof k === 'string' ? document.createTextNode(k) : k);
  return n;
}

function send(msg) {
  return new Promise(resolve => {
    try {
      chrome.runtime.sendMessage(msg, r => {
        const err = chrome.runtime.lastError;
        resolve(err ? {error: err.message} : r);
      });
    } catch (e) {
      resolve({error: String(e)});
    }
  });
}

let toastTimer = 0;
function toast(text, isErr) {
  let t = document.querySelector('.toast');
  if (!t) {
    t = h('div', {class: 'toast'});
    document.body.appendChild(t);
  }
  t.textContent = text;
  t.className = 'toast show' + (isErr ? ' err' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = 'toast'; }, isErr ? 4000 : 1800);
}

function hostOf(url) {
  try { return new URL(url).host; } catch (e) { return ''; }
}

// 方案 → 一行摘要
function planText(plan) {
  if (!plan) return '';
  if (plan.frameError) return '⚠ ' + plan.frameError;
  const frames = (plan.frames || []).map(f => f.dsl + (f.index ? ' #' + f.index : '')).join(' › ');
  let t;
  if (plan.kind === 'none') t = '⚠ ' + (plan.reason || '');
  else if (plan.kind === 'flat') t = plan.dsl;
  else if (plan.kind === 'index') t = plan.dsl + '  --index ' + plan.index + '（共 ' + plan.count + ' 个）';
  else {
    const a = plan.anchor;
    t = (a.index ? a.dsl + '  #' + a.index + '（共 ' + a.count + ' 个）' : a.dsl) + '  ' + plan.rel + '  ' + plan.dsl;
  }
  return (frames ? 'iframe：' + frames + ' › ' : '') + t;
}
const planBad = plan => !plan || plan.kind === 'none' || plan.frameError;

function script() {
  return G.generate(session || {steps: []}, {inline: !$('#opt-find').checked, comments: $('#opt-comments').checked});
}

// ———— 状态 ————
function apply(next) {
  if (next.session !== undefined) session = next.session;
  if (next.options) options = next.options;
  if (next.canRecord !== undefined) canRecord = next.canRecord;
  $('#opt-find').checked = !options.inline;
  $('#opt-comments').checked = !!options.comments;
  $('#opt-mask').checked = !!options.maskPasswords;
  render();
}

async function refresh() {
  if (tabId == null) return;
  const r = await send({type: 'hb:get-state', tabId: tabId});
  if (r && !r.error) apply(r);
}

// ———— 渲染 ————
function render() {
  const steps = (session && session.steps) || [];
  const rec = !!(session && session.recording);
  const btn = $('#btn-record');
  btn.classList.toggle('on', rec);
  btn.querySelector('span').textContent = rec ? '停止录制' : '开始录制';
  btn.disabled = !rec && !canRecord;

  const status = $('#status');
  status.textContent = '';
  if (!session) {
    status.appendChild(h('span', {text: canRecord ? '未录制' : '这个页面不能录制（浏览器内部页面）'}));
  } else {
    status.appendChild(h('b', {text: rec ? '● 录制中' : '○ 已停止'}));
    status.appendChild(document.createTextNode(' · ' + steps.length + ' 步 · ' + (hostOf(session.startUrl) || session.startUrl)));
  }
  $('#step-count').textContent = steps.length;
  $('#btn-pick').disabled = !canRecord;

  const list = $('#steps');
  list.textContent = '';
  if (!steps.length) {
    list.appendChild(h('div', {class: 'empty', text: rec ? '正在录制：像平时一样操作页面，点击 / 输入 / 选择会出现在这里。'
      : '还没有步骤。点「开始录制」，然后像平时一样操作页面。'}));
  }
  steps.forEach((st, i) => {
    const bad = planBad(st.plan) && ['click', 'input', 'press', 'select', 'capture'].indexOf(st.kind) !== -1;
    const row = h('div', {class: 'step' + (st.id === selectedId ? ' sel' : ''), 'data-kind': st.kind},
      h('span', {class: 'no', text: String(i + 1)}),
      h('div', {class: 'body'},
        h('div', {class: 'desc', text: G.describe(st)}),
        st.plan ? h('div', {class: 'loc' + (bad ? ' bad' : ''), text: planText(st.plan)}) : null),
      h('button', {class: 'x', text: '×', title: '删除这一步', onclick: e => { e.stopPropagation(); removeStep(st.id); }}));
    row.addEventListener('click', () => select(st.id === selectedId ? null : st.id));
    list.appendChild(row);
  });
  if (viewing === 'script') $('#script').textContent = script();
  renderEditor();
}

function select(id) {
  selectedId = id;
  render();
}

function removeStep(id) {
  send({type: 'hb:delete-step', tabId: tabId, id: id}).then(r => { if (r.session) apply({session: r.session}); });
}

function patchStep(id, patch) {
  return send({type: 'hb:update-step', tabId: tabId, id: id, patch: patch}).then(r => {
    if (r.session) apply({session: r.session});
    if (r.error) toast(r.error, true);
  });
}

// 选中步骤的编辑区
function renderEditor() {
  const box = $('#editor');
  box.textContent = '';
  const steps = (session && session.steps) || [];
  const st = steps.find(s => s.id === selectedId);
  if (!st) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.appendChild(h('h3', null, '第 ' + (steps.indexOf(st) + 1) + ' 步 · ' + (KIND_LABEL[st.kind] || st.kind),
    h('button', {class: 'x link', text: '收起', onclick: () => select(null)})));

  if (st.plan && st.plan.kind !== 'none') {
    const code = s => h('span', {class: 'mono small', style: 'user-select:text;word-break:break-all', text: s});
    box.appendChild(h('div', {class: 'field'}, h('label', {text: '命令'}), code(G.refLines(st.plan, false).join('  /  ') + '  →  ' + G.target(st.plan))));
    box.appendChild(h('div', {class: 'field'}, h('label', {text: '依据'}), code(st.plan.why || planText(st.plan))));
    const alts = st.plan.alts || [];
    if (alts.length) {
      box.appendChild(h('div', {class: 'field'}, h('label', {text: '换一个'}), h('div', {class: 'alts'},
        ...alts.map((a, i) => h('button', {class: 'mono small', text: a.why || planText(a), title: '换成这个定位方案',
          onclick: () => patchStep(st.id, {planAlt: i})})))));
    }
  }
  if ((st.notes || []).length) box.appendChild(h('div', {class: 'notes'}, ...st.notes.map(t => h('div', {text: t}))));

  const field = (label, control) => box.appendChild(h('div', {class: 'field'}, h('label', {text: label}), control));
  if (st.kind === 'note') {
    const ta = h('textarea', {placeholder: '写一句说明，会以 # 注释出现在脚本里'});
    ta.value = st.text || '';
    ta.addEventListener('change', () => patchStep(st.id, {text: ta.value}));
    field('说明', ta);
  } else if (st.kind === 'sleep') {
    const inp = h('input', {type: 'number', min: '0', step: '0.5', style: 'width:90px'});
    inp.value = st.seconds || 1;
    inp.addEventListener('change', () => patchStep(st.id, {seconds: Math.max(0, Number(inp.value) || 0)}));
    field('等几秒', inp);
  } else if (st.kind === 'input' && !st.secret) {
    const inp = h('input', {type: 'text', placeholder: '回放时输入的内容'});
    inp.value = st.value || '';
    inp.addEventListener('change', () => patchStep(st.id, {value: inp.value}));
    field('输入值', inp);
  } else if (st.kind === 'capture') {
    const cap = st.capture || {what: 'text'};
    const what = h('select', null, ...['text', 'attr', 'html'].map(x => h('option', {value: x, text: {text: '文本', attr: '属性', html: 'HTML'}[x]})));
    what.value = cap.what;
    what.addEventListener('change', () => patchStep(st.id, {capture: {what: what.value}}));
    field('抓取', what);
    if (cap.what === 'attr') {
      const attr = h('input', {type: 'text', placeholder: '属性名，如 href / value / data-id', style: 'max-width:220px'});
      attr.value = cap.attr || 'href';
      attr.addEventListener('change', () => patchStep(st.id, {capture: {attr: attr.value.trim()}}));
      field('属性名', attr);
    }
    if (st.plan && st.plan.all) {
      const all = h('input', {type: 'checkbox'});
      all.checked = !!cap.all;
      all.addEventListener('change', () => patchStep(st.id, {capture: {all: all.checked ? {n: st.plan.all.n} : null}}));
      field('范围', h('label', {class: 'radios'}, all, '全部同类（' + st.plan.all.n + ' 项，生成 loop 循环）'));
    }
  }
}

// ———— 抓取草稿 ————
function showDraft(p) {
  pick = p;
  $('#pick-desc').textContent = p.desc + '（<' + p.tag + '>）';
  $('#pick-plan').textContent = planText(p.plan);
  const sel = $('#pick-attr');
  sel.textContent = '';
  const names = (p.attrs || []).filter(n => n !== 'style').concat(EXTRA_ATTRS.filter(n => (p.attrs || []).indexOf(n) === -1));
  for (const n of names) sel.appendChild(h('option', {value: n, text: n}));
  sel.value = (p.attrs || []).indexOf('href') !== -1 ? 'href' : names[0];
  const hasAll = !!(p.plan && p.plan.all);
  $('#pick-all-label').classList.toggle('off', !hasAll);
  $('#pick-all-n').textContent = hasAll ? p.plan.all.n : 0;
  document.querySelector('input[name=pscope][value=one]').checked = true;
  document.querySelector('input[name=pwhat][value=text]').checked = true;
  $('#pick-draft').hidden = false;
}

function hideDraft() {
  pick = null;
  $('#pick-draft').hidden = true;
}

// ———— 事件 ————
$('#btn-record').addEventListener('click', async () => {
  if (tabId == null) return;
  const r = session && session.recording ? await send({type: 'hb:stop', tabId: tabId}) : await send({type: 'hb:start', tabId: tabId});
  if (r.error) toast(r.error, true);
  if (r.session !== undefined) apply({session: r.session});
  if (r.resumed) toast('继续录制（已有 ' + r.session.steps.length + ' 步）；要重新开始请先「清空」');
});

$('#btn-pick').addEventListener('click', async () => {
  const r = await send({type: 'hb:pick', tabId: tabId, on: true});
  if (r.error) toast(r.error, true);
  else toast('在页面上点选要抓取的元素（Esc 取消）');
});

$('#pick-cancel').addEventListener('click', hideDraft);
$('#pick-add').addEventListener('click', async () => {
  if (!pick) return;
  const what = document.querySelector('input[name=pwhat]:checked').value;
  const all = document.querySelector('input[name=pscope]:checked').value === 'all';
  const cap = {what: what};
  if (what === 'attr') cap.attr = $('#pick-attr').value;
  if (all && pick.plan.all) cap.all = {n: pick.plan.all.n};
  const r = await send({type: 'hb:add-step', tabId: tabId,
                        step: {kind: 'capture', desc: pick.desc, plan: pick.plan, capture: cap}});
  hideDraft();
  if (r.session) {
    apply({session: r.session});
    selectedId = r.id;
    viewing = 'script';
    render();
  }
});

for (const b of document.querySelectorAll('nav.tabs button')) {
  b.addEventListener('click', () => {
    viewing = b.dataset.tab;
    for (const x of document.querySelectorAll('nav.tabs button')) x.classList.toggle('on', x === b);
    for (const x of document.querySelectorAll('.tab')) x.classList.toggle('on', x.id === 'tab-' + viewing);
    render();
  });
}

for (const [id, key, opt] of [['#opt-comments', 'comments', 'comments'], ['#opt-find', 'inline', 'inline'], ['#opt-mask', 'maskPasswords', 'maskPasswords']]) {
  $(id).addEventListener('change', e => {
    options[opt] = opt === 'inline' ? !e.target.checked : e.target.checked;
    send({type: 'hb:set-options', options: {[opt]: options[opt]}});
    render();
  });
}

async function addSimple(step) {
  const r = await send({type: 'hb:add-step', tabId: tabId, step: step});
  if (r.error) toast(r.error, true);
  if (r.session) {
    apply({session: r.session});
    selectedId = r.id;
    render();
  }
}
$('#btn-note').addEventListener('click', () => addSimple({kind: 'note', text: '说明'}));
$('#btn-sleep').addEventListener('click', () => addSimple({kind: 'sleep', seconds: 1}));
$('#btn-clear').addEventListener('click', async () => {
  const r = await send({type: 'hb:clear', tabId: tabId});
  if (r.session) {
    selectedId = null;
    apply({session: r.session});
  }
});

$('#btn-copy').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(script());
    toast('脚本已复制');
  } catch (e) {
    toast('复制失败：' + e.message, true);
  }
});

$('#btn-save').addEventListener('click', async () => {
  const text = script();
  const base = String((session && session.startTitle) || 'hb-record').replace(/[\\/:*?"<>|#^[\]\s]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40) || 'hb-record';
  const d = new Date();
  const pad = x => (x < 10 ? '0' : '') + x;
  const name = base + '-' + d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()) + '-' + pad(d.getHours()) + pad(d.getMinutes()) + '.hb';
  const url = URL.createObjectURL(new Blob([text], {type: 'text/plain'}));
  try {
    await chrome.downloads.download({url: url, filename: name, saveAs: true});
  } catch (e) {
    toast('保存失败：' + e.message, true);
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  }
});

chrome.runtime.onMessage.addListener(msg => {
  if (!msg || msg.tabId !== tabId) return;
  if (msg.type === 'hb:session') apply({session: msg.session});
  else if (msg.type === 'hb:picked') showDraft(msg.pick);
  else if (msg.type === 'hb:pick-cancelled') hideDraft();
});

chrome.tabs.onActivated.addListener(info => {
  tabId = info.tabId;
  selectedId = null;
  hideDraft();
  refresh();
});
chrome.tabs.onUpdated.addListener((tid, change) => {
  if (tid === tabId && (change.status === 'loading' || change.title)) refresh();
});

// ———— 启动 ————
(async () => {
  const tabs = await chrome.tabs.query({active: true, currentWindow: true});
  tabId = tabs[0] ? tabs[0].id : null;
  await refresh();
})();
