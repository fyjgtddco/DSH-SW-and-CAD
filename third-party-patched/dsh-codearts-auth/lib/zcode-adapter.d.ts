/**
 * ZCode（智谱 z.ai 免费额度）LLM 适配器。
 *
 * ## 与其它 provider 的共同点
 *
 * 「读凭据 → 直发远端」—— 这一点与 CodeArts / Buddy / Qoder 等**相同**。
 * 用户装好官方 ZCode 客户端并登录一次即可，不需要任何实例常驻。
 *
 * ## 与其它 provider 的差异（全部实测）
 *
 * | 维度 | ZCode | 对照 |
 * |---|---|---|
 * | 凭据来源 | 解密磁盘 `~/.zcode/v2/credentials.json`（AES-256-GCM） | 浏览器登录拿 token |
 * | 协议 | **Anthropic Messages**（非 OpenAI） | 其余多为 OpenAI 兼容 |
 * | 每请求前置 | **产出一个阿里云 captcha**（约 1.2 秒） | 无 |
 * | 请求体准入 | **必须带官方身份块 + 首轮日期块**（否则 3012） | 无 |
 * | 续期 | 无（静态凭据） | 多数有 refresh_token |
 *
 * ## 三个必须真的做到的点
 *
 * 1. **`system` 必须带官方身份块** —— 缺了上游回 `3012 unusual activity`
 *    （实测矩阵见 `zcode-identity.ts`）。且这是**请求体内容**层面的判据，
 *    与 HTTP 头、运行时无关。
 * 2. **首轮 user 消息要带 `<system-reminder>` 日期块** —— 桥侧源码称之为
 *    「3012 的最后一个开关」。
 * 3. **`tools` 必须真的下发**（转成 Anthropic 的扁平 `input_schema` 形态）——
 *    Qoder 与 TRAE 都因漏发而让模型在正文里臆造 XML 工具调用、harness
 *    认不出 → 任务终止。
 *
 * ## ⚠ captcha 是每请求一次，且**不能复用**
 *
 * 上游对缺失 captcha 的请求回 `3007`。而 captcha param **一次性**——
 * 复用同一个会再得 `3007`（实测：同一页面上重复 mint 必 `F001`）。
 * 故每次 `stream()` 都要产出一个新 param（约 1.2 秒）。
 */
import type { Context } from '@deepseek-ai/cordis';
import type { CredentialRef } from '@deepseek-ai/dsh-credentials';
import { LlmAdapter } from '@deepseek-ai/dsh-llm';
import type { GenerateOptions, LlmModelInfo, LlmProviderInfo, LlmResolvedModelInfo, StreamChunk } from '@deepseek-ai/dsh-llm';
import { AccountPool } from './account-pool.js';
import { type ImageRequestTarget } from './image-budget.js';
import type { ZcodeCredential } from './zcode.js';
import { type ZcodeProduct, type ZcodeRemoteModelLike } from './zcode-product.js';
import { type ZcodeCaptchaConfig } from './zcode-captcha.js';
import { ModelGate } from './model-gate.js';
/** 本适配器注册的 provider 路由名（等价于 `ZCODE.id`）。 */
export declare const PROVIDER = "zcode";
/** 远端模型条目（已归一）。 */
export type ZcodeRemoteModel = ZcodeRemoteModelLike;
/**
 * 思考档位的**展示名**。
 *
 * ⚠ **只用于展示** —— 发给上游的 `id` 必须保持小写（见 `resolveModel`）。
 * 两者混用会让上游认不出档次，是本仓库 qoder 那边记过的同型风险。
 *
 * 上游没提供档位的 i18n 名（`app.asar` 里搜不到，官方 IDE 也直接显示
 * `low`/`high`/`max`），故按用户要求用**首字母大写**：
 * 小写形态在 DSH 的选择器里看着像标识符而不像可选项。
 *
 * 未知档位原样返回（上游加了新档位时不至于显示成空白）。
 */
