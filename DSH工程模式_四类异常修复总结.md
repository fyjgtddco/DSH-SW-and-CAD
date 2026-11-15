# DSH 工程模式 · 四类异常修复总结

工程模式（面向 CAD/SolidWorks 机械设计的 DeepSeek Harness Agent 预设）在四工位转台 / 深海工程母机验收中暴露了四类异常，均已做针对性修复并通过回归测试。以下按「问题现象 → 根因 → 修复措施 → 验证」逐项总结，修复细节与现场记录见 engineering/docs/fixes-*.md。

## 一、子代理不受用户直接控制、停止后仍在运行

### 问题现象
- list_agents 看不到 fork 出来的子代理、wait_agent 无进度，主对话误判"已死"后重复 fork，多个子代理同时存活并抢 analyst 身份、反复弹已答过的问题。
- 用户下达停止任务后 interrupt_agent 只是"请求"，子代理手里的下一条建模命令照样执行，停止后仍有后台进程和 SLDWORKS.EXE 残留。
- recover/reset 曾误把排队等 SW 锁的正常小屋判成"僵尸"并清掉。

### 根因
- 平台限制：fork 子代理不是 Team member，不可见、不可寻址、通信单向，主对话无法主动指挥。
- interrupt_agent 不回收进程，工具链也没有全局停机令牌。
- 判活全靠模型猜测（心跳/耗时/手动 sync），容易误判。

### 修复措施
- 主进程脉冲 pulse：DSH 宿主每 10 秒写一次心跳，35 秒未更新才视为过期；子代理被 disposed 才从台账移除；没有 pulse 字段绝不判死。只有"pulse 字段在但停了"且 agent 状态为 disposed 才算确认已死。
- room-board 九态看板 + safe_to_restart：只有 done/failed/dead 允许重启；waiting_sw（排队等 SW 锁）严禁判死/重启；unbound 补 subagent-assign 即可、禁止 reset；detached（崩溃失联）不可 reset 整局。
- fork 前置闸门 analyst-guard：analyst 还活着就拒绝重复创建 / 抢身份。
- ROOT_SESSION_REQUIRED：init/select/recover/restart/reset 等编排命令只许根会话调用，子代理调用一律拒绝。
- 全局停机令牌 _stop.py：STOP_ALL 文件 + DSH_KILL_SWITCH 环境变量双闸，接入五个命令入口、在任何副作用之前检查，停机期间命令返回 TASK_STOPPED；配套 stop-all / resume 命令和标准停机五步（stop-all → interrupt_agent → close-all --quit → sw-kill → 确认无残留）。
- 并发名额闸门每批 ≤3；subagent-assign 绑定校验防重复 fork 覆盖；recover/restart/reset 改走看板不再自猜僵尸。
- 人设/SKILL 铁律：只认 room-board 判活、禁对 running/waiting_sw/idle reset、已停子代理必 interrupt_agent 收尾、建模小屋禁调编排命令。

### 验证
- 回归 34/34：liveness 49/49（误判死亡/重复 fork/重复弹题）、round2 20/20、analyst_gate、room_board、stop_and_gear（停机令牌）等；重启 DSH 后 room-board 可见 pulse.alive=true。
## 二、同一个零件被建很多次

### 问题现象
- 建模入口没有"目标零件是否已存在 + 是否同规格"的前置检查，save() 直接覆盖同名文件；同一零件多版脚本（_v2/_v3/_v4）反复全量重建，parts/ 出现多余件。
- 台账数据源多轮没接对：自动登记缺必填字段、台账路径指到上一任务、run 成功后根本没写台账，查重闸形同虚设。
- 同名二次 save 失败时误把磁盘上的好件（已 PASS 自检+看图+冻结）移进 parts_rejects/；台账还出现过"输出轴上报成平键尺寸"的张冠李戴。

### 根因
- 没有"已完成零件台账"这一层，COM 失败就改脚本重跑、旧件被覆盖。
- 登记时机缺失、数据源不统一、台账路径归属错误。
- SW 的 ActiveDoc 是进程级全局状态，并发驱动同一实例会串写/覆盖。
- "台账有记录但没有可比字段"被旧逻辑折叠成"已建好"。

