import { credentialRef } from '@deepseek-ai/dsh-credentials';
import Schema from '@deepseek-ai/schemastery';
import { registerCodeArtsLlm } from './llm-adapter.js';
import { registerBuddyLlm } from './buddy-adapter.js';
import { registerLobsteraiLlm } from './lobsterai-adapter.js';
import { registerQoderLlm } from './qoder-adapter.js';
import { registerTraeLlm } from './trae-adapter.js';
import { registerClineLlm } from './cline-adapter.js';
import { registerLoomyLlm, parseLoomyRemoteModels } from './loomy-adapter.js';
import { registerRaccoonLlm } from './raccoon-adapter.js';
import { CODEARTS_CREDENTIAL_REF, CodeArtsAuth } from './service.js';
import { BUDDY_CREDENTIAL_REF, BuddyAuth, createPoolRefresh } from './buddy-auth.js';
import { LobsteraiAuth } from './lobsterai-auth.js';
import { QoderAuth } from './qoder-auth.js';
import { TraeAuth } from './trae-auth.js';
import { ClineAuth } from './cline-auth.js';
import { LoomyAuth } from './loomy-auth.js';
import { RaccoonAuth } from './raccoon-auth.js';
import { LOOMY } from './loomy-product.js';
import { LoomyBalanceSelector } from './loomy-balance-selector.js';
import { RACCOON } from './raccoon-product.js';
import { ZcodeAuth } from './zcode-auth.js';
import { registerZcodeLlm } from './zcode-adapter.js';
import { ZCODE } from './zcode-product.js';
import { ZCODE_CAPTCHA_FALLBACK } from './zcode-captcha.js';
import { AccountPool } from './account-pool.js';
import { hasLegacyNamespaceRegistration, settingsOf, suppressAutoSettingsPage } from './settings-compat.js';
import { buildRaccoonNickname, registerJetHubRpc } from './jet-hub-rpc.js';
import { CODEBUDDY, WORKBUDDY } from './product.js';
import { buddyExpiringWindowDays } from './buddy-balance-rank.js';
import { BuddyBalanceSelector, pickBuddyAccount } from './buddy-balance-selector.js';
import { LOBSTERAI } from './lobsterai-product.js';
import { QODER, QODER_CN } from './qoder-product.js';
import { TRAE } from './trae-product.js';
import { CLINE } from './cline-product.js';
export const name = 'codearts-auth';
// `connection` 刻意不列入静态 inject：它只由 Web bundle（dsh-client-connection）
// 提供，headless/CLI profile 里并不存在。静态 inject 会让本插件在那些 profile
// 里永久 pending，进而让整个 profile 以
// "plugin tree failed to load: 1 entry did not activate" 启动失败
// —— chicheng-cron 的 skill/agent 任务正是通过 `dsh --profile headless` 运行的，
// 会因此全部 exit 1。Jet Hub 的 RPC 端点在 Web 下通过 apply 内的可选注入挂载，
// 其余 profile 只是不注册该端点。
export const inject = ['credentials', 'commands', 'llm'];
/**
 * 插件 Config schema。
 *
 * ⚠️ **DSH 0.1.7-rc.1 起，settings 表单的命名空间就是 profile 条目 id**
 * （本插件的条目 id 是 `codearts-auth`），且只投影本条目 Config 中标记了
 * `.volatile()` 的字段。因此这里保留一个 `providers` 映射：
 * - 它是六个 provider 各自 `registerConfigurableProviders({ settingsNs })` 的
 *   落地位置（0.1.7 下 `settingsNs` = 本条目 id），模型设置页据此把 provider
 *   判定为「已配置」（判据见 `dsh-client-ui-settings-models` 的 `configured`）；
 * - 本插件的凭据与账号管理**不**走这里（那是 Jet Hub 的账号池 +
 *   `ctx.credentials`），故该字段只承接一个宽松映射，不参与业务读取。
 *
 * 必须是 schemastery schema：`SettingsForms.describe()` 会对每个注册项调用
 * `schema.toJSON()`，传入裸函数（`(value) => ...`）会让它抛
 * `TypeError: ... .toJSON is not a function`，进而使所有依赖 settings 的界面
 * （模型设置页、sidebar 的 settings.get/shell.get）全部失败。
 */
export const Config = Schema.object({
    providers: Schema.dict(Schema.any()).default({}).volatile(),
});
/**
 * 注册 provider 配置 namespace（**仅老契约需要**）。
 *
 * - **≤0.1.6**：`ctx.settings` 允许插件注册任意 namespace，六个 provider 各占
 *   一个（`llm-buddy` / `llm-workbuddy` / ...）。注册缺失会让模型设置页在
 *   `refFor → deriveKeyRef(provider)` 处以
 *   `provider.toUpperCase is not a function` 崩溃，故注册后回读 `describe()` 自检。
 * - **0.1.7-rc.1**：settings 换成 `SettingsForms`，**没有 `register`**，命名
 *   空间只能是 profile 条目 id —— 此时不再（也无法）注册；各 provider 的
 *   `settingsNs` 由 `settingsNamespaceFor()` 指向本插件条目 id，模型设置页照常
 *   工作。这里刻意**静默跳过**：旧实现在这条分支上会打一条误导性的
 *   「settings 服务不可用」告警（启动日志实证）。
 */
function registerProviderSettings(ctx, ...namespaces) {
    const settings = settingsOf(ctx);
    if (!hasLegacyNamespaceRegistration(settings) || settings?.register === undefined)
        return;
    for (const ns of namespaces) {
        try {
            settings.register(ns, Config);
        }
        catch (error) {
            ctx.logger.warn(`[codearts-auth] settings namespace "${ns}" 注册失败: ${String(error)}`);
        }
    }
    // 回读确认：模型设置页要求 settingsNs 真实存在于 describe() 中。
    // 注意：describe() 会遍历所有已注册 namespace 并调用各自 schema 的
    // toJSON()/redactSecrets()，任一注册项的 schema 不合规都会让整条调用抛错。
    // 因此这里必须把异常打出来，而不是静默吞掉。
    try {
        const descriptors = settings.describe?.({ redactSecrets: true }) ?? [];
        const registered = descriptors.map(v => v.ns);
        const missing = namespaces.filter(ns => !registered.includes(ns));
        if (missing.length > 0) {
            ctx.logger.warn(`[codearts-auth] provider namespace 未生效: ${missing.join(', ')}`);
        }
        ctx.logger.info(`[codearts-auth] settings.describe ok, namespaces: ${registered.join(', ')}`);
    }
    catch (error) {
        ctx.logger.error(`[codearts-auth] settings.describe 失败（将导致模型设置页/sidebar settings API 不可用）: `
            + `${error instanceof Error ? error.stack ?? error.message : String(error)}`);
    }
}
/**
 * 图片附件桥接：把持久化图片读成原始字节供适配器内联。
 *
 * 用 `ctx.get` 而非 `inject` —— 附件服务缺失时 provider 仍可正常加载，

 * 只是收到图片时报 UNSUPPORTED_CONTENT。三个 provider 共用本实现：
 * 两个 CodeBuddy 系产品（CodeBuddy / WorkBuddy）共用同一后端与协议；
 * LobsterAI 的图片形态同为 OpenAI 兼容的 `image_url` data URL
 * （2026-09-17 实测服务端接受并正确识别内容）。

 */
export function makeReadImage(ctx) {
    return async (attachment) => {
        const attachments = ctx.get('attachments');
        if (attachments?.readImage === undefined) {
            throw new Error('codearts-auth: 附件服务（attachments）不可用，无法把图片内联进请求；'
                + '请确认当前 profile 已装载 @deepseek-ai/dsh-attachment-local。');
        }
        const stored = await attachments.readImage(attachment);
        return { data: stored.data, mediaType: stored.ref.mediaType };
    };
}
/**
 * 图片「请求版本」桥接（issue !IKITT9）。
 *
 * 走 `ctx.attachments.readImageRequest(ref, target)`：由附件服务按目标尺寸与
 * 字节目标产出**确定性、可缓存**的缩放版本（alpha 走 WebP、不透明走 JPEG、
 * 85/75/60 质量阶梯），适配器只负责选目标。
 *
 * ⚠️ **任何不可用都返回 `undefined`，绝不抛错**，调用方据此回退原图。三种
 * 真实成因都必须容忍，否则「加上缩放」本身会变成新的故障源：
 *
 * 1. 宿主 profile 没装附件服务，或该版本没有 `readImageRequest`（老契约）；
 * 2. 附件后端明确拒绝投影（`ATTACHMENT_PROJECTION_UNSUPPORTED`）；
 * 3. 派生过程中的其它错误（缓存不可写、字节校验失败…）。
 *
 * 回退方向是刻意选 conservative 的一侧：宁可发一张大图（顶多触发网关的
 * 图片 token 上限），也不能因为「想缩图」而把一次本来能成功的请求打死。
 */