export declare function reasoningEffortLabel(id: string): string;
/** `ZcodeAdapter` 的构造选项。 */
export interface ZcodeAdapterOptions {
    /** 单凭据回退 ref（无账号池时）。 */
    credentialRef: CredentialRef;
    /** 解析当前可用凭据。 */
    resolveCredential: (modelId?: string) => Promise<ZcodeCredential | undefined>;
    /**
     * 凭据失效时的处理。
     *
     * ⚠ ZCode **不可续期**（凭据是静态的）。这个回调存在只是为了让适配器
     * 与其它 provider 同形；实现应当**重读磁盘凭据**而不是去调 refresh 端点。
     */
    refresh: () => Promise<void>;
    /**
     * 产出一个**新鲜**的 captcha param。
     *
     * 由 `index.ts` 注入（它持有 `ZcodeAuth`，能从服务端拉 captcha 配置并
     * 驱动浏览器）。缺省时适配器会自建一个常驻浏览器。
     *
     * ⚠ `options.signal` 必须被**透传**到浏览器侧（`ZcodeCaptchaBrowser.mint`）：
     * captcha 的取页等待与 WebSocket 建连历史上都没有超时，
     * 不透传就等于「用户点停止也停不下来」（真实缺陷，2026-09-29）。
     */
    mintCaptcha?: (options?: {
        signal?: AbortSignal;
    }) => Promise<string>;
    /** captcha 的区域（进 `x-aliyun-captcha-verify-region`）。 */
    captchaRegion?: string;
    /** 拉取远端模型目录；缺省用兜底表。 */
    fetchRemoteModels?: () => Promise<ZcodeRemoteModel[]>;
    /** 账号池（目录门控与黑名单）。 */
    accountPool?: AccountPool;
    /** 产品配置；默认 {@link ZCODE}。 */
    product?: ZcodeProduct;
    /** 注入的 fetch（测试用）。 */
    fetchImpl?: typeof fetch;
    /**
     * 就绪探测（可注入）。返回 false 时 `listModels` 返回空数组，
     * 让整个 provider 分组隐藏 —— 而不是留一个点不动的条目。
     *
     * 缺省实现 = 「磁盘上有没有可解密的凭据」。
     */
    isReady?: () => Promise<boolean>;
    /**
     * 读取图片附件的原始字节（内联为 data URL 用）。
     *
     * ⚠ **图片链路的必需依赖**：DSH 的图片块只带 `attachment:{attachmentId}`，
     * 真正拿字节要经附件服务。缺了它图片会在序列化层变成
     * `[image unavailable]` 占位符（实测：模型回「没有收到任何图片」）。
     */
    readImage?: (attachment: unknown) => Promise<{
        data: Uint8Array;
        mediaType: string;
    } | undefined>;
    /**
     * 读取图片附件的**请求版本**（按预算缩放后的字节）。
     *
     * ⚠ 与 {@link readImage} 的错误契约相反：**不可用时要返回 `undefined`**
     * 而不是抛错，适配器据此回退原图。理由与桥接实现见
     * `src/index.ts` 的 `makeReadImageRequest`、`src/image-budget.ts` 的
     * `projectRequestImage`。
     */
    readImageRequest?: (ref: unknown, target: ImageRequestTarget) => Promise<{
        data: Uint8Array;
        mediaType: string;
    } | undefined>;
    /** 图片像素预算（原图回退前的缩放目标）。 */
    imagePixelBudget?: number;
    /** 图片字节上限（请求版本的目标）。 */
    imageMaxBytes?: number;
    /**
     * **本次请求实际正在使用的**账号 id（额度受限时标记它）。
     *
     * ⚠ 与 `qoder-adapter.ts` 的同名选项**同因**：必须是「池实际返回的那个账号」，
     * 而不是「池当前的默认账号」—— 一旦切到下一个账号，后者不会跟着变，
     * 用它标记会**反复标记同一个账号**，而新账号从未被标记，
     * 下次取号又把新账号选中，于是两个账号之间来回空转
     * （`qoder-adapter.ts` 的 `switchAccountOnQuota` 注释里记了这条实测）。
     *
     * 由 `index.ts` 在 `resolveCredential` 里记录实际返回的账号并提供。
     */
    currentAccountId?: () => string | undefined;
    /**
     * 上游发车闸门（串行 + 按模型间隔）。
     *
     * 缺省时适配器**自建一个**（单实例即可满足「同一时刻一个上游请求」）。
     * 允许注入是为了单测能替换 sleep / clock，让用例毫秒级完成。
     */
    gate?: ModelGate;
    /** 等待实现（注入以便单测；仅用于并发限流重试的退避）。 */
    sleep?: (ms: number, signal?: AbortSignal) => Promise<void>;
}
/** ZCode 模型适配器。 */
export declare class ZcodeAdapter extends LlmAdapter {
    private readonly options;
    private readonly product;
    private readonly fetchImpl;
    /** 兜底模型索引（id → 条目）。 */
    private readonly fallbackIndex;
    private remoteModels;
    /** 自建的常驻浏览器（仅当调用方没注入 `mintCaptcha` 时用）。 */
    private captchaBrowser;
    /** captcha 配置缓存（配置很少变，但与凭据一样**不长期缓存**）。 */
    private captchaConfig;
    /**
     * 上游发车闸门（串行 + 按模型最小间隔）。
     *
     * ⚠ 必须是**实例字段**（而不是每次请求新建）：闸门靠「共享的尾巴指针」与
     * 「跨请求记住上次发车时刻」生效，每次新建等于没有闸门。
     */
    private readonly gate;
    /** 退避等待实现（注入以便单测毫秒级完成）。 */
    private readonly sleepImpl;
    constructor(options: ZcodeAdapterOptions);
    /**
     * 描述本适配器拥有的 provider 路由。
     *
     * 对入参做防御性归一化：DSH 会强制校验 `info.id === provider`，
     * 而模型设置页会用该 id 计算 `deriveKeyRef(provider)`
     * （内部调 `provider.toUpperCase()`）。一旦 provider 不是字符串，
     * 直接回退到本产品的 id。
     */
    providerInfo(provider: string): LlmProviderInfo;
    /**
     * 完整目录（**不套黑名单**），带最终展示名。
     *
     * 设置页需要它渲染被关闭的模型 —— 否则那些条目只能凭 `disabledMap` 的 key
     * 补回，而那条路径拿不到展示名，会退化成裸 id
     * （`AGENTS.md` 记过 Raccoon 的同款用户报障）。
     */
    listAllModels(): readonly {
        id: string;
        name: string;
    }[];
    /** 取（并缓存）远端模型目录；失败时回退兜底表。 */
    private loadModels;
    private inputModalitiesFor;
    /** 就绪判据：默认看磁盘上有没有可用凭据。 */
    private ready;
    listModels(_provider: string): Promise<readonly LlmModelInfo[]>;
    resolveModel(provider: string, model: string, _signal?: AbortSignal): Promise<LlmResolvedModelInfo>;
    /**
     * 兼容 0.1.1-rc.2：新版 `LlmRuntime.prepareCall()` 会调用
     * `registration.adapter.prepareCall(...)`，而本仓库链接的 dsh-llm 副本
     * 基类尚未提供该方法。与其余适配器同款 shim。
     */
    prepareCall(provider: string, model: string, signal?: AbortSignal): Promise<{
        model: LlmResolvedModelInfo;
        stream: (options: GenerateOptions) => AsyncIterable<StreamChunk>;
    }>;
    /** 取得 captcha param（注入优先，否则自建常驻浏览器）。 */
    private mintCaptcha;
    /** 允许外部（`index.ts`）设置服务端下发的 captcha 配置。 */
    setCaptchaConfig(config: ZcodeCaptchaConfig | undefined): void;
    /**
     * ★★ 超时与中断的**作用域**：必须覆盖整轮
     * （captcha 产出 → 请求 → **流式读取**）。
     *
     * ## 为什么必须搬到这一层（真实缺陷，2026-09-29）
     *
     * 旧实现把 `setTimeout(abort)` 与 `removeEventListener('abort')` 放在
     * **`fetch` 的 `finally`** 里 —— 那个 `finally` 在「响应头回来」时**就已执行**，
     * 于是：
     *
     * 1. **流式读取阶段完全没有超时**：`requestTimeoutMs`（180s）形同虚设；
     * 2. **用户中断的通道在流开始之前就被摘掉**：`options.signal` 的 abort
     *    不再转发给 `controller`，`response.body` 的读取永不中止。
     *
     * 两者叠加的后果正是用户报障（本机实测三次、含一次 1018.7 秒）：
     * UI 停在「深度求索中，用时 5分27秒…」不动，模型既不输出思考也不输出正文，
     * **「停止」按钮点了没反应，只能重启宿主**。
     *
     * ⚠ 会话日志里的收尾事件 `step/end` + `turn/end{kind:'interrupted'}` 与
     * `step/start` **同一毫秒** —— 那是 `dsh-session` 的 `openTurnClosers()`
     * 在 repair 时**合成**的（它「复用最后一个真实事件的时间戳」），
     * 真相是这个 turn **从未结束**。排查时别被它误导。
     */
    stream(options: GenerateOptions): AsyncIterable<StreamChunk>;
    /**
     * `stream()` 的实际实现。
     *
     * ⚠ `controller` 由调用方传入（而不是在这里新建）：它的 signal 必须同时
     * 管住 **captcha 产出** 与 **SSE 读取**，见 `stream()` 的说明。
     */
    private streamScoped;
    /**
     * 取一份可用凭据；两次都拿不到就报明确的「去登录」错误。
     *
     * 抽成方法是因为它现在出现在**切号循环之外**（凭据由循环内的切号逻辑更新），
     * 而「拿不到 → refresh → 再拿」这套顺序必须与既有实现逐字一致。
     */
    private resolveCredentialOrThrow;
    /**
     * 把**一次**上游请求经过闸门发出去，并把传输层异常翻译成 harness 的错误类别。
     *
     * ## 为什么必须经过闸门（`this.gate`）
     *
     * 上游 `429 code:3009` 是**并发配额** —— 同一时刻两个请求在飞，必有一个白撞。
     * 闸门还给「同一模型」加最小发车间隔（串行只保证不重叠，不保证有间隔）。
     * 依据与参数见 `model-gate.ts` 与 `zcode-product.ts` 的字段注释。
     *
     * ## ⚠ 闸门**只包这一下 fetch**，不包 captcha
     *
     * captcha 产出（几百毫秒到几秒）必须在闸门外 —— 否则会变成
     * 「排在 N 个请求后面再 mint」，那边的实测是 `mintMs` 从 200-500ms
     * 暴涨到 2500-3100ms。
     */
    private sendUpstream;
    /**
     * 额度用尽 / 无权益时的**标记 + 换账号**。
     *
     * ## 与 `qoder-adapter.ts` 的同名方法同因同形（差异只在「标记到什么时候」）
     *
     * ⚠ **标记用的「当前账号」必须是调用方传入的 `activeAccountId`**，
     * 不能每次问 `this.options.currentAccountId()` —— 后者是「池当前的默认账号」，
     * 一旦切到下一个账号它**不会跟着变**：用它标记会**再标记一次旧账号**，
     * 而新账号从未被标记，下次取号又把新账号选中，于是在两个账号之间反复空转
     * （qoder 写单测时实测到了这一点：标记记录是 `['acct-A','acct-A']`）。
     *
     * ⚠ **必须把 `tried` 传给 `getAvailableAccount`**：池按用户手动顺序返回候选，
     * 刚失败的那个账号**可能仍排第一**，不排除就会拿回同一个、命中 `tried.has`
     * 而立即放弃切换（换号形同虚设）。
     *
     * ## 标记到什么时候
     *
     * ZCode 的免费额度是**按自然日**结算的（`billing/balance` 的桶带
     * `expires_at`，活动说明为「每日刷新」）。故与 qoder 一致取
     * **UTC+8 当日 24:00** —— 复用 `nextUtc8DayStartMs()`，**不**用
     * `parseRateLimitError` 的「1 小时后」兜底（那会让标记过早失效，
     * 用户 1 小时后再撞一次同样的墙）。
     *
     * ⚠ 只标记**该账号 + 该模型**（`updateModelRateLimit` 的既有语义）：
     * 额度是「账号 + 模型」维度的，同一账号在别的模型上仍可能可用。
     */
    private switchAccountOnQuota;
    /** 释放自建的浏览器（由 `index.ts` 的 cleanup 调用）。 */
    stop(): void;
}
/**
 * 「**秒回空**」= 该账号对这个模型**没有权益**（不是链路故障）。
 *
 * ## 判据（两条同时成立，缺一不可）
 *
 * 1. 错误是 `EMPTY_RESPONSE`（上游 200 但一个内容块都没有）
 * 2. **耗时 < {@link ZCODE_FAST_EMPTY_MS}**（3 秒）
 *
 * ## 依据（`dsh-free-glm` 的实测，2026-09-29）
 *
 * 空回复有两种**成因完全不同**的形态，旧代码混为一谈，于是把用户引向
 * 「重启实例」这个**无效方向**：
 *
 * | 成因 | 耗时 | 壳日志特征 | 正确处置 |
 * |---|---|---|---|
 * | **模型无权益** | **150-200ms** | 从未出现 provider runtime headers 请求 | **换账号 / 换模型** |
 * | 链路卡住 | ≈ 180000ms | `durationMs≈180000, textLength:0` | 重试 / 重启 |
 *
 * 那边实测的原文：`GLM-5.3` 的请求**从未出现**「收到 provider runtime headers
 * 请求」，而 Flash 每次都完整走 —— 即实例拿不到该模型的鉴权材料，
 * **根本没发往上游**，于是立刻回一个空 content。
 *
 * @param error - `consumeAnthropicSse` 抛出的错误。
 * @param elapsedMs - 从开始消费到抛错的耗时。
 */
