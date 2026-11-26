/* locator.js — 为元素生成 happybrowser `?` DSL 定位符（extension/lib）
 *
 * 与 hb 约定一致的几件事（改这里前先对照 src/happybrowser）：
 * - text / owntext 的语义逐条对齐 _locate/query.py 的 _EleView + HB_TEXT_JS；
 *   hbText 原样拷贝自 happybrowser/_functions/web.py，tests/test_recorder.py 会比对。
 * - 生成的条件只用 = * = ^= $= ~= 六种运算（不用正则 /=，避免 Python/JS 正则方言差）。
 * - 文本片段一律取「不含空白」的连续片段：它同时是 hbText 输出、原始文本节点、
 *   XPath string-value 三者的子串，纯 XPath 下推与本地精筛结果才一致。
 * - 页面级唯一性只数本文件档的 light DOM：hb 的 DOM.performSearch 不穿 open shadow，
 *   shadow 内的元素直接标记为无法定位。它会把同进程 iframe 文档里的匹配追加在主文档之后
 *   （深度优先），对主文档里的目标没有影响（--index 不会错位）；iframe 里的目标走 @1 >> 链，
 *   在那个 frame 内求值，和这里“只看本文档”一致。
 *
 * plan（定位方案）三种形态，对应 .hb 里的写法：
 *   flat      find "dsl"                       → @1
 *   index     find "dsl" --index n             → @1        （第 n 个匹配，1 起）
 *   anchored  find "anchor" [--index n]        → @1 >> dsl / @1 > dsl
 *             （父级锚点：目标自身认不出来时，选一个认得出来的祖先做锚）
 */
