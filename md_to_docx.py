# -*- coding: utf-8 -*-
"""md -> docx: DSH 工程模式 四类异常修复总结"""
import sys
from docx import Document
from docx.shared import Pt, RGBColor
from docx.oxml.ns import qn

SRC = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\DSH工程模式_四类异常修复总结.md"
OUT = r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\DSH工程模式_四类异常修复总结.docx"

doc = Document()

def set_eastasia(style_or_run, font_name):
    rpr = style_or_run._element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:eastAsia"), font_name)

# --- base style: body ---
normal = doc.styles["Normal"]
normal.font.name = "Calibri"
normal.font.size = Pt(11)
set_eastasia(normal, "宋体")

# --- heading styles: Chinese fonts ---
for name, size in (("Title", 22), ("Heading 1", 16), ("Heading 2", 13)):
    st = doc.styles[name]
    st.font.name = "Calibri"
    st.font.size = Pt(size)
    st.font.color.rgb = RGBColor(0x1F, 0x3B, 0x5C)
    set_eastasia(st, "微软雅黑")

with open(SRC, "r", encoding="utf-8") as f:
    lines = f.read().split("\n")

for raw in lines:
    line = raw.rstrip()
    if not line.strip():
        continue
    if line.startswith("### "):
        doc.add_heading(line[4:].strip(), level=2)
    elif line.startswith("## "):
        doc.add_heading(line[3:].strip(), level=1)
    elif line.startswith("# "):
        doc.add_heading(line[2:].strip(), level=0)
    elif line.startswith("- "):
        doc.add_paragraph(line[2:].strip(), style="List Bullet")
    else:
        doc.add_paragraph(line.strip())

doc.save(OUT)
print("OK saved:", OUT)
