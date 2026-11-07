#!/usr/bin/env python3
"""Export manufacturing and CFD STLs from the repository's cad/toolhead.f3d."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def recipe():
    paths = [ROOT/'scripts/export_stls.py', *(ROOT/'scripts/fusion/TeroExport').glob('*.py')]
    return {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted(paths)}


def check(staged=False):
    if staged:
        import subprocess
        contents = lambda name: subprocess.check_output(['git','show',':'+name],cwd=ROOT)
    else:
        contents = lambda name: (ROOT/name).read_bytes()
    manifest = json.loads(contents('exports/manifest.json'))
    for name, expected in {'cad/toolhead.f3d': manifest['source_sha256'], **manifest['scripts_sha256'], **manifest['stls_sha256']}.items():
        data = contents(name)
        # Git stores an LFS pointer; its oid is the SHA-256 of the native archive.
        pointer = re.fullmatch(rb'version https://git-lfs.github.com/spec/v1\noid sha256:([0-9a-f]{64})\nsize ([1-9][0-9]*)\n',data) if staged and name == 'cad/toolhead.f3d' else None
        actual = pointer[1].decode('ascii') if pointer else hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise ValueError('STLs are out of sync: '+name+'; run scripts/export_stls.py')
    if not staged and manifest['scripts_sha256'] != recipe():
        raise ValueError('Export scripts changed; run scripts/export_stls.py')
    print('Fusion file, export scripts and STLs match.')


def finish(job):
    request = json.loads((job/'request.json').read_text())
    result = json.loads((job/'result.json').read_text())
    if not result['success']:
        raise RuntimeError(result['error'])
    if request['source_sha256'] != digest(ROOT/'cad/toolhead.f3d') or request['scripts_sha256'] != recipe():
        raise RuntimeError('Fusion file or export scripts changed during export')
    output = Path(request['output'])
    paths = list(output.rglob('*.stl'))
    required = {f'cfd/{v}/{b}.stl' for v in ('large','small') for b in ('walls','inlet','farfield')}
    required |= {f'stl/{p}.stl' for p in ('core','clips-both','duct-large','duct-small')}
    if {p.relative_to(output).as_posix() for p in paths} != required:
        raise RuntimeError('Incomplete STL export set')
    manifest = {'source_sha256': request['source_sha256'], 'scripts_sha256': request['scripts_sha256'],
                'stls_sha256': {'exports/'+p.relative_to(output).as_posix(): digest(p) for p in sorted(paths)}}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    backup = job/'previous-exports'
    if backup.exists():
        raise RuntimeError('Job already published')
    destination = ROOT/'exports'
    if destination.exists():
        os.replace(destination,backup)
    try:
        os.replace(output,destination)
        check()
    except BaseException:
        if destination.exists(): os.replace(destination,output)
        if backup.exists(): os.replace(backup,destination)
        raise
    print(f'Exported {len(paths)} synchronized STLs.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true',help='Check synchronization without Fusion')
    parser.add_argument('--staged',action='store_true',help='Check the exact Git index; use with --check')
    parser.add_argument('--request-only',action='store_true',help='Queue without waiting for Fusion')
    parser.add_argument('--finish',metavar='JOB_ID',help='Publish an already completed export')
    parser.add_argument('--pilot-diameter',type=float,default=2.4,help='Manufacturing threaded-pilot diameter in mm (default: 2.4)')
    args = parser.parse_args()
    if args.staged and not args.check: parser.error('--staged requires --check')
    if args.check:
        check(args.staged);return
    jobs = ROOT/'.build/fusion-jobs'
    jobs.mkdir(parents=True,exist_ok=True)
    if args.finish:
        finish(jobs/str(uuid.UUID(args.finish)));return
    if not 2 <= args.pilot_diameter <= 2.9: parser.error('Pilot diameter must be between 2 and 2.9 mm')
    for old in jobs.glob('*/request.json'):
        if not old.with_name('result.json').exists() and not old.with_name('cancelled').exists():
            parser.error('Unresolved Fusion export: '+old.parent.name)
    job = jobs/str(uuid.uuid4());job.mkdir()
    source = ROOT/'cad/toolhead.f3d'
    source_hash = digest(source)
    frozen = job/'toolhead.f3d';shutil.copyfile(source,frozen)
    if digest(frozen) != source_hash: raise RuntimeError('Source changed while copying')
    request = {'operation':'stl','job_id':job.name,'source':str(frozen),'source_sha256':source_hash,
               'scripts_sha256':recipe(),'output':str(job/'exports'),'pilot_diameter_mm':args.pilot_diameter}
    (job/'request.json').write_text(json.dumps(request,indent=2)+'\n')
    print('STL export queued. Waiting for TeroExport in Fusion (keep Fusion open, with no command dialog).',flush=True)
    if args.request_only:
        print('Job: '+job.name);return
    deadline = time.monotonic()+1800
    while not (job/'result.json').exists():
        if time.monotonic()>deadline:
            raise TimeoutError('Fusion export has not completed. Inspect job '+job.name+' before retrying.')
        time.sleep(1)
    finish(job)


if __name__ == '__main__':
    main()
