"""Optional ParaView postprocessing: a ready-to-open view of the latest field."""
import gc
import json
import math
from pathlib import Path
import sys

import numpy as np
import vtk
from vtk.util.numpy_support import numpy_to_vtk, vtk_to_numpy

work = Path(sys.argv[1]).resolve()
reader = vtk.vtkOpenFOAMReader()
reader.SetFileName(str(work/'case/case.foam'))
reader.UpdateInformation()
reader.DisableAllPatchArrays()
reader.SetPatchArrayStatus('internalMesh', 1)
reader.DisableAllCellArrays()
reader.SetCellArrayStatus('U', 1)
key = vtk.vtkStreamingDemandDrivenPipeline.TIME_STEPS()
times = reader.GetOutputInformation(0).Get(key)
iteration = max(times)
print(f'Loading velocity field at iteration {iteration:g}', flush=True)
reader.UpdateTimeStep(iteration)
grid = reader.GetOutput().GetBlock(0)
if grid is None or grid.GetNumberOfCells() == 0:
    raise RuntimeError('No saved fluid field was found')
average = vtk.vtkCellDataToPointData()
average.SetInputData(grid)
average.SetProcessAllArrays(False)
average.AddCellDataArray('U')
average.Update()
field = average.GetOutput()
field.GetPointData().SetActiveVectors('U')

# Area-distributed seeds on the actual inlet triangles, just inside the air.
inlet = vtk.vtkSTLReader()
inlet.SetFileName(str(work/'case/constant/triSurface/inlet.stl'))
inlet.Update()
surface = inlet.GetOutput()
xyz = vtk_to_numpy(surface.GetPoints().GetData())
triangles = vtk_to_numpy(surface.GetPolys().GetConnectivityArray()).reshape(-1, 3)
tri = xyz[triangles]
cross = np.cross(tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0])
areas = np.linalg.norm(cross, axis=1)/2
normal = cross.sum(axis=0)
normal /= np.linalg.norm(normal)
count = 1000
k = np.arange(count)
selected = tri[np.searchsorted(np.cumsum(areas/areas.sum()), (k+.5)/count)]
a = np.sqrt(((k+.5)*.6180339887498949) % 1)
b = ((k+.5)*.4142135623730951) % 1
points = (1-a[:, None])*selected[:, 0]+(a*(1-b))[:, None]*selected[:, 1]+(a*b)[:, None]*selected[:, 2]
points -= min(.0002, math.sqrt(areas.sum()/math.pi)*.02)*normal
locator = vtk.vtkStaticCellLocator()
locator.SetDataSet(grid)
locator.BuildLocator()
valid = np.array([locator.FindCell(p) >= 0 for p in points])
points = points[valid]
if not len(points):
    raise RuntimeError('No inlet seeds lie inside the saved fluid mesh')
seed = vtk.vtkPolyData()
vertices = vtk.vtkPoints()
vertices.SetData(numpy_to_vtk(points, deep=True))
seed.SetPoints(vertices)
print(f'Tracing {len(points)}/{count} inlet seeds', flush=True)
trace = vtk.vtkStreamTracer()
trace.SetInputData(field)
trace.SetSourceData(seed)
trace.SetIntegratorTypeToRungeKutta45()
trace.SetIntegrationDirectionToForward()
trace.SetInitialIntegrationStep(.2)
trace.SetMinimumIntegrationStep(.01)
trace.SetMaximumIntegrationStep(.5)
bounds = grid.GetBounds()
trace.SetMaximumPropagation(3*math.sqrt(sum((bounds[2*i+1]-bounds[2*i])**2 for i in range(3))))
trace.SetMaximumNumberOfSteps(30000)
trace.SetTerminalSpeed(.02)
trace.SetComputeVorticity(False)
trace.Update()
curves = vtk.vtkPolyData()
curves.DeepCopy(trace.GetOutput())
if not curves.GetNumberOfCells():
    raise RuntimeError('No streamlines were produced')
speed = np.linalg.norm(vtk_to_numpy(curves.GetPointData().GetArray('U')), axis=1)
array = numpy_to_vtk(speed, deep=True)
array.SetName('Speed')
curves.GetPointData().AddArray(array)
path = work/f'streamlines-{iteration:g}.vtp'
writer = vtk.vtkXMLPolyDataWriter()
writer.SetFileName(str(path))
writer.SetInputData(curves)
if not writer.Write():
    raise RuntimeError('Could not save streamlines')
print(f'Saved {curves.GetNumberOfCells()} paths; creating ParaView state', flush=True)
del reader, grid, average, field, locator, trace, curves
gc.collect()

from paraview.simple import (
    GetActiveViewOrCreate, STLReader, XMLPolyDataReader, Show, ColorBy,
    GetColorTransferFunction, GetScalarBar, Text, Render, SaveState, SaveScreenshot,
    SetActiveSource, GetAnimationScene,
)
view = GetActiveViewOrCreate('RenderView')
view.ViewSize = [1400, 1000]
view.UseColorPaletteForBackground = 0
view.Background = [.97, .98, .99]
view.OrientationAxesVisibility = 1
walls = STLReader(registrationName='Toolhead walls', FileNames=[str(work/'case/constant/triSurface/walls.stl')])
walls.UpdatePipeline()
solid = Show(walls, view)
solid.SetScalarColoring(None, 0)
solid.DiffuseColor = [.35, .40, .46]
solid.Opacity = .20
lines = XMLPolyDataReader(registrationName=f'Airflow at iteration {iteration:g}', FileName=[str(path)])
lines.UpdatePipeline()
display = Show(lines, view)
display.LineWidth = 1.2
ColorBy(display, ('POINTS', 'Speed'))
lut = GetColorTransferFunction('Speed')
lut.ApplyPreset('Turbo', True)
lut.RescaleTransferFunction(0, max(10, 5*math.ceil(float(speed.max())/5)))
display.SetScalarBarVisibility(view, True)
bar = GetScalarBar(lut, view)
bar.Title = 'Speed (m/s)'
bar.ComponentTitle = ''
bar.TitleColor = [.12, .15, .18]
bar.LabelColor = [.12, .15, .18]
label = Text(registrationName='Result')
result = json.loads((work/'results.json').read_text()) if (work/'results.json').exists() else {}
flow = abs(result.get('inlet_flow_m3_h', 0))
status = 'residual target met' if result.get('solver_reported_convergence') else 'not converged'
label.Text = f'Iteration {iteration:g} | {flow:.2f} m3/h | {status}\n{len(points)} inlet seeds; all streamline speeds shown'
text = Show(label, view)
text.WindowLocation = 'Upper Left Corner'
text.Color = [.12, .15, .18]
text.FontSize = 14
view.ResetCamera()
center = np.asarray(view.CameraFocalPoint)
distance = np.linalg.norm(np.asarray(view.CameraPosition)-center)
direction = np.array([1., -2., .8])
direction /= np.linalg.norm(direction)
view.CameraPosition = (center+distance*direction).tolist()
view.CameraViewUp = [0, 0, 1]
view.CameraParallelProjection = 1
scene = GetAnimationScene()
scene.StartTime = iteration
scene.EndTime = iteration
scene.AnimationTime = iteration
view.ViewTime = iteration
SetActiveSource(lines)
Render(view)
SaveState(str(work/'view.pvsm'))
SaveScreenshot(str(work/'view.png'), view, ImageResolution=[1400,1000])
print('Prepared '+str(work/'view.pvsm'), flush=True)
