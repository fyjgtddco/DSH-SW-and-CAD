/**
 * Jet Hub 多账号管理的 RPC 端点注册。
 *
 * 使用 DSH 的 connection.fetch.register() 模式注册 HTTP API 端点，
 * 与 dsh-im 的 registerManagementRpc 一致。
 * 通道名 jet-hub → 路径 /api/jet-hub
 * 端点方法：account.list / account.create / account.update / account.delete /
 *           account.reorder / account.refresh / account.retest / account.retestAll /
 *           account.reset / account.resetAll / login.poll /
 *           credits.status / credits.claimAll / credits.balances /
 *           model.list / model.setDisabled /
 *           backup.export / backup.import / backup.status
 */
import type { Context } from '@deepseek-ai/cordis';
import { type CredentialRef } from '@deepseek-ai/dsh-credentials';
import { AccountPool } from './account-pool.js';
import type { CodeArtsAuth } from './service.js';
import type { BuddyAuth } from './buddy-auth.js';
import type { LobsteraiAuth } from './lobsterai-auth.js';
import type { QoderAuth } from './qoder-auth.js';
import type { TraeAuth } from './trae-auth.js';
import type { ClineAuth } from './cline-auth.js';
import type { LoomyAuth } from './loomy-auth.js';
import type { RaccoonAuth } from './raccoon-auth.js';
import type { ZcodeAuth } from './zcode-auth.js';
import type { RaccoonCredential } from './raccoon.js';
import type { BuddyCredential } from './buddy.js';
import { type CheckinStatus, type ClaimOutcome, type CreditBalance } from './credits.js';
import { type BuddyProduct } from './product.js';
import type { ProviderAccountEntry, RpcCreditsStatusResponse, RpcCreditsClaimAllResponse, RpcCreditsClaimSummary, RpcCreditsBalancesResponse } from './types.js';
/** Jet Hub RPC API 路径 */
export declare const JET_HUB_API_PATH = "/api/jet-hub";
/**
 * 构造 Raccoon 账号的**展示名**：`RaccoonAva (1100)`。
 *
 * ## 为什么要追加手机号尾号
 *
 * 服务端的 `name` 是**自动生成的默认名**（实测本机账号为 `RaccoonAva`，
 * 即「Raccoon」+ 随机串）。实证：
 *
 * - `GET /user_info` 的 `data.name = "RaccoonAva"`；
 * - JWT payload 里同样带 `name: "RaccoonAva"`（官方客户端就是读这个：
 *   `M = () => { ... userName: t.name, id: t.sid }`）；
 * - `wechat_bindings` 只有 `[{id, bound_at}]` —— **没有微信昵称/头像**。
 *   微信扫码走 `snsapi_login`（只给 openid），要昵称需额外申请
 *   `snsapi_userinfo`，这里显然没申请。
 *
 * 所以「显示 `RaccoonAva`」本身与官方一致、**不是取错字段**；但它是默认名，
 * 注册第二个账号时服务端很可能又给一个相近的名字 → 多账号重名、无法区分。
 *
 * 修法参考 Loomy（`Loomy 2222`）：这边有真实名字可用，故**保留原名再挂尾号**，
 * 兼顾「看得出服务端原名字」与「多账号可区分」。
 *
 * 退化顺序：昵称 + 手机号尾号 → 昵称 + 用户 id → 昵称 → 账号 id。
 * ⚠️ 手机号取**后 4 位**（够区分且不完整暴露号码）。
 */
export declare function buildRaccoonNickname(credential: Pick<RaccoonCredential, 'nickname' | 'phone' | 'user_id'>, fallbackId: string): string;
/**
 * 支持「锁定永久积分」的 provider 白名单。
 *
 * ⚠️ **必须与前端 `credits-capabilities.js` 的 `supportsPermanentLock` 一致**：
 * 前端拿它决定要不要渲染按钮、要不要发读取请求；后端拿它拒绝越权写入。
 * 两边不一致的后果是「按钮出现但点了报错」或「功能存在却点不出来」。
 *
 * 只有这三家有两个可分的积分池：
 * - Loomy：服务端直接给 `dailyBalance`（当日到期）与 `balance`（永久）；
 * - CodeBuddy / WorkBuddy：要从资源包列表按**扣费截止距今是否满 15 天**现算
 *   （判据见 `buddy-balance-rank.ts`，实测两站形状不同但都成立）。
 * 其余渠道（CodeArts / LobsterAI / Qoder / TRAE / Cline / Raccoon）的积分
 * 模型里没有「会不会作废」这一层区分，登记进来只会多一个无效开关。
 */
