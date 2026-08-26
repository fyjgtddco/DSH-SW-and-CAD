# 🔧 DSH Engineering Mode — 工程模式 / Mode Ingénierie / Инженерный Режим / وضع الهندسة / Engineering Mode

> [中文](#中文) · [English](#english) · [Français](#français) · [Español](#español) · [Русский](#русский)

---

## 中文

### 概述

**工程模式** 是 [DeepSeek Harness](https://github.com/deepseek-ai/dsh) 的一个 Agent Preset，专门用于 **CAD/SolidWorks 机械设计**。

它让 AI 化身为专业的机械设计工程师，以"**完成工图**"为唯一核心目标，严格执行"分析→设计→验证"闭环流程。

### 特性

- 🎯 **目标驱动**：以"完成工图"为唯一核心目标，所有行动都围绕这一目标展开
- 📐 **专业流程**：严格执行"分析→设计→验证"三部曲
- 🔄 **闭环验证**：每完成一个零件必须验证，每完成一次装配必须检查干涉
- 🛠️ **CAD/SW 就绪**：内置 AutoCAD 和 SolidWorks 工作流 Skill
- 📋 **计划模式**：工程设计导向的计划模式，强调设计评审和方案验证
- 🎯 **Goal 追踪**：使用 Goal 系统追踪"完成工图"的总体目标
- 🔬 **物理验证**：内置 2D/3D FEA 有限元求解器（纯 Python + numpy），无需外部软件

### 设计流程

```
第一阶段：需求分析与规划
  ├── 理解需求
  ├── 尺寸合理性分析
  ├── 运行可行性分析
  ├── 制定设计计划
  └── 创建 Goal 目标

第二阶段：详细设计
  ├── 草图绘制
  ├── 特征建模
  ├── 零件验证（FEA 有限元分析）
  ├── 组合零件
  └── 输出中间结果

第三阶段：装配与验证
  ├── 总装配
  ├── 干涉检查
  ├── 运动模拟
  ├── 最终验证
  ├── 输出工程图纸
  └── 更新 Goal 为完成
```

### 安装

#### 一键安装（推荐）

**Windows（PowerShell）：**

```powershell
# 以管理员身份运行 PowerShell，然后执行：
.\install.ps1
```

**macOS / Linux：**

```bash
chmod +x install.sh
./install.sh
```

#### 手动安装

1. 将 `engineering/` 目录复制到 DSH 的 Agent Presets 目录：

   **Windows：**
   ```powershell
   Copy-Item -Recurse .\engineering\ $env:USERPROFILE\.dsh\.agent-presets\engineering\
   ```

   **macOS / Linux：**
   ```bash
   cp -r ./engineering/ ~/.dsh/.agent-presets/engineering/
   ```

2. **可选**：安装 CAD 工作流 Skill：

   **Windows：**
   ```powershell
   Copy-Item -Recurse .\engineering\skills\cad-workflow\ $env:USERPROFILE\.dsh\skills\cad-workflow\
   Copy-Item -Recurse .\engineering\skills\sw-design\ $env:USERPROFILE\.dsh\skills\sw-design\
   ```

   **macOS / Linux：**
   ```bash
   cp -r ./engineering/skills/cad-workflow/ ~/.dsh/skills/cad-workflow/
   cp -r ./engineering/skills/sw-design/ ~/.dsh/skills/sw-design/
   ```

### 使用

1. 安装完成后，重启 DeepSeek Harness
2. 在会话界面中，选择模式为 **工程模式**（Engineering Mode）
3. AI 将以机械设计工程师的身份开始工作
4. 告诉它你的设计需求，它会自动执行"分析→设计→验证"流程

---

## English

### Overview

**Engineering Mode** is an Agent Preset for [DeepSeek Harness](https://github.com/deepseek-ai/dsh), specifically designed for **CAD/SolidWorks mechanical design**.

It transforms the AI into a professional mechanical design engineer with a single core mission: **"Complete the Engineering Drawing"**.

### Features

- 🎯 **Goal-Driven**: Everything revolves around completing the engineering drawing
- 📐 **Professional Workflow**: Strictly follows the "Analyze → Design → Verify" trilogy
- 🔄 **Closed-Loop Validation**: Every part is verified, every assembly is checked for interference
- 🛠️ **CAD/SW Ready**: Built-in AutoCAD and SolidWorks workflow skills
- 📋 **Plan Mode**: Engineering-oriented planning with design review emphasis
- 🎯 **Goal Tracking**: Uses the Goal system to track the overall objective
- 🔬 **Physics Verification**: Built-in 2D/3D FEA solver (pure Python + numpy), no external software required

### Design Process

```
Phase 1: Requirements Analysis & Planning
  ├── Understand requirements
  ├── Dimensional feasibility analysis
  ├── Operational feasibility analysis
  ├── Create design plan
  └── Set Goal

Phase 2: Detailed Design
  ├── Sketching
  ├── Feature modeling
  ├── Part validation (FEA)
  ├── Assembly
  └── Intermediate results

Phase 3: Assembly & Verification
  ├── Final assembly
  ├── Interference check
  ├── Motion simulation
  ├── Final verification
  ├── Engineering drawings
  └── Mark Goal as complete
```

### Installation

**Windows (PowerShell):**
```powershell
.\install.ps1
```

**macOS / Linux:**
```bash
chmod +x install.sh
./install.sh
```

### Usage

1. Restart DeepSeek Harness after installation
2. In the session interface, select **Engineering Mode**
3. The AI will act as a mechanical design engineer
4. Tell it your design requirements, and it will follow the "Analyze → Design → Verify" workflow

---

## Français

### Aperçu

**Mode Ingénierie** est un Agent Preset pour [DeepSeek Harness](https://github.com/deepseek-ai/dsh), spécialement conçu pour la **conception mécanique CAD/SolidWorks**.

Il transforme l'IA en ingénieur en conception mécanique professionnel, avec une mission principale : **"Terminer le plan d'usinage"**.

### Fonctionnalités

- 🎯 **Piloté par objectif** : Tout tourne autour de l'achèvement du plan technique
- 📐 **Workflow professionnel** : Suit strictement la trilogie "Analyser → Concevoir → Vérifier"
- 🔄 **Validation en boucle fermée** : Chaque pièce est vérifiée, chaque assemblage est contrôlé
- 🛠️ **Prêt pour CAD/SW** : Compétences intégrées AutoCAD et SolidWorks
- 📋 **Mode Plan** : Planification orientée ingénierie avec revue de conception
- 🎯 **Suivi Goal** : Utilise le système Goal pour suivre l'objectif global
- 🔬 **Vérification physique** : Solveur FEA 2D/3D intégré (Python pur + numpy), aucun logiciel externe requis

### Processus de conception

```
Phase 1 : Analyse des exigences et planification
  ├── Comprendre les exigences
  ├── Analyse de faisabilité dimensionnelle
  ├── Analyse de faisabilité opérationnelle
  ├── Créer le plan de conception
  └── Définir l'objectif Goal

Phase 2 : Conception détaillée
  ├── Dessin de croquis
  ├── Modélisation par caractéristiques
  ├── Validation des pièces (FEA)
  ├── Assemblage
  └── Résultats intermédiaires

Phase 3 : Assemblage et vérification
  ├── Assemblage final
  ├── Vérification des interférences
  ├── Simulation cinématique
  ├── Vérification finale
  ├── Plans techniques
  └── Marquer Goal comme terminé
```

### Installation

**Windows (PowerShell) :**
```powershell
.\install.ps1
```

**macOS / Linux :**
```bash
chmod +x install.sh
./install.sh
```

### Utilisation

1. Redémarrez DeepSeek Harness après l'installation
2. Dans l'interface de session, sélectionnez **Mode Ingénierie**
3. L'IA agira comme un ingénieur en conception mécanique
4. Décrivez vos besoins de conception, et elle suivra le flux "Analyser → Concevoir → Vérifier"

---

## Español

### Resumen

**Modo Ingeniería** es un Agent Preset para [DeepSeek Harness](https://github.com/deepseek-ai/dsh), diseñado específicamente para el **diseño mecánico CAD/SolidWorks**.

Transforma la IA en un ingeniero de diseño mecánico profesional, con una misión central: **"Completar los planos de fabricación"**.

### Características

- 🎯 **Impulsado por objetivos** : Todo gira en torno a completar el plano técnico
- 📐 **Flujo profesional** : Sigue estrictamente la trilogía "Analizar → Diseñar → Verificar"
- 🔄 **Validación en bucle cerrado** : Cada pieza se verifica, cada ensamblaje se revisa
- 🛠️ **Listo para CAD/SW** : Habilidades integradas de AutoCAD y SolidWorks
- 📋 **Modo Plan** : Planificación orientada a ingeniería con revisión de diseño
- 🎯 **Seguimiento Goal** : Usa el sistema Goal para rastrear el objetivo general
- 🔬 **Verificación física** : Solver FEA 2D/3D integrado (Python puro + numpy), sin software externo requerido

### Proceso de diseño

```
Fase 1 : Análisis de requisitos y planificación
  ├── Entender requisitos
  ├── Análisis de viabilidad dimensional
  ├── Análisis de viabilidad operacional
  ├── Crear plan de diseño
  └── Establecer Goal

Fase 2 : Diseño detallado
  ├── Bocetos
  ├── Modelado por características
  ├── Validación de piezas (FEA)
  ├── Ensamblaje
  └── Resultados intermedios

Fase 3 : Ensamblaje y verificación
  ├── Ensamblaje final
  ├── Verificación de interferencias
  ├── Simulación cinemática
  ├── Verificación final
  ├── Planos técnicos
  └── Marcar Goal como completo
```

### Instalación

**Windows (PowerShell):**
```powershell
.\install.ps1
```

**macOS / Linux:**
```bash
chmod +x install.sh
./install.sh
```

### Uso

1. Reinicie DeepSeek Harness después de la instalación
2. En la interfaz de sesión, seleccione **Modo Ingeniería**
3. La IA actuará como un ingeniero de diseño mecánico
4. Explíquele sus requisitos de diseño y seguirá el flujo "Analizar → Diseñar → Verificar"

---

## Русский

### Обзор

**Инженерный Режим** — это Agent Preset для [DeepSeek Harness](https://github.com/deepseek-ai/dsh), специально разработанный для **механического проектирования CAD/SolidWorks**.

Он превращает ИИ в профессионального инженера-конструктора с единой центральной миссией: **"Выполнить рабочий чертёж"**.

### Возможности

- 🎯 **Управление целями** : Всё строится вокруг выполнения рабочего чертежа
- 📐 **Профессиональный процесс** : Строгое следование триаде "Анализ → Конструирование → Проверка"
- 🔄 **Замкнутый цикл проверки** : Каждая деталь проверяется, каждая сборка контролируется на совмещение
- 🛠️ **Готов к CAD/SW** : Встроенные навыки AutoCAD и SolidWorks
- 📋 **Режим плана** : Инженерно-ориентированное планирование с обзором проекта
- 🎯 **Отслеживание Goal** : Использование системы Goal для отслеживания общей цели
- 🔬 **Физическая верификация** : Встроенный 2D/3D FEA решатель (чистый Python + numpy), без внешнего ПО

### Процесс проектирования

```
Фаза 1 : Анализ требований и планирование
  ├── Понимание требований
  ├── Анализ размерной целесообразности
  ├── Анализ эксплуатационной целесообразности
  ├── Создание плана проектирования
  └── Установка Goal

Фаза 2 : Детальное конструирование
  ├── Эскизирование
  ├── Параметрическое моделирование
  ├── Проверка деталей (FEA)
  ├── Сборка
  └── Промежуточные результаты

Фаза 3 : Сборка и проверка
  ├── Итоговая сборка
  ├── Проверка на совмещение
  ├── Кинематическая симуляция
  ├── Итоговая проверка
  ├── Рабочие чертежи
  └── Отметить Goal как завершённый
```

### Установка

**Windows (PowerShell):**
```powershell
.\install.ps1
```

**macOS / Linux:**
```bash
chmod +x install.sh
./install.sh
```

### Использование

1. Перезапустите DeepSeek Harness после установки
2. В интерфейсе сессии выберите **Инженерный Режим**
3. ИИ будет действовать как инженер-конструктор
4. Опишите ваши требования, и он выполнит цикл "Анализ → Конструирование → Проверка"

---

## История версий

### v0.4 — 2026年8月25日（当前版本）

**新增功能：**

- **2D/3D 纯 Python FEA 求解器**（`physics/feapy_solver.py`）
  - 2D：CST 常应变三角形单元（平面应力/应变），误差 6-8%（符合理论预期）
  - 3D：CTE 常应变四面体单元，10×5×5 网格位移误差 1.5%，应力误差 11.4%
  - 完全基于 numpy，无需任何外部 FEA 软件
  - 悬臂梁测试全部通过，收敛性验证正常

- **3D 四面体网格分割修正**
  - 将每个六面体的四面体拆分从 5 个改为 6 个（5 个会缺失 1/6 体积）
  - 修复后总体积精确匹配，应力计算恢复正常量级

- **2D 厚度参数修复**
  - FEASolver 新增 `thickness` 参数，默认 1.0
  - solve_cantilever_2d 正确传入 T 厚度值

- **兼容性 shim**
  - 添加 `solve_cantilever(L, H, W, F, E, nu)` 统一接口，自动检测 2D/3D
  - 修复 fea_solver.py 中从 feapy_solver 导入函数名不匹配问题

**删除内容：**

- 彻底移除 `qq-notification` 插件及其所有引用
  - 从 agent.cordis.yml 配置中删除
  - 清理所有历史备份文件（bak-qq-*）
  - 清理 backups/qq-cleanup-* 目录
  - install.ps1 / install.sh 无 QQ 相关代码

**测试：** 全量 40/40 PASS

---

### v0.3 — 2026年8月22日

**新增功能：**

- **SolidWorks 自动化桥接**（`tools/sw_bridge.py` + `swapi.py`）
  - 通过 Python win32com 驱动 SolidWorks 2018~2024
  - 支持 sketch/extrude/cut/round 等常用操作
  - 支持导出 PDF / DWG（AutoCAD 可读）
  - 支持质量属性查询和工程图生成

- **AutoCAD 连接桥接**（`tools/ac_bridge.py`）
  - 检测 AutoCAD 连接状态
  - 导出当前图纸为 DXF

- **设计 Skill 体系**
  - `cad-workflow` — AutoCAD 机械设计流程
  - `sw-design` — SolidWorks 机械设计与建模流程
  - `solidworks-bridge` — SW 自动化操作指南
  - `sw-to-cad` — SW 模型转 CAD 图纸流程

---

### v0.2 — 2026年8月20日

**新增功能：**

- **物理验证子系统**（`tools/physics/`）
  - `load_case.py` — 载荷工况 JSON 解析与校验
  - `material_db.py` — 常用材料数据库（钢、铝、钛、工程塑料）
  - `geometry_gate.py` — 几何门禁检查（体积、表面积、壁厚）
  - `mesh_adapter.py` — STEP 导出 + 网格数据生成
  - `fea_solver.py` — 多后端 FEA 求解器（解析解 / CST 三角形 / skfem）
  - `simulation_report.py` — 结构化仿真报告生成
  - `design_state.py` — 迭代设计状态管理
  - `refine_rules.py` — 自动修正规则引擎

- **命令行入口**（`tools/physics_bridge.py`）
  - `physics-status` — 查询可用求解器后端
  - `physics-validate-case` — 校验载荷工况文件
  - `physics-demo` — 运行悬臂梁演示
  - `physics-optimize` — 迭代优化（最大化安全系数）
  - `physics-report` — 生成仿真报告
  - `physics-recommend` — 给出设计改进建议

- **Doctor 环境自检**
  - 检测 Python 版本、pywin32、skfem、Gmsh、CalculiX 等依赖

---

### v0.1 — 2026年8月19日

**初始版本：**

- Agent Preset 基础框架搭建
- `agent.cordis.yml` 配置：persona + 工具链 + 技能系统
- `preset.yml` 元数据
- Plan Mode 工程设计计划模式
- Goal 驱动的工作流追踪
- 上下文压缩（compaction + tool-result-pruner）
- 子代理并行（subagent + workflow）

---

## Changelog

### v0.4 — Aug 25, 2026 (Current)
- **2D/3D pure Python FEA solver** (`physics/feapy_solver.py`)
  - 2D CST triangles: 6-8% error vs analytical (expected)
  - 3D CTE tetrahedra: 1.5% disp / 11.4% stress error at 10×5×5 mesh
  - Pure numpy, zero external FEA software required
- **3D tetrahedron decomposition fix**: 5→6 tets per hexahedron (5 tets miss 1/6 volume)
- **2D thickness parameter fix**: `FEASolver(thickness=T)` default 1.0
- **Compatibility shim**: `solve_cantilever(L, H, W, F, E, nu)` auto-detects 2D/3D
- **Removed**: `qq-notification` plugin and all references, backup files cleaned

### v0.3 — Aug 22, 2026
- **SolidWorks bridge** (`tools/sw_bridge.py` + `swapi.py`): win32com driver for SW 2018~2024
- **AutoCAD bridge** (`tools/ac_bridge.py`): connection status + DXF export
- **Skill system**: cad-workflow, sw-design, solidworks-bridge, sw-to-cad

### v0.2 — Aug 20, 2026
- **Physics verification subsystem** (`tools/physics/`): load_case, material_db, geometry_gate, mesh_adapter, fea_solver, simulation_report, design_state, refine_rules
- **CLI commands**: physics-status, validate-case, demo, optimize, report, recommend
- **Doctor self-check**: Python, pywin32, skfem, Gmsh, CalculiX dependency detection

### v0.1 — Aug 19, 2026
- Agent Preset framework: persona, tool chain, skill system
- Plan Mode for engineering design review
- Goal-driven workflow tracking
- Context compaction (compaction + tool-result-pruner)
- Sub-agent parallelism (subagent + workflow)

---

## Historique des versions

### v0.4 — 25 août 2026 (Version actuelle)
- **Solveur FEA Python pur 2D/3D** (`physics/feapy_solver.py`)
  - Triangles CST 2D : erreur 6-8% vs analytique (attendu)
  - Tétraèdres CTE 3D : erreur 1,5% déplacement / 11,4% contrainte à maillage 10×5×5
  - numpy pur, aucun logiciel FEA externe requis
- **Correction décomposition tétraèdre 3D** : 5→6 tétraèdres par hexaèdre (5 tétraèdres manquent 1/6 de volume)
- **Correction paramètre épaisseur 2D** : `FEASolver(thickness=T)` défaut 1.0
- **Shim compatibilité** : `solve_cantilever(L, H, W, F, E, nu)` détection auto 2D/3D
- **Supprimé** : plugin `qq-notification` et toutes les références, sauvegardes nettoyées

### v0.3 — 22 août 2026
- **Pont SolidWorks** (`tools/sw_bridge.py` + `swapi.py`) : pilote win32com pour SW 2018~2024
- **Pont AutoCAD** (`tools/ac_bridge.py`) : statut de connexion + export DXF
- **Système de compétences** : cad-workflow, sw-design, solidworks-bridge, sw-to-cad

### v0.2 — 20 août 2026
- **Sous-système de vérification physique** (`tools/physics/`) : load_case, material_db, geometry_gate, mesh_adapter, fea_solver, simulation_report, design_state, refine_rules
- **Commandes CLI** : physics-status, validate-case, demo, optimize, report, recommend
- **Auto-vérification Doctor** : Python, pywin32, skfem, Gmsh, CalculiX

### v0.1 — 19 août 2026
- Framework Agent Preset : persona, chaîne d'outils, système de compétences
- Plan Mode pour revue de conception ingénierie
- Suivi de workflow piloté par Goal
- Compression de contexte (compaction + tool-result-pruner)
- Parallélisation sous-agent (subagent + workflow)

---

## Registro de cambios

### v0.4 — 25 de agosto de 2026 (Versión actual)
- **Solver FEA puro en Python 2D/3D** (`physics/feapy_solver.py`)
  - Triángulos CST 2D: error 6-8% vs analítico (esperado)
  - Tetraedros CTE 3D: error 1.5% desplazamiento / 11.4% tensión con malla 10×5×5
  - numpy puro, sin software FEA externo requerido
- **Corrección descomposición tetraedro 3D**: 5→6 tetraedros por hexaedro (5 tetraedros pierden 1/6 de volumen)
- **Corrección parámetro grosor 2D**: `FEASolver(thickness=T)` por defecto 1.0
- **Shim compatibilidad**: `solve_cantilever(L, H, W, F, E, nu)` detección automática 2D/3D
- **Eliminado**: plugin `qq-notification` y todas las referencias, backups limpiados

### v0.3 — 22 de agosto de 2026
- **Puente SolidWorks** (`tools/sw_bridge.py` + `swapi.py`): controlador win32com para SW 2018~2024
- **Puente AutoCAD** (`tools/ac_bridge.py`): estado de conexión + exportación DXF
- **Sistema de habilidades**: cad-workflow, sw-design, solidworks-bridge, sw-to-cad

### v0.2 — 20 de agosto de 2026
- **Sistema de verificación física** (`tools/physics/`): load_case, material_db, geometry_gate, mesh_adapter, fea_solver, simulation_report, design_state, refine_rules
- **Comandos CLI**: physics-status, validate-case, demo, optimize, report, recommend
- **Autoverificación Doctor**: Python, pywin32, skfem, Gmsh, CalculiX

### v0.1 — 19 de agosto de 2026
- Framework Agent Preset: persona, cadena de herramientas, sistema de habilidades
- Plan Mode para revisión de diseño de ingeniería
- Seguimiento de flujo de trabajo impulsado por Goal
- Compresión de contexto (compaction + tool-result-pruner)
- Parallelización sub-agent (subagent + workflow)

---

## История версий

### v0.4 — 25 августа 2026 (Текущая версия)
- **2D/3D FEA решатель на чистом Python** (`physics/feapy_solver.py`)
  - 2D CST треугольники: погрешность 6-8% против аналитики (ожидаемо)
  - 3D CTE тетраэдры: погрешность 1,5% перемещение / 11,4% напряжение при сетке 10×5×5
  - Чистый numpy, без внешнего FEA ПО
- **Исправлена разбивка тетраэдра 3D**: 5→6 тетраэдров на гексаэдр (5 тетраэдров дают пропуск 1/6 объёма)
- **Исправлен параметр толщины 2D**: `FEASolver(thickness=T)` по умолчанию 1.0
- **Совместимый shim**: `solve_cantilever(L, H, W, F, E, nu)` автоопределение 2D/3D
- **Удалено**: плагин `qq-notification` и все ссылки, бэкапы очищены

### v0.3 — 22 августа 2026
- **Мост SolidWorks** (`tools/sw_bridge.py` + `swapi.py`): драйвер win32com для SW 2018~2024
- **Мост AutoCAD** (`tools/ac_bridge.py`): статус подключения + экспорт DXF
- **Система навыков**: cad-workflow, sw-design, solidworks-bridge, sw-to-cad

### v0.2 — 20 августа 2026
- **Подсистема физической верификации** (`tools/physics/`): load_case, material_db, geometry_gate, mesh_adapter, fea_solver, simulation_report, design_state, refine_rules
- **Команды CLI**: physics-status, validate-case, demo, optimize, report, recommend
- **Самопроверка Doctor**: Python, pywin32, skfem, Gmsh, CalculiX

### v0.1 — 19 августа 2026
- Базовый каркас Agent Preset: persona, цепочка инструментов, система навыков
- Plan Mode для инженерного обзора проекта
- Отслеживание рабочего процесса через Goal
- Сжатие контекста (compaction + tool-result-pruner)
- Параллелизм под-агентов (subagent + workflow)

---

## 文件结构

```
engineering/
├── agent.cordis.yml      # Agent 配置（工具链 + 技能 + 人设）
├── preset.yml            # 元数据
├── skills/               # 工作流技能
│   ├── cad-workflow/     # AutoCAD 设计流程
│   ├── solidworks-bridge/# SW 自动化操作指南
│   ├── sw-design/        # SolidWorks 机械设计
│   └── sw-to-cad/        # SW→CAD 转换流程
└── tools/
    ├── sw_bridge.py      # SolidWorks 桥接主脚本
    ├── swapi.py          # SW 高层建模 API
    ├── ac_bridge.py      # AutoCAD 桥接
    ├── physics/          # 物理验证子系统
    │   ├── fea_solver.py
    │   ├── feapy_solver.py   # 纯 Python FEA（2D+3D）
    │   ├── load_case.py
    │   ├── material_db.py
    │   ├── geometry_gate.py
    │   ├── mesh_adapter.py
    │   ├── simulation_report.py
    │   ├── design_state.py
    │   └── refine_rules.py
    └── physics_bridge.py # 物理验证命令行入口
```

---

## 技术栈

| 组件 | 技术 |
|------|------|
| 核心框架 | [DeepSeek Harness](https://github.com/deepseek-ai/dsh) (Cordis) |
| SW 自动化 | Python win32com |
| CAD 自动化 | Python pyautocad / comtypes |
| FEA 求解 | numpy（纯 Python，无外部依赖） |
| 网格生成 | Gmsh（可选） |

---

## License

MIT
