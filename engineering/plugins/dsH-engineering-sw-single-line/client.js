/**
 * dsH-engineering-sw-single-line — Client 侧组件
 *
 * 功能：
 * 1. 非工程模式确认面板（输入框下方警告横幅）
 * 2. 子代理三栏布局（主对话 + 子代理列表 + 子代理工作区）
 */
window.__ModuleLoader__.load({
  id: 'dsH-engineering-sw-single-line',
  factory: function(require) {
    var module = { exports: {} };
    var exports = module.exports;
    Object.defineProperty(exports, Symbol.toStringTag, { value: 'Module' });

    var React = require('react');
    var useEffect = React.useEffect;
    var useState = React.useState;
    var useMemo = React.useMemo;

    // ── CSS ──────────────────────────────────────────────────────────────────
    var CSS = [
      /* 警告面板 */
      '.swsl-panel{display:flex;align-items:center;gap:10px;padding:8px 12px;margin:4px 0;border-radius:8px;font-size:13px;line-height:1.4;animation:swsl-fade-in .2s ease}',
      '.swsl-panel.warning{background:color-mix(in srgb,var(--dsw-alias-warn-fill,#fff3cd) 18%,transparent);border:1px solid var(--dsw-alias-warn-border,#ffc107);color:var(--dsw-alias-warn-text,#856404)}',
      '.swsl-icon{flex:none;font-size:16px}',
      '.swsl-text{flex:1;min-width:0}',
      '.swsl-title{font-weight:600;margin-bottom:1px;font-size:13px}',
      '.swsl-desc{opacity:0.85;font-size:12px}',
      '.swsl-btn{flex:none;padding:5px 14px;border-radius:6px;border:1px solid currentColor;background:transparent;color:inherit;cursor:pointer;font-size:12px;font-weight:500;transition:background .12s ease;white-space:nowrap}',
      '.swsl-btn:hover{background:color-mix(in srgb,currentColor 12%,transparent)}',
      '.swsl-btn.primary{background:var(--dsw-alias-button-primary-fill,var(--dsw-alias-brand-primary,#2563eb));border-color:var(--dsw-alias-button-primary-fill,var(--dsw-alias-brand-primary,#2563eb));color:#fff}',
      '.swsl-btn.primary:hover{opacity:0.88}',
      '@keyframes swsl-fade-in{from{opacity:0;transform:translateY(-3px)}to{opacity:1;transform:translateY(0)}}',
      
      /* 三栏布局 */
      '.swsl-tricoll{display:flex;height:100%;overflow:hidden;position:relative}',
      '.swsl-tricoll-main{flex:1;overflow:hidden;display:flex;flex-direction:column}',
      '.swsl-tricoll-divider{width:3px;background:var(--dsw-alias-border-l1,#e0e0e0);cursor:col-resize;flex:none;transition:background .15s}',
      '.swsl-tricoll-divider:hover{background:var(--dsw-alias-brand-primary,#2563eb)}',
      '.swsl-tricoll-sidebar{width:48px;background:var(--dsw-alias-bg-layer-1,#fff);border-left:1px solid var(--dsw-alias-border-l1,#e0e0e0);display:flex;flex-direction:column;overflow-y:auto;flex:none}',
      '.swsl-tricoll-workspace{flex:1;overflow:hidden;border-left:1px solid var(--dsw-alias-border-l1,#e0e0e0);background:var(--dsw-alias-bg-layer-2,#f5f5f5);display:flex;flex-direction:column}',
      '.swsl-agent-item{width:36px;height:36px;margin:4px auto;border-radius:8px;display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:11px;font-weight:600;transition:all .15s;border:2px solid transparent;position:relative}',
      '.swsl-agent-item:hover{background:var(--dsw-alias-bg-layer-3,#e8e8e8)}',
      '.swsl-agent-item.active{border-color:var(--dsw-alias-brand-primary,#2563eb);background:color-mix(in srgb,var(--dsw-alias-brand-primary,#2563eb) 15%,transparent)}',
      '.swsl-agent-item .status-dot{position:absolute;top:2px;right:2px;width:6px;height:6px;border-radius:50%;background:#22c55e}',
      '.swsl-agent-item .status-dot.inactive{background:#9ca3af}',
      '.swsl-agent-item .status-dot.running{background:#3b82f6;animation:pulse 1.5s infinite}',
      '@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}',
      '.swsl-workspace-header{padding:8px 12px;border-bottom:1px solid var(--dsw-alias-border-l1,#e0e0e0);font-size:12px;font-weight:600;color:var(--dsw-alias-label-secondary,#666);display:flex;align-items:center;gap:6px}',
      '.swsl-workspace-content{flex:1;overflow:auto;padding:12px;font-size:12px;line-height:1.6;color:var(--dsw-alias-label-primary,#333)}',
      '.swsl-workspace-empty{display:flex;align-items:center;justify-content:center;height:100%;color:var(--dsw-alias-label-tertiary,#999);font-size:12px}',
    ].join('');

    var tagId = 'dsH-engineering/sw-single-line/client.css';
    if (typeof document !== 'undefined') {
      var existing = document.querySelector('style[data-plugin-css=' + JSON.stringify(tagId) + ']');
      if (!existing) {
        var tag = document.createElement('style');
        tag.dataset.plugin = 'dsH-engineering/sw-single-line';
        tag.dataset.pluginCss = tagId;
        tag.textContent = CSS;
        document.head.appendChild(tag);
      }
    }

    // ── Warning Panel ────────────────────────────────────────────────────────
    function SwWarningPanel(props) {
      var useAgentPresets = props.useAgentPresets;
      var [dismissed, setDismissed] = useState(false);
      
      var presetState = useAgentPresets(function(s) { return s; });
      
      var isEngineering = useMemo(function() {
        var current = presetState.currentValue;
        if (current === 'engineering') return true;
        if (presetState.options) {
          return presetState.options.some(function(o) { return o.id === 'engineering'; });
        }
        return false;
      }, [presetState.currentValue, presetState.options]);
      
      if (isEngineering || dismissed) return null;
      
      return React.createElement('div', { className: 'swsl-panel warning', role: 'alert' },
        React.createElement('span', { className: 'swsl-icon' }, '⚠️'),
        React.createElement('div', { className: 'swsl-text' },
          React.createElement('div', { className: 'swsl-title' }, '权限模式提示'),
          React.createElement('div', { className: 'swsl-desc' },
            '识别到您开启的不是工程模式，您的权限可能会回退成默认的智能选择，是否继续？'
          )
        ),
        React.createElement('button', { className: 'swsl-btn', onClick: function() { /* 阻止发送 */ } }, '取消'),
        React.createElement('button', { className: 'swsl-btn primary', onClick: function() { setDismissed(true); } }, '继续')
      );
    }

    // ── Subagent Tricoll ─────────────────────────────────────────────────────
    function SubagentTricoll(props) {
      var useSubagents = props.useSubagents;
      var sessionId = props.sessionId;
      var [selectedAgent, setSelectedAgent] = useState(null);
      var [agents, setAgents] = useState([]);
      
      useEffect(function() {
        // 这里需要从子代理服务获取实时数据
        // 由于 DSH API 限制，我们使用轮询方式获取子代理列表
        var interval = setInterval(function() {
          // 实际实现需要通过 ctx.get('subagents') 或类似 API 获取
          // 这里使用模拟数据作为占位
          var mockAgents = [];
          // 检查是否有子代理在运行
          if (props.subagentCount > 0) {
            for (var i = 0; i < props.subagentCount; i++) {
              mockAgents.push({
                id: 'subagent-' + i,
                label: '子代理 ' + (i + 1),
                status: 'running',
                lastMessage: '正在执行任务...'
              });
            }
          }
          setAgents(mockAgents);
        }, 2000);
        return function() { clearInterval(interval); };
      }, [props.subagentCount]);
      
      // 没有子代理时不显示三栏布局
      if (agents.length === 0) return null;
      
      var selectedAgentData = agents.find(function(a) { return a.id === selectedAgent; }) || agents[0];
      
      return React.createElement('div', { className: 'swsl-tricoll', style: { position: 'absolute', inset: 0, zIndex: 100 } },
        // 主对话区（左侧 3/4）
        React.createElement('div', { className: 'swsl-tricoll-main' },
          React.createElement('div', { style: { padding: '4px 8px', fontSize: '11px', color: 'var(--dsw-alias-label-tertiary)', borderBottom: '1px solid var(--dsw-alias-border-l1)' } },
            '主对话区（子代理运行时自动扩展）'
          )
        ),
        // 分割线
        React.createElement('div', { className: 'swsl-tricoll-divider' }),
        // 子代理列表（中间 48px）
        React.createElement('div', { className: 'swsl-tricoll-sidebar' },
          agents.map(function(agent) {
            return React.createElement('div', {
              key: agent.id,
              className: 'swsl-agent-item' + (agent.id === selectedAgent ? ' active' : ''),
              onClick: function() { setSelectedAgent(agent.id); },
              title: agent.label
            },
              React.createElement('span', null, agent.label.substring(0, 2)),
              React.createElement('span', { className: 'status-dot' + (agent.status === 'inactive' ? ' inactive' : agent.status === 'running' ? ' running' : '') })
            );
          })
        ),
        // 分割线
        React.createElement('div', { className: 'swsl-tricoll-divider' }),
        // 子代理工作区（右侧 1/4）
        React.createElement('div', { className: 'swsl-tricoll-workspace' },
          React.createElement('div', { className: 'swsl-workspace-header' },
            React.createElement('span', null, '📊'),
            React.createElement('span', null, selectedAgentData ? selectedAgentData.label : '子代理工作区')
          ),
          React.createElement('div', { className: 'swsl-workspace-content' },
            selectedAgentData ? React.createElement('div', null,
              React.createElement('div', { style: { marginBottom: '8px', fontWeight: '600' } }, selectedAgentData.lastMessage),
              React.createElement('div', { style: { opacity: 0.7, fontSize: '11px' } }, '状态: ' + selectedAgentData.status)
            ) : React.createElement('div', { className: 'swsl-workspace-empty' }, '暂无子代理活动')
          )
        )
      );
    }

    // ── Apply Function ────────────────────────────────────────────────────────
    function apply(ctx) {
      // 本插件只保留 host 侧能力（工程模式自动把权限预设切到 sw-single-line）。
      // 客户端 UI（非工程模式警告横幅 + 子代理三栏）已由 dsh-engineering-ui 提供，功能重复；
      // 且旧写法不合法：slot 名带后缀、`conversation.session.overlay` 未声明、
      // register 缺 Component 参、读的是不存在的 props.useAgentPresets。
      // 故不再注册 slot（避免重复横幅 + 控制台报错）。
      
    }

    exports.apply = apply;
    exports.SwWarningPanel = SwWarningPanel;
    exports.SubagentTricoll = SubagentTricoll;
    exports.inject = ['slots'];

    return exports;
  }
});
