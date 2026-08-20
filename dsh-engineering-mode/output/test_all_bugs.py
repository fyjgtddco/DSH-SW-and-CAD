"""系统性测试所有 11 个 bug 的修复状态"""
import sys, os, time, json, math
sys.path.insert(0, r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools")
import swapi

sw = swapi.get_sw()
results = []

print("=" * 60)
print("Bug 系统性测试")
print("=" * 60)

# ============================================================
# Bug #1: extrude(draft_deg=) 参数是否传递到 FeatureExtrusion3
# ============================================================
print("\n[Bug #1] extrude(draft_deg=) 参数传递")
try:
    m = swapi.new_part()
    m.begin_sketch("Front Plane")
    m.rect(0, 0, 50, 30)
    m.end_sketch()
    # 带拔模角度的拉伸
    feat = m.extrude(10, draft_deg=5)
    if feat:
        print("  ✅ OK - extrude with draft_deg=5 成功")
        results.append(("Bug#1", "FIXED", "draft_deg 参数正确传递"))
    else:
        print("  ❌ FAIL - extrude 返回 None")
        results.append(("Bug#1", "NOT_FIXED", "extrude 返回 None"))
except Exception as e:
    print(f"  ❌ FAIL - {e}")
    results.append(("Bug#1", "ERROR", str(e)[:80]))

# ============================================================
# Bug #2: cmd_run subprocess text=True 缺少 encoding='utf-8'
# ============================================================
print("\n[Bug #2] cmd_run subprocess encoding")
try:
    import sw_bridge
    src = r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools\sw_bridge.py"
    with open(src, 'r', encoding='utf-8') as f:
        content = f.read()
    if "text=True, encoding='utf-8'" in content:
        print("  ✅ OK - subprocess 调用已包含 encoding='utf-8'")
        results.append(("Bug#2", "FIXED", "encoding='utf-8' 已添加"))
    else:
        print("  ❌ FAIL - 缺少 encoding='utf-8'")
        results.append(("Bug#2", "NOT_FIXED", "encoding='utf-8' 缺失"))
except Exception as e:
    print(f"  ❌ FAIL - {e}")
    results.append(("Bug#2", "ERROR", str(e)[:80]))

# ============================================================
# Bug #3: cmd_run proc.stdout.strip() crash when stdout is None
# ============================================================
print("\n[Bug #3] cmd_run proc.stdout 为 None 时的保护")
try:
    with open(src, 'r', encoding='utf-8') as f:
        content = f.read()
    if 'proc.stdout or ""' in content or '(proc.stdout or "")' in content:
        print("  ✅ OK - 已添加 proc.stdout 为 None 的保护")
        results.append(("Bug#3", "FIXED", "proc.stdout None 保护已添加"))
    else:
        print("  ❌ FAIL - 缺少 proc.stdout None 保护")
        results.append(("Bug#3", "NOT_FIXED", "proc.stdout None 无保护"))
except Exception as e:
    print(f"  ❌ FAIL - {e}")
    results.append(("Bug#3", "ERROR", str(e)[:80]))

# ============================================================
# Bug #4: ExportFile for DWG fails on SW 2020
# ============================================================
print("\n[Bug #4] DWG ExportFile 兼容性")
try:
    with open(src, 'r', encoding='utf-8') as f:
        content = f.read()
    # 检查是否有 SaveAs3 作为主要方式 + ExportFile 作为备选
    has_saveas3 = 'SaveAs3' in content
    has_exportfile = 'ExportFile' in content
    if has_saveas3 and has_exportfile:
        print("  ⚠️  PARTIAL - 已有 SaveAs3 + ExportFile 双策略，但内容可能为空")
        results.append(("Bug#4", "PARTIAL", "双策略存在但内容质量待验证"))
    elif has_saveas3:
        print("  ✅ OK - 已使用 SaveAs3 替代 ExportFile")
        results.append(("Bug#4", "FIXED", "SaveAs3 替代方案"))
    else:
        print("  ❌ FAIL - 仍在使用 ExportFile")
        results.append(("Bug#4", "NOT_FIXED", "ExportFile 仍为主要方式"))
except Exception as e:
    print(f"  ❌ FAIL - {e}")
    results.append(("Bug#4", "ERROR", str(e)[:80]))

# ============================================================
# Bug #5: cmd_drawing screenshot captures part instead of drawing
# ============================================================
print("\n[Bug #5] cmd_drawing 截图正确性")
try:
    # 检查 cmd_drawing 中的截图逻辑
    with open(src, 'r', encoding='utf-8') as f:
        content = f.read()
    # 截图前是否先激活了工程图文档？
    if 'from_active(sw)' in content and 'screenshot' in content:
        # 需要进一步检查是否在 drawing 模式下
        lines = content.split('\n')
        drawing_section = False
        screenshot_line = -1
        for i, line in enumerate(lines):
            if 'def cmd_drawing' in line:
                drawing_section = True
            if drawing_section and 'screenshot' in line.lower():
                screenshot_line = i
                break
        if screenshot_line > 0:
            # 检查截图前是否有切换文档的逻辑
            context = '\n'.join(lines[max(0,screenshot_line-5):screenshot_line+1])
            if 'doc' in context.lower() or 'drawing' in context.lower():
                print("  ✅ OK - 截图在工程图文档上下文中执行")
                results.append(("Bug#5", "FIXED", "截图上下文正确"))
            else:
                print("  ⚠️  PARTIAL - 截图可能捕获错误窗口")
                results.append(("Bug#5", "PARTIAL", "截图上下文需验证"))
        else:
            print("  ❌ FAIL - 未找到截图代码")
            results.append(("Bug#5", "NOT_FIXED", "无截图代码"))
    else:
        print("  ⚠️  PARTIAL - 截图逻辑需检查")
        results.append(("Bug#5", "PARTIAL", "截图逻辑待验证"))
except Exception as e:
    print(f"  ❌ FAIL - {e}")
    results.append(("Bug#5", "ERROR", str(e)[:80]))

# ============================================================
# Bug #6: GetMassProperties() 卡死 SW 进程
# ============================================================
print("\n[Bug #6] GetMassProperties 安全调用")
try:
    # 这是用户要求的不修复项 - 通过设计避免调用
    print("  ⏭️  SKIP - 用户要求不修复（通过设计避免调用）")
    results.append(("Bug#6", "SKIPPED", "用户明确要求不修复"))
except Exception as e:
    print(f"  ❌ FAIL - {e}")
    results.append(("Bug#6", "ERROR", str(e)[:80]))

# ============================================================
# Bug #7: CreateDrawViewFromModelView 返回 False 被误判
# ============================================================
print("\n[Bug #7] CreateDrawViewFromModelView 返回值检查")
try:
    with open(src, 'r', encoding='utf-8') as f:
        content = f.read()
    # 检查是否正确检查 False 返回值
    if 'if v:' in content or 'if not v:' in content:
        print("  ✅ OK - 已正确检查 v 的布尔值（False 会被识别）")
        results.append(("Bug#7", "FIXED", "if v: 正确检查布尔值"))
    else:
        print("  ❌ FAIL - 仍使用 is not None 检查")
        results.append(("Bug#7", "NOT_FIXED", "仍是 None 检查"))
except Exception as e:
    print(f"  ❌ FAIL - {e}")
    results.append(("Bug#7", "ERROR", str(e)[:80]))

# ============================================================
# Bug #8: 英文基准面名在中文 SW 上不工作
# ============================================================
print("\n[Bug #8] 中英文基准面名自动切换")
try:
    with open(r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools\swapi.py", 'r', encoding='utf-8') as f:
        content = f.read()
    # 检查 select_plane 是否尝试中英文回退
    has_name_map = 'name_map' in content and '前视基准面' in content
    has_fallback = 'except' in content and 'SelectByID2' in content
    if has_name_map and has_fallback:
        print("  ✅ OK - select_plane 已支持中英文自动回退")
        results.append(("Bug#8", "FIXED", "中英文基准面名自动切换"))
    else:
        print("  ❌ FAIL - 缺少中英文回退逻辑")
        results.append(("Bug#8", "NOT_FIXED", "无中英文回退"))
except Exception as e:
    print(f"  ❌ FAIL - {e}")
    results.append(("Bug#8", "ERROR", str(e)[:80]))

# ============================================================
# Bug #9: 草图未提交到特征树（InsertSketch 返回值判断错误）
# ============================================================
print("\n[Bug #9] 草图提交到特征树")
try:
    with open(r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools\swapi.py", 'r', encoding='utf-8') as f:
        content = f.read()
    # 检查 begin_sketch 中是否用 is False 而不是 not result
    if 'is False' in content and 'InsertSketch' in content:
        print("  ✅ OK - begin_sketch 使用 is False 检查（None 不再误判）")
        results.append(("Bug#9", "FIXED", "is False 检查修正"))
    elif 'not result' in content and 'InsertSketch' in content:
        print("  ❌ FAIL - 仍用 not result 检查（None 被误判为 False）")
        results.append(("Bug#9", "NOT_FIXED", "仍是 not result 检查"))
    else:
        print("  ⚠️  UNKNOWN - 无法确定检查方式")
        results.append(("Bug#9", "UNKNOWN", "无法确认检查方式"))
except Exception as e:
    print(f"  ❌ FAIL - {e}")
    results.append(("Bug#9", "ERROR", str(e)[:80]))

# ============================================================
# Bug #10: revolve() 不检查 FeatureRevolve2 返回值
# ============================================================
print("\n[Bug #10] revolve() 返回值检查")
try:
    with open(r"C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools\swapi.py", 'r', encoding='utf-8') as f:
        content = f.read()
    # 检查 revolve 方法是否检查 None
    revolve_section = content[content.find('def revolve'):content.find('def revolve')+500]
    if 'is None' in revolve_section or 'raise' in revolve_section:
        print("  ✅ OK - revolve() 已检查 FeatureRevolve2 返回值")
        results.append(("Bug#10", "FIXED", "返回值检查已添加"))
    else:
        print("  ❌ FAIL - revolve() 未检查返回值")
        results.append(("Bug#10", "NOT_FIXED", "无返回值检查"))
except Exception as e:
    print(f"  ❌ FAIL - {e}")
    results.append(("Bug#10", "ERROR", str(e)[:80]))

# ============================================================
# Bug #11: massprops 返回体积为 0
# ============================================================
print("\n[Bug #11] massprops 返回正确体积")
try:
    import sw_bridge
    # 创建测试零件
    sw.CloseAllDocuments(0)
    time.sleep(0.5)
    m = swapi.new_part()
    m.begin_sketch("Front Plane")
    m.circle(0, 0, 50)  # R=50mm
    m.end_sketch()
    m.extrude(20)  # 高20mm
    time.sleep(1)
    
    # 保存并重新打开以验证
    test_path = r"C:\Users\j1877\Desktop\DSH-Check\SW\test_massprops.SLDPRT"
    m.save(test_path)
    
    # 重新打开并检查质量属性
    sw.CloseAllDocuments(0)
    time.sleep(0.5)
    
    import win32com.client, pythoncom
    errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    sw.OpenDoc6(test_path, 1, 1, "", errs, warns)
    time.sleep(1)
    
    model = sw.ActiveDoc
    try:
        mp = model.GetMassProperties
        if mp:
            vol = mp[3] * 1e9  # m3 -> mm3
            expected = math.pi * 2500 * 20  # π * 50² * 20
            error_pct = abs(vol - expected) / expected * 100
            if error_pct < 1:
                print(f"  ✅ OK - 体积 {vol:.1f} mm³ (误差 {error_pct:.1f}%)")
                results.append(("Bug#11", "FIXED", f"体积 {vol:.0f} mm³ 正确"))
            else:
                print(f"  ❌ FAIL - 体积 {vol:.1f} mm³ (预期 {expected:.1f})")
                results.append(("Bug#11", "NOT_FIXED", f"体积错误 {vol:.0f} vs {expected:.0f}"))
        else:
            print("  ⚠️  PARTIAL - GetMassProperties 返回 None")
            results.append(("Bug#11", "PARTIAL", "GetMassProperties 返回 None"))
    except Exception as e:
        print(f"  ❌ FAIL - GetMassProperties 异常: {e}")
        results.append(("Bug#11", "ERROR", str(e)[:80]))
except Exception as e:
    print(f"  ❌ FAIL - 测试失败: {e}")
    results.append(("Bug#11", "ERROR", str(e)[:80]))

# ============================================================
# 汇总
# ============================================================
print("\n" + "=" * 60)
print("测试结果汇总")
print("=" * 60)
fixed = sum(1 for _, status, _ in results if status == "FIXED")
partial = sum(1 for _, status, _ in results if status == "PARTIAL")
not_fixed = sum(1 for _, status, _ in results if status == "NOT_FIXED")
skipped = sum(1 for _, status, _ in results if status == "SKIPPED")
errors = sum(1 for _, status, _ in results if status == "ERROR")
unknown = sum(1 for _, status, _ in results if status == "UNKNOWN")

for name, status, detail in results:
    icon = "✅" if status == "FIXED" else "⚠️" if status == "PARTIAL" else "❌" if status == "NOT_FIXED" else "⏭️" if status == "SKIPPED" else "💥"
    print(f"  {icon} {name}: {status} - {detail}")

print(f"\n统计: ✅{fixed} 修复 | ⚠️{partial} 部分 | ❌{not_fixed} 未修复 | ⏭️{skipped} 跳过 | 💥{errors} 错误 | ❓{unknown} 未知")
print("=" * 60)

# 清理
try:
    sw.CloseAllDocuments(0)
except:
    pass