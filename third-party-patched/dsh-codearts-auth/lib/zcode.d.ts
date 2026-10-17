/**
 * ZCode 凭据读取（**纯 Node，不需要 ZCode 实例运行**）。
 *
 * ## 为什么不再用「本机 HTTP 桥」
 *
 * PR #17 初版的设计是「读 `<dataBaseDir>/.zcode/v2/bridge-port.json` → 打本机桥
 * → 由 ZCode 实例代发上游」。那条路有一个致命的可用性问题（实测）：
 *
 * **它依赖一个被补丁注入过的开源版实例。** 官方闭源版
 * （`%LOCALAPPDATA%\Programs\ZCode\ZCode.exe`）**不写** `bridge-port.json`
 * —— 故「装了 ZCode」并不等于「桥可用」。而现实中用户装的正是官方版。
 *
 * 更关键的是，上游准入**并不真的需要 Electron**：
 *
 * - captcha 是阿里云的**网页 SDK**（`o.alicdn.com/captcha-frontend/...`），
 *   只需「一个有 DOM 的浏览器」—— 不需要 ZCode 那个壳；
 * - 3012 的判据是**请求体内容**（system 身份块 + 首轮 user 的
 *   `<system-reminder>` 日期块），与运行时无关 —— 上游实测 curl 同样 200。
 *
 * 而凭据本身就躺在磁盘上，官方用一套**公开可复现**的算法加密：
 * `enc:v1:` + AES-256-GCM + `sha256(secret)` 密钥，secret 缺省由
 * `平台 + 家目录 + 用户名` 派生。故纯 Node 即可解密（实测 7 个键全部成功）。
 *
 * ## 与 `AGENTS.md` 既有约定一致
 *
 * 「靠『文件实际在哪』这个事实探测，比靠『进程记得什么』可靠」——
 * 这里读的是**磁盘上的凭据文件**，不依赖任何进程是否在跑。
 */
/** 密文前缀；无此前缀的值视为明文（官方实现同样如此）。 */
export declare const CREDENTIAL_PREFIX = "enc:v1:";
/** 覆盖密钥的环境变量（官方同名）。 */
export declare const CREDENTIAL_SECRET_ENV = "ZCODE_CREDENTIAL_SECRET";
/**
 * `credentials.json` 的候选位置（按优先级）。
 *
 * ⚠ 与初版 `bridgeDiscoveryCandidates()` 的区别：**不再扫盘**。
 * 初版为找 `bridge-port.json` 会遍历每个盘符的顶层目录（实测 82 个候选、
 * 每次请求约 3ms），因为发现文件可能落在自定义路径（`<项目>\_oss_data`）。
 * 而 `credentials.json` 的位置由**官方固定**：写在
 * `<dataBaseDir>/.zcode/v2/` 下，默认 `dataBaseDir` 就是家目录。
 * 故只需少量确定候选 —— 这让「每次请求重读」的成本可忽略。
 */
export declare function credentialFileCandidates(): readonly string[];
/** 定位实际存在的凭据文件；都找不到时返回首选路径（供报错信息用）。 */
export declare function resolveCredentialFilePath(): string;
/** 读磁盘上的原始凭据表（`键 → 密文或明文`）。失败返回 undefined。 */
export declare function readRawCredentials(filePath?: string): Record<string, string> | undefined;
/**
 * 派生解密密钥（官方 `defaultCredentialSecret` + `deriveCipherKey`）。
 *
 * ⚠ 逐字复刻官方算法，**不要"规范化"**：
 * - 平台用 `process.platform` 原值（`win32` / `darwin` / `linux`）
 * - 用户名取值失败时回落**字面量** `unknown`
 * - 三段用 `:` 连接，前缀是 `zcode-credential-fallback`
 *
 * 任一处不同都会解出垃圾（GCM 认证失败），表现为「凭据读不出来」。
 */
export declare function deriveCredentialKey(env?: NodeJS.ProcessEnv): Buffer;
/**
 * 解密一个凭据值。
 *
 * 无 `enc:v1:` 前缀时**原样返回**（官方 `decrypt()` 行为）——
 * 这让该函数对「用户手工填入明文」也成立。
 *
 * @throws 密文格式非法 / 密钥不匹配 / GCM 认证失败
 */
export declare function decryptCredentialValue(value: string, env?: NodeJS.ProcessEnv): string;
/**
 * ZCode 账号凭据（全部来自磁盘解密，**不需要实例在跑**）。
 *
 * ⚠ 与初版 `ZcodeCredential`（`{bridge_token, bridge_port}`）**完全不同**：
 * 那个描述「本机桥的访问信息」，这个描述「上游账号身份」。
 */
