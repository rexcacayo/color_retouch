"""Retoque de color por caras, todo en modo Objeto.

Cada color es un material de la pieza. Pintar = cambiar el material de las caras
que toca el pincel. No se entra en modo Edición: se lanza un rayo desde el ratón,
se buscan las caras cercanas al punto tocado y se les cambia el índice de material.
"""
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

from .common import AddonError

COPY_FLAG = 'color_copia'
ORIGIN = 'taller_origen'


# --------------------------------------------------------------------------- materiales / paleta
def bsdf(mat):
    if mat is None or mat.node_tree is None:
        return None
    return next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)


def base_color(mat):
    node = bsdf(mat)
    if node is not None:
        return tuple(node.inputs['Base Color'].default_value)[:3]
    return tuple(mat.diffuse_color)[:3]


def sync_viewport(mat):
    """El color de la vista Sólido (diffuse_color) igual al del render (Base Color)."""
    c = base_color(mat)
    if tuple(round(x, 4) for x in mat.diffuse_color[:3]) != tuple(round(x, 4) for x in c):
        mat.diffuse_color = (*c, 1.0)


def new_material(name, color, roughness=0.45, metallic=0.0):
    mat = bpy.data.materials.new(name)
    if mat.node_tree is None:
        mat.use_nodes = True
    node = bsdf(mat)
    if node is not None:
        node.inputs['Base Color'].default_value = (*color, 1.0)
        node.inputs['Roughness'].default_value = roughness
        node.inputs['Metallic'].default_value = metallic
    mat.diffuse_color = (*color, 1.0)
    return mat


def add_color(obj, color, name=''):
    """Añade un color nuevo a la paleta de la pieza. Devuelve su índice."""
    used = {m.name for m in bpy.data.materials}
    base = name.strip() or 'Color'
    candidate, i = base, 2
    while candidate in used:
        candidate = f'{base}_{i}'
        i += 1
    obj.data.materials.append(new_material(candidate, color))
    return len(obj.data.materials) - 1


def ensure_palette(obj):
    """Una pieza sin materiales recibe uno gris para poder empezar."""
    if not obj.data.materials:
        obj.data.materials.append(new_material('Color_base', (0.6, 0.6, 0.6)))


# --------------------------------------------------------------------------- copia de trabajo
def is_working_copy(obj):
    return bool(obj.get(COPY_FLAG)) or bool(obj.get(ORIGIN))


def working_copy(context, obj):
    """Regla de la casa: el original no se toca. Se pinta en una copia y el original se oculta."""
    if is_working_copy(obj):
        return obj
    copy = obj.copy()
    copy.data = obj.data.copy()
    copy.name = obj.name + '_color'
    for col in obj.users_collection:
        col.objects.link(copy)
    copy[COPY_FLAG] = True
    copy[ORIGIN] = obj.name
    obj.hide_set(True)
    obj.hide_render = True
    obj['taller_original_conservado'] = True
    for o in context.selected_objects:
        o.select_set(False)
    copy.select_set(True)
    context.view_layer.objects.active = copy
    return copy


