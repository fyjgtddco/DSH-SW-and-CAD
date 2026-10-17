/**
 * ZCode 的阿里云 captcha 产出（**唯一还需要浏览器的环节**）。
 *
 * ## 为什么需要它
 *
 * ZCode 免费额度通道（`/api/v1/zcode-plan/anthropic`）强制阿里云 captcha：
 * 缺 `x-aliyun-captcha-verify-param` 时上游回
 * `400 {"code":3007,"msg":"captcha verify failed"}`（实测）。
 *
 * ## 为什么能用普通浏览器而不是 ZCode 那个壳
 *
 * captcha 是**网页 SDK**，不是 Electron 专有 API：
 *
 * ```
 * script:  https://o.alicdn.com/captcha-frontend/aliyunCaptcha/AliyunCaptcha.js
 * config:  window.AliyunCaptchaConfig = { region, prefix }
 * 调用:    initAliyunCaptcha({ SceneId, mode, element, button, getInstance, success, … })
 * 取参:    getInstance 里调 instance.startTracelessVerification()（无感验证）
 *          → success(param) 回调给出 param
 * ```
 *
 * 故任何「有 DOM + canvas + 能跑 JS」的浏览器都行。实测
 * **scoop 的 chromium（headful + 基本 stealth 补丁）** 可稳定产出。
 *
 * ## ⚠ 两个实测得到的硬约束（决定本文件的实现形态）
 *
 * ### 1. 同一个页面**不能**重复 mint
 *
 * 同一 page 上连续 `initAliyunCaptcha` 三次的实测结果：
 *
 * | 次序 | 结果 | 耗时 |
 * |---|---|---|
 * | #1 | ✓ len=280 | 817ms |
 * | #2 | ✗ `F001` | 279ms |
 * | #3 | ✗ `F001` | 296ms |
 *
 * ⇒ SDK 实例状态在页面内不可重复初始化。**每次 mint 必须新建 page target**。
 * 采用该策略后实测 **4/4 成功，中位 1246ms**（浏览器冷启动仅 690ms）。
 *
 * ### 2. `--headless=new` 过不了，必须 **headful**
 *
 * | 模式 | 结果 |
 * |---|---|
 * | `--headless=new`（+ 补丁） | ✗ `fail` / `verifyCode: F001` |
 * | **headful + 补丁** | ✓ 280 字符合法 param |
 *
 * 阿里云风控会看这个差异。headful 在 Windows 上可以**不打扰用户**
 * （`--window-position=-32000,-32000` 移出屏幕）。
 */
/** 阿里云 captcha 配置（服务端下发；此处为实测兜底值）。 */
export interface ZcodeCaptchaConfig {
    /** 区域：`cn`。 */
    region: string;
    /** 前缀：决定请求走到哪个阿里云子域。 */
    prefix: string;
    /** 场景 id。 */
    sceneId: string;
}
/**
 * 兜底 captcha 配置。
 *
 * ⚠ 这些值**实测自本机账号**（`GET /api/v1/client/configs?platform=unknown`
 * 的 `data.configs.captcha`）。理论上可能随账号/灰度变化，故
 * `ZcodeAuth.fetchCaptchaConfig()` 会优先向服务端索取，此处仅作兜底。
 */
