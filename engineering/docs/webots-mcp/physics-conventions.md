# A01 physical export core — partial, not live validated

## Frames and units

Shared with A02: right-handed column vectors, `p_target = R p_source + t`.
`physics.mass_properties` converts explicitly labelled m/mm and kg/g once;
inertia input units are mass-unit times length-unit squared. All downstream
records require `units=SI`, a nonempty frame, and `inertia_about=center_of_mass`.
An inertia matrix is the physical symmetric tensor, not a list of principal
moments or SW products. `transform_mass` computes `R I R^T`; translation moves
the COM but does not apply a parallel-axis term to its COM tensor.

`sw_transform` reads 16 `IComponent2.Transform2.ArrayData` values. SW's row
axes are transposed to our column convention. Translation slots 9..11 are
metres; scale slot 12 must equal 1. Improper rotations/reflections, shear and
scale are rejected. Transform2 is root-assembly relative, even for nested
components: do not multiply all ancestor Transform2 values a second time.

`merge_rigid_bodies([(component_id, properties), ...], frame=...)` requires
all members already in the same named frame and unique IDs. Membership is
supplied by A02/explicit fixed-group evidence, never guessed. It computes
mass-weighted COM and `sum(I_i + m_i (dot(d_i,d_i) E - d_i d_i^T))`, retaining
every contributing component ID. It does not select one source as the cause
of a later problem. Mathematical realizability checks are not A04 physical,
collision or numerical-stability acceptance.

SW axes are preserved in URDF. `(x,-z,y)` would be a +90 degree X rotation,
not -90. No implicit such permutation exists here. Any final world-placement
rotation belongs in one explicit converter/world boundary and must rotate
geometry, COM, inertia, gravity assumptions and joint axes consistently.

## Exact mass methods

`parse_mass_array` only exposes the documented raw named slots of
IModelDocExtension.GetMassProperties2 (12), IBody2.GetMassProperties (12), and
IModelDocExtension.GetMassProperties (13 including Accuracy). It deliberately
does not reinterpret cross-term signs or infer units/status/frame. The old
IModelDoc2.GetMassProperties method is rejected. S02 sentinel goldens are
synthetic, not actual SW data.

The new part adapter uses CreateMassProperty, sets/reads UseSystemUnits=True,
reads Mass and CenterOfMass, and calls IMassProperty.GetMomentOfInertia(0).
`sw_inertia_tensor` converts that exact nine-entry API's products by negating
the off-diagonals, following ROS solidworks_urdf_exporter Inertia.SetMomentMatrix.
Raw nine values and UserAssigned are retained. Actual installed SW version,
nonzero cross-term calibration and override/material semantics still require
real capture. The old swapi/sw_bridge fallback/default-density paths are not
used or modified.

## Implemented export surface

`capture_part(model, expected_source={path,sha256,configuration}, sw_version=...,
provenance=..., max_triangles=...)` accepts an already authorized part object.
It does not attach to SW, take a global lock, clear selection, switch config,
rebuild, save or write files. The future host must own the CAD access lock and
isolate the source; caller-provided provenance is metadata, not authentication.

Source hash/path, active configuration and dirty/rebuild flags are checked
before and after capture. Nonempty selection is refused to prevent selected
body mass. Missing/hidden solids, surface bodies, absent/degenerate tessellation,
changed triangle count and a maximum 100000-triangle budget are refused.
GetTessTriangles(True) preserves metres. It is display tessellation with
uncalibrated geometric error, not a machining or collision guarantee.

`part_bundle(snapshot)` produces in-memory robot.urdf, visual.obj and S04 data
payloads. It uses the COM origin with zero inertial rotation and the six full
tensor entries, local mesh origin and scale 1. No collision object is invented.
Consequently physical_validation remains not_run and the bundle is not ready
for physics. Wrap payloads with EvidenceStore using the snapshot's actual
provenance/source_revision; this module does not write shared state.

`rigid_group_bundle([{id,capture,rotation,translation_m}, ...], frame=...)`
transforms each part-local mesh and COM/tensor once into one output-link frame,
then merges physical properties. It preserves source occurrences and a
many-to-one component mapping. It does not prove that standalone part geometry
matches assembly cuts/in-context modifications, or that membership is fixed.
Those are explicit remaining evidence requirements, not silently assumed PASS.

