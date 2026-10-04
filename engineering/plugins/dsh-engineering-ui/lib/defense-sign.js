// dsh-engineering-ui — 三大防线【签名式信任根】（仅工程模式）
// ==========================================================================
// 定位（用户给定）
//   签名 = 主防线：防"AI 直接 write 一份格式完美的伪造凭据"。
//   守卫 = 补丁：围绕签名服务，保证 defense_gate 读到的是签名认可的版本。
//
// 信任模型（必须读）
//   密钥 = HMAC-SHA256 随机密钥，【只存在于宿主进程内存】，绝不落盘。
//   因此：Python 侧【不持有密钥】—— 签名与验签都必须请求宿主。
//   这样即使 AI 读到工程模式目录下的所有文件，也拿不到签发能力。
//
// 已知残余风险（同用户、无隔离环境，用户已确认接受）
//   1. 能运行同用户任意代码的一方，理论上可伪造一个"假宿主"端点。
//   2. AI 可能抢在守卫扫描前写入。
//   守卫（文件监控 + 台账比对 + 自动恢复）用于缩减这两类窗口，
//   并使越权尝试留痕；它不产生信任，只保证校验读取的是认可版本。
//
// 签发约束（关键）
//   /sign 不签调用方给的任意内容：宿主【自己读磁盘报告】，
//   用文件真实内容（含 sha256）构造待签体，再签名。
//   调用方无法让宿主为"不存在的运行"或"与报告不符的结论"背书。
import crypto from 'node:crypto';
import fsSync from 'node:fs';
import pathSync from 'node:path';

/**
 * 签名有效期（毫秒）。超过即视为过期，防重放旧签名。
 *
 * ── 【观察点 14 修复 · 阻断级】从 10 分钟放宽到 24 小时 ──────────────────
 * 原值 `10 * 60 * 1000` 与工程模式的实际作业节奏不匹配，导致**多零件装配
 * 必然无法完成 room-end**：
 *   · 每个零件 `save()` 时各签发一份材料凭据，各自带独立 `_ts`；
 *   · `room-end` 要校验【房间内全部零件】的凭据；
 *   · 大型装配的零件分散保存（实测 9~21 件跨 18:30~18:51，跨度 21 分钟）；
 *   · 最早签发的凭据 10 分钟后即过期 → `host_verify_credential` 返回
 *     `{'ok': False, 'reason': 'signature expired'}` → 门禁报
 *     "【防线①材料】xxx.sldprt: 验签失败（signature expired）"。
 *   即：只要房间内零件数 > 1 且保存时间跨度 > 10 分钟，room-end 必然失败，
 *   只能靠 `--force` 绕过（留痕），三道防线被架空。
 *
 * 新值取 24 小时：覆盖"一天内完成一个装配任务"的正常节奏，同时仍能防住
 *   "隔天拿旧凭据重放"。可用 `DSH_DEFENSE_SIG_TTL_MS` 覆盖（毫秒）。
 *
 * ⚠️ 不要为了省事设成 `Infinity`：那会让重放旧凭据永久有效，防线失去意义。
 */
function resolveSigTtlMs() {
  try {
    const raw = process.env.DSH_DEFENSE_SIG_TTL_MS;
    if (raw !== undefined && raw !== null && String(raw).trim() !== '') {
      const v = Number(String(raw).trim());
      if (Number.isFinite(v) && v > 0) return v;
    }
  } catch (e) {
    // 环境变量不可读 → 用默认值
  }
  return 24 * 60 * 60 * 1000;
}
export const SIG_TTL_MS = resolveSigTtlMs();
/** nonce 缓存上限（用于 this.nonces 的过期清理阈值）。 */
const NONCE_MAX = 4000;
/**
 * 已签发 nonce 台账的【硬上限】（仅防内存无界增长，不参与正确性判定）。
 *
 * ── 【观察点 14 修复·连带】必须显著大于"一个任务可能签发的凭据数" ──────
 * 原实现用 NONCE_MAX(4000) 当裁剪线，且 TTL 只有 10 分钟。TTL 放宽到 24 小时
 * 后，一天内签发量很容易超过 4000（每个零件至少 1 份材料凭据，加上物理/领域
 * 凭据与重签），若仍按 4000 裁剪就会把【仍在有效期内】的 nonce 删掉，
 * 使合法凭据被 verify() 判为 'nonce not issued by this host'。
 * 故这里给一个宽松的兜底值；真正决定 nonce 是否可用的判据是【年龄】。
 */
const NONCE_HARD_MAX = 200000;
/** 工程模式下的防线目录名。 */
export const DEFENSE_DIR_NAME = '.defense';

