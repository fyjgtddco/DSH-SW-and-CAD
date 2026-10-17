/**
 * WorkBuddy 每日签到（领取积分）客户端。
 *
 * 端点与格式均来自对 WorkBuddy 5.5.6 的逆向 + 真实请求实测（2026-09-14）：
 *
 *   状态查询  POST /v2/billing/meter/checkin-activity-status   body {}
 *   领取      POST /v2/billing/meter/daily-checkin             body {}
 *
 * 两个关键结论（实测）：
 *
 * 1. **必须用 checkin-activity-status，不能用 checkin-status**。后者返回的
 *    是占位数据（active:false、checkin_dates:null、claim_button_text:""），
 *    会让人误判为"活动未开启"。前者才是权威状态源。
 *
 * 2. **不需要 X-Device-Token（图灵盾）**。静态分析曾认为该头是主要门槛，
 *    但实测三种请求头组合调用状态接口全部 200，且完全不带该头的请求真实
 *    领取成功（code:0, credit:100）并可见状态翻转。因此不引入 native SDK。
 *
 * 幂等：重复领取返回 HTTP 400 + code 10001（"今天已签到，请明天再来"）。
 * 判定以响应体 code 为准 —— 不能只看 HTTP 状态。
 */
import type { BuddyCredential } from './buddy.js';
import type { BuddyProduct } from './product.js';
/** 签到状态查询端点（权威状态源）。 */
export declare const CHECKIN_ACTIVITY_STATUS_PATH = "/v2/billing/meter/checkin-activity-status";
/** 每日签到领取端点。 */
export declare const DAILY_CHECKIN_PATH = "/v2/billing/meter/daily-checkin";
/**
 * 积分余额查询端点。
 *
 * **两个产品通用**（2026-09-15 实测）：CodeBuddy 中国版
 * （copilot.tencent.com）与 WorkBuddy 国际版（www.workbuddy.ai）都实现该端点，
 * 请求头与响应结构完全一致，只有 baseURL 不同（随 `product.endpoint` 切换）。
 *
 * 这与签到能力形成对比 —— **签到**只有中国版有（国际版内核里连
 * `checkin-status` / `daily-checkin` 的字面量都不存在），但**积分余额查询
 * 两边都有**。两者是彼此独立的能力，不要因为"国际版没有签到"就推断
 * 它也查不到余额。
 *
 * 该端点不在 CLI 内核里（内核只硬编码了 `get-dosage-notify` 用量通知），
 * 是 IDE 前端直接调用的，故静态搜索内核找不到，只能用真实凭据实测发现。
 */
export declare const USER_RESOURCE_PATH = "/v2/billing/meter/get-user-resource";
/** 签到活动状态（字段名已转为 camelCase）。 */
export interface CheckinStatus {
    /** 活动是否进行中。false 时不应尝试领取 */
    active: boolean;
    /** 今日是否已签到 —— 领取判定的权威依据 */
    todayCheckedIn: boolean;
    /** 连续签到天数 */
    streakDays: number;
    /** 每日可领积分 */
    dailyCredit: number;
    /** 今日已领积分 */
    todayCredit: number;
    /** 今日是否为连续奖励日 */
    isStreakDay: boolean;
    /** 累计已领积分 */
    totalCredits: number;
    /** 已签到日期列表（如 ["2026-09-14"]） */
    checkinDates: string[];
    /** 活动名（如「开学季」） */
    activityName: string;
    /** 主题名（如「Buddy加油站」） */
    themeName: string;
    /** 活动结束时间 */
    endTime: string;
    /**
     * 该账号**需要用户先去别处操作**才能参与签到（如未开通活动）。
     *
     * 与 {@link ClaimOutcome} 的同名字段同义 —— 见那里的详细说明。
     */
    actionRequired?: boolean;
}
/**
 * 一次领取的结果。
 *
 * ## `actionRequired`：把「需要用户操作」变成**显式语义**，而不是藏在文案里
 *
 * **真实缺陷（用户报障，2026-09-26）**：用本插件经 GitHub 授权**新注册**的
 * Qoder 账号尚未在 Qoder 侧开通每日领取，一键签到只说「当前没有可领取的活动」，
 * 用户不知道该怎么办。后端其实已给出可操作文案，但汇总行只显示计数
 * 「N 个活动未开启」，**用户看不到那条提示**。
 *
 * 修法不仅是在 UI 里多显示一句 message —— 那会让「这条 inactive 需要用户操作」
 * 这一语义**只存在于中文文案里**，前端（纯 JS，无类型检查）只能靠
 * 「有没有 message」去猜。故补一个**显式可选字段**：
 *
 * - 置 `true` 时，调用方应在结果里**单独、醒目地**展示该 message
 *   （它是给用户的行动指引，混在计数行里会被读漏）；
 * - 不置或 `false` 时按普通 `inactive` 处理（如「今天活动暂未开始」）；
 * - **可选**字段，故另外五个 provider（与所有既有调用方）无需改动，
 *   与 `src/trae-credits.ts` 用 `ClaimOutcome & { … }` 追加字段的先例同型。
 *
 * ⚠️ 与 `checkin-all.js`/`jet-hub.js` 的约定：前端判 `actionRequired === true`
 * **而不是**判 message 是否非空、更不是判文案内容 —— 后者会随措辞变化而失效。
 */
