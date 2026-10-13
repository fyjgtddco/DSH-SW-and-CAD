# DSH `ctx.commandUi` / popupSelect API —— 实据调研结论

> 来源：`Z:\DSH\resources\app.asar`（121,348,951 字节，2,348,005 行）只读扫描。
> 实现包：`@deepseek-ai/dsh-client-ui-commands` v0.2.0-rc.2。
> **归档里只有编译后的 `lib/client.js` + `lib/index.js`，没有 `src/client/contract.ts`、没有 `.d.ts`。**
> 因此契约是**结构化**的（无导出类型名），下面的字段名均由**真实消费点**反证。

---

## 一、注册调用（不是 contribute / client）

`ctx.commandUi` 是 Cordis 服务 `CommandUiRuntime`，服务名 `"commandUi"`（asar 402381）。
公开方法**只有 4 个**（402360-402758）：

```js
register(contribution)   // 402426  注册一个【客户端自有命令】；重名抛错
decorate(decoration)     // 402445  给【已存在的宿主命令】追加弹窗/动作
dismiss(name)            // 402463  关闭该命令所有已打开弹窗与确认框
popupFor(actx)           // 402475  取该会话的 PopupSelectController（内部机制）
```

- `register` 返回 disposer；**重名直接抛** `ui-commands: duplicate contribution for /X`（402429）。
- **contribution 与宿主命令同名也会抛**（402521）：`contribution /X collides with a host command`。
  想挂在既有宿主命令上必须用 `decorate`。
- 第一方一律写成 `scope.effect(() => command.register({...}), "描述")`。

## 二、contribution 对象结构

| 字段 | 类型 | 必填 | 证据 |
|---|---|---|---|
| `name` | string，**不带前导斜杠** | **是** | Map 键 402430；`/model` 用 `"model"` 436129 |
| `label` | `() => string` | 否 | 402524，**每次候选求值都重新调用** |
| `description` | `() => string` | 否 | 402525，同上 |
| `icon` | Icon 组件（**是值，不是 thunk**） | 否 | 402526 |
| `available` | `(session) => boolean` | **是**（无条件调用） | 402520 / 402536 / 402597 |
| `ui` | `CommandUiSpec` | **是** | 402537 按 `ui.kind` 分支 |

> ⚠️ `available` 缺了会 **TypeError**（它被无条件调用）。

**真实 /model contribution（436128-436158，逐字）**：
```js
scope.effect(() => command.register({
    name: "model",
    label: () => t("command.label"),
    description: () => t("command.description"),
    icon: _deepseek_ai_dsh_client_ui_primitives.IconDataOutlineRegular,
    available: (session) => sessions.subagentAddress(session.sessionId) === void 0,
    ui: {
        kind: "popupSelect",
        searchMode: "fuzzy-label",
        searchLabels: () => ({
            placeholder: t("search.placeholder"),
            empty: t("empty.models"),
            noResults: t("search.empty")
        }),
        options: async (session) => {
            if (sessions.subagentAddress(session.sessionId) !== void 0) throw new Error("...");
            return optionsOf(await models.directoryFor(session.sessionId).load(), t);
        },
        onSelect: async (option, session) => { /* ... */ }
    }
}), "ui-model-selection: /model contribution");
```

**真实 /permission decoration（438589-438601，逐字）**：
```js
ctx.effect(() => command.decorate({
    name: "permission",
    available: (session) => selectionOf(sessionFor(session)) !== void 0,
    ui: {
        kind: "popupSelect",
        options: async (session) => { /* ... */ },
        onSelect: (option, session) => submit(session.sessionId, option.id).then(() => void 0)
    }
}), "ui-permission: /permission decoration");
```

**真实 action contribution（`file`，421658-421669）**：
```js
scope.effect(() => commands.register({
    name: "file",
    label: () => t("input.file"),
    icon: _deepseek_ai_dsh_client_ui_primitives.IconPaperclipOutlineRegular,
    available: (session) => inputHub.canPickFiles(session.sessionId),
    ui: { kind: "action", run: (session) => { inputHub.pickFiles(session.sessionId); } }
}), "ui-conversation: File action");
```

## 三、`available(session)` 的真实语义

**签名是 `available(session) => boolean`，不是 `available(projection)`**（`available(projection` 零命中）。
返回**严格真/假**（只用于 `if`），返回对象毫无意义。

- `session.sessionId` —— string
- `session.projections.faceOf("<key>").getSnapshot()` —— projection 取值

**它不是传进来的，要自己取。** 标准写法（/permission 438509-438512）：
```js
function selectionOf(session) {
    return session?.projections.faceOf("permissions").getSnapshot();
}
// 注意：传给 available 的是【触发管线会话】，projection 在【已物化 binding 会话】上
const sessionFor = (session) => sessions.binding(session.sessionId)?.session;
available: (session) => selectionOf(sessionFor(session)) !== void 0,
```
已见 projection 键：`permissions` / `inbox` / `modelSelection` / `plan` / `goal`。

