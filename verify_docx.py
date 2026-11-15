# -*- coding: utf-8 -*-
from docx import Document
doc = Document(r"C:\Users\j1877\Desktop\DSH-SW-and-CAD-main\DSH工程模式_四类异常修复总结.docx")
paras = doc.paragraphs
print("total_paragraphs:", len(paras))
heads = [(p.style.name, p.text) for p in paras if p.style.name.startswith(("Title","Heading"))]
print("headings:", len(heads))
for h in heads:
    print("  ", h[0], "|", h[1])
bullets = [p.text for p in paras if p.style.name == "List Bullet"]
print("bullets:", len(bullets))
# spot-check key phrases
full = "\n".join(p.text for p in paras)
for key in ["子代理不受用户直接控制", "同一个零件被建很多次", "没有齿", "内存爆炸", "遗留事项", "共性原则", "STOP_ALL", "check_reusable", "_verify_gear_geometry", "SUBAGENT_LIMIT_REACHED"]:
    print("contains", key, ":", key in full)
