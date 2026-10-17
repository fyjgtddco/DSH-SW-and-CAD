/**
 * ZCode 的阿里云 captcha 产出（**唯一还需要浏览器的环节**）。
 *
 * ## 为什么需要它
 *
 * ZCode 免费额度通道（`/api/v1/zcode-plan/anthropic`）强制阿里云 captcha：
 * 缺 `x-aliyun-captcha-verify-param` 时上游回
 * `400 {"code":3007,"msg":"captcha verify failed"}`（实测）。
 *
 * ## 为什么能用普通浏览器而不是 ZCode 那个壳
 *
 * captcha 是**网页 SDK**，不是 Electron 专有 API：
 *
 * ```
 * script:  https://o.alicdn.com/captcha-frontend/aliyunCaptcha/AliyunCaptcha.js
 * config:  window.AliyunCaptchaConfig = { region, prefix }
 * 调用:    initAliyunCaptcha({ SceneId, mode, element, button, getInstance, success, … })
 * 取参:    getInstance 里调 instance.startTracelessVerification()（无感验证）
 *          → success(param) 回调给出 param
 * ```
 *
 * 故任何「有 DOM + canvas + 能跑 JS」的浏览器都行。实测
 * **scoop 的 chromium（headful + 基本 stealth 补丁）** 可稳定产出。
 *
 * ## ⚠ 两个实测得到的硬约束（决定本文件的实现形态）
 *
 * ### 1. 同一个页面**不能**重复 mint
 *
 * 同一 page 上连续 `initAliyunCaptcha` 三次的实测结果：
 *
 * | 次序 | 结果 | 耗时 |
 * |---|---|---|
 * | #1 | ✓ len=280 | 817ms |
 * | #2 | ✗ `F001` | 279ms |
 * | #3 | ✗ `F001` | 296ms |
 *
 * ⇒ SDK 实例状态在页面内不可重复初始化。**每次 mint 必须新建 page target**。
 * 采用该策略后实测 **4/4 成功，中位 1246ms**（浏览器冷启动仅 690ms）。
 *
 * ### 2. `--headless=new` 过不了，必须 **headful**
 *
 * | 模式 | 结果 |
 * |---|---|
 * | `--headless=new`（+ 补丁） | ✗ `fail` / `verifyCode: F001` |
 * | **headful + 补丁** | ✓ 280 字符合法 param |
 *
 * 阿里云风控会看这个差异。headful 在 Windows 上可以**不打扰用户**
 * （`--window-position=-32000,-32000` 移出屏幕）。
 */
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { createServer } from 'node:net';
import { homedir, tmpdir } from 'node:os';
import { isAbsolute, join } from 'node:path';
/**
 * 兜底 captcha 配置。
 *
 * ⚠ 这些值**实测自本机账号**（`GET /api/v1/client/configs?platform=unknown`
 * 的 `data.configs.captcha`）。理论上可能随账号/灰度变化，故
 * `ZcodeAuth.fetchCaptchaConfig()` 会优先向服务端索取，此处仅作兜底。
 */
export const ZCODE_CAPTCHA_FALLBACK = {
    region: 'cn',
    prefix: 'no8xfe',
    sceneId: '11xygtvd',
};
/** 阿里云 captcha SDK 地址（与官方逐字一致）。 */
export const ALIYUN_CAPTCHA_SDK_URL = 'https://o.alicdn.com/captcha-frontend/aliyunCaptcha/AliyunCaptcha.js';
/** SDK 需要的 DOM 宿主 id（与官方 `zcode-aliyun-captcha-*` 一致）。 */
export const CAPTCHA_CONTAINER_ID = 'zcode-aliyun-captcha-container';
/** 挂载点 id。 */
export const CAPTCHA_ELEMENT_ID = 'zcode-aliyun-captcha-element';
/** 按钮 id。 */
export const CAPTCHA_BUTTON_ID = 'zcode-aliyun-captcha-button';
/**
 * captcha 页面必须导航到的**真实 origin**。
 *
 * ⚠⚠ **这是能否重复 mint 的关键**（实测矩阵）：
 *
 * | origin | 同一页面连续 mint |
 * |---|---|
 * | `about:blank` | **1/3**（#2 起 `F001`） |
 * | **`https://zcode.z.ai/`** | **5/5，平均 546ms** |
 *
 * 阿里云 SDK 会读 `location.origin` 参与风控判定，而 `about:blank` 的
 * origin 是字符串 `"null"` —— 于是第二次起被拒。
 *
 * ⚠ 这一条此前被**误判**成「同一页面不能重复 mint，必须每次新建 page」，
 * 导致实现多付了 2.3 倍的耗时（1246ms → 546ms）。
 * 结论修正的代价是：**改一个变量、看结果**比堆假设更快。
 */
export const CAPTCHA_PAGE_ORIGIN = 'https://zcode.z.ai/';
/**
 * captcha param 的合法判据（与桥侧 `isUsableCaptchaParam` 同一套）。
 *
 * 三条全中才算合法：
 * 1. 长度 ≥ **200**（实测合法值 280；降级垃圾约 76）
 * 2. 是 base64 且能解出 JSON
 * 3. 含 `securityToken` 且长度 ≥ **50**（实测合法值 128）
 *
 * 任一条不中即判为降级 —— **不发请求**，省一次注定 3007 的往返。
 */
