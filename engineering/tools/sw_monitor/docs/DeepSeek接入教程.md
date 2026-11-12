# 给 DeepSeek 的 DSH 接入教程

目标是把外部监控器接到当前工程模式。先在独立环境验证每步，再由 DSH 一次性切换锁权威。图一对应后台等待任务，图二对应现有子代理面板的零件表。以下代码是接入契约和示例，实际宿主版本的会话身份、工具注册及任务完成事件需就地核对。

## 1. 找到运行中的那份插件和任务目录

1. 在实际工程模式进程确认工程 UI 插件、`tools` 和 `DSH_STATE_DIR`。查看 `engineering/plugins/dsh-engineering-ui/lib/index.js` 内的 `findToolsDir()`、`pyEnv()` 和 SW 看护后台 job 示例。仓库副本和安装副本可能同时存在。
2. 确认本轮 `workflow_state.json` 的 `task_id`、`work_dir`。服务的 `--dsh-tools` 指向有这份权威状态、验签端点配置和 `defense_gate.py` 的 tools。若实际运行把权威状态与 tools 分离，先在证据适配器中显式传递同一权威状态路径，再走下面的校验。旧任务状态和仓库示例状态不能混入。
3. 启动桌面外部服务。为工程宿主配置 URL 与 `host.token` 文件路径。令牌只由可信宿主后端读取，浏览器、代理提示词、普通工具输出和命令行参数只传必要的业务标识。默认同一 Windows 用户、一套 DSH 宿主和一个 SW 资源池；启动多个带不同数据库的监控实例会绕开独占。
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

查找这些入口及同类旁路。已知入口会随着项目版本变化，切换前按真实调用链逐一核实。

| 源码位置 | 接入动作 |
|---|---|
| `engineering/tools/mode_gate.py` 的 `cmd_room_start`、`cmd_sw_request`、`cmd_sw_wait`、`sw-release`、`room-end/fail` | 旧取锁、排队、自动让锁和释放转到宿主调用监控器，避免两套 owner。房间登记、状态记录、清理和原有安全检查仍走 DSH。 |
| `engineering/tools/sw_bridge.py` 的 `_gate_sw_lock`、`_gate_sw_unlock`、`_ROOM_REQUIRED_CMDS` 与更早的主入口 | 在实际触碰 COM 之前要求监控器发放命令许可。覆盖 run、drawing、CAD 验证、配合、导出、关闭文档、直接启动 SW，以及主对话、单房间和串行路线。前端提示词只能引导，不能充当拦截。 |
| 其他 COM 路线，例如直接 `swapi.py`、SolidWorks Simulation、由 DSH 创建的 Python 或 PowerShell 子进程 | 统一经可信宿主的执行入口。未能纳管的路线在接管模式下禁止启动。关闭 `DSH_SW_LOCK_OFF` 和无房间自动放行等旁路。 |
| `engineering/plugins/dsh-engineering-ui/lib/job-wait-guard.js` 和工程预设 | 等待任务改由下列宿主适配器创建。既有 job guard 只控制结果等待窗口，不参与排队或释放 SW。 |

许可信息要由宿主与真实会话、实际工具调用和 worker 绑定。若 Python 入口需要校验，使用受宿主控制的凭据通道，并核对宿主为该次命令分配的一次性调用身份；禁止使用代理可自行填写的 room、lease 字符串来模拟该身份。宿主控制所有 CAD 子进程的创建，`begin` 未确认成功则不启动子进程。若失联发生于已启动命令中，宿主停止派新命令，跟踪旧进程直到可证明已排空。

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

### 3.3 每条 SW 命令都核验并记账

```js
const command = randomUUID();
const cmd = { ...scope, lease, command };
await rpc('begin', cmd);            // 先记账，再允许创建实际 CAD worker
// 在受管 worker 内运行 SW 操作，期间上报逐零件事件（见步骤 4）
// 确认 DSH job settled、worker/子进程已退出、该次命令无在途 COM 操作
await rpc('end', cmd);
// 房间后续还要继续使用 SW 时保留 lease，再用新 command 调 begin。
// 房间 SW 阶段已完成，宿主核实没有残留 worker 或未完成的文档操作后：
await rpc('release', { ...scope, lease, drained: true });
```

锁可在纯文件验签、离线校核和台账核实时提前交给下一房间，前提是原房间确实已退出 SW 使用阶段。后续再做几何调整或调用 SW 仿真时重新排队。命令返回但后台 job 尚未 settled、spawn 失败后的子进程存活情况不明、超时、服务掉线时，留在隔离/在途状态，禁止用 `finally` 无条件 `end/release`。收到 `begin` 请求的回复不确定时，严禁启动命令。先核对该命令未启动或已彻底排空，再以原 command 调用 `end`。