export type ClaimOutcome = {
    kind: 'claimed';
    credit: number;
    streakDays: number;
    isStreakDay: boolean;
    delayedMessage?: string;
} | {
    kind: 'already-claimed';
    message: string;
} | {
    kind: 'inactive';
    message: string;
    actionRequired?: boolean;
} | {
    kind: 'failed';
    code: number;
    message: string;
};
/**
 * 积分资源包（`get-user-resource` 响应里 `Accounts[]` 的一项）。
 *
 * ## 一个账号为什么有多个包
 *
 * 每个包是**一份独立的积分授予**（套餐 + 若干运营活动赠包），各自有独立的
 * 计量周期与到期时间。实测某 CodeBuddy 账号有 5 个包：1 个体验版套餐 +
 * 4 份「国内运营裂变包」，其中 2 份已过期、1 份本周期已耗尽、2 份可用。
 * 所以界面上「5 个资源包」不等于 5 份额度，需要区分有效与失效。
 *
 * ## 两个 "Remain" 字段的口径差异（关键）
 *
 * 响应里同时有两个剩余值，**含义完全不同**：
 *
 * | 字段                     | 含义                     | 实测（体验版包） |
 * |--------------------------|--------------------------|------------------|
 * | `CapacityRemain`         | 该包的**终身**剩余       | 500              |
 * | `CycleCapacityRemain`    | 该包**本计费周期**剩余   | 0                |
 *
 * IDE 顶部的 "Credits Balance" 用的是**周期口径**（`CycleCapacityRemain`）：
 * 实测该账号终身口径求和为 655.67，而 IDE 显示 155.67 —— 差额 500 正是那个
 * 「终身还剩 500、但本周期已一分不剩」的体验版包。用错字段会让数字凭空多出
 * 一大截，且用户无从核对。
 */
