# -*- coding: utf-8 -*-
"""回归测试：【Bug⑫】select_sketch(name_or_idx) 必须兼容中文名/索引并兜底

★ 锁定的真实缺陷（sw_bridge 的已知坑）：
  选草图【必须】用 select_sketch_by_index()，用中文名（"草图1"）在部分
  SW 版本 / locale 下会抛 DISP_E_XXX 找不到草图，建模直接失败
  （零件建到一半，白跑一轮）。工具链文档提示了这一点，但【代码里没有兜底】
  —— 全靠写脚本的人记住。

修复后的约定（本测试钉死）：
  A. 新增 swapi.select_sketch(sw, model, name_or_idx, index_fallback=None)。
  B. 入参解析：int / 纯数字串 → 按索引；其它字符串 → 按名字。
  C. 按名字时三级查找：FeatureByName → 特征树比对 → SelectByID2 原名。
  D. 中英文前缀互换（"草图2" ↔ "Sketch2"）必须可用。
  E. 名字彻底找不到 → 回退索引，并在 warnings 里显式说明（绝不静默）。
  F. 索引越界 / 空标识 → ok=False + error + available_sketches（不谎报成功）。
  G. 全程只做"选择"操作，绝不改几何。
  H. select_sketch_by_index / select_sketch_by_name 既有 API 不被破坏。

⚠️ 本测试只用【假对象(mock)】，【不连接 SolidWorks】、不启动任何进程。

运行：python test_regression_bug12_select_sketch.py
退出码：0 = 全部通过；1 = 有失败
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

PASS = 0
FAIL = 0
FAILURES = []


def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % label)
    else:
        FAIL += 1
        FAILURES.append(label)
        print("  [FAIL] %s %s" % (label, extra))


def section(t):
    print("\n" + "=" * 70)
    print(t)
    print("=" * 70)


# ══════════════════════════════════════════════════════════════════════
#  假 COM 对象：按本仓库既有约定，【无参成员用 property 暴露】
#  （late-binding/dynamic dispatch 下按属性访问，写成方法会拿到 bound method）。
# ══════════════════════════════════════════════════════════════════════
class FakeFeature(object):
    def __init__(self, name, type_name="Sketch", nxt=None):
        self._name = name
        self._type = type_name
        self._next = nxt

    @property
    def Name(self):
        return self._name

    @property
    def GetTypeName2(self):
        return self._type

    @property
    def GetNextFeature(self):
        return self._next


class FakeFeatureManager(object):
    def __init__(self, feats):
        # feats: [(name, type)] → 串成 GetNextFeature 链
        self._nodes = [FakeFeature(n, t) for (n, t) in feats]
        for a, b in zip(self._nodes, self._nodes[1:]):
            a._next = b
        self._first = self._nodes[0] if self._nodes else None

    @property
    def FirstFeature(self):
        return self._first


class FakeModel(object):
    """假 IDocument：只暴露选择草图真正用到的成员。

    select_by_id_works_name: 哪些名字 SelectByID2 能选中（模拟 locale 差异）。
    feature_by_name:         FeatureByName 能返回哪些名字。
    """

    def __init__(self, feats, select_by_id_works_name=None,
                 feature_by_name=None):
        self.FeatureManager = FakeFeatureManager(feats)
        self._sel_ok = (select_by_id_works_name
                        if select_by_id_works_name is not None
                        else set(n for n, _t in feats))
        self._fbn = (feature_by_name if feature_by_name is not None
                     else set(n for n, _t in feats))
        self.selected = []
        self.clear_count = 0
        self.cleared = 0

    def ClearSelection2(self, flag):
        self.clear_count += 1
        return True

    def FeatureByName(self, name):
        if name in self._fbn:
            for f in self._all():
                if f.Name == name:
                    return f
        return None

    def _all(self):
        out, f = [], self.FeatureManager.FirstFeature
        while f is not None:
            out.append(f)
            f = f.GetNextFeature
        return out

    @property
    def Extension(self):
        return _FakeExt(self)


class _FakeExt(object):
    def __init__(self, model):
        self._m = model

    def SelectByID2(self, name, typ, x, y, z, append, mark, callout, selopt):
        if name in self._m._sel_ok:
            self._m.selected.append(name)
            return True
        return False


# 典型零件特征树：三个草图 + 若干非草图特征（要能被正确跳过）
FEATS = [
    ("Sketch1", "ProfileFeature"),
    ("Boss-Extrude1", "Extrusion"),
    ("草图2", "Sketch"),
    ("Cut-Extrude1", "Cut"),
    ("Sketch3", "Sketch"),
]


class FakeFeatureWithTypeName1(FakeFeature):
    """有些 SW 版本/特征上 GetTypeName2 为空，只有 GetTypeName 返回 "Sketch"。"""

    def __init__(self, name, type_name="Sketch", nxt=None, type1="Sketch"):
        FakeFeature.__init__(self, name, type_name, nxt)
        self._type1 = type1

    @property
    def GetTypeName2(self):
        return ""

    @property
    def GetTypeName(self):
        return self._type1


def main():
    import swapi

    # ═══════════════════════════════════════════════════════════════
    section("1. ★ API 存在性（工具链文档承诺的统一入口）")
    check("swapi.select_sketch 存在", hasattr(swapi, "select_sketch"))
    check("签名含 name_or_idx / index_fallback",
          "name_or_idx" in str(getattr(swapi, "select_sketch").__code__.co_varnames))
    check("既有 select_sketch_by_index 未被破坏",
          callable(getattr(swapi, "select_sketch_by_index", None)))
    check("既有 select_sketch_by_name 未被破坏",
          callable(getattr(swapi, "select_sketch_by_name", None)))

    # ═══════════════════════════════════════════════════════════════
    section("2. ★ 按索引选草图（最可靠路径，跨 locale）")
    for idx, expect in ((1, "Sketch1"), (2, "草图2"), (3, "Sketch3")):
        m = FakeModel(FEATS)
        r = swapi.select_sketch(None, m, idx)
        check("索引 %d → 选中 %r（跳过非草图特征）" % (idx, expect),
              r.get("ok") and r.get("count") == 1
              and r.get("sketch_name") == expect, r)
        check("  索引 %d 的 method == index" % idx,
              r.get("method") == "index", r)
        check("  索引 %d 未产生 warning（索引本就不需要兜底）" % idx,
              not r.get("warnings"), r.get("warnings"))

    # 纯数字字符串等价于整数（CLI / 脚本常传字符串）
    m = FakeModel(FEATS)
    r = swapi.select_sketch(None, m, "2")
    check("纯数字串 \"2\" 按索引处理 → 草图2",
          r.get("ok") and r.get("sketch_name") == "草图2" and r.get("method") == "index",
          r)

    # ═══════════════════════════════════════════════════════════════
    section("3. ★ 按名字选草图（中文名 / 英文名都行）")
    m = FakeModel(FEATS)
    r = swapi.select_sketch(None, m, "草图2")
    check("中文名 草图2 选中成功（Bug⑫ 原本会 DISP_E）",
          r.get("ok") and r.get("sketch_name") == "草图2", r)
    check("method 为 feature_by_name（首级查找命中）",
          r.get("method") == "feature_by_name", r)
    check("回填正确索引（草图2 是第 2 个草图）", r.get("index") == 2, r)

    m = FakeModel(FEATS)
    r = swapi.select_sketch(None, m, "Sketch1")
    check("英文名 Sketch1 选中成功",
          r.get("ok") and r.get("sketch_name") == "Sketch1", r)

    # 前缀互换：脚本写 "Sketch2"，但零件里实际叫 "草图2"
    m = FakeModel(FEATS, select_by_id_works_name={"草图2"},
                  feature_by_name=set())
    r = swapi.select_sketch(None, m, "Sketch2")
    check("★ 前缀互换：Sketch2 → 命中真实名 草图2",
          r.get("ok") and r.get("sketch_name") == "草图2", r)
    check("  穿越特征树/原名兜底，method 非空", bool(r.get("method")), r)

    # ═══════════════════════════════════════════════════════════════
    section("4. ★ 名字找不到 → 回退索引 + 显式 warning（绝不静默）")
    # 场景：脚本写死 "草图9"，零件里根本没有 —— 尾部数字 9 越界，
    #       显式给 index_fallback=2 才能救回来。
    m = FakeModel(FEATS)
    r = swapi.select_sketch(None, m, "草图9", index_fallback=2)
    check("★ 回退成功（否则建模会直接断在这一步）",
          r.get("ok") and r.get("count") == 1, r)
    check("method == fallback_index", r.get("method") == "fallback_index", r)
    check("sketch_name == 草图2（按 index_fallback 落位）",
          r.get("sketch_name") == "草图2", r)
    check("★ warnings 非空且指名道姓（绝不静默替换）",
          bool(r.get("warnings")) and "草图9" in str(r.get("warnings")),
          r.get("warnings"))
    check("fallback 标志为 True（调用方据此可感知）",
          r.get("fallback") is True, r)

    # 未给 index_fallback 时，从名字尾部数字推断（"草图2x"→2 不行，"草图2"→2）
    m = FakeModel(FEATS)
    r = swapi.select_sketch(None, m, "草图2")
    check("名字能直接命中时不会误走回退", r.get("fallback") is not True, r)

    # ═══════════════════════════════════════════════════════════════
    section("5. ★ 失败必须如实回报（不谎报成功）")
    m = FakeModel(FEATS)
    r = swapi.select_sketch(None, m, 99)
    check("索引越界 → ok=False", r.get("ok") is False, r)
    check("索引越界 → count=0", r.get("count") == 0, r)
    check("索引越界 → error 说明原因", bool(r.get("error")), r)
    check("★ 索引越界 → 列出真实可用草图（可自救）",
          r.get("available_sketches") == ["Sketch1", "草图2", "Sketch3"],
          r.get("available_sketches"))

    m = FakeModel(FEATS)
    r = swapi.select_sketch(None, m, "")
    check("空标识 → ok=False + error",
          r.get("ok") is False and bool(r.get("error")), r)
    r = swapi.select_sketch(None, None, 1)
    check("model=None → ok=False + error（不抛异常）",
          r.get("ok") is False and bool(r.get("error")), r)

    # 名字找不到且没有任何可回退线索
    m = FakeModel(FEATS, select_by_id_works_name=set(),
                  feature_by_name=set())
    r = swapi.select_sketch(None, m, "完全不存在的草图")
    check("名字查无且无回退索引 → ok=False（不假装成功）",
          r.get("ok") is False, r)
    check("  给出 available_sketches 供纠正",
          bool(r.get("available_sketches")), r)

    # ═══════════════════════════════════════════════════════════════
    section("6. ★ 只做选择、绝不改几何（选择前必先清空选择集）")
    m = FakeModel(FEATS)
    swapi.select_sketch(None, m, 1)
    check("选择前调用了 ClearSelection2（避免叠加旧选择集）",
          m.clear_count >= 1, m.clear_count)
    check("模型上没有任何写入类调用（本测试的 FakeModel 不含建模 API）",
          not hasattr(m, "EditRebuild3") and not hasattr(m, "InsertSketch"),
          [k for k in dir(m) if not k.startswith("_")])

    # ═══════════════════════════════════════════════════════════════
    section("7. 特征树遍历必须只认草图特征（不把拉伸/切除当草图）")
    feats = swapi._iter_sketch_features(FakeModel(FEATS))
    names = [f[1] for f in feats]
    check("★ 只收集 3 个草图特征（Extrusion/Cut 被跳过）",
          names == ["Sketch1", "草图2", "Sketch3"], names)
    check("序号从 1 开始（1 基，符合调用方直觉）",
          [f[0] for f in feats] == [1, 2, 3], [f[0] for f in feats])

    # SketchBlock / SketchPicture 不是可拉伸草图，必须排除
    feats2 = swapi._iter_sketch_features(FakeModel([
        ("Sketch1", "Sketch"),
        ("Block1", "SketchBlock"),
        ("Picture1", "SketchPicture"),
        ("Sketch2", "3DSketch"),
    ]))
    check("★ SketchBlock / SketchPicture 被排除，3DSketch 被纳入",
          [f[1] for f in feats2] == ["Sketch1", "Sketch2"],
          [f[1] for f in feats2])

    # GetTypeName2 为空、只有 GetTypeName 返回 "Sketch" 的版本也要认出来
    class _M(object):
        pass

    _m = _M()
    _n1 = FakeFeatureWithTypeName1("Sketch1")
    _n2 = FakeFeatureWithTypeName1("草图2")
    _n1._next = _n2

    class _FM(object):
        FirstFeature = _n1

    _m.FeatureManager = _FM()
    check("★ GetTypeName2 为空时回退 GetTypeName 仍能识别草图",
          [f[1] for f in swapi._iter_sketch_features(_m)] == ["Sketch1", "草图2"],
          [f[1] for f in swapi._iter_sketch_features(_m)])

    # ═══════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  - " + f)
    print("=" * 70)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
