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
        stocks = [design.timeline.item(i).entity for i in range(design.timeline.count-4, design.timeline.count)]
        if [s.name for s in stocks] != STOCKS:
            raise RuntimeError('Expected the four final sacrificial-material features')

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
                options = target.exportManager.createSTLExportOptions(solid, str(path))
                options.sendToPrintUtility = False
                options.unitType = af.DistanceUnits.MillimeterDistanceUnits
                # Same tessellation/refinement settings as Fusion's default Save as Mesh.
                if not target.exportManager.execute(options):
                    raise RuntimeError('STL export failed: '+str(path))
            finally:
                target_doc.close(False)

        stock(False)
        # Only the CAD's solids, domain and inlet participate. No measurements,
        # topology maps, per-part repairs, STEP translation or cross-kernel checks.
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
        for role, (prefix, name) in ALUMINUM.items():
            matches = [b for o in occurrences if o.name.startswith(prefix) for b in o.bRepBodies if b.name == name]
            if len(matches) != 1:
                raise RuntimeError('Expected one manufacturing body: '+role)
            stl(matches[0], output / 'stl' / (role+'.stl'))
        if sha(request['source']) != request['source_sha256']:
            raise RuntimeError('Input archive changed during export')
        result = {'success': True, 'job_id': request['job_id']}
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
