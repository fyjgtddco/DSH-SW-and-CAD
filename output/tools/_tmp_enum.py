
import sys
sys.path.insert(0,".")
import comtypes.client
mod = comtypes.client.GetModule(r"Z:\Program Files\SOLIDWORKS Corp2025\SOLIDWORKS\swconst.tlb")
for n in ("swBodyType_e","swSelectType_e","swComponentSuppressionState_e"):
    o = getattr(mod, n, None)
    print("ENUM", n, o)
    if o is not None:
        try:
            for k,v in o.__members__.items():
                print("   ", k, int(v))
        except Exception as e:
            print("   ", e)
