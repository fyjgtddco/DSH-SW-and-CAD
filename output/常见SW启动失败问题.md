# 常见 SW 启动失败问题排查手册

> 当按照正常方式启动 SolidWorks（SW）失败时，**优先按本文件顺序排查**。
> 排查顺序：Python 环境 → pywin32 → 许可证文件 → 主目录依赖库。

---

## 1. 检查 Python 版本是否正常

SW 桥接（`sw_bridge.py` / `swapi.py`）依赖 Python 运行环境。

- 执行：`python --version`
- 要求：Python 3.8+（开发环境为 3.14）
- 检查项：
  - 命令是否可用（`python` 是否在 PATH 中）
  - 位数：建议 64 位 Python（与 SolidWorks 位数匹配）
  - 若 `python` 指向错误版本，尝试 `py --version` 或 `py -3` 指定版本
  - `python -c "import sys; print(sys.executable)"` 确认实际解释器路径

## 2. pywin32 是否安装

SW 连接依赖 `win32com`（pywin32 提供的 COM 自动化接口），缺失会导致启动连接失败。

- 检查是否已安装：`python -c "import win32com.client; print('pywin32 OK')"`
- 若报 `ModuleNotFoundError`，安装：
  ```
  pip install pywin32
  ```
- 安装完成后建议运行 post-install 脚本（部分环境需要）：
  ```
  python -m pywin32_postinstall -install
  ```
- 常见错误与对应：
  - `ModuleNotFoundError: No module named 'win32com'` → pywin32 未安装或未安装到当前解释器
  - `TYPE_E_ELEMENTNOTFOUND` → 类型库注册不完整，属已知现象；`sw_bridge.py` 使用
    `win32com.client.dynamic.Dispatch`（IDispatch 后期绑定），不依赖类型库，通常可绕过

## 3. sw_d.lic 是否还存在

SolidWorks 许可证文件（`sw_d.lic`）若丢失或损坏，SW 无法正常启动。

- 该文件通常位于 SolidWorks 安装目录下的 `licenses` 目录（如
  `C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\licenses\` 等）
- 检查项：
  - 用 `sw_bridge.py doctor` 或文件管理器确认 `sw_d.lic` 是否存在
  - 若文件缺失，需恢复许可证文件（从备份/安装介质找回，或重新配置许可）
  - 若文件存在但启动仍失败，检查是否被杀毒软件隔离或权限异常

## 4. 主目录是否缺 loader netapi32.dll

SolidWorks 主目录（安装根目录）缺失 `netapi32.dll`（loader）会导致启动失败。

- 该 DLL 位于 SolidWorks 主安装目录（如 `C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\`）
- 检查项：
  - 确认主目录下是否存在 `netapi32.dll`
  - 若缺失，从可靠来源找回该文件并放置回 SolidWorks 主目录
  - 注意：此文件常被杀毒软件误删/隔离，找回后建议加入白名单
  - 若存在但启动仍失败，检查文件版本是否与 SW 版本匹配

## 5. SW 安装位置是否在 C 盘

SolidWorks 对安装路径较敏感，安装在非 C 盘（D/E 等）时启动/许可可能异常。

- 检查 SW 实际安装位置：
  - 默认位置：`C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\`
  - 用文件管理器或命令行确认：`where SLDWORKS.exe` 或查找 `SLDWORKS.exe`
- 若 SW 不在 C 盘：
  - **直接找到真实安装位置**，后续所有路径（sw_bridge 的 SW 路径、netapi32.dll、
    许可证目录）都要指向真实位置，而不是默认 C 盘路径
  - 确认 `SLDWORKS.exe` 存在且可执行
  - 若安装在非 C 盘仍频繁启动失败，考虑迁移回 C 盘或至少确认路径无中文/空格
    （安装路径含中文或特殊字符也可能导致 COM 注册/启动异常）

## 6. 是否有 SW 正在运行（SW 是单线程/单实例程序）

SolidWorks 是单线程主程序，**同一时刻通常只允许一个实例**。
若已有 SW 进程在运行，新的启动/COM 连接可能失败或挂起。

- 检查当前是否有 SW 进程在运行：
  ```
  tasklist | findstr /i "SLDWORKS"
  ```
  或任务管理器查看 `SLDWORKS.exe` / `sldworks_*.exe`
- 若已有 SW 在运行：
  - 直接复用当前实例（sw_bridge 会自动连接已运行的 SW），不要重复启动
  - 或先关闭已有 SW，再重新启动
  - 若多个残留进程占住，用任务管理器结束全部 `SLDWORKS.exe` 后再启动

## 7. 是否为 DSH 自身沙盒问题导致启动不了

DSH 环境下可能因为**沙盒/权限/隔离**导致 SW 启动或 COM 连接失败，
此时 SW 本身可能没问题，是运行环境限制了它。

- 排查 DSH 沙盒因素：
  - DSH 文件沙盒模式是否拦截了 SW 相关路径/注册表/进程操作
    （报错如 `[sandbox: file access denied]` 时说明是沙盒拦截）
  - COM 自动化（win32com 连接 `SldWorks.Application`）是否被隔离策略拦截
  - DSH 是否在受限账号/隔离进程中运行，导致无法访问 SW 的 COM 注册表项
- 处置：
  - 若确认是沙盒问题：为 SW 相关操作放宽沙盒（workspace-write / full access），
    或在非 DSH 的普通终端里直接测试 `sw_bridge.py` 连接 SW 以区分
  - 用 `python sw_bridge.py doctor` 自检，若自检通过但仍连不上 SW，
    优先怀疑沙盒/隔离而非 SW 本身
  - 普通终端能启动、DSH 里启动不了 → 基本可判定为 DSH 沙盒隔离问题

---

## 排查流程图（按顺序执行）

```
启动 SW 失败
    │
    ▼
① python --version            ← 版本是否正常（3.8+ / 64位）
    │ 不正常 → 修复 Python 环境
    ▼ 正常
② python -c "import win32com" ← pywin32 是否安装
    │ 缺失   → pip install pywin32
    ▼ 正常
③ 检查 sw_d.lic 是否存在      ← 许可证文件
    │ 缺失   → 恢复许可证文件
    ▼ 存在
④ 检查主目录 netapi32.dll     ← loader 依赖库
    │ 缺失   → 找回并放回主目录
    ▼ 存在
⑤ SW 安装位置是否在 C 盘      ← 安装路径
    │ 非C盘 → 找到真实安装位置，路径指向它
    ▼ 在C盘/已定位
⑥ 是否有 SW 正在运行          ← SW 单线程/单实例
    │ 有    → 复用当前实例或先关闭再启动
    ▼ 无
⑦ 是否为 DSH 沙盒问题         ← 运行环境隔离
    │ 是    → 放宽沙盒，或在普通终端验证
    ▼ 否
检查 sw_bridge.py doctor 自检结果，按报告定位下一步
```

> 修复后可用 `python <包目录>\sw_bridge.py doctor` 做环境自检，确认全部通过后再继续建模。
