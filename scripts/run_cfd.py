#!/usr/bin/env python3
"""Mesh and solve a folder of walls.stl, inlet.stl and farfield.stl (millimeters)."""
import argparse
import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import shlex
import shutil
import struct
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'tero-cfd:local'


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    Path(path).write_text(json.dumps(value,indent=2)+'\n')


def foam(path, body, kind='dictionary'):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(f'FoamFile {{version 2.0; format ascii; class {kind}; object {path.name};}}\n'+body+'\n')


def facets(path):
    data = Path(path).read_bytes()
    if len(data)>=84 and len(data)==84+50*struct.unpack_from('<I',data,80)[0]:
        return [struct.unpack_from('<12fH',data,i)[:12] for i in range(84,len(data),50)]
    vertices=[];result=[]
    for line in data.decode('ascii').splitlines():
        words=line.split()
        if words and words[0]=='vertex':
            vertices.extend(map(float,words[1:4]))
            if len(vertices)==9:
                result.append((0.,0.,0.,*vertices));vertices=[]
    if vertices or not result: raise ValueError('Invalid/empty STL: '+str(path))
    return result


def binary_stl(path, triangles):
    with path.open('wb') as stream:
        stream.write(b'CFD boundary in meters'.ljust(80,b'\0')+struct.pack('<I',len(triangles)))
        for t in triangles:
            stream.write(struct.pack('<12fH',*t[:3],*[v/1000 for v in t[3:]],0))


def inlet_geometry(triangles):
    area=0.;weighted=[0.,0.,0.];vector=[0.,0.,0.]
    for t in triangles:
        a,b,c=t[3:6],t[6:9],t[9:12]
        u=[b[i]-a[i] for i in range(3)];v=[c[i]-a[i] for i in range(3)]
        cross=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]]
        weight=math.sqrt(sum(x*x for x in cross))/2
        area+=weight
        for i in range(3):
            weighted[i]+=(a[i]+b[i]+c[i])/3*weight
            vector[i]+=cross[i]/2
    length=math.sqrt(sum(x*x for x in vector))
    if area<=0 or length/area<.99999:
        raise ValueError('Inlet must be a consistently oriented planar surface')
    normal=[v/length for v in vector];center=[v/area for v in weighted]
    if any(abs(sum((t[j+i]-center[i])*normal[i] for i in range(3)))>1e-3 for t in triangles for j in (3,6,9)):
        raise ValueError('Inlet surface is not planar')
    return area,center,normal


