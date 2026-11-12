/**
 * sw-monitor.js — 外部监控器（DSH-SW-Monitor）的宿主桥
 * ==========================================================================
 * 这个文件只做一件事：把"外部监控器的 HTTP 契约"翻译成 DSH 侧可以安全使用的
 * 几个原语。它【不】判断零件状态、【不】判定房间能否收尾、【不】维护队列 ——
 * 那些全部归监控器。宿主侧出现第二份判定就是双权威，而双权威是这套改造
 * 要根治的第一个问题。
 *
 * ── 为什么必须经过宿主，而不让 Python/模型直接连监控器 ────────────────────
 * 教程 §3.1：「许可信息要由宿主与真实会话、实际工具调用和 worker 绑定……
 *   禁止使用代理可自行填写的 room、lease 字符串来模拟该身份。」
 * 于是：
 *   · host.token 只在本模块的进程内存里，从不落磁盘、不进任何提示词/命令行；
 *   · epoch 由宿主生成并独占，模型拿不到也改不了；
 *   · Python 侧只能调本机宿主路由（/dsh-engineering-ui/sw-monitor-op），
 *     由宿主补 epoch / token 后再转发 —— 模型即使伪造 room 字符串，也拿不到
 *     别的会话的票（票作用域在监控器里按 epoch+task+room+session 绑定）。
 *
 * ── 与旧锁实现的关系 ──────────────────────────────────────────────────────
 * 旧实现（mode_state.sw_lock + sw-request/sw-wait/sw-release）在监控器接管
 * 期间【必须完全停用】。本模块不提供"回退到旧锁"的开关：回退只能通过
 * 删除 <状态目录>/sw_monitor.json 并显式停掉监控器宿主来完成，属于运维动作，
 * 不是运行时可选项（教程 §6「禁止双轨同时放行」）。
 */

import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';

/** 监控器配置文件名（放在工程状态目录里，与 mode_state.json 同级）。 */
export const CONFIG_NAME = 'sw_monitor.json';

/** 宿主桥对模型暴露的操作白名单。白名单之外一律拒绝（不转发、不猜测）。 */
const OPS = new Set([
  // 注意：这里列的是【监控器】的操作名，不是我们自己的封装名。
  // 早期版本写的是 'acquire'，而 monitor.py 只认 request/claim ——
  // 接入后会一路 404/UNKNOWN_OPERATION，表现为"永远拿不到许可"。
  'request', 'claim', 'cancel', 'release',
  'begin', 'end',
  'part-start', 'part-saved', 'part-failed',
  'board', 'wait-rooms', 'wait-lock',
  'review-part', 'review-room', 'close-room', 'verify-task',
  'reopen-room', 'room-failed', 'plan',
  'status', 'attach', 'recover',
]);

/** 需要 task 作用域的操作（监控器按 task 隔离房间与票据）。 */
const NEEDS_TASK = new Set([
  'request', 'claim', 'cancel', 'release', 'begin', 'end',
  'part-start', 'part-saved', 'part-failed',
  'board', 'wait-rooms', 'wait-lock', 'plan',
  'review-part', 'review-room', 'close-room', 'verify-task',
  'reopen-room', 'room-failed',
]);

/** 需要长时间阻塞的操作（HTTP 等待窗给足，其余给短窗）。 */
const LONG_OPS = new Set(['acquire', 'wait-rooms']);

/**
 * 读取接入配置。缺失 / 损坏 → null（= 未接入，调用方据此走"未接管"分支）。
 * @param {string} stateDir 工程状态目录
 */
export function readConfig(stateDir) {
  try {
    const file = path.join(stateDir, CONFIG_NAME);
    if (!fs.existsSync(file)) return null;
    const raw = JSON.parse(fs.readFileSync(file, 'utf8'));
    if (!raw || typeof raw !== 'object' || raw.enabled !== true) return null;
    if (!raw.url || typeof raw.url !== 'string') return null;
    if (!raw.tokenFile || typeof raw.tokenFile !== 'string') return null;
    return raw;
  } catch (e) {
    return null;
  }
}

/**
 * 连接管理。一个 DSH 进程只持有一条监控器连接（一个 epoch）。
 *
 * 为什么连接要懒建立并缓存：epoch 一旦变化，监控器会把旧 epoch 的所有
 * 未领取票作废（教程 §6「换 epoch 时旧等待票作废」）。如果每次调用都新建
 * 连接，排队中的房间会不停被作废 —— 那不是"更安全"，那是永远排不上队。
 */
export class MonitorBridge {
  /**
   * @param {object} opts
   * @param {() => string|null} opts.stateDir 返回工程状态目录（= tools 目录）
   * @param {(msg: string) => void} [opts.warn] 告警出口
   */
  constructor({ stateDir, warn } = {}) {
    this.stateDirFn = typeof stateDir === 'function' ? stateDir : () => stateDir;
    this.warn = typeof warn === 'function' ? warn : () => {};
    this.host = null;
    this.key = '';
    this.attached = false;
    this.lastError = '';
    this.startedAt = 0;
    this.adapter = null;
    /** room -> 当前票据（宿主私有）。Python 侧看不到 ticket，归还时由宿主补上。 */
    this.tickets = new Map();
  }

