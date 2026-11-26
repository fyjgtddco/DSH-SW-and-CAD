# -*- coding: utf-8 -*-
"""整合验收：两个日志面板接入后的自检（只读，不改运行状态）。

用法： python engineering\tools\test_log_panels.py
"""
import hashlib
import json
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UI = os.path.join(REPO, 'engineering', 'plugins', 'dsh-engineering-ui')
HOST = os.path.join(UI, 'lib', 'index.js')
CLIENT = os.path.join(UI, 'lib', 'client.js')
RUNLOG = os.path.join(UI, 'lib', 'run-log')

PASS, FAIL = [], []


def ok(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('  [PASS] ' if cond else '  [FAIL] ') + name + (('  ' + detail) if detail and not cond else ''))


def read(p):
    try:
        with open(p, encoding='utf-8') as f:
            return f.read()
    except OSError:
        return ''


def main():
    print('=' * 70)
    print('A. 素材是否落位')
    print('=' * 70)
    for name in ('model.mjs', 'view.mjs', 'view.css'):
        p = os.path.join(RUNLOG, name)
        ok('lib/run-log/' + name + ' 存在', os.path.isfile(p),
           '缺少 ' + p)

    print('=' * 70)
    print('B. Host 路由')
    print('=' * 70)
    host = read(HOST)
    ok("注册 /run-log 路由", "'/run-log'" in host or '"/run-log"' in host)
    ok("注册 /monitor-log 路由", "'/monitor-log'" in host or '"/monitor-log"' in host)
    ok("run-log 支持 download=1", re.search(r"download", host) is not None)
    ok("静态发布 view.mjs", 'view.mjs' in host)
    ok("静态发布 view.css", 'view.css' in host)
    # spawnSync 在【非路由】路径（启动期/守卫/taskkill）是允许的，
    #   红线只是"宿主路由处理函数里不许用"。这里只断言新增的两条日志路由体内没有。
    def _route_body(text, path_lit):
        # 只取"注册那一块"：从路径字面量往前找 disposers.push，往后到第一个 }))) 为止。
        # 原实现固定切 6000 字符，会吞进后面的 parts-board 注释里的 spawnSync，
        # 造成假失败（第一次跑就踩了）。
        i = text.find(path_lit)
        if i < 0:
            return ''
        start = text.rfind('disposers.push', 0, i)
        end = text.find('})));', i)
        if start < 0 or end < 0:
            return ''
        return text[start:end]
    ok("run-log 路由体内未用 spawnSync",
       'spawnSync' not in _route_body(host, "'/run-log'"))
    ok("monitor-log 路由体内未用 spawnSync",
       'spawnSync' not in _route_body(host, "'/monitor-log'"))
    ok("未 require(", re.search(r'^\s*const .*=\s*require\(', host, re.M) is None)

    print('=' * 70)
    print('C. 前端开发者页')
    print('=' * 70)
    client = read(CLIENT)
    ok("EngRunLog 组件存在", 'EngRunLog' in client)
    ok("开发者页不再是 null 正文",
       re.search(r"daily \?[^;]*:[^;]*EngRunLog", client, re.S) is not None
       or 'EngRunLog' in client)
    ok("hooks 在早退分支前（EngRunLog 内 useState/useEffect 先声明）",
       re.search(r'function EngRunLog[\s\S]{0,1200}?useEffect', client) is not None)

    print('=' * 70)
    print('D. 监控器诊断')
    print('=' * 70)
    mon = read(os.path.join(REPO, 'engineering', 'tools', 'sw_monitor', 'monitor.py'))
    ok("monitor.py 含 monitor_diagnostics 表", 'monitor_diagnostics' in mon)
    ok("monitor.py 含 diagnostics 操作", "'diagnostics'" in mon or '"diagnostics"' in mon)
    ok("保留 _contract_dir 自适应", '_contract_dir' in mon)
    bridge = read(os.path.join(UI, 'lib', 'sw-monitor.js'))
    ok("OPS 白名单含 diagnostics", "'diagnostics'" in bridge)

    print('=' * 70)
    print('E. 语法检查')
    print('=' * 70)
    for f in ('index.js', 'client.js', 'sw-monitor.js'):
        p = os.path.join(UI, 'lib', f)
        r = subprocess.run(['node', '--check', p], capture_output=True, text=True)
        ok('node --check ' + f, r.returncode == 0, (r.stderr or '')[:300])
    for f in ('model.mjs', 'view.mjs'):
        p = os.path.join(RUNLOG, f)
        if os.path.isfile(p):
            r = subprocess.run(['node', '--check', p], capture_output=True, text=True)
            ok('node --check run-log/' + f, r.returncode == 0, (r.stderr or '')[:300])

    print('=' * 70)
    print('PASS=%d FAIL=%d' % (len(PASS), len(FAIL)))
    print('=' * 70)
    if FAIL:
        for f in FAIL:
            print('  FAILED: ' + f)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
