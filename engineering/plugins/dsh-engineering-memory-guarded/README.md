# dsh-engineering-memory-guarded

工程模式专用**受约束记忆压缩器**。只在 `engineering` 预设下加载，替换官方的
`@deepseek-ai/dsh-compaction-basic`（以及上一代 `dsh-engineering-compaction`）。

## 一句话

**压缩不靠模型写摘要，靠尺寸依赖图 + 审核提交；压缩历史一条不丢，最新在最上面。**

## 它解决什么

长会话压缩有两个老问题：

1. **摘要失真**：模型写摘要时把 `φ24H7` 写成"约 φ24"、把"d=24 所以 r=12"这条**推导关系**丢掉，
   下一轮又拿 20 当半径去建模。
2. **不可验证**：摘要落盘之后，没人能证明"模型看到的检查点"就是"当时审核通过的那份"。

本插件把这两件事变成结构：

| 闸 | 机制 |
|---|---|
| ① 摘要由**插件**算 | 确定性快照：活动尺寸约束 + 派生值 + 用户原文页。模型没有写摘要的入口 |
| ② 依赖**必须**声明 | `derive` 必须带 `parents`；上游一改，全部下游**级联撤下**，不会留旧值 |
| ③ 提交要**独立审核** | 重新打开已落盘会话，核对 `compaction/start → summary → 实际替换 user/message → 无错 end` 四段齐全 |
| ④ 发模型前**再验哈希** | 每个检查点的 `sha256(content)` 与 pin 记录逐字比对，不一致直接拒 |
| ⑤ 断点**可恢复** | `pending` + `agent.previous.json`；重开会话自动重做审核，绝不在中间态放行 |

## 与上一代压缩器的关系

**互斥，不是叠加。** 同一个上下文只能有一个 `ctx.compaction`，所以 preset 里只能挂一个。

| | 上一代 `dsh-engineering-compaction` | 本插件 |
|---|---|---|
| 摘要 | LLM 分层摘要 + L0 数值抽取/强制回填 | 不调模型，确定性快照 |
| 保住什么 | 大量机械数值（逐字） | 尺寸真相 + 依赖关系 + 用户原文 |
| 改尺寸 | 只是"数字变了" | 级联撤下下游派生值 |
| 可验证 | 无 | 提交审核 + 读前哈希 |
| 崩溃恢复 | 无 | 有 |
| 账本 | `compaction_history.jsonl` | `compaction_history_v2.jsonl` |

> ⚠️ **换后端不丢历史**：UI 同时读两份账本并合并按时间倒序，
> 所以换之前的历史一条不少，只是不再新增。

## 只在工程模式生效

```yaml
# preset-engineering.patch.yml 的 compaction 组
- id: compaction
  name: cordis:group
  group: true
  isolate:
    compaction: true
    toolResultPruner: true
    engineeringCompactionAudit: true   # 审核插件与引擎同一隔离作用域
  config:
    - id: compaction-engineering
      name: 'dsh-engineering-memory-guarded'
```

- preset 子项 = 只在 engineering 会话上下文实例化 → 标准/极简模式读的是它们自己的
  `compaction-basic`，**绝不串台**；
- 本包的 `cordis.patch.yml` 刻意保持 `- insert: []`，**绝不进宿主层**；
- 也不能进 `dsh.profile.bundles`（宿主层挂载会与 preset 实例重复注册）。

## 模型能看到什么

只有一个只读工具 + 一个申请入口：

```json
{ "action": "read" }                                  // 看当前尺寸依赖图
{ "action": "correct", "id": "...", "value": "..." }  // 只能更正用户本轮明确给出的值
{ "action": "derive", "id": "...", "value": "...", "parents": ["..."] }
{ "action": "invalidate", "id": "..." }               // 撤下算错的派生值及全部下游
```

- `read` 返回 `{version, nodes, edges}`，边方向 `source → target`（输入指向派生）；
- `correct` 的用户证据来自**用户原文**：没有 `!尺寸 x = y` 这类明确输入就拒绝；
- 用户约束（`source: user`）**不允许**被 `derive` / `invalidate` 顶替。

## 用户怎么用

```
!尺寸 shaft.diameter = 20 mm           ← 明确登记
请把 shaft.diameter 改为 24 mm。        ← 同等效力
!尺寸 a.d = 10 mm\n!尺寸 b.len = 30 mm  ← 一条消息多项
我要换尺寸                              ← 只表达意图：暂停工程工具，等补全 ID/值/单位
!继续原尺寸                             ← 收回变更
```

改上游后，旧值与所有后代从活动图里消失；历史备份仍在，可回滚。
**已经写进 CAD 文件的几何不会自己变** —— 必须重新建模、复核、保存。

## 压缩历史（图一 / 图二那两条黄圈）

每次压缩成功追加一行到：

```
<state_dir>/compaction_history_v2.jsonl
```

```json
{"schema":2,"engine":"dsh-engineering-memory-guarded","at":1762500000000,
 "target":"<会话id>","root":"<根会话id>","label":"建模小屋-输入轴",
 "constraints":3,"input_chars":48000,"output_chars":9000,"saved_chars":39000,
 "hash":"<sha256>","summary_excerpt":"活动约束 3 条：…"}
```

- `target` = 具体会话 → **不同子代理各自成链**；
- `target === root` = 主对话；
- `label` = 房间名（UI 上显示的中文名）；
- 只增不改：进程被杀最多丢最后一行。

UI 侧（`GET /dsh-engineering-ui/compaction-history`）**同时读 v1 与 v2**，
合并后按 `at` 倒序 —— 最新在最上面，旧记录不删除、只被刷下去。

## 典型错误码

| 码 | 含义 | 处置 |
|---|---|---|
| `MEMORY_COMPACTION_PENDING` | 还在压缩/审核 | 等它结束；不要清 pending |
| `MEMORY_DIMENSIONS_REQUIRED` | 用户说了"要换尺寸"但缺 ID/值/单位 | 先追问 |
| `MEMORY_USER_EVIDENCE_REQUIRED` | `correct` 没有用户原文佐证 | 让用户明确输入 `!尺寸 …` |
| `MEMORY_USER_CONSTRAINT_PROTECTED` | 想改用户给定的约束 | 只能由用户改 |
| `MEMORY_DEPENDENCY_INVALID` | `derive` 的 parents 不存在/成环/有被撤下的 | 先补齐父节点 |
| `MEMORY_MINIMUM_OVER_BUDGET` | 最低保真内容都放不下 | 继续同一会话让它扩大范围，或用新会话继续 |
| `MEMORY_COMMIT_NOT_DURABLE` | 会话日志里找不到完整提交流水 | 检查写入权限/日志位置 |
| `MEMORY_RECOVERY_BACKUP_INVALID` | 旧备份无法核验 | 先整个复制会话目录保留现场，再查磁盘 |

> ⛔ 任何情况下**不要直接清 `pending`** —— 那等于绕过审核。

## 测试

```powershell
cd engineering\plugins\dsh-engineering-memory-guarded
node --test test/*.test.mjs
```

- `guarded-memory.test.mjs` —— 指令识别 / 依赖图 / 备份哈希 / 账本（16 项）
- `wiring.test.mjs` —— preset 接线 / 双账本合并 / 最新在上 / 只增不改（9 项）
- `history-ui.test.mjs` —— 主子对话分链 + 倒序 + 截断口径（3 项）
