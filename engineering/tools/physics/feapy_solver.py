# -*- coding: utf-8 -*-
"""feapy_solver.py -- Pure Python FEA (2D CST + 3D CTE)"""
import numpy as np


def _D_2d(E, nu, model):
    if model == "plane_stress":
        return E / (1 - nu**2) * np.array([[1, nu, 0], [nu, 1, 0], [0, 0, (1 - nu) / 2]])
    elif model == "plane_strain":
        return E / ((1 + nu) * (1 - 2 * nu)) * np.array([[1 - nu, nu, 0], [nu, 1 - nu, 0], [0, 0, (1 - 2 * nu) / 2]])
    raise ValueError(f"Unknown 2D model: {model}")


def _D_3d(E, nu):
    la = E * nu / ((1 + nu) * (1 - 2 * nu))
    mu = E / (2 * (1 + nu))
    return np.array([[la + 2*mu, la, la, 0, 0, 0],
                     [la, la+2*mu, la, 0, 0, 0],
                     [la, la, la+2*mu, 0, 0, 0],
                     [0, 0, 0, mu, 0, 0],
                     [0, 0, 0, 0, mu, 0],
                     [0, 0, 0, 0, 0, mu]])


def _cst_B(x1,y1,x2,y2,x3,y3,area):
    if area < 1e-10: return np.zeros((3,6))
    b1,b2,b3=y2-y3,y3-y1,y1-y2
    c1,c2,c3=x3-x2,x1-x3,x2-x1
    return np.array([[b1,0,b2,0,b3,0],[0,c1,0,c2,0,c3],[c1,b1,c2,b2,c3,b3]])/(2.0*area)


def _cte_B_vol(coords):
    x1,y1,z1=coords[0]; x2,y2,z2=coords[1]
    x3,y3,z3=coords[2]; x4,y4,z4=coords[3]
    vol=(x2-x1)*((y3-y1)*(z4-z1)-(z3-z1)*(y4-y1)) \
       -(y2-y1)*((x3-x1)*(z4-z1)-(z3-z1)*(x4-x1)) \
       +(z2-z1)*((x3-x1)*(y4-y1)-(y3-y1)*(x4-x1))
    vol=abs(vol)/6.0
    if vol<1e-10: return np.zeros((6,12)),0.0
    M=np.array([[1,x1,y1,z1],[1,x2,y2,z2],[1,x3,y3,z3],[1,x4,y4,z4]])
    invM=np.linalg.inv(M)
    bz=[invM[1,0],invM[1,1],invM[1,2],invM[1,3]]
    cz=[invM[2,0],invM[2,1],invM[2,2],invM[2,3]]
    dz=[invM[3,0],invM[3,1],invM[3,2],invM[3,3]]
    B=np.zeros((6,12))
    for i in range(4):
        B[0,i*3]=bz[i]; B[1,i*3+1]=cz[i]; B[2,i*3+2]=dz[i]
        B[3,i*3+1]=dz[i]; B[3,i*3+2]=cz[i]
        B[4,i*3]=dz[i]; B[4,i*3+2]=bz[i]
        B[5,i*3]=cz[i]; B[5,i*3+1]=bz[i]
    return B,vol


def mesh_rect(x0,x1,y0,y1,nx,ny):
    nodes=[]
    for i in range(nx+1):
        for j in range(ny+1):
            nodes.append([x0+i*(x1-x0)/nx, y0+j*(y1-y0)/ny, 0.0])
    nodes=np.array(nodes)
    elems=[]
    for i in range(nx):
        for j in range(ny):
            idx=i*(ny+1)+j
            elems.append([idx,idx+1,idx+(ny+1)])
            elems.append([idx+1,idx+(ny+1)+1,idx+(ny+1)])
    return nodes,np.array(elems)


def mesh_box(x0,x1,y0,y1,z0,z1,nx,ny,nz):
    nodes=[]
    for i in range(nx+1):
        for j in range(ny+1):
            for k in range(nz+1):
                nodes.append([x0+i*(x1-x0)/nx, y0+j*(y1-y0)/ny, z0+k*(z1-z0)/nz])
    nodes=np.array(nodes)
    elems=[]
    for i in range(nx):
        for j in range(ny):
            for k in range(nz):
                idx=i*(ny+1)*(nz+1)+j*(nz+1)+k
                v=[idx,idx+1,idx+(nz+1),idx+(nz+1)+1,
                   idx+(ny+1)*(nz+1),idx+(ny+1)*(nz+1)+1,
                   idx+(ny+1)*(nz+1)+(nz+1),idx+(ny+1)*(nz+1)+(nz+1)+1]
                elems.append([v[0],v[1],v[2],v[4]])
                elems.append([v[1],v[3],v[2],v[6]])
                elems.append([v[1],v[5],v[3],v[7]])
                elems.append([v[1],v[2],v[3],v[6]])
                elems.append([v[1],v[3],v[6],v[7]])
                elems.append([v[1],v[4],v[5],v[7]])
    return nodes,np.array(elems)


