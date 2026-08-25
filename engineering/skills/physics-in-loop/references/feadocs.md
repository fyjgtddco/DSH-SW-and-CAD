# Physics-in-the-Loop 参考文档

## 1. 载荷工况 Schema（load_case_schema.md）

### 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `schema_version` | string | 是 | 当前版本 `"1.0"` |
| `meta.problem_id` | string | 是 | 问题唯一标识（大写下划线） |
| `meta.title` | string | 否 | 人类可读标题 |
| `units` | object | 否 | 单位约定（默认 mm/N/MPa/kg） |
| `design_domain.bounds` | object | 是 | 6 个边界坐标（mm） |
| `material.id` | string | 是 | 材料 ID（见 material_db.py） |
| `material.youngs_modulus_mpa` | float | 是 | 弹性模量 |
| `material.yield_strength_mpa` | float | 是 | 屈服强度 |
| `material.density_kg_m3` | float | 是 | 密度 |
| `spatial_selectors[]` | array | 是 | 区域选择器（固定面/载荷面） |
| `spatial_selectors[].id` | string | 是 | 选择器唯一 ID |
| `spatial_selectors[].type` | string | 是 | `box` / `cylinder` / `sphere` |
| `spatial_selectors[].bounds` | object | box 时必填 | 6 个边界坐标 |
| `boundary_conditions[]` | array | 是 | 约束列表 |
| `loads[]` | array | 是 | 载荷列表 |
| `acceptance.min_safety_factor` | float | 是 | 最小安全系数（通常 2.0） |
| `acceptance.target_safety_factor_max` | float | 是 | 最大安全系数（通常 5.0） |
| `acceptance.max_displacement_mm` | float | 否 | 最大允许位移 |

### 单位约定

所有坐标、尺寸单位为 **mm**，力为 **N**，应力为 **MPa**（= N/mm²），质量为 **kg**。
求解器内部自动完成单位换算。

---

## 2. 材料策略（material_policy.md）

### 材料来源优先级

1. **用户指定**：用户提供材料牌号或屈服强度 → 直接使用
2. **material_db 匹配**：ID 匹配内置材料库 → 使用库值
3. **TBD 标记**：均不可得 → 标 TBD，禁止给出确定性安全结论

### 安全系数与材料的关系

| 材料类型 | 推荐 min SF | 推荐 max SF | 备注 |
|---------|------------|------------|------|
| 结构钢（静载） | 1.5 | 3.0 | 延性材料，失效前有明显变形 |
| 铝合金（静载） | 2.0 | 4.0 | 需注意腐蚀和疲劳 |
| 钛合金 | 2.0 | 4.0 |  aerospace 常用 |
| 工程塑料 | 3.0 | 6.0 | 蠕变敏感，保守设计 |
| 铸铁 | 3.0 | 5.0 | 脆性材料，SF 需更大 |
| 疲劳载荷 | 按 S-N 曲线 | — | 本系统暂不支持疲劳分析 |

### 不支持的材料行为

以下情况必须声明 `NOT_EVALUATED`：
- 塑性大变形（σ > σy 后行为）
- 疲劳寿命预测
- 屈曲（薄壁件失稳）
- 冲击/动态载荷
- 热-结构耦合
- 接触非线性（螺栓预紧、摩擦）

---

## 3. 求解有效性（fea_validity.md）

### 解析解适用条件

解析解（Euler-Bernoulli 梁理论）仅在以下条件下可靠：
1. 长细比 L/h > 10（梁近似成立）
2. 材料线弹性（σ < 0.5×σy）
3. 小变形（δ/L < 0.01）
4. 均匀截面，无突变

**不满足上述条件时，解析解结果仅作参考，必须升级求解器。**

### 网格质量门控

| 指标 | 合格阈值 | 处理方式 |
|------|---------|---------|
| 单元最小 Quality | ≥ 0.1 | 失败则细化网格 |
| 单元数量 | 1,000 ~ 1,000,000 | 超出范围需人工审查 |
| 雅可比行列式 | > 0 | 负值表示单元反转，网格无效 |
| 自由边界数 | = 0（封闭体） | 开放体需确认设计意图 |

### 收敛判定

连续两轮（n, n+1）同时满足：
```
|SF_n - SF_{n+1}| / SF_n < 5%   AND   |V_n - V_{n+1}| / V_n < 3%
```
则视为收敛，即使 SF 仍在目标区间外也建议人工介入。

---

## 4. 修正策略（refinement_policy.md）

### 修正动作优先级

```
1. 安全系数过低（SF < min）
   → 优先增加壁厚（最有效，对 I 影响最大）
   → 次选增大圆角（降低应力集中）
   → 最后加筋（增加局部刚度）

2. 安全系数过高（SF > max）
   → 减小壁厚（在 min 之上）
   → 减小截面高度（若位移允许）

3. 位移超限
   → 增大截面高度（I ∝ h³，效果最显著）
   → 增加支撑点（改变边界条件）
   → 增大壁厚（次选）

4. 设计空间越界
   → 拒绝本轮，缩小参数范围重新搜索
   → 不允许越界设计通过
```

### 禁止的修正动作

- 修改 locked 参数（接口尺寸、安装孔径）
- 改变设计空间边界
- 改变材料（除非用户明确允许）
- 添加未声明的支撑或约束
- 减少实体特征（如去掉加强筋）以降低质量但牺牲强度

### 修正透明度

每次修正必须记录：
```json
{
  "change_id": "iter_03_change_01",
  "param_id": "thickness_mm",
  "from": 5.0,
  "to": 7.0,
  "reason": "safety factor 1.2 < 2.0; thickness scales ~sqrt(2.0/1.2)",
  "expected_effect": "SF ≈ 2.1, displacement ≈ -30%",
  "verified": true
}
```
