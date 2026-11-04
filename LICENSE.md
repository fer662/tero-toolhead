# Licensing

Tero uses separate licenses for original design material and original software.
Copyright (c) 2026 Fernando Mazzon, for original contributions.

## CAD, design documentation and data

Original Tero CAD, design documentation and project data are licensed under
**Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International
(CC BY-NC-SA 4.0)**. This covers original contributions in:

- `cad/`;
- `exports/`, including STL exports and their synchronization hashes;
- `README.md` and `docs/`;
- `profiles/`, subject to the third-party source exclusions below.

You may share and adapt this material for noncommercial purposes under the
license's attribution and ShareAlike conditions. When sharing, credit the
creator, link the license and identify modifications. Commercial uses outside
this grant require separate permission from the relevant rights holders.

The full license is in [LICENSES/CC-BY-NC-SA-4.0.txt](LICENSES/CC-BY-NC-SA-4.0.txt).
The [Creative Commons summary](https://creativecommons.org/licenses/by-nc-sa/4.0/)
is provided for convenience; the legal text governs.

## Software tooling

Original software tooling is licensed under the **MIT License**. This covers:

- `scripts/`, including Fusion scripts and add-ins;
- original code and configuration in `cfd/`, subject to the template origin below;
- `config/`, `.githooks/`, `.gitignore`, `.gitattributes` and `.dockerignore`;
- documentation embedded in that software, such as comments and docstrings.

MIT permits commercial and noncommercial software reuse under its notice
conditions. It does not change the license of CAD or data processed by the tools.
The full license is in [LICENSES/MIT.txt](LICENSES/MIT.txt).

## Third-party material

These grants apply only to original Tero contributions. The native assembly
contains imported reference components; their presence does not make them
original Tero work or place them under these licenses. Third-party CAD, source
material used for fan curves, software dependencies, container images and other
third-party material retain their own applicable licenses, notices and permissions.
Derived exports do not remove those upstream obligations.

Preserve applicable upstream attribution and license notices when redistributing
third-party material. A source link or compatibility reference is not itself a
redistribution permission. No ownership, endorsement or relicensing of third-party
material is asserted here.

### Imported CAD references

The following references are included in the native archive. Moving them into
assembly coordinates, assigning materials or simplifying them does not transfer
ownership. The entries marked **unverified** need permission/source confirmation
before publishing the complete native assembly; this table does not grant it.

| Material | Source and credit | Terms and local changes |
| --- | --- | --- |
| CHUBE Air integration assembly | [CHUBE / Luke's Laboratory](https://www.chubehotends.com/integrations), supplied integration CAD | Positioned in Tero; simplified copies are used for CFD. Redistribution terms **unverified**. |
| Sherpa Mini assembly, `sherpa_mini_a3_r17` | [Anlin and Annex Engineering](https://github.com/Annex-Engineering/Sherpa_Mini-Extruder) | Housing/idler references and mounting hardware were removed or adapted for Tero; motor reference replaced. [Annex license](LICENSES/ANNEX-Engineering.txt). The current license asks that Annex parts be removed from shared original assemblies; permission to include this assembly is **unverified**, as is the license accompanying this particular source revision. |
| LDO 36STH20 motor reference | User-supplied `LDO-36STH20-1004.f3d` from Ice Cream Factory Discord | Flattened and positioned with source materials retained. Author, public source and redistribution terms **unverified**. |
| Beacon RevH Normal integration assembly | [Beacon CAD](https://github.com/beacon3d/docs/tree/master/mcad/beacon) | Positioned in Tero; upper keepout reference removed. [Beacon notice](LICENSES/Beacon-CAD.txt), including its separate Molex sub-assembly terms. |
| Detailed MGN12H mesh reference | [Steven de Roos, GrabCAD](https://grabcad.com/library/mgn12h-full-detail-1) | Converted saved SolidWorks meshes and positioned them; assigned materials. Redistribution terms **unverified**. |
| Monolith gantry reference | [Monolith3D](https://github.com/Monolith3D/Monolith_Gantry), supplied `Monolith-Gantry-M-V2-A-9-0-S-S-FT-NP-N-N-350-350.step` | Positioned for fit context, duplicate carriage hidden and reference joints added. Upstream project uses [CC BY-NC-SA 4.0](LICENSES/CC-BY-NC-SA-4.0.txt); source revision and notices for nested purchased parts still need confirmation. |
| Other retained/nested CAD and hardware references | Includes the legacy carriage, E3D V6 nozzle reference, filament guide, fasteners and purchased parts inside imported assemblies | Exact author/source and applicable terms **unverified** where not identified above. No blanket Tero license is granted for them. |

### CFD template, curve and runtime

`cfd/templates/snappyHexMeshDict` uses configuration values from a case generated
by **NEXTFOAM BARAM 26.3.0**, at
[commit 1e68f04](https://github.com/nextfoam/baram/tree/1e68f04da6cd62e0cb89798b2a81f2fdc3fbdeb9).
Tero replaces geometry, mesh sizes, cell limits and seed with runtime inputs and
disables layer addition. No BARAM Python source or application is bundled or
required. BARAM itself is [GPL-3.0](https://github.com/nextfoam/baram/blob/1e68f04da6cd62e0cb89798b2a81f2fdc3fbdeb9/LICENSE).
The MIT grant covers original Tero tooling and template edits; it does not
relicense BARAM or any upstream material.

`profiles/ws9290.json` contains points read from the nominal WM9290-24V curve
[published on the Monolith gantry updates site](https://monolith-gantry-updates.zen3d.eu/img/cpap-image-3.webp).
The original chart is not bundled. The profile description retains that source;
Tero does not claim ownership of the manufacturer's curve.

The container uses the pinned `microfluidica/openfoam` image, OpenFOAM 2412,
NumPy, VTK and Matplotlib. They are runtime dependencies, not original Tero code;
their upstream licenses and notices remain applicable. Fusion and optional
ParaView are separately installed applications.
