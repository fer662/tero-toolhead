"""Export the repository archive in a disposable Fusion document."""
import hashlib
import json
from pathlib import Path
import re
import struct
import traceback

# All design-specific manufacturing names live here. CFD membership lives in CAD.
ALUMINUM = {
    'core': ('02 Carriage with integrated front face', 'Carriage core - clean relieved Sherpa ramp'),
    'clip-left': ('12 Left toothed belt return clip', 'Toothed front return belt clip'),
    'clip-right': ('13 Right toothed belt return clip', 'Toothed front return belt clip'),
    'duct-large': ('98 Organic duct', 'Duct - generated organic path'),
    'duct-small': ('99 ALTERNATIVE DUCT', 'Duct - 16.4 inlet - 16.7 lip - 13.9 bore'),
}
STOCKS = [
    'MILLING STOCK - SMALL DUCT Beacon 0.5 mm - suppress for PLA',
    'MILLING STOCK - CHUBE 0.5 mm - suppress for PLA',
    'MILLING STOCK - MGN12 0.5 mm - suppress for PLA',
    'MILLING STOCK - Beacon 0.5 mm - suppress for PLA',
]
STOCK_ROLES = {'chube', 'mgn12', 'beacon-large', 'beacon-small'}


def attribute(entity, name):
    value = entity.attributes.itemByName('toolhead.export', name)
    if not value:
        native = getattr(entity, 'nativeObject', None)
        if native:
            value = native.attributes.itemByName('toolhead.export', name)
    return value.value if value else None


def resolve_stocks(design):
    """Prefer explicit CAD identities; retain support for published legacy CAD."""
    tagged = [(attribute(f, 'stock'), f) for c in design.allComponents
              for f in c.features if attribute(f, 'stock')]
    if tagged:
        if len(tagged) != 4 or {role for role, _ in tagged} != STOCK_ROLES:
            raise RuntimeError('Expected one stock feature for each of the four CAD roles')
        return [feature for _, feature in sorted(tagged, key=lambda item: item[0])]
    features = [f for c in design.allComponents for f in c.features if f.name in STOCKS]
    if len(features) != 4 or {f.name for f in features} != set(STOCKS):
        raise RuntimeError('Expected four unambiguous sacrificial-material features')
    return features


def resolve_parts(occurrences):
    parts = {}
    tagged = []
    for occurrence in occurrences:
        if attribute(occurrence.component, 'kind') != 'manufacturing':
            continue
        for body in occurrence.bRepBodies:
            role = attribute(body, 'part')
            if role == 'clip':
                role = attribute(occurrence, 'part')
                if role not in ('clip-left', 'clip-right'):
                    raise RuntimeError('Shared clip requires an explicit occurrence export role')
            if role:
                tagged.append((role, body))
    if tagged:
        for role, body in tagged:
            if role in parts or role not in ALUMINUM:
                raise RuntimeError('Duplicate or unknown manufacturing role: '+role)
            parts[role] = body
    else:
        for role, (prefix, name) in ALUMINUM.items():
            matches = [b for o in occurrences if o.name.startswith(prefix)
                       for b in o.bRepBodies if b.name == name]
            if len(matches) != 1:
                raise RuntimeError('Expected one manufacturing body: '+role)
            parts[role] = matches[0]
    if set(parts) != set(ALUMINUM):
        raise RuntimeError('Missing manufacturing roles: '+str(set(ALUMINUM)-set(parts)))
    return parts


def combine_stls(paths, destination):
    """Package binary STL triangles without changing any vertex or normal."""
    records = []
    count = 0
    for path in paths:
        data = Path(path).read_bytes()
        if len(data) < 84:
            raise RuntimeError('Invalid binary STL: '+str(path))
        triangles = struct.unpack_from('<I', data, 80)[0]
        if not triangles or len(data) != 84 + 50*triangles:
            raise RuntimeError('Invalid binary STL: '+str(path))
        count += triangles
        records.append(data[84:])
    if not count:
        raise RuntimeError('No STL triangles to combine')
    Path(destination).write_bytes(b'Both clips - original assembly positions'.ljust(80,b'\0')
                                 + struct.pack('<I', count) + b''.join(records))


