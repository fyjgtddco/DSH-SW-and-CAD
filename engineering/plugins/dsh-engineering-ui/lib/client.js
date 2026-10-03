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
      '.eng-settings-body{background:var(--dsw-alias-bg-layer-2,#f6f7f9);border:1px solid var(--dsw-alias-border-l1,#e5e7eb);border-radius:10px;padding:12px;text-align:left}',
      // ── 【问题4】三层设置结构 ──────────────────────────────────────
      '.eng-set-layer{border:1px solid var(--dsw-alias-border-l1,#e5e7eb);border-radius:9px;margin:0 0 10px;overflow:hidden;background:var(--dsw-alias-bg-layer-1,#fff)}',
      '.eng-set-layer:last-child{margin-bottom:0}',
      '.eng-set-layer-hd{align-items:center;background:transparent;border:0;color:inherit;cursor:pointer;display:flex;font:inherit;gap:9px;padding:11px 12px;text-align:left;width:100%}',
      '.eng-set-layer-hd:hover{background:var(--dsw-alias-bg-layer-3,#eef1f5)}',
      '.eng-set-layer-no{align-items:center;background:var(--dsw-alias-button-primary-fill,#2563eb);border-radius:50%;color:#fff;display:inline-flex;flex:none;font-size:11px;font-weight:700;height:19px;justify-content:center;width:19px}',
      '.eng-set-layer-t{font-size:13px;font-weight:600;flex:none}',
      '.eng-set-layer-d{color:var(--dsw-alias-label-tertiary,#8b95a3);font-size:11.5px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
      '.eng-set-layer-caret{color:var(--dsw-alias-label-tertiary,#8b95a3);flex:none;font-size:11px}',
      '.eng-set-layer-bd{border-top:1px solid var(--dsw-alias-border-l1,#e5e7eb);padding:12px}',
      '.eng-set-note{font-size:12px;line-height:1.75;margin:0 0 10px}',
      '.eng-set-note.dim{color:var(--dsw-alias-label-tertiary,#8b95a3);font-size:11.5px;margin:10px 0 0}',
      '.eng-set-kv{align-items:center;display:flex;gap:8px;margin:0 0 4px}',
      '.eng-set-k{color:var(--dsw-alias-label-tertiary,#8b95a3);font-size:12px}',
      '.eng-set-v{background:var(--dsw-alias-bg-layer-3,#eef1f5);border-radius:5px;font-size:12px;font-weight:600;padding:2px 8px}',
      '.eng-set-v.on{background:rgba(37,99,235,.12);color:var(--dsw-alias-button-primary-fill,#2563eb)}',
      '.eng-set-list{font-size:12px;line-height:1.9;margin:0;padding-left:18px}',
      // 开关行
      '.eng-sw-row{align-items:center;display:flex;gap:12px;justify-content:space-between}',
      '.eng-sw-txt{flex:1;min-width:0}',
      '.eng-sw-label{font-size:13px;font-weight:600}',
      '.eng-sw-hint{color:var(--dsw-alias-label-tertiary,#8b95a3);font-size:11.5px;line-height:1.65;margin-top:3px}',
      '.eng-sw{background:var(--dsw-alias-bg-layer-3,#cbd5e1);border:0;border-radius:999px;cursor:pointer;flex:none;height:22px;padding:0;position:relative;transition:background .18s ease;width:40px}',
      '.eng-sw.on{background:var(--dsw-alias-button-primary-fill,#2563eb)}',
      '.eng-sw-knob{background:#fff;border-radius:50%;box-shadow:0 1px 3px rgba(0,0,0,.25);height:18px;left:2px;position:absolute;top:2px;transition:transform .18s ease;width:18px}',
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
        '.eng-set-layer{background:#22252c;border-color:#33383f}' +
        '.eng-set-layer-hd:hover{background:#2a2e37}' +
        '.eng-set-layer-bd{border-top-color:#33383f}' +
        '.eng-set-v{background:#2a2e37}' +
        '.eng-set-v.on{background:rgba(37,99,235,.22)}' +
        '.eng-sw{background:#3a3f4a}' +
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

    // ══ 【问题4】设置页「工程模式」分区：三层结构 ═════════════════════════
    //
    // 契约来源：@deepseek-ai/dsh-client-ui-settings 的 slots 声明
    //   'settings.section': { kind: 'list', scope: 'root',
    //                          owner: SettingsSectionOwnerProps }
    //   SettingsSectionOwnerProps = { close: () => void }
    //
    // 用户要求把设置分为【三层】，并把"是否使用工程模式自带的子代理显示页"
    //   放在【第三层】。这里按信息层级组织：
    //     第一层 · 概览        —— 这个分区是干什么的、当前生效状态
    //     第二层 · 工作流与门禁 —— 门禁/防线相关（只读展示，指向命令行）
    //     第三层 · 界面与显示   —— 界面行为开关（本开关在此）
    //   每层是一个可折叠小节，层级清晰且不喧宾夺主。
    function EngSettingsLayer(props) {
      var openSt = useState(props.defaultOpen !== false);
      var open = openSt[0];
      return h('div', { className: 'eng-set-layer' + (open ? ' open' : '') },
        h('button', {
          className: 'eng-set-layer-hd',
          type: 'button',
          'aria-expanded': open ? 'true' : 'false',
          onClick: function () { openSt[1](!open); }
        },
          h('span', { className: 'eng-set-layer-no' }, props.no),
          h('span', { className: 'eng-set-layer-t' }, props.title),
          h('span', { className: 'eng-set-layer-d' }, props.desc || ''),
          h('span', { className: 'eng-set-layer-caret' }, open ? '▾' : '▸')),
        open ? h('div', { className: 'eng-set-layer-bd' }, props.children) : null
      );
    }

    /** 单个开关行（自绘，不依赖官方组件库）。 */
    function EngSwitch(props) {
      var on = !!props.checked;
      return h('div', { className: 'eng-sw-row' },
        h('div', { className: 'eng-sw-txt' },
          h('div', { className: 'eng-sw-label' }, props.label),
          props.hint ? h('div', { className: 'eng-sw-hint' }, props.hint) : null),
        h('button', {
          className: 'eng-sw' + (on ? ' on' : ''),
          type: 'button',
          role: 'switch',
          'aria-checked': on ? 'true' : 'false',
          title: props.label,
          onClick: function () { props.onChange(!on); }
        },
          h('span', { className: 'eng-sw-knob' }))
      );
    }

    function EngineeringSettingsSection(props) {
      var close = props && props.close;
      var settings = useEngSettings();
      var useOwn = settings.useOwnSubagentPane;
      return h('div', { className: 'eng-settings', 'data-eng-section': 'engineering' },
        h('div', { className: 'eng-settings-hd' },
          h('span', { className: 'eng-settings-ico' }, '🛠️'),
          h('div', null,
            h('div', { className: 'eng-settings-title' }, '工程模式'),
            h('div', { className: 'eng-settings-sub' },
              'SolidWorks / CAD 机械设计工作流的专用设置'))),

        h('div', { className: 'eng-settings-body' },

          // ── 第一层：概览 ──────────────────────────────────────────────
          h(EngSettingsLayer, {
            no: '1', title: '概览', desc: '工程模式做什么、当前生效状态'
          },
            h('div', { className: 'eng-set-note' },
              '工程模式以「完成工图」为核心目标，执行 分析 → 设计 → 验证 的闭环流程，' +
              '并用三大防线（材料 / 物理 / 领域）对结果做代码级校验。'),
            h('div', { className: 'eng-set-kv' },
              h('span', { className: 'eng-set-k' }, '子代理显示页'),
              h('span', { className: 'eng-set-v' + (useOwn ? ' on' : '') },
                useOwn ? '使用工程模式自带' : '使用 DSH 官方')),
            h('div', { className: 'eng-set-note dim' },
              '说明：三大防线的阈值与门禁策略由命令行工具管理，' +
              '可通过 `python tools/defense_gate.py status` 查看当前状态。')),

          // ── 第二层：工作流与门禁 ──────────────────────────────────────
          h(EngSettingsLayer, {
            no: '2', title: '工作流与门禁', desc: '设计流程、房间调度与防线', defaultOpen: false
          },
            h('div', { className: 'eng-set-note' },
              '本分区用于说明工程模式的流程约束；具体参数由 tools/ 下的命令行工具' +
              '（workflow_gate.py / mode_gate.py / defense_gate.py）管理，' +
              '以免界面与门禁状态不一致。'),
            h('ul', { className: 'eng-set-list' },
              h('li', null, '零件设计三部曲：分析 → 设计 → 验证'),
              h('li', null, '多零件任务按「房间」并行/串行调度'),
              h('li', null, '每个房间下线前必须通过三大防线校验'),
              h('li', null, '所有防线凭据必须由 DSH 宿主签名'))),

          // ── 第三层：界面与显示（本开关在此）───────────────────────────
          h(EngSettingsLayer, {
            no: '3', title: '界面与显示', desc: '子代理面板等界面行为'
          },
            h(EngSwitch, {
              checked: useOwn,
              label: '使用工程模式自带的子代理显示页',
              hint: useOwn
                ? '已开启：右侧子代理面板由工程模式接管（三栏布局）。' +
                  '官方子代理入口在工程模式下也指向本面板。'
                : '已关闭：隐藏工程模式自带的子代理面板，右侧交回 DSH 官方显示。',
              onChange: function (next) {
                setEngSetting('useOwnSubagentPane', next);
              }
            }),
            h('div', { className: 'eng-set-note dim' },
              '关闭后本插件不再占用右侧第三列，官方子代理/文件预览按原生方式显示；' +
              '重新开启即可恢复三栏布局。此设置会保存在本机，重启后仍然生效。'))),

        close ? h('div', { className: 'eng-settings-ft' },
          h('button', { className: 'eng-settings-close', onClick: close }, '关闭')) : null
      );
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
    var _engSettings = { useOwnSubagentPane: true };
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
        };
      });
      useEffect(function () {
        return subscribeEngSettings(function () {
          st[1]({
            useOwnSubagentPane: _engSettings.useOwnSubagentPane,
          });
        });
      }, []);
      return st[0];
    }

    function apply(ctx) {
      installStyle();
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
          disposers.push(disposeType);
          // ② 注册正文：key 必须是上面那个 id（tab-registry 用 definition.id 派发）
          ctx.slots.inject('sidebar.right.pane.tab', function () {
            return ctx.slots.register({
              name: 'sidebar.right.pane.tab',
              key: ENG_SUBAGENT_TAB_ID,
            }, EngSubagentTabBody);
          });
        } catch (e) {
          console.error('[dsh-engineering-ui] official subagent tab takeover', e);
        }
      }
      try {
        installOfficialTabTakeover();
        // 设置变化时重装（开关切换即时生效，无需刷新）
        subscribeEngSettings(function () {
          for (var i = disposers.length - 1; i >= 0; i--) {
            try { disposers[i](); } catch (e) {}
          }
          disposers.length = 0;
          installOfficialTabTakeover();
        });
      } catch (e) { console.error('[dsh-engineering-ui] tab takeover setup', e); }
    }
    exports.apply = apply;
    exports.inject = ['sessions', 'slots'];
    exports.SubagentConsole = SubagentConsole;
    exports.EngineeringSettingsSection = EngineeringSettingsSection;
    exports.EngSettingsLayer = EngSettingsLayer;
    exports.EngSwitch = EngSwitch;
    exports.SubagentDock = SubagentConsole;
    exports.formatMessage = formatMessage;
    exports.renderEvent = renderEvent;
    exports.AskCard = AskCard;
    exports.parseQuestions = parseQuestions;
    return module.exports;
  }
});
