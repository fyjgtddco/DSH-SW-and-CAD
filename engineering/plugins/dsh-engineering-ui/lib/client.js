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
    var PRESET_LABEL = 'SW单行模式';
    var PRESET_KEY = 'sw-single-line';
    var PRESET_ICON = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAACXBIWXMAAAsTAAALEwEAmpwYAAAFnUlEQVRIx41WW1MaSRT2N+5f2H+wT/uWrd3a1ObBoBijBsEb8ZpEDTdRUFRESYxG0QgIogIzA3NngIEZpPf0DOAwEE3Vmaqe7tP9nfOdS/cAQqiuPvCSyooKCNcW1iCdmSeWTMJJSk1pwuEDdeWBZGoEjSVPtwakJsaxUfJtzY6+aW/rl63B4QOCpMJ/galRmpCaUN2/ZPeSSceoZpyHY7mSOgAfjKif7OzMmwa9vyYAPKZrjKi0AMifnNXrEMH0VyZ6zIJjWSOArpEvVkGIYpWkZfMRtAxLOSwy0QOsb8xrG/WjSAygUYQDogOwtZKMxAqSZMSVGoQBA7YxggrzYhWkSXZ7WWDrpWprIyMoJN0BUAb4kkrS+un1LFWenA+MzfotNtfOYZyTmvmijK2jZVZsfI3djzg8b6a8zk9hUO44XeCUTF6EXW9n/KAQS5A0r1K0TJoogtnkLfvX4OLxRTEUvX01ugoekKzuvsyXmyMOryv44ypT/m90/fD0his1MRvFqlRFy65Dx9J+Kiu/HFnbiyZ4CS91AFoegSpfQWOzG8eXhYqKrHbft4ssW3oAGFpQExlm2O6lOMRKKHCYcixul2soX6jCKic9WB2e8yRfENDb2a1MTixyyiNFOoBuZqmCPm+ezK9FpRr64Pu+4j4CZnOFilhGW+HL2Y+RooDuKPVHWrS8c+lZwPBqHLAnvRSPIie5iblNiASE2gjwWAeM0LhIUhabhy6h6Dk1bHdz5WaWqgDMuHMz/C2bKz5cpHhaRMN233HsHvSBjZ1I3LG0B0471478uzGhjFrZAYVmpKiTKsOTnu9xhmDR6wk3MMOID7dEyWJzw8yS6+ufL51SHa36zxbWw3CWUEH2he1QNMOUMGo8TQMqaaqDDgCOmIwW1g/WNs/LCppcCG1HrioK+H4zNhcs19GbKf8fL2ypbPX8WrBMuhmxAWUx4vAl7yrnSW50agOSTTf/kSK2XQd6KUFUIR2tjg1RRsFICjiFjji1FAoepq+zFfvizsJa+HMgBukwiP1jf6Roq8MPyutbsQ/eKJCJa83oAdvdiyDBQeP1O3fyrpy4Kw/ZPDCJbbyv7H65fb96EL+hLTYveONcPdrav9yOxBddx6UaejO9eXKZY0WcdaTRA1Ozg9OhmB2LO5vhpFCFrA3492LjcwHg/d38Tvg4RYuNwQkXpPyXGGVfDM582IuekfALRkAeQkk/9o92kB8B9DhDAYe/psad22CXN3QFpG/sJUgWDU16oBKrODZBYA+i+s/Qyt+WFUjQ3WhmejkElhEaP4TJg1braHfKolb6kKy3JE75337/9/q+AuU9NuOHeoYw7n25Hp/briM0ZPe9tK7CwDa/CzZBynb46eNBp6Hnteofn/VHTnPZYsMXuoIEXfGeuAKnkJRAQiYnaPDKZVo4vijcFRrWqY10li/yirHJtwDYkmKkiNBaG5Suf/di7hMu3QxRIzmcoLEkhfOyUIVUAW8OTnLAG8k2o2fUhHMLsHNtfsi+FHUASFzSavyGGbJ77wuNDFHHOT7tB+rw/qIMzcAXOoe6Bc9y9AM45w6eAQAsdd13Jg+Mdx5un1LTavd8TzByAwUiqbmP+5IWQ7AAet9VGicrdBSuojtXAOeMhnbFgKTN9yqEAcx0B09fja5DVb8YXD7FOd7QG1xeq8fR6Y2J9yHH8j4kKN1m/5cAHgds7eBbOnBwCX2N1vjplChcMjc5MXSUgHvp+o7HALRsfgD0jYFJ4FYBrsDe3gsdQgJLILj7d1/g1BNB7nFTxtcW/ZNHirba+2p6BqDvK4p67u3VR/NpD54FoJ7DaHVT/en4xGuO+oWzeqWgeQBv6gFFbZJsnTA8Y8meQd8nsEl61QAGP37...';
    var STYLE = [
      ':root{--eng-sb:280px}',
      'body.' + DOCK_CLASS + ' div[style*="grid-template-columns"]{grid-template-columns:var(--eng-sb) 3fr 2fr !important}',
      '.eng-console{background:#fff;border-left:1px solid #cbd5e1;bottom:0;box-sizing:border-box;color:#0f172a;display:flex;flex-direction:row;font-size:12px;pointer-events:auto;position:fixed;right:0;top:0;width:calc((100vw - var(--eng-sb)) * 0.4);z-index:30}',
      '.eng-tree{background:#f1f5f9;border-right:1px solid #cbd5e1;box-sizing:border-box;display:flex;flex:2;flex-direction:column;height:100%;min-width:0;overflow:hidden}',
      '.eng-tree-cap{border-bottom:1px solid #cbd5e1;color:#334155;flex:none;font-size:12px;font-weight:700;letter-spacing:.03em;padding:11px 12px}',
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
      '.eng-detail{background:#fff;box-sizing:border-box;display:flex;flex:5;flex-direction:column;height:100%;min-width:0;overflow:hidden}',
      '.eng-head{align-items:center;background:#f8fafc;border-bottom:1px solid #cbd5e1;display:flex;flex:none;gap:7px;min-height:46px;padding:0 10px 0 13px}',
      '.eng-name{color:#0f172a;flex:1;font-size:13px;font-weight:700;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
      '.eng-tag{background:#e2e8f0;border-radius:5px;color:#475569;flex:none;font-size:10px;font-weight:600;padding:2px 7px;white-space:nowrap}',
      '.eng-tag.run{background:#16a34a;color:#fff}',
      '.eng-x{background:#fff;border:1px solid #cbd5e1;border-radius:6px;color:#475569;cursor:pointer;flex:none;font-size:13px;font-weight:700;height:24px;line-height:1;width:24px}',
      '.eng-x:hover{background:#e2e8f0}',
      '.eng-log{flex:1;min-height:0;overflow-y:auto;padding:12px 14px 18px}',
      '.eng-note{color:#64748b;font-size:12px;padding:18px 14px;text-align:center}',
      // ── 聊天气泡 ─────────────────────────────────────────────
      '.eng-bubble{border-radius:10px;margin:0 0 9px;padding:8px 12px;font-size:12px;line-height:1.6;white-space:pre-wrap;word-break:break-word;background:#eff6ff;border:1px solid #bfdbfe;color:#1e3a5f}',
      '.eng-bubble.sys{background:#f8fafc;border-color:#e2e8f0;color:#475569}',
      // ── 思考引用框 ───────────────────────────────────────────
      '.eng-think{border-left:3px solid #cbd5e1;color:#64748b;font-size:11.5px;font-style:italic;line-height:1.6;margin:0 0 9px;padding:6px 0 6px 10px;white-space:pre-wrap;word-break:break-word}',
      // ── 工具卡 ───────────────────────────────────────────────
      '.eng-tool-card{background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;margin:0 0 7px;overflow:hidden}',
      '.eng-tool-card-h{align-items:center;color:#0369a1;cursor:pointer;display:flex;font-size:11.5px;font-weight:600;gap:6px;padding:6px 10px;user-select:none}',
      '.eng-tool-card-h:hover{background:#e0f2fe}',
      '.eng-tool-txt{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}',
      '.eng-tool-caret{flex:none;font-size:10px;opacity:.55}',
      '.eng-tool-card-b{background:#fff;border-top:1px solid #bae6fd;color:#334155;font-family:ui-monospace,Consolas,monospace;font-size:11px;max-height:200px;overflow:auto;padding:7px 10px;white-space:pre-wrap;word-break:break-word}',
      // ── 错误告警卡 ───────────────────────────────────────────
      '.eng-err-card{align-items:flex-start;background:#fef2f2;border:1px solid #fca5a5;border-radius:8px;color:#991b1b;display:flex;font-size:11.5px;gap:7px;line-height:1.5;margin:0 0 8px;padding:7px 10px;word-break:break-word}',
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
      // ── 折叠开关（未闭合的工具结果等）────────────────────────
      '.eng-fold{color:#94a3b8;cursor:pointer;font-size:11px;margin:0 0 8px;padding:3px 0}',
      '.eng-fab{position:fixed;right:12px;top:76px;z-index:30}',
      '.eng-preset-icon{background-image:url(' + PRESET_ICON + ');background-position:center;background-repeat:no-repeat;background-size:contain;border-radius:3px;display:inline-block;flex:none;height:15px;width:15px}',
      '[aria-label*="' + PRESET_LABEL + '"]::before{background-image:url(' + PRESET_ICON + ');background-position:center;background-repeat:no-repeat;background-size:contain;border-radius:3px;content:"";flex:none;height:15px;margin-right:5px;width:15px}',
      '.eng-warn{align-items:center;background:#fffbeb;border:1px solid #f59e0b;border-radius:8px;box-shadow:0 1px 3px rgba(15,23,42,.10);color:#78350f;display:flex;font-size:12.5px;gap:10px;line-height:1.45;margin:6px 0;padding:8px 11px}',
      '.eng-warn-txt{flex:1;min-width:0}',
      '.eng-btn{background:#fff;border:1px solid #d97706;border-radius:6px;color:#92400e;cursor:pointer;font-size:12px;font-weight:600;padding:4px 12px;white-space:nowrap}',
      '.eng-btn:hover{background:#fef3c7}',
      '.eng-btn.p{background:#d97706;border-color:#d97706;color:#fff}',
      '@media(prefers-color-scheme:dark){' +
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
        '.eng-warn{background:#3a2f10;border-color:#a16207;color:#fde68a}' +
        '.eng-btn{background:#22262e;border-color:#a16207;color:#fde68a}' +
        '.eng-btn.p{background:#a16207;color:#fff}}',
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
    function installFrameWidthSync() {
      if (typeof document === 'undefined' || typeof MutationObserver !== 'function') return;
      var frame = null;
      var sync = function () {
        if (frame === null || !frame.isConnected) {
          frame = document.querySelector('div[style*="grid-template-columns"]');
        }
        if (frame === null) return;
        var raw = frame.style.gridTemplateColumns || '';
        var first = raw.trim().split(/\\s+/)[0] || '';
        if (/^[0-9.]+px$/.test(first)) {
          document.documentElement.style.setProperty('--eng-sb', first);
        }
      };
      sync();
      var obs = new MutationObserver(function (records) {
        for (var i = 0; i < records.length; i++) {
          if (records[i].target === frame) { sync(); return; }
        }
        if (frame === null) sync();
      });
      obs.observe(document.body, { attributes: true, attributeFilter: ['style'], childList: true, subtree: true });
      return function () { obs.disconnect(); };
    }
    function installPresetIcon() {
      if (typeof document === 'undefined' || typeof MutationObserver !== 'function') return;
      var CLS = 'eng-preset-icon';
      var mark = function () {
        var nodes = document.querySelectorAll('button[role="menuitem"]');
        for (var i = 0; i < nodes.length; i++) {
          var btn = nodes[i];
          if (btn.getElementsByClassName(CLS).length > 0) continue;
          var label = btn.querySelector('[class*="itemLabel"]');
          if (!label || String(label.textContent || '').indexOf(PRESET_LABEL) < 0) continue;
          var icon = document.createElement('span');
          icon.className = CLS;
          icon.setAttribute('aria-hidden', 'true');
          btn.insertBefore(icon, btn.firstChild);
        }
      };
      mark();
      var obs = new MutationObserver(mark);
      obs.observe(document.body, { childList: true, subtree: true });
      return function () { obs.disconnect(); };
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

      var submit = function (cause) {
        // 唯一合法触发源：用户点按钮。cause 不是 'user-click' 一律不提交。
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
      var nextQ = function () {
        userTouched.current = true;
        if (idx < sorted.length - 1) idxSt[1](idx + 1);
        else { stSt[1]('sending'); msgSt[1]('全部答完，提交中…'); submit('user-click'); }
      };
      // 当前题的选项
      var blocks = null;
      if (curQ) {
        var multi = !!curQ.multi;
        var opts = (curQ.options || []).map(function (o, oi) {
          var on = curPicked.indexOf(o.label) >= 0;
          return h('button', {
            key: curQ.id + ':' + oi,
            className: 'eng-opt' + (on ? ' on' : ''),
            onClick: function () {
              // 【需求3】用户真实点击才算数：标记 + 记录选择
              userTouched.current = true;
              if (state !== 'sending' && state !== 'ok') pick(curQ, o.label);
            }
          },
            h('span', { className: 'eng-opt-mark ' + (multi ? 'box' : 'radio') }),
            h('div', { className: 'eng-opt-txt' },
              h('div', { className: 'eng-opt-label' }, o.label),
              o.description ? h('div', { className: 'eng-opt-desc' }, o.description) : null));
        });
        blocks = h('div', null,
          h('div', { className: 'eng-ask-q' },
            curQ.header ? h('div', { className: 'eng-ask-qh' }, curQ.header) : null,
            curQ.question ? h('div', null, curQ.question) : null),
          opts.length > 0
            ? h('div', { className: 'eng-ask-opts' }, opts)
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
          h('span', { style: { marginLeft: 'auto', fontSize: '10px', fontWeight: 400, opacity: 0.75 } }, multi ? '多选' : '单选')),
        h('div', { style: { padding: '6px 0' } }, blocks || h('div', { className: 'eng-note' }, '加载中…')),
        state !== 'ok'
          ? h('div', { className: 'eng-ask-ft' },
              h('span', { className: 'eng-ask-state' + (state === 'ok' ? ' ok' : (state === 'err' ? ' err' : '')) }, msgSt[0]),
              h('button', {
                className: 'eng-ask-btn',
                disabled: !curDone || state === 'sending',
                onClick: nextQ
              },
                idx < total - 1 ? '下一题 →' : '提交全部 ✓'))
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
    function EngineeringBanner(props) {
      var sessionId = props.sessionId;
      var useSessions = props.useSessions;
      var useProjection = props.useProjection;
      var top = useState(false);
      var modePreset;
      if (typeof useSessions === 'function' && sessionId) {
        modePreset = useSessions(function (s) { var row = s.byId ? s.byId[sessionId] : null; return row ? row.agentPreset : undefined; });
      }
      var perms = typeof useProjection === 'function' ? useProjection('permissions') : undefined;
      if (top[0]) return null;
      if (modePreset === undefined || modePreset === 'engineering') return null;
      var current = perms && perms.currentValue !== undefined ? perms.currentValue : undefined;
      if (current !== PRESET_KEY) return null;
      return h('div', { className: 'eng-warn', role: 'alert' },
        h('span', null, '⚠️'),
        h('div', { className: 'eng-warn-txt' }, '识别到您开启的不是工程模式，您的权限可能会回退成默认的智能选择，是否继续？'),
        h('button', { className: 'eng-btn', onClick: function () { top[1](true); } }, '取消'),
        h('button', { className: 'eng-btn p', onClick: function () { top[1](true); } }, '继续'));
    }
    function SubagentConsole(props) {
      var useSessions = props.useSessions;
      var closedSt = useState(false);
      var currentId = typeof useSessions === 'function' ? useSessions(function (s) { return s.current; }) : undefined;
      var catalogs = typeof useSessions === 'function' ? useSessions(function (s) { return s.subagentsByParent; }) : undefined;
      var summaries = typeof useSessions === 'function' ? useSessions(function (s) { return s.byId; }) : undefined;
      var preset = typeof useSessions === 'function' ? useSessions(function (s) {
        var row = s && s.current !== undefined && s.byId ? s.byId[s.current] : null;
        return row ? row.agentPreset : undefined;
      }) : undefined;
      var catalog = catalogs && currentId ? catalogs[currentId] : undefined;
      var entries = [];
      if (catalog && catalog.entries) {
        for (var i = 0; i < catalog.entries.length; i++) {
          var en = catalog.entries[i];
          if (en && en.kind === 'child') entries.push(en);
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
          fetch('/dsh-engineering-ui/log?sessionId=' + encodeURIComponent(selected) + '&since=' + seqRef.current + '&limit=300')
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
                logSt[1](function (prev) { return prev.concat(disp).slice(-1000); });
              }
            })
            .catch(function (e) { if (alive) errSt[1](String((e && e.message) || e)); });
        };
        pull();
        var timer = setInterval(pull, 1500);
        return function () { alive = false; clearInterval(timer); };
      }, [selected, closedSt[0]]);
      useEffect(function () {
        var box = boxRef.current;
        if (box) box.scrollTop = box.scrollHeight;
      }, [logSt[0].length]);
      // 【问题2】自动顶替：有子代理在等待用户选择时，自动把展示切到它。
      // 多个待答时按 seq 升序排队，答完一个自动切下一个。
      useEffect(function () {
        var timer = setInterval(function () {
          var waits = waitRef.current || {};
          var pending = [];
          for (var k in waits) {
            if (Object.prototype.hasOwnProperty.call(waits, k)) pending.push(k);
          }
          if (pending.length === 0) return;
          // 按 seq 升序（先提问的先答）
          pending.sort(function (a, b) { return (waits[a].seq || 0) - (waits[b].seq || 0); });
          var head = pending[0];
          var cur = selSt[0];
          // 当前展示的不是待答对象 → 顶替过去
          if (cur !== head) {
            if (!prevSelRef.current) prevSelRef.current = cur;
            selSt[1](head);
          }
        }, 1200);
        return function () { clearInterval(timer); };
      }, []);
      // 答完清账：当某子代理的 ask 卡已提交成功，从待答队列移除
      var clearWait = function (childId) {
        if (waitRef.current && waitRef.current[childId]) {
          delete waitRef.current[childId];
        }
      };
      var active = entries.length > 0 && !closedSt[0];
      useEffect(function () {
        if (typeof document === 'undefined') return;
        if (active) document.body.classList.add(DOCK_CLASS); else document.body.classList.remove(DOCK_CLASS);
        return function () { document.body.classList.remove(DOCK_CLASS); };
      }, [active]);
      if (entries.length === 0) return null;
      if (preset !== undefined && preset !== 'engineering') return null;
      if (closedSt[0]) {
        return h('button', {
          className: 'eng-x eng-fab',
          title: '展开子代理控制台',
          onClick: function () { closedSt[1](false); }
        }, String(entries.length));
      }
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
          onClick: function () { selSt[1](e2.id); }
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
      return h('div', { className: 'eng-console' },
        h('div', { className: 'eng-tree' },
          h('div', { className: 'eng-tree-cap' }, '子代理目录 · v3'),
          h('div', { className: 'eng-tree-list' }, nodes)),
        h('div', { className: 'eng-detail' },
          h('div', { className: 'eng-head' },
            h('span', { className: 'eng-name' }, title),
            h('span', { className: 'eng-tag' + (running ? ' run' : '') }, running ? '运行中' : (done ? '已完成' : '未运行')),
            h('span', { className: 'eng-tag' }, current && current.mode === 'one-shot' ? '一次性' : '可持续'),
            h('button', { className: 'eng-x', title: '收起控制台', onClick: function () { closedSt[1](true); } }, '»')),
          h('div', { className: 'eng-log', ref: boxRef }, body)));
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

    function apply(ctx) {
      installStyle();
      try { ctx.effect(function () { installStyle(); }, 'dsh-engineering-ui: styles'); } catch (e) { installStyle(); }
      try { ctx.effect(function () { return installFrameWidthSync(); }, 'dsh-engineering-ui: frame sync'); } catch (e) { installFrameWidthSync(); }
      try { ctx.effect(function () { return installPresetIcon(); }, 'dsh-engineering-ui: preset icon'); } catch (e) { installPresetIcon(); }
      try {
        ctx.slots.inject('conversation.input.dock', function () {
          return ctx.slots.register({ name: 'conversation.input.dock', id: 'eng-banner', order: -20 }, EngineeringBanner);
        });
      } catch (e) { console.error('[dsh-engineering-ui] banner slot', e); }
      try {
        ctx.slots.inject('shell.overlay', function () {
          return ctx.slots.register({ name: 'shell.overlay', id: 'eng-subagent-console', order: 31, label: '子代理控制台' }, SubagentConsole);
        });
      } catch (e) { console.error('[dsh-engineering-ui] console slot', e); }
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
    }
    exports.apply = apply;
    exports.inject = ['sessions', 'slots'];
    exports.SubagentConsole = SubagentConsole;
    exports.SubagentDock = SubagentConsole;
    exports.EngineeringBanner = EngineeringBanner;
    exports.formatMessage = formatMessage;
    exports.renderEvent = renderEvent;
    exports.AskCard = AskCard;
    exports.parseQuestions = parseQuestions;
    return module.exports;
  }
});
