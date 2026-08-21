# -*- coding: utf-8 -*-
import win32com.client, pythoncom, sys, os
sys.path.insert(0, r'C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools')
import sw_bridge

pythoncom.CoInitialize()
sw = win32com.client.dynamic.Dispatch('SldWorks.Application')
print(f'SW: {sw.RevisionNumber}')

part_path = r'C:\Users\j1877\Desktop\DSH-Check\DSH_三角锥底座.sldprt'
out = r'C:\Users\j1877\Desktop\dsh-engineering-mode\engineering\tools\test_bbox_layout.slddrw'
result = sw_bridge.cmd_drawing(sw, part_path, out)
print(f'Result: ok={result["ok"]}, views={result["views"]}, saved={result.get("saved")}')
