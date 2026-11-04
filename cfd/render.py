"""Render the latest saved velocity field on the configured nozzle-centre plane."""
import argparse
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
import numpy as np
import vtk
from vtk.util.numpy_support import numpy_to_vtk, vtk_to_numpy


def render(work, nozzle_tip=None):
    run = json.loads((work/'run.json').read_text())
    if nozzle_tip is None:
        nozzle_tip = run['settings']['view']['nozzle_tip_mm']
    origin = np.asarray(nozzle_tip, dtype=float)
    if origin.shape != (3,) or not np.isfinite(origin).all():
        raise ValueError('view.nozzle_tip_mm must contain three finite CAD coordinates in millimeters')

    def surface(name):
        reader = vtk.vtkSTLReader()
        reader.SetFileName(str(work/'case/constant/triSurface'/name))
        reader.Update()
        if not reader.GetOutput().GetNumberOfCells():
            raise ValueError('Missing or empty surface: '+name)
        return reader

    walls = surface('walls.stl')
    ambient = surface('farfield.stl')
    inlet = surface('inlet.stl')
    wall_bounds = np.array(walls.GetOutput().GetBounds())*1000
    boundaries = np.array([s.GetOutput().GetBounds() for s in (walls, ambient, inlet)])*1000
    domain_bounds = np.empty(6)
    domain_bounds[::2] = boundaries[:, ::2].min(axis=0)
    domain_bounds[1::2] = boundaries[:, 1::2].max(axis=0)
    if not domain_bounds[2] < origin[1] < domain_bounds[3]:
        raise ValueError('The nozzle-centre Y slice is outside the fluid domain; check view.nozzle_tip_mm')

    reader = vtk.vtkOpenFOAMReader()
    reader.SetFileName(str(work/'case/case.foam'))
    reader.UpdateInformation()
    reader.DisableAllPatchArrays()
    reader.SetPatchArrayStatus('internalMesh', 1)
    reader.DisableAllCellArrays()
    reader.SetCellArrayStatus('U', 1)
    times = reader.GetOutputInformation(0).Get(vtk.vtkStreamingDemandDrivenPipeline.TIME_STEPS())
    if not times:
        raise ValueError('No saved timesteps were found')
    iteration = max(times)
    print(f'Rendering iteration {iteration:g}; nozzle tip {origin.tolist()} mm', flush=True)
    reader.UpdateTimeStep(iteration)
    field = reader.GetOutput().GetBlock(0)
    if field is None or not field.GetNumberOfCells() or field.GetCellData().GetArray('U') is None:
        raise ValueError('No saved fluid velocity field was found')
    # The reader also creates point U. Remove it so probing copies containing-
    # cell velocities rather than interpolating across polyhedral cells; that
    # interpolation produced overshoots in the original presentation trial.
    field.GetPointData().RemoveArray('U')

    padding = max(2, (wall_bounds[1]-wall_bounds[0])*.125)
    xmin = max(domain_bounds[0], wall_bounds[0]-padding)
    xmax = min(domain_bounds[1], wall_bounds[1]+padding)
    zmin = max(domain_bounds[4], origin[2]-.2*max(10, wall_bounds[5]-origin[2]))
    zmax = min(domain_bounds[5], wall_bounds[5])
    if xmin >= xmax or zmin >= zmax:
        raise ValueError('No front-view extent; check the geometry and nozzle-tip coordinates')
    x, z = np.meshgrid(np.linspace(xmin, xmax, 481), np.linspace(zmin, zmax, 729))
    points = vtk.vtkPoints()
    points.SetData(numpy_to_vtk(np.column_stack([x.ravel(), np.full(x.size, origin[1]), z.ravel()])/1000, deep=True))
    seeds = vtk.vtkPolyData()
    seeds.SetPoints(points)
    probe = vtk.vtkProbeFilter()
    probe.SetCellLocatorPrototype(vtk.vtkStaticCellLocator())
    probe.SetSourceData(field)
    probe.SetInputData(seeds)
    probe.Update()
    arrays = probe.GetOutput().GetPointData()
    valid = vtk_to_numpy(arrays.GetArray('vtkValidPointMask')).astype(bool).reshape(x.shape)
    velocity = vtk_to_numpy(arrays.GetArray('U'))
    speed = np.linalg.norm(velocity, axis=1).reshape(x.shape)
    if not valid.any() or not np.isfinite(speed[valid]).all():
        raise ValueError('No finite fluid velocities on the configured front slice')
    maximum = max(5, 5*math.ceil(float(speed[valid].max())/5))

    camera = vtk.vtkCamera()
    camera.SetPosition(origin[0]/1000, origin[1]/1000-1, origin[2]/1000)
    camera.SetFocalPoint(*(origin/1000))
    camera.ParallelProjectionOn()
    outline = vtk.vtkPolyDataSilhouette()
    outline.SetInputData(walls.GetOutput())
    outline.SetCamera(camera)
    outline.SetEnableFeatureAngle(0)
    outline.Update()
    plane = vtk.vtkPlane()
    plane.SetOrigin(*(origin/1000))
    plane.SetNormal(0, 1, 0)
    cut = vtk.vtkCutter()
    cut.SetCutFunction(plane)
    cut.SetInputData(walls.GetOutput())
    cut.Update()

    def segments(poly):
        lines = []
        for i in range(poly.GetNumberOfCells()):
            cell = poly.GetCell(i)
            p = np.array([poly.GetPoint(cell.GetPointId(j)) for j in range(cell.GetNumberOfPoints())])*1000-origin
            lines.append(p[:, [0, 2]])
        return lines

    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 11,
                         'axes.spines.top': False, 'axes.spines.right': False,
                         'text.color': '#172033', 'axes.labelcolor': '#334155',
                         'xtick.color': '#64748b', 'ytick.color': '#64748b'})
    fig, ax = plt.subplots(figsize=(9, 11.5))
    fig.subplots_adjust(left=.12, right=.8, bottom=.115, top=.88)
    cmap = plt.get_cmap('magma').copy()
    cmap.set_bad('#a9b2bd')
    image = ax.imshow(np.ma.masked_where(~valid, speed), origin='lower',
                      extent=[xmin-origin[0], xmax-origin[0], zmin-origin[2], zmax-origin[2]],
                      interpolation='bilinear', cmap=cmap, vmin=0, vmax=maximum)
    ax.add_collection(LineCollection(segments(outline.GetOutput()), colors='#c7d0da', linewidths=.55, alpha=.43))
    ax.add_collection(LineCollection(segments(cut.GetOutput()), colors='#f1f5f9', linewidths=.75, alpha=.8))
    ax.axhline(0, color='white', linewidth=.7, linestyle=(0, (4, 5)), alpha=.42)
    ax.set(xlim=(xmin-origin[0], xmax-origin[0]), ylim=(zmin-origin[2], zmax-origin[2]),
           xlabel='X from nozzle axis (mm)', ylabel='Height above nozzle tip (mm)')
    ax.set_aspect('equal')
    bar = fig.colorbar(image, ax=ax, fraction=.052, pad=.045, shrink=.7)
    bar.set_label('Air speed (m/s)')
    fig.text(.12, .945, 'Airflow — full toolhead, front view', fontsize=20, fontweight='bold')
    fig.text(.12, .914, f'Through nozzle centre · nozzle-relative Y = 0 mm (CAD Y = {origin[1]:g} mm)', fontsize=11, color='#475569')
    fig.text(.12, .067, 'Grey: no fluid in this slice · faint outlines: full geometry projected from all depths', fontsize=9, color='#64748b')
    result = json.loads((work/'results.json').read_text()) if (work/'results.json').exists() else {}
    status = 'solver residual target met' if result.get('solver_reported_convergence') else 'field has not converged'
    fig.text(.12, .045, f'Iteration {iteration:g} · saved cell velocities · {status}', fontsize=9, color='#64748b')
    target = work/'front-slice.png'
    fig.savefig(target, dpi=180, facecolor='white')
    plt.close(fig)
    print(f'Saved {target}; slice speed range 0–{float(speed[valid].max()):.2f} m/s', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('work', type=Path)
    parser.add_argument('--nozzle-tip-mm', type=float, nargs=3, help='View coordinates for an older run without view settings')
    args = parser.parse_args()
    render(args.work.resolve(), args.nozzle_tip_mm)