/**
 * 规范化 JSON：递归按键排序、无多余空白。
 * 签名与验签必须使用完全相同的字节序列。
 * @param value - 可 JSON 化的值。
 * @returns 规范字符串。
 */
export function canonicalJson(value) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return '[' + value.map(canonicalJson).join(',') + ']';
  const keys = Object.keys(value).sort();
  return '{' + keys.map((k) => JSON.stringify(k) + ':' + canonicalJson(value[k])).join(',') + '}';
}

/** 计算文件的 sha256（读取失败返回 null）。 */
function fileSha256(p) {
  try {
    return crypto.createHash('sha256').update(fsSync.readFileSync(p)).digest('hex');
  } catch (e) {
    return null;
  }
}

/**
 * 三大防线的签名服务与守卫。仅由工程模式装配。
 */
export class DefenseSigner {
  /**
   * @param toolsDir - 工程模式 tools 目录（绝对路径）。
   * @param onLog - 日志回调 (msg: string) => void。
   */
  constructor(toolsDir, onLog = null) {
    this.toolsDir = toolsDir;
    this.log = typeof onLog === 'function' ? onLog : () => {};
    this.dir = pathSync.join(toolsDir, DEFENSE_DIR_NAME);
    this.key = crypto.randomBytes(32);
    this.kid = crypto.createHash('sha256').update(this.key).digest('hex').slice(0, 16);
    this.startedAt = Date.now();
    /** nonce → 首次出现时间（防重放）。 */
    this.nonces = new Map();
    /**
     * 本宿主签发过的 nonce 台账：验签时核对，挡住伪造/跨语境复用。
     *
     * ── 【观察点 14 修复·连带】类型从 Set 改为 Map(nonce → 签发时间) ──────
     * 只存 nonce 无法按【年龄】裁剪；TTL 放宽到 24 小时后必须能区分
     * "已过期可删" 与 "仍在有效期内必须保留"，否则合法凭据会被误判为伪造。
     */
    this.signedNonces = new Map();
    /** 签发台账（审计与守卫比对）。 */
    this.ledger = [];
    /** 守卫观察到的基线：路径 → sha256。 */
    this.baseline = new Map();
    /** 守卫发现的异常。 */
    this.anomalies = [];
    this.guardTimer = null;
    this.initRuntimeFile();
  }

  /** 写运行时发现文件，供 Python 侧定位宿主端点。 */
  initRuntimeFile() {
    try {
      fsSync.mkdirSync(this.dir, { recursive: true });
      this.runtimePath = pathSync.join(this.dir, 'runtime.json');
      this.writeRuntime();
      this.log('defense signer ready, kid=' + this.kid);
    } catch (e) {
      this.log('defense signer init failed: ' + String((e && e.message) || e));
    }
  }

  /**
   * 写入/刷新运行时文件。
   * @param port - 宿主 HTTP 端口（可用时）。
   */
  writeRuntime(port) {
    try {
      if (typeof port === 'number') this.port = port;
      // [Bug8 修复] 不得让非宿主进程覆盖 runtime.json。
      // 实测：任何加载本插件的进程（测试脚本、临时 node、第二个 DSH
      //   实例）都会调用本方法，把 port 写成 null、pid 写成自己，
      //   于是真正宿主的端点信息被覆盖，Python 侧 _host_base() 返回
      //   None，三道防线全部 fail-closed（表现为签名端点未装配）。
      if (this.port === undefined || this.port === null) {
        this.log('runtime not written: no port available (non-host context)');
        return;
      }
      try {
        if (fsSync.existsSync(this.runtimePath)) {
          const prev = JSON.parse(fsSync.readFileSync(this.runtimePath, 'utf8'));
          const prevTitle = String(prev.process_title || '');
          const curTitle = String(process.title || '');
          const looksHost = (t) => /deepseek|harness|electron/i.test(t);
          if (prev.pid && prev.port && looksHost(prevTitle) && !looksHost(curTitle)) {
            this.log('runtime not overwritten: existing record belongs to host pid='
                     + String(prev.pid));
            return;
          }
        }
      } catch (e) {
        // 读不到旧记录时按可写处理
      }
      fsSync.writeFileSync(this.runtimePath, JSON.stringify({
        kid: this.kid,
        port: this.port,
        host: '127.0.0.1',
        // pid 供 Python 侧做端口归属校验（堵假宿主冒充）。
        pid: process.pid,
        process_title: process.title || 'dsh',
        startedAt: this.startedAt,
        style: 'hmac-host-mediated',
      }, null, 2), 'utf8');
    } catch (e) {
      this.log('runtime write failed: ' + String((e && e.message) || e));
    }
  }

