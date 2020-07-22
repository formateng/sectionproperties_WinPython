#from __future__ import print_function
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import rhino3dm as rhino
import sectionproperties.pre.sections as sections
from sectionproperties.analysis.cross_section import CrossSection
import sectionproperties.post.post as post
import uuid
import io
import base64
import sys
import json

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

def parse_polyline(polyline, start_idx):
    points = []
    for i in range(polyline.PointCount - 1):
        points.append([polyline.Point(i).X, polyline.Point(i).Y])

    facets = []
    for i in range(polyline.PointCount - 2):
        facets.append([i + start_idx, i + start_idx + 1])
    facets.append([polyline.PointCount + start_idx - 2, start_idx])

    return points, facets

def rhino_mesh_from_meshpy(mesh):
    
    rmesh = rhino.Mesh()
    for p in mesh.points:
        rmesh.Vertices.Add(p[0], p[1], 0.0)
    for e in mesh.elements:
        rmesh.Faces.AddFace(e[0], e[1], e[2])

    return rmesh

def parse_input(data):
    eprint("start parsing input data")
    perimeter_polyline = rhino.CommonObject.Decode(data['perimeter'])

    points = []
    facets = []

    # perimeter
    pts, fs = parse_polyline(perimeter_polyline, 0)
    points.extend(pts)
    facets.extend(fs)

    perimeter = [i for i in range(len(fs))]

    # holes
    holes = []
    if 'holes' in data and 'hole_points' in data:
        hole_polylines = [rhino.CommonObject.Decode(d) for d in data['holes']]
        for h in hole_polylines:
            pts, fs = parse_polyline(h, len(points))
            points.extend(pts)
            facets.extend(fs)

        for p in data['hole_points']:
            holes.append([p['X'], p['Y']])

    # control_points
    control_points = []
    for p in data['control_points']:
        control_points.append([p['X'], p['Y']])

    # loads to check
    loadcases = []
    if 'loadcases' in data:
        for load in data['loadcases']:
            loadcase = []
            loadcase.append(load['LC'])
            loadcase.append(load['N'])
            loadcase.append(load['Vx'])
            loadcase.append(load['Vy'])
            loadcase.append(load['Mxx'])
            loadcase.append(load['Myy'])
            loadcase.append(load['Mzz'])
            loadcases.append(loadcase)

    # eprint(loadcases)

    # imagesToSend - list of images we want back again

    # resultsToSend - list of result sets we want back again
    
    # custom material - also check what is the default for clarity!
    # note the following is in cm
    
    # meshsize
    mesh_size = 2.0
    if 'mesh_size' in data:
        mesh_size = data['mesh_size']

    return points, facets, holes, control_points, perimeter, mesh_size, loadcases