主对话使用同一服务等待显式房间名单：

```js
const jobId = monitor.startWaitJob(jobs, {
  owner: rootJobOwner, kind: 'wait-rooms',
  payload: {task: taskId, rooms: ['传动齿轮件', '标准件外购房间']},
});
// ready 表示目标房间已验收并收尾；attention 表示有失败/过期证据要处理。
// DSH turn/end 和 job_output 的窗口到期都不能替代这个结论。
```

## 4. 接入图二的逐零件看板

1. 在实际开始造某个零件时发送 `part-start`。首次发送 `gen:0`，以后使用 `board` 返回的当前 `generation`。服务返回本次新 `gen`。
2. `sw_bridge.run` / `SWModel.save()` 完成、文件已稳定且完成当前零件最后一次保存后，传准确的受管文件路径给 `part-saved`。同一命令可顺序产生多件，每件必须有独立事件，不能把房间报告的某一个文件复制给其他行。失败发 `part-failed`。命令结束仍有未上报保存或失败的零件，监控器记失败。
3. UI 的 `GET /dsh-engineering-ui/parts-board` 原来执行 `mode2_flow.py parts-board`。替换其中的数据读取为宿主 `rpc('board', { task: taskId })`。保留宿主附加的 pending jobs 字段用于排障。前端按返回的 `label`、`state`、`detail`、`checks` 渲染，补齐 queued、validating、blocked、disconnected、stale 等样式，保留 counts.done/total。后台断开时展示不可用或缓存时间。禁止回退到旧 prime-report 判已完成。
4. 模型重建时，先排空并结束旧票。向监控器 `reopen-room` 传 task、room、可选 part。指定 part 会只撤掉它的旧验收并标记需要重建；重建后的 `part-start` 会递增 generation。新凭据必须与新文件和新校核绑定。旧文件如何归档由现有 A/B/C 决策和 DSH 流程处理。

```js
const {gen} = await rpc('part-start', {...cmd, part: partName, gen: currentGeneration});
await rpc('part-saved', {...cmd, part: partName, gen, file: absolutePartFile});
// 若建模失败，改调：
// await rpc('part-failed', {...cmd, part: partName, gen, reason: '草图重建失败'});
```

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
- 建模房间所有零件均通过、外购房间清单通过后，接入者复用原 DSH 收尾处理，包括看图、自检、位姿及其他仍适用的检查。原 `room-end` 如果重复读取房间级最后一份三防线凭据，把该段改为读取监控器当前逐件核实的汇总，不要用 `--force`、`DSH_DEFENSE_BYPASS` 或伪造总理报告。确认旧 SW 清理动作在释放前完成，防止延迟清理关闭下一房间已打开的模型。
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
| begin / end / release | scope + lease；begin/end 加 command；release 加 drained:true。 |
| part-start / part-saved / part-failed | cmd + part + gen；保存加 file，失败加 reason。 |
| review-part / review-room / close-room / verify-task | review-part 传 task、room、part、receipts；review-room 传 task、room、可选 receipts；close-room 传 task、room；verify-task 传 task。 |
| reopen-room / room-failed | task、room；reopen-room 可加 part，room-failed 加 reason。必须先结清票。 |
| wait-rooms | task、显式 rooms 数组，可选 timeout 秒；ready / attention / paused / waiting。 |

故障恢复顺序：

1. 停止发送新 SW 命令。查看真实 DSH jobs、子进程及 SolidWorks 文档/进程，核对旧命令是否仍在保存、重建或通过 COM 操作。
2. 若宿主能确认旧命令和所有 CAD 子进程已彻底排空，用受保护的维护入口执行 `recover` 并写原因。保留同一宿主 epoch 时，队列继续原顺序。换 epoch 时旧等待票作废，由新宿主重新登记，禁止拿旧 lease 继续运行。
3. 连接失败、任务目录对不上、凭据不可读、出现重复 worker 时保持暂停。恢复前不删除 SQLite 来清锁。
4. 回滚时先排空监控模式的所有工作，再停止其宿主入口和后台等待。确认没有活跃监控租约后，才能重新启用 DSH 原锁实现。禁止双轨同时放行。

## 7. 按顺序验收接入提交

先做只读看板影子对照，确认编制、同名件、文件归属和待验收原因。接着在隔离 DSH 配置中切换锁，跑三房间和主对话等待。然后开启实际防线及总装门禁。最后按《覆盖验收.md》登记真实样本、原始日志和结论，达到门槛再打开日常项目接入。