def prepare(work, source, settings, profile, limits):
    mesh=settings['mesh'];air=settings['air'];solver=settings['solver']
    tip=settings.get('view',{}).get('nozzle_tip_mm')
    if not isinstance(tip,list) or len(tip)!=3 or any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in tip):
        raise ValueError('Set view.nozzle_tip_mm to the CAD nozzle-tip coordinates [X, Y, Z] in the settings file')
    sizes=['background_mm','wall_mm','inlet_mm','near_wall_mm','near_wall_distance_mm']
    if any(not math.isfinite(mesh[k]) or mesh[k]<=0 for k in sizes):raise ValueError('Mesh sizes must be positive')
    triangles={name:facets(source/(name+'.stl')) for name in ('walls','inlet','farfield')}
    for name,items in triangles.items():
        if not items or any(not math.isfinite(x) for t in items for x in t):raise ValueError('Invalid STL: '+name)
    area,center,normal=inlet_geometry(triangles['inlet'])
    all_triangles=[t for items in triangles.values() for t in items]
    points=[t[j:j+3] for t in all_triangles for j in (3,6,9)]
    lo=[min(p[i] for p in points)/1000 for i in range(3)];hi=[max(p[i] for p in points)/1000 for i in range(3)]
    if not lo[1]*1000<tip[1]<hi[1]*1000:raise ValueError('view.nozzle_tip_mm places the Y slice outside the fluid domain')
    base=mesh['background_mm']/1000
    counts=[math.ceil((b-a)/base) for a,b in zip(lo,hi)]
    if min(counts)<1:raise ValueError('Boundary has no enclosed volume')
    padding=min((b-a)/n for a,b,n in zip(lo,hi,counts))/100
    lo=[x-padding for x in lo];hi=[x+padding for x in hi]
    case=work/'case';surfaces=case/'constant/triSurface';surfaces.mkdir(parents=True)
    for name,items in triangles.items():binary_stl(surfaces/(name+'.stl'),items)
    binary_stl(surfaces/'boundary.stl',all_triangles)
    vector=lambda p:'('+' '.join(f'{x:.12g}' for x in p)+')'
    vertices=[[x,y,z] for z in (lo[2],hi[2]) for x,y in ((lo[0],lo[1]),(hi[0],lo[1]),(hi[0],hi[1]),(lo[0],hi[1]))]
    faces=[(0,3,7,4),(1,2,6,5),(0,1,5,4),(3,7,6,2),(0,1,2,3),(4,5,6,7)]
    boundary=' '.join(f'box{i} {{type patch; faces ({vector(f)});}}' for i,f in enumerate(faces))
    foam(case/'system/blockMeshDict',f'scale 1; vertices ({" ".join(map(vector,vertices))}); blocks (hex (0 1 2 3 4 5 6 7) {vector(counts)} simpleGrading (1 1 1)); boundary ({boundary});')
    level=lambda mm:max(0,math.ceil(math.log2(mesh['background_mm']/mm)-1e-9))
    geometry=[];features=[];patches=[];extract=[]
    for name in triangles:
        geometry.append(f'{name}.stl {{type triSurfaceMesh; name {name};}}')
        n=0 if name=='farfield' else level(mesh['inlet_mm'] if name=='inlet' else mesh['wall_mm'])
        features.append(f'{{file "{name}.eMesh"; level {max(1,n)};}}')
        patches.append(f'{name} {{level ({n} {max(1,n)}); patchInfo {{type {"wall" if name=="walls" else "patch"};}} addBufferLayers false;}}')
        extract.append(f'{name}.stl {{extractionMethod extractFromSurface; extractFromSurfaceCoeffs {{includedAngle 150;}} writeObj no;}}')
    foam(case/'system/surfaceFeatureExtractDict','\n'.join(extract))
    seed=mesh.get('location_mm',[center[i]-normal[i]*min(1,math.sqrt(area/math.pi)*.2) for i in range(3)])
    regions=f'walls {{mode distance; levels (({mesh["near_wall_distance_mm"]/1000:.12g} {level(mesh["near_wall_mm"])}));}}'
    text=(ROOT/'cfd/templates/snappyHexMeshDict').read_text()
    for key,value in {'GEOMETRY':'\n'.join(geometry),'FEATURES':'\n'.join(features),'SURFACES':'\n'.join(patches),'REGIONS':regions,'MAX_CELLS':str(mesh['max_cells']),'LOCATION':vector([x/1000 for x in seed])}.items():
        text=text.replace('@'+key+'@',value)
    (case/'system/snappyHexMeshDict').write_text(text)
    foam(case/'system/decomposeParDict',f'numberOfSubdomains {limits["cpus"]}; method scotch;')
    (case/'system/fvSchemes').write_bytes((ROOT/'cfd/templates/fvSchemes').read_bytes())
    speed=solver['initial_flow_m3_h']/3600/(area*1e-6)
    k=1.5*(air['turbulence_intensity']*speed)**2
    omega=math.sqrt(k)/(.09**.25*air['mixing_length_mm']/1000)
    rho=air['density_kg_m3']
    curve=profile['inlet_model']['curve_points_m3_h_pa']
    if len(curve)<2 or any(len(p)!=2 or any(not math.isfinite(x) or x<0 for x in p) for p in curve) or any(b[0]<=a[0] or b[1]>a[1] for a,b in zip(curve,curve[1:])):
        raise ValueError('Invalid fan curve')
    table=' '.join(f'({q/3600:.12g} {p/rho:.12g})' for q,p in curve)
    inlet_speed=vector([-speed*n for n in normal])
    fields={
      'U':('volVectorField','[0 1 -1 0 0 0 0]','(0 0 0)','type noSlip;',f'type pressureInletOutletVelocity; value uniform {inlet_speed};','type pressureInletOutletVelocity; value uniform (0 0 0);'),
      'p':('volScalarField','[0 2 -2 0 0 0 0]','0','type zeroGradient;',f'type fanPressure; direction in; fanCurve table ({table}); outOfBounds clamp; p0 uniform 0; value uniform 3000;','type totalPressure; p0 uniform 0; value uniform 0;'),
      'k':('volScalarField','[0 2 -2 0 0 0 0]',str(k),f'type kqRWallFunction; value uniform {k};',f'type turbulentIntensityKineticEnergyInlet; intensity {air["turbulence_intensity"]}; value uniform {k};',f'type inletOutlet; inletValue uniform 0.0001; value uniform {k};'),
      'omega':('volScalarField','[0 0 -1 0 0 0 0]',str(omega),f'type omegaWallFunction; value uniform {omega};',f'type turbulentMixingLengthFrequencyInlet; mixingLength {air["mixing_length_mm"]/1000}; value uniform {omega};',f'type inletOutlet; inletValue uniform 10; value uniform {omega};'),
      'nut':('volScalarField','[0 2 -1 0 0 0 0]','0','type nutUSpaldingWallFunction; value uniform 0;','type calculated; value uniform 0;','type calculated; value uniform 0;')}
    for name,(kind,dimensions,initial,wall,inlet,outlet) in fields.items():
        foam(case/'0'/name,f'dimensions {dimensions}; internalField uniform {initial}; boundaryField {{walls {{{wall}}} inlet {{{inlet}}} farfield {{{outlet}}}}}',kind)
    foam(case/'constant/transportProperties',f'transportModel Newtonian; nu [0 2 -1 0 0 0 0] {air["nu_m2_s"]};')
    foam(case/'constant/turbulenceProperties','simulationType RAS; RAS {RASModel kOmegaSST; turbulence on; printCoeffs on;}')
    target=solver['residual']
    foam(case/'system/fvSolution',f'''solvers {{
 p {{solver GAMG; tolerance 1e-8; relTol 0.05; smoother GaussSeidel;}}
 "(U|k|omega)" {{solver smoothSolver; smoother symGaussSeidel; tolerance 1e-8; relTol 0.1;}}
}}
SIMPLE {{nNonOrthogonalCorrectors 1; consistent yes; residualControl {{p {target}; U {target}; k {target}; omega {target};}}}}
relaxationFactors {{fields {{p 0.15;}} equations {{U 0.3; k 0.5; omega 0.5;}}}}''')
    functions=' '.join(f'{name}Flow {{type surfaceFieldValue; libs (fieldFunctionObjects); writeControl timeStep; writeInterval 10; writeFields false; regionType patch; name {name}; operation sum; fields (phi);}}' for name in ('inlet','farfield'))
    # Preserve the final fields even when endTime is between scheduled writes.
    functions+=' finalFields {type writeObjects; libs (utilityFunctionObjects); objects (U p k omega nut phi); writeOption anyWrite; writeControl onEnd;}'
    interval=min(solver['write_interval'],solver['iterations'])
    foam(case/'system/controlDict',f'''application simpleFoam; startFrom latestTime; startTime 0;
stopAt endTime; endTime {solver['iterations']}; deltaT 1;
writeControl timeStep; writeInterval {interval}; purgeWrite 3; writeFormat binary;
writePrecision 10; writeCompression off; timeFormat general; timePrecision 8;
runTimeModifiable false; functions {{{functions}}}''')
    (case/'case.foam').touch()
    shutil.copy2(ROOT/'cfd/run.sh',work/'run.sh')
    write(work/'run.json',{'settings':settings,'profile':profile,'resources':limits,'inlet_area_mm2':area,'inlet_center_mm':center,'inlet_outward_normal':normal,'mesh_seed_mm':seed,
          'stls_sha256':{name:hashlib.sha256((source/(name+'.stl')).read_bytes()).hexdigest() for name in triangles}})


