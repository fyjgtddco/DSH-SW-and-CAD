# S01 规格与实际现场差异

依据：原 `Webots-MCP仿真层-接口规格.md` 的当前 SHA-256 为 `C9AC53304373A14A3CC1D2CFE11F146E62FDE4B79759C5230A8A5C591252E017`；冻结 PLAN.md 不作改写。下表只记录事实、处理责任和验证层级。官方访问日期 2026-10-29，固定 tag 及内容哈希见 environment-index 和控制目录 evidence/S01/official-sources.json。

| ID | 规格/摘要或现场 | 核定事实与处理 |
|---|---|---|
| D01 | 旧 HEAD 1b59378 可当开发起点 | 实际有大量受控脏改和必要未跟踪源码。私有基线捕获真实字节/删除；基线为 50457a841953c26652ba3ad0684b1356ad956b18，不只 checkout 旧 HEAD |
| D02 | output 看似源码目录 | 实为指向用户安装的 Junction；不跟随、不复制。私有基线排除 731 个受控投影和 62 个未跟踪链接路径，原 Git 历史保留；不是源文件丢失或正式部署 |
| D03 | _store 顶部摘要及规格列 DSH_STATE_DIR→ENGINEERING_ROOT→_root→自身 | 实际函数把安装 tools 放在显式 STATE_DIR 之后、ENGINEERING_ROOT 之前；还要求目录已存在并缓存。隔离必须预建且显式传 DSH_STATE_DIR，启动核验实际落点；14 断言探针已复测 |
| D04 | 规格说 WEBOTS_HOME 被环境清洗必然移除 | [官方清洗函数](https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/dsh-v0.2.1-rc.3/packages/subprocess/subprocess/src/index.ts) 只按敏感名或 DSH_* 分类；WEBOTS_HOME 不匹配。仍显式配置以保证复现，但不把“必丢失”当事实 |
| D05 | 加 sim-* 到 PURE_COMPUTE 即可不占 SW | main 先走独立跳锁/房间逻辑，再决定是否 get_sw；取锁还可能起 guard。S07 必须独立保留房间归属并豁免仿真 SW 锁，不用全局 SW_LOCK_OFF 绕开 |
| D06 | Webots connected / 端口监听就等于可仿真 | conn_state.probe_cae 会因安装路径存在而 connected=true；官方 extern 连接还需明确 robot/port。连接区可用与作业 READY 分开；A04 实现真实握手 |
| D07 | simulationQuit 返回或退出码 0 就证明仿真成功/清理 | 退出事实、物理判定和归属进程树退出分别核验；未知结果保留 unknown。不得通过全局结束 webots* 清场。A04 负责核心，S03/S05 供接口与反例 |
| D08 | --minimize/FAST/--no-rendering 可直接等价为低占用 | [R2025a 启动文档](https://raw.githubusercontent.com/cyberbotics/webots/R2025a/docs/guide/starting-webots.md) 分别定义选项，minimize 还有 FAST 行为；文档 mode 枚举自身出现 run 表述差异。本机组合、CPU/GPU/内存由 S03/S06 探针验证，本次不启动来猜结论 |
| D09 | sync -DryRun 应完全无文件变更 | Copy-Tree 先创建缺失目标目录，再分流 DryRun。后续仅对隔离 DSH_HOME/profile 预演；当前脚本尚未排除新 webots_* 状态名，S07 补充后再验收同步 |
| D10 | 全局 Python 存在就有 MCP 与转换器 | 本机全局 3.13.15 无 mcp/urdf2webots，旧 venv 才有 1.30.0/2025.0.0。本次只读取元数据；S03 建独立依赖环境并核查许可证、工具发现与转换 |
| D11 | DSH version 文件就是产品版本 | Z:/DSH/version=44.7.0；PE 与卸载注册表是 0.2.1-rc.3。官方同名 tag 源码成功访问，但未校验 app.asar 内客户端编译字节，不混同官方源码与本机加载实现 |
| D12 | 质量属性可以直接喂给转换 | 当前 sw_bridge 的 massprops 读 vals[2]、质心 vals[4:7]、惯量仅五项；swapi 另一路有不同解析及真实体积×密度 fallback。只保留原始方法/单位来源，由 S02 金样和 A01 具体 API 契约核定，S01 不修算法 |
| D13 | (x,-z,y) 对应绕 X -90°；单位统一 /1000 | 冻结计划已指出符号/入口单位问题；本次不选旋转约定、不二次缩放。交 A01 以非对称盒和真实 SW 对照验收 |
| D14 | 组件名称/文件路径可唯一回写 | 重复实例、配置与重建会改变身份要求；现有台账不是 A02 的持久引用映射。S04 仅承载候选和 missing_information，歧义阻止自动回写 |
| D15 | 截图存在、空接触或改节点位姿可当工程通过 | 这些仅是对应观测，不能替代真实设备驱动/传感器、碰撞覆盖及步长稳定性；留 A04 门槛，结果使用 observed/derived/suspected |
| D16 | 原规格全部“实测”可继承 | 作者实测与历史旧包 11 测试不属于本轮。本次仅源码/官方文档、环境元数据、快照字节与14断言合同探针通过；真实 SW/Webots/DSH/硬件均未验证 |

联网失败记录：GitHub 未认证 tree API 403 rate limit，未取得 tag commit SHA；三条猜测的 subprocess 路径 404，已用实际成功路径替代。9 份目标官方源码/文档均成功归档，未用失败来源推导事实。

以上处理不新增本轮 Astra 实现、正式同步或重启授权。实现与验收仍按原计划阶段责任推进。
