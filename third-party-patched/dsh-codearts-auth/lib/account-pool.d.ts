import { Context } from '@deepseek-ai/cordis';
import type { BuddyCredential } from './buddy.js';
import type { BuddyProduct } from './product.js';
import type { JetHubState, ModelDisableMap, PermanentLockMap } from './jet-hub-store.js';
import type { CodeArtsCredential, ProviderAccountEntry, ProviderAccountStatus } from './types.js';
export { JET_HUB_NS } from './jet-hub-store.js';
export type { ModelDisableMap } from './jet-hub-store.js';
declare module '@deepseek-ai/cordis' {
    interface Context {
        accountPool: AccountPool;
    }
}
/**
 * AccountPool —— 多账号管理核心
 *
 * 职责：
 * - 账号列表 CRUD（索引走 {@link JetHubStore}，凭据存于 ctx.credentials，
 *   两者各自独立）
 * - 获取指定 provider + 模型的下一个可用账号
 *   算法：enabled=true 且模型不在重置期内 → 取第一个
 * - 更新模型重置时间（收到限流错误后调用）
 *
 * 持久化后端按 DSH 版本能力探测（见 `src/jet-hub-store.ts`）：0.1.7 起
 * `ctx.settings` 不再允许插件注册 namespace，故改用插件自有状态文档；
 * 两者都不可用时退化为内存态，保证不抛错。
 */
