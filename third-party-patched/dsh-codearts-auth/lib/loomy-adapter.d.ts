/**
 * Loomy LLM 适配器。
 *
 * ## 协议
 *
 * 实测（2026-09-26）`POST {apiBase}/chat/completions` 是**标准 OpenAI 兼容
 * + 标准 SSE**：`data:` 帧 + 空 `data:` 终止帧，思考在 `delta.reasoning_content`，
 * **无加密、无信封、无格式转换**。与 qoder 同形，故消息序列化与 SSE 消费
 * 直接复用 `src/openai-compat.ts` —— 那是为这种形态抽的共享层，
 * **新增** provider 用它正是其设计意图（AGENTS.md 只禁止拿它去重构
 * buddy/lobsterai 的既有实现）。
 *
 * ⚠️ **认证头与业务端点不同**：chat 端点**只认** `Authorization: Bearer`，
 * 而 `/models`、`/points/*` 只认 `token`。故 `stream()` 用
 * `loomyChatHeaders()`（两个都发），`listModels()` 用 `loomyBusinessHeaders()`。
 */
import type { Context } from '@deepseek-ai/cordis';
import type { CredentialRef } from '@deepseek-ai/dsh-credentials';
import { LlmAdapter } from '@deepseek-ai/dsh-llm';
import type { GenerateOptions, LlmModelInfo, LlmProviderInfo, LlmResolvedModelInfo, StreamChunk } from '@deepseek-ai/dsh-llm';
import { AccountPool } from './account-pool.js';
import { type LoomyCredential } from './loomy.js';
import { type LoomyProduct } from './loomy-product.js';
/** 本适配器注册的 provider 路由名（等价于 `LOOMY.id`）。 */
export declare const PROVIDER = "loomy";
/** 远端模型条目（已归一）。 */
export interface LoomyRemoteModel {
    id: string;
    /** **已规范化**的展示名（含倍率）。 */
    name: string;
    contextWindow: number;
    supportsImage: boolean;
    supportsThinking: boolean;
    /**
     * 可选思考档位（远端 `reasoning_efforts`，如 `['none','low','medium','high','xhigh']`）。
     *
     * ⚠️ **取自远端而非硬编码** —— 服务端 `GET /models` 直接下发了这两个字段，
     * 且带 `reasoning_catalog_version`（catalog 版本哈希），故档位随服务端更新，
     * 无需改代码。
     *
     * 缺失（`undefined`）表示**该模型不提供档位选择**，此时不声明 `reasoning`，
     * UI 显示「当前模型未提供推理等级」—— 而不是给一个发了也没用的档位。
     */
    efforts?: string[];
    /** 远端声明的默认档位（`default_reasoning_effort`）。 */
    defaultEffort?: string;
}
/**
 * 解析远端 `GET /models` 响应，只保留 `type === 'chat'` 的条目。
 *
 * ⚠️ 过滤判据是 `type`，**不能**看 `input_modalities` —— 实测 5 个 chat
 * 模型的输入模态含 `image`（能看图），那不是生图模型。
 * ⚠️ 展示名经 `loomyDisplayName` 规范化（远端原值是三种括号风格混用）。
 */
