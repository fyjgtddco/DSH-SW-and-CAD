// Repair incomplete conversation preferences before the host restores its draft.
(function repairMissingConversationDrafts() {
  try {
    var repairs = [];
    Object.keys(localStorage).forEach(function (key) {
      if (key.indexOf('dsh.conversation.') !== 0) return;
      var raw = localStorage.getItem(key);
      var state;
      try { state = JSON.parse(raw); } catch (e) { return; }
      if (!state || typeof state !== 'object' || Array.isArray(state) ||
          Object.prototype.hasOwnProperty.call(state, 'draft')) return;
      repairs.push({ key: key, raw: raw, state: state });
    });
    if (!repairs.length) return;
    var backupKey = 'dsh-engineering-ui/recovery-before-draft-fix/20261004';
    if (localStorage.getItem(backupKey) === null) {
      localStorage.setItem(backupKey, JSON.stringify(repairs.map(function (entry) {
        return { key: entry.key, raw: entry.raw };
      })));
    }
    repairs.forEach(function (entry) {
      entry.state.draft = '';
      if (!Object.prototype.hasOwnProperty.call(entry.state, 'viewRequest')) entry.state.viewRequest = null;
      localStorage.setItem(entry.key, JSON.stringify(entry.state));
    });
  } catch (e) { console.warn('[dsh-engineering-ui] conversation draft recovery', e); }
})();

// FINAL_UI_FIX_1789137672695
// dsh-engineering-ui — Client 半
// ==========================================================================
// 工程模式三面板控制台
//   [主对话 60%]  DSH 原生 center 列
//   [目录 11.4%]  子代理列表
//   [详情 28.6%]  子代理对话流（气泡 / 思考引用框 / 工具卡 / 交互提问）
// ==========================================================================
window.__ModuleLoader__.load({
  id: 'dsh-engineering-ui',
  factory: function (require) {
    var module = { exports: {} };
    var exports = module.exports;
    var React = require('react');
    var useState = React.useState;
    var useEffect = React.useEffect;
    var useRef = React.useRef;
    var h = React.createElement;
    // ── 原始事件 → 显示事件（字段映射层；host 下发原始 DSH 事件）──
    // 真实持久化字段：tool/call → data.name / data.arguments；
    //               tool/result → data.message.content / data.error；
    //               assistant/message → data.message.content（reasoning/text 块）
    function squashEvent(ev) {
      var d = ev.data || {};
      var base = { seq: ev.seq, time: ev.time };
      switch (ev.type) {
        case 'assistant/message': {
          var msg = d.message || {};
          var content = Array.isArray(msg.content) ? msg.content : (Array.isArray(d.content) ? d.content : []);
          var out = [];
          var texts = [];
          for (var i = 0; i < content.length; i++) {
            var b = content[i];
            if (!b || typeof b !== 'object') continue;
            if (b.type === 'reasoning' && typeof b.text === 'string') {
              if (b.text.trim()) out.push({ kind: 'thinking', text: b.text.trim().slice(0, 4000), seq: ev.seq });
            } else if (b.type === 'text' && typeof b.text === 'string') {
              texts.push(b.text);
            }
          }
          var t = texts.join('\n').trim();
          if (t) out.push({ kind: 'message', text: t.slice(0, 4000), seq: ev.seq });
          return out;
        }
        case 'user/message': {
          // ── 用户气泡 ─────────────────────────────────────────────────
          // 原实现没有这个分支，因此右侧面板只显示助手侧内容。
          // 子代理工作区必须成对显示「用户问 / 助手答」，故补上。
          // 事件形状：'user/message' 的 data 就是一个 UserMessage
          //   （dsh-session/src/types.ts:309），正文在 data.message.content[]
          //   —— 与 assistant/message 同构，故解析方式一致。
          // 兼容两种落地：整体就是 message，或 data.message 是 message。
          var um = (d.message && Array.isArray(d.message.content)) ? d.message : d;
          var uc = Array.isArray(um && um.content) ? um.content : [];
          var utexts = [];
          for (var ui = 0; ui < uc.length; ui++) {
            var ub = uc[ui];
            if (ub && typeof ub === 'object' && ub.type === 'text' && typeof ub.text === 'string') {
              utexts.push(ub.text);
            }
          }
          var ut = utexts.join('\n').trim();
          return ut ? [{ kind: 'user-message', text: ut.slice(0, 4000), seq: ev.seq }] : [];
        }
        case 'tool/call': {
          var rawArgs = d.arguments !== undefined ? d.arguments : d.args;
          var argsText;
          try { argsText = typeof rawArgs === 'string' ? rawArgs : JSON.stringify(rawArgs === undefined ? null : rawArgs); } catch (e) { argsText = ''; }
          return [{ kind: 'tool-call', tool: d.name || d.tool || 'tool', args: String(argsText || '').slice(0, 20000), callId: d.callId, seq: ev.seq }];
        }
        case 'tool/result': {
          var msg2 = d.message || {};
          var content2 = Array.isArray(msg2.content) ? msg2.content : [];
          var parts = [];
          for (var j = 0; j < content2.length; j++) {
            var b2 = content2[j];
            if (b2 && typeof b2 === 'object' && b2.type === 'text' && typeof b2.text === 'string') parts.push(b2.text);
          }
          var text = parts.join('\n').trim();
          var isErr = !!d.error || msg2.isError === true;
          if (!text && d.error) {
            try { text = typeof d.error === 'string' ? d.error : JSON.stringify(d.error); } catch (e2) { text = ''; }
          }
          return [{ kind: 'tool-result', text: String(text || '').slice(0, 8000), isError: isErr, callId: d.callId, seq: ev.seq }];
        }
        default:
          return [];
      }
    }
    // 兼容两种 host：已折叠事件（带 kind）直接透传；原始事件走 squashEvent
    function toDisplayEvents(ev) {
      if (ev && ev.kind) return [ev];
      try { return squashEvent(ev) || []; } catch (e) { return []; }
    }
    var STYLE_ID = 'dsh-engineering-ui-style';
    var DOCK_CLASS = 'dsh-eng-dock';
    // ── 【问题1 修复】独立的"工程模式会话"标记类 ─────────────────────────
    // 原设计只有 DOCK_CLASS，而它仅在【子代理面板展开时】才挂到 body。
    //   后果：没有子代理（或面板收起）时，主对话里的工程模式样式全部失效 ——
    //   这正是"主对话问答没变成表格"的深层原因之一。
    // 新增 ENG_MODE_CLASS：只要当前会话是 engineering 预设就挂上，
    //   与面板开合解耦，保证主对话样式始终生效；
    //   同时它天然具备【作用域隔离】——非工程模式绝不挂，样式不会外溢。
    var ENG_MODE_CLASS = 'dsh-eng-mode';
    // 【问题2 修复】SW单行模式权限预设及其配套横幅/图标已整体移除 ——
    //   权限只保留 DSH 标准三档（read-only / workspace-write / danger-full-access）。
    //   原先的 PRESET_LABEL / PRESET_KEY / PRESET_ICON 常量随之删除。
    var STYLE = [
      // ── 【布局重构】弹性尺寸变量 ──────────────────────────────────────
      // 原实现的两个致命问题：
      //   ① 用 !important 强改宿主的 grid-template-columns（固定 280px + 3fr + 2fr），
      //      窗口一变窄，中间对话区被压成 3/5，文字被挤成代码式断行；
      //   ② .eng-console 用 position:fixed + width:calc(...*0.4) 抢占视口，
      //      既不参与流式布局，又会覆盖/挤压主区域，长文本还溢出边框。
      // 现改为：不再改宿主 grid，改为【给主区域让出右侧空间】+ Flex 弹性分配。
      // ══ 【布局 v3 · 真正的 Flex 并排】════════════════════════════════
      // 上一版用 position:fixed + padding 让位，观感上像"补丁盖在主界面上"，
      // 且宽度上限 560px 过宽 —— 已废弃。
      //
      // 本版回归最简单可靠的方案：让面板成为宿主流式布局里的【普通子项】，
      // 与中间主区域并排；面板出现 → 主区域自动变窄（flex 收缩），绝不遮挡。
      //
      // 宽度策略（按用户要求 300~400px，且不超过视口 30%）：
      //   flex: 0 0 auto  —— 不参与放大，宽度由 width 决定
      //   width: clamp(300px, 26vw, 400px)
      //   max-width: 30vw —— 硬上限，绝不无限撑开
      ':root{' +
        '--eng-sb:280px;' +                                // 最左侧导航栏（宿主自带）
        // ── 【滚动区】设置页可用高度（与宿主设置面板同一公式）──────────
        // 宿主设置面板（SettingsRoot）的高度就是：
        //   min(800px, calc(100vh - 2 * max(24px, var(--dsh-frame-overlay-top,24px))))
        // 这里复用同一表达式，让本插件的滚动区【刚好装进】宿主面板，
        // 从而只出现【一条】滚动条（宿主的 .options 不会再有内容可滚）。
        // --dsh-frame-overlay-top 由宿主在 html 上按平台注入；缺失时回落 24px。
        '--eng-opts-h:min(800px, calc(100vh - 2 * max(24px, var(--dsh-frame-overlay-top, 24px))));' +
        // 展开宽度严格落在 350~450px（用户要求），不再随视口缩到 300
        // 【布局 v9 · 3:1 比例】主区 75% / 面板 25%（按用户要求）
        // 面板宽度 = (视口 - 左导航) × 25%，运行时由 JS 精确写入；
        // 这里给保守初值，JS 会覆盖。
        '--eng-pane:40%;' +
        '--eng-pane-max:40%;' +
        // 主区域最低阅读宽度：低于此值时优先保证主区可读
        '--eng-main-min:520px;' +
      '}',
      // 主内容容器：允许收缩（min-width:0 是 flex/grid 子项能收缩的前提）
      'body.' + DOCK_CLASS + ' div[style*="grid-template-columns"]{' +
        'min-width:0;' +
      '}',
      // ══ 【布局 v4】右侧面板：默认折叠 + 平滑展开 + 挤压主区域 ═════════
      // 用户明确要求的三条：
      //   ① 默认只在右边缘留一个漂亮的小标签，点击才展开；
      //   ② 展开时作为 Flex 侧边栏【挤压】主区域，绝不覆盖；
      //   ③ 展开/收起带平滑过渡动画。
      //
      // 【布局 v11 · 最终】面板 = 第 3 列的文档流子项（绝对无 fixed/absolute）。
      // grid-column:3 把面板钉进宿主 grid 的第三列，宽度由列宽（--eng-pane）
      // 决定 —— 列开则面板在，列合则面板随列宽 0 消失，物理上不可能覆盖中间列。
      '.eng-console{' +
        'background:var(--dsw-alias-bg-layer-1,#fff);' +
        'border-left:1px solid var(--dsw-alias-border-l2,#d8dee9);box-sizing:border-box;' +
        'color:var(--dsw-alias-label-primary,#0f172a);' +
        'display:flex;flex-direction:row;font-size:12px;' +
        'position:relative;' +                       // 文档流（无 fixed/absolute）
        'grid-column:3;grid-row:1;' +                // 钉进宿主 grid 第 3 列
        'width:100%;min-width:0;max-width:100%;' +   // 宽度=列宽
        'height:100%;overflow:hidden;' +
        'opacity:1;' +
        'transition:opacity .2s ease' +
      '}' +
      // 收起态：面板随第三列一起归零（列宽 0 + 自身透明）
      '.eng-console.collapsed{' +
        'opacity:0;pointer-events:none;' +
      '}' +
      // ── 【让位时隐藏面板本体】─────────────────────────────────────────
      // 用【元素自身的类】而不是 body 上的类来表达"让位"。
      //   原因：让位时我们已把 DOCK_CLASS 整个摘掉（避免污染宿主层叠），
      //   若这条规则仍写成 `body.dsh-eng-dock:not(.eng-pane-open) …`，
      //   摘掉类之后它就【不再匹配】→ 面板反而暴露出来覆盖主区域。
      //   用 .yielded（由 React 直接按 yieldToHost 打上）最可靠：
      //   状态与渲染同源，不依赖任何 body 类。
      '.eng-console.yielded{' +
        'display:none !important;' +
      '}' +
      // ── 【问题4·开启侧】嵌入模式（渲染在官方右列 tab 内）───────────────
      // 此时容器由官方提供，因此【取消 grid 定位】改为填满父容器，
      //   否则 grid-column:3 / grid-row:1 会把面板摆到宿主 grid 的错误位置。
      '.eng-console.embedded{' +
        'grid-column:auto;grid-row:auto;' +
        'height:100%;width:100%;' +
        'border-left:none;' +
      '}' +
      // （收起态样式见上方 .eng-console.collapsed：transform 位移出场）
      // 【布局 v3】不再需要 padding 让位 —— 面板已是流内子项，
      // 位置由宿主的 flex/grid 布局自然分配，主区域自动收缩。
      // 这里只保证主区域子项可收缩（min-width:0 是收缩的前提）。
      'body.' + DOCK_CLASS + ' div[style*="grid-template-columns"]>*{' +
        'min-width:0;' +
      '}',
      // ── 目录（次级导航）：浅底、窄、安静，不与正文抢视觉 ──────────────
      '.eng-tree{background:var(--dsw-alias-bg-layer-2,rgba(15,23,42,.03));' +
        'border-right:1px solid var(--dsw-alias-border-l2,#e5e7eb);box-sizing:border-box;' +
        // ── 【自适应】频道列宽度不再写死 168px ──────────────────────────
        //   现场：168px 里要塞「状态点 + 折叠箭头 + 层级徽标 + 标签 + 状态」，
        //   连只有一级的「结构件房间：底盘/轮毂/…」都被截成「结构件房间：底..」，
        //   二级/三级更是只剩「总装干涉...」。
        //   改为：宽度 = clamp(190px, 面板宽 × 42%, 300px)，由 JS 写 --eng-tree-w；
        //   变量缺失时回落到 clamp() 自身的响应式值（纯 CSS 也能自适应）。
        'display:flex;flex:0 0 var(--eng-tree-w, clamp(190px, 42%, 300px));' +
        'flex-direction:column;height:100%;min-width:0;overflow:hidden;' +
        // ★ 容器查询的锚点：让内部 @container engtree (...) 按【本列宽度】生效
        'container-type:inline-size;container-name:engtree}',
      '.eng-tree-cap{' +
        'align-items:center;border-bottom:1px solid var(--dsw-alias-border-l2,#e5e7eb);' +
        'color:var(--dsw-alias-label-secondary,#475569);display:flex;flex:none;' +
        'font-size:11.5px;font-weight:700;gap:6px;justify-content:space-between;' +
        'letter-spacing:.04em;padding:11px 10px}' +
      '.eng-tree-cap-t{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
      '.eng-tree-cap-n{' +
        'background:var(--dsw-alias-bg-layer-3,rgba(15,23,42,.08));border-radius:999px;' +
        'flex:none;font-size:10px;font-weight:700;padding:1px 6px' +
      '}' +
      // ── 子代理列表 = 左列【上半区】──────────────────────────────────
      // 用户要求：零件表/子代理表「要往下滑动，自适应」。
      // 现在左列拆成「列表(上) + 压缩历史(下)」，因此这里改成 flex:1 1 auto：
      //   列表吃掉剩余高度并在自己内部滚动，绝不会把下面的压缩历史挤出去。
      // 【布局 v12】左列 = 目录 50% / 零件表 50%（下方 .eng-lower 用 flex:1 1 50%）。
      '.eng-tree-list{display:flex;flex:1 1 50%;flex-direction:column;gap:4px;' +
        'min-height:0;overflow-y:auto;padding:7px}',
      // 目录条目：默认透明（不抢眼），选中才实心高亮 —— 一眼看出"当前在看谁"
      '.eng-node{align-items:center;background:transparent;border:1px solid transparent;' +
        'border-radius:8px;box-sizing:border-box;color:var(--dsw-alias-label-secondary,#475569);' +
        'cursor:pointer;display:flex;font-family:inherit;font-size:12px;font-weight:600;' +
        'gap:7px;padding:8px 9px;text-align:left;width:100%}' +
      '.eng-node:hover{background:var(--dsw-alias-interactive-bg-hover,rgba(15,23,42,.06))}' +
      '.eng-node.on{' +
        'background:var(--dsw-alias-state-business-tertiary,#eef2ff);' +
        'border-color:var(--dsw-alias-state-business-primary,#2563eb);' +
        'color:var(--dsw-alias-label-primary-bluish,#3730a3)}',
      // ── 【子代理层级】缩进 / 折叠三角 / 层级徽标 / 不可读分支 ─────────────
      '.eng-node-wrap{display:flex;align-items:center;gap:2px;width:100%;min-width:0}',
      // ── 【层级缩进自适应】每级 14px 太吃宽度（3 级就吃掉 42px）──────
      //   通道列只有 190~300px，缩进必须克制：第 2 级 10px，之后每级 6px。
      //   具体 padding 由 JS 按 depth 内联写（见 nodes 渲染），这里只兜底。
      '.eng-node-wrap>.eng-node{flex:1;min-width:0}',
      '.eng-node-wrap.lv{position:relative}',
      '.eng-node-wrap.lv::before{' +
        'content:"";position:absolute;left:6px;top:2px;bottom:2px;width:1px;' +
        'background:var(--dsw-alias-border-l2,#e5e7eb)' +
      '}',
      '.eng-node-tw{background:transparent;border:none;color:var(--dsw-alias-label-tertiary,#8b95a3);' +
        'cursor:pointer;flex:none;font-size:10px;line-height:1;padding:2px;width:14px;text-align:center}',
      '.eng-node-tw:hover{color:var(--dsw-alias-label-primary,#0f172a)}',
      '.eng-node-tw.ph{visibility:hidden}',
      '.eng-lv{background:var(--dsw-alias-bg-layer-3,rgba(15,23,42,.08));border-radius:4px;' +
        'flex:none;font-size:9px;font-weight:700;letter-spacing:.02em;padding:0 4px;opacity:.8}',
      '.eng-dot.diag{background:#f59e0b}',
      '.eng-node.diag{opacity:.75}',
      '.eng-dot{border-radius:50%;flex:none;height:8px;width:8px;background:#94a3b8}',
      '.eng-dot.run{background:#16a34a;animation:eng-pulse 1.3s ease-in-out infinite}',
      '.eng-dot.done{background:#0ea5e9}',
      '@keyframes eng-pulse{0%,100%{opacity:1}50%{opacity:.25}}',
      '.eng-node-label{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
      '.eng-node-state{flex:none;font-size:10px;opacity:.75}',
      // ── 【真·自适应：容器查询】按【频道列自身宽度】收敛，不依赖 JS 计算 ──
      //   为什么用 @container 而不是 @media：
      //     · @media 看的是【窗口】宽度，而频道列宽度取决于面板占比（40→46%），
      //       窗口很宽但面板窄时它判断不出来；
      //     · @container 直接量 .eng-tree 自己的内容盒，拖窗口、缩放、
      //       换分辨率都自动重算，零 JS、零重排抖动。
      //   三档收敛（按需隐藏最不重要的信息，标签永远优先）：
      //     < 240px  → 隐藏「未运行/运行中」文字（圆点已表达同样语义）
      //     < 210px  → 再隐藏层级徽标 L2/L3
      //     < 180px  → 再压掉折叠箭头的占位宽度
      '@container engtree (max-width:240px){' +
        '.eng-node-state{display:none}' +
      '}',
      '@container engtree (max-width:210px){' +
        '.eng-lv{display:none}' +
      '}',
      '@container engtree (max-width:180px){' +
        '.eng-node-tw{width:10px;padding:0}' +
      '}',
      '.eng-detail{background:#fff;box-sizing:border-box;display:flex;flex:1 1 0;flex-direction:column;' +
        'height:100%;min-width:0;overflow:hidden}' +
      // ── 【右列页签】详情区 = 「实时流程」+「压缩历史」两页（布局 v12）─────
      // 用户要求：左列选中哪个子代理，右列就展示"那个子代理的流程"与
      //   "那个子代理的压缩历史"。因此两页共用同一个选中项，切换只换内容。
      // 页签条常驻（不随内容滚走），页签体自成一列滚动容器。
      '.eng-dtabs{background:transparent;' +
        'border-bottom:1px solid var(--dsw-alias-border-l2,#e5e7eb);' +
        'display:flex;flex:none;gap:2px;padding:6px 10px 0;overflow-x:auto}' +
      '.eng-dtab{background:transparent;border:1px solid transparent;border-bottom:none;' +
        'border-radius:7px 7px 0 0;color:var(--dsw-alias-label-tertiary,#8b95a3);' +
        'cursor:pointer;font-family:inherit;font-size:11.5px;font-weight:600;' +
        'line-height:1.5;padding:5px 11px;white-space:nowrap}' +
      '.eng-dtab:hover{color:var(--dsw-alias-label-secondary,#475569);' +
        'background:var(--dsw-alias-interactive-bg-hover,rgba(15,23,42,.05))}' +
      '.eng-dtab.on{background:var(--dsw-alias-state-business-tertiary,#eef2ff);' +
        'border-color:var(--dsw-alias-border-l2,#e5e7eb);' +
        'color:var(--dsw-alias-state-business-primary,#2563eb)}' +
      '.eng-dtab-n{background:var(--dsw-alias-bg-layer-3,rgba(15,23,42,.08));border-radius:999px;' +
        'font-size:9.5px;font-weight:700;margin-left:5px;padding:0 5px}' +
      '.eng-dtabs-body{display:flex;flex:1 1 auto;flex-direction:column;min-height:0;overflow:hidden}' +
      '.eng-dtabs-body>.eng-log{flex:1 1 auto;min-height:0;overflow-y:auto}' +
      // ── 【背景 · 2026-11-07】页签条不再自带深色底 ─────────────────────
      // 踩过的坑：这里原本【没包在 @media 里】，于是无论浅色还是深色主题，
      //   .eng-dtabs 都被钉成 #1b1e24 —— 用户截图里右列出现整块黑底、
      //   页签条和下面的内容全是黑的，正是这条造成的。
      // 现在：页签条一律透明（父容器 .eng-detail 的白底透上来），
      //   只保留"选中页签"的浅蓝底作为状态色。
      // ── 详情标题栏：两行结构（名称+状态 / 副标题摘要）───────────────
      '.eng-head{' +
        'background:var(--dsw-alias-bg-layer-1,#fff);' +
        'border-bottom:1px solid var(--dsw-alias-border-l2,#e5e7eb);' +
        'display:flex;flex:none;flex-direction:column;gap:3px;' +
        'padding:11px 12px 10px;position:relative' +
      '}' +
      '.eng-head-main{align-items:center;display:flex;gap:7px;min-width:0;padding-right:56px}' +
      '.eng-name{' +
        'color:var(--dsw-alias-label-primary,#0f172a);flex:1;font-size:13.5px;' +
        'font-weight:700;min-width:0;overflow:hidden;text-overflow:ellipsis;' +
        'white-space:nowrap' +
      '}' +
      '.eng-head-sub{' +
        'color:var(--dsw-alias-label-tertiary,#94a3b8);font-size:11px;' +
        'min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap' +
      '}' +
      '.eng-head .eng-collapse{position:absolute;right:10px;top:12px}' +
      '.eng-tag{' +
        'background:var(--dsw-alias-bg-layer-3,rgba(15,23,42,.07));border-radius:5px;' +
        'color:var(--dsw-alias-label-secondary,#475569);flex:none;font-size:10px;' +
        'font-weight:700;padding:2px 7px;white-space:nowrap' +
      '}' +
      '.eng-tag.quiet{opacity:.72}' +
      '.eng-tag.run{background:#16a34a;color:#fff}' +
      '.eng-tag.done{background:#0ea5e9;color:#fff}',
      '.eng-x{background:#fff;border:1px solid #cbd5e1;border-radius:6px;color:#475569;cursor:pointer;flex:none;font-size:13px;font-weight:700;height:24px;line-height:1;width:24px}',
      '.eng-x:hover{background:#e2e8f0}',
      // ── 【排版优化】日志区：舒适的行高/字号 + 强制换行，避免"堆砌代码块"感 ──
      // ── 【UI 美化】深色模式补齐 ─────────────────────────────────────
      // 原来只有少量规则带 --dsw-alias token，大量硬编码浅色值在暗色主题下
      //   会刺眼（纯白底 + 深灰字）。这里统一给出暗色覆盖。
      '@media(prefers-color-scheme:dark){' +
        '.eng-bubble.sys{background:rgba(255,255,255,.06);color:#cbd5e1}' +
        '.eng-tool-card{background:rgba(14,165,233,.10);border-color:rgba(14,165,233,.32)}' +
        '.eng-tool-card-h{color:#7dd3fc}' +
        '.eng-tool-card-b{background:#1c2128;border-top-color:rgba(14,165,233,.3);color:#cbd5e1}' +
        '.eng-err-card{background:rgba(239,68,68,.14);border-color:rgba(239,68,68,.42);color:#fca5a5}' +
        '.eng-md-pre{background:rgba(255,255,255,.07)}' +
        '.eng-md-code{background:rgba(37,99,235,.22)}' +
      '}',
      // ══ 【UI 美化】子代理面板 · 阅读区 ══════════════════════════════════
      // 用户反馈：自带面板"很乱，根本看不来啥"。这里按"时间轴 + 卡片"重排：
      //   · 每条事件 = 一个 .eng-evt（左侧色条区分类型，标题行有图标+标签）；
      //   · 日志区加大留白与行高，长段落不再挤成一坨；
      //   · 全部改用 --dsw-alias-* 语义 token，自动跟随亮/暗主题。
      '.eng-log{' +
        // ── 【自适应·居中】详情内容限宽并居中（用户要求「其他的偏中间一点」）──
        //   面板变宽后，若正文仍从最左排到最右，长行会显得散。
        //   用 max-width + margin auto 让日志列在自己的栏里居中；
        //   窄屏时 max-width 不起作用，自动退回全宽。
        '--eng-log-max:min(100%, 900px);' +
        'display:flex;flex:1;flex-direction:column;gap:10px;' +
        'font-size:12.5px;letter-spacing:.01em;line-height:1.72;' +
        'min-height:0;overflow-wrap:break-word;overflow-x:hidden;' +
        'overflow-y:auto;padding:14px 14px 22px;word-break:break-word' +
      '}' +
      '.eng-log p,.eng-log div{overflow-wrap:break-word;word-break:break-word;max-width:100%}',
      // 详情正文居中：内容列限宽后左右留白，长行不会一路顶到面板边缘
      '.eng-log>*{width:100%;max-width:var(--eng-log-max);margin-left:auto;margin-right:auto}' +
      '.eng-log *{box-sizing:border-box}' +
      '.eng-note{' +
        'color:var(--dsw-alias-label-tertiary,#94a3b8);font-size:12px;' +
        'padding:22px 14px;text-align:center' +
      '}' +

      // ── 事件条目：时间轴卡片 ───────────────────────────────────────
      '.eng-evt{' +
        'border-left:3px solid var(--dsw-alias-border-l2,#e2e8f0);' +
        'display:flex;flex-direction:column;gap:4px;' +
        'min-width:0;padding:1px 0 1px 10px' +
      '}' +
      // 【本次改动】标题行变成折叠开关：鼠标手势 + 悬停反馈 + 箭头
      '.eng-evt-hd{align-items:center;cursor:pointer;display:flex;gap:5px;' +
        'min-height:16px;outline:none;user-select:none}' +
      '.eng-evt-hd:hover .eng-evt-label{color:var(--dsw-alias-state-business-primary,#3b82f6)}' +
      '.eng-evt-caret{color:var(--dsw-alias-label-tertiary,#94a3b8);flex:none;font-size:10px;opacity:.75}' +
      // 【本次改动】收起态的一行摘要：灰、单行、省略号，不抢标题
      '.eng-evt-peek{' +
        'color:var(--dsw-alias-label-tertiary,#94a3b8);flex:1 1 auto;font-size:10.5px;' +
        'font-weight:400;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap' +
      '}' +
      // 折叠态：只留标题行，正文不渲染（DOM 也一起移除，长日志更省内存）
      '.eng-evt.collapsed{padding-bottom:0}' +
      '.eng-evt-ico{flex:none;font-size:11px;line-height:1;opacity:.9}' +
      '.eng-evt-label{' +
        'color:var(--dsw-alias-label-tertiary,#94a3b8);flex:none;' +
        'font-size:10.5px;font-weight:700;letter-spacing:.04em' +
      '}' +
      '.eng-evt-bd{min-width:0}' +
      // 颜色语义：思考=灰 / 输出=蓝 / 命令=青 / 结果=石板 / 报错=红 / 提问=紫
      '.eng-evt-think{border-left-color:var(--dsw-alias-border-l2,#cbd5e1)}' +
      '.eng-evt-text{border-left-color:var(--dsw-alias-state-business-primary,#3b82f6)}' +
      '.eng-evt-tool{border-left-color:#0ea5e9}' +
      '.eng-evt-result{border-left-color:#94a3b8}' +
      '.eng-evt-error{border-left-color:#ef4444}' +
      '.eng-evt-ask{border-left-color:#8b5cf6}' +

      // ── 输出气泡（助手正文）：不再是蓝底方块，改为干净的文档流 ───────
      // 大段正文套气泡只会显得闷；这里让它读起来像文档：无底色、大行高。
      '.eng-bubble{' +
        'color:var(--dsw-alias-label-primary,#1e293b);font-size:12.5px;' +
        'line-height:1.78;max-width:100%;overflow-wrap:anywhere;' +
        'padding:0;white-space:normal;word-break:break-word' +
      '}' +
      '.eng-bubble.sys{' +
        'background:var(--dsw-alias-bg-layer-2,rgba(15,23,42,.04));' +
        'border-radius:8px;color:var(--dsw-alias-label-secondary,#475569);' +
        'font-family:ui-monospace,SFMono-Regular,Consolas,monospace;' +
        'font-size:11.5px;line-height:1.6;overflow:auto;' +
        'padding:8px 10px;white-space:pre-wrap' +
      '}' +
      // 【本次改动】结果块默认收起时也可能很长；展开后给一个更宽松的上限，
      //   避免"点开了却只看到一小截"。超过仍在块内滚动，不撑破面板。
      '.eng-bubble.sys{max-height:min(52vh,520px)}' +

      // ── 思考框：弱化但不消失 ───────────────────────────────────────
      '.eng-think{' +
        'color:var(--dsw-alias-label-tertiary,#7a8699);font-size:12px;' +
        'font-style:normal;line-height:1.7;max-width:100%;' +
        'overflow-wrap:anywhere;text-align:left;white-space:pre-wrap;' +
        'word-break:break-word' +
      '}' +

      // ── 工具卡：一行标题 + 可展开参数 ─────────────────────────────
      '.eng-tool-card{' +
        'background:var(--dsw-alias-bg-layer-2,rgba(14,165,233,.06));' +
        'border:1px solid var(--dsw-alias-border-l2,#bae6fd);border-radius:8px;' +
        'max-width:100%;min-width:0;overflow:hidden' +
      '}' +
      '.eng-tool-card-h{' +
        'align-items:center;color:#0369a1;cursor:pointer;display:flex;' +
        'font-size:11.5px;font-weight:600;gap:6px;padding:7px 10px;' +
        'user-select:none' +
      '}' +
      '.eng-tool-card-h:hover{background:rgba(14,165,233,.10)}' +
      '.eng-tool-txt{' +
        'flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap' +
      '}' +
      '.eng-tool-caret{flex:none;font-size:10px;opacity:.55}' +
      '.eng-tool-card-b{' +
        'background:var(--dsw-alias-bg-layer-1,#fff);border-top:1px solid rgba(14,165,233,.25);' +
        'color:var(--dsw-alias-label-secondary,#334155);' +
        'font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:11px;' +
        'max-height:220px;max-width:100%;overflow:auto;overflow-wrap:anywhere;' +
        'padding:8px 10px;white-space:pre-wrap;word-break:break-word' +
      '}' +

      // ── 错误卡 ────────────────────────────────────────────────────
      '.eng-err-card{' +
        'align-items:flex-start;background:var(--dsw-alias-state-error-tertiary,#fef2f2);' +
        'border:1px solid var(--dsw-alias-state-error-primary,#fca5a5);' +
        'border-radius:8px;color:#991b1b;display:flex;font-size:11.5px;gap:7px;' +
        'line-height:1.55;max-width:100%;min-width:0;overflow-wrap:anywhere;' +
        'padding:8px 10px;word-break:break-word' +
      '}' +
      '.eng-err-card>*{min-width:0;overflow-wrap:anywhere}' +
      '.eng-err-ico{flex:none}' +
      // ── 交互式提问卡（小弹窗） ───────────────────────────────
      '.eng-ask{background:#fff;border:1px solid #c7d2fe;border-radius:12px;box-shadow:0 2px 10px rgba(30,64,175,.10);margin:0 0 11px;overflow:hidden}',
      '.eng-ask-hd{align-items:center;background:linear-gradient(180deg,#eef2ff,#e0e7ff);border-bottom:1px solid #c7d2fe;color:#3730a3;display:flex;font-size:12px;font-weight:700;gap:6px;padding:8px 11px}',
      '.eng-ask-q{color:#334155;font-size:12px;line-height:1.55;padding:9px 11px 2px}',
      '.eng-ask-qh{color:#1e293b;font-weight:700}',
      '.eng-ask-opts{display:flex;flex-direction:column;gap:5px;padding:7px 11px 3px}',
      '.eng-opt{align-items:flex-start;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;cursor:pointer;display:flex;gap:8px;padding:7px 10px;text-align:left;width:100%;box-sizing:border-box}',
      '.eng-opt:hover{background:#eef2ff;border-color:#c7d2fe}',
      '.eng-opt.on{background:#eef2ff;border-color:#6366f1;box-shadow:inset 0 0 0 1px #6366f1}',
      '.eng-opt-mark{border:1.5px solid #94a3b8;flex:none;height:13px;margin-top:1px;width:13px;box-sizing:border-box;background:#fff}',
      '.eng-opt-mark.radio{border-radius:50%}',
      '.eng-opt-mark.box{border-radius:3px}',
      '.eng-opt.on .eng-opt-mark{background:#4f46e5;border-color:#4f46e5}',
      '.eng-opt-txt{flex:1;min-width:0}',
      '.eng-opt-label{color:#1e293b;font-size:11.5px;font-weight:600;line-height:1.4}',
      '.eng-opt-desc{color:#64748b;font-size:10.5px;line-height:1.4;margin-top:2px}',
      '.eng-ask-ft{align-items:center;border-top:1px solid #e2e8f0;display:flex;gap:8px;margin-top:8px;padding:8px 11px}',
      '.eng-ask-state{flex:1;font-size:11px;line-height:1.4;min-width:0}',
      '.eng-ask-state.ok{color:#15803d}',
      '.eng-ask-state.err{color:#b91c1c}',
      '.eng-ask-btn{background:#4f46e5;border:1px solid #4f46e5;border-radius:7px;color:#fff;cursor:pointer;flex:none;font-size:11.5px;font-weight:600;padding:5px 14px}',
      '.eng-ask-btn:hover{background:#4338ca}',
      '.eng-ask-btn:disabled{background:#c7d2fe;border-color:#c7d2fe;cursor:default}',
      // ══ 【PhaseB·第2项】确认卡片表格化 ═════════════════════════════════
      // 用户反馈：步步确认时选项密密麻麻，需要更清爽的排版。
      // 方案：选项改为【表格行】布局（序号 | 单选标记 | 文案 | 说明），
      //   行距与分隔线让信息分层。
      '.eng-ask-tbl{background:#fff;border:1px solid #e2e8f0;border-radius:8px;margin:7px 11px 3px;overflow:hidden}',
      '.eng-ask-tr{align-items:center;background:#fff;border-top:1px solid #f1f5f9;cursor:pointer;display:flex;gap:9px;padding:8px 10px;text-align:left;width:100%;box-sizing:border-box;transition:background .12s}',
      '.eng-ask-tr:first-child{border-top:none}',
      '.eng-ask-tr:hover{background:#f8fafc}',
      '.eng-ask-tr.on{background:#eef2ff;box-shadow:inset 3px 0 0 #6366f1}',
      '.eng-ask-td-no{color:#94a3b8;flex:none;font-size:10.5px;font-weight:700;min-width:15px;text-align:right}',
      '.eng-ask-td-mark{border:1.5px solid #94a3b8;flex:none;height:13px;width:13px;box-sizing:border-box;background:#fff}',
      '.eng-ask-td-mark.radio{border-radius:50%}',
      '.eng-ask-td-mark.box{border-radius:3px}',
      '.eng-ask-tr.on .eng-ask-td-mark{background:#4f46e5;border-color:#4f46e5}',
      '.eng-ask-td-txt{flex:1;min-width:0}',
      '.eng-ask-td-label{color:#1e293b;font-size:11.5px;font-weight:600;line-height:1.4}',
      '.eng-ask-td-desc{color:#64748b;font-size:10.5px;line-height:1.4;margin-top:2px}',
      '.eng-ask-td-tag{background:#e0e7ff;border-radius:5px;color:#4338ca;flex:none;font-size:9.5px;font-weight:700;padding:2px 6px}', +
      // 【本次改动】「推荐」徽标：比「默认」更醒目
      '.eng-ask-td-tag.rec{background:#dcfce7;color:#15803d;border:1px solid #86efac}',
      '.eng-ask-tr.on .eng-ask-td-tag{background:#4f46e5;color:#fff}',
                                    // ══ 【问题1 修复】主对话原生问答卡片 —— 表格化增强 ══════════════════
      // 背景：主对话的问答 UI 由 DSH 官方包 @deepseek-ai/dsh-client-ui-user-questions
      //   渲染在 conversation.composer 插槽，【不由本插件渲染】。
      //   它本身已有结构化 DOM（number/optionLabel/description/badge），
      //   但默认样式把 label 与 description 挤在同一行，视觉上"密密麻麻"。
      //
      // 方案：用【属性选择器】做样式增强 —— 而非改官方包代码。
      //   为什么用 [class*="_option"] 而不是硬编码类名：
      //     官方用的是 CSS Module 哈希类名（如 Mbwy4a_option），
      //     哈希随包版本变化，硬编码会在升级后失效；
      //     部分匹配（*="_option"）跨版本稳定。
      //   仅作用于【工程模式】会话（body.dsh-eng-dock / .eng-qc-scope）。
      '@keyframes eng-qc-pulse{0%,100%{opacity:1}50%{opacity:.45}}',
      // 卡片整体：更清晰的边框与留白
      'body.' + ENG_MODE_CLASS + ' [class*="_card"]:has([class*="_options"]){border-radius:12px}',
      // ═══ 选项表格化（真·表格布局）═══════════════════════════════════
      // 目标：把「序号 | 选项文字 | 说明」排成规整的表格三列，
      //   序号列固定宽度、选项名列左对齐、说明列独占第二行缩进。
      // 实现要点：原生 DOM 是 optionCopy > optionLine > (label, badge, desc)，
      //   label 与 description 是【同级 flex 子项】所以原本挤在一行；
      //   这里把 optionLine 改成【两行网格】，让说明强制换到第二行。

      // ① 表格外框：整体圆角 + 细边框，形成"表"的观感
      'body.' + ENG_MODE_CLASS + ' [class*="_options"]{' +
        'background:#fff!important;border:1px solid #e2e8f0!important;' +
        'border-radius:10px!important;display:flex!important;' +
        'flex-direction:column!important;gap:0!important;overflow:hidden!important}',

      // ② 每个选项 = 一个表格行（行间分隔线）
      'body.' + ENG_MODE_CLASS + ' [class*="_options"]>[class*="_option"]{' +
        'align-items:flex-start!important;background:transparent!important;' +
        'border:none!important;border-top:1px solid #eef2f7!important;' +
        'border-radius:0!important;display:flex!important;gap:10px!important;' +
        'min-height:40px!important;padding:10px 12px!important;' +
        'text-align:left!important;width:100%!important;box-sizing:border-box!important;' +
        'transition:background .12s ease!important}',
      'body.' + ENG_MODE_CLASS + ' [class*="_options"]>[class*="_option"]:first-child{border-top:none!important}',
      'body.' + ENG_MODE_CLASS + ' [class*="_options"]>[class*="_option"]:hover{background:#f8fafc!important}',

      // ③ 第一列：序号（固定宽度 + 等宽数字 + 垂直居中于首行）
      'body.' + ENG_MODE_CLASS + ' [class*="_option"]>[class*="_number"]{' +
        'color:#94a3b8!important;flex:0 0 20px!important;font-size:11px!important;' +
        'font-variant-numeric:tabular-nums!important;font-weight:700!important;' +
        'line-height:1.5!important;padding-top:1px!important;text-align:right!important}',
      // 多选时用勾选框占同一列宽，保证单选/多选两种模式列对齐
      'body.' + ENG_MODE_CLASS + ' [class*="_option"]>[class*="_checkbox"]{' +
        'align-items:center!important;display:inline-flex!important;' +
        'flex:0 0 20px!important;justify-content:center!important;margin-top:1px!important}',

      // ④ 第二列：内容区（占满剩余宽度）
      'body.' + ENG_MODE_CLASS + ' [class*="_option"] [class*="_optionCopy"]{' +
        'display:block!important;flex:1 1 auto!important;min-width:0!important}' +

      // ⑤ 关键：optionLine 改为【纵向两行】——
      //    第一行 = 选项名（+推荐徽标），第二行 = 说明文字。
      //    原先是 flex-row + wrap，所以 label 与 desc 会挤在一行。
      'body.' + ENG_MODE_CLASS + ' [class*="_option"] [class*="_optionLine"]{' +
        'display:flex!important;flex-direction:column!important;' +
        'align-items:flex-start!important;flex-wrap:nowrap!important;gap:2px!important}' +

      // ⑥ 选项名：加粗、深色，作为表格主内容
      'body.' + ENG_MODE_CLASS + ' [class*="_option"] [class*="_optionLabel"]{' +
        'color:#1e293b!important;font-weight:600!important;' +
        'font-size:12.5px!important;line-height:1.45!important;' +
        'display:block!important;width:100%!important}',
      // ⑦ 说明文字：独立第二行、灰色小字（表格的"副行"）
      'body.' + ENG_MODE_CLASS + ' [class*="_option"] [class*="_description"]{' +
        'color:#64748b!important;display:block!important;' +
        'flex:1 0 100%!important;font-size:11px!important;' +
        'line-height:1.5!important;margin-top:1px!important;width:100%!important}',
      // ⑧ 推荐徽标：跟在选项名后，不换行
      'body.' + ENG_MODE_CLASS + ' [class*="_option"] [class*="_badge"]{flex:none!important}',

      // ⑨ 选中态：左侧强调条 + 浅底，强化"当前行"
      'body.' + ENG_MODE_CLASS + ' [class*="_option"][aria-checked="true"]{' +
        'background:rgba(99,102,241,.08)!important;' +
        'box-shadow:inset 3px 0 0 #6366f1!important}' +
      'body.' + ENG_MODE_CLASS + ' [class*="_option"][aria-checked="true"] [class*="_number"]{color:#4f46e5!important}',
      // 进度指示（第 n / N 题）更醒目
      'body.' + ENG_MODE_CLASS + ' [class*="_progress"]{font-variant-numeric:tabular-nums!important;font-weight:600!important}',
      // ── 折叠开关（未闭合的工具结果等）────────────────────────
      '.eng-fold{color:#94a3b8;cursor:pointer;font-size:11px;margin:0 0 8px;padding:3px 0}',
      // ── 【问题4】设置页「工程模式」分区样式（浅色）──────────────
      '.eng-settings{padding:4px 2px;color:var(--dsw-alias-label-primary,#1f2328)}',
      '.eng-settings-hd{align-items:center;display:flex;gap:10px;margin-bottom:14px}',
      '.eng-settings-ico{font-size:20px;line-height:1}',
      '.eng-settings-title{font-size:15px;font-weight:600}',
      '.eng-settings-sub{color:var(--dsw-alias-label-tertiary,#8b95a3);font-size:12px;margin-top:2px}',
      // ── 【需求1】头部右侧「模式说明 / 如何使用」按钮（同官方卡片脚部样式）──
      '.eng-settings-hd-txt{flex:1 1 auto;min-width:0}',
      '.eng-settings-hd-help{display:flex;gap:6px;flex:0 0 auto}',
      '.eng-help-btn{background:transparent;border:1px solid transparent;border-radius:6px;'
        + 'color:var(--dsw-alias-label-secondary,#5b6472);cursor:pointer;'
        + 'font-family:inherit;font-size:12px;line-height:1;padding:5px 8px;transition:background .12s}',
      '.eng-help-btn:hover{background:var(--dsw-alias-bg-hover,rgba(127,127,127,.12));'
        + 'color:var(--dsw-alias-label-primary,#1f2328)}',
      '.eng-help-btn:focus-visible{outline:2px solid var(--dsw-alias-brand-primary,#3b82f6);outline-offset:1px}',
      // ── 【需求1】说明弹窗（两 Tab，外观照抄官方 PresetGuideDialog）────────
      '.eng-guide-mask{background:rgba(0,0,0,.45);bottom:0;left:0;position:fixed;right:0;top:0;'
        + 'z-index:2147483000;display:flex;align-items:center;justify-content:center;padding:24px}',
      '.eng-guide-dlg{background:var(--dsw-alias-bg-base,#fff);border-radius:12px;'
        + 'box-shadow:0 12px 48px rgba(0,0,0,.28);display:flex;flex-direction:column;'
        + 'max-height:min(78vh,720px);max-width:640px;width:100%;overflow:hidden}',
      '.eng-guide-hd{align-items:flex-start;display:flex;gap:12px;padding:20px 22px 12px}',
      '.eng-guide-title{font-size:17px;font-weight:600}',
      '.eng-guide-intro{color:var(--dsw-alias-label-secondary,#5b6472);font-size:12.5px;'
        + 'line-height:1.6;margin-top:6px}',
      '.eng-guide-x{background:transparent;border:0;border-radius:6px;color:#8b95a3;cursor:pointer;'
        + 'flex:0 0 auto;font-size:20px;line-height:1;padding:2px 8px}',
      '.eng-guide-x:hover{background:var(--dsw-alias-bg-hover,rgba(127,127,127,.12));color:#1f2328}',
      '.eng-guide-tabs{display:flex;gap:6px;margin:0 22px 4px;'
        + 'background:var(--dsw-alias-bg-sunken,#f2f4f7);border-radius:8px;padding:3px}',
      '.eng-guide-tab{background:transparent;border:0;border-radius:6px;color:#5b6472;cursor:pointer;'
        + 'flex:1 1 0;font-family:inherit;font-size:13px;padding:7px 10px;transition:background .12s}',
      '.eng-guide-tab.on{background:var(--dsw-alias-bg-base,#fff);color:#1f2328;font-weight:600;'
        + 'box-shadow:0 1px 3px rgba(0,0,0,.08)}',
      '.eng-guide-body{overflow-y:auto;padding:14px 22px 22px;font-size:13px;line-height:1.7}',
      '.eng-guide-h{font-size:14px;font-weight:600;margin:14px 0 8px}',
      '.eng-guide-h:first-child{margin-top:2px}',
      '.eng-guide-p{color:var(--dsw-alias-label-primary,#1f2328);margin:0 0 10px}',
      '.eng-guide-tag{background:var(--dsw-alias-bg-sunken,#f2f4f7);border-radius:5px;color:#5b6472;'
        + 'display:inline-block;font-size:11px;margin:0 0 8px;padding:2px 7px}',
      '.eng-guide-quote{background:var(--dsw-alias-bg-sunken,#f7f8fa);'
        + 'border-left:3px solid var(--dsw-alias-brand-primary,#3b82f6);border-radius:6px;'
        + 'color:var(--dsw-alias-label-primary,#1f2328);margin:0 0 10px;padding:10px 12px}',
      // 深色模式适配（宿主用 .dark 类切换）
      '.dark .eng-guide-dlg{background:#1c1f24}',
      '.dark .eng-guide-title,.dark .eng-guide-h,.dark .eng-guide-p,.dark .eng-guide-quote{color:#e6e8eb}',
      '.dark .eng-guide-intro{color:#9aa4b2}',
      '.dark .eng-guide-tabs{background:#24282e}',
      '.dark .eng-guide-tag{background:#24282e;color:#9aa4b2}',
      '.dark .eng-guide-quote{background:#24282e}',
      '.dark .eng-guide-tab.on{background:#31363d}',
      '.dark .eng-help-btn:hover{color:#e6e8eb}',
      // ── 【需求1】注入到【预设卡片】上的按钮（对齐官方 cardHelp/helpButton）──
      // 官方：.cardFoot 用 flex 两端对齐，左侧 cardHelp（两个 ghost 按钮），
      //   右侧 iconButton（查看图标）。工程模式卡片因无 help，左侧是空的 ——
      //   这里把同款按钮填进去，视觉位置与内置模式完全一致。
      '.eng-preset-help{display:flex;gap:2px;align-items:center;flex:1 1 auto;min-width:0}',
      '.eng-preset-help-btn{background:transparent;border:0;border-radius:6px;'
        + 'color:var(--dsw-alias-label-secondary,#5b6472);cursor:pointer;'
        + 'font-family:inherit;font-size:12px;line-height:1;padding:4px 8px;'
        + 'transition:background .12s,color .12s;white-space:nowrap}',
      '.eng-preset-help-btn:hover{background:var(--dsw-alias-bg-hover,rgba(127,127,127,.12));'
        + 'color:var(--dsw-alias-label-primary,#1f2328)}',
      '.eng-preset-help-btn:focus-visible{outline:2px solid var(--dsw-alias-brand-primary,#3b82f6);'
        + 'outline-offset:1px}',
      '.dark .eng-preset-help-btn{color:#9aa4b2}',
      '.dark .eng-preset-help-btn:hover{color:#e6e8eb}',
      // ── 【滚动区】设置正文自成滚动容器（用户要求：能上下滑的滑块）──────
      // 背景：宿主设置面板的 .options 本身就有 overflow-y:auto，但内容不足时
      //   不会出现滑块（实测本分区约 690px < 可用约 720px），用户看不到滚动条。
      //   随设置项增多虽会自动出现，但用户明确要求"现在就调出来"。
      // 做法：给正文加【有上限的】滚动区。上限由 --eng-opts-h（= 宿主面板高度）
      //   减去本分区非正文部分的占用（宿主 header 54 + options 下内边距 24 +
      //   本区块标题区约 54 + 底部按钮区约 43 + 本区块内边距 8 ≈ 183，取 200
      //   留出余量）。余量取偏大值是刻意的：宁可正文区略矮，也不要让宿主
      //   .options 再溢出 —— 那会变成两条滚动条。
      // 滚动条外观不用自己画：宿主 ui-theme 的 scrollbar.css 用的是
      //   【无作用域】::-webkit-scrollbar 规则，任何滚动元素都自动套用；
      //   且面板已把 --dsh-scrollbar-thumb 重绑到本层级色板（深浅色自适应）。
      '.eng-settings-body{background:var(--dsw-alias-bg-layer-2,#f6f7f9);border:1px solid var(--dsw-alias-border-l1,#e5e7eb);border-radius:10px;padding:14px;text-align:left;' +
        'max-height:max(180px, calc(var(--eng-opts-h, 800px) - 200px));' +
        'overflow-y:auto;' +
      '}',
      // ── 【版式 v2】主栏目 + 子项卡片（用户指定版式）──────────────────
      // 结构：主栏目大标题 → 卡片容器 → 若干子项行（左标题 + 右箭头 ›）
      '.eng-grp{margin:0 0 18px}',
      '.eng-grp:last-child{margin-bottom:0}',
      '.eng-grp-title{color:var(--dsw-alias-label-primary,#1f2328);font-size:17px;font-weight:700;letter-spacing:.01em;margin:0 0 9px;padding:0 2px}',
      '.eng-grp-card{background:var(--dsw-alias-bg-layer-1,#fff);border:1px solid var(--dsw-alias-border-l1,#e5e7eb);border-radius:10px;overflow:hidden}',
      // 子项行：整行可点区域 + 行间细分隔线（首行无线）
      '.eng-row{align-items:center;box-sizing:border-box;display:flex;gap:10px;min-height:46px;padding:10px 14px;transition:background .12s ease}',
      '.eng-row+.eng-row{border-top:1px solid var(--dsw-alias-border-l1,#f0f2f5)}',
      '.eng-row.clickable{cursor:pointer}',
      '.eng-row.clickable:hover{background:var(--dsw-alias-bg-layer-3,#f7f8fa)}',
      '.eng-row:focus-visible{background:var(--dsw-alias-bg-layer-3,#f7f8fa);outline:2px solid var(--dsw-alias-button-primary-fill,#2563eb);outline-offset:-2px}',
      '.eng-row-txt{flex:1;min-width:0}',
      '.eng-row-label{color:var(--dsw-alias-label-primary,#1f2328);font-size:13.5px;line-height:1.5;overflow-wrap:break-word}',
      '.eng-row-hint{color:var(--dsw-alias-label-tertiary,#8b95a3);font-size:11.5px;line-height:1.65;margin-top:3px}',
      // 右箭头：灰色、不换行、不参与拉伸
      '.eng-row-chev{color:#c0c4cc;flex:none;font-size:16px;line-height:1;transform:translateY(-1px)}',
      // ── 【子目录内联展开】embedded 版式 ────────────────────────────────
      // 用户要求：「"内存与并发"和"模型自动接替"统一放在"设计过程优化"的
      //   子目录下面」。
      // 这两个面板原来各自是一个主栏目（大标题 + 独立卡片）。收进子目录后，
      //   如果继续画卡片壳就会出现"卡片套卡片 + 两层标题"，所以 EngGroup 在
      //   embedded 模式下只输出内容；这里给展开区加一条左内缩 + 细分隔线，
      //   让它看起来确实是"这一行的子级"，而不是又一个平级栏目。
      '.eng-grp-embedded{border-top:1px solid var(--dsw-alias-border-l1,#f0f2f5);padding:2px 14px 12px 22px}',
      // ── 【模型自动接替】10 槽位表格（默认收起，展开后显示）────────────
      // 目标：紧凑。10 行必须能在一屏内看完，所以行高与字号都压到最小可用值。
      '.eng-mc-table{border-top:1px solid var(--dsw-alias-border-l1,#e5e7eb);margin-top:6px;padding-top:6px}',
      '.eng-mc-head,.eng-mc-row{align-items:center;display:flex;gap:6px;padding:3px 0}',
      '.eng-mc-head{color:var(--dsw-alias-label-tertiary,#8b95a3);font-size:10.5px;padding-bottom:2px}',
      '.eng-mc-head .eng-mc-cell{font-weight:600;letter-spacing:.02em}',
      '.eng-mc-idx{color:var(--dsw-alias-label-tertiary,#8b95a3);flex:none;font-size:11px;text-align:center;width:16px}',
      '.eng-mc-cell{flex:1 1 0;min-width:0}',
      // ⚠️ 颜色必须【显式】写死，绝不能用 color:inherit ——
      //    实测（2026-10-30 用户截图）输入框变成黑底黑字、完全看不见内容，
      //    根因就是 inherit 把容器的深色前景继承了下来。
      '.eng-mc-in{background:#fff;border:1px solid #d8dde5;border-radius:6px;color:#1f2328;flex:1 1 0;font-family:inherit;font-size:11.5px;min-width:0;padding:3px 7px}',
      '.eng-mc-in::placeholder{color:#a8b0bb}',
      '.eng-mc-in:focus{border-color:#2563eb;box-shadow:0 0 0 2px rgba(37,99,235,.15);outline:none}',
      // 下拉：原生 select（点开是系统列表，可按首字母跳转，最稳）
      '.eng-mc-sel{appearance:none;-webkit-appearance:none;background:#fff;border:1px solid #d8dde5;border-radius:6px;color:#1f2328;cursor:pointer;flex:1 1 0;font-family:inherit;font-size:11.5px;min-width:0;padding:3px 20px 3px 7px;text-overflow:ellipsis}',
      '.eng-mc-sel:focus{border-color:#2563eb;box-shadow:0 0 0 2px rgba(37,99,235,.15);outline:none}',
      '.eng-mc-selwrap{display:flex;flex:1 1 0;min-width:0;position:relative}',
      // 档位列窄一些（provider / model 才是主要信息）
      '.eng-mc-effort{flex:0 0 68px}',
      '.eng-mc-head .eng-mc-effort{flex:0 0 68px}',
      '.eng-mc-selwrap::after{color:#8b95a3;content:"\u25be";font-size:9px;pointer-events:none;position:absolute;right:6px;top:50%;transform:translateY(-50%)}',
      '.eng-mc-ops{display:flex;flex:none;gap:3px;width:auto}',
      '.eng-mc-ops .eng-conn-btn.tiny{font-size:10.5px;padding:1px 6px}',
      '.eng-mc-ft{align-items:center;display:flex;gap:8px;padding-top:6px}',
      '.eng-mc-ok{color:#15803d;font-size:11px}',
      '.eng-mc-err{color:#b91c1c;font-size:11px;overflow-wrap:anywhere}',
      '.eng-mc-empty{background:rgba(37,99,235,.06);border:1px dashed #b9cdf3;border-radius:6px;color:#3b5bdb;font-size:11px;line-height:1.55;margin-top:6px;padding:6px 9px}',
      // 行内"手动填写"切回链接
      '.eng-mc-manual{background:none;border:0;color:#2563eb;cursor:pointer;font-size:10.5px;padding:0 0 0 4px;text-decoration:underline}',
      // ── "+ 添加模型" 一行（用户要求：默认 3 个，+ 让他们自己加）──────────
      '.eng-mc-addrow{display:flex;justify-content:center;padding:4px 0 2px}',
      '.eng-mc-add{background:rgba(37,99,235,.08);border:1px dashed #9db8ee;border-radius:6px;color:#2563eb;cursor:pointer;font-family:inherit;font-size:11px;padding:3px 14px;transition:background .15s}',
      '.eng-mc-add:hover{background:rgba(37,99,235,.16)}',
      // 每行的删除按钮（配合 + 使用；只剩 1 行时置灰）
      '.eng-mc-del{background:none;border:0;color:#b91c1c;cursor:pointer;font-size:11px;line-height:1;padding:2px 4px}',
      '.eng-mc-del:disabled{color:#c8ccd4;cursor:default}',
      // ── 【连接区】状态徽标 + 连接按钮（当前仅 UI，真实连接后续接入）──────
      '.eng-chip{align-items:center;border-radius:999px;display:inline-flex;flex:none;font-size:11px;font-weight:600;gap:5px;padding:2px 9px;white-space:nowrap}',
      '.eng-chip-dot{border-radius:50%;flex:none;height:6px;width:6px}',
      '.eng-chip.on{background:rgba(22,163,74,.12);color:#15803d}',
      '.eng-chip.on .eng-chip-dot{background:#16a34a}',
      '.eng-chip.off{background:rgba(148,163,184,.16);color:#64748b}',
      '.eng-chip.off .eng-chip-dot{background:#94a3b8}',
      '.eng-chip.idle{background:rgba(148,163,184,.12);color:#8b95a3}',
      '.eng-chip.idle .eng-chip-dot{background:#cbd5e1}',
      '.eng-conn-btn{background:var(--dsw-alias-button-primary-fill,#2563eb);border:0;border-radius:7px;color:#fff;cursor:pointer;flex:none;font-size:11.5px;font-weight:600;padding:5px 14px;transition:opacity .15s ease}',
      '.eng-conn-btn:hover{opacity:.88}',
      '.eng-conn-btn:active{opacity:.76}',
      '.eng-conn-btn:disabled{opacity:.5;cursor:default}',
      // 【启动按钮】次级样式，与「连接」区分但同排
      '.eng-conn-btn.alt{background:var(--dsw-alias-button-secondary-fill,#475569);margin-left:6px}',
      '.eng-conn-btn.tiny{font-size:10.5px;padding:3px 9px;border-radius:6px}',
      // 连接行容器：按钮并排 + 日志区在下方
      '.eng-conn-block{display:flex;flex-direction:column;gap:6px}',
      '.eng-conn-block .eng-row.conn{align-items:center;display:flex;gap:8px}',
      '.eng-conn-log{background:rgba(15,23,42,.04);border-radius:8px;padding:6px 8px}',
      '.eng-conn-log-hd{align-items:center;display:flex;gap:8px;justify-content:space-between}',
      '.eng-conn-log-tg{cursor:pointer;font-size:11.5px;font-weight:600}',
      '.eng-conn-log-sent{color:#b91c1c;font-size:11px;margin-top:4px}',
      '.eng-conn-log-pre{background:rgba(15,23,42,.06);border-radius:6px;font-family:ui-monospace,Consolas,monospace;font-size:10.5px;line-height:1.5;margin:6px 0 0;max-height:190px;overflow:auto;padding:7px 9px;white-space:pre-wrap;word-break:break-word}',
      // ── 【工程环境检查】连接区上方的新栏目（用户需求 3）───────────────
      '.eng-env{display:flex;flex-direction:column;gap:8px}',
      '.eng-env-top{align-items:center;display:flex;gap:8px;justify-content:space-between}',
      '.eng-env-sum{font-size:11.5px;line-height:1.6;color:var(--dsw-alias-label-tertiary,#8b95a3);flex:1;min-width:0}',
      '.eng-env-list{display:flex;flex-direction:column;gap:4px;margin-top:2px}',
      '.eng-env-item{align-items:flex-start;display:flex;gap:8px;padding:5px 0;border-top:1px dashed var(--dsw-alias-border-tertiary,rgba(148,163,184,.35))}',
      '.eng-env-item:first-child{border-top:0}',
      '.eng-env-dot{border-radius:50%;flex:none;height:7px;width:7px;margin-top:5px}',
      '.eng-env-dot.ok{background:#16a34a}',
      '.eng-env-dot.warn{background:#f59e0b}',
      '.eng-env-dot.missing{background:#dc2626}',
      '.eng-env-dot.unknown{background:#94a3b8}',
      '.eng-env-txt{flex:1;min-width:0}',
      '.eng-env-name{font-size:12.5px;font-weight:600}',
      '.eng-env-name .opt{font-size:10.5px;font-weight:500;opacity:.65;margin-left:5px}',
      '.eng-env-det{font-size:11px;line-height:1.55;color:var(--dsw-alias-label-tertiary,#8b95a3);word-break:break-all;margin-top:1px}',
      '.eng-env-fix{font-size:11px;line-height:1.55;color:#b45309;margin-top:2px}',
      '.eng-env-prompt{background:rgba(15,23,42,.05);border-radius:8px;font-family:ui-monospace,Consolas,monospace;font-size:10.5px;line-height:1.55;margin:6px 0 0;max-height:240px;overflow:auto;padding:8px 10px;white-space:pre-wrap;word-break:break-word}',
      '.eng-env-acts{display:flex;flex-wrap:wrap;gap:6px}',
      '.eng-env-note{font-size:11px;line-height:1.6;color:var(--dsw-alias-label-tertiary,#8b95a3)}',
      '.eng-path-row{align-items:center;display:flex;gap:8px;margin-top:6px}',
      '.eng-path-in{background:var(--dsw-alias-bg-layer-2,#fff);border:1px solid var(--dsw-alias-border-secondary,#cbd5e1);border-radius:7px;flex:1;font-size:11.5px;min-width:0;padding:5px 9px;color:inherit}',
      // 开关行（行内只放开关本体，标签由 .eng-row 提供）
      '.eng-row.sw{align-items:center}',
      '.eng-row.sw .eng-sw{flex:none}',
      // 兼容旧类的样式（.eng-set-note 等仍被新版提示文案复用）
      '.eng-set-note{font-size:12px;line-height:1.75;margin:0 0 10px}',
      '.eng-set-note.dim{color:var(--dsw-alias-label-tertiary,#8b95a3);font-size:11.5px;margin:10px 0 0}',
      // 开关
      '.eng-sw-row{align-items:center;display:flex;gap:12px;justify-content:space-between}',
      '.eng-sw-txt{flex:1;min-width:0}',
      '.eng-sw-label{font-size:13px;font-weight:600}',
      '.eng-sw-hint{color:var(--dsw-alias-label-tertiary,#8b95a3);font-size:11.5px;line-height:1.65;margin-top:3px}',
      // ── 开关：颜色【自带 + !important】，不依赖皮肤变量 ────────────────
      // 用户反馈：「换了一个皮肤后，按键就不一样了」，并希望"颜色相反的自适配"。
      // 根因：原来 on 态用 var(--dsw-alias-button-primary-fill,#2563eb)。
      //   皮肤可以把这个名字定义成浅色，于是 **开/关两态看起来一模一样**
      //   （都是一个白胶囊），完全分不出状态 —— 正是截图里的样子。
      // 修法：
      //   ① 轨道/滑块改用【固定字面色】—— 中灰在浅底与深底上都可见，
      //      蓝色是饱和强调色；滑块加描边+阴影，落在浅轨道上也不"融进去"。
      //   ② 加 !important：本控件是第三方插件自有 class，皮肤样式表
      //      无法预料它，却可能通过宽泛选择器把颜色改掉。这是本文件里
      //      唯一使用 !important 的地方，范围严格限定在这个开关上。
      //   ③ 不再需要深色模式单独覆盖（中灰在两种底色上都成立）。
      '.eng-sw{background:rgba(118,128,144,.62)!important;border:0;border-radius:999px;' +
        'box-shadow:inset 0 0 0 1px rgba(15,23,42,.18);cursor:pointer;flex:none;' +
        'height:22px;padding:0;position:relative;transition:background .18s ease;width:40px}' +
      '.eng-sw.on{background:#2563eb!important;box-shadow:inset 0 0 0 1px rgba(37,99,235,.6)}' +
      '.eng-sw-knob{background:#fff!important;border-radius:50%;' +
        'box-shadow:0 1px 3px rgba(0,0,0,.35),0 0 0 1px rgba(15,23,42,.10);' +
        'height:18px;left:2px;position:absolute;top:2px;' +
        'transition:transform .18s ease;width:18px}' +
      '.eng-sw.on .eng-sw-knob{transform:translateX(18px)}',
      '.eng-settings-empty-t{font-size:13px;font-weight:600;margin-bottom:6px}',
      '.eng-settings-empty-d{color:var(--dsw-alias-label-tertiary,#8b95a3);font-size:12px;line-height:1.7}',
      '.eng-settings-ft{display:flex;justify-content:flex-end;margin-top:14px}',
      '.eng-settings-close{background:var(--dsw-alias-button-primary-fill,#2563eb);border:0;border-radius:6px;color:#fff;cursor:pointer;font-size:12px;padding:6px 16px}',
      '.eng-settings-close:hover{opacity:.88}',
      // ══ 【布局 v3】响应式断点 ════════════════════════════════════════
      // 设计原则（按用户要求）：
      //   · 面板永远是【流内并排】的侧边栏，绝不覆盖/悬浮在内容上；
      //   · 宽度 300~400px，且硬上限 30vw；
      //   · 视口不足以同时容纳时 → 自动折叠为悬浮按钮（需要时再展开），
      //     而不是硬挤成覆盖层。
      // 面板展开宽度恒在 350~450px（用户明确要求），不做断点性缩水；
      // 空间不足时由用户折叠，而不是把面板压得难以阅读。
      // 【3:1 比例】不再需要断点缩水 —— 面板宽度由 JS 按 25% 精确计算；
      // 空间不足时由 _calcPaneWidth 返回 0（自动折叠），主区永远 ≥600px。

      // ── 主区域（第 2 列）：最低阅读宽度 ──────────────────────────────
      // ── 【问题"左侧项目栏边框跳出来"根因修复·关键】────────────────────
      // 原选择器是 `div[style*="grid-template-columns"]>*` —— `>*` 命中
      //   【全部三个 grid 子项】，包括：
      //     · 第 1 列 = 左侧项目/导航栏（宿主固定 280px）
      //     · 第 2 列 = 主对话列（本规则真正想保的那一列）
      //     · 第 3 列 = 右列（官方预览，已被上一次修复限定）
      //   于是 `min-width:520px` 把【左侧项目栏从 280px 强行撑到 520px】，
      //   它的右边框就"跳"到了屏幕中间 —— 这正是用户说的
      //   「一点击子代理，旁边的项目的边框就又跳出来了」。
      // 正确做法：只给【第 2 列】设最低宽度，用 :nth-child(2) 精确定位。
      //   第 1 列保持宿主自己的 280px（由 --eng-sb 反映），第 3 列由
      //   我方的列宽变量控制，两者都不该被这条规则触碰。
      'body.' + DOCK_CLASS + '.eng-pane-open div[style*="grid-template-columns"]>:nth-child(2){' +
        'min-width:var(--eng-main-min)}' +

      // ══ 【布局 v11 · 真三列（文档流）】═══════════════════════════════
      // 用户的铁律：左/中/右三根柱子并排平铺，右柱绝不压中柱。
      //
      // 宿主真实结构（实测）：.pI_x6G_frame 是 3 列 grid，
      //   内联样式 grid-template-columns: 280px minmax(0px,1fr) 0px;
      //   第 3 列（detailsCol）默认 0px，插件面板渲染在 overlayLayer（覆盖层）。
      //
      // 方案：用【样式表 !important】覆盖宿主内联 grid ——
      //   CSS 层叠规则：作者 !important 声明 > 内联普通声明，
      //   且宿主自身的类规则特异性更低，所以稳定生效（已实测）。
      //   第 3 列从 0px 变为面板宽度 → 中间列被 grid 物理挤压，
      //   气泡文字自动换行适应，绝不重叠。
      //
      // 面板本体：改为渲染进第 3 列（见 SubagentConsole 的挂载点调整），
      //   但保守起见同时保留 overlay 渲染路径 + 将面板自身改为
      //   position:relative 并用 grid-column 定位到第 3 列。
      // ── 【问题3 修复·关键】两个条件缺一不可 ────────────────────────────
      // ① body.dsh-eng-dock      —— 本插件接管右列的会话
      // ② body.eng-pane-open     —— 且【当前确实由我方占列】（未给官方让位）
      // 只写 ① 会在"让位"期间仍然覆盖宿主的 grid，把官方右列（文件预览）
      //   的宽度写死成 var(--eng-pane) → 官方预览布局错乱、关闭按钮点不到。
      //   用户反馈的"代码展示页关也关不了"根因即此。
      // 让位时我方不输出任何 grid 规则 → 宿主的【内联】grid-template-columns
      //   自然生效，官方右列按自己的 openRightbar 逻辑拿到正确宽度。
      'body.' + DOCK_CLASS + '.eng-pane-open div[style*="grid-template-columns"]{' +
        'grid-template-columns:var(--eng-sb) minmax(0px,1fr) var(--eng-pane) !important;' +
        'transition:grid-template-columns .26s cubic-bezier(.4,0,.2,1) !important;' +
      '}' +
      // 让位期间：绝不能再写 0px 版本（那会把官方右列压扁）。
      //   这里刻意【没有】:not(.eng-pane-open) 的规则 —— 撤掉覆盖即可。
      // 【关键】overlayLayer 默认铺满整帧（绝对定位的覆盖层）。
      // 用 display:contents 把它"透明化"：其子元素（面板）直接提升为
      // frame 的 grid item，从而可以 grid-column:3 落进第三列 ——
      // 这就是面板从"覆盖层"进入"文档流三列"的通道。
      // 【问题3 修复】同样只在 eng-pane-open 时生效：
      //   让位期间若仍把 overlayLayer 改成 display:contents，
      //   官方右列里的覆盖层布局会被打乱（预览/弹层定位错乱）。
      //   撤掉覆盖 = 官方自己那套绝对定位语义完整恢复。
      'body.' + DOCK_CLASS + '.eng-pane-open div[class*="overlayLayer"]{' +
        'display:contents !important;' +
      '}' +
      // ── 【P0-4 修复·保护其他 overlay 住户】────────────────────────────
      // overlayLayer 是【共享】的列表槽：0.2.0 里除本插件外还有配额提示、
      //   插件刷新 toast、日程删除 toast 等住户（见官方 slot 目录的
      //   `shell.overlay` occupants）。display:contents 会让它们也变成
      //   frame 的 grid item，被自动排进三列 → toast 位置错乱。
      // 因此把【非本插件】的子项显式钉回"覆盖整帧"的语义。
      //   注意必须同时排除 .eng-fab（收起态小标签，也是本插件的子项，
      //   它自带 position:fixed 且靠右贴边，不能被拉伸成整帧）。
      // 原 `.overlayLayer > *{pointer-events:auto}` 仍匹配（display:contents
      //   只影响父盒子，不影响后代选择器），点击语义不变。
      'body.' + DOCK_CLASS + '.eng-pane-open div[class*="overlayLayer"] > *:not(.eng-console):not(.eng-fab){' +
        'grid-area:1 / 1 / -1 / -1 !important;' +
        'z-index:20;' +
      '}' +

      // ══ 【布局 v10 · 内容铺满主区】══════════════════════════════════
      // 问题：宿主给消息卡片/输入卡设了 max-width:688px 且居中。
      // 让位后内容根变 990px，卡片仍 688 居中 → 右侧空出一条 + 漏出背景装饰图，
      // 看起来"气泡缩在左边、像被挤压"。
      // 修复：让位态（body.dsh-eng-dock）下解除限宽并改为铺满，
      //       卡片自身有内边距，铺满后仍保持可读行宽。
      // ── 【问题3 修复】必须同时要求 eng-pane-open ──────────────────────
      // 这些"解除限宽、铺满"的规则是为【我方占列、主区被压窄】而设的。
      //   若在"给官方让位"期间仍然生效，会把主区内容按错误宽度铺满
      //   （官方右列同时占位时，行宽与留白都会错乱）。
      //   → 统一加 .eng-pane-open，让位时这些规则整体不生效。
      'body.' + DOCK_CLASS + '.eng-pane-open [class*="wSkVaW_root"] > *,' +
      'body.' + DOCK_CLASS + '.eng-pane-open [class*="wSkVaW_scrollBody"] > *{' +
        'max-width:100% !important;' +
        'width:100% !important;' +
      '}' +
      // 居中层（composerStack/composerHero）：取消水平居中的 margin/auto，
      // 改为撑满 + 保持内边距，让输入卡与气泡同宽对齐
      'body.' + DOCK_CLASS + '.eng-pane-open [class*="composerStack"],' +
      'body.' + DOCK_CLASS + '.eng-pane-open [class*="composerHero"],' +
      'body.' + DOCK_CLASS + '.eng-pane-open [class*="composerSeat"],' +
      'body.' + DOCK_CLASS + '.eng-pane-open [class*="uV2eYG_root"]{' +
        'max-width:100% !important;width:100% !important;' +
        'margin-left:0 !important;margin-right:0 !important;' +
        'padding-left:18px !important;padding-right:18px !important;' +
        'box-sizing:border-box !important;' +
      '}' +
      // hero 大标题区（首页）：同样铺满，避免缩在左边
      'body.' + DOCK_CLASS + '.eng-pane-open [class*="uV2eYG_hero"]{' +
        'max-width:100% !important;width:100% !important;' +
      '}' +
      // 输入卡（白底圆角）：撑满除 padding 外的全部宽度，与气泡对齐
      'body.' + DOCK_CLASS + '.eng-pane-open [class*="uV2eYG_card"],' +
      'body.' + DOCK_CLASS + '.eng-pane-open [class*="uV2eYG_grow"]{' +
        'max-width:100% !important;width:100% !important;' +
      '}' +

      // ── 右侧面板（25%）：内部自成滚动体系，绝不撑破宽度 ───────────
      // （换行/滚动的基础规则已在上方 .eng-log 与 .eng-tool-card-b 定义，
      //   这里只补"面板 25% 宽度下"的额外约束与目录列表）
      // 【布局 v12】这里原先把目录列【写死 190px】，把 .eng-tree 自己的
      //   flex-basis:var(--eng-tree-w, clamp(190px,42%,300px)) 完全覆盖掉了
      //   （选择器权重 0,2,0 > 0,1,0）—— 于是 JS 写的 --eng-tree-w 从来没生效，
      //   用户看到的就是"列宽不跟面板走、长中文名一律被截断"。
      //   现在改为同一条自适应表达式：面板变宽 → 目录列按 42% 长，上限 300px。
      '.eng-console>.eng-tree{min-width:0;' +
        'flex:0 0 var(--eng-tree-w, clamp(190px, 42%, 300px))}' +
      '.eng-console>.eng-detail{min-width:0;flex:1 1 0}' +
      '.eng-console>*{min-width:0;max-width:100%}' +
      '.eng-detail>*,.eng-tree>*{min-width:0;max-width:100%}' +
      '.eng-log,.eng-tool-card-b,.eng-tree-list{overscroll-behavior:contain}' +
      '.eng-tree-list{overflow-x:hidden}' +
      // 面板内横向永不滚动：任何内容都纵向消化（25% 宽的硬约束）
      '.eng-detail{overflow-x:hidden}' +
      // ── 【零件进度表】子代理列表下方（用户要求：红线下方放零件表）────────
      // 视觉上刻意比子代理列表"轻"：小字号、细分隔线、状态用色块分辨，
      //   避免和上面的子代理频道抢注意力。
      '.eng-pb-sep{background:var(--dsw-alias-border-l1,#e5e7eb);flex:none;height:1px;margin:6px 0 0}' +
      // ── 左列下半区容器（只放零件表）────────────────────────────────────
      // 【2026-11-07 · 布局 v12】用户要求：
      //   「把那个零件表扩大，扩大到 50% 的大小，然后那个子代理目录和模型表
      //     各占左边的 50%，自适应即可。然后那个压缩历史移到右边那个绿色的框」
      //   ⇒ 左列 = 子代理目录 50% + 零件表 50%，两块各自独立滚动；
      //      压缩历史整块搬到右列详情区（见 .eng-dtabs / EngCompactPage）。
      //   用 flex-basis:50% + min-height:0 实现"严格五五分且都能滚"，
      //   任何一块内容再多也不会把另一块挤没（两边都是 min-height:0 的滚动容器）。
      '.eng-lower{border-top:1px solid var(--dsw-alias-border-l1,#e5e7eb);display:flex;' +
        'flex:1 1 50%;flex-direction:column;min-height:0;overflow:hidden}' +
      '.eng-parts-board{display:flex;flex:1 1 auto;flex-direction:column;gap:2px;' +
        'max-height:none;min-height:0;overflow-y:auto;padding:6px 10px 8px}' +
      // 紧凑版（左列窄区）：省掉右侧留白，允许更长中文名
      '.eng-parts-board.compact{padding:6px 8px 4px;max-height:none}' +
      '.eng-parts-board.compact .eng-pb-name{font-size:11px}' +
      '.eng-parts-board.compact .eng-pb-row{padding:2px 3px;gap:4px}' +
      '.eng-pb-cap{align-items:center;display:flex;gap:6px;padding:0 1px 3px}' +
      '.eng-pb-cap-t{color:var(--dsw-alias-label-secondary,#475569);flex:1;font-size:11.5px;font-weight:600}' +
      '.eng-pb-cap-n{color:var(--dsw-alias-label-tertiary,#8b95a3);font-size:11px}' +
      '.eng-pb-refresh{background:transparent;border:0;border-radius:5px;color:var(--dsw-alias-label-tertiary,#8b95a3);cursor:pointer;font-size:12px;padding:2px 5px}' +
      '.eng-pb-refresh:hover{background:var(--dsw-alias-interactive-bg-hover,rgba(15,23,42,.05))}' +
      '.eng-pb-empty{color:var(--dsw-alias-label-tertiary,#8b95a3);font-size:11.5px;padding:6px 1px}' +
      '.eng-pb-list{display:flex;flex-direction:column;gap:1px;min-height:0}' +
      '.eng-pb-row{align-items:center;border-radius:6px;display:flex;gap:6px;padding:3px 4px}' +
      '.eng-pb-row:hover{background:var(--dsw-alias-interactive-bg-hover,rgba(15,23,42,.04))}' +
      '.eng-pb-name{color:var(--dsw-alias-label-primary,#1f2328);flex:1;font-size:11.5px;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
      '.eng-pb-room{color:var(--dsw-alias-label-tertiary,#94a3b8);flex:none;font-size:10.5px;max-width:38%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
      '.eng-pb-badge{border-radius:9px;flex:none;font-size:10px;font-weight:600;padding:1px 6px}' +
      '.eng-pb-badge.idle{background:rgba(148,163,184,.16);color:#64748b}' +
      '.eng-pb-badge.run{background:rgba(37,99,235,.14);color:#2563eb}' +
      '.eng-pb-badge.ok{background:rgba(22,163,74,.15);color:#15803d}' +
      '.eng-pb-badge.warn{background:rgba(234,179,8,.18);color:#a16207}' +
      '.eng-pb-badge.bad{background:rgba(220,38,38,.15);color:#b91c1c}' +
      // 「等待 SW」用中性偏冷色，与"正在建"的蓝区分开：排队不是干活。
      '.eng-pb-badge.wait{background:rgba(14,116,144,.14);color:#0e7490}' +
      '.eng-pb-ft{align-items:center;border-top:1px solid var(--dsw-alias-border-l1,#f0f2f5);display:flex;flex-wrap:wrap;gap:8px;margin-top:4px;padding-top:4px}' +
      '.eng-pb-stat{font-size:10.5px;color:var(--dsw-alias-label-tertiary,#8b95a3)}' +
      '.eng-pb-stat.ok{color:#15803d}' +
      '.eng-pb-stat.run{color:#2563eb}' +
      '.eng-pb-stat.warn{color:#a16207}' +
      '.eng-pb-stat.bad{color:#b91c1c}' +
      '.eng-pb-stat.wait{color:#0e7490}' +
      '.eng-pb-alert{color:#b91c1c;font-size:10.5px;font-weight:600}' +
      // ── 【压缩历史】右列详情区的独立一页（用户截图里的「绿框」）─────────
      // 【布局 v12】用户要求：
      //   「那个压缩历史移到右边那个绿色的框（占 1/4）……左边的选择子代理的时候，
      //     右边会展示出来那个实际的流程，压缩历史也是同样的，左边选择了那个
      //     子代理，右边就有那个子代理展示页和对应的压缩历史。然后那个压缩历史
      //     那个页面要自适应」
      //   ⇒ 压缩历史从"左列底部固定小块"改成"右列的第二个页签"，
      //      它填满 .eng-detail（即官方右列宽度，约 1/4 屏），内部自适应。
      //   · 高度：flex:1 1 auto 填满 .eng-dtabs-body，绝不撑破父容器；
      //   · 字号：clamp(10px, 1.9cqw, 12.5px) 跟着【容器宽度】缩放（cqw）；
      //   · 宽度：.eng-cp 设 container-type:inline-size 作 cqw 锚点。
      '.eng-cp{background:transparent;box-sizing:border-box;' +
        'container-type:inline-size;container-name:engcp;' +
        'display:flex;flex:1 1 auto;flex-direction:column;' +
        'min-height:0;overflow:hidden;padding:4px 2px 6px}' +
      '.eng-cp-cap{align-items:center;display:flex;flex:none;gap:6px;padding:0 2px 6px}' +
      '.eng-cp-cap-t{color:var(--dsw-alias-label-secondary,#475569);flex:1;font-weight:600;' +
        'font-size:clamp(11px, 2.6cqw, 13px);' +
        'overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
      '.eng-cp-cap-n{color:var(--dsw-alias-label-tertiary,#8b95a3);' +
        'font-size:clamp(10px, 2.2cqw, 12px);flex:none}' +
      '.eng-cp-tabs{display:flex;flex:none;gap:3px}' +
      '.eng-cp-tab{background:transparent;border:1px solid transparent;border-radius:5px;' +
        'color:var(--dsw-alias-label-tertiary,#8b95a3);cursor:pointer;' +
        'font-size:clamp(10px, 2.2cqw, 12px);' +
        'line-height:1.4;padding:2px 7px}' +
      '.eng-cp-tab:hover{background:var(--dsw-alias-interactive-bg-hover,rgba(15,23,42,.06));' +
        'color:var(--dsw-alias-label-secondary,#475569)}' +
      '.eng-cp-tab.on{background:rgba(37,99,235,.12);border-color:rgba(37,99,235,.35);' +
        'color:#2563eb;font-weight:600}' +
      // ── 【自适应】摘要卡：一句话说完这次压缩救回了什么 ────────────────
      '.eng-cp-sum{display:flex;flex:none;flex-wrap:wrap;gap:4px 10px;' +
        'border-bottom:1px solid var(--dsw-alias-border-l1,#eef1f5);' +
        'margin-bottom:5px;padding:0 2px 7px}' +
      '.eng-cp-sum-i{color:var(--dsw-alias-label-tertiary,#8b95a3);' +
        'font-size:clamp(9.5px, 2.1cqw, 11.5px)}' +
      '.eng-cp-sum-i b{color:var(--dsw-alias-label-secondary,#475569);font-weight:600}' +
      '.eng-cp-list{display:flex;flex:1 1 auto;flex-direction:column;gap:1px;min-height:0;' +
        'overflow-y:auto;overscroll-behavior:contain;padding-right:2px}' +
      // 行：时间 / 名称 / L0 / 补回 / 省字数 —— 窄容器下自动折行，绝不横向溢出
      '.eng-cp-row{align-items:center;border-radius:6px;display:flex;flex-wrap:wrap;' +
        'gap:4px 6px;padding:4px 5px;font-size:clamp(10px, 2.1cqw, 12px);min-width:0}' +
      '.eng-cp-row:hover{background:var(--dsw-alias-interactive-bg-hover,rgba(15,23,42,.05))}' +
      '.eng-cp-row.clickable{cursor:pointer}' +
      '.eng-cp-time{color:var(--dsw-alias-label-tertiary,#94a3b8);flex:none;' +
        'font-variant-numeric:tabular-nums}' +
      '.eng-cp-name{color:var(--dsw-alias-label-primary,#1f2328);flex:1 1 60%;' +
        'min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
      '.eng-cp-num{background:rgba(22,163,74,.14);border-radius:9px;color:#15803d;flex:none;' +
        'font-size:clamp(9px, 1.9cqw, 10.5px);font-weight:600;padding:1px 6px}' +
      '.eng-cp-fix{background:rgba(234,179,8,.20);border-radius:9px;color:#a16207;flex:none;' +
        'font-size:clamp(9px, 1.9cqw, 10.5px);font-weight:600;padding:1px 5px}' +
      '.eng-cp-save{color:var(--dsw-alias-label-tertiary,#8b95a3);flex:none;' +
        'font-size:clamp(9px, 1.9cqw, 10.5px);font-variant-numeric:tabular-nums}' +
      // 摘要正文：点开行才显示（避免长文本把列表撑爆）
      '.eng-cp-body{box-sizing:border-box;color:var(--dsw-alias-label-secondary,#475569);' +
        'flex:1 1 100%;font-size:clamp(10px, 2.05cqw, 11.5px);line-height:1.55;' +
        'max-height:9.5em;overflow-y:auto;padding:4px 2px 2px;white-space:pre-wrap;' +
        'word-break:break-word}' +
      '.eng-cp-empty{color:var(--dsw-alias-label-tertiary,#8b95a3);' +
        'font-size:clamp(10.5px, 2.2cqw, 12px);padding:8px 2px}' +
      // ── 【背景 · 2026-11-07】压缩历史不再自带深色底 ────────────────────
      // 用户原话：「这压缩历史不要搞背景，你可以参考图二一样，白色背景」。
      // 根因：这里原先把 .eng-cp 钉成 #16181d（且 .eng-detail/.eng-log 在深色
      //   模式下也是 #16181d）—— 于是右列/会话视图里出现一大块"黑框"，
      //   和旁边白色的对话流完全脱节。
      // 现在：不再给 .eng-cp 任何自己的底色（透明 → 父容器白底透上来），
      //   深色模式下也只调文字层级色，【永不】出现黑色块。
      // ── 【会话视图】官方 conversation.view 页签的容器（整页自适应）──────────
      // 它拿到的是【对话区】的完整尺寸（不像右列只有 1/4），因此这里按
      //   阅读优先排版：限宽居中、留白充足、字号用 clamp 跟视口走。
      '.eng-sv{box-sizing:border-box;display:flex;flex:1 1 auto;flex-direction:column;' +
        'height:100%;min-height:0;overflow:hidden;' +
        'background:var(--dsw-alias-bg-layer-1,#fff);' +
        'color:var(--dsw-alias-label-primary,#0f172a);padding:16px 18px 0}' +
      // 深色模式下也只把底色交给宿主变量，绝不钉死成 #16181d
      '@media(prefers-color-scheme:dark){' +
        '.eng-sv{background:var(--dsw-alias-bg-layer-1,#16181d);color:#e5e7eb}' +
      '}' +
      '.eng-sv-head{align-items:baseline;display:flex;flex:none;flex-wrap:wrap;' +
        'gap:4px 12px;padding:0 2px 10px}' +
      '.eng-sv-title{font-size:clamp(14px, 1.5vw, 17px);font-weight:700}' +
      '.eng-sv-sub{color:var(--dsw-alias-label-tertiary,#94a3b8);' +
        'font-size:clamp(11px, 1.05vw, 12.5px)}' +
      '.eng-sv-body{display:flex;flex:1 1 auto;flex-direction:column;min-height:0;' +
        'max-width:1100px;overflow:hidden;width:100%}' +
      '.eng-sv-body>.eng-cp{background:transparent;padding-bottom:14px}' +
      '.eng-sv-empty{color:var(--dsw-alias-label-tertiary,#94a3b8);' +
        'font-size:clamp(11.5px, 1.1vw, 13px);line-height:1.75;padding:22px 2px}' +


      // ── 极窄屏(<900px)：面板内部纵向堆叠（目录在上、日志在下）──────
      // 注意：仍是【流内】布局，不做任何覆盖。
      '.eng-console.stacked{flex-direction:column;flex:0 0 auto;width:100%;max-width:100%}' +
      '.eng-console.stacked .eng-tree{' +
        'flex:0 0 auto;max-width:none;width:100%;max-height:34vh;' +
        'border-right:none;border-bottom:1px solid #cbd5e1}' +
      '.eng-console.stacked .eng-detail{flex:1 1 auto;min-height:0}' +

      // ── 【折叠态】右边缘的竖向小标签（优雅、轻量、不遮挡内容）────────
      // 这是面板收起时唯一的可见元素：贴右边缘垂直居中，
      // 圆角只圆左侧两角，像"抽屉拉手"，配色沿用主色调蓝色。
      '.eng-fab{' +
        'align-items:center;background:linear-gradient(180deg,#3b82f6,#2563eb);' +
        'border:1px solid #2563eb;border-right:none;' +
        'border-radius:10px 0 0 10px;' +
        'box-shadow:-2px 0 10px rgba(37,99,235,.22);' +
        'color:#fff;cursor:pointer;display:flex;flex-direction:column;gap:6px;' +
        'font-size:11px;font-weight:600;letter-spacing:.08em;' +
        'padding:12px 7px;' +
        'position:fixed;right:0;top:50%;z-index:25;' +
        // 动画：进出场都平滑
        'transform:translateY(-50%) translateX(0);' +
        'transition:transform .22s cubic-bezier(.4,0,.2,1),' +
                   'box-shadow .2s ease,background .2s ease' +
      '}' +
      '.eng-fab:hover{' +
        'background:linear-gradient(180deg,#2563eb,#1d4ed8);' +
        'box-shadow:-3px 0 16px rgba(37,99,235,.34);' +
        'transform:translateY(-50%) translateX(-2px)' +
      '}' +
      // ── 【本次改动·Bug4】可拖动 ───────────────────────────────────────
      // 拖动中：禁用过渡（否则会拖出"橡皮筋"延迟），并换成抓手光标。
      // 位置由 JS 写内联 left/top，此时 transform 已被内联样式清掉。
      '.eng-fab{draggable:false}' +
      '.eng-fab.dragging{' +
        'transition:none!important;cursor:grabbing!important;' +
        'box-shadow:-4px 0 22px rgba(37,99,235,.42);z-index:40' +
      '}' +
      '.eng-fab:active{cursor:grabbing}' +
      // 有子代理在运行时切换为绿色，一眼可辨
      '.eng-fab.run{background:linear-gradient(180deg,#22c55e,#16a34a);border-color:#16a34a}' +
      '.eng-fab.run:hover{background:linear-gradient(180deg,#16a34a,#15803d);box-shadow:-3px 0 16px rgba(22,163,74,.34)}' +
      // ── 【小标签吸附】贴左边时的视觉镜像（圆角/阴影/描边都要反过来）──────
      //   同一颗按钮要能在左/右两侧都像"抽屉拉手"，否则贴左会变成"断口朝外"。
      '.eng-fab.left{' +
        'border:1px solid #2563eb;border-left:none;' +
        'border-radius:0 10px 10px 0;' +
        'box-shadow:2px 0 10px rgba(37,99,235,.22)' +
      '}' +
      '.eng-fab.left:hover{' +
        'box-shadow:3px 0 16px rgba(37,99,235,.34);' +
        'transform:translateX(2px)' +
      '}' +
      '.eng-fab.left.run{background:linear-gradient(180deg,#22c55e,#16a34a);border-color:#16a34a}' +
      '.eng-fab.left.run:hover{background:linear-gradient(180deg,#16a34a,#15803d);box-shadow:3px 0 16px rgba(22,163,74,.34)}' +
      // 拖动中：吸附动画必须关掉，否则会拖出橡皮筋延迟
      '.eng-fab.dragging{transition:none!important}' +
      '.eng-fab-ico{font-size:12px;line-height:1;opacity:.85}' +
      '.eng-fab-txt{writing-mode:vertical-rl;text-orientation:mixed;line-height:1.15}' +
      '.eng-fab-badge{' +
        'background:rgba(255,255,255,.24);border-radius:9px;' +
        'font-size:10px;font-weight:700;padding:1.5px 5px;min-width:16px;text-align:center' +
      '}' +
      '.eng-fab-dot{' +
        'background:#fbbf24;border-radius:50%;height:7px;width:7px;' +
        'box-shadow:0 0 0 2px rgba(255,255,255,.35);' +
        'animation:eng-pulse 1.3s ease-in-out infinite' +
      '}' +

      // ── 面板顶部的折叠/关闭按钮（明显、易点）─────────────────────
      '.eng-collapse{' +
        'align-items:center;background:#f1f5f9;border:1px solid #cbd5e1;border-radius:6px;' +
        'color:#475569;cursor:pointer;display:inline-flex;flex:none;' +
        'font-size:12px;font-weight:700;gap:3px;height:26px;padding:0 9px;white-space:nowrap' +
      '}' +
      '.eng-collapse:hover{background:#e2e8f0;color:#1e293b}' +

      // ── 底部输入框：严格随主区域宽度，不被侧栏挤压变形 ──────────────
      // 主区域子项允许收缩（min-width:0），输入框本身限制最大宽度，
      // 保证侧边栏展开时输入框跟着主区域一起变窄、而不是溢出或被压扁。
      'body.' + DOCK_CLASS + ' div[style*="grid-template-columns"]>*{min-width:0}' +
      'body.' + DOCK_CLASS + ' textarea,body.' + DOCK_CLASS + ' [contenteditable="true"]{' +
        'max-width:100%;box-sizing:border-box;overflow-wrap:break-word;word-break:break-word' +
      '}' +



      // ── Markdown 渲染后的排版（子代理面板与工具卡正文）────────
      '.eng-md-p{margin:0}' +
      '.eng-md-p + .eng-md-p{margin-top:6px}' +
      '.eng-md-h{font-weight:700;line-height:1.5;margin:8px 0 4px}' +
      '.eng-md-h1{font-size:14px}' +
      '.eng-md-h2{font-size:13.5px}' +
      '.eng-md-h3{font-size:13px}' +
      '.eng-md-h4,.eng-md-h5,.eng-md-h6{font-size:12.5px}' +
      '.eng-md-h:first-child{margin-top:0}' +
      '.eng-md-list{margin:4px 0;padding-left:18px}' +
      '.eng-md-list li{margin:2px 0}' +
      // 【本次改动】子代理面板里的 Markdown：紧凑一些，
      //   并与 .eng-think / .eng-bubble 的灰黑配色对齐（不抢正文）。
      '.eng-log .eng-md-p{margin:0}' +
      '.eng-log .eng-md-p + .eng-md-p{margin-top:5px}' +
      '.eng-log .eng-md-h{font-weight:700;line-height:1.45;margin:7px 0 3px}' +
      '.eng-log .eng-md-list{margin:3px 0;padding-left:16px}' +
      '.eng-log .eng-md-quote{margin:4px 0}' +
      '.eng-log strong{font-weight:700;color:var(--dsw-alias-label-primary,#0f172a)}' +
      '.eng-log em{font-style:italic;opacity:.92}' +
      '.eng-md-code{' +
        'background:rgba(37,99,235,.10);border-radius:4px;font-family:ui-monospace,' +
        'SFMono-Regular,Consolas,monospace;font-size:11.5px;padding:1px 4px' +
      '}' +
      '.eng-md-pre{' +
        'background:var(--dsw-alias-bg-layer-3,rgba(15,23,42,.06));border-radius:8px;' +
        'font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:11.5px;' +
        'line-height:1.6;margin:6px 0;overflow-x:auto;padding:8px 10px;' +
        'white-space:pre' +
      '}' +
      '.eng-md-pre code{font-family:inherit}' +
      '.eng-md-quote{' +
        'border-left:3px solid var(--dsw-alias-border-l2,#cbd5e1);' +
        'color:var(--dsw-alias-label-secondary,#475569);margin:6px 0;padding:2px 0 2px 9px' +
      '}' +
      '.eng-md-hr{border-top:1px solid var(--dsw-alias-border-l2,#e5e7eb);margin:8px 0}' +
      '.eng-md-link{color:var(--dsw-alias-state-business-primary,#2563eb);text-decoration:underline}' +

      // ══ 【工程人设】设置页填空 ══════════════════════════════════════════
      '.eng-persona{display:flex;flex-direction:column;gap:8px;padding:10px 2px 2px}' +
      '.eng-persona-in{' +
        'background:var(--dsw-alias-bg-layer-2,rgba(15,23,42,.03));' +
        'border:1px solid var(--dsw-alias-border-l2,#d0d5dd);border-radius:10px;' +
        'box-sizing:border-box;color:var(--dsw-alias-label-primary,#111827);' +
        'font-family:inherit;font-size:12.5px;line-height:1.7;' +
        'min-height:120px;outline:none;padding:9px 11px;resize:vertical;width:100%' +
      '}' +
      '.eng-persona-in:focus{border-color:var(--dsw-alias-state-business-primary,#2563eb)}' +
      '.eng-persona-ft{align-items:center;display:flex;gap:10px;justify-content:space-between}' +
      '.eng-persona-state{color:var(--dsw-alias-label-tertiary,#8b95a3);font-size:11.5px;min-width:0}' +
      '.eng-persona-save{' +
        'background:var(--dsw-alias-state-business-primary,#2563eb);border:0;border-radius:7px;' +
        'color:#fff;cursor:pointer;flex:none;font-family:inherit;font-size:12px;' +
        'font-weight:600;padding:6px 16px' +
      '}' +
      '.eng-persona-save:disabled{' +
        'background:var(--dsw-alias-border-l2,#cbd5e1);cursor:default' +
      '}' +
      '@media(prefers-color-scheme:dark){' +
        '.eng-persona-in{background:#22262e;border-color:#3a4048;color:#e5e7eb}' +
      '}',

      '@media(prefers-color-scheme:dark){' +
        '.eng-mask{background:rgba(0,0,0,.45)}' +
        '.eng-console{background:#16181d;border-left-color:#33383f;color:#e5e7eb}' +
        '.eng-tree{background:#1c1f26;border-right-color:#33383f}' +
        '.eng-tree-cap{border-bottom-color:#33383f;color:#cbd5e1}' +
        '.eng-node{background:#22262e;border-color:#3a4048;color:#cbd5e1}' +
        '.eng-node:hover{background:#2c313a}' +
        '.eng-node.on{background:#2563eb;border-color:#2563eb;color:#fff}' +
        // 【背景 · 2026-11-07】.eng-detail 不再跟着变黑 —— 它是右列整块的底，
        //   变黑会把「压缩历史」那一页也拖成黑底（用户明确要求白底）。
        //   只保留 .eng-log（日志正文区）的深色，面板整体仍是宿主的浅色分层。
        '.eng-log{background:#16181d}' +
        '.eng-head{background:#1c1f26;border-bottom-color:#33383f}' +
        '.eng-name{color:#e5e7eb}' +
        '.eng-tag{background:#2c313a;color:#cbd5e1}' +
        '.eng-x{background:#22262e;border-color:#3a4048;color:#cbd5e1}' +
        '.eng-bubble{background:#1a2436;border-color:#2c4a75;color:#cbd5e1}' +
        '.eng-bubble.sys{background:#22262e;border-color:#33383f;color:#94a3b8}' +
        '.eng-think{border-left-color:#3a4048;color:#8b95a3}' +
        '.eng-tool-card{background:#1a2436;border-color:#2c4a75}' +
        '.eng-tool-card-h{color:#93c5fd}' +
        '.eng-tool-card-b{background:#12151b;border-top-color:#2c4a75;color:#cbd5e1}' +
        '.eng-err-card{background:#3a1a1a;border-color:#7f1d1d;color:#fca5a5}' +
        '.eng-ask{background:#1c1f26;border-color:#3f3f8f}' +
        '.eng-ask-hd{background:#26294a;border-bottom-color:#3f3f8f;color:#c7d2fe}' +
        '.eng-ask-q,.eng-ask-qh{color:#e5e7eb}' +
        '.eng-opt{background:#22262e;border-color:#3a4048}' +
        '.eng-opt:hover,.eng-opt.on{background:#26294a;border-color:#6366f1}' +
        '.eng-opt-mark{background:#16181d;border-color:#4b5563}' +
        '.eng-opt-label{color:#e5e7eb}' +
        '.eng-opt-desc{color:#94a3b8}' +
        '.eng-ask-ft{border-top-color:#33383f}' +
        '.eng-note{color:#94a3b8}' +
        '.eng-fold{color:#64748b}' +
        '.eng-settings{color:#e5e7eb}' +
        '.eng-settings-body{background:#1c1f26;border-color:#33383f}' +
        // ── 【版式 v2】主栏目 + 子项行（深色）──
        '.eng-grp-title{color:#e5e7eb}' +
        '.eng-grp-card{background:#22252c;border-color:#33383f}' +
        '.eng-row+.eng-row{border-top-color:#2f333c}' +
        '.eng-row.clickable:hover,.eng-row:focus-visible{background:#2a2e37}' +
        '.eng-row-label{color:#e5e7eb}' +
        '.eng-row-hint{color:#8b95a3}' +
        '.eng-row-chev{color:#5b6270}' +
        // 模型接替表格（深色）
        '.eng-mc-in{background:#15181d;border-color:#33383f;color:#e5e7eb}' +
        '.eng-mc-add{background:rgba(96,165,250,.12);border-color:#3b5bdb;color:#93c5fd}' +
        '.eng-mc-add:hover{background:rgba(96,165,250,.22)}' +
        '.eng-mc-del{color:#f87171}' +
        '.eng-mc-table{border-top-color:#2f333c}' +
        '.eng-mc-head{color:#8b95a3}' +
        // 连接区徽标（深色）
        '.eng-chip.on{background:rgba(22,163,74,.22);color:#4ade80}' +
        '.eng-chip.off{background:rgba(148,163,184,.18);color:#94a3b8}' +
        '.eng-chip.idle{background:rgba(148,163,184,.12);color:#8b95a3}' +
        '.eng-set-note.dim{color:#8b95a3}' +
        // 开关不再按深色模式覆盖：基础色是中灰，浅底/深底都成立；
        //   而且它带 !important，这里的覆盖也不会生效（留着只会误导）。
        '.eng-settings-empty-d,.eng-settings-sub{color:#8b95a3}' +
      '}',
    ].join('');
    function installStyle() {
      if (typeof document === 'undefined') return;
      var old = document.getElementById(STYLE_ID);
      if (old !== null) old.remove();
      var el = document.createElement('style');
      el.id = STYLE_ID;
      el.textContent = STYLE;
      document.head.appendChild(el);
    }

    // ══════════════════════════════════════════════════════════════════════
    // 【需求1 · 落点修正】把「模式说明 / 如何使用」注入【预设卡片】本身
    // ──────────────────────────────────────────────────────────────────────
    // 用户明确要求：这两个入口要在【Agent 预设列表里工程模式那张卡片】上
    //   （与内置 mode 的卡片同一位置），而不是藏在设置页里。
    //
    // 为什么必须用 DOM 注入而不是 React 组件替换：
    //   预设卡片由宿主包 @deepseek-ai/dsh-client-ui-agent-preset 渲染，
    //   整包打进 app.asar，第三方插件【无法注册/包裹它的 React 组件】。
    //   它的 guides 是硬编码白名单（只含 standard/ptc/minimal/cordis），
    //   且 presetGuide() 只在"内置分组"时才查表 —— 工程模式属自定义分组，
    //   宿主【永远不会】给它渲染这两个按钮。
    //   因此只能：在自己的插件里往那张卡片的空位（cardFoot）注入同款按钮。
    //
    // 定位策略（不依赖哈希类名，抗宿主升级）：
    //   ① 找到工程模式卡片的「查看/浏览」按钮（aria-label 以"查看"开头，
    //      且 data-tip/key 含 view）—— 它是 cardFoot 里的稳定锚点；
    //   ② 从其父节点（= cardFoot）里找到【空的】那个子容器
    //      （宿主的 cardHelp 在无 help 时根本不渲染 → cardFoot 只剩一个
    //        iconButton，其"左侧空位"就是我们要占的位置）；
    //   ③ 把我们的按钮插到 iconButton 之前，视觉位置与内置卡片完全一致。
    //   ④ 用 MutationObserver 监听列表重渲染（切分组/切页会重建 DOM），
    //      幂等补注入（已注入则跳过）。
    // ══════════════════════════════════════════════════════════════════════
    var ENG_PRESET_ID = 'engineering';
    var INJECT_FLAG = 'data-eng-guide-injected';

    /** 在预设列表里找到【工程模式】那张卡片的 cardFoot。 */
    function findEngineeringCardFoot() {
      if (typeof document === 'undefined') return null;

      /** 从卡片主体元素推出 cardFoot（含按钮的那个兄弟 div）。 */
      function footOf(mainEl) {
        var root = mainEl ? mainEl.parentNode : null;
        if (!root || !root.children) return null;
        var kids = root.children;
        // ① 优先：含 <button> 的兄弟 div（= 宿主 cardFoot，里面是 iconButton）
        for (var i = 0; i < kids.length; i++) {
          var el = kids[i];
          if (el === mainEl) continue;
          if (el.tagName === 'DIV' && el.querySelector && el.querySelector('button')) {
            // 该 div 内的「查看」按钮作为插入锚点（把我们的按钮插到它之前）
            var ib = el.querySelector('button[data-tip]')
                  || el.querySelector('button');
            return { foot: el, anchor: ib };
          }
        }
        // ② 退化：最后一个 div（结构变化时仍尽量兜住）
        for (var j = kids.length - 1; j >= 0; j--) {
          if (kids[j].tagName === 'DIV') return { foot: kids[j], anchor: null };
        }
        return null;
      }

      // ── 锚点①【最稳】卡片 ID 元素 ────────────────────────────────────
      // 宿主把 preset id 渲染成 <code class="…cardId">engineering</code>。
      //   id 不随界面语言变化，比 aria-label / 文案都可靠。
      //   ⚠️ 曾经的 bug：用 aria-label 搜 "engineering"，而宿主填的是
      //   【显示名】"工程模式"（`${t("view")}: ${display.name}`），
      //   于是永远匹配不上 → 按钮从未注入（截图里那一栏始终是空的）。
      try {
        var codes = document.querySelectorAll('code');
        for (var i = 0; i < codes.length; i++) {
          var c = codes[i];
          if (String(c.textContent || '').trim() !== ENG_PRESET_ID) continue;
          // 向上找到卡片主体按钮（button.cardMain）
          var node = c, main = null;
          for (var d = 0; d < 8 && node; d++) {
            if (node.tagName === 'BUTTON') { main = node; break; }
            node = node.parentNode;
          }
          if (!main) continue;
          var f = footOf(main);
          if (f) return f;
        }
      } catch (e) {}

      // ── 锚点②兜底：aria-label 含【显示名】的卡片按钮 ─────────────────
      // 万一宿主改了 cardId 的渲染方式，用显示名再试一次。
      try {
        var btns = document.querySelectorAll('button[aria-label]');
        for (var j2 = 0; j2 < btns.length; j2++) {
          var b = btns[j2];
          var al = String(b.getAttribute('aria-label') || '');
          if (al.indexOf(ENG_GUIDE.name) < 0 && al.indexOf(ENG_PRESET_ID) < 0) continue;
          // 必须是卡片脚部的「查看」按钮（有 data-tip），而不是卡片主体按钮
          if (!b.hasAttribute('data-tip')) continue;
          var foot = b.parentNode;
          if (!foot || foot.nodeType !== 1) continue;
          return { foot: foot, anchor: b };
        }
      } catch (e) {}

      return null;
    }

    /** 注入「模式说明 / 如何使用」两个按钮（幂等）。 */
    function injectPresetGuideButtons(onOpen) {
      if (typeof document === 'undefined') return false;
      var hit = findEngineeringCardFoot();
      if (!hit) return false;
      var foot = hit.foot;

      // ── 幂等判据：直接看【按钮是否真的在这个 foot 里】──────────────────
      // 为什么不用 data-* 标记：宿主整体重渲染时会【重建 cardFoot 元素】，
      //   标记随之丢失；也可能复用元素但清掉我们的子节点。两种情况都会让
      //   标记与实际状态不一致。因此以"DOM 里确实有我们的按钮"为准。
      var exist = foot.querySelectorAll('.eng-preset-help-btn');
      if (exist && exist.length >= 2) return true;

      // 清理可能残留的不完整注入
      try {
        var olds = foot.querySelectorAll('.eng-preset-help');
        for (var q = 0; q < olds.length; q++) {
          if (olds[q].parentNode) olds[q].parentNode.removeChild(olds[q]);
        }
      } catch (e) {}

      // 造一个容器插到「查看」按钮之前（视觉位置与内置卡片一致）；
      //   没有 anchor 时直接追加到 foot 末尾。
      var host = document.createElement('div');
      host.className = 'eng-preset-help';
      try {
        if (hit.anchor && hit.anchor.parentNode === foot) {
          foot.insertBefore(host, hit.anchor);
        } else {
          foot.appendChild(host);
        }
      } catch (e) {
        try { foot.appendChild(host); } catch (e2) { return false; }
      }

      var mk = function (label, page) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'eng-preset-help-btn';
        b.textContent = label;
        b.setAttribute('aria-label', label + ': ' + ENG_GUIDE.name);
        b.addEventListener('click', function (ev) {
          try { ev.preventDefault(); ev.stopPropagation(); } catch (e) {}
          onOpen(page);
        });
        return b;
      };
      host.appendChild(mk(ENG_GUIDE.modeExplanation, 'explanation'));
      host.appendChild(mk(ENG_GUIDE.howToUse, 'usage'));
      // 标记仅作辅助（真正判据是按钮是否在 DOM 里）
      try { foot.setAttribute(INJECT_FLAG, '1'); } catch (e) {}
      return true;
    }

    /**
     * 【需求1】预设卡片按钮注入 + 弹窗承载。
     *
     * 打开流程（不依赖 ReactDOM 硬造 root）：
     *   DOM 注入的按钮 → 写一个"待打开"状态 → 通过自定义事件唤醒
     *   shell.overlay 里常驻的 EngPresetGuideHost 组件渲染弹窗。
     * 这样弹窗始终活在插件自己的 React 树里，与官方插槽机制一致。
     */
    var _presetGuideState = { mounted: false, observer: null };
    var ENG_GUIDE_EVT = 'eng-guide-open';

    function openPresetGuide(page) {
      if (typeof document === 'undefined') return;
      try {
        document.dispatchEvent(new CustomEvent(ENG_GUIDE_EVT, {
          detail: { page: page || 'explanation' }
        }));
      } catch (e) {
        try {
          var ev = document.createEvent('CustomEvent');
          ev.initCustomEvent(ENG_GUIDE_EVT, true, true, { page: page || 'explanation' });
          document.dispatchEvent(ev);
        } catch (e2) {}
      }
    }

    /** 常驻在 shell.overlay 的弹窗宿主：监听事件并按需渲染弹窗。 */
    function EngPresetGuideHost() {
      var _s = useState(null);
      var guide = _s[0], setGuide = _s[1];
      useEffect(function () {
        function onOpen(e) {
          var p = (e && e.detail && e.detail.page) || 'explanation';
          setGuide({ page: p });
        }
        try { document.addEventListener(ENG_GUIDE_EVT, onOpen); } catch (e) {}
        return function () {
          try { document.removeEventListener(ENG_GUIDE_EVT, onOpen); } catch (e) {}
        };
      }, []);
      if (!guide) return null;
      return h(EngGuideDialog, {
        initialPage: guide.page,
        onClose: function () { setGuide(null); }
      });
    }

    function startPresetGuideInjector() {
      if (typeof document === 'undefined') return;
      if (_presetGuideState.mounted) return;
      _presetGuideState.mounted = true;

      // ── 注入策略：定时轮询 + DOM 变化监听【双保险】─────────────────────
      // 踩过的坑：
      //   ① 只用 requestAnimationFrame 抖动：rAF 在【后台标签页/窗口最小化】
      //      时会被浏览器暂停，于是 20 次抖动可能一次都不跑 → 从不注入；
      //   ② 只监听一次：用户"先开 DSH、之后才打开设置页"时，预设列表是
      //      后续才挂载的，早期尝试必然落空。
      // 现在：setInterval 每 800ms 检查一次（rAF 暂停也不影响），
      //   配合 MutationObserver 即时响应；注入成功后自动降频为 5s 兜底。
      var _done = false;
      var _timer = null;

      function attempt(reason) {
        var ok = false;
        try { ok = injectPresetGuideButtons(openPresetGuide); } catch (e) { ok = false; }
        if (ok && !_done) {
          _done = true;
          _presetGuideState.injected = true;
          // 注入成功后降频：仍保留兜底轮询（宿主可能整体重渲染把按钮冲掉）
          if (_timer) { clearInterval(_timer); }
          _timer = setInterval(function () {
            try { injectPresetGuideButtons(openPresetGuide); } catch (e) {}
          }, 5000);
          try {
            if (window.console && console.log) {
              console.log('[dsh-engineering-ui] 预设卡片「模式说明/如何使用」已注入'
                          + (reason ? '（' + reason + '）' : ''));
            }
          } catch (e) {}
        }
        return ok;
      }

      // ① 立即试一次
      attempt('init');
      // ② 快速轮询（覆盖"设置页稍后才打开"的场景）
      _timer = setInterval(function () {
        if (_done) return;
        attempt('poll');
      }, 800);
      // ③ DOM 变化即时响应（切分组 / 列表重建）
      try {
        if (typeof MutationObserver !== 'undefined') {
          _presetGuideState.observer = new MutationObserver(function () {
            try { injectPresetGuideButtons(openPresetGuide); } catch (e) {}
          });
          _presetGuideState.observer.observe(document.body, {
            childList: true, subtree: true
          });
        }
      } catch (e) {}
      // ④ 页面可见性变化时补一次（切回标签页）
      try {
        document.addEventListener('visibilitychange', function () {
          if (!document.hidden) attempt('visible');
        });
      } catch (e) {}
    }

    /** 【诊断】报告注入器的当前状态（供排障；可 console 调用）。 */
    function presetGuideInjectorStatus() {
      var hit = null;
      try { hit = findEngineeringCardFoot(); } catch (e) {}
      return {
        mounted: !!_presetGuideState.mounted,
        injected: !!_presetGuideState.injected,
        card_found: !!hit,
        foot_children: hit && hit.foot ? hit.foot.children.length : null,
        has_anchor: !!(hit && hit.anchor),
        buttons_in_dom: (typeof document !== 'undefined')
          ? document.querySelectorAll('.eng-preset-help-btn').length : 0,
        preset_id: ENG_PRESET_ID,
        preset_name: ENG_GUIDE.name,
      };
    }
    // ══ 【布局 v5 · 关键机制】驱动宿主第三列开合 ═══════════════════════
    // 经实测确认的宿主真实结构（DSH Web）：
    //
    //   .pI_x6G_frame  (display:grid)
    //     ├─ .pI_x6G_sidebarCol   (第1列) 左侧导航
    //     ├─ .pI_x6G_centerCol    (第2列) 中间对话区  ← 会被"挤压"的那个
    //     ├─ .pI_x6G_detailsCol   (第3列) 默认 0px   ← 我们要撑开的就是它
    //     ├─ .pI_x6G_overlayLayer          覆盖层（插件原本渲染在这 → 必然遮挡）
    //     └─ .pI_x6G_handle                拖拽手柄
    //
    // 宿主用【内联样式】写 grid-template-columns，并带 .3s 过渡：
    //     grid-template-columns: 280px minmax(0px, 1fr) 0px;
    //
    // 因此要让面板"挤压而非覆盖"，唯一正确的做法是：
    //   把第 3 列从 0px 改成面板宽度 —— 中间列 minmax(0px,1fr) 会自动收缩。
    //   实测：280px 1120px 0px  →  280px 720px 400px（主区被挤压，不重叠）。
    //
    // 注意：改内联样式后必须等 .3s 过渡结束再测量，否则读到的仍是旧值
    //      （这是之前误判"改了没效果"的原因）。

    // ══ 【布局 v11】面板宽度管理（已简化）═════════════════════════════
    // 三列布局由样式表 !important 规则 + body.eng-pane-open 类驱动，
    // 面板本体 grid-column:3 落进第三列。此处只保留：
    //   ① --eng-pane 的 3:1 宽度计算；
    //   ② 左侧导航宽度变量同步（沿用原 installFrameWidthSync 行为）。
    // 旧版 margin 让位 / --eng-push / eng-pushed 机制已整体废弃。

    /** 【3:1 比例】面板宽 = (视口 - 左导航) × 25%，主区最低 600px。
     *  空间不足以容纳面板（<240px）时返回 0（调用方应折叠面板）。 */
    function _calcPaneWidth() {
      try {
        var vw = window.innerWidth || document.documentElement.clientWidth || 1280;
        var sb = 0;
        var sbEl = document.querySelector('[class*=' + String.fromCharCode(34) + 'sidebarCol' + String.fromCharCode(34) + ']');
        if (sbEl) sb = sbEl.getBoundingClientRect().width;
        else {
          var v = parseFloat(getComputedStyle(document.documentElement)
                    .getPropertyValue("--eng-sb")) || 280;
          sb = v;
        }
        var avail = vw - sb;
        // 【自适应·往左扩】用户反馈：右侧内容被挤得太窄（频道列 + 详情都断字）。
  //   40% → 46%，并保证主区仍有可读宽度（--eng-main-min 兜底）。
  var pane = Math.round(avail * 0.46);
        var mainMin = parseFloat(getComputedStyle(document.documentElement)
                       .getPropertyValue("--eng-main-min")) || 600;
        if (avail - pane < mainMin) {
          pane = Math.max(0, avail - mainMin);
        }
        if (pane < 240) return 0;
        return pane;
      } catch (e) { return 420; }
    }

    // ── 【自适应】频道列宽度随面板宽度联动 ──────────────────────────────
    //   频道列不再写死 168px：面板宽 380px 时 168px 够用；面板宽 640px 时
    //   需要约 269px 才能装下「结构件房间：底盘/轮毂/方向机」这类标签。
    //   这里按 --eng-pane 的像素值算频道列宽度并写 CSS 变量；
    //   CSS 侧另有 clamp(190px,42%,300px) 兜底，本函数失效也能自适应。
    function _syncTreeWidth(panePx) {
      try {
        var p = parseFloat(panePx);
        if (!isFinite(p) || p <= 0) return;
        var tw = Math.round(Math.max(190, Math.min(300, p * 0.42)));
        document.documentElement.style.setProperty("--eng-tree-w", tw + "px");
      } catch (e) { /* 写不进去就让 CSS clamp 兜底 */ }
    }
    /** 让位/收起时把频道列宽度变量摘掉，交回 CSS clamp() 兜底。 */
    function _resetTreeWidth() {
      try { document.documentElement.style.removeProperty("--eng-tree-w"); } catch (e) {}
    }


    /** 同步左侧导航宽度到 --eng-sb（宿主可能调整首列宽度）。 */
    function installFrameWidthSync() {
      if (typeof document === 'undefined' || typeof MutationObserver !== 'function') return;
      var sync = function () {
        var frame = document.querySelector('div[style*="grid-template-columns"]');
        if (!frame) return;
        var raw = (frame.style.gridTemplateColumns || "").trim();
        var first = raw.split(/\s+/)[0] || "";
        if (/^[0-9.]+px$/.test(first)) {
          document.documentElement.style.setProperty("--eng-sb", first);
        }
      };
      sync();
      var obs = new MutationObserver(function () { sync(); });
      obs.observe(document.body, { attributes: true, attributeFilter: ['style'], childList: true, subtree: true });
      return function () { obs.disconnect(); };
    }

    // ══ 【本次改动·Bug1】主对话 DOM 层的哨兵清理 ═══════════════════════════
    // 上面的 stripProtocolSentinel 只覆盖【本插件自己渲染】的气泡（子代理面板等）。
    // 主对话正文由 DSH 官方组件渲染，插件拿不到它的 props，
    //   所以这里再加一层【非侵入的 DOM 清理】：扫描文本节点，把哨兵删掉。
    // 设计约束（很重要，否则会搞坏官方组件）：
    //   · 只改【文本节点的 data】，不替换节点、不动 DOM 结构 → React 不会错位；
    //   · 只在文本里确实含 '[System: Empty message content' 时才动；
    //   · 用 requestIdleCallback / 短节流合并，避免流式输出时每 token 扫一次；
    //   · 严格模式：只匹配这一条哨兵，绝不误删用户/模型的正常方括号文本。
    function installSentinelSweeper() {
      if (typeof document === 'undefined' || typeof MutationObserver !== 'function') return;
      var pending = false;
      var sweep = function () {
        pending = false;
        try {
          if (typeof document.createTreeWalker !== 'function') return;
          var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
          var node;
          var changed = 0;
          while ((node = walker.nextNode())) {
            var v = node.nodeValue;
            if (!v || v.indexOf('[System: Empty message content') < 0) continue;
            var cleaned = stripProtocolSentinel(v);
            if (cleaned !== v) {
              node.nodeValue = cleaned;
              changed++;
              if (changed > 400) break;   // 单轮上限，剩下的下一轮再扫
            }
          }
        } catch (e) { /* 清理失败不影响任何功能 */ }
      };
      var schedule = function () {
        if (pending) return;
        pending = true;
        try {
          if (typeof requestAnimationFrame === 'function') requestAnimationFrame(sweep);
          else setTimeout(sweep, 16);
        } catch (e) { setTimeout(sweep, 16); }
      };
      schedule();
      var mo = new MutationObserver(schedule);
      try {
        mo.observe(document.body, { childList: true, subtree: true, characterData: true });
      } catch (e) { /* 观察失败就只扫一次 */ }
      return function () {
        try { mo.disconnect(); } catch (e) {}
      };
    }

    // ══ 【本次改动·Bug1】模型退化哨兵文本过滤 ═════════════════════════════
    // 现象：消息末尾偶发出现
    //     [System: Empty message content sanitised to satisfy protocol]
    // 取证结论（遍历 app.asar 182MB + unpacked + .dsh 全库 + 343 个会话日志）：
    //   · DSH 与任何插件里【根本不存在】这个字符串（0 处，UTF-8/UTF-16 双查）；
    //   · 它出现在 assistant/message 的 data.stream[].text-chunks（逐 token
    //     流式原文）里，且之前没有任何 system/message 注入事件；
    //   · 全库 201 条命中：assistant/message 155 条，system/message 0 条。
    // ⇒ 这是【模型自己吐出来的占位文本】（上游把空内容按协议替换成这句话），
    //   不是宿主注入。既无法在 DSH 包内改，也不能假装它能"根治"。
    //
    // 本插件的职责边界：只做【显示层】清理，不伪造正文、不吞掉真实内容 ——
    //   把这句话从气泡里去掉；如果清洗后整段为空，这一段就不渲染
    //   （返回 null），用户看到的是干净的回复，而不是一行噪声。
    var PROTOCOL_SENTINEL_RE = new RegExp(
      '\\[System:\\s*Empty message content[^\\]]*\\]', 'gi');
    function stripProtocolSentinel(s) {
      var t = String(s == null ? '' : s);
      if (t.indexOf('[System:') < 0) return t;
      t = t.replace(PROTOCOL_SENTINEL_RE, '');
      // 清掉因此留下的多余空行（最多三层）
      t = t.replace(/\n{3,}/g, '\n\n').replace(/^\s+|\s+$/g, '');
      return t;
    }

    // ══ 文本清洗与分类 ═══════════════════════════════════════════
    function isNoise(s) {
      return /^(null|undefined|\[\]|\{\}|""|'')$/.test(String(s).trim());
    }
    // 【需求4·防自导自演】识别模型自己伪造的"用户回答"。
    // 大模型只负责输出【问题和选项】，用户的选择必须由前端真实捕获并注入。
    // 若模型在文本里自造 [用户回答]/选择：确认 这类内容，一律标记为伪造，
    // 前端不渲染成正常气泡，而是显示醒目警告，从源头掐断"自问自答"幻觉。
    function isFabricatedUserTurn(s) {
      var t = String(s || '');
      // 典型伪造形态：模型在同一段输出里既给选项又"替用户作答"
      var hasUserAnswerTag = /【用户回答】|\[用户回答\]|【用户已确认|【用户已回复/.test(t);
      var hasChoiceLine = /^\s*选择\s*[:：]/m.test(t) || /\n\s*选择\s*[:：]/.test(t);
      var claimsUserSaid = /用户(已)?(回复|选择|确认|同意|答复)\s*[:：].{0,40}(继续|确认|可以|OK|是|好的)/i.test(t);
      return hasUserAnswerTag || (hasChoiceLine && claimsUserSaid) || claimsUserSaid;
    }
    function stripAnsi(s) {
      return String(s).replace(/\u001b\[[0-9;]*m/g, '');
    }
    function isErrorPayload(s) {
      var t = String(s);
      return /"code"\s*:\s*"[A-Z_]{3,}"/.test(t) ||
        /ToolOutcome[A-Za-z]*Error/.test(t) ||
        /DELEGATED_CALLER|TOOL_OUTCOME_UNKNOWN|ASK_CANCELLED|ASK_ABORTED|CALLER_NOT_LIVE/.test(t);
    }
    function friendlyError(s) {
      var t = String(s);
      if (/DELEGATED_CALLER|owned by another live agent/.test(t)) {
        // 【bug5 修复】旧文案说"可在下方卡片选择，答案会作为消息发给它" ——
        // 那是已废弃的 followup 路径，会误导子代理以为"列完选项就能结束回合"，
        // 于是出现"不调用提问、直接把选项写进文本然后结束"的现象。
        // 正确语义：子代理必须用 report 把问题上报主对话，由主对话（有提问权）代问；
        // 答案沿 ask() 返回路径成为 tool_result，不是"发一条消息给它"。
        return '子代理无权直接向用户提问（DSH 平台限制：子代理被主代理拥有）。'
             + '正确做法：子代理把问题和选项用 report 上报主对话，由主对话调用 '
             + 'ask_user_question 弹出选项卡；用户作答后答案作为 tool_result 回到主对话，'
             + '再由主对话转达子代理。不要因为无法提问就直接结束回合。';
      }
      if (/TOOL_OUTCOME_UNKNOWN/.test(t)) return '工具执行异常 —— 后端未返回结果，可重试。';
      if (/ASK_CANCELLED|ASK_ABORTED/.test(t)) return '提问已被取消。';
      return '工具执行异常 —— 已隐藏原始错误详情。';
    }
    // 解析 ask_user_question 的参数 JSON，失败返回 null（退化为普通工具卡）
    function parseQuestions(argsText) {
      var obj = null;
      try { obj = JSON.parse(String(argsText)); } catch (e) { return null; }
      if (!obj || !Array.isArray(obj.questions)) return null;
      var out = [];
      for (var i = 0; i < obj.questions.length; i++) {
        var q = obj.questions[i];
        if (!q || typeof q !== 'object') continue;
        var opts = [];
        if (Array.isArray(q.options)) {
          for (var j = 0; j < q.options.length; j++) {
            var o = q.options[j];
            if (o === null || o === undefined) continue;
            if (typeof o === 'string') opts.push({ label: o, description: '' });
            else opts.push({ label: String(o.label || ''), description: String(o.description || '') });
          }
        }
        out.push({
          id: String(q.id || ('q' + (i + 1))),
          header: String(q.header || ''),
          question: String(q.question || ''),
          multi: q.multi_select === true,
          options: opts
        });
      }
      return out.length > 0 ? out : null;
    }
    function toolIcon(name) {
      var n = String(name).toLowerCase();
      if (n.indexOf('pwsh') >= 0 || n.indexOf('bash') >= 0 || n.indexOf('shell') >= 0) return '🖥️';
      if (n.indexOf('ask_user') >= 0) return '❓';
      if (n.indexOf('subagent') >= 0 || n.indexOf('workflow') >= 0 || n.indexOf('ralph') >= 0) return '🤖';
      if (n.indexOf('read') >= 0) return '📖';
      if (n.indexOf('write') >= 0 || n.indexOf('edit') >= 0) return '✏️';
      if (n.indexOf('glob') >= 0 || n.indexOf('grep') >= 0 || n.indexOf('search') >= 0) return '🔎';
      return '🔧';
    }
    function summarizeArgs(o) {
      if (!o || typeof o !== 'object') return '';
      if (typeof o.command === 'string') return o.command.replace(/\s+/g, ' ').slice(0, 70);
      if (typeof o.file_path === 'string') return o.file_path.split(/[\\/]/).pop();
      if (typeof o.pattern === 'string') return o.pattern.slice(0, 50);
      if (typeof o.description === 'string') return o.description.slice(0, 60);
      var keys = [];
      for (var k in o) { if (Object.prototype.hasOwnProperty.call(o, k)) keys.push(k); }
      return keys.slice(0, 4).join(', ');
    }
    function prettyArgs(args) {
      try { return JSON.stringify(JSON.parse(String(args)), null, 2); } catch (e) { return String(args); }
    }
    // ══ 展示组件 ═════════════════════════════════════════════════
    // ── 【本次修复·用户截图 3】事件正文按 Markdown 渲染 ────────────────────
    // 现象：模型输出的 **加粗**、`代码`、- 列表、## 标题全部原样显示成
    //   一堆星号/反引号，长段落挤成一大坨（用户："太乱了，没有转化成功"）。
    // 根因：Bubble/ThinkBox 直接把 props.text 当纯文本塞进 div，
    //   而插件里【早就有】一个零依赖的 Markdown 渲染器 engRenderMarkdown()
    //   （原先只给侧边分析面板用，从没接到子代理面板这条渲染链上）。
    // 修法：接到这条链上。engRenderMarkdown 是函数声明（会提升），
    //   所以这里定义在前面也能直接调用；出错时回退为纯文本，绝不吞内容。
    function engSafeMarkdown(text, keyPrefix) {
      try {
        var s = String(text == null ? '' : text);
        if (!s) return null;
        // 没有 Markdown 迹象的短文本 → 保持纯文本（省开销、避免误解析）
        if (!/(^|\n)\s*(#{1,6}\s|[-*+]\s|\d{1,3}[.)]\s|>\s|```|\|)|\*\*|`[^`\n]+`/.test(s)) {
          return s;
        }
        var nodes = engRenderMarkdown(s, keyPrefix || 'md');
        return nodes && nodes.length ? nodes : s;
      } catch (e) {
        return String(text == null ? '' : text);
      }
    }
    // 【问问AI · 轻量 Markdown 渲染】
    // ──────────────────────────────────────────────────────────────────────
    // 为什么需要它：模型回答里大量使用 **加粗**、行内代码、- 列表、## 标题。
    //   过去面板直接把原文塞进气泡（white-space:pre-wrap），这些标记就原样
    //   显示成一串反引号和星号，看起来又乱又不像成品 —— 用户反馈
    //   "模型原本想要放大字体的符号没有正确"，说的正是这个。
    //
    // 设计约束（重要）：
    //   · 不引入任何新依赖：插件只 require('react')，必须零依赖；
    //   · 不用 dangerouslySetInnerHTML：模型输出不可信，绝不拼 HTML；
    //   · 因此这里做【行内解析 + React 节点】的极小子集渲染：
    //       代码围栏 / # 标题 / - * 列表 / > 引用 / --- 分隔线 /
    //       **粗体** / *斜体* / 行内代码 / 链接 [t](url)
    //     不认识的语法一律按纯文本输出，绝不吞字。
    var ENG_MD_INLINE = [
      // 行内代码优先级最高：内部的 ** / * 不应再被解析
      { re: /\x60([^\x60\n]+)\x60/g, kind: 'code' },
      { re: /\*\*([^*\n]+)\*\*/g, kind: 'bold' },
      { re: /(?<!\*)\*([^*\n]+)\*(?!\*)/g, kind: 'italic' },
      { re: /(?<![\w])_([^_\n]+)_(?![\w])/g, kind: 'italic' },
      { re: /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, kind: 'link' }
    ];

    /** 把一段行内文本渲染成 React 节点数组（递归下降，最多 3 层防炸）。 */
    function engRenderInline(text, keyPrefix, depth) {
      var s = String(text == null ? '' : text);
      if (!s) return [];
      var d = depth || 0;
      if (d > 3) return [s];
      var best = null;
      for (var i = 0; i < ENG_MD_INLINE.length; i++) {
        var rule = ENG_MD_INLINE[i];
        rule.re.lastIndex = 0;
        var m = rule.re.exec(s);
        if (!m) continue;
        if (!best || m.index < best.m.index ||
            (m.index === best.m.index && m[0].length > best.m[0].length)) {
          best = { rule: rule, m: m };
        }
      }
      if (!best) return [s];
      var m2 = best.m;
      var out = [];
      if (m2.index > 0) out.push(s.slice(0, m2.index));
      var inner = m2[1] != null ? m2[1] : m2[0];
      var tail = s.slice(m2.index + m2[0].length);
      var key = keyPrefix + '-' + best.rule.kind;
      if (best.rule.kind === 'code') {
        out.push(h('code', { className: 'eng-md-code', key: key }, inner));
      } else if (best.rule.kind === 'bold') {
        out.push(h('strong', { key: key }, engRenderInline(inner, key, d + 1)));
      } else if (best.rule.kind === 'italic') {
        out.push(h('em', { key: key }, engRenderInline(inner, key, d + 1)));
      } else if (best.rule.kind === 'link') {
        out.push(h('a', {
          className: 'eng-md-link', key: key,
          href: m2[2], target: '_blank', rel: 'noreferrer noopener'
        }, engRenderInline(inner, key, d + 1)));
      } else {
        out.push(inner);
      }
      var rest = engRenderInline(tail, key + 'r', d + 1);
      for (var j = 0; j < rest.length; j++) out.push(rest[j]);
      return out;
    }

    /** 把整段 Markdown 文本渲染成 React 节点数组（块级 + 行内）。 */
    function engRenderMarkdown(src, keyPrefix) {
      var text = String(src == null ? '' : src).replace(/\r\n?/g, '\n');
      var lines = text.split('\n');
      var out = [];
      var kp = keyPrefix || 'md';
      var i = 0;
      var seq = 0;
      function nextKey() { return kp + '-' + (seq++); }
      var FENCE_RE = /^\s*(\x60{3,}|~{3,})\s*([\w+-]*)\s*$/;
      var H_RE = /^\s*(#{1,6})\s+(.*)$/;
      var HR_RE = /^\s*(-{3,}|\*{3,}|_{3,})\s*$/;
      var Q_RE = /^\s*>\s?/;
      var LI_RE = /^\s*([-*+]|\d{1,3}[.)])\s+/;
      while (i < lines.length) {
        var ln = lines[i];
        // ① 代码围栏
        var fence = FENCE_RE.exec(ln);
        if (fence) {
          var ch = fence[1].charAt(0);
          var closeRe = new RegExp('^\\s*' + (ch === '~' ? '~' : '\\x60') + '{3,}\\s*$');
          var codeLines = [];
          i++;
          while (i < lines.length && !closeRe.test(lines[i])) {
            codeLines.push(lines[i]);
            i++;
          }
          i++;
          out.push(h('pre', { className: 'eng-md-pre', key: nextKey() },
            h('code', null, codeLines.join('\n'))));
          continue;
        }
        // ② 标题
        var hd = H_RE.exec(ln);
        if (hd) {
          var lv = Math.min(6, hd[1].length);
          out.push(h('div', { className: 'eng-md-h eng-md-h' + lv, key: nextKey() },
            engRenderInline(hd[2], nextKey(), 0)));
          i++;
          continue;
        }
        // ③ 分隔线
        if (HR_RE.test(ln)) {
          out.push(h('div', { className: 'eng-md-hr', key: nextKey() }));
          i++;
          continue;
        }
        // ④ 引用
        if (Q_RE.test(ln)) {
          var quote = [];
          while (i < lines.length && Q_RE.test(lines[i])) {
            quote.push(lines[i].replace(Q_RE, ''));
            i++;
          }
          out.push(h('div', { className: 'eng-md-quote', key: nextKey() },
            engRenderMarkdown(quote.join('\n'), nextKey())));
          continue;
        }
        // ⑤ 列表（有序 / 无序）
        if (LI_RE.test(ln)) {
          var ordered = /^\s*\d{1,3}[.)]\s+/.test(ln);
          var items = [];
          while (i < lines.length && LI_RE.test(lines[i])) {
            items.push(lines[i].replace(LI_RE, ''));
            i++;
            while (i < lines.length && /^\s{2,}\S/.test(lines[i])
                   && !LI_RE.test(lines[i])) {
              items[items.length - 1] += ' ' + lines[i].trim();
              i++;
            }
          }
          out.push(h(ordered ? 'ol' : 'ul', { className: 'eng-md-list', key: nextKey() },
            items.map(function (it, ii) {
              return h('li', { key: nextKey() + '-li' + ii },
                engRenderInline(it, nextKey() + '-li' + ii, 0));
            })));
          continue;
        }
        // ⑥ 空行
        if (!ln.trim()) { i++; continue; }
        // ⑦ 普通段落：连续非空行合并（行间用 <br> 保留换行）
        var para = [];
        while (i < lines.length && lines[i].trim()
               && !FENCE_RE.test(lines[i]) && !H_RE.test(lines[i])
               && !HR_RE.test(lines[i]) && !Q_RE.test(lines[i])
               && !LI_RE.test(lines[i])) {
          para.push(lines[i]);
          i++;
        }
        if (para.length) {
          var pk = nextKey();
          var nodes = [];
          for (var ci = 0; ci < para.length; ci++) {
            if (ci) nodes.push(h('br', { key: pk + '-br' + ci }));
            var inl = engRenderInline(para[ci], pk + '-c' + ci, 0);
            for (var q = 0; q < inl.length; q++) nodes.push(inl[q]);
          }
          out.push(h('div', { className: 'eng-md-p', key: pk }, nodes));
        } else {
          i++;   // 防御：避免死循环
        }
      }
      return out;
    }

    function Bubble(props) {
      var cls = 'eng-bubble' + (props.sys ? ' sys' : '') + (props.md === false ? ' plain' : '');
      // 结果类（sys）多为工具原始输出，保持等宽纯文本更好读；
      //   正文（非 sys）走 Markdown。
      if (props.sys) {
        return h('div', { className: cls }, props.text);
      }
      return h('div', { className: cls }, engSafeMarkdown(props.text, props.mdKey));
    }
    function ThinkBox(props) {
      return h('div', { className: 'eng-think' },
        engSafeMarkdown(props.text, props.mdKey || 'think'));
    }
    function ErrorCard(props) {
      return h('div', { className: 'eng-err-card' },
        h('span', { className: 'eng-err-ico' }, '⚠️'),
        h('span', null, props.text));
    }
    function ToolCard(props) {
      var openSt = useState(false);
      var args = String(props.args || '');
      var preview = '';
      if (args) {
        try { preview = summarizeArgs(JSON.parse(args)); } catch (e) { preview = args.replace(/\s+/g, ' ').slice(0, 70); }
      }
      return h('div', { className: 'eng-tool-card' },
        h('div', { className: 'eng-tool-card-h', onClick: function () { openSt[1](!openSt[0]); } },
          h('span', null, toolIcon(props.name)),
          h('span', { className: 'eng-tool-txt' }, String(props.name) + (preview ? ' · ' + preview : '')),
          h('span', { className: 'eng-tool-caret' }, openSt[0] ? '\u25be' : '\u25b8')),
        openSt[0] ? h('div', { className: 'eng-tool-card-b' }, args ? prettyArgs(args) : '(无参数)') : null);
    }
    // 「推荐」选项判定：label 或 description 里带「推荐 / 建议 / recommended」
    function isRecommendedOption(o) {
      if (!o) return false;
      var t = String(o.label || '') + ' ' + String(o.description || '');
      return /推荐|建议|recommended/i.test(t);
    }

    // ══ 交互式提问卡 ═════════════════════════════════════════════
    // 【Bug1】答案必须绑定到一次具体提问（pendingId），经 /answer 端点
    // resolve 掉 host 侧挂起的 ask()，从而成为那次工具调用的 tool_result。
    // 不再把选择拼成文字 followup 投回去（那会变成"下一轮新指令"，导致错乱）。
    function AskCard(props) {
      var questions = props.questions || [];
      // 按seq从高到低排序（高序号先答）
      var sorted = questions.slice().sort(function (a, b) {
        return (Number(b.seq || 0) - Number(a.seq || 0));
      });
      var selSt = useState({});       // { [q.id]: [labels] }
      var stSt = useState('idle');    // idle|sending|ok|err
      var msgSt = useState('');
      var idxSt = useState(0);        // 当前展示第几题（0-indexed）
      var regSt = useState('idle');   // idle|registering|ready|err
      var pidSt = useState(null);     // host 侧挂起提问的 pendingId
      var sel = selSt[0];
      var state = stSt[0];
      var idx = idxSt[0];

      // 【用户诉求】挂载时向 host 登记本次提问，取得 pendingId。
      //
      // 两种场景分流到不同端点：
      //   · 子代理面板（props.asChild）→ /ask-child
      //       host 代表该子代理发起 ask()，并把 pendingId 绑定到这个子代理；
      //       用户在【右侧面板】选完提交 → resolve → 子代理直接拿到结果继续。
      //       这正是"在子代理里选完就生效"，无需结束回合、无需主对话转发。
      //   · 主对话（非 asChild）→ 交给 DSH 原生选项卡（ui-user-questions），
      //       本卡片不重复登记，避免出现两个选项卡、答案走错通道。
      useEffect(function () {
        if (pidSt[0]) return;
        if (!props.asChild) { regSt[1]('ready'); return; }

        var cid = props.childSessionId || props.sessionId || '';
        if (!cid) { regSt[1]('err'); msgSt[1]('缺少子代理会话ID，无法登记提问'); return; }
        regSt[1]('registering');
        fetch('/dsh-engineering-ui/ask-child', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            childSessionId: cid,
            questions: sorted.map(function (q) {
              return {
                id: q.id, question: q.question,
                header: q.header, options: q.options, multiSelect: q.multi
              };
            })
          })
        }).then(function (r) { return r.json(); })
          .then(function (j) {
            if (j && j.ok && j.pendingId) {
              pidSt[1](j.pendingId); regSt[1]('ready');
            } else {
              regSt[1]('err');
              msgSt[1]('无法登记提问：' + String((j && j.error) || '未知错误'));
            }
          })
          .catch(function (e) {
            regSt[1]('err'); msgSt[1]('登记失败：' + String((e && e.message) || e));
          });
      }, [props.asChild, props.sessionId, props.parentSessionId, props.childSessionId]);
      var pick = function (q, label) {
        selSt[1](function (prev) {
          var next = {};
          for (var k in prev) { if (Object.prototype.hasOwnProperty.call(prev, k)) next[k] = prev[k]; }
          var cur = next[q.id] ? next[q.id].slice() : [];
          if (q.multi) {
            var ix = cur.indexOf(label);
            if (ix >= 0) cur.splice(ix, 1); else cur.push(label);
          } else {
            cur = (cur.length === 1 && cur[0] === label) ? [] : [label];
          }
          next[q.id] = cur;
          return next;
        });
      };
      // 判断当前题是否已选
      var curQ = sorted[idx];
      var curPicked = curQ ? (sel[curQ.id] || []) : [];
      var curDone = curQ ? curPicked.length > 0 : false;
      var allDone = sorted.length > 0 && sorted.every(function (q) { return (sel[q.id] || []).length > 0; });
      // 【需求3·前端自嗨修复】submit 只能由用户的真实点击触发。
      // 这里用 ref 标记"是否已由用户交互"，任何非点击路径（如 effect、
      // 定时器、渲染期调用）一律拒绝提交，杜绝"UI 一渲染就自动回继续"。
      var userTouched = useRef(false);
      // ── 【卡死修复 A】已结案标记：提交成功后禁止二次提交 / 重启计时器 ──
      //   问：「为什么需要它」—— 原实现提交成功后 stSt 变成 'ok'，但卡片仍然
      //   可点、倒计时仍在跑；用户在等待期间再点一次就会走到 /answer 拿到
      //   "该问题已作答，请勿重复提交"，看起来像"点了没反应"，进一步让人
      //   以为卡死。用一个 ref 一次性闭锁，语义最干净。
      var settledDoneRef = useRef(false);
      // 【PhaseB】供倒计时回调调用 submit（submit 定义在下方，用 ref 打通时序）
      var submitRef = useRef(null);

      var submit = function (cause) {
        // 唯一合法触发源：用户点按钮 / 倒计时超时。其余一律不提交。
        if (cause !== 'user-click') {
          try { console.warn('[eng-ui] 拒绝非用户触发的提交:', cause); } catch (e) {}
          return;
        }
        if (settledDoneRef.current) {
          // 已结案：静默忽略，不再发请求、不再报错（避免"看起来卡住"）
          return;
        }
        if (!userTouched.current) {
          stSt[1]('err'); msgSt[1]('请先点击选项后再提交'); return;
        }
        // 逐题收集（保持题目顺序；未作答的题不提交）
        var picked = [];
        var missing = [];
        for (var i = 0; i < sorted.length; i++) {
          var q = sorted[i];
          var arr = sel[q.id] || [];
          if (arr.length === 0) { missing.push(q.header || q.id); continue; }
          for (var j = 0; j < arr.length; j++) picked.push({ qid: q.id, label: arr[j] });
        }
        if (picked.length === 0) { stSt[1]('err'); msgSt[1]('请先选择后再提交'); return; }
        if (missing.length > 0) {
          stSt[1]('err'); msgSt[1]('还有未作答的问题：' + missing.join('、')); return;
        }

        // 【Bug1】答案必须绑定到具体提问（pendingId）→ resolve 挂起的 ask()
        // → 成为那次工具调用的 tool_result，而不是一条新的 user 消息。
        // 主对话场景下本卡不持有 pendingId（登记已交给原生），因此不会走到这里；
        // 子代理场景则由 /ask-child 登记回来的 pendingId 完成绑定。
        var pendingId = pidSt[0] || props.pendingId || null;
        if (!pendingId) {
          stSt[1]('err');
          msgSt[1]('无法绑定：该提问尚未登记成功（' + regSt[0] + '）。请稍候或改用主对话提问。');
          return;
        }

        stSt[1]('sending'); msgSt[1]('提交中…');
        fetch('/dsh-engineering-ui/answer', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ pendingId: pendingId, selected: picked })
        }).then(function (r) { return r.text().then(function (t) { try { return JSON.parse(t); } catch (e) { return null; } }); })
          .then(function (j) {
            if (j === null) { stSt[1]('err'); msgSt[1]('提交端点未就绪 —— 需重启 DSH 使 host 半生效后再试'); return; }
            if (j && j.ok === true) {
              stSt[1]('ok');
              msgSt[1]('已提交 —— 答案已作为 tool_result 绑定到提问');
              if (typeof props.onAnswered === 'function') {
                try { props.onAnswered(props.childSessionId); } catch (e2) {}
              }
              // ══ 【卡死修复 A · 前端侧】提交成功后必须让卡片进入"已结案" ══
              //   现场（session-55a2009b）：用户点了提交、服务器也返回 ok，
              //   但界面仍停在选项页，且 DSH 整轮不再推进 —— 用户只能强关。
              //   原因不是"没提交成功"，而是**提交成功之后没有任何收尾动作**：
              //     · 卡片自身没有闭锁（仍可点、仍显示倒计时）；
              //     · 原生 uq.ask 的挂起此前也没被结掉（见 host 半卡死修复 A）。
              //   这里做两件事：① 锁死本卡（settled 标记，禁止二次提交）；
              //   ② 记下 pendingId 供面板做"已答清账"，并在 1.2s 后把卡片
              //      从待答队列里彻底移除（父组件靠 onAnswered 处理）。
              try { settledDoneRef.current = true; } catch (e3) {}
              try {
                if (typeof props.onAnswered === 'function') {
                  setTimeout(function () {
                    try { props.onAnswered(props.childSessionId); } catch (e4) {}
                  }, 1200);
                }
              } catch (e5) {}
            } else {
              stSt[1]('err');
              msgSt[1]('提交失败：' + String((j && j.error) || 'unknown'));
            }
          })
          .catch(function (e) { stSt[1]('err'); msgSt[1]('提交失败：' + String((e && e.message) || e)); });
      };
      // ── 【Bug#1 修复】nextQ 支持两种来源 ──────────────────────────────
      //   auto=false（手动点「下一题」）：标记人已接管，停【当前题】表。
      //   auto=true （倒计时自动推进）  ：不置 userTouched（否则会误导
      //     submit 的"必须由真实点击触发"校验），只切题并让 effect 重建表。
      var nextQ = function (auto) {
        var _isAuto = (auto === true);
        if (!_isAuto) {
          userTouched.current = true;
        }
        if (idx < sorted.length - 1) idxSt[1](idx + 1);
        else {
          stSt[1]('sending'); msgSt[1]('全部答完，提交中…'); submit('user-click');
        }
      };
      // ── 【已删除 · 2026-11-08】本题卡的倒计时 ──────────────────────────
      // 用户要求把倒计时整体删掉。原实现在 120 秒后自动选「推荐项」并提交，
      // 导致用户真实选择还没送出就被默认值顶掉（session-6e5c9b47）。
      // 现在：不挂任何计时器，每题都必须由用户自己点。

      // ── 【PhaseB·第2项】选项表格化渲染 ────────────────────────────────
      // 用户反馈：步步确认时选项密密麻麻。改为表格行（序号|标记|文案|说明），
      //   分层清晰；无 description 的行自动收窄留白，不再堆叠。
      var blocks = null;
      if (curQ) {
        var multi = !!curQ.multi;
        var opts = (curQ.options || []).map(function (o, oi) {
          var on = curPicked.indexOf(o.label) >= 0;
          // 【倒计时删除】标记只显示「推荐」（选项自身属性）；不再有「默认」
          var isRec = isRecommendedOption(o);
          return h('button', {
            key: curQ.id + ':' + oi,
            className: 'eng-ask-tr' + (on ? ' on' : ''),
            onClick: function () {
              userTouched.current = true;
              // 【倒计时已删除】这里不再需要停表 —— 已无任何计时器在跑。
              if (state !== 'sending' && state !== 'ok') pick(curQ, o.label);
            }
          },
            h('span', { className: 'eng-ask-td-no' }, String(oi + 1)),
            h('span', { className: 'eng-ask-td-mark ' + (multi ? 'box' : 'radio') }),
            h('div', { className: 'eng-ask-td-txt' },
              h('div', { className: 'eng-ask-td-label' }, o.label),
              o.description ? h('div', { className: 'eng-ask-td-desc' }, o.description) : null),
            isRec
              ? h('span', { className: 'eng-ask-td-tag rec' }, '推荐')
              : null);
        });
        blocks = h('div', null,
          h('div', { className: 'eng-ask-q' },
            curQ.header ? h('div', { className: 'eng-ask-qh' }, curQ.header) : null,
            curQ.question ? h('div', null, curQ.question) : null),
          opts.length > 0
            ? h('div', { className: 'eng-ask-tbl' }, opts)
            : h('div', { className: 'eng-note' }, '（无选项，请在主对话中直接回复）'));
      }
      var total = sorted.length;
      var seqInfo = total > 1
        ? h('span', { style: { marginLeft: '8px', fontSize: '10px', opacity: 0.65 } },
            '第 ' + (idx + 1) + ' / ' + total + ' 题') : null;
      return h('div', { className: 'eng-ask' },
        h('div', { className: 'eng-ask-hd' },
          h('span', null, '❓'),
          h('span', null, total > 1 ? '按顺序作答' : '等待你的选择'),
          seqInfo,
          h('span', { style: { fontSize: '10px', fontWeight: 400, opacity: 0.75, marginLeft: 'auto' } }, multi ? '多选' : '单选')),
        h('div', { style: { padding: '6px 0' } }, blocks || h('div', { className: 'eng-note' }, '加载中…')),
        state !== 'ok'
          ? h('div', { className: 'eng-ask-ft' },
              h('span', { className: 'eng-ask-state' + (state === 'ok' ? ' ok' : (state === 'err' ? ' err' : '')) }, msgSt[0]),
              h('button', {
                className: 'eng-ask-btn',
                disabled: !curDone || state === 'sending',
                onClick: function () { nextQ(false); }
              },
                idx < total - 1 ? '下一题 →' : '提交 ✓'))
          : null);
    }
    // ── 显示事件（kind）→ 渲染消息（t）──────────────────────────────
    // renderEvent 的入参是 toDisplayEvents 产出的显示事件；此处做 kind→t
    // 归一化，并顺带做错误富化（friendlyError）与交互提问识别（parseQuestions）。
    function formatMessage(ev) {
      if (!ev || typeof ev !== 'object') return null;
      var kind = ev.kind;
      if (kind === 'thinking' || kind === 'think') {
        // 【本次改动·Bug1】先摘掉模型退化哨兵，剩下的才有意义
        var think = stripProtocolSentinel(stripAnsi(ev.text || '')).trim();
        if (!think || isNoise(think)) return null;
        return { t: 'think', text: think };
      }
      if (kind === 'message' || kind === 'text') {
        var text = stripProtocolSentinel(stripAnsi(ev.text || '')).trim();
        if (!text || isNoise(text)) return null;
        if (isErrorPayload(text)) return { t: 'error', text: friendlyError(text) };
        // 【需求4】模型自行伪造"用户回答"→ 不放行成正常气泡，改为醒目警告，
        // 避免把幻觉当成真实用户输入继续往下传。
        if (isFabricatedUserTurn(text)) {
          return { t: 'error', text: '⚠️ 检测到模型自行编造"用户回答"（幻觉）。'
            + '用户的选择必须由前端选项卡真实捕获，模型不得自问自答。'
            + '此段内容已拦截，不作为用户输入。' };
        }
        return { t: 'text', text: text };
      }
      if (kind === 'tool-call' || kind === 'tool') {
        var name = String(ev.tool || ev.name || 'tool');
        if (name.indexOf('ask_user') >= 0) {
          var questions = parseQuestions(ev.args);
          if (questions) return { t: 'ask', questions: questions };
        }
        return { t: 'tool', name: name, args: String(ev.args == null ? '' : ev.args) };
      }
      if (kind === 'tool-result' || kind === 'result') {
        var rtext = stripProtocolSentinel(stripAnsi(ev.text || '')).trim();
        if (ev.isError) return { t: 'error', text: (rtext && isErrorPayload(rtext)) ? friendlyError(rtext) : (rtext || '工具执行异常') };
        if (!rtext || isNoise(rtext)) return null;
        if (isErrorPayload(rtext)) return { t: 'error', text: friendlyError(rtext) };
        return { t: 'result', text: rtext };
      }
      if (kind === 'error') {
        var etext = stripAnsi(ev.text || '').trim();
        return { t: 'error', text: etext ? friendlyError(etext) : '工具执行异常' };
      }
      if (kind === 'ask') {
        var qs = Array.isArray(ev.questions) ? ev.questions : null;
        return qs && qs.length ? { t: 'ask', questions: qs } : null;
      }
      return null;
    }
    // ── 【UI 美化】事件类型徽标表 ──────────────────────────────────────
    // 用户反馈面板"看不来啥"：所有事件长得一样，分不清哪段是思考、哪段是命令、
    //   哪段是结果。这里给每类事件一个固定图标 + 标签 + 色调，扫一眼即可识别。
    var ENG_EVENT_META = {
      think:  { ico: '💭', label: '思考',  cls: 'think' },
      text:   { ico: '🗨',  label: '输出',  cls: 'text' },
      result: { ico: '📄', label: '结果',  cls: 'result' },
      tool:   { ico: '⚙',  label: '命令',  cls: 'tool' },
      error:  { ico: '⚠',  label: '报错',  cls: 'error' },
      ask:    { ico: '❓', label: '需要你确认', cls: 'ask' }
    };

    /** 把事件包进统一的"时间轴条目"：左侧色条 + 类型徽标 + 正文。
     *
     * ── 【本次改动】除「命令」（ToolCard 自带折叠）外，其余所有事件
     *    （思考 / 输出 / 结果 / 报错）都改成【可折叠】：
     *    · 点击标题行 → 展开/收起正文；
     *    · 默认【展开】，保持原有阅读习惯；
     *    · 折叠状态记在该事件自己的组件里（按 key 挂载，不会串题）。
     *   折叠时标题行右侧显示一行的内容摘要，方便扫读。
     */
    function engWrapEvent(kind, key, node, collapsible) {
      var meta = ENG_EVENT_META[kind] || ENG_EVENT_META.text;
      if (collapsible === false) {
        return h('div', { className: 'eng-evt eng-evt-' + meta.cls, key: key },
          h('div', { className: 'eng-evt-hd' },
            h('span', { className: 'eng-evt-ico' }, meta.ico),
            h('span', { className: 'eng-evt-label' }, meta.label)),
          h('div', { className: 'eng-evt-bd' }, node));
      }
      return h(EngCollapsibleEvent, {
        key: key, kind: meta.cls, ico: meta.ico, label: meta.label, node: node,
        // 收起态的摘要：优先取事件原文（纯文本），压成单行并截断
        peek: (function () {
          try {
            var t = String((node && node.props && node.props.text) || '');
            t = t.replace(/\s+/g, ' ').trim();
            return t.length > 60 ? t.slice(0, 60) + '…' : t;
          } catch (e) { return ''; }
        })()
      });
    }

    /** 可折叠的事件条目（思考 / 输出 / 结果 / 报错 共用）。
     *
     * ── 【本次改动·用户要求】默认展开/收起规则 ────────────────────────────
     * 用户原话：「默认可以收起的最好，然后"输出"就是默认展开的，
     *   其他都是默认收起的即可」。因此：
     *   · 🗨 输出（kind='text'）  → 默认【展开】（正文是主角，直接可读）；
     *   · 💭 思考 / 📄 结果 / ⚠ 报错 → 默认【收起】（标题行可见，点开再看）。
     * 展开状态记在组件自身，用户手动点过就以用户为准（不会被后续刷新重置）。
     */
    function EngCollapsibleEvent(props) {
      var openSt = useState(props.kind === 'text');
      var open = openSt[0];
      return h('div', { className: 'eng-evt eng-evt-' + props.kind + (open ? '' : ' collapsed') },
        h('div', {
          className: 'eng-evt-hd',
          onClick: function () { openSt[1](!open); },
          title: open ? '点击收起这一段' : '点击展开这一段',
          role: 'button',
          tabIndex: 0,
          onKeyDown: function (e) {
            if (e && (e.key === 'Enter' || e.key === ' ')) { openSt[1](!open); }
          }
        },
          h('span', { className: 'eng-evt-ico' }, props.ico),
          h('span', { className: 'eng-evt-label' }, props.label),
          // 收起时在标题行右侧显示一行摘要 —— 否则"默认收起"后完全看不出内容
          open ? null : h('span', { className: 'eng-evt-peek' }, String(props.peek || '')),
          h('span', { className: 'eng-evt-caret' }, open ? '▾' : '▸')),
        open ? h('div', { className: 'eng-evt-bd' }, props.node) : null);
    }

    // ── 【本次改动·Bug2】单个事件行的 memo 包装 ────────────────────────────
    // 轮询每 800ms 追加几条事件时，旧实现会把【整屏 400 条】全部重建一遍，
    //   配合"强制滚到底"就成了肉眼可见的闪烁。
    // 现在：事件对象引用不变 → memo 直接跳过重渲染，只有新事件会被创建。
    //   · React.memo 缺失（测试 shim）时降级为普通组件，行为不变。
    var EngEventRow = (typeof React.memo === 'function')
      ? React.memo(function EngEventRowImpl(props) {
          return renderEvent(props.ev, props.idx, props.info);
        }, function (a, b) {
          return a.ev === b.ev && a.info === b.info && a.idx === b.idx;
        })
      : function EngEventRowPlain(props) {
          return renderEvent(props.ev, props.idx, props.info);
        };

    function renderEvent(ev, idx, info) {
      var m = formatMessage(ev);
      if (!m) return null;
      var key = String(ev.sessionId || '') + ':' + String(ev.seq) + ':' + String(idx);
      // 【本次改动】传 mdKey：Markdown 渲染出的节点需要稳定 key 前缀，
      //   否则每次渲染 key 全变 → React 重建整棵子树（又变成闪烁）。
      if (m.t === 'think') return engWrapEvent('think', key, h(ThinkBox, { text: m.text, mdKey: key + '-t' }));
      if (m.t === 'text') return engWrapEvent('text', key, h(Bubble, { text: m.text, mdKey: key + '-m' }));
      if (m.t === 'result') return engWrapEvent('result', key, h(Bubble, { sys: true, text: m.text }));
      if (m.t === 'tool') return engWrapEvent('tool', key, h(ToolCard, { name: m.name, args: m.args }));
      if (m.t === 'error') return engWrapEvent('error', key, h(ErrorCard, { text: m.text }));
      if (m.t === 'ask') return h(AskCard, {
        key: key,
        questions: m.questions,
        // 【用户诉求】这是【子代理面板】的渲染点（renderEvent 仅被
        // SubagentConsole 调用），所以标 asChild=true：
        // 走 /ask-child 把提问绑到该子代理，用户在此面板选完即生效。
        asChild: true,
        sessionId: info.childSessionId,
        parentSessionId: info.parentSessionId,
        childSessionId: info.childSessionId
      });
      return null;
    }
    // ══ 【本次改动·Bug3 根因修复】常驻的「工程模式标记」组件 ═══════════════
    // 唯一职责：把 body.dsh-eng-mode 挂在【当前会话确实是工程模式】时。
    //
    // 为什么要单独一个组件：
    //   之前这个类只在 SubagentConsole 里挂，而那个组件遇到
    //     · preset !== 'engineering'
    //     · 用户关掉「使用工程模式自带的子代理显示页」
    //     · entries.length === 0（还没有子代理）
    //   都会【提前 return】→ 它的 useEffect 根本不执行 → 类挂不上。
    //   而主对话排版样式以这个类为前提，
    //   于是表现为「倒计时没了」「样式时灵时不灵」。
    //
    // 现在：本组件【永远渲染 null】，只负责维护 body 类，判定与面板解耦：
    //   · 当前会话 preset === 'engineering' → 挂类（唯一判据）；
    function EngModeMarker(props) {
      var useSessions = props && props.useSessions;
      // 与 SubagentConsole 完全同一套「当前会话」解析（mainView 唯一权威）
      var mainViewId = typeof useSessions === 'function' ? useSessions(function (s) {
        var byId = (s && s.byId) || {};
        var idsn = Object.keys(byId);
        for (var i = 0; i < idsn.length; i++) {
          var r = byId[idsn[i]];
          if (r && r.retainedBy && (r.retainedBy.mainView || 0) > 0) return idsn[i];
        }
        return null;
      }) : null;
      var preset = typeof useSessions === 'function' ? useSessions(function (s) {
        var row = mainViewId && s.byId ? s.byId[mainViewId] : null;
        if (!row) return undefined;
        var pv = row.projectionValues;
        if (pv && typeof pv.agentPreset === 'string') return pv.agentPreset;
        return row.agentPreset;
      }) : undefined;
      // ── 【本次改动·Bug3】判定只看【会话预设】，不再受"自带显示页"开关牵连 ──
      // 为什么解耦：body.dsh-eng-mode 同时是
      //   ① 主对话问答卡片排版、② 选项倒计时 的前提。
      // 而「使用工程模式自带的子代理显示页」只是【右侧面板】的开关 ——
      // 用户关掉它只是想用官方右侧面板，并不等于"工程模式的所有增强都关掉"。
      // 旧实现把两者绑在一起，于是关掉面板 → 倒计时一起消失（用户实测）。
      // 现在：面板开关只由 SubagentConsole 自己尊重；
      //       本类只反映"这个会话是不是工程模式"。
      useEffect(function () {
        if (typeof document === 'undefined') return;
        if (preset === 'engineering') {
          document.body.classList.add(ENG_MODE_CLASS);
        } else if (preset !== undefined && preset !== null) {
          // 明确是别的预设 → 摘掉（保证样式零外溢）
          document.body.classList.remove(ENG_MODE_CLASS);
        }
        // preset 尚未加载（undefined/null）→ 保持现状，避免"进会话闪一下"。
        // 本组件不负责卸载清理：切到非工程会话时上面的分支会摘掉它，
        // 且组件本身常驻（不随会话卸载），清理反而会让类在会话切换瞬间闪烁。
      }, [preset]);
      return null;
    }

    function SubagentConsole(props) {
      var useSessions = props.useSessions;
      // 【布局 v3 → P0-4 修复】默认【展开】，让三栏布局一进工程模式就出现。
      //   原实现是 useState(true)（默认收起）—— 即使面板成功挂载，用户看到的
      //   也只是右边缘一个小标签，主观感受仍是"三栏布局没有出现"。
      //   用户明确要求的是"三栏布局"，因此默认展开；仍可手动折叠。
      var closedSt = useState(false);
      // 【问题4】读取工程模式设置（含"是否使用自带子代理显示页"）。
      //   必须在这里（hooks 区）调用，且在任何 return 之前。
      var engSettings = useEngSettings();
      // ══ 【问题1/2 根因修复·当前会话 id】════════════════════════════════
      // 症状：
      //   ① 非工程模式的对话右侧出现"蓝边"（实为收起态蓝色小标签 .eng-fab）；
      //   ② 从 A 对话切到 B 对话，右侧面板仍显示 A 的子代理，不跟随。
      // 共同根因：**当前会话的解析方式是错的**。
      //
      // 原实现（两种都不可靠）：
      //   · `useSessions(s => s.current)` —— 0.2.0 的 SessionListState 没有该字段；
      //   · `uiSession.adapter.current` —— 该 adapter 是给【session 作用域】用的
      //     （见 ui-session/src/client/index.ts:681 `installScope('session', ...)`），
      //     root 作用域的 shell.overlay 组件并不是它的消费方；
      //   · 兜底"任一 preset==='engineering' 的会话" —— 这是最糟的一条：
      //     它【忽略用户当前在看哪个对话】，永远命中第一个工程会话，
      //     于是切到 B 对话时仍解析出 A → 问题②；而且只要列表里存在
      //     任何工程会话，非工程对话也会被判为"有会话"→ 问题①。
      //
      // 正确做法（0.2.0 官方一致用法）：当前显示在主对话区的会话，
      //   由 `byId[*].retainedBy.mainView > 0` 标记。官方多处这样取当前会话：
      //     · ui-layout/src/client/DocumentTitle.tsx:19-23
      //     · ui-agent-preset/src/client/index.ts:86,116,201
      //     · ui-open-in-app/src/client/index.ts:59
      //   在 `useSessions` 选择器里计算 → 天然随会话切换而重渲染（响应式）。
      // 同时仍优先采用宿主注入的 hook（若有），再回落 mainView。
      var currentId = undefined;
      try {
        if (typeof props.useEngCurrentSessionId === 'function') {
          currentId = props.useEngCurrentSessionId(function (v) { return v; });
        }
      } catch (e) { currentId = undefined; }
      if ((currentId === undefined || currentId === null) && props.sessionId) {
        currentId = props.sessionId;
      }
      // ══ 【P0-4 修复】DSH 0.2.0 子代理目录读法 ═══════════════════════════
      // 0.1.x：SessionListState 直接暴露 subagentsByParent[parentId].entries，
      //   条目形如 { id, kind:'child', label, activity }。
      // 0.2.0：【删除了 subagentsByParent】，改为按会话的投影表
      //     state.projectionsBySession[parentId].values.subagentCatalog
      //   条目形如 { id, createdAt, mode:'one-shot'|'continuable', label? }
      //   —— 没有 kind 字段，activity 也不再随条目下发。
      // 原实现只认旧形态 → 目录恒空 → 整个面板 return null（实测面板永不出现）。
      // 现在两种形态都读：优先新投影，缺失时回落旧字段。
      var projections = typeof useSessions === 'function'
        ? useSessions(function (s) { return s.projectionsBySession; }) : undefined;
      var catalogs = typeof useSessions === 'function' ? useSessions(function (s) { return s.subagentsByParent; }) : undefined;
      var summaries = typeof useSessions === 'function' ? useSessions(function (s) { return s.byId; }) : undefined;
      // ── 当前会话解析（唯一权威：mainView 保留者）──────────────────────
      var _presetOfRow = function (row) {
        if (!row) return undefined;
        // 0.2.0：预设名在 projectionValues 下；0.1.x：直接挂在 row 上
        var pv = row.projectionValues;
        if (pv && typeof pv.agentPreset === 'string') return pv.agentPreset;
        return row.agentPreset;
      };
      var mainViewId = typeof useSessions === 'function' ? useSessions(function (s) {
        var byId = (s && s.byId) || {};
        var ids = Object.keys(byId);
        for (var i = 0; i < ids.length; i++) {
          var r = byId[ids[i]];
          if (r && r.retainedBy && (r.retainedBy.mainView || 0) > 0) return ids[i];
        }
        return null;
      }) : null;
      // mainView 是最权威的"用户正在看的对话"，优先于 hook/兜底
      if (mainViewId) {
        currentId = mainViewId;
      } else if (currentId === undefined || currentId === null) {
        // 仅当 mainView 也不可用时，才退到"唯一会话"（**不再**退到"任一工程会话"：
        //   那正是问题①/②的根因 —— 它会让非工程对话也判定成功，且切对话不跟随）
        var _idsOnly = summaries ? Object.keys(summaries) : [];
        if (_idsOnly.length === 1) currentId = _idsOnly[0];
      }
      // 【React hooks 顺序铁律】这里【不能】提前 return：
      //   下面还有 useState/useRef/useEffect 等 hooks，提前返回会让 hooks 数量
      //   随渲染变化 → "Rendered fewer hooks than expected" 崩溃。
      //   currentId 取不到时，preset 自然为 undefined，由【所有 hooks 之后】的
      //   `if (preset !== 'engineering') return null` 统一兜住。
      var preset = typeof useSessions === 'function' ? useSessions(function (s) {
        return _presetOfRow(currentId !== undefined && currentId !== null && s.byId
          ? s.byId[currentId] : null);
      }) : undefined;
      // 子代理的 running 状态：0.2.0 由 byId 行的 running 字段推导
      // （旧版条目自带 activity，这里统一成同一个判据函数）
      var isRunning = function (id) {
        var row = summaries ? summaries[id] : null;
        if (row && row.running === true) return true;
        return false;
      };
      // ══ 【用户反馈 2】子代理的【子代理】（多级）必须能显示，且要区分层级 ══════
      // 现场（图二）：面板只列出【当前会话的直接子代理】（depth=1），
      //   "总装干涉检查"下面明明还挂着它自己的子代理，面板里完全看不到；
      //   于是用户看到的是一堆平铺、层级不明的条目。
      //
      // 数据来源（已核实 DSH 0.2.1 源码，dsh-subagent/lib）：
      //   ctx.subagents.listDescendants(rootSessionId)
      //     → 返回【整棵子树】的扁平列表，每条带：
      //         id / parentId / depth / kind('child'|'diagnostic') /
      //         activity('running'|'inactive') / hasChildren / mode / label
      //   也就是说宿主【本来就给了】完整层级，只是这里以前没去取。
      //
      // 为什么不用 projectionsBySession[parent].values.subagentCatalog：
      //   那个投影只覆盖【单个会话的直接子代理】（源码 listChildren → 一条
      //   observeSession(parentSessionId).subagentCatalog），拿不到孙辈。
      //
      // 策略（渐进增强，不破坏旧版）：
      //   ① 先按旧逻辑构建直接子代理（保证首屏立刻有东西）；
      //   ② 再异步请求 /agents?rootSessionId=<当前会话>，拿到整棵子树后
      //      升级为带 depth / parentId 的完整列表；
      //   ③ 接口不可用（旧版 DSH / 服务缺失）→ 保持 ①，零回归。
      var treeSt = useState(null);      // null = 尚未拿到；[] = 拿到但是空
      var tree = treeSt[0];
      useEffect(function () {
        if (typeof fetch !== 'function') return;
        if (!currentId) { treeSt[1](null); return; }
        var alive = true;
        var tick = function () {
          fetch('/dsh-engineering-ui/agents?rootSessionId=' + encodeURIComponent(currentId))
            .then(function (r) { return r.json(); })
            .then(function (j) {
              if (!alive) return;
              if (j && j.ok === true && Array.isArray(j.agents)) treeSt[1](j.agents);
            })
            .catch(function () { /* 拿不到就保持旧数据，不打断面板 */ });
        };
        tick();
        // 子代理会【在自己运行中新建孙代理】→ 必须持续刷新，否则新层不出现。
        //   5s 足够快（用户能看到新建的子代理），又不会打爆宿主。
        var timer = setInterval(tick, 5000);
        return function () { alive = false; clearInterval(timer); };
      }, [currentId]);

      var entries = [];
      var entryKind = {};       // id -> 'child' | 'diagnostic'
      var entryParent = {};     // id -> parentId
      var entryDepth = {};      // id -> depth（1 = 直接子代理）
      var entryHasKids = {};    // id -> 是否还有下一级
      // ── ① 旧逻辑：当前会话的【直接】子代理（首屏兜底）──────────────────
      var _proj = projections && currentId ? projections[currentId] : undefined;
      var _cat = _proj && _proj.values ? _proj.values.subagentCatalog : undefined;
      if (Array.isArray(_cat)) {
        for (var pi = 0; pi < _cat.length; pi++) {
          var pe = _cat[pi];
          if (!pe || pe.id === undefined || pe.id === null) continue;
          var _pid = String(pe.id);
          entries.push({
            id: _pid,
            label: pe.label,
            mode: pe.mode,
            createdAt: pe.createdAt,
            depth: 1,
            parentId: currentId,
            hasChildren: false,
            activity: isRunning(_pid) ? 'running' : 'inactive'
          });
          entryKind[_pid] = 'child';
          entryParent[_pid] = currentId;
          entryDepth[_pid] = 1;
        }
      }
      // ② 旧形态（DSH 0.1.x）：subagentsByParent[parent].entries，按 kind 过滤
      if (entries.length === 0) {
        var catalog = catalogs && currentId ? catalogs[currentId] : undefined;
        if (catalog && catalog.entries) {
          for (var i = 0; i < catalog.entries.length; i++) {
            var en = catalog.entries[i];
            if (!en || (en.kind !== undefined && en.kind !== 'child')) continue;
            entries.push({
              id: String(en.id), label: en.label, mode: en.mode,
              createdAt: en.createdAt, depth: 1, parentId: currentId,
              hasChildren: false, activity: en.activity || 'inactive'
            });
          }
        }
      }
      // ── ③ 升级：整棵子树（含孙辈），按前序展开成扁平表 ─────────────────
      //   宿主返回的顺序已是稳定前序（源码：stable pre-order），这里照抄即可，
      //   只补 UI 需要的字段（running 用 byId 快照再确认一次）。
      if (Array.isArray(tree) && tree.length > 0) {
        var up = [];
        for (var ti = 0; ti < tree.length; ti++) {
          var te = tree[ti];
          if (!te || te.id === undefined || te.id === null) continue;
          var tid = String(te.id);
          var kind = (te.kind === 'diagnostic') ? 'diagnostic' : 'child';
          var run2 = isRunning(tid) || te.activity === 'running';
          up.push({
            id: tid,
            label: te.label,
            mode: te.mode,
            createdAt: te.createdAt,
            depth: (typeof te.depth === 'number' && te.depth > 0) ? te.depth : 1,
            parentId: te.parentId || currentId,
            hasChildren: te.hasChildren === true,
            kind: kind,
            diagnostic: (kind === 'diagnostic') ? String(te.reason || '') : null,
            activity: run2 ? 'running' : 'inactive'
          });
          entryKind[tid] = kind;
          entryParent[tid] = te.parentId || currentId;
          entryDepth[tid] = up[up.length - 1].depth;
          entryHasKids[tid] = te.hasChildren === true;
        }
        if (up.length > 0) entries = up;
      } else {
        for (var ui = 0; ui < entries.length; ui++) {
          entryHasKids[entries[ui].id] = entries[ui].hasChildren === true;
        }
      }

      // 子代理 id 串（用作 effect 依赖：ID 集合变了才重算，避免每帧重跑）
      var ids = entries.map(function (e) { return e.id; }).join(',');
      var selSt = useState(entries.length > 0 ? entries[0].id : null);
      // ── 【布局 v12】右列两页：'flow'（实时流程）/ 'compact'（压缩历史）────
      // 用户要求：「左边的选择子代理的时候，右边会展示出来那个实际的流程，
      //   压缩历史也是同样的」。⇒ 两页共享同一个 selected，切换只换内容。
      //   必须声明在 hooks 区（早退之前），否则会破坏 hooks 顺序。
      var rightTabSt = useState('flow');
      var logSt = useState([]);
      var errSt = useState(null);
      var seqRef = useRef(0);
      var boxRef = useRef(null);
      var selected = selSt[0];
      // 【问题2】交互队列：记录"正在等待用户选择"的子代理，按 seq 升序排队。
      // 有提问的子代理自动顶替展示；答完自动切回，或切到下一个待答的。
      var waitRef = useRef({});        // childId -> { seq, hasAsk }
      var prevSelRef = useRef(null);   // 提问前的展示对象（答完切回）
      // 【问题3 修复】记录用户最后一次【手动点选】的时间戳。
      //   自动顶替逻辑在用户刚点过（2 秒内）时让路，避免"刚点开就被抢走"。
      var manualPickRef = useRef(0);
      // ══ 【本次改动·Bug2】用户手动选中的子代理 = 【钉住】══════════════════
      // 用户原话：「我在看已经完成的子代理的消息，但正在运行的子代理一刷新，
      //   就自己给我替换过去了……应该是用户不看的时候，再换到正在运行的」。
      // 旧实现只用 2 秒宽限期，2 秒后照样被抢走 → 体验极差。
      // 现在：只要用户【手动点过】某个子代理，就一直钉住它，
      //   · 不再被"运行中优先"自动顶替；
      //   · 只有【该子代理自己弹出提问】时才短暂切过去（否则看不见问题）；
      //   · 用户再点一次别的子代理 → 钉住目标随之改变。
      var pinnedRef = useRef(null);
      // ══ 【本次改动·Bug2】按子代理缓存日志 ══════════════════════════════
      // 旧实现每次切换都 logSt=[] → 先变空白再重新拉取 → 视觉上"闪一下"。
      // 现在切回去直接用缓存渲染（seq 也从缓存续上），首屏零空白；
      // 新事件仍按增量追加，滚动位置不再被重置。
      var logCacheRef = useRef({});   // childId -> { items: [], seq: number }
      // 记录"用户是否贴着底部"：只有贴底时才自动跟随滚动，否则不打扰阅读
      var stickBottomRef = useRef(true);
      // 【本次改动·Bug2】渲染用的稳定 info 容器（memo 依赖它保持引用一致）
      var infoRef = useRef(null);
      // ── 【本次改动·Bug4】收起态小标签的拖动状态 ────────────────────────
      //   fabPos: {x, y} —— 左上角相对视口坐标；null = 用默认位置（右侧居中）
      var fabPosSt = useState(function () { return loadFabPosition(); });
      var fabDragSt = useState({ dragging: false });
      // ── 【用户反馈 2】子代理树的逐分支折叠状态（id -> true 表示已收起）─────
      //   ⚠️ 必须放在【hooks 区】、任何 return 之前 —— 放在渲染阶段（早退之后）
      //      会让 hooks 数量随 entries 从 0 变非 0 而跳变，React 直接抛错 →
      //      整个子代理面板（含右侧小标签）消失。
      var collapsedSt = useState({});
      var fabMovedRef = useRef(false);          // 本次按下是否真的移动过
      var suppressClickRef = useRef(false);     // 拖动后的那次 click 要吞掉
      var fabSizeRef = useRef({ w: 0, h: 0 });  // 小标签实际尺寸（夹紧到视口用）
      // ── 【拖动丢失修复】拖动中的实时坐标必须放 ref，不能放 state ──────────
      //   现场：小标签只能左右动，上下完全拖不动。
      //   根因：onUp 里读的是 `fabDragSt[0]` —— 那是【本次渲染时捕获的快照】，
      //     React 的 setState 不会改写闭包里的那个旧元组，所以读到永远是
      //     {dragging:false}（没有 live）→ 回退到起点 originX/originY →
      //     松手瞬间又跳回原位，垂直位移 100% 丢失。
      //   修法：live 坐标写 ref（跨渲染稳定），onUp 从 ref 读。
      var fabLiveRef = useRef(null);
      var fabPos = fabPosSt[0];
      var fabDrag = fabDragSt[0];
      var askIdsSt = useState([]);     // 当前待答的 childId 列表（有序）
      var askIds = askIdsSt[0];
      useEffect(function () {
        if (entries.length === 0) { if (selected !== null) selSt[1](null); return; }
        var ok = false;
        for (var i = 0; i < entries.length; i++) { if (entries[i].id === selected) ok = true; }
        if (!ok) { selSt[1](entries[0].id); seqRef.current = 0; logSt[1]([]); errSt[1](null); }
      }, [ids, selected]);
      useEffect(function () {
        if (!selected || closedSt[0]) return;
        // ── 【本次改动·Bug2】切到某个子代理：命中缓存就直接渲染，绝不先清空 ──
        //   旧实现无条件 seqRef=0 + logSt=[] → 每次切换都白屏闪一下。
        var cached = logCacheRef.current[selected];
        if (cached && Array.isArray(cached.items)) {
          seqRef.current = cached.seq || 0;
          logSt[1](cached.items);
        } else {
          seqRef.current = 0;
          logSt[1]([]);
        }
        errSt[1](null);
        var alive = true;
        var pull = function () {
          // 【问题2 优化】limit 从 300 降到 120：
          //   增量拉取（since=seqRef）本身只取新事件，300 是纯浪费；
          //   首屏 120 条足够，后续每次通常只有几条。
          fetch('/dsh-engineering-ui/log?sessionId=' + encodeURIComponent(selected) + '&since=' + seqRef.current + '&limit=120')
            .then(function (r) { return r.json(); })
            .then(function (j) {
              if (!alive) return;
              if (!j || j.ok !== true) { errSt[1](String((j && j.error) || 'no data')); return; }
              errSt[1](null);
              if (j.latestSeq) seqRef.current = j.latestSeq;
              var _hasAsk = false;
              if (j.events && j.events.length) {
                var disp = [];
                for (var pi = 0; pi < j.events.length; pi++) {
                  var arr = toDisplayEvents(j.events[pi]);
                  for (var pk = 0; pk < arr.length; pk++) {
                    disp.push(arr[pk]);
                    if (arr[pk] && arr[pk].kind === 'tool-call' && arr[pk].tool === 'ask_user_question') _hasAsk = true;
                  }
                }
                // 【问题2】发现新提问 → 登记该子代理为"待答"
                if (_hasAsk) {
                  var _key = selected;
                  var _prev = waitRef.current[_key];
                  var _seq = j.latestSeq || 0;
                  if (!_prev || _seq > _prev.seq) {
                    waitRef.current[_key] = { seq: _seq, at: Date.now() };
                  }
                }
                // 【问题2 优化】渲染窗口 1000 -> 400：
                //   面板里同时渲染上千个 DOM 节点是卡顿主因；
                //   400 条已远超一屏可见范围，滚动查看历史仍够用。
                //   同时保持"只有真有新事件才 setState"，避免空轮询触发重渲染。
                if (disp.length > 0) {
                  logSt[1](function (prev) {
                    var next = prev.concat(disp);
                    return next.length > 400 ? next.slice(-400) : next;
                  });
                  // 【本次改动·Bug2】同步写缓存：切走再切回不用重新拉、不闪白
                  try {
                    var _c = logCacheRef.current[selected];
                    var _items = (_c && Array.isArray(_c.items) ? _c.items : []).concat(disp);
                    if (_items.length > 400) _items = _items.slice(-400);
                    logCacheRef.current[selected] = { items: _items, seq: seqRef.current };
                  } catch (e2) {}
                }
              }
            })
            .catch(function (e) { if (alive) errSt[1](String((e && e.message) || e)); });
        };
        // ── 【问题2 优化】自适应轮询（自调度，无多余定时器）──────────────
        // 原实现固定 1500ms 轮询：小屋在跑时够快，但空闲/已完成时仍在
        //   无谓地打接口，累积成"加载慢、响应迟"。
        // 现改为：本轮有新事件 → 800ms 快速跟进；连续空轮 → 退避到 3000ms。
        //   功能完全不变（仍是增量拉取 since=seqRef），只是请求节奏贴合活跃度。
        var idleRounds = 0;
        var timer = null;
        var schedule = function (ms) {
          if (!alive) return;
          timer = setTimeout(run, ms);
        };
        var run = function () {
          if (!alive) return;
          var before = seqRef.current;
          pull();
          // 用微任务后检查 seq 是否推进来决定下次间隔
          setTimeout(function () {
            if (!alive) return;
            if (seqRef.current !== before) {
              idleRounds = 0;
              schedule(800);
            } else {
              idleRounds++;
              schedule(idleRounds > 6 ? 3000 : (idleRounds > 3 ? 2000 : 1500));
            }
          }, 60);
        };
        run();
        return function () { alive = false; clearTimeout(timer); };
      }, [selected, closedSt[0]]);
      // ── 【本次改动·Bug2】滚动跟随：只在用户贴着底部时才自动滚到底 ──────
      //   旧实现每次都强制 scrollTop = scrollHeight，用户往上翻看历史时会被
      //   反复拽回底部，配合整块重渲染就成了"一闪一闪"的观感。
      //   现在：贴底 → 跟随；用户上翻 → 完全不动（不打断阅读）。
      useEffect(function () {
        var box = boxRef.current;
        if (!box) return;
        var onScroll = function () {
          var gap = box.scrollHeight - box.scrollTop - box.clientHeight;
          stickBottomRef.current = gap < 40;
        };
        onScroll();
        box.addEventListener('scroll', onScroll, { passive: true });
        return function () { box.removeEventListener('scroll', onScroll); };
      }, [selected]);
      useEffect(function () {
        var box = boxRef.current;
        if (!box) return;
        if (!stickBottomRef.current) return;
        box.scrollTop = box.scrollHeight;
      }, [logSt[0].length, selected]);
      // ── 【问题3 修复】自动顶替：让【正在干活】的子代理占据展示位 ──────
      // 用户诉求原文：小屋1 已完成不用干活了，小屋5 在干活，
      //   右边就应该展示小屋5，而不是用户不点就一直卡在小屋1。
      //
      // 优先级（从高到低）：
      //   1) 正在等待用户作答的子代理（最高，否则用户看不见提问）
      //   2) 正在运行(running)的子代理
      //   3) 保持用户手动选择（不打扰）
      //
      // 关键约束：用户【手动点选】过的子代理不会被抢走；
      //   只有当用户当前看的这个已经不在运行、且另有在跑的，才顶替。
      // 用 ref 记录用户最后一次手动点击，避免与自动逻辑打架。
      useEffect(function () {
        if (typeof setInterval !== 'function') return;
        var timer = setInterval(function () {
          try {
            var cur = selSt[0];
            var sums = summaries || {};
            var isRun = function (id) {
              var s = sums[id];
              if (s && s.running) return true;
              // 兜底：agent 条目自身的 activity
              for (var ai = 0; ai < entries.length; ai++) {
                if (entries[ai].id === id) return entries[ai].activity === 'running';
              }
              return false;
            };
            // (1) 待答优先（保持原逻辑）
            var waits = waitRef.current || {};
            var pending = [];
            for (var k in waits) {
              if (Object.prototype.hasOwnProperty.call(waits, k)) pending.push(k);
            }
            if (pending.length > 0) {
              pending.sort(function (a, b) { return (waits[a].seq || 0) - (waits[b].seq || 0); });
              var head = pending[0];
              if (cur !== head) {
                if (!prevSelRef.current) prevSelRef.current = cur;
                selSt[1](head);
              }
              return;
            }
            // (2) 【本次改动·Bug2】运行中优先 —— 但【用户钉住的子代理优先于它】
            //   用户明确要求："应该是我（用户）不看的时候，再换到正在运行的
            //   子代理去"。所以：
            //     · 用户手动点过某个子代理（pinnedRef）→ 永远不抢；
            //     · 只有用户没钉住、且当前看的这个已经停了，才让运行中的顶替。
            //   另外保留 3 秒宽限，避免"刚松手就被换走"的错愕感。
            var pinned = pinnedRef.current;
            if (pinned && pinned === cur) return;      // 钉住 → 不动
            var manualAt = manualPickRef.current || 0;
            if (Date.now() - manualAt < 3000) return;
            if (cur && isRun(cur)) return;          // 正在看的就在跑 → 不动
            var runId = null;
            for (var i = 0; i < entries.length; i++) {
              if (isRun(entries[i].id)) { runId = entries[i].id; break; }
            }
            if (runId && runId !== cur) {
              // 不在这里清日志：由 selected 变化的 effect 负责命中缓存
              selSt[1](runId);
            }
          } catch (e) {}
        }, 1000);
        return function () { clearInterval(timer); };
      }, [ids, summaries]);
      // 答完清账：当某子代理的 ask 卡已提交成功，从待答队列移除
      var clearWait = function (childId) {
        if (waitRef.current && waitRef.current[childId]) {
          delete waitRef.current[childId];
        }
      };
      // ── 【布局 v3】响应式：仅用于"面板内部堆叠"判断 ─────────────────
      // 面板本身始终是【流内并排】的侧边栏 —— 空间不足时由用户折叠，
      // 或由下面的 stacked 模式让目录/日志纵向排列，绝不覆盖主内容。
      var veryNarrowSt = useState(false);
      useEffect(function () {
        if (typeof window === 'undefined') return;
        var onResize = function () {
          veryNarrowSt[1]((window.innerWidth || 1200) < 900);
        };
        onResize();
        window.addEventListener('resize', onResize);
        return function () { window.removeEventListener('resize', onResize); };
      }, []);
      var veryNarrow = veryNarrowSt[0];

      // ══ 【布局 v11】展开/收起 → 切换 body.eng-pane-open ═════════════
      // 列宽的开合完全由样式表规则驱动（body.eng-pane-open），
      // JS 只负责：① 维护这个类；② 按视口计算 --eng-pane（3:1 比例）。
      // 不再触碰宿主元素的内联样式 —— 与 React 零冲突。
      // ── 【本次改动·Bug3 根因】ENG_MODE_CLASS 的维护已【移出本组件】────────
      // 旧实现在这里的 useEffect 里挂 body.dsh-eng-mode，但本组件会在
      //   · preset !== 'engineering'
      //   · 设置里关掉「使用工程模式自带的子代理显示页」
      //   · entries.length === 0
      // 时提前 return —— 一旦提前 return，这个 effect 根本不会执行，
      // body 上就没有 dsh-eng-mode，于是：
      //   · 主对话问答卡片样式不生效；
      //   （该函数已随倒计时删除。）
      // 现在改由常驻组件 EngModeMarker 独占维护（见文件上部），
      //   与面板是否渲染彻底解耦。本组件不再重复挂这个类（避免互相抢着摘）。

      // ══ 【问题4·开启侧】嵌入模式（渲染在官方右列 tab 内）═════════════════
      // 当本组件作为"官方 subagentchat tab 的正文"渲染时（engEmbedded=true），
      //   官方右列已经提供了容器与宽度，因此【绝不能】再去抢占第三列、
      //   也不能去改 grid-template-columns —— 否则会和官方右列打架。
      // 嵌入模式下：只渲染面板本体（.eng-console 用流内相对定位），
      //   并跳过所有"抢列"的副作用。
      var embedded = !!(props && props.engEmbedded);

      var active = entries.length > 0 && !closedSt[0];
      // ══ 【问题3 修复】官方右列（文件预览等）打开时，本面板必须让位 ══════
      // 症状：点击 AI 生成的文件后，官方文档预览要在右列显示，
      //   但本插件的面板强占了第三列（用 !important 覆盖 grid-template-columns），
      //   导致预览被挤掉 / 无法在右侧正常查看。
      //
      // 机制（已核对 0.2.0 源码）：
      //   · 右列是 ui-layout 的 `rightbar` track，官方住户是 ui-sidebar-right
      //     的 tab 系统（文件/文档预览注册进 `sidebar.right.tab.document`）。
      //   · 该 track 的开合由 layout store 的 `rightbarTrack` 决定，
      //     AppFrame 把它反映为 frame 元素上的属性：
      //       data-rightbar-collapsed  (track 关闭时存在)
      //       data-rightbar-fullscreen (全屏覆盖时存在)
      //     （ui-layout/src/client/AppFrame.tsx:268-271）
      //   · 本插件是 shell.overlay 的住户，官方并不知道我们占了第三列，
      //     所以必须【自己检测】官方右列是否已打开，并主动让位。
      //
      // 判据（任一成立即让位）：
      //   ① frame 上没有 data-rightbar-collapsed → 官方 track 已打开；
      //   ② frame 上有 data-rightbar-fullscreen → 官方全屏覆盖。
      // 让位动作：摘掉 eng-pane-open（列宽归 0、面板透明），
      //   但保留 DOCK_CLASS 与面板挂载，官方关闭后自动恢复。
      var yieldSt = useState(false);
      var yieldToHost = yieldSt[0];
      useEffect(function () {
        if (typeof document === 'undefined') return;
        var tick = function () {
          try {
            var shouldYield = hostRightbarBusy();
            yieldSt[1](function (prev) { return prev === shouldYield ? prev : shouldYield; });
          } catch (e) { /* 保持上一次判定 */ }
        };
        tick();
        var mo = null;
        try {
          if (typeof MutationObserver === 'function') {
            mo = new MutationObserver(tick);
            mo.observe(document.body, { childList: true, subtree: true,
                                        attributes: true,
                                        attributeFilter: ['data-rightbar-collapsed',
                                                          'data-rightbar-fullscreen',
                                                          'style'] });
          }
        } catch (e) { mo = null; }
        window.addEventListener('resize', tick);
        var iv = setInterval(tick, 700);
        return function () {
          try { if (mo) mo.disconnect(); } catch (e) {}
          window.removeEventListener('resize', tick);
          clearInterval(iv);
        };
      }, []);
      // 实际生效的"打开"状态：官方没占右列时才展开本面板。
      // 嵌入模式（在官方右列 tab 内）【永不】抢列 → paneOpen 恒 false。
      var paneOpen = active && !yieldToHost && !embedded;
      // ── 【问题1/4 根因修复·关键】把影响"是否该占列"的每个输入都放进依赖 ──
      // React 语义：effect 只在【依赖变化】时重跑/清理。
      //   原依赖是 [active, paneOpen, embedded]，而 active 定义在 preset/setting
      //   门禁【之前】，所以：
      //     · 关掉设置开关后本组件 `return null`（不再渲染），
      //       effect 依赖若未变化 → 【清理函数不执行】→
      //       body 上的 dsh-eng-dock / eng-pane-open【永久残留】→
      //       CSS 继续把第三列强行撑开 = 右侧那条空白蓝带，
      //       而且它连【左侧项目栏的边界】一起改（grid 三列比例被覆盖）。
      //   这就是"设置没有用""蓝边又来了"的真正原因：不是渲染没生效，
      //   而是【卸载时没把 body 类摘掉】。
      //   另外 engAllowed 显式表达"本会话是否允许我占列"，
      //   让「预设切换」和「设置开关」都能触发一次清理。
      var engAllowed = (preset === 'engineering') && !!engSettings.useOwnSubagentPane;
      // ── 【根因修复·让位时必须整体摘掉 DOCK_CLASS】──────────────────────
      // 之前"让位"只摘 eng-pane-open，却【故意保留】DOCK_CLASS 想"平滑恢复"。
      //   后果：body 上始终留着我方类名，而样式表里凡是 `body.dsh-eng-dock …`
      //   的规则仍在参与层叠 —— 用户点开子代理时列宽突变，看起来就是
      //   "旁边项目的边框又跳出来了"。
      // 正确语义：要么【完整占列】（两个类都在），要么【完全不介入】（两个类都无）。
      //   中间态（只有 DOCK_CLASS）没有任何规则依赖，纯粹是污染源。
      useEffect(function () {
        if (typeof document === 'undefined') return;
        if (engAllowed && active && !embedded) {
          document.body.classList.add(DOCK_CLASS);
          if (paneOpen) {
            document.body.classList.add("eng-pane-open");
            var px = _calcPaneWidth();
            if (px > 0) {
              _syncTreeWidth(px);
              document.documentElement.style.setProperty("--eng-pane", px + "px");
            }
          } else {
            // 让位给官方右列：两个类都摘掉 → 我方 grid 覆盖整条失效，
            //   宿主的【内联】grid-template-columns 自然生效。
        try { _resetTreeWidth(); } catch (e) {}
            document.body.classList.remove("eng-pane-open");
            document.body.classList.remove(DOCK_CLASS);
          }
        } else {
          document.body.classList.remove(DOCK_CLASS);
        try { _resetTreeWidth(); } catch (e) {}
          document.body.classList.remove("eng-pane-open");
        }
        return function () {
          document.body.classList.remove(DOCK_CLASS);
        try { _resetTreeWidth(); } catch (e) {}
          document.body.classList.remove("eng-pane-open");
        };
      }, [active, paneOpen, embedded, engAllowed]);
      // ══ 【布局 v11 · 自适应】════════════════════════════════════════
      // 三列布局由样式表规则驱动，无需再清理让位残留（旧 margin 机制已废弃）。
      // 这里只做一件事：视口/宿主结构变化时，重算 --eng-pane（3:1 比例），
      // 保证面板始终占 25%、主区不低于 600px。折叠态无需处理（列宽为 0）。
      useEffect(function () {
        if (typeof window === 'undefined') return;
        var t = null;
        var onRe = function () {
          if (t) clearTimeout(t);
          t = setTimeout(function () {
            if (!document.body.classList.contains('eng-pane-open')) return;
            var px = _calcPaneWidth();
            if (px > 0) {
              _syncTreeWidth(px);
              document.documentElement.style.setProperty("--eng-pane", px + "px");
            }
          }, 140);
        };
        window.addEventListener('resize', onRe);
        if (typeof MutationObserver === 'function') {
          var mo = new MutationObserver(onRe);
          mo.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['style'] });
          return function () {
            window.removeEventListener('resize', onRe);
            try { mo.disconnect(); } catch (e) {}
            if (t) clearTimeout(t);
          };
        }
        return function () {
          window.removeEventListener('resize', onRe);
          if (t) clearTimeout(t);
        };
      }, []);

      // ── 【问题4 修复】非工程模式严禁渲染本面板 ──────────────────────────
      // 原条件: if (preset !== undefined && preset !== 'engineering') return null;
      //   漏洞：preset 为 undefined（会话数据尚未加载/字段缺失）时，
      //   第一个条件为 false → 整个判断被跳过 → 面板照样渲染。
      //   后果：在其他模式开子代理时，工程模式的面板会跳出来且一片空白，
      //   还会因为拿不到 catalog 而报错。
      // 修复：改为【白名单】——只有明确等于 'engineering' 才渲染，
      //   其余一律（含 undefined/null/其他预设）不渲染。
      //
      // 【P0-4 修复·判定顺序】预设门禁必须放在【最前面】。
      //   原顺序是先 `entries.length === 0` 再查 preset：虽然结果一样，
      //   但一旦将来 entries 的判定放宽，非工程模式就可能先走到后面的
      //   副作用（加 body class），造成样式外溢到其他模式。
      //   先判预设 = 从结构上保证"只在工程模式生效"。
      if (preset !== 'engineering') {
        // 复位：切到非工程模式后，官方 tab 接管必须立刻让位（见 canOpen）
        try { _engContextActive = false; } catch (e) {}
        return null;
      }
      // ── 【问题4】设置开关：关闭则隐藏本插件面板，右侧交回官方 ──────────
      //   注意：此判断必须在【所有 hooks 之后】，否则 hooks 数量会随设置变化，
      //   触发 "Rendered fewer hooks than expected"。useEngSettings 本身是 hook，
      //   已在 hooks 区调用，这里只读它的值。
      if (!engSettings.useOwnSubagentPane) {
        try { _engContextActive = false; } catch (e) {}
        return null;
      }
      if (entries.length === 0) {
        try { _engContextActive = false; } catch (e) {}
        return null;
      }
      // 【问题4】标记"当前上下文确为工程模式"，供官方 tab 接管的 canOpen 读取。
      //   canOpen 在 React 之外【同步】执行，读不到 hook，因此用一个模块级布尔；
      //   这里在两道门禁都通过之后置位，语义最准确。
      // 注意：必须在"非工程模式"时【复位】，否则一旦某次渲染置为 true，
      //   后续切到普通对话时 canOpen 仍返回 true 会误接管官方 tab。
      try { _engContextActive = true; } catch (e) {}

      // ── 【布局 v4】折叠态 = 边缘小标签；展开态 = Flex 侧边栏 ─────────
      // 关键：面板【始终挂载】，只用 .collapsed 类切换宽度，
      //       这样 width 过渡动画才能生效（条件 return 会直接闪现、无动画）。
      // 嵌入模式（官方右列 tab 内）：容器由官方提供，恒为展开、不渲染小标签。
      var isCollapsed = embedded ? false : closedSt[0];
      var anyRunning = entries.some(function (e3) {
        var s3 = summaries ? summaries[e3.id] : null;
        return e3.activity === 'running' || !!(s3 && s3.running);
      });

      // 收起时：仅渲染右边缘的竖向小标签（fixed 定位，不占布局宽度）
      // 【让位时也不渲染】官方占着右列时，我方小标签同样不该出现
      //   （否则又变成用户看到的"多出来的蓝色小边"）。
      // ── 【本次改动·Bug4】拖动逻辑（在早退之后定义，不新增 hooks）──────
      // ══ 【小标签吸附 · 用户反馈 1】拖到中间必须自动吸回边上 ══════════════════
      // 现场：小标签能拖动，但拖到屏幕中间就停在那儿"漂在空中"，很怪。
      // 期望（用户原话）：「在拖到非俩边的时候应该是要自动吸附到右边的」。
      //
      // 设计：
      //   · 拖动中 —— 完全跟随鼠标（自由位置），给足"我在拖它"的反馈；
      //   · 松手   —— 吸附到【最近的边】（左/右），并记住 side；
      //   · 竖向 —— 只在视口内夹紧，不吸附上下（贴顶部/底部更难看）。
      //   · 记住 side 而不是绝对 x：窗口变宽/变窄后仍按边重贴，不会跑到屏幕外。
      var FAB_W_FALLBACK = 44, FAB_H_FALLBACK = 92;
      /** 把 (x, y) 夹紧到视口内。 */
      var clampFab = function (x, y) {
        try {
          var vw = (typeof window !== 'undefined' ? window.innerWidth : 1200) || 1200;
          var vh = (typeof window !== 'undefined' ? window.innerHeight : 800) || 800;
          var w = fabSizeRef.current.w || FAB_W_FALLBACK;
          var h = fabSizeRef.current.h || FAB_H_FALLBACK;
          var nx = Math.max(0, Math.min(vw - w, x));
          var ny = Math.max(4, Math.min(vh - h - 4, y));
          return { x: nx, y: ny };
        } catch (e) { return { x: x, y: y }; }
      };
      /** 吸附：返回 { x, y, side }，x 为贴边后的左坐标。 */
      var snapFab = function (x, y) {
        try {
          var vw = (typeof window !== 'undefined' ? window.innerWidth : 1200) || 1200;
          var w = fabSizeRef.current.w || FAB_W_FALLBACK;
          var c = clampFab(x, y);
          // 以"离哪边更近"决定吸附方向（用中心点比较，比用左边界更符合直觉）
          var centerX = c.x + w / 2;
          var side = (centerX <= vw / 2) ? 'left' : 'right';
          var nx = (side === 'left') ? 0 : Math.max(0, vw - w);
          return { x: nx, y: c.y, side: side };
        } catch (e) { return { x: x, y: y, side: null }; }
      };
      /** 由已保存的 {x,y,side} 算出这次渲染的 left/top。 */
      var fabStyle = undefined;
      // ── 【小标签消失兜底】存下来的位置若已经完全在视口外（换显示器 / 缩放 /
      //    分辨率变化），不能死认它 —— 那会让标签跑到屏幕外，用户以为它被删了。
      //    这里判为「无效位置」并回落到右侧垂直居中的默认位。
      if (fabPos && fabPos !== null) {
        try {
          var _vh0 = (typeof window !== 'undefined' ? window.innerHeight : 800) || 800;
          var _vw0 = (typeof window !== 'undefined' ? window.innerWidth : 1200) || 1200;
          var _badY = !(fabPos.y >= 0 && fabPos.y <= _vh0 - 8);
          var _badX = (!fabPos.side && typeof fabPos.x === 'number' &&
                       (fabPos.x < 0 || fabPos.x > _vw0 - 8));
          if (_badY || _badX) fabPos = null;
        } catch (e) { /* 判定失败就照旧用存下来的值 */ }
      }
      if (fabPos && fabPos !== null) {
        try {
          var _vw = (typeof window !== 'undefined' ? window.innerWidth : 1200) || 1200;
          var _w = fabSizeRef.current.w || FAB_W_FALLBACK;
          var _x = fabPos.x;
          if (fabPos.side === 'left') _x = 0;
          else if (fabPos.side === 'right') _x = Math.max(0, _vw - _w);
          if (typeof _x === 'number' && isFinite(_x)) {
            var _c0 = clampFab(_x, fabPos.y);
            fabStyle = { left: _c0.x + 'px', top: _c0.y + 'px',
                        // ⚠️ 必须清掉基础规则里的 translateY(-50%)，否则贴边后
                        //    会被整体上移半个高度（看起来像"消失了"）。
                        right: 'auto', bottom: 'auto', transform: 'none' };
          }
        } catch (e) { fabStyle = undefined; }
      }
      // 拖动进行中：用临时坐标（跟随鼠标），不受已保存位置影响
      if (fabDrag && fabDrag.live && typeof fabDrag.live.x === 'number') {
        fabStyle = {
          left: fabDrag.live.x + 'px',
          top: fabDrag.live.y + 'px',
          right: 'auto', bottom: 'auto', transform: 'none'
        };
      }
      var fabOnMouseDown = function (ev) {
        try {
          if (!ev || (ev.button !== undefined && ev.button !== 0)) return;
          var el = ev.currentTarget;
          var rect = el && el.getBoundingClientRect ? el.getBoundingClientRect() : null;
          if (!rect) return;
          // 记住真实尺寸（夹紧 / 吸附都要用）
          fabSizeRef.current = { w: rect.width || FAB_W_FALLBACK, h: rect.height || FAB_H_FALLBACK };
          var startX = ev.clientX, startY = ev.clientY;
          var originX = rect.left, originY = rect.top;
          var moved = false;
          fabMovedRef.current = false;
          suppressClickRef.current = false;
            fabLiveRef.current = null;   // 每次按下重置实时坐标
          try { el.setPointerCapture && el.setPointerCapture(ev.pointerId); } catch (e0) {}
          try { document.body.style.userSelect = 'none'; } catch (e0) {}

          var onMove = function (mv) {
            var dx = mv.clientX - startX, dy = mv.clientY - startY;
            if (!moved && (Math.abs(dx) > 3 || Math.abs(dy) > 3)) {
              moved = true;
              fabMovedRef.current = true;
            }
            if (!moved) return;
            var next = clampFab(originX + dx, originY + dy);
            // ★ 实时坐标【同时】写 ref（onUp 从 ref 读，闭包里的 state 是旧快照）
            fabLiveRef.current = next;
            // 拖动中自由跟随（live），松手才吸附
            fabDragSt[1]({ dragging: true, live: next });
            if (mv.preventDefault) mv.preventDefault();
          };
          var onUp = function () {
            try { document.removeEventListener('mousemove', onMove, true); } catch (e1) {}
            try { document.removeEventListener('mouseup', onUp, true); } catch (e1) {}
            try { document.body.style.userSelect = ''; } catch (e1) {}
            if (moved) {
              // ★ 松手 → 吸附到最近的边（用户要求：非两边时回右边）
              // ★ 必须从 ref 读：闭包里的 fabDragSt[0] 是旧快照（没有 live），
              //   那样会让垂直位移全部丢失、松手后弹回原点。
              var live = fabLiveRef.current || clampFab(originX, originY);
              var snapped = snapFab(live.x, live.y);
              fabPosSt[1](snapped);
              fabDragSt[1]({ dragging: false });
              // 吞掉随后那次 click（否则一拖就展开面板）
              suppressClickRef.current = true;
              try { saveFabPosition(snapped); } catch (e2) {}
              fabLiveRef.current = null;
              setTimeout(function () { fabMovedRef.current = false; }, 0);
            } else {
              fabDragSt[1]({ dragging: false });
            }
          };
          document.addEventListener('mousemove', onMove, true);
          document.addEventListener('mouseup', onUp, true);
        } catch (e) {}
      };
      var fabResetPos = function (ev) {
        try { if (ev && ev.preventDefault) ev.preventDefault(); } catch (e0) {}
        try {
          fabSizeRef.current = { w: 0, h: 0 };
          fabPosSt[1](null);
          saveFabPosition(null);
        } catch (e) {}
      };
      // 视口变化（缩放窗口 / 换分辨率）：按【记住的边】重新贴，竖向夹紧。
      //   ⚠️ 只改这次渲染用的 style，不回写 state —— 渲染期禁止 setState。
      if (fabPos && !(fabDrag && fabDrag.live)) {
        try {
          var _vw2 = (typeof window !== 'undefined' ? window.innerWidth : 1200) || 1200;
          var _w2 = fabSizeRef.current.w || FAB_W_FALLBACK;
          var _want = (fabPos.side === 'left') ? 0
                    : (fabPos.side === 'right') ? Math.max(0, _vw2 - _w2)
                    : fabPos.x;
          var _c2 = clampFab(_want, fabPos.y);
          if (_c2 && (Math.abs(_c2.x - _want) > 1 || Math.abs(_c2.y - fabPos.y) > 1)) {
            fabStyle = {
              left: _c2.x + 'px', top: _c2.y + 'px', right: 'auto', bottom: 'auto'
            };
          }
        } catch (e) {}
      }


      // ── 【本次改动·Bug4】收起态小标签支持【拖动】──────────────────────
      // 用户反馈：这个"子代理"小按钮拖不动。旧实现是固定的
      //   position:fixed; right:0; top:50% —— 没有任何拖动逻辑。
      // 现在：
      //   · 拖 → 松手后移动到新位置（限制在视口内）；
      //   · 没拖动（位移 < 4px）→ 视为点击，照常展开面板；
      //   · 位置存 localStorage，刷新/重启后仍在用户放的地方；
      //   · 双击 → 复位到右侧垂直居中。
      var fabSide = (fabPos && fabPos.side) ? fabPos.side : 'right';
      var fab = (isCollapsed && !yieldToHost) ? h('button', {
        className: 'eng-fab' + (fabSide === 'left' ? ' left' : '') +
                   (anyRunning ? ' run' : '') + (fabDrag.dragging ? ' dragging' : ''),
        style: fabStyle,
        title: '展开子代理面板（' + entries.length + ' 个子代理' +
               (anyRunning ? '，有运行中' : '') + '）；可拖动（松手自动贴边），双击复位到右侧',
        onMouseDown: fabOnMouseDown,
        onDoubleClick: fabResetPos,
        onClick: function (ev) {
          // 拖动结束时浏览器也会补一个 click —— 必须吞掉，否则一拖就展开
          try {
            if (fabMovedRef.current || suppressClickRef.current) {
              fabMovedRef.current = false;
              suppressClickRef.current = false;
              if (ev && ev.preventDefault) ev.preventDefault();
              return;
            }
          } catch (e) {}
          closedSt[1](false);
        }
      },
        h('span', { className: 'eng-fab-ico' }, '‹'),
        h('span', { className: 'eng-fab-txt' }, '子代理'),
        h('span', { className: 'eng-fab-badge' }, treeCountText),
        anyRunning ? h('span', { className: 'eng-fab-dot' }) : null) : null;

      var current = null;
      for (var k = 0; k < entries.length; k++) { if (entries[k].id === selected) current = entries[k]; }
      var sum = summaries && current ? summaries[current.id] : null;
      var running = !!(current && (current.activity === 'running' || (sum && sum.running)));
      var done = !!(sum && sum.completed);
      var title = current ? String(current.label || (sum && sum.displayTitle) || current.id) : '子代理详情';
      // ── 【用户反馈 2】层级化渲染：缩进 + 折叠 + 层级徽标 ────────────────────
      // 只把整棵子树拍平成一行行还不够 —— 用户要的是"分得出等级"。
      // 这里做三件事：
      //   ① 按 depth 缩进（每级 14px），并用竖线把父子连起来；
      //   ② 有下级的条目给一个折叠三角（默认展开，收起后整条分支隐藏）；
      //   ③ 徽标区分身份：直接子代理不显示，二级显示 L2，依次类推；
      //      diagnostic（宿主报"读不到该分支"）单独标成灰色"不可读"。
      //
      // 折叠状态用 state 存（用户手动收起的分支在轮询刷新后仍然保持收起）。
      var collapsed = collapsedSt[0];
      // 计算"因祖先被收起而应隐藏"的 id 集合（扁平表 → 可见子集）
      var visibleIds = {};
      var depthOfId = {};
      var parentOfId = {};
      for (var vi = 0; vi < entries.length; vi++) {
        var ve = entries[vi];
        depthOfId[ve.id] = (typeof ve.depth === 'number' && ve.depth > 0) ? ve.depth : 1;
        parentOfId[ve.id] = ve.parentId || null;
      }
      for (var wi = 0; wi < entries.length; wi++) {
        var we = entries[wi];
        // 向上找祖先链，只要有一个被收起就隐藏
        var hidden = false;
        var cursor = parentOfId[we.id];
        var guard = 0;
        while (cursor && guard < 32) {
          if (collapsed[cursor] === true) { hidden = true; break; }
          cursor = parentOfId[cursor];
          guard++;
        }
        if (!hidden) visibleIds[we.id] = true;
      }
      var visibleEntries = [];
      for (var wj = 0; wj < entries.length; wj++) {
        if (visibleIds[entries[wj].id]) visibleEntries.push(entries[wj]);
      }
      var hasKidsInList = function (id) {
        // 只有在【可见列表】里真有下级时才给折叠三角，否则是空三角
        for (var hk = 0; hk < entries.length; hk++) {
          if ((entries[hk].parentId || null) === id) return true;
        }
        return false;
      };
      var nodes = visibleEntries.map(function (e2) {
        var s2 = summaries ? summaries[e2.id] : null;
        var run = e2.activity === 'running' || !!(s2 && s2.running);
        var fin = !!(s2 && s2.completed);
        var isDiag = e2.kind === 'diagnostic';
        var cls = isDiag ? ' diag' : (run ? ' run' : (fin ? ' done' : ''));
        var state = isDiag ? '不可读'
                  : (run ? '运行中' : (fin ? '已完成' : '未运行'));
        var lb = String(e2.label || (s2 && s2.displayTitle) || e2.id);
        var dep = depthOfId[e2.id] || 1;
        var kids = (!isDiag) && hasKidsInList(e2.id);
        var isCollapsed = collapsed[e2.id] === true;
        // 【自适应缩进】每级 14px 在 190~300px 的频道列里太奢侈（3 级吃掉 42px）。
        var pad = (dep >= 2 ? 10 : 0) + (dep >= 3 ? 6 : 0) + (dep >= 4 ? 6 : 0);
        return h('div', {
          key: e2.id,
          className: 'eng-node-wrap' + (dep > 1 ? ' lv' : '') + (isDiag ? ' diag' : ''),
          style: pad > 0 ? { paddingLeft: pad + 'px' } : undefined
        },
          // 折叠三角（只有真有下级才出现；无下级时占位保持对齐）
          kids
            ? h('button', {
                className: 'eng-node-tw' + (isCollapsed ? ' off' : ''),
                type: 'button',
                title: isCollapsed ? '展开该子代理的下级' : '收起该子代理的下级',
                onClick: function (ev) {
                  try { ev.preventDefault(); ev.stopPropagation(); } catch (e0) {}
                  var next = {};
                  for (var k2 in collapsed) {
                    if (Object.prototype.hasOwnProperty.call(collapsed, k2)) next[k2] = collapsed[k2];
                  }
                  next[e2.id] = !isCollapsed;
                  collapsedSt[1](next);
                }
              }, isCollapsed ? '▸' : '▾')
            : h('span', { className: 'eng-node-tw ph' }),
          h('button', {
            className: 'eng-node' + (e2.id === selected ? ' on' : '') + (isDiag ? ' diag' : ''),
            type: 'button',
            title: lb + ' — ' + state + (dep > 1 ? ('（第 ' + dep + ' 级）') : ''),
            onClick: function () {
              try { manualPickRef.current = Date.now(); } catch (e) {}
              try { pinnedRef.current = e2.id; } catch (e) {}
              selSt[1](e2.id);
            }
          },
            h('span', { className: 'eng-dot' + cls }),
            // 层级徽标：1 级不显示（避免整列都是 L1 的噪音）
            dep > 1 ? h('span', { className: 'eng-lv' }, 'L' + dep) : null,
            h('span', { className: 'eng-node-label' }, lb),
            h('span', { className: 'eng-node-state' }, state)));
      });
      // 目录条目数徽标用【可见数/总数】—— 收起后用户能看出"还有几个没展开"
      var treeCountText = (visibleEntries.length === entries.length)
        ? String(entries.length)
        : (visibleEntries.length + '/' + entries.length);

      // ── 【本次改动·Bug2】info 必须【引用稳定】，否则 memo 永远失效 ──────
      //   旧实现每次渲染都新建对象 → EngEventRow 的浅比较必然不等 →
      //   整屏重建（这就是闪烁的另一半原因）。
      //   ⚠️ infoRef 的 useRef 声明在【上方 hooks 区】—— 这里只改字段，
      //      绝不能在"早退之后"新增 hook（会破坏 hooks 顺序）。
      if (!infoRef.current) infoRef.current = {};
      infoRef.current.parentSessionId = currentId;
      infoRef.current.childSessionId = selected;
      infoRef.current.onAnswered = clearWait;
      var info = infoRef.current;
      var body;
      if (errSt[0]) body = h(ErrorCard, { text: '无法读取子代理日志：' + errSt[0] });
      else if (logSt[0].length === 0) body = h('div', { className: 'eng-note' }, running ? '子代理正在运行，等待输出…' : '该子代理暂无输出');
      else body = logSt[0].map(function (ev, idx) {
        var _k = String(ev.sessionId || '') + ':' + String(ev.seq) + ':' + String(idx);
        return h(EngEventRow, { key: _k, ev: ev, idx: idx, info: info });
      });
      // ── 展开态：标准 Flex 侧边栏（挤压主区域，绝不覆盖）─────────────
      // 极窄屏（<900px）时目录与详情纵向堆叠，仍在流内。
      // 注意：面板始终挂载，用 .collapsed 类切换宽度以驱动过渡动画；
      //       收起时 width:0 + pointer-events:none，等价于完全不占位。
      var stackTree = veryNarrow;
      // 右列当前页（'flow' | 'compact'）：只是取值，不新增 hook。
      var rightTab = rightTabSt[0];
      // ── 【UI 美化】面板主体 ────────────────────────────────────────────
      // 用户反馈（对照 DSH 官方子代理界面）：自带的这套面板"很乱，看不来啥"。
      // 主要毛病与对应改法：
      //   ① 目录/详情/日志三层全是同等视觉重量 → 用【分区 + 层级】拉开：
      //      目录是次级导航（浅底、窄），详情是主角（白底、留白大）。
      //   ② 每个事件块长得一模一样 → 给每个事件加【类型徽标 + 图标】
      //      （思考/输出/命令/结果/报错），扫一眼就知道这一段是什么。
      //   ③ 事件之间没有分隔，密密麻麻 → 加时间轴竖线 + 卡片间距。
      //   ④ 标题栏信息挤成一行 → 拆成「名称 + 状态」与「副标题摘要」两行。
      var consoleEl = h('div', {
        className: 'eng-console' + (stackTree ? ' stacked' : '') +
                   (isCollapsed ? ' collapsed' : '') +
                   (embedded ? ' embedded' : '') +
                   (!embedded && yieldToHost ? ' yielded' : '')
      },
        h('div', { className: 'eng-tree' },
          h('div', { className: 'eng-tree-cap' },
            h('span', { className: 'eng-tree-cap-t' }, '子代理'),
            h('span', { className: 'eng-tree-cap-n' }, treeCountText),
            embedded ? null : h('button', {
              className: 'eng-collapse',
              title: '折叠子代理面板，让对话区占满全宽',
              onClick: function () { closedSt[1](true); }
            }, '›')),
          // ── 子代理频道列表：上半区，超出时自己滚动 ──────────────────────
          h('div', { className: 'eng-tree-list' }, nodes),
          // ── 【布局 v12 · 2026-11-07】左列下半区 = 零件表（占 50%）──────────
          // 用户要求（逐条落实）：
          //   ① 「把那个零件表扩大，扩大到 50% 的大小」→ 目录/零件表严格五五分；
          //   ② 「子代理目录和模型表(零件表)各占左边的 50%，自适应即可」
          //      → 两块都 flex:1 1 50% + min-height:0，各自内部滚动；
          //   ③ 「压缩历史移到右边那个绿色的框」→ 已搬到右列详情区（.eng-dtabs）。
          // ⚠️ 这里【只】放零件表；压缩历史在右列，绝不能再挂回来，
          //    否则同一个组件会出现两份（左窄+右宽），用户看到的还是"乱"。
          h('div', { className: 'eng-lower' },
            h(EngPartsBoard, { compact: true }))),
        h('div', { className: 'eng-detail' },
          h('div', { className: 'eng-head' },
            h('div', { className: 'eng-head-main' },
              h('span', { className: 'eng-name' }, title),
              h('span', { className: 'eng-tag' + (running ? ' run' : (done ? ' done' : '')) },
                running ? '运行中' : (done ? '已完成' : '未运行')),
              h('span', { className: 'eng-tag quiet' },
                current && current.mode === 'one-shot' ? '一次性' : '可持续')),
            h('div', { className: 'eng-head-sub' },
              String(logSt[0].length) + ' 条输出 · '
              + (running ? '正在实时接收' : (done ? '已结束' : '等待启动'))),
            embedded ? null : h('button', {
              className: 'eng-collapse',
              title: '折叠子代理面板',
              onClick: function () { closedSt[1](true); }
            }, '折叠')),
          // ── 【布局 v12】右列两页：实时流程 / 压缩历史 ─────────────────────
          // 用户要求：「左边的选择子代理的时候，右边会展示出来那个实际的流程，
          //   压缩历史也是同样的，左边选择了那个子代理，右边就有那个子代理
          //   展示页和对应的压缩历史」。
          //   ⇒ 页签本身不吃 hooks（直接渲染按钮），两页共享同一个 selected，
          //     切页不改变选中项，因此"选中谁 → 两页都是谁"是结构性成立的。
          h('div', { className: 'eng-dtabs' },
            h('button', {
              type: 'button',
              className: 'eng-dtab' + (rightTab === 'flow' ? ' on' : ''),
              title: '这个子代理的实时输出与工具调用',
              onClick: function () { rightTabSt[1]('flow'); },
            }, '实时流程'),
            h('button', {
              type: 'button',
              className: 'eng-dtab' + (rightTab === 'compact' ? ' on' : ''),
              title: '这个子代理的压缩历史（L0 机械数值是否被保住）',
              onClick: function () { rightTabSt[1]('compact'); },
            }, '压缩历史')),
          h('div', { className: 'eng-dtabs-body' },
            rightTab === 'compact'
              // 压缩历史：跟着选中项走。selectedId 变化 → 面板自己重新过滤。
              ? h(EngCompactPage, {
                  rootSessionId: currentId,
                  selectedId: selected,
                  label: title,
                })
              : h('div', { className: 'eng-log', ref: boxRef }, body))));
      // 用 Fragment 同时输出：收起态的小标签 + 面板本体
      // 嵌入模式不输出小标签（官方右列自己管开合）
      return embedded ? consoleEl : h(React.Fragment, null, consoleEl, fab);
    }


    // ══ 【Bug1】主对话内的交互提问节点 ═══════════════════════════════════════
    // 旧版 AskCard 只挂在 SubagentConsole（右侧子代理面板）里 —— 用户必须跑到
    // 子代理面板才能点选项，而答案又靠 followup 当"新消息"发回，属于伪交互。
    // 这里把同一个 AskCard 通过 conversation.chat.node 插槽挂进【主对话】，
    // 用户在主对话直接点选项即可；答案走 /answer 的权威确认通道投递。
    //
    // ChatNode 结构容错：不同 DSH 版本节点字段略有差异，这里做多路探测，
    // 拿不到就返回 null（不渲染），绝不抛错影响主对话。
    function MainAskNode(props) {
      var node = props && props.node;
      if (!node) return null;
      // 1) 取工具名
      var toolName = '';
      var call = node.call || node.toolCall || node.tool || null;
      if (call) toolName = String(call.name || call.tool || '');
      if (!toolName && node.toolName) toolName = String(node.toolName);
      if (!toolName && node.kind) toolName = String(node.kind);
      if (toolName.indexOf('ask_user') < 0) return null;
      // 2) 取参数（不同版本：arguments / args / input / params）
      var raw = null;
      if (call) raw = call.arguments !== undefined ? call.arguments : call.args;
      if (raw === undefined || raw === null) {
        raw = node.arguments !== undefined ? node.arguments : (node.args !== undefined ? node.args : node.input);
      }
      if (raw === undefined || raw === null) return null;
      var argsText = typeof raw === 'string' ? raw : (function () {
        try { return JSON.stringify(raw); } catch (e) { return ''; }
      })();
      var questions = parseQuestions(argsText);
      if (!questions) return null;
      // 3) 主对话自身的 sessionId 就是 parent（子代理提问已被平台禁止，
      //    真到这一步说明是主对话在提问 → 直接把答案留在主对话即可）
      var sid = node.sessionId || props.sessionId || '';
      return h(AskCard, {
        questions: questions,
        // 主对话自身是运行时根代理 → 只有它能通过 ask() 的所有权校验。
        sessionId: sid,
        parentSessionId: sid,
        childSessionId: sid,
        mainInline: true
      });
    }

    /* ══════════════════════════════════════════════════════════════════
     * 【零件进度表】EngPartsBoard
     * ══════════════════════════════════════════════════════════════════
     * 用户要求（2026-11-07，图二）：
     *   「红线上面是子代理，下面是要造的零件的表……总计子代理分析完成之后，
     *     把所有的零件都给放在那个展示表中……交给总理子代理去看零件完成度，
     *     正在建就同步成『正在建 xx 零件』，同理『未开始、完成』都一样，
     *     还有需要『重建』；假设状态是正在建模，那么假设 DSH 断了，比如直接
     *     关了 DSH 这种情况，那就显示『失败』」。
     *
     * 数据源：GET /parts-board（转发 mode2_flow.py parts-board）。
     *   ⚠️ 状态判定【一律在 Python 侧】——UI 只负责画。历史上 UI 自己猜状态
     *      与门禁打架，带来过"看板说活着、门禁说已死"的扯皮。
     */
    // 状态 → 徽标样式。教程 4.3 要求补齐 queued/validating/blocked/
    // disconnected/stale 这几档，因为监控器的零件状态机比旧的
    // 「未开始/正在建/完成」多出"等待 SW / 待验收 / 验收受阻 / 宿主失联 /
    // 文件已变更"。少了映射就会退化成灰色 idle，把"验收受阻"画成"未开始"。
    var ENG_PART_STATE_CLS = {
      done: 'ok',
      building: 'run',
      validating: 'run',
      queued: 'wait',
      rebuilding: 'warn',
      blocked: 'warn',
      stale: 'warn',
      failed: 'bad',
      disconnected: 'bad',
      not_started: 'idle',
    };
    function EngPartsBoard(props) {
      // 紧凑模式：渲染在左列窄区（用户把零件表从右侧详情区搬到了左列下方），
      //   房间列会被省略、状态徽标收窄，保证长中文零件名不被挤没。
      var compact = !!(props && props.compact);
      var boardCls = 'eng-parts-board' + (compact ? ' compact' : '');
      var useState = React.useState;
      var useEffect = React.useEffect;
      var st = useState({ data: null, err: null, busy: false });
      var S = st[0];
      var refresh = function () {
        st[1](function (p) {
          var n = {}; for (var k in p) if (Object.prototype.hasOwnProperty.call(p, k)) n[k] = p[k];
          n.busy = true; return n;
        });
        fetch('/dsh-engineering-ui/parts-board')
          .then(function (r) { return r.json(); })
          .then(function (j) {
            st[1](function (p) {
              var n = {}; for (var k in p) if (Object.prototype.hasOwnProperty.call(p, k)) n[k] = p[k];
              n.busy = false;
              if (j && j.ok !== false) { n.data = j; n.err = null; }
              else { n.err = String((j && j.error) || '读取失败'); }
              return n;
            });
          })
          .catch(function (e) {
            st[1](function (p) {
              var n = {}; for (var k in p) if (Object.prototype.hasOwnProperty.call(p, k)) n[k] = p[k];
              n.busy = false; n.err = String((e && e.message) || e); return n;
            });
          });
      };
      // 挂载即拉一次；面板开着时每 6s 刷一次（看板要跟得上建模节奏）
      useEffect(function () {
        refresh();
        var iv = setInterval(refresh, 6000);
        return function () { clearInterval(iv); };
      }, []);
      var data = S.data || {};
      var parts = Array.isArray(data.parts) ? data.parts : [];
      var sum = data.summary || {};
      var counts = sum.counts || {};
      // 徽标文案：优先用服务端给的 label（监控器是唯一的判定方，
      //   它的中文标签就是权威），本地表只在缺 label 时兜底。
      var label = function (k, serverLabel) {
        if (serverLabel) return String(serverLabel);
        return ({ done: '完成', building: '正在建', validating: '待验收',
                  queued: '等待 SW', rebuilding: '需要重建', blocked: '验收受阻',
                  stale: '文件已变更', failed: '失败', disconnected: '宿主失联',
                  not_started: '未开始' })[k] || k;
      };
      var head = h('div', { className: 'eng-pb-cap' },
        h('span', { className: 'eng-pb-cap-t' }, '零件'),
        h('span', { className: 'eng-pb-cap-n' },
          (parts.length ? (String(counts.done || 0) + '/' + parts.length) : '—')),
        h('button', {
          className: 'eng-pb-refresh', type: 'button', title: '刷新零件进度',
          onClick: function (ev) { ev.stopPropagation(); refresh(); },
        }, S.busy ? '…' : '↻'));
      if (S.err) {
        return h('div', { className: boardCls }, head,
          h('div', { className: 'eng-pb-empty' }, '读取失败：' + S.err));
      }
      if (!parts.length) {
        return h('div', { className: boardCls }, head,
          h('div', { className: 'eng-pb-empty' },
            data.generated_at ? '暂无零件（总计子代理还没登记编制）' : '读取中…'));
      }
      var rows = parts.map(function (p, i) {
        var cls = ENG_PART_STATE_CLS[p.state] || 'idle';
        // 验收明细（材料/物理/领域/仿真/登记）逐条进 tooltip：
        //   「差在哪一条证据」是使用者最常问的问题，不该藏在日志里。
        var checks = p.checks && typeof p.checks === 'object' ? p.checks : null;
        var checkLines = '';
        if (checks) {
          var ks = Object.keys(checks);
          if (ks.length) {
            checkLines = '\n验收：' + ks.map(function (k) {
              var v = checks[k] || {};
              return k + '=' + String(v.status || '?');
            }).join(' / ');
          }
        }
        return h('div', {
          className: 'eng-pb-row', key: 'pb' + i,
          title: (p.room ? ('房间：' + p.room + '\n') : '') + (p.detail || '')
            + (p.generation ? ('\n第 ' + p.generation + ' 代') : '')
            + (p.file ? ('\n文件：' + p.file) : '')
            + checkLines
            + (p.next_action ? ('\n下一步：' + p.next_action) : ''),
        },
          h('span', { className: 'eng-pb-name' }, String(p.name || '')),
          h('span', { className: 'eng-pb-badge ' + cls }, label(p.state, p.label)),
          compact ? null : h('span', { className: 'eng-pb-room' }, String(p.room || '')));
      });
      var foot = h('div', { className: 'eng-pb-ft' },
        ['done', 'building', 'validating', 'queued', 'rebuilding', 'blocked',
         'stale', 'failed', 'disconnected', 'not_started'].map(function (k) {
          if (!counts[k]) return null;
          return h('span', { className: 'eng-pb-stat ' + (ENG_PART_STATE_CLS[k] || 'idle'), key: 's' + k },
            label(k) + ' ' + counts[k]);
        }),
        // 来源与新鲜度：监控器不可达时必须让使用者看到"这是不可用"而不是
        // "零件列表是空的"（教程 4.3「后台断开时展示不可用或缓存时间」）。
        data.source === 'sw_monitor'
          ? h('span', { className: 'eng-pb-stat idle', key: 'src' }, '监控器')
          : null,
        data.pulse_alive === false
          ? h('span', { className: 'eng-pb-alert' },
              data.quarantined ? '监控器隔离中' : '宿主失联') : null,
        data.degraded
          ? h('span', { className: 'eng-pb-alert' }, '监控器不可用') : null);
      return h('div', { className: boardCls }, head,
        h('div', { className: 'eng-pb-list' }, rows), foot);
    }

    /* ══════════════════════════════════════════════════════════════════
     * 【压缩历史】EngCompactPage — 右列详情区的独立一页（用户截图里的「绿框」）
     * ══════════════════════════════════════════════════════════════════
     * 【布局 v12 · 2026-11-07】原先它是左列底部固定 40% 高的一小块（黑框），
     *   用户要求「那个压缩历史移到右边那个绿色的框（占 1/4）」并且
     *   「左边的选择子代理的时候，右边就有那个子代理展示页和对应的压缩历史」，
     *   于是改成右列 .eng-dtabs 的第二页：宽度 = 右列宽度，高度自适应，
     *   内容跟随左列 selectedId。
     * 用户原文（2026-11-07，图三）：
     *   「红框里面放上各个子代理的压缩历史……黑框加上主对话的历史压缩记录，
     *     然后显示的使用设置里面的『使用工程模式自带的子代理显示页』判断是否开启」
     *   「那个红框的大小不要动了，然后字最好要自适应大小，可以让用户往下滑动的」
     *
     * 行为：
     *   · 开关关闭（设置页「使用工程模式自带的子代理显示页」= 关）→ 整块不渲染；
     *   · 只读：数据完全来自 /compaction-history（append-only 账本），不在这里改任何东西；
     *   · 两个分区：选中子代理的压缩链（默认）/ 主对话的压缩链，可切换；
     *   · 高度锁死在父容器给的区域内，内部滚动，字号随容器宽度自适应。
     */
    function EngCompactPage(props) {
      var useState = React.useState;
      var useEffect = React.useEffect;
      var useRef = React.useRef;

      // ── 开关：沿用设置页同一个字段（不新增开关，避免两处语义分叉）──
      var settings = useEngSettings();
      var enabled = !!(settings && settings.useOwnSubagentPane);
      // ── 强制视图（右侧面板用它锁成"主对话"）────────────────────────────
      // 「会话视图」（官方 conversation.view 页签）里只该看主对话的压缩链，
      //   而右列 .eng-dtabs 里默认跟随左列选中的子代理。
      //   因此这里允许父组件传 forceTab='main'，且此时隐藏页签切换（不给用户
      //   一个"点了会跳去看子代理"的按钮 —— 那是另一个页面的职责）。
      var forceTab = props && props.forceTab ? String(props.forceTab) : '';

      // ── 视图：选中的子代理 vs 主对话 ──────────────────────────────
      var tabSt = useState('agent');
      var tab = forceTab || tabSt[0];
      // 选中项变化时不强制切 tab：用户正在看主对话链时切房间，不该被打断。
      var dataSt = useState({ ok: true, entries: [], counts: [], err: null });
      var D = dataSt[0];
      var boxRef = useRef(null);
      // ── 展开状态：哪一条记录的摘要正文被展开（null = 全收起）───────────
      // 必须在 hooks 区声明：本组件有多个早退分支（err / 空列表），
      //   把 useState 放在分支后面会让第二次渲染的 hook 数量变化 → React 报错。
      var openSt = useState(null);
      var openAt = openSt[0];
      var setOpenAt = openSt[1];

      var rootId = props && props.rootSessionId ? String(props.rootSessionId) : '';
      var selectedId = props && props.selectedId ? String(props.selectedId) : '';

      // 换选中的子代理 / 换主对话页 → 收起已展开的摘要（内容已经不是同一条了）
      useEffect(function () {
        openSt[1](null);
      }, [selectedId, tab]);

      // 拉数据：挂载即拉；有活跃压缩时 5s 一次；窗口不可见时停（省电）
      useEffect(function () {
        if (!enabled) return undefined;
        var alive = true;
        var timer = null;
        var pull = function () {
          if (!alive) return;
          var url = '/dsh-engineering-ui/compaction-history?limit=400';
          if (rootId) url += '&rootSessionId=' + encodeURIComponent(rootId);
          fetch(url)
            .then(function (r) { return r.json(); })
            .then(function (j) {
              if (!alive) return;
              if (j && j.ok !== false) {
                // ══ 【压缩面板误报修复 · 2026-11-08】══════════════════════════
                // 现场（用户截图）：面板红字「压缩引擎未加载：本会话没有启用
                //   『工程模式压缩器』…」，但磁盘上 compaction_engine.json 明明
                //   写着 {mode:"real"} —— 引擎其实是加载好的。
                //
                // 根因：这里只回填了 entries / counts，把 host 半返回的
                //   engine / threshold / exists 全【丢掉了】。于是空列表分支里
                //   D.engine 恒为 undefined → 永远走"未加载"那一条文案。
                //   用户看到的是"压缩没启动"，而真实原因可能只是"还没到阈值"。
                //
                // 修复：把 engine / threshold / exists / stateDir 原样带进 D，
                //   让空状态能区分「未加载 / 降级 / 只是还没压缩」三种情况。
                dataSt[1]({
                  ok: true,
                  entries: Array.isArray(j.entries) ? j.entries : [],
                  counts: Array.isArray(j.counts) ? j.counts : [],
                  engine: j.engine || null,
                  threshold: j.threshold || null,
                  exists: j.exists !== false,
                  stateDir: j.stateDir || '',
                  err: null
                });
              } else {
                dataSt[1](function (p) {
                  var n = {}; for (var k in p) if (Object.prototype.hasOwnProperty.call(p, k)) n[k] = p[k];
                  n.err = String((j && j.error) || '读取失败'); return n;
                });
              }
            })
            .catch(function (e) {
              if (!alive) return;
              dataSt[1](function (p) {
                var n = {}; for (var k in p) if (Object.prototype.hasOwnProperty.call(p, k)) n[k] = p[k];
                n.err = String((e && e.message) || e); return n;
              });
            });
        };
        pull();
        timer = setInterval(function () {
          try { if (typeof document !== 'undefined' && document.hidden) return; } catch (e) {}
          pull();
        }, 5000);
        return function () { alive = false; if (timer) clearInterval(timer); };
      }, [enabled, rootId]);

      if (!enabled) return null;

      var all = D.entries || [];
      // 主对话链 = target === rootId；子代理链 = 其它（含孙代）
      var mainEntries = all.filter(function (e) { return rootId && String(e.target || '') === rootId; });
      var agentEntries = all.filter(function (e) { return !rootId || String(e.target || '') !== rootId; });
      var shown = tab === 'main' ? mainEntries : agentEntries;
      // ── 【布局 v12】严格跟随左列选中项 ────────────────────────────────
      // 用户要求「左边选择了那个子代理，右边就有那个子代理展示页和对应的
      //   压缩历史」。旧实现是"该子代理没有记录就退回显示全部"——那会让用户
      //   以为别的房间的压缩记在这个房间头上。现在：没有就是没有，显示空态。
      //   仅当左列还没选中任何子代理时才展示全部（概览语义）。
      if (tab === 'agent' && selectedId) {
        shown = shown.filter(function (e) { return String(e.target || '') === selectedId; });
      }

      // ── 【布局 v12】右列整页：顶部一句话摘要 + 可点开的记录行 ─────────────
      // 用户要求「压缩历史那个页面要自适应」：
      //   · 宽度 → 填满 .eng-detail（官方右列 ≈ 1/4 屏），不再挤在 190px 窄列里；
      //   · 字号 → clamp(px, cqw, px) 跟容器宽度缩放（container-type 在 .eng-cp）；
      //   · 高度 → flex:1 + 内部滚动，永远不撑破面板。
      // 摘要统计：一眼看清"这个会话被压缩了几次、共保住多少机械数值"。
      var sumNums = 0, sumRestored = 0, sumSaved = 0;
      for (var si = 0; si < shown.length; si++) {
        sumNums += Number(shown[si].l0_numbers || 0) + Number(shown[si].l0_materials || 0);
        sumRestored += Number(shown[si].l0_restored || 0);
        sumSaved += Math.max(0, Number(shown[si].saved_chars || 0));
      }

      var cap = h('div', { className: 'eng-cp-cap' },
        h('span', { className: 'eng-cp-cap-t' },
          tab === 'main' ? '主对话压缩历史' : (props && props.label ? (String(props.label) + ' · 压缩历史') : '压缩历史')),
        h('span', { className: 'eng-cp-cap-n' }, String(shown.length) + ' 次'),
        forceTab ? null : h('span', { className: 'eng-cp-tabs' },
          h('button', {
            type: 'button',
            className: 'eng-cp-tab' + (tab === 'agent' ? ' on' : ''),
            title: '各子代理的压缩链（默认跟随左列选中的子代理）',
            onClick: function () { tabSt[1]('agent'); },
          }, '子代理'),
          h('button', {
            type: 'button',
            className: 'eng-cp-tab' + (tab === 'main' ? ' on' : ''),
            title: '主对话的压缩记录',
            onClick: function () { tabSt[1]('main'); },
          }, '主对话')));

      var sumBar = (D.err || !shown.length) ? null : h('div', { className: 'eng-cp-sum' },
        h('span', { className: 'eng-cp-sum-i' }, '压缩 ', h('b', null, String(shown.length)), ' 次'),
        h('span', { className: 'eng-cp-sum-i' }, '保住数值 ', h('b', null, String(sumNums)), ' 条'),
        sumRestored > 0
          ? h('span', { className: 'eng-cp-sum-i' }, '自动补回 ', h('b', null, String(sumRestored)), ' 条')
          : null,
        h('span', { className: 'eng-cp-sum-i' }, '省下 ', h('b', null, String(sumSaved)), ' 字符'));

      if (D.err) {
        return h('div', { className: 'eng-cp' }, cap,
          h('div', { className: 'eng-cp-empty' }, '读取失败：' + D.err));
      }
      if (!all.length) {
        // 空态的三种真实原因必须分开说，否则用户永远在猜：
        //   ① engine 为空 → 压缩引擎没加载（preset 那行没解析到 / 包没装）；
        //   ② engine 存在但 mode=fallback → 父类没解析到，压缩被降级成空壳；
        //   ③ engine 正常 → 只是这条会话还没到阈值。
        var eng = D.engine || null;
        var th = D.threshold || null;
        var why;
        // ── 【判定修正 · 2026-11-08】三种空状态必须说清，别再一律报"未加载" ──
        //   现场：磁盘上明明有 compaction_engine.json（mode:"real"），
        //   面板却红字报"未加载"——因为前端把 engine 字段丢了（已修）。
        //   现在把阈值也一并展示，用户能自己判断"是没到阈值"还是"真坏了"。
        if (!eng) {
          why = '压缩引擎未加载：没有读到 compaction_engine.json。'
              + '请确认 preset 的 compaction 子项解析到 dsh-engineering-compaction，'
              + '且该插件已随 DSH 重启加载过（引擎标记在它构造时写）。';
        } else if (eng.mode && eng.mode !== 'real') {
          why = '压缩引擎已加载但处于【降级模式】（' + eng.mode + '）：'
              + '父类 @deepseek-ai/dsh-compaction-basic 未解析到，本轮不会产生压缩记录。';
        } else {
          var _tv = th && typeof th.value === 'number' ? th.value : null;
          why = '引擎已就绪（mode=real），只是【还没有压缩过】：'
              + (_tv ? ('当前阈值 ' + Math.round(_tv / 1000) + 'K token，上下文到阈值才会自动压缩并记账。')
                     : '等上下文使用量到阈值后会自动压缩并记账。');
        }
        return h('div', { className: 'eng-cp' }, cap,
          h('div', { className: 'eng-cp-empty' }, why));
      }
      if (!shown.length) {
        return h('div', { className: 'eng-cp' }, cap,
          h('div', { className: 'eng-cp-empty' },
            tab === 'main' ? '主对话尚未压缩过' : '这个子代理尚未压缩过'));
      }

      // 每条记录渲染成一行（窄容器自动折行）；点行展开摘要正文。
      //   openAt 记的是"哪一条被点开"，同一时刻只展开一条（避免长文本堆叠）。
      var rows = shown.slice(-200).reverse().map(function (e, i) {
        var nums = Number(e.l0_numbers || 0) + Number(e.l0_materials || 0);
        var restored = Number(e.l0_restored || 0);
        var at = Number(e.at) || 0;
        var hhmm = '';
        try {
          var d = new Date(at);
          hhmm = String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
        } catch (err) {}
        var label = String(e.label || '').trim() || String(e.target || '').slice(0, 8);
        var open = String(openAt) === String(at);
        var excerpt = String(e.summary_excerpt || '').trim();
        var rowCls = 'eng-cp-row' + (excerpt ? ' clickable' : '');
        var children = [
          h('span', { className: 'eng-cp-time', key: 't' }, hhmm),
          h('span', { className: 'eng-cp-name', key: 'n' }, label),
          h('span', { className: 'eng-cp-num', key: 'l', title: '本次保住的机械数值条数' }, 'L0 ' + nums),
          restored > 0
            ? h('span', { className: 'eng-cp-fix', key: 'f', title: '模型漏写、已自动补回 ' + restored + ' 条' }, '+' + restored)
            : null,
          h('span', { className: 'eng-cp-save', key: 's', title: '字符数 ' + (e.input_chars || 0) + ' → ' + (e.output_chars || 0) },
            '-' + Math.max(0, Number(e.saved_chars || 0)))
        ];
        if (open && excerpt) children.push(h('div', { className: 'eng-cp-body', key: 'b' }, excerpt));
        return h('div', {
          className: rowCls,
          key: 'cp' + i + ':' + at,
          title: excerpt ? '点击展开/收起本次压缩摘要' : '',
          onClick: excerpt
            ? function (ev) {
                try { ev.stopPropagation(); } catch (err) {}
                setOpenAt(function (prev) { return String(prev) === String(at) ? null : at; });
              }
            : undefined,
        }, children);
      });

      return h('div', { className: 'eng-cp' }, cap, sumBar,
        h('div', { className: 'eng-cp-list', ref: boxRef }, rows));
    }

    /* ══════════════════════════════════════════════════════════════════
     * 【会话视图】EngSessionViewPage — 官方 conversation.view 页签的正文
     * ══════════════════════════════════════════════════════════════════
     * 用户要求（2026-11-07，图二黑框）：
     *   「在黑框那里，加一个文字，官方叫"会话视图"，在会话视图中，加上主对话的
     *     压缩历史，先这样子加吧」
     *
     * 为什么用官方 conversation.view，而不是自己在对话区上方塞一排假页签：
     *   官方把「聊天 / 轨迹」那排页签声明为 conversation.view（list, session
     *   作用域；Chat 是 order=0 的 chat，Trajectory 是 order=10 的 trajectory）。
     *   通过 ctx.slots.inject('conversation.view', …) 注册同款条目，本页就会
     *   作为一个【真正的会话视图页签】出现在那排里，并且官方负责：
     *     · 页签条显隐（≥2 个视图才显示）、宽度自适应、折叠进"更多会话视图"；
     *     · 每个会话的选中状态与偏好持久化；
     *     · 渲染容器（填满对话区）。
     *   自己造页签则要复刻以上全部行为，且换 DSH 版本就会错位。
     *
     * 数据来源：与右列压缩历史同一个只读账本 /compaction-history
     *   （dsh-engineering-compaction 写的 compaction_history.jsonl）。
     *   rootSessionId 由页签注入的 sessionId 提供 —— 那就是"当前这条主对话"。
     */
    function EngSessionViewPage(props) {
      var useState = React.useState;
      var useEffect = React.useEffect;
      // 开关：关掉工程模式自带显示页 → 交回官方（返回 null，页签照常存在但空白，
      //   因此这里用 canOpen 之外的第二道闸：直接渲染官方风格的提示，不装死）。
      var settings = useEngSettings();
      var enabled = !!(settings && settings.useOwnSubagentPane);
      // 会话 id：conversation.view 的 inject(sessionId) 会把它作为 prop 注入；
      //   取不到时退回 DOM/列表兜底（engCurrentSessionId）。
      var sid = (props && props.sessionId) ? String(props.sessionId) : '';
      if (!sid) {
        try { sid = engCurrentSessionId(null) || ''; } catch (e) { sid = ''; }
      }
      if (!enabled) {
        return h('div', { className: 'eng-sv' },
          h('div', { className: 'eng-sv-empty' },
            '「工程模式自带显示页」已在设置里关闭 —— '
            + '会话视图的主对话压缩记录一并关闭。'));
      }
      // 页签已经叫「工程模式」，页内标题不再重复一次"会话视图"（用户要求改名）。
      return h('div', { className: 'eng-sv' },
        h('div', { className: 'eng-sv-head' },
          h('span', { className: 'eng-sv-title' }, '工程模式'),
          h('span', { className: 'eng-sv-sub' }, '主对话的压缩历史（L0 机械数值逐次保住）')),
        h('div', { className: 'eng-sv-body' },
          h(EngCompactPage, {
            rootSessionId: sid,
            selectedId: '',
            forceTab: 'main',
            label: '主对话',
          })));
    }


    // ══ 【问题4·开启侧】官方右列子代理 tab 的正文（本插件自有实现）═════════
    // 用户要求："所有官方的子代理在右侧显示的都指向我们现在搞的"。
    // 本组件就是"指向的去处"：当工程模式 + 开关开启时，官方右列的
    //   subagentchat tab 由本组件渲染（接管见 apply()）。
    //
    // 【不使用官方组件】这里完全自绘：直接复用本插件自己的
    //   SubagentConsole（三栏工作区本体），因此观感与插件面板一致，
    //   官方只是提供了"容器/入口"。
    //
    // 为什么直接渲染 SubagentConsole 而不写一套新 UI：
    //   · 用户的诉求就是"官方入口 → 指向我们的界面"，用同一个组件最忠实；
    //   · SubagentConsole 自己会算当前会话（mainView）、读子代理目录、
    //     拉日志、按 preset 门禁自渲染，语义完全适用。
    // 传给它的 props：useSessions 由渲染器按 session 作用域注入（该 seat 是
    //   scope:'session'，所以这里能拿到 useSessions 标准 prop）。
    function EngSubagentTabBody(props) {
      // 开关关闭 / 非工程模式 → 不接管，交回官方（配合 canOpen 双保险）
      if (!_engSettings.useOwnSubagentPane || !_isEngineeringContext()) return null;
      return h(SubagentConsole, Object.assign({}, props, { engEmbedded: true }));
    }

    // ══ 设置页「工程模式」分区：主栏目 + 子项行 ═══════════════════════════
    //
    // 契约来源：@deepseek-ai/dsh-client-ui-settings 的 slots 声明
    //   'settings.section': { kind: 'list', scope: 'root',
    //                          owner: SettingsSectionOwnerProps }
    //   SettingsSectionOwnerProps = { close: () => void }
    //
    // ── 【版式 v2 · 用户指定】────────────────────────────────────────────
    // 版式（对照用户给的示意图）：
    //   · 主栏目 = 大标题 + 一张圆角卡片；
    //   · 卡片内每个子项 = 一行「左侧名称 + 右侧箭头 ›」，行间一条细分隔线；
    //   · 三个主栏目依次为：主栏目1 / 主栏目2 / 美化工程模式。
    //
    // 硬性约束（用户明确要求）：
    //   · 「使用工程模式自带的子代理显示页」开关【必须保留】，且行为与
    //     持久化逻辑【完全不变】（仍写 localStorage 的 useOwnSubagentPane）；
    //   · 它固定放在【美化工程模式】的【第 1 行】。
    //
    // 其余行暂用示意图里的占位文案「子栏目选项设置N」，后续按需替换 ——
    //   占位行只渲染视觉，不带点击行为（不可点，避免"点了没反应"的错觉）。
    // ══════════════════════════════════════════════════════════════════════
    // 【需求1】工程模式「模式说明 / 如何使用」弹窗
    // ──────────────────────────────────────────────────────────────────────
    // 为什么在插件里自建，而不是像内置模式那样由宿主渲染：
    //   宿主 @deepseek-ai/dsh-client-ui-agent-preset 的说明弹窗是【硬编码
    //   白名单】—— guides Map 只含 standard / ptc / minimal / cordis 四项，
    //   且 presetGuide() 只在 trust === "system"（内置分组）时才查表；
    //   工程模式自己发布了 name → 属"自定义"分组 → 直接返回 undefined →
    //   两个按钮根本不渲染。宿主 schema 也没有 guide/docs 一类字段可填。
    //   因此只能在插件内自建同款弹窗（外观与官方保持一致）。
    // 结构照抄官方：
    //   Tab1「模式说明」= "### 工作方式" + 一段；"### 什么时候选" + 一段
    //   Tab2「如何使用」= 若干 "### 标题" 段，每段含 "> 提示词" 与"预期产出："
    // ══════════════════════════════════════════════════════════════════════
    var ENG_GUIDE = {
      name: '工程模式',
      intro: '新建任务时选择「工程模式」，说明要设计什么零件或装配体，'
           + '并给出关键尺寸、材料与工况；不确定的参数会被逐一确认。',
      explanation: [
        '### 工作方式',
        'Agent 通过 Python win32com 直接驱动 SolidWorks 建模、用 AutoCAD 出图，'
        + '并在本地做有限元与疲劳校核（纯 Python + numpy，无需外部 FEA 软件）。'
        + '整个流程围绕「完成工图」推进：分析 → 设计 → 验证，'
        + '由 Python 门禁脚本在关键节点强制校验，不合格不放行。'
        + '大型装配体会拆成若干"房间"，由子 Agent 串行或并行建模，'
        + '再统一总装与出图。',
        '### 什么时候选',
        '需要真正产出三维模型与工程图（.sldprt / .slddrw / DWG / DXF）时选它；'
        + '需要三维建模、二维制图或强度校核时选它。'
        + '如果只是写代码、处理文件或整理资料，用「标准模式」即可。'
      ].join('\n\n'),
      usage: [
        '### 画一个零件并出图',
        '> 帮我画一个长 11mm 的正方形，材料 Q235，然后转成 CAD 图纸。',
        '预期产出：DSH_正方形.sldprt 零件文件、三视图工程图（.slddrw）、'
        + '可直接用 AutoCAD 打开的 DWG，以及一张截图供验收。',
        '### 设计一个多零件机构',
        '> 帮我设计一个三自由度机械臂，负载 5kg，臂展 400mm。',
        '预期产出：拆分为结构件 / 传动机构 / 壳体机架等房间，逐房间建模并做'
        + '干涉检查与强度校核，最后总装并输出全套工程图与校核报告。',
        '### 校核一个已有设计的强度',
        '> 校核这个大臂的强度，材料 6061-T6，末端载荷 10kg。',
        '预期产出：静力 FEA 结果（应力 / 位移 / 安全系数）、疲劳寿命校核，'
        + '以及是否满足目标安全系数的判定结论。'
      ].join('\n\n'),
      modeExplanation: '模式说明',
      howToUse: '如何使用',
      guideExampleTask: '示例任务'
    };

    /** 把一段 Markdown 子集渲染成 React 节点（标题 / 引用 / 正文）。

    只支持本插件实际用到的三种形态（与官方 guide 文案结构一致）：
      · "### X"   → 小标题
      · "> X"     → 提示词气泡（示例任务的输入）
      · 其余       → 普通段落
    不引入 Markdown 依赖：插件只 require('react')，保持零新增依赖。
    */
    function engRenderMd(md, keyPrefix) {
      var blocks = String(md || '').split(/\n\n+/);
      var out = [];
      for (var i = 0; i < blocks.length; i++) {
        var b = blocks[i].trim();
        if (!b) continue;
        var k = keyPrefix + '-' + i;
        if (b.indexOf('### ') === 0) {
          out.push(h('div', { className: 'eng-guide-h', key: k },
            b.slice(4).trim()));
        } else if (b.charAt(0) === '>') {
          out.push(h('div', { className: 'eng-guide-quote', key: k },
            b.replace(/^>\s?/gm, '').trim()));
        } else {
          out.push(h('div', { className: 'eng-guide-p', key: k }, b));
        }
      }
      return out;
    }

    /** 「如何使用」页：按 "### 标题" 切段，每段 = 标题 + 示例任务标签 + 正文。 */
    function engRenderUsage(md) {
      var parts = String(md || '').split(/^### /m).filter(function (s) {
        return s.trim();
      });
      var out = [];
      for (var i = 0; i < parts.length; i++) {
        var seg = parts[i];
        var nl = seg.indexOf('\n');
        var title = (nl >= 0 ? seg.slice(0, nl) : seg).trim();
        var body = (nl >= 0 ? seg.slice(nl + 1) : '').trim();
        out.push(h('div', { className: 'eng-guide-sec', key: 'u-' + i },
          h('div', { className: 'eng-guide-h' }, title),
          h('div', { className: 'eng-guide-tag' }, ENG_GUIDE.guideExampleTask),
          engRenderMd(body, 'ub-' + i)));
      }
      return out;
    }

    /** 【需求1】模式说明 / 如何使用弹窗（两 Tab，外观照抄官方）。 */
    function EngGuideDialog(props) {
      var _st = useState(props.initialPage || 'explanation');
      var page = _st[0], setPage = _st[1];
      var onClose = props.onClose;
      // Esc 关闭（与宿主弹窗一致的键盘习惯）
      useEffect(function () {
        function onKey(e) {
          if (e && e.key === 'Escape' && typeof onClose === 'function') onClose();
        }
        try { document.addEventListener('keydown', onKey); } catch (e) {}
        return function () {
          try { document.removeEventListener('keydown', onKey); } catch (e) {}
        };
      }, [onClose]);
      return h('div', {
        className: 'eng-guide-mask',
        onClick: function (e) {
          // 点遮罩关闭；点弹窗内部不关
          if (e && e.target === e.currentTarget && typeof onClose === 'function') {
            onClose();
          }
        }
      },
        h('div', { className: 'eng-guide-dlg', role: 'dialog', 'aria-modal': 'true' },
          h('div', { className: 'eng-guide-hd' },
            h('div', null,
              h('div', { className: 'eng-guide-title' }, ENG_GUIDE.name),
              h('div', { className: 'eng-guide-intro' }, ENG_GUIDE.intro)),
            h('button', {
              className: 'eng-guide-x', type: 'button',
              'aria-label': '关闭', onClick: onClose
            }, '×')),
          h('div', { className: 'eng-guide-tabs' },
            h('button', {
              className: 'eng-guide-tab' + (page === 'explanation' ? ' on' : ''),
              type: 'button',
              onClick: function () { setPage('explanation'); }
            }, ENG_GUIDE.modeExplanation),
            h('button', {
              className: 'eng-guide-tab' + (page === 'usage' ? ' on' : ''),
              type: 'button',
              onClick: function () { setPage('usage'); }
            }, ENG_GUIDE.howToUse)),
          h('div', { className: 'eng-guide-body' },
            page === 'usage'
              ? engRenderUsage(ENG_GUIDE.usage)
              : engRenderMd(ENG_GUIDE.explanation, 'ex'))));
    }

    /** 一个主栏目：大标题 + 卡片容器。
     *
     * props.embedded=true 时【不画大标题、不画卡片壳】，只把 children 原样
     *   返回 —— 供「设计过程优化」里的子栏目（模型自动接替 / 内存与并发）
     *   复用同一套内部版式，又不会出现"卡片套卡片 + 重复标题"。
     */
    function EngGroup(props) {
      if (props && props.embedded) {
        return h('div', { className: 'eng-grp-embedded' }, props.children);
      }
      return h('div', { className: 'eng-grp' },
        h('div', { className: 'eng-grp-title' }, props.title),
        h('div', { className: 'eng-grp-card' }, props.children));
    }

    /** 一个子项行：左侧名称 + 右侧箭头。未传 onClick 时是不可点的纯展示行。 */
    function EngRow(props) {
      var clickable = typeof props.onClick === 'function';
      return h('div', {
        className: 'eng-row' + (clickable ? ' clickable' : ''),
        role: clickable ? 'button' : undefined,
        tabIndex: clickable ? 0 : undefined,
        title: props.title || props.label,
        onClick: clickable ? props.onClick : undefined
      },
        h('div', { className: 'eng-row-txt' },
          h('div', { className: 'eng-row-label' }, props.label),
          props.hint ? h('div', { className: 'eng-row-hint' }, props.hint) : null),
        h('span', { className: 'eng-row-chev' }, '\u203a'));
    }

    /** 连接状态徽标（展示）。
     *
     * state: 'on'（已连接）/ 'off'（未连接）/ 'idle'（未检测）/ 'busy'（探测中）
     */
    function EngStatusChip(props) {
      var st = props.state || 'idle';
      var _MAP = {
        on:   { cls: 'on',   text: '已连接' },
        off:  { cls: 'off',  text: '未连接' },
        idle: { cls: 'idle', text: '未检测' },
        busy: { cls: 'idle', text: '探测中…' },
      };
      var it = _MAP[st] || _MAP.idle;
      return h('span', {
        className: 'eng-chip ' + it.cls,
        title: props.title || it.text
      },
        h('span', { className: 'eng-chip-dot' }),
        props.label || it.text);
    }

    /** 【连接区】SW / CAD 连接状态的共享读取（模块级，供 UI 多处复用）。
     *
     * ── 为什么状态放【JSON 文件】而不是 localStorage ────────────────────
     *   用户需求："在流程中搞一个代码判断：如果设置那边 SW 连接成功了，
     *   AI 就直接启动 SW 开始建模；没连接就走原来的启动流程。"
     *   那个"代码判断"在 Python/Agent 侧执行，而 localStorage 是浏览器私有，
     *   AI【完全读不到】。所以状态必须落在双方都能读的文件里：
     *     <tools>/connection_state.json   （由 conn_state.py 维护）
     *   本函数只【读】该文件（经宿主 /conn-state 端点），零副作用。
     */
    function EngWebotsSnapshot(props) {
      var s = props.snapshot;
      if (!s) return h('p', null, '仿真未执行 / 状态 unknown；安装与进程检测不能证明仿真就绪。');
      return h('div', { role: 'status' },
        h('p', null, '来源：', String(s.provenance || 'unknown'), '；核心：',
          s.core_ready === true ? '宿主报告可用（仍需核验握手与身份）' : '未实现 / 未确认',
          '；执行：', String(s.execution_state || 'unknown'), '；作业原始状态：', String(s.job_state || 'UNKNOWN')),
        h('p', null, '快照：', String(s.snapshot_at || 'unknown'), '；stale：', s.stale === false ? 'false' : 'true / unknown'),
        h('p', null, 'unsupported：', s.unsupported === undefined ? 'unknown' : JSON.stringify(s.unsupported)),
        s.execution_state === 'unknown' ? h('p', { role: 'alert' }, '结果 unknown：动作可能已经执行，禁止盲重发。') : null);
    }

    function EngWebotsReport(props) {
      var report = props.report;
      if (!report) return h('p', null, '尚无已读取报告（未执行 / unknown）。');
      var evidence = Array.isArray(report.evidence) ? report.evidence : [];
      var last = Math.max(0, Math.ceil(evidence.length / 20) - 1);
      var page = Math.min(last, Math.max(0, props.page || 0));
      var origin = typeof report.provenance === 'object' ? report.provenance.kind : report.provenance;
      var preStyle = { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: '260px', overflow: 'auto' };
      return h('div', { 'aria-label': 'Webots报告' },
        h('p', null, String(report.report_type), ' · ', String(report.run_id), ' · 来源 ', String(origin || 'unknown'),
          ' · 支持状态 ', String(report.status || 'unknown')),
        h('p', null, '源版本：', String(report.source_revision || 'unknown'), '；生成时间：', String(report.created_at || 'unknown')),
        h('p', null, '支持状态与原始判定不等于物理验收通过；fake / synthetic / fixture 仅为夹具证据。'),
        h('p', null, '缺失信息：', JSON.stringify(report.missing_information || [])),
        h('details', null, h('summary', null, '候选零件与证据等级（不代表根因）、原始报告数据'),
          h('pre', { style: preStyle }, JSON.stringify(report.data, null, 2))),
        h('p', null, '证据 ', evidence.length ? (page * 20 + 1) + '–' + Math.min((page + 1) * 20, evidence.length) : '0', ' / ', evidence.length),
        evidence.slice(page * 20, (page + 1) * 20).map(function (item, index) {
          return h('pre', { key: page * 20 + index, 'data-webots-evidence': true, style: preStyle }, JSON.stringify(item, null, 2));
        }),
        h('button', { type: 'button', disabled: page === 0, onClick: function () { props.onPage(page - 1); } }, '上一页证据'),
        h('button', { type: 'button', disabled: page === last, onClick: function () { props.onPage(page + 1); } }, '下一页证据'));
    }

    function currentWebotsSession() {
      var sessions = engService('sessions');
      var list = sessions && sessions.list;
      var snapshot = list && typeof list.getSnapshot === 'function' ? list.getSnapshot() : null;
      var rows = snapshot && snapshot.byId || {};
      var ids = Object.keys(rows).filter(function (id) {
        return rows[id] && rows[id].retainedBy && rows[id].retainedBy.mainView > 0;
      });
      return ids.length === 1 ? ids[0] : '';
    }

    function EngWebotsPanel(props) {
      var sessionId = props.sessionId || currentWebotsSession();
      var pair = useState({ sessionId: '', context: null, run: '', type: 'experiment_report', report: null,
        page: 0, log: null, busy: false, error: null });
      var s = pair[0], set = pair[1];
      var request = useRef(0);
      var tick = useState(0), rerender = tick[1];
      function patch(values) { set(function (prev) { return Object.assign({}, prev, values); }); }
      useEffect(function () {
        var service = engService('sessions');
        var list = service && service.list;
        return list && typeof list.subscribe === 'function' ? list.subscribe(function () { rerender(function (n) { return n + 1; }); }) : undefined;
      }, []);
      useEffect(function () {
        request.current++;
        patch({ sessionId: sessionId, context: null, report: null, log: null, page: 0, error: null, busy: false });
        return function () { request.current++; };
      }, [sessionId]);
      var owned = s.sessionId === sessionId && s.context;
      async function read(kind, next) {
        var token = ++request.current;
        var query = 'sessionId=' + encodeURIComponent(sessionId);
        if (kind !== 'context') query += '&run_id=' + encodeURIComponent(s.run);
        if (kind === 'report') query += '&report_type=' + encodeURIComponent(s.type);
        if (kind === 'evidence') {
          query += '&offset=' + (next && s.log ? s.log.next_offset : 0) + '&limit=16384';
          if (next && s.log) query += '&expected_sha256=' + encodeURIComponent(s.log.sha256);
        }
        patch({ busy: true, error: null });
        try {
          var response = await fetch('/dsh-engineering-ui/webots-' + kind + '?' + query);
          var result = await response.json();
          if (token !== request.current) return;
          if (!response.ok || !result || result.ok !== true) throw new Error(
            String(result && result.error_code || 'READ_FAILED') + ': ' + String(result && (result.error || result.hint) || '读取失败'));
          if (kind === 'context') patch({ sessionId: sessionId, context: result, report: null, log: null, page: 0 });
          else if (kind === 'report') patch({ report: result.data, page: 0 });
          else patch({ log: result.data });
        } catch (error) {
          if (token !== request.current) return;
          patch({ error: String(error.message || error) + '；日志或证据变更时请从头读取。', report: null, log: null,
            context: kind === 'context' || response && response.status === 403 || result && result.error_code === 'CONTEXT_UNAVAILABLE' ? null : s.context });
        } finally { if (token === request.current) patch({ busy: false }); }
      }
      return h('details', { className: 'eng-conn-log' },
        h('summary', null, 'Webots 仿真报告与证据（只读）'),
        h('p', null, '当前会话：', sessionId || 'unknown', '；须在绑定活跃房间的工程会话中读取。'),
        h('button', { type: 'button', disabled: !sessionId || s.busy, onClick: function () { return read('context'); } }, '读取仿真上下文'),
        h(EngWebotsSnapshot, { snapshot: owned || null }),
        owned ? h('p', null, '任务：', owned.task_id, '；房间：', owned.room_label || owned.room_id) : h('p', null, '未授权 / 未读取上下文；不会按输入猜测任务或房间。'),
        h('label', null, '运行ID ', h('input', { 'aria-label': '运行ID', value: s.run, maxLength: 96,
          onChange: function (event) { request.current++; patch({ run: event.target.value, report: null, log: null, page: 0, busy: false, error: null }); } })),
        h('label', null, '报告类型 ', h('select', { 'aria-label': '报告类型', value: s.type,
          onChange: function (event) { request.current++; patch({ type: event.target.value, report: null, page: 0, busy: false, error: null }); } },
          ['model_manifest', 'conversion_report', 'experiment_report'].map(function (type) { return h('option', { key: type, value: type }, type); }))),
        h('button', { type: 'button', disabled: !owned || !s.run || s.busy, onClick: function () { return read('report'); } }, '读取报告'),
        s.error ? h('p', { role: 'alert' }, s.error) : null,
        h(EngWebotsReport, { report: owned ? s.report : null, page: s.page, onPage: function (page) { patch({ page: page }); } }),
        h('p', null, 'worker 日志（UTF-8 字节分页，每页最多 16 KiB）'),
        h('button', { type: 'button', disabled: !owned || !s.run || s.busy, onClick: function () { return read('evidence', false); } }, '从头读取日志'),
        h('button', { type: 'button', disabled: !owned || !s.log || s.log.eof || s.busy, onClick: function () { return read('evidence', true); } }, '下一页日志'),
        owned && s.log ? h('div', null,
          h('p', { style: { overflowWrap: 'anywhere' } }, s.log.offset, '–', s.log.next_offset, ' / ', s.log.size_bytes, ' bytes；SHA-256 ', s.log.sha256),
          h('pre', { style: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: '260px', overflow: 'auto' } }, s.log.text)) : null);
    }

    function fetchConnState(cb) {
      try {
        fetch('/dsh-engineering-ui/conn-state')
          .then(function (r) { return r.json(); })
          .then(function (j) { cb(j || null); })
          .catch(function () { cb(null); });
      } catch (e) { cb(null); }
    }

    /** 触发一次真实探测（POST，会让 Python 去连 SW/CAD）。 */
    function probeConn(target, cb) {
      try {
        fetch('/dsh-engineering-ui/conn-probe', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ target: target })
        })
          .then(function (r) { return r.json(); })
          .then(function (j) { cb(j || null); })
          .catch(function (e) { cb({ ok: false, error: String(e && e.message || e) }); });
      } catch (e) { cb({ ok: false, error: String(e && e.message || e) }); }
    }

    /** 【10 分钟自动刷新】只做【进程层】检测（POST，绝不发 COM）。
     *
     * ── 用户诉求原文 ──────────────────────────────────────────────────
     *   "10 分钟自动探测就是看是不是在运行的，没有的话那个 UI 就熄灭，
     *    让用户到时候自己选择去设置连接啥的，就是只做检测就行了，
     *    关了 UI 就更新就行了，其他不用搞。"
     *
     * ── 为什么不能复用 probeConn ──────────────────────────────────────
     *   probeConn → /conn-probe 会发 COM Dispatch：SW 没开时可能把它
     *   【拉起来】，还会把窗口置前/最大化 —— 每 10 分钟打断用户一次。
     *   本函数走 /conn-probe-process-only（纯 tasklist），
     *   进程没了就把状态置为未连接（灯熄灭），用户想重连时自己点「连接」。
     */
    function probeConnProcessOnly(cb) {
      try {
        fetch('/dsh-engineering-ui/conn-probe-process-only', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({})
        })
          .then(function (r) { return r.json(); })
          .then(function (j) { cb(j || null); })
          .catch(function (e) { cb({ ok: false, error: String(e && e.message || e) }); });
      } catch (e) { cb({ ok: false, error: String(e && e.message || e) }); }
    }

    /** 【启动按钮】显式启动 SW / CAD（用户主动点击才走这里）。 */
    function launchConn(target, cb) {
      try {
        fetch('/dsh-engineering-ui/conn-launch', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ target: target })
        })
          .then(function (r) { return r.json(); })
          .then(function (j) { cb(j || null); })
          .catch(function (e) { cb({ ok: false, error: String(e && e.message || e) }); });
      } catch (e) { cb({ ok: false, error: String(e && e.message || e) }); }
    }

    /** 【发给模型】把启动日志作为一条用户消息投递给当前会话的模型。 */
    function sendLogToModel(target, logText, sessionId, cb) {
      try {
        fetch('/dsh-engineering-ui/conn-diagnose', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({
            target: target, log_text: logText, sessionId: sessionId
          })
        })
          .then(function (r) { return r.json(); })
          .then(function (j) { cb(j || null); })
          .catch(function (e) { cb({ ok: false, error: String(e && e.message || e) }); });
      } catch (e) { cb({ ok: false, error: String(e && e.message || e) }); }
    }

    /** 连接行：名称 + 状态徽标 + 「连接」/「启动」按钮 + 错误日志 + 「发给模型」。
     *
     * 用户确定的行为：
     *   · 「连接」→ 只探测（安全，不启动软件）；
     *   · 「启动」→ 真的把软件拉起来（用户主动点击才走），
     *      启动后自动隐藏欢迎页并最大化主窗口（"不需要 SW 自己跳出来"）；
     *   · 启动失败 → 行内贴出错误日志；
     *   · 「发给模型」→ 把日志作为用户消息投给当前会话，让 DSH 判断根因。
     *
     * 轮询：进入设置页读一次，之后每 15s 刷新（外部改了状态也能反映）。
     */
    function EngConnRow(props) {
      // target: 'sw' | 'cad'（真实连接）；'abaqus' | 'nx' 当前为占位
      var target = props.target || 'sw';
      // ── 【占位模式】外观与真实行完全一致，但不触发任何探测/启动 ──────────
      // 现状（【NX → Webots 改造】后）：
      //   · Abaqus —— 用户明确说"另外一个 A 开头的先不管它了"，保持占位；
      //   · Webots —— 用户要求"做实际连接"，已从占位【升级为真实目标】
      //     （有后端 target，能探测、能启动、能诊断）。
      // 占位行仅把 onClick 换成"未接入"的说明，避免误触发。
      var isPlaceholder = props.placeholder === true || target === 'abaqus';
      // 各目标的显示名（用于默认 label 与状态文案，避免"SolidWorks/CAD"二选一写死）
      var TARGET_DISPLAY = {
        sw: { label: 'SOLIDWORKS连接', name: 'SolidWorks' },
        cad: { label: 'AutoCAD 连接', name: 'AutoCAD' },
        abaqus: { label: 'Abaqus连接', name: 'Abaqus' },
        webots: { label: 'Webots连接', name: 'Webots' },
        // 旧名兼容：旧配置里若还写着 nx，按 Webots 渲染（Python 侧同样映射）
        nx: { label: 'Webots连接', name: 'Webots' }
      };
      // 归一化：旧 target 'nx' → 'webots'（与后端 _norm_target 一致）
      var reqTarget = (target === 'nx') ? 'webots' : target;
      var _td = TARGET_DISPLAY[target] || TARGET_DISPLAY.sw;
      var label = props.label || _td.label;
      var sessionId = props.sessionId || '';
      var _st0 = useState({
        phase: 'idle', data: null, note: null,
        logText: null, logOpen: false, sent: null
      });
      var S = _st0[0], setS = _st0[1];
      var mounted = useRef(true);

      function patch(o) {
        if (!mounted.current) return;
        setS(function (prev) {
          var n = {}; for (var k in prev) n[k] = prev[k];
          for (var k2 in o) n[k2] = o[k2];
          return n;
        });
      }

      function apply(state) {
        if (!mounted.current) return;
        // 占位项不走状态机（没有后端 target），保持"预留"文案
        if (isPlaceholder) {
          patch({
            phase: 'idle',
            data: { state: 'idle', title: _td.name + ' 连接（预留，暂未接入）' },
            note: '预留项：按钮与 SOLIDWORKS / AutoCAD 一致，'
                  + '连接能力尚未接入（后续版本开放）。'
          });
          return;
        }
        var entry = state && state[reqTarget] ? state[reqTarget] : null;
        var connected = !!(entry && entry.connected);
        var running = entry ? entry.running : null;
        var n = null;
        if (entry && entry.error) {
          n = String(entry.error);
        } else if (connected) {
          n = '已连接' + (entry && entry.revision ? '（' + entry.revision + '）' : '')
              + (entry && entry.pid ? ' PID ' + entry.pid : '');
        } else if (running === false) {
          n = _td.name + ' 未运行 —— 点「启动」自动拉起';
        } else if (running === true) {
          n = '进程在运行，但 COM 未连上';
        } else if (entry && entry.checked_at) {
          n = '未连接（检测于 ' + entry.checked_at + '）';
        } else {
          n = '尚未检测过 —— 点「连接」探测，或点「启动」直接拉起';
        }
        // Abaqus 无 COM：把"未连上 COM"的措辞换成符合其能力边界的说明
        if (target === 'abaqus' && running === true && !connected) {
          n = 'Abaqus 进程在运行（无 COM 接口，集成走 `abaqus python` 脚本）';
        }
        // ── 【NX → Webots 改造】Webots 也没有 COM，措辞必须如实 ──────────
        //   connected 的语义是"进程在跑 或 安装可验证"，不是"COM 会话已建立"；
        //   若沿用 SW/CAD 的"COM 未连上"文案会误导用户以为连接失败。
        if (reqTarget === 'webots') {
          if (connected) {
            n = '安装/进程可用；仿真未确认（需真实控制器握手与机器人身份）';
          } else if (running === true) {
            n = 'Webots 进程在运行（无 COM 接口，外部控制走 webots-controller）';
          } else if (entry && entry.checked_at) {
            n = '未检测到 Webots 进程或安装（检测于 ' + entry.checked_at + '）';
          }
        }
        // 自动检测写入的说明（例如"进程已重启，请点连接重建会话"）优先透出
        if (entry && entry.auto_note && !connected) {
          n = String(entry.auto_note);
        }
        if (state && state.stale && connected) {
          n = (n || '') + '（状态可能已过期）';
        }
        patch({
          phase: 'idle',
          data: {
            state: connected ? 'on' : (entry && entry.checked_at ? 'off' : 'idle'),
            title: n
          },
          note: n
        });
      }

      function refresh() {
        // ── 【占位模式】不发任何请求：占位项没有后端 target，
        //   轮询只会拿到 undefined 并显示误导性的"无法读取连接状态"。
        if (isPlaceholder) {
          patch({
            phase: 'idle',
            data: { state: 'idle', title: _td.name + ' 连接（预留，暂未接入）' },
            note: '预留项：按钮与 SOLIDWORKS / AutoCAD 一致，'
                  + '连接能力尚未接入（后续版本开放）。'
          });
          return;
        }
        fetchConnState(function (s) {
          if (!mounted.current) return;
          if (!s) { patch({ phase: 'idle', data: null, note: '无法读取连接状态（宿主端点不可用）' }); return; }
          apply(s);
        });
      }

      useEffect(function () {
        mounted.current = true;
        refresh();
        // ── 【10 分钟自动刷新】只检测"进程在不在"，不发 COM、不弹窗 ────────
        // 用户诉求原文："10 分钟自动探测就是看是不是在运行的，没有的话那个
        //   UI 就熄灭，让用户到时候自己选择去设置连接啥的，就是只做检测就行
        //   了，关了 UI 就更新就行了，其他不用搞。"
        //
        // 两条定时器，职责严格分开：
        //   ① 15s / 读文件（refresh）—— 纯读 connection_state.json，零副作用，
        //      让"点了连接/启动"的结果尽快反映到所有行；
        //   ② 10min / 重探进程（probeConnProcessOnly）—— 真正去查进程是否还在，
        //      进程没了或换了实例就把状态置为未连接，灯随之熄灭。
        //      刻意【不】用 /conn-probe（那会发 COM，可能把软件拉起来）。
        var tFile = isPlaceholder ? null : setInterval(refresh, 15000);
        var tProc = isPlaceholder ? null : setInterval(function () {
          probeConnProcessOnly(function (r) {
            if (!mounted.current) return;
            // 检测失败不打扰用户（自动检测是后台行为，不该弹错误）
            if (r && r.ok && r.state) apply(r.state);
          });
        }, 10 * 60 * 1000);
        return function () {
          mounted.current = false;
          if (tFile) clearInterval(tFile);
          if (tProc) clearInterval(tProc);
        };
      }, [target]);

      var busy = (S.phase === 'busy');
      var busyLaunch = (S.phase === 'launching');

      return h('div', { className: 'eng-conn-block' },
        h('div', { className: 'eng-row conn' },
          h('div', { className: 'eng-row-txt' },
            h('div', { className: 'eng-row-label' }, label),
            S.note ? h('div', { className: 'eng-row-hint' }, S.note) : null),
          h(EngStatusChip, {
            state: (busy || busyLaunch) ? 'busy' : ((S.data && S.data.state) || 'idle'),
            label: reqTarget === 'webots' && !busy && !busyLaunch && S.data && S.data.state === 'on' ? '安装/进程可用' : undefined,
            title: (S.data && S.data.title) || '连接状态（读取 connection_state.json）',
          }),
          h('button', {
            className: 'eng-conn-btn',
            type: 'button',
            disabled: busy || busyLaunch,
            'aria-label': '连接' + label,
            title: isPlaceholder
              ? (_td.name + ' 连接为预留项，暂未接入实际探测') : undefined,
            onClick: function () {
              if (busy || busyLaunch) return;
              // 占位项：只更新说明文字，绝不发探测请求
              if (isPlaceholder) {
                patch({
                  phase: 'idle',
                  data: { state: 'idle', title: _td.name + ' 连接（预留）' },
                  note: _td.name + ' 连接为预留项，暂未接入实际探测；'
                        + '需要时可先用其命令行完成分析。'
                });
                return;
              }
              patch({ phase: 'busy', note: '正在探测…', sent: null });
              probeConn(reqTarget, function (r) {
                if (!mounted.current) return;
                if (r && r.ok && r.state) {
                  apply(r.state);
                } else {
                  patch({
                    phase: 'idle',
                    data: { state: 'off', title: (r && r.error) || '探测失败' },
                    note: '探测失败：' + ((r && r.error) || '未知错误')
                  });
                }
              });
            }
          }, busy ? '探测中' : '连接'),
          h('button', {
            className: 'eng-conn-btn alt',
            type: 'button',
            disabled: busy || busyLaunch,
            'aria-label': '启动' + label,
            // ── 【NX → Webots 改造】提示文案不能只提 SW 的窗口行为 ────────
            //   原文案对所有目标都说"会自动隐藏欢迎页并最大化主窗口"，
            //   那只对 SolidWorks 成立；CAD/Webots 没有这套窗口处理。
            title: isPlaceholder
              ? (_td.name + ' 启动为预留项，暂未接入')
              : (reqTarget === 'sw'
                  ? '启动软件（会自动隐藏欢迎页并最大化主窗口）'
                  : '启动 ' + _td.name + '（定位可执行文件后拉起进程）'),
            onClick: function () {
              if (busy || busyLaunch) return;
              // 占位项：不拉起任何进程，只给出说明
              if (isPlaceholder) {
                patch({
                  phase: 'idle',
                  data: { state: 'idle', title: _td.name + ' 启动（预留）' },
                  note: _td.name + ' 启动为预留项，暂未接入；'
                        + '当前请手动启动该软件，或在命令行下调用。'
                });
                return;
              }
              patch({
                phase: 'launching', sent: null, logOpen: true,
                note: '正在启动 ' + _td.name
                      + '（首次启动可能要几十秒）…'
              });
              launchConn(reqTarget, function (r) {
                if (!mounted.current) return;
                var txt = (r && r.log_text) || null;
                if (r && r.ok) {
                  patch({
                    phase: 'idle',
                    logText: txt,
                    note: reqTarget === 'webots' ? '启动请求完成；仿真未确认' : '启动成功' + (r.connected ? '，已连接' : '（进程已就绪）')
                  });
                  if (r.state) apply(r.state); else refresh();
                } else {
                  patch({
                    phase: 'idle',
                    logText: txt,
                    logOpen: true,
                    data: { state: 'off', title: '启动失败' },
                    note: '启动失败：' + ((r && r.error) || '未知错误')
                  });
                }
              });
            }
          }, busyLaunch ? '启动中' : '启动')),

        // ── 启动日志区（可折叠）+「发给模型」──────────────────────────
        //   ⚠️ 标题用「启动日志」而不是「错误日志」：启动成功时这里同样会
        //   展示完整步骤日志（find path → spawn → wait → connect），
        //   全部 OK 的记录不叫错误日志，否则用户会误以为出了问题。
        S.logText ? h('div', { className: 'eng-conn-log' },
          h('div', { className: 'eng-conn-log-hd' },
            h('span', {
              className: 'eng-conn-log-tg',
              onClick: function () { patch({ logOpen: !S.logOpen }); }
            }, (S.logOpen ? '▾ ' : '▸ ') + '启动日志'),
            h('button', {
              className: 'eng-conn-btn tiny',
              type: 'button',
              title: '把这段日志作为消息发给当前会话，让 DSH 模型判断根因',
              onClick: function () {
                if (S.sent === 'sending') return;
                patch({ sent: 'sending' });
                sendLogToModel(reqTarget, S.logText, sessionId, function (r2) {
                  if (!mounted.current) return;
                  if (r2 && r2.ok) patch({ sent: 'ok' });
                  else patch({ sent: 'err:' + ((r2 && r2.error) || '未知错误') });
                });
              }
            }, S.sent === 'sending' ? '发送中'
               : (S.sent === 'ok' ? '已发送 ✓' : '发给模型诊断'))),
          S.sent && S.sent.indexOf('err:') === 0
            ? h('div', { className: 'eng-conn-log-sent' }, '发送失败：' + S.sent.slice(4))
            : null,
          S.logOpen ? h('pre', { className: 'eng-conn-log-pre' }, S.logText) : null)
          : null);
    }

    // ══ 【工程环境检查】栏目（用户需求 3）══════════════════════════════════
    // 用户原话：
    //   「在设置 → 工程模式中，连接区上面再加一个『检查电脑是否有对应的环境』，
    //    然后写一段提示词自己发给新的对话，新的 Agent 对话就自己去查看看少啥，
    //    自己安装。」
    //
    // 设计要点（对着用户的三条现场问题逐条落地）：
    //   ① 为什么必须有这个栏目 —— 用户说「当电脑缺少一些该插件需要的内容的时候，
    //      一些设置将失效」。缺 pywin32 / 缺 numpy / python 是 3.5，界面上都看不出
    //      区别，只表现为「某些设置没反应」。这里把每一项都摊开：绿/黄/红三色 +
    //      「怎么修」一行命令。
    //   ② 「自己发给新的对话」—— 提示词由后端（tools/env_check.py）现场生成，
    //      内含本机实测结论、正确的解释器路径、逐条 pip 命令与验收标准；
    //      前端只负责把它塞进一个新工程会话（复用既有 engSendToWorkSession）。
    //   ③ 判定逻辑【一行都不写在 JS】：/env-check 与 /env-prompt 都转调 env_check.py，
    //      保证「设置页看到的」与「Agent 跑出来的」是同一套结论。
    function EngEnvPanel() {
      var st = useState({
        phase: 'idle',      // idle | busy | done | err
        data: null,
        err: null,
        prompt: null,
        promptBusy: false,
        sent: null,
        promptOpen: false,
        root: '',
        rootSaving: false,
        rootMsg: null,
        // ── 【用户反馈 3】可放可收：默认收起，只在【有问题】时自动展开 ──────
        // 用户原话：「一般来说，应该是没问题就默认收起来，有问题就弹出来的，
        //   不然人家本来是正常的，然后你设置跳出来一大堆的」。
        // 规则：首次检查完 status!=='ok' 才展开；用户手动点过之后一律以用户为准。
        expanded: null,
        userToggled: false,
      });
      var S = st[0];
      var mounted = useRef(true);
      function patch(o) {
        if (!mounted.current) return;
        st[1](function (prev) {
          var n = {};
          for (var k in prev) if (Object.prototype.hasOwnProperty.call(prev, k)) n[k] = prev[k];
          for (var k2 in o) if (Object.prototype.hasOwnProperty.call(o, k2)) n[k2] = o[k2];
          return n;
        });
      }
      function runCheck() {
        patch({ phase: 'busy', err: null });
        try {
          fetch('/dsh-engineering-ui/env-check')
            .then(function (r) { return r.json(); })
            .then(function (j) {
              if (!mounted.current) return;
              if (j && j.items) {
                // 【用户反馈 3】自动开合：只有「不 ok」才自动展开；
                //   userToggled=true 表示用户亲手点过，之后以用户为准。
                var _autoExpand = (j.status !== 'ok');
                patch({ phase: 'done', data: j, err: null });
                if (!S.userToggled) patch({ expanded: _autoExpand });
                // 顺带把交付目录回填到输入框（只填一次，用户改过就不覆盖）
                var dr = null;
                for (var i = 0; i < j.items.length; i++) {
                  if (j.items[i].id === 'delivery_root') { dr = j.items[i].detail || ''; break; }
                }
                if (dr && !S.root) {
                  var m = /^(.+?)（来源：/.exec(dr);
                  patch({ root: m ? m[1] : dr });
                }
              } else {
                patch({ phase: 'err', data: j || null,
                        err: (j && (j.error || j.hint)) || '自检没有返回结果' });
              }
            })
            .catch(function (e) {
              patch({ phase: 'err', err: String((e && e.message) || e) });
            });
        } catch (e) {
          patch({ phase: 'err', err: String((e && e.message) || e) });
        }
      }
      useEffect(function () {
        mounted.current = true;
        runCheck();
        return function () { mounted.current = false; };
      }, []);
      function makePrompt() {
        patch({ promptBusy: true, sent: null });
        try {
          fetch('/dsh-engineering-ui/env-prompt')
            .then(function (r) { return r.json(); })
            .then(function (j) {
              if (!mounted.current) return;
              if (j && j.prompt) patch({ promptBusy: false, prompt: j.prompt, promptOpen: true });
              else patch({ promptBusy: false,
                           err: (j && (j.error || j.hint)) || '没能生成提示词' });
            })
            .catch(function (e) {
              patch({ promptBusy: false, err: String((e && e.message) || e) });
            });
        } catch (e) { patch({ promptBusy: false, err: String((e && e.message) || e) }); }
      }
      function sendPrompt() {
        if (!S.prompt || S.sent === 'sending') return;
        patch({ sent: 'sending' });
        var head = '【工程环境修复】请照下面这份清单逐项处理，处理后把结果告诉我。\n\n';
        engSendToWorkSession(head + S.prompt)
          .then(function (res) {
            patch({ sent: (res && res.sent) ? 'ok' : (res && res.drafted ? 'drafted' : 'ok') });
          })
          .catch(function (e) {
            patch({ sent: 'err:' + String((e && e.message) || e) });
          });
      }
      function saveRoot() {
        if (S.rootSaving) return;
        patch({ rootSaving: true, rootMsg: null });
        try {
          fetch('/dsh-engineering-ui/settings', {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ key: 'delivery_root', value: S.root })
          })
            .then(function (r) { return r.json(); })
            .then(function (j) {
              if (!mounted.current) return;
              patch({ rootSaving: false,
                      rootMsg: (j && j.ok) ? '已保存（换电脑会自动忽略，不会误用）'
                                           : ('保存失败：' + ((j && j.error) || '未知')) });
            })
            .catch(function (e) {
              patch({ rootSaving: false, rootMsg: '保存失败：' + String((e && e.message) || e) });
            });
        } catch (e) { patch({ rootSaving: false, rootMsg: String((e && e.message) || e) }); }
      }

      var D = S.data;
      var items = (D && D.items) || [];
      var chipState = !D ? 'idle' : (D.status === 'ok' ? 'on' : (D.status === 'bad' ? 'off' : 'idle'));
      var chipText = !D ? '未检测'
        : (D.status === 'ok' ? '环境完整'
          : (D.status === 'warn' ? '部分可选缺失' : '缺必需项'));
      var rows = [];
      for (var i = 0; i < items.length; i++) {
        var it = items[i];
        var cls = (it.state === 'ok') ? 'ok'
          : (it.state === 'missing' ? 'missing'
            : (it.state === 'unknown' ? 'unknown' : 'warn'));
        rows.push(h('div', { className: 'eng-env-item', key: 'env-' + it.id },
          h('span', { className: 'eng-env-dot ' + cls }),
          h('div', { className: 'eng-env-txt' },
            h('div', { className: 'eng-env-name' }, it.name,
              it.optional ? h('span', { className: 'opt' }, '（可选）') : null),
            it.detail ? h('div', { className: 'eng-env-det' }, it.detail) : null,
            (it.fix && it.state !== 'ok')
              ? h('div', { className: 'eng-env-fix' }, '怎么修：' + it.fix) : null)));
      }
      return h('div', { className: 'eng-env' },
        h('div', { className: 'eng-env-top' },
          h('div', { className: 'eng-env-sum' },
            S.phase === 'busy' ? '正在检查本机环境…'
              : (S.err ? ('检查失败：' + S.err)
                : (D ? (D.summary || chipText)
                     : '点右侧按钮检查本机是否装了工程模式需要的东西'))),
          h(EngStatusChip, { state: S.phase === 'busy' ? 'busy' : chipState, title: chipText }),
          // 【用户反馈 3】可放可收：默认收起，只有"有问题"才自动展开
          h('button', {
            className: 'eng-conn-btn tiny',
            type: 'button',
            title: S.expanded ? '收起明细' : '展开明细',
            onClick: function () { patch({ expanded: !S.expanded, userToggled: true }); }
          }, S.expanded ? '收起' : '展开'),
          h('button', {
            className: 'eng-conn-btn',
            type: 'button',
            disabled: S.phase === 'busy',
            title: '重新检查本机环境（Python / pywin32 / numpy / ezdxf / SW / CAD / 交付目录）',
            onClick: runCheck
          }, S.phase === 'busy' ? '检查中' : '重新检查')),

        (S.expanded === true) ? h('div', { className: 'eng-env-body' },
          // ── 交付目录（不再写死 桌面\test）─────────────────────────────
          h('div', { className: 'eng-env-note' },
            '交付目录：以前固定落在桌面 test，换台电脑就会在那台桌面凭空建一个 test。'
            + '现在可在这里指定；设置只在本机生效，换电脑自动忽略。'),
          h('div', { className: 'eng-path-row' },
            h('input', {
              className: 'eng-path-in',
              type: 'text',
              value: S.root,
              placeholder: 'C:\Users\你的用户名\Documents\dsh-out',
              onChange: function (ev) { patch({ root: ev.target.value, rootMsg: null }); }
            }),
            h('button', {
              className: 'eng-conn-btn tiny',
              type: 'button',
              disabled: S.rootSaving,
              onClick: saveRoot
            }, S.rootSaving ? '保存中' : '保存到本机')),
          S.rootMsg ? h('div', { className: 'eng-env-note' }, S.rootMsg) : null,

          rows.length ? h('div', { className: 'eng-env-list' }, rows) : null,

          h('div', { className: 'eng-env-acts' },
            h('button', {
              className: 'eng-conn-btn',
              type: 'button',
              disabled: S.promptBusy,
              title: '把本机实测结果写进一段提示词，交给一个新的工程模式对话去修',
              onClick: makePrompt
            }, S.promptBusy ? '生成中…' : '生成修复提示词'),
            // 【弹窗修复】给「设置提醒弹窗」一条确定性手动入口
            h('button', {
              className: 'eng-conn-btn tiny',
              type: 'button',
              title: '把「把最大递进深度与子智能体并行上限设为 999」的提醒弹出来',
              onClick: function () { showEngLimitReminder(); }
            }, '立即查看提醒')),

          S.prompt ? h('div', null,
            h('div', { className: 'eng-conn-log-hd' },
              h('span', {
                className: 'eng-conn-log-tg',
                onClick: function () { patch({ promptOpen: !S.promptOpen }); }
              }, (S.promptOpen ? '▾ ' : '▸ ') + '修复提示词（可原样发给新对话）'),
              h('button', {
                className: 'eng-conn-btn tiny',
                type: 'button',
                disabled: S.sent === 'sending',
                title: '新建一个「工程模式」会话，把这段提示词作为首轮发出去',
                onClick: sendPrompt
              }, S.sent === 'sending' ? '发送中'
                 : (S.sent === 'drafted' ? '✓ 已填入新会话'
                   : (S.sent === 'ok' ? '✓ 已发送' : '发给新对话')))),
            S.sent && S.sent.indexOf('err:') === 0
              ? h('div', { className: 'eng-conn-log-sent' }, '发送失败：' + S.sent.slice(4))
              : null,
            S.promptOpen ? h('pre', { className: 'eng-env-prompt' }, S.prompt) : null) : null
        ) : null
      );

    }

    /** 一个开关（自绘，不依赖官方组件库）。
     *
     * bare=true 时【只渲染开关本体】，标签由外层 .eng-row 提供 ——
     *   新版版式把开关放进子项行里，若仍渲染自带标签会出现重复文案。
     */
    function EngSwitch(props) {
      var on = !!props.checked;
      var toggle = h('button', {
        className: 'eng-sw' + (on ? ' on' : ''),
        type: 'button',
        role: 'switch',
        'aria-checked': on ? 'true' : 'false',
        'aria-label': props.label,
        title: props.label,
        onClick: function () { props.onChange(!on); }
      },
        h('span', { className: 'eng-sw-knob' }));
      if (props.bare) return toggle;
      return h('div', { className: 'eng-sw-row' },
        h('div', { className: 'eng-sw-txt' },
          h('div', { className: 'eng-sw-label' }, props.label),
          props.hint ? h('div', { className: 'eng-sw-hint' }, props.hint) : null),
        toggle);
    }



    /* ══════════════════════════════════════════════════════════════════
     * 【内存守卫】设置面板（并发上限 + 手动回收）
     * ══════════════════════════════════════════════════════════════════
     * 用户需求（原文）：
     *   「在多子代理并行中，会出现一堆子代理都在猛猛开的情况，导致出现"给
     *     测试者的电脑内存给干爆了"，这样子改，只能最多三个子代理开启，然后
     *     要避免内存干爆的情况，你看看可不可以搞一下啥类似的只清理系统运行
     *     内存但是不影响流程的正常使用的」。
     *
     * 这里只暴露两件事：
     *   · 并发上限（默认 3）：写 tools/mem_guard.json，宿主【当场】更新运行期
     *     覆盖，不必重启；范围 1..12（再大就等于没有闸门）。
     *   · 「立刻回收一次」：调 POST /mem-guard，走 EmptyWorkingSet ——
     *     只把工作集里的干净页换出，不杀进程、不中断任何屋子。
     * 阈值与间隔仍由 DSH_ENG_MEM_RECLAIM_PCT / _MS 控制，面板只读展示。
     */
    function EngMemGuardPanel(props) {
      var useState = React.useState;
      var useEffect = React.useEffect;
      // embedded（挂在「设计过程优化」子目录里）→ 默认展开明细，见上条注释。
      var st = useState({ open: !!props && props.embedded === true, loading: false, saving: false, err: null, saved: null, data: null, limit: 3 });
      var S = st[0];
      function patch(o) {
        st[1](function (p) {
          var n = {};
          for (var k in p) if (Object.prototype.hasOwnProperty.call(p, k)) n[k] = p[k];
          for (var k2 in o) if (Object.prototype.hasOwnProperty.call(o, k2)) n[k2] = o[k2];
          return n;
        });
      }
      var load = function () {
        patch({ loading: true, err: null });
        fetch('/dsh-engineering-ui/mem-guard')
          .then(function (r) { return r.json(); })
          .then(function (j) {
            if (j && j.ok) patch({ loading: false, data: j, limit: j.max_subagents });
            else patch({ loading: false, err: String((j && j.error) || '读取失败') });
          })
          .catch(function (e) { patch({ loading: false, err: String((e && e.message) || e) }); });
      };
      useEffect(function () { if (S.open && !S.data) load(); }, [S.open]);
      var save = function (next) {
        if (S.saving) return;
        patch({ saving: true, err: null, saved: null, limit: next });
        fetch('/dsh-engineering-ui/mem-guard', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ max_subagents: next }),
        })
          .then(function (r) { return r.json(); })
          .then(function (j) {
            if (j && j.ok) patch({ saving: false, saved: '已保存（立刻生效）', limit: j.max_subagents });
            else patch({ saving: false, err: String((j && j.error) || '保存失败') });
          })
          .catch(function (e) { patch({ saving: false, err: String((e && e.message) || e) }); });
      };
      var reclaim = function () {
        patch({ loading: true, err: null, saved: null });
        fetch('/dsh-engineering-ui/mem-guard', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ force: true }),
        })
          .then(function (r) { return r.json(); })
          .then(function (j) {
            var res = (j && j.result) || {};
            var what = res && res.skipped ? ('已跳过：' + res.skipped)
              : (res && res.ok ? ('回收完成：处理 ' + (res.reclaimed || 0) + ' 个进程')
                : ('回收失败：' + String((res && res.error) || (j && j.error) || '未知')));
            patch({ loading: false, saved: what });
            load();
          })
          .catch(function (e) { patch({ loading: false, err: String((e && e.message) || e) }); });
      };
      var mem = (S.data && S.data.memory) || null;
      var memText = (mem && mem.ok && typeof mem.load === 'number')
        ? (mem.load + '%（可用 ' + mem.free_mb + ' MB / 共 ' + mem.total_mb + ' MB）')
        : '读取中…';
      // ── 回收时机（用户要求原文）────────────────────────────────────────
      // 「这个清理内存是每三个子代理都干完了，然后主对话自己清理的」
      //   ⇒ 宿主侧只在【整批收工】后回收一次；这里如实显示"这批还在跑"，
      //     避免用户以为"刚 fork 就该清一次"。
      var batches = (S.data && Array.isArray(S.data.pending_batches)) ? S.data.pending_batches : [];
      var timing = batches.length
        ? ('当前有 ' + batches.length + ' 批子代理在跑；整批收工后自动回收一次')
        : '当前没有待观察批次；整批收工后自动回收一次';
      var head = h('div', { className: 'eng-row sw' },
        h('div', { className: 'eng-row-txt' },
          h('div', { className: 'eng-row-label' }, '并行子代理上限'),
          h('div', { className: 'eng-row-hint' },
            '当前上限 ' + S.limit + ' 个（默认 3）。同时开太多会把内存打爆；'
            + '超限的 subagent / subagent_fork 会被直接拒绝，并提示先等一个屋收工。'
            + ' 内存回收在【整批子代理都收工后】自动做一次（只回收工作集，不杀进程）。')),
        h('div', { className: 'eng-mc-ft' },
          h('button', {
            className: 'eng-conn-btn tiny', type: 'button',
            title: '内存占用超阈值时只调用 EmptyWorkingSet 回收工作集，不杀进程',
            onClick: function () { if (!S.loading) reclaim(); },
          }, S.loading ? '处理中…' : '立刻回收内存'),
          // embedded（挂在子目录里）时不再给「展开/收起」——
          //   外层子项行已经负责开合，这里再来一个按钮会让人以为有两层折叠。
          (props && props.embedded) ? null : h('button', {
            className: 'eng-conn-btn tiny', type: 'button',
            onClick: function () { patch({ open: !S.open }); },
          }, S.open ? '收起' : '展开')));
      var body = S.open ? h('div', null,
        h('div', { className: 'eng-mc-table' },
          h('div', { className: 'eng-mc-head' },
            h('span', { className: 'eng-mc-idx' }, '#'),
            h('span', { className: 'eng-mc-cell' }, '设定'),
            h('span', { className: 'eng-mc-cell' }, '说明')),
          h('div', { className: 'eng-mc-row' },
            h('span', { className: 'eng-mc-idx' }, '1'),
            h('span', { className: 'eng-mc-cell' }, '最多同时'),
            h('span', { className: 'eng-mc-cell' },
              h('select', {
                className: 'eng-mc-select',
                value: String(S.limit),
                disabled: S.saving,
                onChange: function (ev) { save(Number(ev.target.value) || 3); },
              }, [1, 2, 3, 4, 5, 6, 8, 10, 12].map(function (n) {
                return h('option', { key: 'n' + n, value: String(n) }, n + ' 个');
              }))))),
        h('div', { className: 'eng-mc-ft' },
          h('span', { className: 'eng-row-hint' }, '内存：' + memText),
          h('span', { className: 'eng-row-hint' },
            '回收阈值 ' + ((S.data && S.data.reclaim_threshold_pct) || '-') + '% · '
            + '最小间隔 ' + Math.round(((S.data && S.data.reclaim_interval_ms) || 0) / 1000) + 's'),
          h('span', { className: 'eng-row-hint' }, timing),
          S.saved ? h('span', { className: 'eng-mc-ok' }, '\u2713 ' + S.saved) : null,
          S.err ? h('span', { className: 'eng-mc-err' }, S.err) : null)) : null;
      return h(EngGroup, { title: '内存与并发', embedded: props && props.embedded }, head, body);
    }

    /* ══════════════════════════════════════════════════════════════════
     * 【模型自动接替】设置面板
     * ══════════════════════════════════════════════════════════════════
     * 用户需求：
     *   「第一个模型额度不够嘎了，第二个就立马接上，可以直接指定10个模型，
     *     但是一般是收起来那个表」
     *   「这我也看不到是啥模型啊，而且你起码给我一个可以有模型表的模型让我选择吧」
     *
     * 因此版式与交互：
     *   · 默认【收起】—— 一行摘要「A → B → C」+ 开关；
     *   · 点摘要行展开 10 行表格；
     *   · 每行 provider / model 都是【下拉选择】，选项来自宿主真实模型目录
     *     （/model-catalog → ctx.llm.listProviders + adapter.listModels），
     *     不再需要手打字符串；
     *   · 目录里没有的模型仍可切「手填」兜底；
     *   · 下拉的值是真实 provider/model，显示的是友好名。
     */
    function EngModelChainPanel(props) {
      var useState = React.useState;
      var st = useState({
        // ── 【2026-11-06 结构调整】embedded 模式下默认【展开】 ───────────────
        //   原来它自己是一个主栏目，"默认收起 + 点摘要行展开"是为了省版面；
        //   现在它挂在「设计过程优化」的子目录里，用户点开子项才挂载这个组件，
        //   再让它默认收起就成了"点两次才看得到内容"（实测体验很差）。
        loaded: false, enabled: false, models: [], open: !!props && props.embedded === true, saved: '', error: '',
        cat: null, catLoading: true, catError: '',
        manual: {},
        hostDefault: null, headMatches: true
      });
      var S = st[0], setS = st[1];
      function patch(part) {
        setS(function (prev) {
          var n = {};
          for (var k in prev) n[k] = prev[k];
          for (var k2 in part) n[k2] = part[k2];
          return n;
        });
      }
      // ── 【2026-10-30 改版】默认 3 个槽位，且【不限个数】 ─────────────────
      // 用户要求：「默认3个，然后后面给一个"+"，让他们自己加模型，
      //            不限制他们应该加多少个」。
      // 所以这里不再 slice(0,10)，只保证至少有 3 行可用。
      var MIN_SLOTS = 3;
      function withTen(ms) {
        var a = Array.isArray(ms) ? ms.slice() : [];
        while (a.length < MIN_SLOTS) a.push({ provider: '', model: '' });
        return a;
      }
      function load() {
        fetch('/dsh-engineering-ui/model-chain')
          .then(function (r) { return r.json(); })
          .then(function (j) {
            if (j && j.ok) {
              patch({
                loaded: true, enabled: !!j.enabled, models: withTen(j.models), error: '',
                // 【2026-10-30】记录宿主当前真实默认模型，用于"实际在用"提示
                hostDefault: j.host_default || null,
                headMatches: j.head_matches_default !== false,
              });
            } else {
              patch({ loaded: true, models: withTen([]), error: (j && j.error) || '读取失败' });
            }
          })
          .catch(function (e) {
            patch({ loaded: true, models: withTen([]), error: String((e && e.message) || e) });
          });
      }
      function loadCatalog() {
        fetch('/dsh-engineering-ui/model-catalog')
          .then(function (r) { return r.json(); })
          .then(function (j) {
            if (j && j.ok) patch({ cat: j, catLoading: false, catError: '' });
            else patch({ catLoading: false, catError: (j && j.error) || '模型目录为空', cat: j || null });
          })
          .catch(function (e) {
            patch({ catLoading: false, catError: String((e && e.message) || e) });
          });
      }
      React.useEffect(function () { load(); loadCatalog(); }, []);
      function save(next) {
        fetch('/dsh-engineering-ui/model-chain', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ enabled: next.enabled, models: next.models })
        }).then(function (r) { return r.json(); })
          .then(function (j) {
            if (j && j.ok) patch({ saved: '已保存', error: '' });
            else patch({ saved: '', error: (j && j.error) || '保存失败' });
          })
          .catch(function (e) { patch({ saved: '', error: String((e && e.message) || e) }); });
      }
      function setSlot(i, field, val) {
        var ms = S.models.slice();
        var cur = {};
        for (var k in (ms[i] || {})) cur[k] = ms[i][k];
        cur[field] = val;
        // 换 provider 后清空 model，避免出现"provider 与 model 不匹配"的脏组合
        if (field === 'provider') cur.model = '';
        ms[i] = cur;
        patch({ models: ms, saved: '' });
      }
      function move(i, dir) {
        var j = i + dir;
        if (j < 0 || j >= S.models.length) return;
        var ms = S.models.slice();
        var t = ms[i]; ms[i] = ms[j]; ms[j] = t;
        patch({ models: ms, saved: '' });
      }
      function toggleManual(i) {
        var m = {};
        for (var k in S.manual) m[k] = S.manual[k];
        m[i] = !m[i];
        patch({ manual: m });
      }
      /** 追加一个空槽位（"+" 按钮）。不限个数。 */
      function addSlot() {
        var ms = S.models.slice();
        ms.push({ provider: '', model: '' });
        patch({ models: ms, saved: '' });
      }
      /** 删除某一槽位（至少保留 1 行，避免界面空掉）。 */
      function removeSlot(i) {
        var ms = S.models.slice();
        if (ms.length <= 1) { ms[0] = { provider: '', model: '' }; }
        else { ms.splice(i, 1); }
        // 手动标记是按下标存的，删除后要整体重映射
        var nm = {};
        var k2 = 0;
        for (var k in S.manual) {
          var idx = parseInt(k, 10);
          if (idx === i) continue;
          nm[idx > i ? idx - 1 : idx] = S.manual[k];
        }
        patch({ models: ms, manual: nm, saved: '' });
      }
      /**
       * 清空全部备用模型。
       * 用户要求：「加一个清空，没填模型就是使用默认的，用户自己选择的模型即可」。
       * 清空后只留 3 个空槽位，保存即等于"关闭兜底"。
       */
      function clearAll() {
        var ms = [];
        for (var i = 0; i < MIN_SLOTS; i++) ms.push({ provider: '', model: '' });
        patch({ models: ms, manual: {}, saved: '' });
        // 立刻落盘：空链 = 不兜底
        save({ enabled: true, models: [] });
      }
      /** 保存前裁掉尾部连续空槽位，避免把一堆空行写进配置。 */
      function trimEmpty(ms) {
        var a = ms.slice();
        while (a.length && !(a[a.length - 1].provider && a[a.length - 1].model)) a.pop();
        return a;
      }

      var groups = (S.cat && S.cat.groups) || [];
      var totalModels = (S.cat && S.cat.total) || 0;
      function modelsOf(pid) {
        for (var i = 0; i < groups.length; i++) {
          if (groups[i].id === pid) return groups[i].models || [];
        }
        return [];
      }
      /** 找某个 provider/model 在目录里的元信息（名字 / reasoning 档位）。 */
      function infoOf(pid, mid) {
        var ms = modelsOf(pid);
        for (var i = 0; i < ms.length; i++) {
          if (ms[i].id === mid) return ms[i];
        }
        return null;
      }
      /**
       * 显示名：与官方选择器口径一致 —— 优先用模型 name（如
       * "DeepSeek-V4.1-Flash"），拿不到才退回 id。
       * 用户反馈就是"看不到是啥模型"，所以这里绝不直接甩 id。
       */
      function displayName(pid, mid) {
        var it = infoOf(pid, mid);
        if (it && it.name) return String(it.name);
        // 目录里没有（链外/手填）→ 把 id 做轻度美化：连字符转驼峰首字母
        var s = String(mid || '');
        return s || '（未填）';
      }
      /** 思考档位显示名（×低/×中/×高 这类，取决于 provider 的 efforts）。 */
      function effortName(pid, mid, effortId) {
        if (!effortId) return '';
        var it = infoOf(pid, mid);
        var rs = it && it.reasoning;
        if (rs && Array.isArray(rs.efforts)) {
          for (var i = 0; i < rs.efforts.length; i++) {
            if (rs.efforts[i].id === effortId) return rs.efforts[i].name || effortId;
          }
        }
        return String(effortId);
      }
      function shortName(s, max) {
        var v = String(s || '');
        var n = max || 18;
        if (v.length > n) v = v.slice(0, n - 1) + '\u2026';
        return v;
      }
      var filled = S.models.filter(function (m) { return m && m.provider && m.model; });
      // 摘要：显示友好名（+ 档位），与图二那种"模型名 · 档位"一致
      var summary = filled.length
        ? filled.map(function (m) {
            var nm = shortName(displayName(m.provider, m.model), 20);
            var ef = effortName(m.provider, m.model, m.reasoningEffort);
            return ef ? (nm + ' \u00b7 ' + shortName(ef, 6)) : nm;
          }).join('  \u2192  ')
        : '未配置（展开后从下拉里选，最多 10 个）';

      var rows = S.models.map(function (m, i) {
        var isManual = !!S.manual[i];
        var pid = m.provider || '';
        var mids = modelsOf(pid);
        var knownPids = groups.map(function (g) { return g.id; });

        var provOpts = [];
        provOpts.push(h('option', { value: '', key: 'p' + i + '-empty' }, '— 选择 provider —'));
        if (pid && knownPids.indexOf(pid) < 0) {
          provOpts.push(h('option', { value: pid, key: 'p' + i + '-cur' }, pid + '（目录外）'));
        }
        groups.forEach(function (g) {
          // 显示 "CodeBuddy (腾讯) · 12" —— 让用户一眼看出是哪家、有几个模型
          var cnt = (g.models || []).length;
          var txt = (g.label || g.id) + (cnt ? (' \u00b7 ' + cnt) : '');
          provOpts.push(h('option', { value: g.id, key: 'p' + i + '-' + g.id }, txt));
        });

        var knownMids = mids.map(function (x) { return x.id; });
        var modelOpts = [];
        var noModelHint = !pid
          ? '— 先选 provider —'
          : (mids.length ? '— 选择模型 —' : '— 该 provider 无广告模型，请「手填」—');
        modelOpts.push(h('option', { value: '', key: 'm' + i + '-empty' }, noModelHint));
        if (m.model && knownMids.indexOf(m.model) < 0) {
          modelOpts.push(h('option', { value: m.model, key: 'm' + i + '-cur' }, m.model + '（目录外）'));
        }
        mids.forEach(function (mm) {
          // 与官方选择器一致：显示 model.name（没有 name 才退回 id）
          modelOpts.push(h('option', {
            value: mm.id, key: 'm' + i + '-' + mm.id, title: mm.id
          }, mm.name || mm.label || mm.id));
        });

        var provCell = isManual
          ? h('input', {
              className: 'eng-mc-in', value: pid,
              placeholder: i === 0 ? '如 raccoon' : '',
              onChange: function (e) { setSlot(i, 'provider', e.target.value); }
            })
          : h('select', {
              className: 'eng-mc-sel', value: pid,
              title: pid || '选择 provider',
              onChange: function (e) { setSlot(i, 'provider', e.target.value); }
            }, provOpts);

        var modelCell = isManual
          ? h('input', {
              className: 'eng-mc-in', value: m.model || '',
              placeholder: i === 0 ? '如 sn-sensenova-6-8-flash' : '',
              onChange: function (e) { setSlot(i, 'model', e.target.value); }
            })
          : h('select', {
              className: 'eng-mc-sel', value: m.model || '',
              disabled: !pid,
              title: m.model || '选择模型',
              onChange: function (e) { setSlot(i, 'model', e.target.value); }
            }, modelOpts);

        // ── 思考档位（对应官方选择器里的 "· Hi / · x0.11" 那一段）──────
        // 只有当该模型真的广告了 reasoning.efforts 才显示，否则留空。
        var minfo = infoOf(pid, m.model);
        var efforts = (minfo && minfo.reasoning && Array.isArray(minfo.reasoning.efforts))
          ? minfo.reasoning.efforts : [];
        var effortCell = null;
        if (!isManual && efforts.length) {
          var eOpts = [h('option', { value: '', key: 'e' + i + '-auto' },
            '默认' + (minfo.reasoning.defaultEffort ? ('(' + effortName(pid, m.model, minfo.reasoning.defaultEffort) + ')') : ''))];
          efforts.forEach(function (ef) {
            eOpts.push(h('option', { value: ef.id, key: 'e' + i + '-' + ef.id },
              ef.name || ef.id));
          });
          effortCell = h('span', { className: 'eng-mc-selwrap eng-mc-effort' },
            h('select', {
              className: 'eng-mc-sel', value: m.reasoningEffort || '',
              title: '思考档位（留空=模型默认）',
              onChange: function (e) { setSlot(i, 'reasoningEffort', e.target.value); }
            }, eOpts));
        }

        return h('div', { className: 'eng-mc-row', key: 'mc' + i },
          h('span', { className: 'eng-mc-idx' }, String(i + 1)),
          h('span', { className: 'eng-mc-selwrap' }, provCell),
          h('span', { className: 'eng-mc-selwrap' }, modelCell),
          effortCell,
          h('span', { className: 'eng-mc-ops' },
            h('button', {
              className: 'eng-conn-btn tiny', type: 'button', disabled: i === 0,
              title: '上移', onClick: function () { move(i, -1); }
            }, '\u2191'),
            h('button', {
              className: 'eng-conn-btn tiny', type: 'button',
              disabled: i === S.models.length - 1,
              title: '下移', onClick: function () { move(i, 1); }
            }, '\u2193'),
            h('button', {
              className: 'eng-mc-manual', type: 'button',
              title: isManual ? '改回下拉选择' : '目录里没有？手动填写',
              onClick: function () { toggleManual(i); }
            }, isManual ? '下拉' : '手填'),
            // 删除本行（与"+"配对，形成自由增删；只有 1 行时置灰）
            h('button', {
              className: 'eng-mc-del', type: 'button',
              disabled: S.models.length <= 1,
              title: '删除这一行',
              onClick: function () { removeSlot(i); }
            }, '\u2715')));
      });

      // ── 【2026-10-30 改版】去掉"额度不足自动换模型"开关 ──────────────────
      // 用户要求：「把那个选项『额度不足自动换模型』给去掉」。
      // 语义改为：只要列表里填了模型，就按顺序兜底；没填就用默认（用户自己选的）。
      // 因此不再需要一个显式开关 —— 空列表天然等于"关闭"。
      var embedded = !!(props && props.embedded);
      return h(EngGroup, { title: '模型自动接替', embedded: embedded },
        // embedded 时外层「设计过程优化」的子项行已经负责开合，
        //   这里不再画第二个折叠头（否则会出现"两层折叠"）。
        embedded ? h('div', { className: 'eng-row-hint' },
          '额度/限流耗尽时按下列顺序换下一个模型：' + summary) : h('div', {
          className: 'eng-row clickable',
          role: 'button',
          tabIndex: 0,
          title: '展开/收起模型列表',
          onClick: function () { patch({ open: !S.open }); }
        },
          h('div', { className: 'eng-row-txt' },
            h('div', { className: 'eng-row-label' },
              (S.open ? '▾ ' : '▸ ') + '备用模型（按顺序兜底）'),
            h('div', { className: 'eng-row-hint' }, summary)),
          h('span', { className: 'eng-row-chev' },
            filled.length ? String(filled.length) : '›')),

        // ── 【2026-10-30】"实际在用哪个模型"提示 ─────────────────────────
        // 用户反馈"模型显示没跟上"：UI 只显示链，不显示宿主当前真实使用
        // 的模型，于是"我选的 DeepSeek 到底有没有生效"完全看不出来。
        // 这里把宿主默认选择显式列出来；若与链首不一致，额外提示一句。
        S.hostDefault
          ? h('div', { className: 'eng-mc-empty' },
              '宿主当前默认：' + (S.hostDefault.provider || '?') + ' / '
              + (S.hostDefault.model || '?')
              + (S.hostDefault.reasoningEffort ? (' · ' + S.hostDefault.reasoningEffort) : '')
              + (S.headMatches
                  ? '\u3000✓ 与链首一致'
                  : '\u3000⚠ 与链首不一致；接替只在该模型失败后才会改用下一个，'
                    + '平时仍用这个默认值。'))
          : null,

        (S.open && S.catError)
          ? h('div', { className: 'eng-mc-empty' },
              '模型目录读取失败：' + S.catError + '\u3000（可切「手填」直接输入 provider / model）')
          : null,

        S.open ? h('div', { className: 'eng-mc-table' },
          h('div', { className: 'eng-mc-head' },
            h('span', { className: 'eng-mc-idx' }, '#'),
            h('span', { className: 'eng-mc-cell' }, 'provider'),
            h('span', { className: 'eng-mc-cell' }, 'model'),
            h('span', { className: 'eng-mc-cell eng-mc-effort' }, '档位'),
            h('span', { className: 'eng-mc-ops' }, '')),
          rows,
          // "+" 添加一行（不限个数）—— 用户要求：默认 3 个，后面给个 + 让他们自己加
          h('div', { className: 'eng-mc-addrow' },
            h('button', {
              className: 'eng-mc-add', type: 'button',
              title: '再加一个备用模型',
              onClick: function () { addSlot(); }
            }, '\uFF0B 添加模型')),
          h('div', { className: 'eng-mc-ft' },
            h('button', {
              className: 'eng-conn-btn tiny', type: 'button',
              onClick: function () { save({ enabled: true, models: trimEmpty(S.models) }); }
            }, '保存'),
            h('button', {
              className: 'eng-conn-btn tiny', type: 'button',
              title: '重新读取宿主可用模型',
              onClick: function () { patch({ catLoading: true }); loadCatalog(); }
            }, S.catLoading ? '读取中…' : '刷新目录'),
            // 清空 —— 用户要求：没填模型就用默认（自己选的）模型
            h('button', {
              className: 'eng-conn-btn tiny', type: 'button',
              title: '清空所有备用模型（等于不兜底，始终用你自己选的模型）',
              onClick: function () { clearAll(); }
            }, '清空'),
            h('span', { className: 'eng-row-hint' },
              S.catLoading
                ? '正在读取模型目录…'
                : ('目录共 ' + totalModels + ' 个模型 / ' + groups.length + ' 个 provider')),
            S.saved ? h('span', { className: 'eng-mc-ok' }, '\u2713 ' + S.saved) : null,
            S.error ? h('span', { className: 'eng-mc-err' }, S.error) : null)) : null);
    }
    function EngineeringSettingsSection(props) {
      var close = props && props.close;
      var settings = useEngSettings();
      var useOwn = settings.useOwnSubagentPane;
      // ── 「是否启用工程模式的会话视图」（2026-11-07）─────────────────────
      // 控制官方 conversation.view 里那个「工程模式」页签。
      //   · 与「子代理显示页」解耦：那个开关管右列三栏，这个只管对话页头页签；
      //   · 只影响工程模式会话；非工程模式会话无论开关如何都没有该页签。
      // 默认 true：装上就能看见，不需要用户先去找开关。
      var showEngView = settings.showEngConversationView !== false;
      // ── 子目录展开态（用户要求：模型自动接替 / 内存与并发 收进「设计过程优化」）
      //   ⚠️ React hooks 规则：必须【无条件】调用，且放在任何 return 之前。
      //   默认收起 —— 设置页一屏能看全 3 个开关 + 2 个子项，点开才展开细节。
      var oc = React.useState(false);
      var openModelChain = oc[0];
      var setOpenModelChain = oc[1];
      var om = React.useState(false);
      var openMemGuard = om[0];
      var setOpenMemGuard = om[1];
      return h('div', { className: 'eng-settings', 'data-eng-section': 'engineering' },
        h('div', { className: 'eng-settings-hd' },
          h('span', { className: 'eng-settings-ico' }, '🛠️'),
          h('div', { className: 'eng-settings-hd-txt' },
            h('div', { className: 'eng-settings-title' }, '工程模式'),
            h('div', { className: 'eng-settings-sub' },
              'SolidWorks / CAD 机械设计工作流的专用设置'))),

        h('div', { className: 'eng-settings-body' },

          // ── 主栏目1：连接区 ──────────────────────────────────────────
          // 每行两个按钮：
          //   「连接」→ /conn-probe：只探测（不启动软件，安全默认）
          //   「启动」→ /conn-launch：真的拉起软件，并自动隐藏欢迎页、
          //             最大化主窗口（用户要求"不需要 SW 自己跳出来"）
          // 启动失败 → 行内展示错误日志，并可用「发给模型诊断」把日志
          //   作为消息投给当前会话，由 DSH 模型判断根因。
          // 结果统一落盘 connection_state.json（UI 与 AI 流程共读）。
          // ── 主栏目0：工程环境检查（用户需求 3：放在「连接区」上面）──────────
          // 为什么排在最前：用户遇到的「设置静默失效」，根因几乎都在这一层
          //   （Python 3.5 / 缺 pywin32 / SW 装在别的盘）。先自检再看连接，
          //   顺序反了会让人一直以为「是连接没连上」，白排查。
          h(EngGroup, { title: '工程环境检查' },
            h(EngEnvPanel, {})),

          h(EngGroup, { title: '连接区' },
            h(EngConnRow, { target: 'sw', label: 'SOLIDWORKS连接' }),
            h(EngConnRow, { target: 'cad', label: 'AutoCAD 连接' }),
            // ── 【连接区扩展】Abaqus 占位 / Webots 真实连接 ────────────────
            //   · Abaqus：用户明确"另外一个 A 开头的先不管它了" → 保持占位；
            //   · Webots：用户要求"做实际连接，我已经下载了" → 真实目标，
            //     探测走 WEBOTS_HOME + msys64 布局定位，启动走 webots.exe。
            //     两者都没有 COM；Webots 的外部控制走 webots-controller
            //     （TCP 控制器协议），后续 MCP 会话就接在这一层之上。
            h(EngConnRow, { target: 'abaqus', label: 'Abaqus连接' }),
            h(EngConnRow, { target: 'webots', label: 'Webots连接' }),
            // ── 【按用户要求移除】这里原先挂的是 h(EngWebotsPanel, {})，
            //   即设置页「连接区」下方那行「▶ Webots 仿真报告与证据（只读）」。
            //   用户明确要求删掉这个入口（2026-11-05）。
            //   ⚠️ 只删入口，不删组件：EngWebotsPanel / EngWebotsSnapshot /
            //      EngWebotsReport 仍在下方定义并已导出，测试与将来的
            //      子代理工作区可以直接复用；接回设置页只需恢复这一行。
            h(EngRow, { label: '子栏目选项设置3' })),

          // ── 主栏目2：设计过程优化（用户指定名称）───────────────────────
          // 首行「是否启用选项倒计时」。原「右键引用调出AI解释 / 问问AI」
          //   整条功能已按用户要求【整体删除】（2026-11-07），此处不再有该开关。
          //
          // ── 【2026-11-06 结构调整】用户要求（原文）：
          //   「"内存与并发"和"模型自动接替"统一放在"设计过程优化"的子目录下面」。
          //   因此这两块不再是独立主栏目，改为用 EngRow（带箭头、可点的子项行）
          //   挂在本栏目里；点开后就地展开（embedded 模式：不再画第二层卡片标题）。
          //   这样设置页从上到下就是：连接区 → 设计过程优化（3 个开关 + 2 个子项）
          //   → 美化工程模式 → 工程人设，层级与用户的口述一致。
          h(EngGroup, { title: '设计过程优化' },
            // ── 子目录 1：模型自动接替（原来是一个独立主栏目）───────────────
            //   点这一行就地展开/收起；箭头方向由 openModelChain 控制。
            h(EngRow, {
              label: (openModelChain ? '\u25be ' : '\u203a ') + '模型自动接替',
              hint: '额度/限流耗尽时按列表顺序换下一个模型（含子代理）。',
              onClick: function () { setOpenModelChain(!openModelChain); }
            }),
            openModelChain
              ? h(EngModelChainPanel, { embedded: true })
              : null,
            // ── 子目录 2：内存与并发（并发上限 + 只回收工作集）──────────────
            h(EngRow, {
              label: (openMemGuard ? '\u25be ' : '\u203a ') + '内存与并发',
              hint: '并行子代理上限（默认 3）+ 只回收工作集、不杀进程。',
              onClick: function () { setOpenMemGuard(!openMemGuard); }
            }),
            openMemGuard
              ? h(EngMemGuardPanel, { embedded: true })
              : null),

          // ── 主栏目3：美化工程模式（第 1 行 = 子代理显示页开关）────────
          h(EngGroup, { title: '美化工程模式' },
            h('div', { className: 'eng-row sw' },
              h('div', { className: 'eng-row-txt' },
                h('div', { className: 'eng-row-label' },
                  '使用工程模式自带的子代理显示页'),
                h('div', { className: 'eng-row-hint' }, useOwn
                  ? '右侧由工程模式接管（三栏布局），官方入口也指向本面板。'
                  : '已关闭：右侧交回 DSH 官方显示。')),
              h(EngSwitch, {
                bare: true,
                checked: useOwn,
                label: '使用工程模式自带的子代理显示页',
                onChange: function (next) {
                  setEngSetting('useOwnSubagentPane', next);
                }
              })),
            // ── 【会话视图开关 · 2026-11-07】原来的占位行「子栏目选项设置3」────────
            // 用户要求（原文）：「图四那里改为"是否启用工程模式的会话视图"，
            //   小一点，和旁边的一样」。⇒ 与上一行同款紧凑样式（.eng-row.sw），
            //   不换行、不占额外高度，标签与提示字号完全一致。
            //
            // 语义（用户另一条要求）：「那个会话视图哪怕开了，都是只能在工程模式
            //   运行，不能在其他模式运行」。因此这里【只控制工程模式会话】里的
            //   那个页签；非工程模式会话无论开关如何都不会出现（见 register 处
            //   的 _sessionPresetIsEngineering 判定）。
            h('div', { className: 'eng-row sw' },
              h('div', { className: 'eng-row-txt' },
                h('div', { className: 'eng-row-label' }, '是否启用工程模式的会话视图'),
                h('div', { className: 'eng-row-hint' }, showEngView
                  ? '对话页头多一个「工程模式」页签，里面是主对话的压缩历史。'
                  : '已关闭：「工程模式」页签不出现（仅影响工程模式会话）。')),
              h(EngSwitch, {
                bare: true,
                checked: showEngView,
                label: '是否启用工程模式的会话视图',
                onChange: function (next) {
                  setEngSetting('showEngConversationView', next);
                }
              }))),

          // ── 主栏目4：工程人设（用户要求：新建一个主栏目放填空）────────
          // 用户需求原文：「当使用者使用工程模式的时候，在一开始会当作提示词
          //   发给模型，具体的位置放在"美化工程模式"下新建一个主栏目去放，
          //   当填空题给他们填空」。
          // 行为（Host 侧 /persona + agent/created 的 per-agent section）：
          //   · 保存后【下一轮】请求就带上，无需重启或新建会话；
          //   · 只在【工程模式】的会话里注入（其他预设不受影响）；
          //   · 留空 = 不注入任何文本，等于关闭。
          h(EngGroup, { title: '工程人设' },
            h(EngPersonaEditor, {}))),

        close ? h('div', { className: 'eng-settings-ft' },
          h('button', { className: 'eng-settings-close', onClick: close }, '关闭')) : null
      );
    }
    /**
     * 工程人设编辑器：一个多行填空 + 保存。
     *
     * 与设置页其它行不同，这里需要【读写 Host 文件】（tools/persona.json），
     *   因此走 /persona 端点（GET 读 / POST 写）。
     * 保存后由 Host 的 per-agent systemPrompt.section() 在每轮装配时现场求值，
     *   所以下一轮对话立刻生效 —— 不需要重启 DSH、也不需要新建会话。
     */
    function EngPersonaEditor() {
      var st = useState({ text: '', loaded: false, saving: false, saved: null, err: null });
      var S = st[0];
      function patch(o) {
        st[1](function (p) {
          var n = {};
          for (var k in p) if (Object.prototype.hasOwnProperty.call(p, k)) n[k] = p[k];
          for (var k2 in o) if (Object.prototype.hasOwnProperty.call(o, k2)) n[k2] = o[k2];
          return n;
        });
      }
      useEffect(function () {
        fetch('/dsh-engineering-ui/persona')
          .then(function (r) { return r.json(); })
          .then(function (j) {
            if (j && j.ok) patch({ text: String(j.text || ''), loaded: true });
            else patch({ loaded: true, err: String((j && j.error) || '读取失败') });
          })
          .catch(function (e) { patch({ loaded: true, err: String((e && e.message) || e) }); });
      }, []);
      var save = function () {
        if (S.saving) return;
        patch({ saving: true, saved: null, err: null });
        fetch('/dsh-engineering-ui/persona', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ text: S.text }),
        })
          .then(function (r) { return r.json(); })
          .then(function (j) {
            if (j && j.ok) patch({ saving: false, saved: '已保存' });
            else patch({ saving: false, err: String((j && j.error) || '保存失败') });
          })
          .catch(function (e) { patch({ saving: false, err: String((e && e.message) || e) }); });
      };
      return h('div', { className: 'eng-persona' },
        h('div', { className: 'eng-row-hint' },
          '这段文字会在【工程模式】的新一轮请求开始时作为提示词发给模型。' +
          '留空则不发送。修改后从下一轮对话起生效，无需重启。'),
        h('textarea', {
          className: 'eng-persona-in',
          rows: 6,
          placeholder: '例如：\n你是一位资深机械结构工程师，沟通简洁、先给结论再给依据。\n所有尺寸默认单位 mm；不确定的参数必须先问我，不要自行假设。',
          value: S.text,
          disabled: !S.loaded || S.saving,
          onChange: function (ev) { patch({ text: ev.target.value, saved: null }); },
        }),
        h('div', { className: 'eng-persona-ft' },
          h('span', { className: 'eng-persona-state' },
            !S.loaded ? '读取中…'
              : (S.err ? S.err
                : (S.saved ? S.saved
                  : (S.text.trim() ? ('当前 ' + S.text.trim().length + ' 字') : '当前为空（不发送人设）')))),
          h('button', {
            className: 'eng-persona-save',
            type: 'button',
            disabled: !S.loaded || S.saving,
            onClick: save,
          }, S.saving ? '保存中…' : '保存')));
    }
    // ══ 【问题4】工程模式设置：持久化开关 + 三层结构 ═════════════════════
    // 用户需求：
    //   · 在设置页「工程模式」里加一个选项：**是否使用工程模式自带的子代理显示页**。
    //     开 → 右侧子代理显示由本插件接管（官方子代理入口也指向这里）；
    //     关 → 隐藏本插件面板。
    //   · 设置分【三层】，该开关放在【第三层】。
    //
    // 持久化：用 localStorage（官方 packages/client/store 与 shortcuts 都这么做）。
    //   键名带插件前缀，避免与其他插件冲突。读取失败一律回落默认值 true
    //   （默认"使用自带显示页"= 保持用户此前看到的行为，不静默改变现状）。
    var ENG_SETTINGS_KEY = 'dsh-engineering-ui/settings/v1';
    // ── 【本次改动·Bug4】收起态小标签的落点（可拖动）───────────────────────
    // 单独一个键，避免和设置对象混在一起；读写失败一律回落默认位置。
    var ENG_FAB_POS_KEY = 'dsh-engineering-ui/fab-position/v1';
    // ── 【小标签吸附】位置记录 = { x, y, side } ──────────────────────────────
    // 用户反馈（图一）：小标签能拖动，但拖到屏幕中间就【漂在空中】。
    //   期望：松手时若不在左右边缘，自动吸附到【最近的边】（像悬浮球那样）。
    // 存 side 是为了记住「贴哪一边」：窗口宽度变化后按边重新贴，
    //   而不是死记一个绝对 x（那会随视口变化跑到中间或屏幕外）。
    function loadFabPosition() {
      try {
        if (typeof localStorage === 'undefined') return null;
        var raw = localStorage.getItem(ENG_FAB_POS_KEY);
        if (!raw) return null;
        var j = JSON.parse(raw);
        if (j && typeof j.y === 'number' && isFinite(j.y)) {
          var okSide = (j.side === 'left' || j.side === 'right') ? j.side : null;
          var okX = (typeof j.x === 'number' && isFinite(j.x)) ? j.x : null;
          if (okSide || okX !== null) return { x: okX, y: j.y, side: okSide };
        }
      } catch (e) { /* 读取失败 → 用默认位置 */ }
      return null;
    }
    function saveFabPosition(pos) {
      try {
        if (typeof localStorage === 'undefined') return;
        if (!pos) { localStorage.removeItem(ENG_FAB_POS_KEY); return; }
        localStorage.setItem(ENG_FAB_POS_KEY, JSON.stringify({
          x: (typeof pos.x === 'number' ? pos.x : null),
          y: pos.y,
          side: pos.side || null
        }));
      } catch (e) { /* 写入失败不影响本次会话 */ }
    }
    var _engSettings = {
      useOwnSubagentPane: true,
      // ── 【新增·设置页「美化工程模式」→「是否启用工程模式的会话视图」】─────
      // 控制官方 conversation.view 页签「工程模式」（里面是主对话的压缩历史）。
      // 默认 true：装上就能看见；关掉只是不出现这个页签，不影响其它任何功能。
      // ⚠️ 它【只】对工程模式会话有效 —— 别的预设里该页签永远不存在。
      showEngConversationView: true,
    };
    var _engSettingsSubs = [];
    // ══ 【问题3】官方右列占用探针（可选服务，缺失时回落 DOM）══════════════
    // 研究结论（已核对 0.2.0 源码）：
    //   · 官方读面是 `ctx.sidebarRight`：isExpanded() / active() / openTabs
    //     （ui-sidebar-right/src/client/service.ts:231 / :226 / :264）
    //   · DOM 属性 `data-rightbar-collapsed` 【只反映 track】，且窄屏
    //     （<768px）autoFullscreen 为真时 track=false → 属性仍在，会误判
    //     （SidebarRight.tsx:362,369 + AppFrame.tsx:174,269）
    //     所以 DOM 只能当兜底。
    // 这里把官方服务存成模块级引用，供组件内的探测器使用。
    var _hostRightbar = null;
    // ── 【一键发送给工作模式建模】所需服务的模块级引用 ──────────────────
    // 为什么不用插槽的 inject：shell.overlay 的声明是
    //   { kind:'list'; scope:'root' }，【没有 inject 面】
    //   （ui-layout/src/client/index.ts:98）。
    //   往这种槽注册时传 inject 属于超出声明的用法，风险不值得冒。
    // 本文件既有的同类需求（_hostRightbar）就是用模块级引用的方式解决的，
    //   这里沿用同一套做法，行为确定、不依赖未承诺的插槽能力。
    var _engCtx = null;
    // ── 【能力获取】remote.agentPresets.select（给新会话设预设）────────────
    // 为什么不能直接写 ctx.remote.agentPresets：
    //   Cordis 对【未在 inject 里声明】的服务属性会抛
    //   "cannot get property ... without inject"。官方 ui-agent-preset 的
    //   inject 里就明确列了 'remote.agentPresets'
    //   （ui-agent-preset/src/client/index.ts:62-63）。这正是之前点击报
    //   "cannot get property remote.agentPresets" 的原因。
    //
    // 为什么【不能】把它加进本插件顶层的 inject：
    //   inject 是【硬依赖】：一旦该命名空间在某个部署/版本下不存在，
    //   本插件会永久停在 PENDING —— 守卫、三大防线、整个工程模式 UI 全失效。
    //   这与"别人 clone 下来就能用"的要求直接冲突。
    //
    // 因此用【嵌套 inject】获取能力（官方同款写法，见同一文件 :153 的
    //   ctx.inject([...], scope => ...)）：父子 fiber 独立，依赖缺失时
    //   只有这个子 fiber 不激活，插件本体照常加载。
    var _agentPresetSelect = null;
    /** 设置某会话的预设；能力不可用时返回 null（由调用方决定降级方式）。 */
    function engSelectPreset(sessionId, presetId) {
      if (typeof _agentPresetSelect !== 'function') return null;
      try { return _agentPresetSelect(sessionId, presetId); } catch (e) { return null; }
    }
    /** 惰性读取一个客户端服务；缺失时返回 null 而不是抛错。 */
    function engService(name) {
      try {
        if (_engCtx && typeof _engCtx.get === 'function') {
          var s = _engCtx.get(name);
          if (s) return s;
        }
      } catch (e) { /* 落到下面的属性兜底 */ }
      try { return (_engCtx && _engCtx[name]) || null; } catch (e) { return null; }
    }

    // ══════════════════════════════════════════════════════════════════════
    // 【一键发送给 Agent 建模】—— 共用实现
    // ══════════════════════════════════════════════════════════════════════
    // 用户需求：「新开一个『工程模式的工作』的 Agent，那个 Agent 就可以根据
    //   聊天分析的数据去指导和分析开始建模了」。
    //
    // 全部走官方 API，不碰官方存储：
    //   ① sessions.create({ cwd })     新建空会话（尚未发过消息）
    //   ② remote.agentPresets.select(newId, 'engineering')
    //      —— 必须在【第一条消息之前】：官方在会话开始后锁定预设
    //         （agent-registry 检查 turnBoundary 后抛
    //          'This session has already started'）
    //   ③ sessions.using(newId, ...) → binding.session.prompt([...], 'queue')
    //      —— 走官方 prompt 通道，这条消息【就是】该会话的首轮，
    //         与手动输入完全同链路（同模型、同持久化、同工具面）
    //   ④ uiWorkspace.openSession(newId)  跳过去，立刻看到工作区在干活
    //
    // @param text 首轮任务内容（调用方组装）
    // @returns Promise<void>；失败时 reject，由调用方展示
    function engSendToWorkSession(text) {
      var body = String(text || '').trim();
      if (!body) return Promise.reject(new Error('没有可发送的内容'));

      var sessionsSvc = engService('sessions');
      var wsNav = engService('uiWorkspace');
      // 注：预设选择不在这里读 remote —— 见 engSelectPreset（嵌套 inject 获取）

      // ── 必须落在【同一个工作区】里 ──────────────────────────────────
      // ⚠️ 这里踩过一个坑，务必不要改回 sessions.create({cwd})：
      //   用 cwd 建出来的会话【没有工作区归属】，UI 会显示"选择工作区"、
      //   输入框被禁用 —— 用户看到的就是"跳过去了却发不出去"。
      //   官方建会话用的是 workspaceId（ui-workspace/src/client/navigation.ts:188
      //     this.sessions.create({ workspaceId: workspace.workspaceId })），
      //   并且有现成的 connectWorkspace() 会在该工作区里复用/新建空会话。
      var wsId = engCurrentWorkspaceId();
      if (!wsId) {
        return Promise.reject(new Error(
          '当前会话不在任何工作区里：请先在上方选择一个工作区，再使用该功能'));
      }

      var created;
      if (wsNav && typeof wsNav.connectWorkspace === 'function') {
        // 官方路径：复用该工作区的空会话，没有就新建 —— 天然带工作区归属
        created = Promise.resolve().then(function () { return wsNav.connectWorkspace(wsId); });
      } else if (sessionsSvc && typeof sessionsSvc.create === 'function') {
        created = sessionsSvc.create({ workspaceId: wsId });
      } else {
        return Promise.reject(new Error('无法新建会话：sessions / uiWorkspace 服务均不可用'));
      }

      return created.then(function (newId) {
        if (!newId) throw new Error('新建会话未返回会话 id');

        // ── 顺序很重要：先设预设，再跳转，最后发首轮 ────────────────────
        // 预设必须在【任何可能让会话开始的动作】之前设好：
        //   官方在会话开始后锁定预设（turnBoundary 检查），一旦开始就设不进去。
        //   导航本身不发消息，但把它放在最前面没有好处、只有风险。
        var sel = engSelectPreset(newId, 'engineering');
        if (sel === null && typeof _agentPresetSelect !== 'function') {
          // 能力不可用（本机 DSH 没有该 remote 命名空间）
          throw new Error('本机 DSH 未提供预设选择能力，无法把新会话设为工程模式');
        }
        var afterPreset = (sel && typeof sel.then === 'function')
          ? sel : Promise.resolve({ ok: true });
        return afterPreset.then(function (res) {
          if (res && res.ok === false) {
            var why = (res.error && (res.error.message || res.error.code)) || '未知原因';
            throw new Error('设置工程模式预设失败：' + String(why));
          }
          // 预设已定 → 跳过去（用户立刻看到新会话；也是"填入输入框"退化的前提）
          try {
            if (wsNav && typeof wsNav.openSession === 'function') wsNav.openSession(newId);
          } catch (e0b) { /* 跳转失败不阻断发送 */ }
          return sessionsSvc.using(newId, { source: 'gateway' }, function (reference) {
            return reference.binding.session.prompt([{ type: 'text', text: body }], 'queue');
          });
        }).then(function (pres) {
          if (pres && pres.ok === false) {
            var why2 = (pres.error && (pres.error.message || pres.error.code)) || '未知原因';
            throw new Error(String(why2));
          }
          return { sessionId: newId, sent: true };
        }).catch(function (err) {
          // ── 退化路径：自动发送失败 → 把任务【填进新会话的输入框】 ──────
          // 为什么要有这条路：自动发送依赖会话创建/预设选择/首轮提交三跳，
          //   任何一跳在具体环境里被拒（预设被锁、准入被拒、服务未就绪），
          //   功能就整个不可用 —— 用户白点一次。而"新会话 + 输入框里已备好
          //   任务"仍然完整达成目的，用户只需按一次回车。
          return engDraftIntoComposer(body).then(function (ok) {
            if (ok) return { sessionId: newId, sent: false, drafted: true };
            throw err;   // 连输入框都够不到 → 如实上报
          });
        });
      });
    }

    /**
     * 把文本填进当前会话的输入框（退化路径）。
     *
     * 会话切换后 composer 需要一帧才挂载，因此这里轮询重试而不是只试一次。
     * 用 execCommand('insertText') 走原生输入路径，React/Lexical 才能收到。
     * @param text - 要填入的文本
     * @returns 是否填入成功
     */
    function engDraftIntoComposer(text) {
      var body = String(text || '');
      if (!body) return Promise.resolve(false);
      var tries = 0;
      return new Promise(function (resolve) {
        var attempt = function () {
          tries += 1;
          try {
            var el = document.querySelector(
              '[data-conversation-region="composer"] [contenteditable="true"]');
            if (el) {
              el.focus();
              // 已有草稿时先换行，避免粘连
              var pre = String(el.textContent || '').trim() ? '\n\n' : '';
              document.execCommand('insertText', false, pre + body);
              resolve(true);
              return;
            }
          } catch (e) { /* 继续重试 */ }
          if (tries >= 20) { resolve(false); return; }
          setTimeout(attempt, 150);
        };
        attempt();
      });
    }

    /** 组装交给工作会话的首轮任务文本（面板按钮与操作行按钮共用格式）。 */
    function engComposeWorkPrompt(parts) {
      var lines = [];
      lines.push('以下内容来自历史会话的分析记录，请在工程模式下继续推进。');
      lines.push('');
      if (parts.source) {
        lines.push('【引用的原文】');
        lines.push(String(parts.source).slice(0, 8000));
        lines.push('');
      }
      if (parts.transcript) {
        lines.push('【聊天分析记录】');
        lines.push(String(parts.transcript).slice(0, 20000));
        lines.push('');
      }
      if (parts.analysis) {
        lines.push('【已有的分析结论】');
        lines.push(String(parts.analysis).slice(0, 12000));
        lines.push('');
      }
      lines.push('请基于以上内容开始建模工作：');
      lines.push('1. 先提炼出关键设计参数与约束（尺寸、材料、载荷、接口条件）；');
      lines.push('2. 列出仍需我确认的信息，一次问清，不要反复追问；');
      lines.push('3. 然后调用相应工具（SolidWorks / CAD / 物理仿真）开始建模与验证；');
      lines.push('4. 每一步给出可核对的中间结果。');
      return lines.join('\n');
    }

    /**
     * 找当前主会话所在的【工作区 id】。
     *
     * 为什么需要它：新建工作会话必须带 workspaceId，否则新会话不属于任何
     *   工作区，UI 显示"选择工作区"、输入框禁用，任务发不出去。
     *
     * 数据来源：workspaces 服务（@deepseek-ai/dsh-api-workspace-controller/client，
     *   服务名 'workspaces'，见其 service.ts:126 `super(ctx, 'workspaces')`）——
     *   其 list 快照的 items 是 WorkspaceView[]，含 workspaceId / path / sessionIds。
     *   ui-workspace 的 connectWorkspace 内部也正是用 sessionIds.includes 归属。
     * @returns 工作区 id，找不到时 null
     */
    function engCurrentWorkspaceId() {
      try {
        var wsSvc = engService('workspaces');
        var snap = wsSvc && wsSvc.list && typeof wsSvc.list.getSnapshot === 'function'
          ? wsSvc.list.getSnapshot() : null;
        var items = (snap && snap.items) || [];
        if (!items.length) return null;
        var cur = engCurrentSessionId(null);
        if (cur) {
          for (var i = 0; i < items.length; i++) {
            var it = items[i];
            if (it && it.sessionIds && it.sessionIds.indexOf(cur) >= 0) {
              return it.workspaceId;
            }
          }
        }
        // 当前会话还没归属（例如刚新建）→ 只有一个工作区时直接用它
        if (items.length === 1) return items[0].workspaceId;
      } catch (e) { /* 落空 → null */ }
      return null;
    }

    /** 从会话日志取「聊天分析记录」纯文本（供操作行按钮使用）。 */
    function engFetchTranscript(sessionId) {
      if (!sessionId) return Promise.resolve('');
      return fetch('/dsh-engineering-ui/log?sessionId=' + encodeURIComponent(sessionId) + '&limit=200')
        .then(function (r) { return r.json(); })
        .then(function (j) {
          if (!j || j.ok !== true) return '';
          var out = [];
          var evs = j.events || [];
          for (var i = 0; i < evs.length; i++) {
            var arr = toDisplayEvents(evs[i]);
            for (var k = 0; k < arr.length; k++) {
              var d = arr[k];
              if (!d) continue;
              if (d.kind === 'message' || d.kind === 'text') {
                var t1 = String(d.text || '').trim();
                if (t1) out.push('助手：' + t1);
              } else if (d.kind === 'user-message') {
                var t2 = String(d.text || '').trim();
                if (t2) out.push('我：' + t2);
              }
            }
          }
          return out.join('\n\n');
        })
        .catch(function () { return ''; });
    }

    /**
     * 解析"当前主会话"的 id，props 缺失时逐级兜底。
     *
     * 为什么需要兜底：操作行按钮点击失败最常见的原因就是拿不到 sessionId
     *   （随后 /log 查空 → 报"没有可用的对话内容"）。三种来源依次尝试：
     *     ① 插槽标准 prop（session 作用域正常都有）
     *     ② DOM 上对话区标记的会话 id（官方 ConversationContent 会写）
     *     ③ 会话列表中 mainView 保留者（官方 root 侧取法）
     * @param preferred - 插槽传入的 sessionId，优先使用
     * @returns 会话 id，或 null
     */
    function engCurrentSessionId(preferred) {
      if (preferred) return String(preferred);
      try {
        var el = document.querySelector('[data-conversation-session]');
        if (el) {
          var v = el.getAttribute('data-conversation-session');
          if (v) return String(v);
        }
      } catch (e) { /* 落到列表兜底 */ }
      try {
        var s = engService('sessions');
        var snap = s && s.list && s.list.getSnapshot ? s.list.getSnapshot() : null;
        var ids = (snap && snap.ids) || [];
        for (var i = 0; i < ids.length; i++) {
          var r = snap.byId[ids[i]];
          if (r && r.retainedBy && r.retainedBy.mainView > 0) return String(ids[i]);
        }
      } catch (e2) { /* 全部失败 → null */ }
      return null;
    }
    // ── 【问题4】"当前上下文是否工程模式"（供官方 tab 接管的 canOpen 用）────
    // canOpen 是同步谓词、且在 React 之外执行，所以不能读 hook。
    // 这里用一个模块级布尔：由 SubagentConsole 每次渲染时更新
    //   （它已经可靠地算出了 preset === 'engineering'）。
    // 初始 false = 不接管，宁可不接管也不要污染非工程模式。
    var _engContextActive = false;
    function _isEngineeringContext() { return _engContextActive === true; }
    // ── 当前主会话的预设（模块级同步快照）────────────────────────────────
    // ⚠️ 官方 resolveSlotLabel 的实现是「是函数就调用」，**一个参数都不传**
    //   （app.asar 里逐字核对：function EI(e){return typeof e=="function"?e():e}）。
    //   所以 label 回调拿不到 sessionId，只能读模块级的"当前会话"。
    //   这没问题：页签条本来也只画当前这条会话。
    //
    // ⚠️ 也不能沿用 _isEngineeringContext()：那个标记在「工程模式但还没有
    //   子代理」时会被置成 false（SubagentConsole 里 entries.length===0 就早退
    //   复位）—— 于是新开会话、子代理还没建时页签就一片空白。
    //   这就是「工程模式四个字不见了」的根因。
    //   这里单独维护一份只看【会话预设】的快照，与子代理数量无关。
    var _engMainPresetId = '';
    var _engMainSessionId = '';
    function refreshEngMainPreset() {
      try {
        var s = engService('sessions');
        var snap = s && s.list && s.list.getSnapshot ? s.list.getSnapshot() : null;
        var byId = (snap && snap.byId) || {};
        var ids = (snap && snap.ids) || Object.keys(byId);
        var mainId = '';
        for (var i = 0; i < ids.length; i++) {
          var r = byId[ids[i]];
          if (r && r.retainedBy && (r.retainedBy.mainView || 0) > 0) { mainId = String(ids[i]); break; }
        }
        var preset = '';
        if (mainId && byId[mainId]) {
          var row = byId[mainId];
          var pv = row.projectionValues;
          preset = (pv && typeof pv.agentPreset === 'string')
            ? pv.agentPreset
            : (typeof row.agentPreset === 'string' ? row.agentPreset : '');
        }
        var wasEng = (_engMainPresetId === 'engineering');
        _engMainSessionId = mainId;
        _engMainPresetId = preset;
        // 预设一从"非工程"变成"工程"（或反过来），页签该出现/消失。
        //   官方只在 slots 变动或 locale revision 变化时重算 viewTabs()，
        //   所以这里主动碰一下槽强制重算 —— 否则用户得刷新页面才看得到。
        if ((preset === 'engineering') !== wasEng) reinstallSessionView();
      } catch (e) { /* 拿不到就保持上次结果 */ }
    }
    /** 当前主会话是不是工程模式（读上面的模块级快照）。 */
    function _sessionPresetIsEngineering() {
      if (_engMainPresetId) return _engMainPresetId === 'engineering';
      // 快照还没建立（刚启动、会话列表未就绪）→ 退回渲染上下文标记
      return _isEngineeringContext();
    }
    /** 探测官方右列是否正在占用（true = 本面板应让位）。
     *
     * ── 【根因修复·只认 isExpanded】────────────────────────────────────────
     * 之前同时用了三个判据（isExpanded / active / openTabs），其中两个有害：
     *
     * ① `openTabs` —— 官方该快照是【跨全部会话 + localStorage 持久化】的
     *    （ui-sidebar-right/src/client/tab-inventory.ts:22-47 启动即扫 localStorage）。
     *    于是"历史上开过任何一个 tab"就让它永远非空 →
     *    hostRightbarBusy() 恒为 true → 本插件【永远让位】→
     *    用户看到"该出没出"（面板该显示时却不显示）。已删除该判据。
     *
     * ② `active()` —— 只表示"当前 pane 的活动 tab 是谁"，即使右列【没有展开】
     *    也可能有活动记录。把它当"占用"会误判。已删除。
     *
     * 唯一可靠的语义 = 【官方右列是否展开】（isExpanded）。
     *    展开 = 官方正在用第三列 → 我让位；
     *    未展开 = 第三列空闲 → 我方占列（三栏布局）。
     *    这正是官方 service 的公开读面（service.ts:472-474）。
     */
    function hostRightbarBusy() {
      try {
        var sr = _hostRightbar;
        if (sr && typeof sr.isExpanded === 'function') {
          return !!sr.isExpanded();
        }
      } catch (e) { /* 服务异常 → 走 DOM 兜底 */ }
      // ── DOM 兜底（仅在拿不到官方服务时）────────────────────────────
      // 注意：data-rightbar-collapsed 只反映 track，窄屏全屏时仍存在，
      //   故这里只在"未 collapsed"或"全屏"时判占用（保守，宁可让位）。
      try {
        if (typeof document === 'undefined') return false;
        var frame = document.querySelector('div[style*="grid-template-columns"]');
        if (!frame) return false;
        var collapsed = frame.hasAttribute('data-rightbar-collapsed');
        var fullscreen = frame.hasAttribute('data-rightbar-fullscreen');
        return !collapsed || fullscreen;
      } catch (e2) { return false; }
    }
    function loadEngSettings() {
      try {
        if (typeof localStorage === 'undefined') return;
        var raw = localStorage.getItem(ENG_SETTINGS_KEY);
        if (!raw) return;
        var j = JSON.parse(raw);
        if (j && typeof j === 'object') {
          if (typeof j.useOwnSubagentPane === 'boolean') {
            _engSettings.useOwnSubagentPane = j.useOwnSubagentPane;
          }
          if (typeof j.showEngConversationView === 'boolean') {
            _engSettings.showEngConversationView = j.showEngConversationView;
          }
        }
      } catch (e) { /* 读取失败 → 保持默认 */ }
    }
    function saveEngSettings() {
      try {
        if (typeof localStorage === 'undefined') return;
        localStorage.setItem(ENG_SETTINGS_KEY, JSON.stringify(_engSettings));
      } catch (e) { /* 写入失败不影响本次会话 */ }
    }
    // ── 【会话视图页签】重装钩子（apply() 里装配，这里只留占位）──────────
    // 为什么需要：官方 viewTabs() 只在 slots 变动时重算，而我们的 label 是
    //   函数、不订阅设置变化 —— 开关改了必须"注销再注册"才能让页签立刻出现/
    //   消失。留下这个模块级钩子，设置页与 apply() 之间就不必互相 import。
    var _reinstallSessionView = null;
    function reinstallSessionView() {
      try {
        if (typeof _reinstallSessionView === 'function') _reinstallSessionView();
      } catch (e) { /* 忽略：重装失败最多是页签晚一步刷新 */ }
    }
    function setEngSetting(key, value) {
      _engSettings[key] = value;
      saveEngSettings();
      for (var i = 0; i < _engSettingsSubs.length; i++) {
        try { _engSettingsSubs[i](); } catch (e) {}
      }
      // ── 【会话视图页签】开关变化后必须让官方重新问一遍 label ────────────
      // 官方在 slots 发生变化时重算 viewTabs()；label 是函数、不订阅设置，
      //   所以这里主动"碰一下"槽：注销后立刻重注册，等于强制重算一次。
      //   不做的话，用户得刷新页面才能看到页签出现/消失。
      try {
        if (key === 'showEngConversationView' || key === 'useOwnSubagentPane') {
          reinstallSessionView();
        }
      } catch (e) { /* 重装失败不影响设置本身 */ }
    }
    // 跨标签页/跨窗口同步（官方 shortcuts/storage.ts 同思路）
    try {
      if (typeof window !== 'undefined' && window.addEventListener) {
        window.addEventListener('storage', function (ev) {
          if (ev && ev.key === ENG_SETTINGS_KEY) {
            loadEngSettings();
            for (var i = 0; i < _engSettingsSubs.length; i++) {
              try { _engSettingsSubs[i](); } catch (e) {}
            }
          }
        });
      }
    } catch (e) {}
    loadEngSettings();
    /** 供组件订阅设置变化（返回取消订阅函数）。 */
    function subscribeEngSettings(fn) {
      _engSettingsSubs.push(fn);
      return function () {
        var i = _engSettingsSubs.indexOf(fn);
        if (i >= 0) _engSettingsSubs.splice(i, 1);
      };
    }
    /** 在组件里以 state 形式读取设置（随变化重渲染）。 */
    function useEngSettings() {
      var st = useState(function () {
        return {
          useOwnSubagentPane: _engSettings.useOwnSubagentPane,
          showEngConversationView: _engSettings.showEngConversationView,
        };
      });
      useEffect(function () {
        return subscribeEngSettings(function () {
          // ⚠️ 这里必须把【全部字段】都带上，一个都不能漏。
          //   bug 现场（2026-11-08）：「是否启用工程模式的会话视图」怎么按都是开的，
          //   关掉设置页再打开又自己变开 —— 根因就是这个回调只回填了两个字段，
          //   showEngConversationView 被丢掉 → 组件读到 undefined
          //   → 判定 `!== false` 恒为真 → 开关永远显示"开"。
          st[1]({
            useOwnSubagentPane: _engSettings.useOwnSubagentPane,
            showEngConversationView: _engSettings.showEngConversationView,
          });
        });
      }, []);
      return st[0];
    }


    /** 读某个会话挂在会话列表行上的 preset（会话列表是唯一权威来源）。 */



    function apply(ctx) {
      installStyle();
      // 供「一键发送给工作模式建模」读取 sessions / remote / uiWorkspace
      _engCtx = ctx;
      // ── 用嵌套 inject 拿 remote.agentPresets（不阻塞本插件加载）──────────
      // 依赖缺失时这个子 fiber 不激活，_agentPresetSelect 保持 null，
      //   点击时走"无预设"降级路径，而不是让整个插件挂掉。
      try {
        if (typeof ctx.inject === 'function') {
          ctx.inject(['remote', 'remote.agentPresets'], function (scope) {
            try {
              var ns = scope.remote.agentPresets;
              if (ns && typeof ns.select === 'function') {
                _agentPresetSelect = function (sessionId, presetId) {
                  return ns.select(sessionId, presetId);
                };
              }
            } catch (e) { _agentPresetSelect = null; }
          });
        }
      } catch (e) { /* 不支持嵌套 inject：保持 null，功能降级 */ }
      // ── 【问题3】抓取官方右列服务（可选，缺失不影响加载）────────────────
      // ctx.get 是可选服务读取法：服务不在时返回 undefined，不会让插件失败。
      try {
        if (ctx && typeof ctx.get === 'function') {
          _hostRightbar = ctx.get('sidebarRight') || null;
        }
      } catch (e) { _hostRightbar = null; }
      try { ctx.effect(function () { installStyle(); }, 'dsh-engineering-ui: styles'); } catch (e) { installStyle(); }
      try { ctx.effect(function () { return installFrameWidthSync(); }, 'dsh-engineering-ui: frame sync'); } catch (e) { installFrameWidthSync(); }
      // ── 【已删除 · 2026-11-08】主对话问答卡片的倒计时 ───────────────────
      // 用户要求整体删除（原话：「算了算了，你帮我把倒计时给删了吧」）。
      // 原实现在 120 秒后自动选「推荐项」并提交，用户真实选择还没送出
      // 就被默认值顶掉（session-6e5c9b47 现场）。现不再装配任何计时器。
      // 【本次改动·Bug1】清理模型退化哨兵文本（只影响显示，不改任何数据）
      try {
        ctx.effect(function () { return installSentinelSweeper(); },
                   'dsh-engineering-ui: protocol sentinel sweeper');
      } catch (e) { try { installSentinelSweeper(); } catch (e2) {} }
      // ══ 【问题1/2】当前会话解析：以 mainView 为唯一权威 ═════════════════
      // 结论（已核对 0.2.0 源码 + 官方多处用法）：
      //   本面板挂在 root 作用域的 shell.overlay 上，而 root 作用域的 slot
      //   【不会】收到 `sessionId` 标准 prop（那是 session 作用域才有）；
      //   SessionListState 也【没有】`current` 字段。
      //   官方 root 侧取"当前会话"的标准做法是
      //     useSessions(s => Object.values(s.byId).find(x => x.retainedBy.mainView > 0))
      //   —— 见 ui-layout/DocumentTitle.tsx:19-23、ui-settings-general/
      //   SettingsRoot.tsx:134-138、ui-workspace/tree.ts:40。
      //   本插件即在 SubagentConsole 内用这一条（响应式，切换会话自动跟随），
      //   因此这里【不再需要】自己注入当前会话 hook。
      //
      // 历史说明：曾尝试用 `uiSession.adapter.current` 注入 hook。该 adapter 是
      //   ui-session 为【session 作用域】安装的默认绑定，并非第三方稳定契约
      //   （README 未承诺），且实测在本组合下未生效。已移除，改用 mainView。
      // ══ 【本次改动·Bug3 根因】把 body.dsh-eng-mode 的挂载【移出面板组件】══
      // 旧实现：ENG_MODE_CLASS 只在 SubagentConsole 的 useEffect 里挂，
      //   而该组件在 preset !== 'engineering' 或「设置关闭」时会【提前 return】
      //   → effect 不执行 → body 上没有 dsh-eng-mode →
      //   （该函数已随倒计时删除。）
      //   直接 stopAll() 不挂表 → 用户看到「倒计时没了」。
      //   （同时还会让主对话排版样式一起失效，属于同一个根因。）
      // 现在：由常驻的 EngModeMarker 独立维护这个类，与面板是否渲染解耦。
      try {
        ctx.slots.inject('shell.overlay', function () {
          return ctx.slots.register(
            { name: 'shell.overlay', id: 'eng-mode-marker', order: 30, label: '工程模式标记' },
            EngModeMarker,
          );
        });
      } catch (e) { console.error('[dsh-engineering-ui] mode marker slot', e); }

      try {
        ctx.slots.inject('shell.overlay', function () {
          return ctx.slots.register({ name: 'shell.overlay', id: 'eng-subagent-console', order: 31, label: '子代理控制台' }, SubagentConsole);
        });
      } catch (e) { console.error('[dsh-engineering-ui] console slot', e); }

      // ══ 【需求1】模式说明 / 如何使用弹窗（预设卡片上的入口）═════════════
      // 落点：shell.overlay（root 作用域全帧覆盖层），常驻但默认渲染 null，
      //   只有卡片按钮派发事件时才显示 —— 因此不影响任何既有布局。
      // 卡片按钮本身由 injectPresetGuideButtons() 用 DOM 注入（宿主卡片由
      //   asar 内的包渲染，第三方无法注册其 React 组件；详见该函数注释）。
      try {
        ctx.slots.inject('shell.overlay', function () {
          return ctx.slots.register(
            { name: 'shell.overlay', id: 'eng-preset-guide', order: 45,
              label: '模式说明' },
            EngPresetGuideHost,
          );
        });
      } catch (e) { console.error('[dsh-engineering-ui] preset guide slot', e); }
      // 启动卡片按钮注入器（幂等；不依赖任何插槽是否已声明）
      try { startPresetGuideInjector(); } catch (e) {
        console.error('[dsh-engineering-ui] preset guide injector', e);
      }

      // ══ 【会话视图 · 页签名「工程模式」】官方 conversation.view 页签 ═══════
      // 用户要求（2026-11-07）：
      //   ① 「在黑框那里，加一个文字，官方叫"会话视图"」→ 已实现为官方页签；
      //   ② 「上面那个应该是把"会话视图"改为"工程模式"」→ 页签显示名改成「工程模式」；
      //   ③ 「那个会话视图哪怕开了，都是只能在工程模式运行，不能在其他模式运行」
      //      → 非工程模式会话里【这个页签根本不出现】（不是进去看空页）。
      //
      // 官方契约（0.2.2-rc.1，已从 app.asar 逐字核对，别再凭猜）：
      //   · resolveSlotLabel 的实现是：function EI(e){return typeof e=="function"?e():e}
      //     —— 是函数就【空参调用】，不是函数就原样返回。没有 sessionId 可拿。
      //   · viewTabs() 的过滤条件只有两条：id 必须存在；Trajectory 受开发工具开关
      //     控制。**没有"label 为空就跳过"这回事**。
      //   ⇒ 所以：想让页签"只在工程模式出现"，**不能**靠 label 返回空串
      //     （那会得到一个没有文字的空白页签 —— 这正是上一轮的现象）。
      //     唯一正确的做法是【真的注销注册】：非工程模式时根本不注册。
      //   本函数的职责就是"按当前状态装或卸"，由 reinstallSessionView() 驱动。
      var disposeSessionView = null;
      var _sessionViewDisposer = null;
      var _presetWatchDisposer = null;
      /** 当前是否【应该】有「工程模式」页签（唯一判据，装卸都用它）。 */
      function sessionViewWanted() {
        // ① 设置页自己的开关（与右列三栏面板开关【解耦】）
        if (_engSettings.showEngConversationView === false) return false;
        // ② 只看会话预设：非工程模式会话一律不注册这个页签
        return _sessionPresetIsEngineering();
      }
      var installSessionView = function () {
        // 幂等：已经装着就不重复注册（同一 id 二次注册会被 SlotCore 拒绝）
        if (typeof disposeSessionView === 'function') return;
        if (!sessionViewWanted()) return;
        try {
          disposeSessionView = ctx.slots.inject('conversation.view', function () {
            return ctx.slots.register({
              name: 'conversation.view',
              id: 'engineering-session',
              order: 20,
              // label 是【零参】函数（见上方契约说明）：返回固定文案即可。
              //   出现/消失由"装不装"决定，不由返回值决定 —— 返回空串会得到
              //   一个空白页签，而不是让它消失。
              label: function () { return '工程模式'; },
              inject: function (sessionId) {
                return { sessionId: String(sessionId || '') };
              },
            }, EngSessionViewPage);
          });
        } catch (e) {
          disposeSessionView = null;
          console.error('[dsh-engineering-ui] conversation.view slot', e);
        }
      };
      var uninstallSessionView = function () {
        try { if (typeof disposeSessionView === 'function') disposeSessionView(); } catch (e) {}
        disposeSessionView = null;
      };
      // 先把"当前主会话预设"快照建起来，再装页签 —— 否则首次渲染时
      //   label() 只能退回 _isEngineeringContext()（那条在无子代理时为 false），
      //   用户看到的就是一个【没有文字】的空白页签。
      refreshEngMainPreset();
      installSessionView();
      // 会话列表一变（切换会话 / 预设改完 / 新建会话）就刷新快照，
      //   并按需重装页签 —— 这样"工程模式"四个字不会因为会话状态变动而消失。
      // ⚠️ 同样不能直接 disposers.push（此刻它还没赋值，见下方说明）
      try {
        var _engListSvc = engService('sessions');
        var _engList = _engListSvc && _engListSvc.list;
        if (_engList && typeof _engList.subscribe === 'function') {
          var _d = _engList.subscribe(function () { refreshEngMainPreset(); });
          if (typeof _d === 'function') _presetWatchDisposer = _d;
        }
      } catch (e) { console.error('[dsh-engineering-ui] preset watch', e); }
      // 状态（开关 / 会话预设）一变就【重装】：不想要就真注销，想要就真注册。
      //   这是"只在工程模式出现"的唯一实现方式 —— label 返回空串只能得到一个
      //   空白页签，官方不会替你跳过（见上面契约说明）。
      _reinstallSessionView = function () {
        if (sessionViewWanted()) installSessionView();
        else uninstallSessionView();
      };
      // ⚠️ 这里【不能】直接 disposers.push：apply() 里的 `var disposers = []`
      //   声明在下方（var 提升只提升声明不提升赋值），此刻 disposers 还是
      //   undefined → push 抛 TypeError → 整个 apply 中断（实测会让
      //   eng-limit-popup 的两组真执行用例全红）。
      //   因此先存到一个本作用域变量，等下面声明好再一并挂进 disposers。
      _sessionViewDisposer = function () {
        _reinstallSessionView = null;
        uninstallSessionView();
      };

      // ── 【问题4】设置页「工程模式」分区 ──────────────────────────────
      // order=25：紧跟「Agent 预设」(order=20) 之后。
      // label 用中文字面量（注册方自带文案，shell 不订阅 locale）。
      try {
        ctx.slots.inject('settings.section', function () {
          return ctx.slots.register({ name: 'settings.section', id: 'engineering', order: 25, label: '工程模式' }, EngineeringSettingsSection);
        });
      } catch (e) { console.error('[dsh-engineering-ui] settings.section slot', e); }
      // 【重要】主对话的提问选项卡【不由本插件渲染】。
      // DSH 原生包 @deepseek-ai/dsh-client-ui-user-questions 已注册
      // （dsh-web-app/cordis.patch.yml 的 ui-user-questions 行），它：
      //   - 挂在 conversation.composer（chain/session），有提问挂起时接管输入区
      //   - 用 selectQuestion({interactions}) 判定，只显示当前会话的待答问题
      //   - 点击选项 → pending.answer() → wait.respond() → 标准 RPC → **tool_result**
      //   - 未作答时给 error.unanswered，绝不自动提交
      // 因此这里**不再注册** conversation.chat.node，避免同一个提问出现两个选项卡。
      // 本插件只负责子代理侧的三栏工作区展示（原生不覆盖那里）。
      void MainAskNode;

      // ══ 【问题4·开启侧】"官方右侧子代理显示指向本插件" ═══════════════════
      // 用户需求原话：
      //   「如果选择是的话，那么所有官方的子代理在右侧显示的都指向我们现在搞的。」
      //
      // 官方机制（已核对 0.2.0 源码，ui-subagent/src/client/sidebar-chat/index.tsx）：
      //   官方右侧显示子代理的【唯一】通道是一个 tab 类型：
      //     id       = '@deepseek-ai/dsh-client-ui-subagent'
      //     kind     = 'subagentchat'
      //     priority = 'builtin'          ← 关键：可被 'extension' 档合法接管
      //   它的正文注册在 `sidebar.right.pane.tab`，key = 该 id。
      //   官方 tab-registry 的 band 规则：
      //     extension(3) > builtin(2) > fallback(1)；
      //     同 kind 下更高档位接管；接管者注销后 builtin 自动恢复
      //     （tab-registry.ts:36-59, 253-281, 291-310）。
      //
      // 因此"让官方指向我们"= 用【同一个 kind】+【priority:'extension'】+
      // 【我们自己的新 id】注册一个类型，再用该 id 注册我们的正文。
      //   · 这不是 hack，是官方的一等机制（官方测试 tab-registry.client.spec.ts
      //     就用 extension 接管 builtin 来验证）；
      //   · 用新 id 而非复用官方 id：官方禁止同 id 二次注册（:258），
      //     且新 id 让"关闭开关"时能干净注销、官方自动恢复。
      //   · 我们【不使用】任何官方组件：正文渲染的是本插件自有的 SubagentConsole
      //     同款信息（三栏工作区），只是被挂进了官方的右列 tab。
      var ENG_SUBAGENT_TAB_ID = 'dsh-engineering-ui/subagentchat';
      var disposers = [];
      // 会话视图页签的卸载器（在上一节就已装配好，这里补挂进统一回收列表）
      if (typeof _sessionViewDisposer === 'function') disposers.push(_sessionViewDisposer);
      if (typeof _presetWatchDisposer === 'function') disposers.push(_presetWatchDisposer);
      // ══ 三条独立的 disposer 列表（互不干扰，这是 bug 的根治点）══════════
      // 教训（踩过两次）：
      //   · disposers     —— 插件【卸载】时统一回收，设置变化绝不碰它
      //   · tabDisposers  —— 【tab 接管】专用，设置变化时清空并重装
      //   · dockDisposers —— 【聊天切换器】专用，注册一次常驻，设置变化绝不碰
      //
      // 之前的错误：把切换器的清理函数 push 进 disposers，而设置回调会
      //   遍历并执行 disposers 全部条目 → 切换器被销毁且从不重建
      //   → 表现就是"开关关了又开，除非重启 DSH，UI 再也不出来"。
      //   根因不是"列表没分开"，而是【清理函数本身仍在被清空的列表里】。
      var tabDisposers = [];

      function installOfficialTabTakeover() {
        // 关闭开关时：不注册 → 官方 builtin 生效
        if (!_engSettings.useOwnSubagentPane) return;
        var tabs = null;
        try { tabs = ctx.get ? ctx.get('sidebarRightTabs') : null; } catch (e) { tabs = null; }
        if (!tabs || typeof tabs.register !== 'function') {
          // 服务不在（旧版 DSH / 非 web）→ 静默跳过，不影响其他功能
          return;
        }
        try {
          // ① 注册 tab 类型：接管 'subagentchat' kind（extension 档 > builtin）
          var disposeType = tabs.register({
            id: ENG_SUBAGENT_TAB_ID,
            kind: 'subagentchat',           // ← 同 kind 才能接管官方子代理会话
            patterns: ['dsh-resource://subagentchat/session/**'],
            priority: 'extension',          // ← 高于 builtin，构成接管
            // ── 只在工程模式接管 ────────────────────────────────────────
            // tab-registry 的路由：globs 先筛候选，canOpen 是否决票，
            //   幸存者按 priority band 排序（tab-registry.ts:11 注释）。
            //   因此 canOpen 返回 false 时本类型【退出竞争】，
            //   官方 builtin 自动生效 —— 这正是"限定工程模式"的正确落点：
            //   普通对话点开子代理，看到的仍是官方原生界面。
            canOpen: function () { return _isEngineeringContext(); },
            title: function () { return '子代理工作区'; },
          });
          tabDisposers.push(disposeType);
          // ② 注册正文：key 必须是上面那个 id（tab-registry 用 definition.id 派发）
          //    ⚠️ 这个 inject 的 disposer 也要收进 tabDisposers：
          //    否则每次设置变化都新增一条 inject，累积后同 key 重复注册会抛错。
          var disposePane = ctx.slots.inject('sidebar.right.pane.tab', function () {
            return ctx.slots.register({
              name: 'sidebar.right.pane.tab',
              key: ENG_SUBAGENT_TAB_ID,
            }, EngSubagentTabBody);
          });
          if (typeof disposePane === 'function') tabDisposers.push(disposePane);
        } catch (e) {
          console.error('[dsh-engineering-ui] official subagent tab takeover', e);
        }
      }
      // 设置变化时【只重建 tab 接管】，绝不触碰切换器
      try {
        installOfficialTabTakeover();
        subscribeEngSettings(function () {
          for (var i = tabDisposers.length - 1; i >= 0; i--) {
            try { tabDisposers[i](); } catch (e) {}
          }
          tabDisposers.length = 0;
          installOfficialTabTakeover();
        });
      } catch (e) { console.error('[dsh-engineering-ui] tab takeover setup', e); }
      // 插件卸载时回收 tab 接管
      disposers.push(function () {
        for (var i = tabDisposers.length - 1; i >= 0; i--) {
          try { tabDisposers[i](); } catch (e) {}
        }
        tabDisposers.length = 0;
      });


      // ══ 【第5项】进入工程模式时的强制设置提醒弹窗 ═══════════════════════
      // 注册一个 command contribution（走官方弹窗外壳，不自绘 UI）。
      // 常驻注册：触发与否完全由 available 里的工程模式变量决定，
      //   **不绑定设置页**（用户明确要求"必须是强制触发的"）。
      try {
        var disposeLimit = installEngLimitPopup();
        if (typeof disposeLimit === 'function') disposers.push(disposeLimit);
      } catch (e) { console.error('[dsh-engineering-ui] eng limit popup registration', e); }
      // ★【缺陷1 修复】把【自动弹出的触发源】接上。
      //   原先只调了 installEngLimitPopup()（仅注册），
      //   tryAutoOpenEngLimitPopup 全文零调用点 → 弹窗永不自动出现。
      //   这里订阅会话列表，会话变化即尝试弹出（内部有按会话的防轰炸）。
      try {
        var disposeAuto = startEngLimitAutoTrigger();
        if (typeof disposeAuto === 'function') disposers.push(disposeAuto);
      } catch (e) { console.error('[dsh-engineering-ui] eng limit auto trigger', e); }
    }
    // ══════════════════════════════════════════════════════════════════════
    // 【第5项】进入工程模式时的【强制】设置提醒弹窗
    // ══════════════════════════════════════════════════════════════════════
    // 用户需求原文：
    //   「搞一个弹窗，当用户点击"工程模式"的时候，自动跳出这个弹窗，弹窗的文字是
    //     '注意：请在使用工程模式的时候，将左边的"插件"页面中的"子智能体"，
    //      将最大递进深度和子智能体并行上限均设置为999，否则，
    //      **预设模式将无法正常生效**！！！！'
    //     然后设置弹窗的俩个按键为：'稍后提醒'和'不再提醒'。
    //     稍后提醒就是不注销那个弹窗，只要选择那个工程模式，就会弹出那个弹窗。
    //     '不再提醒'就是按了一次之后，再弹出一个小弹窗：'请一定要修改，否则预设
    //      模式将根本无法发挥正常作用'，第一个选项是'好的，我回去修改'，然后就
    //      跳转到上一个弹窗。第二个是'我确认'。按下了，我确认就直接注销这个弹窗，
    //      不再跳出
    //     方法是：通过 ctx.commandUi 注册一个 command contribution，带上
    //     popupSelect spec。……在 contribution 的 available 钩子里判断你的预设变量。
    //     变量为真时返回可用，弹窗就可以被触发；变量为假时不可用，弹窗不会出现。
    //     不能将这个弹窗绑定在设置中，必须是强制触发的」
    //
    // ── 与官方 API 的差距（实据见 NOTES-commandUi-api.md）──────────────────
    //   ① popupSelect 是【严格单选列表】，没有"多按钮"这回事（402159 单飞、
    //      402237 成功后无条件关闭）。因此"两个按键"实现为**两个选项行**。
    //   ② 二级小弹窗用 `option.confirmation`（官方唯一的嵌套确认框 402991）。
    //      注意它的语义：cancelLabel → 回到仍打开的上一层选择器
    //      （正是用户要的"跳转到上一个弹窗"）；confirmLabel 才真正选中。
    //      它强制带一个 acknowledge 勾选框（452918），无法去掉。
    //   ③ **官方没有公开的命令式 open()**（NOTES §5）。文档认可的打开方式
    //      只有"用户输入 /命令"或从 / 菜单选。这里尝试内部通道
    //      popupFor(actx).open(...)，失败则优雅降级为引导提示。
    var ENG_LIMIT_CMD = 'eng-subagent-limits';
    var ENG_LIMIT_DISMISS_KEY = 'dsh-engineering-ui/limit-popup/v1';
    var _engLimitTried = {};        // sessionId → 已尝试过自动弹出（防轰炸）

    /** 读取"不再提醒"的持久标记。与设置页【无关】（用户要求不得绑设置）。 */
    function _engLimitState() {
      try {
        if (typeof localStorage === 'undefined') return {};
        var raw = localStorage.getItem(ENG_LIMIT_DISMISS_KEY);
        var j = raw ? JSON.parse(raw) : null;
        return (j && typeof j === 'object') ? j : {};
      } catch (e) { return {}; }
    }
    function _engLimitNeverShow() {
      return _engLimitState().never === true;
    }
    function _engLimitSetNever(v) {
      try {
        if (typeof localStorage === 'undefined') return;
        var j = _engLimitState();
        j.never = !!v;
        j.at = new Date().toISOString();
        localStorage.setItem(ENG_LIMIT_DISMISS_KEY, JSON.stringify(j));
      } catch (e) { /* 写入失败只影响持久性，不影响本次 */ }
    }

    /** 弹窗正文（用户给的原话，一字不改）。 */
    var ENG_LIMIT_TITLE = '注意：请在使用工程模式的时候，将左边的「插件」页面中的'
      + '「子智能体」，将最大递进深度和子智能体并行上限均设置为 999，'
      + '否则，预设模式将无法正常生效！！！！';

    /**
     * ★【缺陷2 修复】工程模式判据 —— 用【官方会话身份投影】，而不是 _engContextActive。
     *
     * 为什么必须换掉 _isEngineeringContext()（vm 实测暴露的真实缺陷）：
     *   它的唯一置位点在 SubagentConsole 内（L2731），而前面 L2722 有一道闸门
     *   `if (entries.length === 0) { _engContextActive = false; return null; }`
     *   —— 即**只有该会话已经存在子代理时它才为真**。
     *   而"刚点进工程模式、还没建任何子代理"恰恰是本提醒【最该出现】的时刻，
     *   此时它恒为 false → available() 为假 → 弹窗永不出现。
     *
     * 改用会话身份的原因是它更贴合用户原话：
     *   「这个触发点是和切换工程模式时出现的『聊天』和『工作』这两个特殊UI的
     *     变量是一样的」—— 与会话行的 agentPreset 同一口径，
     *   而 preset 来自会话行的 agentPreset（见 L4197-4205），不是 _engContextActive。
     *
     * 取值方式与官方 /permission 一致（projections.faceOf(...).getSnapshot()，
     * 见 NOTES-commandUi-api.md §3）。
     * 注意：传给 available 的是【触发管线会话】，projection 在【已物化 binding
     *   会话】上，因此两级都尝试（与官方 sessionFor 的间接取法同理）。
     */
    function _sessionIsEngineering(session) {
      try {
        if (!session) return false;
        // ① 直接读会话身份投影（不同版本键名可能是 agentPreset / preset）
        var proj = session.projections;
        if (proj && typeof proj.faceOf === 'function') {
          var keys = ['agentPreset', 'preset', 'agentPresetId'];
          for (var i = 0; i < keys.length; i++) {
            try {
              var face = proj.faceOf(keys[i]);
              var v = face && typeof face.getSnapshot === 'function'
                ? face.getSnapshot() : null;
              if (v === ENG_PRESET_ID) return true;
              if (v && v.id === ENG_PRESET_ID) return true;
            } catch (e) { /* 该键不存在 → 试下一个 */ }
          }
        }
        // ② 退回会话行（会话行的取法：projectionValues.agentPreset）
        if (session.sessionId) {
          var pv = session.projectionValues;
          if (pv && pv.agentPreset === ENG_PRESET_ID) return true;
          if (session.agentPreset === ENG_PRESET_ID) return true;
          // ③ 再退回会话列表（权威来源）
          var listSrc = engService('sessions');
          var row = _engSessionRow(listSrc && listSrc.list, session.sessionId);
          var p = row
            ? ((row.projectionValues && row.projectionValues.agentPreset)
               || row.agentPreset || null)
            : null;
          if (p === ENG_PRESET_ID) return true;
        }
        // ④ 最后退回旧的模块级判据（作为兜底，不放弃已生效的路径）
        return _isEngineeringContext();
      } catch (e) {
        return false;
      }
    }

    /** 从会话列表快照里取某会话行（与会话行 agentPreset 同口径）。 */
    function _engSessionRow(listSvc, sessionId) {
      try {
        if (!listSvc || !sessionId) return null;
        var snap = typeof listSvc.getSnapshot === 'function' ? listSvc.getSnapshot() : null;
        if (!snap) return null;
        if (snap.byId && snap.byId[sessionId]) return snap.byId[sessionId];
        var items = snap.items || snap.rows || [];
        for (var i = 0; i < items.length; i++) {
          if (items[i] && items[i].sessionId === sessionId) return items[i];
        }
        return null;
      } catch (e) { return null; }
    }

    /** 选项行（= 用户说的"两个按键"）。 */
    function _engLimitOptions() {
      return [
        {
          id: 'later',
          label: '稍后提醒',
          // ★【缺陷3 修复】官方 popupSelect 没有自由正文区（渲染词汇表只有
          //   label / badge / detail / active，见 NOTES §10-5），`detail` 是唯一
          //   能承载整段文字的渲染位（asar 402924：常驻渲染，不依赖高亮）。
          //   原先这里是短句，导致用户【看不到】那段完整警告 —— 等于提醒没生效。
          detail: ENG_LIMIT_TITLE + '（本次先不修改；下次进入工程模式时仍会提醒。）',
        },
        {
          id: 'never',
          label: '不再提醒',
          detail: ENG_LIMIT_TITLE + '（确认后不再弹出。）',
          // ── 二级小弹窗：官方唯一的嵌套确认框 ──────────────────────────
          // cancelLabel（"好的，我回去修改"）→ 回到上面那个选择器（= 上一层弹窗）
          // confirmLabel（"我确认"）        → 真正选中 never，写入永久标记
          confirmation: {
            title: '请一定要修改，否则预设模式将根本无法发挥正常作用',
            description: '在左侧「插件」页面找到「子智能体」，把「最大递进深度」'
              + '与「子智能体并行上限」都改成 999。改完再回来即可，不影响当前会话。',
            acknowledgeLabel: '我已了解需要修改这两项设置',
            cancelLabel: '好的，我回去修改',
            confirmLabel: '我确认',
          },
        },
      ];
    }

    /**
     * 注册"设置提醒"命令。
     *
     * available 是【纯同步、不可抛】的判定（NOTES §3：每次候选求值都会调用）：
     *   · 非工程模式        → false（不出现）
     *   · 已选"不再提醒"    → false（永久消失）
     *   · 其余（工程模式）  → true
     */
    function installEngLimitPopup() {
      var cmdUi = null;
      // ★【BUG 修复】本函数定义在 apply(ctx) 之外，**没有 ctx 在作用域里**。
      //   原先写 `ctx.get('commandUi')` 会抛 ReferenceError，被下面的
      //   try/catch 吞掉 → cmdUi=null → 直接 return null →
      //   commandUi.register **从未被调用**，弹窗永远不出现
      //   （用 vm 真加载实测：registered 数 = 0）。
      //   正确做法：走文件里本就为此准备的 engService()（读模块级 _engCtx）。
      try { cmdUi = engService('commandUi'); } catch (e) { cmdUi = null; }
      if (!cmdUi || typeof cmdUi.register !== 'function') {
        // 该 profile 没有命令服务 → 静默跳过，绝不影响其他功能
        return null;
      }
      var dispose = cmdUi.register({
        name: ENG_LIMIT_CMD,
        label: function () { return '工程模式设置提醒'; },
        description: function () { return '提醒把子智能体上限设为 999'; },
        // ★ 触发变量：改用【会话身份】判据 _sessionIsEngineering（缺陷2 修复）。
        //   原先用 _isEngineeringContext()，它在"还没建任何子代理"时恒为 false
        //   —— 而那正是本提醒最该出现的时刻，导致弹窗永不出现。
        //   官方签名是 available(session)（不是 projection，见 NOTES §3）。
        available: function (session) {
          try {
            if (_engLimitNeverShow()) return false;
            return _sessionIsEngineering(session);
          } catch (e) {
            // available 不可抛（抛了会打断整个候选求值）→ 保守返回 false
            return false;
          }
        },
        ui: {
          kind: 'popupSelect',
          searchMode: 'substring',
          searchLabels: function () {
            return {
              placeholder: '选择：稍后提醒 / 不再提醒',
              empty: '没有可选项',
              noResults: '没有匹配的选项',
            };
          },
          options: function () {
            // 每次打开只取一次（官方语义）；本弹窗是静态两行
            return Promise.resolve(_engLimitOptions());
          },
          onSelect: function (option) {
            var id = option && option.id;
            if (id === 'never') {
              // 走到这里说明用户在二级弹窗点了"我确认"（cancel 不会触发 onSelect）
              _engLimitSetNever(true);
            }
            // 'later'：不写任何持久标记 → 下次进工程模式仍会提醒（用户要求）
            return Promise.resolve();
          },
        },
      });
      return dispose;
    }

    /**
     * 尝试【自动】弹出（用户要求"当用户点击工程模式的时候自动跳出"）。
     *
     * ⚠️ 官方没有公开的命令式 open()（NOTES §5 已确证）。这里走内部通道
     *    `commandUi.popupFor(actx).open(...)`：
     *      · 需要"已保留的 Session scope"，拿不到就抛
     *        `command.popupFor requires a retained Session scope`；
     *      · 属内部机制，官方文档只承诺"输入 /命令"。
     *    因此全程 try/catch，失败则**优雅降级**：不抛到插件外、不白屏，
     *    改为在控制台留一条可操作提示（用户可手动输入 /命令 打开）。
     *
     * 防轰炸：每个 sessionId 只自动尝试一次（available 每次求值都会调用，
     *   若不设闸门会在关掉后被立刻重新弹出）。
     */
    /**
     * 自绘降级卡片 —— 自动弹出失败时，用户仍能【看得见】提醒。
     *
     * 为什么必须有它（子代理指出的缺口）：
     *   官方没有公开的命令式 open()，自动弹出可能失败。若只写 console.info，
     *   **用户什么都看不到** —— 等于这个强制提醒压根没生效。
     *   因此这里用自有 DOM 渲染一张最小卡片（不走官方弹窗外壳），
     *   提供与 popupSelect 相同的两个动作。
     *
     * ⚠️ 不能 import @deepseek-ai/dsh-client-ui-primitives（授权规则禁止），
     *    所以自带标记与 CSS，类名统一用 eng-limits-* 前缀，样式只用
     *    --dsw-alias-* 设计 token，避免与官方样式冲突。
     */
    /**
     * 自绘降级卡片 —— 自动弹出失败时，用户仍能【看得见】提醒。
     *
     * @param session 会话对象（可选）
     * @param preChecked 调用方【已经】确认过"当前是工程模式"（可选）。
     *   ★ 为什么需要它：`tryAutoOpenEngLimitPopup` 在入口就已经用
     *     `_sessionIsEngineering()` 把过关（非工程模式会直接 return false），
     *     因此它走到任何一个降级分支时，"是不是工程模式"已经有了结论。
     *     此时若在卡片里【再查一次】，而那次查询因会话列表查不到该 id 而失败，
     *     就会返回 false → 卡片被自己挡掉 → 用户什么都看不到
     *     （这正是缺陷4 的完整形态：判据换了，但"二次自检"把结论又推翻）。
     *     传 preChecked=true 表示"结论已成立，别再自检"。
     */
    function showEngLimitFallbackCard(session, preChecked) {
      try {
        if (typeof document === 'undefined' || !document.body) return false;
        // ★【缺陷4 修复】原先这里仍用旧的 _isEngineeringContext() ——
        //   它的置位点在 SubagentConsole 内，且要求 entries.length > 0，
        //   即"只有该会话已有子代理时才为真"。
        //   后果正是缺陷2 的翻版：**刚进工程模式、还没建子代理**时它 = false，
        //   而此时恰恰是最容易需要降级的时刻（新会话 scope 往往还没保留 →
        //   popupFor 抛 → 走降级）→ 降级卡片自己也被挡住 → 用户什么都看不到。
        if (!preChecked) {
          var _sess = session && typeof session === 'object'
            ? session : { sessionId: _engLimitCurrentSessionId() };
          if (!_sessionIsEngineering(_sess)) return false;
        }
        if (_engLimitNeverShow()) return false;
        // 已存在就不重复插入
        if (document.getElementById('eng-limits-mask')) return true;

        var mask = document.createElement('div');
        mask.id = 'eng-limits-mask';
        mask.setAttribute('data-eng-limits', '1');
        mask.style.cssText = [
          'position:fixed', 'inset:0', 'z-index:2147483000',
          'background:rgba(15,23,42,.45)',
          'display:flex', 'align-items:center', 'justify-content:center',
          'padding:24px',
        ].join(';');

        var card = document.createElement('div');
        card.style.cssText = [
          'max-width:560px', 'width:100%',
          'background:var(--dsw-alias-bg-layer-2,#fff)',
          'color:var(--dsw-alias-label-primary,#0f172a)',
          'border-radius:12px', 'padding:20px 22px',
          'box-shadow:0 12px 40px rgba(0,0,0,.28)',
          'font-size:14px', 'line-height:1.7',
        ].join(';');

        var title = document.createElement('div');
        title.style.cssText = 'font-weight:600;font-size:15px;margin-bottom:10px;';
        title.textContent = ENG_LIMIT_TITLE;

        var tip = document.createElement('div');
        tip.style.cssText = 'opacity:.75;margin-bottom:16px;';
        tip.textContent = '（自动弹窗不可用，已改用本提示。'
          + '也可在输入框输入 /' + ENG_LIMIT_CMD + ' 再次打开。）';

        var row = document.createElement('div');
        row.style.cssText = 'display:flex;gap:10px;justify-content:flex-end;';

        var btnLater = document.createElement('button');
        btnLater.textContent = '稍后提醒';
        btnLater.style.cssText = 'padding:6px 14px;border-radius:8px;cursor:pointer;'
          + 'border:1px solid var(--dsw-alias-border-secondary,#cbd5e1);'
          + 'background:transparent;color:inherit;';
        btnLater.onclick = function () {
          // 不写永久标记 → 下次进工程模式仍会提醒（与 popupSelect 的 later 一致）
          try { mask.remove(); } catch (e) {}
        };

        var btnNever = document.createElement('button');
        btnNever.textContent = '不再提醒';
        btnNever.style.cssText = 'padding:6px 14px;border-radius:8px;cursor:pointer;'
          + 'border:none;background:var(--dsw-alias-interactive-bg-primary,#2563eb);'
          + 'color:#fff;';
        btnNever.onclick = function () {
          // 二级确认：与 popupSelect 的 confirmation 等价（两个动作）
          if (!window.confirm('请一定要修改，否则预设模式将根本无法发挥正常作用\n\n'
            + '点「确定」= 我确认（不再提醒）\n点「取消」= 好的，我回去修改')) {
            try { mask.remove(); } catch (e) {}
            return;
          }
          _engLimitSetNever(true);
          try { mask.remove(); } catch (e) {}
        };

        row.appendChild(btnLater);
        row.appendChild(btnNever);
        card.appendChild(title);
        card.appendChild(tip);
        card.appendChild(row);
        mask.appendChild(card);
        document.body.appendChild(mask);
        return true;
      } catch (e) {
        // 连降级 UI 都失败：不再抛，保持控制台提示即可
        console.info('[dsh-engineering-ui] 降级提示渲染失败：%s', (e && e.message) || e);
        return false;
      }
    }

    function tryAutoOpenEngLimitPopup(sessionId, session) {
      try {
        if (!sessionId) return false;
        if (_engLimitTried[sessionId]) return false;
        // ★【缺陷2 修复】判据改用会话身份（原先 _isEngineeringContext() 在
        //   "还没建任何子代理"时恒为 false，而那正是最该提醒的时刻）。
        if (!_sessionIsEngineering(session || { sessionId: sessionId })) return false;
        if (_engLimitNeverShow()) return false;
        // ⚠️【记账点后移】_engLimitTried 必须等到"确实即将 open"时才置位。
        //   原先在函数开头就置位，导致首次因 scope 未保留而失败时
        //   **一次性机会被白白烧掉**，此后再不重试（vm 实测踩到）。
        var cmdUi = null;
        // ★【BUG 修复】同样不能用 ctx.get —— 本函数在 apply 之外。
        try { cmdUi = engService('commandUi'); } catch (e) { cmdUi = null; }
        if (!cmdUi || typeof cmdUi.popupFor !== 'function') {
          _engLimitTried[sessionId] = true;
          return showEngLimitFallbackCard(session || { sessionId: sessionId }, true);
        }
        // 取会话 scope：不同版本服务名不同，逐个尝试，全失败就放弃（降级）
        var sessionsSvc = null;
        try { sessionsSvc = engService('sessions'); } catch (e) { sessionsSvc = null; }
        var scope = null;
        try {
          if (sessionsSvc && typeof sessionsSvc.scope === 'function') {
            scope = sessionsSvc.scope(sessionId);
          }
        } catch (e) { scope = null; }
        if (!scope) {
          // ★【缺陷4 附带修复】此处原先【没有】置位 _engLimitTried，
          //   与另外两处降级分支语义不一致：scope 一时拿不到时，
          //   每个 tick（会话列表每次变更）都会重跑一遍并反复尝试插卡片。
          //   卡片内部虽有 getElementById 幂等保护不会重复弹，
          //   但仍应与其他分支一致地记账，避免无谓重试。
          _engLimitTried[sessionId] = true;
          console.info('[dsh-engineering-ui] 自动弹出不可用（拿不到会话 scope），改用降级提示。');
          return showEngLimitFallbackCard(session || { sessionId: sessionId }, true);
        }
        var ctl = cmdUi.popupFor(scope);
        if (!ctl || typeof ctl.open !== 'function') {
          _engLimitTried[sessionId] = true;
          console.info('[dsh-engineering-ui] 自动弹出不可用（无 popupFor 控制器），改用降级提示。');
          return showEngLimitFallbackCard(session || { sessionId: sessionId }, true);
        }
        // ★ 到这里才记账：确实拿到了控制器、马上要 open（机会没被浪费）
        _engLimitTried[sessionId] = true;
        ctl.open(ENG_LIMIT_CMD, {
          kind: 'popupSelect',
          searchMode: 'substring',
          searchLabels: function () {
            return { placeholder: '选择：稍后提醒 / 不再提醒',
                     empty: '没有可选项', noResults: '没有匹配的选项' };
          },
          options: function () { return Promise.resolve(_engLimitOptions()); },
          onSelect: function (option) {
            if (option && option.id === 'never') _engLimitSetNever(true);
            return Promise.resolve();
          },
        }, { sessionId: sessionId }, { via: 'enter', token: '/' + ENG_LIMIT_CMD });
        return true;
      } catch (e) {
        // ★ 降级：官方弹窗通道整体不可用 → 至少让用户看得见提醒
        console.info('[dsh-engineering-ui] 自动弹出设置提醒失败（%s），改用降级提示。',
          (e && e.message) || e);
        try { _engLimitTried[sessionId] = true; } catch (e2) {}
        return showEngLimitFallbackCard(session ? { sessionId: sessionId } : undefined, true);
      }
    }
    exports._engLimitCurrentSessionId = _engLimitCurrentSessionId;

    /**
     * ★【缺陷1 修复】自动弹出的【触发源】—— 原先整条链是死的。
     *
     * 实测（子代理与我都确认）：`tryAutoOpenEngLimitPopup` 全文只有"定义"和
     * "导出"两处，**零调用点**。于是即便 register 成功，用户不手动输入
     * `/eng-subagent-limits` 就永远看不到弹窗 —— "点击工程模式自动跳出"
     * 这条核心需求当时是 0% 实现。
     *
     * 这里的做法：由 apply() 订阅会话列表，每当"当前会话"变化就 tick 一次；
     * tick 里按会话身份判定，命中则尝试自动弹出（内部自带防轰炸）。
     */
    var _engLimitLastSession = null;
    var _engLimitTickCount = 0;

    /**
     * 【缺陷4 修复】取"当前工程模式会话 id"。
     *
     * 降级卡片没有现成的 session 对象（它是从 catch 分支直接调用的），
     * 因此需要一个能从模块态回查当前会话的入口：
     *   ① 优先用 tick 记录的最后一次会话（会话列表订阅时写入）；
     *   ② 退回宿主右栏/mainView 的当前会话；
     *   ③ 都取不到则返回 null（_sessionIsEngineering 会安全返回 false）。
     *
     * 为什么不让降级卡片直接读 _isEngineeringContext()：
     *   那个判据要求"该会话已有子代理"，而"刚进工程模式还没建子代理"
     *   恰恰是最需要降级的时刻 —— 用它会把降级卡片一起挡掉。
     */
    function _engLimitCurrentSessionId() {
      try {
        if (_engLimitLastSession) return _engLimitLastSession;
      } catch (e) { /* ignore */ }
      // ② 从会话列表快照里找 active 会话（与 startEngLimitAutoTrigger 同口径）
      try {
        var listSvc = engService('sessions');
        var list = listSvc && listSvc.list ? listSvc.list : null;
        var snap = list && typeof list.getSnapshot === 'function'
          ? list.getSnapshot() : null;
        if (snap) {
          if (snap.activeId) return snap.activeId;
          if (snap.activeSessionId) return snap.activeSessionId;
          if (snap.byId) {
            var ids = Object.keys(snap.byId);
            for (var i = 0; i < ids.length; i++) {
              var r = snap.byId[ids[i]];
              if (r && (r.active || r.isActive)) return ids[i];
            }
          }
        }
      } catch (e) { /* ignore */ }
      return null;
    }

    // ── 【弹窗修复 · 第2轮】同一会话允许【失败后重试】，成功后才封顶 ────────
    // 现场问题：「我记得里面搞了一个弹窗的，但是没搞出来」。
    // 根因：旧实现一 tick 就写 _engLimitLastSession，无论成败 →
    //   apply() 那一刻会话往往【还没被保留】（sessions.scope 拿不到）、
    //   document.body 也可能还没就绪 → 第一次注定失败，
    //   而这一次失败就把该会话的【唯一机会】烧掉了，之后再也不弹。
    // 现在：失败计入 _engLimitTries，最多重试 3 次（仍是防轰炸）；
    //   成功一次即封顶（值为 3），后续 tick 一律放行不再弹。
    var _engLimitTries = {};
    function engLimitAutoTick(sessionId) {
      try {
        _engLimitTickCount++;
        if (!sessionId) return false;
        if (_engLimitNeverShow()) return false;
        var tried = _engLimitTries[sessionId] || 0;
        if (tried >= 3) return false;          // 成功过 或 已用完重试额度
        var same = (_engLimitLastSession === sessionId);
        if (same && tried === 0) return false; // 已弹过，不再打扰
        var ok = tryAutoOpenEngLimitPopup(sessionId);
        _engLimitTries[sessionId] = ok ? 3 : (tried + 1);
        if (!same) _engLimitLastSession = sessionId;
        return ok;
      } catch (e) {
        return false;
      }
    }

    /**
     * 【弹窗修复 · 手动入口】用户要求「弹窗必须能出来」。
     *
     * 自动弹出依赖会话列表订阅 + 保留 scope，任一时序不巧就静默失效。
     * 这里给一条【永远可用】的确定性路径：设置页里一个按钮 →
     *   ① 先试官方弹窗外壳（popupFor().open）；
     *   ② 失败则直接渲染自绘降级卡片（同一套两按钮语义）。
     * 返回 true 表示用户至少【看得见】这个提醒。
     */
    function showEngLimitReminder(session) {
      try {
        var sid = (session && session.sessionId) || _engLimitCurrentSessionId();
        // 先给一次自动通道的机会（会写 _engLimitTried/_engLimitTries）
        try { if (tryAutoOpenEngLimitPopup(sid, session)) return true; } catch (e) {}
        return showEngLimitFallbackCard(session || { sessionId: sid }, true);
      } catch (e) {
        return false;
      }
    }

    /**
     * 订阅会话列表，把触发源接上。返回 disposer。
     * 用 subscribe（若可用）+ 立即取一次快照，兼容两种服务形态。
     */
    function startEngLimitAutoTrigger() {
      var listSvc = null;
      try { listSvc = engService('sessions'); } catch (e) { listSvc = null; }
      var list = listSvc && listSvc.list ? listSvc.list : null;
      if (!list) return null;

      var pickActive = function () {
        try {
          var snap = typeof list.getSnapshot === 'function' ? list.getSnapshot() : null;
          if (!snap) return null;
          // 优先官方 active 标记；否则退回"最后一个会话"
          if (snap.activeId) return snap.activeId;
          if (snap.activeSessionId) return snap.activeSessionId;
          if (snap.byId) {
            var ids = Object.keys(snap.byId);
            for (var i = 0; i < ids.length; i++) {
              var r = snap.byId[ids[i]];
              if (r && (r.active || r.isActive)) return ids[i];
            }
            return ids.length ? ids[ids.length - 1] : null;
          }
          return null;
        } catch (e) { return null; }
      };

      var fire = function () { engLimitAutoTick(pickActive()); };

      var dispose = null;
      try {
        if (typeof list.subscribe === 'function') {
          dispose = list.subscribe(fire);
        } else if (typeof list.watch === 'function') {
          dispose = list.watch(fire);
        }
      } catch (e) { dispose = null; }
      // 无论如何先跑一次（会话已经打开的情况下也要能触发）
      fire();
      return function () { try { if (typeof dispose === 'function') dispose(); } catch (e) {} };
    }
    exports.engLimitAutoTick = engLimitAutoTick;
    exports.startEngLimitAutoTrigger = startEngLimitAutoTrigger;
    // 【弹窗修复】手动入口（设置页「工程环境检查」里的「立即查看」按钮用它）
    exports.showEngLimitReminder = showEngLimitReminder;
    exports._sessionIsEngineering = _sessionIsEngineering;
    exports.showEngLimitFallbackCard = showEngLimitFallbackCard;
    exports.installEngLimitPopup = installEngLimitPopup;
    exports.tryAutoOpenEngLimitPopup = tryAutoOpenEngLimitPopup;
    exports.ENG_LIMIT_CMD = ENG_LIMIT_CMD;
    exports.ENG_LIMIT_TITLE = ENG_LIMIT_TITLE;
    exports.ENG_LIMIT_DISMISS_KEY = ENG_LIMIT_DISMISS_KEY;
    exports._engLimitOptions = _engLimitOptions;
    exports._engLimitNeverShow = _engLimitNeverShow;
    exports._engLimitSetNever = _engLimitSetNever;

    exports.apply = apply;
    // ⚠️ 'commandUi' 必须列进 inject：没有该服务的 profile 里插件保持不激活，
    //    而不是在注册时抛错（官方插件授权规则；见 NOTES-commandUi-api.md §9）。
    exports.inject = ['sessions', 'slots', 'commandUi'];
    exports.SubagentConsole = SubagentConsole;
    exports.EngineeringSettingsSection = EngineeringSettingsSection;
    exports.EngGroup = EngGroup;
    exports.EngRow = EngRow;
    exports.EngConnRow = EngConnRow;
    exports.EngWebotsSnapshot = EngWebotsSnapshot;
    exports.EngWebotsReport = EngWebotsReport;
    exports.EngWebotsPanel = EngWebotsPanel;
    // 【工程环境检查】栏目（设置页「连接区」上方）
    exports.EngEnvPanel = EngEnvPanel;
    exports.EngStatusChip = EngStatusChip;
    // 【需求1】模式说明 / 如何使用弹窗（供回归测试与外部复用）
    exports.EngGuideDialog = EngGuideDialog;
    exports.ENG_GUIDE = ENG_GUIDE;
    // 【需求1】预设卡片入口：弹窗宿主 + DOM 注入器（测试可单独调用）
    exports.EngPresetGuideHost = EngPresetGuideHost;
    // 【诊断】注入器状态（可在浏览器 console 里调：
    //   window.__dshEngPresetGuide() 查看锚点是否找到、按钮是否注入）
    exports.presetGuideInjectorStatus = presetGuideInjectorStatus;
    exports.injectPresetGuideButtons = injectPresetGuideButtons;
    exports.startPresetGuideInjector = startPresetGuideInjector;
    exports.EngSwitch = EngSwitch;
    // 【2026-11-07】零件进度表（子代理面板红线下方）—— 导出便于单测直接渲染
    // 【布局 v12】EngCompactPanel → EngCompactPage（右列整页）；旧名保留别名，
    //   避免外部/测试还在按老名字取；真正的渲染入口只认 EngCompactPage。
    exports.EngCompactPage = EngCompactPage;
    exports.EngCompactPanel = EngCompactPage;
    exports.EngSessionViewPage = EngSessionViewPage;
    exports.EngPartsBoard = EngPartsBoard;
    exports.SubagentDock = SubagentConsole;
    exports.formatMessage = formatMessage;
    exports.renderEvent = renderEvent;
    exports.AskCard = AskCard;
    exports.parseQuestions = parseQuestions;
    return module.exports;
  }
});
