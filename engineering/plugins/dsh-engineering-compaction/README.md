# dsh-engineering-compaction

工程模式专用**分层压缩器**（L0–L3）。只在 `engineering` 预设下加载，用它替换官方的
`@deepseek-ai/dsh-compaction-basic`。

## 一句话

**压缩 N 次，机械数值一个都不动。**

## 为什么需要它

官方压缩器把对话当普通文本摘要。机械设计场景里，一段对话里塞满了
`φ24H7`、`12mm`、`206000 MPa`、`Q235`、`M8×1.25` 这类数值 —— 摘几次就开始漂：
0.3 变 0.30、φ24H7 变"约 φ24"、206000 MPa 变"200 GPa"。图纸一错，全盘返工。

## 怎么保证「数值不动」

三道闸，缺一不可：

| 闸 | 位置 | 作用 |
|---|---|---|
| ① 抽取 | `facts.extractFacts()` | 压缩前把每个数值连同上下文抽成结构化 L0 事实，**不交给模型** |
| ② 注入 | `prompt.buildCompactionInstruction()` | 把事实区块作为只读材料塞进提示词，命令模型逐字复制 |
| ③ 核对+回填 | `facts.verifySummary()` / `restoreFacts()` | 摘要回来后逐条比对；缺失就**原样补回** |

关键在第三道：即使模型完全不听指令，最终落盘的数值也一定与压缩前一致。
这是**结构性保证**，不是靠模型自觉。

如果回填后摘要反而比原文长（原文本来就很短、或 L0 事实极多），
**放弃本次压缩**并抛 `ENG_COMPACTION_NOT_SMALLER` —— 宁可这轮不省 token，
也不删数。

## 四层压缩

| 层 | 内容 | 策略 | 实现 |
|---|---|---|---|
| **L0** | 尺寸、材料、配合、公差、约束、机械关系 | **无损，直接复制** | 模型之外单独保管 + 强制回填 |
| **L1** | 已完成零件、装配状态、特征树、进度 | 结构化更新 | 逐条 `对象 → 状态 → 证据` |
| **L2** | 方案取舍理由、结构修改原因 | 可提炼，但不丢有效决策 | 跨次累积，**只增不减** |
| **L3** | 思考过程、重复操作、无效尝试、冗余交流 | **全删** | 只保留用户关键要求（逐字引用） |

## 失败 / 成功方案怎么压缩

**失败方案**（按失败条件去重，不是按方案名）：

```
- 尝试: <做了什么>
  失败条件: <可检验的具体条件>
  不可再做: <以失败条件表述，不要写成方案名>
```

**成功方案**（五段式）：

1. 具体操作顺序
2. 哪些参数不能修改
3. 哪些步骤可以复用
4. 哪些步骤依赖之前的特征
5. 验证成功时的模型版本 / 文件 / 报告

## L2 / L1 的「只能加不能减」

每次压缩前，从账本里取上一条记录的 `l1_text` / `l2_text` 注入提示词，
明确要求 `APPEND-ONLY: keep every line still valid, do NOT shorten, do NOT delete`。
L0 事实集则用 `mergeFacts()` 做**单调并集**（`history.js` 只提供 append，不提供 update/remove）。

## 账本（红框面板的数据源）

每次压缩追加一行到 `<state_dir>/compaction_history.jsonl`：

```json
{"at":1762500000000,"target":"<sessionId>","root":"<rootSessionId>","label":"结构件房间",
 "l0_numbers":18,"l0_materials":2,"l0_restored":3,
 "input_chars":48000,"output_chars":9000,"saved_chars":39000}
```

- `target`：子代理会话 id；主对话的 `target === root`
- `l0_restored > 0`：模型漏写、被自动补回的条数（UI 用黄色徽标提示）
- 只增不改：进程被杀最多丢最后一行，不会写坏整个文件

## 配置

与 `compaction-basic` **完全一致**（本插件继承自它，只覆盖了 `summarize()` 钩子）：

```yaml
- id: compaction-engineering
  name: 'dsh-engineering-compaction'
  config:
    thresholdRatio: 0.5
    headroomTokens: 48000
    retainRatio: 0.12
```

## 为什么不重写整个压缩子系统

区域事务（选区、锁、`compaction/start…end`、surface 替换、token 计量、并发与取消）
在 `compaction-basic` 里已经写死且验证过，官方也明确 `summarize()` 是**唯一的子类定制钩子**。
本插件只接管「摘要怎么写」，其余全部继承 —— 替换压缩器不会把会话存档机制搞坏。

## 测试

```bash
node --test "test/*.test.mjs"
```

- `facts.test.mjs` —— 数值提取/核对/回填/单调合并（16 项）
- `engine.test.mjs` —— 提示词分层结构与账本 append-only（9 项）
- `summarize.test.mjs` —— 流式装配与 fail-closed（6 项）
- `ui-wiring.test.mjs` —— UI 接线一致性静态回归（9 项）