export function validateCaptchaParam(param) {
    if (typeof param !== 'string' || param.length === 0) {
        return { ok: false, reason: 'captcha param 缺失' };
    }
    if (param.length < 200) {
        return {
            ok: false,
            reason: `captcha param 长度 ${param.length} < 200（疑似 SDK 降级输出，发了必 3007）`,
        };
    }
    let decoded;
    try {
        decoded = Buffer.from(param, 'base64').toString('utf8');
    }
    catch {
        return { ok: false, reason: 'captcha param 不是合法 base64' };
    }
    let parsed;
    try {
        parsed = JSON.parse(decoded);
    }
    catch {
        return { ok: false, reason: 'captcha param 解出的不是 JSON' };
    }
    if (typeof parsed.certifyId !== 'string' || parsed.certifyId.length === 0) {
        return { ok: false, reason: 'captcha param 缺 certifyId' };
    }
    const token = parsed.securityToken;
    if (typeof token !== 'string' || token.length < 50) {
        return {
            ok: false,
            reason: `securityToken 缺失或过短（${typeof token === 'string' ? token.length : 0} < 50）`,
        };
    }
    return { ok: true };
}
/**
 * 浏览器可执行文件的候选位置（按优先级）。
 *
 * ## ⚠ 两个必须防的坑（都实测踩过）
 *
 * ### 1. 空环境变量会产生**相对路径**
 *
 * `join('', 'Google', 'Chrome', …)` 返回 `Google\Chrome\…` —— 一个**相对路径**，
 * `existsSync` 会相对**当前工作目录**解析。于是「当前目录下恰好有个同名文件」
 * 会被误判成浏览器，而真正的浏览器却找不到。
 *
 * ⇒ 只接受**绝对路径**候选（`isAbsolute` 过滤）。
 *
 * ### 2. `USERPROFILE` 不等于家目录
 *
 * scoop 的安装位置在 `~/.scoop` 或 `~/scoop` 下，而 `~` 应当用
 * `os.homedir()` 求（它还会看 `HOME`，且在 `USERPROFILE` 被改写时仍正确）。
 * 用 `process.env.USERPROFILE` 拼路径会在「环境变量异常/被沙箱改写」时失效 ——
 * 实测：把 `USERPROFILE` 指到沙箱后，原本可用的 scoop chromium 就找不到了。
 */