def stage(work, limits, name, image, extra=()):
    container='tero-cfd-'+uuid.uuid4().hex[:12]
    command=['docker','run','--rm','--name',container,'--network','none','--cpus',str(limits['cpus']),
             '--memory',f'{limits["memory_gb"]}g','--memory-swap',f'{limits["memory_gb"]}g',
             '-e',f'OMP_NUM_THREADS={limits["cpus"]}','-e',f'VTK_SMP_MAX_THREADS={limits["cpus"]}',
             '-v',f'{work}:/work',image,'/work/run.sh',name,str(limits['cpus']),*extra]
    timeout=limits.get(name+'_timeout_seconds',limits.get('stage_timeout_seconds',3600))
    print(f'{name}: {limits["cpus"]} CPUs, {limits["memory_gb"]} GiB; logs in {work}',flush=True)
    started=time.monotonic();process=None
    try:
        with (work/(name+'-container.log')).open('w') as log:
            process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT)
            update=started
            while process.poll() is None:
                if time.monotonic()-started>timeout:raise TimeoutError(name+' exceeded its configured allowance')
                if time.monotonic()-update>=30:
                    progress='running';path=work/(name+'.log')
                    if path.exists():
                        with path.open('rb') as stream:
                            stream.seek(max(0,path.stat().st_size-65536));tail=stream.read().decode(errors='replace')
                        iterations=re.findall(r'^Time = (\S+)',tail,re.MULTILINE)
                        if iterations:progress='iteration '+iterations[-1]
                    print(name+': '+progress,flush=True);update=time.monotonic()
                time.sleep(1)
            if process.returncode:raise RuntimeError(name+' failed; inspect '+str(work/(name+'-container.log'))+' and its stage logs')
    finally:
        subprocess.run(['docker','rm','-f',container],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if process is not None and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()


def report(work):
    text=(work/'solve.log').read_text()
    blocks=text.split('\nTime = ')
    final=blocks[-1];residuals={}
    for field,value in re.findall(r'Solving for (\w+), Initial residual = ([\d.eE+-]+)',final):residuals.setdefault(field,float(value))
    result={'iteration':float(final.splitlines()[0]),'solver_reported_convergence':'SIMPLE solution converged' in text,'initial_residuals':residuals}
    for name in ('inlet','farfield'):
        values=[]
        for p in (work/'case/postProcessing'/(name+'Flow')).glob('*/surfaceFieldValue*.dat'):
            values.extend(float(line.split()[1]) for line in p.read_text().splitlines() if line.strip() and not line.startswith('#'))
        if values:result[name+'_flow_m3_h']=sum(values[-10:])/len(values[-10:])*3600
    if result.get('inlet_flow_m3_h'):
        result['mass_imbalance_pct']=100*abs(result['inlet_flow_m3_h']+result['farfield_flow_m3_h'])/abs(result['inlet_flow_m3_h'])
    result['scope']='Steady isothermal airflow. Residual convergence is not mesh independence or a cooling prediction.'
    write(work/'results.json',result)
    print(json.dumps(result,indent=2),flush=True)


def render_outputs(work, limits, image, view=None):
    shutil.copy2(ROOT/'cfd/render.py',work/'render.py')
    # Older runs predate view settings. A caller can explicitly supply the
    # current view settings without rewriting that run's saved physics inputs.
    extra=['--nozzle-tip-mm',*map(str,view['nozzle_tip_mm'])] if view is not None else []
    stage(work,limits,'render',image,extra)


def products(work):
    print('\nProducts in '+str(work)+':',flush=True)
    for name,description in [
        ('results.json','flow, residuals and convergence status'),
        ('front-slice.png','front-view airspeed heatmap through the configured nozzle centre'),
        ('case/case.foam','OpenFOAM case for ParaView: mesh and saved fields'),
        ('run.json','settings, fan curve, resource limits and input STL hashes'),
        ('view.pvsm','prepared ParaView streamline view'),
        ('view.png','preview of the prepared streamline view'),
    ]:
        if (work/name).is_file():print(f'  {work/name} — {description}',flush=True)
    for path in sorted(work.glob('streamlines-*.vtp')):
        print(f'  {path} — cached streamline geometry',flush=True)
    if list(work.glob('*.log')):print(f'  {work}/*.log — stage progress and surface/mesh diagnostics',flush=True)
    print('Prepared streamlines view (requires local ParaView): '+shlex.join(['python3','scripts/run_cfd.py','--view',str(work)]),flush=True)


def open_view(work):
    """Optional local viewer; ParaView supplies its own Python/VTK packages."""
    import os
    work=work.resolve()
    if not (work/'case/case.foam').is_file():
        raise ValueError('--view expects a result directory containing case/case.foam')
    pvpython=shutil.which('pvpython')
    gui=shutil.which('paraview')
    if not pvpython:
        apps=sorted(Path('/Applications').glob('ParaView*.app'))
        if apps:
            pvpython=str(apps[-1]/'Contents/bin/pvpython')
            gui=str(apps[-1]/'Contents/MacOS/paraview')
    if not pvpython or not gui:
        raise RuntimeError('Install ParaView and put pvpython and paraview on PATH; no host Python packages are needed')
    state=work/'view.pvsm'
    helper=ROOT/'cfd/view.py'
    inputs=[helper,*[work/'case/constant/triSurface'/name for name in ('walls.stl','inlet.stl')],
            *[p/'U' for p in (work/'case').iterdir() if p.is_dir() and (p/'U').is_file()]]
    env=os.environ.copy()
    env.update(OMP_NUM_THREADS='4',VTK_SMP_MAX_THREADS='4')
    if not state.exists() or state.stat().st_mtime < max(p.stat().st_mtime for p in inputs):
        subprocess.run([pvpython,'--force-offscreen-rendering',str(helper),str(work)],env=env,check=True)
    command=[gui,'--state='+str(state)]
    if '.app/Contents/MacOS/' in gui:
        command=['open','-na',str(Path(gui).parents[2]),'--args','--state='+str(state)]
    with (work/'paraview.log').open('a') as log:
        subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    print('Opened prepared ParaView view: '+str(state))
    products(work)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build',action='store_true',help='Build the single OpenFOAM container image')
    selection=parser.add_mutually_exclusive_group()
    selection.add_argument('--duct',default=None,help='Use exports/cfd/NAME (default: large)')
    selection.add_argument('--stls',type=Path,help='Any folder containing the three named CFD boundary STLs')
    selection.add_argument('--view',type=Path,help='Open an existing result at its latest timestep, with walls and streamlines; requires local ParaView')
    selection.add_argument('--render',type=Path,help='Generate the front-slice heatmap for an existing result using Docker; does not solve again')
    parser.add_argument('--profile',default='ws9290',help='Fan profile name or JSON path')
    parser.add_argument('--settings',type=Path,default=ROOT/'cfd/settings.json')
    parser.add_argument('--resources',type=Path,default=ROOT/'config/resources.local.json')
    parser.add_argument('--iterations',type=int,help='Maximum solver iterations')
    parser.add_argument('--mesh-only',action='store_true')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.view:
        open_view(args.view);return
    if args.build:
        subprocess.run(['docker','build','-f',str(ROOT/'cfd/Dockerfile'),'-t',IMAGE,str(ROOT)],check=True);return
    if not args.resources.exists():parser.error('Copy config/resources.example.json to config/resources.local.json and set your limits')
    limits=read(args.resources);settings=read(args.settings)
    if args.iterations is not None:settings['solver']['iterations']=args.iterations
    if not isinstance(limits['cpus'],int) or isinstance(limits['cpus'],bool) or limits['cpus']<1 or not math.isfinite(limits['memory_gb']) or limits['memory_gb']<=0:parser.error('Resource limits must be positive')
    if settings['solver']['iterations']<1:parser.error('Iterations must be positive')
    source=(args.stls or ROOT/'exports/cfd'/(args.duct or 'large')).resolve()
    profile=Path(args.profile)
    if not profile.is_file():profile=ROOT/'profiles'/(args.profile+'.json')
    image=json.loads(subprocess.check_output(['docker','image','inspect',IMAGE],text=True))[0]['Id']
    if args.render:
        work=args.render.resolve()
        if not (work/'case/case.foam').is_file():parser.error('--render expects a result directory containing case/case.foam')
        saved=read(work/'run.json')
        view=saved['settings'].get('view',settings.get('view'))
        if not view:parser.error('Set view.nozzle_tip_mm in --settings for this older run')
        shutil.copy2(ROOT/'cfd/run.sh',work/'run.sh')
        try:render_outputs(work,limits,image,view)
        finally:products(work)
        return
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    work=(args.output or ROOT/'results'/(stamp+'-'+source.name+'-'+uuid.uuid4().hex[:8])).resolve()
    work.mkdir(parents=True,exist_ok=False)
    prepare(work,source,settings,read(profile),limits)
    try:
        stage(work,limits,'mesh',image)
        boundary=(work/'case/constant/polyMesh/boundary').read_text()
        patches={name:int(re.search(r'\bnFaces\s+(\d+)',body)[1]) for name,body in re.findall(r'(\w+)\s*\{([^{}]*)\}',boundary) if re.search(r'\bnFaces\s+(\d+)',body)}
        if {k for k,v in patches.items() if v}!={'walls','inlet','farfield'}:raise ValueError('Mesh leaked or lost a boundary: '+str(patches))
        if not args.mesh_only:
            stage(work,limits,'solve',image);report(work)
            render_outputs(work,limits,image)
    finally:
        products(work)


if __name__=='__main__':
    main()
