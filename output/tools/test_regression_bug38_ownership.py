
# -*- coding: utf-8 -*-
"""【Bug-38 回归测试】零件归属清单 + 越界校验 + 去重。"""
import sys, os, json, tempfile, shutil
sys.stdout.reconfigure(encoding='utf-8')
TOOLS = r'C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\engineering\tools'
sys.path.insert(0, TOOLS)
import importlib.util as ilu
spec = ilu.spec_from_file_location('wg38', os.path.join(TOOLS,'workflow_gate.py'))
wg = ilu.module_from_spec(spec); spec.loader.exec_module(wg)

tmp = tempfile.mkdtemp()
wg.BASE_DIR = tmp
wg.STATE_PATH = os.path.join(tmp,'workflow_state.json')
PASS=[];FAIL=[]
def check(n,c,d=""):
    (PASS if c else FAIL).append(n); print(("  [PASS] " if c else "  [FAIL] ")+n+(("  -> "+str(d)) if d else ""))

task = ("设计一台校内竞速小车，赛道4500x4500mm；3D打印")
ctx  = ("②长300×宽180×高120，轴距200 轮距150 ③单片3D打印底板+横梁骨架 "
        "④前置摄像头座+电池仓+电机座 ⑬PETG车架车轮ABS外壳Nylon齿轮 ⑭全部3D打印")
with open(wg.STATE_PATH,'w',encoding='utf-8') as f:
    json.dump({"task":task,"context":ctx,
               "subagent_config":{"rooms":[["结构件","structural"],
                                          ["传动机构","transmission"],
                                          ["壳体机架","housing"],
                                          ["支撑结构","support"]]}}, f, ensure_ascii=False)

print("=== Bug-38: 归属清单生成 ===")
man = wg.build_part_ownership(task, ctx,
        [["结构件","structural"],["传动机构","transmission"],
         ["壳体机架","housing"],["支撑结构","support"]])
check("schema 正确", man["schema"].startswith("dsh-engineering/part-ownership"))
check("任务类型=vehicle", man["task_type"]=="vehicle", man["task_type"])
check("含 4 个房间", len(man["rooms"])==4, list(man["rooms"]))
p2r = man["part_to_room"]
check("车架底板→结构件", p2r.get("车架底板")=="结构件", p2r.get("车架底板"))
check("车轮→传动机构", p2r.get("车轮")=="传动机构", p2r.get("车轮"))
check("上罩→壳体机架", p2r.get("上罩")=="壳体机架", p2r.get("上罩"))
check("舵机支架→支撑结构", p2r.get("舵机支架")=="支撑结构", p2r.get("舵机支架"))

print("\n=== Bug-38: 越界校验（台账真实案例）===")
# 台账：结构件小屋误产"电机座"（属传动机构）
r1 = wg.check_part_ownership("DSH_电机座.SLDPRT", "结构件")
check("结构件产出电机座 → 拒绝", r1["allowed"] is False, r1.get("hint","")[:60])
check("指出实际归属=传动机构", r1.get("owner_room")=="传动机构", r1.get("owner_room"))
# 台账：传动机构小屋误产"转向支撑座"（属支撑结构）
r2 = wg.check_part_ownership("DSH_转向支撑座.SLDPRT", "传动机构")
check("传动机构产出转向支撑座 → 拒绝", r2["allowed"] is False, r2.get("owner_room"))
# 正当产出
r3 = wg.check_part_ownership("DSH_车架底板.SLDPRT", "结构件")
check("结构件产出车架底板 → 允许", r3["allowed"] is True, r3.get("matched_keyword"))
r4 = wg.check_part_ownership("DSH_车轮.SLDPRT", "传动机构")
check("传动机构产出车轮 → 允许", r4["allowed"] is True, r4.get("matched_keyword"))

print("\n=== Bug-38: verify-ownership 去重 ===")
wd = os.path.join(tmp,"deliver"); os.makedirs(wd, exist_ok=True)
# 3 个正常零件 + 1 个"同名不同后缀大小写"的真重复（模拟两个小屋各存了一份）
for n in ("DSH_车架底板","DSH_车轮","DSH_上罩"):
    open(os.path.join(wd, n+".SLDPRT"),"w").write("x")
# 真重复：子目录里的同名零件（总装时最常见）
sub = os.path.join(wd, "结构件小屋"); os.makedirs(sub, exist_ok=True)
open(os.path.join(sub, "DSH_车架底板.SLDPRT"),"w").write("x")
with open(wg.STATE_PATH,'w',encoding='utf-8') as f:
    json.dump({"task":task,"context":ctx,"work_dir":wd,
               "subagent_config":{"rooms":[["结构件","structural"],
                                          ["传动机构","transmission"],
                                          ["壳体机架","housing"]]}}, f, ensure_ascii=False)
v = wg.verify_ownership(wd)
check("总零件数=4", v["total"]==4, v["total"])
check("检出重复 1 组", len(v["duplicates"])==1, v["duplicates"])
check("重复项=车架底板", v["duplicates"][0]["part"]=="车架底板", v["duplicates"][0]["part"])
check("按房间归类", "结构件" in v["by_room"] and "传动机构" in v["by_room"], list(v["by_room"]))

shutil.rmtree(tmp, ignore_errors=True)
print("\nPASS=%d FAIL=%d" % (len(PASS),len(FAIL)))
sys.exit(1 if FAIL else 0)