export function makeReadImageRequest(ctx) {
    return async (attachment, target) => {
        const attachments = ctx.get('attachments');
        if (attachments?.readImageRequest === undefined)
            return undefined;
        try {
            const projected = await attachments.readImageRequest(attachment, target);
            const mediaType = projected.mediaType ?? projected.attachment?.mediaType;
            if (mediaType === undefined)
                return undefined;
            return { data: projected.data, mediaType };
        }
        catch (error) {
            // 只记日志、随后回退原图：缩放是优化，不是请求的前置条件。
            ctx.logger?.warn?.('[jet-hub] 图片请求版本派生失败，回退原图：'
                + `${error instanceof Error ? error.message : String(error)}`);
            return undefined;
        }
    };
}
/** 注册 codeartsAuth 服务与 codearts LLM 路由（不注册斜杠命令）。 */
export function apply(ctx) {
    // 本插件自带 Jet Hub 设置页，关闭 0.1.7 起由 Config schema 反渲染的自动表单
    // （老契约没有 configure()，静默跳过）。
    suppressAutoSettingsPage(ctx);
    // provider 配置命名空间的注册**只在老契约（≤0.1.6）下需要**：
    // 那时 `settings.register` 可用，六个 namespace 分别对应 codearts 路由、
    // CodeBuddy（buddy）、WorkBuddy（workbuddy）、LobsterAI（lobsterai）、
    // Qoder（qoder）、TRAE（trae）—— 后五者由 registerBuddyLlm /
    // registerLobsteraiLlm / registerQoderLlm / registerTraeLlm 以
    // `llm-${product.id}` 派生。
    //
    // 0.1.7-rc.1 起 settings 换成 SettingsForms（无 register），命名空间只能是
    // profile 条目 id，故这里不做任何注册；各 provider 的 settingsNs 由
    // `settingsNamespaceFor()` 解析为本插件条目 id。
    registerProviderSettings(ctx, 'llm-buddy', 'llm-workbuddy', 'llm-codearts', 'llm-lobsterai', 'llm-qoder', 'llm-qodercn', 'llm-trae', 'llm-cline', 'llm-loomy', 'llm-raccoon', 'llm-zcode');
    const service = new CodeArtsAuth(ctx);
    const pool = new AccountPool(ctx);
    // WorkBuddy provider 已从中国版（copilot.tencent.com）改造为国际版
    // （www.workbuddy.ai）。旧账号存的是中国版凭据，其 token.domain 指向旧端点，
    // 用新 endpoint 发请求必然失败且会一直续期失败，故启动时清理掉。
    // 判据是「凭据 domain ≠ 产品 apiDomain」，只清真正失配的条目。
    void pool.pruneAccountsWithForeignDomain(WORKBUDDY).then((removed) => {
        if (removed.length > 0) {
            ctx.logger.info(`[jet-hub] 已清理 ${removed.length} 个 WorkBuddy 旧版（中国版）账号，请重新登录：${removed.join(', ')}`);
        }
    }).catch((error) => {
        ctx.logger.warn(`[jet-hub] 清理 WorkBuddy 旧版账号失败：${String(error)}`);
    });
    // ⚠️ **CodeArts 不注册任何斜杠命令**（`codearts-login` / `codearts-status` /
    // `codearts-refresh` 三个已删除）：登录、状态与续期统一在 Jet Hub 设置页完成，
    // 与 buddy / workbuddy / lobsterai / qoder / trae 的既有做法一致。
    const codearts = registerCodeArtsLlm(ctx, {
        credentialRef: credentialRef(CODEARTS_CREDENTIAL_REF),
        resolveCredential: async () => {
            // CodeArts **只认账号池**，与其余五个 provider 一致。
            //
            // ⚠️ 早期它额外支持「单凭据模式」（`CODEARTS_ACCESS_TOKEN`）：登录后把凭据
            // 写到那个固定 ref，适配器在账号池取不到时回退去读它。该模式**已移除** ——
            // 登录入口只有 Jet Hub 设置页，凭据一律写入 `CODEARTS_ACCOUNT_XXX`，
            // 固定的 `CODEARTS_ACCESS_TOKEN` 不会再被写入或读取。
            //
            // 这里仍保留 `credentialRef` 选项，仅为满足适配器契约与报错文案
            // （其余 provider 同样传各自的默认 ref，但都不再作为回退来源）。
            const available = await pool.getAvailableAccount('codearts', '');
            return available?.credential;
        },
        // 续期按**账号池里的具体账号**走：`refreshAccountCredential` 读写的是
        // `CODEARTS_ACCOUNT_XXX`，而旧的 `service.refresh()` 读写的是已废弃的
        // 单凭据 ref —— 那会刷到另一个（不存在的）凭据上。
        refresh: async () => {
            const available = await pool.getAvailableAccount('codearts', '');
            if (available)
                await service.refreshAccountCredential(available.entry.credentialRef, pool, available.entry.id);
        },
        fetchRemoteModels: () => service.refreshModels(pool),
        accountPool: pool,
    });
    // ===== Buddy (腾讯 CodeBuddy) 服务 =====
    // 不注册斜杠命令：登录/状态/续期都在 Jet Hub 设置页完成（多账号 + 账号池），
    // 命令式的单凭据入口已无必要。
    /**
     * 按凭据 ref 解析 buddy 系凭据（CodeBuddy 与 WorkBuddy **共用这一个函数**）。
     *
     * 两站凭据同构（同一 CLI 内核、同一认证协议），差别只在 endpoint；
     * 解析这件事与 endpoint 无关，故不写两份。
     */
    const resolveBuddyCredentialByRef = async (refName) => {
        const resolved = await ctx.credentials.resolve(credentialRef(refName));
        if (!resolved)
            return undefined;
        try {
            return JSON.parse(resolved.value);
        }
        catch {
            return undefined;
        }
    };
    /**
     * 两个 buddy 各自的**按到期分档选号器**（各自一份余额缓存，绝不串味）。
     *
     * ⚠️ **为什么需要它**：一个 buddy 账号常同时持有多种资源包（实测 WorkBuddy
     * 每个号都有「Bonus Pack（14 天到期）」+「Free Plan Subscription（扣费截止
     * 8 年后）」），而**服务端扣哪个包不由插件决定**。插件能决定的只有「用哪个号」：
     * 优先用还有「15 天内到期积分」的号（那部分再不用就作废），只剩长期积分的号
     * 排最后 —— 与「锁定永久积分」配套（锁定时后者直接判不可用）。
     *
     * 判据出处见 `buddy-balance-rank.ts`（为什么是 15 天、为什么用 DeductionEndTime
     * 而不是 CycleEndTime / ExpiredTime，都有实测对照）。
     */
    const buddyBalanceSelector = new BuddyBalanceSelector({
        product: CODEBUDDY,
        resolveCredential: resolveBuddyCredentialByRef,
    });
    const workbuddyBalanceSelector = new BuddyBalanceSelector({
        product: WORKBUDDY,
        resolveCredential: resolveBuddyCredentialByRef,
    });
    /**
     * buddy 系的选号编排：按「enabled + 模型未受限」筛候选 → 按余额分档选号 → 取凭据。
     *
     * ⚠️ 余额分档**只在这批候选内部进行** —— 即与 Loomy 同一约定：策略建立在
     * 「模型没有受限且账号没有被停用」的基础上，不能因为某个号积分多就绕开限流标记。
     *
     * @returns 选中账号的凭据；`credential` 为空时 `tried` 是「档位最优但凭据坏了」
     * 的账号集合，调用方要把它作为排除集合传给 `getAvailableAccount` 继续兜底。
     *   锁定且无可用账号时**抛错**（绝不回落，否则锁定形同虚设）。
     */
    const pickBuddyCredential = async (options) => {
        const tried = new Set();
        const candidates = pool
            .listAccountsByProvider(options.product.id)
            .filter(a => a.enabled)
            .filter((a) => {
            // 与 `getAvailableAccount` 的限流判据保持一致（空 modelId = 不过滤）。
            const key = options.modelId ?? '';
            if (key.length === 0)
                return true;
            if (!a.modelRateLimits)
                return true;
            const resetAt = a.modelRateLimits[key];
            return resetAt === undefined || resetAt === 0 || Date.now() >= resetAt;
        })
            .map(a => ({ id: a.id, credentialRef: a.credentialRef }));
        const allowPermanent = !pool.permanentLocked(options.product.id);
        const picked = await pickBuddyAccount(options.selector, candidates, {
            allowPermanent,
            resolveCredential: resolveBuddyCredentialByRef,
        });
        if (picked.kind === 'account') {
            return { credential: picked.credential, tried: new Set(picked.tried) };
        }
        for (const id of picked.tried)
            tried.add(id);
        if (picked.kind === 'locked') {
            // ⚠️ **锁定时绝不可落到调用方的 `getAvailableAccount` 兜底** —— 那会绕过
            // 锁定、照样消耗永久积分，锁定形同虚设（与 Loomy 那条同因）。
            const days = buddyExpiringWindowDays();
            // ⚠️ 同样要区分「真的用尽」与「查不到」（与 Loomy 那条同型缺陷）：
            // 把一次网络抖动报成"额度已用尽"，用户会去解锁或白等，而号其实有钱。
            if (picked.reason?.kind === 'unknown') {
                throw new Error(`${options.displayName}：无法确认是否有可用账号。已锁定永久积分，而部分账号的余额查询失败`
                    + `（${picked.reason.errors.slice(0, 2).join('；')}）。这些账号**可能仍有**「${days} 天内到期」`
                    + '的积分 —— 请重试，或在 Jet Hub 对应面板检查凭据是否失效。');
            }
            throw new Error(`${options.displayName}：没有可用账号。已锁定永久积分，而所有账号的「${days} 天内到期」`
                + '积分都已用尽。请在 Jet Hub 的 '
                + `${options.displayName} 面板解锁永久积分，或等待资源包到期后重新发放额度。`);
        }
        return { tried };
    };
    const buddy = new BuddyAuth(ctx);
    const buddyAdapter = registerBuddyLlm(ctx, {
        credentialRef: credentialRef(BUDDY_CREDENTIAL_REF),
        resolveCredential: async (modelId) => {
            // 优先按「快到期积分」分档选号（锁定永久积分时也走这条，见上）。
            const picked = await pickBuddyCredential({
                product: CODEBUDDY,
                selector: buddyBalanceSelector,
                displayName: 'CodeBuddy',
                modelId,
            });
            if (picked.credential)
                return picked.credential;
            // 回退到账号池的既有选择（凭据损坏的账号已被 tried 排除），最后才退单凭据 ref。
            // provider 实参用 CODEBUDDY.id 而非字面量 'buddy'：写死字面量在改名/多产品
            // 场景下会静默查不到账号（本插件在 workbuddy 上踩过同类坑）。
            // ⚠️ `modelId` 必须透传：限流按**模型**记（`modelRateLimits[model]`），
            // 传空串会让 `getAvailableAccount` 的限流过滤整体短路
            //（`if (modelId.length === 0) return true`）→ 被标记限流的账号仍被选中。
            const available = await pool.getAvailableAccount(CODEBUDDY.id, modelId ?? '', picked.tried);
            if (available)
                return available.credential;
            const resolved = await ctx.credentials.resolve(credentialRef(BUDDY_CREDENTIAL_REF));
            if (!resolved)
                return undefined;
            try {
                return JSON.parse(resolved.value);
            }
            catch {
                return undefined;
            }
        },
        // 刷新**账号池里实际使用的那一个账号**，而不是默认单凭据 ref ——
        // 后者在 Jet Hub 登录路径下根本不存在，会把 401 报成「未配置凭据」
        // 并自锁。详见 createPoolRefresh 的注释。
        refresh: createPoolRefresh(pool, 'buddy', buddy),
        fetchRemoteModels: () => buddy.fetchModels(pool),
        readImage: makeReadImage(ctx),
        // 图片请求版本（缩放）桥接：issue !IKITT9。不可用时适配器自动回退原图。
        readImageRequest: makeReadImageRequest(ctx),
        accountPool: pool,
        product: CODEBUDDY,
    });
    // ===== WorkBuddy (腾讯 WorkBuddy) 服务 =====
    // 与 CodeBuddy 同源（同后端、同协议），差异全部由 product 配置承载。
    // 服务名由 BuddyAuth 依 product.id 派生，故两个产品分别注册为
    // ctx.buddyAuth / ctx.workbuddyAuth，互不覆盖。
    // 同样不注册斜杠命令：入口在 Jet Hub 的 WorkBuddy 面板。
    const workbuddy = new BuddyAuth(ctx, { product: WORKBUDDY });
    const workbuddyAdapter = registerBuddyLlm(ctx, {
        credentialRef: credentialRef(WORKBUDDY.defaultCredentialRef),
        resolveCredential: async (modelId) => {
            // 只从 workbuddy 的账号池取账号，回退到 WorkBuddy 自己的单凭据 ref，
            // 保证不会串用 CodeBuddy 的凭据。
            // 选号策略与 CodeBuddy 同款，但用的是**WorkBuddy 自己的锁状态与余额缓存**
            // （两站积分构成不同：实测国际版是「Bonus Pack 14 天 + Free Plan 长期」）。
            const picked = await pickBuddyCredential({
                product: WORKBUDDY,
                selector: workbuddyBalanceSelector,
                displayName: 'WorkBuddy',
                modelId,
            });
            if (picked.credential)
                return picked.credential;
            // ⚠️ `modelId` 透传：否则模型级限流标记被忽略（详见 buddy 处说明）。
            // provider 实参用 WORKBUDDY.id 而非字面量 'workbuddy'（同类坑见上）。
            const available = await pool.getAvailableAccount(WORKBUDDY.id, modelId ?? '', picked.tried);
            if (available)
                return available.credential;
            const resolved = await ctx.credentials.resolve(credentialRef(WORKBUDDY.defaultCredentialRef));
            if (!resolved)
                return undefined;
            try {
                return JSON.parse(resolved.value);
            }
            catch {
                return undefined;
            }
        },
        // 同上：必须刷池内账号（`WORKBUDDY_ACCESS_TOKEN` 从未被写入过）。
        // 这条正是「workbuddy + deepseek-v4.1-flash 一直报未配置凭据」的根因。
        refresh: createPoolRefresh(pool, 'workbuddy', workbuddy),
        fetchRemoteModels: () => workbuddy.fetchModels(pool),
        readImage: makeReadImage(ctx),
        readImageRequest: makeReadImageRequest(ctx),
        accountPool: pool,
        product: WORKBUDDY,
    });
    // ===== LobsterAI (有道龙虾) 服务 =====
    // 第三个产品线，但协议与腾讯系**完全不同**：不走 external-link 轮询登录，
    // 而是本地回调 + authCode 换 token（见 src/lobsterai-oauth.ts）。
    // 服务名由 LobsteraiAuth 依 product.id 派生，注册为 ctx.lobsteraiAuth。
    // 与其他 provider 一样不注册斜杠命令：入口在 Jet Hub 的 LobsterAI 面板。
    const lobsterai = new LobsteraiAuth(ctx);
    const lobsteraiAdapter = registerLobsteraiLlm(ctx, {
        credentialRef: credentialRef(LOBSTERAI.defaultCredentialRef),
        resolveCredential: async (modelId) => {
            // 只从 LobsterAI 自己的账号池取账号，回退到自己的单凭据 ref，
            // 保证不会串用 CodeBuddy / WorkBuddy / CodeArts 的凭据。
            // provider 实参用 LOBSTERAI.id 而非字面量 'lobsterai'：写死字面量在
            // 改名/多产品场景下会静默查不到账号（本插件在 workbuddy 上踩过同类坑）。
            // ⚠️ `modelId` 透传：否则模型级限流标记被忽略（详见 buddy 处说明）。
            const available = await pool.getAvailableAccount(LOBSTERAI.id, modelId ?? '');
            if (available)
                return available.credential;
            const resolved = await ctx.credentials.resolve(credentialRef(LOBSTERAI.defaultCredentialRef));
            if (!resolved)
                return undefined;
            try {
                return JSON.parse(resolved.value);
            }
            catch {
                return undefined;
            }
        },
        refresh: async () => {
            // 必须刷新**解析凭据时所用的那一个**账号，而不是默认单凭据 ref。
            //
            // 为什么：resolveCredential（上面）优先从账号池取
            // `LOBSTERAI_ACCOUNT_XXX` 的凭据，而 `lobsterai.refresh()` 读写的是
            // `LOBSTERAI_ACCESS_TOKEN`。两者错配的后果是 —— 适配器检测到池凭据
            // 过期 → 调 refresh → 成功回写到**另一个** ref → 再 resolve 仍取到
            // 那份未更新的过期凭据 → 带着过期 token 发请求 → 401。
            // 用户看到的是「刚在 Jet Hub 登录好，却一直认证失败」，
            // 而日志里续期全是成功的，极难排查。
            //
            // 与 Go 一致：`handler.go:197-209` 也是先 Pick 出账号、再对该账号
            // `RefreshToken(acct)`（而非某个全局单例）。
            const available = await pool.getAvailableAccount(LOBSTERAI.id, '');
            if (available)
                await lobsterai.refreshAccountCredential(available.entry.credentialRef, pool, available.entry.id);
            else
                await lobsterai.refresh();
        },
        fetchRemoteModels: () => lobsterai.fetchModels(pool),
        resolveClientVersion: () => lobsterai.resolveClientVersion(),
        readImage: makeReadImage(ctx),
        // 图片请求版本（缩放）桥接：issue !IKITT9。该家实测 13 张原图（≈50 MiB）
        // 就回 `SERVER code=500`，撞的是请求体体积。
        readImageRequest: makeReadImageRequest(ctx),
        accountPool: pool,
        product: LOBSTERAI,
    });
    // ===== Qoder (阿里系 AI IDE) 服务 =====
    // 第五个产品线，协议与四者**都不同源**：PKCE 设备码轮询登录
    // （不起本地回调服务器，见 src/qoder-oauth.ts）。
    // 服务名由产品 id 派生，注册为 ctx.qoderAuth。
    // 与其它 provider 一样不注册斜杠命令：入口在 Jet Hub 的 Qoder 面板。
    const qoder = new QoderAuth(ctx);
    /**
     * 「本次实际使用的账号 id」跟踪表（provider id → 账号 id）。
     *
     * ## 为什么需要它
     *
     * 额度受限时要标记**当前账号**（见 `QoderAdapter.switchAccountOnQuota`）。
     * 但「当前账号」不能靠「再问一次账号池的默认账号」得到 —— 池的选号是
     * `getAvailableAccount()` 的即时决策，与适配器**本次实际拿到**的那份凭据
     * 可能是两个账号（例如池已因限流切走，而适配器手上仍是旧凭据）。
     * 标记落错账号的后果：真正受限的账号没被标记 → 下次又被选中 → 反复撞墙；
     * 无辜账号被标记 → 它当天用不了（虽不致命，但属无谓损失）。
     *
     * 故在 `resolveCredential` 里**记录实际返回的那个账号**，供适配器查询。
     * 用 `Map` 按 provider 分开，国际版与中国版互不影响。
     */
    const activeQoderAccountId = new Map();
    const qoderAdapter = registerQoderLlm(ctx, {
        credentialRef: credentialRef(QODER.defaultCredentialRef),
        resolveCredential: async (modelId) => {
            // 只从 Qoder 自己的账号池取账号，回退到自己的单凭据 ref，
            // 保证不会串用其它 provider 的凭据。
            // provider 实参用 QODER.id 而非字面量 'qoder'：写死字面量在
            // 改名/多产品场景下会静默查不到账号（本插件在 workbuddy 上踩过同类坑）。
            // ⚠️ `modelId` 透传：Qoder 的额度是「模型 + 账号」维度（见
            // `qoder-adapter.ts` 的 `switchAccountOnQuota`），传空串会让它刚写下的
            // 当日额度标记在下次选号时被忽略。
            const available = await pool.getAvailableAccount(QODER.id, modelId ?? '');
            if (available) {
                activeQoderAccountId.set(QODER.id, available.entry.id);
                return available.credential;
            }
            // 回退到单凭据路径：没有账号条目可标记，清空以免标记到过期的 id。
            activeQoderAccountId.set(QODER.id, undefined);
            const resolved = await ctx.credentials.resolve(credentialRef(QODER.defaultCredentialRef));
            if (!resolved)
                return undefined;
            try {
                return JSON.parse(resolved.value);
            }
            catch {
                return undefined;
            }
        },
        refresh: async () => {
            // 必须刷新**解析凭据时所用的那一个**账号，而不是默认单凭据 ref。
            //
            // 为什么：resolveCredential（上面）优先从账号池取
            // `QODER_ACCOUNT_XXX` 的凭据，而 `qoder.refresh()` 读写的是
            // `QODER_ACCESS_TOKEN`。两者错配的后果是 —— 适配器检测到池凭据
            // 过期 → 调 refresh → 成功回写到**另一个** ref → 再 resolve 仍取到
            // 那份未更新的过期凭据 → 带着过期 token 发请求 → 401。
            // 用户看到的是「刚在 Jet Hub 登录好，却一直认证失败」，
            // 而日志里续期全是成功的，极难排查。
            const available = await pool.getAvailableAccount(QODER.id, '');
            if (available)
                await qoder.refreshAccountCredential(available.entry.credentialRef, pool, available.entry.id);
            else
                await qoder.refresh();
        },
        readImage: makeReadImage(ctx),
        // 图片请求版本（缩放）桥接：issue !IKITT9。该家实测 15 张原图（≈57 MiB）
        // 直接 `TRANSPORT: fetch failed`，撞的是请求体体积。
        // ⚠️ qoder 与 qodercn **共用同一个 QoderAdapter 类**，故两站同时受益
        //（与「差异收敛到产品配置」这个模式一致 —— 别以为只改了一站）。
        readImageRequest: makeReadImageRequest(ctx),
        accountPool: pool,
        product: QODER,
        // 额度受限时标记「本次实际使用的账号」（理由见 `activeQoderAccountId` 注释）。
        currentAccountId: () => activeQoderAccountId.get(QODER.id),
    });
    // ===== Qoder 中国版（qodercn）=====
    // 与上面的国际版是**同一套协议实现**的第二个实例（差异全在 QODER_CN 配置里：
    // 域名 qoder.cn / openapi.qoder.com.cn / gateway.qoder.com.cn、client_id
    // 732aef47-…、以及一张自己的 14 条模型表）。刻意**不复制**任何 qoder*.ts
    // 实现文件 —— 协议同源，复制会让同类缺陷（tools 不下发、工具历史丢
    // tool_calls、错误帧不抛错）修两遍。
    // 服务名由 `${product.id}Auth` 派生，注册为 ctx.qoderCnAuth。
    // 不注册斜杠命令：入口在 Jet Hub 的「Qoder (中国版)」面板。
    const qoderCn = new QoderAuth(ctx, { product: QODER_CN });
    const qoderCnAdapter = registerQoderLlm(ctx, {
        credentialRef: credentialRef(QODER_CN.defaultCredentialRef),
        resolveCredential: async (modelId) => {
            // 只取中国版自己账号池的账号，回退到 QODERCN_ACCESS_TOKEN。
            // ⚠️ provider 实参必须是 QODER_CN.id：写死 'qoder' 会让中国版
            // 永远查不到自己的账号（本插件在 workbuddy 上踩过同类坑）。
            // ⚠️ `modelId` 透传：与 QODER.id 同因（当日额度是「模型 + 账号」维度）。
            const available = await pool.getAvailableAccount(QODER_CN.id, modelId ?? '');
            if (available) {
                activeQoderAccountId.set(QODER_CN.id, available.entry.id);
                return available.credential;
            }
            activeQoderAccountId.set(QODER_CN.id, undefined);
            const resolved = await ctx.credentials.resolve(credentialRef(QODER_CN.defaultCredentialRef));
            if (!resolved)
                return undefined;
            try {
                return JSON.parse(resolved.value);
            }
            catch {
                return undefined;
            }
        },
        refresh: async () => {
            // ⚠️ 必须刷新**解析凭据时所用的那一个**账号，而不是默认单凭据 ref。
            // 理由与国际版那条真实缺陷完全同因：resolveCredential 优先取池内凭据，
            // 而 refresh() 读写 QODERCN_ACCESS_TOKEN，两者错配会让日志里续期全成功、
            // 用户却「刚登录却一直认证失败」。
            const available = await pool.getAvailableAccount(QODER_CN.id, '');
            if (available)
                await qoderCn.refreshAccountCredential(available.entry.credentialRef, pool, available.entry.id);
            else
                await qoderCn.refresh();
        },
        readImage: makeReadImage(ctx),
        // 同 QODER：两站共用同一个适配器类，缩放桥接也必须两边都接
        //（只接一边会让中国版的截图照样撞 57 MiB）。
        readImageRequest: makeReadImageRequest(ctx),
        accountPool: pool,
        product: QODER_CN,
        // 额度受限时用它标记「当前账号」（见 `QoderAdapter.switchAccountOnQuota`）。
        // ⚠️ 取「**本次实际使用**的账号」而非池里默认那一个：池的默认账号可能与之
        // 不同（例如本账号被限流、池已切到别的账号），标错就会让标记落在无辜账号上。
        currentAccountId: () => activeQoderAccountId.get(QODER_CN.id),
    });
    // ===== TRAE（字节 TRAE IDE）服务 =====
    // 第六个产品线，协议与前面几者**完全不同**：认证用 ExchangeToken（轮换 refreshToken），
    // 对话用 Cloud-IDE-JWT 鉴权，载荷需从 OpenAI 格式转换为 SOLO 格式，
    // SSE 为自定义格式（非 OpenAI 标准），需独立解析。
    // 服务名由 TraeAuth 依 product.id 派生，注册为 ctx.traeAuth。
    // 不注册斜杠命令：入口在 Jet Hub 的 TRAE 面板。
    const trae = new TraeAuth(ctx);
    const traeAdapter = registerTraeLlm(ctx, {
        credentialRef: credentialRef(TRAE.defaultCredentialRef),
        resolveCredential: async (modelId) => {
            // ⚠️ `modelId` 必须透传：限流是**按模型**记的（`modelRateLimits[model]`），
            // 传空串会让 `getAvailableAccount` 的限流过滤整体短路
            //（`if (modelId.length === 0) return true`）→ 被标记限流的账号仍被选中，
            // 换号形同虚设（用户报障「没有切换」的根因之一）。
            const available = await pool.getAvailableAccount(TRAE.id, modelId ?? '');
            if (available)
                return available.credential;
            const resolved = await ctx.credentials.resolve(credentialRef(TRAE.defaultCredentialRef));
            if (!resolved)
                return undefined;
            try {
                return JSON.parse(resolved.value);
            }
            catch {
                return undefined;
            }
        },
        refresh: async () => {
            const available = await pool.getAvailableAccount(TRAE.id, '');
            if (available)
                await trae.refreshAccountCredential(available.entry.credentialRef, pool, available.entry.id);
            else
                await trae.refresh();
        },
        fetchRemoteModels: () => trae.fetchModels(pool),
        // 图片字节桥接：TRAE 上游**支持图片**（见 Issue #IKHDKC 的实测记录），
        // 但模态按模型判定（远端 `display_config.multimodal`），故这里只负责读字节。
        readImage: makeReadImage(ctx),
        accountPool: pool,
        product: TRAE,
    });
    // ===== Cline（Cline 桌面端 / Cline API）服务 =====
    // 第七个产品线，协议与前面六者**都不同源**：登录是 **WorkOS 设备码轮询**
    // （api.workos.com，不起本地回调端口），鉴权头是 `Bearer workos:<jwt>`
    // （前缀**不可剥**），推理是**标准 OpenAI 兼容**端点。
    // 服务名由 ClineAuth 依 product.id 派生，注册为 ctx.clineAuth。
    // 不注册斜杠命令：入口在 Jet Hub 的 Cline 面板。
    const cline = new ClineAuth(ctx);
    const clineAdapter = registerClineLlm(ctx, {
        credentialRef: credentialRef(CLINE.defaultCredentialRef),
        resolveCredential: async (modelId) => {
            // 只从 Cline 自己的账号池取账号，回退到自己的单凭据 ref，
            // 保证不会串用其它 provider 的凭据。
            // provider 实参用 CLINE.id 而非字面量 'cline'：写死字面量在
            // 改名/多产品场景下会静默查不到账号（本插件在 workbuddy 上踩过同类坑）。
            // ⚠️ `modelId` 透传：否则模型级限流标记被忽略（详见 buddy 处说明）。
            const available = await pool.getAvailableAccount(CLINE.id, modelId ?? '');
            if (available)
                return available.credential;
            const resolved = await ctx.credentials.resolve(credentialRef(CLINE.defaultCredentialRef));
            if (!resolved)
                return undefined;
            try {
                return JSON.parse(resolved.value);
            }
            catch {
                return undefined;
            }
        },
        refresh: async () => {
            // 必须刷新**解析凭据时所用的那一个**账号，而不是默认单凭据 ref。
            //
            // 为什么：resolveCredential（上面）优先从账号池取
            // `CLINE_ACCOUNT_XXX` 的凭据，而 `cline.refresh()` 读写的是
            // `CLINE_ACCESS_TOKEN`。两者错配的后果是 —— 适配器检测到池凭据
            // 过期 → 调 refresh → 成功回写到**另一个** ref → 再 resolve 仍取到
            // 那份未更新的过期凭据 → 带着过期 token 发请求 → 401。
            // 用户看到的是「刚在 Jet Hub 登录好，却一直认证失败」，
            // 而日志里续期全是成功的，极难排查。
            const available = await pool.getAvailableAccount(CLINE.id, '');
            if (available)
                await cline.refreshAccountCredential(available.entry.credentialRef, pool, available.entry.id);
            else
                await cline.refresh();
        },
        // 图片字节桥接：Cline 内嵌目录的 `capabilities` 含 `images`，
        // 模态按模型判定（见 ClineAdapter.inputModalitiesFor）。
        readImage: makeReadImage(ctx),
        // 图片请求版本（缩放）桥接：issue !IKITT9。该家实测 24 张原图全过、
        // 32 张（≈122 MiB）才 `TRANSPORT` —— 余量比其他家大，但仍需兜住长会话。
        readImageRequest: makeReadImageRequest(ctx),
        accountPool: pool,
        product: CLINE,
    });
    // ===== Loomy（讯飞办公助手）服务 =====
    // 第八个产品线，与前面七者**都不同源**：登录是**短信验证码**
    // （讯飞 CAccount，HMAC-SHA1 签名，没有 loginUrl 可打开），
    // 推理是标准 OpenAI 兼容（复用 openai-compat.ts）。
    // 服务名由 LoomyAuth 依 product.id 派生，注册为 ctx.loomyAuth。
    // 不注册斜杠命令：入口在 Jet Hub 的 Loomy 面板。
    const loomy = new LoomyAuth(ctx);
    /**
     * 按凭据 ref 解析 Loomy 凭据（供选号器与兜底路径共用）。
     *
     * 抽成局部函数而非内联两遍：选号器需要它查余额，而解析最终凭据又要用它 ——
     * 两处若各写一遍 JSON 解析，格式一变就会只改一处。
     */
    const resolveLoomyCredentialByRef = async (refName) => {
        const resolved = await ctx.credentials.resolve(credentialRef(refName));
        if (!resolved)
            return undefined;
        try {
            return JSON.parse(resolved.value);
        }
        catch {
            return undefined;
        }
    };
    /**
     * Loomy 的**按余额优先选号器**（负载均衡）。
     *
     * ⚠️ **为什么需要它**（真实缺陷）：实测 Loomy 的今日赠送额度（每天 5000）
     * 耗尽后，服务端**继续扣永久积分且不报错** —— 「耗尽」是**静默降级**而非错误。
     * 而本插件既有的「限流 → 换号」只在服务端返回限流错误时触发，
     * 故对 Loomy **完全无效**：会一直烧同一个号（用户报障）。
     *
     * 策略：优先有今日额度的号 → 其次有永久积分的号 → 都无/查不到排最后。
     * 档内保持手动拖拽顺序（详见 `loomy-balance-rank.ts`）。
     */
    const loomyBalanceSelector = new LoomyBalanceSelector({
        product: LOOMY,
        resolveCredential: resolveLoomyCredentialByRef,
    });
    const loomyAdapter = registerLoomyLlm(ctx, {
        credentialRef: credentialRef(LOOMY.defaultCredentialRef),
        /**
         * 解析本轮该用哪个账号的凭据。
         *
         * ⚠️ `modelId` 由适配器传入（见 `LoomyAdapterOptions.resolveCredential`
         * 的签名说明）—— **必须透传给 `getAvailableAccount`**，否则模型级限流
         * 过滤失效（早期实现传空串 `''`，等于「不按模型过滤」）。
         */
        resolveCredential: async (modelId) => {
            // 只从 Loomy 自己的账号池取账号，回退到自己的单凭据 ref，
            // 保证不会串用其它 provider 的凭据。
            // provider 实参用 LOOMY.id 而非字面量 'loomy'：写死字面量在
            // 改名/多产品场景下会静默查不到账号（本插件在 workbuddy 上踩过同类坑）。
            //
            // ⚠️ 先按「模型未受限 + 未停用」筛出候选，**再**按余额分档选号。
            // 余额排序只在这批候选内部进行 —— 即你的要求：
            // 「策略建立在模型没有受限且账户没有被设置为停用的基础上」。
            const candidates = pool
                .listAccountsByProvider(LOOMY.id)
                .filter(a => a.enabled)
                .filter((a) => {
                // 与 `getAvailableAccount` 的限流判据保持一致（空 modelId = 不过滤）。
                const key = modelId ?? '';
                if (key.length === 0)
                    return true;
                if (!a.modelRateLimits)
                    return true;
                const resetAt = a.modelRateLimits[key];
                return resetAt === undefined || resetAt === 0 || Date.now() >= resetAt;
            })
                .map(a => ({ id: a.id, credentialRef: a.credentialRef }));
            // 「锁定永久积分」：只允许消耗今日赠送额度（用户要求，且持久化）。
            // 开关按 provider 存（CodeBuddy / WorkBuddy 各有一份，互不影响）。
            const allowPermanent = !pool.permanentLocked(LOOMY.id);
            if (candidates.length > 0) {
                const picked = await loomyBalanceSelector.select(candidates, { allowPermanent });
                if (picked.ok) {
                    const credential = await resolveLoomyCredentialByRef(picked.account.credentialRef);
                    if (credential !== undefined)
                        return credential;
                }
                else if (!allowPermanent) {
                    // ⚠️ **锁定时绝不可落到下面的单凭据兜底** —— 那会绕过锁定、
                    // 照样消耗永久积分，锁定形同虚设。这里直接抛明确错误（用户要求）。
                    //
                    // ⚠️ **必须区分「真的用尽」与「查不到」**（真实缺陷，用户报障
                    // 2026-09-29）：曾把两者混成一句「今日额度都已用尽」，于是
                    // **一次网络抖动**就让用户被告知"钱花完了"（实测当时 4 个号里
                    // 3 个还有 4965/5000/5000）—— 用户会去解锁或白等一天，而号其实有钱。
                    const reason = picked.reason;
                    if (reason.kind === 'unknown') {
                        throw new Error('Loomy：无法确认是否有可用账号。已锁定永久积分，而部分账号的余额查询失败'
                            + `（${reason.errors.slice(0, 2).join('；')}）。这些账号**可能仍有**今日额度 —— `
                            + '请重试，或在 Jet Hub 的 Loomy 面板检查凭据是否失效。');
                    }
                    throw new Error('Loomy：没有可用账号。已锁定永久积分，而所有账号的今日赠送额度都已用尽。'
                        + '请在 Jet Hub 的 Loomy 面板解锁永久积分，或等待明日额度刷新。');
                }
            }
            // 兜底：账号池为空/全部不可解析时，退回单凭据 ref。
            const resolved = await ctx.credentials.resolve(credentialRef(LOOMY.defaultCredentialRef));
            if (!resolved)
                return undefined;
            try {
                return JSON.parse(resolved.value);
            }
            catch {
                return undefined;
            }
        },
        refresh: async () => {
            // ⚠️ Loomy **没有 refresh 端点**，这里的 `refresh` 语义是
            // 「探测凭据是否仍有效」，失效时抛错提示重新登录。
            //
            // 仍须刷新**解析凭据时所用的那一个**账号，而不是默认单凭据 ref ——
            // 否则探测的是另一份凭据，用户会看到「刚登录好却一直认证失败」。
            const available = await pool.getAvailableAccount(LOOMY.id, '');
            if (available)
                await loomy.refreshAccountCredential(available.entry.credentialRef, pool, available.entry.id);
            else
                await loomy.refresh();
        },
        // 远端模型目录：GET /api/v1/models。
        // ⚠️ 必须用 **token 头**（业务端点），不是 Bearer —— 带错会得到
        // `100002 缺少 token`，表现为「模型列表永远停在兜底表」。
        // 失败时返回空数组，由适配器回退兜底表。
        fetchRemoteModels: async () => {
            const available = await pool.getAvailableAccount(LOOMY.id, '');
            const resolved = available !== null && available !== undefined
                ? { value: JSON.stringify(available.credential) }
                : await ctx.credentials.resolve(credentialRef(LOOMY.defaultCredentialRef));
            if (resolved === undefined)
                return [];
            let credential;
            try {
                credential = JSON.parse(resolved.value);
            }
            catch {
                return [];
            }
            const response = await fetch(`${LOOMY.apiBase}/models`, {
                headers: { Accept: 'application/json', token: credential.access_token },
                signal: AbortSignal.timeout(30_000),
            });
            if (!response.ok)
                return [];
            return parseLoomyRemoteModels(await response.json());
        },
        // 图片字节桥接：按模型能力判定（远端 capabilities.input_modalities 含 image）。
        readImage: makeReadImage(ctx),
        accountPool: pool,
        product: LOOMY,
    });
    // ===== Raccoon Work（商汤小浣熊）服务 =====
    // 第九个产品线。登录与 Loomy 同型（**本地页承载**的微信扫码 + 短信双路径），
    // 但**有** refresh 端点（凭据可静默续期），且客户端可能未安装。
    //
    // ⚠️ **不依赖客户端**：官方桌面端靠 `office-raccoon://auth/callback` 自定义协议
    // 回调，本插件（宿主侧 Node 进程）收不到；故改为「宿主本地生成 code + 自行轮询」，
    // 完全绕开该回调。凭据存插件自有的 ctx.credentials，不读客户端任何文件。
    // 见 tests/unit/raccoon-client-independence.spec.ts 的回归防线。
    //
    // 服务名由 RaccoonAuth 依 product.id 派生，注册为 ctx.raccoonAuth。
    // 不注册斜杠命令：入口在 Jet Hub 的 Raccoon 面板。
    const raccoon = new RaccoonAuth(ctx);
    const raccoonAdapter = registerRaccoonLlm(ctx, {
        credentialRef: credentialRef(RACCOON.defaultCredentialRef),
        resolveCredential: async (modelId) => {
            // 只从 raccoon 自己的账号池取账号，回退到自己的单凭据 ref，
            // 保证不会串用其它 provider 的凭据。
            // provider 实参用 RACCOON.id 而非字面量：写死字面量在改名/多产品场景下
            // 会静默查不到账号（本插件在 workbuddy 上踩过同类坑）。
            // ⚠️ `modelId` 透传：否则模型级限流标记被忽略（详见 buddy 处说明）。
            const available = await pool.getAvailableAccount(RACCOON.id, modelId ?? '');
            // `getAvailableAccount` 的凭据类型是 `CodeArtsCredential | BuddyCredential`
            // 联合（历史遗留），与 `RaccoonCredential` 无充分重叠，故经 `unknown` 转换。
            // 运行时安全性由 provider 过滤保证：查询用 `RACCOON.id`，取到的必是 raccoon 凭据。
            if (available)
                return available.credential;
            const resolved = await ctx.credentials.resolve(credentialRef(RACCOON.defaultCredentialRef));
            if (!resolved)
                return undefined;
            try {
                return JSON.parse(resolved.value);
            }
            catch {
                return undefined;
            }
        },
        refresh: async () => {
            // ⚠️ raccoon **有** refresh 端点（与 Loomy 恒 false 不同），这里是真续期。
            //
            // 仍须刷新**解析凭据时所用的那一个**账号，而不是默认单凭据 ref ——
            // 否则续期的是另一份凭据，用户会看到「刚登录好却一直认证失败」。
            const available = await pool.getAvailableAccount(RACCOON.id, '');
            if (available) {
                // ⚠️ **必须传 pool + entry.id**：续期成功后要把新的 `expiresAt` 写回
                // 账号池，否则 UI 会一直显示「已过期」而实际能正常发消息
                //（真实缺陷：JWT 已续到 15:09、账号池仍是 12:02，相差 3.1 小时）。
                // 这条路径正是「发消息时按需续期」，故它是最常触发回写的地方。
                await raccoon.refreshAccountCredential(available.entry.credentialRef, pool, available.entry.id);
            }
            else {
                await raccoon.refresh();
            }
        },
        // 远端模型目录：委托给 RaccoonAuth.fetchModels（它负责 Bearer 头与
        // visible 过滤 + raccoonDisplayName 生成含倍率的展示名）。
        // 失败时返回空数组，由适配器回退兜底表。
        fetchRemoteModels: () => raccoon.fetchModels(pool),
        // 图片字节桥接：按模型能力判定（远端 tags 含 vision）。
        readImage: makeReadImage(ctx),
        // ⚠️ raccoon 尤其需要请求版本：该网关按**请求体字节**设限
        // （实测 `HTTP_413: request body exceeds 10MB`，两张大截图就占掉大半配额）。
        readImageRequest: makeReadImageRequest(ctx),
        accountPool: pool,
        product: RACCOON,
    });
    // 一次性修复**老账号**的昵称与凭据字段（与上面 WorkBuddy 的启动清理同类）。
    //
    // 早期实现把服务端的 `name` 直接当昵称用，而实测它是**自动生成的默认名**
    //（本机账号是 `RaccoonAva`），注册第二个账号时会重名、无法区分；
    // 且凭据里没存 `phone`（后来才发现 `user_info.phone` 可用于消歧）。
    // 光改代码只影响新登录的账号，故这里主动补一次：
    // 拉 `user_info` 补 `phone`，并用 `buildRaccoonNickname` 重算昵称。
    //
    // ⚠️ 幂等 + 失败不阻塞启动（`repairAccountNicknames` 内部逐账号 catch）。
    void raccoon.repairAccountNicknames(pool, buildRaccoonNickname).then((repaired) => {
        if (repaired.length > 0) {
            ctx.logger.info(`[jet-hub] 已修正 ${repaired.length} 个 Raccoon 账号的显示名（追加手机号尾号以便区分）：${repaired.join(', ')}`);
        }
    }).catch((error) => {
        ctx.logger.warn(`[jet-hub] 修正 Raccoon 账号显示名失败：${String(error)}`);
    });
    // ===== ZCode（智谱 z.ai 免费额度通道）=====
    //
    // 形态与前面所有 provider **相同**：读凭据 → 直发远端。
    // 用户只需在官方 ZCode 客户端登录一次 —— 凭据就落在
    // `~/.zcode/v2/credentials.json`（AES-256-GCM 加密，公开算法），
    // 本插件纯 Node 解密即可，**不需要任何实例常驻**。
    //
    // 与其余 provider 的三处差异（全部实测）：
    //   1. 协议是 **Anthropic Messages**（不是 OpenAI 兼容）——
    //      见 `zcode-anthropic.ts` 的转换层。
    //   2. 每请求要产出一个**一次性**的阿里云 captcha（约 1.2 秒）——
    //      见 `zcode-captcha.ts`。
    //   3. 请求体必须带官方身份块与首轮日期块，否则上游回 `3012` ——
    //      见 `zcode-identity.ts`。
    //
    // ⚠ **可脱离官方客户端使用**：登录走官方 CLI 设备授权流
    // （`/oauth/cli/init` → 浏览器授权 → `/oauth/cli/poll/{flow_id}`，
    // 纯 HTTP，见 `zcode-login.ts`），且 `device_mid` **由插件自己生成**。
    // 若机器上已装并登录过官方客户端，也会自动读取它的凭据作为回退。
    //
    // ⚠ **不可续期**：凭据是静态的（JWT 的 payload 里没有 `exp`）。
    // 失效时上游回 401/1002，适配器归为 AUTH 并提示用户重新登录。
    //
    // 服务名注册为 ctx.zcodeAuth（由 `Service` 基类完成）。
    // 不注册斜杠命令：入口在 Jet Hub 的 ZCode 面板。
    //
    // ⚠ **必须把账号池传进去**：`ZcodeAuth.current()` 要从账号池里找用户在
    // Jet Hub 登录时创建的那个 ref（`ZCODE_ACCOUNT_XXXX`），而不是只认固定的
    // `ZCODE_CREDENTIAL`。不传的话「登录成功但面板显示未配置」——
    // 且该缺口会被「回退读官方凭据文件」掩盖，只有没装官方客户端的用户才看得到。
    const zcode = new ZcodeAuth(ctx, { accountPool: pool });
    /**
     * captcha 配置：优先向服务端索取，失败回退内置兜底值。
     *
     * ## ⚠ 这里**不再**做缓存（2026-10-01 修正）
     *
     * 旧实现是 `??=` 的**永久缓存**，两个缺陷（都已实测确认）：
     *
     * 1. **服务端换 `sceneId`／灰度切换后永不生效**（必须重启宿主才能跟上）；
     * 2. **首次拉取失败会被永久固化** —— `??=` 连「回退到兜底值」这个结果
     *    一起记住，此后即使服务端恢复也不会重试。
     *
     * 正确做法是官方 `f3()` 的语义：**60 秒 TTL + 在飞去重、失败不缓存**。
     * 那个能力已下沉到 `ZcodeAuth.fetchCaptchaConfig()`
     *（见其 `captchaConfigCacheInstance`），故这里只做「拿 → 回退」的编排。
     */
    const resolveZcodeCaptchaConfig = async () => {
        const remote = await zcode.fetchCaptchaConfig().catch(() => undefined);
        if (remote !== undefined) {
            zcodeAdapter.setCaptchaConfig(remote);
            return remote;
        }
        return ZCODE_CAPTCHA_FALLBACK;
    };
    /**
     * 「本次 ZCode 请求**实际使用**的账号 id」。
     *
     * ## 为什么需要（与 `activeQoderAccountId` 同因）
     *
     * 额度受限时适配器要**标记失败的账号**，而它能拿到的只有这个回调。
     * 池的选号是即时决策，且**切号后回调不会跟着变** —— 若适配器改用
     * 「池当前的默认账号」，切到 B 之后失败时会**再标记一次 A**，
     * B 从未被标记，下次取号又把 B 选中，于是在 A/B 之间反复空转
     * （`qoder-adapter.ts` 的 `switchAccountOnQuota` 注释里记了这条实测）。
     *
     * ⇒ 在 `resolveCredential` 里记录**实际返回的那个账号**，供适配器查询。
     */
    const activeZcodeAccountId = new Map();
    const zcodeAdapter = registerZcodeLlm(ctx, {
        credentialRef: credentialRef(ZCODE.defaultCredentialRef),
        resolveCredential: async (modelId) => {
            // 只从 zcode 自己的账号池取账号，回退到自己的单凭据 ref，
            // 保证不会串用其它 provider 的凭据。
            // provider 实参用 ZCODE.id 而非字面量：写死字面量在改名/多产品场景下
            // 会静默查不到账号（本插件在 workbuddy 上踩过同类坑）。
            // ⚠ `modelId` 透传：否则模型级限流标记被忽略（详见 buddy 处说明）。
            const available = await pool.getAvailableAccount(ZCODE.id, modelId ?? '');
            /**
             * `getAvailableAccount` 的凭据类型是历史遗留的联合类型，
             * 与 `ZcodeCredential` 无充分重叠，故经 `unknown` 转换。
             * 运行时安全性由 provider 过滤保证：查询用 `ZCODE.id`，取到的必是 zcode 凭据。
             */
            if (available) {
                // 记下**实际返回的**账号：额度受限时适配器要标记的是它。
                activeZcodeAccountId.set(ZCODE.id, available.entry.id);
                return available.credential;
            }
            /**
             * 账号池里没有条目时，落到 `ZcodeAuth.current()` ——
             * 它已经实现了「插件自存优先 → 官方客户端凭据回退」。
             * ⚠ 不要在这里重复实现那条优先级（重复必然漂移）。
             *
             * ⚠ 同时**清空**记录：没有账号条目可标记，留着旧 id 会误伤一个无辜账号。
             */
            activeZcodeAccountId.set(ZCODE.id, undefined);
            return await zcode.current();
        },
        refresh: async () => {
            // ⚠ ZCode **没有** refresh 端点（与 Loomy 恒 false 同类，但原因不同）：
            // 凭据是**静态**的（在官方客户端登录一次就固定下来）。
            // 这里做的是「重读磁盘凭据并回写账号条目」——
            // 使用户在官方客户端重新登录后，本插件无需重启即可用上新凭据。
            const credential = await zcode.current();
            if (credential === undefined)
                return;
            const available = await pool.getAvailableAccount(ZCODE.id, '');
            // ⚠ `getAvailableAccount` 返回的可能是 `null`（本仓库该 API 的约定），
            // 只判 `undefined` 会漏掉它 —— 用显式判空覆盖两者。
            if (available === null || available === undefined)
                return;
            try {
                await ctx.credentials.set(credentialRef(available.entry.credentialRef), JSON.stringify(credential));
            }
            catch {
                // 回写失败不影响请求（请求走磁盘凭据）。静默即可。
            }
        },
        // ⚠ captcha 是**一次性**的 —— 每次调用都必须现产一个新 param。
        // 走 `zcode.mintCaptcha`：整个插件**共用一台**常驻浏览器
        //（适配器自建会变成两台，白占 200MB）。
        //
        // ⚠ `options.signal` 必须继续往下传：captcha 侧的取页等待与建连历史上有
        // 无超时的路径，不传就等于「用户点停止也停不下来」（真实缺陷，2026-09-29）。
        mintCaptcha: async (options) => {
            const config = await resolveZcodeCaptchaConfig();
            return await zcode.mintCaptcha(config, options);
        },
        captchaRegion: ZCODE_CAPTCHA_FALLBACK.region,
        /**
         * ⚠ **图片字节桥接** —— 这是图片能真正发出去的关键。
         *
         * DSH 的图片块只带 `attachment:{attachmentId}`，真正拿字节要经附件服务。
         * 缺了这两项，`serializeMessages` 拿到**空映射**，图片会退化成
         * `[image unavailable]` 占位符 —— 实测症状是模型回
         * 「我在当前对话中没有收到任何图片」。
         *
         * ⚠ 与其余 provider 同款约定（十余处一致）：
         *   - `readImage`：拿原始字节（内联为 data URL）
         *   - `readImageRequest`：拿**按预算缩放后**的字节；**不可用时返回
         *     `undefined`**（不抛错），适配器据此回退原图
         */
        readImage: makeReadImage(ctx),
        // 图片请求版本（缩放）桥接：ZCode 免费通道的请求体没有实测硬上限，
        // 但 base64 后的截图很大（2560×1600 各约 3.9 MB），两张就接近常见网关
        // 的 10MB 门槛 —— 与 raccoon 的同因（issue !IKITT9 那一族）。
        readImageRequest: makeReadImageRequest(ctx),
        // 模型目录用静态白名单（实测可用的两个）—— 上游模型池含
        // 实测返回空响应的两条（GLM-5-Turbo / GLM-5.2），故不枚举远端。
        fetchRemoteModels: () => zcode.fetchModels(),
        accountPool: pool,
        product: ZCODE,
        // 就绪判据 = 有可用凭据（插件自存或官方客户端凭据）。
        isReady: async () => (await zcode.current()) !== undefined,
        /**
         * 额度用尽 / 无权益时，适配器据此**标记失败的账号并切换**。
         *
         * 回调的是「本次实际使用的账号」（理由见 `activeZcodeAccountId` 的注释）——
         * 不要改成 `pool.getAvailableAccount(...)` 之类「再问一次池」的实现。
         */
        currentAccountId: () => activeZcodeAccountId.get(ZCODE.id),
    });
    // 一次性修复**老 TRAE 账号**的展示名（与上面 Raccoon 同类，同因）：
    // 服务端 ScreenName 是**按 uid 自动生成的默认名**（`用户26815487395`），
    // 多账号无法区分；`GetUserInfo` 的 `NonPlainTextMobile`（脱敏手机号）可区分。
    // 光改代码只影响新登录的账号，故这里主动补一次。
    //
    // ⚠️ 幂等 + 失败不阻塞启动（`repairAccountNicknames` 内部逐账号 catch）。
    void trae.repairAccountNicknames(pool).then((repaired) => {
        if (repaired.length > 0) {
            ctx.logger.info(`[jet-hub] 已修正 ${repaired.length} 个 TRAE 账号的显示名（改用脱敏手机号以便区分）：${repaired.join(', ')}`);
        }
    }).catch((error) => {
        ctx.logger.warn(`[jet-hub] 修正 TRAE 账号显示名失败：${String(error)}`);
    });
    // 一次性修复**老 LobsterAI 账号**的展示名（同类，但成因不同）：
    // 服务端把**手机号本身**当 `user.nickname` 下发，且只脱敏到「露末 4 位」
    // （`130****1100`）—— 按用户要求收敛为只露末 2 位（`130******00`）。
    // 纯本地归一化（幂等），无需重新登录。
    //
    // ⚠️ 幂等 + 失败不阻塞启动（`repairAccountNicknames` 内部逐账号 catch）。
    void lobsterai.repairAccountNicknames(pool).then((repaired) => {
        if (repaired.length > 0) {
            ctx.logger.info(`[jet-hub] 已修正 ${repaired.length} 个 LobsterAI 账号的显示名（手机号改为只露末 2 位）：${repaired.join(', ')}`);
        }
    }).catch((error) => {
        ctx.logger.warn(`[jet-hub] 修正 LobsterAI 账号显示名失败：${String(error)}`);
    });
    // ===== 多账号静默续期调度 =====
    // 替代原有的单账号 scheduleRefresh()，使用 refreshAll() 遍历所有账号续期
    const REFRESH_INTERVAL_MS = 30 * 60 * 1000; // 每 30 分钟检查一次
    /**
     * 十个 provider 实例的续期入口（`buddy` 与 `workbuddy` 是两个实例、同一个类）。
     *
     * 收成一张表是为了让「失败必须留日志」这条规则**只写一遍** —— 原先这里是
     * 十个空 catch（注释写着「静默」），把 provider 内部的告警与异常一起吞掉，
     * 「凭据一直刷不动」在日志里完全无痕（issue !IKIRTT 的可观测性条目）。
     */
    const refreshTargets = [
        ['codearts', (p) => service.refreshAll(p)],
        ['buddy', (p) => buddy.refreshAll(p)],
        ['workbuddy', (p) => workbuddy.refreshAll(p)],
        ['lobsterai', (p) => lobsterai.refreshAll(p)],
        ['qoder', (p) => qoder.refreshAll(p)],
        ['qodercn', (p) => qoderCn.refreshAll(p)],
        ['trae', (p) => trae.refreshAll(p)],
        ['cline', (p) => cline.refreshAll(p)],
        // ⚠️ Loomy 不可续期：这里只探测**已过期**的账号（见 LoomyAuth.refreshAll）。
        ['loomy', (p) => loomy.refreshAll(p)],
        // raccoon **可续期**：只按 refreshable 过滤，且只续进入 lead 窗口的账号。
        ['raccoon', (p) => raccoon.refreshAll(p)],
        // zcode **不可续期**（凭据是静态的）—— 但这个方法仍做实事：
        // 把磁盘上最新的凭据回写到全部 zcode 账号，
        // 使用户在官方客户端重新登录后无需重启 DSH。
        // ⚠ 只按 `refreshable` 过滤、**不看 `enabled`**（AGENTS.md 既有约定）。
        ['zcode', (p) => zcode.refreshAll(p)],
    ];
    async function refreshAllCredentials() {
        for (const [tag, refreshAll] of refreshTargets) {
            try {
                await refreshAll(pool);
            }
            catch (error) {
                // 单个 provider 抛错不得中断其余九个（各 refreshAll 内部本就逐账号
                // try，能冒到这里的已是「整批失败」级别的异常）。
                ctx.logger?.warn?.(`[jet-hub] ${tag} 批量续期失败：${error instanceof Error ? error.message : String(error)}`);
            }
        }
    }
    // 启动时如果有任何可续期账号，安排定期续期。
    //
    // ⚠️ 判据只看 `refreshable`，**不看 `enabled`**：停用只影响账号池的自动
    // 选号，不该让凭据停止续期。早期这里写成 `a.refreshable && a.enabled`，
    // 于是「所有账号都被停用」时续期定时器**根本不启动**，凭据一路过期到
    // refresh_token 失效，用户重新启用后只能重新登录（真实缺陷）。
    //
    // ⚠️ **必须立刻先跑一轮**（issue !IKIRTT 的主缺陷）：早先这里只有
    // `setInterval`，第一次处理要等满一个周期。短寿命 provider（cline 1 小时、
    // codearts 约 2 小时、raccoon 3 小时）的凭据在宿主关闭期间早就到期了，
    // 于是重启后**最长 30 分钟**一直显示「已过期」、积分行一直 401。
    // 现在这一轮与 lead-time 过滤配合（`src/expiry-sync.ts` 的 `shouldRefreshNow`），
    // 只对「距过期不足 1 小时」的账号发续期请求，其余只做一次本地对账 ——
    // 既补上了首轮，又不会在启动时打出几十个无谓请求。
    pool.listAllAccounts().then(accounts => {
        const hasRefreshable = accounts.some(a => a.refreshable);
        if (!hasRefreshable)
            return;
        void refreshAllCredentials();
        const refreshTimer = setInterval(() => void refreshAllCredentials(), REFRESH_INTERVAL_MS);
        refreshTimer.unref?.();
        ctx.effect(() => () => {
            clearInterval(refreshTimer);
            service.stop();
            buddy.stop();
            workbuddy.stop();
            lobsterai.stop();
            qoder.stop();
            qoderCn.stop();
            trae.stop();
            cline.stop();
            loomy.stop();
            zcode.stop();
            // ⚠ 必须 dispose captcha 浏览器 —— 否则会留下孤儿 chromium
            //（约 200-400MB，且用户没有界面能关掉它）。
            zcodeAdapter.stop();
        }, 'jet-hub: multi-account refresh scheduler');
    }).catch((error) => {
        // ⚠️ 原来这个 `.then()` **没有** `.catch()`：`listAllAccounts()` 一旦 reject
        // （存储层异常），续期定时器就**永远不武装**，且日志里一个字都没有 ——
        // 那时上面的「最长 30 分钟」会恶化成「永不自愈」。
        ctx.logger?.warn?.(`[jet-hub] 多账号续期调度器启动失败（本次会话不会自动续期）：`
            + `${error instanceof Error ? error.message : String(error)}`);
    });
    // 保留旧的 stop scheduler（兼容旧命令）
    ctx.effect(() => () => {
        service.stop();
        buddy.stop();
        workbuddy.stop();
        lobsterai.stop();
        qoder.stop();
        qoderCn.stop();
        trae.stop();
        cline.stop();
        loomy.stop();
        zcode.stop();
        // 同上：captcha 浏览器必须随插件一起回收。
        zcodeAdapter.stop();
    }, 'codearts-auth.scheduler (legacy)');
    // ===== Jet Hub RPC 注册 =====
    // provider → 适配器实例：Jet Hub「显示列表」需要 `listAllModels()`（不受用户
    // 黑名单影响的全量目录，带最终展示名/倍率）。DSH 的 `ctx.llm` 只保证
    // `listModels`，不透传自定义方法，故这里显式把实例传下去。
    const modelAdapters = {
        // `codearts` 是 registerCodeArtsLlm 返回的**适配器实例**（与 CodeArtsAuth
        // 服务实例 `service` 不同名，故这里可以简写）。
        codearts,
        buddy: buddyAdapter,
        workbuddy: workbuddyAdapter,
        lobsterai: lobsteraiAdapter,
        qoder: qoderAdapter,
        qodercn: qoderCnAdapter,
        trae: traeAdapter,
        cline: clineAdapter,
        loomy: loomyAdapter,
        raccoon: raccoonAdapter,
        zcode: zcodeAdapter,
    };
    registerJetHubRpc(ctx, pool, service, buddy, workbuddy, lobsterai, qoder, qoderCn, trae, cline, loomy, raccoon, zcode, modelAdapters);
    ctx.provide('accountPool', pool);
}
//# sourceMappingURL=index.js.map