# --------------------------------------------------------------------------- geometría para pintar
class Canvas:
    """Datos de la malla para pintar rápido: árbol de rayos, árbol de centros y adyacencia."""

    def __init__(self, obj):
        if obj.type != 'MESH':
            raise AddonError('Selecciona una malla.')
        if any(m.show_viewport for m in obj.modifiers):
            raise AddonError('La pieza tiene modificadores activos: aplícalos antes de pintar.')
        me = obj.data
        if not len(me.polygons):
            raise AddonError('La malla no tiene caras.')
        self.obj = obj
        self.me = me
        mw = obj.matrix_world
        n = len(me.polygons)
        co = np.empty(len(me.vertices) * 3, np.float64)
        me.vertices.foreach_get('co', co)
        co = co.reshape(-1, 3)
        m = np.array(mw)
        self.world = co @ m[:3, :3].T + m[:3, 3]
        loop_start = np.empty(n, np.int64)
        loop_total = np.empty(n, np.int64)
        me.polygons.foreach_get('loop_start', loop_start)
        me.polygons.foreach_get('loop_total', loop_total)
        loops = np.empty(len(me.loops), np.int64)
        me.loops.foreach_get('vertex_index', loops)
        self.loop_start, self.loop_total, self.loops = loop_start, loop_total, loops
        polys = [loops[s:s + t].tolist() for s, t in zip(loop_start, loop_total)]
        self.tree = BVHTree.FromPolygons(self.world.tolist(), polys, all_triangles=False)
        center = np.empty(n * 3, np.float64)
        me.polygons.foreach_get('center', center)
        center = center.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3]
        self.center = center
        self.kd = KDTree(n)
        for i, c in enumerate(center):
            self.kd.insert(c, i)
        self.kd.balance()
        self.mat = np.empty(n, np.int32)
        me.polygons.foreach_get('material_index', self.mat)
        self._adj = None
        self.history = []      # [(caras, materiales anteriores)]

    # ---- consulta
    def ray(self, origin, direction):
        loc, _normal, index, _d = self.tree.ray_cast(origin, direction)
        return (loc, index) if loc is not None else (None, None)

    def faces_near(self, point, radius):
        return np.fromiter((i for _c, i, _d in self.kd.find_range(point, radius)), np.int64)

    # ---- escritura
    def paint(self, faces, index):
        faces = faces[self.mat[faces] != index]
        if not len(faces):
            return 0
        self.history.append((faces, self.mat[faces].copy()))
        self.mat[faces] = index
        self.flush()
        return len(faces)

    def undo(self):
        if not self.history:
            return False
        faces, old = self.history.pop()
        self.mat[faces] = old
        self.flush()
        return True

    def flush(self):
        self.me.polygons.foreach_set('material_index', self.mat)
        self.me.update()

    # ---- relleno
    def adjacency(self):
        """Vértice → caras en formato CSR (se calcula una vez)."""
        if self._adj is None:
            face_of_loop = np.repeat(np.arange(len(self.loop_start)), self.loop_total)
            order = np.argsort(self.loops, kind='stable')
            faces_by_vert = face_of_loop[order]
            counts = np.bincount(self.loops, minlength=len(self.world))
            start = np.concatenate([[0], np.cumsum(counts)])
            self._adj = (faces_by_vert, start)
        return self._adj

    def _verts_of(self, faces):
        s, t = self.loop_start[faces], self.loop_total[faces]
        idx = np.repeat(s, t) + (np.arange(t.sum()) - np.repeat(np.cumsum(t) - t, t))
        return np.unique(self.loops[idx])

    def _faces_of(self, verts):
        faces_by_vert, start = self.adjacency()
        s, e = start[verts], start[verts + 1]
        t = e - s
        idx = np.repeat(s, t) + (np.arange(t.sum()) - np.repeat(np.cumsum(t) - t, t))
        return np.unique(faces_by_vert[idx])

    def region(self, seed, angle=None, allowed=None, same_color=True):
        """Caras conectadas a `seed` (como el cubo de pintura).
        - same_color: solo caras del mismo color que la de partida.
        - angle (grados): no cruza aristas más dobladas que eso («parar en aristas»).
        - allowed: máscara de caras donde se puede crecer (p. ej. el círculo del pincel).
        Búsqueda por frentes: cada paso solo mira el borde de lo ya encontrado."""
        ok = np.ones(len(self.mat), bool) if allowed is None else allowed.copy()
        if same_color:
            ok &= self.mat == self.mat[seed]
        ok[seed] = True
        seen = np.zeros(len(self.mat), bool)
        seen[seed] = True
        frontier = np.array([seed])
        if angle is None:
            while len(frontier):
                near = self._faces_of(self._verts_of(frontier))
                near = near[ok[near] & ~seen[near]]
                seen[near] = True
                frontier = near
            return np.nonzero(seen)[0]
        nb, start, cosang = self.edge_graph()
        limit = np.cos(np.radians(angle))
        while len(frontier):
            s, e = start[frontier], start[frontier + 1]
            t = e - s
            idx = np.repeat(s, t) + (np.arange(t.sum()) - np.repeat(np.cumsum(t) - t, t))
            idx = idx[cosang[idx] >= limit]
            near = np.unique(nb[idx])
            near = near[ok[near] & ~seen[near]]
            seen[near] = True
            frontier = near
        return np.nonzero(seen)[0]

    # ---- aristas
    def normals(self):
        if getattr(self, '_normal', None) is None:
            n = np.empty(len(self.mat) * 3, np.float64)
            self.me.polygons.foreach_get('normal', n)
            m = np.array(self.obj.matrix_world.to_3x3().inverted().transposed())
            n = n.reshape(-1, 3) @ m.T
            self._normal = n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
        return self._normal

    def edge_graph(self):
        """Vecinos por arista en CSR: (vecino, inicio por cara, coseno del ángulo entre las dos caras)."""
        if getattr(self, '_edges', None) is None:
            n = len(self.loop_start)
            face_of_loop = np.repeat(np.arange(n), self.loop_total)
            nxt = np.arange(len(self.loops)) + 1
            last = self.loop_start + self.loop_total - 1
            nxt[last] = self.loop_start                       # el último lazo cierra con el primero
            a, b = self.loops, self.loops[nxt]
            key = np.minimum(a, b).astype(np.int64) * (len(self.world) + 1) + np.maximum(a, b)
            order = np.argsort(key, kind='stable')
            key_s, face_s = key[order], face_of_loop[order]
            same = key_s[1:] == key_s[:-1]                    # dos lazos con la misma arista = caras vecinas
            f1, f2 = face_s[:-1][same], face_s[1:][same]
            src = np.concatenate([f1, f2])
            dst = np.concatenate([f2, f1])
            nrm = self.normals()
            cos = np.einsum('ij,ij->i', nrm[src], nrm[dst])
            o = np.argsort(src, kind='stable')
            src, dst, cos = src[o], dst[o], cos[o]
            start = np.concatenate([[0], np.cumsum(np.bincount(src, minlength=n))])
            self._edges = (dst, start, cos)
        return self._edges
