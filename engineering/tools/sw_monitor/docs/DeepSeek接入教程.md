# 给 DeepSeek 的交接文档 · ClawVM 和 COM 门卫

## 0. 先分清交付包的两侧

桌面的 `DSH-SW-Monitor` 根目录是独立监控器。其下 `工程模式接入包` 是给 `DSH-SW-and-CAD-main` 的新增文件和补丁。当前仓库已经有一版旧监控桥，仍通过 `sw-monitor-permit/done → begin/end` 调用原 SW 执行器。新监控器启动后拒绝这些旧的放行入口。**按下面顺序在隔离分支接入，不能只替换监控器并让旧 DSH 继续建模。**

### A. 应用工程模式补丁

在 DS 的测试副本仓库根目录运行。把第一行换成交付实际位置。

```powershell
$pack = 'C:\Users\j1877\Desktop\DSH-SW-Monitor\工程模式接入包'
$patch = Join-Path $pack '工程模式现有文件.patch'
git apply --check $patch
if ($LASTEXITCODE -ne 0) { throw '补丁与当前版本不匹配，请按变更点逐段合并' }
git apply $patch
if ($LASTEXITCODE -ne 0) { throw '工程模式补丁未应用' }
$from = Join-Path $pack 'engineering'
Get-ChildItem -LiteralPath $from -File -Recurse | ForEach-Object {
    $relative = $_.FullName.Substring($from.Length + 1)
    $target = Join-Path (Join-Path (Get-Location).Path 'engineering') $relative
    if (Test-Path -LiteralPath $target) { throw "新增文件已存在，先人工合并：$target" }
    New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
    Copy-Item -LiteralPath $_.FullName -Destination $target -ErrorAction Stop
}
```

目标仓库快照为 `7f6594f5474b50fdfcc67e7980c3a3c4f027704b`。部署时让工程 UI、压缩插件、Python CLI 和监控器都指向同一套权威状态与已安装 tools。监控器的 `--dsh-tools` 必须包含 `pages_manifest.json` 和 `page_contract.py`。不要从交付目录再复制一份清单到监控器根。若状态和脚本分目录，工程侧使用 `DSH_STATE_DIR`；压缩侧另设 `DSH_MEMORY_TOOLS` 指向实际脚本目录。预算默认 6000，可设 `DSH_MEMORY_COMPACTION_BUDGET`，或按部署配置向压缩实例传 `memoryContract.compactionBudget`。本版预算按 UTF-8 字节计，宿主仍以自己的 token 检查为准。

### B. 确认两侧状态接口

```powershell
python -B .\engineering\tools\memory_gate.py page-status
python -B .\engineering\tools\memory_gate.py page-check --dirty
python -B .\engineering\tools\memory_gate.py page-checkpoint
python -B .\engineering\tools\memory_gate.py compaction-prep --budget 6000
python -B .\engineering\tools\mode2_flow.py stage-status --fidelity structured
python -B .\engineering\tools\workflow_gate.py gate-summary --fidelity pointer
```

- 第一次读取已有任务，磁盘页会标脏。核对工作流、工况、房间和台账已完成写回后，用 `page-checkpoint` 记录该任务的内容指纹。它只提交检查点，不能代替写入缺失业务证据。检查点后的再次改动会重新标脏。
- 监控器的 `page-status` 返回同构的页面和修订号。主侧的检查点不会清除正在建模、待验收和持锁页。待验收的零件先走三道防线；明确放弃的零件按结清票据后的 `room-failed` 留痕处理。准备重置前暂停新任务和状态写入，按项目单任务串行的运维入口做检查点和 reset。
- 隔离恢复先核实旧 SW 和 worker 已结束。执行 `mode_gate.py sw-monitor recover --reason "<真实排空说明>"`，若返回 `DIRTY_PAGES_PENDING`，核对整份清单后再带 `--dirty-rev <revision>` 重试。监控器在提交时重算清单并核对修订。发现新事件后重新读取，禁止通过无版本清单绕过。接管 `attach --drained` 也会检查磁盘及监控器脏页。
- `POST /v1/audit` 使用 `{task, since_rev, limit}`，按 `next_since_rev` 翻页，直到 `has_more=false`。状态变更和事件同事务提交。老数据库只从升级后的第一次提交开始有审计历史。迁移前停机备份 SQLite；不要删库或伪造旧流水。
- 宿主 `GET /dsh-engineering-ui/page-table` 已接既有 `guardRoute`。这是页面元数据；P3 可折叠面板为可选项，当前未加。全局无 task 的监控页只给可信维护路径，普通前端固定当前任务。
- 压缩钩子已挂在仓库实际存在的 `EngineeringCompactionEngine.summarize()`。在压缩前核对预算，返回前重新读取并追加机器生成的页面；任务切换或低于最低保真时取消压缩。只对单个运行中的工程任务启用此模式，不要让多个独立工程会话共用同一状态目录。最终替换仍由父类执行。