export declare const ZCODE_CAPTCHA_FALLBACK: ZcodeCaptchaConfig;
/** 阿里云 captcha SDK 地址（与官方逐字一致）。 */
export declare const ALIYUN_CAPTCHA_SDK_URL = "https://o.alicdn.com/captcha-frontend/aliyunCaptcha/AliyunCaptcha.js";
/** SDK 需要的 DOM 宿主 id（与官方 `zcode-aliyun-captcha-*` 一致）。 */
export declare const CAPTCHA_CONTAINER_ID = "zcode-aliyun-captcha-container";
/** 挂载点 id。 */
export declare const CAPTCHA_ELEMENT_ID = "zcode-aliyun-captcha-element";
/** 按钮 id。 */
export declare const CAPTCHA_BUTTON_ID = "zcode-aliyun-captcha-button";
/**
 * captcha 页面必须导航到的**真实 origin**。
 *
 * ⚠⚠ **这是能否重复 mint 的关键**（实测矩阵）：
 *
 * | origin | 同一页面连续 mint |
 * |---|---|
 * | `about:blank` | **1/3**（#2 起 `F001`） |
 * | **`https://zcode.z.ai/`** | **5/5，平均 546ms** |
 *
 * 阿里云 SDK 会读 `location.origin` 参与风控判定，而 `about:blank` 的
 * origin 是字符串 `"null"` —— 于是第二次起被拒。
 *
 * ⚠ 这一条此前被**误判**成「同一页面不能重复 mint，必须每次新建 page」，
 * 导致实现多付了 2.3 倍的耗时（1246ms → 546ms）。
 * 结论修正的代价是：**改一个变量、看结果**比堆假设更快。
 */
export declare const CAPTCHA_PAGE_ORIGIN = "https://zcode.z.ai/";
/**
 * captcha param 的合法判据（与桥侧 `isUsableCaptchaParam` 同一套）。
 *
 * 三条全中才算合法：
 * 1. 长度 ≥ **200**（实测合法值 280；降级垃圾约 76）
 * 2. 是 base64 且能解出 JSON
 * 3. 含 `securityToken` 且长度 ≥ **50**（实测合法值 128）
 *
 * 任一条不中即判为降级 —— **不发请求**，省一次注定 3007 的往返。
 */
export declare function validateCaptchaParam(param: unknown): {
    ok: boolean;
    reason?: string;
};
/**
 * 浏览器可执行文件的候选位置（按优先级）。
 *
 * ## ⚠ 两个必须防的坑（都实测踩过）
 *
 * ### 1. 空环境变量会产生**相对路径**
 *
 * `join('', 'Google', 'Chrome', …)` 返回 `Google\Chrome\…` —— 一个**相对路径**，
 * `existsSync` 会相对**当前工作目录**解析。于是「当前目录下恰好有个同名文件」
 * 会被误判成浏览器，而真正的浏览器却找不到。
 *
 * ⇒ 只接受**绝对路径**候选（`isAbsolute` 过滤）。
 *
 * ### 2. `USERPROFILE` 不等于家目录
 *
 * scoop 的安装位置在 `~/.scoop` 或 `~/scoop` 下，而 `~` 应当用
 * `os.homedir()` 求（它还会看 `HOME`，且在 `USERPROFILE` 被改写时仍正确）。
 * 用 `process.env.USERPROFILE` 拼路径会在「环境变量异常/被沙箱改写」时失效 ——
 * 实测：把 `USERPROFILE` 指到沙箱后，原本可用的 scoop chromium 就找不到了。
 */
export declare function findBrowserExecutable(): string | undefined;
/**
 * 一次 captcha 产出的**结果 + 观测**。
 *
 * `interactive` 的语义（对齐官方 `mnn` 的 `interactive_displayed`）：
 * - `false` = **无感验证**直接通过（正常路径）
 * - `true`  = 被要求**交互式验证**（滑块/拼图）⇒ **设备信誉可能已下降**
 *
 * ⚠ 判据是「曾经观察到交互元素」——因为阿里云的这一层**不对外透出**
 *（官方文档 Q9 原文：「该安全策略逻辑不支持自定义，不对外透出」），
 * 只能靠看 DOM。
 */