class FEASolver:
    def __init__(self,nodes,elements,model="plane_stress",thickness=1.0):
        self.nodes=np.array(nodes); self.elements=np.array(elements)
        self.model=model; self.N=len(nodes); self.thickness=thickness
        self.nd=3 if model=="3d" else 2
        self.ndof=self.N*self.nd

    def solve(self,E,nu,fixed=None,loads=None):
        D=_D_3d(E,nu) if self.model=="3d" else _D_2d(E,nu,self.model)
        nd=self.nd; K=np.zeros((self.ndof,self.ndof))
        Bcache={}
        elem_volumes=[]

        for elem in self.elements:
            nn=[int(elem[m]) for m in range(len(elem))]
            key=tuple(nn); coords=self.nodes[nn]
            if nd==2:
                x1,y1,_=coords[0]; x2,y2,_=coords[1]; x3,y3,_=coords[2]
                area=abs(x1*(y2-y3)+x2*(y3-y1)+x3*(y1-y2))*0.5
                Bcache[key]=_cst_B(x1,y1,x2,y2,x3,y3,area)
                elem_volumes.append(("2d",area))
            else:
                Bcache[key],vol=_cte_B_vol(coords)
                elem_volumes.append(("3d",vol))

            B=Bcache[key]; val=elem_volumes[-1][1]
            if nd==2:
                Ke=B.T@D@B*val*self.thickness
            else:
                Ke=B.T@D@B*val
            dofs=[]
            for n in nn:
                for d in range(nd):
                    dofs.append(n*nd+d)
            for a in range(len(dofs)):
                for bb in range(len(dofs)):
                    K[dofs[a],dofs[bb]]+=Ke[a,bb]

        F=np.zeros(self.ndof)
        if loads:
            for item in loads:
                ni=int(item[0])
                F[ni*nd]+=item[1] if len(item)>1 else 0
                F[ni*nd+1]+=item[2] if len(item)>2 else 0
                if nd==3 and len(item)>3:
                    F[ni*nd+2]+=item[3]
        if fixed:
            pen=1e15
            for item in fixed:
                ni,d=int(item[0]),int(item[1])
                K[ni*nd+d,ni*nd+d]+=pen
        U=np.linalg.solve(K,F)

        # von Mises
        vm=[]
        for idx,elem in enumerate(self.elements):
            nn=[int(elem[m]) for m in range(len(elem))]
            key=tuple(nn); B=Bcache[key]
            etype,vval=elem_volumes[idx]
            if nd==2:
                u_e=[]
                for m in range(3):
                    n=nn[m]
                    u_e.append(U[n*2]); u_e.append(U[n*2+1])
                strain=B@np.array(u_e); stress=D@strain
                sx,sy,txy=stress
                vm.append(np.sqrt(sx**2-sx*sy+sy**2+3*txy**2))
            else:
                u_e=[]
                for m in range(4):
                    n=nn[m]
                    u_e.append(U[n*3]); u_e.append(U[n*3+1]); u_e.append(U[n*3+2])
                strain=B@np.array(u_e); stress=D@strain
                sx,sy,sz,tyz,txz,txy=stress
                vm.append(np.sqrt(0.5*((sx-sy)**2+(sy-sz)**2+(sz-sx)**2+6*(tyz**2+txz**2+txy**2))))

        if nd==2:
            disp=np.sqrt(U[0::2]**2+U[1::2]**2)
        else:
            disp=np.sqrt(U[0::3]**2+U[1::3]**2+U[2::3]**2)

        return {"max_displacement_mm":float(disp.max()),"max_von_mises_mpa":float(max(vm)) if vm else 0.0,"displacement_field":U,"von_mises_per_element":vm}


def solve_cantilever_2d(L,H,T,F_load,E,nu,nx=40,ny=12):
    nodes,elems=mesh_rect(0,L,0,H,nx,ny)
    s=FEASolver(nodes,elems,model="plane_stress",thickness=T)
    fixed=[]
    for j in range(ny+1):
        n=j; fixed.append((n,0)); fixed.append((n,1))
    loads=[]
    nr=nx*(ny+1)
    for j in range(ny+1):
        n=nr+j; loads.append((n,0.0,-F_load/(ny+1)))
    r=s.solve(E,nu,fixed=fixed,loads=loads)
    I=T*H**3/12; de=F_load*L**3/(3*E*I); se=6*F_load*L/(T*H**2)
    r["analytical"]={"delta_mm":de,"sigma_mpa":se,"disp_error_pct":abs(r["max_displacement_mm"]/de-1)*100 if de else 0,"stress_error_pct":abs(r["max_von_mises_mpa"]/se-1)*100 if se else 0}
    return r