def validate_mesh(path, allow_submicron_seams=False):
    """Close bounded tessellation seams, with vertex movement limited to one micron."""
    import collections
    import math
    data = path.read_bytes()
    count = struct.unpack_from('<I', data, 80)[0]
    if not count or len(data) != 84 + count*50:
        raise RuntimeError('Invalid binary STL: '+str(path))
    facets = [data[i:i+50] for i in range(84, len(data), 50)]

    def inspect(records):
        edges = collections.defaultdict(list)
        volume = 0.0
        bounds = [[math.inf]*3, [-math.inf]*3]
        for facet in records:
            values = struct.unpack('<12fH', facet)
            if not all(math.isfinite(v) for v in values[:12]):
                raise RuntimeError('Nonfinite STL coordinate')
            points = [tuple(values[i:i+3]) for i in (3,6,9)]
            a,b,c = points
            u = [b[i]-a[i] for i in range(3)]
            v = [c[i]-a[i] for i in range(3)]
            cross = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]]
            if sum(x*x for x in cross) == 0:
                raise RuntimeError('Degenerate STL triangle')
            volume += sum(a[i]*cross[i] for i in range(3))/6
            for point in points:
                for i in range(3):
                    bounds[0][i] = min(bounds[0][i], point[i])
                    bounds[1][i] = max(bounds[1][i], point[i])
            for left,right in zip(points,points[1:]+points[:1]):
                edges[tuple(sorted((left,right)))].append((left,right))
        return edges,volume,bounds

    edges,_,_ = inspect(facets)
    boundary = [values[0] for values in edges.values() if len(values)==1]
    welds = []
    dropped = []
    if boundary and allow_submicron_seams:
        vertices = sorted({p for edge in boundary for p in edge})
        if len(vertices)>1000:
            raise RuntimeError('Too many open mesh vertices for bounded seam repair')
        groups = []
        mapping = {}
        for point in vertices:
            group = next((g for g in groups if all(math.dist(point,p)<=.001 for p in g)),None)
            if group is None:
                group = []
                groups.append(group)
            group.append(point)
            mapping[point] = group[0]
        welds = [g for g in groups if len(g)>1]
        if welds:
            kept = []
            for index,facet in enumerate(facets):
                values = struct.unpack('<12fH',facet)
                points = [tuple(values[i:i+3]) for i in (3,6,9)]
                points = [mapping.get(p,p) for p in points]
                if len(set(points))<3:
                    dropped.append(index)
                    continue
                kept.append(struct.pack('<12fH',*values[:3],*[x for p in points for x in p],values[-1]))
            facets = kept
            edges,_,_ = inspect(facets)
            if any(len(v)>2 for v in edges.values()):
                raise RuntimeError('Seam welding produced a non-manifold edge')
            boundary = [values[0] for values in edges.values() if len(values)==1]
    repairs = []
    if boundary:
        if not allow_submicron_seams:
            raise RuntimeError('Open STL boundary')
        outgoing = {}
        for a,b in boundary:
            if b in outgoing:
                raise RuntimeError('Branched STL boundary')
            outgoing[b] = a  # Opposite the adjacent triangle's orientation.
        while outgoing:
            start = next(iter(outgoing))
            points = []
            current = start
            while True:
                if current not in outgoing or len(points)>3:
                    raise RuntimeError('Unsupported STL boundary loop')
                points.append(current)
                current = outgoing.pop(current)
                if current == start:
                    break
            if len(points)!=3:
                raise RuntimeError('Only triangular tessellation slivers may be closed')
            a,b,c = points
            u = [b[i]-a[i] for i in range(3)]
            v = [c[i]-a[i] for i in range(3)]
            normal = [u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]]
            area2 = math.sqrt(sum(x*x for x in normal))
            longest = max(math.dist(p,q) for p in points for q in points)
            altitude = area2/longest
            if not (0 < altitude <= .001 and longest <= 1 and area2/2 <= .001):
                raise RuntimeError('STL gap exceeds the submicron tessellation-seam limit')
            facets.append(struct.pack('<12fH',*[x/area2 for x in normal],*[x for p in points for x in p],0))
            repairs.append({'vertices_mm':points,'altitude_mm':altitude,'area_mm2':area2/2})
    edges,volume,bounds = inspect(facets)
    if any(len(v)!=2 or v[0] != (v[1][1],v[1][0]) for v in edges.values()):
        raise RuntimeError('STL is not a closed consistently oriented manifold')
    if volume<=0:
        raise RuntimeError('STL signed volume is not positive')
    if repairs or welds:
        path.write_bytes(data[:80]+struct.pack('<I',len(facets))+b''.join(facets))
    return {'triangles':len(facets),'signed_volume_mm3':volume,'bounds_mm':bounds,
            'closed_oriented_manifold':True,'seam_triangles_added':repairs,'seam_vertex_groups':welds,
            'collapsed_seam_triangles_removed':dropped}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def export_job(request_file):
    import adsk.core as ac
    import adsk.fusion as af
    request_file = Path(request_file)
    request = json.loads(request_file.read_text())
    result_file = request_file.with_name('result.json')
    if result_file.exists():
        raise RuntimeError('Export job already completed')
    with request_file.with_name('started.json').open('x') as stream:
        json.dump({'job_id': request['job_id']}, stream)
    app = ac.Application.get()
    original = app.activeDocument
    doc = None
    try:
        if app.userInterface.activeCommand != 'SelectCommand':
            raise RuntimeError('Finish the active Fusion command before exporting')
        if request['operation'] != 'stl' or sha(request['source']) != request['source_sha256']:
            raise RuntimeError('Invalid operation or changed input archive')
        output = Path(request['output'])
        output.mkdir(parents=True, exist_ok=False)
        doc = app.importManager.importToNewDocument(app.importManager.createFusionArchiveImportOptions(request['source']))
        if not doc or doc == original or doc.dataFile:
            raise RuntimeError('Expected a disposable local archive import')
        design = af.Design.cast(doc.products.itemByProductType('DesignProductType'))
        root = design.rootComponent
        occurrences = list(root.allOccurrences)
        tm = af.TemporaryBRepManager.get()
        stocks = resolve_stocks(design)
        improved_meshes = attribute(root, "schema") == "1"
        mesh_checks = {}

        def health():
            failed = (af.FeatureHealthStates.WarningFeatureHealthState, af.FeatureHealthStates.ErrorFeatureHealthState)
            errors = [c.name+'/'+f.name+': '+f.errorOrWarningMessage
                      for c in design.allComponents for f in c.features
                      if not f.isSuppressed and f.healthState in failed]
            if errors:
                raise RuntimeError('Active feature warnings/errors: '+'; '.join(errors))

        def stock(enabled):
            for feature in stocks:
                feature.isSuppressed = not enabled
            design.timeline.moveToEnd()
            health()

        def stl(body, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            target_doc = app.documents.add(ac.DocumentTypes.FusionDesignDocumentType)
            try:
                target = af.Design.cast(target_doc.products.itemByProductType('DesignProductType'))
                base = target.rootComponent.features.baseFeatures.add()
                base.startEdit()
                solid = target.rootComponent.bRepBodies.add(tm.copy(body), base)
                base.finishEdit()
                solid = target.rootComponent.bRepBodies.item(0)
                improve = improved_meshes and path.stem.startswith(('duct-', 'fluid-'))
                if improve:
                    expected_volume = solid.getPhysicalProperties(af.CalculationAccuracy.VeryHighCalculationAccuracy).volume*1000
                    native_box = solid.preciseBoundingBox
                    expected_bounds = [[v*10 for v in p.asArray()] for p in (native_box.minPoint,native_box.maxPoint)]
                    converted = tm.copy(solid).convert(af.BRepConvertOptions.SplitPeriodicFacesConversion)
                    converted_base = target.rootComponent.features.baseFeatures.add()
                    converted_base.startEdit()
                    target.rootComponent.bRepBodies.add(converted, converted_base)
                    converted_base.finishEdit()
                    solid = target.rootComponent.bRepBodies.item(1)
                    faces = ac.ObjectCollection.create()
                    for face in solid.faces:
                        if (ac.NurbsSurface.cast(face.geometry) and face.area>1
                            and face.boundingBox.minPoint.z<6.5<face.boundingBox.maxPoint.z):
                            faces.add(face)
                    if faces.count:
                        pi = target.rootComponent.constructionPlanes.createInput()
                        pi.setByOffset(target.rootComponent.xYConstructionPlane,ac.ValueInput.createByReal(6.5))
                        plane = target.rootComponent.constructionPlanes.add(pi)
                        splits = target.rootComponent.features.splitFaceFeatures
                        split = splits.add(splits.createInput(faces,plane,True))
                        if split.healthState != af.FeatureHealthStates.HealthyFeatureHealthState:
                            raise RuntimeError('Unhealthy export-only face subdivision')
                        solid = target.rootComponent.bRepBodies.item(1)
                options = target.exportManager.createSTLExportOptions(solid, str(path))
                options.sendToPrintUtility = False
                options.unitType = af.DistanceUnits.MillimeterDistanceUnits
                # Keep Fusion's original default tessellation for every archive.
                if not target.exportManager.execute(options):
                    raise RuntimeError('STL export failed: '+str(path))
                if improved_meshes:
                    check = validate_mesh(path, allow_submicron_seams=improve)
                    check['mesh_settings'] = {key:getattr(options,key) for key in ['meshRefinement','surfaceDeviation','normalDeviation','maximumEdgeLength','aspectRatio']}
                    if improve:
                        check['native_volume_mm3'] = expected_volume
                        check['relative_volume_error'] = abs(check['signed_volume_mm3']/expected_volume-1)
                        check['max_bound_error_mm'] = max(abs(a-b) for p,q in zip(check['bounds_mm'],expected_bounds) for a,b in zip(p,q))
                        if check['relative_volume_error']>.001 or check['max_bound_error_mm']>.05:
                            raise RuntimeError('Export mesh exceeds native-volume/bounds checks: '+str(path))
                    mesh_checks[path.relative_to(output).as_posix()] = check
            finally:
                target_doc.close(False)

        stock(False)
        # Only the CAD's solids, domain and inlet participate. No measurements,
        # topology maps or STEP translation. Rebuilt archives receive export-only
        # periodic representation conversion, subdivision and bounded seam closure.
        for variant in ('large', 'small'):
            objects = {}
            for occurrence in occurrences:
                names = [re.sub(r' \(\d+\)$', '', p.rsplit(':', 1)[0]) for p in occurrence.fullPathName.split('+')]
                if len(names) < 3 or names[0] != 'CFD' or names[1] not in ('common', variant):
                    continue
                for body in occurrence.bRepBodies:
                    if body.name.startswith('SOURCE ONLY - '):
                        continue
                    role = '/'.join([*names[2:], body.name])
                    if role.startswith('solids/') or role in ('domain/domain', 'reference/inlet'):
                        if role in objects:
                            raise RuntimeError('Duplicate CAD CFD role: '+role)
                        objects[role] = tm.copy(body)
            domain = objects['domain/domain']
            inlet = objects['reference/inlet']
            box = domain.preciseBoundingBox
            bounds = [v*10 for v in box.minPoint.asArray()+box.maxPoint.asArray()]
            box_volume = (bounds[3]-bounds[0])*(bounds[4]-bounds[1])*(bounds[5]-bounds[2])
            if abs(domain.volume*1000/box_volume-1) > 1e-5:
                raise RuntimeError('CFD domain must be an axis-aligned box')
            if inlet.faces.count != 1 or not ac.Plane.cast(inlet.faces.item(0).geometry):
                raise RuntimeError('CFD inlet must be one planar sheet')
            plane = ac.Plane.cast(inlet.faces.item(0).geometry)
            origin = [x*10 for x in plane.origin.asArray()]
            normal = plane.normal.asArray()
            fluid = tm.copy(domain)
            solids = [(role, body) for role, body in objects.items() if role.startswith('solids/')]
            if not solids:
                raise RuntimeError('No solids in native CFD assembly')
            for role, body in solids:
                if not body.isSolid or not tm.booleanOperation(fluid, body, af.BooleanTypes.DifferenceBooleanType):
                    raise RuntimeError('Could not subtract '+variant+'/'+role+' from the CAD domain')
            if not fluid.isSolid or fluid.lumps.count != 1:
                raise RuntimeError('CAD CFD assembly must form one connected air volume')
            raw = output / ('fluid-'+variant+'.stl')
            stl(fluid, raw)
            data = raw.read_bytes()
            count = struct.unpack_from('<I', data, 80)[0]
            if len(data) != 84+50*count:
                raise RuntimeError('Expected Fusion default binary STL output')
            regions = {'walls': [], 'inlet': [], 'farfield': []}
            for offset in range(84, len(data), 50):
                facet = data[offset:offset+50]
                values = struct.unpack('<12fH', facet)
                vertices = [values[3:6], values[6:9], values[9:12]]
                center = [sum(p[i] for p in vertices)/3 for i in range(3)]
                on_plane = all(abs(sum((p[i]-origin[i])*normal[i] for i in range(3))) < 1e-4 for p in vertices)
                if on_plane and app.measureManager.measureMinimumDistance(ac.Point3D.create(*[x/10 for x in center]), inlet).value < 1e-5:
                    name = 'inlet'
                elif any(all(abs(p[i]-bound) < 1e-4 for p in vertices) for i in range(3) for bound in (bounds[i], bounds[i+3])):
                    name = 'farfield'
                else:
                    name = 'walls'
                regions[name].append(facet)
            for name, facets in regions.items():
                if not facets:
                    raise RuntimeError('Missing CFD boundary: '+name)
                path = output / 'cfd' / variant / (name+'.stl')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(name.encode().ljust(80,b'\0')+struct.pack('<I',len(facets))+b''.join(facets))
            raw.unlink()

        thread = design.userParameters.itemByName('m3_threaded_diameter')
        diameter = request['pilot_diameter_mm']
        if not 2 <= diameter <= 2.9 or not thread:
            raise RuntimeError('Expected the threaded-pilot parameter and a diameter between 2 and 2.9 mm')
        thread.expression = f'{diameter:g} mm'
        stock(True)
        if abs(thread.value*10-diameter) > 1e-7:
            raise RuntimeError('Threaded-pilot diameter did not evaluate')
        for role, body in resolve_parts(occurrences).items():
            stl(body, output / 'stl' / (role+'.stl'))
        clips = [output/'stl'/(role+'.stl') for role in ('clip-left','clip-right')]
        combined = output/'stl/clips-both.stl'
        combine_stls(clips, combined)
        mesh_checks['stl/clips-both.stl'] = validate_mesh(combined)
        for clip in clips:
            clip.unlink()
            mesh_checks.pop(clip.relative_to(output).as_posix(), None)
        if sha(request['source']) != request['source_sha256']:
            raise RuntimeError('Input archive changed during export')
        result = {'success': True, 'job_id': request['job_id'], 'mesh_checks': mesh_checks}
    except BaseException:
        result = {'success': False, 'job_id': request['job_id'], 'error': traceback.format_exc()}
    finally:
        if doc:
            doc.close(False)
        if original and original.isValid:
            original.activate()
    result_file.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))
    return result