### C. Trace、重放与验收边界

仓库确认存在 `ctx.on('tools/execute', (exec,next) => ...)`，因此在同一包装器内记录起点和 `next()` 的返回/异常，不假设有另一个 `tools/result` 事件。写入 `<状态目录>/trace/<task_id>.jsonl`，只记录身份、工具名、参数与结果哈希。**不写新的 Session 事件，不记录参数或结果正文。**

```powershell
python -B .\engineering\tools\trace_replay.py replay --task '<task_id>' --budget 6000 --oracle
```

可把某个任务的 audit 分页结果合并为 `{"events":[...]}` 后传 `--audit-json <文件>`。重复读取和重复构建指标只是候选。COM 成功流水提供建模参数和文件的双摘要。压缩钩子只能证明摘要已准备（`compaction.prepared`）。如果要测压缩后丢页和 oracle，DS 须在已核实的宿主提交点写 `compaction.committed`（真实注入的 coverage、minimum、bytes），在可信读页点写 `page.use`（page ID、session）。没有这些证据时 oracle 返回 UNKNOWN；只有部分观测时返回 PARTIAL。不要把 prepared 补写成 committed，也不要将候选数当覆盖率。真实重放验收时确认 session 归属及被替换的摘要内容。

### D. 切换到 COM 门卫

先在测试部署完成以下第 1–7 节。工程模式补丁接好的是记忆契约、页面与审计接口。DS 还要把旧 `sw-monitor-permit/done` 建模工具换成宿主受信的 `com-build/com-status` 工具，依真实宿主会话绑定房间，再封住任意脚本/COM 入口。不能把 lease、token 或普通终端当门卫入口。旧的装配、工程图等尚无对应固定处理器，启用 COM 门卫时这类路线要明确阻塞，按测试结果逐项接入。

## 1. 找到运行中的那份插件和任务目录

1. 在实际工程模式进程确认工程 UI 插件、`tools` 和 `DSH_STATE_DIR`。查看 `engineering/plugins/dsh-engineering-ui/lib/index.js` 内的 `findToolsDir()`、`pyEnv()` 和 SW 看护后台 job 示例。仓库副本和安装副本可能同时存在。
2. 确认本轮 `workflow_state.json` 的 `task_id`、`work_dir`。服务的 `--dsh-tools` 指向有这份权威状态、验签端点配置和 `defense_gate.py` 的 tools。若实际运行把权威状态与 tools 分离，先在证据适配器中显式传递同一权威状态路径，再走下面的校验。旧任务状态和仓库示例状态不能混入。
3. 按 README 启动桌面外部服务，指定实际的 SLDWORKS.exe 和 DSH tools。已有人工会话须正常保存并退出。为工程宿主配置 URL 与 `host.token` 文件路径。令牌只由可信宿主后端读取，浏览器、代理提示词、普通工具输出和命令行参数只传必要的业务标识。默认同一 Windows 用户、一套 DSH 宿主和一个 SW 资源池；启动多个带不同数据库的监控实例会绕开独占。
4. 检查本机 Node 提供 `fetch`、`AbortSignal.timeout`、`AbortSignal.any`。通过已部署的本机绝对路径加载 `host-adapter.mjs`，或在 DS 自己的接入提交中纳入该文件。确认 `ctx.get('jobs')` 存在。缺少 jobs 服务时先接好宿主依赖，暂停切换。

