/**
 * buddy (腾讯 CodeBuddy) LlmAdapter
 *
 * 使用标准 OpenAI Chat Completions 协议 + Bearer access_token 鉴权。
 * 认证由 buddy-auth.ts 服务完成（external-link-v2 轮询式登录 + refresh_token 续期）。
 *
 * 端点：https://copilot.tencent.com/v2/chat/completions
 * 模型列表：静态默认（对齐 /v3/config craft agent models）+ 登录后的动态拉取
 */
import type { Context } from '@deepseek-ai/cordis';
import type { CredentialRef } from '@deepseek-ai/dsh-credentials';
import { LlmAdapter } from '@deepseek-ai/dsh-llm';
import { AccountPool } from './account-pool.js';
import type { GenerateOptions, LlmModelInfo, LlmProviderInfo, LlmResolvedModelInfo, StreamChunk } from '@deepseek-ai/dsh-llm';
import type { BuddyCredential, BuddyRemoteModel } from './buddy.js';
import { type BuddyProduct } from './product.js';
import { type ImageRequestTarget } from './image-budget.js';
/**
 * CodeBuddy（中国版）的 chat completions 基址。
 *
 * 仅供既有导入方（如 e2e 探针）使用；适配器实例实际请求的基址是
 * `` `${this.product.endpoint}/v2` `` —— 国际版 WorkBuddy 的域名不同
 * （www.workbuddy.ai），故不能再用本常量拼接请求 URL。
 */
export declare const CHAT_API_BASE = "https://copilot.tencent.com/v2";
/**
 * CodeBuddy 的 provider 路由名（历史常量，保留导出以兼容既有导入方）。
 *
 * 注意：适配器实例实际使用的路由名是 `product.id`（`this.product.id`），
 * 本常量只表示 CodeBuddy 那一份取值，不再代表所有产品。
 */
export declare const PROVIDER = "buddy";
/** 默认模型（deepseek-v4-flash，对齐 IDE 默认）。 */
export declare const DEFAULT_MODEL = "deepseek-v4-flash";
export interface BuddyAdapterOptions {
    credentialRef: CredentialRef;
    /** 前缀缓存会话标识（prompt_cache_key）；未提供时随机生成一个。 */
    sessionId?: string;
    /** 从凭据存储解析凭据。 */
    resolveCredential: (modelId?: string) => Promise<BuddyCredential | undefined>;
    /** 静默续期凭据。 */
    refresh: () => Promise<void>;
    /** 动态拉取远端模型列表（含上下文窗口与能力，若远端下发）；失败时调用方回退到静态列表。 */
    fetchRemoteModels?: () => Promise<BuddyRemoteModel[]>;
    /**
     * 读取一张图片的原始字节（图片输入必需）。
     *
     * 由调用方桥接 `ctx.attachments.readImage(ref)`。**失败必须抛错**：
     * 未提供本回调时适配器会报 UNSUPPORTED_CONTENT；提供了但读不到字节时
     * 也必须抛错（不要返回 undefined），否则图片会被静默丢弃、线上请求
     * 退化成纯文本，而用户看不到任何原因。
     *
     * 返回类型刻意不含 `undefined`——早期契约允许返回 undefined 表示
     * 「读不到」，调用方据此 `continue`，正是静默丢图的源头。
     */
    readImage?: (attachment: unknown) => Promise<{
        data: Uint8Array;
        mediaType: string;
    }>;
    /**
     * 读取一张图片的**请求版本**（按目标尺寸缩放后的字节），用于替代裸原图。
     *
     * ⚠️ 与 {@link readImage} 的错误契约**刻意相反**：本回调**不可用时必须返回
     * `undefined`**（而不是抛错），因为缩放是一项优化 —— 附件服务未装、版本过旧
     * 没有 `readImageRequest`、或后端拒绝投影（`ATTACHMENT_PROJECTION_UNSUPPORTED`）
     * 时，正确行为是**发原图**，而不是把一次本来能成功的请求打死。
     * 桥接实现见 `src/index.ts` 的 `makeReadImageRequest`。
     *
     * 背景（issue !IKITT9）：原先恒发原图，36 张 1721×997 的截图就顶穿网关
     * 「单次请求图片视觉 token ≈100,000」的上限，此后该会话每一轮都失败。
     */
    readImageRequest?: (attachment: unknown, target: ImageRequestTarget) => Promise<{
        data: Uint8Array;
        mediaType: string;
    } | undefined>;
    fetchImpl?: typeof fetch;
    /** 多账号池（用于限流时切换账号） */
    accountPool?: AccountPool;
    /**
     * 产品配置；默认为 CodeBuddy。
     *
     * 决定请求身份标识（X-Product-Code / User-Agent）、模型元数据的 provider
     * 字段、providerInfo 的展示名，以及 registerBuddyLlm 注册的路由与
     * settingsNs。两个内置产品（CodeBuddy / WorkBuddy）共用同一后端与协议，
     * 差异全部由本配置承载。
     */
    product?: BuddyProduct;
}
/**
 * 纯函数：由「现在」算出策略拦截冷却的到期时刻。
 *
 * 单测靠它锁死时长（不必等真实时间流逝），也与 {@link markPolicyBlockedAccount}
 * 分开，避免为了验证一个数字而去 mock 整个账号池。
 */