export function findBrowserExecutable() {
    const localAppData = process.env.LOCALAPPDATA ?? '';
    const programFiles = process.env.PROGRAMFILES ?? '';
    const programFilesX86 = process.env['PROGRAMFILES(X86)'] ?? '';
    const home = homedir();
    const candidates = [
        /**
         * ① scoop 的 **显式声明的**根目录优先于默认推导。
         *
         * `SCOOP` / `SCOOP_GLOBAL` 是 scoop 自己的「我装在哪」的权威声明 ——
         * 用户设了它们就说明默认路径不对。若排在 `~/scoop` 之后，会出现
         * 「旧默认路径恰好存在 → 用它，而用户实际在用的那个被忽略」。
         */
        ...(process.env.SCOOP !== undefined && process.env.SCOOP.trim().length > 0
            ? [join(process.env.SCOOP.trim(), 'apps', 'chromium', 'current', 'chrome.exe')]
            : []),
        ...(process.env.SCOOP_GLOBAL !== undefined && process.env.SCOOP_GLOBAL.trim().length > 0
            ? [join(process.env.SCOOP_GLOBAL.trim(), 'apps', 'chromium', 'current', 'chrome.exe')]
            : []),
        // ② scoop 默认根目录（`~/scoop`）。实测可用。
        join(home, 'scoop', 'apps', 'chromium', 'current', 'chrome.exe'),
        // ③ 系统 Chrome。
        join(programFiles, 'Google', 'Chrome', 'Application', 'chrome.exe'),
        join(programFilesX86, 'Google', 'Chrome', 'Application', 'chrome.exe'),
        join(localAppData, 'Google', 'Chrome', 'Application', 'chrome.exe'),
        // ④ Chromium。
        join(localAppData, 'Chromium', 'Application', 'chrome.exe'),
        join(programFiles, 'Chromium', 'Application', 'chrome.exe'),
        // ⑤ Edge（Chromium 内核，系统普遍自带）。
        join(programFilesX86, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
        join(programFiles, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
        join(localAppData, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
    ];
    const override = process.env.ZCODE_CHROME_PATH;
    if (typeof override === 'string' && override.trim().length > 0) {
        candidates.unshift(override.trim());
    }
    for (const candidate of candidates) {
        try {
            /**
             * ⚠ 只接受**绝对路径**：空环境变量产生的相对路径会相对 CWD 解析，
             * 可能误匹配同名本地文件（见函数头第 1 条）。
             */
            if (candidate.length === 0 || !isAbsolute(candidate))
                continue;
            if (existsSync(candidate))
                return candidate;
        }
        catch {
            // 试下一个。
        }
    }
    return undefined;
}
/** 极简 CDP 客户端（用 Node 内置 WebSocket，**零第三方依赖**）。 */
class CdpConnection {
    ws;
    nextId = 0;
    pending = new Map();
    constructor(ws) {
        this.ws = ws;
        ws.addEventListener('message', (event) => {
            let message;
            try {
                message = JSON.parse(String(event.data));
            }
            catch {
                return;
            }
            if (message.id === undefined)
                return;
            const slot = this.pending.get(message.id);
            if (slot === undefined)
                return;
            this.pending.delete(message.id);
            if (message.error !== undefined)
                slot.reject(new Error(JSON.stringify(message.error)));
            else
                slot.resolve(message.result);
        });
    }
    /** 发送一条 CDP 命令并等结果。 */
    send(method, params = {}, timeoutMs = 30_000) {
        const id = ++this.nextId;
        return new Promise((resolve, reject) => {
            this.pending.set(id, { resolve, reject });
            try {
                this.ws.send(JSON.stringify({ id, method, params }));
            }
            catch (error) {
                this.pending.delete(id);
                reject(error instanceof Error ? error : new Error(String(error)));
                return;
            }
            const timer = setTimeout(() => {
                if (this.pending.has(id)) {
                    this.pending.delete(id);
                    reject(new Error(`CDP ${method} 超时（${timeoutMs}ms）`));
                }
            }, timeoutMs);
            timer.unref?.();
        });
    }
    close() {
        try {
            this.ws.close();
        }
        catch {
            // 已关闭。
        }
    }
}
/**
 * 反检测补丁。
 *
 * ⚠ 必须用 `Page.addScriptToEvaluateOnNewDocument` 装 —— 那样它在
 * **每个新文档**上都先于页面脚本执行。只 evaluate 一次不行：
 * 每次 mint 都是新页面（见文件头说明）。
 */
const STEALTH_PATCH = `
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
Object.defineProperty(navigator, 'plugins', {
  get: () => [{ name: 'PDF Viewer' }, { name: 'Chrome PDF Viewer' }, { name: 'Chromium PDF Viewer' }],
});
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
window.chrome = window.chrome ?? { runtime: {}, loadTimes: () => {}, csi: () => {} };
try {
  const originalGetParameter = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function (parameter) {
    if (parameter === 37445) return 'Intel Inc.';
    if (parameter === 37446) return 'Intel Iris OpenGL Engine';
    return originalGetParameter.call(this, parameter);
  };
} catch (error) { /* 内核无 WebGL 时忽略 */ }
`;
const sleep = (ms) => new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    timer.unref?.();
});
/** 构造请求体里那段「建 DOM + 注入 SDK」的表达式。 */
function buildDomExpression() {
    return `document.body.innerHTML =
    '<div id="${CAPTCHA_CONTAINER_ID}" aria-hidden="true">' +
    '<div id="${CAPTCHA_ELEMENT_ID}"></div>' +
    '<button id="${CAPTCHA_BUTTON_ID}">verify</button></div>'; 'ok'`;
}
/** 构造注入 SDK 的表达式（用 `<script src>`，简单可靠）。 */
function buildSdkInjectExpression() {
    return `new Promise((resolve) => {
    if (typeof window.initAliyunCaptcha === 'function') { resolve('already'); return; }
    const script = document.createElement('script');
    script.src = ${JSON.stringify(ALIYUN_CAPTCHA_SDK_URL)};
    script.async = true;
    script.onload = () => resolve(typeof window.initAliyunCaptcha);
    script.onerror = () => resolve('onerror');
    document.head.appendChild(script);
    setTimeout(() => resolve('timeout'), 25000);
  })`;
}
/** 构造「触发无感验证并等 param」的表达式。 */
function buildMintExpression(config) {
    return `new Promise((resolve) => {
    const done = (payload) => resolve(JSON.stringify(payload));
    /**
     * ★ **降级检测**（2026-10-01，对齐官方 \`mnn\` 的 interactive_displayed）。
     *
     * 我们调的是无感验证（\`startTracelessVerification\`）。阿里云若把本设备
     * 判为风险用户，会**静默弹出交互式验证**（滑块/拼图）—— 而官方文档明确说
     * 「该安全策略逻辑不支持自定义，不对外透出」（验证码 2.0 功能相关问题 Q9）。
     * 也就是说**没有回调告诉我们被降级了**，只能自己看 DOM。
     *
     * ⇒ 轮询检测那些交互元素是否出现过，用一个标志记住「曾经出现过」。
     *
     * ⚠ 用「曾经出现」而不是「此刻存在」：交互元素在验证完成后会被 SDK 移除，
     * 只在 success 那一刻查 DOM 会**漏报**（那正是最需要知道的场景）。
     *
     * ⚠ 选择器取自 dsh-free-glm 对空开源版 renderer 的实测记录
     *（\`bench/CAPTCHA-SLIDER-ATTEMPT.md\` 的 2.1 节，四个 id 逐字一致）。
     */
    let sawInteractive = false;
    const detectInteractive = () => {
      try {
        if (sawInteractive) return;
        if (document.querySelector('#aliyunCaptcha-window-popup')
          || document.querySelector('#aliyunCaptcha-sliding-slider')
          || document.querySelector('#aliyunCaptcha-puzzle')) {
          sawInteractive = true;
        }
      } catch (error) { /* 检测失败不影响产出 */ }
    };
    const pollTimer = setInterval(detectInteractive, 200);
    try {
      window.AliyunCaptchaConfig = ${JSON.stringify({ region: config.region, prefix: config.prefix })};
    } catch (error) { done({ stage: 'cfg-throw', err: String(error) }); }
    try {
      initAliyunCaptcha({
        SceneId: ${JSON.stringify(config.sceneId)},
        mode: 'popup',
        element: '#${CAPTCHA_ELEMENT_ID}',
        // ⚠ 必须传**元素对象**：传字符串会报「button参数传入值不合法」。
        button: document.getElementById('${CAPTCHA_BUTTON_ID}'),
        getInstance: (instance) => {
          try {
            if (typeof instance.startTracelessVerification === 'function') {
              instance.startTracelessVerification();
            } else if (typeof instance.show === 'function') {
              /**
               * ⚠ 走 \`show()\` 说明 SDK 里**没有**无感验证能力 —— 那本身
               * 就是一种降级形态（我们请求的是无感，拿到的是强制交互）。
               */
              sawInteractive = true;
              instance.show();
            }
          } catch (error) {
            done({ stage: 'call-throw', err: String((error && error.message) || error) });
          }
        },
        success: (param) => {
          /**
           * ⚠ 收尾前再查一次 DOM（轮询间隔 200ms，可能在两次轮询之间就完成了）。
           */
          detectInteractive();
          clearInterval(pollTimer);
          done({ stage: 'success', param, interactive: sawInteractive });
        },
        fail: (error) => {
          clearInterval(pollTimer);
          done({
            stage: 'fail',
            err: String((error && error.message) || JSON.stringify(error)),
            interactive: sawInteractive,
          });
        },
        onError: (error) => {
          clearInterval(pollTimer);
          done({
            stage: 'onError',
            err: String((error && error.message) || JSON.stringify(error)),
            interactive: sawInteractive,
          });
        },
      });
    } catch (error) {
      clearInterval(pollTimer);
      done({ stage: 'init-throw', err: String((error && error.message) || error) });
    }
    setTimeout(() => {
      clearInterval(pollTimer);
      done({ stage: 'timeout', interactive: sawInteractive });
    }, 60000);
  })`;
}
/**
 * 挑一个**确实空闲**的调试端口。
 *
 * ⚠ 为什么不能随机取（见 `launch()` 里 `this.port` 处的完整缺陷链）：
 * 随机撞上「尚未退干净的旧实例」会让新实例启动失败、而我们连到旧实例上，
 * 最终 `dispose()` 打空 ⇒ 旧实例整棵树泄漏。
 *
 * 做法：先用 `net.createServer` **真的占用**一下候选端口来验证可用性
 * （比查「谁在监听」可靠：既覆盖监听态，也避开刚关闭进入 TIME_WAIT 的端口）。
 *
 * ⚠ 探测与真正启动之间仍有理论竞态，故 `launch()` 里还有第二道
 * 「应答端口是否一致」的校验。
 */
async function pickFreePort() {
    for (let attempt = 0; attempt < 40; attempt += 1) {
        const candidate = 9300 + Math.floor(Math.random() * 500);
        if (await isPortFree(candidate))
            return candidate;
    }
    // 兜底：极端情况下退回随机（后续的端口校验会把问题暴露成明确报错）。
    return 9300 + Math.floor(Math.random() * 500);
}
/** 试占一个端口判断是否空闲（能绑上就算空闲）。 */
function isPortFree(port) {
    return new Promise((resolve) => {
        const server = createServer();
        server.once('error', () => resolve(false));
        server.once('listening', () => {
            server.close(() => resolve(true));
        });
        // 只绑回环：chromium 的调试端口也在回环上。
        try {
            server.listen(port, '127.0.0.1');
        }
        catch {
            resolve(false);
        }
    });
}
/**
 * 常驻浏览器会话。
 *
 * ## 为什么常驻
 *
 * 冷启动实测约 690ms，但**每次请求都冷启动**会让首字延迟凭空多一秒。
 * 而每请求的 mint 开销实测约 1.2 秒 —— 常驻后总开销就在这个量级。
 *
 * ## 生命周期
 *
 * `dispose()` 必须被调用（`ZcodeAuth.stop()` 里做），否则会留下
 * 一个孤儿 chromium 进程（约 200-400MB）。此外还注册了进程退出钩子兜底。
 */
export class ZcodeCaptchaBrowser {
    options;
    child;
    browserWs;
    browserCdp;
    profileDir;
    port = 0;
    starting;
    /**
     * 复用的 captcha 页面（停在 {@link CAPTCHA_PAGE_ORIGIN} 上）。
     *
     * ⚠ 复用而非每次新建：实测 546ms vs 1246ms（快 2.3 倍）。
     * 前提是 origin 必须真实 —— 见 `mint()` 的说明。
     */
    reusablePage;
    /**
     * 页面是否正被某次 mint 占用。
     *
     * captcha param 是**一次性**的，两个并发 mint 共用同一页面会互相踩状态。
     * 故用该标志把取页串行化（并发调用排队，而不是拿到同一个页面）。
     */
    pageBusy = false;
    constructor(options = {}) {
        this.options = options;
    }
    /** 浏览器是否已就绪。 */
    get ready() {
        return this.browserCdp !== undefined && this.browserWs?.readyState === 1;
    }
    /** 启动（幂等；并发调用共享同一次启动）。 */
    async start() {
        if (this.ready)
            return;
        if (this.starting !== undefined) {
            await this.starting;
            return;
        }
        const task = this.launch().finally(() => {
            this.starting = undefined;
        });
        this.starting = task;
        await task;
    }
    async launch() {
        const executable = this.options.executablePath ?? findBrowserExecutable();
        if (executable === undefined) {
            throw new Error('zcode: 找不到可用的浏览器（用于产出阿里云 captcha）。' +
                '已尝试 scoop chromium / Chrome / Chromium / Edge；' +
                '可用 ZCODE_CHROME_PATH 显式指定 chrome.exe 路径。');
        }
        this.profileDir = mkdtempSync(join(tmpdir(), 'zcode-captcha-'));
        /**
         * ★ **必须挑一个确实空闲的端口**（真实缺陷）。
         *
         * 早期是 `9300 + Math.floor(Math.random() * 500)` —— 纯随机、**不检查占用**。
         * 若撞上一个**尚未完全退出的旧实例**的端口，会串成一条隐蔽的失效链：
         *
         * 1. 新 chromium 因端口被占而启动失败（自行退出）
         * 2. 但 `fetch('/json/version')` **成功** —— 应答的是**旧实例**！
         * 3. 于是我们连上了旧浏览器，而 `this.child` 指向那个**已死**的新进程
         * 4. `dispose()` 的 `taskkill` 打在一个死 pid 上 → **毫无效果**
         * 5. 真正在跑的旧实例从此无人回收 ⇒ **整棵树泄漏**（实测 10~14 个进程）
         *
         * 实测证据：`dispose()` 立即接下一轮时**偶发**泄漏（随机端口才撞得上），
         * 而每轮间隔 2.5 秒时 5/5 干净 —— 正是「旧实例还没退干净」的特征。
         *
         * 故改为：在候选端口里**探测到空闲**的那个。
         */
        this.port = this.options.debugPort ?? await pickFreePort();
        const hide = this.options.hideWindow !== false;
        this.child = spawn(executable, [
            `--remote-debugging-port=${this.port}`,
            `--user-data-dir=${this.profileDir}`,
            '--no-first-run',
            '--no-default-browser-check',
            '--disable-background-networking',
            '--disable-sync',
            '--disable-features=Translate',
            '--lang=zh-CN',
            '--window-size=1280,900',
            /**
             * ⚠ **不用 `--headless=new`** —— 实测 headless 一律 `F001`，必须 headful。
             * 为了不打扰用户，把窗口移到屏幕外。
             */
            ...hide ? ['--window-position=-32000,-32000'] : [],
        ], {
            stdio: 'ignore',
            windowsHide: hide,
            /**
             * ⚠ POSIX 上必须 `detached: true`：这样 chromium 自成**进程组**，
             * `killProcessTree()` 才能用负 pid 一次杀掉全部子进程。
             * 不加的话子进程会脱离、变成孤儿（实测留下 12 个 chrome.exe）。
             *
             * ⚠ Windows 不用这个（它有 `taskkill /T`），且 `detached` 在 Windows
             * 上会额外开一个控制台窗口 —— 与「不打扰用户」冲突。
             */
            detached: process.platform !== 'win32',
        });
        const timeoutMs = this.options.readyTimeoutMs ?? 30_000;
        const deadline = Date.now() + timeoutMs;
        let debuggerUrl;
        while (Date.now() < deadline) {
            try {
                const response = await fetch(`http://127.0.0.1:${this.port}/json/version`, {
                    signal: AbortSignal.timeout(2_000),
                });
                if (response.ok) {
                    const version = await response.json();
                    if (typeof version.webSocketDebuggerUrl === 'string') {
                        debuggerUrl = version.webSocketDebuggerUrl;
                        break;
                    }
                }
            }
            catch {
                // 还没起来。
            }
            await sleep(300);
        }
        if (debuggerUrl === undefined) {
            this.kill();
            throw new Error(`zcode: 浏览器调试端口未就绪（${timeoutMs}ms 超时）`);
        }
        /**
         * ★ **确认应答的确实是我们启动的那个浏览器**（防「连到旧实例」）。
         *
         * 端口已经挑空闲的了，这里是**第二道防线**：万一仍有竞态（别的进程
         * 在我们探测之后抢占了端口），`/json/version` 的 `Browser` 值/`webSocketDebuggerUrl`
         * 里的端口会暴露它。启动时 chromium 一定会占住我们给的端口，
         * 故再校验一次 `debuggerUrl` 里的端口号是否等于 `this.port`。
         *
         * ⚠ 不等就**抛错**而不是继续用 —— 继续用会导致「dispose 打空 + 真实例泄漏」
         * 那条隐蔽链（见上面 `pickFreePort` 的说明）。
         */
        if (!debuggerUrl.includes(`:${this.port}/`)) {
            this.kill();
            throw new Error(`zcode: 调试端口 ${this.port} 应答的不是本实例（${debuggerUrl}）—— ` +
                '疑似端口被其它 chromium 占用，已放弃以避免泄漏');
        }
        const ws = new WebSocket(debuggerUrl);
        await new Promise((resolve, reject) => {
            ws.addEventListener('open', () => resolve(), { once: true });
            ws.addEventListener('error', () => reject(new Error('zcode: 连接浏览器调试端口失败')), { once: true });
        });
        this.browserWs = ws;
        this.browserCdp = new CdpConnection(ws);
    }
    /**
     * 产出**一个新鲜**的 captcha param。
     *
     * ## ⚠⚠ 三条实测约束（第三条是 2026-09-29 用户报障后补的）
     *
     * ### 1. 页面 origin 必须是**真实 https**，不能用 `about:blank`
     *
     * | origin | 同页连续 mint |
     * |---|---|
     * | `about:blank` | **1/3**（#2 起 `F001`） |
     * | `https://zcode.z.ai/` | 连续 5/5 |
     *
     * 阿里云 SDK 会检查 origin（`about:blank` 的是 `"null"`），风控据此拒绝。
     *
     * ### 2. 但**空闲约 15 秒后，同一个页面必然失效**
     *
     * **真实缺陷（用户报障）**：登录后发文本成功，接着发图片报
     * ```
     * captcha 产出失败（stage=fail, err={"success":true,
     *   "verifyResult":false,"verifyCode":"F001","certifyId":"3nieVHobAK"})
     * ```
     *
     * 复现矩阵（本机实测）：
     *
     * | 用例 | 结果 |
     * |---|---|
     * | 立即 mint | ✓ 3786ms |
     * | **间隔 15s**（复用页面） | ✗ `F001` 416ms |
     * | 间隔 45s（复用页面） | ✗ `F001` |
     * | 间隔 90s（复用页面） | ✗ `F001` |
     * | **全新浏览器 + 新页面** | ✓ 3692ms |
     * | 同一新页面再 mint | ✓ 464ms |
     *
     * ⇒ 页面**热时复用很快（约 0.5 秒），冷后必失败**。
     * 用户场景正好命中：发文本 → 模型思考 + 用户打字（数十秒）→ 发图片。
     *
     * ⚠ 我上一轮把「每次新建页面」改成「复用」以提速 2.9 倍，
     * 但那 5 次是**秒级连续**测的，没暴露空闲失效 —— **这是一次回归**。
     *
     * ### 3. 修法：热时复用，冷时换新，**失败即换新重试**
     *
     * ```
     * 距上次使用 < idleReuseMs（默认 8s） → 复用（快）
     * 否则                              → 换新页面（对）
     * 任一次 mint 失败（F001 等）        → 丢弃页面、换新重试一次
     * ```
     *
     * ⚠ 空闲阈值取 **8 秒**：实测 15 秒已失效，故留一半余量。
     * 偏保守只会多付一次新页面成本（约 3.7 秒），比 `F001` 让用户看到报错好。
     */
    /**
     * 产出 captcha param，**并报告本次是否被降级为交互式验证**。
     *
     * ## 为什么需要（对齐官方 ZCode 的观测，2026-10-01）
     *
     * 官方闭源版对每次产出结果都会区分并上报（`out/renderer/assets/styles-*.js`）：
     *
     * ```js
     * mnn({ result: e ? 'interactive_displayed' : 'traceless_passed', … })
     * // pnn() 里维护 traceless_passed_count / captcha_displayed_count
     * ```
     *
     * 那是它判断**设备信誉是否在恶化**的手段 —— 而此前我们没有任何这个数，
     * 排查「为什么突然 502 mint failed」时只能靠猜（这正是本次最缺的东西）。
     *
     * ⚠ 与 {@link mint} 的关系：那个是「只要 param」的既有签名（多处调用），
     * 这个是它的**超集**，内部复用同一条产出路径 —— 不要各写一份。
     */
    async mintWithOutcome(config = ZCODE_CAPTCHA_FALLBACK, options = {}) {
        return await this.mintInternal(config, options);
    }
    /**
     * 产出 captcha param。
     *
     * ## 复用策略（⚠ 空闲失效，见本方法上方的实测表）
     *
     * ```
     * 距上次使用 < idleReuseMs（默认 8s） → 复用（快）
     * 否则                              → 换新页面（对）
     * 任一次 mint 失败（F001 等）        → 丢弃页面、换新重试一次
     * ```
     *
     * ⚠ 空闲阈值取 **8 秒**：实测 15 秒已失效，故留一半余量。
     * 偏保守只会多付一次新页面成本（约 3.7 秒），比 `F001` 让用户看到报错好。
     */
    async mint(config = ZCODE_CAPTCHA_FALLBACK, options = {}) {
        return (await this.mintInternal(config, options)).param;
    }
    /** `mint` / `mintWithOutcome` 的共用实现（**唯一**的产出路径）。 */
    async mintInternal(config, options) {
        await this.start();
        /**
         * ⚠ 最多两次：第一次用（可能复用的）页面，失败则**丢掉它换新**再试。
         *
         * 第二次仍失败才上抛 —— 那种情况通常是服务端限频/风控，
         * 换页面也救不了（实测过：全新浏览器也失败）。
         */
        const attempts = 2;
        let lastError;
        for (let attempt = 1; attempt <= attempts; attempt += 1) {
            /**
             * ⚠ 每轮先看中断：用户点了「停止」就不该再开新一轮
             * （否则会出现「停了还在后台 mint」的错觉）。
             */
            if (options.signal?.aborted === true) {
                throw lastError ?? new Error('zcode: captcha 产出已取消');
            }
            const forceFresh = attempt > 1;
            const page = await this.acquirePage(forceFresh, options.signal);
            try {
                const outcome = await this.mintOnPage(page, config);
                page.lastUsedAt = Date.now();
                return outcome;
            }
            catch (error) {
                lastError = error instanceof Error ? error : new Error(String(error));
                /**
                 * ⚠ **失败的页面必须作废**：它已进入坏的 captcha 会话状态，
                 * 留在池里会让下一次也失败（实测：失败页面上再 mint 依然 `F001`）。
                 */
                this.discardPage(page);
                if (attempt < attempts) {
                    // 换新页面前稍微让一下，避免与刚作废的会话竞争。
                    await sleep(300);
                }
            }
            finally {
                /**
                 * ⚠⚠ **必须无条件复位 `pageBusy`**（真实缺陷，2026-09-29）。
                 *
                 * 旧写法是 `if (this.reusablePage === page) this.releasePage(page)` ——
                 * 而 catch 里已经 `discardPage(page)`（它把 `reusablePage` 置空），
                 * 于是这个条件**恒为假**，`pageBusy` 永远停在 `true`。
                 * 下一次 `acquirePage()` 就卡在 `while (this.pageBusy) await sleep(50)`
                 * 里永久自旋 ⇒ 适配器的 `await this.mintCaptcha()` 永不返回，
                 * 请求根本不发出，UI 永远「深度求索中」。
                 *
                 * 故这里补上 else 分支：`pageBusy = false` 原本只出现在
                 * `releasePage` / `kill` 里，而那两条都不是本路径的必然出口。
                 */
                if (this.reusablePage === page)
                    this.releasePage(page);
                else
                    this.pageBusy = false;
            }
        }
        throw lastError ?? new Error('zcode: captcha 产出失败（未知原因）');
    }
    /** 在**指定页面**上跑一次 captcha（不含页面获取/重试逻辑）。 */
    async mintOnPage(page, config) {
        // ⚠ 每次重置 DOM（不重置时实测偶发失败）。
        await page.cdp.send('Runtime.evaluate', { expression: buildDomExpression(), returnByValue: true });
        const injected = await page.cdp.send('Runtime.evaluate', {
            expression: buildSdkInjectExpression(),
            awaitPromise: true,
            returnByValue: true,
        });
        const injectedType = injected?.result?.value;
        if (injectedType !== 'function' && injectedType !== 'already') {
            throw new Error(`zcode: 阿里云 captcha SDK 未加载（${String(injectedType)}）`);
        }
        const minted = await page.cdp.send('Runtime.evaluate', {
            expression: buildMintExpression(config),
            awaitPromise: true,
            returnByValue: true,
        }, 75_000);
        const raw = minted?.result?.value;
        let parsed;
        try {
            parsed = JSON.parse(String(raw));
        }
        catch {
            throw new Error(`zcode: captcha 结果无法解析（${String(raw).slice(0, 160)}）`);
        }
        if (parsed.stage !== 'success' || typeof parsed.param !== 'string') {
            throw new Error(`zcode: captcha 产出失败（stage=${parsed.stage ?? '?'}` +
                `${parsed.err !== undefined ? `, err=${parsed.err}` : ''}）`);
        }
        const verdict = validateCaptchaParam(parsed.param);
        if (!verdict.ok) {
            throw new Error(`zcode: captcha param 不可用（${verdict.reason ?? '未知'}）`);
        }
        /**
         * ⚠ `interactive` 缺失时取 `false`（保守）：我们**只在明确观察到弹窗时**
         * 才认定被降级。宁可不报（少一次误导性告警），也不要把正常无感通过
         * 错报成降级 —— 那会让用户以为信誉出了问题而去做无谓处置。
         */
        return { param: parsed.param, interactive: parsed.interactive === true };
    }
    /**
     * 取一个可用的页面。
     *
     * ## 复用策略（⚠ 空闲失效，见 `mint()` 的说明）
     *
     * ```
     * forceFresh = true                       → 丢弃旧页、建新页
     * 空闲 > idleReuseMs（默认 8 秒）          → 丢弃旧页、建新页
     * 否则                                    → 复用（约 0.5 秒）
     * ```
     *
     * ⚠ 阈值 8 秒的依据：实测**空闲 15 秒必然 `F001`**，故取一半留余量。
     * 偏保守只多付一次建页成本（约 3.7 秒），比让用户看到报错好。
     *
     * ⚠ 串行化：captcha 是**一次性**的，两个并发 mint 共用同一页面会互相
     * 踩状态。故用 `busy` 标志把取页串起来 —— 并发调用会排队，
     * 而不是拿到同一个页面。
     */
    async acquirePage(forceFresh = false, signal) {
        /**
         * ⚠ 等待必须**有界且可取消**（真实缺陷，2026-09-29）。
         *
         * 旧实现是裸的 `while (this.pageBusy) await sleep(50)`：一旦某个持有者
         * 没能复位标志（见 `mint()` 的 finally），这里就是**永久自旋** ——
         * 而它既不看 signal 也没有上限，于是表现为「请求根本不发出 +
         * 用户点停止也无反应」，只能重启宿主。
         *
         * ⚠ 超时**不**复位 `pageBusy`：此刻它属于**另一个**持有者，
         * 越权复位会让两个 mint 同时用同一页面（captcha 是一次性的，必串状态）。
         */
        const waitTimeoutMs = this.options.pageWaitTimeoutMs ?? 30_000;
        const deadline = Date.now() + waitTimeoutMs;
        while (this.pageBusy) {
            if (signal?.aborted === true)
                throw new Error('zcode: captcha 取页等待已取消');
            if (Date.now() >= deadline) {
                throw new Error(`zcode: captcha 页面被占用超过 ${waitTimeoutMs}ms（疑似上一次 mint 未归还）`);
            }
            await sleep(50);
        }
        this.pageBusy = true;
        const idleReuseMs = this.options.idleReuseMs ?? 8_000;
        const existing = this.reusablePage;
        if (existing !== undefined) {
            const idle = Date.now() - existing.lastUsedAt;
            if (!forceFresh && idle <= idleReuseMs)
                return existing;
            /**
             * ⚠ 该换新了 —— **必须先丢弃**（关闭它），否则页面会累积，
             * 且旧的坏会话可能仍有副作用。
             */
            this.discardPage(existing);
        }
        const browser = this.browserCdp;
        if (browser === undefined) {
            this.pageBusy = false;
            throw new Error('zcode: 浏览器未就绪');
        }
        /**
         * ⚠ `createTarget` 必须包在 try 里（真实缺陷，2026-09-29）：它走 CDP，
         * 超时（30s）或浏览器僵死时会**抛错**，而旧代码把它放在 try 之外 ——
         * 抛出后 `pageBusy` 不复位 ⇒ 后续**每一次** mint 都在自旋里死等。
         */
        let targetId;
        try {
            const created = await browser.send('Target.createTarget', { url: 'about:blank' });
            targetId = typeof created.targetId === 'string' ? created.targetId : undefined;
        }
        catch (error) {
            this.pageBusy = false;
            throw error;
        }
        if (targetId === undefined) {
            this.pageBusy = false;
            throw new Error('zcode: 无法新建页面 target');
        }
        let ws;
        try {
            const targets = await (await fetch(`http://127.0.0.1:${this.port}/json/list`, {
                signal: AbortSignal.timeout(5_000),
            })).json();
            const target = targets.find((item) => item.id === targetId);
            if (typeof target?.webSocketDebuggerUrl !== 'string') {
                throw new Error('zcode: 新页面没有可用的调试地址');
            }
            ws = new WebSocket(target.webSocketDebuggerUrl);
            const connectTimeoutMs = this.options.connectTimeoutMs ?? 10_000;
            await new Promise((resolve, reject) => {
                /**
                 * ⚠ **必须有超时**（真实缺陷，2026-09-29）：旧实现只等 `open` / `error`，
                 * 而 Chromium 僵死时**两个事件都不会来** —— 永久挂起；更糟的是
                 * 它不抛错，`pageBusy` 也就不会复位（下一轮直接死锁）。
                 *
                 * ⚠ signal 也要接进来：用户点「停止」时不该继续等建连。
                 */
                let settled = false;
                let timer;
                const cleanup = () => {
                    if (timer !== undefined)
                        clearTimeout(timer);
                    signal?.removeEventListener('abort', onAbort);
                };
                const done = (settle) => {
                    if (settled)
                        return;
                    settled = true;
                    cleanup();
                    settle();
                };
                const onAbort = () => done(() => reject(new Error('zcode: 连接新页面已取消')));
                timer = setTimeout(() => done(() => reject(new Error(`zcode: 连接新页面超时（${connectTimeoutMs}ms）`))), connectTimeoutMs);
                timer.unref?.();
                ws?.addEventListener('open', () => done(resolve), { once: true });
                ws?.addEventListener('error', () => done(() => reject(new Error('zcode: 连接新页面失败'))), { once: true });
                signal?.addEventListener('abort', onAbort, { once: true });
            });
            const cdp = new CdpConnection(ws);
            await cdp.send('Page.enable');
            await cdp.send('Runtime.enable');
            await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source: STEALTH_PATCH });
            /**
             * ⚠ **导航到真实 https origin**（不是 `about:blank`）——
             * 这是能否重复 mint 的关键（实测 1/3 → 5/5）。
             */
            await cdp.send('Page.navigate', { url: CAPTCHA_PAGE_ORIGIN });
            // 等 DOM 可用（domcontentloaded 之后 body 就存在了）。
            await sleep(this.options.navigationWaitMs ?? 1_500);
            const page = { targetId, ws, cdp, lastUsedAt: Date.now() };
            this.reusablePage = page;
            return page;
        }
        catch (error) {
            try {
                ws?.close();
            }
            catch { /* 已关闭 */ }
            this.pageBusy = false;
            try {
                await fetch(`http://127.0.0.1:${this.port}/json/close/${targetId}`, {
                    signal: AbortSignal.timeout(3_000),
                });
            }
            catch { /* ignore */ }
            throw error;
        }
    }
    /**
     * 作废一个页面：关掉它、从池里摘掉。
     *
     * ⚠ **必须真的关闭 target**（不只是清引用）—— 否则页面会累积，
     * 且每个坏页面都占着一份 SDK 实例。
     *
     * ⚠ 关不掉也不抛错：这是清理路径，不该因为清理失败而让 mint 报错。
     */
    discardPage(page) {
        if (this.reusablePage === page)
            this.reusablePage = undefined;
        try {
            page.cdp.close();
        }
        catch { /* 已关闭 */ }
        try {
            page.ws.close();
        }
        catch { /* 已关闭 */ }
        void (async () => {
            try {
                await fetch(`http://127.0.0.1:${this.port}/json/close/${page.targetId}`, {
                    signal: AbortSignal.timeout(3_000),
                });
            }
            catch { /* 关不掉也无妨 */ }
        })();
    }
    /** 归还页面（保留复用）。 */
    releasePage(page) {
        void page;
        this.pageBusy = false;
    }
    /**
     * 终止浏览器进程**树**并清理所有 CDP 连接。
     *
     * ## ⚠ 为什么必须杀**整棵树**（真实缺陷）
     *
     * 早期只写 `this.child?.kill()` —— 那只杀**主进程**。而 Chromium 是
     * **多进程架构**（browser / gpu / renderer / utility 各一个进程），
     * 主进程被 `SIGTERM` 后**子进程会变成孤儿继续运行**。
     *
     * 实测证据（一次被中断的测试后）：
     *   - **12 个** 残留 `chrome.exe` 全指向同一个 `--user-data-dir`
     *   - **28 个**残留的 `zcode-captcha-*` 临时 profile 目录
     *
     * 生产影响：**每次会话泄漏约 200MB**（一个 Chromium 实例），
     * 且残留进程占着调试端口，会干扰后续启动（曾让一次 `max` 档位测试
     * 表现得像「卡住 80 秒」，实为旧实例干扰）。
     *
     * 修法：POSIX 用进程组（`detached: true` + `kill(-pid)`），
     * Windows 用 `taskkill /T /F`（`/T` = 含子进程树）。
     */
    kill() {
        try {
            this.reusablePage?.cdp.close();
        }
        catch { /* 已关闭 */ }
        try {
            this.reusablePage?.ws.close();
        }
        catch { /* 已关闭 */ }
        this.reusablePage = undefined;
        this.pageBusy = false;
        try {
            this.browserCdp?.close();
        }
        catch { /* 已关闭 */ }
        try {
            this.browserWs?.close();
        }
        catch { /* 已关闭 */ }
        this.browserCdp = undefined;
        this.browserWs = undefined;
        this.killProcessTree();
    }
    /**
     * 终止浏览器及其**全部子进程**。
     *
     * ⚠ 不能退回成 `child.kill()`（见 {@link kill} 的说明：会留下孤儿）。
     *
     * ⚠⚠ **必须同步等它做完**（真实缺陷）：早期用异步 `spawn('taskkill', …)`
     * 后立即返回 —— 调用方以为清理完了、紧接着启动下一轮，而旧实例**还活着
     * 占着端口**。若下一轮随机撞到同一端口就会串成：
     * 连到旧实例 → `child` 指向已退出的新进程 → `dispose()` 打空 →
     * **旧实例整棵树泄漏**（实测 10~14 个进程）。
     *
     * 实测对照：`dispose()` 后**立即**下一轮会偶发泄漏；
     * 每轮间隔 2.5 秒则 5/5 干净 —— 正是「没等它退完」的特征。
     *
     * 故改用 `spawnSync`：清理路径阻塞几十毫秒是可接受的，
     * 换来确定性的「返回即已终止」。
     */
    killProcessTree() {
        const child = this.child;
        this.child = undefined;
        if (child === undefined || child.pid === undefined)
            return;
        const pid = child.pid;
        if (process.platform === 'win32') {
            /**
             * ⚠ Windows 没有进程组信号，用 `taskkill /T`（树）+ `/F`（强制）。
             * 必须 `stdio: 'ignore'`：某些沙箱下捕获子进程输出会 EPERM，
             * 而这是清理路径，不该因此失败。
             */
            try {
                spawnSync('taskkill', ['/pid', String(pid), '/T', '/F'], {
                    stdio: 'ignore',
                    windowsHide: true,
                    // 最多等 10 秒（正常几十毫秒；卡住说明系统异常，别无限等）。
                    timeout: 10_000,
                });
            }
            catch { /* 尽力而为 */ }
            // 兜底：万一 taskkill 不可用，至少杀掉主进程。
            try {
                child.kill();
            }
            catch { /* 已退出 */ }
            return;
        }
        /**
         * POSIX：`detached: true` 时子进程自成进程组，负 pid 即整组。
         * ⚠ 失败（ESRCH 等）就退回单进程 kill。
         */
        try {
            process.kill(-pid, 'SIGKILL');
        }
        catch {
            try {
                child.kill('SIGKILL');
            }
            catch { /* 已退出 */ }
        }
    }
    /** 关闭浏览器并清理临时 profile。 */
    dispose() {
        this.kill();
        const dir = this.profileDir;
        this.profileDir = undefined;
        if (dir === undefined)
            return;
        /**
         * ⚠ 删除要**重试几次**：chromium 的子进程刚被 taskkill 掉，
         * 句柄释放有延迟 —— 一次性 `rmSync` 实测会留下 28 个残留目录。
         * 用退避重试（100/500/1500ms），全部失败就交给系统回收临时目录。
         */
        const attempts = [0, 100, 500, 1_500];
        const tryRemove = (index) => {
            if (index >= attempts.length)
                return;
            const delay = attempts[index];
            const run = () => {
                try {
                    rmSync(dir, { recursive: true, force: true });
                }
                catch {
                    tryRemove(index + 1);
                }
            };
            if (delay === 0)
                run();
            else
                setTimeout(run, delay).unref?.();
        };
        tryRemove(0);
    }
}
/**
 * 用户主目录（导出给测试注入用；避免测试真的去碰 `~/.zcode`）。
 *
 * 目前仅用于文档目的 —— 实际路径解析在 `zcode.ts` 的候选表里。
 */
export const ZCODE_HOME = homedir();
//# sourceMappingURL=zcode-captcha.js.map