def process_geometry(geometry, mesh_sizes, loadcases):
    # update this to receive the geometry, mesh info, material and loads
    
    # generate a finite element mesh
    mesh = geometry.create_mesh(mesh_sizes=mesh_sizes)

    # generate material - can be overwritten if needed --all in N and cm

    # create a CrossSection object for analysis
    section = CrossSection(geometry, mesh)

    # calculate various cross-section properties
    section.calculate_geometric_properties()
    section.calculate_warping_properties()
    section.calculate_plastic_properties()

    # Area
    area = section.get_area()
    sheararea = section.get_As()
    asx = sheararea[0]
    asy = sheararea[1]
    
    # Second Moment of Area about centroid
    (ixx,iyy,ixy) = section.get_ic()

    # Centroid
    (xg,yg) = section.get_c()

    # Radii of Gyration
    (rxx,ryy) = section.get_rc()

    # Principal bending axis angle
    phi = section.get_phi()
    # St. Venant torsion constant
    ipp = section.get_j()
    # Warping Constant
    cw = section.get_gamma()

    # Elastic Section Moduli
    (welx_top,welx_bottom,wely_top,wely_bottom) = section.get_z()

    # Plastic Section Moduli
    (wplx,wply) = section.get_s()

    # plot centroid to image
    section.plot_centroids(pause=False)
    buf = io.BytesIO()
    plt.savefig(buf, format='png',bbox_inches='tight')
    buf.seek(0)
    plot_centroid = base64.b64encode(buf.getvalue()).decode()
    plt.close()

    # calculate torsion resistance from stress and torque
    #from the below can also return torsional stress if wanted
    stress_post = section.calculate_stress(Mzz=10)
    unit_mzz_zxy = []
    maxstress = []
    for group in stress_post.material_groups:
        maxstress.append(max(group.stress_result.sig_zxy_mzz))
        unit_mzz_zxy.append(group.stress_result.sig_zxy_mzz.tolist())
    #there should be only one maxstress value therefore:
    wt = 10/maxstress[0]

    #plot this image
    stress_post.plot_stress_mzz_zxy(pause=False)
    buf = io.BytesIO()
    plt.savefig(buf, format='png',bbox_inches='tight')
    buf.seek(0)
    plot_unittorsionstress = base64.b64encode(buf.getvalue()).decode()
    plt.close()

    #foreach load case submitted calculate vm stress state and create image

    vmStressImages = {}
    vmStressStates = {}
    for loadcase in loadcases:
        lc_name = loadcase[0]
        s_n = loadcase[1]
        s_vx = loadcase[2]
        s_vy = loadcase[3]
        s_mxx = loadcase[4]
        s_myy = loadcase[5]
        s_mzz = loadcase[6]
        stress_post = section.calculate_stress(N=s_n,Vx=s_vx,Vy=s_vy,Mxx=s_mxx,Myy=s_myy,Mzz=s_mzz)
        stress_state = []
        for group in stress_post.material_groups:
            stress_state.append(group.stress_result.sig_vm.tolist())
        vmStressStates['lc_'+str(lc_name)+'_vm_stress'] = stress_state
        #plot this image
        stress_post.plot_stress_vm(pause=False)
        buf = io.BytesIO()
        plt.savefig(buf, format='png',bbox_inches='tight')
        buf.seek(0)
        vmStressImages['lc_'+str(lc_name)+'_vm_stress'] = base64.b64encode(buf.getvalue()).decode()
        plt.close()

    # create rhino mesh
    rmesh = rhino_mesh_from_meshpy(mesh)

    # return send_file(path, as_attachment=True)

    # get some of the calculated section properties
    return_data = {}
    return_data['properties'] = {
        'area': area,
        'Avx': asx,
        'Avy':asy,
        'xg': xg,
        'yg': yg,
        'rxx': rxx,
        'ryy': ryy,
        'phi': phi,
        'ixx': ixx,
        'iyy': iyy,
        'ipp':ipp,
        'cw':cw,
        'welx+': welx_top,
        'welx-': welx_bottom,
        'wely+': wely_top,
        'wely-': wely_bottom,
        'wplx': wplx,
        'wply': wply,
        'wt':wt,
    }
    return_data['geometry'] = {
        'mesh': rhino.CommonObject.Encode(rmesh),
    }
    return_data['images'] = {
        'centroids': plot_centroid,
        'unittorsion_vxy_stress': plot_unittorsionstress,
    }
    return_data['images'].update(vmStressImages)
    return_data['stress_results'] = {
        'unittorsion_vxy_stress': unit_mzz_zxy,
    }
    return_data['stress_results'].update(vmStressStates)

    return return_data

#main part of the file starts here:
filename = sys.argv[1]
eprint("start working on the file:"+ filename) 

with open(filename) as f:
    lines = f.readlines()
    data = eval(lines[0].strip())

points, facets, holes, control_points, perimeter, mesh_size, loadcases = parse_input(data)
mesh_sizes = [mesh_size]

geometry = sections.CustomSection(points, facets, holes, control_points)
geometry.clean_geometry()

data = process_geometry(geometry,mesh_sizes,loadcases)

json_result = json.dumps(data)
print(json_result)
eprint('done')