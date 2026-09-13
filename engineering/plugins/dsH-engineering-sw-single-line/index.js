/**
 * dsH-engineering-sw-single-line — Host 侧条件式预设激活服务
 *
 * 代码层面强制：当 engineering 预设激活时，自动将所有活跃会话的权限预设
 * 切换到 sw-single-line。非工程模式下回退到 workspace-write。
 */
import { Service } from '@deepseek-ai/cordis';

const ENGINEERING_PRESET = 'engineering';
const SW_SINGLE_LINE_PRESET = 'sw-single-line';
const DEFAULT_PRESET = 'workspace-write';

export class SwSingleLineModeService extends Service {
  /** 需要 permissionPresets 来切换预设，sessions 来遍历活跃会话 */
  static inject = ['permissionPresets', 'sessions'];

  constructor(ctx) {
    super(ctx);
    this._activePreset = null;

    // 监听 agent-preset/selected 事件
    // 事件格式: ctx.emit("agent-preset/selected", session.id, event.data.agentPreset)
    ctx.on('agent-preset/selected', (_, presetId) => {
      this._onPresetSelected(presetId);
    });

    // 监听 session 创建事件，对新会话立即应用
    ctx.on('session/created', (session) => {
      const presetId = this._resolveCurrentPreset(session);
      if (presetId === ENGINEERING_PRESET) {
        this._applyPreset(session, SW_SINGLE_LINE_PRESET);
      }
    });

    // 对现有会话立即应用
    this._applyToAllSessions();
  }

  /** 从 session events 中解析当前 agent preset */
  _resolveCurrentPreset(session) {
    if (!session || !session.events) return null;
    for (let i = session.events.length - 1; i >= 0; i--) {
      const ev = session.events[i];
      if (ev && ev.type === 'agent-preset/selected') {
        return ev.data && ev.data.agentPreset;
      }
    }
    return session.header && session.header.agentPreset;
  }

  /** 对现有会话立即应用：已是 engineering 的会话切换其权限预设到 sw-single-line */
  _applyToAllSessions() {
    const sessions = this.ctx.sessions.list();
    for (const session of sessions) {
      if (this._resolveCurrentPreset(session) === ENGINEERING_PRESET) {
        this._applyPreset(session, SW_SINGLE_LINE_PRESET);
      }
    }
  }

  /** 检测到 preset 选择变化时的处理 */
  _onPresetSelected(presetId) {
    if (presetId === this._activePreset) return;

    const prevActive = this._activePreset === ENGINEERING_PRESET;
    const nextActive = presetId === ENGINEERING_PRESET;

    if (prevActive && !nextActive) {
      // 从工程模式退出 → 回退所有 session 到默认预设
      this._activePreset = presetId;
      this._rollbackAllSessions();
    } else if (!prevActive && nextActive) {
      // 进入工程模式 → 激活 sw-single-line
      this._activePreset = presetId;
      this._activateAllSessions();
    }
  }

  /** 对所有活跃会话应用 sw-single-line 预设 */
  _activateAllSessions() {
    const sessions = this.ctx.sessions.list();
    for (const session of sessions) {
      this._applyPreset(session, SW_SINGLE_LINE_PRESET);
    }
  }

  /** 对所有活跃会话回退到默认预设 */
  _rollbackAllSessions() {
    const sessions = this.ctx.sessions.list();
    for (const session of sessions) {
      this._applyPreset(session, DEFAULT_PRESET);
    }
    this._activePreset = null;
  }

  /** 对单个 session 应用指定的权限预设 */
  _applyPreset(session, presetName) {
    try {
      const permService = this.ctx.permissionPresets;
      if (permService && typeof permService.set === 'function') {
        permService.set(session, presetName);
      }
    } catch (err) {
      // 静默失败：session 可能已销毁或 permissionPresets 未就绪
      this.ctx.logger?.warn?.(
        '[sw-single-line] failed to apply preset "' + presetName + '" to session ' + session.id + ': ' + err.message
      );
    }
  }
}

export default SwSingleLineModeService;
