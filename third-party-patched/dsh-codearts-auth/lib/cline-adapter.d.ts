/**
 * Cline LLM 适配器。
 *
 * ## 与既有 provider 的差异
 *
 * Cline 的推理端点是**标准 OpenAI 兼容**的
 * （`POST {apiBase}/api/v1/chat/completions`，实测标准 SSE），
 * 故 OpenAI 协议层（消息序列化 / SSE 消费 / 错误归类）**完全复用**
 * `src/openai-compat.ts` —— 与 Qoder 同做法。
 *
 * 差异只有三处：
 *
 * 1. **鉴权头是 `Bearer workos:<jwt>`**（前缀不可剥，见 `src/cline.ts`）；
 * 2. **思考字段是 `delta.reasoning`**（不是 `delta.reasoning_content`）——
 *    已由 `consumeOpenAiSse` 同时兼容；
 * 3. **模型目录来自两个端点**（`recommended-models` 给 free 集合、
 *    `/models` 给全量 id），见 `src/cline-models.ts`。
 *
 * ## 免费标注
 *
 * 免费模型在 `name` 里拼 ` · 免费`。⚠️ **必须写进 `name` 而非 `description`**：
 * composer 的模型切换菜单只渲染 `name`（`dsh-client-ui-model-selection` 的
 * ModelSelect 里只有 `title: model.name` 与 `children: model.name`）。
 * 这是被用户报障纠正过的结论。
 *
 * ⚠️ 免费资格是**服务端动态下发**的（`recommended-models` 的 `free` 数组），
 * 故本适配器**不硬编码任何免费模型名** —— 与 CodeArts benefit 集合同约定。
 */
import type { Context } from '@deepseek-ai/cordis';
import type { CredentialRef } from '@deepseek-ai/dsh-credentials';
import { LlmAdapter } from '@deepseek-ai/dsh-llm';
import type { GenerateOptions, LlmModelInfo, LlmProviderInfo, LlmResolvedModelInfo, StreamChunk } from '@deepseek-ai/dsh-llm';
import { AccountPool } from './account-pool.js';
import { type ClineCredential } from './cline.js';
import { type ClineModel } from './cline-models.js';
import { type ClineProduct } from './cline-product.js';
import { type ImageRequestTarget } from './image-budget.js';
/** 本适配器注册的 provider 路由名（等价于 `CLINE.id`）。 */
export declare const PROVIDER = "cline";
/**
 * 清洗工具参数 schema 里的 `enum`（递归）。
 *
 * ## 为什么必须清洗（实测 400，用户报障 2026-09-25）
 *
 * harness 下发的工具集里，某些参数的 `enum` 含**空字符串**成员。Gemini 系模型
 * （经 `google` / `vertex` provider）对此**严格校验**，直接拒绝整个请求：
 *
 * ```
 * GenerateContentRequest.tools[0].function_declarations[34]
 *   .parameters.properties[permission].enum[3]: cannot be empty
 * ```
 *
 * ⚠️ 该错误**只在部分 provider 上暴露**：上游一次请求会依次尝试多个 provider，
 * 实测命中 vertex 时报「maxOutputTokens 越界」、命中 google 时报上述 enum 错误。
 * 两者是**两个独立根因**，都要修，否则路由一漂移就复发。
 *
 * ⚠️ 本适配器**从不自己造 enum** —— `stream()` 原样透传
 * `options.tools[].parameters`，故脏数据来自上游 harness。但请求是我们发的，
 * 只能在我们这一侧拦住。
 *
 * ## 三条边界（都要守）
 *
 * - **只删空字符串**（含纯空白），其余成员原样保留 —— `enum` 可能是数字/布尔
 *   数组，按「只留字符串」过滤会把合法的数值枚举整段丢掉；
 * - 过滤后为空则**整个 `enum` 键丢弃**（空 `enum` 同样非法），而非留下 `[]`；
 * - **递归下钻**：`properties` / `items` 等嵌套层里的 `enum` 同罪。
 */
