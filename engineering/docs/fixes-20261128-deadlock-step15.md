# step 1.5 死锁修复（2026-11-28）

> 现场：纯汇总步（frame 声明"无需检索"）无论怎么提交都过不去，任务卡死。
> 这是我**上一轮修复引入的真死锁** —— 判据对了，但放错了地方。

---

## 一句话结论

把"豁免理由"塞进了 `gaps`（缺证台账），于是 **submit 门禁要求它必须是
blocking**，而 **commit 门禁要求它不能是 blocking** —— 两条门禁互斥，无解。

---

## 死锁是怎么形成的

```
frame 声明"无需检索"
        ↓
① gaps 全 blocking=false + sources:[]
        → submit 拒：NO_SEARCH_DECLARED_NEEDS_BLOCKING_GAP（要求 ≥1 条 blocking）
        ↓
② 加一条 blocking=true 的"本步无检索依赖"
        → submit 过 ✓
        → 但 commit 的 [...design.gaps,...knowledge.gaps].some(x=>x.blocking)
          立刻把它当"关键缺证" → evidence_wait ✗
        → review 还提 rv1-5-knowledge-bogus-blocking-gap
        ↓
③ 怎么填都过不去 = 真死锁
```

**根因是我的设计错误**：`gaps` 的语义是"还缺什么、谁来补"，
而"为什么本步不需要检索"是一条**方法说明**，不是缺失项。
混在同一个数组里，commit 无法区分「真的缺证据」和「在解释为什么不查」。

---

## 修法：方案② 独立字段

用户给了三个方案，选第二个，因为**只有它不削弱判据**：

| 方案 | 为什么不选 |
|---|---|
| ① 无检索步不强制 blocking，改判条件放行 | 等于"什么都不用说就能过"，证据要求被架空 |
| **② 把理由移到独立字段 `noSearchReason`** | ✅ 选中 |
| ③ commit 聚合时排除 owner=knowledge 的 gap | owner 是模型自填的，排除它 = 给"伪造非阻塞"开口子 |

### 改动

1. **submit**：无检索步必须带 `noSearchReason`（≥8 字），不再要求 blocking gap。
   拒绝码 `NO_SEARCH_REASON_REQUIRED`。
2. **归档**：`noSearchReason` 存进 `slot.knowledge`，与 `gaps` 分开。
3. **commit**：只有**两个条件同时成立**才排除某条 gap ——
   - 本步确实有 `noSearchReason`；
   - 该 gap 是 `owner=knowledge` 且描述里明确提到"无需/不依赖/不需要 + 检索/外部/资料/来源"。
   
   为什么要卡这么死：单纯"排除 owner=knowledge 的 blocking gap"会让人
   手填一个 owner=knowledge 的真缺口也绕过 commit。
4. **nextAction.ready**：frame 声明无需检索时返回 `ready-to-submit` +
   `noSearch:true`，不再返回 `no-search-yet`（这是死锁的入口之一）。
5. **actionHelp**：新增 `noSearchReason` 契约，并明确警告
   "gaps 是缺证台账，方法说明不要写进去"。

---

## 验证

新增 `e2e-deadlock-1128.mjs`，**必须走到 commit 成功**——
只测 submit 通过是不够的，死锁的另一半在 commit。

| 组 | 断言 |
|---|---|
| 1 | nextAction 不再报 no-search-yet，带 noSearch 标记 |
| 2 | 缺 noSearchReason 仍被拒（豁免不能白给） |
| 3 | 空 gaps + noSearchReason 能过 submit |
| 4 | **整条链走到 commit 成功，progress 前进到 1/63** |
| 5 | 对照：**真的** blocking 缺证仍然被拦（豁免没被写宽） |

| 测试 | 结果 |
|---|---|
| `e2e-deadlock-1128.mjs`（新增） | ✅ **16 / 0** |
| `e2e-buglog-20261128.mjs` | ✅ 19 / 0（契约同步更新） |
| `test_regression_seven_stage_wiring.py` | ✅ 290 / 0 |
| `smoke.mjs` | ✅ OK |

### 顺手处理的一个回归

加 `noSearchReason` 的说明文案后，review 的 status 涨到 **8193 字节**，
刚好越过 8192 的 tool-result-pruner 裁剪阈值 —— 会触发中间裁剪
（正是 BUG-9 那类问题的成因）。已把文案压缩回 8191 以内。

> 教训：status 体积是有硬上限的，**加任何一个 help 字段都要复核总长**。

---

## 部署

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\sync-to-dsh.ps1
python engineering\tools\verify_sync.py
# 然后【完全退出并重开 DSH】
```