**约束**：必须**同步且不可抛**（每次候选求值都调）。

## 四、popupSelect spec 结构

`ui.kind` 只有两种：`"popupSelect"` | `"action"`。

### popupSelect
| 字段 | 类型 | 说明 |
|---|---|---|
| `options` | `(context, signal?) => Promise<Option[]>` | **必填**。**每次打开只取一次** |
| `onSelect` | `(option, context) => Promise<void>` | **必填**。抛错会让外壳保持打开 |
| `searchMode` | `"substring"` \| `"fuzzy-label"` | 默认 substring |
| `searchLabels` | `() => { placeholder, empty, noResults }` | 每次打开调一次 |

`context` 就是 **session**（见 `onSelect: (option, session) => submit(session.sessionId, ...)`）。

**选项行（Option）完整字段**：

| 字段 | 类型 | 必填 | 证据 |
|---|---|---|---|
| `id` | string | **是**（React key） | 402932 |
| `label` | string | **是**（渲染 + 匹配） | 402917 |
| `detail` | string | 否（也参与 substring 匹配） | 402923 |
| `badge` | string（上标） | 否 | 402918 |
| `group` | `{ name, label }` | 否 | 401957 |
| `active` | `true` 标记当前值 | 否 | 402024 |
| `confirmation` | object（见 §六） | 否 | 402174 |

> **没有** `current` / `value` / `onClick` / `disabled` / `icon`。当前值标记是 `active: true`。

**渲染词汇表就这么点**（402901-402932）：`label` + `badge`(上标) + `detail` + `active`(对勾)，包在 `group` 标题下。

## 五、打开 / 关闭机制 ★关键限制

**没有任何公开的命令式 `open()` / `show()`。** 公开面只有 `register` / `decorate` / `dismiss` / `popupFor`。

**官方唯一的打开路径**：
1. 用户在 `/` 菜单里选到该行；或
2. 用户**键入 `/名称` 再回车**（`matchEnter` 402405 → `invoke` 402639）。

**内部通道（存在，但非契约）**：`popupFor(actx)` 返回会话的 `PopupSelectController`，其 `open` 签名（402057）：
```js
open(command, spec, context, segment)
```
即 `ctx.commandUi.popupFor(sessions.scope(sessionId)).open("mycmd", mySpec, session, {via:"enter", token:"/mycmd"})`。
- **前提**：必须给**已保留的 Session scope**，否则抛 `command.popupFor requires a retained Session scope`（402479）。
- `segment` 是选后 token 消费的 CAS；token 不在草稿里时**良性失败**（402161）。
- 属内部机制，README 的公开说法只有"键入 /名称"。

**生命周期**：每个会话一个控制器，惰性创建，随会话 scope 销毁。**重新打开会顶掉上一个外壳**（402051），**不是嵌套**。

## 六、多按钮 —— 明确结论：popupSelect 做不到

popupSelect 是**严格单选列表**。证据链：
1. `filterOptions(...)[index]` 只取**一个** option（402172）；
2. `select(index)` 是**单飞**的（402159："首次调用进入 submitting，此后到落定前再调即 no-op"）；
3. 成功后**无条件关闭外壳**（402237-402241）；
4. 渲染成 `role="option"` + `aria-selected`，容器 `role="listbox"`（402902）。

**没有** `actions: [...]`、没有页脚按钮、没有每行次级按钮。**层叠弹窗也不可能**（一个会话只有一份外壳状态，重开即顶掉）。

### 可行的替代（按保真度排序）

- **A. 选项即动作 + `confirmation`（推荐）** —— 每个动作一行；需要警告时给该行挂 `confirmation`，即弹出**真实模态**，带 `cancelLabel`/`confirmLabel` 两个键。`/permission` 正是这样给 `danger-full-access` 设卡的。
- **B. 拆成两条命令** —— 各注册一个 contribution，`/a` `/b`，零嵌套。
- **C. 槽位自绘模态** —— 注册进 `conversation.input.overlay`（会话级）或 `shell.overlay`（全框），**按钮数量任意**。代价：不能 import `@deepseek-ai/dsh-client-ui-primitives`，要自带 Modal 的标记与 CSS（类名换自己前缀，只用 `--dsw-alias-*` token）。
- **E. 纯选项，无弹窗** —— 每行一个动作，`onSelect` 按 `option.id` 区分。

## 七、确有嵌套确认框：`option.confirmation`

**五个字段全都被用到**：`title` / `description` / `acknowledgeLabel` / `cancelLabel` / `confirmLabel`
（`closeLabel` 由外壳提供，不用你给）。