export declare function sanitizeClineToolParameters(value: unknown): unknown;
/** `ClineAdapter` 的构造选项。 */
export interface ClineAdapterOptions {
    /** 默认凭据 ref（仅用于类型/日志，实际解析走 `resolveCredential`）。 */
    credentialRef: CredentialRef;
    /** 从凭据存储解析凭据。 */
    resolveCredential: (modelId?: string) => Promise<ClineCredential | undefined>;
    /** 静默续期凭据。 */
    refresh: () => Promise<void>;
    /** 多账号池（用于限流时切换账号与模型黑名单）。 */
    accountPool?: AccountPool;
    /**
     * 读取图片附件的原始字节（内联为 data URL 用）。
     *
     * 由调用方桥接 `ctx.attachments.readImage(ref)`；未提供时收到图片会报
     * `UNSUPPORTED_CONTENT`（而不是静默丢弃）。
     */
    readImage?: (attachment: unknown) => Promise<{
        data: Uint8Array;
        mediaType: string;
    } | undefined>;
    /**
     * 读取图片附件的**请求版本**（按像素预算与字节目标缩放后的字节）。
     *
     * ⚠️ 与 {@link readImage} 的错误契约**相反**：不可用时必须返回 `undefined`
     * 而不是抛错。判据与回退都在共享的 `projectRequestImage` 里。
     *
     * 背景（issue !IKITT9）：Cline 撞的是**请求体体积**（实测 24 张原图全过、
     * 32 张 ≈122 MiB 才 `TRANSPORT`），不是腾讯那道图片 token 预算 ——
     * 但解法同样是缩放，且阈值余量最小，所以配一个宽松的字节目标。
     */
    readImageRequest?: (attachment: unknown, target: ImageRequestTarget) => Promise<{
        data: Uint8Array;
        mediaType: string;
    } | undefined>;
    /** 产品配置；默认 {@link CLINE}。 */
    product?: ClineProduct;
    /** 注入的 fetch（测试用）。 */
    fetchImpl?: typeof fetch;
    /**
     * 模型目录加载器覆盖（测试用）。
     *
     * 默认走 `loadClineModels`（两次远端请求）。注入后单测可完全离线。
     */
    loadModels?: (options: {
        credential?: ClineCredential;
    }) => Promise<{
        models: ClineModel[];
        warnings: string[];
    }>;
}
/**
 * Cline 模型适配器。
 *
 * 使用 `Bearer workos:<jwt>` 鉴权，仅支持 SSE（与官方客户端一致）。
 */
export declare class ClineAdapter extends LlmAdapter {
    private readonly options;
    private readonly product;
    private readonly fetchImpl;
    /** 远端模型目录缓存（首次成功后填充）。 */
    private remoteModels;
    /** 正在进行中的目录加载（避免并发重复请求）。 */
    private loading;
    constructor(options: ClineAdapterOptions);
    /**
     * 描述本适配器拥有的 provider 路由。
     *
     * 对入参做防御性归一化：DSH 会强制校验 `info.id === provider`，而模型设置页
     * 会用该 id 计算 `deriveKeyRef(provider)`（内部调 `provider.toUpperCase()`）。
     * 一旦 provider 不是字符串（上游传入 undefined），直接回退到本产品的 id，
     * 避免 `undefined.toUpperCase is not a function` 在客户端炸开。
     */
    providerInfo(provider: string): LlmProviderInfo;
    /**
     * 模型接受的输入模态。
     *
     * 按**模型**判定（内嵌目录的 `capabilities` 含 `images`），不是按 provider
     * 一刀切。未声明时**保守报 text**：宁可少报能力（用户改用文本描述），
     * 也不要报一个服务端不认的模态（请求会失败）。
     */
    private inputModalitiesFor;
    /**
     * 懒加载远端模型目录。
     *
     * `resolveModel` 可能先于 `listModels` 被调用（如直接进入会话），
     * 此时同样触发远端拉取。
     *
     * ⚠️ **并发去重**：`listModels` 与 `resolveModel` 会在启动时被 DSH 并发调用，
     * 不去重会打出多份重复的远端请求（两个端点各一次，乘以并发数）。
     */
    private ensureRemoteModels;
    /** 兜底目录（远端不可用时的静态表，含 5 个免费模型）。 */
    private fallbackCatalog;
    /**
     * 完整模型目录（**不应用用户黑名单**），含最终展示名（免费标记）。
     *
     * 设置页必须渲染被关闭的模型（否则用户无法重新打开），而 `listModels` 会按
     * 黑名单过滤掉它们 —— RPC 层只能凭裸 id 补回，展示名随之丢失
     * （用户报障：「关闭的就没有显示倍率」）。详见 `model.list` 端点的注释。
     *
     * ⚠️ 本方法是**同步**的（与 RPC 层 `ModelCatalogSource` 契约一致），
     * 故它只能读已缓存的目录。首次调用若缓存为空会触发一次**后台**加载，
     * 由下一次调用（或 DSH 的目录刷新）拿到结果 —— 而 `model.list` 端点
     * 之前一定会先走 `ctx.llm.listModels()`（那会 await 加载完成），
     * 故实际使用中不会读到空目录。
     */
    listAllModels(): readonly {
        id: string;
        name: string;
    }[];
    listModels(_provider: string): Promise<readonly LlmModelInfo[]>;
    resolveModel(provider: string, model: string, _signal?: AbortSignal): Promise<LlmResolvedModelInfo>;
    /**
     * 兼容 0.1.1-rc.2：新版 `LlmRuntime.prepareCall()` 会调用
     * `registration.adapter.prepareCall(...)`，而本仓库链接的 dsh-llm 副本
     * 基类尚未提供该方法，缺少时会在每轮请求开始时抛
     * `registration.adapter.prepareCall is not a function`。
     * 与 `BuddyAdapter` / `QoderAdapter` 同款 shim。
     */
    prepareCall(provider: string, model: string, signal?: AbortSignal): Promise<{
        model: LlmResolvedModelInfo;
        stream: (options: GenerateOptions) => AsyncIterable<StreamChunk>;
    }>;
    stream(options: GenerateOptions): AsyncIterable<StreamChunk>;
    /** 消费 OpenAI 兼容 SSE（共享实现）。 */
    private consume;
    /** 发起一次 chat 请求；网络失败映射为可重试的 TRANSPORT 错误。 */
    private send;
}
/**
 * 该失败是否值得**换号重试**。
 *
 * 判据与 buddy 系一致：429（频率限制）与 402（额度耗尽）。
 * 其余错误（400 请求格式错、5xx 服务端故障）换号也无用 ——
 * 5xx 是所有账号共用的服务端问题，400 是请求本身的问题。
 */
