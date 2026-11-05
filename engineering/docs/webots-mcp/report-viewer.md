# S08 report viewer and sol handoff boundary

The existing engineering settings connection group now contains a read-only Webots report panel directly below the existing Webots row. The row's green label is **安装/进程可用**; its note explicitly leaves simulation unconfirmed. The viewer does not launch a simulator, replay an action, infer a root cause or modify CAD.

## Context and routes

The viewer selects only the unique session retained by `mainView`, following the official `dsh-v0.2.1-rc.3` UI convention. It does not fall back to a historical or arbitrary engineering session. The host rechecks live engineering-agent/task/room binding for every request. Opening a root chat without its own binding can therefore legitimately return `CONTEXT_UNAVAILABLE`; open the bound room chat. This is not bypassed with user-entered task/room identifiers.

| GET route (prefix `/dsh-engineering-ui/`) | Query / result |
|---|---|
| `webots-context` | `sessionId`; authenticated `task_id`, stable `room_id`, original `room_label`, state root and conservative simulation snapshot |
| `webots-report` | `sessionId`, `run_id`, `report_type`; `data` is one full verified S04 report |
| `webots-evidence` | `sessionId`, `run_id`, optional `name`, byte `offset`, `limit`, `expected_sha256`; `data` is one S04 UTF-8 log page |

The UI exposes the three report types and the default `worker` log. Report reads are already bounded by S04's 1 MiB limit. Evidence metadata is paged locally over the frozen report, 20 records at a time, retaining path/hash/size/kind/provenance/producer. Report payload remains available as escaped, collapsible JSON, including every candidate component, its instance/configuration/reference, evidence level, raw observation, original verdict and missing information. Metadata paths are text, not arbitrary downloadable URLs or HTML.

Logs display one page at a time, at most 16 KiB, with offset/next offset/size/SHA-256. The next request forwards the preceding snapshot hash. Invalidated logs clear the old page and show the host error; restart at offset 0. Report and run changes clear their prior data; an unsuccessful read never leaves a previous report displayed as current. Session changes hide prior scope data and invalidate outstanding responses. Only the most recent request can update the panel.

Context `execution_state` describes simulation. A report-read envelope saying `completed` means **reading finished**, and is not used to set simulation execution state. `supported`/`approximate`/`unsupported`/`unknown` are report support states, not physical verdicts. `fake`/`synthetic`/`fixture` remain fixtures; `unknown` actions may already have executed and must not be replayed. `stale` and missing timestamps remain visible. Neither this panel nor the skill upgrades `core_ready`.

## Verification and references

Run the client behavior checks without installing dependencies or launching DSH:

```text
node --test engineering/tests/webots_mcp/test_report_viewer.mjs
```

The Node checks execute the shipped components with a small React element/hook boundary adapter; they exercise the UI's request queries, rendered strings, controls and cursor/hash behavior. They are not actual browser layout, React lifecycle or desktop-host playback validation. S07's isolated route tests and S08's shared regression records are the separate host evidence; no real desktop deployment is implied.

The scoped guide is [webots-simulation](../../skills/webots-simulation/SKILL.md). Control-root `evidence/S08/` retains RED/GREEN logs, independent peer feedback, official snapshots and final acceptance/handoff records. The frozen PLAN is unchanged. The shared exact commit is recorded after S07/S08 commits are assembled and reverified; no intermediate green check counts as that integration result.

Official sources rechecked on 2026-10-30, with URL/version/date/content hash in `evidence/S08/official-sources.json`:

- [DSH target-tag DocumentTitle](https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/dsh-v0.2.1-rc.3/packages/client/ui-layout/src/client/DocumentTitle.tsx): `byId[*].retainedBy.mainView` session selection.
- [DSH target-tag MCP client](https://raw.githubusercontent.com/deepseek-ai/deepseek-harness/dsh-v0.2.1-rc.3/packages/mcp/mcp-client/src/index.ts): explicit stdio client configuration; not proof installed app bytes equal the tag.
- [Webots R2025a extern controllers](https://raw.githubusercontent.com/cyberbotics/webots/R2025a/docs/guide/running-extern-robot-controllers.md): robot selection, launcher and controller-connection distinctions.
- [MCP 2025-11-25 transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports): protocol-only stdout and separate stderr logs.

No A01–A04 implementation, automatic SW export, numerical/physical acceptance, real DSH→SW→Webots→SW loop or I01 final core integration is delivered by S08. The existing S03 eight-step diagnostic probe has missing environment PROTOs and unknown physical validity. Any supplemental G1 diagnostic is recorded separately under control-root `evidence/S08/`; its actual executed scope and outstanding gaps must be read from the final handoff, not inferred from UI or fixture tests.
