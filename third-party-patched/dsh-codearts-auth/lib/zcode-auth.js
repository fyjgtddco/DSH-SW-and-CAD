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
import { Service } from '@deepseek-ai/cordis';
import { isUsableZcodeCredential, readZcodeCredential, } from './zcode.js';
import { ZCODE } from './zcode-product.js';
import { ZcodeCaptchaBrowser, ZCODE_CAPTCHA_FALLBACK, validateCaptchaParam } from './zcode-captcha.js';
import { CaptchaPool, captchaPoolConfigFromEnv } from './captcha-pool.js';
import { CaptchaBackoff, captchaBackoffConfigFromEnv, captchaQueueEnabledFromEnv, CAPTCHA_CONFIG_TTL_MS } from './captcha-backoff.js';
import { SerialQueue } from './serial-queue.js';
import { TtlCache } from './ttl-cache.js';
import { generateDeviceMid, runZcodeLogin } from './zcode-login.js';
import { claimZcodePlan, fetchZcodeBalance, fetchZcodeCaptchaConfig, fetchZcodeClaimablePlans, fetchZcodeModels, reportZcodeActivation, } from './zcode-upstream.js';
/** ZCode 认证服务。 */
export class ZcodeAuth extends Service {
    product;
    fetchImpl;
    readCredential;
    /** 注入的账号池（测试用；生产走 ctx.get）。 */
    injectedPool;
    /**
     * 常驻 captcha 浏览器。
     *
     * ⚠ 常驻是必需的：冷启动约 690ms，而每次 mint 还要新建 page
     * （约 1.2 秒）。每次请求都冷启动会让首字延迟凭空多一秒。
     * 生命周期由 `stop()` 收尾。
     */
    captchaBrowser;
    /**
     * captcha **预取池**（挂在与浏览器同一个生命周期上）。
     *
     * ⚠ 惰性创建：纯插件登录、从不用推理的用户不该为它做任何事。
     */
    captchaPool;
    /**
     * 最近一次 `mintCaptcha` 用的 captcha 配置。
     *
     * 池的 `mint` 回调不带参数（它的语义是「产一个 param」），故配置由这里传递。
     * 配置来自服务端 `client/configs` 且极少变化，跟着最近一次调用走即可。
     */
    captchaMintConfig;
    /**
     * captcha **产出失败退避**（设备级信誉保护）。
     *
     * ⚠ 与 `captchaPool` 不同，它**不惰性创建**：闸门要在第一次
     * `mintCaptcha` 之前就生效，且构造它无任何副作用。
     */
    captchaBackoff;
    /**
     * captcha 产出的**全局串行队列**（对齐官方 `jnn`/`wnn`）。
     *
     * ⚠ 必须是**实例字段**：队列靠共享的尾巴指针生效，每次新建等于没有队列。
     */
    captchaQueue;
    /**
     * captcha 产出的**观测计数**（对齐官方 `mnn` 的 ARMS 上报思路）。
     *
     * 官方把每次结果作为 `traceless_passed` / `interactive_displayed` 上报，
     * 并维护两个计数器 —— 那是它判断「设备信誉是否在恶化」的手段。
     * 我们至少要把这两个数**记下来并通过日志暴露**，否则降级发生时
     * 用户和我们都没有任何趋势可看（这正是这次排查最缺的东西）。
     */
    captchaStats = {
        /** 无感验证直接通过的次数。 */
        tracelessPassed: 0,
        /** 弹出了交互式验证（滑块/拼图）的次数 —— **升高的信号要警惕**。 */
        interactiveDisplayed: 0,
        /** 产出失败的次数。 */
        failed: 0,
    };
    /** 最近一次失败原因（供 `status()` 暴露给 UI）。 */
    lastError;
    constructor(ctx, options = {}) {
        const product = options.product ?? ZCODE;
        super(ctx, options.serviceName ?? `${product.id}Auth`);
        this.product = product;
        this.fetchImpl = options.fetchImpl ?? fetch;
        this.readCredential = options.readCredential ?? (() => readZcodeCredential());
        this.injectedPool = options.accountPool;
        this.captchaBackoff = new CaptchaBackoff({
            /**
             * 阈值与冷却沿用 `dsh-free-glm` 的实测值（同一个上游）。
             * 关闭方式：`DSH_ZCODE_CAPTCHA_BACKOFF=0`（回到「每次都试」的旧行为）。
             */
            ...captchaBackoffConfigFromEnv(),
        });
        this.captchaQueue = new SerialQueue({ enabled: captchaQueueEnabledFromEnv() });
    }
    /** 凭据 ref 名（供 Jet Hub 展示）。 */
    get credentialRefName() {
        return this.product.defaultCredentialRef;
    }
    /** 产品配置（测试与 Jet Hub 用）。 */
    get productConfig() {
        return this.product;
    }
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
    async current() {
        const stored = await this.readStoredCredential();
        if (stored !== undefined)
            return stored;
        try {
            return this.readCredential();
        }
        catch (error) {
            this.lastError = error instanceof Error ? error.message : String(error);
            return undefined;
        }
    }
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
    async readStoredCredential() {
        // ① 账号池：用户显式登录创建的账号条目（ref 形如 ZCODE_ACCOUNT_XXXX）。
        const fromPool = await this.readCredentialFromPool();
        if (fromPool !== undefined)
            return fromPool;
        // ② 单凭据回退 ref。
        return await this.readCredentialFromRef(this.product.defaultCredentialRef);
    }
    /**
     * 从账号池里第一个**凭据可用**的 zcode 账号读取。
     *
     * ⚠ **不看 `enabled`** —— 与其余 provider 的既有约定一致
     * （`AGENTS.md`：停用只影响自动选号，与凭据是否可用无关）。
     * 用户停用了账号，面板仍应能显示它的额度与状态。
     */
    async readCredentialFromPool() {
        const pool = this.accountPool();
        if (pool === undefined)
            return undefined;
        try {
            for (const entry of pool.listAccountsByProvider(this.product.id)) {
                const credential = await this.readCredentialFromRef(entry.credentialRef);
                if (credential !== undefined)
                    return credential;
            }
        }
        catch {
            // 账号池异常（未初始化/存储损坏）→ 退回单凭据路径。
        }
        return undefined;
    }
    /** 从某个 ref 解析凭据（带形状校验）。 */
    async readCredentialFromRef(ref) {
        try {
            const resolved = await this.ctx.credentials.resolve(ref);
            if (resolved === undefined || resolved === null)
                return undefined;
            const parsed = JSON.parse(resolved.value);
            if (!isUsableZcodeCredential(parsed))
                return undefined;
            // 补上来源标记（旧凭据里没有这个字段）。
            return { ...parsed, source: parsed.source ?? 'plugin' };
        }
        catch {
            // 未配置或 JSON 损坏都视为「这份 ref 不可用」，让调用方试下一个。
            return undefined;
        }
    }
    /**
     * 取账号池。
     *
     * ⚠ 用 `ctx.get` 而非构造注入：本服务可能在**账号池注册之前**被构造
     * （`index.ts` 里 `new ZcodeAuth(ctx)` 早于 `registerJetHubRpc`，
     * 且测试里账号池可能是后提供的）。惰性读取能让两种情况都成立。
     */
    accountPool() {
        try {
            const pool = this.ctx.get('accountPool');
            if (pool !== undefined && pool !== null)
                return pool;
        }
        catch {
            // 服务未注册 —— 正常（headless / 单测）。
        }
        return this.injectedPool;
    }
    /**
     * 探活：凭据是否可用。
     *
     * 判据是**端到端**的 —— 能不能真的查到额度。这样「证书解出来了但
     * 已失效」也会被如实反映（比只看文件存在可靠）。
     */
    async probe() {
        const credential = await this.current();
        if (credential === undefined) {
            return {
                available: false,
                reason: '未找到可用的 ZCode 登录态。请在 Jet Hub 里点「添加账号」完成登录' +
                    '（若已装官方 ZCode 客户端并登录过，本插件也会自动读取它的凭据）。',
            };
        }
        try {
            const balance = await fetchZcodeBalance(credential, this.fetchImpl);
            if (balance === undefined) {
                return {
                    available: false,
                    accountLabel: credential.account_label,
                    reason: '额度接口不可用（凭据失效或网络异常）',
                };
            }
            return {
                available: true,
                accountLabel: credential.account_label,
                remaining: balance.remaining,
            };
        }
        catch (error) {
            const reason = error instanceof Error ? error.message : String(error);
            this.lastError = reason;
            return { available: false, accountLabel: credential.account_label, reason };
        }
    }
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
    async startLogin(options = {}) {
        /**
         * ⚠ 授权 URL 必须**立刻**拿到并返回：前端的 `window.open` 只在
         * 用户手势窗口内有效（等用户授权完再返回必被弹窗拦截 ——
         * `AGENTS.md` 记过 CodeArts 早期这个缺陷）。
         *
         * 故这里手工编排「发起 → 回调 URL → 后台轮询」，而不是直接
         * `await runZcodeLogin()`（那会阻塞到授权完成）。
         */
        const ref = (options.refName ?? this.product.defaultCredentialRef);
        let resolveUrl = () => { };
        let rejectUrl = () => { };
        const urlPromise = new Promise((resolve, reject) => {
            resolveUrl = resolve;
            rejectUrl = reject;
        });
        const result = (async () => {
            const loginResult = await runZcodeLogin({
                fetchImpl: this.fetchImpl,
                appVersion: options.appVersion ?? this.product.appVersionFallback,
                onAuthorizeUrl: (url) => resolveUrl(url),
            });
            /** 登录成功后组装凭据，并生成**自用**的 device_mid（见下）。 */
            const credential = {
                zcode_jwt: loginResult.zcodeJwt,
                /**
                 * ⚠ **自己生成 device_mid**，而不是读官方客户端的
                 * `telemetry-state.json` —— 这正是「脱离 IDE」的关键。
                 *
                 * 实测依据：同一 JWT 换任意随机 UUID，`billing/balance` 都回 200；
                 * 缺它才回 400 code 3001。故它的**值**不被绑定校验，
                 * 只需**稳定**（生成后持久化在凭据里，登录一次就固定）。
                 */
                device_mid: generateDeviceMid(),
                /**
                 * ★ **必须搬 `userId`**（真实缺陷，2026-10-02）。
                 *
                 * 它是**唯一**稳定的账号标识（服务端下发），也是「同一个账号
                 * 被添加两次」的**唯一**可靠判据。此前这一跳把它丢了，
                 * 于是无法去重 —— 同一账号点两次「添加账号」就得到两条。
                 *
                 * ⚠ 不要改用 `device_mid` 做判据：那是我们**随机生成**的，
                 * 同一账号重新登录会变（见 `zcode.ts` 的字段注释）。
                 */
                ...loginResult.userId.length > 0 ? { user_id: loginResult.userId } : {},
                bigmodel_access_token: loginResult.bigmodelAccessToken,
                account_label: loginResult.displayName,
                app_version: options.appVersion ?? this.product.appVersionFallback,
                source: 'plugin',
            };
            await this.ctx.credentials.set(ref, JSON.stringify(credential));
            return { refName: ref, credential };
        })();
        /**
         * 若发起阶段就失败（网络/服务端拒绝），`onAuthorizeUrl` 永不触发，
         * 故这里把 URL promise 与结果 promise 对齐 —— 避免前端永久等待。
         */
        result.catch((error) => {
            rejectUrl(error instanceof Error ? error : new Error(String(error)));
        });
        let loginUrl;
        try {
            loginUrl = await urlPromise;
        }
        catch (error) {
            // 发起就失败：把错误抛给调用方（前端会显示原因）。
            throw error instanceof Error ? error : new Error(String(error));
        }
        return { loginUrl, result };
    }
    /**
     * 阻塞式登录（等待用户在浏览器完成授权）。
     *
     * ⚠ 与 `startLogin` 的区别：这个会**等到授权完成**才返回。
     * 供「没有前端、只想在脚本里登录」的场景用；Jet Hub 走 `startLogin`
     * （两步式，避免弹窗被拦截）。
     */
    async login(options = {}) {
        const started = await this.startLogin(options);
        return await started.result;
    }
    /**
     * 把**当前可用凭据**（插件自存或官方文件）写进 `ctx.credentials`。
     *
     * 用途：把「官方客户端已登录」的状态**固化**成插件自存凭据，
     * 使其后即便官方客户端被卸载也能继续用。
     *
     * ⚠ 若已有插件自存凭据，本方法会**覆盖**它 —— 调用方需自行确认
     * （Jet Hub 的「添加账号」在已有账号时不会走到这里）。
     */
    async persistCurrent(refName) {
        const credential = await this.current();
        if (credential === undefined) {
            throw new Error('ZCode 凭据不可用：请在 Jet Hub 里点「添加账号」完成登录' +
                '（若已装官方 ZCode 客户端并登录过，本插件也会自动读取它的凭据）。');
        }
        const ref = (refName ?? this.product.defaultCredentialRef);
        /**
         * ⚠ `ctx.credentials.set` 的第二个参数是**字符串**（不是对象）——
         * 与 `RaccoonAuth` 同款约定：把凭据本体序列化成 JSON 存进去，
         * 读的时候再 `JSON.parse`。
         */
        await this.ctx.credentials.set(ref, JSON.stringify(credential));
        return { refName: ref, credential };
    }
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
    async fetchCaptchaConfig() {
        /**
         * ⚠ 缓存的是**配置**而不是「带凭据的请求」：凭据可能变（切号/重新登录），
         * 故缓存的 `load` 每次都重新解析**当时**的凭据。
         */
        return await this.captchaConfigCacheInstance().get();
    }
    /** captcha 配置缓存（60 秒 TTL，对齐官方 `f3()`）。惰性创建。 */
    captchaConfigCache;
    captchaConfigCacheInstance() {
        this.captchaConfigCache ??= new TtlCache({
            ttlMs: CAPTCHA_CONFIG_TTL_MS,
            load: async () => {
                const credential = await this.current();
                if (credential === undefined)
                    return undefined;
                return await fetchZcodeCaptchaConfig(credential, this.fetchImpl);
            },
        });
        return this.captchaConfigCache;
    }
    /** 查额度（Jet Hub 的「余额」用）。 */
    async fetchBalance() {
        const credential = await this.current();
        if (credential === undefined)
            return undefined;
        return await fetchZcodeBalance(credential, this.fetchImpl);
    }
    /**
     * 用**给定凭据**查额度（Jet Hub 逐账号查询时用）。
     *
     * 与 {@link fetchBalance} 的区别：那个用「当前磁盘凭据」，
     * 这个用调用方给的那份（每个账号条目各自的凭据）——
     * 多账号场景下两者可能不是同一份。
     */
    async fetchBalanceFor(credential) {
        return await fetchZcodeBalance(credential, this.fetchImpl);
    }
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
    async mintCaptcha(config, options = {}) {
        /**
         * ⚠ 闸门必须在**取池之前**：池的 `prefetch()` 后台路径也走同一个
         * `mint` 回调，故它天然也被挡住（不需要池自己判断退避）。
         */
        const remainMs = this.captchaBackoff.remainingMs();
        if (remainMs > 0) {
            throw new Error(`zcode: captcha 产出处于冷却中（连续 ${this.captchaBackoff.failureStreak()} 次失败），` +
                `约 ${Math.ceil(remainMs / 1000)} 秒后可重试。` +
                '这通常意味着设备信誉不足（上游把无感验证降级为滑块），' +
                '继续重试只会让信誉更差 —— 请稍后再试。');
        }
        /**
         * ⚠ 串行队列**包住整个「取配置 + 产出」**，而不是只包浏览器那一跳：
         * 排队本身要尽早发生，否则 N 个并发调用会各自先把配置拉一遍再排队。
         *
         * ⚠ 队列**不吞中断**：等待期间 `signal` 中止会抛 `QueueAbortedError`。
         */
        return await this.captchaQueue.run(async () => {
            const resolved = config ?? await this.fetchCaptchaConfig() ?? ZCODE_CAPTCHA_FALLBACK;
            // 池的 mint 回调不接受参数，故把「本次的 captcha 配置」记在实例字段上。
            this.captchaMintConfig = resolved;
            return await this.captchaPoolInstance().take(options);
        }, options);
    }
    /** 取（并惰性创建）captcha 预取池。 */
    captchaPoolInstance() {
        this.captchaPool ??= new CaptchaPool({
            mint: async (options) => {
                try {
                    this.captchaBrowser ??= new ZcodeCaptchaBrowser();
                    const outcome = await this.captchaBrowser.mintWithOutcome(this.captchaMintConfig ?? ZCODE_CAPTCHA_FALLBACK, options);
                    /**
                     * ★ **观测**（对齐官方 `mnn` 的上报口径）：
                     * 记下本次是「无感直接通过」还是「弹了交互式验证」。
                     *
                     * ⚠ `interactiveDisplayed` 的**上升趋势**是设备信誉恶化的先行指标 ——
                     * 而此前我们完全没有这个数，排查时只能靠猜（这正是本次最缺的东西）。
                     */
                    if (outcome.interactive)
                        this.captchaStats.interactiveDisplayed += 1;
                    else
                        this.captchaStats.tracelessPassed += 1;
                    /**
                     * ⚠ **降级要显式告警**（不能只默默计数）：它意味着上游已把我们
                     * 当风险用户，继续高频请求只会更糟。
                     */
                    if (outcome.interactive) {
                        this.ctx.logger?.warn?.('[jet-hub] zcode captcha 被要求**交互式验证**（滑块/拼图）—— ' +
                            '设备信誉可能已下降；若频繁出现请降低调用频率或稍后再试。' +
                            `（累计：无感 ${this.captchaStats.tracelessPassed} 次 / ` +
                            `交互 ${this.captchaStats.interactiveDisplayed} 次）`);
                    }
                    /**
                     * ⚠ **只有真的产出成功才清零**。
                     *
                     * 注意成功在此处、而非在 `take()` 返回时判定：池命中时根本没调
                     * 这个回调，那种情况不该影响信誉计数（它说明本机产出能力正常）。
                     */
                    this.captchaBackoff.noteSuccess();
                    return outcome.param;
                }
                catch (error) {
                    this.captchaStats.failed += 1;
                    const until = this.captchaBackoff.noteFailure();
                    if (until > 0) {
                        this.ctx.logger?.warn?.(`[jet-hub] zcode captcha 连续产出失败 ${this.captchaBackoff.failureStreak()} 次，` +
                            `进入冷却约 ${Math.ceil((until - Date.now()) / 1000)} 秒（期间不再发起 mint）`);
                    }
                    throw error;
                }
            },
            ...captchaPoolConfigFromEnv(),
            /**
             * 入池与取出时各校验一次：阿里云 SDK 的**降级产物**看起来像正常返回值，
             * 但发出去必然 `3007`（那边实测：合法 280 字符 vs 降级约 76 字符）。
             * 宁可在本地丢掉重产，也不要让它变成用户可见的一次失败。
             */
            validate: (param) => validateCaptchaParam(param).ok,
            onWarn: (message) => this.ctx.logger?.warn?.(message),
        });
        return this.captchaPool;
    }
    /**
     * captcha 产出的**观测快照**（供 Jet Hub / 诊断读取）。
     *
     * 对齐官方维护 `traceless_passed_count` / `captcha_displayed_count` 的思路：
     * 用户与我们都该能看到「无感通过 vs 被要求交互」的比例趋势。
     */
    captchaObservability() {
        return {
            ...this.captchaStats,
            failureStreak: this.captchaBackoff.failureStreak(),
            queuePending: this.captchaQueue.stats().pending,
            cooldownRemainingMs: this.captchaBackoff.remainingMs(),
        };
    }
    /**
     * 用**给定凭据**领取每日额度（Jet Hub 逐账号领取时用）。
     *
     * 与 {@link claimDaily} 的区别同 {@link fetchBalanceFor}。
     */
    async claimDailyFor(credential, mintCaptcha, captchaRegion = ZCODE_CAPTCHA_FALLBACK.region) {
        return await this.claimDailyWith(credential, mintCaptcha, captchaRegion);
    }
    /**
     * 拉模型目录。
     *
     * ⚠ ZCode 的模型表是**静态白名单**（实测可用的两个），不发网络请求
     * 去枚举 —— 上游 `/v1/models` 是桥的端点（我们不再依赖桥），
     * 而 `client/configs` 的模型池含**实测不可用**的两条
     * （`GLM-5-Turbo` / `GLM-5.2` 返回空响应）。
     * 故直接返回兜底表，语义是「实测可用的清单」。
     */
    async fetchModels() {
        /**
         * ★ **优先上游**（真实缺陷）。
         *
         * 早先这里**直接照抄兜底表**，于是：
         *   - 窗口 / 输出上限用的是兜底表的估值（`200_000` / `32_768`），
         *     而上游说的是 `1_000_000` / `128_000`；
         *   - **思考档位完全不出现**（兜底表当时没有档位字段）。
         *
         * 用户报障正是「上下文窗口 1000000、最大输出 128000，但选不了思考档位」
         * —— 那两个数字来自上游/用户手填，而**档位是代码里根本没有**。
         *
         * ⇒ 档位、视觉能力、窗口、输出上限**全部以上游为准**。
         */
        const credential = await this.current();
        if (credential !== undefined) {
            const remote = await fetchZcodeModels(credential, this.fetchImpl);
            if (remote !== undefined && remote.length > 0)
                return remote;
        }
        // 上游不可用（未登录 / 网络异常）→ 回退兜底表（其值已与上游对齐）。
        return this.product.fallbackModels.map((model) => ({
            id: model.id,
            name: model.name,
            contextWindow: model.contextWindow,
            maxTokens: model.maxTokens,
            supportsImage: model.supportsImage,
            ...model.reasoningLevels !== undefined ? { reasoningLevels: model.reasoningLevels } : {},
            ...model.defaultReasoningLevel !== undefined
                ? { defaultReasoningLevel: model.defaultReasoningLevel }
                : {},
        }));
    }
    // ===== 签到（与 CodeArts / Buddy 等共用 CheckinStatus / ClaimOutcome 形状）=====
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
    async fetchCheckinStatus() {
        const credential = await this.current();
        if (credential === undefined) {
            return emptyCheckinStatus(false, true);
        }
        // 补活跃信号 —— 不补则 preview 恒为空。
        await reportZcodeActivation(credential, this.fetchImpl);
        const plans = await fetchZcodeClaimablePlans(credential, this.fetchImpl);
        /**
         * ⚠ `active` 恒为 `true`（拿到凭据即 true）—— 与 Qoder 的同款约定：
         * 若按「列表非空」判 `active:false`，`collectClaimResults` 会先命中
         * 「活动未开启」分支，把「今天已领」误报成「签到活动未开启」。
         *
         * ⚠ ZCode 是**额度制**（不是积分制）：`ClaimOutcome` / `CheckinStatus`
         * 那些 `credit` 字段的单位是「积分」，而 ZCode 的额度单位是 **token**。
         * 两者不是同一量纲，故这里**一律填 0**，不把 token 数伪装成积分
         * （否则 Jet Hub 的汇总会把 1 亿 token 显示成 1 亿积分）。
         *
         * `todayCheckedIn` 只能由「有没有可领的 plan」推断（ZCode 没有独立的
         * 「今日是否已领」端点）：有可领 = 还没领；没有 = 已领或未投放。
         */
        return {
            active: true,
            todayCheckedIn: plans.length === 0,
            streakDays: 0,
            dailyCredit: 0,
            todayCredit: 0,
            isStreakDay: false,
            totalCredits: 0,
            checkinDates: [],
            activityName: 'ZCode Start Plan 每日额度',
            themeName: 'ZCode',
            endTime: '',
        };
    }
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
    async claimDaily(mintCaptcha, captchaRegion = ZCODE_CAPTCHA_FALLBACK.region) {
        const credential = await this.current();
        if (credential === undefined) {
            return [{ kind: 'failed', code: -1, message: '未找到可用的 ZCode 登录态' }];
        }
        return await this.claimDailyWith(credential, mintCaptcha, captchaRegion);
    }
    /** 领取的共用实现（`claimDaily` 与 `claimDailyFor` 都走它）。 */
    async claimDailyWith(credential, mintCaptcha, captchaRegion) {
        if (mintCaptcha === undefined) {
            return [{ kind: 'failed', code: -1, message: 'captcha 产出不可用（找不到浏览器？）' }];
        }
        await reportZcodeActivation(credential, this.fetchImpl);
        const plans = await fetchZcodeClaimablePlans(credential, this.fetchImpl);
        /**
         * ⚠ 「没有可领 plan」**不等于**「今天已领」——也可能是活动未投放。
         * 但两种情况下用户的动作都是「明天再来」，故归为 `already-claimed`
         * 并给出如实文案（与 Buddy / Qoder 的幂等语义一致）。
         */
        if (plans.length === 0) {
            return [{ kind: 'already-claimed', message: '今日暂无可领额度（服务端按日刷新）' }];
        }
        const outcomes = [];
        for (const plan of plans) {
            try {
                // ⚠ 每个 plan 单独 mint（captcha 一次性）。
                const param = await mintCaptcha();
                const outcome = await claimZcodePlan(credential, plan.planId, { param, region: captchaRegion }, this.fetchImpl);
                outcomes.push(toClaimOutcome(outcome, plan.planId));
            }
            catch (error) {
                outcomes.push({
                    kind: 'failed',
                    code: -1,
                    message: `${plan.planId}: ${error instanceof Error ? error.message : String(error)}`,
                });
            }
        }
        return outcomes;
    }
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
    async refreshAccountCredential(refName, pool, accountId) {
        /**
         * ⚠ 用**目标账号自己的** ref 重新解析凭据，而不是 `current()`。
         *
         * `current()` 的语义是「池里第一个可用账号」，与「要刷新的那个账号」
         * 可能**不是同一个** —— 这正是那个数据破坏缺陷的成因。
         */
        const own = await this.readCredentialFromRef(refName);
        if (own !== undefined) {
            await this.ctx.credentials.set(refName, JSON.stringify(own));
            return;
        }
        void pool;
        void accountId;
        /**
         * 该 ref 自己解析不出凭据（未配置/损坏/被清空）。
         *
         * ## ⚠ 这里**刻意不做**「用磁盘凭据补上」的兜底（虽是旧行为）
         *
         * 旧实现拿 `current()` 写进来，**副作用**是「该账号凭据损坏时会被
         * 别的账号填上」—— 那正是本次数据破坏的成因。而「用**官方客户端磁盘
         * 凭据**补上」听起来像合理的兜底，实际**不可实施**：
         *
         * 磁盘 `~/.zcode/v2/credentials.json` 是**单账号**格式，而池是**多账号**的
         * —— 我们**无法判断**那份磁盘凭据属于池里的**哪一个**账号。
         * 拿它去补任意一个条目，等于重犯同一个错误（只是换成「单体覆盖」）。
         *
         * ⇒ 如实报错，让用户重新登录该账号。这是**唯一**不会造成数据破坏的选项。
         */
        throw new Error(`ZCode 账号（${refName}）的凭据不可用或已损坏，请重新登录该账号` +
            '（本插件不会用其它账号的凭据覆盖它）。');
    }
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
    async refreshAll(pool) {
        const accounts = pool.listAccountsByProvider(this.product.id);
        for (const account of accounts) {
            const ref = account.credentialRef;
            try {
                /**
                 * ⚠ **读该账号自己的 ref**（不是 `current()`）——这是本方法的关键。
                 */
                const own = await this.readCredentialFromRef(ref);
                if (own === undefined) {
                    /**
                     * ⚠ **跳过，而不是用别的账号填它**。
                     *
                     * 旧实现会在这里用 `current()` 覆盖 ⇒ 破坏该账号的真实凭据。
                     * 现在的选择是「什么都不做 + 留一条可排查的日志」——
                     * 数据完整性优先于「把字段补齐」。
                     */
                    this.ctx.logger?.warn?.(`[jet-hub] zcode 账号 ${account.id} 的凭据不可用（跳过，不会用其它账号覆盖）`);
                    continue;
                }
                await this.ctx.credentials.set(ref, JSON.stringify(own));
            }
            catch {
                // 单个账号失败不影响其余（与 BuddyAuth.refreshAll 同语义）。
            }
        }
    }
    /**
     * `status()` —— 供 Jet Hub 展示「是否已配置」。
     *
     * 与其余 provider 同形（返回 `{configured, ...}`）。
     */
    async status() {
        const credential = await this.current();
        if (credential === undefined) {
            return { configured: false, error: this.lastError };
        }
        return { configured: true, label: credential.account_label };
    }
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
    stop() {
        // 池先清空（在飞的预取会随浏览器关闭一起失败，失败已被池吞掉并只记日志）。
        this.captchaPool?.clear();
        this.captchaPool = undefined;
        this.captchaBrowser?.dispose();
        this.captchaBrowser = undefined;
    }
}
/** 把上游的领取结果映射成 Jet Hub 的 `ClaimOutcome`。 */
export function toClaimOutcome(outcome, planId) {
    if (outcome.ok) {
        if (outcome.alreadyClaimed === true) {
            return { kind: 'already-claimed', message: `额度已领取过（${planId}）` };
        }
        /**
         * ⚠ ZCode 的额度单位是 **token**，不是「积分」—— 而 `ClaimOutcome` 的
         * `claimed` 分支强制要求 `credit: number` 与 `streakDays`。
         * 这里填 0 并在文案里说明，避免把 token 数伪装成积分
         * （那会让 Jet Hub 的汇总把 1 亿 token 显示成 1 亿积分）。
         */
        return { kind: 'claimed', credit: 0, streakDays: 0, isStreakDay: false };
    }
    // 3007 = captcha 失败；给出可操作的提示而不是裸码。
    if (outcome.code === 3007) {
        return { kind: 'failed', code: 3007, message: `captcha 校验失败（${planId}），请重试` };
    }
    return {
        kind: 'failed',
        // ⚠ `code` 是 `failed` 分支的**必填**字段；上游没给码时用 -1 表示
        // 「无业务码」（HTTP 层失败）。
        code: outcome.code ?? -1,
        message: `${planId}: ${outcome.message ?? `HTTP ${outcome.httpStatus ?? '?'}`}`,
    };
}
/** 构造一个「不可用」的 `CheckinStatus`（字段全部显式给，满足契约）。 */
export function emptyCheckinStatus(active, actionRequired = false) {
    return {
        active,
        todayCheckedIn: false,
        streakDays: 0,
        dailyCredit: 0,
        todayCredit: 0,
        isStreakDay: false,
        totalCredits: 0,
        checkinDates: [],
        activityName: '',
        themeName: '',
        endTime: '',
        ...actionRequired ? { actionRequired: true } : {},
    };
}
//# sourceMappingURL=zcode-auth.js.map