export interface ZcodeCredential {
    /** ZCode JWT（`zcodejwttoken`）—— 免费额度通道的 `Authorization: Bearer`。 */
    zcode_jwt: string;
    /**
     * 设备标识。
     *
     * 插件登录路径下由 `generateDeviceMid()` **自己随机生成**；
     * 读官方客户端凭据时来自 `~/.zcode/v2/telemetry-state.json` 的 `deviceMid`。
     *
     * ⚠ **必需**：`billing/balance` 等端点缺它会返回
     * `400 {"code":3001,"msg":"parameter error"}`（实测）。
     *
     * ⚠⚠ **它不是账号标识，别拿它去重 / 认账号**：
     * 插件登录时它由我们随机生成（实测其值不被服务端绑定校验），
     * 同一账号**每次重新登录都会得到一个新值** —— 拿它判「是否同一账号」
     * 会把同一账号判成不同账号。
     */
    device_mid: string;
    /**
     * ★ **账号标识**（服务端下发的 `user.user_id`）。
     *
     * ## 为什么必须有（真实缺陷，2026-10-02）
     *
     * 它是**唯一**能判断「两条账号记录是不是同一个账号」的稳定标识 ——
     * 但此前 `startLogin` 组装凭据时**把它丢掉了**（`loginResult.userId`
     * 从未被搬进 credential），于是**无从去重**：同一账号点两次
     * 「添加账号」就得到两条，两条各自消耗额度。
     *
     * ⚠ 与 `device_mid` 的关键差异（**别混用**）：
     *
     * | 字段 | 来源 | 同一账号多次登录 |
     * |---|---|---|
     * | `device_mid` | **我们随机生成** | **会变**（不可作标识） |
     * | `user_id` | **服务端下发** | **不变**（正确的标识） |
     *
     * ⚠ 早期登录的凭据里**没有**这个字段 —— 读取时按 `undefined` 处理，
     * 去重逻辑必须容忍缺失（回退或放弃去重，**不能报错**）。
     */
    user_id?: string;
    /** 大模型 access token（`oauth:bigmodel:access_token`），备用身份。 */
    bigmodel_access_token?: string;
    /** Coding Plan api-key（zai 侧），仅 ultra 通道需要。 */
    coding_plan_key_zai?: string;
    /** Coding Plan api-key（bigmodel 侧），仅 ultra 通道需要。 */
    coding_plan_key_bigmodel?: string;
    /** 展示用标签（脱敏手机号 / 用户名 / 设备码）。 */
    account_label?: string;
    /** 客户端版本，随请求头下发。 */
    app_version?: string;
    /**
     * 凭据来源。
     *
     * - `plugin` = 用户在 Jet Hub 里**插件内登录**拿到的（无需官方客户端）
     * - `ide`    = 解密自官方客户端的 `~/.zcode/v2/credentials.json`
     *
     * 仅用于展示与排查，不参与鉴权。
     */
    source?: 'plugin' | 'ide';
    /** 不可续期（静态凭据；失效需用户重新登录 ZCode）。 */
    refresh_token?: undefined;
}
/** 从 userInfo 里提取展示名（手机号末 4 位 / 用户名 / id 末 6 位）。 */
export declare function labelFromUserInfo(raw: string | undefined): string | undefined;
/**
 * 读设备标识。
 *
 * ⚠ 缺它时上游对 `billing/*` 与推理端点一律回
 * `400 code 3001 parameter error`（实测）—— 故它是**准入门槛的一部分**。
 */
export declare function readDeviceMid(): string | undefined;
/** 兜底的客户端版本（探测失败时用）。 */
export declare const ZCODE_APP_VERSION_FALLBACK = "3.14.3";
/**
 * 探测已安装的 ZCode 客户端版本。
 *
 * 为什么需要：请求头 `X-ZCode-App-Version` 与实际安装版本一致更自然。
 * 取不到时用兜底值 —— **不让版本探测失败连带让 provider 不可用**
 * （版本只是一个头，不该成为硬依赖）。
 */
export declare function detectZcodeAppVersion(): string;
/**
 * 读取**可用**的 ZCode 凭据（来源：官方客户端的凭据文件）。
 *
 * 返回 `undefined` 表示**没有可用的 ZCode 登录态**（凭据文件不存在、
 * 解不开、或缺关键字段）—— 调用方据此让 provider 整体隐藏，
 * 而不是抛错（抛错会在 DSH 界面上多一条 provider 失败记录）。
 *
 * ⚠ 这是**回退路径**。优先路径是 `ctx.credentials` 里的插件自存凭据
 * （见 `ZcodeAuth.current()`）—— 那条路让用户不必安装官方客户端。
 */
export declare function readZcodeCredential(env?: NodeJS.ProcessEnv, filePath?: string): ZcodeCredential | undefined;
/**
 * 从两个来源里挑一个可用凭据。
 *
 * 优先级：**插件自存 > 官方客户端凭据文件**。
 *
 * ## 为什么插件自存优先
 *
 * 「插件内登录」的目标是让用户**不装 ZCode 客户端也能用**。
 * 若反过来（官方优先），一个用户在插件里登录后，只要机器上碰巧有
 * 另一份（可能已失效的）官方凭据，就会被后者覆盖 —— 表现为
 * 「明明刚登录成功，却报凭据失效」。
 *
 * @param stored 插件自存的凭据（来自 `ctx.credentials`；已解析成对象）。
 */
export declare function resolveZcodeCredential(stored: ZcodeCredential | undefined, env?: NodeJS.ProcessEnv): ZcodeCredential | undefined;
/**
 * 凭据是否「够用」。
 *
 * 判据是**上游真正需要的两个字段**：JWT 与 device_mid。
 * 其余（coding-plan key 等）都是可选的 —— 缺了只影响 ultra 通道。
 */
export declare function isUsableZcodeCredential(value: unknown): value is ZcodeCredential;
/**
 * 凭据是否过期 —— **恒为 `false`**。
 *
 * ZCode 凭据是静态的：JWT 的 payload 里**没有 `exp`**（实测只有
 * `{user_id, token_version, sub, iat}`）。真失效时上游回 401/1002，
 * 由适配器归为 AUTH 并提示用户重新登录 —— 不做本地猜测。
 *
 * 保留该函数是为了让适配器无条件调用（与其它 provider 同形），
 * 而不是到处写 `provider === 'zcode'` 的特例。
 */
export declare function isZcodeExpired(_credential: ZcodeCredential): boolean;
/** ZCode 是否可续期 —— 见 {@link isZcodeExpired}，**否**。 */
export declare const ZCODE_REFRESHABLE = false;
//# sourceMappingURL=zcode.d.ts.map