  /** 清理过期 nonce。 */
  prune() {
    const now = Date.now();
    if (this.nonces.size <= NONCE_MAX) return;
    for (const [n, t] of this.nonces) {
      if (now - t > SIG_TTL_MS) this.nonces.delete(n);
    }
  }

  /**
   * 对宿主【自行从磁盘确认】的内容签名。
   * @param payload - 已由宿主构造、含磁盘事实的载荷。
   * @returns 签名封装或 null。
   */
  sign(payload) {
    try {
      this.prune();
      const nonce = crypto.randomBytes(16).toString('hex');
      const ts = Date.now();
      // 关键：签名对象 = 【元字段 + 载荷】，验签时先剥离元字段再重算，
      //   两侧必须用同一套字节。这里直接对 payload 签名，元字段不参与，
      //   避免"签的时候带 _ts、验的时候剥离后重算"造成永不匹配。
      const mac = crypto.createHmac('sha256', this.key)
        .update(canonicalJson(payload), 'utf8').digest('base64');
      this.nonces.set(nonce, ts);
      if (!this.signedNonces) this.signedNonces = new Map();
      this.signedNonces.set(nonce, ts);
      // ── 【观察点 14 修复·连带】nonce 台账改为【按年龄】裁剪 ──────────────
      // 原实现按【条数】裁剪（> NONCE_MAX*2 就只留最近 NONCE_MAX 条），
      //   当时的理由是"凭据 TTL 仅 10 分钟，更早的 nonce 对应凭据早已过期"。
      //   TTL 放宽到 24 小时后该前提不再成立：若一天内签发超过 NONCE_MAX 份
      //   凭据，早期 nonce 会被裁掉，而它对应的凭据【仍在有效期内】→
      //   verify() 返回 'nonce not issued by this host' → 凭据被误判为伪造。
      // 现改为：① 先按 TTL 清掉确实过期的 nonce（过期凭据本就该失败）；
      //         ② 再保留一个【宽松的】硬上限兜底内存（NONCE_HARD_MAX），
      //            仅防无界增长，正常装配任务远达不到。
      this.pruneSignedNonces(ts);
      this.ledger.push({ kind: 'sign', ts, nonce, room: payload.room, run_id: payload.run_id });
      if (this.ledger.length > 4000) this.ledger = this.ledger.slice(-4000);
      return { _sig: mac, _kid: this.kid, _ts: ts, _nonce: nonce };
    } catch (e) {
      this.log('sign failed: ' + String((e && e.message) || e));
      return null;
    }
  }

  /**
   * 清理已过期的已签发 nonce 台账。
   *
   * 只要 nonce 对应的凭据可能仍在 TTL 内，就必须保留它 —— 否则 verify()
   * 会把一份【合法且未过期】的凭据判成 'nonce not issued by this host'。
   * @param now - 当前时间戳（毫秒）。
   */
  pruneSignedNonces(now) {
    if (!this.signedNonces || this.signedNonces.size === 0) return;
    // ① 按年龄清理（过期的凭据本来就会因 'signature expired' 被拒，无需再记）
    for (const [n, t] of this.signedNonces) {
      if (now - t > SIG_TTL_MS) this.signedNonces.delete(n);
    }
    // ② 硬上限兜底：只防无界增长，不参与正确性判定
    if (this.signedNonces.size > NONCE_HARD_MAX) {
      const keep = Array.from(this.signedNonces.entries())
        .sort((a, b) => a[1] - b[1])
        .slice(-NONCE_HARD_MAX);
      this.signedNonces = new Map(keep);
    }
  }

  /** 拆出签名元字段，返回待验签的载荷体。 */
  static stripMeta(cred) {
    const body = {};
    for (const k of Object.keys(cred || {})) {
      if (k === '_sig' || k === '_kid' || k === '_ts' || k === '_nonce') continue;
      body[k] = cred[k];
    }
    return body;
  }

