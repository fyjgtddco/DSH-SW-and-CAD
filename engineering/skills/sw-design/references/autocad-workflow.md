# AutoCAD 工作流与几何验证（DSH 工程模式）

> 来源：CADX 工程模式 + AutoCAD-skills mechanical-drafting-gbt
> 用途：指导 AI 操作 AutoCAD 时的流程规范、验证规则和坐标约定。

## 连接与安全

- 优先挂接已有 AutoCAD 进程，不强制启动或关闭用户会话
- 所有 COM 操作使用 dynamic dispatch（`win32com.client.dynamic.Dispatch`），不依赖类型库注册
- AutoCAD busy 时（RPC_E_CALL_REJECTED）先发送 ESC 取消挂起命令，再重试
- 坐标单位统一 mm，与 swapi 一致

## 几何验证流程（7 项检查）

每次生成或修改 AutoCAD 图纸后，必须运行以下验证：

| # | 检查项 | 严重等级 | 说明 |
|---|--------|---------|------|
| 1 | UNCLOSED_POLYLINE | WARNING/INFO | 应闭合但开口 > 10mm 的多段线 |
| 2 | OVERLAPPING_GEOMETRY | CRITICAL | 墙体/轮廓线错误重叠 |
| 3 | ROOM_TOO_SMALL | CRITICAL | 房间面积低于 GB/T 最低标准 |
| 4 | ROOM_ASPECT_RATIO | WARNING | 长宽比超出合理范围（> 3:1） |
| 5 | WALL_TOO_THIN | CRITICAL | 平行墙间距 < 100mm（机械）或 150mm（建筑） |
| 6 | MISSING_ELEMENTS | WARNING/INFO | 房间缺少门或窗 |
| 7 | DIMENSION_MISMATCH | CRITICAL | 标注值与实际几何不符（误差 > 2%） |

验证入口：
```powershell
python "<工程目录>\sw_bridge.py" cad-validate <图纸.dwg 或 .dxf> [规则文件]
# 或直连 AutoCAD：
python "<工程目录>\sw_bridge.py" cad-validate-live
```

## 坐标与命名约定

- **单位**：所有尺寸 mm（整数），输出前 ×1000 换算
- **图层命名**：大写 + 字母数字下划线，如 `OUTLINE`、`DIM`、`CENTER`
- **线型层级**：
  - `OUTLINE`：粗实线（可见轮廓）
  - `THIN`：细实线（尺寸、剖面线）
  - `CENTER`：细点画线（轴线）
  - `HIDDEN`：细虚线（不可见轮廓）
  - `HATCH`：细实线（剖面填充）
  - `DIM`：细实线（尺寸标注）
  - `TEXT`：细实线（文字说明）

## CAD 执行铁律

1. **事务原子性**：每次批量创建实体前记录句柄，失败时仅回滚该批次
2. **后验验证**：每创建实体后立即读取返回句柄，比对类型/图层/坐标
3. **禁止猜测**：未确定的配合/公差/材料一律标 `TBD`，不自行补全
4. **视图一致性**：多视图必须共享同一参数源，禁止从不同视角独立估算
5. **比例真实性**：标题栏声明 `1:1` 时，实际导出比例必须确实是 1:1；`FIT`/`NTS` 不得与固定比例混用

## 快速参考命令

```powershell
# 环境自检
python "<工程模式根目录>\tools\sw_bridge.py" doctor

# 连接状态
python "<工程模式根目录>\tools\sw_bridge.py" status

# 列出已打开文档
python "<工程模式根目录>\tools\sw_bridge.py" list

# SolidWorks 零件 → 工程图
python "<工程模式根目录>\tools\sw_bridge.py" drawing <零件.sldprt>

# 工程图 → DWG（AutoCAD 可读）
python "<工程模式根目录>\tools\sw_bridge.py" dwg <零件.sldprt>

# 导出 DXF（供 CADX 验证）
python "<工程模式根目录>\tools\sw_bridge.py" export-dxf <图纸.dxf>

# AutoCAD 几何验证
python "<工程模式根目录>\tools\sw_bridge.py" cad-validate <图纸.dxf>

# 展示 SolidWorks 窗口 + 截图
python "<工程模式根目录>\tools\sw_bridge.py" show
```