export declare function isFastEntitlementMiss(error: unknown, elapsedMs: number): boolean;
/**
 * 「秒回空」被判为**权益/额度**问题时的错误文案。
 *
 * ## 为什么必须有这一段（真实缺陷，2026-10-01）
 *
 * **用户报障**：zcode 赠送额度用完之后，界面显示的是
 *
 * > 本轮运行失败　zcode: 模型返回了空响应（无任何 text / thinking / tool 内容）
 * > `EMPTY_RESPONSE`
 *
 * —— 这是 SSE 消费器的**通用**文案，它描述的只是「我们没收到内容」这个现象，
 * **完全没说出真实原因**（额度用尽），用户无从判断该等额度、换模型还是加账号。
 *
 * ## 判据本来就是现成的（这才是最可惜的地方）
 *
 * 「**秒回空** = 该账号对这个模型没有权益（请求根本没送达模型）」
 * 这条判据早就在 {@link isFastEntitlementMiss} 里，依据是那边的实测
 * （150-200ms 空响应 vs 卡住形态的 ≈180000ms）。
 * 但它此前**只用于决定「要不要切号」**，判据本身从未进入文案 ——
 * 于是走到「无法再切号」这一步时，抛出的还是那个通用的裸错误。
 *
 * ## 连带修掉的第二个缺陷：错误码
 *
 * 原先抛 `EMPTY_RESPONSE`，而它**在** harness 的 `DEFAULT_RETRYABLE_CODES`
 * 里 —— 于是「额度已用尽」这种**确定性**错误被白退避重试 5 次
 * （用户截图里的「已重试模型请求 (5/5)」就是它，约 15.5 秒）。
 * 现在抛 `QUOTA_EXCEEDED`（**不在**该集合里）→ 立即失败并给出真实原因。
 *
 * ⚠ 与 `qoder` 那次「110 额度错误落在 `SERVER`」是**同型缺陷**：
 * 用错误码的默认归类代替了对业务语义的判断（`AGENTS.md` 记过该教训）。
 *
 * ## ⚠ 措辞必须诚实：不断言是「额度用尽」还是「无权益」
 *
 * 这两种成因在 wire 上**表现完全相同**（都是秒回空），我们**无法区分**：
 * - 赠送额度用尽（`billing/balance` 的桶为 0）
 * - 该账号对这个模型没有权益（对照那边实测的 `GLM-5.3` 从未拿到鉴权材料）
 *
 * 故文案写「额度已用尽或该模型无可用权益」，并给出**两种都能解决**的建议 ——
 * 不编造一个我们其实没验证过的结论。
 *
 * @param model - 请求的模型 id（用户据此决定换哪个）。
 * @param attemptedAccounts - 本次已尝试过的账号数（>1 时才提，否则误导）。
 */