```js
import { pathToFileURL } from 'node:url';
const { createMonitorHost } = await import(pathToFileURL(adapterAbsolutePath).href);
const monitor = await createMonitorHost({ url: 'http://127.0.0.1:18779', tokenFile });
const { rpc } = monitor;
const jobs = ctx.get('jobs');

// 首次接管前：先停旧队列入口，确认旧命令树、COM 调用和 SW 文档操作已清空。
const attached = await rpc('attach', { drained: true });
if (attached.state !== 'ready') throw new Error('监控器需要隔离恢复');
monitor.startHeartbeat(message => logger.warn(message));
// 插件卸载时调用 monitor.stopHeartbeat()，让旧宿主租约停止续期。
```

## 2. 先登记任务和零件编制

从 `mode2_flow.py` 的 `subagent_plan.rooms[].parts` 读取准确的零件名，再使用 `workflow_gate.py select` 同步后的正式房间编制及稳定的任务 ID。不要根据房间标题推测一个零件是否正在建。 `model` 是实际建模房间，`purchased` 是只交 BOM 的外购房间，`other` 是总装、出图等由 DSH 原门禁收尾的房间。总计分析仍保留，取消仅为轮询看板和验收而开的总理子代理。

```js
await rpc('plan', {
  task: taskId, root: absoluteWorkDir, simulation_required: false,
  rooms: [
    { name: '传动齿轮件', type: 'model', parts: ['DSH_高速轴齿轮轴', 'DSH_中间轴齿轮轴'] },
    { name: '标准件外购房间', type: 'purchased', parts: ['DSH_标准件BOM清单'] },
    { name: '总装与验证', type: 'other', parts: [] },
  ],
});
```

任务目录一轮一目录，零件名需与设计台账键完全一致。模型名在同一任务内唯一。重复发送同一份编制安全，可以追加新的房间。已有房间的名称、类型、零件清单和仿真策略不能静默替换。确实重拆编制时为新任务建立独立工作目录。

## 3. 接管 SW 锁，并让后台等待立即返回

### 3.1 先接入所有能调用 SW 的入口

| 位置 | 接入动作 |
|---|---|
| 工程 UI 宿主、工具分派器 | 注册受信任的建模工具，把真实 task/room/session 注入请求。令牌、ticket 和 lease 留在宿主。模型只能提交参数化的操作清单。 |
| `mode_gate.py` 的 room-start、sw-request、sw-wait、sw-release、room-end/fail | 保留房间登记和收尾；COM 队列只用监控器。旧逻辑不得启动、关闭或抢占 SW。 |
| `sw_bridge.py`、`swapi.py`、`_gate_sw_lock`、直接 COM、PowerShell 宏、Simulation | 旧工具的 AI 调用入口必须切断，统一由门卫执行。门卫工作进程内部仍调用经过配置的本机 swapi.py，不能把该进程和其模块直接暴露给模型。 |
| DSH 自带 SW 健康看护 | 禁止接管模式继续用按进程名杀 SW 的旧看护。新门卫超时回收只针对自己持有的进程句柄。 |

同一 Windows 用户下任意代码仍可直接走 COM。若 DSH 还给 AI 提供通用终端、脚本、宏或任意插件执行能力，必须将这些工具限制在无权调用 SW 的隔离环境，或为这个工程预设禁用这些工具。门卫的白名单只约束经它执行的命令，不能拦截绕过 DSH 的 Windows COM。验收时用主对话、子代理和通用工具分别试绕过。

本版 COM 白名单只实现独立零件建模。装配、工程图、几何读回工具和真实 SW Simulation 需要 DeepSeek 增加受审查的固定处理器及各自的文档释放条件。相关操作在支持之前保留阻塞，避免两套入口并用。非 COM 三防线和台账工作仍可并行。

### 3.2 把图一的命令型等待换成宿主后台任务

