
# DSH 0.2.1-rc.2 适配 · {{cwd}} 报错修复记录

故障时间：2026-10-17
故障现象：切到工程模式后发送消息报
```
unknown prompt variable "{{cwd}}" in section "deployment:persona-prefix";
registered variables: provider, model
```
→ 整轮请求失败，工程模式完全不可用。

---

## 根因

1. **0.2.0** 的 \`dsh-agent-loop\` 注册过 3 个 prompt 变量：\`provider\` / \`model\` / \`cwd\`。
2. **0.2.1-rc.2 移除了 \`cwd\`**，只剩 \`provider\` 与 \`model\`
   （\`dsh-agent-loop/lib/index.js:1591-1592\`）。
   全仓核实：0.2.1 里 \`systemPrompt.variable(\` 只有这两处调用。
3. \`dsh-system-prompt\` 的 \`interpolate()\` 对未注册变量**直接抛错**，
   不是降级为空串（\`dsh-system-prompt/lib/index.js:172-175\`）。
4. 工程人设首行写了 \`你的工作目录是 {{cwd}}\`，于是每次装配都抛。

---

## 修复方案

**人设里保留 \`{{cwd}}\`（工作目录是工程模式的必要信息，不能为了绕开报错就删掉功能），
由插件把 0.2.0 的这个变量补注册回来。**

### 1. 插件注册 cwd 变量
\`engineering/plugins/dsh-engineering-ui/lib/index.js\`，在 \`apply()\` 开头：
- 走 \`ctx.inject(['systemPrompt'], ...)\`，systemPrompt 是可选服务，缺失时不硬依赖
- 取值优先级：
  1. \`ctx.workingDirectory.get(session)\` —— 0.2.1 的权威同步值，无 I/O
  2. \`session.header.cwd\` —— 会话创建时记录的原始目录
  3. 都取不到 → **返回空字符串**（不是 \`undefined\`）
- 为什么必须返回空串：官方 \`interpolate()\` 对 \`value === undefined\` 会抛
  \`has no value for this assembly\`（:176-177），而空串是合法值。
  取不到目录时让 \`{{cwd}}\` 渲染成空白，远好过让整轮请求失败。
- 为什么注册在**插件全局 ctx** 而不是 \`agent.ctx\`：
  \`assembleContextFor(agent)\` 恒为 \`{ agent, scope: agent }\`
  （\`dsh-agent/lib/index.js:291-297\`），provider 一定能拿到 \`context.agent\`；
  变量装配是 global 先、scope 后覆盖（\`dsh-system-prompt:360-362\`），
  全局注册对每个会话都生效，且**不依赖 \`agent/created\` 的时序**
  —— 否则可能出现 persona section 已渲染、变量还没注册的竞态。

### 2. 新增防回归测试
\`engineering/plugins/dsh-engineering-ui/test/prompt-variables.test.mjs\`（9 项）：
- 插件必须注册 cwd 变量
- 必须挂在 \`ctx.inject(['systemPrompt'], ...)\` 上
- 取值必须优先 \`workingDirectory.get\`
- 必须有 \`session.header.cwd\` 兜底
- **取不到时必须返回空串，不得返回 undefined**
- provider 内任何异常只降级为空串
- 人设引用的每个变量都必须有人提供
- **人设必须保留 \`{{cwd}}\`**（防止后人为了绕错把它删掉）
- \`agent.cordis.yml\` 与 preset 的变量集合必须一致

---

## 验证

### 端到端装配模拟（用 DSH 安装副本里的真实人设，28862 字符）

| 场景 | 结果 |
|---|---|
| A 正常（cwd 有值） | ✔ 装配成功，首行显示真实路径 |
| B 降级（cwd 返回空串） | ✔ 装配成功，路径处空白，不报错 |
| C 未注册（故障复现） | ✖ unknown prompt variable "{{cwd}}"… ← 正是原故障 |

### 真实执行验证
用假 ctx 跑插件 \`apply()\` 里的注册片段：
\`\`\`
注册到的变量: ["cwd"]
provider 返回值: "C:\\fake\\cwd"
\`\`\`

### 全量回归

| 测试文件 | 结果 |
|---|---|
| body-class | 11 / 0 |
| compatibility | 6 / 0 |
| eng-limit-popup | 21 / 0 |
| eng-limit-popup-runtime | 29 / 0 |
| repeat-output | 7 / 0 |
| subagent-panel | 95 / 0 |
| sw-guard-job | 13 / 0 |
| **prompt-variables（新）** | **9 / 0** |
| **合计** | **191 / 0** |

### 同步一致性
仓库与 DSH（desktop + web 两个 profile）逐文件 SHA-256 一致；
\`{{cwd}}\` 在 5 个生效位置（repo preset / repo agent.cordis / desktop preset / web preset / .agent-presets agent.cordis）
**全部保留**，人设工作目录功能完整。

---

## 生效方式

**完全退出并重开 DSH 桌面端**（Host 半是进程级的），然后切工程模式发消息验证。
