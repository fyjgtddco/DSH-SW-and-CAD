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

// ── Bug3：选项倒计时 ─────────────────────────────────────────────────────
console.log('\n=== Bug3 · 选项倒计时 120s + 推荐项 + 三题豁免 + 设置开关 ===');
check('倒计时 120 秒（AskCard）', /var COUNTDOWN_SEC = 120;/.test(SRC));
check('主对话注入点也是 120 秒', /installQuestionCountdown\(120\)/.test(SRC));
check('不再残留 10 秒测试值',
  !/COUNTDOWN_SEC = 10;/.test(SRC) && !/installQuestionCountdown\(10\)/.test(SRC));
check('三题豁免判定 isNoCountdownQuestion()',
  /function isNoCountdownQuestion\(q\)/.test(SRC));
check('豁免题含「参数需求强度」', /'参数需求强度'/.test(SRC));
check('豁免题含「搭建方式」', /'搭建方式'/.test(SRC));
check('豁免题含「并行策略」', /'并行策略'/.test(SRC));
check('超时选「推荐」项 pickCountdownOption()',
  /function pickCountdownOption\(q\)/.test(SRC));
check('推荐判定 isRecommendedOption()', /function isRecommendedOption\(o\)/.test(SRC));
check('设置项 enableOptionCountdown 存在', /enableOptionCountdown: true/.test(SRC));
check('设置页文案改成「是否启用选项倒计时」',
  /'是否启用选项倒计时'/.test(SRC));
check('原占位「子栏目选项设置2」已移除', !/'子栏目选项设置2'/.test(SRC));
check('用户点选后立刻停表（cdTakenRef 按题置位）',
  /cdTakenRef\.current\[cdQid\] = true/.test(SRC));
check('设置关闭时主对话倒计时不挂表（enabledByUser）',
  /if \(!isEngMode\(\) \|\| !enabledByUser\(\)\) \{ stopAll\(\); return; \}/.test(SRC));

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
check('选项组优先用 role=radiogroup/group',
  /\[data-question-key\] \[role="radiogroup"\]/.test(SRC));
check('选项优先用 role=radio/checkbox',
  /group\.querySelectorAll\('\[role="radio"\], \[role="checkbox"\]'\)/.test(SRC));
check('提交按钮只在 footerActions 内找（不误点 pager）',
  /_footerActions|_footerActions/.test(SRC) && /findNextButton\(cardRoot\)/.test(SRC));
check('排除「跳过 / 上一题」按钮', /跳过\|skip\|上一题/.test(SRC));

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
check('AskCard 有倒计时句柄 cdTimerRef', /var cdTimerRef = useRef\(null\)/.test(SRC));
check('点击选项时 clearInterval 当场停表',
  /if \(cdTimerRef\.current\) \{ clearInterval\(cdTimerRef\.current\); cdTimerRef\.current = null; \}/.test(SRC));
check('主对话卡片点选也当场停表（stopThis）',
  /var stopThis = function \(\) \{[\s\S]{0,200}clearInterval\(iv\)/.test(SRC));


// ── 第三轮反馈（2026-10-20）：倒计时乱跑 / 折叠默认态 / Markdown ─────────
console.log('\n=== 第三轮 · 倒计时只在真正的题干选项组上 ===');
check('徽标只插在提问卡内部（data-question-key）',
  /_cardRoot = \(group\.closest && group\.closest\('\[data-question-key\]'\)\)/.test(SRC));
check('找不到提问卡就不插（宁可不显示也不乱跑）',
  /不是提问卡 → 直接放弃这组，绝不在页面其它地方乱插/.test(SRC));
check('有孤立徽标巡检 sweepStrayBadges', /var sweepStrayBadges = function \(\)/.test(SRC));
check('attach 前先清乱跑徽标', /sweepStrayBadges\(\);[\s\S]{0,90}var cards = findCards/.test(SRC));
check('选项组要求至少 2 个 radio/checkbox', /optCount < 2/.test(SRC));
check('排除 nav/header/toolbar 等容器', /rc === 'toolbar' \|\| rc === 'navigation'/.test(SRC));
check('三题豁免只读【本卡片题干】extractStemText',
  /var extractStemText = function \(group\)/.test(SRC));
check('豁免不再向上爬 4 层取整页文本',
  !/for \(var up = 0; up < 4 && scope; up\+\+\)/.test(SRC));

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


// ── 第四轮反馈（2026-10-20）：三题豁免失效 / 换题后倒计时消失 ───────────
console.log('\n=== 第四轮 · 三题豁免真正生效 ===');
check('题干提取向上多层查找（不再只取 parentNode）',
  /for \(var up = 0; up < 5 && node; up\+\+\)/.test(SRC));
check('题干只取【选项组之外】的兄弟文本',
  /if \(kid === node\) continue;/.test(SRC));
check('三题豁免仍按题干判定', /if \(\/搭建方式\/\.test\(head\)\) return true;/.test(SRC));

console.log('\n=== 第四轮 · 换题后倒计时不消失（按指纹而非 DOM 元素）===');
check('计时器改为按指纹的对象表', /var timers = \{\};/.test(SRC));
check('接管标记改为按指纹', /var takenMap = \{\};/.test(SRC));
check('新增 questionFingerprint（题干+选项）',
  /var questionFingerprint = function \(group, stemText\)/.test(SRC));
check('元素记录了上次指纹（换题可感知）',
  /var elementFp = new WeakMap\(\)/.test(SRC));
check('换题时清掉旧表与旧徽标',
  /if \(_prevFp !== undefined && _prevFp !== fp\)/.test(SRC));
check('按指纹判定已挂表', /if \(timers\[fp\]\) return;/.test(SRC));
check('按指纹判定已接管', /if \(takenMap\[fp\]\) return;/.test(SRC));
check('点选后按指纹记接管', /takenMap\[fp\] = true;/.test(SRC));
check('同卡片内去重徽标（不出现两个倒计时）',
  /_host.querySelectorAll\('\.eng-qc-cd'\)/.test(SRC));

console.log('\n===== 结果：' + pass + ' 通过 / ' + fail + ' 失败 =====');
if (fail > 0) process.exitCode = 1;
