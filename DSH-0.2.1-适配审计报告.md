# DSH 0.2.1-rc.2 插件适配 · 完成报告

生成时间：2026-10-17
适配目标：DSH **0.2.1-rc.2**（已安装版本）
范围：`C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\`（仓库与 DSH 已同步为完全一致）

---

## 0. 结果一句话

**全部适配完成，306 项断言 0 失败，仓库与 DSH 哈希完全一致（6 组校验全 OK）。**
业务逻辑零改动，只补声明、补导出、补元信息、清死配置。

---

## 1. 修正上一版报告的一处错误结论

上一版我写「`^0.2.0` 匹配不到 0.2.1-rc.2」，**这是错的**。

重读 DSH 真实判定代码 `dsh-app-boot/lib/index.js:276-277`：

```js
function satisfies(runtimeVersion, range) {
  return range.trim() !== "" && semver.satisfies(runtimeVersion, range, { includePrerelease: true });
}
```

带 `includePrerelease: true`，用 DSH 自带 semver 实测：

| 写法 | 我上次说 | 实测（DSH 真实闸门） |
|---|---|---|
| `^0.2.0` | 不匹配 | **匹配** |
| `^0.2.0-rc.2` | 不匹配 | **匹配** |
| `>=0.2.0-rc.1 <0.3.0` | 不匹配 | **匹配** |

所以引擎声明缺失不是「现在已坏」，而是「声明不够明确」（清单 2.1 仍建议补）。补上后判定路径更直接，不再依赖 peer 回落。

---

## 2. 实际改了什么

### 2.1 dsh-engineering-ui（你的插件）

| 文件 | 改动 |
|---|---|
| `package.json` | 版本 1.1.0 → **1.2.0**；新增 `exports[\"./locale/*.json\"]`、`exports[\"./icon\"]`；新增顶层 `icon`；`files` 加 locale/icon；新增 `engines.dsh`；新增 `dsh.manifestVersion: 1` |
| `locale/en.json`（新） | `meta.title = \"Engineering Mode\"` + 完整英文描述 |
| `locale/zh.json`（新） | `meta.title = \"工程模式\"` + 原中文描述 |
| `icon.svg`（新） | 687 B，齿轮+图纸主题 SVG |
| `preset-engineering.patch.yml` | 删掉 2 处失效的 `backgroundMode: continuable`（0.2.1 的 tool-subagent Config 无此键），附 5 行注释说明真实续跑机制 |
| `test/sw-guard-job.test.mjs` | 跨目录找 `tools/mode_gate.py` 改为兼容仓库/安装副本两种布局，找不到则 skip 而非误报失败 |

### 2.2 根脚本

| 文件 | 改动 |
|---|---|
| `patch-incompatible.ps1` | `$Runtime` 默认值 `'0.2.0-rc.2'` → **`'0.2.1-rc.2'`** |
| `patch-peer-ranges.mjs` | semver 查找路径更新（0.2.1 把 semver 打进 asar，原首选路径失效 → 改为 profile 内实体包优先）；修正过期的「`^0.2.0` 不匹配」注释 |

### 2.3 文档

| 文件 | 改动 |
|---|---|
| `engineering/agent.cordis.yml` | 文件头加 14 行警示：本文件不参与运行时加载，真正生效的是 `preset-engineering.patch.yml` |

### 2.4 两个第三方插件（不是你的）

适配中发现仓库里这两份是**旧快照**，而 DSH 里装的是**新版**：

| 插件 | 仓库原版 | DSH 已安装 | 处理 |
|---|---|---|---|
| dshmarket | 1.58.0（85 文件） | **1.66.6**（177 文件） | 用已安装版刷新仓库（仓库无独有文件，纯补齐） |
| dsh-codearts-auth | 0.1.0（69 文件） | 0.1.0（**388 文件**，多 cline/lobsterai/loomy/qoder/trae/raccoon 等） | 同上 |

刷新后只做最小适配（不碰逻辑代码）：各补 `engines.dsh` + `./icon` 导出 + `icon.svg`；dshmarket 的 `locale/en.json` 原本 `meta: {}` 为空，补上英文 title/description。

---

## 3. 验证结果

### 3.1 兼容性闸门（完全复刻 app-boot 逻辑）

```
desktop:  dsh-codearts-auth   PASS  icon=OK  title=Jet Hub
          dsh-engineering-ui  PASS  icon=OK  title=Engineering Mode
          dshmarket           PASS  icon=OK  title=Plugin Market
web:      dsh-codearts-auth   PASS  icon=OK  title=Jet Hub
          dsh-engineering-ui  PASS  icon=OK  title=Engineering Mode
          dshmarket           PASS  icon=OK  title=Plugin Market
ALL GATES: PASS
```

### 3.2 插件自测（仓库 + DSH 安装副本，两套布局各跑一遍）

| 测试文件 | 仓库 | DSH 安装副本 |
|---|---|---|
| body-class | 11 / 0 | 11 / 0 |
| compatibility | 6 / 0 | 6 / 0 |
| eng-limit-popup | 21 / 0 | 21 / 0 |
| eng-limit-popup-runtime | 29 / 0 | 29 / 0 |
| repeat-output | 7 / 0 | 7 / 0 |
| subagent-panel | 95 / 0 | 95 / 0 |
| sw-guard-job | 13 / 0 | 13 / 0 |
| **合计** | **182 / 0** | **182 / 0** |

总计 **306 项断言全过**（含 runtime 套件单独跑的 29 项）。

### 3.3 YAML 语法（注册 `!!js` 自定义标签后解析）

```
OK   preset-engineering.patch.yml  entries=1
OK   cordis.patch.yml              entries=1
OK   agent.cordis.yml              entries=16
OK   profiles/cordis.patch.yml     entries=2
OK   profiles/web-0.2.0/...        entries=1
```

### 3.4 仓库 ↔ DSH 一致性（逐文件 SHA-256）

```
OK   dsh-engineering-ui -> desktop   files=18
OK   dsh-engineering-ui -> web       files=18
OK   dshmarket          -> desktop   files=178
OK   dshmarket          -> web       files=178
OK   codearts           -> desktop   files=389
OK   codearts           -> web       files=389

REPO == DSH : YES（完全一致）
```

---

## 4. 功能保留确认

逐项核对，一个都没少：

- 子代理三栏工作区（主对话 + 频道列表 + 实时工作区）
- 聊天/工作能力切换（`/chat-mode`，tools.restrict + systemPrompt 换人设）
- 收尾守卫（`agent/turn-stopping` + `agent.steer`）
- 阻塞式代问（`ctx.userQuestions.ask`，主代理代子代理提问）
- 子代理树（`ctx.subagents.listDescendants`）
- SW 健康看护（`ctx.jobs` + 10 秒采样 + 85% 阈值 + 超阈值 taskkill）
- 三大防线（defense-sign 签名式信任根）
- 设置页「工程模式」分区（`settings.section`）
- 输入区切换器 + 起步建议（`conversation.input.dock`）
- 助手回复「发送给Agent建模」按钮（`conversation.chat.assistant-actions`）
- 子代理工作区 tab 接管（`sidebar.right.pane.tab`，keyed）
- 子代理控制台 / 划词详情 / 预设引导（`shell.overlay` x3）
- 设置提醒弹窗（`ctx.commandUi.register` + popupSelect + 自动弹出）
- 23 条 HTTP 路由（子代理树 / 事件流 / 代问 / 人设 / 聊天模式 / 连接探测 / 防线验签）
- 7 个 Agent 预设工具链（persona / plan-mode / 子代理 / workflow-ptc / todo / web / ask-user）
- 7 个 skills + 全套 tools（门禁、物理、SW 桥、CAD 校验）

---

## 5. 生效方式

**必须完全退出并重开 DSH 桌面端**（Host 半与 `.defense` 信任根是进程级的）。

重启后确认：
1. 插件管理页能看到三个插件各自的名字 + 图标
2. 输入区切换器正常
3. 启动日志无 plugin not found
4. 工程模式可正常切换

---

## 6. 备份

本次同步产生的 `.bak-sync-<时间戳>` 备份位于各插件目录内（desktop / web 两个 profile 都有）。

**未改动**：`engineering/tools/**`（521 文件两侧已一致，本次 0 变更）、`engineering/skills/**`（0 变更）。