控制器逻辑（402174-402181）：
```js
if (option.confirmation !== void 0) {
    this.state.set({ ...s, confirming: option, acknowledged: false, error: null });
    return;
}
await this.settle(binding, option);
```
渲染（402991-403009）：`RiskConfirmation`，props 见 452902。

**行为契约**（JSDoc 402185-402213）：
- **取消** → 回到**仍然打开的选择器**（`cancelConfirmation`）——这是最接近"嵌套导航"的能力。
- ⚠️ **确认键在勾选 acknowledge 之前是禁用的**：`disabled: disabled || !acknowledged`（452918）。
  **这是"风险闸门"，不是普通是/否确认** —— 永远带一个**强制勾选框**。
- `dismiss(name)` 也会关掉打开的确认框。

> **没有** `ctx.dialog`、没有 `confirm()` 服务、没有 `window.confirm` 包装。

## 八、归档里有什么 / 没什么

- **有**：`node_modules/@deepseek-ai/dsh-client-ui-commands/lib/client.js`（编译后，带完整 JSDoc）与 `lib/index.js`。
- **没有**：`src/client/contract.ts`、`resolution.ts`、任何 `.d.ts`。
  `Select-String 'CommandUiContribution|PopupSelectSpec|PopupSelectOption'` → **零命中**。
  **没有可导出的 TS 类型名**，契约是结构化的，要自己照抄形状。

## 九、客户端插件通用注册形态

模块返回 **`{ inject: [...], apply(ctx) {...} }`**（不是 `export default function(ctx)`）。

```js
window.__ModuleLoader__.load({
  id: '@local/my-decoration',
  factory(require) {
    const React = require('react');
    const h = React.createElement;
    function Decoration() { /* ... */ }
    return {
      inject: ['slots'],
      apply(ctx) {
        ctx.slots.inject('conversation.composer.dock', () => ctx.slots.register({
          name: 'conversation.composer.dock', id: 'my-decoration', order: 5,
        }, Decoration));
      },
    };
  },
});
```
```json
{
  "dsh": {
    "bundle": { "patch": "./cordis.patch.yml" },
    "client": { "platform": "web", "immediately": true,
                "inject": ["@deepseek-ai/dsh-client-ui-conversation"] }
  }
}
```
**要把 `"commandUi"` 放进 `inject`**（或用 `ctx.inject(["commandUi"], ...)`），这样没有该服务的 profile 里插件保持不激活，而不是抛错。

第一方 inject 实例：
- ui-commands（403016）：`["inputTriggers","sessions","remote","remote.commands","locale"]`
- ui-model-selection（436103）：`["commandUi","locale","sessions","slots","remote","remote.session"]`
- ui-permission-presets（438497）：`["commandUi","connection","sessions","slots","locale","remote","remote.permissionPresets","remote.settings","configForms","settingsSchema"]`

## 十、能力边界汇总（★ 别踩）

1. **popupSelect 无多按钮**；单选、单飞、成功后必关。
2. **不能层叠 popupSelect**；重开即顶掉。
3. **无公开命令式 open()**；只有 `register`/`decorate`/`dismiss`/`popupFor`。
   `popupFor(...).open(...)` 可用但属内部，需已保留会话 scope，非官方契约。
   **文档认可的打开方式只有用户手势（输入 `/名称`）。**
4. **打开后选项不刷新**：`options` 每次打开只跑一次，搜索是纯本地过滤。要刷新只能 `dismiss` 后重开。
5. **无流式/增量选项、无每行 `disabled`/`icon`/`tooltip`、无自定义行渲染、无页脚、无自由内容。**
6. **没有 `available(projection)`**；给你 session，projection 自己取。
7. **`available` 必须同步且不能抛**（每次候选求值都调）。
8. **contribution 不能遮蔽宿主命令**（抛错）；要挂宿主命令用 `decorate`。
9. ⚠️ **`available:false` 会挡住 Enter 派发**（402597 检查），
   但 `matchSpace` **不检查 available**（402562-402568，只查 `contributions.has(name)`）——
   即"已注册但不可用"的命令**仍会占住该名字的空格快捷键**。
10. **归档无导出 TS 类型**，自行照形状写。
11. **带附件时 popupSelect 会被拒**（402599）：只有 `kind:"action"` 能跑。
12. **非裸调用无效**：`if (!bare) return void 0;`（402598）—— `/mycmd somearg` 不会开弹窗。

## 十一、最佳抄写模板（asar 行号）

| 用途 | 位置 |
|---|---|
| popupSelect contribution 全功能范例（含 fuzzy、groups、active） | **436128-436158**（/model） |
| popupSelect + confirmation 范例（风险闸门） | **438589-438601**，`optionsOf` 在 **438514-438529** |
| action contribution | **421658-421669**（file） |
| action + 自绘槽位对话框 | **434515-434526** + **434490-434514**（feedback） |
| `dismiss(name)` 实战 | **438557**（/permission 订阅失效即关闭） |