export declare function isClineRotatableFailure(status: number, body: string): boolean;
/**
 * 额度/限流文案标记。
 *
 * 中英双通道：Cline 是国际产品，但用户账号可能是中文界面，
 * 且网关在不同层可能给出不同文案。
 */
export declare const CLINE_CREDIT_MARKERS: readonly string[];
/**
 * 该失败是否应**记为模型的限流标记**（让 UI 亮出「限额重置」徽章）。
 *
 * 只覆盖真正表达「这个模型/账号此刻不可用」的状态码：429 与 402。
 * 文案命中的 4xx（如 400 + 含 "credit" 的措辞）**不记徽章** ——
 * 徽章的含义必须是「受限」，而不是「这个账号出过错」
 * （与 `recordsLobsteraiRateLimit` 同约定）。
 */
export declare function recordsClineRateLimit(status: number, _body: string): boolean;
/**
 * 该失败是否是**与凭据无关的访问限制**（地域封锁 / 模型未开通）。
 *
 * ## 为什么必须单独识别（真实缺陷，用户报障 2026-09-25）
 *
 * Cline 对「该地区不可用」的模型返回 **403**，而 401/403 在本适配器里原本一律
 * 被当作「凭据过期」：触发续期 → 重试 → 仍 403 → 最终 `httpErrorCode(403)`
 * 归成 `AUTH` → DSH 渲染成「**API 密钥无效**」。
 *
 * 实测 `cline-free/muse-spark-1.3-contributor`：
 *
 * ```
 * 403 {"error":"access forbidden: cline-free/muse-spark-1.3-contributor
 *       is not available in your region","success":false}
 * ```
 *
 * 后果有两个：① 真实原因（地域限制）被完全掩盖，用户以为要去重新登录；
 * ② 每次请求都白跑一次续期（续期还会成功，所以不会提前报错，纯属浪费）。
 *
 * ⚠️ **不能按状态码一刀切**：同一批 403 里既有真的凭据问题，也有地域限制，
 * 只能靠**响应体文案**区分。认三种表述（上游措辞可能微调，故取特征词）：
 * `not available in your region` / `access forbidden` / `region not supported`。
 *
 * ⚠️ 命中时**跳过续期**，并让错误文案带出真实原因 —— 续期在这里永远无用，
 * 反而拖慢失败反馈。
 */
export declare function isClineRegionForbidden(status: number, body: string): boolean;
/**
 * 在 `ctx.llm` 上注册 Cline provider 路由与适配器。
 *
 * 路由名与配置页展示名由产品配置驱动，得到 `cline`。`settingsNs` 经
 * `settingsNamespaceFor()` 解析：老契约（≤0.1.6）下是 `llm-cline`；
 * 0.1.7-rc.1 起 settings 命名空间只能是 profile 条目 id，故解析为本插件条目 id。
 */
export declare function registerClineLlm(ctx: Context, options: ClineAdapterOptions): ClineAdapter;
//# sourceMappingURL=cline-adapter.d.ts.map