```js
import { randomUUID } from 'node:crypto';

// 第一步：工具请求。taskId / room / sessionId 来自 DSH 运行上下文。
async function requestSw({taskId, room, sessionId, actualDshJobOwner}) {
  const scope = { task: taskId, room, session: sessionId, ticket: randomUUID() };
  const result = await rpc('request', scope);
  if (result.state === 'reserved') return { scope, result };
  if (result.state !== 'queued') throw new Error(result.state);
  const jobId = monitor.startWaitJob(jobs, {
    owner: actualDshJobOwner, kind: 'wait-lock', payload: scope,
  });
  return { scope, jobId };
}

// 第二步：仅在快速预留返回或对应 job 已完成后，从宿主继续。
// jobOutput 是由宿主取到的结果正文。scope 来自宿主保存的记录。
async function acceptSw(scope, jobOutput) {
  const result = typeof jobOutput === 'string' ? JSON.parse(jobOutput) : jobOutput;
  if (result.ticket !== scope.ticket || result.state !== 'reserved') {
    throw new Error('当前后台任务没有收到此票的锁预留');
  }
  return rpc('claim', scope); // 返回 lease，保留在宿主
}
```

用真实 DSH job ID 关联 scope，校验结果属于该会话后才调用第二步。保留票号直到终态，不能每次等待重建票。锁由监控器在同一次持久化提交中转给队首；队首 job 的 `done` Promise 随即完成。其他房间的等待 job 和实际建模 job 保持原状态。旧 `sw-wait`/ `wait-rooms` 已在运行时，迁移前由宿主核对精确 job ID、owner 和任务类型，确认只执行等待命令后再用 DSH 原生机制取消，随后注册新等待 job。正常运行使用新 job 的自然结束，避免跨会话强杀。

队首获得 45 秒领取窗口。没领取会过期，随后让下一位领取。已领取的使用权没有自动过期机制。等待 job 被取消时，适配器只取消同一张未领取票。DSH 结束某个房间会话时，也应主动撤销它的未领取票并处理报错。若取消期间服务不可达，宿主恢复后用原票补发 `cancel`，并核对过期或已领取状态。

### 3.3 参数备齐后再申请。每张票执行一个零件

AI 可以先思考 20–30 分钟或更久，期间不要领取 SW，也不要启动 COM worker。模型给出完整零件的操作清单后，宿主再按 3.2 请求 FIFO 票。预留和已领取但未提交的票各只有 45 秒。迟到的请求必须重新排队。正在执行的票只由门卫收尾。监控器运行期间，宿主持续发送心跳。

```js
// scope 和 lease 由上一步宿主取得，均不向 AI 公开。
const command = randomUUID(); // 由宿主创建并持久化，用于网络重试和取回结果
const cmd = { ...scope, lease, command };
const input = {
  ...cmd,
  part: '试件',
  gen: 0,                         // 读 board 的 generation
  file: 'parts/试件-r1.SLDPRT',   // 相对任务根，父目录已存在，目标文件必须不存在
  material: 'Q235',
  steps: [
    { op: 'sketch', args: ['Top Plane'] },
    { op: 'circle', args: [0, 0, 10] },
    { op: 'end_sketch', args: [] },
    { op: 'extrude', args: [8] },
  ],
};
const accepted = await rpc('com-build', input);
if (accepted.state === 'running') {
  const jobId = monitor.startWaitJob(jobs, {
    owner: actualDshJobOwner, kind: 'com-status', payload: cmd,
  });
  // 用 jobId 观察终态；等待任务被取消时，门卫仍管理真实 COM 作业。
}
// 重新提交时使用同 command、同 input，返回原状态，不会重复建模。
```

适配器也提供 `monitor.startComBuildJob(jobs, {owner, task, room, session, recipe})`，供实际宿主工具直接调用。它将排队、领取、重复提交保护和等待结果放进同一个 DSH job。采用此入口时不要再提前申请票。宿主必须从真实会话注入 owner、task、room 和 session；模型只提供 recipe（part、gen、file、material、steps）。