### 修复措施
- 零件台账 part_registry：JSON 单文件，带 task_id 归属（写路径校验任务一致，不一致拒写）；必填字段+如实 reason；每条修订带 SHA-256 指纹，审计时与磁盘重算比对、不一致即 tampered；调试件（diag_/tmp_ 等）过滤不进交付判定。
- 建模前查重 check_reusable：台账有记录且文件真在磁盘且非空、尺寸/material 一致才判 reuse（跳过建模，提示禁止再建同名件）；缺可比字段判 unknown、绝不视为已建好；规格变了判 rebuild 并要求写明差异。
- 建模后自动登记：run 成功后对每个 .sldprt 取 sha256，材料/bbox/dims 从旁挂文件读，登记到本任务台账；登记失败只记 warning 不把建模判失败。
- 同名二次 save 好件守卫：三条独立证据（冻结台账/自检 PASS/材料凭据）任一命中，好件不得挪入 parts_rejects，只记录本次失败尝试（skipped_good 留痕）。
- 文档身份锁：new_part 只清理自己登记过的未命名文档，别人建的半成品绝不关。
- 总装前 verify-ownership 递归扫描交付目录去重核对，有 duplicates 警告"总装前必须去重"；room-end 冻结本房间产物（sha256+size+只读）；stale-artifacts 把早于本任务纪元的遗留件标 legacy 隔离。
- 外部监控器逐零件看板：part-saved 对同一文件路径做 FILE_ALREADY_ASSIGNED 互斥认领，一个零件不能被两个房间同时认领；verify-task 校验台账集合与编制计划一致、无篡改、无遗留、邻接尺寸一致。

### 验证
- 回归 62/62：b9_part_reuse（复用/重建/unknown 判据）、repro_fixes R1/R2（数据源接对）、v2_repro_fixes（字面路径/中文名/变量路径可提取）、registry_mismatch（张冠李戴拦截）、bug38_ownership（duplicates 检出）等。
## 三、没有齿的"齿轮"被宣布建模成功

### 问题现象
- 减速机任务中两个齿轮房间都返回"建模完成、三道防线 PASS"，但几何里根本没有齿（大齿轮是带轮毂的圆盘、小齿轮轴是阶梯轴+圆柱段）。
- 复检发现断言是 opt-in 的：小屋给无齿圆柱写"圆柱自己的期望"，bbox/体积/特征三段全 PASS，room-end 正常下线。

### 根因
- 完成判据只有"文件存在+凭据+截图"，没有零件定义断言。
- 只验包围盒抓不到无齿件：齿顶圆就是最大外径，同外径圆盘与真齿轮包围盒几乎一样；体积才是决定性判据（真齿比同外径圆盘小 15%~25%）。
- 期望由小屋自写可绕过；"读不到"被折叠成"通过"（属性误用被吞异常导致特征数恒 0、期望 0 恰好判过）。

### 修复措施
- 齿轮几何断言 _verify_gear_geometry 双重回读：齿顶圆回读（包围盒较大边 ≈ da=mz+2m，偏差 ≤0.05mm）+ 体积回读（齿廓解析面积×齿宽−中心孔，实测偏差 ≤2%），无实体即 FAIL。
- 零件自检四段式（bbox/体积/特征白名单/齿轮段）：任何一段被跳过 = INCOMPLETE 绝不 PASS；齿轮段失败不被前段掩盖；特征计数用白名单+属性/方法兼容调用。
- 齿轮类按"零件身份"强制：房间 type=transmission 时全部零件按齿轮类对待（防整房把齿圈画成圆柱），或文件名含齿轮语义标记；self_check.json 必须含 stages.gear 且 ok=true，缺段即 SELF_CHECK_REQUIRED 拦下。
- room-end 门禁链（看图闭环→自检→三大防线），唯一绕行 --force / DSH_DEFENSE_BYPASS=1 并留痕。
- 看图闭环每轮多验体积+特征计数，读不到 = MISMATCH。
- 齿轮建模默认超时从 600s 提到 1800s，超时返回进度断点可续建。

