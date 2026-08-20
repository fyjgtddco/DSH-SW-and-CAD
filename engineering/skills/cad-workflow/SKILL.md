---
name: cad-workflow
description: AutoCAD 机械设计工作流
whenToUse: 当需要进行 AutoCAD 零件设计、装配设计或工程图纸输出时
disable-model-invocation: false
user-invocable: true
source: engineering
provider: filesystem
---

## AutoCAD 机械设计工作流

### 一、设计前分析

1. **需求分析**
   - 明确零件的功能要求
   - 确定载荷类型和大小
   - 确定工作环境（温度、湿度、腐蚀性等）

2. **尺寸合理性检查**
   - 壁厚是否均匀？
   - 是否有应力集中风险？
   - 加工工艺是否可行？

3. **运行时分析**
   - 运动部件是否有足够间隙？
   - 配合公差是否合理？
   - 是否考虑热膨胀？

### 二、零件设计流程

1. **草图绘制**
   - 选择合适的基准面
   - 使用几何约束和尺寸约束
   - 检查草图是否完全定义

2. **特征建模**
   - 拉伸、旋转、扫描、放样
   - 添加圆角、倒角
   - 添加孔、槽等特征

3. **零件验证**
   - 检查质量属性
   - 干涉检查
   - 应力分析（如需要）

### 三、装配设计

1. **装配顺序规划**
   - 确定装配基准件
   - 规划装配路径
   - 考虑装配工具空间

2. **配合定义**
   - 间隙配合、过渡配合、过盈配合
   - 选择合适的配合公差

3. **装配验证**
   - 干涉检查
   - 运动模拟
   - 爆炸视图生成

### 四、工程图纸输出

1. **视图选择**
   - 主视图、俯视图、左视图
   - 剖视图、局部放大图

2. **标注规范**
   - 尺寸标注
   - 公差标注
   - 表面粗糙度
   - 形位公差

3. **技术要求**
   - 材料要求
   - 热处理要求
   - 表面处理要求

### 五、AutoCAD 调用示例

```powershell
# 通过 COM 接口启动 AutoCAD
$acad = New-Object -ComObject "AutoCAD.Application"
$acad.Visible = $true
$doc = $acad.Documents.Add()

# 创建一条直线
$line = $doc.ModelSpace.AddLine(
    (New-Object -ComObject "AutoCAD.Point" -ArgumentList 0, 0, 0),
    (New-Object -ComObject "AutoCAD.Point" -ArgumentList 100, 50, 0)
)

# 保存图纸
$doc.SaveAs("C:\path\to\drawing.dwg")
```