尺寸单位 mm，角度单位度。当前固定操作为 `sketch(Top Plane/Front Plane/Right Plane)`、`circle(cx,cy,r)`、`rectangle(cx,cy,w,h)`、`line(x1,y1,x2,y2)`、`end_sketch()`、`extrude(depth)`、`cut(depth)`、`gear(module,teeth,width,bore)`、`helix_gear(module,teeth,width,helixAngle,pressureAngle,bore)`。内置工作进程调用 DSH 固定 swapi 方法。禁止把任意 Python、路径到脚本、COM 成员名或 COM 对象作为操作参数。

`com-build` 返回 running 只代表入场。真实成功时返回 `state:saved, closed:true, documents:0, pid, sha256, file, gen`。门卫持久化这一结果、更新零件看板并交接 FIFO。同一房间继续建下一件也要领新票，允许原队列更靠前的房间先执行。真实的保存、关闭和释放动作由门卫完成，宿主不发旧 `begin/end/part-saved`。上面的 CAD 输出路径必须为新路径，重建用新版本名，避免覆盖上一版。

### 3.4 超时和恢复规则

- 默认每次 COM 作业最多 45 分钟（启动参数 `--com-timeout`，秒），包括 SW 冷启动、实际 COM、保存和关档。计时从受管工作进程执行开始。坐标计算、排队和等待验收不耗这段时间。
- 模态对话框挂住时，选择超时回收路径。门卫不会对未知弹窗自动点确定。超时先终止并等受管 worker 退出，再用服务启动时保留的 SW 进程句柄终止受管实例，复查进程列表。只有确认回收成功才给下一票。下一件按需重新启动 SW。旧文件如已出现，只记录为未验收文件，不自动覆盖或删除。
- 保存失败、关档未确认、连接到外部实例、多实例、超时回收失败都会暂停交接。保留可人工检查的文档。若故障发生时并无活跃 SW，门卫可直接结算失败并推进下一位。
- 独占期间禁止在该 SW 实例手工编辑文档。超时强制回收会丢失该实例尚未保存的修改。确认过其他实例已退出后才接管；运行中出现新的其他实例时保持隔离。
- 服务重启后失去之前的受管进程句柄，旧 in-flight 作业显示隔离。操作者在 Windows 正常保存并关闭残留实例，确认无旧 worker 后，通过可信宿主 `recover`。系统不按进程名清理。重建失败件可 `reopen-room` 后递交新票、新文件名。
- 主对话等房间仍用 `startWaitJob(jobs, {owner:rootJobOwner, kind:'wait-rooms', payload:{task:taskId, rooms:[...]}})`。ready 代表目标房间全部验收收尾，attention 代表需要处理失败或失效。后台输出窗口到期不是建模完成。

## 4. 接入图二的逐零件看板

1. 直接把 `GET /dsh-engineering-ui/parts-board` 的数据源换为宿主 `rpc('board', {task:taskId})`。读 `sw.owner`、`sw.queue[].position` 显示正在使用者及 FIFO 排位。
2. 前端渲染 `label/state/detail/checks` 和 `summary.counts.done`。建模事件由门卫自己写入。正在运行不等于已保存，已保存不等于通过三防线。宿主掉线时显示不可用或缓存时间。
3. 需要重建时，先结清旧票。调用 `reopen-room` 传 task、room、可选 part。再读当前 generation，排新票并提交新版本文件名。旧文件归档按 DSH 原 A/B/C 决策处理。

## 5. 用监控器接替总理验收

### 5.1 建模件

先用现有 `mode_gate room-artifact` 和 `part_registry` 正常登记归属与设计数据。监控器核对当前任务的 `load_cases/part_registry.json` 内零件名、房间和磁盘路径。正常调用现有 `SWModel.save` 的材料签名、`physics-optimize` 和 `physics-validate-domain`。由同一受信任 DSH 宿主完成验签。不要自行生成带 PASS 的 JSON。

