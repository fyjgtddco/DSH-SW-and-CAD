/**
 * ZCode 认证/凭据服务（`ctx.zcodeAuth`）。
 *
 * ## 与其它 auth 服务的关键差异
 *
 * 其它 `ctx.xxxAuth` 管「浏览器登录 → 拿 token → 续期」。
 * ZCode **没有登录流程** —— 用户在官方 ZCode 客户端里登录一次，
 * 凭据就落在磁盘上（`~/.zcode/v2/credentials.json`，AES-256-GCM 加密）。
 * 本服务的职责是：
 *
 * 1. **读凭据**：解密磁盘凭据（`zcode.ts`）—— **不需要任何实例在跑**
 * 2. **探活**：凭据是否可用（能不能拿到额度）
 * 3. **额度**：`billing/balance`
 * 4. **签到**：补激活上报 → preview → claim（每个 plan 单独 mint captcha）
 * 5. **captcha 配置**：`client/configs`（拿 region/prefix/sceneId）
 *
 * ## ⚠ 必须 `extends Service`
 *
 * 其余八个 auth 服务全部继承 `@deepseek-ai/cordis` 的 `Service` 基类，
 * 由基类构造函数完成 `ctx.provide(<name>, this)` 注册。
 * 初版 `ZcodeAuth` 是个**裸 class**，既没继承也没自己 provide ——
 * 结果是 `ctx.zcodeAuth` **恒为 undefined**（行为探针实证），
 * 与注释里「注册为 ctx.zcodeAuth」的声明直接矛盾。
 *
 * 服务名由 `product.id` 派生为 `zcodeAuth`，与 `RaccoonAuth` 同款做法。
 *
 * ## ⚠ 不注册任何斜杠命令
 *
 * 与其余 provider 一致：登录/状态/额度/签到全部在 Jet Hub 面板完成。
 */
