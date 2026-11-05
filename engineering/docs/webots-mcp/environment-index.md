# S01 环境与接口证据索引

核查日期：2026-10-29。执行依据为控制目录冻结 PLAN.md，SHA-256 `A004121A5BCA9763481A11C6AB9D2ECAA2D087B78FCB546F6DEB1B69BE2DBADE`。本文件记录本机现场和接入条件，不改变计划范围。

## 源码与隔离基线

| 项目 | 本次核定值 |
|---|---|
| 原源码 | `C:/Users/j1877/Desktop/DSH-SW-and-CAD-main` |
| 原分支 / HEAD | `main` / `1b593782d5d8b5e1dee2d921f566408e367a42ee` |
| 原现场 | 2084 个受控路径，77 个未跟踪路径；受控修改和删除、必要未跟踪源码均存在，不能只从旧 HEAD 开始 |
| 隔离基线提交 | `50457a841953c26652ba3ad0684b1356ad956b18`，`webots/sol-baseline` |
| 集成目录 | `C:/Users/j1877/Documents/Codex/Webots-MCP-20261029/integration` |
| S01 工作树 | 同控制目录 `worktrees/S01`，分支 `webots/s01` |
| S02 工作树 | 同控制目录 `worktrees/S02`，分支 `webots/s02` |
| 工作树工具选择 | 当前聊天 CWD `Z:/DSH` 是安装目录且非 Git 仓库；app create_worktree 无目标仓库 root 参数，list_artifacts 为空。采用 `git clone --no-local --no-hardlinks --no-checkout` + 原生 Git worktree，不共享原仓库对象硬链接或索引 |
| AGENTS | 已检查源码各级父目录、控制目录各级父目录及源码内 AGENTS.md；未发现适用文件 |

捕获保留 1353 条非链接受控记录和 11 条必要未跟踪 `.py`/`.md`（合计 1364 条，包含真实删除记录）；逐文件 SHA-256/不存在状态在原源码、integration、S01、S02 四处一致。原索引 SHA-256 为 `8B500F9F5AB8584C770D100574C9A8B8CD6F458604E3CDA56984382C67A29CF4`，捕获前后未变。

特殊排除：原 `output` 是 Junction，指向 `C:/Users/j1877/.dsh/.agent-presets/engineering` 安装现场。排除其 731 个受控投影和 62 个未跟踪路径，在私有基线中删除历史 output 投影，原 Git 历史仍保留。另排除 4 个未跟踪运行文件；忽略文件、运行目录、巨型输出及链接目标不复制。非链接且原已受控的历史状态文件仅作惰性快照，不能当作隔离运行状态。没有 reset/clean/stash/pop 或递归删除。

全量清单与排除路径见控制目录 `evidence/S01/source-manifest.json`；快照结果及验证分别见 `baseline-result.json`、`baseline-verification.json`。本次没有改动原源码、原 Git index 或安装副本。

## 本机安装与依赖

| 项目 | 路径 / 版本 | 本次证据与边界 |
|---|---|---|
| Python | `C:/Users/j1877/AppData/Local/Programs/Python/Python313/python.exe`；3.13.15，64 位 | 运行解释器读取 sys.version 和发行包元数据；不是 WindowsApps 别名 |
| 全局 Python 包 | pywin32 312；numpy 2.5.2；mcp、urdf2webots 未安装在该解释器 | importlib.metadata；未安装或升级任何依赖 |
| 旧独立环境 | `C:/Users/j1877/Documents/Codex/2026-10-09/ban-2/work/.venv/Scripts/python.exe`；Python 3.13.15，mcp 1.30.0，urdf2webots 2025.0.0 | 当前仅核对元数据；不继承历史测试通过数，不修改此环境。S03 可将这两个版本作为独立环境候选，经当前 smoke check 后再冻结 |
| Webots | `D:/Webots`；R2025a | resources/version.txt 与卸载注册表一致；未执行 Webots |
| Webots 启动器 | `D:/Webots/msys64/mingw64/bin/webots.exe` | 文件存在且已记录 SHA-256 |
| extern 启动器 | 同目录 `webots-controller.exe` | 文件存在；其参数和 SDK 路径已查官方 R2025a 文档；尚未真实连接 |
| Python controller SDK | `D:/Webots/lib/controller/python/controller/__init__.py` | 文件存在；本次未 import controller、构造 Supervisor 或启动物理引擎 |
| SolidWorks | `Z:/Program Files/SOLIDWORKS Corp2025/SOLIDWORKS/SLDWORKS.exe`；产品 SOLIDWORKS 2025，PE 33.5.0.0053 | 注册表称 2025 SP05，但注册表不同项还报 33.150.0053/33.5.0.53，原值全部保留；未启动 SW、未连 COM、未验证许可/导出器兼容 |
| DSH Desktop | `Z:/DSH/DeepSeek Harness.exe`；PE FileVersion 0.2.1-rc.3，ProductVersion 0.2.1.0 | 注册表显示 0.2.1-rc.3；`Z:/DSH/version` 是 44.7.0，不能当 DSH 产品版本 |
| 工程插件 | `engineering/plugins/dsh-engineering-ui/package.json`；1.2.0 | 源码要求 Node >=22，DSH >=0.2.0-rc.1 <0.3.0；未证明所有 API 对当前宿主可用 |

