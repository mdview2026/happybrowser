/* hbgen.js — 录制步骤 → .hb 脚本（extension/lib）
 *
 * 每一步默认写成「find 定位 → 用 @1 操作」两行，而不是把定位符内联进 click：
 * 回放报错时分得清是没找到还是操作失败，随手插一行 hl 就能看高亮。
 * 同一个元素上的连续操作（输入 → 回车）共用上一个 find；点击/选择/回车/跳转之后
 * 页面可能已经变了，引用一律作废重找。
 *
 * .hb 分词规则（happybrowser/hbscript.py）在这里用到的几条：
 * - 引号包住的 token 里一律原样，所以定位符统一包一层引号；定位符自身含 " 时外层用 '。
 * - 行尾「空格 # 空格」是注释：步骤说明直接挂在命令行尾，不单独占行。
 */
(function (root, factory) {
  const api = factory();
  root.HbScriptGen = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const DEFAULTS = {inline: false, comments: true};
  const WHAT = {text: '文本', attr: '属性', html: 'HTML'};
  const MAX_REFS = 50;        // hb find 最多缓存 50 个引用（cli._MAX_REFS）

  function oneLine(s) { return String(s == null ? '' : s).replace(/[\r\n\t]+/g, ' ').trim(); }

  // .hb token：定位符/值里有 " 就外层用 '（hb 引号内不转义）；两种引号都有时舍弃 "
  function q(s) {
    s = oneLine(s);
    if (s.indexOf('"') === -1) return '"' + s + '"';
    if (s.indexOf("'") === -1) return "'" + s + "'";
    return '"' + s.replace(/"/g, '') + '"';
  }

  function find(dsl, index, all) {
    return 'find ' + q(dsl) + (index ? ' --index ' + index : '') + (all ? ' --all' : '');
  }

  // ───────── 方案 → 引用 ─────────
  // plan.frames：iframe 链（外到内），每层 {dsl, index}
  function refLines(plan, all) {
    const lines = [];
    const frames = plan.frames || [];
    frames.forEach((f, i) => lines.push(find(i === 0 ? f.dsl : '@1 >> ' + f.dsl, f.index, false)));
    const scoped = dsl => (frames.length ? '@1 >> ' + dsl : dsl);
    if (all) {
      const a = plan.all;
      lines.push(find(scoped(a.dsl), null, true));
      return lines;
    }
    if (plan.kind === 'flat') lines.push(find(scoped(plan.dsl), null, false));
    else if (plan.kind === 'index') lines.push(find(scoped(plan.dsl), plan.index, false));
    else if (plan.kind === 'anchored') lines.push(find(scoped(plan.anchor.dsl), plan.anchor.index, false));
    return lines;
  }

  // 操作目标：引用链必须是一个 token（整体加引号），否则 hb 会把链里的标签名当成变量
  function target(plan) {
    if (plan.kind === 'anchored') return q(plan.rel === '>' ? '@1 > ' + plan.dsl : '@1 >> ' + plan.dsl);
    return '@1';
  }

  function canInline(plan) { return plan.kind === 'flat' && !(plan.frames || []).length; }

  function captureCmd(capture, ref) {
    if (capture.what === 'attr') return 'attr ' + ref + ' ' + q(capture.attr || 'href');
    if (capture.what === 'html') return 'html ' + ref;
    return 'text ' + ref;
  }

  function allKey(st) {
    const p = st.plan || {};
    return JSON.stringify([p.frames || [], p.all ? p.all.dsl : null]);
  }

  // ───────── 单步 → 命令行 ─────────
  // ctx：{refKey（当前已 find 的引用键，null 表示必须重找）, n（步骤序号）}
  // 返回 [引用行]，并在最后一行（引用行或随后的命令行）挂上步骤说明
  function ensure(plan, ctx, c) {
    const fl = refLines(plan, false);
    const key = JSON.stringify(fl);
    if (ctx.refKey === key || !fl.length) return {lines: [], head: tail(c)};
    ctx.refKey = key;
    fl[fl.length - 1] += tail(c);
    return {lines: fl, head: ''};
  }

  function tail(comment) { return comment && comment.text ? '  # ' + comment.text : ''; }

  function unsupported(st, c, opts) {
    if (!opts.comments) return [];
    return ['# ' + c.text + '　⚠ 未生成命令，请手动补：' + describe(st) +
            '（' + ((st.plan && (st.plan.reason || st.plan.frameError)) || '无法定位') + '）'].concat(extraNotes(st, opts));
  }

  function stepLines(st, ctx, opts) {
    const c = {text: opts.comments ? ctx.n + '. ' + describe(st) : ''};
    const plan = st.plan || {kind: 'none', reason: '没有定位信息'};
    const supported = plan.kind !== 'none' && !plan.frameError;
    switch (st.kind) {
      case 'click': {
        if (!supported) return unsupported(st, c, opts);
        if (opts.inline && canInline(plan)) {
          const out = ['click ' + q(plan.dsl) + tail(c)];
          ctx.refKey = null;                     // 点击之后页面可能已经变了
          return out.concat(extraNotes(st, opts));
        }
        const ref = ensure(plan, ctx, c);
        const count = st.count === 2 ? 2 : 1;
        const out = ref.lines;
        for (let i = 0; i < count; i++) {
          out.push('click ' + target(plan) + (i === 0 ? ref.head : ''));
        }
        ctx.refKey = null;
        return out.concat(extraNotes(st, opts));
      }

      case 'input': {
        if (!supported) return unsupported(st, c, opts);
        const ref = ensure(plan, ctx, c);
        ref.lines.push('input ' + target(plan) + ' ' + q(st.secret ? '********' : st.value) + ref.head);
        return ref.lines.concat(extraNotes(st, opts));
      }

      case 'press': {
        if (!supported) return unsupported(st, c, opts);
        const ref = ensure(plan, ctx, c);
        ref.lines.push('press ' + target(plan) + ' ' + q(st.key) + ref.head);
        if (/enter|return/i.test(st.key)) ctx.refKey = null;   // 回车可能提交/跳转
        return ref.lines;
      }

      case 'select': {
        if (!supported) return unsupported(st, c, opts);
        const ref = ensure(plan, ctx, c);
        ref.lines.push('select ' + target(plan) + ' ' + q(st.value) + (st.by && st.by !== 'text' ? ' --by ' + q(st.by) : '') + ref.head);   // 裸词一律当变量，选项值也要引号
        ctx.refKey = null;
        return ref.lines;
      }

      case 'capture': {
        const cap = st.capture;
        if (cap.all && plan.all) return [];        // “全部同类”由 generate() 合并成 loop 块
        if (!supported) return unsupported(st, c, opts);
        const ref = ensure(plan, ctx, c);
        ref.lines.push(captureCmd(cap, target(plan)) + ref.head);
        return ref.lines;
      }

      case 'nav':
        if (st.mode === 'open') {
          ctx.refKey = null;
          return ['open ' + q(st.url) + tail(c)];
        }
        if (st.leavesPage) ctx.refKey = null;     // 整页跳转/刷新：旧引用全失效
        return opts.comments ? ['# ' + c.text].concat(extraNotes(st, opts)) : [];

      case 'sleep':
        return ['sleep ' + (Number(st.seconds) > 0 ? Number(st.seconds) : 1) + tail(c)];

      default:
        return opts.comments ? ['# ' + oneLine(st.text)] : [];
    }
  }

  // 附加说明（提升到了可点击的父级、新标签页、密码等）
  function extraNotes(st, opts) {
    if (!opts.comments) return [];
    return (st.notes || []).map(t => '#    ↳ ' + oneLine(t));
  }

  // ───────── 步骤说明（也用于面板）─────────
  function describe(st) {
    switch (st.kind) {
      case 'click':
        return '点击 ' + st.desc + (st.count === 2 ? '（双击）' : '');
      case 'input': {
        const v = st.secret ? '********（密码）' : String(st.value == null ? '' : st.value);
        return '输入 ' + st.desc + ' = "' + (v.length > 40 ? v.slice(0, 40) + '…' : v) + '"';
      }
      case 'press':
        return '在 ' + st.desc + ' 上按 ' + st.key;
      case 'select':
        return '下拉选择 ' + st.desc + ' → "' + st.value + '"' + (st.by && st.by !== 'text' ? '（' + st.by + '）' : '');
      case 'capture': {
        const cap = st.capture;
        return '抓取' + (WHAT[cap.what] || cap.what) + (cap.what === 'attr' ? ' ' + (cap.attr || '') : '') +
          '：' + st.desc + (cap.all ? '（全部同类 ' + cap.all.n + ' 项）' : '');
      }
      case 'nav':
        return st.text || ('跳转到 ' + (st.url || ''));
      case 'sleep':
        return '等待 ' + st.seconds + ' 秒';
      default:
        return st.text || '说明';
    }
  }

  // “全部同类”抓取：find --all + loop n: 循环体里用 @${i}（hb 的 ${} 插值对 @ 后面的引用同样生效）
  function loopBlock(st, ctx, opts, group) {
    const plan = st.plan;
    const a = plan.all;
    const lines = refLines(plan, true);
    const title = opts.comments ? ctx.n + '. 抓取全部同类（' + a.n + ' 项）：' + group.map(x => x.desc).join('、') : '';
    lines[lines.length - 1] += tail({text: title});
    if (a.n > MAX_REFS && opts.comments) {
      lines.push('#    ↳ 注意：hb 的 find 最多缓存 ' + MAX_REFS + ' 个引用（@1…@' + MAX_REFS + '），' +
        '超出部分 @${i} 取不到；条数变多时请改用 Python API 或分页抓取');
    }
    lines.push('loop ' + a.n + ':');
    for (const s of group) {
      const ref = q('@${i}' + (a.rel ? (a.relOp === '>' ? ' > ' : ' >> ') + a.rel : ''));
      const cap = s.capture;
      const body = cap.what === 'attr' ? 'attr ' + ref + ' ' + q(cap.attr || 'href')
        : cap.what === 'html' ? 'html ' + ref : 'text ' + ref;
      lines.push('    ' + body + tail({text: opts.comments ? s.desc : ''}));
    }
    ctx.refKey = null;
    return lines;
  }

  // ───────── 整份脚本 ─────────
  // session：{startUrl, startTitle, startedAt, steps:[…]}
  function generate(session, options) {
    const opts = Object.assign({}, DEFAULTS, options || {});
    const lines = header(session, opts);
    const ctx = {refKey: null, n: 0};
    const steps = session.steps || [];
    for (let i = 0; i < steps.length; i++) {
      const st = steps[i];
      ctx.n++;
      if (st.kind === 'capture' && st.capture && st.capture.all && st.plan && st.plan.all) {
        const key = allKey(st);
        const group = [st];
        while (i + 1 < steps.length && steps[i + 1].kind === 'capture' && steps[i + 1].capture.all &&
               allKey(steps[i + 1]) === key) {
          group.push(steps[++i]);
          ctx.n++;
        }
        lines.push(...loopBlock(st, ctx, opts, group));
        continue;
      }
      lines.push(...stepLines(st, ctx, opts));
    }
    if (!steps.length) lines.push('# （还没有录制到任何操作）');
    return lines.join('\n') + '\n';
  }

  function header(session, opts) {
    const steps = session.steps || [];
    const kinds = {};
    for (const st of steps) kinds[st.kind] = (kinds[st.kind] || 0) + 1;
    const LABEL = {click: '点击', input: '输入', press: '按键', select: '选择', capture: '抓取', nav: '跳转', sleep: '等待', note: '说明'};
    const summary = Object.keys(LABEL).filter(k => kinds[k]).map(k => LABEL[k] + ' ' + kinds[k]).join(' · ');
    const when = session.startedAt ? new Date(session.startedAt) : new Date();
    const pad = x => (x < 10 ? '0' : '') + x;
    const stamp = when.getFullYear() + '-' + pad(when.getMonth() + 1) + '-' + pad(when.getDate()) + ' ' +
      pad(when.getHours()) + ':' + pad(when.getMinutes());
    const bar = '# ' + '─'.repeat(46);
    if (!opts.comments) return session.startUrl ? ['open ' + q(session.startUrl)] : [];
    return [
      bar,
      '# hb 录制脚本' + (session.startTitle ? ' · ' + oneLine(session.startTitle) : ''),
      '# 起始地址：' + oneLine(session.startUrl),
      '# 录制时间：' + stamp + (summary ? '　步骤：' + steps.length + '（' + summary + '）' : ''),
      '# 运行：hb run 这个文件.hb　（--echo 逐行回显，-c 失败也继续，--json 每行输出一条 JSON）',
      bar,
      session.startUrl ? 'open ' + q(session.startUrl) : null,
      session.startUrl ? '' : null,
    ].filter(l => l !== null);
  }

  // 校验用：建立引用后打印 data-hbn（tests/test_recorder.py 给每个元素盖的编号）
  function verifyLines(plan) {
    return refLines(plan, false).concat(['attr ' + target(plan) + ' "data-hbn"']);
  }

  return {generate: generate, stepLines: stepLines, refLines: refLines, target: target,
          describe: describe, verifyLines: verifyLines, q: q};
});