export declare function policyBlockResetAtMs(nowMs: number): number;
/** buddy (腾讯 CodeBuddy 系) 模型适配器。使用 Bearer access_token 鉴权。 */
export declare class BuddyAdapter extends LlmAdapter {
    private readonly options;
    /** 本适配器所属的产品配置（默认 CodeBuddy）。 */
    private readonly product;
    private readonly fetchImpl;
    /**
     * 前缀缓存会话标识（prompt_cache_key）。同一会话内所有请求复用同一 key，
     * 服务端据此把相同前缀的 KV 缓存跨请求复用；缺失时缓存命中恒为 0。
     */
    private readonly sessionId;
    /** 动态模型缓存（首次 listModels 成功后填充）。 */
    private remoteModels;
    /** 远端下发的模型元数据（id → 能力），listModels/resolveModel/stream 共用。 */
    private remoteMeta;
    /** 远端下发的模型上下文窗口（/v3/config data.models[].maxInputTokens）。 */
    private remoteContextWindows;
    /**
     * 产品级兜底模型索引（`product.fallbackModels` 的 id → 条目）。
     * 远端缺失时补位；构造时一次性建立，只读。
     */
    private readonly productFallbackIndex;
    /** 产品级兜底上下文窗口（构造时从 fallbackModels 提取）。 */
    private readonly productFallbackContextWindows;
    constructor(options: BuddyAdapterOptions);
    /**
     * 描述本适配器拥有的 provider 路由。
     *
     * DSH 会强制校验 `info.id === provider` 且 `info.name` 为非空字符串；
     * 模型设置页还会用该 id 计算 `deriveKeyRef(provider)`（内部调用
     * `provider.toUpperCase()`）。因此这里对入参做防御性归一化：
     * 一旦 `provider` 不是字符串（例如上游传入了 undefined），
     * 直接回退到本适配器所属产品的 id，避免
     * `undefined.toUpperCase is not a function` 在客户端炸开。
     *
     * 展示名同样来自产品配置：CodeBuddy 为 'CodeBuddy (腾讯)'，
     * WorkBuddy 为 'WorkBuddy'。
     */
    providerInfo(provider: string): LlmProviderInfo;
    /**
     * 模型列表：优先使用 /v3/config 动态拉取的远端列表，否则回退静态默认。
     * 动态拉取失败时静默回退（与 Rust fetch_models 的 Vec::new() 语义一致）。
     */
    /**
     * 懒加载远端模型目录（仅拉取一次）。listModels 与 resolveModel 共用：
     * resolveModel 可能先于 listModels 被调用（如直接进入会话），此时同样
     * 触发一次远端拉取，保证 /v3/config 的 maxInputTokens 能生效。
     */
    /**
     * 按产品的像素预算派生一张图片的**请求版本**。
     *
     * 判据与回退都在共享的 `projectRequestImage` 里（raccoon 用的是同一份 ——
     * 两个适配器各写一遍正是本仓库反复出缺陷的形态）。返回 `undefined`
     * 表示本次发原图，四种正常情形见该函数的注释。
     *
     * ⚠️ 预算取 `product.imagePixelBudget`，未配置时用
     * `DEFAULT_IMAGE_PIXEL_BUDGET`（640,000 px ≈ 1,037 视觉 token）。
     */
    private projectRequestImage;
    private ensureRemoteModels;
    /**
     * 用产品兜底表校正远端结果。
     *
     * 为什么需要校正：服务端按**认证上下文**决定返回哪些模型，插件的 CLI
     * token 拿到的集合可能是残缺甚至错的 —— 实测 WorkBuddy 国际版的 CLI token
     * 只拿到 13 个内部别名（含实际不可用的 `o4-mini`），而 IDE 用的是 20 个
     * （含全部 GPT 系列）。此时若直接采信远端，模型选择器会缺掉用户真正要用的模型。
     *
     * 有产品兜底表时以它为准：
     * - 只保留兜底表里声明的 id（远端多出来的别名/内部模型被丢弃）；
     * - 兜底表声明但远端缺失的模型补进来（用兜底表的元数据）；
     * - ⚠️ **例外：被 agent 引用的模型即使不在兜底表也保留**（见下）。
     *
     * ⚠️ **为什么需要那个例外**：两个端点下发的 id 集合**不同**，而兜底表是
     * 编译期快照、只覆盖其中一套。实测（2026-09-21）`hy4-preview-f`
     * —— 新用户限时免费变体 —— **只由 `/v3/config` 下发**，且被
     * `craft`/`ask`/`plan` 三个 agent 引用（即服务端声明「对话里可选」），
     * 但**不在兜底表**里。白名单重建会把它丢掉，于是用户看不到那个免费变体，
     * 而 IDE 里能看到（用户报障「hy4 preview 现在 ide 是免费我们还是 0.29」）。
     *
     * 判据用 `agentReferenced`（服务端自己的「可选」信号）而非猜测 id 后缀 ——
     * 后缀规则不统一（`-f` / `-x` / `-sg` / `-ioa` 含义各异），猜错会放进
     * 不可用的模型。未标记的内部别名（如 `default`）不会被误留。
     */
    private reconcileWithFallback;
    /**
     * 远端能力字段被实测证伪、需要强制覆盖为「支持图片」的模型。
     *
     * 为什么需要它：上游两个模型端点对同一模型的能力声明会互相矛盾。
     * 实测 `glm-5.1`（2026-09）：
     * - scoped 端点 `/console/enterprises/personal/models` → `supportsImages: false`
     * - `/v3/config` → `supportsImages: true`
     * - 真实请求（纯红图 + 问颜色）→ 答出「红色」，**确实能看到图片**
     *
     * 由于 `fetchModels` 优先采用 scoped 端点，若不覆盖，`glm-5.1` 会被判成
     * 纯文本，用户贴图时直接吃 host 的 `MODEL_DOES_NOT_SUPPORT_IMAGES` 拒绝
     * （前端文案「当前模型不支持图片」），而图片根本到不了上游。
     *
     * 为什么用显式白名单而不是「兜底表 true 优先」这类通用规则：通用规则会让
     * 兜底表永久压过远端，一旦某模型真的下线或能力变更，用户会被放行后被上游
     * 400 拒绝 —— 错误更晚、更难懂。白名单只覆盖已实测确认的个案，新增条目
     * 必须先有真实请求证据。
     */
    private static readonly IMAGE_CAPABILITY_OVERRIDES;
    /**
     * 模型接受的输入模态：远端 supportsImages 优先，静态表兜底；
     * {@link IMAGE_CAPABILITY_OVERRIDES} 中的模型强制为支持图片。
     */
    private inputModalitiesFor;
    /** 模型可选的思考等级：远端 supportedEfforts 优先，产品兜底表次之，通用静态表最后。 */
    private effortsFor;
    /**
     * 模型声明的默认思考等级（远端 `reasoning.defaultEffort` 优先，产品兜底表次之）。
     *
     * 用途：composer 未选档位时补 `reasoning_effort`（deepseek 系不带档位 = 不思考）。
     * 若声明值不在该模型的支持档内（远端数据不一致）则视为未声明，由调用方回退。
     */
    private defaultEffortFor;
    /**
     * 产品级兜底模型目录（`product.fallbackModels`）。
     *
     * 用于远端不可用或远端未覆盖到该模型时。与 `remoteMeta` 分开存放，
     * 使远端一旦可用就自动优先，而产品兜底只在缺失时补位。
     */
    private get productFallbackMeta();
    /**
     * 完整模型目录（**不应用用户黑名单**），含最终展示名（倍率 + 同名消歧）。
     *
     * 设置页（Jet Hub「显示列表」）必须把**被关闭的**模型也渲染出来，否则用户
     * 无法重新打开；而 `listModels` 会按黑名单过滤掉它们，RPC 层只能凭黑名单的
     * key（裸 id）补回 —— 那条路径拿不到展示名，只能退化成裸 id，**倍率随之丢失**
     * （用户报障：「关闭的就没有显示倍率」）。
     *
     * ⚠️ 同名消歧必须基于**未过滤**的全量集合：`displayNameFor(model, source)`
     * 而非 `listed`。用过滤后的集合会让「关掉其中一个同名模型」改变另一个的
     * 变体标记，名字随开关跳变。
     */
    listAllModels(): readonly {
        id: string;
        name: string;
    }[];
    listModels(_provider: string): Promise<readonly LlmModelInfo[]>;
    /**
     * 静态兜底模型目录：优先用产品自带的 `fallbackModels`，否则用通用默认表。
     *
     * 产品兜底表存在的原因：模型池由服务端按认证上下文下发，插件的 CLI
     * token 未必能取到完整集合（实测 WorkBuddy 国际版经 CLI token 只能拿到
     * 13 个别名，拿不到 GPT 系列）。产品兜底表提供该产品权威的完整清单。
     */
    private staticFallbackModels;
    resolveModel(provider: string, model: string, _signal?: AbortSignal): Promise<LlmResolvedModelInfo>;
    /**
     * 兼容 0.1.1-rc.2：新版 LlmRuntime.prepareCall() 会调用
     * `registration.adapter.prepareCall(...)`，而本仓库链接的 dsh-llm 副本
     * （0.1.0-rc.6）的 LlmAdapter 基类尚未提供该方法，缺少时会在每轮请求
     * 开始时抛 `registration.adapter.prepareCall is not a function`。这里把
     * 模型解析与分发绑定到同一个适配器实例（与 CodeArtsAdapter 同款 shim）。
     */
    prepareCall(provider: string, model: string, signal?: AbortSignal): Promise<{
        model: LlmResolvedModelInfo;
        stream: (options: GenerateOptions) => AsyncIterable<StreamChunk>;
    }>;
    stream(options: GenerateOptions): AsyncIterable<StreamChunk>;
    /** 发起一次 chat 请求；网络失败映射为可重试的 TRANSPORT 错误。 */
    private send;
    /**
     * 消费 SSE 响应并产出 StreamChunk。
     *
     * CodeBuddy 返回标准 OpenAI SSE：`delta.content` 为正文、
     * `delta.reasoning_content` 为思考、`delta.tool_calls` 为工具调用。
     * 流式工具调用仅首个分片携带真实 id（chatcmpl-tool-xxx），后续参数分片
     * 只有 index——按 index 缓存 id 保证同一工具的所有分片 id 一致。
     */
    private consumeSse;
}
/**
 * 在 ctx.llm 上注册 CodeBuddy 系产品的 provider 路由与适配器。
 *
 * 路由名、配置页展示名与 settingsNs 全部由产品配置驱动：
 * CodeBuddy 得到 `buddy`，WorkBuddy 得到 `workbuddy`。
 * `settingsNs` 经 `settingsNamespaceFor()` 解析：老契约（≤0.1.6）下是各产品的
 * `llm-<id>` 命名空间；0.1.7-rc.1 起 settings 命名空间只能是 profile 条目 id，
 * 故解析为本插件条目 id。
 */
export declare function registerBuddyLlm(ctx: Context, options: BuddyAdapterOptions): BuddyAdapter;
//# sourceMappingURL=buddy-adapter.d.ts.map