`capture_assembly` now traverses GetComponents(False) without source-file
deduplication. It records snapshot-local occurrence IDs (never persistent IDs),
referenced configurations, suppression states and raw root-relative Transform2.
Only resolved states 2/3 are read; failures stay as unsupported records. Assembly
containers receive no mass; leaf part documents use the bounded part reader.
Total mesh and occurrence budgets are enforced, source documents are rechecked.
The capture always returns ready_for_physics=False: standalone part geometry
can differ from assembly cuts/in-context bodies. A02 reference/mate capture must
be paired with the same actual component object and same source snapshot; IDs
must not be matched by enumeration order across captures. Jointed URDF and
assembly-context body extraction are still separate unfinished work.

### Assembly-context native reader and joint serialization (2026-11-03)

`capture_context_component` now reads GetBodies3 solid/user bodies (assembly
cuts included) and feeds them to the owning assembly's CreateMassProperty /
AddBodies. Returned COM/tensor is **root-assembly** frame: no component transform
is applied again. Raw per-face GetTessTriangles(True) coordinates are retained as
`vertices_raw_system_units` with `mesh_frame=unknown`; they are intentionally not
accepted by the part-local mesh path. This is implemented capture, but the native
face frame, tessellation accuracy and installed COM out-argument marshaling are
not calibrated. Unknown/dirty/rebuild source state is rejected, including dirty
child documents; file hashes, occurrence configuration/pose and root state are
rechecked. These guards detect common drift, not an atomic CAD snapshot; caller
must still own exclusive authorized CAD access.
The surface query must return a list/tuple; None/False/0 are unknown and rejected.
The installed empty COM array/null convention needs calibration before enabling
this path; no undocumented null-to-empty fallback is supplied. Body-info integers
are retained verbatim, not claimed to be classified or semantically validated.

`reframe_component(..., owning_frame=..., link_frame='link:<id>')` uses inverse
link pose to express component pose in its selected link frame. The original
pose is retained under source_transform and the label changes; reapplying a
root-frame transform fails. `jointed_bundle([{id,bundle},...], joints)` requires
link-local rigid-group frames, unique occurrence membership and A02's actual
validate_joint/validate_tree. It writes fixed/revolute/prismatic joints, remaps
safe mesh filenames and preserves source mappings plus tool/approximation/
omission/missing-information audit fields. It does not infer missing joints or
drop loops. A02 joint_from_solution chooses a child joint frame parallel to the
parent at the axis point; the child mesh AND COM/tensor must be reframed to that
chosen frame before this serialization. No production host wiring is added.

Before I01, the source-only joint seam test uses the original A02 files without
copying or merging them:

```powershell
$env:WEBOTS_A02_TOOLS = '<A02 worktree>/engineering/tools'
python -B engineering/tests/webots_mcp/test_jointed_export.py -v
```

If the A02 dependency is absent the seam test fails its import; it never silently
skips or replaces A02 validation with a fake validator. Independent A02 review
on 2026-11-03 rechecked the 3 seam tests and four audit-field sentinels; context
capture was outside that review scope.

## Not yet delivered / real gate

No live SW/COM invocation, calibrated native assembly-context mesh binding,
complete native assembly-to-joint orchestration, collision extraction/validation, production
MCP wiring or live urdf2webots/Webots conversion has been executed. Installed
typelib marshaling and native geometry equivalence must be checked before
enabling the adapter. A02 handles persistent identities and mate reasoning;
A04 handles physical acceptance. G1 remains PARTIAL; A01/G2 is not accepted.

Correction to earlier A01 prose: the archived Cyberbotics writeRobot.py uses
`%lf` for mass/COM, but **`%e` for inertia**. The isolated official-writer probe
in evidence/A01 preserves tiny inertia, rounds mass 1e-8 kg to zero and tiny COM
coordinates to zero, and omits an exactly unit diagonal inertia tensor. These
are source-fragment observations, not a complete converter/Webots run. Current
Python has no urdf2webots; the S03 historical venv path no longer exists. No new
dependencies were installed. Production conversion must pin an actual available
version and compare all emitted physical values; this gate is not passed.

## Evidence and sources

