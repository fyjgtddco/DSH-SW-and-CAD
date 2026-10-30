# 2026-10-29 第二轮修 bug（循迹小车任务实测）

任务：`C:\Users\j1877\Desktop\test\20261029_082936_4159A331`
原始记录：该目录下 `插件问题记录.md` 与 `PLUGIN_BUGS_20261029.md`
会话存档：`dsh-session-session-b4891227-7995-445c-96f7-e5072fd8c2d4.zip`

## 修复清单

| 编号 | 缺陷 | 修复 | 落点 |
|---|---|---|---|
| G01 | init 遇僵尸房间直接 `ZOMBIE_DETECTED`，必须手动 reset | init 自动清理僵尸房间并继续，返回 `zombies_cleaned` / `auto_clean_note` | `workflow_gate.cmd_init` |
| G03 | `ask_user.py` 写死 `127.0.0.1:3080`，宿主实际端口不是它 → WinError 10061，卡到 540s | 默认 host 按 `--host > DSH_HOST > DSH_WEB_URL > 3080/19387` 解析；逐个 3s 探测；全不通就返回 `ASK_BRIDGE_UNREACHABLE` + 父代理代问降级指引，不再干等 | `ask_user.py` |
| G04 | DSH 崩溃重启后，fork 出来的子代理不可寻址（send_message 报 TEAM_MEMBER_NOT_FOUND），但看板仍显示 idle/存活 | 看板新增 `detached` 状态（注册表里查不到该 id，或 runId 变化=整批失联）；`safe_to_restart=false`，`next_action`=先 room-fail 再 room-start+fork | `mode_gate._board_entry` + 插件 `platform_agents.json` 增 `runId/runStartedAt` |
| G05 | 十阶段跑完但 analyst 已失联，新子代理必须 `--force` 才能绑 | 「旧 analyst 已 disposed + 十阶段已完成」时自动免 force 接管，返回 `auto_rebind` | `mode2_flow.analyst_bind` |
| G06 | room-fail 后 room-start 仍 `history_preserved=true`，父代理误判"已建模" | 上次记录为 failed 时默认按 fresh 清理（报告/心跳/旧绑定），`--keep` 才保留；返回 `fresh_reason` | `mode_gate.cmd_room_start` |
| S01 | `conn-gate --target sw` 只看 `connection_state.json` 时间戳（125645s），SW 实际在跑却报 `skip_startup=false` | `conn_state.gate()` 先做**实时进程探测**（零 COM）：进程在 → `connected/skip_startup=true` + `source="live_process"`；不在才回落状态文件；探测失败保守回退并给 `live_probe_error` | `conn_state._live_process_probe` / `gate` |
| S02 | `close-all` 返回 `ok=false, docs_after=15`（15 个文档没关） | 循环关闭直到 `docs_after==0`（最多 3 轮）；关不掉返回 `ok=false + failing_docs + hint`；`--quit` 才允许动进程 | `sw_bridge.close_documents_until_zero` / `cmd_close_all` |
| S03 | `Stop-Process -Force` 后 5 秒又冒出新的 SLDWORKS PID，杀不干净 | 新增 `sw-kill`（3 轮 × 5s 轮询确认）；`finally_exited=true` 才 `ok=true`，note 说明"每轮出现新 PID ⇒ 被 SW 后台守护重新拉起" | `sw_bridge.kill_solidworks_confirmed` / `cmd_sw_kill` |

## 相关但不在本轮（已记录，未改）

- BUG-S04 材质库缺 TPU / PETG 被近似替换成 PET（密度 1420 vs 1270）：
  需要扩充 SW 材质库映射或 `set_custom_material` 登记策略，属材料层，单独处理。
- BUG-C01~C04 几何/截图类：由 `engineering/docs/swapi-geometry-fixes.md`（联网检索 SW API 后给出）承接。

## 回归

```
python engineering/tools/run_regression.py                          # 32 / 32 全通过
python engineering/tools/test_regression_20261029_round2.py        # 20 / 20（G01/G03/G04/G05/G06）
python engineering/tools/test_regression_sw_lifecycle2.py          # 88 / 88（S01/S02/S03）
```

> 另外：三个既有测试（`mode2_flow` / `flow_live_defects` / `requirements_live` / `20261028`）
> 因本轮新增的「总计子代理真实身份」与「root 会话」两道闸门而需要跟着调整测试夹具
> （改成隔离 `DSH_STATE_DIR` + 伪造 `platform_agents.json` + 区分 root/analyst 身份），
> 已全部改好，不是产品代码回退。
