# 2026-11-16 「零件」面板连不上（图二）复盘

## 现场

子代理工作区一切正常：能读到 `quarantined=false`、SW 当前被「输入轴系」占用、
FIFO 排队可见。唯独右下角「零件」面板整块报红：

```
读取失败: 连不上 DSH 宿主桥
(http://127.0.0.1:19387)。监控器接管期间这是硬失败：请确认 DSH 正在运行且
工程 UI 插件已装配，不要改用旧锁路径。若端口不对，设置
DSH_WEB_URL=http://127.0.0.1:<实际端口> 后重试。
```

而该次 GUI 实际在 **54023**。

**这个对比本身就是全部线索**：同一个会话里，子代理能连上（说明插件端口没问题），
只有零件面板连不上（说明问题出在这条路由的**子进程环境**，不是插件本身）。

## 缺陷 1（主因）：pyEnv() 没有把宿主端口传给子进程

`parts-board` 路由用 `spawnSync` 起 `mode2_flow.py`，env 来自：

```js
function pyEnv() {
  const env = Object.assign({}, process.env);
  env.PYTHONIOENCODING = 'utf-8';
  env.PYTHONUTF8 = '1';
  return env;
}
```

也就是说**完全赌宿主进程自己有没有 `DSH_WEB_URL`**。
宿主进程里没有时，Python 侧解析链

```
DSH_SW_MONITOR_HOST → DSH_WEB_URL → DSH_WEB_URL_ALT → DSH_WEB_PORT → 固定 19387
```

一路落到固定 `19387` → 连不上。

**这是我上一轮留下的缺口**：我只修了 Python 侧的**解析顺序**（让它读 DSH_WEB_URL），
没修「**谁来喂这个变量**」—— 同一件事的另一半。当时的 e2e 测试是手动设 env 的，
所以又绕过去了。**测试替身替得太好，第二次屏蔽了同一类缺陷。**

**修复**：插件自己知道端口（`ctx.webServer.port`，防线签名器早就在用），
直接写进子进程环境：

```js
function hostPort() { /* 读 ctx.webServer.port，非法值返回 null */ }
function pyEnv() {
  ...
  const origin = hostOrigin();
  if (origin) {
    if (!env.DSH_SW_MONITOR_HOST) env.DSH_WEB_URL = origin;
    env.DSH_WEB_PORT = String(hostPort());
  }
  return env;
}
```

## 缺陷 2：错误提示与研究结论对不上

`recover` 因「本机有不受管的 SolidWorks」被拒时，返回的是：

```json
{"code": "UNMANAGED_SW_RUNNING",
 "hint": "SW 使用权由外部监控器统一分配…先启动监控器并执行 attach --drained。"}
```

**code 是对的，hint 是错的** —— 所有 MonitorError 共用一句通用提示。
使用者照着 hint 做一遍（attach），问题照旧，因为真正要做的是**关掉 SolidWorks**。

**修复**：给 `_monitor_error` 加 `_MONITOR_HINTS` 表，按 code 给可执行处置。
现在 `UNMANAGED_SW_RUNNING` 会直接说「先在 SolidWorks 里正常保存并完全退出该实例，
用 tasklist 确认已无进程」。

## 缺陷 3（排障中发现）：recover 的 reason 传递

排查过程中一度以为 `reason` 在这一层丢了（recover 报「必须写明原因」）。
实测确认**透传正常**，是我自己的探测脚本写空了 reason。
不过顺手把 `payload.reason == null ? '' : String(...)` 的防御补上，
杜绝 undefined 穿到监控器侧触发长度校验。

## 顺带查清的：DIRTY_PAGES_PENDING

监控器重启后 `attach` 报 `DIRTY_PAGES_PENDING`（`mode.rooms` / `reg.parts` /
`wf.mechanics_result`）。这是**新版记忆契约的正确行为**，不是 bug。
处理顺序：

1. 确认三份文件真实存在且非空（实测 10304 / 5901 / 31368 字节）；
2. `python memory_gate.py page-checkpoint` 记录内容指纹；
3. 若只剩 `mon.owner`（监控器侧持锁页），用
   `sw-monitor recover --reason "<真实排空说明>" --dirty-rev <revision>`。

## 缺陷 4（补测时发现）：测试自身只认仓库布局

新增的 `test_regression_partsboard_port.py` 最初把插件路径写死成
`tools/../plugins/...`——只有仓库源码树成立。安装副本里插件在
`<DSH_HOME>/profiles/*/node_modules/`，于是**仓库绿、副本崩**
（`FileNotFoundError`），等于没覆盖真实部署。

修复：加 `_find_plugin_index()`，按
`仓库相对路径 → 遍历 profiles/*/node_modules` 依次探测。
现在两种布局都能跑，各 11/11。

这已经是本次改造里第三次「测试替身/路径假设掩盖真实问题」：
① e2e 自己设 `DSH_WEB_PORT` → 掩盖端口写死；
② e2e 自己设 `DSH_WEB_URL` → 掩盖 pyEnv 不注入；
③ 本测试写死仓库路径 → 掩盖安装副本布局。
**规律：凡是"由外部提供"的东西，测试都应当断言"谁负责提供"，而不是自己提供。**

## 验收

| 项目 | 结果 |
|---|---|
| 新增 `test_regression_partsboard_port.py` | **11 / 11**（仓库 + 安装副本均通过） |
| `parts-board` 实际报错 | 由「连不上 19387」变为 `MONITOR_NOT_ATTACHED`（端口已对） |
| `recover` 提示 | 变为可执行的「关掉 SolidWorks」 |
| 全量回归 | 见 reg_portfix.txt |

## 教训（第二次踩同一坑）

1. **修「读取方」必须同时修「写入方」。** 上一轮只让 Python 学会读 `DSH_WEB_URL`，
   却没保证有人写它。配置类缺陷几乎总是成对的。
2. **测试里手动设 env = 屏蔽真实缺陷。** 这已经是第二次：端口写死那次也是
   因为 e2e 自己设了 `DSH_WEB_PORT`。凡是"宿主注入"的变量，测试就应当
   反过来断言**谁负责注入**（本文件的 10 条断言就是干这个的）。
3. **错误码对、提示错，等于没修。** 使用者读的是提示，不是 code。