  /**
   * 懒加载 host-adapter.mjs。
   *
   * 为什么用动态 import 而不是静态 import：
   *   插件有两个部署位置 —— 源码 engineering/plugins/dsh-engineering-ui/lib/，
   *   以及安装副本 <profile>/node_modules/dsh-engineering-ui/lib/。
   *   后者与 tools/ 之间不存在稳定的相对路径（tools 在 .agent-presets 下）。
   *   唯一两边都成立的事实是「监控器内核跟着 tools 一起部署」——
   *   所以按运行时解析到的 tools 目录加载，而不是写死相对路径。
   */
  async loadAdapter() {
    if (this.adapter) return this.adapter;
    const dir = this.stateDirFn();
    if (!dir) throw new Error('未定位到 tools 目录，无法加载监控器适配器');
    const file = path.join(dir, 'sw_monitor', 'host-adapter.mjs');
    if (!fs.existsSync(file)) {
      throw new Error('找不到监控器适配器：' + file
        + '（sw_monitor/ 未随 tools 一起部署）');
    }
    const mod = await import(pathToFileURL(file).href);
    if (typeof mod.createMonitorHost !== 'function') {
      throw new Error('监控器适配器缺少 createMonitorHost 导出：' + file);
    }
    this.adapter = mod.createMonitorHost;
    return this.adapter;
  }

  /** 当前配置（每次现读 —— 接入/断开会话级生效，不需要重启 DSH）。 */
  config() {
    try {
      return readConfig(this.stateDirFn());
    } catch (e) {
      return null;
    }
  }

  enabled() {
    return !!this.config();
  }

  /**
   * 建立（或复用）连接。drained 表示"旧命令树 / COM 调用 / SW 文档操作已清空"，
   * 首次接管必须为 true（教程「首次接管前：先停旧队列入口」）。
   */
  async connect() {
    const cfg = this.config();
    if (!cfg) {
      this.lastError = '未接入监控器（缺少 ' + CONFIG_NAME + '）';
      return null;
    }
    const key = cfg.url + '|' + cfg.tokenFile;
    if (this.host && this.key === key) return this.host;
    try {
      const createMonitorHost = await this.loadAdapter();
      this.host = await createMonitorHost({ url: cfg.url, tokenFile: cfg.tokenFile });
      this.key = key;
      this.attached = false;
      this.lastError = '';
      this.startedAt = Date.now();
    } catch (e) {
      this.host = null;
      this.lastError = String((e && e.message) || e);
      throw e;
    }
    return this.host;
  }

  /** 首次接管：attach 且要求 drained。返回监控器状态（ready / quarantined）。 */
  async attach({ drained = false, recover = false, reason = '' } = {}) {
    const host = await this.connect();
    if (!host) return { state: 'unattached', error: this.lastError };
    const payload = { drained: !!drained };
    if (recover) {
      payload.reason = String(reason || '').trim();
      if (payload.reason.length < 5) {
        throw new Error('恢复必须写明原因（至少 5 个字符），这条要求是监控器侧的硬约束');
      }
    }
    const out = await host.rpc(recover ? 'recover' : 'attach', payload);
    this.attached = out.state === 'ready';
    if (out.state !== 'ready') {
      this.warn('监控器处于隔离状态（' + out.state + '），需要人工确认排空后 recover');
    }
    return out;
  }

  /** 后台心跳：失联时监控器会自动冻结已领取票（教程 §6 故障恢复 1）。 */
  startHeartbeat() {
    if (!this.host) return;
    this.host.startHeartbeat((msg) => this.warn('监控器心跳异常：' + msg));
  }

  stopHeartbeat() {
    try { if (this.host) this.host.stopHeartbeat(); } catch (e) {}
  }

  /**
   * 转发一次操作。
   * status 是本地合成的（不打扰监控器），其余全部原样转发。
   */
  async call(op, payload = {}) {
    if (!OPS.has(op)) throw new Error('UNKNOWN_MONITOR_OP: ' + op);
    if (op === 'status') return this.status();
    if (op === 'attach' || op === 'recover') {
      const out = await this.attach({
        drained: payload.drained === true,
        recover: op === 'recover',
        reason: payload.reason,
      });
      if (this.attached) this.startHeartbeat();
      return out;
    }
    const host = await this.connect();
    if (!host) {
      const e = new Error('MONITOR_UNAVAILABLE: ' + (this.lastError || '未接入监控器'));
      e.code = 'MONITOR_UNAVAILABLE';
      throw e;
    }
    if (!this.attached) {
      // 接管前必须先 attach(drained)。这里不做"自动 drained"的默认放行：
      // 没确认排空就 attach，等于让监控器在旧命令还在跑的时候放心发锁。
      const e = new Error('MONITOR_NOT_ATTACHED: 监控器尚未 attach(drained)');
      e.code = 'MONITOR_NOT_ATTACHED';
      throw e;
    }
    // 监控器按 task 隔离：除心跳/接管类操作外都必须带 task。
    //   task 由宿主从 workflow_state.json 读，模型无法伪造（也看不到）。
    let body = payload;
    // release / 收尾类操作：补上宿主保存的票（模型拿不到也不需要拿到）。
    if ((op === 'release' || op === 'cancel' || op === 'begin' || op === 'end' ||
         op === 'part-start' || op === 'part-saved' || op === 'part-failed') &&
        !body.ticket && body.room) {
      const t = this.tickets.get(String(body.room));
      if (t) body = Object.assign({}, body, { ticket: t });
    }
    if (NEEDS_TASK.has(op) && !body.task) {
      const task = this.currentTask();
      if (!task) {
        const e = new Error('TASK_UNKNOWN: 宿主读不到本任务的 task_id（workflow_state.json 缺失或未 select）');
        e.code = 'TASK_UNKNOWN';
        throw e;
      }
      body = Object.assign({}, body, { task });
    }
    const out = await host.rpc(op, body);
    if (op === 'release' && body.room) this.tickets.delete(String(body.room));
    return out;
  }