(function (root, factory) {
  const api = factory();
  root.HbLocator = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // ───────── hbText：与 happybrowser._functions.web.HB_TEXT_JS 逐字一致 ─────────
  function hbText(root) {
    const NOWRAP = new Set(['br','sub','sup','em','strong','a','font','b','span','s','i','del','ins','img','td',
        'th','abbr','bdi','bdo','cite','code','data','dfn','kbd','mark','q','rp','rt','ruby','samp','small',
        'time','u','var','wbr','button','slot','content']);
    const WRAP_AFTER = new Set(['p','div','h1','h2','h3','h4','h5','h6','ol','li','blockquote','header',
        'footer','address','article','aside','main','nav','section','figcaption','summary']);
    const NO_TEXT = new Set(['script','style','video','audio','iframe','embed','noscript','canvas','template']);
    const TAB = new Set(['td','th']);
    function fmt(s) {return s.replace(/\r\n/g, ' ').replace(/\n/g, ' ').replace(/ {2,}/g, ' ');}
    function blank(s) {return s.replace(/[ \n\t\r]/g, '') === '';}
    function walk(ele, pre) {
        const tag = ele.tagName ? ele.tagName.toLowerCase() : '';
        if (tag === 'br') return [true];
        if (!pre && tag === 'pre') pre = true;
        const out = [];
        if (NO_TEXT.has(tag) && !pre) return out;
        let prevTag = '';
        for (const n of ele.childNodes) {
            if (n.nodeType === 3) {                       // 文本节点
                if (pre) out.push(n.textContent);
                else if (!blank(n.textContent)) out.push(fmt(n.textContent));
            } else if (n.nodeType === 1) {                // 元素节点
                const t = n.tagName.toLowerCase();
                if (!NOWRAP.has(t) && out.length && out[out.length - 1] !== '\n') out.push('\n');
                if (TAB.has(t) && TAB.has(prevTag)) out.push('\t');
                out.push(...walk(n, pre));
                prevTag = t;
            }
        }
        if (WRAP_AFTER.has(tag) && out.length && out[out.length - 1] !== '\n'
                && out[out.length - 1] !== true) out.push('\n');
        return out;
    }
    const tag = root.tagName ? root.tagName.toLowerCase() : '';
    if (NO_TEXT.has(tag)) return root.innerText;
    let parts = walk(root, false);
    if (parts.length && parts[parts.length - 1] === '\n') parts.pop();
    const r = [];
    for (let i = 0; i < parts.length - 1; i++) {
        const a = parts[i], b = parts[i + 1];
        if (a === true) {r.push('\n'); continue;}
        if (b === true) {r.push(a); continue;}
        r.push(a.endsWith(' ') && b.startsWith(' ') ? a.slice(0, -1) : a);
    }
    if (parts.length) r.push(parts[parts.length - 1] === true ? '\n' : parts[parts.length - 1]);
    return r.join('').trim();
  }

  const WS = /\s+/;
  const TAG_BARE = /^[A-Za-z][A-Za-z0-9-]*$/;           // hb _TAG_BARE
  const ATTR_NAME = /^[A-Za-z_][A-Za-z0-9_.:\-]*$/;      // hb _WORD（@ 后面的属性名）
  const CSS_IDENT = /^[A-Za-z0-9_-]+$/;                  // hb _CSS_IDENT（#id / .class 简写）
  const HAS_LETTER = /[\p{L}\p{N}]/u;

  // 候选上限 / 扣分
  const MAX_INDEX_COUNT = 200;     // 超过这么多匹配就不敢用 --index
  const INDEX_PENALTY = 32;        // 靠序号定位的扣分
  const MAX_ANCHOR_DEPTH = 6;      // 往上找父级锚点的层数
  const TIME_BUDGET = 150;         // 单次 best() 的毫秒预算，超了就用已得到的最好结果
  // 父级锚点方案的评分 = 相对定位自身的评分 + 锚点带来的有限加分：
  // 锚点是为了“缩小范围补到唯一”，不该让一个弱条件显得很强
  const ANCHOR_BONUS = 0.2;
  const ANCHOR_BONUS_MAX = 20;
  const TAG_BONUS = 4;             // 组合里带上标签名（div && .x 比 .x 明确）的小加分
  const WRAPPER_SHARE = 0.6;       // 祖先里装了大半个页面的（main / #app 根）不配当锚点
  const STOP_TAGS = new Set(['html', 'body']);

  // 语义化标签（单独出现就有定位价值）与纯容器标签
  const SEMANTIC_TAGS = new Set(('nav,header,footer,main,aside,section,article,form,dialog,table,thead,tbody,ul,ol,li,' +
    'tr,td,th,button,a,input,select,textarea,label,h1,h2,h3,h4,h5,h6,summary,details,iframe,img,option,fieldset,' +
    'search,address,figure,video,canvas,map,area,menu,del,ins,time,mark,progress,meter').split(','));
  const GENERIC_TAGS = new Set('div,span,p,i,b,small,strong,em,u,s,dl,dd,dt,tt,code,pre,blockquote,font,nobr'.split(','));

  // 状态类 class（会变）：按 - _ 切段后命中任一段 → 这个 class 不稳定
  const STATE_SEGMENTS = new Set(('active,selected,checked,focus,focused,hover,disabled,enabled,open,opened,closed,' +
    'collapse,collapsed,expand,expanded,current,visible,hidden,show,shown,hide,loading,loaded,dirty,touched,pristine,' +
    'valid,invalid,pending,error,success,warning,highlight,highlighted,pressed,dragging,dragged,sortable,animate,' +
    'animated,transition,entering,leaving,enter,leave,ripple,empty,indeterminate,failed,done,complete,completed,' +
    'processing,busy,sticky,starred,liked,loved,followed,following,muted,blocked,pinned,unread,playing,paused,' +
    'stopped,fullscreen,scrolled,stuck,affix,affixed,toggle,toggled,validated,activated,deactivated,isolated,' +
    'interactable,tabbable,clickable,linkified,visited,hasfocus').split(','));
  const STATE_PREFIX = /^(?:ng-|ui-state|svelte-|cdk-|v-ripple|aria-|was-)/i;
  // 生成物：CSS-in-JS / CSS Modules / JSS 的哈希 class
  const GENERATED_CLASS = [
    /^(?:css|jsx|scss|less|emotion|sc|mui|chakra|radix|jss)[-_][a-z0-9]*\d[a-z0-9]*$/i,   // css-1q2w3e / sc-12ab3c
    /^sc-[a-z0-9]{6,}$/i,                                                                  // sc-bdVaJa
    /-(?:sc|css|jsx|emotion)-[a-z0-9]{5,}$/i,                                              // Button__Root-sc-1q2w3e
    /^_?[a-z]+[-_](?:styles?|modules?|cls|classname)[-__]/i,                                // styles_btn__2Qx
    /^jss\d+$/i,
    /^(?:ngcontent|nghost)[-_]/i,
    /(?:^|[-_])(?:ngcontent|nghost|hydrated)(?:[-_]|$)/i,
  ];
  const GENERATED_ID = /^(?:ui-id-\d+|ember\d+|radix-|mui-\d|headlessui-|ext-gen-|ext-comp-|node-\d+$|elem_|__|:)/i;

  // 属性优先级：测试钩子 > name > 文本类 > 可见标签属性 > 其它
  const TEST_ID_ATTRS = new Set(('data-testid,data-test-id,data-test,data-qa,data-cy,data-automation-id,' +
    'data-qa-id,data-unit-testing-id,data-aut-id').split(','));
  const VOLATILE_DATA = new Set(('data-index,data-idx,data-state,data-status,data-loaded,data-active,data-disabled,' +
    'data-open,data-expanded,data-selected,data-visible,data-count,data-pos,data-position,data-offset,data-dirty,' +
    'data-pristine,data-valid,data-invalid,data-pending,data-rendered,data-ready,data-scroll,data-top,data-left,' +
    'data-width,data-height,data-x,data-y').split(','));

  // ───────── 工具 ─────────
  const now = (typeof performance !== 'undefined' && performance.now) ? () => performance.now() : () => Date.now();

  function tagOf(el) { return el.localName ? el.localName.toLowerCase() : ''; }

  // DSL 值：统一双引号 + 转义 \ 和 "（hb _quoted 只认这两种转义）
  function dslValue(v) { return '"' + v.replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"'; }

  function condDsl(c) {
    if (c.k === 'tag') return TAG_BARE.test(c.v) ? c.v : 'tag=' + dslValue(c.v);
    if (c.k === 'attr') {
      if (c.op === '=' && c.n === 'id' && CSS_IDENT.test(c.v)) return '#' + c.v;
      if (c.op === '~=' && c.n === 'class' && CSS_IDENT.test(c.v)) return '.' + c.v;
      const ref = ATTR_NAME.test(c.n) ? '@' + c.n : '@' + dslValue(c.n);
      return ref + c.op + dslValue(c.v);
    }
    return c.k + c.op + dslValue(c.v);
  }

  function dslOf(conds) { return conds.map(condDsl).join(' && '); }

  // ───────── 求值（对齐 query.py 的 _match_node / _EleView）─────────
  function deepText(el, cache) {
    let t = cache && cache.get(el);
    if (t === undefined) {
      t = hbText(el).replace(/\u00a0/g, ' ');       // format_html
      if (cache) cache.set(el, t);
    }
    return t;
  }

  function matchCond(el, c, cache) {
    switch (c.k) {
      case 'tag':
        return tagOf(el) === c.v;
      case 'attr': {
        const a = el.getAttribute(c.n);
        if (a === null) return false;
        if (c.op === '=') return a === c.v;
        if (c.op === '*=') return a.includes(c.v);
        if (c.op === '^=') return a.startsWith(c.v);
        if (c.op === '$=') return a.endsWith(c.v);
        return a.split(WS).indexOf(c.v) !== -1;       // ~= 整词
      }
      case 'text':
        return deepText(el, cache).includes(c.v);
      default: {                                     // owntext：任一直属文本节点包含
        for (let n = el.firstChild; n; n = n.nextSibling) {
          if (n.nodeType === 3 && n.data.includes(c.v)) return true;
        }
        return false;
      }
    }
  }

  // 让便宜的判断先跑
  const COST = {tag: 0, attr: 1, owntext: 2, text: 3};

  function matchAll(el, conds, cache) {
    for (let i = 0; i < conds.length; i++) {
      if (!matchCond(el, conds[i], cache)) return false;
    }
    return true;
  }

  let ignoreFn = null;
  function setIgnore(fn) { ignoreFn = fn; }

  // scope 内按文档序求值；hb 页面级是 //*（不含 scope 自己），元素级是 .//*，这里一致
  function evalSet(conds, scope, cache) {
    let tag = null;
    for (const c of conds) if (c.k === 'tag') { tag = c.v; break; }
    const pool = scope.getElementsByTagName(tag || '*');
    const rest = conds.filter(c => c.k !== 'tag').sort((a, b) => COST[a.k] - COST[b.k]);
    const out = [];
    for (let i = 0; i < pool.length; i++) {
      const el = pool[i];
      if (ignoreFn && ignoreFn(el)) continue;
      if (matchAll(el, rest, cache)) out.push(el);
    }
    return out;
  }

  function evalIn(conds, list, cache) {
    const rest = conds.slice().sort((a, b) => COST[a.k] - COST[b.k]);
    const out = [];
    for (let i = 0; i < list.length; i++) {
      if (matchAll(list[i], rest, cache)) out.push(list[i]);
    }
    return out;
  }

  // ───────── 稳定性判断 ─────────
  function isStableClass(tok) {
    if (!tok || tok.length > 60 || /[:[\](/\\%!&*+,<>?~^|$]/.test(tok)) return false;
    const chunks = tok.split(/[-_]/);
    for (let i = 0; i < chunks.length; i++) {
      const ch = chunks[i];
      if (!ch) continue;
      if (STATE_SEGMENTS.has(ch.toLowerCase())) return false;
      if (/\d[a-z]/i.test(ch) && ch.length >= 5) return false;        // 1q2w3e / 3xKFG
    }
    if (STATE_PREFIX.test(tok)) return false;
    const last = chunks[chunks.length - 1];
    if (/^\d{3,}$/.test(last)) return false;                           // xxx-1234（序号/哈希）
    for (const re of GENERATED_CLASS) if (re.test(tok)) return false;
    return true;
  }

  function stableClasses(el) {
    const cls = el.getAttribute('class');
    if (!cls) return [];
    const out = [];
    const seen = new Set();
    for (const tok of cls.split(WS)) {
      if (!tok || seen.has(tok) || !isStableClass(tok)) continue;
      seen.add(tok);
      out.push(tok);
    }
    // 更“具体”的排前面：长一点、带连字符的优先
    out.sort((a, b) => classScore(b) - classScore(a) || a.localeCompare(b));
    return out;
  }

  function classScore(tok) {
    const hyphens = (tok.match(/-/g) || []).length;
    return 50 + Math.min(8, tok.length / 3) + 2 * Math.min(3, hyphens);
  }

  function idScore(id) {
    return /\d/.test(id) ? 115 : 135;
  }

  function isStableId(id) {
    return !!id && id.length <= 80 && !/[\s:]/.test(id) && !GENERATED_ID.test(id) &&
      !/^\d+$/.test(id) && !/\d{6,}/.test(id);
  }

  function usableValue(v, max) {
    return !!v && v.length <= (max || 80) && !/[\r\n]/.test(v) && v.indexOf('${') === -1 &&
      !/^[[{]/.test(v.trim());
  }

  // 文本片段：取最长的「无空白、无引号」连续片段（仍是原文子串），掐头去尾的标点，至少含一个字母/数字/汉字
  function snippetOf(text, maxLen) {
    let best = '';
    const re = /[^\s'"`]+/g;
    let m;
    while ((m = re.exec(text))) {
      const s = m[0].replace(/^[\p{P}\p{S}]+|[\p{P}\p{S}]+$/gu, '');
      if (s.length > best.length && HAS_LETTER.test(s)) best = s;
    }
    return best ? best.slice(0, maxLen) : '';
  }

  // 后代文本节点里最长（去空白后）的片段；跳过 script/style 等不出文本的元素（与 hbText 的 NO_TEXT 一致）
  function deepSnippetOf(el, cache) {
    let best = '';
    const walker = el.ownerDocument.createTreeWalker(el, 4 /* SHOW_TEXT */);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (!n.data || !HAS_LETTER.test(n.data)) continue;
      if (n.parentElement && n.parentElement.closest('script,style,video,audio,iframe,embed,noscript,canvas,template')) continue;
      const s = snippetOf(n.data, 24);
      if (s.length > best.length) best = s;
    }
    return best;
  }

  function looksDynamic(s) {
    if (!/\p{L}/u.test(s)) return true;                         // 纯数字/符号：数量、价格、序号
    return /\d{2,}[.,:/-]\d{2,}/.test(s) || /\d{3,}/.test(s);   // 日期、价格、长数字
  }

  // ───────── 候选条件 ─────────
  function attrAtom(el, name, op, score, maxLen) {
    const v = el.getAttribute(name);
    if (!usableValue(v, maxLen)) return null;
    if (op === '^=') {
      if (/^(?:javascript|mailto|tel|data|blob):/i.test(v)) return null;
      const head = v.split(/[?#]/)[0];
      if (head.length < 2 || head === '/') return null;
      return {cond: {k: 'attr', n: name, op: v === head ? '=' : '^=', v: head},
              score: v === head ? score : score - 8};
    }
    return {cond: {k: 'attr', n: name, op: op, v: v}, score: score};
  }

  function buildAtoms(el, cache) {
    const tag = tagOf(el);
    const A = {tag: {cond: {k: 'tag', v: tag}, score: SEMANTIC_TAGS.has(tag) ? 24 : (GENERIC_TAGS.has(tag) ? 10 : 15)}};

    if (isStableId(el.id)) A.id = {cond: {k: 'attr', n: 'id', op: '=', v: el.id}, score: idScore(el.id)};

    let data = null;
    for (const a of el.attributes) {
      const n = a.name;
      if (n.startsWith('data-')) {
        if (VOLATILE_DATA.has(n) || /^data-v-/.test(n) || !usableValue(a.value, 80)) continue;
        const score = TEST_ID_ATTRS.has(n) ? 128 : 96;
        if (!data || score > data.score) data = {cond: {k: 'attr', n: n, op: '=', v: a.value}, score: score};
      }
    }
    if (data) A.data = data;

    const name = el.getAttribute('name');
    if (usableValue(name, 60)) A.name = {cond: {k: 'attr', n: 'name', op: '=', v: name}, score: 92};

    const classes = stableClasses(el);
    A.classes = classes.slice(0, 3).map(c => ({cond: {k: 'attr', n: 'class', op: '~=', v: c}, score: classScore(c)}));

    // 文本：先直属文本（标签文字通常在直属节点里），再深层文本
    let own = '';
    for (let n = el.firstChild; n; n = n.nextSibling) {
      if (n.nodeType === 3) {
        const s = snippetOf(n.data, 24);
        if (s.length > own.length) own = s;
      }
    }
    if (own.length >= 2 || (own.length === 1 && /[^\x00-\x7f]/.test(own))) {
      A.own = {cond: {k: 'owntext', op: '*=', v: own}, score: looksDynamic(own) ? 24 : 48};
    }
    // 深层文本：片段必须落在“单个文本节点”里。hb 回放时 text*= 走 XPath string-value，
    // 相邻子元素之间的空白文本节点还在；hbText（= ele.text）却会把它们去掉再拼接。
    // 从拼接结果里截片段可能跨元素 → 录制时唯一、回放时 0 匹配。
    const deepSnippet = deepSnippetOf(el, cache);
    if (deepSnippet && deepSnippet !== own && (deepSnippet.length >= 2 || /[^\x00-\x7f]/.test(deepSnippet))) {
      let score = looksDynamic(deepSnippet) ? 20 : 44;
      if (deepText(el, cache).length > 60) score -= 8;     // 长文本：多半是内容而不是标签
      A.text = {cond: {k: 'text', op: '*=', v: deepSnippet}, score: score};
    }

    A.placeholder = attrAtom(el, 'placeholder', '=', 56, 60);
    A.ariaLabel = attrAtom(el, 'aria-label', '=', 60, 60);
    A.labelledby = attrAtom(el, 'aria-labelledby', '=', 56, 80) || attrAtom(el, 'aria-describedby', '=', 50, 80);
    A.title = attrAtom(el, 'title', '=', 46, 60);
    A.alt = attrAtom(el, 'alt', '=', 46, 60);
    A.for = attrAtom(el, 'for', '=', 80, 80);
    const type = el.getAttribute('type');
    if (tag === 'input' && /^(?:submit|button|reset)$/.test(type || '')) A.value = attrAtom(el, 'value', '=', 48, 40);
    if (tag === 'option') A.value = attrAtom(el, 'value', '=', 40, 60);
    if (tag === 'a' || tag === 'area') A.href = attrAtom(el, 'href', '^=', 54, 120);
    if (tag === 'form') A.action = attrAtom(el, 'action', '=', 50, 120);
    if (tag === 'input' || tag === 'button' || tag === 'ul' || tag === 'ol') {
      A.type = attrAtom(el, 'type', '=', 20, 30) || null;
    }
    A.role = attrAtom(el, 'role', '=', 18, 40);
    return A;
  }

  // 条件组合模板（出现的键都存在才成立）；按评分从高到低试
  const SETS = [
    ['id'], ['data'], ['name'], ['tag', 'name'], ['for'],
    ['classes0'], ['tag', 'classes0'], ['tag', 'classes0', 'classes1'], ['classes0', 'classes1'],
    ['tag', 'classes0', 'classes1', 'classes2'],
    ['tag', 'ariaLabel'], ['ariaLabel'], ['tag', 'placeholder'], ['placeholder'], ['tag', 'labelledby'], ['labelledby'],
    ['tag', 'own'], ['own'], ['classes0', 'own'], ['tag', 'classes0', 'own'], ['tag', 'type', 'own'],
    ['tag', 'role', 'own'], ['tag', 'ariaLabel', 'own'], ['tag', 'placeholder', 'own'],
    ['tag', 'text'], ['text'], ['tag', 'classes0', 'text'], ['classes0', 'text'],
    ['tag', 'href'], ['href'], ['tag', 'classes0', 'href'], ['tag', 'action'], ['action'],
    ['tag', 'title'], ['title'], ['tag', 'alt'], ['alt'], ['tag', 'value'], ['value'],
    ['tag', 'classes0', 'placeholder'], ['classes0', 'placeholder'],
    ['tag', 'type'], ['tag', 'role'], ['tag', 'classes0', 'type'], ['tag', 'classes0', 'role'],
    ['tag'],
  ];

  function buildSets(A) {
    const sets = [];
    for (const keys of SETS) {
      const conds = [];
      let score = 0;
      let ok = true;
      for (const key of keys) {
        const m = /^classes(\d)$/.exec(key);
        const atom = m ? (A.classes && A.classes[+m[1]]) : A[key];
        if (!atom) { ok = false; break; }
        conds.push(atom.cond);
        score += atom.score;
      }
      if (!ok) continue;
      // 组合分 = 最强条件的分（+ 带标签的小加分）− 每个附加条件的扣分：
      // 附加条件只为“补到唯一”，不为加分；所以一个组合永远排在自己的子集后面，
      // 先试简单的，简单的不唯一才轮到复杂的
      const hasTag = keys.indexOf('tag') !== -1;
      score = Math.max.apply(null, keys.map(k => {
        const m = /^classes(\d)$/.exec(k);
        return (m ? A.classes[+m[1]] : A[k]).score;
      })) + (hasTag && keys.length > 1 ? TAG_BONUS : 0) - 3 * (keys.length - 1 - (hasTag ? 1 : 0));
      sets.push({conds: conds, dsl: dslOf(conds), score: score});
    }
    sets.sort((a, b) => b.score - a.score);
    return sets;
  }

  // ───────── 搜索 ─────────
  // 返回 {unique, indexed}：评分最高的唯一组合 / 评分最高的“数量可控”组合（带序号）
  function search(el, scope, ctx, wantIndexed) {
    const sets = buildSets(buildAtoms(el, ctx.cache));
    let unique = null;
    let indexed = null;
    for (const s of sets) {
      const m = evalSet(s.conds, scope, ctx.cache);
      if (m.length === 1 && m[0] === el) {
        if (!unique) unique = {kind: 'flat', dsl: s.dsl, conds: s.conds, score: s.score};   // 评分高的先试，先到先得
        if (!wantIndexed) break;
      } else if (wantIndexed && !indexed && m.length > 1 && m.length <= MAX_INDEX_COUNT) {
        const i = m.indexOf(el);
        if (i !== -1) {
          indexed = {kind: 'index', dsl: s.dsl, conds: s.conds, score: s.score - INDEX_PENALTY - Math.min(15, m.length / 8),
                     index: i + 1, count: m.length, generic: s.conds.every(c => c.k === 'tag')};   // 只剩标签名的太泛
        }
      }
      if (unique && indexed) break;
      if (now() > ctx.deadline) break;
    }
    return {unique: unique, indexed: indexed};
  }

  function anchorBonus(anchor, penalty) {
    return Math.min(ANCHOR_BONUS_MAX, ANCHOR_BONUS * Math.max(0, anchor.score - penalty));
  }

  // 父级锚点：从最近的祖先往上找，认得出（唯一或序号可控）的祖先做锚，目标在锚里唯一
  function anchored(el, ctx) {
    let a = el.parentElement;
    const out = [];
    for (let depth = 0; a && depth < MAX_ANCHOR_DEPTH && !STOP_TAGS.has(tagOf(a)); depth++, a = a.parentElement) {
      if (ignoreFn && ignoreFn(a)) continue;
      if (now() > ctx.deadline) break;
      // 装了大半个页面的祖先（<main id=content>、#app 之类）当锚点没有意义，等于没缩小范围
      if (a.getElementsByTagName('*').length > ctx.total * WRAPPER_SHARE) continue;
      const sa = search(a, a.ownerDocument, ctx, true);
      const anchor = sa.unique || sa.indexed;
      if (!anchor) continue;
      const penalty = /text/.test(anchor.dsl) ? 10 : 0;       // 靠文本认出来的容器不够稳
      const sr = search(el, a, ctx, false);
      if (!sr.unique) {
        // >> 不唯一且目标是直接子元素：试试 >（嵌套列表、同名后代常见的坑）
        if (el.parentElement === a) {
          const sets = buildSets(buildAtoms(el, ctx.cache));
          for (const s of sets) {
            const m = evalIn(s.conds, a.children, ctx.cache);
            if (m.length === 1 && m[0] === el) {
              out.push({kind: 'anchored', anchor: anchor, rel: '>', dsl: s.dsl, conds: s.conds,
                        score: s.score + anchorBonus(anchor, penalty)});
              break;
            }
          }
        }
        continue;
      }
      out.push({kind: 'anchored', anchor: anchor, rel: '>>', dsl: sr.unique.dsl, conds: sr.unique.conds,
                score: sr.unique.score + anchorBonus(anchor, penalty)});
    }
    if (!out.length) return null;
    out.sort((x, y) => y.score - x.score);
    return out[0];
  }

  // 给方案补一句人话说明
  function explain(plan) {
    if (plan.kind === 'flat') return '页面内唯一：' + plan.dsl;
    if (plan.kind === 'index') return '第 ' + plan.index + ' 个匹配（共 ' + plan.count + ' 个）：' + plan.dsl;
    const a = plan.anchor;
    const head = a.index ? '第 ' + a.index + ' 个「' + a.dsl + '」（共 ' + a.count + ' 个）'
                         : '「' + a.dsl + '」';
    return '父级锚点 ' + head + ' 内的 ' + plan.dsl;
  }

  function inLightDom(el) {
    const r = el.getRootNode ? el.getRootNode() : null;
    return !!r && r.nodeType === 9;            // DOCUMENT_NODE；shadow root 是 11
  }

  // ───────── 对外 ─────────
  // 不靠 --index：回放时比“第 n 个匹配”稳（条目增删不会错位）
  function solid(plan) {
    return (plan.kind === 'flat' || (plan.kind === 'anchored' && !plan.anchor.index)) ? 1 : 0;
  }

  // best(el) → plan；plan.alts 是备选方案（面板里可切换），plan.all 是“全部同类”抓取用的描述
  function best(el, options) {
    options = options || {};
    if (!el || el.nodeType !== 1) return {kind: 'none', reason: '目标不是元素'};
    const tag = tagOf(el);
    if (STOP_TAGS.has(tag)) {
      return {kind: 'flat', dsl: tag, conds: [{k: 'tag', v: tag}], score: 24, why: '整页：' + tag, alts: []};
    }
    if (!inLightDom(el)) {
      return {kind: 'none', reason: '元素在 shadow DOM 内，hb 的页面级查找进不去', alts: []};
    }
    const doc = el.ownerDocument;
    const ctx = {cache: options.cache || new Map(), deadline: now() + (options.budget || TIME_BUDGET),
                 total: Math.max(1, (doc.body ? doc.body.getElementsByTagName('*').length : 0))};
    if (options.ignore) setIgnore(options.ignore);
    const flat = search(el, doc, ctx, true);
    // 目标自己就能唯一认出 → 不劳烦父级；认不出（同类太多）→ 请一个认得出的祖先当锚点
    const an = flat.unique ? null : anchored(el, ctx);
    const cands = [flat.unique, flat.indexed, an].filter(Boolean);
    if (!cands.length) {
      return {kind: 'none', reason: '页面上没有能区分它的属性/文本（同类太多）', alts: []};
    }
    // 不靠序号的方案优先于靠序号的；其余比评分
    cands.sort((x, y) => (solid(y) - solid(x)) || (y.score - x.score));
    const chosen = cands[0];
    const alts = cands.slice(1, 4);
    // “全部同类”：带序号的那一层就是“同款”；只剩个标签名的（a × 2）不算同款，不给批量抓取
    let all = null;
    const group = p => (p && !p.generic) ? {dsl: p.dsl, n: p.count, rel: null, conds: p.conds} : null;
    if (chosen.kind === 'index') all = group(chosen);
    else if (chosen.kind === 'anchored' && chosen.anchor.index) {
      all = {dsl: chosen.anchor.dsl, n: chosen.anchor.count, rel: chosen.dsl, relOp: chosen.rel,
             conds: chosen.anchor.conds, relConds: chosen.conds};
    } else if (flat.indexed) {
      all = group(flat.indexed);
    }
    const plan = Object.assign({}, chosen, {why: explain(chosen), alts: alts.map(x => Object.assign({}, x, {why: explain(x)}))});
    if (all) plan.all = all;
    return plan;
  }

  // 方案 → 元素（高亮、校验用）；找不到返回 null
  function resolve(plan, doc) {
    doc = doc || document;
    const cache = new Map();
    if (plan.kind === 'none' || !plan.conds) return null;
    if (plan.kind === 'anchored') {
      if (!plan.anchor.conds) return null;
      const m = evalSet(plan.anchor.conds, doc, cache);
      const base = plan.anchor.index ? m[plan.anchor.index - 1] : m[0];
      if (!base) return null;
      const list = plan.rel === '>' ? evalIn(plan.conds, base.children, cache) : evalSet(plan.conds, base, cache);
      return list[0] || null;
    }
    const m = evalSet(plan.conds, doc, cache);
    return plan.kind === 'index' ? (m[plan.index - 1] || null) : (m.length === 1 ? m[0] : null);
  }

  function resolveAll(all, doc) {
    doc = doc || document;
    if (!all || !all.conds) return [];
    const cache = new Map();
    const m = evalSet(all.conds, doc, cache);
    if (!all.rel) return m;
    const out = [];
    for (const base of m) {
      const list = all.relOp === '>' ? evalIn(all.relConds, base.children, cache)
                                     : evalIn(all.relConds, base.getElementsByTagName('*'), cache);
      if (list.length) out.push(list[0]);
    }
    return out;
  }

  // 换用第 i 个备选方案（侧边栏里“换一个定位符”）：带着原来的 iframe 链，重算“全部同类”
  function promoteAlt(plan, i) {
    const alt = plan.alts && plan.alts[i];
    if (!alt) return plan;
    const others = [plan].concat(plan.alts.filter((_, k) => k !== i));
    const p = Object.assign({}, alt, {frames: plan.frames, why: alt.why || explain(alt),
                                      alts: others.map(x => Object.assign({}, x, {why: x.why || explain(x)}))});
    let all = null;
    if (p.kind === 'index' && !p.generic) all = {dsl: p.dsl, n: p.count, rel: null, conds: p.conds};
    else if (p.kind === 'anchored' && p.anchor.index) {
      all = {dsl: p.anchor.dsl, n: p.anchor.count, rel: p.dsl, relOp: p.rel, conds: p.anchor.conds, relConds: p.conds};
    }
    if (all) p.all = all;
    return p;
  }

  function describe(el) {
    if (!el || el.nodeType !== 1) return '?';
    const tag = tagOf(el);
    if (STOP_TAGS.has(tag)) return tag;              // body/html：拿页面第一行文字当标签没有意义
    let label = '';
    for (let n = el.firstChild; n; n = n.nextSibling) {
      if (n.nodeType === 3 && n.data.trim()) { label = n.data.trim(); break; }
    }
    if (!label) label = (deepText(el) || '').split('\n')[0].trim();
    if (!label && el.labels && el.labels.length) label = el.labels[0].textContent;     // 表单控件关联的 <label>
    if (!label) {
      label = el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('alt') ||
        el.getAttribute('title') || '';
    }
    label = label.replace(/\s+/g, ' ').slice(0, 16);
    const id = isStableId(el.id) ? '#' + el.id : '';
    if (label) return tag + id + '「' + label + '」';
    const cls = stableClasses(el)[0];
    return cls ? tag + id + '.' + cls : (id ? tag + id : tag);
  }

  return {
    hbText: hbText,
    best: best,
    promoteAlt: promoteAlt,
    resolve: resolve,
    resolveAll: resolveAll,
    describe: describe,
    dslOf: dslOf,
    condDsl: condDsl,
    matchCond: matchCond,
    isStableClass: isStableClass,
    isStableId: isStableId,
    setIgnore: setIgnore,
    snippetOf: snippetOf,
  };
});
