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
          // ── 【独立聊天页】用户气泡 ──────────────────────────────────────
          // 原实现没有这个分支，因此右侧面板只显示助手侧内容。
          // 纯聊天面必须成对显示「用户问 / 助手答」，故补上。
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
        'background:#fff;border-left:1px solid #cbd5e1;box-sizing:border-box;color:#0f172a;' +
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
      '.eng-tree{background:#f1f5f9;border-right:1px solid #cbd5e1;box-sizing:border-box;' +
        'display:flex;flex:0 0 190px;flex-direction:column;height:100%;' +
        'min-width:0;overflow:hidden}',
      '.eng-tree-cap{' +
        'align-items:center;border-bottom:1px solid #cbd5e1;color:#334155;' +
        'display:flex;flex:none;font-size:12px;font-weight:700;gap:6px;' +
        'justify-content:space-between;letter-spacing:.02em;padding:9px 10px}' +
      '.eng-tree-list{flex:1;min-height:0;overflow-y:auto;padding:8px}',
      '.eng-node{align-items:center;background:#fff;border:1px solid #cbd5e1;border-radius:8px;box-sizing:border-box;color:#334155;cursor:pointer;display:flex;font-size:12px;font-weight:600;gap:7px;margin:0 0 6px;padding:8px 9px;text-align:left;width:100%}',
      '.eng-node:hover{background:#e2e8f0}',
      '.eng-node.on{background:#1d4ed8;border-color:#1d4ed8;color:#fff}',
      '.eng-dot{border-radius:50%;flex:none;height:8px;width:8px;background:#94a3b8}',
      '.eng-dot.run{background:#16a34a;animation:eng-pulse 1.3s ease-in-out infinite}',
      '.eng-dot.done{background:#0ea5e9}',
      '@keyframes eng-pulse{0%,100%{opacity:1}50%{opacity:.25}}',
      '.eng-node-label{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
      '.eng-node-state{flex:none;font-size:10px;opacity:.75}',
      '.eng-detail{background:#fff;box-sizing:border-box;display:flex;flex:1 1 0;flex-direction:column;' +
        'height:100%;min-width:0;overflow:hidden}',
      '.eng-head{align-items:center;background:#f8fafc;border-bottom:1px solid #cbd5e1;' +
        'display:flex;flex:none;gap:6px;min-height:44px;padding:0 8px 0 10px}',
      '.eng-name{color:#0f172a;flex:1;font-size:13px;font-weight:700;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
      '.eng-tag{background:#e2e8f0;border-radius:5px;color:#475569;flex:none;font-size:10px;font-weight:600;padding:2px 7px;white-space:nowrap}',
      '.eng-tag.run{background:#16a34a;color:#fff}',
      '.eng-x{background:#fff;border:1px solid #cbd5e1;border-radius:6px;color:#475569;cursor:pointer;flex:none;font-size:13px;font-weight:700;height:24px;line-height:1;width:24px}',
      '.eng-x:hover{background:#e2e8f0}',
      // ── 【排版优化】日志区：舒适的行高/字号 + 强制换行，避免"堆砌代码块"感 ──
      '.eng-log{' +
        'flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;' +
        'padding:12px 14px 18px;' +
        'line-height:1.75;' +                 // 舒适行高，长段落更易读
        'font-size:12.5px;' +                 // 略大字号，不再是密集小字
        'overflow-wrap:break-word;' +         // 长单词断行
        'word-break:break-word;' +            // 兼容旧浏览器
        'letter-spacing:.01em' +
      '}' +
      // 统一各类文本块：换行、行宽、间距
      '.eng-log p,.eng-log div{overflow-wrap:break-word;word-break:break-word;max-width:100%}',
      '.eng-log *{box-sizing:border-box}',
      // 段落间距：避免大段文字挤成一团
      '.eng-bubble,.eng-think,.eng-err-card,.eng-tool-card{margin-bottom:9px}',
      '.eng-bubble:last-child,.eng-think:last-child{margin-bottom:0}',
      '.eng-note{color:#64748b;font-size:12px;padding:18px 14px;text-align:center}',
      // ── 聊天气泡 ─────────────────────────────────────────────
      // 【布局重构】气泡：pre-wrap 保留换行 + 任意位置断行，长串不溢出
      '.eng-bubble{border-radius:10px;margin:0 0 9px;padding:8px 12px;font-size:12px;line-height:1.6;' +
        'white-space:pre-wrap;word-break:break-word;overflow-wrap:anywhere;max-width:100%;' +
        'background:#eff6ff;border:1px solid #bfdbfe;color:#1e3a5f}',
      '.eng-bubble.sys{background:#f8fafc;border-color:#e2e8f0;color:#475569}',
      // ── 思考引用框 ───────────────────────────────────────────
      // 【排版优化】思考框：取消斜体（用户反馈大段斜体阅读体验差），
      // 改用左侧竖线 + 稍淡的文字色来区分，保持可读性。
      '.eng-think{border-left:3px solid #cbd5e1;color:#64748b;font-size:12px;font-style:normal;line-height:1.68;' +
        'margin:0 0 9px;padding:6px 0 6px 10px;white-space:pre-wrap;word-break:break-word;' +
        'overflow-wrap:anywhere;max-width:100%;text-align:left}',
      // ── 工具卡 ───────────────────────────────────────────────
      '.eng-tool-card{background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;margin:0 0 7px;' +
        'overflow:hidden;max-width:100%;min-width:0}',
      '.eng-tool-card-h{align-items:center;color:#0369a1;cursor:pointer;display:flex;font-size:11.5px;font-weight:600;gap:6px;padding:6px 10px;user-select:none}',
      '.eng-tool-card-h:hover{background:#e0f2fe}',
      '.eng-tool-txt{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
      '.eng-tool-caret{flex:none;font-size:10px;opacity:.55}',
      // 【布局重构】代码块/工具输出：强制换行 + 横向滚动兜底，杜绝溢出边框
      '.eng-tool-card-b{background:#fff;border-top:1px solid #bae6fd;color:#334155;' +
        'font-family:ui-monospace,Consolas,monospace;font-size:11px;max-height:200px;' +
        'overflow:auto;padding:7px 10px;white-space:pre-wrap;word-break:break-word;' +
        'overflow-wrap:anywhere;max-width:100%}',
      // ── 错误告警卡 ───────────────────────────────────────────
      '.eng-err-card{align-items:flex-start;background:#fef2f2;border:1px solid #fca5a5;border-radius:8px;' +
        'color:#991b1b;display:flex;font-size:11.5px;gap:7px;line-height:1.5;margin:0 0 8px;padding:7px 10px;' +
        'word-break:break-word;overflow-wrap:anywhere;max-width:100%;min-width:0}',
      '.eng-err-card>*{min-width:0;overflow-wrap:anywhere}',
      '.eng-err-ico{flex:none}',
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
      // ══ 【PhaseB·第2项】确认卡片表格化 + 倒计时 ═══════════════════════
      // 用户反馈：步步确认时选项密密麻麻，需要更清爽的排版。
      // 方案：选项改为【表格行】布局（序号 | 单选标记 | 文案 | 说明），
      //   行距与分隔线让信息分层；顶部右侧放倒计时，超时自动选第一项。
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
      '.eng-ask-td-tag{background:#e0e7ff;border-radius:5px;color:#4338ca;flex:none;font-size:9.5px;font-weight:700;padding:2px 6px}',
      '.eng-ask-tr.on .eng-ask-td-tag{background:#4f46e5;color:#fff}',
      // ── 倒计时徽标（置于卡片标题栏右侧）──
      '.eng-cd{align-items:center;background:#eef2ff;border:1px solid #c7d2fe;border-radius:20px;color:#4338ca;display:inline-flex;flex:none;font-size:10.5px;font-weight:700;gap:4px;margin-left:auto;padding:2px 9px;transition:background .2s,color .2s}',
      '.eng-cd.warn{background:#fef3c7;border-color:#fcd34d;color:#92400e}',
      '.eng-cd.hot{background:#fee2e2;border-color:#fca5a5;color:#b91c1c;animation:eng-pulse 1s ease-in-out infinite}',
      '.eng-cd-num{font-variant-numeric:tabular-nums;min-width:16px;text-align:center}',
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
      '.eng-console>.eng-tree{min-width:0;flex:0 0 190px}' +
      '.eng-console>.eng-detail{min-width:0;flex:1 1 0}' +
      '.eng-console>*{min-width:0;max-width:100%}' +
      '.eng-detail>*,.eng-tree>*{min-width:0;max-width:100%}' +
      '.eng-log,.eng-tool-card-b,.eng-tree-list{overscroll-behavior:contain}' +
      '.eng-tree-list{overflow-x:hidden}' +
      // 面板内横向永不滚动：任何内容都纵向消化（25% 宽的硬约束）
      '.eng-detail{overflow-x:hidden}' +

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
      // 有子代理在运行时切换为绿色，一眼可辨
      '.eng-fab.run{background:linear-gradient(180deg,#22c55e,#16a34a);border-color:#16a34a}' +
      '.eng-fab.run:hover{background:linear-gradient(180deg,#16a34a,#15803d);box-shadow:-3px 0 16px rgba(22,163,74,.34)}' +
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

      // ══ 【独立聊天页】纯聊天面样式 ══════════════════════════════════════
      // 设计原则（对齐官方观感，见 packages/client/AGENTS.md 的样式规则）：
      //   · 只用 --dsw-alias-* 语义 token，不写死颜色 → 自动跟随亮/暗主题；
      //   · 内容宽度复用官方变量 --dsh-chat-content-width，让气泡与官方
      //     对话区同宽对齐，切换视图时不会"跳宽度"；
      //   · 不覆盖任何官方类名：本视图自带 eng-chat-* 前缀，零外溢。
      '.eng-chat{' +
        'box-sizing:border-box;display:flex;flex-direction:column;' +
        'gap:10px;height:100%;min-height:0;overflow-y:auto;' +
        'padding:18px calc(var(--dsh-composer-side-clearance, 24px) + 16px) 8px;' +
        'scroll-behavior:smooth' +
      '}' +
      // 气泡行：用户靠右、助手靠左（网页端聊天的基本观感）
      '.eng-chat-row{display:flex;width:100%;max-width:var(--dsh-chat-content-width,760px);margin:0 auto}' +
      '.eng-chat-row.me{justify-content:flex-end}' +
      '.eng-chat-row.bot{justify-content:flex-start}' +
      '.eng-chat-bubble{' +
        'border-radius:12px;box-sizing:border-box;font-size:14px;' +
        'line-height:calc(24px + var(--dsh-content-font-delta, 0px));' +
        'max-width:80%;overflow-wrap:anywhere;padding:9px 13px;white-space:pre-wrap;' +
        'word-break:break-word' +
      '}' +
      '.eng-chat-row.me .eng-chat-bubble{' +
        'background:var(--dsw-alias-state-business-tertiary, #eef2ff);' +
        'color:var(--dsw-alias-label-primary)' +
      '}' +
      '.eng-chat-row.bot .eng-chat-bubble{' +
        'background:var(--dsw-alias-bg-layer-2, #f8fafc);' +
        'border:1px solid var(--dsw-alias-border-l2, #e2e8f0);' +
        'color:var(--dsw-alias-label-primary)' +
      '}' +
      '.eng-chat-typing{' +
        'color:var(--dsw-alias-label-tertiary, #8b95a3);font-size:13px;' +
        'margin:2px auto;max-width:var(--dsh-chat-content-width,760px);width:100%' +
      '}' +
      '.eng-chat-err{' +
        'background:var(--dsw-alias-state-error-tertiary, #fef2f2);border-radius:10px;' +
        'color:var(--dsw-alias-state-error-primary, #991b1b);font-size:13px;' +
        'margin:8px auto;max-width:var(--dsh-chat-content-width,760px);padding:10px 12px;width:100%' +
      '}' +
      // ── 推荐追问（对照用户图二：助手消息下方一列「↳ 问题」）──────────
      '.eng-chat-sug{' +
        'display:flex;flex-direction:column;gap:2px;' +
        'margin:2px auto 4px;max-width:var(--dsh-chat-content-width,760px);width:100%' +
      '}' +
      '.eng-chat-sug-item{' +
        'align-items:center;background:transparent;border:0;border-radius:8px;' +
        'color:var(--dsw-alias-label-secondary, #475569);cursor:pointer;' +
        'display:flex;font-family:inherit;font-size:13.5px;gap:9px;' +
        'padding:8px 10px;text-align:left;width:100%' +
      '}' +
      '.eng-chat-sug-item:hover{' +
        'background:var(--dsw-alias-interactive-bg-hover, rgba(15,23,42,.05));' +
        'color:var(--dsw-alias-label-primary)' +
      '}' +
      '.eng-chat-sug-ico{color:var(--dsw-alias-label-tertiary,#94a3b8);flex:none}' +
      '.eng-chat-sug-more{' +
        'align-self:flex-start;background:transparent;border:0;border-radius:7px;' +
        'color:var(--dsw-alias-label-tertiary,#8b95a3);cursor:pointer;' +
        'font-family:inherit;font-size:12px;margin-left:27px;padding:4px 8px' +
      '}' +
      '.eng-chat-sug-more:hover{background:var(--dsw-alias-interactive-bg-hover,rgba(15,23,42,.05))}' +
      // 说明：起步建议（eng-chat-starter）的样式统一在下方
      //   【视图切换器 + 起步建议】区块里定义，此处不再重复。',

      // ══ 【视图切换器 + 起步建议】布局 ══════════════════════════════════
      // 两个落点（对应用户给的截图里的红框 / 蓝框）：
      //   · 红框 = 输入框【上方】 → conversation.input.dock
      //   · 蓝框 = 输入框【下方】 → 起步建议卡片
      //
      // 官方插槽会被 ui-renderer 包成 <div data-slot="<key>"
      //   style="display:contents">（ui-renderer/src/client/scoped-slots.tsx
      //   :1081-1091）。display:contents 让这层壳不占布局，因此我可以直接用
      //   [data-slot=...] 选择器给【我的内容】定位，而不会影响官方其它住户。
      //   ⚠️ 只针对我自己的 .eng-vs / .eng-chat-starter 设置样式，
      //      绝不给插槽壳或官方元素写样式（避免污染官方布局）。
      //
      // 切换器：居中、胶囊形、两段（对照用户图一的 segmented control）。
      '.eng-vs{' +
        'align-items:center;background:var(--dsw-alias-bg-layer-2,rgba(15,23,42,.06));' +
        'border-radius:999px;box-sizing:border-box;display:flex;gap:2px;' +
        'margin:0 auto 2px;padding:3px;width:fit-content' +
      '}' +
      '.eng-vs-item{' +
        'background:transparent;border:0;border-radius:999px;color:var(--dsw-alias-label-secondary,#475569);' +
        'cursor:pointer;font-family:inherit;font-size:13px;font-weight:500;' +
        'line-height:1;padding:7px 18px;transition:background .15s,color .15s' +
      '}' +
      '.eng-vs-item:hover{color:var(--dsw-alias-label-primary)}' +
      '.eng-vs-item.on{' +
        'background:var(--dsw-alias-bg-layer-1,#fff);' +
        'box-shadow:0 1px 3px rgba(15,23,42,.12);' +
        'color:var(--dsw-alias-label-primary)' +
      '}' +
      '.eng-vs-item.busy{opacity:.6;cursor:default}' +
      // 切换失败提示：不静默失败，用户必须看得见原因
      '.eng-vs-err{' +
        'color:var(--dsw-alias-state-error-primary,#b91c1c);font-size:12px;' +
        'line-height:1.6;margin:2px auto 0;max-width:var(--dsh-composer-card-max-width,760px);' +
        'padding:0 4px;width:100%' +
      '}' +
      // 起步建议卡片：位于输入框【下方】，宽度与输入卡对齐
      //   （复用官方变量 --dsh-composer-card-max-width，让它与输入框同宽）。
      '.eng-chat-starter{' +
        'box-sizing:border-box;display:flex;flex-direction:column;gap:6px;' +
        'margin:10px auto 0;max-width:var(--dsh-composer-card-max-width,760px);' +
        'padding:0 var(--dsh-composer-side-clearance,0);width:100%' +
      '}' +
      '.eng-chat-starter-list{display:flex;flex-direction:column;gap:5px}' +
      '.eng-chat-starter-item{' +
        'align-items:center;background:var(--dsw-alias-bg-layer-2,rgba(15,23,42,.03));' +
        'border:1px solid var(--dsw-alias-border-l2,transparent);border-radius:10px;' +
        'color:var(--dsw-alias-label-secondary,#334155);cursor:pointer;' +
        'display:flex;font-family:inherit;font-size:13.5px;gap:9px;' +
        'padding:10px 13px;text-align:left;width:100%' +
      '}' +
      '.eng-chat-starter-item:hover{' +
        'border-color:var(--dsw-alias-state-business-primary,#2563eb);' +
        'color:var(--dsw-alias-label-primary)' +
      '}' +
      '.eng-chat-starter-ico{flex:none;font-size:14px}' +
      // ── 推荐追问（图二）：一轮结束后显示，点一下填进输入框 ──────────────
      // 视觉对齐截图：左侧「↳」箭头 + 灰字，整行可点，无边框。
      '.eng-sugg{' +
        'display:flex;flex-direction:column;gap:1px;' +
        'margin:2px auto 4px;max-width:var(--dsh-composer-card-max-width,760px);' +
        'padding:0 var(--dsh-composer-side-clearance,0);width:100%' +
      '}' +
      '.eng-sugg-item{' +
        'align-items:center;background:transparent;border:0;border-radius:8px;' +
        'color:var(--dsw-alias-label-secondary,#475569);cursor:pointer;' +
        'display:flex;font-family:inherit;font-size:13.5px;gap:9px;' +
        'padding:8px 10px;text-align:left;width:100%' +
      '}' +
      '.eng-sugg-item:hover{' +
        'background:var(--dsw-alias-interactive-bg-hover,rgba(15,23,42,.05));' +
        'color:var(--dsw-alias-label-primary)' +
      '}' +
      '.eng-sugg-ico{color:var(--dsw-alias-label-tertiary,#94a3b8);flex:none}' +
      // ── 【一键发送给Agent建模】操作行按钮 ────────────────────────────
      // 与官方那排图标按钮并排，所以尺寸克制、样式贴近同排控件；
      //   但用文字而非纯图标 —— 用户要求它必须看得见。
      '.eng-msg-act-wrap{align-items:center;display:inline-flex;gap:6px}' +
      '.eng-msg-act{' +
        'background:transparent;border:1px solid var(--dsw-alias-border-l2,#d0d5dd);' +
        'border-radius:6px;color:var(--dsw-alias-label-secondary,#475569);' +
        'cursor:pointer;font-family:inherit;font-size:11.5px;line-height:1;' +
        'padding:4px 8px;white-space:nowrap' +
      '}' +
      '.eng-msg-act:hover:not(:disabled){' +
        'background:var(--dsw-alias-interactive-bg-hover,rgba(15,23,42,.05));' +
        'border-color:var(--dsw-alias-state-business-primary,#2563eb);' +
        'color:var(--dsw-alias-state-business-primary,#2563eb)' +
      '}' +
      '.eng-msg-act:disabled{color:var(--dsw-alias-label-tertiary,#94a3b8);cursor:default}' +
      '.eng-msg-act.done{' +
        'border-color:var(--dsw-alias-state-success-primary,#16a34a);' +
        'color:var(--dsw-alias-state-success-primary,#16a34a)' +
      '}' +
      '.eng-msg-act-err{color:var(--dsw-alias-state-error-primary,#dc2626);font-size:11px}' +
      // 深色模式下的切换器底色
      '@media(prefers-color-scheme:dark){' +
        '.eng-vs{background:rgba(255,255,255,.08)}' +
        '.eng-vs-item.on{background:rgba(255,255,255,.16);box-shadow:none}' +
      '}',

      // ── 【关键】把切换器排到输入框上方、起步建议排到下方 ──────────────
      // 官方结构：.composerStack 里依次是 [HeroShell] [workspaceRow]
      //   [data-slot="conversation.input.dock"] [data-slot="…composer.bar"]。
      //
      // ⚠️ 选择器必须用【后代】而非 `>` 直接子级：
      //   我的元素在 DOM 上嵌套了两层 display:contents 包装
      //   （slot 壳 + .eng-dock-host），`>` 按 DOM 树匹配、会落空；
      //   而后代选择器搭配 order 是安全的 —— order 只对 flex 子项生效，
      //   非 flex 子项会忽略它，不会误伤官方其它元素。
      //     .eng-vs            → order 0（输入框上方 = 用户红框）
      //     [data-slot="…composer.bar"] → order 2（输入框本体，DOM 直接子级）
      //     .eng-chat-starter  → order 3（输入框下方 = 用户蓝框）
      // :has() 限定「只有本插件在场时才重排」，插件不出现时官方顺序完全不变。
      '.composerStack:has(.eng-dock-host){display:flex;flex-direction:column}' +
      '.composerStack .eng-vs{order:0}' +
      '.composerStack .eng-vs-err{order:1}' +
      '.composerStack > [data-slot="conversation.composer.bar"]{order:2}' +
      '.composerStack .eng-sugg{order:3}' +
      '.composerStack .eng-chat-starter{order:4}',

      // ══ 【划词详情】浮层按钮 + 右下角分析面板 ══════════════════════════
      // 全部使用 eng-sel-* / eng-side-* 前缀，不触碰任何官方类名。
      // 只用 --dsw-alias-* 语义 token → 自动跟随亮/暗主题。
      '.eng-sel-btn{' +
        'background:var(--dsw-alias-bg-layer-1,#fff);' +
        'border:1px solid var(--dsw-alias-border-l2,#d0d5dd);border-radius:8px;' +
        'box-shadow:0 4px 14px rgba(15,23,42,.16);color:var(--dsw-alias-label-primary,#111827);' +
        'cursor:pointer;font-family:inherit;font-size:12.5px;font-weight:600;' +
        'padding:6px 12px;position:fixed;z-index:60;white-space:nowrap' +
      '}' +
      '.eng-sel-btn:hover{background:var(--dsw-alias-interactive-bg-hover,rgba(15,23,42,.06))}' +
      // 面板（图三）：右下角固定。宽高由【用户拖拽】决定（.eng-side 上以
      //   内联 style 写入），因此这里只给最大约束，不再写死宽度。
      '.eng-side{' +
        'background:var(--dsw-alias-bg-layer-1,#fff);' +
        'border:1px solid var(--dsw-alias-border-l2,#d0d5dd);border-radius:14px;' +
        'bottom:18px;box-shadow:0 10px 34px rgba(15,23,42,.20);' +
        'box-sizing:border-box;color:var(--dsw-alias-label-primary,#111827);' +
        'display:flex;flex-direction:column;font-size:13px;' +
        'max-height:calc(100vh - 36px);max-width:calc(100vw - 36px);' +
        'position:fixed;right:18px;z-index:55;overflow:hidden' +
      '}' +
      // 左上角拖拽手柄（用户要求能自由放大放小）
      '.eng-side-grip{' +
        'align-items:center;color:var(--dsw-alias-label-tertiary,#94a3b8);' +
        'cursor:nwse-resize;display:flex;font-size:13px;height:22px;' +
        'justify-content:center;left:0;position:absolute;top:0;' +
        'touch-action:none;user-select:none;width:22px;z-index:2' +
      '}' +
      '.eng-side-grip:hover{color:var(--dsw-alias-state-business-primary,#2563eb)}' +
      // ── 【一键发送给工作模式建模】按钮 ──────────────────────────────
      // ⚠️ flex:none 是【必需】的，不是可选优化：
      //   面板是固定高度 + overflow:hidden 的 flex 列。此块若保持默认
      //   flex-shrink:1，在内容变多时会被压缩到 0 高并被裁掉 —— 表现正是
      //   "按钮不见了"。同理列表要能滚动必须 min-height:0（见 .eng-side-list）。
      '.eng-side-work{flex:none;padding:0 10px 8px}' +
      '.eng-side-workbtn{' +
        'background:var(--dsw-alias-state-business-primary,#2563eb);border:0;' +
        'border-radius:9px;color:#fff;cursor:pointer;font-family:inherit;' +
        'font-size:13px;font-weight:600;padding:9px 12px;width:100%' +
      '}' +
      '.eng-side-workbtn:hover:not(:disabled){filter:brightness(1.08)}' +
      '.eng-side-workbtn:disabled{background:var(--dsw-alias-bg-layer-2,#e5e7eb);color:var(--dsw-alias-label-tertiary,#94a3b8);cursor:default}' +
      '.eng-side-ok{color:var(--dsw-alias-state-success-primary,#16a34a);font-size:12px;padding-top:6px;text-align:center}' +
      '.eng-side-hd{' +
        'align-items:center;background:var(--dsw-alias-bg-layer-2,rgba(15,23,42,.03));' +
        'border-bottom:1px solid var(--dsw-alias-border-l2,#e5e7eb);' +
        'display:flex;flex:none;gap:7px;padding:9px 10px' +
      '}' +
      '.eng-side-ico{flex:none;font-size:14px}' +
      '.eng-side-title{flex:1;font-size:13px;font-weight:700;min-width:0}' +
      '.eng-side-badge{' +
        'background:var(--dsw-alias-state-business-tertiary,#eef2ff);border-radius:6px;' +
        'color:var(--dsw-alias-label-primary-bluish,#3730a3);flex:none;' +
        'font-size:10.5px;font-weight:700;padding:2px 7px' +
      '}' +
      '.eng-side-x{' +
        'background:transparent;border:0;border-radius:6px;color:var(--dsw-alias-label-tertiary,#6b7280);' +
        'cursor:pointer;flex:none;font-size:16px;line-height:1;padding:2px 6px' +
      '}' +
      '.eng-side-x:hover{background:var(--dsw-alias-interactive-bg-hover,rgba(15,23,42,.06))}' +
      // 选中原文摘要：最多两行，超过省略
      '.eng-side-src{' +
        'background:var(--dsw-alias-bg-layer-2,rgba(15,23,42,.03));' +
        'border-bottom:1px solid var(--dsw-alias-border-l2,#e5e7eb);' +
        'color:var(--dsw-alias-label-tertiary,#6b7280);' +
        'display:-webkit-box;flex:none;font-size:11.5px;line-height:1.5;' +
        'overflow:hidden;padding:7px 11px;-webkit-box-orient:vertical;-webkit-line-clamp:2' +
      '}' +
      // min-height:0（而非 80px）是可滚动 flex 子项的正确写法：
      //   flex 子项默认 min-height:auto，会被内容撑开、把下面的按钮和输入框
      //   挤出固定高度的面板（配合 overflow:hidden 就是"看不见了"）。
      //   设成 0 才允许它收缩并在自身内部滚动。
      '.eng-side-list{display:flex;flex:1 1 auto;flex-direction:column;gap:8px;' +
        'min-height:0;overflow-y:auto;padding:10px 11px}' +
      '.eng-side-row{display:flex;flex-direction:column;gap:3px;max-width:100%}' +
      '.eng-side-row.me{align-items:flex-end}' +
      '.eng-side-row.bot{align-items:flex-start}' +
      '.eng-side-bubble{' +
        'border-radius:10px;font-size:12.5px;line-height:1.7;max-width:92%;' +
        'overflow-wrap:anywhere;padding:8px 11px;white-space:pre-wrap;word-break:break-word' +
      '}' +
      '.eng-side-row.me .eng-side-bubble{' +
        'background:var(--dsw-alias-state-business-tertiary,#eef2ff);' +
        'color:var(--dsw-alias-label-primary,#1e293b)' +
      '}' +
      '.eng-side-row.bot .eng-side-bubble{' +
        'background:var(--dsw-alias-bg-layer-2,#f8fafc);' +
        'border:1px solid var(--dsw-alias-border-l2,#e5e7eb);' +
        'color:var(--dsw-alias-label-primary,#1e293b)' +
      '}' +
      '.eng-side-acts{display:flex;gap:6px;padding-left:2px}' +
      '.eng-side-act{' +
        'background:transparent;border:1px solid var(--dsw-alias-border-l2,#d0d5dd);' +
        'border-radius:6px;color:var(--dsw-alias-label-secondary,#475569);' +
        'cursor:pointer;font-family:inherit;font-size:11px;padding:3px 9px' +
      '}' +
      '.eng-side-act:hover{' +
        'border-color:var(--dsw-alias-state-business-primary,#2563eb);' +
        'color:var(--dsw-alias-state-business-primary,#2563eb)' +
      '}' +
      '.eng-side-note{color:var(--dsw-alias-label-tertiary,#8b95a3);font-size:12px;padding:10px 2px;text-align:center}' +
      '.eng-side-err{' +
        'background:var(--dsw-alias-state-error-tertiary,#fef2f2);border-radius:8px;' +
        'color:var(--dsw-alias-state-error-primary,#b91c1c);font-size:11.5px;' +
        'line-height:1.6;overflow-wrap:anywhere;padding:7px 9px' +
      '}' +
      '.eng-side-ft{' +
        'align-items:center;border-top:1px solid var(--dsw-alias-border-l2,#e5e7eb);' +
        'display:flex;flex:none;gap:7px;padding:8px 10px' +
      '}' +
      '.eng-side-in{' +
        'background:transparent;border:0;color:var(--dsw-alias-label-primary,#111827);' +
        'flex:1;font-family:inherit;font-size:12.5px;min-width:0;outline:none;padding:5px 2px' +
      '}' +
      '.eng-side-send{' +
        'background:var(--dsw-alias-state-business-primary,#2563eb);border:0;border-radius:50%;' +
        'color:#fff;cursor:pointer;flex:none;font-size:14px;height:28px;line-height:1;' +
        'padding:0;width:28px' +
      '}' +
      '.eng-side-send:disabled{background:var(--dsw-alias-border-l2,#cbd5e1);cursor:default}',

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
        '.eng-detail,.eng-log{background:#16181d}' +
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
        var pane = Math.round(avail * 0.40);
        var mainMin = parseFloat(getComputedStyle(document.documentElement)
                       .getPropertyValue("--eng-main-min")) || 600;
        if (avail - pane < mainMin) {
          pane = Math.max(0, avail - mainMin);
        }
        if (pane < 240) return 0;
        return pane;
      } catch (e) { return 420; }
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
    // ══ 【问题1 修复 → Bug#1 强化】主对话原生问答卡片 —— 逐题独立倒计时 ══
    // 用户要求：卡片旁边显示倒计时，结束时自动选择默认『可以』。
    //
    // ── 【Bug#1 修复】旧实现只认"全局仅一组选项"，多题问卷直接不挂表 ────
    //   旧判据：`document.querySelectorAll('[class*="_options"]').length !== 1
    //            → continue`
    //   后果：17 题问卷（强需求路径）在主对话里【完全没有倒计时】；
    //   且一旦渲染出多组选项，连第一组的表都被跳过 —— 与"每题都要有
    //   独立倒计时"的要求直接矛盾。这就是测试反馈的"倒计时有时不出现"。
    //
    // ── 新设计：按【选项组】逐个挂表，每组一个独立计时器 ──────────────
    //   · 每个 [class*="_options"] 组 = 一道系统问题 → 各自一张倒计时表；
    //   · 多题时按 DOM 顺序串行推进：当前组的默认项被选后自动点
    //     「下一题/提交」，下一组出现后由 MutationObserver 自动挂新表；
    //   · 用户点过的那一组 → 该组停表（按组隔离，不串到别组）；
    //   · 已提交/已消失的卡片不再干预。
    //
    // 为什么用 DOM 注入而不是改组件：
    //   主对话问答由 DSH 官方包渲染（conversation.composer 插槽），
    //   本插件无法通过 props 介入。改用【非侵入 DOM 增强】：
    //     ① 监听原生卡片出现；
    //     ② 在其页脚注入倒计时徽标；
    //     ③ 倒计时结束 → 程序化点击第一个选项 + 提交按钮。
    //   全程不修改官方包代码，升级安全；且只在工程模式（ENG_MODE_CLASS）下运行。
    //
    // 安全约束：
    //   · 用户任意点击后立即停【该组】表；
    //   · 已提交/已消失的卡片不再干预；
    //   · 每组独立，互不影响（第1题被接管不会让第2题失去倒计时）。
    function installQuestionCountdown(seconds) {
      if (typeof document === 'undefined' || typeof MutationObserver !== 'function') return;
      var SEC = Number(seconds) || 10;   // 测试值，可通过参数调整
      // 【Bug#1 修复】WeakMap 按【选项组元素】记计时器 —— 支持多组并存
      var timers = new WeakMap();
      // 按组隔离的"已接管"标记，避免用户在第1题点击导致第2题失效
      var takenMap = new WeakMap();
      var COOLDOWN_MS = 15000;
      var lastFire = 0;

      var isEngMode = function () {
        try { return document.body.classList.contains(ENG_MODE_CLASS); } catch (e) { return false; }
      };

      // ── 【Bug#1 修复】收集页面上【所有】选项组（不再要求全局唯一）──────
      var findCards = function () {
        var out = [];
        var opts;
        try { opts = document.querySelectorAll('[class*="_options"]'); } catch (e) { return out; }
        for (var i = 0; i < opts.length; i++) {
          var card = opts[i];
          if (!card) continue;
          // 只处理仍挂在文档里的、且有可点选项的组
          if (!document.body.contains(card)) continue;
          var anyOpt = null;
          try { anyOpt = card.querySelector('[class*="_option"]'); } catch (e) { anyOpt = null; }
          if (!anyOpt) continue;
          out.push(card);
        }
        return out;
      };

      var stopAll = function () {
        try {
          var badges = document.querySelectorAll('.eng-qc-cd');
          for (var i = 0; i < badges.length; i++) {
            if (badges[i].parentNode) badges[i].parentNode.removeChild(badges[i]);
          }
        } catch (e) {}
      };

      // 找"下一题/提交"按钮：优先按文案匹配，再兜底最后一个可用按钮。
      var findNextButton = function () {
        var btns = [];
        try {
          btns = document.querySelectorAll(
            '[class*="_footer"] button, [class*="_footerActions"] button, [class*="_actions"] button');
        } catch (e) { btns = []; }
        for (var bi = 0; bi < btns.length; bi++) {
          var b = btns[bi];
          var txt = String(b.textContent || '');
          if (b.disabled) continue;
          if (/下一题|提交|确认|确定|继续|submit|next|ok/i.test(txt)) return b;
        }
        return null;
      };

      // ── 单组的挂表逻辑（每组一个独立计时器）─────────────────────────
      var attachOne = function (group) {
        if (timers.has(group)) return;   // 该组已挂表
        if (takenMap.has(group) && takenMap.get(group)) return;  // 该组已被用户接管

        // ── 注入倒计时徽标 ──
        var badge = document.createElement('div');
        badge.className = 'eng-qc-cd';
        badge.style.cssText = 'align-items:center;background:#eef2ff;border:1px solid #c7d2fe;' +
          'border-radius:20px;color:#4338ca;display:inline-flex;font-size:11px;font-weight:700;' +
          'gap:5px;margin:6px 0 0;padding:3px 10px;width:fit-content';
        var num = document.createElement('span');
        num.textContent = SEC + 's';
        num.style.fontVariantNumeric = 'tabular-nums';
        badge.appendChild(document.createTextNode('⏱ 倒计时 '));
        badge.appendChild(num);
        badge.appendChild(document.createTextNode(' 后自动选默认项'));
        try { group.parentNode.insertBefore(badge, group.nextSibling); } catch (e) {}

        var left = SEC;
        // 用户点击【本组】任一选项即停【本组】表
        var onPick = function () {
          takenMap.set(group, true);
        };
        try { group.addEventListener('click', onPick, true); } catch (e) {}

        var iv = setInterval(function () {
          if (!isEngMode() || !document.body.contains(group)) {
            clearInterval(iv); try { badge.remove(); } catch (e) {}
            try { timers.delete(group); } catch (e2) {}
            return;
          }
          left--;
          num.textContent = Math.max(0, left) + 's';
          if (left <= 3) { badge.style.background = '#fee2e2'; badge.style.borderColor = '#fca5a5'; badge.style.color = '#b91c1c'; }
          else if (left <= 5) { badge.style.background = '#fef3c7'; badge.style.borderColor = '#fcd34d'; badge.style.color = '#92400e'; }
          if (left > 0) return;
          clearInterval(iv);
          try { timers.delete(group); } catch (e2) {}
          // ── 【Bug#1 修复】归零瞬间再校验一次"本组是否已接管" ──────────
          //   用户可能恰好在同一瞬间点击 → 必须让人，绝不抢答。
          if (takenMap.get(group)) { try { badge.remove(); } catch (e) {} return; }
          if (Date.now() - lastFire < COOLDOWN_MS) { try { badge.remove(); } catch (e) {} return; }
          lastFire = Date.now();
          takenMap.set(group, true);
          // ── 自动选择本组第一项（默认项）并推进 ──
          try {
            var first = group.querySelector('[class*="_option"]');
            if (first && !first.disabled) {
              if (typeof first.click === 'function') first.click();
              // 等 React 提交选中态后再点「下一题/提交」
              setTimeout(function () {
                try {
                  var b = findNextButton();
                  if (b) b.click();
                } catch (e2) {}
              }, 250);
            }
          } catch (e3) {}
          try { badge.remove(); } catch (e) {}
        }, 1000);
        timers.set(group, iv);
      };

      var attach = function () {
        if (!isEngMode()) { stopAll(); return; }
        var cards = findCards();
        // 【Bug#1 修复】逐个挂表 —— 多题问卷的每一题都有各自独立的倒计时
        for (var ci = 0; ci < cards.length; ci++) attachOne(cards[ci]);
      };

      attach();
      var mo = new MutationObserver(function () { attach(); });
      mo.observe(document.body, { childList: true, subtree: true });
      return function () {
        try { mo.disconnect(); } catch (e) {}
        stopAll();
      };
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
    function Bubble(props) {
      return h('div', { className: 'eng-bubble' + (props.sys ? ' sys' : '') }, props.text);
    }
    function ThinkBox(props) {
      return h('div', { className: 'eng-think' }, props.text);
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
      // 【PhaseB】供倒计时回调调用 submit（submit 定义在下方，用 ref 打通时序）
      var submitRef = useRef(null);

      var submit = function (cause) {
        // 唯一合法触发源：用户点按钮 / 倒计时超时。其余一律不提交。
        if (cause !== 'user-click') {
          try { console.warn('[eng-ui] 拒绝非用户触发的提交:', cause); } catch (e) {}
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
          try { if (cdQid) cdTakenRef.current[cdQid] = true; } catch (e) {}
        }
        if (idx < sorted.length - 1) idxSt[1](idx + 1);
        else {
          stSt[1]('sending'); msgSt[1]('全部答完，提交中…'); submit('user-click');
        }
      };
      // ══ 【PhaseB·第2项 → Bug#1 修复】倒计时：每题独立 + 多题逐题自动推进 ══
      // 用户要求（本次明确）：
      //   · 倒计时必须是【每一条系统问题各自一个】—— 切到第 N 题就重新从
      //     COUNTDOWN_SEC 起表，上一题的剩余秒数绝不能带到下一题；
      //   · 多题问卷也要【逐题自动推进】：当前题超时 → 选中默认项(第一项)
      //     → 自动跳到下一题继续计时 → 直到全部答完再提交。
      //   · 修复"倒计时概率性在用户点选后仍自动提交"的竞态（见下方 takenOf）。
      //
      // ── 旧实现的两个缺陷（本次修复的根因）────────────────────────────
      //   ① 计时器依赖固定为 [cdOn]，而 cdOn 对多题恒为 false（旧版要求
      //      sorted.length === 1）→ 多题卡片根本没有倒计时；
      //   ② 即便单题，切题不会重建计时器 —— 读数沿用上一题的剩余值，
      //      表现为"第2题刚出现就只剩1~2秒"，正是用户说的"概率性"错乱。
      //
      // ── 新设计 ─────────────────────────────────────────────────────
      //   · 计时器依赖固定为 [cdOn, idx]（当前题序号）：切题即重建，
      //     每题都拿到完整 COUNTDOWN_SEC —— 这就是"一题一个倒计时"。
      //   · 自动推进走 nextQRef（与手动点击「下一题」同一条代码路径）。
      //   · 「用户已接管」用**按题隔离**的 takenOf 记录：用户在第1题的
      //     点击不能顺延成第2题已接管，反之亦然 —— 消除跨题串扰。
      //   · 用户任意一次真实点击都会停【当前题】的表（人已接管）。
      //   · 仅在【子代理面板】(asChild) 生效，主对话走 DSH 原生选项卡。
      //   · 多选(multi)题不自动作答（多选默认值语义不明确，留给用户）。
      var COUNTDOWN_SEC = 10;   // 【测试值】验证通过后可调大（如 120）
      var cdSt = useState(COUNTDOWN_SEC);
      var cdLeft = cdSt[0];
      var cdOn = false;
      var cdQid = null;
      try {
        cdQid = curQ ? curQ.id : null;
        cdOn = !!(props.asChild) && !!curQ && !curQ.multi &&
               !!(curQ.options && curQ.options.length) &&
               state !== 'sending' && state !== 'ok';
      } catch (e) { cdOn = false; }
      // ── 【Bug#1 修复·竞态】按题隔离的"已接管"标记 ────────────────────
      // 旧实现用单个 cdTakenRef 布尔：第1题用户点过之后它永久为 true，
      //   于是第2题的倒计时走到 0 也不会自动选（表现为"倒计时失效"）；
      //   更糟的是 effect 重建时机与点击时机交错时，会出现"用户刚点完
      //   却被自动提交"——这就是"概率性按到选项"的真相。
      // 现在改为对象，按 question.id 分别记录，切题天然互不影响。
      var cdTakenRef = useRef({});
      // 供倒计时回调跳题（nextQ 定义在下方，用 ref 打通时序）
      var nextQRef = useRef(null);
      nextQRef.current = nextQ;
      submitRef.current = submit;
      // 依赖固定为 [cdOn, idx]：
      //   · cdOn —— 该不该显示倒计时；
      //   · idx  —— 当前题序号。切题必须重建，否则读数串到下一题。
      //   刻意不放 state/sending/sel：每次 tick 重渲染都清表重来，
      //   倒计时永远走不到 0 —— 这是最容易踩的坑，务必保持依赖最小。
      useEffect(function () {
        if (!cdOn) return;
        cdSt[1](COUNTDOWN_SEC);                    // 每题挂载都重置读数
        // 【Bug#1 修复】进入新题时清掉【本题】的接管标记 ——
        //   切题 = 新的一题，理应有全新的倒计时与全新的默认项。
        try { if (cdQid) cdTakenRef.current[cdQid] = false; } catch (e) {}
        var t = setInterval(function () {
          cdSt[1](function (prev) {
            var next = prev - 1;
            if (next <= 0) {
              clearInterval(t);
              var _qid = cdQid;
              // ── 【Bug#1 修复】以"本题是否已接管"为准，且此刻再校验一次 ──
              //   用户可能恰好在倒计时归零的同一瞬间点击 → 必须让位于人。
              var _taken = false;
              try { _taken = !!cdTakenRef.current[_qid]; } catch (e) { _taken = false; }
              if (_taken || userTouched.current) return 0;
              try { cdTakenRef.current[_qid] = true; } catch (e) {}
              try {
                // 锁定"归零那一刻"的题，避免异步期间 idx 已变
                var q = null;
                for (var _i = 0; _i < sorted.length; _i++) {
                  if (sorted[_i] && sorted[_i].id === _qid) { q = sorted[_i]; break; }
                }
                if (!q) q = curQ;
                var first = (q && q.options && q.options[0]) || null;
                if (first) {
                  userTouched.current = true;
                  pick(q, first.label);
                  var _isLast = (idx >= sorted.length - 1);
                  if (_isLast) {
                    // ── 最后一题 → 等一帧让 sel 写入后提交（与手动点击同一通道）──
                    setTimeout(function () {
                      try {
                        stSt[1]('sending');
                        msgSt[1]('倒计时结束，已自动选择默认项：' + first.label);
                        if (submitRef.current) submitRef.current('user-click');
                      } catch (e2) {}
                    }, 80);
                  } else {
                    // ── 【Bug#1 新增】非最后一题 → 自动跳到下一题继续计时 ──
                    //   这就是"多题逐题自动推进"；走 nextQRef，与手动点
                    //   「下一题」完全同一条代码路径。
                    setTimeout(function () {
                      try {
                        msgSt[1]('倒计时结束，已自动选择默认项：' + first.label + '，进入下一题…');
                        if (nextQRef.current) nextQRef.current(true);
                      } catch (e2) {}
                    }, 80);
                  }
                }
              } catch (e3) {}
              return 0;
            }
            return next;
          });
        }, 1000);
        return function () { clearInterval(t); };
      }, [cdOn, idx]);
      var cdBadge = (cdOn && state !== 'ok')
        ? h('span', {
            className: 'eng-cd' + (cdLeft <= 3 ? ' hot' : (cdLeft <= 5 ? ' warn' : '')),
            title: '倒计时结束后将自动选择第一项（默认项）'
          },
            h('span', null, '⏱'),
            h('span', { className: 'eng-cd-num' }, String(cdLeft)),
            h('span', null, 's'))
        : null;

      // ── 【PhaseB·第2项】选项表格化渲染 ────────────────────────────────
      // 用户反馈：步步确认时选项密密麻麻。改为表格行（序号|标记|文案|说明），
      //   分层清晰；无 description 的行自动收窄留白，不再堆叠。
      var blocks = null;
      if (curQ) {
        var multi = !!curQ.multi;
        var opts = (curQ.options || []).map(function (o, oi) {
          var on = curPicked.indexOf(o.label) >= 0;
          // 第一项标记为「默认」：倒计时超时会自动选它，让用户一眼看到归属
          var isDefault = (oi === 0);
          return h('button', {
            key: curQ.id + ':' + oi,
            className: 'eng-ask-tr' + (on ? ' on' : ''),
            onClick: function () {
              userTouched.current = true;
              // 【Bug#1 修复】人已接管 → 停【本题】表，不再自动选默认项
              try { if (cdQid) cdTakenRef.current[cdQid] = true; } catch (e) {}
              if (state !== 'sending' && state !== 'ok') pick(curQ, o.label);
            }
          },
            h('span', { className: 'eng-ask-td-no' }, String(oi + 1)),
            h('span', { className: 'eng-ask-td-mark ' + (multi ? 'box' : 'radio') }),
            h('div', { className: 'eng-ask-td-txt' },
              h('div', { className: 'eng-ask-td-label' }, o.label),
              o.description ? h('div', { className: 'eng-ask-td-desc' }, o.description) : null),
            isDefault && cdOn ? h('span', { className: 'eng-ask-td-tag' }, '默认') : null);
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
          // 【PhaseB】倒计时徽标（超时自动选默认项）
          cdBadge,
          h('span', { style: { fontSize: '10px', fontWeight: 400, opacity: 0.75, marginLeft: cdBadge ? '8px' : 'auto' } }, multi ? '多选' : '单选')),
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
        var think = stripAnsi(ev.text || '').trim();
        if (!think || isNoise(think)) return null;
        return { t: 'think', text: think };
      }
      if (kind === 'message' || kind === 'text') {
        var text = stripAnsi(ev.text || '').trim();
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
        var rtext = stripAnsi(ev.text || '').trim();
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
    function renderEvent(ev, idx, info) {
      var m = formatMessage(ev);
      if (!m) return null;
      var key = String(ev.sessionId || '') + ':' + String(ev.seq) + ':' + String(idx);
      if (m.t === 'think') return h(ThinkBox, { key: key, text: m.text });
      if (m.t === 'text') return h(Bubble, { key: key, text: m.text });
      if (m.t === 'result') return h(Bubble, { key: key, sys: true, text: m.text });
      if (m.t === 'tool') return h(ToolCard, { key: key, name: m.name, args: m.args });
      if (m.t === 'error') return h(ErrorCard, { key: key, text: m.text });
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
      var entries = [];
      // ① 新形态（DSH 0.2.0）：projectionsBySession[parent].values.subagentCatalog
      var _proj = projections && currentId ? projections[currentId] : undefined;
      var _cat = _proj && _proj.values ? _proj.values.subagentCatalog : undefined;
      if (Array.isArray(_cat)) {
        for (var pi = 0; pi < _cat.length; pi++) {
          var pe = _cat[pi];
          if (!pe || pe.id === undefined || pe.id === null) continue;
          entries.push({
            id: String(pe.id),
            label: pe.label,
            mode: pe.mode,
            createdAt: pe.createdAt,
            activity: isRunning(String(pe.id)) ? 'running' : 'inactive'
          });
        }
      }
      // ② 旧形态（DSH 0.1.x）：subagentsByParent[parent].entries，按 kind 过滤
      if (entries.length === 0) {
        var catalog = catalogs && currentId ? catalogs[currentId] : undefined;
        if (catalog && catalog.entries) {
          for (var i = 0; i < catalog.entries.length; i++) {
            var en = catalog.entries[i];
            // 旧条目用 kind==='child' 标注；新条目没有 kind，故此处仅在旧形态下过滤
            if (!en || (en.kind !== undefined && en.kind !== 'child')) continue;
            entries.push(en);
          }
        }
      }
      var ids = entries.map(function (e) { return e.id; }).join(',');
      var selSt = useState(entries.length > 0 ? entries[0].id : null);
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
        seqRef.current = 0;
        logSt[1]([]);
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
      useEffect(function () {
        var box = boxRef.current;
        if (box) box.scrollTop = box.scrollHeight;
      }, [logSt[0].length]);
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
            // (2) 运行中优先：当前展示的已停、且存在在跑的子代理 → 顶替过去
            //     若用户刚刚手动点选过（2 秒内），尊重用户选择不抢
            var manualAt = manualPickRef.current || 0;
            if (Date.now() - manualAt < 2000) return;
            if (cur && isRun(cur)) return;          // 正在看的就在跑 → 不动
            var runId = null;
            for (var i = 0; i < entries.length; i++) {
              if (isRun(entries[i].id)) { runId = entries[i].id; break; }
            }
            if (runId && runId !== cur) {
              selSt[1](runId);
              seqRef.current = 0;
              logSt[1]([]);
              errSt[1](null);
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
      // ── 【问题1 修复】工程模式标记：与面板开合解耦 ────────────────────
      // preset 明确为 'engineering' 时才挂 ENG_MODE_CLASS。
      //   这样主对话的问答卡片样式在任何时候都生效，
      //   而其他模式绝不挂 → 样式零外溢（满足"只在工程模式搞"的要求）。
      useEffect(function () {
        if (typeof document === 'undefined') return;
        // 【问题4】必须同时受【设置开关】约束：
        //   ENG_MODE_CLASS 会改主对话的问答卡片样式，属"工程模式视觉接管"。
        //   用户关掉「使用工程模式自带的子代理显示页」后，若它仍挂着，
        //   还会残留一套我方样式 —— 与"关闭即交回官方"的语义不符。
        //   注意 preset 仍是 engineering（会话身份没变），所以这里用
        //   engAllowed（= preset 且设置开）而不是只判 preset。
        if (preset === 'engineering' && engSettings.useOwnSubagentPane) {
          document.body.classList.add(ENG_MODE_CLASS);
        } else {
          document.body.classList.remove(ENG_MODE_CLASS);
        }
        return function () {
          try { document.body.classList.remove(ENG_MODE_CLASS); } catch (e) {}
        };
      }, [preset, engSettings.useOwnSubagentPane]);

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
              document.documentElement.style.setProperty("--eng-pane", px + "px");
            }
          } else {
            // 让位给官方右列：两个类都摘掉 → 我方 grid 覆盖整条失效，
            //   宿主的【内联】grid-template-columns 自然生效。
            document.body.classList.remove("eng-pane-open");
            document.body.classList.remove(DOCK_CLASS);
          }
        } else {
          document.body.classList.remove(DOCK_CLASS);
          document.body.classList.remove("eng-pane-open");
        }
        return function () {
          document.body.classList.remove(DOCK_CLASS);
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
      var fab = (isCollapsed && !yieldToHost) ? h('button', {
        className: 'eng-fab' + (anyRunning ? ' run' : ''),
        title: '展开子代理面板（' + entries.length + ' 个子代理' +
               (anyRunning ? '，有运行中' : '') + '）',
        onClick: function () { closedSt[1](false); }
      },
        h('span', { className: 'eng-fab-ico' }, '‹'),
        h('span', { className: 'eng-fab-txt' }, '子代理'),
        h('span', { className: 'eng-fab-badge' }, String(entries.length)),
        anyRunning ? h('span', { className: 'eng-fab-dot' }) : null) : null;

      var current = null;
      for (var k = 0; k < entries.length; k++) { if (entries[k].id === selected) current = entries[k]; }
      var sum = summaries && current ? summaries[current.id] : null;
      var running = !!(current && (current.activity === 'running' || (sum && sum.running)));
      var done = !!(sum && sum.completed);
      var title = current ? String(current.label || (sum && sum.displayTitle) || current.id) : '子代理详情';
      var nodes = entries.map(function (e2) {
        var s2 = summaries ? summaries[e2.id] : null;
        var run = e2.activity === 'running' || !!(s2 && s2.running);
        var fin = !!(s2 && s2.completed);
        var cls = run ? ' run' : (fin ? ' done' : '');
        var state = run ? '运行中' : (fin ? '已完成' : '未运行');
        var lb = String(e2.label || (s2 && s2.displayTitle) || e2.id);
        return h('button', {
          key: e2.id,
          className: 'eng-node' + (e2.id === selected ? ' on' : ''),
          title: lb + ' — ' + state,
          onClick: function () {
            // 【问题3】用户主动选择 → 记录时间戳，自动顶替让路 2 秒
            try { manualPickRef.current = Date.now(); } catch (e) {}
            selSt[1](e2.id);
          }
        },
          h('span', { className: 'eng-dot' + cls }),
          h('span', { className: 'eng-node-label' }, lb),
          h('span', { className: 'eng-node-state' }, state));
      });
      var info = { parentSessionId: currentId, childSessionId: selected, onAnswered: clearWait };
      var body;
      if (errSt[0]) body = h(ErrorCard, { text: '无法读取子代理日志：' + errSt[0] });
      else if (logSt[0].length === 0) body = h('div', { className: 'eng-note' }, running ? '子代理正在运行，等待输出…' : '该子代理暂无输出');
      else body = logSt[0].map(function (ev, idx) { return renderEvent(ev, idx, info); });
      // ── 展开态：标准 Flex 侧边栏（挤压主区域，绝不覆盖）─────────────
      // 极窄屏（<900px）时目录与详情纵向堆叠，仍在流内。
      // 注意：面板始终挂载，用 .collapsed 类切换宽度以驱动过渡动画；
      //       收起时 width:0 + pointer-events:none，等价于完全不占位。
      var stackTree = veryNarrow;
      var consoleEl = h('div', {
        className: 'eng-console' + (stackTree ? ' stacked' : '') +
                   (isCollapsed ? ' collapsed' : '') +
                   (embedded ? ' embedded' : '') +
                   // 【让位】由 React 直接表达（不依赖 body 类），
                   //   配合 .eng-console.yielded{display:none} 生效。
                   (!embedded && yieldToHost ? ' yielded' : '')
      },
        h('div', { className: 'eng-tree' },
          h('div', { className: 'eng-tree-cap' },
            h('span', null, '子代理目录'),
            // 顶部折叠按钮：放在目录标题栏右侧，显眼易点
            // 嵌入模式不给折叠按钮（官方 tab 自带关闭/切换，避免语义重复）
            embedded ? null : h('button', {
              className: 'eng-collapse',
              title: '折叠子代理面板，让对话区占满全宽',
              onClick: function () { closedSt[1](true); }
            }, '折叠 ›')),
          h('div', { className: 'eng-tree-list' }, nodes)),
        h('div', { className: 'eng-detail' },
          h('div', { className: 'eng-head' },
            h('span', { className: 'eng-name' }, title),
            h('span', { className: 'eng-tag' + (running ? ' run' : '') }, running ? '运行中' : (done ? '已完成' : '未运行')),
            h('span', { className: 'eng-tag' }, current && current.mode === 'one-shot' ? '一次性' : '可持续'),
            embedded ? null : h('button', {
              className: 'eng-collapse',
              title: '折叠子代理面板',
              onClick: function () { closedSt[1](true); }
            }, '折叠 ›')),
          h('div', { className: 'eng-log', ref: boxRef }, body)));
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

    /** 一个主栏目：大标题 + 卡片容器。 */
    function EngGroup(props) {
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
        it.text);
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
      // ── 【占位模式】外观与 SW/CAD 完全一致，但不触发任何探测/启动 ────────
      // 用户要求："先给 abaqus/nx 做占位符，先不需要做实际连接"。
      //   因此按钮样式（.eng-conn-btn / .alt / 徽标 / 提示文案）全部复用，
      //   仅把 onClick 换成"未接入"的说明，避免误触发。
      var isPlaceholder = props.placeholder === true
        || target === 'abaqus' || target === 'nx';
      // 各目标的显示名（用于默认 label 与状态文案，避免"SolidWorks/CAD"二选一写死）
      var TARGET_DISPLAY = {
        sw: { label: 'SOLIDWORKS连接', name: 'SolidWorks' },
        cad: { label: 'AutoCAD 连接', name: 'AutoCAD' },
        abaqus: { label: 'Abaqus连接', name: 'Abaqus' },
        nx: { label: 'NX连接', name: 'NX' }
      };
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
        var entry = state && state[target] ? state[target] : null;
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
        // 占位项无需轮询：状态不会变，且没有后端可查
        var t = isPlaceholder ? null : setInterval(refresh, 15000);
        return function () {
          mounted.current = false;
          if (t) clearInterval(t);
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
              probeConn(target, function (r) {
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
            title: isPlaceholder
              ? (_td.name + ' 启动为预留项，暂未接入') 
              : '启动软件（会自动隐藏欢迎页并最大化主窗口）',
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
              launchConn(target, function (r) {
                if (!mounted.current) return;
                var txt = (r && r.log_text) || null;
                if (r && r.ok) {
                  patch({
                    phase: 'idle',
                    logText: txt,
                    note: '启动成功' + (r.connected ? '，已连接' : '（进程已就绪）')
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

        // ── 错误日志区（可折叠）+「发给模型」────────────────────────────
        S.logText ? h('div', { className: 'eng-conn-log' },
          h('div', { className: 'eng-conn-log-hd' },
            h('span', {
              className: 'eng-conn-log-tg',
              onClick: function () { patch({ logOpen: !S.logOpen }); }
            }, (S.logOpen ? '▾ ' : '▸ ') + '错误日志'),
            h('button', {
              className: 'eng-conn-btn tiny',
              type: 'button',
              title: '把这段日志作为消息发给当前会话，让 DSH 模型判断根因',
              onClick: function () {
                if (S.sent === 'sending') return;
                patch({ sent: 'sending' });
                sendLogToModel(target, S.logText, sessionId, function (r2) {
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

    function EngineeringSettingsSection(props) {
      var close = props && props.close;
      var settings = useEngSettings();
      var useOwn = settings.useOwnSubagentPane;
      var standalone = settings.useStandaloneChat;
      var explainOn = settings.enableSelectionExplain;
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
          h(EngGroup, { title: '连接区' },
            h(EngConnRow, { target: 'sw', label: 'SOLIDWORKS连接' }),
            h(EngConnRow, { target: 'cad', label: 'AutoCAD 连接' }),
            // ── 【连接区扩展】Abaqus / NX 连接（与 SW/CAD 完全同款按钮）──
            // 说明：两者都没有 COM 自动化接口，探测只覆盖"进程 + 安装路径"，
            //   按钮外观与交互（连接/启动/诊断）与 SW/CAD 一致；
            //   未安装时如实报错，不伪装成功。
            h(EngConnRow, { target: 'abaqus', label: 'Abaqus连接' }),
            h(EngConnRow, { target: 'nx', label: 'NX连接' }),
            h(EngRow, { label: '子栏目选项设置3' })),

          // ── 主栏目2：设计过程优化（用户指定名称）───────────────────────
          // 首行「右键引用调出AI解释」= 划词/右键「问问AI」功能的开关与控制。
          //   用户强调「这个功能是必须要有效的」，因此它是一个【真开关】：
          //   关闭后 SelectionDetailLayer 不再挂载，绝不出现"按钮在但没反应"。
          h(EngGroup, { title: '设计过程优化' },
            h('div', { className: 'eng-row sw' },
              h('div', { className: 'eng-row-txt' },
                h('div', { className: 'eng-row-label' }, '右键引用调出AI解释'),
                h('div', { className: 'eng-row-hint' }, explainOn
                  ? '已开启：在对话里选中文字（或右键）会浮现「问问AI」，' +
                    '点开即在右下角开一个小面板，就这段内容展开分析，结果可回填到输入框。'
                  : '已关闭：选中文字不再出现「问问AI」入口。')),
              h(EngSwitch, {
                bare: true,
                checked: explainOn,
                label: '右键引用调出AI解释',
                onChange: function (next) {
                  setEngSetting('enableSelectionExplain', next);
                }
              })),
            h(EngRow, { label: '子栏目选项设置2' }),
            h(EngRow, { label: '子栏目选项设置3' })),

          // ── 主栏目3：美化工程模式（第 1 行 = 子代理显示页开关）────────
          h(EngGroup, { title: '美化工程模式' },
            h('div', { className: 'eng-row sw' },
              h('div', { className: 'eng-row-txt' },
                h('div', { className: 'eng-row-label' },
                  '使用工程模式自带的子代理显示页'),
                h('div', { className: 'eng-row-hint' }, useOwn
                  ? '已开启：右侧子代理面板由工程模式接管（三栏布局）。' +
                    '官方子代理入口在工程模式下也指向本面板。'
                  : '已关闭：隐藏工程模式自带的子代理面板，右侧交回 DSH 官方显示。')),
              h(EngSwitch, {
                bare: true,
                checked: useOwn,
                label: '使用工程模式自带的子代理显示页',
                onChange: function (next) {
                  setEngSetting('useOwnSubagentPane', next);
                }
              })),
            h('div', { className: 'eng-row sw' },
              h('div', { className: 'eng-row-txt' },
                h('div', { className: 'eng-row-label' },
                  '是否开启单独的聊天而非 Agent'),
                h('div', { className: 'eng-row-hint' }, standalone
                  ? '已开启：工程模式下顶部多一个「聊天」视图，只做纯对话，不显示工具调用与工作过程；' +
                    '「工作」视图仍是原来的三栏工作区。'
                  : '已关闭：不提供独立聊天视图，工程模式只保留 DSH 官方界面。')),
              h(EngSwitch, {
                bare: true,
                checked: standalone,
                label: '是否开启单独的聊天而非 Agent',
                onChange: function (next) {
                  setEngSetting('useStandaloneChat', next);
                }
              })),
            h(EngRow, { label: '子栏目选项设置3' })),

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
    // ── 【独立聊天页】useStandaloneChat ────────────────────────────────────
    // 用户需求：「美化工程模式」里的「是否开启单独的聊天而非 Agent」。
    //   开 → 工程模式下提供「聊天 / 工作」切换器；
    //   关 → 完全不注册该入口，界面交回 DSH 官方。
    // 默认 true：与用户明确要的行为一致（否则功能默认不可见）。
    //
    // enableSelectionExplain：「设计过程优化」→「右键引用调出AI解释」。
    //   开 → 选中文字时浮现「问问AI」并可用右下角面板分析；
    //   关 → SelectionDetailLayer 完全不挂载（零监听、零 DOM）。
    var _engSettings = {
      useOwnSubagentPane: true,
      useStandaloneChat: true,
      enableSelectionExplain: true,
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
      lines.push('以下内容来自「纯聊天」模式的分析，请在工作模式下继续推进。');
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
          if (typeof j.useStandaloneChat === 'boolean') {
            _engSettings.useStandaloneChat = j.useStandaloneChat;
          }
          if (typeof j.enableSelectionExplain === 'boolean') {
            _engSettings.enableSelectionExplain = j.enableSelectionExplain;
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
    function setEngSetting(key, value) {
      _engSettings[key] = value;
      saveEngSettings();
      for (var i = 0; i < _engSettingsSubs.length; i++) {
        try { _engSettingsSubs[i](); } catch (e) {}
      }
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
          useStandaloneChat: _engSettings.useStandaloneChat,
          enableSelectionExplain: _engSettings.enableSelectionExplain,
        };
      });
      useEffect(function () {
        return subscribeEngSettings(function () {
          st[1]({
            useOwnSubagentPane: _engSettings.useOwnSubagentPane,
            useStandaloneChat: _engSettings.useStandaloneChat,
            enableSelectionExplain: _engSettings.enableSelectionExplain,
          });
        });
      }, []);
      return st[0];
    }

    // ══════════════════════════════════════════════════════════════════════
    // 【聊天 / 工作】同会话切换器
    // ══════════════════════════════════════════════════════════════════════
    // 用户要求：在【工程模式里面直接选聊天】就能纯聊天，不要另开预设、不要新建会话。
    //
    // 之前为什么失败：只换显示，模型照样挂满工具 → 照样调 skill。
    // 现在由 Host 的 /chat-mode 做【真正的能力切换】（见 lib/index.js）：
    //   · tools.restrict({deny:[现场取到的全部工具名]}) → 模型看不到任何工具
    //   · systemPrompt.section({complete:true}) → 整套 CAD 人设被替换成对话人设
    // 两者同时生效，同一个 Agent 才真的"不是 Agent"。
    // 会话不变 → 历史、模型、输入框全部沿用。
    var ENG_PRESET_ID = 'engineering';

    /** 读某个会话挂在会话列表行上的 preset（会话列表是唯一权威来源）。 */
    function presetOfSession(listSource, sessionId) {
      try {
        var snap = listSource && listSource.getSnapshot ? listSource.getSnapshot() : null;
        var row = snap && snap.byId ? snap.byId[sessionId] : null;
        if (!row) return null;
        if (row.projectionValues && typeof row.projectionValues.agentPreset === 'string') {
          return row.projectionValues.agentPreset;
        }
        return typeof row.agentPreset === 'string' ? row.agentPreset : null;
      } catch (e) { return null; }
    }

    /**
     * 「聊天 / 工作」切换器 + 聊天会话的起步建议。
     *
     * ── 落点（对应用户截图里的红框 / 蓝框）──────────────────────────────
     * 官方结构（ui-conversation/src/client/skeleton/ConversationContent.tsx:163-170）：
     *   <div class="composerStack">
     *     {hero && <HeroShell/>}                          大标题
     *     {hero && heroWorkspaceRow}                      工作区 chip + 预设
     *     {renderSlot('conversation.input.dock', zone)}   ← 输入框【上方】= 红框
     *     {inputBar}                                      ← 输入框本体
     *   </div>
     * ui-renderer 把每个插槽包成 <div data-slot="..." style="display:contents">
     *   （scoped-slots.tsx:1081-1091）。display:contents 不生成盒子，
     *   所以我的元素成为 .composerStack 的 flex 子项，用 CSS order 排序：
     *     .eng-vs           order 0  → 输入框上方（红框）
     *     [composer.bar]    order 2  → 输入框本体（官方）
     *     .eng-chat-starter order 3  → 输入框下方（蓝框）
     * 只改排列顺序，不改官方任何视觉属性。
     */
    function EngInputDock(props) {
      var sessionId = props.sessionId;
      var useSession = props.useSession;
      var useSessions = props.useSessions;
      var listSource = props.engListSource;

      // ══ 【React hooks 规则】所有 hook 必须在任何 return 之前调用 ═══════
      // 曾经的严重 bug：非工程模式只调 3 个 hook 就 return null，工程模式
      //   调 4 个（多的那个 useEffect 在 early return 之后）。
      //   React 检测到同一次挂载内 hook 数量变化会抛错，slot entry 随即
      //   【abdicate 永久失效】—— 表现为"切到标准模式再切回工程模式，
      //   工作/聊天切换器和推荐都不见了"。
      // 修法：先把所有 hook 无条件调完，再做门禁判断与渲染分支。
      var blank = useSession ? useSession(function (s) { return !!(s && s.blank); }) : false;
      var running = useSession ? useSession(function (s) { return !!(s && s.running); }) : false;
      // 预设（响应式订阅；会话列表是权威来源）
      // ⚠️ 这个 useSessions 也【必须无条件调用】—— 不能写
      //   `sessionId ? useSessions(...) : null`，否则 sessionId 从无到有时
      //   hook 数量变化，同样触发 React 报错导致 entry 永久失效。
      //   选择器内部对 sessionId 为空做保护即可。
      var livePreset = (typeof useSessions === 'function')
        ? useSessions(function (s) {
            if (!sessionId) return null;
            var row = s && s.byId ? s.byId[sessionId] : null;
            return row ? ((row.projectionValues && row.projectionValues.agentPreset) || row.agentPreset || null) : null;
          })
        : null;
      var preset = (livePreset === undefined || livePreset === null)
        ? presetOfSession(listSource, sessionId) : livePreset;

      // chat: 当前是否聊天模式；busy: 切换中；err: 失败原因
      // sugg*: 推荐追问（图二）
      var st = useState({
        chat: false, busy: false, err: null,
        sugg: [], suggBusy: false, suggFor: '',
      });
      var isChat = st[0].chat;
      // 记录上一轮的 running，用于检测"一轮刚结束"
      var prevRunningRef = useRef(false);

      // ── 推荐追问【持久化】────────────────────────────────────────────
      // 用户要求：「不管做啥，关了又开了也好，咋做都能像文字一样，一直不消失」。
      // 之前只放在 useState 里，于是：
      //   · 刷新页面 → 组件重挂 → 推荐没了
      //   · 换皮肤 → 客户端模块重新加载 → 组件重挂 → 推荐没了
      //   · 动设置开关 → 同样的重挂路径 → 推荐没了
      // 现在按会话存进 localStorage，挂载时恢复。
      //   与工程模式其它设置同一套做法（官方 shortcuts/store 也这么持久化）。
      var SUGG_KEY = 'dsh-engineering-ui/sugg/v1';
      var loadSugg = function (sid) {
        try {
          if (typeof localStorage === 'undefined' || !sid) return null;
          var raw = localStorage.getItem(SUGG_KEY);
          if (!raw) return null;
          var all = JSON.parse(raw);
          var one = all && all[sid];
          if (!one || !Array.isArray(one.items) || !one.items.length) return null;
          return { items: one.items.map(String), forSeq: String(one.forSeq || '') };
        } catch (e) { return null; }
      };
      var saveSugg = function (sid, items, forSeq) {
        try {
          if (typeof localStorage === 'undefined' || !sid) return;
          var raw = localStorage.getItem(SUGG_KEY);
          var all = raw ? JSON.parse(raw) : {};
          if (!all || typeof all !== 'object') all = {};
          if (items && items.length) all[sid] = { items: items.map(String), forSeq: String(forSeq || ''), at: Date.now() };
          else delete all[sid];
          // 只保留最近 40 个会话，避免无限增长
          var keys = Object.keys(all);
          if (keys.length > 40) {
            keys.sort(function (a, b) { return (all[b].at || 0) - (all[a].at || 0); });
            for (var i = 40; i < keys.length; i++) delete all[keys[i]];
          }
          localStorage.setItem(SUGG_KEY, JSON.stringify(all));
        } catch (e) { /* 写不了就只在内存里 */ }
      };

      // 挂载/切会话时：立刻恢复上次的推荐（刷新、换皮肤、动设置都能活下来）
      useEffect(function () {
        if (!sessionId) return;
        var saved = loadSugg(sessionId);
        st[1](function (p) {
          var n = {};
          for (var k in p) if (Object.prototype.hasOwnProperty.call(p, k)) n[k] = p[k];
          if (saved) { n.sugg = saved.items; n.suggFor = saved.forSeq; }
          return n;
        });
      }, [sessionId]);

      // ① 进会话/切会话时向 Host 查询聊天模式（运行期状态，前端不缓存）
      useEffect(function () {
        if (!sessionId) return;
        var alive = true;
        fetch('/dsh-engineering-ui/chat-mode?sessionId=' + encodeURIComponent(sessionId))
          .then(function (r) { return r.json(); })
          .then(function (j) {
            if (!alive || !j || !j.ok) return;
            st[1](function (p) {
              if (p.busy || p.chat === !!j.chatMode) return p;
              var n = {};
              for (var k in p) if (Object.prototype.hasOwnProperty.call(p, k)) n[k] = p[k];
              n.chat = !!j.chatMode;
              return n;
            });
          })
          .catch(function () { /* 查询失败保持现状 */ });
        return function () { alive = false; };
      }, [sessionId]);

      // ② 一轮对话【刚结束】时拉推荐追问（图二）
      // 触发条件：running 由 true → false，且处于聊天模式。
      //   用"结束"而不是"每次渲染"：避免请求风暴，也避免在流式输出中途打扰。
      useEffect(function () {
        var was = prevRunningRef.current;
        prevRunningRef.current = running;
        if (!sessionId) return;

        // ── 新一轮【开始】时立刻收起上一轮的推荐 ──────────────────────────
        // 用户指出：「发送消息之后，模型开始思考的时候它不会自己消失」。
        //   上一轮的推荐是"接着上一轮往下聊"的建议，新一轮已经开始，
        //   它们就是过期内容，必须马上下线（否则误导用户以为还能点）。
        // 内存与本地缓存【一起清】：只清内存的话，此刻刷新页面又会从
        //   localStorage 里把过期推荐读回来。
        if (running) {
          if (was !== true) saveSugg(sessionId, [], '');
          st[1](function (p) {
            if (!p.sugg.length) return p;
            var n = {};
            for (var k in p) if (Object.prototype.hasOwnProperty.call(p, k)) n[k] = p[k];
            n.sugg = [];
            return n;
          });
          return;
        }

        if (!(was && !running)) return;          // 只在 true→false 的瞬间触发
        if (!isChat) return;                     // 只在纯聊天模式下给推荐
        var alive = true;
        st[1](function (p) {
          var n = {};
          for (var k in p) if (Object.prototype.hasOwnProperty.call(p, k)) n[k] = p[k];
          n.suggBusy = true;
          return n;
        });
        // 从会话日志取最近几轮文本，交给宿主生成推荐
        fetch('/dsh-engineering-ui/log?sessionId=' + encodeURIComponent(sessionId) + '&limit=60')
          .then(function (r) { return r.json(); })
          .then(function (j) {
            if (!alive || !j || j.ok !== true) return null;
            var turns = [];
            var evs = j.events || [];
            for (var i = 0; i < evs.length; i++) {
              var arr = toDisplayEvents(evs[i]);
              for (var k = 0; k < arr.length; k++) {
                var d = arr[k];
                if (!d) continue;
                if (d.kind === 'message' || d.kind === 'text') {
                  turns.push({ role: 'assistant', text: String(d.text || '') });
                } else if (d.kind === 'user-message') {
                  turns.push({ role: 'user', text: String(d.text || '') });
                }
              }
            }
            if (!turns.length) return null;
            var route = (j.route && j.route.provider && j.route.model) ? j.route : null;
            return fetch('/dsh-engineering-ui/suggest', {
              method: 'POST',
              headers: { 'content-type': 'application/json' },
              body: JSON.stringify({
                turns: turns.slice(-8),
                provider: route ? route.provider : null,
                model: route ? route.model : null,
              }),
            }).then(function (r2) { return r2.json(); }).then(function (j2) {
              return { j2: j2, marker: String(evs.length) };
            });
          })
          .then(function (res) {
            if (!alive) return;
            st[1](function (p) {
              var n = {};
              for (var k in p) if (Object.prototype.hasOwnProperty.call(p, k)) n[k] = p[k];
              n.suggBusy = false;
              if (res && res.j2 && res.j2.ok && res.j2.items && res.j2.items.length) {
                n.sugg = res.j2.items;
                n.suggFor = res.marker;
                // 持久化：刷新/换皮肤/动设置后仍能恢复
                saveSugg(sessionId, res.j2.items, res.marker);
              } else if (!loadSugg(sessionId)) {
                // 本次没生成出来，且本地也没有旧值 → 保持空
                n.sugg = [];
              }
              // 若本地有旧值：保留它，不要因为一次失败就清空用户看到的东西
              return n;
            });
          })
          .catch(function () {
            if (!alive) return;
            st[1](function (p) {
              var n = {};
              for (var k in p) if (Object.prototype.hasOwnProperty.call(p, k)) n[k] = p[k];
              n.suggBusy = false;
              return n;
            });
          });
        return function () { alive = false; };
      }, [running, sessionId, isChat]);

      // ══ 所有 hooks 已调用完毕，下面才允许做门禁与渲染分支 ═══════════════
      var inEngineering = (preset === ENG_PRESET_ID);
      if (!inEngineering) return null;
      if (!_engSettings.useStandaloneChat) return null;

      // ── 显示位置（用户明确要求）──────────────────────────────────────
      // 用户指出：在【已有对话内容】的会话里，这个切换器悬在正文与输入框
      //   之间很碍眼，要求删掉；但【新建对话】时它必须还在
      //   （那是用户选择「工作 / 聊天」的唯一入口）。
      //
      // 因此只在两种情况下渲染：
      //   ① blank        —— 空会话（新建对话的欢迎屏）。用户截图要保留的就是这里。
      //   ② isChat       —— 已处于聊天模式。
      //      ⚠️ 这一条是必要的安全出口，不是"其他地方的残留"：
      //         纯聊天模式下模型没有任何工具，若把切换器一并藏掉，
      //         用户发出第一条消息后就【再也无法切回工作模式】，
      //         只能新建会话重来 —— 那会是一个真正的死锁。
      //   其余情况（工作模式 + 已有内容）→ return null，正文与输入框之间
      //   不再有任何本插件元素，正是用户要的"删掉"。
      if (!blank && !isChat) return null;

      /** 把一段文字填进官方输入框（推荐追问、起步建议共用）。 */
      var fillDraft = function (txt) {
        try {
          var ia = props.inputActions;    // session 作用域标准 prop
          if (ia && typeof ia.setDraft === 'function') { ia.setDraft(txt); return true; }
        } catch (e) { /* 落到 DOM 兜底 */ }
        try {
          var el = document.querySelector('[data-conversation-region="composer"] [contenteditable="true"]');
          if (el) { el.focus(); document.execCommand('insertText', false, txt); return true; }
        } catch (e2) { /* 无输入框则放弃 */ }
        return false;
      };

      var switchTo = function (chat) {
        if (st[0].busy) return;
        st[1](function (p) {
          var n = {};
          for (var k in p) if (Object.prototype.hasOwnProperty.call(p, k)) n[k] = p[k];
          n.chat = chat; n.busy = true; n.err = null;
          return n;
        });
        // ══ 【核心】同一个会话内切换 —— 不新建会话、不换预设 ══════════════
        // 由 Host 端的 /chat-mode 完成真正的能力切换（见 lib/index.js）：
        //   · tools.restrict({deny:[...现场取的全部工具名]}) → 模型看不到任何工具
        //   · systemPrompt.section({complete:true}) → 那套 CAD 工程人设被整体替换
        //   · 再加一道 guard 兜底：聊天模式下任何工具都无法执行
        // 会话本身不变 → 历史、模型、输入框全部沿用。
        fetch('/dsh-engineering-ui/chat-mode', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ sessionId: sessionId, on: !!chat }),
        })
          .then(function (r) { return r.json(); })
          .then(function (j) {
            st[1](function (p) {
              var n = {};
              for (var k in p) if (Object.prototype.hasOwnProperty.call(p, k)) n[k] = p[k];
              if (j && j.ok) {
                n.chat = !!j.chatMode; n.busy = false; n.err = null;
                if (!j.chatMode) n.sugg = [];     // 回工作模式就清掉推荐
              } else {
                n.chat = !chat; n.busy = false;
                n.err = String((j && j.error) || '切换失败');
              }
              return n;
            });
          })
          .catch(function (e) {
            st[1](function (p) {
              var n = {};
              for (var k in p) if (Object.prototype.hasOwnProperty.call(p, k)) n[k] = p[k];
              n.chat = !chat; n.busy = false;
              n.err = '切换失败：' + String((e && e.message) || e);
              return n;
            });
          });
      };

      var busy = !!st[0].busy;
      var seg = function (on, label, chat) {
        return h('button', {
          key: label,
          type: 'button',
          className: 'eng-vs-item' + (on ? ' on' : '') + (busy ? ' busy' : ''),
          'aria-pressed': on ? 'true' : 'false',
          disabled: busy,
          onClick: function () { if (!busy) switchTo(chat); }
        }, label);
      };
      // ── 「工作 / 聊天」切换器：【只在空会话（新建对话）显示】──────────
      // 用户要求：「帮我画红框的那个给删了，就在那里删了，其他地方不要删」
      //   + 「在正常用户选择模式新建对话的时候…这个切换器一定不能丢了」。
      // 合起来 = 对话进行中不显示，新建对话时必须显示。
      //   这也与 DSH 自身的预设选择器一致（预设同样只在空会话选）。
      // 非空会话仍会走到下面渲染【推荐追问】——那是聊天模式的产物，
      //   不能因为隐藏切换器而一起消失，所以这里是 vs=null 而不是 return null。
      var vs = blank
        ? h('div', { className: 'eng-vs', role: 'group', 'aria-label': '聊天 / 工作' },
            seg(!isChat, '工作', false),
            seg(isChat, busy ? '聊天…' : '聊天', true))
        : null;
      // 失败必须看得见，否则用户只看到"点了没反应"
      var errLine = st[0].err
        ? h('div', { className: 'eng-vs-err' }, String(st[0].err)) : null;

      // ── 推荐追问（图二）：一轮对话刚结束后显示，点一下填进输入框 ────────
      var sugg = null;
      if (isChat && !busy && st[0].sugg.length) {
        sugg = h('div', { className: 'eng-sugg' },
          st[0].sugg.map(function (q, i) {
            return h('button', {
              key: 'sg' + i,
              className: 'eng-sugg-item',
              type: 'button',
              title: '填入输入框',
              onClick: function () { fillDraft(String(q)); },
            }, h('span', { className: 'eng-sugg-ico' }, '↳'), String(q));
          }));
      }

      // 起步建议：只在【聊天模式 + 空会话】显示，位于输入框下方（蓝框）
      var starter = null;
      if (isChat && blank) {
        starter = h('div', { className: 'eng-chat-starter' },
          h('div', { className: 'eng-chat-starter-list' },
            chatStarterItems().map(function (txt, i) {
              return h('button', {
                key: 'st' + i,
                className: 'eng-chat-starter-item',
                type: 'button',
                onClick: function () { fillDraft(txt); },
              }, h('span', { className: 'eng-chat-starter-ico' }, '💬'), txt);
            })));
      }
      // display:contents → 子块成为 .composerStack 的独立 flex 项
      return h('div', { className: 'eng-dock-host', style: { display: 'contents' } }, vs, errLine, sugg, starter);
    }

    /** 聊天模式的起步建议（不诱导模型调工具）。 */
    function chatStarterItems() {
      return [
        '帮我梳理一下这个设计方案的思路',
        '解释一下悬臂梁的受力原理',
        '我有个零件的想法，帮我分析可行性'
      ];
    }

    // ══════════════════════════════════════════════════════════════════════
    // 【划词详情】SelectionDetailLayer —— 三张截图对应的功能
    // ══════════════════════════════════════════════════════════════════════
    // 完整交互链：
    //   ① 用户在 Agent 回复里选中一段文字 → 选区附近浮现「更多详情」按钮（图二）
    //   ② 点击 → 右下角打开分析面板，自动就该内容发起分析（图三）
    //   ③ 面板里可继续追问；每条助手回复可「添加到对话」→ 回填主对话输入框
    //   ④ 本次分析的历史留在面板内（图一红框要求"放左栏"见下方说明）
    //
    // 为什么不用官方插槽渲染"左栏历史"：
    //   图一红框是【子代理目录】所在的侧栏，而侧栏由官方 ui-sidebar 拥有、
    //   其内部结构不在本插件可注入的插槽清单里（本插件只能往
    //   conversation.* / settings.* / shell.overlay 等已声明插槽注册）。
    //   因此历史采用【面板内历史列表】实现，视觉上与子代理严格区分：
    //     · 子代理 = 蓝色圆点 + 「未运行/运行中」徽标
    //     · 详情分析 = 紫色对话气泡图标 + 「分析 N 轮」徽标
    //   若后续要真正嵌进侧栏，需要 ui-sidebar 暴露插槽（属官方改动，不做）。
    function SelectionDetailLayer(props) {
      // ── 作用域门禁：设置里关掉「右键引用调出AI解释」→ 完全不工作 ──────
      // 用户强调「这个功能是必须要有效的」，所以这里必须是【真门禁】：
      //   关闭时不挂鼠标监听、不渲染按钮/面板（零副作用），
      //   而不是"按钮还在但点了没反应"。
      // ⚠️ 所有 hook 必须在 return 之前调用（React hooks 规则，历史上
      //   在 EngInputDock 上踩过：条件调用 hook 会让 entry 永久失效）。
      var settings = useEngSettings();
      var st = useState({
        open: false,          // 面板是否打开
        anchor: null,         // 选区锚点（用于浮层按钮定位）
        btnVisible: false,    // 浮层按钮是否显示
        selection: '',        // 本次选中的原文
        messages: [],         // 面板内的分析对话（前端持有，宿主无状态）
        input: '',            // 面板输入框内容
        busy: false,
        err: null,
        route: null,          // {provider, model}：从当前会话读，用于辅助调用
        // 【一键发送给工作模式建模】：sending=发送中；sentTo=已发送到的会话 id
        sending: false,
        sentTo: null,
        sentDrafted: false,
        // 面板尺寸：用户可以拖左上角手柄自由放大放小（用户明确要求）
        size: { w: 420, h: 520 },
      });
      var S = st[0];
      var setS = st[1];
      function patch(o) {
        st[1](function (p) {
          var n = {};
          for (var k in p) if (Object.prototype.hasOwnProperty.call(p, k)) n[k] = p[k];
          for (var k2 in o) if (Object.prototype.hasOwnProperty.call(o, k2)) n[k2] = o[k2];
          return n;
        });
      }
      var panelRef = useRef(null);
      var listRef = useRef(null);

      // ── 读取当前会话的模型路由 ────────────────────────────────────────
      // /side-chat 需要 provider/model 才能发辅助请求。取当前会话最近一次
      //   请求头里的路由（宿主 /log 端点在 route 字段里回传）。
      useEffect(function () {
        if (!S.open || S.route) return;
        try {
          // 当前会话 id：官方 root 侧标准做法（mainView 保留者）
          var sid = null;
          var badges = document.querySelector('[data-conversation-content]');
          if (badges) sid = badges.getAttribute('data-conversation-session');
          if (!sid) return;
          fetch('/dsh-engineering-ui/log?sessionId=' + encodeURIComponent(sid) + '&limit=1')
            .then(function (r) { return r.json(); })
            .then(function (j) {
              if (j && j.ok && j.route && j.route.provider && j.route.model) {
                patch({ route: j.route });
              }
            })
            .catch(function () { /* 取不到就在发送时报错 */ });
        } catch (e) { /* 忽略 */ }
      }, [S.open, S.route]);

      // ── ① 划词检测：在 Agent 回复区域监听 selectionchange ──────────────
      // 只认【对话区内的选区】，避免选中侧栏/设置等无关文字时也弹按钮。
      // 用 mouseup 而不是 selectionchange 收尾：selectionchange 在拖选过程中
      //   会高频触发，按钮会跟着乱跳。
      useEffect(function () {
        var onUp = function () {
          try {
            var sel = window.getSelection ? window.getSelection() : null;
            if (!sel || sel.isCollapsed) { patch({ btnVisible: false }); return; }
            var text = String(sel.toString() || '').trim();
            if (text.length < 2) { patch({ btnVisible: false }); return; }
            // 选区必须落在对话区内（[data-conversation-content] 或滚动体）
            var node = sel.anchorNode;
            var el = node && node.nodeType === 1 ? node : (node ? node.parentElement : null);
            var inConv = false;
            while (el) {
              if (el.getAttribute && (el.getAttribute('data-conversation-content') !== null
                  || el.getAttribute('data-conversation-scroll') !== null)) { inConv = true; break; }
              el = el.parentElement;
            }
            if (!inConv) { patch({ btnVisible: false }); return; }
            // 记录选区矩形，用于给浮层按钮定位
            var rect = null;
            try {
              var r = sel.getRangeAt(0).getBoundingClientRect();
              if (r && (r.width || r.height)) rect = { left: r.left, top: r.top, bottom: r.bottom, right: r.right };
            } catch (e) { /* 无矩形时退回鼠标位置 */ }
            patch({ btnVisible: true, selection: text.slice(0, 8000), anchor: rect });
          } catch (e) { /* 选区 API 异常 → 不显示 */ }
        };
        var onDown = function (ev) {
          // 点的不是浮层按钮/面板 → 收起按钮（但不动已打开的面板）
          try {
            var t = ev.target;
            if (t && t.closest && (t.closest('.eng-sel-btn') || t.closest('.eng-side'))) return;
            patch({ btnVisible: false });
          } catch (e) { /* 忽略 */ }
        };
        document.addEventListener('mouseup', onUp, true);
        document.addEventListener('mousedown', onDown, true);
        return function () {
          document.removeEventListener('mouseup', onUp, true);
          document.removeEventListener('mousedown', onDown, true);
        };
      }, []);

      // 面板打开时滚动到底（流式输出时也跟着滚）
      useEffect(function () {
        var box = listRef.current;
        if (box) box.scrollTop = box.scrollHeight;
      }, [S.open, S.messages.length, S.messages.length ? S.messages[S.messages.length - 1].text : '']);

      // ── ② 发起一次分析（首轮或追问都走这里）────────────────────────────
      var ask = function (questionText) {
        var q = String(questionText || '').trim();
        if (S.busy) return;
        if (!S.route) {
          patch({ err: '无法确定模型路由（请确认当前会话已开始过，或稍后重试）' });
          return;
        }
        // 面板内历史 + 本次提问
        var nextMsgs = S.messages.slice();
        if (q) nextMsgs.push({ role: 'user', text: q });
        // 先放一条空的助手消息占位，流式增量会不断更新它的 text
        //   → 面板立刻有内容，不再长时间空白（修「一直出不来分析」）
        var withPlaceholder = nextMsgs.concat([{ role: 'assistant', text: '', streaming: true }]);
        var botIdx = withPlaceholder.length - 1;
        patch({ busy: true, err: null, messages: withPlaceholder, input: '' });

        /** 把最后一条助手消息的文本替换成 latest（流式更新）。 */
        var setBotText = function (latest, done) {
          st[1](function (p) {
            var n = {};
            for (var k in p) if (Object.prototype.hasOwnProperty.call(p, k)) n[k] = p[k];
            var msgs = p.messages.slice();
            if (msgs[botIdx]) {
              msgs[botIdx] = { role: 'assistant', text: String(latest || ''), streaming: !done };
            }
            n.messages = msgs;
            if (done) { n.busy = false; }
            return n;
          });
        };
        var setErr = function (m) {
          st[1](function (p) {
            var n = {};
            for (var k in p) if (Object.prototype.hasOwnProperty.call(p, k)) n[k] = p[k];
            // 失败时去掉占位气泡，避免留一条空消息
            var msgs = p.messages.slice();
            if (msgs[botIdx] && !msgs[botIdx].text) msgs.splice(botIdx, 1);
            n.messages = msgs;
            n.busy = false;
            n.err = String(m || '分析失败');
            return n;
          });
        };

        // 用 SSE 流式读取；不支持时自动回退到一次性 JSON。
        fetch('/dsh-engineering-ui/side-chat', {
          method: 'POST',
          headers: { 'content-type': 'application/json', 'accept': 'text/event-stream' },
          body: JSON.stringify({
            selection: S.selection,
            messages: nextMsgs,
            provider: S.route.provider,
            model: S.route.model,
            stream: '1',
          }),
        })
          .then(function (r) {
            var ct = String((r.headers && r.headers.get && r.headers.get('content-type')) || '');
            if (ct.indexOf('text/event-stream') < 0) {
              // 服务端没走流式（例如出错早返回 JSON）→ 按 JSON 处理
              return r.json().then(function (j) {
                if (j && j.ok && j.reply) setBotText(j.reply, true);
                else setErr((j && j.error) || '分析失败');
              });
            }
            // 手动读流并解析 SSE 帧
            var reader = r.body && r.body.getReader ? r.body.getReader() : null;
            if (!reader) {
              return r.text().then(function (txt) {
                var m = /"reply"\s*:\s*"([\s\S]*?)"\s*\}/.exec(txt);
                if (m) setBotText(m[1], true); else setErr('无法读取响应');
              });
            }
            var dec = new TextDecoder();
            var buf = '';
            var finished = false;
            var pump = function () {
              return reader.read().then(function (res) {
                if (res.done) {
                  if (!finished) setBotText('', true);
                  return;
                }
                buf += dec.decode(res.value, { stream: true });
                // SSE 帧以空行分隔
                var parts = buf.split('\n\n');
                buf = parts.pop();
                for (var i = 0; i < parts.length; i++) {
                  var line = parts[i].trim();
                  if (line.indexOf('data:') !== 0) continue;
                  var obj = null;
                  try { obj = JSON.parse(line.slice(5).trim()); } catch (e) { continue; }
                  if (!obj) continue;
                  if (obj.type === 'delta') setBotText(obj.text, false);
                  else if (obj.type === 'done') { finished = true; setBotText(obj.reply, true); }
                  else if (obj.type === 'error') { finished = true; setErr(obj.error); }
                }
                return pump();
              });
            };
            return pump();
          })
          .catch(function (e) {
            setErr(String((e && e.message) || e));
          });
      };

      // ── ③ 回填到主对话输入框（图三「添加到对话」）──────────────────────
      // 走官方 composer 的 contenteditable；用 execCommand 走原生输入路径，
      //   这样 React/Lexical 能正确收到这次输入。
      var addToConversation = function (text) {
        var body = String(text || '').trim();
        if (!body) return false;
        try {
          var el = document.querySelector('[data-conversation-region="composer"] [contenteditable="true"]');
          if (!el) return false;
          el.focus();
          // 已有草稿时先换行，避免和原文粘在一起
          var pre = String(el.textContent || '').trim() ? '\n\n' : '';
          document.execCommand('insertText', false, pre + body);
          return true;
        } catch (e) { return false; }
      };

      // ── ④【一键发送给工作模式建模】（面板内版本）───────────────────────
      // 发送内容 = 选中原文 + 面板里已有的分析结论。
      // 真正的会话创建/预设设置/发送/跳转都在 engSendToWorkSession 里，
      //   与操作行按钮（conversation.chat.assistant-actions）共用同一条链路。
      var sendToWorkMode = function () {
        if (S.sending) return;
        var analysis = S.messages.filter(function (m) { return m.role === 'assistant' && m.text; })
          .map(function (m) { return String(m.text).trim(); }).filter(Boolean).join('\n\n---\n\n');
        var sel = String(S.selection || '').trim();
        if (!sel && !analysis) {
          patch({ err: '没有可发送的内容' });
          return;
        }
        patch({ sending: true, err: null });
        engSendToWorkSession(engComposeWorkPrompt({ source: sel, analysis: analysis }))
          .then(function (res) {
            patch({
              sending: false, err: null,
              sentTo: (res && res.sessionId) || null,
              sentDrafted: !!(res && res.drafted),
            });
          })
          .catch(function (e3) {
            patch({ sending: false, err: '发送到工作模式失败：' + String((e3 && e3.message) || e3) });
          });
      };

      // ══ 设置门禁（所有 hook 之后）══════════════════════════════════════
      // 「设计过程优化 → 右键引用调出AI解释」关闭时：整个功能不工作。
      //   放在 hook 之后是为了不违反 React hooks 规则（同 EngInputDock 的教训）。
      if (!settings.enableSelectionExplain) return null;

      // ── 浮层按钮（图二）──────────────────────────────────────────────
      var btn = null;
      if (S.btnVisible && !S.open && S.anchor) {
        // 定位在选区下方的水平中心；靠近顶部时放到选区上方，避免被裁掉。
        var W = 96;
        var cx = (S.anchor.left + S.anchor.right) / 2;
        var left = Math.max(8, Math.min((window.innerWidth || 1200) - W - 8, cx - W / 2));
        var below = (S.anchor.bottom + 8);
        var useAbove = below + 30 > (window.innerHeight || 800) - 8;
        var top = useAbove ? Math.max(8, S.anchor.top - 34) : below;
        btn = h('button', {
          className: 'eng-sel-btn',
          type: 'button',
          style: { left: left + 'px', top: top + 'px' },
          // ⚠️ 用 onMouseDown 而不是 onClick：mouseup 会先改变选区，
          //    onClick 期间 selection 可能已被清空 → 拿不到原文。
          onMouseDown: function (ev) {
            ev.preventDefault();   // 阻止默认行为以保留选区
            ev.stopPropagation();
            patch({ open: true, btnVisible: false });
            // 打开即自动发起一次分析（图三的行为）
            setTimeout(function () { ask(''); }, 0);
          },
        }, '问问AI');
      }

      // ── 右下角面板（图三）────────────────────────────────────────────
      var panel = null;
      if (S.open) {
        var rows = S.messages.map(function (m, i) {
          var isBot = m.role === 'assistant';
          return h('div', { key: 'm' + i, className: 'eng-side-row ' + (isBot ? 'bot' : 'me') },
            h('div', { className: 'eng-side-bubble' }, m.text),
            isBot
              ? h('div', { className: 'eng-side-acts' },
                  h('button', {
                    className: 'eng-side-act',
                    type: 'button',
                    title: '把这段分析插入主对话输入框',
                    onClick: function () {
                      var ok = addToConversation(m.text);
                      if (!ok) patch({ err: '找不到输入框，无法回填' });
                    },
                  }, '添加到对话'))
              : null);
        });
        panel = h('div', {
            className: 'eng-side',
            ref: panelRef,
            // 面板尺寸由用户拖拽决定，存在 state 里（切会话/重开保留本页期间的值）
            style: { width: S.size.w + 'px', height: S.size.h + 'px' },
          },
          // ── 左上角拖拽手柄：调整面板大小（用户要求"能自由放大放小"）────
          // 用 pointer events + setPointerCapture：拖动时即使指针移出面板
          //   也能继续收到事件，不会中途丢失。
          h('div', {
            className: 'eng-side-grip',
            title: '拖动调整大小',
            onPointerDown: function (ev) {
              try {
                ev.preventDefault();
                ev.stopPropagation();
                var el = ev.currentTarget;
                if (el.setPointerCapture) el.setPointerCapture(ev.pointerId);
                var startX = ev.clientX, startY = ev.clientY;
                var w0 = S.size.w, h0 = S.size.h;
                var move = function (e2) {
                  // 面板锚在右下角：向左拖变宽、向上拖变高
                  var w = w0 + (startX - e2.clientX);
                  var hh = h0 + (startY - e2.clientY);
                  var maxW = Math.max(320, (window.innerWidth || 1200) - 36);
                  var maxH = Math.max(240, (window.innerHeight || 800) - 36);
                  w = Math.max(300, Math.min(maxW, w));
                  hh = Math.max(220, Math.min(maxH, hh));
                  patch({ size: { w: Math.round(w), h: Math.round(hh) } });
                };
                var up = function () {
                  document.removeEventListener('pointermove', move, true);
                  document.removeEventListener('pointerup', up, true);
                };
                document.addEventListener('pointermove', move, true);
                document.addEventListener('pointerup', up, true);
              } catch (e) { /* 不支持 pointer events 时退化为不可缩放 */ }
            },
          }, '⤡'),
          h('div', { className: 'eng-side-hd' },
            h('span', { className: 'eng-side-ico' }, '💬'),
            h('span', { className: 'eng-side-title' }, '问问AI'),
            h('span', { className: 'eng-side-badge' }, S.messages.length ? (S.messages.length + ' 条') : ''),
            h('button', {
              className: 'eng-side-x',
              type: 'button',
              title: '关闭',
              onClick: function () { patch({ open: false }); },
            }, '×')),
          // 选中原文（折叠展示，让用户知道在分析什么）
          h('div', { className: 'eng-side-src', title: S.selection }, S.selection),
          h('div', { className: 'eng-side-list', ref: listRef },
            rows.length ? rows : h('div', { className: 'eng-side-note' }, S.busy ? '正在分析…' : '正在等待分析…'),
            S.err ? h('div', { className: 'eng-side-err' }, S.err) : null),
          // ── 【一键发送给工作模式建模】────────────────────────────────
          // 新建一个工程模式会话，把「引用原文 + 已有分析」作为首轮任务发过去，
          //   然后跳到那个会话 —— 由工作模式的工作区真正干活（建模/仿真）。
          // 放在输入框上方，与「继续追问」区分开：一个是继续聊，
          //   一个是"带着结论去开工"。
          h('div', { className: 'eng-side-work' },
            h('button', {
              className: 'eng-side-workbtn',
              type: 'button',
              disabled: S.sending || S.busy || (!S.selection && !S.messages.length),
              title: '新建一个工程模式会话，把本次分析交给它去建模',
              onClick: function () { sendToWorkMode(); },
            }, S.sending ? '正在交给工作模式…' : '⚙ 一键发送给工作模式建模'),
            S.sentTo
              ? h('div', { className: 'eng-side-ok' }, S.sentDrafted
                  ? '已新建工作会话并把任务填入输入框，请按回车开始'
                  : '已发送，正在跳转到新的工作会话…')
              : null),
          h('div', { className: 'eng-side-ft' },
            h('input', {
              className: 'eng-side-in',
              type: 'text',
              placeholder: '继续追问…（Enter 发送）',
              value: S.input,
              disabled: S.busy,
              onChange: function (ev) { patch({ input: ev.target.value }); },
              onKeyDown: function (ev) {
                if (ev.key === 'Enter' && !ev.shiftKey) {
                  ev.preventDefault();
                  ask(S.input);
                }
              },
            }),
            h('button', {
              className: 'eng-side-send',
              type: 'button',
              disabled: S.busy || !S.input.trim(),
              onClick: function () { ask(S.input); },
            }, S.busy ? '…' : '↑')));
      }

      if (!btn && !panel) return null;
      // display:contents：让两个浮层元素都直接参与 shell.overlay 的层级
      return h('div', { className: 'eng-sel-host', style: { display: 'contents' } }, btn, panel);
    }

    // ══════════════════════════════════════════════════════════════════════
    // 【一键发送给Agent建模】—— 每条助手回复的操作行按钮
    // ══════════════════════════════════════════════════════════════════════
    // 落点：官方插槽 conversation.chat.assistant-actions
    //   （ui-chat/src/client/contract/slots.ts:321，kind:'list' scope:'session'，
    //     ownerProps = { messageId }；由 ui-chat 的 register-node-renderers.ts:71
    //     声明，TurnTailNodeView 用 renderSlot 渲染到
    //     MessageIconActions 那一排 —— 也就是复制/点赞/分享/时间戳所在的行）。
    //
    // 行为：读当前会话的完整对话 → 组装任务 → 新开一个工程模式工作会话 →
    //   把任务作为该会话首轮发出去 → 跳转过去开始建模。
    function EngSendToAgentAction(props) {
      // ⚠️ hooks 必须在任何 return 之前（本文件已有前车之鉴）
      var st = useState({ busy: false, done: false, drafted: false, err: null });
      var sessionId = props.sessionId;
      var useSessions = props.useSessions;

      // ── 作用域门禁：只在【工程模式】会话里出现 ──────────────────────
      // 用户反馈：「蔓延到非工程模式里面去了」。
      //   这个按钮会把整段对话交给一个新的工程模式工作 Agent，
      //   它是工程模式的入口，不该出现在标准模式/其它预设的会话里。
      // 必须在所有 hook 之后判断（否则 hook 数量变化 → entry 永久失效）。
      var preset = (typeof useSessions === 'function')
        ? useSessions(function (s) {
            if (!sessionId) return null;
            var row = s && s.byId ? s.byId[sessionId] : null;
            return row
              ? ((row.projectionValues && row.projectionValues.agentPreset) || row.agentPreset || null)
              : null;
          })
        : null;

      var run = function () {
        if (st[0].busy) return;
        st[1](function (p) {
          var n = {};
          for (var k in p) if (Object.prototype.hasOwnProperty.call(p, k)) n[k] = p[k];
          n.busy = true; n.err = null; n.done = false;
          return n;
        });
        engFetchTranscript(engCurrentSessionId(sessionId)).then(function (tr) {
          if (!String(tr || '').trim()) {
            throw new Error('读不到当前会话的对话内容（请确认该会话已开始过）');
          }
          return engSendToWorkSession(engComposeWorkPrompt({ transcript: tr }));
        }).then(function (res) {
          st[1](function (p) {
            var n = {};
            for (var k in p) if (Object.prototype.hasOwnProperty.call(p, k)) n[k] = p[k];
            n.busy = false;
            n.done = !!(res && res.sent);
            n.drafted = !!(res && res.drafted);
            return n;
          });
        }).catch(function (e) {
          var msg = String((e && e.message) || e);
          try { console.warn('[dsh-engineering-ui] 一键发送给Agent建模失败:', msg); } catch (e2) {}
          st[1](function (p) {
            var n = {};
            for (var k in p) if (Object.prototype.hasOwnProperty.call(p, k)) n[k] = p[k];
            n.busy = false; n.err = msg;
            return n;
          });
        });
      };

      // ── 门禁（所有 hook 之后）────────────────────────────────────────
      // 只在工程模式会话显示。非工程模式 → return null（零 DOM、零副作用），
      //   修「蔓延到非工程模式里面去了」。
      if (preset !== ENG_PRESET_ID) return null;
      // 该按钮独立于「右键引用调出AI解释」开关：
      //   那个开关管的是划词浮层，与"把分析交给工作 Agent"是两件事，
      //   共用一个开关会让用户关掉一个却连带失去另一个。
      return h('span', { className: 'eng-msg-act-wrap' },
        h('button', {
          className: 'eng-msg-act' + (st[0].busy ? ' busy' : '') + (st[0].done ? ' done' : ''),
          type: 'button',
          disabled: st[0].busy,
          title: st[0].err
            ? ('发送失败：' + st[0].err)
            : '新开一个「工程模式的工作」Agent，按这段聊天的分析数据开始建模',
          onClick: function (ev) { ev.preventDefault(); ev.stopPropagation(); run(); },
        }, st[0].busy ? '正在新建…'
          : (st[0].done ? '✓ 已交给工作Agent'
            : (st[0].drafted ? '✓ 已填入新会话，按回车发送' : '⚙ 一键发送给Agent建模'))),
        // 失败必须【看得见原因】，只写"失败"等于让用户无从下手
        st[0].err ? h('span', { className: 'eng-msg-act-err', title: st[0].err }, st[0].err.slice(0, 40)) : null);
    }

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
      // 【问题1 修复】主对话原生问答卡片的倒计时（仅工程模式生效）
      try {
        ctx.effect(function () { return installQuestionCountdown(10); },
                   'dsh-engineering-ui: question countdown');
      } catch (e) { try { installQuestionCountdown(10); } catch (e2) {} }
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
      try {
        ctx.slots.inject('shell.overlay', function () {
          return ctx.slots.register({ name: 'shell.overlay', id: 'eng-subagent-console', order: 31, label: '子代理控制台' }, SubagentConsole);
        });
      } catch (e) { console.error('[dsh-engineering-ui] console slot', e); }

      // ══ 【划词详情】右下角分析面板（shell.overlay 的独立住户）════════════
      // 用户需求（对照三张截图）：
      //   · 在 Agent 回复里划选文字 → 浮现「更多详情」；
      //   · 点击 → 右下角小面板，就这段内容展开分析；
      //   · 分析结果可「添加到对话」回填到主对话输入框。
      // 落点：shell.overlay（root 作用域，全帧覆盖层），因此不受会话切换影响。
      //   order=40：排在子代理控制台(31)之后，保证浮在最上层。
      try {
        ctx.slots.inject('shell.overlay', function () {
          return ctx.slots.register(
            { name: 'shell.overlay', id: 'eng-selection-detail', order: 40, label: '划词详情' },
            SelectionDetailLayer,
          );
        });
      } catch (e) { console.error('[dsh-engineering-ui] selection detail slot', e); }

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

      // ══ 【一键发送给Agent建模】每条助手回复的操作行按钮 ═════════════════
      // 落点 = 官方 conversation.chat.assistant-actions（复制/点赞/分享那一排）。
      // 用 slots.inject 等官方声明出现（由 ui-chat 的 register-node-renderers.ts
      //   声明），声明消失/重建时自动注销与重装。
      try {
        ctx.slots.inject('conversation.chat.assistant-actions', function () {
          return ctx.slots.register({
            name: 'conversation.chat.assistant-actions',
            id: 'dsh-engineering-ui/send-to-agent',
            order: 50,
          }, EngSendToAgentAction);
        });
      } catch (e) { console.error('[dsh-engineering-ui] assistant-actions slot', e); }
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

      // ══ 【聊天 / 工作】切换器注册 ═══════════════════════════════════════
      // 架构（重要）：
      //   · 「聊天」= 同一个会话内**【真正的能力切换】**，由 Host 的 /chat-mode
      //     完成（tools.restrict 摘工具 + systemPrompt complete 换人设）。
      //     不新建会话、不换预设 —— 历史、模型、输入框全部沿用。
      //   · client 端只负责【入口按钮】，并把点击转成一次 /chat-mode 请求。
      //   · 注册一次常驻；组件内部按"当前会话是否工程模式"决定渲不渲染
      //     （非工程 → return null，零 DOM、零副作用）。
      //
      // ⚠️【必须用独立的 dockDisposers，不能复用上面那个 disposers】
      //   上面 subscribeEngSettings 的回调会遍历 disposers 全部 dispose()
      //   然后只重装 tab 接管。若把切换器的 disposer 也放进那个数组，
      //   用户每动一次设置开关（含"是否开启单独的聊天"）切换器就被销毁一次
      //   且永不重建 —— 表现正是"关了又开之后，除非重启 DSH，UI 再也不出来"。
      var dockDisposers = [];
      try {
        var sessionsSvc = ctx.sessions;
        var listSrc = sessionsSvc && sessionsSvc.list;
        // ── 【输入区】注册「聊天/工作」切换器 + 起步建议 ──────────────────
        // 用 slots.inject 等待 official 声明出现（conversation.input.dock
        //   由 ui-conversation 声明），声明消失时自动注销。
        try {
          var disposeDock = ctx.slots.inject('conversation.input.dock', function () {
            return ctx.slots.register({
              name: 'conversation.input.dock',
              id: 'dsh-engineering-ui/view-switch',
              order: 0,
              inject: function (sessionId) {
                // 组件只需要一个"当前会话 preset"的判据源（决定显不显示）。
                // 能力切换本身走 HTTP /chat-mode，不需要其他服务。
                return { engListSource: listSrc, engSessionId: sessionId };
              },
            }, EngInputDock);
          });
          // 常驻：交给插件生命周期统一回收（dockDisposers 不被设置回调清空）。
          if (typeof disposeDock === 'function') dockDisposers.push(disposeDock);
        } catch (e) { console.error('[dsh-engineering-ui] input dock registration', e); }
        // 插件卸载时回收
        disposers.push(function () {
          for (var i = dockDisposers.length - 1; i >= 0; i--) {
            try { dockDisposers[i](); } catch (e) {}
          }
          dockDisposers.length = 0;
        });

        if (!(listSrc && typeof listSrc.getSnapshot === 'function')) {
          // 拿不到会话列表服务（旧版/异常）→ 切换器仍会渲染，但判据取不到
          //   preset，组件会 return null（功能静默缺失，不报错、不影响其他功能）。
          console.warn('[dsh-engineering-ui] sessions.list 不可用，聊天切换器将不显示');
        }
      } catch (e) { console.error('[dsh-engineering-ui] chat switch registration', e); }
    }
    exports.apply = apply;
    exports.inject = ['sessions', 'slots'];
    exports.SubagentConsole = SubagentConsole;
    exports.EngineeringSettingsSection = EngineeringSettingsSection;
    exports.EngGroup = EngGroup;
    exports.EngRow = EngRow;
    exports.EngConnRow = EngConnRow;
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
    exports.SubagentDock = SubagentConsole;
    exports.formatMessage = formatMessage;
    exports.renderEvent = renderEvent;
    exports.AskCard = AskCard;
    exports.parseQuestions = parseQuestions;
    return module.exports;
  }
});
