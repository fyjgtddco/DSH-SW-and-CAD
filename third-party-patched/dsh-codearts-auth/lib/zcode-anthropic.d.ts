/**
 * ZCode 免费额度通道的 **Anthropic Messages 协议层**。
 *
 * ## 为什么需要单独一层（不能复用 `openai-compat.ts`）
 *
 * ZCode 免费通道**只认 Anthropic Messages 格式**（实测：`zcode-plan` 下
 * 的 `openai` / 裸 `v1/chat/completions` 路径一律 `404 page not found`，只有
 * `/api/v1/zcode-plan/anthropic/v1/messages` 存在——无 captcha 时回 `3007`）。
 *
 * 而 `openai-compat.ts` 的 `serializeMessages()` 产出的是 **OpenAI 形态**
 * （`tool_calls` / `tool_call_id` / `tools[].function`）—— 喂给 Anthropic
 * 端点必 400。故需要一个转换层：
 *
 * ```
 * DSH Message[]  ──serializeMessages──▶  OpenAI 形态
 *                        │
 *                        └──本文件─▶ Anthropic Messages 形态（system 块数组 + tools[].input_schema）
 *
 * Anthropic SSE ──本文件──▶ StreamChunk（block-start / text-delta / tool-call-delta / block-end）
 * ```
 *
 * ## Anthropic Messages 的关键形态差异（都是实测/规范确认的坑）
 *
 * | 维度 | OpenAI | Anthropic |
 * |---|---|---|
 * | system | `messages[0].role='system'` | **顶层 `system` 字段**（块数组） |
 * | 工具声明 | `tools[].function.{name,description,parameters}` | **`tools[].{name,description,input_schema}`**（扁平） |
 * | 工具调用（assistant） | `tool_calls:[{id,function:{name,arguments}}]`（字符串 JSON） | **`content:[{type:'tool_use',id,name,input}]`**（**对象**，非字符串） |
 * | 工具结果 | `{role:'tool',tool_call_id,content}` | **`{role:'user',content:[{type:'tool_result',tool_use_id,content}]}`** |
 * | SSE 结束 | `data: [DONE]` | `message_stop` 事件（**无** `[DONE]`） |
 * | 思考 | `delta.reasoning_content` | **`content_block_delta` 的 `thinking_delta`** |
 *
 * ⚠ 两个最容易踩的：`tool_use.input` 是**对象**（不是 JSON 字符串）；
 * 工具结果必须包成 `role:'user'` 里的 `tool_result` 块（不是独立的 `tool` 角色）。
 */
import type { StreamChunk } from '@deepseek-ai/dsh-llm';
/** Anthropic 内容块（本实现用到的子集）。 */
export type AnthropicBlock = {
    type: 'text';
    text: string;
} | {
    type: 'image';
    source: {
        type: 'base64';
        media_type: string;
        data: string;
    };
} | {
    type: 'tool_use';
    id: string;
    name: string;
    input: unknown;
} | {
    type: 'tool_result';
    tool_use_id: string;
    content: string | Array<Record<string, unknown>>;
    is_error?: boolean;
};
/** Anthropic 消息。 */
export interface AnthropicMessage {
    role: 'user' | 'assistant';
    content: string | AnthropicBlock[];
}
/** Anthropic 工具声明（**扁平**，不是 OpenAI 的嵌套 `function`）。 */
export interface AnthropicTool {
    name: string;
    description?: string;
    input_schema: Record<string, unknown>;
}
/** 把 JSON 字符串安全解析成对象（Anthropic 要对象，不要字符串）。 */
export declare function parseToolArguments(raw: unknown): unknown;
/**
 * 把 OpenAI 形态的消息序列转成 Anthropic Messages 形态。
 *
 * ## 转换规则（逐条对应上表的差异）
 *
 * 1. `role: 'system'` 的消息**抽出来**（Anthropic 用顶层 `system` 字段）——
 *    由 `splitSystemMessages()` 做，本函数只处理 user/assistant/tool。
 * 2. `role: 'tool'` → 包成 `role:'user'` 的 `tool_result` 块。
 *    连续多个 tool 结果会**合并到同一条 user 消息**（Anthropic 允许，
 *    且比发多条 user 消息更贴近官方形态）。
 * 3. assistant 的 `tool_calls` → `content:[{type:'tool_use',…}]`，
 *    `arguments`（字符串）→ `input`（**对象**）。
 * 4. assistant 的文本与 tool_use 可在同一条消息里共存。
 */
export declare function toAnthropicMessages(wire: readonly Record<string, unknown>[]): AnthropicMessage[];
/** 把 OpenAI 形态的工具表转成 Anthropic 的扁平 `input_schema` 形态。 */
export declare function toAnthropicTools(tools: readonly {
    name: string;
    description?: string;
    parameters?: Record<string, unknown>;
}[]): AnthropicTool[];
/** Anthropic SSE 事件的名字（用于快速分派）。 */
export type AnthropicSseEvent = 'message_start' | 'content_block_start' | 'content_block_delta' | 'content_block_stop' | 'message_delta' | 'message_stop' | 'ping' | 'error';
/** 解析一行 `event:` / `data:` 对。 */
export interface SseFrame {
    event?: string;
    data: string;
}
/**
 * 把原始 SSE 文本流切成帧。
 *
 * ⚠ Anthropic 的 SSE 与 OpenAI **同构**（`event:` + `data:` 行、空行分隔），
 * 但**没有 `data: [DONE]`** —— 结束靠 `message_stop` 事件。
 * 故不能复用 `consumeOpenAiSse`（它找 `[DONE]`）。
 */
export declare function iterateSseFrames(body: ReadableStream<Uint8Array>, options?: {
    signal?: AbortSignal;
}): AsyncIterable<SseFrame>;
/** 解析一个 SSE 帧的原始文本。 */
export declare function parseFrame(raw: string): SseFrame | undefined;
/**
 * 把 Anthropic SSE 流转换成 DSH 的 `StreamChunk` 流。
 *
 * ## 事件映射
 *
 * | Anthropic 事件 | 产出 |
 * |---|---|
 * | `content_block_start`（`text`） | `block-start`（blockType `text`） |
 * | `content_block_start`（`thinking`） | `block-start`（blockType `reasoning`） |
 * | `content_block_start`（`tool_use`） | 记录 id/name（等 `input_json_delta`） |
 * | `content_block_delta`（`text_delta`） | `text-delta` |
 * | `content_block_delta`（`thinking_delta`） | `reasoning-delta` |
 * | `content_block_delta`（`input_json_delta`） | `tool-call-delta` |
 * | `content_block_stop` | `block-end` |
 * | `error` | 抛 `LlmError` |
 *
 * ⚠ `error` 事件**必须抛错**（`AGENTS.md` 记过 Qoder 的同型缺陷：
 * 错误被静默当成「正常结束、无内容」，UI 表现为「干净地停止、无任何报错」）。
 */
export declare function consumeAnthropicSse(body: ReadableStream<Uint8Array>, options: {
    label: string;
    model: string;
    signal?: AbortSignal;
}): AsyncIterable<StreamChunk>;
/** 从 Anthropic 非流式响应里取可见文本（探活与日志用）。 */
export declare function extractAnthropicText(payload: unknown): string;
//# sourceMappingURL=zcode-anthropic.d.ts.map