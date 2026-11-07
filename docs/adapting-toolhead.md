# CFD inputs, assumptions and adapting another CPAP toolhead

This workflow solves airflow through the ducts and into the surrounding air.
It is intended for a remote CPAP blower represented by a pressure–flow curve at
one inlet. It runs from three boundary STLs; Fusion is only needed to produce
those STLs with the included exporter. The meshing controls came from a tested
BARAM setup. BARAM itself is not a runtime dependency.

## Dependencies and commands

| Task | Dependencies |
| --- | --- |
| Export from the native source | Fusion on macOS or Windows, its Python API, the registered `TeroExport` add-in, and host Python 3. |
| Mesh, solve and produce the heatmap | Host Python 3 standard library and a running Docker engine. The image supplies OpenFOAM 2412, MPI, Python, NumPy, VTK and Matplotlib. No host packages or venv are required. |
| Open the prepared interactive view | Local ParaView with `pvpython` and `paraview` available on `PATH`; macOS app installations are also detected. It uses ParaView's bundled packages. |

The base OpenFOAM image is pinned by digest in `cfd/Dockerfile`, with AMD64 and
ARM64 builds. Rendering packages are installed from the base Ubuntu distribution
when building. A build needs network access; mesh, solve and rendering containers
run with networking disabled. They bind-mount their result folder for output.

```sh
cp config/resources.example.json config/resources.local.json
# Set CPU, memory and time limits for this machine.
python3 scripts/run_cfd.py --build
python3 scripts/run_cfd.py --stls /path/to/boundary-stls \
  --profile /path/to/blower.json --settings /path/to/settings.json --iterations 100
```

Use the short run to check the workflow, then choose a suitable iteration budget.
`--mesh-only` stops before solving and does not generate a heatmap. Every normal
run creates a new results folder; the command is not a continuation of an old run.
The Fusion archive, export manifest and Fusion installation are unnecessary on
the CFD machine. Copy the three STLs, runner/container files, settings and fan
profile there.

## The three-STL contract

All three files must use **millimeters and the same assembly coordinate frame**.
The runner converts vertices to meters when creating the OpenFOAM case. STL
does not carry units, component identities or CAD roles; those cannot be inferred
reliably from arbitrary part meshes.

| File | Meaning |
| --- | --- |
| `walls.stl` | All surfaces separating the simulated air from solid obstacles, including the duct passage walls and relevant exterior obstructions. |
| `inlet.stl` | One consistently wound planar fan-inlet boundary. Its triangle order must give the normal pointing **out of the fluid**. Area, centroid and normal are measured from these triangles. |
| `farfield.stl` | The open ambient boundaries surrounding the external jets. |

Together these surfaces must enclose **one connected fluid region**, including
both the duct passages and the exterior air. Their shared edges must agree;
holes, intersecting facets, sealed passages or disconnected trapped pockets can
prevent meshing or give the wrong fluid region. The code does not repair parts.

The duct mouths are openings **inside** this connected fluid domain. They are
not prescribed outlet boundary conditions and must not be capped as walls. The
runner does not assume six mouths, their names, their heights, a circular inlet
or any particular nozzle, screws or hotend. Multiple independently driven fan
inlets are not supported by this three-patch setup.

Choose an ambient domain large enough for the jets and entrainment; moving its
boundaries farther away should not materially change the quantities of interest.
Include a bed, printed object or other obstruction explicitly if its influence
is part of the question. The current Tero setup has no bed or printed object;
its below-tip plume is free-air flow, not a solved flow footprint on a print.

## Producing the inputs in CAD

Another CAD package or exporter can produce the three files directly. To reuse
the included Fusion exporter, the native model needs the following organization:

```text
CFD
  common
    solids / arbitrary solid bodies or nested components
    domain / domain
  large                         # selected variant; small is the other built-in variant
    solids / variant-specific solid bodies
    reference / inlet
```