Run `python -B engineering/tests/webots_mcp/test_physics.py -v` and
`python -B engineering/tests/webots_mcp/test_sw_export.py -v`. These are stdlib
analytic/fake-object tests only. Evidence is in control-root evidence/A01.

Checked 2026-10-31: official SW indexed documentation exposed method contracts;
direct SW page opens sometimes returned a shell and direct archive downloads
failed (recorded honestly in official-sources.json). ROS/Cyberbotics raw source
bytes were archived with SHA256; master was inspected, not a pinned release.

- [SW MathTransform](https://help.solidworks.com/2023/english/api/sldworksapi/SOLIDWORKS.Interop.sldworks~SOLIDWORKS.Interop.sldworks.IMathTransform.html?verRedirect=1)
- [SW Transform2](https://help.solidworks.com/2019/english/api/sldworksapi/SOLIDWORKS.Interop.sldworks~SOLIDWORKS.Interop.sldworks.IComponent2~Transform2.html)
- [SW inertia](https://help.solidworks.com/2025/English/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IMassProperty~GetMomentOfInertia.html?format=P&value=)
- [SW system units](https://help.solidworks.com/2024/English/api/sldworksapi/SolidWorks.interop.sldworks~SolidWorks.interop.sldworks.IMassProperty~UseSystemUnits.html)
- [SW tessellation](https://help.solidworks.com/2024/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IPartDoc~GetTessTriangles.html)
- [ROS inertia sign conversion](https://github.com/ros/solidworks_urdf_exporter/blob/master/SW2URDF/URDF/Inertia.cs)
- [Cyberbotics physics writer](https://github.com/cyberbotics/urdf2webots/blob/master/urdf2webots/writeRobot.py)
- [SW assembly AddBodies semantics](https://help.solidworks.com/2025/English/api/sldworksapi/SOLIDWORKS.Interop.sldworks~SOLIDWORKS.Interop.sldworks.IMassProperty~AddBodies.html)
- [SW GetBodies3 user bodies](https://help.solidworks.com/2018/english/api/sldworksapi/SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IComponent2~GetBodies3.html)
- [SW rebuild status bitmask](https://help.solidworks.com/2026/English/api/swconst/SolidWorks.Interop.swconst~SolidWorks.Interop.swconst.swModelRebuildStatus_e.html)

## Same-enumeration native evidence seam (2026-11-04)

`capture_assembly(..., generation=<nonnegative integer>)` uses A02's native
persistent-reference capture on each exact occurrence object read for physical
properties. Names are descriptive; identical display names do not merge objects.
It captures root-document mate features twice, rejects changes, duplicate reference
tokens and changed occurrence sets, and carries source hash/configuration/generation.
The generation is caller-owned evidence, not a lock, global counter or permission.
No shared state is written. Without generation the earlier snapshot-local capture
remains available; its null generation cannot satisfy A02 snapshot mapping guards.

Unknown mate records are retained, and `mates_complete` remains false even for an
empty list: root traversal does not establish nested/flexible/global completeness.
References are captured but not resolved, and do not authorize CAD writes. The
before/after check detects observed changes; it cannot establish an atomic native
transaction or detect changes that revert between reads. An exclusively owned
source remains a caller prerequisite. This seam is source-only and has no live COM
validation. Context mesh frame is still unknown, so automatic physical output is
blocked rather than substituting standalone geometry for assembly cuts.

`capture_mode="context"` requires generation and binds each occurrence reference
to `capture_context_component` using that same native object. Root assembly mass
is never transformed again. Referenced configuration is retained separately from
the part document's active configuration, which is checked before/after for drift.
There is no fallback from a failed context read to standalone geometry: failed
occurrences keep their identity and explicit unsupported status. The context mode
retains raw mesh coordinates, unknown mesh frame and false physical readiness.
Rigid-group and jointed manifests preserve supplied reference ownership/context
metadata without upgrading the identity resolution status.

`rigid_group_bundle(..., source_frame="root-assembly")` is the pure conversion
seam for context captures whose mesh frame has independently been established as
root-assembly. Both mass and raw metre vertices receive the supplied root-to-link
transform exactly once. Unknown or part-local mesh labels are rejected. The
native adapter never supplies this established label today; changing a string in
untrusted data is not calibration. The default part-local path remains separate.
