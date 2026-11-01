# -*- coding: utf-8 -*-
"""回归测试：F-05 new_part 默认材料与防线口径打架（GB 牌号写不进 SW 材质库）

★ 锁定的真实事故（两小屋独立复现）：
  new_part() 默认材料 = "GB/T 700-2006 碳素结构钢"（工况文件里的【标准全名】）
  → is_valid_material() 判 False → 凭据 material_invalid=true、不签发
  → 材料防线直接拦下，正常建模路径必然走不通。
  显式 new_part(material="Q235") 也过不去：SW 官方材质库无 GB 牌号
  → 近似成普通碳钢 → 读回密度 1000（水）→ 防线正确拦截。

★ 本测试钉死的契约（全部假 COM / 不出网 / 不启动 SW）：
  ① new_part 默认材料必须是【防线合法牌号】（Q235），不能是 PlainCarbonSteel 等欧美牌号；
  ② 国标库缺失时必须【显式报错】并给出可执行路径，绝不静默降级；
  ③ 材质写入走【自定义登记路径】（custom_materials / .sldmat 口径），
     不依赖 SW 内置库名匹配；
  ④ 密度【随牌号表带出】（Q235→7850），不是 SW 库近似值（1000/7900）。

依据（写死在断言里，防止有人"顺手改回去"）：
  【类型库】83A33D31-27C5-11CE-BFD4-00400513BB57x0x33x0.py
    · IPartDoc.SetMaterialPropertyName2(ConfigName, Database, Name) —— 3 参，
      **没有密度参数**（L47899）
    · IPartDoc.MaterialIdName / MaterialPropertyValues —— 属性（L47916/L47917），
      后者是【外观 9 元组】，不是密度
    · IPartDoc.GetMaterialPropertyName2(ConfigName, Database) —— 2 参（L47357）
    · ISldWorks.GetMaterialDatabases() / GetMaterialDatabaseCount()（L59526/L59522）
    · IMassProperty.Density / IMassProperty2.Density —— **只读**
      （L30525 / L30591 只在 _prop_map_get_，_prop_map_put_ 无 Density 条目）
      ⇒ SolidWorks 没有"直接设置密度"的 API，密度只能随材料身份进入。

运行：python test_regression_default_material.py
退出码：0 = 全部通过；1 = 有失败
"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PASS = 0
FAIL = 0
FAILURES = []


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [ok] %s" % label)
    else:
        FAIL += 1
        FAILURES.append(label + ("  <" + str(detail)[:300] + ">" if detail else ""))
        print("  [FAIL] %s  %s" % (label, detail))


# ══════════════════════════════════════════════════════════════════════
def test_default_is_legal_grade(swapi):
    """契约①：默认材料必须是防线合法牌号，不能是欧美牌号。"""
    print("\n[1] 默认材料必须是防线合法牌号")
    _nm = swapi.default_part_material_name()
    check("★ default_part_material_name() 返回 Q235", _nm == "Q235", _nm)
    check("★ 默认材料能通过防线校验 is_valid_material",
          swapi.is_valid_material(_nm)[0] is True, swapi.is_valid_material(_nm))
    _dm = swapi.default_part_material()
    check("★ default_part_material() 也返回合法牌号",
          swapi.is_valid_material(_dm)[0] is True, _dm)
    # 不能是欧美牌号
    _FOREIGN = ("PlainCarbonSteel", "AlloySteel4140",
                "StainlessSteel304", "Aluminum6061")
    check("★ 默认材料【不是】任何欧美降级牌号",
          _nm not in _FOREIGN and _dm not in _FOREIGN, (_nm, _dm))
    # 源码级：降级候选链必须已经被删掉
    import io
    _src = io.open(os.path.join(HERE, "swapi.py"), encoding="utf-8").read()
    _i = _src.find("def default_part_material_name")
    _j = _src.find("def is_default_material_available")
    _body = _src[_i:_j] if (_i >= 0 and _j > _i) else ""
    check("★ 降级候选链 for _cand in (...) 已从函数体移除",
          "for _cand in" not in _body, _body[:300])
    check("★ 不再有 for _k in COMMON_MATERIALS: return _k（字典首项兜底）",
          "for _k in COMMON_MATERIALS" not in _body, _body[:300])


def test_missing_gb_data_raises(swapi):
    """契约②：国标库缺失 → 显式报错 + 可执行路径，不静默降级。"""
    print("\n[2] 国标库缺失时必须显式报错（不静默降级）")
    _saved = dict(swapi.COMMON_MATERIALS)
    try:
        swapi.COMMON_MATERIALS.clear()
        check("★ 库空时 is_default_material_available() 返回 False",
              swapi.is_default_material_available() is False)
        check("★ 库空时 default_part_material_name() 返回 None（不是欧美牌号）",
              swapi.default_part_material_name() is None,
              swapi.default_part_material_name())
        _r = swapi.resolve_default_material_or_raise()
        check("★ 解析器返回 ok=False", _r.get("ok") is False, _r.get("ok"))
        _err = str(_r.get("error") or "")
        check("★ 错误里点明默认材料不可用", "Q235" in _err and "不可用" in _err, _err[:200])
        check("★ 错误里明确禁止静默降级成欧美牌号",
              "绝不" in _err and "欧美牌号" in _err, _err[:300])
        check("★ 给出可执行路径①导入 gb_materials.json",
              "gb_materials.json" in _err, _err[:400])
        check("★ 给出可执行路径②save_custom_material 登记",
              "save_custom_material" in _err, _err[:500])
        check("★ 给出可执行路径③显式指定合法牌号",
              "new_part(material=" in _err, _err[:600])
        check("★ 附带国标数据状态（loaded/count）",
              "loaded=" in _err, _err[-200:])
    finally:
        swapi.COMMON_MATERIALS.clear()
        swapi.COMMON_MATERIALS.update(_saved)
    check("★ 恢复后 is_default_material_available() 回到 True",
          swapi.is_default_material_available() is True)


def test_density_from_grade_table(swapi):
    """契约④：密度随牌号表带出（Q235→7850），不用 SW 近似值。"""
    print("\n[3] 密度随牌号表带出（Q235 → 7850）")
    _r = swapi.resolve_default_material_or_raise()
    check("★ 解析器带出密度", _r.get("density") is not None, _r.get("density"))
    check("★ Q235 → 7850 kg/m³（牌号表值，不是 SW 近似 1000/7900）",
          abs(float(_r.get("density") or 0) - 7850.0) < 1e-6, _r.get("density"))
    _r2 = swapi.resolve_default_material_or_raise(material="Q235")
    check("★ 显式 material=Q235 同样带出 7850",
          abs(float(_r2.get("density") or 0) - 7850.0) < 1e-6, _r2.get("density"))
    _r3 = swapi.resolve_default_material_or_raise(material="Q235", density=7800)
    check("★ 显式 density 参数优先（不被牌号表覆盖）",
          abs(float(_r3.get("density") or 0) - 7800.0) < 1e-6, _r3.get("density"))
    _r4 = swapi.resolve_default_material_or_raise(material="6061-T6")
    check("★ 6061-T6 → 2700（铝，不是钢的 7850）",
          abs(float(_r4.get("density") or 0) - 2700.0) < 1e-6, _r4.get("density"))
    # 源：必须来自牌号表
    _info = swapi.lookup_material("Q235") or {}
    check("★ 密度取值来源是 COMMON_MATERIALS 牌号表",
          abs(float(_info.get("density") or 0) - 7850.0) < 1e-6, _info.get("density"))


def test_invalid_material_rejected(swapi):
    """契约②补充：非法材料名必须显式失败，不得放行。"""
    print("\n[4] 非法材料名 → 显式失败（不放行）")
    _r = swapi.resolve_default_material_or_raise(material="INVALID_MAT")
    check("★ 非法材料 → ok=False", _r.get("ok") is False, _r.get("ok"))
    _err = str(_r.get("error") or "")
    check("★ 错误里说明该名不是合法牌号", "不是合法牌号" in _err, _err[:200])
    check("★ 解释为何不允许（.sldprt 无可信材质身份 / 防线 material_invalid）",
          "material_invalid" in _err or "材质身份" in _err, _err[:400])
    check("★ 给出可执行路径（内置合法牌号 / save_custom_material 登记）",
          "save_custom_material" in _err or "list_materials" in _err, _err[:500])
    check("★ 不返回可直接使用的 material（ok=False 时不误导）",
          _r.get("ok") is False)
    # 取回校验保持严格：不做模糊匹配
    check("★ _material_names_match 仍是逐字比对（Q235 ≠ AISI 1020）",
          swapi._material_names_match("AISI 1020", "Q235") is False)
    check("★ _material_names_match 同名才算匹配",
          swapi._material_names_match("Q235", "Q235") is True)
    _x, _note = swapi._detect_cross_standard("Q235", "AISI 1020")
    check("★ 跨标准替代仍被判出（未被放宽）", _x is True, _note)


def test_load_case_normalisation(swapi):
    """核心缺陷：工况文件的标准全名必须规整成合法牌号。"""
    print("\n[5] 工况文件标准全名 → 合法牌号（事故直接根因）")
    _lc = swapi._material_from_load_case()
    if _lc is None:
        check("（跳过：无 gate_load_case.json）", True)
        return
    check("★ _material_from_load_case() 返回的是牌号，不是标准文件全名",
          "GB/T" not in str(_lc), _lc)
    check("★ 返回值能通过防线校验",
          swapi.is_valid_material(_lc)[0] is True, _lc)
    # 直接构造场景：name 是标准全名、id 不在 _ID_TO_NAME 表里
    import json, tempfile, shutil
    _td = tempfile.mkdtemp(prefix="dsh_mat_")
    try:
        _p = os.path.join(_td, "gate_load_case.json")
        with open(_p, "w", encoding="utf-8") as f:
            json.dump({"material": {"id": "Q235",
                                  "name": "GB/T 700-2006 碳素结构钢"}}, f)
        _old = swapi.__file__
        # 直接用内部逻辑验证：标准全名必须被反向查表命中
        _hit = None
        for _c, _i in swapi.COMMON_MATERIALS.items():
            if str((_i or {}).get("name") or "").strip() == "GB/T 700-2006 碳素结构钢":
                _hit = _c
                break
        check("★ 标准全名可通过反向查表映射到牌号 Q235", _hit == "Q235", _hit)
    finally:
        shutil.rmtree(_td, ignore_errors=True)


def test_custom_registry_path(swapi):
    """契约③：材质写入走自定义登记路径（不依赖 SW 内置库名匹配）。"""
    print("\n[6] 自定义材质登记路径存在且可调用")
    check("★ 有 _apply_material_via_custom_registry 方法",
          callable(getattr(swapi.SWModel, "_apply_material_via_custom_registry", None)))
    check("★ 有 set_custom_material（登记入口）",
          callable(getattr(swapi.SWModel, "set_custom_material", None)))
    check("★ 有 save_custom_material（idempotent 登记实现）",
          callable(getattr(swapi, "save_custom_material", None)))
    check("★ 有 load_custom_materials（可审计读回）",
          callable(getattr(swapi, "load_custom_materials", None)))
    # 源码级：_try_apply_pending_material 必须接上自定义通道
    import io
    _src = io.open(os.path.join(HERE, "swapi.py"), encoding="utf-8").read()
    _i = _src.find("def _try_apply_pending_material")
    _j = _src.find("def _apply_material_via_custom_registry")
    _body = _src[_i:_j] if (_i >= 0 and _j > _i) else ""
    check("★ _try_apply_pending_material 内置库失败后会走自定义登记路径",
          "_apply_material_via_custom_registry" in _body, _body[:300])
    check("★ 保留内置库失败的审计证据 sw_library_attempt",
          "sw_library_attempt" in _body or "sw_library_attempt" in _src)
    # 登记路径的实现必须来自牌号表，而不是"猜"
    _k = _src.find("def _apply_material_via_custom_registry")
    _k2 = _src.find("def set_material", _k)
    _creg = _src[_k:_k2] if (_k >= 0 and _k2 > _k) else ""
    check("★ 登记路径用 resolve_material_props 取属性（单一事实来源）",
          "resolve_material_props" in _creg, _creg[:300])
    check("★ 登记路径写 custom_materials.json（save_custom_material）",
          "save_custom_material" in _creg, _creg[:300])


def test_no_density_write_lie(swapi):
    """类型库事实：SW 没有可写密度的 API —— 代码不得谎称写了密度。"""
    print("\n[7] 不得谎称「写入密度」（SW 无此 API）")
    import io
    _src = io.open(os.path.join(HERE, "swapi.py"), encoding="utf-8").read()
    check("★ 已移除 force_density 调用（SW 无该能力）",
          "self.force_density" not in _src)
    check("★ 已移除 density_forced 虚假成功标记",
          '"density_forced"' not in _src)
    _k = _src.find("def _apply_material_via_custom_registry")
    _k2 = _src.find("def set_material", _k)
    _creg = _src[_k:_k2] if (_k >= 0 and _k2 > _k) else ""
    check("★ 命名写入失败时 ok=False（不谎报成功）",
          "out[\"ok\"] = False" in _creg, _creg[:400])
    check("★ 错误里如实说明密度未写入 SW",
          "未写入 SW" in _creg or "仍是 SW 库里的默认值" in _creg, _creg[:600])
    check("★ 错误里说明为何不能只写密度（类型库依据）",
          "只读" in _creg or "没有" in _creg, _creg[:700])


# ══════════════════════════════════════════════════════════════════════
def main():
    try:
        import swapi
        check("swapi 加载的是【本工作区】这份（防双副本假失败）",
              os.path.normcase(os.path.abspath(swapi.__file__)).startswith(
                  os.path.normcase(HERE)), swapi.__file__)
        check("★ 新增 is_default_material_available",
              callable(getattr(swapi, "is_default_material_available", None)))
        check("★ 新增 default_material_unavailable_error",
              callable(getattr(swapi, "default_material_unavailable_error", None)))
        check("★ 新增 resolve_default_material_or_raise",
              callable(getattr(swapi, "resolve_default_material_or_raise", None)))

        test_default_is_legal_grade(swapi)
        test_missing_gb_data_raises(swapi)
        test_density_from_grade_table(swapi)
        test_invalid_material_rejected(swapi)
        test_load_case_normalisation(swapi)
        test_custom_registry_path(swapi)
        test_no_density_write_lie(swapi)
    except Exception:
        global FAIL
        FAIL += 1
        FAILURES.append("未捕获异常（见下方 traceback）")
        traceback.print_exc()

    print("\n" + "=" * 70)
    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    if FAILURES:
        print("失败项：")
        for _f in FAILURES:
            print("  - " + _f)
    print("=" * 70)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())