export declare function zcodeEntitlementErrorMessage(model: string, attemptedAccounts: number): string;
/**
 * 上游「并发限流」的业务码。
 *
 * 实测形态（`dsh-free-glm` 抓到，`bench/CAPABILITY-REPORT.md` 有原始拒绝体）：
 * ```json
 * HTTP 429 {"code":3009,"msg":"model concurrency limit exceeded"}
 * ```
 *
 * ⚠ 它与 `1005` 都走 HTTP 429，**只看状态码分不清**（那边的注释原话：
 * 「429 的语义藏在 `{"code":1005}` 里」）。
 */
export declare const ZCODE_CONCURRENCY_CODE = "3009";
/**
 * 「秒回空」的耗时阈值（毫秒）—— 判据是「**快速**返回 + 内容为空」的组合。
 *
 * 依据（`dsh-free-glm/src/adapter.ts` 的 `EMPTY_REPLY_FAST_MS` 家族，
 * 实测 2026-09-29）：空回复有**两种成因完全不同**的形态：
 *
 * | 成因 | 耗时 | 处置 |
 * |---|---|---|
 * | **模型/账号无权益**（实例拿不到该模型的鉴权材料，根本没发上游） | **150-200ms** | 换账号 / 换模型 |
 * | 链路卡住（上游静默直到超时） | ≈ 超时上限（180s） | 重试 / 排查链路 |
 *
 * 取 3000ms：远高于实测的 200ms，又远低于卡住形态 —— 两者不会混淆。
 */