The exporter combines `common` with exactly one variant. A role such as
`domain/domain` or `reference/inlet` must occur once across that combination;
it may live under either group. Bodies beneath `solids` may have arbitrary names
and counts. Browser visibility does not select simulation membership.
`SOURCE ONLY - …` bodies are excluded. Other CAD groups, including older
refinement, probe, monitor and measurement groups, are **not consumed** by the
current STL workflow.

The exporter subtracts all selected solids from the `domain/domain` body in
Fusion, requires one connected air volume, and tessellates its boundary using
Fusion's default Save as Mesh settings. The domain must be an axis-aligned box.
`reference/inlet` must be one planar sheet matching the inlet aperture on that
outer domain boundary. Triangles on that sheet become `inlet`; the remaining
outer-box faces become `farfield`; other faces become `walls`.

Author the simulation solids as linked copies of the intended finished parts.
New mechanical features must precede the CFD copy features in the timeline to
appear in those copies. Replacing a source body or face may require repairing
its references. Adding a new mechanical part does not automatically include it
in the simulation. Check the isolated CFD assembly after geometry changes.

Geometry simplifications belong in that assembly and should be visible in CAD.
Tero's CFD preparation includes closing the nozzle bore and screw drives and
CFD-only shell/shield adjustments. Review such choices for another toolhead;
the Python runner neither knows nor recreates them. Closed bores remove those
air paths, and shell offsets change the space available to the air.

The included exporter is still coupled to Tero's `large`/`small` variants, five
manufacturing part roles, four sacrificial-material controls and the
`m3_threaded_diameter` parameter. Rebuilt CAD identifies parts and controls with
`toolhead.export` attributes; legacy designs use the names in
`scripts/fusion/TeroExport/exporter.py`. Stock controls do not need to occupy the
last four timeline positions. The exporter suppresses stock for CFD, then enables
it and sets the pilots for manufacturing STLs. Both clip roles are packaged in
`clips-both.stl` at their installed positions, producing four manufacturing files.
Adapt these roles when reusing the exporter for another design; they are not
required by `run_cfd.py --stls` with boundary files from elsewhere.

For rebuilt designs tagged with export schema `1`, duct and CFD exports use
periodic-surface conversion and face subdivision in disposable staging geometry,
then Fusion's default tessellation. Mesh processing may weld boundary vertices
within 0.001 mm and close only triangular slivers with altitude at most 0.001 mm,
longest edge at most 1 mm and area at most 0.001 mm². Unresolved defects abort
export, as do failures of the mesh-volume or bounding-box checks. This export-only
processing does not modify the source CAD or create simulation simplifications.

`export_stls.py` opens the **repository** `cad/toolhead.f3d` in a disposable
document, not the current cloud document. Save/update that archive deliberately
and run the export command after changing CAD. `exports/manifest.json` only
checks native-source/export-script/STL synchronization; CFD does not read it.
There is no STEP translation. Export jobs record mesh checks separately from the
synchronization manifest; the CFD runner does not repair input parts.

## Blower and physical assumptions