仅记录允许的路径环境：当前 `WEBOTS_HOME=D:/Webots`；`DSH_HOME`、`DSH_STATE_DIR`、`DSH_ENGINEERING_ROOT` 均未显式设置。未读取或输出任意密钥/令牌值。二进制、版本文件和依赖元数据的完整证据见 `evidence/S01/environment.json`。

## 接口入口定位

以下均为隔离快照的相对路径；精确字节哈希以 source-manifest 为准，行号仅作为本基线导航。

| 入口 | 当前位置 / 实际作用 |
|---|---|
| 状态根 | `engineering/tools/_store.py:133`，state_dir；缓存、只认已存在目录、安装副本优先细节见 integration-contract |
| 工程根 | `engineering/tools/_root.py:148`，engineering_root；不能代替 state_dir |
| 连接区存储 | `engineering/tools/conn_state.py:67`，state_path；Webots 是 process/install availability，非物理会话 READY |
| Webots 探测 | `conn_state.py:837`，probe_cae；`connected` 可因存在安装而为 true |
| 桥接入口 | `engineering/tools/sw_bridge.py:6355`，main；命令分类、房间识别、取锁、get_sw、分发必须分开核对 |
| SW 锁 | `sw_bridge.py:6234`、`:6328`；`mode_gate.py:5975`/`:6073`/`:6262` 的 sw-request/sw-wait/sw-release |
| UI 状态根 | `engineering/plugins/dsh-engineering-ui/lib/index.js:729`，findToolsDir；显式 DSH_STATE_DIR 与 Python 对齐 |
| UI Python 环境 | 同文件 `:2865`，pyEnv；连接探测按 target 单独传入，UTF-8 环境显式设置 |
| 当前有效 preset | `engineering/plugins/dsh-engineering-ui/preset-engineering.patch.yml`，config.plugins；旧 engineering/agent.cordis.yml 仅作历史兼容材料 |
| 同步 | `sync-to-dsh.ps1`，代码/技能/文档/preset/各 profile 插件；有运行文件排除和逐文件备份，DryRun 仍可能建目录 |
| 零件台账 | `engineering/tools/part_registry.py`，registry_path/load/save；可承载来源 ID，不能替代 A02 持久身份算法 |
| 质量读取 | `sw_bridge.py:1873` 与 `swapi.py:5238`；数组解析不一致，留 A01 校准，不从现输出直接生成可信惯量 |

## 官方联网核查

访问日期均为 2026-10-29；证据级别为“官方文档/固定 tag 源码只读核对”，不是本机运行证据。所有成功返回内容已存控制目录 `evidence/S01/official/`，URL、时间和 SHA-256 见 `official-sources.json`。

- [DSH MCP 配置与命名，dsh-v0.2.1-rc.3](https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/dsh-v0.2.1-rc.3/packages/mcp/mcp-client/src/index.ts)：确认 stdio、namespace、显式 command/args/cwd/env 及非致命启动配置。
- [DSH transport](https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/dsh-v0.2.1-rc.3/packages/mcp/mcp-client/src/transport.ts)、[环境清洗定义](https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/dsh-v0.2.1-rc.3/packages/subprocess/subprocess/src/index.ts)、[重连](https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/dsh-v0.2.1-rc.3/packages/mcp/mcp-client/src/connection.ts)：显式 env 在清洗之后合并；宿主重连不能证明动作可重试。
- [Webots R2025a extern controller](https://raw.githubusercontent.com/cyberbotics/webots/R2025a/docs/guide/running-extern-robot-controllers.md)、[启动选项](https://raw.githubusercontent.com/cyberbotics/webots/R2025a/docs/guide/starting-webots.md)：核对 Windows launcher、robot/port 选择及 rendering/minimize/mode 的边界。
- [MCP 2025-11-25 stdio](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)、[取消](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/cancellation)：stdout 仅协议，取消存在竞争窗口，不能等同物理撤销。

GitHub 未认证 tree API 返回 403（rate limit）；三条猜测的 subprocess 路径返回 404，已改用上面成功访问的精确路径。未使用密钥绕过。tag 文件已逐个捕获，但 tag 对应完整 commit SHA 未由 API 核实；独立安装的 MCP client 编译字节未从 app.asar 取出核对，不能声称本机客户端与 tag 完全相同。

## S01 验证范围

`evidence/S01/contract_probe.py` 的 14 条断言核对状态解析优先级、目录缓存、已有连接命令分类、SW 锁独立性、UI 环境、同步 dry-run 副作用和新运行文件尚未排除的事实。只读 `_store.state_dir()`，仅在自身证据目录创建空 probe-state；未迁移状态、未调用 sw_bridge 主入口。

`capture_baseline.py --verify` 核对全部捕获文件、原索引和冻结计划。现有完整回归可能涉及进程/状态动作，本次没有运行；没有构建、SW/Webots 物理、DSH 全链或真实硬件通过结论。
