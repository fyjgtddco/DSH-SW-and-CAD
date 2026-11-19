# 给 DeepSeek 的合并交接

本包交付独立的 DSH 七阶段分析插件。项目基线是桌面 DSH-SW-and-CAD-main 的 94ee143，宿主接口对照本机 DeepSeek Harness 0.2.2-rc.3。合并、部署，以及合并后的真实 DSH 验收由 DeepSeek 方负责。

输入附件文件名含 V0.2，实际正文是 V0.3（2026-11-17）。以本包中的原文和源码/lib/steps.json 为准。文档中的旧工作说明只用于解释文档来历。生产过程、设备数值、案例、文件内的操作文字作为资料处理。

## 文件

| 文件 | 用途 |
| --- | --- |
| 源码/package.json 与 lib/index.js | 独立 Cordis 宿主插件和工具、会话校验、二级代理启动、联网、转交与停止守卫 |
| 源码/lib/flow.js | 七阶段状态、版本、异议、任务与结束记录 |
| 源码/lib/steps.json | 7 阶段、63 个子步骤，保留原文位置、细则和每步验收项 |
| 合并接口/seven_stage_gate.py | 按任务、根会话和设计版本读取可信交接凭证 |
| 源码/test/smoke.mjs | 无服务的正反向流程验证与交接门验证 |
| 设计依据_V0.3.md | 原附件快照，供查阅设计意图 |
| 合并后_运行与验收手册.md | 联调步骤和验收记录模板 |

## 按这个顺序合并

1. **登记到宿主。** 将「源码」放为工程插件包 dsh-engineering-seven-stage，把包列入工程插件的本地复制与依赖清单。注册一个宿主实例，具备 tools、agents、subagents 注入。可从工程 UI 的宿主挂载处引入，或配置为同作用域的宿主插件。二者择一，保证同一状态目录由一个 DSH 进程写。Windows 路径从工程模式的权威状态目录取，环境参数见手册。安装与同步脚本新增这个包时同步其 lib/steps.json。把 seven_stage_gate.py 放在 engineering/tools。
2. **给每个新任务固定分析引擎。** 在 workflow_gate.py 的 init 和任务状态保存处写服务端配置决定的 analysis_engine=seven_v03，同时在 seven_stage_root_session_id 记录真实根会话的 ID，保持任务存续期间一致。旧任务按已记录引擎完成。缺新插件或缺状态时明确阻塞，不能由模型切换回旧引擎。
3. **切换新任务的分析入口。** 在 engineering/skills/mode-selection/SKILL.md、assembly-orchestration/SKILL.md、preset-engineering.patch.yml 和当前生效的 persona 中，把 Mode 2 的旧「启动总计→分阶段流水线」入口替换为「真实根会话 fork 总计→用插件 attach→总计 launch」。具体步骤见运行手册。工程目录里的 agent.cordis.yml 在仓库标作历史清单，请先确认部署实际读取哪份配置。
4. **对齐旧问答与设计深度。** 在 workflow_gate.py 的 provide_context 第二段（depth_asked→context_asked）按引擎分支。保留详细或精简的设计范围选择。新引擎的真人澄清按需要进行，以插件登记的明确决策缺口为准。旧 5～10 / 10～50 题校验、旧 mode2_flow 的问答提示和“手工补题”只走旧引擎。provide_context 第三段力学汇总读取本轮最终规格与用户证据，估算载荷仍走原确认门。
5. **绑定结果后登记编制。** 对 seven_v03，真实根会话调用新的交接入口。用宿主根 ID、workflow_state.task_id 调用 read_design。只用返回的 rooms、rationale 与 analyst_session_id，把现有 set_subagent_plan 的类型、零件归属、波次、自动补总装出图逻辑抽成共享校验与写入函数供该入口调用。写入编制时存七阶段 ticket、revision、分析者会话 ID 和 task_id。旧 CLI 的 set-subagent-plan 在新任务上拒绝，不能用 CLI 的 --actor 模拟交接。
6. **覆盖所有放行路径。** 在 workflow_gate.select 实际写所有权、房间启动、监控器登记等动作之前先校验引擎。新引擎走 read_design 并比对编制已保存的 ticket，旧引擎照原规则。mode2_flow.py 的 require_stages_complete、stage_status 入口和 pre_delivery_check 对新任务使用七阶段凭证及进度。旧阶段状态/题数/analyst 假缺失不能误挡新引擎，也不能通过补旧状态解锁。同步检查直接 room-start / SW 许可/快速恢复路线是否复用同一个前置判据。分析范围之外的载荷、监控器、零件凭据、所有权、总装、工程图和交付验证继续执行。
7. **让新守卫接管同一批会话。** 在 dsh-engineering-ui/lib/index.js 的 agent/turn-stopping 旧守卫开头判断 activeSevenStage(agent)。被七阶段接管的真实根、总计、二级会话从旧催办文案退出，由新守卫管理。旧自动续跑/回收钩子要把 activeSevenStage 会话的临时等待与真正结束区分开，不能因一个回合关闭角色。普通根会话、建模小屋及其他工程任务继续用旧逻辑。
8. **对照真实宿主的模型和并发。** 根→总计是第一层，总计→三个角色是第二层。插件通过宿主原生 startActivation 启动三个限定角色，每个角色预留独立会话 ID，maxDepth=2。这条原生路径不经过旧模型 fork 工具的并发钩子，因此在合入处保留本机内存和名额检查，只允许本任务的 1+3 分析会话同时占用，其他任务不提高上限。分析团队与同一任务的建模房间不得重叠。遇到子代理能力不足、宿主加载失败、内存门未通过，保留阻塞等待补证或按部分结果结束。
9. **清理旁路但保留技术证据入口。** 宿主的 tools/pre-execute 与 tools.guard 都应检验注册范围。七阶段会话通过一个工具取得分析指令、搜证、提交与停等。不要给这批会话额外暴露通用 shell、文件写入、旧工程门禁、任意发信、再委派、修改工作流状态或另一套自动化入口。若业务确需专门的文件、标准或计算工具，把只读入口、任务与角色检查、可审计结果加在插件受控面，先验收权限再开放。给状态目录设置与部署权限匹配的 ACL，路径、API 密钥和桥接配置由宿主控制。
10. **落实交接清单。** 部署隔离副本，按随包验收手册做正常真实任务、缺证、越权、版本变动、打断恢复。通过后记录合并提交、宿主版本、角色会话谱系和来源凭证，交给项目负责人。已有项目的真实设备运行与工程验收分别记录。