### 验证
- F10~F14 回归：齿轮名+PASS 缺 gear 段被拦（欺骗路径堵死）、gear FAIL 被拦、transmission 房间全零件强制。
- 真机：真齿轮齿顶圆 51.98 vs 52.0、体积 0% 偏差通过；同样外径的无齿圆盘 bbox 通过但体积段 FAIL。
- 全量 62/62。
## 四、子代理过多导致运行内存爆炸

### 问题现象
- 一次 fork 8~12 个小屋同时跑 SolidWorks，测试机内存被打爆。
- SW 看护阈值 85%，但本机内存常年 86~92%，每次取锁自动拉起看护、第一拍就 taskkill SLDWORKS，建模被腰斩无产物；看护杀完自己还退出、保护缺席。
- 并发闸门曾静默失效（第 4 个 fork 竟然成功），也曾反向死锁（历史子代理占满名额导致出图波次开不出来）。
- sleep 轮询占回合：单轮 477 次 sleep(30) 轮询 4 小时，GUI 无反馈像卡死。

### 根因
- 每个房间常驻独立上下文内存；subagent_fork 继承父会话上下文；SW ActiveDoc 是进程级全局状态，并发脚本串写覆盖。
- 看护阈值与本机内存水位冲突导致误杀；闸门契约不匹配、名额把 inactive 也算。
- sleep 轮询占着回合伪装卡死。

### 修复措施
- 并发硬上限：每批最多 3 个（pre-execute 硬闸门，超限返回 SUBAGENT_LIMIT_REACHED），编制超 3 就分批 3+2、本批收工释放名额再开下一批；设置页「内存与并发」可调 1..12；名额只数 running 的后代，inactive 历史子代理不占 slot。
- 波次编排：D 三波式（建模分批→总装→出图）/ E 单小屋串联；wait-rooms 一次性阻塞原语、等待挂宿主后台 job，不占回合。
- SW 资源看护 sw_guard：阈值严格大于 85% 才杀、killed_continue 杀完不退出继续守护、建模中只告警不杀、没 SW 可杀也绝不退出、close-all 三轮清场。
- 外部监控器 DSH-SW-Monitor：SW 单实例使用权 FIFO 排队（票据按 epoch+task+room+session 绑定），忙时返回 job_id 后台等待，未接入 fail-closed 拒绝执行。
- 内存回收：整批子代理收工后对 node/SLDWORKS 做 EmptyWorkingSet 收工作集，不杀进程、不丢上下文。
- 长等待守卫 job-wait-guard：job_output 单次等待切 ≤45s 窗口，到点返回"仍在运行"正常回执，GUI 保持响应；inactive context 降级为工具错误，不打断整轮。

### 验证
- 回归 61/61 + sw_guard_lifecycle（阈值 1000 只采样不杀、验证幂等/存活/pid 复用）+ monitor selfcheck 5/5、e2e 13/13 + 插件单测 mem-guard（名额口径、绝不杀进程）。

## 五、遗留事项
- 零件规格单一真源 part_spec 尚未落地：当前已堵死"无齿圆柱缺齿轮断言"的路径，但小屋仍可能给出错误的齿轮期望（把真齿轮说成别的齿轮），后续用 part_spec 统一下发可根治。
- 装配朝向断言（J2/J4/J5）尚未嵌入总装 room-end。
- 台账 bbox 与磁盘不符目前是 WARN+escalate（必须被看见），尚未升级为硬阻断。

## 六、共性原则
- 四类修复遵循同一套原则：判据从"模型自报"收归"代码事实"；fail-closed（缺判据绝不放行）；绕行必定留痕；单一权威（SW 使用权只认监控器、房间状态只认看板、零件只认台账）；每项修复都带回归测试与修复文档（engineering/docs/fixes-*.md）。

以上为四类异常的修复总结，均已按代码门禁、监控器、人设技能三层落地并通过回归验证。