每个零件保存后分别生成一套凭据。物理凭据必须是该零件当前内容的 `part_sha256`，包含受管物理报告、报告 SHA-256 和 run_id。领域凭据需使用该物理运行的相同 run_id。当前 DSH 默认每房间仅写一套文件，**多零件必须在下一件覆盖前归档原始签名字节**，然后以具体路径调用。凭据原样复制，修改字段会破坏签名。物理报告留在签名所指位置；默认接受任务根及该 tools 部署的受管 `output/physics_runs`，额外运行根需由接入者显式加入白名单。验收需要 PASS，材料 WARN 和物理 REVIEW 会给出阻塞原因。

```js
// 放到真实后台校核任务 settled 和其报告稳定写入后的事件处理器
const reviewed = await rpc('review-part', {
  task: taskId, room, part: partName,
  receipts: {
    physics: 'reports/逐件归档/齿轮.physics.json',
    domain: 'reports/逐件归档/齿轮.domain.json',
  },
});
if (!reviewed.passed) notifyRoot(reviewed.blockers);
```

材料凭据按 DSH 原路径读取。物理与领域路径也可省略，省略时取当前任务目录的 `reports/<房间>.physics.json` 和 `reports/<房间>.domain.json`。已有材料/物理/领域签名实现不同的部署，应先对照 `dsh_evidence.py` 用一件真实零件验证字段和签名时钟。签发时间需要在本零件保存事件之后。若签名缺少归属或模型哈希，监控器会阻止完成，先补生产链再验收。

### 5.2 外购件、房间收尾与总装确认

- 外购房间调用 `review-room`，可显式传 `receipts:{bom:'reports/采购房.bom.json'}`。清单要有零件名、正整数数量、规格或标准号。编制是逐项计划时需覆盖每个计划采购件。
- 建模房间所有零件均通过、外购房间清单通过后，接入者复用原 DSH 收尾处理，包括看图、自检、位姿及其他仍适用的检查。原 `room-end` 如果重复读取房间级最后一份三防线凭据，把该段改为读取监控器当前逐件核实的汇总，不要用 `--force`、`DSH_DEFENSE_BYPASS` 或伪造总理报告。COM 模式下由门卫关档并释放，旧 room-end 清理分支不得再关闭或杀死 SolidWorks。
- DSH 确认房间正常结束并且票已结清后，调用 `close-room`。主对话的等待 job 才会得到 ready。DSH 原收尾失败时向监控器保留阻塞。不可恢复失败且票已结清时，可调 `room-failed` 并附原因。
- 总装、出图等 `other` 类型继续走原 DSH 校验。DSH 写入本任务的正常 room-end 后，可调 `review-room` 读回已收尾状态，再调 `close-room`。跳过/强制下线的检测需在宿主事件路由拦截，禁止发送该事件。
- `workflow_gate.py confirm-assembly`、`_check_wave1_done` 和交付收尾中的总理要求，接到 `wait-rooms` 和 `verify-task`。后者核对所有建模及外购房间已收尾、零件修订指纹、邻接尺寸、未解决冲突。最终交付另外检查所有应完成的总装、出图等房间。总装确认与归档每次都用刚返回的结果，并禁止同一期间发起可修改该任务模型的动作。替换原来必须存在总理子代理的编制和报告要求，保留 DSH 其他流程约束及审计。
- 若已验收的模型、凭据、物理报告或对应台账行发生改动，下次看板或门禁读取即变 stale。总装归档前重新读取，不能保存一次 PASS 后永久复用。长时间中断后涉及宿主签名策略更新时，对相关零件重新验收。

```js
await rpc('close-room', {task: taskId, room});
const result = await rpc('verify-task', {task: taskId});
if (!result.passed) throw new Error(result.blockers.join('\n'));
```

### 5.3 将来接真实仿真

当前编制设 `simulation_required:false` 时，看板显示 `NOT_REQUIRED`。未来设为 true 的任务，没有真实验证器会得到 `UNSUPPORTED`，无法标为完成。启动参数 `--simulation-verifier <本机可信 Python 脚本>` 指向 DeepSeek 接好的验证器。服务通过标准输入传入 `task/room/part/root/generation/model/sha256/receipt`。验证器要读取真实运行结果，验证后端、许可证能力、任务与零件归属、当前模型和工况，并在标准输出返回相同身份字段、`status`、`real_run`、`run_id`、`evidence`（任务根内报告路径到 SHA-256 的字典）。没有运行或不受支持时如实返回状态，禁止把调用成功、样例、占位结果或旧版报告当成仿真 PASS。只有配置在服务启动命令中的本机可信验证器可以被执行。