export declare const PERMANENT_LOCK_PROVIDERS: ReadonlySet<string>;
/**
 * 汇总一次批量领取的结果。
 * 纯函数，便于单测；inactive（无资格/活动结束）与 failed 分开计数，
 * 因为前者是正常的业务状态、后者才是需要用户关注的问题。
 */
export declare function computeClaimSummary(outcomes: readonly ClaimOutcome[]): RpcCreditsClaimSummary;
/**
 * 积分端点的可注入依赖。
 *
 * 抽出这一层是为了让「逐账号处理」能脱离 `ctx.connection.fetch` 注册流程
 * 单独单测：端点内不做任何业务判断，只负责取账号列表并转交下面的纯函数。
 *
 * **对凭据/产品类型做泛型化**（而非写死 Buddy 系类型）：LobsterAI 的协议
 * 完全不同（无签名、三步签到、身份字段是 keyfrom），但「逐账号顺序执行、
 * 单个失败不中断、凭据解析在 try 之内」这套编排逻辑是**通用**的。
 * 泛型化让 `collect*` 三兄弟只写一遍，两套协议各自注入自己的下钻函数。
 * 默认类型参数保持 Buddy 系，故既有调用点与测试一行都不用改。
 */
export interface CreditsEndpointDeps<TCredential = BuddyCredential, TProduct = BuddyProduct> {
    /**
     * 解析凭据引用。
     * 按设计该接口**不可信**（凭据可能已被外部删除、provider 后端异常），
     * 实现允许抛错，调用方必须把异常算在单个账号头上。
     */
    resolve(ref: CredentialRef): Promise<{
        value: string;
    } | undefined>;
    /** 查询签到状态；默认使用真实的 fetchCheckinStatus。 */
    fetchStatus?: (credential: TCredential, product: TProduct) => Promise<CheckinStatus | null>;
    /** 执行签到领取；默认使用真实的 claimDailyCheckin。 */
    claim?: (credential: TCredential, product: TProduct, entry: ProviderAccountEntry) => Promise<ClaimOutcome>;
    /** 查询积分余额；默认使用真实的 fetchCreditBalance。 */
    fetchBalance?: (credential: TCredential, product: TProduct) => Promise<CreditBalance | null>;
    /**
     * 余额查询的**带原因**版本（优先于 {@link fetchBalance}）。
     *
     * 为什么需要它：`fetchBalance` 只用 `null` 表达「查不到」，调用方统一回
     * 「余额查询失败」。但 CodeArts 还有第三种情形 —— **非积分计费账户**
     * （Token 计费）：它不是故障，如实显示「余额查询失败」会把用户引向错误的
     * 排查方向。该钩子让实现能带回精确文案，同时仍复用本函数的逐账号编排
     * （顺序执行、单账号失败不中断、凭据解析在 try 之内）。
     */
    fetchBalanceDetailed?: (credential: TCredential, product: TProduct) => Promise<{
        balance: CreditBalance | null;
        error?: string;
    }>;
    /** 单账号异常时的告警出口（不参与控制流）。 */
    warn?: (message: string) => void;
    /**
     * 领取前是否先查一次签到状态（默认 `true`）。
     *
     * CodeBuddy 系拆成「查状态 + 领取」两个独立端点，先查可以省掉一次无效的
     * 领取请求（活动未开 / 今天已领时直接短路）。
     *
     * LobsterAI 的领取流程**自身就是多步的**（slot → context → check_in），
     * `claimedToday` / `actions` 判断已在内部完成并会返回对应的
     * `already-claimed` / `inactive`，外部再查一次纯属重复请求 ——
     * 故它传 `false` 跳过预检，直接交给 `claim`。
     */
    precheckStatus?: boolean;
    /**
     * 默认实现（`fetchCheckinStatus` / `claimDailyCheckin` / `fetchCreditBalance`）
     * 使用的 fetch。
     *
     * ⚠️ **必须经此注入，不要在调用点直接 `fetch(...)`**：这些默认实现的真实签名是
     * `(credential, product, fetcher)`，而本模块的历史写法是
     * `deps.claim ?? (claimDailyCheckin as unknown as …)`，把三参函数硬转成
     * 「只传两个参数」的类型 —— 于是调用点写 `claim(credential, product, entry)`
     * 时，`entry` 落进了 `fetcher` 位置，运行时抛
     * **`TypeError: fetcher is not a function`**（真实缺陷：用户一键领取 4 个
     * CodeBuddy 账号全部失败）。
     *
     * 现改为**显式包装**默认实现（见下面的 `resolveClaim` 等），既保留 `entry`
     * 给需要它的 provider（TRAE 用 `entry.id` 取签到设备代次），又把 fetcher
     * 正确送进第三参。未提供时用全局 `fetch`。
     */
    fetcher?: typeof fetch;
}
/**
 * 逐账号收集签到状态（顺序执行，避免并发触发风控）。
 *
 * **包含已停用账号**：停用只影响账号池的自动选择与限流切换，不改变账号本身
 * 是否已签到。用户要看到的是「这个账号今天领了没」，因此这里不过滤 enabled。
 *
 * 关键约束：**凭据解析也在 try 之内**。`credentialRef()` 会对名称做正则校验
 * （非法名称抛 TypeError），`deps.resolve()` 也可能抛错。若把它们留在 try
 * 之外，任一账号的异常都会冒泡到 handleMethod 外层 catch，使整批请求以
 * `jet-hub/handler-failed` 失败——违背「单个账号失败不中断整体」的设计。
 */