  /**
   * 验签：校验 kid、时间窗、nonce 未复用过、MAC 一致。
   * @param cred - 完整凭据（含 _sig/_kid/_ts/_nonce）。
   * @returns {{ok: boolean, reason?: string}}
   */
  verify(cred, consume = true) {
    try {
      if (!cred || typeof cred !== 'object') return { ok: false, reason: 'malformed credential' };
      const sig = cred._sig;
      if (!sig) return { ok: false, reason: 'missing signature' };
      if (cred._kid !== this.kid) {
        return { ok: false, reason: 'key id mismatch (signer restarted?)' };
      }
      const ts = Number(cred._ts);
      if (!Number.isFinite(ts)) return { ok: false, reason: 'missing/invalid timestamp' };
      if (Date.now() - ts > SIG_TTL_MS) return { ok: false, reason: 'signature expired' };
      if (ts - Date.now() > 60 * 1000) return { ok: false, reason: 'timestamp in the future' };
      const nonce = String(cred._nonce || '');
      if (!nonce) return { ok: false, reason: 'missing nonce' };
      // 【缺口2 修复】nonce 必须真正起作用。
      //   注意：凭据是【静态事实声明】（"房间 R 在 run X 通过了校核"），
      //   门禁需要对同一份凭据重复校验（room-end → confirm-assembly → finish），
      //   因此【不能】把"用过的 nonce"一律拒绝（那会让门禁第二次检查就失败）。
      //   nonce 的正确职责是【台账核对】：必须是本宿主签发过的 nonce，
      //   从而挡住"伪造/跨语境复用"；重复校验是允许的。
      if (this.signedNonces && !this.signedNonces.has(nonce)) {
        return { ok: false, reason: 'nonce not issued by this host (forged or foreign credential)' };
      }
      const body = DefenseSigner.stripMeta(cred);
      const expect = crypto.createHmac('sha256', this.key)
        .update(canonicalJson(body), 'utf8').digest('base64');
      const a = Buffer.from(expect);
      const b = Buffer.from(String(sig));
      if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) {
        return { ok: false, reason: 'signature mismatch' };
      }
      return { ok: true, nonce };
    } catch (e) {
      return { ok: false, reason: String((e && e.message) || e) };
    }
  }

  /**
   * 为物理凭据构造待签体：宿主【自己读报告】，不信调用方给的结论。
   * @param room - 房间名。
   * @param reportPath - 报告绝对路径（必须在受管根目录内）。
   * @returns {{ok: boolean, payload?: object, reason?: string}}
   */
  buildPhysicsPayload(room, reportPath) {
    try {
      const roots = this.managedRoots();
      const abs = pathSync.resolve(String(reportPath || ''));
      const under = roots.some((r) => abs === r || abs.startsWith(r + pathSync.sep));
      if (!under) return { ok: false, reason: 'report path outside managed physics_runs root' };
      let rep;
      try {
        rep = JSON.parse(fsSync.readFileSync(abs, 'utf8'));
      } catch (e) {
        return { ok: false, reason: 'report unreadable or invalid JSON' };
      }
      if (!rep || typeof rep !== 'object') return { ok: false, reason: 'report is not an object' };
      const needs = ['schema_version', 'run_id', 'fea_result', 'geometry_gate', 'overall_status'];
      const missing = needs.filter((k) => !(k in rep));
      if (missing.length) {
        return { ok: false, reason: 'report missing fields: ' + missing.join(',') };
      }
      const repRoom = String(rep.room || '').trim();
      if (repRoom && repRoom !== String(room)) {
        return { ok: false, reason: 'report belongs to another room' };
      }
      // ── 待签体完全由【磁盘事实】构造，调用方无法指定结论 ──────────────
      // 【签后补字段 BUG 修复】原先宿主只签 overall_status / report_path 等少数
      //   字段，而校验端（defense_gate.check_physics_attestation）读的是
      //   overall / safety_factor / run_dir / at 等另一批名字。Python 侧只能在
      //   签名【之后】往凭据里补这些字段 —— 于是 HMAC 必然失配，防线②③永远
      //   验签失败（只能靠 --force 绕过）。
      //   正确做法：把所有【校验端会读的字段】全部纳入待签体，由宿主从
      //   磁盘报告里取出并签名；Python 侧此后一个字段都不许再改。
      const _fea = rep.fea_result || {};
      const _fat = _fea.fatigue || {};
      const payload = {
        kind: 'physics',
        // schema / at / ts 由宿主在签名时写定，Python 侧不得再补（否则验签失配）
        schema: 'dsh-physics-attestation/1',
        at: new Date().toISOString(),
        ts: Date.now(),
        room: String(room),
        run_id: String(rep.run_id || ''),
        report_path: abs,
        report_sha256: fileSha256(abs),
        // run_dir：由已签名的 report_path 推导，无需调用方另行补字段
        run_dir: pathSync.dirname(abs),
        // overall 与 overall_status 同值：前者是校验端的读名，后者是报告原名
        overall: String(rep.overall_status || ''),
        overall_status: String(rep.overall_status || ''),
        safety_factor: _fea.safety_factor === undefined ? null : _fea.safety_factor,
        max_von_mises_mpa: _fea.max_von_mises_mpa === undefined ? null : _fea.max_von_mises_mpa,
        max_displacement_mm: _fea.max_displacement_mm === undefined ? null : _fea.max_displacement_mm,
        passed_gates: Array.isArray(rep.passed_gates) ? rep.passed_gates : [],
        failed_gates: Array.isArray(rep.failed_gates) ? rep.failed_gates : [],
        not_evaluated: Array.isArray(rep.not_evaluated) ? rep.not_evaluated : [],
        release_status: String((rep.release_readiness || {}).status || ''),
        fatigue_verdict: String(_fat.verdict || ''),
        fatigue_sf: _fat.fatigue_sf === undefined ? null : _fat.fatigue_sf,
        source: 'physics_bridge',
      };
      return { ok: true, payload };
    } catch (e) {
      return { ok: false, reason: String((e && e.message) || e) };
    }
  }

  /** 受管的物理运行根目录。 */
  managedRoots() {
    // ── 【Bug8 修复】必须覆盖【两个部署位置】────────────────────────────
    // 工程模式有"两份副本"：源码工作区 与 ~/.dsh 安装目录。
    //   宿主按【自己的 toolsDir】(安装目录) 算受管根，而小屋脚本在
    //   【工作区】里跑、报告也写在工作区 output/ → 两边路径对不上，
    //   于是 /defense/sign 一律以 "report path outside managed
    //   physics_runs root" 拒签，防线②凭据永远签不出来。
    // 现在把所有已知落点都纳入受管根（宁宽勿误拒，安全由"报告必须
    //   真实存在 + 结论一致 + 房间归属"共同保证）。
    const cands = [
      pathSync.join(this.toolsDir, "..", "..", "output", "physics_runs"),
      pathSync.join(this.toolsDir, "output", "physics_runs"),
      pathSync.join(this.toolsDir, "..", "output", "physics_runs"),
    ];
    // 额外：DSH_HOME 下的安装位置，以及工作区（含 engineering/ 与根目录）
    try {
      const home = process.env.USERPROFILE || process.env.HOME || "";
      const dshHome = process.env.DSH_HOME || (home ? pathSync.join(home, ".dsh") : "");
      if (dshHome) {
        cands.push(pathSync.join(dshHome, ".agent-presets", "engineering",
                                 "tools", "..", "..", "output", "physics_runs"));
        cands.push(pathSync.join(dshHome, "engineering", "output", "physics_runs"));
      }
    } catch (e) {
      // 环境变量缺失不影响主候选
    }
    // ── 工作区副本（关键）：宿主只知道自己那份 toolsDir，但小屋脚本与
    //   报告常常在【源码工作区】。这里主动读取工程模式根目录线索：
    //   ① 环境变量 DSH_ENGINEERING_ROOT（SKILL 文档推荐的权威来源）
    //   ② <toolsDir>/.defense/workspace.txt（由 Python 侧写入的落点声明）
    try {
      const _eng = process.env.DSH_ENGINEERING_ROOT;
      if (_eng) {
        cands.push(pathSync.join(_eng, "output", "physics_runs"));
        cands.push(pathSync.join(_eng, "..", "output", "physics_runs"));
      }
    } catch (e) {
      // 环境变量缺失不影响主候选
    }
    try {
      const _wsFile = pathSync.join(this.dir, "workspace.txt");
      if (fsSync.existsSync(_wsFile)) {
        // workspace.txt 可含【多行】根目录（Python 侧会写入仓库根与
        //   engineering 两处，见 defense_gate._publish_workspace_root）。
        const _wsRoots = String(fsSync.readFileSync(_wsFile, "utf8"))
          .split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
        for (const _wsRoot of _wsRoots) {
          cands.push(pathSync.join(_wsRoot, "output", "physics_runs"));
          cands.push(pathSync.join(_wsRoot, "engineering", "output", "physics_runs"));
          cands.push(pathSync.join(_wsRoot, "engineering", "tools", "output", "physics_runs"));
        }
      }
    } catch (e) {
      // 声明文件缺失不影响主候选
    }
    return cands.map((p) => pathSync.resolve(p));
  }

  /** 启动守卫：周期性核对凭据与台账，发现异常即记录并可恢复。 */
  startGuard(intervalMs = 5000) {
    if (this.guardTimer) return;
    this.snapshotBaseline();
    this.guardTimer = setInterval(() => {
      try {
        this.guardTick();
      } catch (e) {
        this.log('guard tick error: ' + String((e && e.message) || e));
      }
    }, intervalMs);
    if (this.guardTimer && typeof this.guardTimer.unref === 'function') this.guardTimer.unref();
  }

  /** 停止守卫。 */
  stopGuard() {
    if (this.guardTimer) {
      clearInterval(this.guardTimer);
      this.guardTimer = null;
    }
  }

  /** 取当前 snapshot 范围（.defense 下的公钥/运行文件 + 凭据目录）。 */
  observedPaths() {
    const out = [];
    const defDir = this.dir;
    for (const f of ['runtime.json', 'public.pem', 'kid.txt']) {
      out.push(pathSync.join(defDir, f));
    }
    const reportsDir = pathSync.join(this.toolsDir, 'reports');
    try {
      for (const name of fsSync.readdirSync(reportsDir)) {
        if (name.endsWith('.physics.json') || name.endsWith('.domain.json')) {
          out.push(pathSync.join(reportsDir, name));
        }
      }
    } catch (e) {
      // reports 目录尚不存在：无凭据可观察
    }
    return out;
  }

  /** 记录基线哈希。 */
  snapshotBaseline() {
    for (const p of this.observedPaths()) {
      const h = fileSha256(p);
      if (h) this.baseline.set(p, h);
    }
  }

  /**
   * 守卫单次巡检：凭据必须带有效签名；被改动过的凭据记异常。
   * 这是"补丁"：不产生信任，只保证校验读取的是签名认可的版本。
   */
  guardTick() {
    const reportsDir = pathSync.join(this.toolsDir, 'reports');
    let names = [];
    try {
      names = fsSync.readdirSync(reportsDir);
    } catch (e) {
      return;
    }
    for (const name of names) {
      if (!(name.endsWith('.physics.json') || name.endsWith('.domain.json'))) continue;
      const p = pathSync.join(reportsDir, name);
      const h = fileSha256(p);
      if (!h) continue;
      // 未带签名 → 异常（合法凭据必带签名）
      let cred = null;
      try {
        cred = JSON.parse(fsSync.readFileSync(p, 'utf8'));
      } catch (e) {
        this.flag('credential-unreadable', p, 'JSON 解析失败');
        continue;
      }
      // 【缺口4 修复】baseline 真正启用：巡检发现凭据相对基线被改写时留痕。
      //   原实现 snapshotBaseline() 存了快照却从不读，宣称的"篡改检测"未实现。
      const base = this.baseline.get(p);
      if (base && base !== h) {
        this.note('credential-modified', p, '凭据在基线之后被改写（可能是签发者重写，也可能是外部篡改）');
      }
      this.baseline.set(p, h);
      if (!cred || !cred._sig) {
        this.flag('credential-unsigned', p, '凭据缺少签名（疑似手写伪造）');
        continue;
      }
      // 守卫巡检用 consume=false：只读校验，不消费 nonce，也不把
      // "仅因 TTL 过期"的合法凭据当作攻击 —— 过期只记录、不隔离。
      const v = this.verify(cred, false);
      if (!v.ok) {
        const reason = String(v.reason || '');
        if (reason.indexOf('expired') >= 0) {
          // 【缺口3 修复】过期 ≠ 伪造。隔离会造成"昨天签的合法凭据凭空消失"，
          //   这是破坏性假阳性。只记录，交由 defense_gate 按"过期"正常拒绝。
          this.note('credential-expired', p, reason);
          continue;
        }
        this.flag('credential-signature-invalid', p, reason);
        continue;
      }
      // 与台账交叉核对：该房间确有签发记录
      const room = String(cred.room || '');
      const has = this.ledger.some((e) => e.kind === 'sign' && e.room === room);
      if (!has) this.flag('credential-not-in-ledger', p, '台账无该房间的签发记录');
    }
  }

  /**
   * 记录一条守卫观察（不触发隔离）。
   *
   * 与 flag() 的区别：过期等"非攻击"情形只留痕，不移除凭据 ——
   * 否则守卫本身会成为破坏源（缺口3）。
   * @param kind - 观察类别。
   * @param path - 相关文件路径。
   * @param detail - 细节。
   */
  note(kind, path, detail) {
    const entry = { at: new Date().toISOString(), kind, path, detail };
    this.anomalies.push(entry);
    if (this.anomalies.length > 500) this.anomalies = this.anomalies.slice(-500);
    this.log('defense guard (note): ' + kind + ' @ ' + path + ' :: ' + detail);
  }

  /**
   * 记录一次守卫异常，并把未通过验签的凭据【隔离】。
   *
   * 隔离（而非"恢复原文件"）才是凭据场景下的正确语义：一份无效凭据
   * 无法凭空重建为有效版本 —— 重建需要重跑真实校核。因此守卫的作用是
   * 把它移出校验位置，让 defense_gate 因"缺少凭据"而 fail-closed，
   * 同时保留原件供审计。这正对应"读不到合法文件时让校验失败"。
   */
  flag(kind, path, detail) {
    const entry = { at: new Date().toISOString(), kind, path, detail };
    this.anomalies.push(entry);
    if (this.anomalies.length > 500) this.anomalies = this.anomalies.slice(-500);
    this.log('defense guard: ' + kind + ' @ ' + path + ' :: ' + detail);
    if (kind === 'credential-unsigned' || kind === 'credential-signature-invalid'
        || kind === 'credential-not-in-ledger' || kind === 'credential-unreadable') {
      this.quarantine(path, kind);
    }
    try {
      fsSync.writeFileSync(pathSync.join(this.dir, 'guard.json'),
        JSON.stringify({ items: this.anomalies.slice(-200) }, null, 2), 'utf8');
    } catch (e) {
      // 守卫报告写入失败不影响巡检本身
    }
  }

  /**
   * 把无效凭据移入 .defense/quarantine/，并记下时间戳。
   *
   * 只移动失败一次的文件；重复移动同一路径会被跳过，避免频繁 IO。
   * @param path - 凭据绝对路径。
   * @param kind - 异常类别。
   */
  quarantine(path, kind) {
    try {
      if (!this.quarantined) this.quarantined = new Set();
      if (this.quarantined.has(path)) return;
      if (!fsSync.existsSync(path)) return;
      const qdir = pathSync.join(this.dir, 'quarantine');
      fsSync.mkdirSync(qdir, { recursive: true });
      const stamp = new Date().toISOString().replace(/[:.]/g, '-');
      const dest = pathSync.join(qdir, stamp + '__' + pathSync.basename(path));
      fsSync.renameSync(path, dest);
      this.quarantined.add(path);
      this.anomalies.push({
        at: new Date().toISOString(),
        kind: 'credential-quarantined',
        path,
        dest,
        detail: '无效凭据（' + kind + '）已隔离；defense_gate 将因缺少凭据而 fail-closed',
      });
      // 隔离后基线失效，重新采样
      this.baseline.delete(path);
    } catch (e) {
      this.log('quarantine failed: ' + String((e && e.message) || e));
    }
  }

  /**
   * 宿主侧执行一次完整防线判定（C 方案：判定权收回宿主）。
   *
   * 为什么必须由宿主判定：Python 侧信任的是"某个 HTTP 端点"，而该端点
   * 可以被同用户进程冒充（对抗测试已实证：伪造 runtime.json 指向一个
   * 恒真服务即可整体绕过）。把判定搬进宿主后，AI 在 Python 侧不再有
   * 可冒充的判定点 —— 它要么让真宿主判定，要么篡改 defense_gate 本身
   * （那是另一类攻击，且守约会记录凭据异常）。
   *
   * @param room - 房间名。
   * @param kind - 'room' | 'task'。
   * @param opts - { requirePhysics, rooms } 等上下文，由 Python 传入。
   * @returns 判定结果（结构同 defense_gate.check_room_defense）。
   */
  judgeDefense(room, kind, opts = {}) {
    const out = { ok: false, room, blockers: [], warnings: [], details: {}, judged_by: 'host' };
    try {
      const reportsDir = pathSync.join(this.toolsDir, 'reports');
      const requirePhysics = opts.requirePhysics !== false;
      // ── 物理凭据 ──
      if (requirePhysics) {
        const ph = this.readCredential(reportsDir, room, '.physics.json');
        if (!ph) {
          out.blockers.push('【防线②物理】缺少物理校核凭据或验签失败（reports/' + room + '.physics.json）');
        } else {
          const overall = String(ph.overall_status || ph.overall || '').toUpperCase();
          if (overall === 'FAIL') {
            out.blockers.push('【防线②物理】物理校核判定为 FAIL');
          } else if (overall !== 'PASS' && overall !== 'REVIEW') {
            out.blockers.push('【防线②物理】物理校核结论缺失或无法识别（' + overall + '）');
          } else if (overall === 'REVIEW') {
            out.warnings.push('【防线②物理】物理校核为 REVIEW，已放行但留痕');
          }
          out.details.physics = { overall, run_id: ph.run_id, verified: true };
        }
        const dm = this.readCredential(reportsDir, room, '.domain.json');
        if (!dm) {
          out.blockers.push('【防线③领域】缺少领域校验凭据或验签失败（reports/' + room + '.domain.json）');
        } else {
          const v = Array.isArray(dm.violations) ? dm.violations : null;
          if (v === null) out.blockers.push('【防线③领域】violations 不是数组');
          else if (v.length) out.blockers.push('【防线③领域】存在 ' + v.length + ' 条 CRITICAL 违规');
          out.details.domain = { domain: dm.domain, violations: v ? v.length : null, verified: true };
        }
      }
      // ── 材料凭据（本房间登记零件）──
      const mats = this.checkRoomMaterials(room);
      out.details.material = mats;
      for (const b of mats.blockers) out.blockers.push(b);
      out.ok = out.blockers.length === 0;
      return out;
    } catch (e) {
      // 宿主判定异常 → fail-closed
      out.blockers.push('宿主判定异常（fail-closed）: ' + String((e && e.message) || e));
      out.ok = false;
      return out;
    }
  }

  /**
   * 读取并验签一份凭据；验签失败或缺失返回 null。
   * @param reportsDir - reports 目录。
   * @param room - 房间名。
   * @param suffix - '.physics.json' / '.domain.json'。
   * @returns 验签通过的凭据体，或 null。
   */
  readCredential(reportsDir, room, suffix) {
    try {
      const safe = String(room).replace(/[^A-Za-z0-9_\u4e00-\u9fa5 -]/g, '_');
      const fp = pathSync.join(reportsDir, safe + suffix);
      if (!fsSync.existsSync(fp)) return null;
      let cred = null;
      try {
        cred = JSON.parse(fsSync.readFileSync(fp, 'utf8'));
      } catch (e) {
        return null;
      }
      // 巡检模式验签：不消费 nonce（判定可能被重复调用）
      const v = this.verify(cred, false);
      if (!v.ok) return null;
      return cred;
    } catch (e) {
      return null;
    }
  }

  /**
   * 校验本房间登记零件的材料凭据（签名 + 哈希绑定）。
   * @param room - 房间名。
   * @returns {{part_count: number, blockers: string[], warnings: string[]}}
   */
  checkRoomMaterials(room) {
    const out = { part_count: 0, blockers: [], warnings: [] };
    try {
      const regPath = pathSync.join(this.toolsDir, 'artifacts_registry.json');
      if (!fsSync.existsSync(regPath)) return out;
      let reg = null;
      try {
        reg = JSON.parse(fsSync.readFileSync(regPath, 'utf8'));
      } catch (e) {
        return out;
      }
      const list = reg && reg[room];
      if (!Array.isArray(list)) return out;
      for (const it of list) {
        if (!it || typeof it !== 'object') continue;
        const part = String(it.path || '');
        if (!part.toLowerCase().endsWith('.sldprt')) continue;
        if (!fsSync.existsSync(part)) continue;
        out.part_count += 1;
        const attPath = part + '.material.json';
        if (!fsSync.existsSync(attPath)) {
          out.blockers.push('【防线①材料】' + pathSync.basename(part) + ': 缺少材料凭据');
          continue;
        }
        let att = null;
        try {
          att = JSON.parse(fsSync.readFileSync(attPath, 'utf8'));
        } catch (e) {
          out.blockers.push('【防线①材料】' + pathSync.basename(part) + ': 凭据不可解析');
          continue;
        }
        const v = this.verify(att, false);
        if (!v.ok) {
          out.blockers.push('【防线①材料】' + pathSync.basename(part) + ': 验签失败（' + v.reason + '）');
          continue;
        }
        // 哈希绑定：凭据记录的 part_sha256 必须与当前文件一致
        const nowSha = fileSha256(part);
        if (att.part_sha256 && nowSha && att.part_sha256 !== nowSha) {
          out.blockers.push('【防线①材料】' + pathSync.basename(part) + ': 零件内容与凭据不符（疑似替换）');
          continue;
        }
        const dens = Number(att.density_kg_m3);
        if (Number.isFinite(dens) && Math.abs(dens - 1000) < 1) {
          out.blockers.push('【防线①材料】' + pathSync.basename(part) + ': 密度 1000（疑似未赋材质）');
        }
      }
    } catch (e) {
      out.blockers.push('材料防线校验异常: ' + String((e && e.message) || e));
    }
    return out;
  }

  /** 汇总状态（供 /defense/info 与故障排查）。 */
  status() {
    return {
      ok: true,
      kid: this.kid,
      startedAt: new Date(this.startedAt).toISOString(),
      ttl_ms: SIG_TTL_MS,
      signed_count: this.ledger.filter((e) => e.kind === 'sign').length,
      anomaly_count: this.anomalies.length,
      quarantined_count: this.quarantined ? this.quarantined.size : 0,
      recent_anomalies: this.anomalies.slice(-10),
      runtime_file: this.runtimePath,
    };
  }
}