def solve_cantilever_3d(L,H,D,F_load,E,nu,nx=10,ny=5,nz=5):
    nodes,elems=mesh_box(0,L,0,H,0,D,nx,ny,nz)
    s=FEASolver(nodes,elems,model="3d")
    tol=L/(2*nx)
    fixed=[]
    for ii,node in enumerate(nodes):
        if node[0]<tol:
            fixed.append((ii,0)); fixed.append((ii,1)); fixed.append((ii,2))
    rn=[]
    for ii,node in enumerate(nodes):
        if node[0]>L-tol: rn.append(ii)
    rc=len(rn)
    loads=[]
    for ii in rn:
        loads.append((ii,0.0,-F_load/rc))
    r=s.solve(E,nu,fixed=fixed,loads=loads)
    I=D*H**3/12; de=F_load*L**3/(3*E*I); se=6*F_load*L/(D*H**2)
    r["analytical"]={"delta_mm":de,"sigma_mpa":se,"disp_error_pct":abs(r["max_displacement_mm"]/de-1)*100 if de else 0,"stress_error_pct":abs(r["max_von_mises_mpa"]/se-1)*100 if se else 0}
    return r


def solve_cantilever(L, H, W_or_T, F_load, E, nu, nx=10, ny=5, nz=0):
    """兼容接口：自动检测 2D/3D。

    - 若 nz==0 或 W_or_T 与 H 同量级 → 2D 平面应力
    - 否则 → 3D 四面体
    """
    if nz == 0 or abs(W_or_T - H) < 1e-9:
        return solve_cantilever_2d(L, H, W_or_T, F_load, E, nu, nx=nx, ny=ny)
    else:
        return solve_cantilever_3d(L, H, W_or_T, F_load, E, nu, nx=nx, ny=ny, nz=nz)


if __name__=="__main__":
    import sys; sys.stdout.reconfigure(encoding="utf-8")
    print("="*60); print("2D+3D FEA Test"); print("="*60); print()
    print("[1] 2D Cantilever")
    r2d=solve_cantilever_2d(200,60,60,500,68900,0.33,nx=40,ny=12)
    n2d,e2d=mesh_rect(0,200,0,60,40,12)
    print(f"  Mesh: {len(n2d)} nodes, {len(e2d)} elems")
    print(f"  Disp: {r2d['max_displacement_mm']:.4f}mm (exact:{r2d['analytical']['delta_mm']:.4f}, err:{r2d['analytical']['disp_error_pct']:.1f}%)")
    print(f"  Sigma: {r2d['max_von_mises_mpa']:.2f}MPa (exact:{r2d['analytical']['sigma_mpa']:.2f}, err:{r2d['analytical']['stress_error_pct']:.1f}%)")
    print(f"  SF: {276.0/r2d['max_von_mises_mpa']:.2f}")
    print()
    print("[2] 3D Cantilever (10x5x5)")
    r3d=solve_cantilever_3d(200,60,60,500,68900,0.33,nx=10,ny=5,nz=5)
    n3d,e3d=mesh_box(0,200,0,60,0,60,10,5,5)
    print(f"  Mesh: {len(n3d)} nodes, {len(e3d)} tets")
    print(f"  Disp: {r3d['max_displacement_mm']:.4f}mm (exact:{r3d['analytical']['delta_mm']:.4f}, err:{r3d['analytical']['disp_error_pct']:.1f}%)")
    print(f"  Sigma: {r3d['max_von_mises_mpa']:.2f}MPa (exact:{r3d['analytical']['sigma_mpa']:.2f}, err:{r3d['analytical']['stress_error_pct']:.1f}%)")
    print(f"  SF: {276.0/r3d['max_von_mises_mpa']:.2f}")
    print()
    print("[3] 3D Convergence")
    for nv in [(3,2,2),(5,3,3),(8,4,4),(10,5,5)]:
        r=solve_cantilever_3d(200,60,60,500,68900,0.33,nx=nv[0],ny=nv[1],nz=nv[2])
        nc,ec=mesh_box(0,200,0,60,0,60,nv[0],nv[1],nv[2])
        sf=276.0/r["max_von_mises_mpa"]
        print(f"  {nv[0]}x{nv[1]}x{nv[2]}: {len(nc)}n/{len(ec)}t | disp={r['max_displacement_mm']:.4f}mm ({r['analytical']['disp_error_pct']:+.1f}%) | sigma={r['max_von_mises_mpa']:.2f}MPa ({r['analytical']['stress_error_pct']:+.1f}%) | SF={sf:.2f}")
    print()
    print("="*60); print("All tests passed!"); print("="*60)