import { Service, type Context } from '@deepseek-ai/cordis';
import type { AccountPool } from './account-pool.js';
import type { CheckinStatus } from './credits.js';
import type { ClaimOutcome } from './credits.js';
import { type ZcodeCredential } from './zcode.js';
import { type ZcodeProduct, type ZcodeRemoteModelLike } from './zcode-product.js';
import { type ZcodeBalanceResult, type ZcodeClaimOutcome } from './zcode-upstream.js';
/** 一次探活的结果。 */
export interface ZcodeProbeResult {
    /** 凭据是否可用。 */
    available: boolean;
    /** 展示名（脱敏手机号 / 设备码）。 */
    accountLabel?: string;
    /** 剩余额度（探活成功时）。 */
    remaining?: number;
    /** 不可用时的原因（人类可读）。 */
    reason?: string;
}
/** `ZcodeAuth` 的构造选项。 */
export interface ZcodeAuthOptions {
    /** 产品配置；默认 {@link ZCODE}。 */
    product?: ZcodeProduct;
    /** 服务名覆盖（默认由产品 id 派生为 `zcodeAuth`）。 */
    serviceName?: string;
    /** 注入的 fetch（测试用）。 */
    fetchImpl?: typeof fetch;
    /** 注入的凭据读取（测试用，避免碰真实磁盘）。 */
    readCredential?: () => ZcodeCredential | undefined;
    /**
     * 账号池（测试用）。
     *
     * 生产路径靠 `ctx.get('accountPool')` 惰性取（见 `accountPool()`），
     * 但单测里未必把池注册到 ctx 上 —— 故保留这个注入口。
     */
    accountPool?: AccountPool;
}
/** ZCode 认证服务。 */
export declare class ZcodeAuth extends Service {
    private readonly product;
    private readonly fetchImpl;
    private readonly readCredential;
    /** 注入的账号池（测试用；生产走 ctx.get）。 */
    private readonly injectedPool;
    /**
     * 常驻 captcha 浏览器。
     *
     * ⚠ 常驻是必需的：冷启动约 690ms，而每次 mint 还要新建 page
     * （约 1.2 秒）。每次请求都冷启动会让首字延迟凭空多一秒。
     * 生命周期由 `stop()` 收尾。
     */
    private captchaBrowser;
    /**
     * captcha **预取池**（挂在与浏览器同一个生命周期上）。
     *
     * ⚠ 惰性创建：纯插件登录、从不用推理的用户不该为它做任何事。
     */
    private captchaPool;
    /**
     * 最近一次 `mintCaptcha` 用的 captcha 配置。
     *
     * 池的 `mint` 回调不带参数（它的语义是「产一个 param」），故配置由这里传递。
     * 配置来自服务端 `client/configs` 且极少变化，跟着最近一次调用走即可。
     */
    private captchaMintConfig;
    /**
     * captcha **产出失败退避**（设备级信誉保护）。
     *
     * ⚠ 与 `captchaPool` 不同，它**不惰性创建**：闸门要在第一次
     * `mintCaptcha` 之前就生效，且构造它无任何副作用。
     */
    private readonly captchaBackoff;
    /**
     * captcha 产出的**全局串行队列**（对齐官方 `jnn`/`wnn`）。
     *
     * ⚠ 必须是**实例字段**：队列靠共享的尾巴指针生效，每次新建等于没有队列。
     */
    private readonly captchaQueue;
    /**
     * captcha 产出的**观测计数**（对齐官方 `mnn` 的 ARMS 上报思路）。
     *
     * 官方把每次结果作为 `traceless_passed` / `interactive_displayed` 上报，
     * 并维护两个计数器 —— 那是它判断「设备信誉是否在恶化」的手段。
     * 我们至少要把这两个数**记下来并通过日志暴露**，否则降级发生时
     * 用户和我们都没有任何趋势可看（这正是这次排查最缺的东西）。
     */
    private readonly captchaStats;
    /** 最近一次失败原因（供 `status()` 暴露给 UI）。 */
    private lastError;
    constructor(ctx: Context, options?: ZcodeAuthOptions);
    /** 凭据 ref 名（供 Jet Hub 展示）。 */
    get credentialRefName(): string;
    /** 产品配置（测试与 Jet Hub 用）。 */
    get productConfig(): ZcodeProduct;
    /**
     * 读取当前凭据。
     *
     * ## 两个来源，插件自存优先
     *
     * 1. **插件自存**（`ctx.credentials` 的 `ZCODE_CREDENTIAL`）——
     *    用户在 Jet Hub 里走插件内登录拿到的，**不需要官方客户端**。
     * 2. **回退**：解密官方客户端的 `~/.zcode/v2/credentials.json` ——
     *    让「已经装了官方客户端并登录过」的用户零操作即可用。
     *
     * ⚠ **每次调用都重新读**，不缓存 —— 用户刚登录完或刚在官方客户端
     * 重新登录后，无需重启 DSH 即可生效。
     *
     * 返回 `undefined` 表示「没有可用的 ZCode 登录态」。
     */
    current(): Promise<ZcodeCredential | undefined>;
    /**
     * 读插件自存的凭据（`ctx.credentials`）。
     *
     * ## ⚠ 必须同时认**账号池里的 ref**（真实缺陷）
     *
     * 凭据可能落在**两个**地方，ref 名不同：
     *
     * | 来源 | ref |
     * |---|---|
     * | RPC `account.create`（用户点「添加账号」） | **`ZCODE_ACCOUNT_XXXX`**（`refName`） |
     * | 单凭据回退 / 手工写入 | `ZCODE_CREDENTIAL`（`defaultCredentialRef`） |
     *
     * 早期只读后者 —— 于是「用户在 Jet Hub 登录成功」之后，
     * `probe()` / `fetchBalance()` / `status()` / `fetchCheckinStatus()`
     * **全都读不到凭据**（它们都走本方法），表现为：
     * **能聊天（适配器读账号条目的 ref），但面板显示「未配置」、积分查不出**。
     *
     * 这个缺口一度被「回退读官方凭据文件」掩盖 —— 装了官方客户端的机器上
     * 现象会消失，只有**没装**的用户才会看到。
     *
     * ⇒ 顺序：**账号池（用户的显式登录）> 单凭据 ref（回退）**。
     * 与适配器的解析顺序保持一致，避免「适配器能用而面板不能用」。
     *
     * ⚠ 形状校验必须做：凭据存储里可能有**任何**字符串（用户手填、旧版本
     * 残留）。`isUsableZcodeCredential` 保证后续代码拿到的是完整对象。
     */
    private readStoredCredential;
    /**
     * 从账号池里第一个**凭据可用**的 zcode 账号读取。
     *
     * ⚠ **不看 `enabled`** —— 与其余 provider 的既有约定一致
     * （`AGENTS.md`：停用只影响自动选号，与凭据是否可用无关）。
     * 用户停用了账号，面板仍应能显示它的额度与状态。
     */
    private readCredentialFromPool;
    /** 从某个 ref 解析凭据（带形状校验）。 */
    private readCredentialFromRef;
    /**
     * 取账号池。
     *
     * ⚠ 用 `ctx.get` 而非构造注入：本服务可能在**账号池注册之前**被构造
     * （`index.ts` 里 `new ZcodeAuth(ctx)` 早于 `registerJetHubRpc`，
     * 且测试里账号池可能是后提供的）。惰性读取能让两种情况都成立。
     */
    private accountPool;
    /**
     * 探活：凭据是否可用。
     *
     * 判据是**端到端**的 —— 能不能真的查到额度。这样「证书解出来了但
     * 已失效」也会被如实反映（比只看文件存在可靠）。
     */
    probe(): Promise<ZcodeProbeResult>;
    /**
     * ★ **插件内登录** —— 走官方 CLI 设备授权流，**不需要 ZCode IDE**。
     *
     * ## 为什么这是「登录」而不是「读凭据」
     *
     * 早期实现只是「确认磁盘上有一份官方客户端写的凭据」。那要求用户
     * 先装并登录官方 ZCode 客户端 —— 与本插件「装完即用」的定位冲突。
     *
     * 实测确认官方 3.12.3+ 用的是**服务端中介的设备授权流**
     * （`/oauth/cli/init` → 浏览器授权 → `/oauth/cli/poll/{flow_id}`），
     * 完全不经 `zcode://` 回调，**普通 Node 进程就能走完**（见 `zcode-login.ts`）。
     *
     * ## 返回形状与其余 provider 一致
     *
     * `{ loginUrl, result }` —— `loginUrl` 是**真的**授权 URL（前端据此弹窗），
     * `result` 是等待用户授权完成的 Promise。
     *
     * ⚠ 与本文件其它方法不同，这个方法**不是幂等的**：每次调用都会向
     * 服务端申请一条新的授权流程。前端只在用户点「添加账号」时调一次。
     */
    startLogin(options?: {
        refName?: string;
        appVersion?: string;
    }): Promise<{
        loginUrl: string | undefined;
        result: Promise<{
            refName: string;
            credential: ZcodeCredential;
        }>;
    }>;
    /**
     * 阻塞式登录（等待用户在浏览器完成授权）。
     *
     * ⚠ 与 `startLogin` 的区别：这个会**等到授权完成**才返回。
     * 供「没有前端、只想在脚本里登录」的场景用；Jet Hub 走 `startLogin`
     * （两步式，避免弹窗被拦截）。
     */
    login(options?: {
        refName?: string;
        appVersion?: string;
    }): Promise<{
        refName: string;
        credential: ZcodeCredential;
    }>;
    /**
     * 把**当前可用凭据**（插件自存或官方文件）写进 `ctx.credentials`。
     *
     * 用途：把「官方客户端已登录」的状态**固化**成插件自存凭据，
     * 使其后即便官方客户端被卸载也能继续用。
     *
     * ⚠ 若已有插件自存凭据，本方法会**覆盖**它 —— 调用方需自行确认
     * （Jet Hub 的「添加账号」在已有账号时不会走到这里）。
     */
    persistCurrent(refName?: string): Promise<{
        refName: string;
        credential: ZcodeCredential;
    }>;
    /**
     * 拉取服务端下发的 captcha 配置（**带 60 秒 TTL 缓存**）。
     *
     * ## 为什么要缓存（对齐官方）
     *
     * 官方 `f3()` 对 captcha 配置做了 60 秒 TTL 缓存 + 在飞去重
     *（`out/renderer/assets/styles-*.js` 的 `expiresAt: t + 6e4`）。
     * 此前我们在 `index.ts` 用 `??=` 做**永久缓存** —— 两个问题：
     *
     * 1. **服务端换 `sceneId`／灰度切换后永不生效**（必须重启宿主）；
     * 2. **首次拉取失败会被永久固化**（`??=` 把失败结果也记住）。
     *
     * 现在改成 {@link TtlCache}：60 秒后自动重取，**失败不缓存**。
     *
     * 失败返回 `undefined`，由调用方回退到 `ZCODE_CAPTCHA_FALLBACK`。
     */
    fetchCaptchaConfig(): Promise<{
        region: string;
        prefix: string;
        sceneId: string;
    } | undefined>;
    /** captcha 配置缓存（60 秒 TTL，对齐官方 `f3()`）。惰性创建。 */
    private captchaConfigCache;
    private captchaConfigCacheInstance;
    /** 查额度（Jet Hub 的「余额」用）。 */
    fetchBalance(): Promise<ZcodeBalanceResult | undefined>;
    /**
     * 用**给定凭据**查额度（Jet Hub 逐账号查询时用）。
     *
     * 与 {@link fetchBalance} 的区别：那个用「当前磁盘凭据」，
     * 这个用调用方给的那份（每个账号条目各自的凭据）——
     * 多账号场景下两者可能不是同一份。
     */
    fetchBalanceFor(credential: ZcodeCredential): Promise<ZcodeBalanceResult | undefined>;
    /**
     * 产出 captcha param（`ctx.zcodeAuth` 的公开入口，供 RPC 层与适配器调用）。
     *
     * ## 走**预取池**（2026-09-30 新增）
     *
     * 每次请求现产的成本实测 0.5-3.7 秒（页面空闲 <8s 复用约 0.5s，更久则要
     * 新建页面约 3.7s），而 agent 多步循环的两步间隔通常**大于 8 秒** ——
     * 也就是说现产路径几乎每步都付新建页面的钱。
     * {@link CaptchaPool} 把这段成本移到**后台**：上一轮结束时产好下一轮的 param。
     *
     * ⚠ 语义没变：池只存**尚未使用**的 param，取走即弃（复用必 `3007`）。
     *
     * 关闭方式：`DSH_ZCODE_CAPTCHA_POOL=0`（关闭后行为与引入池之前逐字一致）。
     *
     * ⚠ `options.signal` 会被透传到浏览器侧（取页等待 / 建连超时 / abort）——
     * 推理链路的「停止」能否生效就靠它（真实缺陷，2026-09-29）。
     *
     * ## ★ 产出失败会进入**指数退避**（2026-10-01 新增，会话实证驱动）
     *
     * `session-eced01ed` 里额度耗尽后连续 **12 次**空响应，而每次重试都重新
     * mint 一个 captcha —— 在注定失败的情况下白耗 12 个配额，且**扣设备信誉**
     * （同分钟另一个 session 就报 `502 Failed to mint auth material`）。
     *
     * 故这里加闸门：连续产出失败达阈值后，**直接抛错不再发起 mint**
     * （那边注释原话：「继续请求不会让信誉恢复，只会更糟」）。
     * 详见 {@link CaptchaBackoff}。
     *
     * ## ★★ 产出走**全局串行队列**（2026-10-01 新增，对齐官方）
     *
     * 官方闭源版把 captcha 产出链在一条全局 promise 上（`jnn`/`wnn`，
     * 日志 `zcode-plan verification queue slot acquired`）——
     * **同一时刻只产一个**。原因是阿里云按**设备维度**限流
     * （官方文档：同设备每小时 150 次），并发产出是纯浪费。
     *
     * 而 DSH 会并发发请求（主回复 + 标题生成 + 压缩），此前每个都独立 mint。
     */
    mintCaptcha(config?: {
        region: string;
        prefix: string;
        sceneId: string;
    }, options?: {
        signal?: AbortSignal;
    }): Promise<string>;
    /** 取（并惰性创建）captcha 预取池。 */
    private captchaPoolInstance;
    /**
     * captcha 产出的**观测快照**（供 Jet Hub / 诊断读取）。
     *
     * 对齐官方维护 `traceless_passed_count` / `captcha_displayed_count` 的思路：
     * 用户与我们都该能看到「无感通过 vs 被要求交互」的比例趋势。
     */
    captchaObservability(): {
        tracelessPassed: number;
        interactiveDisplayed: number;
        failed: number;
        failureStreak: number;
        queuePending: number;
        cooldownRemainingMs: number;
    };
    /**
     * 用**给定凭据**领取每日额度（Jet Hub 逐账号领取时用）。
     *
     * 与 {@link claimDaily} 的区别同 {@link fetchBalanceFor}。
     */
    claimDailyFor(credential: ZcodeCredential, mintCaptcha: (() => Promise<string>) | undefined, captchaRegion?: string): Promise<ClaimOutcome[]>;
    /**
     * 拉模型目录。
     *
     * ⚠ ZCode 的模型表是**静态白名单**（实测可用的两个），不发网络请求
     * 去枚举 —— 上游 `/v1/models` 是桥的端点（我们不再依赖桥），
     * 而 `client/configs` 的模型池含**实测不可用**的两条
     * （`GLM-5-Turbo` / `GLM-5.2` 返回空响应）。
     * 故直接返回兜底表，语义是「实测可用的清单」。
     */
    fetchModels(): Promise<ZcodeRemoteModelLike[]>;
    /**
     * 查签到状态。
     *
     * ## ⚠ 判据是「有没有可领的 plan」，**不是**「列表是否为空」
     *
     * 与 Qoder 那次教训同型（`AGENTS.md` 记过）：服务端在活动不同阶段
     * 都可能回空列表。而 ZCode 的服务端**不会主动推送**活动 ——
     * 必须先补 `event/report`（`app_launch` + `app_daily_active`），
     * `preview` 才会下发 plan。
     *
     * 故本方法**先补激活信号再查**，否则会稳定误报「今日已领」。
     */
    fetchCheckinStatus(): Promise<CheckinStatus>;
    /**
     * 领取每日额度。
     *
     * ## 流程（每一步都不能省）
     *
     * 1. 补活跃上报 → 2. 查 preview → 3. 逐个 claim
     *
     * ## ⚠ captcha 是**一次性**的
     *
     * 每个 plan 都必须**重新 mint** 一个新 param（复用会得 `3007`）。
     * 故 captcha 的产出来自调用方注入的 `mintCaptcha` 回调 ——
     * 让本服务不必知道浏览器怎么起（也便于单测注入桩）。
     *
     * ## ⚠ `1003`（已领取）是**成功**
     *
     * 服务端对「已领取过」回 `code:1003`。把它当失败会让定时任务
     * 反复误报 —— 这与 Buddy / Qoder 的幂等语义一致。
     */
    claimDaily(mintCaptcha: (() => Promise<string>) | undefined, captchaRegion?: string): Promise<ClaimOutcome[]>;
    /** 领取的共用实现（`claimDaily` 与 `claimDailyFor` 都走它）。 */
    private claimDailyWith;
    /**
     * 账号卡片「刷新」按钮。
     *
     * ## ⚠⚠ 这里曾经有一个**数据破坏缺陷**（真实缺陷，2026-10-02）
     *
     * **用户报障**：「登录了 2 个账号（两个不同微信各自收到 bigmodel 登录通知），
     * 第二个账号有余额，但插件刷新积分显示 0，发消息报『额度已用尽』，
     * 而 IDE 里同一个账号发消息能收到回复」。
     *
     * **根因**：本方法与 {@link refreshAll} 都拿 `this.current()` 的结果
     * **无条件写回目标 ref** —— 而 `current()` 只返回**第一个凭据可用的账号**。
     * 于是账号 A 的凭据被写进账号 B 的 ref，**B 的原始凭据被永久覆盖**。
     *
     * **实测证据**（用户机器 `~/.dsh/.credentials.yaml`）：两个条目的
     * `zcode_jwt` 的 sha256、`device_mid`、`account_label`（同一昵称）、
     * `bigmodel_access_token` **全部逐字节相同** —— 同一个账号占了两条。
     * 用户确认「是两个不同微信账号」，故**只能是覆盖所致**。
     *
     * ⚠ **此处刻意不写真实值**：ref 名会暴露账号编号、昵称是用户的微信账号名、
     * `device_mid` 是设备标识。需要复核时从本机凭据自行取。
     *
     * **症状为何那么像服务端问题**：IDE 用自己那份真实凭据（B）→ 正常；
     * 插件池里两条都是 A → A 已耗尽 → 报额度用尽。
     *
     * ## 修法：**绝不跨账号写**
     *
     * ZCode **不可续期**（凭据是静态的，没有 refresh 端点），所以「刷新」
     * 唯一正确的语义是：**重新解析该账号自己的 ref，再写回它自己**
     * （用于「用户在别处更新了这个账号的凭据」这种情形）。
     * 与 `BuddyAuth.refreshAll` 的做法一致（那边也是逐账号读自己的 ref）。
     *
     * ⚠ 传了 `refName` 就**只动那一个 ref**；没传才回退到当前账号自己的 ref。
     * 无论如何**不会**拿到 A 的凭据去写 B。
     */
    refreshAccountCredential(refName: string, pool?: AccountPool, accountId?: string): Promise<void>;
    /**
     * 批量续期（定时调度器调用）。
     *
     * ## ⚠⚠ 这里曾经是**跨账号覆盖**的第二个入口（真实缺陷，2026-10-02）
     *
     * 旧实现：
     * ```ts
     * const credential = await this.current()          // ← 只取「第一个可用账号」
     * for (const account of accounts) {
     *   await set(account.credentialRef, credential)   // ← 覆盖**每一个**账号
     * }
     * ```
     * 于是 30 分钟一轮的定时器会把账号 A 的凭据**铺满整个池**，
     * 抹掉其余账号的真实凭据（详见 {@link refreshAccountCredential} 的实测证据）。
     *
     * 旧注释的本意是「用户在官方客户端重新登录后，新凭据能铺开到所有条目」——
     * 那个前提在**多账号池**下是**错的**：磁盘凭据只对应**一个**账号。
     *
     * ## 正确做法：逐账号、各写各的
     *
     * ZCode **不可续期**，故这里没有「续期」动作；做的是**逐账号对账**：
     * 每个账号重新解析**自己的** ref，能解出就写回自己（规范化字段），
     * 解不出就**跳过并告警**（不填别人的凭据）。
     *
     * ⚠ **只按 `refreshable` 过滤、不看 `enabled`**（`AGENTS.md` 既有约定：
     * 停用只影响自动选号，与凭据新鲜度无关）。
     */
    refreshAll(pool: AccountPool): Promise<void>;
    /**
     * `status()` —— 供 Jet Hub 展示「是否已配置」。
     *
     * 与其余 provider 同形（返回 `{configured, ...}`）。
     */
    status(): Promise<{
        configured: boolean;
        label?: string;
        error?: string;
    }>;
    /**
     * 释放资源。
     *
     * ⚠ 契约要求：`index.ts` 的 cleanup 对**全部** provider 统一调 `stop()`，
     * 缺了它会以 `is not a function` 崩在启动路径上
     * （`LoomyAuth` 的注释记过同一条）。
     *
     * ⚠ 会一并关闭 captcha 浏览器 —— 否则留下孤儿 chromium
     * （约 200-400MB，且用户没有界面能关掉它）。
     */
    stop(): void;
}
/** 把上游的领取结果映射成 Jet Hub 的 `ClaimOutcome`。 */
export declare function toClaimOutcome(outcome: ZcodeClaimOutcome, planId: string): ClaimOutcome;
/** 构造一个「不可用」的 `CheckinStatus`（字段全部显式给，满足契约）。 */
export declare function emptyCheckinStatus(active: boolean, actionRequired?: boolean): CheckinStatus;
//# sourceMappingURL=zcode-auth.d.ts.map