export interface CaptchaMintOutcome {
    /** 产出的 param。 */
    param: string;
    /** 本次是否被降级为交互式验证（`true` = 信誉预警信号）。 */
    interactive: boolean;
}
/** 常驻浏览器的启动选项。 */
export interface ZcodeCaptchaBrowserOptions {
    /** 可执行文件；缺省自动探测。 */
    executablePath?: string;
    /** 调试端口；缺省随机（9300-9799）。 */
    debugPort?: number;
    /** 是否隐藏窗口（默认 true —— 用户不应被打扰）。 */
    hideWindow?: boolean;
    /** 就绪等待上限（毫秒）。 */
    readyTimeoutMs?: number;
    /**
     * 页面导航后的等待（毫秒）。
     *
     * ⚠ 真实 origin 的页面需要等 `domcontentloaded` 才有 `body`
     * （实测约 1.5 秒足够；`about:blank` 只需几百毫秒）。
     */
    navigationWaitMs?: number;
    /**
     * 页面**空闲多久后不再复用**（毫秒，默认 8000）。
     *
     * ⚠ 实测依据：同一页面**空闲 15 秒后 mint 必然 `F001`**
     * （而秒级连续 mint 是 5/5 成功）。故取 8 秒留一半余量 ——
     * 偏保守只多付一次建页成本（约 3.7 秒），比让用户看到报错好。
     *
     * 设成很大的值等于「永远复用」（会重现 `F001`）；
     * 设成 0 等于「每次新建」（慢但不会因空闲失败）。
     */
    idleReuseMs?: number;
    /**
     * 等**别的 mint** 让出页面时的上限（毫秒，默认 30000）。
     *
     * ⚠ 这不是「性能参数」而是**防死锁参数**（真实缺陷，2026-09-29）：
     * 取页是 `while (pageBusy) await sleep(50)` 的自旋，一旦某个持有者没能
     * 复位标志，这里就是**永久自旋** —— 而它既不看 signal 也没有上限，
     * 表现为「请求根本不发出、UI 永远深度求索中、点停止也无反应」。
     * 有界失败远好于永久挂起（失败会被上报成错误，挂起只能重启宿主）。
     */
    pageWaitTimeoutMs?: number;
    /**
     * 新建页面时等待 CDP WebSocket `open` 的上限（毫秒，默认 10000）。
     *
     * ⚠ 同理是防死锁：旧实现只等 `open` / `error` 两个事件，Chromium 僵死时
     * **两个都不来**，于是永久挂起（且因为不抛错，`pageBusy` 也不会复位）。
     */
    connectTimeoutMs?: number;
}
/**
 * 常驻浏览器会话。
 *
 * ## 为什么常驻
 *
 * 冷启动实测约 690ms，但**每次请求都冷启动**会让首字延迟凭空多一秒。
 * 而每请求的 mint 开销实测约 1.2 秒 —— 常驻后总开销就在这个量级。
 *
 * ## 生命周期
 *
 * `dispose()` 必须被调用（`ZcodeAuth.stop()` 里做），否则会留下
 * 一个孤儿 chromium 进程（约 200-400MB）。此外还注册了进程退出钩子兜底。
 */