export declare class AccountPool {
    private readonly ctx;
    /** 持久化后端；两个后端都不可用时是仅内存实现。 */
    private readonly store;
    /**
     * 账号列表的**权威进程内副本**。
     *
     * 不直接把后端的读取结果当读源：后端的落盘快照在写入后未必立即反映到
     * 下一次读取，而本类的每次写入都是「读 → 改 → 整体写回」。
     * 若以滞后快照为读源，并发/连续的 updateModelRateLimit 会互相覆盖
     * （典型表现：多个账号触发限流后，落盘文档里一条 modelRateLimits
     * 都没有）。因此首次载入后，这份副本即为唯一读源。
     */
    private cache;
    /**
     * 模型黑名单的**权威进程内副本**（与 {@link cache} 同理：载入一次后即以
     * 本副本为准）。
     */
    private modelCache;
    /**
     * 「锁定永久积分」开关表的**权威进程内副本**（与 {@link cache} 同理）。
     *
     * ⚠️ 每个 provider 一项（Loomy / CodeBuddy / WorkBuddy 各自独立），
     * **只记录已锁定的**（缺键 = 未锁定）。
     *
     * ⚠️ 它的落盘位置与账号池**不是同一份文档**：住在
     * `$DSH_HOME/jet-hub/permanent-locks.json`，因为 state.json 是同机多 profile
     * 共享的文档，而另一条工作区里的旧版本代码全量重写它时**不会携带自己不认识
     * 的键** —— 放那儿会被静默抹掉，解锁的后果是真把永久积分烧掉。
     * 详见 `src/permanent-lock-store.ts` 的文件头。
     */
    private permanentLockCache;
    /** 是否已完成首次载入。 */
    private loaded;
    /** 锁定表的独立后端（权威落盘点）。 */
    private readonly lockStore;
    constructor(ctx: Context);
    /** 首次访问时从后端载入账号列表、黑名单与锁定表。 */
    private ensureLoaded;
    /**
     * 载入锁定表：独立文档是权威；它**不存在**时才从 state.json 的镜像字段迁移。
     *
     * ⚠️ 迁移判据必须是「文档不存在」而不是「表里没有某键」：表里的缺键语义是
     * 「用户明确解锁了」，此时若还回看镜像字段那个陈旧的 `true`，就会出现
     * **解不掉的开关** —— 比丢状态更难排查。
     *
     * ⚠️ 迁移出的内容立即固化到独立文档：否则每次冷启动都要重新读镜像，
     * 而镜像随时可能被另一条工作区的旧代码改回旧值。
     */
    private loadLocks;
    /** 读取账号列表（进程内权威副本）。 */
    private readAccounts;
    /**
     * 持久化账号列表（同时更新进程内权威副本）。
     *
     * **必须连同黑名单一起写回**：两种后端都是整体写入，
     * 只写 `{ accounts }` 会把同一文档里的 `disabledModels` 抹掉。
     */
    private writeAccounts;
    /**
     * 读取某 provider 的模型黑名单（被关闭的模型 id 集合）。
     *
     * 适配器只调用这一个方法，因此进程内副本就是它们的读源：设置页改开关
     * 后，下一次 `listModels` 立即生效，无需重启或重新注册适配器。
     */
    disabledModelsFor(provider: string): ReadonlySet<string>;
    /**
     * 列出某 provider 的模型黑名单，供设置页渲染开关。
     *
     * 返回**全部键**（含显式设为 false 的），以便 UI 区分"从未设置过"与
     * "曾被关闭又打开"——两者对用户都是"开"，但保留记录便于排查。
     */
    listDisabledModels(provider: string): Record<string, boolean>;
    /**
     * 打开/关闭某个模型。
     *
     * 关闭时写入 `true`；打开时**删除该键**而不是写 `false` —— 保持黑名单
     * 里只留真正被关闭的模型，`disabledModelsFor` 的语义因此始终是
     * "键存在且为 true 即隐藏"，配置文件也不会随开关操作无限膨胀。
     */
    setModelDisabled(provider: string, modelId: string, disabled: boolean): Promise<void>;
    /**
     * 批量关闭一批模型（Jet Hub 模型列表的「关闭全部」）。
     *
     * 语义是**按当前列表逐项加入黑名单**，与 {@link setModelDisabled} 的关闭方向
     * 一致，只是**一次落盘**：逐条调用会写 N 次完整文档（30 个模型就是 30 次
     * 整体重写 + 30 次目录广播），且中途失败会留下「关了一半」的黑名单。
     *
     * 空列表直接返回、不落盘：没有变更就不该产生一次无意义的写入与广播。
     * 注意这与 {@link clearDisabledModels} **不对称** —— 后者的语义是「清空」，
     * 即使传入空列表也仍有事可做（详见该方法注释）。
     */
    setModelsDisabled(provider: string, modelIds: readonly string[]): Promise<void>;
    /**
     * 清空某 provider 的全部关闭项（Jet Hub 模型列表的「打开全部」）。
     *
     * ⚠️ **刻意不看模型目录**：直接删掉该 provider 在黑名单里的**全部**键，
     * 而不是按当前目录逐个删。理由是「曾被关闭、后来从服务端目录里下线」的
     * 历史遗留键 —— 按目录删的话它们永远清不掉，黑名单会积累死键，残留键
     * 将来若被同名模型复用还会莫名隐藏它。
     *
     * 该 provider 本就无关闭项时直接返回、不落盘。
     */
    clearDisabledModels(provider: string): Promise<void>;
    /** 持久化模型黑名单（同时更新进程内权威副本）。 */
    private writeModels;
    /**
     * 状态文档里那份**镜像字段**（`loomyPermanentLocked`）。
     *
     * ⚠️ 它只是镜像，权威表在独立文档（见 {@link persistLocks}）。仍继续同源写出
     * 有两个理由：① 同机其它 profile 里的**旧版本代码**只认这个字段（它读它、
     * 也原样写回它），保持一致才能让那一侧的 Loomy 面板显示正确的锁定态；
     * ② 回退到老版本时用户不会看到「锁定悄悄失效」。
     * 二者**取自同一次快照**，于是「同一份状态里两个字段自相矛盾」这种隐性分歧
     * 从结构上就不可能出现。
     */
    private lockFields;
    /**
     * 某 provider 的「锁定永久积分」是否开启。
     *
     * 锁定后选号**只允许消耗会近期作废的积分**，永久积分不参与 ——
     * 只剩永久积分的账号在锁定期间等同于不可用（用户语义：「没有临时积分后
     * 找可用账号就是没有可用账号」）。
     *
     * ⚠️ 「什么算永久积分」各 provider 不同（Loomy 看服务端给的每日池；两个 buddy
     * 看资源包的扣费截止距今是否满 15 天），但**开关本身是同一件事**，故共用本表。
     */
    permanentLocked(provider: string): boolean;
    /**
     * 设置某 provider 的「锁定永久积分」开关（持久化）。
     *
     * ⚠️ 解锁时**删除该键**而不是写 `false`（与模型黑名单同款约定）：表里只留
     * 真正处于锁定态的 provider，`permanentLocked` 的判据因此始终是
     * 「键存在且为 true 即锁定」。
     */
    setPermanentLocked(provider: string, locked: boolean): Promise<void>;
    /**
     * 落盘锁定表：先写**权威**（独立文档），再同步**镜像**（state.json）。
     *
     * ⚠️ 顺序与容错都是有意的：
     * - 权威先落 —— 独立文档才是本 profile 选号的依据；镜像写失败只让另一条
     *   工作区的面板显示旧值，不会让我们**误烧用户的永久积分**；
     * - 镜像失败只 warn，不向上抛 —— 否则一次 settings 后端抖动会让面板上的
     *   「锁定」按钮报错，而实际开关已经生效。
     *
     * 写镜像沿用「读 → 改 → 整体写回」的既有约定：必须连同账号与黑名单一起带，
     * 否则那份文档里的另外两份数据会被抹掉。
     */
    private persistLocks;
    /** 锁定表的当前快照（备份导出用；权威来自独立文档）。 */
    permanentLocksSnapshot(): PermanentLockMap;
    /** 全部处于锁定态的 provider（供设置页一次性读取）。 */
    listPermanentLocked(): string[];
    /** 列出某个 provider 的所有账号（含状态信息） */
    listAccounts(provider: string): Promise<ProviderAccountStatus[]>;
    /** 列出所有 provider 的账号 */
    listAllAccounts(): Promise<ProviderAccountEntry[]>;
    /**
     * 清理「凭据域名与当前产品配置不符」的账号。
     *
     * 用途：WorkBuddy provider 从中国版（copilot.tencent.com）改造为国际版
     * （www.workbuddy.ai）后，旧账号存的仍是中国版凭据 —— 它们的
     * `token.domain` 指向旧端点，用新 endpoint 发请求必然失败（且会一直续期失败）。
     * 这类条目已无修复价值，直接删除，让用户在 Jet Hub 重新登录。
     *
     * 判据是**凭据里记录的 domain 与产品配置的 apiDomain 不一致**（而不是简单按
     * provider 名删），这样只清理真正失配的条目，不会误删已在新端点登录的账号。
     *
     * @returns 被删除的账号 id 列表（供调用方记日志）。
     */
    pruneAccountsWithForeignDomain(product: BuddyProduct): Promise<string[]>;
    /** 添加新账号（登录成功后调用） */
    addAccount(entry: ProviderAccountEntry): Promise<void>;
    /** 更新账号部分字段 */
    updateAccount(id: string, patch: Partial<Pick<ProviderAccountEntry, 'nickname' | 'enabled' | 'expiresAt' | 'refreshable'>>): Promise<void>;
    /** 删除账号（同时清理凭据） */
    removeAccount(id: string): Promise<void>;
    /**
     * 重排某 provider 下账号的顺序（Jet Hub 拖拽排序）。
     *
     * ## 为什么顺序有实际意义
     *
     * 账号列表的数组顺序就是 {@link getAvailableAccount} 的**候选优先级**：
     * 自动选号、限流后的换号重试都按这个顺序取「第一个可用账号」。
     * 因此拖拽不是 UI 装饰，它直接决定实际用哪个账号发请求。
     *
     * ## 只动本 provider 的槽位
     *
     * 账号存在**一个全局数组**里（各 provider 混排，靠 `provider` 字段区分），
     * 而设置页是按 provider 分组渲染的。因此这里取「该 provider 账号原本占用的
     * 那些下标」，把新顺序填回这些下标 —— 其他 provider 的账号**位置不变**。
     *
     * 不这么做（例如把该 provider 的账号整体挪到数组头部）会让拖拽 CodeArts
     * 的顺序顺带改变 Buddy 账号的相对位置，属于跨面板的意外副作用。
     *
     * ## 校验：必须是同一集合的一个排列
     *
     * `orderedIds` 必须恰好包含该 provider 的**全部**账号 id（顺序可变、集合不可变）。
     * 不满足就抛错而不是「尽力而为」：
     * - 少了某个 id（前端列表过期，期间账号被别处新增）→ 若静默忽略，那个账号
     *   会莫名其妙掉到末尾，用户看到的是"顺序自己变了"；
     * - 多了未知 id → 说明前端状态与服务端不一致。
     * 两种情况都让用户刷新重试，比悄悄改数据安全。
     *
     * @param provider - provider id
     * @param orderedIds - 该 provider 全部账号 id 的目标顺序
     */
    reorderAccounts(provider: string, orderedIds: readonly string[]): Promise<void>;
    /**
     * 按凭据内容反查账号 id（供适配器记录"当前用的是哪个账号"）。
     *
     * 适配器不持有 ctx，也不该直接访问本类的私有凭据存储，
     * 因此这里集中做「遍历已启用账号 → 解析凭据 → 比对标识字段」。
     * @param provider - provider 名称（'buddy' | 'workbuddy' | 'codearts'）。
     * @param identity - 比对用的标识值：CodeBuddy 系传 access_token，CodeArts 传 access_key_id。
     * @returns 匹配到的账号 id；无匹配返回空串。
     */
    findAccountIdByCredential(provider: string, identity: string): Promise<string>;
    /** 解析某个 credentialRef 下的凭据 JSON；不可用时返回 undefined。 */
    private resolveCredentialByRef;
    /**
     * 按凭据里的**任意身份字段**查找同 provider 的已有账号。
     *
     * ## 与 {@link findAccountIdByCredential} 的区别
     *
     * 那个是**限流记录归属**专用，写死了「codearts 用 access_key_id、
     * 其余用 access_token」两套字段名，且**只看已启用账号**。
     * 本方法是**通用去重**用：调用方给字段名与值，且**不看 `enabled`** ——
     * 停用的账号同样占着一个条目的位置，重复添加它仍是重复。
     *
     * ## 为什么必须容忍「字段缺失」
     *
     * 早期登录的凭据里可能**没有**该字段（例如 zcode 的 `user_id` 是
     * 2026-10-02 才补上的）。此时**跳过该条目**（视为「无法判断」），
     * 而不是把它当成「不匹配」或直接报错 —— 前者会漏判，
     * 后者会让老用户根本添加不了账号。
     *
     * @param provider - provider id（如 `zcode`）。
     * @param field - 凭据里用作身份判据的字段名（如 `user_id`）。
     * @param identity - 要比对的值（空串直接返回 `''`，调用方据空串放弃去重）。
     * @returns 匹配到的账号 id；无匹配返回空串。
     */
    findAccountIdByIdentityField(provider: string, field: string, identity: string): Promise<string>;
    /** 按 id 查找账号条目（含已停用账号）。 */
    findAccount(id: string): ProviderAccountEntry | undefined;
    /** 列出某 provider 的全部账号（含已停用），供「重测所有 / 重置所有」使用。 */
    listAccountsByProvider(provider: string): ProviderAccountEntry[];
    /**
     * 该 provider 是否**至少有一个已登录（凭据可用）的账号**。
     *
     * 供适配器的 `listModels` 做门控：没有已登录账号时返回空目录，让 DSH 的
     * `buildModelCatalog` 把整个 provider 分组隐藏（它显式
     * `.filter(group => group.models.length > 0)`），从而显著减少模型选择
     * 列表里用不上的条目（用户需求：「没有已登录账号就不显示该供应商的所有
     * 模型」）。
     *
     * ## 为什么判据是「凭据可解析」而不是「有条目」
     *
     * 1. **`logout()` 只清凭据、保留账号条目**（删除条目是另一条路径
     *    `removeAccount`）。若只看「有没有条目」，用户登出后模型仍会显示，
     *    门控形同虚设。
     * 2. **不看 `enabled`**：停用只应影响「自动选号」，与「是否已登录」无关。
     *    这与续期调度器「只按 `refreshable` 过滤、不看 `enabled`」是同一条
     *    既有约定（停用账号同样参与积分领取），故这里保持一致。
     *
     * ⚠️ **这是异步的**：需要逐个解析凭据。但只解析到**第一个可用账号**即返回
     * （短路），多账号场景下通常第一次就命中。
     *
     * ⚠️ **本方法只用于「目录展示」的门控**，绝不能用于路由判定 ——
     * DSH 约定 `listModels` 结果仅供参考，隐藏目录不等于拒绝请求
     * （被隐藏的模型仍可 `resolveModel` / 正常收发）。
     */
    hasLoggedInAccount(provider: string): Promise<boolean>;
    /**
     * 按账号 id 解析凭据（**不检查 enabled**）。
     *
     * 限流重测必须能对已停用账号发请求（用户明确要求"停用的账号也能发送"），
     * 因此这里刻意与 {@link getAvailableAccount} 的过滤条件区分开：自动选择
     * 只认启用账号，而按 id 的显式探测认全部账号。
     * @returns 凭据对象；账号不存在或凭据不可用时返回 undefined。
     */
    resolveCredentialForAccount(id: string): Promise<CodeArtsCredential | BuddyCredential | undefined>;
    /**
     * 清除限流标记。
     *
     * @param accountId - 目标账号。
     * @param modelIds - 要清除的模型；省略时清除该账号的**全部**标记。
     * @returns 实际清除的标记数。
     */
    clearModelRateLimits(accountId: string, modelIds?: readonly string[]): Promise<number>;
    /**
     * 获取指定 provider + 模型的下一个可用账号。
     *
     * `modelId` 为空串时**不做限流过滤**——调用方（provider 的
     * resolveCredential 入口）此时还不知道要发哪个模型，只能退化为
     * "任取一个启用账号"。但 `enabled` 过滤在任何情况下都生效：
     * 停用账号绝不参与自动选择，空 modelId 也不例外。
     *
     * @param provider - provider id（`this.product.id`，不要写死字面量）
     * @param modelId - 目标模型；空串表示不按模型过滤
     * @param excludeAccountIds - 需要跳过的账号 id。
     *
     * **为什么需要 `excludeAccountIds`**：调用方在「请求级轮换」时会逐个换号
     * 重试，必须能拿到**下一个**账号而不是每次都拿回同一个。
     * 本池默认按「重置时间最早到期」排序，当失败类别**不写限流标记**时
     * （如 5xx / 请求错误 —— 它们不是限流，不该留徽章），
     * 刚失败的账号仍是排序第一，调用方若不排除它就会原地打转、
     * 换号形同虚设。Go 侧对应的是 `PickExcluding(tried)`（`pool.go:131`）。
     *
     * 在池这一层排除（而非让调用方自己跳过）是必要的：调用方只能拿到
     * 「池认为最优的一个」，无法枚举候选自己去重。
     */
    getAvailableAccount(provider: string, modelId: string, excludeAccountIds?: ReadonlySet<string>): Promise<{
        entry: ProviderAccountEntry;
        credential: CodeArtsCredential | BuddyCredential;
    } | null>;
    /**
     * 更新某账号某模型的重置时间。
     *
     * 关键：基于**读取到的最新账号列表**做局部合并，再把整个列表写回。
     * settings scope 的 get() 返回的是服务内部快照，可能滞后于磁盘；
     * 但 replace() 是整体替换，因此这里每次都在最新快照上合并，
     * 避免"写 A 的限流 → 读旧快照 → 写 B 的限流"把 A 的记录抹掉。
     */
    updateModelRateLimit(accountId: string, modelId: string, resetAtMs: number): Promise<void>;
    /** 清理已过期的重置时间记录 */
    sweepExpiredRateLimits(): Promise<void>;
    /**
     * 记录 TRAE 签到设备轮换代次（命中 9074 后由积分领取流程调用）。
     *
     * 与 {@link updateModelRateLimit} 同款：在**最新快照**上做局部合并后整体
     * 写回，避免与并发的账号操作互相覆盖。
     *
     * 只接受比现值**更大**的代次，防止乱序/重复回调把代次写回小值而让同一个
     * 被限流的设备号复活。
     */
    updateTraeCheckinDeviceGeneration(accountId: string, generation: number): Promise<void>;
    /** 读取 TRAE 签到设备轮换代次（未设置时为 0）。 */
    traeCheckinDeviceGenerationFor(accountId: string): number;
    /**
     * 读取当前完整状态快照（账号列表 + 模型黑名单 + Loomy 镜像字段）。
     *
     * 供备份导出使用：返回的副本与进程内权威副本解耦，调用方修改返回值
     * 不会污染池的运行时状态。`disabledModels` 是嵌套结构，必须深拷贝
     * （浅拷贝会让内层 provider 表仍共享引用）。
     *
     * ⚠️ 这里**不含**锁定表本体（它住在独立文档里），备份导出要走
     * {@link permanentLocksSnapshot}。
     */
    getStateSnapshot(): JetHubState;
    /**
     * 整体替换账号列表与模型黑名单（备份导入用）。
     *
     * 与 {@link writeAccounts} / {@link writeModels} 的约定一致：整体写入时
     * 必须同时携带账号与黑名单，否则会把另一份数据抹掉。这里一次落盘完成
     * 两件事，避免中间态。
     *
     * ⚠️ 导入数据来自用户提供的备份文件（可能被手工编辑），因此先经
     * `sanitizeAccounts` / `sanitizeDisabledModels` 归一化：只保留可用的
     * 账号条目与显式 `true` 的黑名单项，坏条目直接丢弃而不是写进池里
     * 反复触发选号失败。
     */
    replaceAll(accounts: readonly ProviderAccountEntry[], disabledModels: ModelDisableMap, permanentLocks?: PermanentLockMap): Promise<void>;
}
/**
 * `listModels` 的门控：**该 provider 是否应在模型目录中展示**。
 *
 * ## 需求来源
 *
 * 「如果某供应商没有已登录的账号，就不显示该供应商的所有模型 —— 这样对大多数
 * 用户来说模型选择选项卡臃肿的问题能改善很多。」
 *
 * ## 为什么可行：DSH 原生支持「空目录即隐藏」
 *
 * `dsh-api-session-controller` 的 `buildModelCatalog` 显式做了
 * `.filter(group => group.models.length > 0)`（注释：*"successful non-empty
 * provider groups"*）。因此适配器返回 `[]` 就能让整个 provider 分组从模型
 * 选择器中消失 —— **无需任何前端改动**。
 *
 * ⚠️ **必须返回空数组，不能抛错**：`buildModelCatalog` 的 `catch` 会把抛错
 * 归入 `failures`，界面上会多出一条 provider 报错，比「不显示」更糟。
 *
 * ⚠️ **不影响路由**：`catalog.routableProviders` 由 `listProviders()` 单独
 * 生成（不经过该 filter），且 DSH 明确约定 *"Catalog membership is advisory
 * and never changes routing"* —— 隐藏目录不等于拒绝请求，已持久化的模型
 * 仍能 `resolveModel` / 正常收发。
 *
 * ## 判定语义
 *
 * - **默认开启**（`DSH_HIDE_MODELS_WITHOUT_ACCOUNT=0` 可关）：与
 *   `DSH_TRAE_MAX_MODE` 同为「默认开、显式假值才关」的语义，故单列解析函数。
 * - `accountPool` 缺失（headless / CLI / 单测）或替身未实现
 *   `hasLoggedInAccount` 时**视为可见** —— 门控是**展示优化而非安全边界**，
 *   判定不可用时宁多勿少（否则会让整个 provider 的模型凭空消失）。
 * - **六个 provider 判据完全一致**：都只看账号池。早期 CodeArts 有一个「单凭据
 *   例外」（额外认固定 ref `CODEARTS_ACCESS_TOKEN`），该模式已移除，故
 *   `extraCredentialRefs` 参数也一并删除，避免留下无人使用的分支。
 *
 * @param accountPool - 适配器的账号池（可能为 undefined）。
 * @param provider - provider id。
 */
export declare function providerCatalogVisible(accountPool: AccountPool | undefined, provider: string): Promise<boolean>;
//# sourceMappingURL=account-pool.d.ts.map