## 6. 接口速查与恢复

所有调用使用 `POST /v1/<op>`、UTF-8 JSON 和宿主 Bearer token。成功传输为 `ok:true`，验收结果另外读 `passed`。JS 适配器会添加 `epoch`。服务的 HTTP 等待窗最大 25 秒，适配器自动续窗；房间/锁发生变化立即通知，不等窗口到点。

| 操作 | 业务参数或返回要点 |
|---|---|
| attach / heartbeat / recover | epoch 由宿主生成。首次 attach 要 drained，recover 另外需 reason。恢复后隔离中的旧 owner 作废。 |
| plan / board | plan 见步骤 2。board 传 task，返回 parts、rooms、sw、summary、pulse_alive、quarantined。 |
| request / wait-lock / claim / cancel | task、room、session、ticket。只有 claim 返回 lease。队首 reserved 不是 SW 命令许可。 |
| com-build / com-status | scope、lease、command；提交加 part、gen、file、material、steps；状态可加 timeout 秒。 |
| release | 提交前放弃已领取的票使用 scope、lease、drained:true。在途作业不能 release。 |
| review-part / review-room / close-room / verify-task | review-part 传 task、room、part、receipts；review-room 传 task、room、可选 receipts；close-room 传 task、room；verify-task 传 task。 |
| reopen-room / room-failed | task、room；reopen-room 可加 part，room-failed 加 reason。必须先结清票。 |
| wait-rooms | task、显式 rooms 数组，可选 timeout 秒；ready / attention / paused / waiting。 |

故障恢复顺序：

1. 停止发送新 SW 命令。查看真实 DSH jobs、子进程及 SolidWorks 文档/进程，核对旧命令是否仍在保存、重建或通过 COM 操作。
2. 若宿主能确认旧命令和所有 CAD 子进程已彻底排空，用受保护的维护入口执行 `recover`，带 `page-status` 返回的完整 `dirty_pages` 和对应 `observed_rev` 并写原因。保留同一宿主 epoch 时，队列继续原顺序。换 epoch 时旧等待票作废，由新宿主重新登记，禁止拿旧 lease 继续运行。
3. 连接失败、任务目录对不上、凭据不可读、出现重复 worker 时保持暂停。恢复前不删除 SQLite 来清锁。
4. 回滚时先排空监控模式的所有工作，再停止其宿主入口和后台等待。确认没有活跃监控租约后，才能重新启用 DSH 原锁实现。禁止双轨同时放行。

## 7. 按顺序验收接入提交

先做只读看板影子对照，确认编制、同名件、文件归属和待验收原因。接着在隔离 DSH 配置中切换锁，跑三房间和主对话等待。然后开启实际防线及总装门禁。最后按《覆盖验收.md》登记真实样本、原始日志和结论，达到门槛再打开日常项目接入。回滚时先排空并停止监控服务，再恢复原插件配置和同版本数据库备份，确保只有一套 SW 放行机制。


## 8. 设计参考

参考 [ClawVM 论文](https://arxiv.org/abs/2604.10352) 的宿主中介与分阶段核验写回。这里的对应做法是先持久化任务、会话、零件版本和作业号，工作进程保存后核对文档身份和文件摘要，确认关档才提交可验收状态和交接。关闭 SolidWorks 文档的语义以 [CloseDoc 官方文档](https://help.solidworks.com/2021/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.ISldWorks~CloseDoc.html) 和 [GetDocumentCount 官方文档](https://help.solidworks.com/2024/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.ISldWorks~GetDocumentCount.html) 为准。记录下来的状态与模型版本可恢复，未保存的 CAD 内存状态在强制退出后无法恢复。