  /**
   * 读当前任务的 task_id。权威来源是 workflow_state.json（与门禁同一个文件），
   * 也就是教程第 2 步说的"本轮 task_id"。
   */
  currentTask() {
    try {
      const dir = this.stateDirFn();
      if (!dir) return null;
      const file = path.join(dir, 'workflow_state.json');
      if (!fs.existsSync(file)) return null;
      const st = JSON.parse(fs.readFileSync(file, 'utf8'));
      const tid = st && st.task_id;
      return tid ? String(tid) : null;
    } catch (e) {
      return null;
    }
  }

  /**
   * 「申请 SW 使用权」的高层封装：request → （队首则 claim）。
   *
   * 为什么封装：监控器把它拆成两步（request 建票 / claim 领租约），
   * 因为 claim 必须由宿主决定"现在真的要用 SW 了才领"。但上层
   * （Python 的 permit 端点）只关心"能不能动"。这里把两步合成一次调用，
   * 队列/票据语义仍然完全由监控器持有。
   *
   * 返回 { state: 'reserved'|'queued', ticket, lease?, position? }
   */
  async acquire({ room, session, wait_s = 0 } = {}) {
    const ticket = 'tk-' + randomUUID();
    let got = await this.call('request', {
      room, session: session || '', ticket,
      timeout: Math.max(0, Math.min(Number(wait_s) || 0, 25)),
    });
    // 队首会立刻被预留；否则在队列里等。wait_s>0 时让监控器自己阻塞到
    // 有结论（最多 25 秒，适配器会自动续窗）。
    if (got.state === 'reserved' || got.state === 'claimed') {
      const claimed = await this.call('claim', { room, session: session || '', ticket });
      if (room) this.tickets.set(String(room), ticket);
      return Object.assign({ ticket }, got, claimed);
    }
    if (room) this.tickets.set(String(room), ticket);
    return Object.assign({ ticket }, got);
  }

  /**
   * 本地状态摘要：给 mode_gate status / 设置页 / 看板降级用。
   * 绝不因为监控器不可达而抛错 —— 它本来就是用来看"为什么不可达"的。
   */
  async status() {
    const cfg = this.config() || {};
    const out = {
      ok: true,
      enabled: this.enabled(),
      attached: this.attached,
      url: cfg.url || null,
      epoch: (this.host && this.host.epoch) || null,
      connectedAt: this.startedAt || null,
      lastError: this.lastError || null,
    };
    if (!out.enabled) {
      out.hint = '未接入：缺少 ' + path.join(String(this.stateDirFn() || ''), CONFIG_NAME);
      return out;
    }
    try {
      const board = await this.call('board', {});
      out.pulse_alive = board.pulse_alive;
      out.quarantined = board.quarantined;
      out.task = board.task;
      out.revision = board.revision;
      out.summary = board.summary;
      out.sw = board.sw;
    } catch (e) {
      out.pulse_alive = false;
      out.error = String((e && e.message) || e);
      out.code = (e && e.code) || null;
    }
    return out;
  }

  /**
   * 挂一个"等待 SW 使用权"的 DSH 后台 job（图一的替换点）。
   * 返回 job id；job 正常结束（队首预留 / 失败 / 取消）。
   */
  startWaitLockJob(ctx, jobs, scope, owner) {
    if (!this.host) throw new Error('MONITOR_UNAVAILABLE');
    return this.host.startWaitJob(jobs, { owner, kind: 'wait-lock', payload: scope });
  }

  /**
   * 挂一个"等房间收尾"的 DSH 后台 job（主对话不再被 wait-rooms 卡住回合）。
   */
  startWaitRoomsJob(jobs, payload, owner) {
    if (!this.host) throw new Error('MONITOR_UNAVAILABLE');
    return this.host.startWaitJob(jobs, { owner, kind: 'wait-rooms', payload });
  }
}

/**
 * 生成一次命令 ID。宿主侧生成可以保证"未确认 begin 成功就不启动子进程"
 * （教程 §3.3：收到 begin 的回复不确定时，严禁启动命令）。
 */
export function newCommandId() {
  return randomUUID();
}
