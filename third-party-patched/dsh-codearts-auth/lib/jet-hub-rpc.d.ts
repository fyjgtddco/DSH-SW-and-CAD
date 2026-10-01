/**
 * Jet Hub 多账号管理的 RPC 端点注册。
 *
 * 使用 DSH 的 connection.fetch.register() 模式注册 HTTP API 端点，
 * 与 dsh-im 的 registerManagementRpc 一致。
 * 通道名 jet-hub → 路径 /api/jet-hub
 * 端点方法：account.list / account.create / account.update / account.delete / account.refresh / login.poll
 */
import type { Context } from '@deepseek-ai/cordis';
import { AccountPool } from './account-pool.js';
import type { CodeArtsAuth } from './service.js';
import type { BuddyAuth } from './buddy-auth.js';
/** Jet Hub RPC API 路径 */
export declare const JET_HUB_API_PATH = "/api/jet-hub";
/**
 * 注册 Jet Hub 管理 API 端点。
 * 使用 ctx.connection.fetch.register() 注册 HTTP POST 端点。
 */
export declare function registerJetHubRpc(ctx: Context, pool: AccountPool, codearts: CodeArtsAuth, buddy: BuddyAuth): void;
//# sourceMappingURL=jet-hub-rpc.d.ts.map