export declare const ZCODE_FAST_EMPTY_MS = 3000;
/**
 * 判断是否是**并发限流**（`3009`）—— 「等一下就能过」，故**重试**，
 * 且**不切账号**（换账号也一样撞，白白标记掉一个可用账号）。
 */
export declare function isZcodeConcurrencyLimited(status: number, body: string): boolean;
/**
 * 判断是否是**额度用尽**（确定性错误：等到账期重置才可能恢复）。
 *
 * 两个码都要认：
 * - `1005` `exceed quota limit` —— 免费额度通道的**额度用尽**
 * - `1113` `余额不足或无可用资源包` —— ultra/coding-plan 侧的余额不足
 *
 * ⚠ **必须排除 `3009`**：并发限流同样返回 429，但它「等一下就能过」，
 * 若被归到这里就会把账号错标成「当日用尽」（误伤一个完全可用的账号，
 * 与 `qoder` 那次「把 rate_limit 当成 billing」是同一类错误）。
 */
export declare function isZcodeQuotaExhausted(status: number, body: string): boolean;
/**
 * 并发限流重试的退避时长（**线性**：base、2×base、…）。
 *
 * 参数依据（`dsh-free-glm` 的实测）：退避起点从 900ms 提到 **1500ms** ——
 * 实测 900ms 的重试**仍然撞 429**，说明并发窗口比 900ms 长。
 * 重试 2 次，最坏总等待 ≈ 1500 + 3000 = 4.5 秒（用户可接受）。
 *
 * @param attempt - 已失败次数（0 表示第一次失败后的等待）。
 */