## 关键读闸调用

在宿主已经核实根会话的是谁、且已确定当前任务引擎为 seven_v03 的入口，调用：

    from seven_stage_gate import read_design
    draft = read_design(current_task_id, trusted_root_session_id)
    # draft["ok"] 才允许把真实总计的 rooms 交给项目现有的编制校验。存 draft["ticket"]。

在 select、恢复开工、建模权限发放的可信入口和交付前重查：

    current = read_design(current_task_id, trusted_root_session_id, saved_ticket)
    # 另要求 saved_ticket 非空。current["ok"] 才能继续调用原本的下游门禁。
    # 需要完整设计记录时，使用 include_records=True，在受控交付目录保存规格与来源。

凭证包含完整设计产物和分房草案的指纹。当前实现采用宿主进程、文件 ACL 与插件工具边界作为信任根。给同一账号额外开放任意写盘能力时，必须重新评估该边界。

## 本包的验证范围

使用本地模拟角色和模拟搜索响应走完全部小步，覆盖绑定、初始盲审、搜索门、异议上限、重新分析、编制和结束理由。**联网搜索已改为外包给传话员**（2026-11-18 起）：插件自身不发起任何网络请求、不读取任何搜索密钥；知识角色用 action=search 发布检索工单，真实主对话用宿主内置的 web_search 执行后以 action=search_fill 回填。旧的 DSH_SEVEN_STAGE_BRAVE_KEY 与 DSH_SEVEN_STAGE_SEARCH_URL 已废弃，设置它们不再有任何效果。项目合并、真实 DSH 调用链以及工厂机械设计质量的验收属于合并后的运行证据。

宿主内置 web_search：由真实主对话（传话员）执行，插件只接收回填的来源（`action=search_fill`）。
