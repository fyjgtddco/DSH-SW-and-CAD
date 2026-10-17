import type { Context } from '@deepseek-ai/cordis';
import type { CredentialRef } from '@deepseek-ai/dsh-credentials';
import { LlmAdapter } from '@deepseek-ai/dsh-llm';
import type { GenerateOptions, LlmModelInfo, LlmProviderInfo, LlmResolvedModelInfo, StreamChunk } from '@deepseek-ai/dsh-llm';
import { AccountPool } from './account-pool.js';
import type { CodeArtsCredential } from './types.js';
export declare const CHAT_API_BASE = "https://snap-access.cn-north-4.myhuaweicloud.com/api/v2";
export declare const PROVIDER = "codearts";
export interface CodeArtsAdapterOptions {
    credentialRef: CredentialRef;
    resolveCredential: () => Promise<CodeArtsCredential | undefined>;
    refresh: () => Promise<void>;
    /** 动态拉取远端模型列表；失败时调用方回退到静态列表。 */
    fetchRemoteModels?: () => Promise<Array<{
        id: string;
        name: string;
    }>>;
    fetchImpl?: typeof fetch;
    chatId?: string;
    sessionId?: string;
    /** 多账号池（用于限流时切换账号） */
    accountPool?: AccountPool;
}
/**
 * 将 harness 对话消息序列化为 CodeArts chat-completions 的传输
 * 格式。助手的 `tool-call` 块转换为 `tool_calls` 字段；`reasoning`
 * 块折叠为 `reasoning_content` 字段（deepseek-v4 等推理模型的后端
 * 校验要求 assistant 消息必须携带该字段，缺失会报 "Missing
 * `reasoning_content` field"）；工具结果（搭载在 harness 用户消息中）
 * 展开为独立的 `{role: 'tool'}` 消息，使模型能看到其调用的返回值。
 * 其余非文本块（图片）被丢弃，与端点接受的格式一致。
 *
 * ⚠️ 入口先做 **DSH 0.1.7 消息形状归一化**（见 `message-shape.ts`）：0.1.7 把工具
 * 结果改为一等 `role:'tool'` 消息，若不归一化，下面的 `type === 'tool-result'`
 * 判据恒不命中 → 工具调用被 `resolveToolPairing` 整体剔除。
 */
export declare function serializeMessages(messages: readonly {
    role: string;
    content: unknown;
}[]): Array<Record<string, unknown>>;
/**
 * CodeArts 并发排队端点。当后端按账户的会话
 * 并发上限达到时，chat completions 请求会以
 * `TM.00001041`（"并发会话数已达上限"）失败，调用方需轮询
 * 排队状态端点，直到后端再次允许该会话。
 */
export declare const QUEUE_STATUS_BASE = "https://snap-access.cn-north-4.myhuaweicloud.com/api/v1/queue/status";
/** CodeArts 后端返回的一次排队状态响应。 */
export interface CodeArtsQueueStatus {
    readonly status: 'waiting' | 'working' | 'error' | 'queue_full';
    readonly queuePosition: number;
    readonly message: string;
}
/** 兼容 OpenAI 格式的 CodeArts 模型适配器，使用华为请求签名。 */
export declare class CodeArtsAdapter extends LlmAdapter {
    private readonly options;
    private readonly fetchImpl;
    private readonly chatId;
    private readonly sessionId;
    constructor(options: CodeArtsAdapterOptions);
    /**
     * 描述本适配器拥有的 provider 路由。
     *
     * 与 BuddyAdapter 同款防御：DSH 校验 `info.id === provider`，且模型设置页
     * 会用该 id 计算 `deriveKeyRef(provider)`（内部 `provider.toUpperCase()`）。
     * 入参异常时回退到 PROVIDER 常量，避免客户端抛
     * `undefined.toUpperCase is not a function`。
     */
    providerInfo(provider: string): LlmProviderInfo;
    /** 动态模型缓存（首次 listModels 成功后填充）。 */
    private remoteModels;
    /**
     * 懒加载远端模型目录。resolveModel 可能先于 listModels 被调用
     * （如直接进入会话），此时同样触发远端拉取。
     */
    private ensureRemoteModels;
    /**
     * 完整模型目录（**不应用用户黑名单**）。
     *
     * 设置页必须渲染被关闭的模型（否则用户无法重新打开），而 `listModels` 会按
     * 黑名单过滤掉它们 —— RPC 层只能凭裸 id 补回，展示名随之丢失
     * （用户报障：「关闭的就没有显示倍率」）。CodeArts 目录虽无倍率，但同样
     * 需要正确的 `name`（否则关闭项显示 `deepseek-v4-flash` 这类裸 id）。
     */
    listAllModels(): readonly {
        id: string;
        name: string;
    }[];
    listModels(_provider: string): Promise<readonly LlmModelInfo[]>;
    resolveModel(provider: string, model: string, _signal?: AbortSignal): Promise<LlmResolvedModelInfo>;
    prepareCall(provider: string, model: string, signal?: AbortSignal): Promise<{
        model: LlmResolvedModelInfo;
        stream: (options: GenerateOptions) => AsyncIterable<StreamChunk>;
    }>;
    stream(options: GenerateOptions): AsyncIterable<StreamChunk>;
    /**
     * 消费一个 HTTP 200 的 SSE chat 响应并产出 StreamChunk。
     *
     * CodeArts 后端有时以 HTTP 200 + SSE 内嵌错误事件的形式返回排队/限流
     * （如 `InferHub.ModelArts.81111.429` TPM 超限，事件形如
     * `{"text":"[DONE]","error_code":"...","error_msg":"..."}`），而不是
     * 4xx——这类错误若直接当成流结束会被静默吞掉（表现为"思考后无输出"）。
     * 本方法解析每个 SSE 事件的 `error_code`/`error_msg`：可重试的排队/限流
     * 错误抛 {@link SseQueueRetryError} 让外层重试循环按 TM.00001041 同等
     * 处理（10s 间隔重试整个 chat 请求）；不可重试错误抛普通 LlmError。
     */
    private consumeSse;
    /**
     * 查询某个会话的 CodeArts 并发队列状态。该端点
     * 与 chat API 一样使用 AK/SK 签名；GET 不携带请求体，因此无 content-type。
     * @param credential - 用于签名请求的 AK/SK/SecurityToken。
     * @param model - 模型 id，作为 `model` 查询参数回传。
     * @param signal - 状态请求的取消信号。
     * @returns 解析后的排队状态，或当端点不可达
     *   或返回无法识别的载荷时返回 `undefined`。
     */
    private queryQueueStatus;
}
/** 在 ctx.llm 上注册 codearts 提供商路由和适配器。 */
export declare function registerCodeArtsLlm(ctx: Context, options: CodeArtsAdapterOptions): CodeArtsAdapter;
/** 判断错误文本是否为频率限制错误 */
export declare function isRateLimited(body: string): boolean;
/** 从限流错误中提取重置时间 */
export declare function parseRateLimitError(body: string, currentModel: string): {
    modelId: string;
    resetTimeMs: number;
} | null;
//# sourceMappingURL=llm-adapter.d.ts.map