export declare function parseLoomyRemoteModels(payload: unknown): LoomyRemoteModel[];
/** {@link LoomyAdapter} 的构造选项。 */
export interface LoomyAdapterOptions {
    /** 单凭据回退 ref（无账号池时）。 */
    credentialRef: CredentialRef;
    /**
     * 解析当前可用凭据。
     *
     * ⚠️ 入参是**本轮要用的模型 id**：Loomy 的选号策略要按「该模型是否受限」
     * 先过滤账号（限流是**按模型**记的），再按余额分档。实现**必须**把它透传给
     * `AccountPool.getAvailableAccount` 的 `modelId` —— 早期实现传空串
     * （等于不按模型过滤），会让模型级限流失效。
     */
    resolveCredential: (modelId?: string) => Promise<LoomyCredential | undefined>;
    /**
     * 凭据失效时的处理。
     *
     * ⚠️ Loomy **没有 refresh 端点**，故实现只做**有效性探测**并在失效时抛错，
     * 不会（也无法）续期。
     */
    refresh: () => Promise<void>;
    /** 拉取远端模型目录；失败时适配器回退兜底表。 */
    fetchRemoteModels?: () => Promise<LoomyRemoteModel[]>;
    /** 读取图片附件的原始字节（内联为 data URL 用）。 */
    readImage?: (attachment: unknown) => Promise<{
        data: Uint8Array;
        mediaType: string;
    } | undefined>;
    /** 账号池（目录门控与黑名单）。 */
    accountPool?: AccountPool;
    /** 产品配置；默认 {@link LOOMY}。 */
    product?: LoomyProduct;
    /** 注入的 fetch（测试用）。 */
    fetchImpl?: typeof fetch;
}
/** Loomy 模型适配器。chat 端点用 Bearer，业务端点用 token。 */
export declare class LoomyAdapter extends LlmAdapter {
    private readonly options;
    private readonly product;
    private readonly fetchImpl;
    /** 兜底模型索引（id → 条目）。 */
    private readonly fallbackIndex;
    /** 远端模型缓存（含展示名与能力）；未拉取时为 undefined。 */
    private remoteModels;
    constructor(options: LoomyAdapterOptions);
    /**
     * 描述本适配器拥有的 provider 路由。
     *
     * 对入参做防御性归一化：DSH 会强制校验 `info.id === provider`，而模型设置页
     * 会用该 id 计算 `deriveKeyRef(provider)`（内部调 `provider.toUpperCase()`）。
     * 一旦 provider 不是字符串（上游传入 undefined），直接回退到本产品的 id，
     * 避免 `undefined.toUpperCase is not a function` 在客户端炸开。
     */
    providerInfo(provider: string): LlmProviderInfo;
    /** 完整目录（**不套黑名单**），带最终展示名。设置页需要它渲染被关闭的模型。 */
    listAllModels(): readonly {
        id: string;
        name: string;
    }[];
    /** 取（并缓存）远端模型目录；失败时回退兜底表。 */
    private loadModels;
    private inputModalitiesFor;
    listModels(_provider: string): Promise<readonly LlmModelInfo[]>;
    resolveModel(provider: string, model: string, _signal?: AbortSignal): Promise<LlmResolvedModelInfo>;
    /**
     * 该模型的思考档位（`resolveModel` 的 `reasoning` 字段）。
     *
     * ⚠️ **此前根本不声明** —— DSH 的思考强度选择器**只会**从
     * `resolveModel().reasoning` 渲染，故两个站点从来没出现过档位选择器，
     * 尽管远端早就下发了 `reasoning_efforts`。这与 Qoder 那次（AGENTS.md 2.2 节）
     * 是**完全同型**的缺陷。
     *
     * 三条口径：
     * 1. `efforts` **原样取远端顺序**（服务端下发的就是展示顺序）；
     * 2. `defaultEffort` **必须落在 `efforts` 内** —— DSH 会拿它直接发请求，
     *    给一个不存在的档位会抛 `UNSUPPORTED_REASONING_EFFORT`，比不给更糟；
     * 3. 远端未下发档位时不声明 `reasoning`（UI 显示「当前模型未提供推理等级」）。
     */
    private reasoningFor;
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
}
/**
 * 在 `ctx.llm` 上注册 Loomy provider 路由与适配器。
 *
 * 返回适配器实例：Jet Hub「显示列表」需要 `listAllModels()`
 * （不受黑名单影响、带最终展示名）。`ctx.llm` 不透传自定义方法，
 * 故须由调用方持有引用并在 `index.ts` 的 `modelAdapters` 里登记。
 */
export declare function registerLoomyLlm(ctx: Context, options: LoomyAdapterOptions): LoomyAdapter;
//# sourceMappingURL=loomy-adapter.d.ts.map