export declare function collectCreditsStatus<TCredential = BuddyCredential, TProduct = BuddyProduct>(accounts: readonly ProviderAccountEntry[], product: TProduct, deps: CreditsEndpointDeps<TCredential, TProduct>): Promise<RpcCreditsStatusResponse['accounts']>;
/**
 * 逐账号执行一键领取（顺序执行，单个账号失败不中断整体）。
 *
 * **包含已停用账号**：签到领取与「是否参与账号池自动选择」无关 —— 停用的
 * 账号同样有当日积分可领，用户点「一键领取」时期望所有账号都尝试一遍。
 * 停用只影响限流切换时的候选集合，不影响这里。
 *
 * 与 collectCreditsStatus 同理：凭据解析位于每个账号自己的 try 之内，
 * 异常只让该账号记为 failed。
 */
export declare function collectClaimResults<TCredential = BuddyCredential, TProduct = BuddyProduct>(accounts: readonly ProviderAccountEntry[], product: TProduct, deps: CreditsEndpointDeps<TCredential, TProduct>): Promise<RpcCreditsClaimAllResponse>;
/**
 * 逐账号收集积分余额（顺序执行，避免并发触发风控）。
 *
 * 与 {@link collectCreditsStatus} 的关键差异：**这里保留失败原因**。
 * 余额查不到时用户最需要知道"为什么"（凭据过期？网络不通？），把它降级成
 * 一个 null 会让账号卡片显示成空白或 0 分，反而误导。因此失败时带上 error 文案。
 *
 * **包含已停用账号**：停用只影响账号池的自动选择，与"这个账号还剩多少积分"
 * 无关——用户就是想在同一个列表里看全部账号的余额。
 *
 * 凭据解析同样位于每个账号自己的 try 之内：单个账号的凭据缺失/损坏/名称非法
 * 都不会冒泡中断整批。
 */
export declare function collectCreditBalances<TCredential = BuddyCredential, TProduct = BuddyProduct>(accounts: readonly ProviderAccountEntry[], product: TProduct, deps: CreditsEndpointDeps<TCredential, TProduct>): Promise<RpcCreditsBalancesResponse['accounts']>;
/**
 * 注册 Jet Hub 管理 API 端点。
 *
 * `connection` 服务只存在于 Web bundle；这里用**惰性注入**而非插件级静态
 * `inject`，因此在 headless / CLI profile 下本模块正常加载、只是不注册端点，
 * 而不是把整个插件树卡在 pending（那会让 profile 启动直接失败）。
 */
export declare function registerJetHubRpc(ctx: Context, pool: AccountPool, codearts: CodeArtsAuth, buddy: BuddyAuth, workbuddy: BuddyAuth, lobsterai: LobsteraiAuth, qoder: QoderAuth, 
/** Qoder **中国版**实例（与 `qoder` 同协议、不同 product；RPC 分支按注册表分派）。 */
qoderCn: QoderAuth, trae: TraeAuth, cline: ClineAuth, loomy: LoomyAuth, raccoon: RaccoonAuth, zcode: ZcodeAuth, 
/**
 * provider → 适配器实例（可选）。
 *
 * 用于「显示列表」拿到**不受用户黑名单影响**的全量目录（`listAllModels`），
 * 使被关闭的模型也能显示正确的展示名（含倍率），而不是退化成裸 id。
 * 省略时退化为只用 `ctx.llm.listModels()` 的历史行为。
 */
modelAdapters?: Readonly<Record<string, ModelCatalogSource>>): void;
/**
 * 「显示列表」所需的最小适配器接口：能给出**不套用户黑名单**的完整目录。
 *
 * 只声明用到的方法（结构化类型），避免让本模块依赖五个具体适配器类。
 */
export interface ModelCatalogSource {
    listAllModels(): readonly {
        id: string;
        name: string;
    }[];
}
//# sourceMappingURL=jet-hub-rpc.d.ts.map