export declare function zcodeConcurrencyRetryDelayMs(attempt: number, baseMs: number): number;
/**
 * 给工具表的**最后一个**工具打 prompt caching 断点。
 *
 * ## 为什么只打一个（这是 Anthropic 缓存的语义，不是省事）
 *
 * Anthropic 的 prompt caching 是**前缀式**的：某个位置上的
 * `cache_control` 断点覆盖「**该断点之前的所有内容**」（system + 它之前的全部 tools）。
 * 故只在最后一个 tool 上打一个点，就等于把「system + 全部 tools」整段纳入缓存，
 * **不必逐个打**（而逐个打会撞上「最多 4 个断点」的上限，见下）。
 *
 * ## 真实缺陷（本仓库此前缺失，证据来自 dsh-free-glm 的 P0-2）
 *
 * 那边 dump 出的实际请求里：
 * ```
 * system blocks:  len=42 cc=True / len=2856 cc=True / len=2836 cc=True
 * tools[0] keys:  name, description, input_schema   ← 无 cache_control
 * ```
 * **24 个工具、19492 字节，一个断点都没有** —— 每步请求全量重算这段 prefill。
 *
 * 本仓库同样缺（`toAnthropicTools` 从不产出 `cache_control`），且我们还有个
 * 放大器：`system` 里含调用方（DSH）的完整规范。⇒ 这条对**每一步**都有效，
 * 是端到端耗时的主要可优化项之一。
 *
 * ## ⚠ 断点预算
 *
 * Anthropic 单请求最多 **4 个** `cache_control` 断点。`zcode-identity.ts` 的
 * system 块当前是「每块都打」（3-4 个）—— 已贴近上限。若上游因超限报错，
 * 把 system 收敛成「只在最后一块打断点」（那样仍覆盖全部 system 块），
 * 再把预算留给这里的 tools 断点。
 *
 * @param tools - 转换后的 Anthropic 工具数组（**不修改入参**）。
 */