The case uses steady, incompressible, constant-property, isothermal RANS with
`simpleFoam` and k–omega SST. There is no temperature equation, conjugate heat
transfer, buoyancy, rotating fan, transient jet motion, moving toolhead or
extrusion. An airspeed heatmap is not a cooling-temperature prediction. The
[OpenFOAM simpleFoam documentation](https://doc.openfoam.com/2306/tools/processing/solvers/rtm/incompressible/simpleFoam/)
describes the underlying steady incompressible solver.

The generated boundary conditions are:

- **Walls:** stationary no-slip velocity, pressure zero gradient and turbulence
  wall functions. No measured surface-roughness model is supplied.
- **Inlet:** `fanPressure` with the profile curve, ambient reference total
  pressure zero, and `pressureInletOutletVelocity`. Inlet turbulence uses the
  configured intensity and mixing length. Swirl and a measured nonuniform
  blower/hose velocity profile are not imposed.
- **Farfield:** zero reference total pressure and pressure-driven velocity that
  permits inflow or outflow, with specified turbulence values for ambient inflow.
  This is open air, not a physical wall.

A profile contains a `name`, a `description` and
`inlet_model.curve_points_m3_h_pa`: increasing volume-flow values in **m³/h** paired
with non-increasing pressure values in **Pa**. Values must be finite and
nonnegative. The runner converts flow to m³/s and pressure to kinematic pressure
by dividing by density. The fan boundary selects the operating flow from the
curve and the simulated system resistance; `initial_flow_m3_h` is only an
initialization value. Values beyond the curve endpoints are clamped. See the
[OpenFOAM fanPressure documentation](https://doc.openfoam.com/2312/tools/processing/boundary-conditions/rtm/derived/inletOutlet/fanPressure/)
for the total-pressure/flow boundary model.

Use a curve appropriate to the blower, its actual operating setting and the
location of this inlet boundary. The supplied `ws9290` curve is nominal and is
not a calibrated controller-percentage model. Hose, fittings, leaks and upstream
losses are absent unless included in geometry or represented in an appropriate
effective inlet curve. Do not count those losses twice. A different blower
normally needs a different profile.

A small fan can sometimes use the same pressure–flow approximation, but this
does not model its blades, hub blockage, swirl or outlet profile automatically.
Multiple fans, a fan inside the domain, or important rotating-flow effects need
different boundaries or a different fan model; changing the profile alone is
not sufficient.

## Settings and the remaining design coupling

Copy `cfd/settings.json`, edit it, and pass it with `--settings`. These are the
current defaults, not values automatically fitted to a new design:

| Setting | Current value and purpose |
| --- | --- |
| `mesh.background_mm` | 1.6 mm base cells. |
| `mesh.wall_mm`, `mesh.inlet_mm` | 0.4 / 0.2 mm requested wall/inlet refinement. |
| `mesh.near_wall_mm`, `mesh.near_wall_distance_mm` | 0.2 mm cells requested within 0.8 mm of walls. |
| `mesh.max_cells` | 8,000,000 refinement limit; not a promise of a specific cell count or memory use. |
| `mesh.location_mm` (optional) | A point inside the intended fluid region. Otherwise placed just inside the inlet, along its inward normal, at 20% of equivalent inlet radius capped at 1 mm. Override for curved, obstructed or unusual inlets. |
| `air.density_kg_m3`, `air.nu_m2_s` | 1.2 kg/m³ and 0.000015 m²/s. |
| `air.turbulence_intensity`, `air.mixing_length_mm` | 0.05 and 1.1 mm; inlet turbulence assumptions, not inferred from CAD. |
| `solver.iterations`, `solver.residual` | Maximum 1800 steady iterations and a 0.00001 residual target for pressure, velocity, k and omega. `--iterations` overrides the budget. |
| `solver.write_interval` | 100 iterations; the case retains three written checkpoints, plus reconstructed latest parallel fields. |
| `solver.initial_flow_m3_h` | 32 m³/h for velocity/turbulence initialization; not prescribed solved flow. |
| `view.nozzle_tip_mm` | `[0, -3, 0]` in the CAD/STL coordinate frame. Used only to position and label the heatmap. |

The solver does not require a particular global orientation. The **picture**
assumes Z up along the nozzle, looks along Y, and plots X horizontally. Set the
nozzle-tip point for the new model; the slice passes through its Y coordinate
and plot heights are relative to its Z coordinate. Tero's CAD Y = −3 mm is
nozzle-relative Y = 0. Anonymous boundary STLs cannot update this point when
the nozzle moves; update it in settings. Reorient the geometry or adapt the
renderer for a differently oriented nozzle.

Mesh sizes become octree refinement levels rounded up from the base size, so
actual sizes change in powers of two. `cfd/templates/snappyHexMeshDict` contains
the BARAM-derived snapping/quality controls. `addLayers` is false: there are no
prism boundary layers. `cfd/templates/fvSchemes` and the case generation in
`scripts/run_cfd.py` contain the numerical schemes, relaxation factors and
boundary models. Review wall/jet resolution for a new geometry; increasing the
iteration count cannot recover geometric detail missing from the mesh.

`config/resources.local.json` sets CPU and memory limits independently of the
physics. Docker's VM limits are an additional ceiling. The example allows four
CPUs, 10 GiB, 3600 seconds for meshing and 14400 seconds for solving. Rendering
uses the same CPU/memory bounds and a default 3600-second allowance; an optional
`render_timeout_seconds` overrides it. Legacy `stage_timeout_seconds` is the
fallback when a stage-specific value is absent. A timeout stops that stage's
container; it does not resume or extend the run automatically. Rendering also
needs memory to read the saved mesh and field.

## Adapting a new toolhead, in order

1. Choose the finished solids and relevant obstructions in CAD, including any
   intentional analysis simplifications, then author the inlet and ambient
   domain. Inspect the resulting connected air volume.
2. Export the three matching boundary STLs in millimeters. Use the native
   exporter only after adapting its Tero-specific selectors/variants, or obtain
   these boundaries from another CAD workflow.
3. Copy a profile and replace its curve with one appropriate to the blower and
   upstream setup. Copy the settings and review mesh sizes and inlet turbulence.
4. Measure the nozzle-tip centre in that same assembly coordinate frame and set
   `view.nozzle_tip_mm`. Check that the model has Z up and Y front-to-back for the
   supplied front-view renderer.
5. Run `--mesh-only`, inspect the mesh and logs, then run a short full case to
   check fields, heatmap placement and boundary-flow signs. Use a longer run and
   mesh-refinement comparison to assess the actual design.

## Outputs and judging a new toolhead

The final command output lists the products that actually exist:

- `case/case.foam`: the OpenFOAM mesh and saved velocity, pressure and turbulence
  fields. Pressure `p` is kinematic pressure; multiply by density for Pa.
- `results.json`: final residuals, the solver's convergence flag, recent inlet
  and farfield volume flows, and their relative imbalance. Boundary flow is
  positive out of the fluid, so inlet flow is normally negative.
- `front-slice.png`: automatically rendered airspeed magnitude in m/s at the
  latest saved timestep, on the configured nozzle-centre Y plane. Grey is no
  fluid sample in the section; faint outlines project the complete geometry.
  These outlines can show objects outside the colored slice. This is a single
  plane, not an all-depth projection or a measure of flow striking a surface.
- `run.json` and `*.log`: the supplied settings/profile/resources, input STL
  hashes, mesh/surface diagnostics and mesh/solver/render progress.

To regenerate just the heatmap for a saved run, without another solve:

```sh
python3 scripts/run_cfd.py --render results/RUN_DIRECTORY
```

It uses the saved view settings. For older runs without them, it uses the view
point in the current `--settings` file without rewriting their saved physics.
After changing `cfd/Dockerfile`, rebuild the image before running or rendering.

The existing optional interactive output remains available:

```sh
python3 scripts/run_cfd.py --view results/RUN_DIRECTORY
```

This prepares/caches `streamlines-ITERATION.vtp`, `view.pvsm` and `view.png`, then
opens ParaView at the saved iteration. Streamlines are seeded over the inlet;
their visual density is not a quantitative cooling or per-exit flow measurement.

Inspect `surface-check.log` and `check-mesh.log`. Runtime gates require one
connected region, positive cell volumes, valid boundary definitions and exactly
the three expected nonempty patches. Other mesh/surface warnings remain visible;
passing these gates does not prove every outlet or thin gap matches CAD.
Check important openings and near-tip geometry against the mesh, examine mass
balance and residual/flow histories, and compare with a finer mesh before
trusting small design differences. Reaching the iteration limit is not
convergence; solver residual convergence alone is not mesh independence.

For part cooling, assess airflow around and below the tip and coverage of the
deposition area while avoiding unnecessary flow onto the hot nozzle. A free-air
slice can help compare aiming; it cannot establish performance on a particular
print surface or predict cooling temperatures.
