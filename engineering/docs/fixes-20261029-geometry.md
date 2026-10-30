# 2026-10-29 第三轮：SW 几何缺陷 C01~C04 已落成代码

对应方案文档：`engineering/docs/swapi-geometry-fixes.md`
全部改动集中在 `engineering/tools/swapi.py`（+1 个回归测试）。

## 改了什么

| 编号 | 缺陷 | 代码改动 | 落点 |
|---|---|---|---|
| **C01** | `cut` 在多矩形内环选段不稳（现场 20/24 部分失败） | 选段重试链 `_STRATEGIES` 只剩 `("Select2", "Select")`，**Select4 分支与缓存彻底删除**（真机实测 Select4 0/24 全抛 com_error，官方已标 Obsolete） | `select_all_sketch_segments` |
| **C02** | `_has_curved_face_at` 误判挡住 M3 沉孔 | 新增 `_face_is_planar`（官方 `ISurface.IsPlane` + `Identity==4001` 兜底）；`_has_curved_face_at` 改为「平面直接跳过 + GetBox 仅粗筛 + `_point_on_curved_face` 几何确认」，**不确定一律返回 False**（fail-open 到平面路径）；`begin_sketch_on_face` 改为**平面优先**（`_plane_hit`），旧的「曲面先行否决」删除 | `_face_is_planar` / `_has_curved_face_at` / `_point_on_curved_face` / `begin_sketch_on_face` |
| **C03** | `FeatureCut4` 每次必抛 `-2147352561 非选择性的参数` | 新增 `_cut_args(api, ...)`：**Cut3 传 26 参、Cut4 传 27 参**（Cut4 末尾补 `OptimizeGeometry=False`）。3 个 cut 调用点全部改为走它，不再共用同一个元组 | `cut()` 内 `_cut_args` + 3 处调用 |
| **C04** | 截图失败（前台被 DSH GUI 占用） | 新增 `_screenshot_via_savebmp`（`ModelDoc2.SaveBMP` 后台渲染，校验 BMP magic `BM` + 大小 >54 字节）+ 模块级 `_bmp_to_png`（Pillow 转换）；`screenshot()` 改为 **SaveBMP 优先**，只有它不可用才回落抓屏 | `screenshot` / `_screenshot_via_savebmp` / `_bmp_to_png` |

## 关键实测依据（本机 SW2025 SP5.0）

- `ISketchSegment.Select4` **0/24**；`Select2(Append,0)` **24/24**。
- 圆柱面 `GetBox = [-5,-5,0 → 5,5,10]`，Z 跨整个板厚 → `_has_curved_face_at(0,0,10)` 曾返回 **True**（已复现）。
- `FeatureCut4(26 参)` 抛 com_error；`FeatureCut4(27 参)` **真实切除**，去料 0.818% == Ø10 通孔理论值。
- `SaveBMP` 在窗口 `ShowWindow(hwnd,6)` 最小化后仍成功，1600×900 输出 4320054 字节（= 1600×900×3 + 54）。

> 注：`_cut_args` 里 `UseAutoSelect` 本来就映射在正确位置（index 19），
> 之前"参数错位"的说法不成立 —— 真正的 bug 只是 **Cut4 少传了 1 个参数**。

## 回归

```
python engineering/tools/test_regression_20261029_geometry.py   # 35 / 35（新增）
python engineering/tools/run_regression.py                      # 33 / 33 全通过
```

新增测试全部是**静态/纯函数断言**（AST 解析 + 源码检查），不需要 SolidWorks 在跑，
换机/换版本后可随时复核。真机验证项在测试末尾列出（需人工执行）。

## 尚未做（有意为之）

- **没有**把"多孔板一律改走 `extrude_with_holes`"—— 那属于建模策略重构，
  影响面大，建议先跑一轮真实任务观察 C01 修复后的选段成功率再决定。
- **没有**引入 `_API_CAPS` 能力探测表（方案文档 5 节的建议），
  属可选优化，不阻塞本次缺陷修复。