export interface CreditPackage {
    /** 包名（如 'Bonus Pack' / 'CodeBuddy个人体验版'） */
    name: string;
    /** 额度单位（'credit' / 'credits'） */
    unit: string;
    /** 本计费周期剩余额度（IDE 展示口径，精确值含小数） */
    remaining: number;
    /** 本计费周期总额度（精确值） */
    total: number;
    /** 本计费周期已用额度（精确值） */
    used: number;
    /**
     * 该包是否仍然有效。
     *
     * 判定：服务端 `Status !== 3`（实测 3 = 已过期）且未过 `ExpiredTime`。
     * 失效包仍会出现在 `Accounts[]` 里（额度可能非 0），UI 需要能区分出来，
     * 否则用户会以为那些额度还能用。
     */
    active: boolean;
    /** 计量周期开始时间（服务端本地时间字符串，可能为空） */
    cycleStartTime: string;
    /** 计量周期结束时间（服务端本地时间字符串，可能为空） */
    cycleEndTime: string;
    /** 该包自身的失效时间（可能为空 = 无固定失效时间） */
    expiredTime: string;
    /**
     * 该包的**扣费截止时间**（毫秒时间戳；实测缺失或 0 = 服务端未下发）。
     *
     * ⚠️ **这才是「这批积分什么时候作废」的字段**，与 `expiredTime` 不是一回事：
     * 实测（2026-09-29，两站各取真实账号）有效包的 `ExpiredTime` **一律为空串**，
     * 它只在包**真正失效之后**由服务端回填（此时 `Status` 已变 3、余额已归零）；
     * 而 `DeductionEndTime` 在包还有效时就有值，且两站的实测分布差距极大：
     *
     * | 包 | `DeductionEndTime` 距今 | `CycleEndTime` 距今 |
     * |---|---|---|
     * | WorkBuddy「Bonus Pack」 | 9 天 | 9 天 |
     * | WorkBuddy「Free Plan Subscription」 | **3008 天** | 2 天 |
     * | CodeBuddy「拉新权益包 / 国内运营裂变包」 | 17～208 天 | 同左或更长 |
     * | CodeBuddy「个人体验版」 | 3008 天 | 已到期 |
     *
     * ⇒ 判「会不会近期作废」**只能用本字段**：套餐（订阅）包的周期虽短，
     * 扣费截止却在 8 年后，取 `CycleEndTime` 会把每月刷新的套餐误判成快作废。
     *
     * ⚠️ 可选字段：其余 provider 的余额解析器（loomy / qoder / trae / …）不产出它，
     * 依赖方必须把 `undefined` 当作「到期时间未知」而不是「已过期」。
     */
    deductionEndTime?: number;
}
/** 账号的积分余额汇总。 */
export interface CreditBalance {
    /**
     * 当前可用总余额（各**有效**包的本周期剩余之和）。
     *
     * 这个口径与 IDE 顶部的 "Credits Balance" 一致，用户可直接核对。
     *
     * 刻意不用服务端的 `TotalDosage`：它是**终身口径**且取整（实测同一响应里
     * TotalDosage=655 而 IDE 显示 155.67），既口径不对又有截断误差。
     */
    total: number;
    /** 各资源包明细（含已失效的，由 `active` 区分） */
    packages: CreditPackage[];
    /**
     * 已失效包里的剩余额度合计。
     *
     * 单独给出而不是并进 `total`：这些额度服务端仍会返回，但实际不可用于扣费。
     * UI 可以据此提示「另有 N 已失效」，既不误导也不丢信息。
     */
    expiredTotal: number;
}
/**
 * 查询签到活动状态。
 * 使用 checkin-activity-status（权威源，非 checkin-status）。
 * 网络失败、响应非法或业务码非 0 时返回 null。
 */
export declare function fetchCheckinStatus(credential: BuddyCredential, product: BuddyProduct, fetcher?: typeof fetch): Promise<CheckinStatus | null>;
/**
 * 执行每日签到领取。
 *
 * 判定顺序：先看业务码是否属于「已领取 / 无资格 / 活动结束」这些非致命类别，
 * 再看是否成功，最后归为 failed。判定以响应体 code 为准（重复领取是 HTTP 400，
 * 只看状态码会把幂等情况误报为失败）。
 */
export declare function claimDailyCheckin(credential: BuddyCredential, product: BuddyProduct, fetcher?: typeof fetch): Promise<ClaimOutcome>;
/**
 * 查询账号的积分余额（剩余 credits）。
 *
 * 两个产品通用（见 {@link USER_RESOURCE_PATH} 的说明）。网络失败、响应非法或
 * 业务码非 0 时返回 null —— 与 {@link fetchCheckinStatus} 同款语义，让调用方
 * 能把「查不到」与「余额为 0」区分开，不要把网络故障显示成 0 积分。
 *
 * 注意 `data` 是**双层嵌套**：`data.Response.Data.Accounts[]`。这与签到端点的
 * 单层 `data` 结构不同，是本接口最容易解析错的地方。
 */
export declare function fetchCreditBalance(credential: BuddyCredential, product: BuddyProduct, fetcher?: typeof fetch): Promise<CreditBalance | null>;
/**
 * 把额度规整为两位小数。
 *
 * 用 `Math.round(v * 100) / 100` 而不是 `toFixed` 后 parse：后者对
 * 负数与极大值的行为不一致，且返回字符串会污染数值类型。这里只处理
 * 服务端下发的正数额度，乘法取整足够且结果仍是 number。
 *
 * 导出供其它 provider 复用（`qoder-credits.ts`）：多包相加的浮点尾数噪声
 * 是所有 provider 的共同问题，各写一份必然分叉。
 */
export declare function roundCredits(value: number): number;
//# sourceMappingURL=credits.d.ts.map