export declare class ZcodeCaptchaBrowser {
    private readonly options;
    private child;
    private browserWs;
    private browserCdp;
    private profileDir;
    private port;
    private starting;
    /**
     * 复用的 captcha 页面（停在 {@link CAPTCHA_PAGE_ORIGIN} 上）。
     *
     * ⚠ 复用而非每次新建：实测 546ms vs 1246ms（快 2.3 倍）。
     * 前提是 origin 必须真实 —— 见 `mint()` 的说明。
     */
    private reusablePage;
    /**
     * 页面是否正被某次 mint 占用。
     *
     * captcha param 是**一次性**的，两个并发 mint 共用同一页面会互相踩状态。
     * 故用该标志把取页串行化（并发调用排队，而不是拿到同一个页面）。
     */
    private pageBusy;
    constructor(options?: ZcodeCaptchaBrowserOptions);
    /** 浏览器是否已就绪。 */
    get ready(): boolean;
    /** 启动（幂等；并发调用共享同一次启动）。 */
    start(): Promise<void>;
    private launch;
    /**
     * 产出**一个新鲜**的 captcha param。
     *
     * ## ⚠⚠ 三条实测约束（第三条是 2026-09-29 用户报障后补的）
     *
     * ### 1. 页面 origin 必须是**真实 https**，不能用 `about:blank`
     *
     * | origin | 同页连续 mint |
     * |---|---|
     * | `about:blank` | **1/3**（#2 起 `F001`） |
     * | `https://zcode.z.ai/` | 连续 5/5 |
     *
     * 阿里云 SDK 会检查 origin（`about:blank` 的是 `"null"`），风控据此拒绝。
     *
     * ### 2. 但**空闲约 15 秒后，同一个页面必然失效**
     *
     * **真实缺陷（用户报障）**：登录后发文本成功，接着发图片报
     * ```
     * captcha 产出失败（stage=fail, err={"success":true,
     *   "verifyResult":false,"verifyCode":"F001","certifyId":"3nieVHobAK"})
     * ```
     *
     * 复现矩阵（本机实测）：
     *
     * | 用例 | 结果 |
     * |---|---|
     * | 立即 mint | ✓ 3786ms |
     * | **间隔 15s**（复用页面） | ✗ `F001` 416ms |
     * | 间隔 45s（复用页面） | ✗ `F001` |
     * | 间隔 90s（复用页面） | ✗ `F001` |
     * | **全新浏览器 + 新页面** | ✓ 3692ms |
     * | 同一新页面再 mint | ✓ 464ms |
     *
     * ⇒ 页面**热时复用很快（约 0.5 秒），冷后必失败**。
     * 用户场景正好命中：发文本 → 模型思考 + 用户打字（数十秒）→ 发图片。
     *
     * ⚠ 我上一轮把「每次新建页面」改成「复用」以提速 2.9 倍，
     * 但那 5 次是**秒级连续**测的，没暴露空闲失效 —— **这是一次回归**。
     *
     * ### 3. 修法：热时复用，冷时换新，**失败即换新重试**
     *
     * ```
     * 距上次使用 < idleReuseMs（默认 8s） → 复用（快）
     * 否则                              → 换新页面（对）
     * 任一次 mint 失败（F001 等）        → 丢弃页面、换新重试一次
     * ```
     *
     * ⚠ 空闲阈值取 **8 秒**：实测 15 秒已失效，故留一半余量。
     * 偏保守只会多付一次新页面成本（约 3.7 秒），比 `F001` 让用户看到报错好。
     */
    /**
     * 产出 captcha param，**并报告本次是否被降级为交互式验证**。
     *
     * ## 为什么需要（对齐官方 ZCode 的观测，2026-10-01）
     *
     * 官方闭源版对每次产出结果都会区分并上报（`out/renderer/assets/styles-*.js`）：
     *
     * ```js
     * mnn({ result: e ? 'interactive_displayed' : 'traceless_passed', … })
     * // pnn() 里维护 traceless_passed_count / captcha_displayed_count
     * ```
     *
     * 那是它判断**设备信誉是否在恶化**的手段 —— 而此前我们没有任何这个数，
     * 排查「为什么突然 502 mint failed」时只能靠猜（这正是本次最缺的东西）。
     *
     * ⚠ 与 {@link mint} 的关系：那个是「只要 param」的既有签名（多处调用），
     * 这个是它的**超集**，内部复用同一条产出路径 —— 不要各写一份。
     */
    mintWithOutcome(config?: ZcodeCaptchaConfig, options?: {
        signal?: AbortSignal;
    }): Promise<CaptchaMintOutcome>;
    /**
     * 产出 captcha param。
     *
     * ## 复用策略（⚠ 空闲失效，见本方法上方的实测表）
     *
     * ```
     * 距上次使用 < idleReuseMs（默认 8s） → 复用（快）
     * 否则                              → 换新页面（对）
     * 任一次 mint 失败（F001 等）        → 丢弃页面、换新重试一次
     * ```
     *
     * ⚠ 空闲阈值取 **8 秒**：实测 15 秒已失效，故留一半余量。
     * 偏保守只会多付一次新页面成本（约 3.7 秒），比 `F001` 让用户看到报错好。
     */
    mint(config?: ZcodeCaptchaConfig, options?: {
        signal?: AbortSignal;
    }): Promise<string>;
    /** `mint` / `mintWithOutcome` 的共用实现（**唯一**的产出路径）。 */
    private mintInternal;
    /** 在**指定页面**上跑一次 captcha（不含页面获取/重试逻辑）。 */
    private mintOnPage;
    /**
     * 取一个可用的页面。
     *
     * ## 复用策略（⚠ 空闲失效，见 `mint()` 的说明）
     *
     * ```
     * forceFresh = true                       → 丢弃旧页、建新页
     * 空闲 > idleReuseMs（默认 8 秒）          → 丢弃旧页、建新页
     * 否则                                    → 复用（约 0.5 秒）
     * ```
     *
     * ⚠ 阈值 8 秒的依据：实测**空闲 15 秒必然 `F001`**，故取一半留余量。
     * 偏保守只多付一次建页成本（约 3.7 秒），比让用户看到报错好。
     *
     * ⚠ 串行化：captcha 是**一次性**的，两个并发 mint 共用同一页面会互相
     * 踩状态。故用 `busy` 标志把取页串起来 —— 并发调用会排队，
     * 而不是拿到同一个页面。
     */
    private acquirePage;
    /**
     * 作废一个页面：关掉它、从池里摘掉。
     *
     * ⚠ **必须真的关闭 target**（不只是清引用）—— 否则页面会累积，
     * 且每个坏页面都占着一份 SDK 实例。
     *
     * ⚠ 关不掉也不抛错：这是清理路径，不该因为清理失败而让 mint 报错。
     */
    private discardPage;
    /** 归还页面（保留复用）。 */
    private releasePage;
    /**
     * 终止浏览器进程**树**并清理所有 CDP 连接。
     *
     * ## ⚠ 为什么必须杀**整棵树**（真实缺陷）
     *
     * 早期只写 `this.child?.kill()` —— 那只杀**主进程**。而 Chromium 是
     * **多进程架构**（browser / gpu / renderer / utility 各一个进程），
     * 主进程被 `SIGTERM` 后**子进程会变成孤儿继续运行**。
     *
     * 实测证据（一次被中断的测试后）：
     *   - **12 个** 残留 `chrome.exe` 全指向同一个 `--user-data-dir`
     *   - **28 个**残留的 `zcode-captcha-*` 临时 profile 目录
     *
     * 生产影响：**每次会话泄漏约 200MB**（一个 Chromium 实例），
     * 且残留进程占着调试端口，会干扰后续启动（曾让一次 `max` 档位测试
     * 表现得像「卡住 80 秒」，实为旧实例干扰）。
     *
     * 修法：POSIX 用进程组（`detached: true` + `kill(-pid)`），
     * Windows 用 `taskkill /T /F`（`/T` = 含子进程树）。
     */
    private kill;
    /**
     * 终止浏览器及其**全部子进程**。
     *
     * ⚠ 不能退回成 `child.kill()`（见 {@link kill} 的说明：会留下孤儿）。
     *
     * ⚠⚠ **必须同步等它做完**（真实缺陷）：早期用异步 `spawn('taskkill', …)`
     * 后立即返回 —— 调用方以为清理完了、紧接着启动下一轮，而旧实例**还活着
     * 占着端口**。若下一轮随机撞到同一端口就会串成：
     * 连到旧实例 → `child` 指向已退出的新进程 → `dispose()` 打空 →
     * **旧实例整棵树泄漏**（实测 10~14 个进程）。
     *
     * 实测对照：`dispose()` 后**立即**下一轮会偶发泄漏；
     * 每轮间隔 2.5 秒则 5/5 干净 —— 正是「没等它退完」的特征。
     *
     * 故改用 `spawnSync`：清理路径阻塞几十毫秒是可接受的，
     * 换来确定性的「返回即已终止」。
     */
    private killProcessTree;
    /** 关闭浏览器并清理临时 profile。 */
    dispose(): void;
}
/**
 * 用户主目录（导出给测试注入用；避免测试真的去碰 `~/.zcode`）。
 *
 * 目前仅用于文档目的 —— 实际路径解析在 `zcode.ts` 的候选表里。
 */
export declare const ZCODE_HOME: string;
//# sourceMappingURL=zcode-captcha.d.ts.map