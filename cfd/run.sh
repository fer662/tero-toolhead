#!/bin/bash
source /usr/lib/openfoam/openfoam2412/etc/bashrc
set -eo pipefail
cd /work/case
cpus="$2"
if [ "$1" = mesh ]; then
    surfaceCheck constant/triSurface/boundary.stl > /work/surface-check.log 2>&1
    # Surface warnings are retained; the generated fluid mesh is checked below.
    surfaceFeatureExtract > /work/features.log 2>&1
    blockMesh > /work/block-mesh.log 2>&1
    if [ "$cpus" -gt 1 ]; then
        decomposePar -force -time none > /work/mesh-decompose.log 2>&1
        mpirun --allow-run-as-root --oversubscribe -np "$cpus" snappyHexMesh -parallel -overwrite > /work/mesh.log 2>&1
        reconstructParMesh -constant -mergeTol 1e-8 > /work/mesh-reconstruct.log 2>&1
    else
        snappyHexMesh -overwrite > /work/mesh.log 2>&1
    fi
    checkMesh > /work/check-mesh.log 2>&1
    grep -q 'Number of regions: 1 (OK)' /work/check-mesh.log
    grep -q 'Cell volumes OK' /work/check-mesh.log
    grep -q 'Boundary definition OK' /work/check-mesh.log
elif [ "$1" = solve ]; then
    if [ "$cpus" -gt 1 ]; then
        decomposePar -force > /work/decompose.log 2>&1
        mpirun --allow-run-as-root --oversubscribe -np "$cpus" simpleFoam -parallel > /work/solve.log 2>&1
        reconstructPar -latestTime > /work/reconstruct.log 2>&1
    else
        simpleFoam > /work/solve.log 2>&1
    fi
elif [ "$1" = render ]; then
    MPLBACKEND=Agg python3 /work/render.py /work "${@:3}" > /work/render.log 2>&1
else
    echo "Unknown stage: $1" >&2
    exit 1
fi
