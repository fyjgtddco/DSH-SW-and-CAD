// 本次修复（Bug1~4）的源码级回归测试。
// 目的：这些行为都写在 client.js 里、没有可调用的导出函数，
//   因此按本目录既有做法（读源码 + 结构化断言）锁死关键契约，
//   防止后续重构把它们悄悄改回去。
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const PLUGIN = process.argv[2] || path.join(here, '..');
const SRC = readFileSync(path.join(PLUGIN, 'lib', 'client.js'), 'utf8');

let pass = 0, fail = 0;
const check = (n, ok, d = '') => {
  console.log((ok ? '  ✅ ' : '  ❌ ') + n + (d ? '  → ' + d : ''));
  ok ? pass++ : fail++;
};

// ── Bug1：模型退化哨兵文本 ────────────────────────────────────────────────
console.log('\n=== Bug1 · [System: Empty message content ...] 清理 ===');
check('定义了哨兵正则 PROTOCOL_SENTINEL_RE', /PROTOCOL_SENTINEL_RE/.test(SRC));
check('定义了 stripProtocolSentinel()', /function stripProtocolSentinel\(/.test(SRC));
check('formatMessage 的 text 分支会过滤哨兵',
  /var text = stripProtocolSentinel\(stripAnsi\(ev\.text \|\| ''\)\)/.test(SRC));
check('formatMessage 的 think 分支会过滤哨兵',
  /var think = stripProtocolSentinel\(stripAnsi\(ev\.text \|\| ''\)\)/.test(SRC));
check('主对话 DOM 层也有清理（installSentinelSweeper）',
  /function installSentinelSweeper\(/.test(SRC));
check('DOM 清理只改文本节点，不替换节点（node.nodeValue）',
  /node\.nodeValue = cleaned/.test(SRC));
check('DOM 清理已注册到 ctx.effect',
  /installSentinelSweeper\(\)\;?\s*\},?[\s\S]{0,80}sentinel sweeper/.test(SRC));

// 真实行为验证：抽出来跑一遍
{
  const m = SRC.match(/var PROTOCOL_SENTINEL_RE = new RegExp\(\s*([\s\S]*?)\);\s*\n\s*function stripProtocolSentinel/);
  assert.ok(m, 'PROTOCOL_SENTINEL_RE 无法定位');
  const RE = eval('new RegExp(' + m[1] + ')');
  const strip = (s) => s.replace(RE, '').replace(/\n{3,}/g, '\n\n').replace(/^\s+|\s+$/g, '');
  const sentinel = '[System: Empty message content sanitised to satisfy protocol]';
  check('纯哨兵 → 空串', strip(sentinel) === '');
  check('句尾粘连 → 只留正文',
    strip('[进度: 99%] 运行枚举电机座圆柱面： ' + sentinel) === '[进度: 99%] 运行枚举电机座圆柱面：');
  check('正常文本不被误删', strip('正常文本 [无哨兵]') === '正常文本 [无哨兵]');
}

// ── Bug2：子代理面板 ─────────────────────────────────────────────────────
console.log('\n=== Bug2 · 子代理面板（钉住 / 折叠 / 防闪）===');
check('存在「钉住」状态 pinnedRef', /var pinnedRef = useRef\(null\)/.test(SRC));
check('自动顶替尊重钉住（pinned === cur → return）',
  /var pinned = pinnedRef\.current;\s*\n\s*if \(pinned && pinned === cur\) return;/.test(SRC));
check('手动点选会写入钉住', /pinnedRef\.current = e2\.id/.test(SRC));
check('存在按子代理的日志缓存 logCacheRef',
  /var logCacheRef = useRef\(\{\}\)/.test(SRC));
check('切回时命中缓存、不先清空',
  /var cached = logCacheRef\.current\[selected\]/.test(SRC));
check('滚动只在贴底时跟随（stickBottomRef）',
  /stickBottomRef\.current = gap < 40/.test(SRC) &&
  /if \(!stickBottomRef\.current\) return;/.test(SRC));
check('事件行用 React.memo 包裹（EngEventRow）',
  /var EngEventRow = \(typeof React\.memo === 'function'\)/.test(SRC));
check('info 引用稳定（infoRef，避免 memo 失效）',
  /var infoRef = useRef\(null\)/.test(SRC) && /var info = infoRef\.current/.test(SRC));
check('所有事件类型都可折叠（EngCollapsibleEvent）',
  /function EngCollapsibleEvent\(props\)/.test(SRC));
check('折叠开关渲染 caret 箭头', /eng-evt-caret/.test(SRC));
check('「命令」仍走自己的 ToolCard 折叠', /function ToolCard\(props\)/.test(SRC));

// ── Bug3：选项倒计时【已于 2026-11-08 整体删除】─────────────────────────
// 用户原话：「算了算了，你帮我把倒计时给删了吧，不要这个倒计时了」。
// 删除原因（session-6e5c9b47 现场）：120 秒归零后自动选「推荐项」并提交，
//   用户真实选择还没送出就被默认值顶掉 —— 对工程模式是灾难（载荷/材料/
//   公差被默认值替换，后面整个设计都建立在错误前提上）。
// 因此本组断言【反向锁定】：倒计时不许再被加回来。
console.log('=== Bug3 · 选项倒计时已删除（反向锁定）===');
check('不再有 COUNTDOWN_SEC 常量', !/COUNTDOWN_SEC/.test(SRC));
check('不再有 installQuestionCountdown 装配', !/installQuestionCountdown/.test(SRC));
check('不再有倒计时辅助函数 isNoCountdownQuestion',
  !/function isNoCountdownQuestion/.test(SRC));
check('不再有自动选推荐项 pickCountdownOption',
  !/function pickCountdownOption/.test(SRC));
check('不再有设置项 enableOptionCountdown', !/enableOptionCountdown/.test(SRC));
check('设置页不再有「是否启用选项倒计时」开关',
  !/'是否启用选项倒计时'/.test(SRC));
check('不再注入倒计时样式 .eng-cd', !/\.eng-cd/.test(SRC));
check('「推荐」徽标保留（选项自身属性，与倒计时无关）',
  /function isRecommendedOption\(o\)/.test(SRC));
check('原占位「子栏目选项设置2」已移除', !/'子栏目选项设置2'/.test(SRC));

// ── Bug3 根因：body.dsh-eng-mode 与面板解耦 ──────────────────────────────
console.log('\n=== Bug3 根因 · eng-mode 标记与面板渲染解耦 ===');
check('新增常驻组件 EngModeMarker', /function EngModeMarker\(props\)/.test(SRC));
check('EngModeMarker 注册到 shell.overlay',
  /id: 'eng-mode-marker'/.test(SRC));
check('EngModeMarker 只按 preset 判定，不受面板开关牵连',
  /if \(preset === 'engineering'\) \{\s*\n\s*document\.body\.classList\.add\(ENG_MODE_CLASS\);/.test(SRC));
check('preset 未加载时不摘类（避免进会话闪一下）',
  /preset !== undefined && preset !== null/.test(SRC));
check('SubagentConsole 内不再重复挂这个类',
  !/if \(preset === 'engineering' && engSettings\.useOwnSubagentPane\) \{\s*\n\s*document\.body\.classList\.add\(ENG_MODE_CLASS\)/.test(SRC));

// ── Bug3 DOM 加固（按 0.2.1 真实结构）───────────────────────────────────
console.log('\n=== Bug3 DOM 加固 · 不依赖 hash ===');
// 【倒计时删除后】选项组定位 / findNextButton 等 DOM 辅助随倒计时一起删除了；
//   这些断言原本只为验证"自动点选按钮"的安全性，现已无对应代码。
check('不再有自动点选按钮的辅助 findNextButton',
  !/function findNextButton/.test(SRC));

// ── Bug4：小标签可拖动 ───────────────────────────────────────────────────
console.log('\n=== Bug4 · 收起态「子代理」小标签可拖动 ===');
check('存在拖拽处理 fabOnMouseDown', /var fabOnMouseDown = function \(ev\)/.test(SRC));
check('拖动后吞掉 click（不会一拖就展开）',
  /suppressClickRef\.current = true/.test(SRC));
check('位置持久化（localStorage）', /ENG_FAB_POS_KEY/.test(SRC));
check('限制在视口内 clampFab()', /var clampFab = function \(x, y\)/.test(SRC));
check('双击复位 fabResetPos()', /var fabResetPos = function \(ev\)/.test(SRC));
check('拖动中禁用过渡（.eng-fab.dragging）',
  /'\.eng-fab\.dragging\{'/.test(SRC));


// ── Bug3：点选立刻停表（用户要求"按了选项就立马停"）─────────────────────
console.log('\n=== Bug3 · 点选立刻停表（不再继续读秒）===');
check('AskCard 不再有倒计时句柄 cdTimerRef', !/cdTimerRef/.test(SRC));
check('主对话卡片不再有倒计时表 stopThis', !/var stopThis = function/.test(SRC));


// ── 第三/四轮（2026-10-20）：倒计时相关调试记录【已随功能整体删除】───────
// 用户 2026-11-08 要求把倒计时整体删掉，因此原先这一大批"倒计时不乱跑"
//   的内部实现断言（sweepStrayBadges / questionFingerprint / takenMap …）
//   所对应的代码已不存在。这里改为【反向锁定】，防止有人把倒计时加回来：
console.log('\n=== 倒计时相关实现已全部移除（反向锁定）===');
check('不再有倒计时巡检 sweepStrayBadges', !/sweepStrayBadges/.test(SRC));
check('不再有题目指纹 questionFingerprint', !/questionFingerprint/.test(SRC));
check('不再有指纹计时表 timers = {}', !/var timers = \{\};/.test(SRC));
check('不再有接管标记 takenMap', !/takenMap/.test(SRC));
check('不再有倒计时徽标 DOM 类 .eng-qc-cd / .eng-cd',
  !/eng-qc-cd/.test(SRC) && !/\.eng-cd/.test(SRC));

console.log('\n=== 第三轮 · 折叠默认态（输出展开，其余收起）===');
check('输出(kind=text) 默认展开',
  /var openSt = useState\(props\.kind === 'text'\)/.test(SRC));
check('收起时显示一行摘要 eng-evt-peek', /eng-evt-peek/.test(SRC));

console.log('\n=== 第三轮 · 事件正文按 Markdown 渲染 ===');
check('新增 engSafeMarkdown 包装', /function engSafeMarkdown\(text, keyPrefix\)/.test(SRC));
check('Bubble 走 Markdown', /engSafeMarkdown\(props\.text, props\.mdKey\)/.test(SRC));
check('ThinkBox 走 Markdown', /engSafeMarkdown\(props\.text, props\.mdKey \|\| 'think'\)/.test(SRC));
check('渲染失败回退纯文本，绝不吞内容',
  /catch \(e\) \{\s*\n\s*return String\(text == null \? '' : text\);/.test(SRC));
check('Markdown key 稳定（mdKey 由事件 key 派生）', /mdKey: key \+ '-m'/.test(SRC));

console.log('\n===== 结果：' + pass + ' 通过 / ' + fail + ' 失败 =====');
if (fail > 0) process.exitCode = 1;