export declare function withToolCacheBreakpoint<T extends object>(tools: readonly T[]): (T | (T & {
    cache_control: {
        type: 'ephemeral';
    };
}))[];
/**
 * 把上游错误翻成人能看懂的一句话。
 *
 * ⚠ 两个业务码要单独说清，因为它们的**处理方式完全不同**：
 * - `3007` = captcha 校验失败（**可重试**：换个新 param 即可）
 * - `3012` = 风控拦截（**不要重试**：有账号冷却惩罚，重复触发会升级封禁）
 */
export declare function describeUpstreamError(status: number, body: string): string;
/**
 * 把上游错误码映射到 harness 的错误类别。
 *
 * ⚠ 映射决定了**会不会被自动重试**（harness 的 `DEFAULT_RETRYABLE_CODES`
 * 是 `[EMPTY_RESPONSE, RATE_LIMIT, SERVER, TIMEOUT, TRANSPORT]`）：
 *
 * | 上游 | 映射 | 会被重试吗 | 理由 |
 * |---|---|---|---|
 * | `3007` captcha | `RATE_LIMIT` | **是** | 换新 param 就能过，值得重试 |
 * | `3012` 风控 | `PERMISSION` | **否** | 有账号冷却惩罚，重试会加重 |
 * | `1002`/401 | `AUTH` | 否 | 需用户重新登录 |
 * | `1113` 余额 | `QUOTA_EXCEEDED` | **否** | 确定性错误（要充值） |
 * | 其余 5xx | `SERVER` | 是 | 暂时性 |
 */
export declare function httpErrorCodeForZcode(status: number, body: string): string;
/**
 * 在 `ctx.llm` 上注册 zcode provider 路由与适配器。
 *
 * 返回适配器实例：Jet Hub「显示列表」需要 `listAllModels()`
 * （不受黑名单影响、带最终展示名）。`ctx.llm` 不透传自定义方法，
 * 故须由调用方持有引用并在 `index.ts` 的 `modelAdapters` 里登记。
 */
export declare function registerZcodeLlm(ctx: Context, options: ZcodeAdapterOptions): ZcodeAdapter;
//# sourceMappingURL=zcode-adapter.d.ts.map