bl_info = {'name': 'Color Retouch', 'author': 'Ricardo Lugaresi', 'version': (1, 1, 0),
           'blender': (4, 2, 0), 'location': 'Vista 3D > N > Color Retouch',
           'description': 'Retoca los colores de una pieza con pincel, cubo y cuentagotas, en modo Objeto',
           'category': 'Paint'}

import bpy
import gpu
import numpy as np
from bpy.app.handlers import persistent
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from . import common, paint

TOOL_ITEMS = [('BRUSH', 'Pincel', 'Pinta las caras que tocas al arrastrar'),
              ('FILL', 'Cubo', 'Un clic pinta toda la zona conectada del mismo color'),
              ('PICK', 'Cuentagotas', 'Un clic coge el color de la pieza')]


class ColorRetouchProps(bpy.types.PropertyGroup):
    tool: bpy.props.EnumProperty(name='Herramienta', items=TOOL_ITEMS, default='BRUSH')
    size: bpy.props.IntProperty(name='Tamaño del pincel', default=25, min=3, max=300, subtype='PIXEL')
    through: bpy.props.BoolProperty(name='Atravesar', default=True,
                                    description='Pinta también la cara de atrás de tubos y piezas finas')
    edges: bpy.props.BoolProperty(name='Parar en aristas', default=False,
                                  description='El pincel no pasa de una arista marcada (esquinas, bordes de paneles)')
    fill_until: bpy.props.EnumProperty(name='Rellenar hasta', default='COLOR', items=[
        ('COLOR', 'Cambio de color', 'Rellena la zona del mismo color'),
        ('EDGE', 'Arista', 'Rellena hasta los bordes marcados, sea del color que sea')])
    angle: bpy.props.FloatProperty(name='Ángulo de arista', default=30.0, min=1.0, max=90.0, precision=0,
                                   subtype='NONE', description='Cuánto tiene que doblarse la superficie para contar como arista')
    new_color: bpy.props.FloatVectorProperty(name='Color nuevo', subtype='COLOR', size=3, min=0, max=1,
                                             default=(0.8, 0.13, 0.012))
    new_name: bpy.props.StringProperty(name='Nombre', default='')
    done: bpy.props.StringProperty()


_canvas = {'key': None, 'canvas': None}


def get_canvas(obj):
    key = (obj.name, obj.data.name, len(obj.data.polygons), tuple(round(x, 6) for row in obj.matrix_world for x in row))
    if _canvas['key'] != key:
        _canvas['canvas'] = paint.Canvas(obj)
        _canvas['key'] = key
    else:
        c = _canvas['canvas']
        obj.data.polygons.foreach_get('material_index', c.mat)   # por si se cambió por otro lado
    return _canvas['canvas']


def target(context):
    obj = context.active_object
    if obj is None or obj.type != 'MESH' or common.is_helper(obj):
        return None
    return obj


# ---------------------------------------------------------------- dibujo del pincel
_shader = None


def _circle(x, y, r, segments=48):
    import math
    return [(x + r * math.cos(2 * math.pi * i / segments), y + r * math.sin(2 * math.pi * i / segments)) for i in range(segments)]


def _draw_brush(op, context):
    if op.mouse is None:
        return
    global _shader
    if _shader is None:
        _shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    props = context.scene.color_retouch_props
    x, y = op.mouse
    r = props.size if props.tool == 'BRUSH' else 8
    obj = op.obj
    idx = obj.active_material_index
    mat = obj.material_slots[idx].material if idx < len(obj.material_slots) else None
    col = paint.base_color(mat) if mat else (1, 1, 1)
    gpu.state.blend_set('ALPHA')
    gpu.state.line_width_set(2.0)
    for rr, c in ((r, (*col, 1.0)), (r + 2, (1, 1, 1, .8))):
        pts = _circle(x, y, rr)
        batch = batch_for_shader(_shader, 'LINE_LOOP', {'pos': pts})
        _shader.bind()
        _shader.uniform_float('color', c)
        batch.draw(_shader)
    gpu.state.line_width_set(1.0)
    gpu.state.blend_set('NONE')


# ---------------------------------------------------------------- operadores
class PAINT_OT_color_retouch(bpy.types.Operator):
    """Retoca la pintura con el ratón: pincel, cubo o cuentagotas"""
    bl_idname = 'paint.color_retouch'
    bl_label = 'Retocar'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return common.poll_object_mode(cls, context) and target(context) is not None

    def invoke(self, context, event):
        if context.area is None or context.area.type != 'VIEW_3D':
            self.report({'ERROR'}, 'Usa este botón desde la Vista 3D.')
            return {'CANCELLED'}
        obj = paint.working_copy(context, target(context))
        paint.ensure_palette(obj)
        context.window.cursor_set('WAIT')
        try:
            self.canvas = get_canvas(obj)
        except common.AddonError as exc:
            context.window.cursor_set('DEFAULT')
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        context.window.cursor_set('PAINT_BRUSH')
        self.obj = obj
        self.canvas.history.clear()
        self.mouse = None
        self.painting = False
        self.orbit = False
        self.changed = 0
        self.last = None
        sp = context.area.spaces.active
        sp.shading.type = 'SOLID'
        sp.shading.color_type = 'MATERIAL'
        self.handle = bpy.types.SpaceView3D.draw_handler_add(_draw_brush, (self, context), 'WINDOW', 'POST_PIXEL')
        context.window_manager.modal_handler_add(self)
        self._header(context)
        return {'RUNNING_MODAL'}

    def _header(self, context):
        p = context.scene.color_retouch_props
        tool = dict((k, n) for k, n, _d in TOOL_ITEMS)[p.tool]
        context.area.header_text_set(
            f'{tool} · clic izquierdo: pintar · Shift+clic: línea recta · E parar en aristas · B pincel · F cubo · I cuentagotas · 1-9 color · '
            f'[ ] tamaño · Ctrl+Z deshacer · botón central: girar · Esc o clic derecho: terminar')

    def _ray(self, context, x, y):
        from bpy_extras import view3d_utils
        region, rv3d = context.region, context.region_data
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, (x, y))
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, (x, y))
        loc, index = self.canvas.ray(origin, direction)
        return loc, index, direction

    def _world_radius(self, context, x, y, loc, px):
        from bpy_extras import view3d_utils
        region, rv3d = context.region, context.region_data
        a = view3d_utils.region_2d_to_location_3d(region, rv3d, (x, y), loc)
        b = view3d_utils.region_2d_to_location_3d(region, rv3d, (x + px, y), loc)
        return max((b - a).length, 1e-9)

    def _apply(self, context, x, y):
        p = context.scene.color_retouch_props
        loc, index, direction = self._ray(context, x, y)
        if loc is None:
            return
        color = self.obj.active_material_index
        if p.tool == 'PICK':
            self.obj.active_material_index = int(self.canvas.mat[index])
            p.tool = 'BRUSH'
            self._header(context)
            return
        if p.tool == 'FILL':
            if p.fill_until == 'EDGE':
                faces = self.canvas.region(index, angle=p.angle, same_color=False)
            else:
                faces = self.canvas.region(index)
        else:
            faces = self.canvas.faces_near(loc, self._world_radius(context, x, y, loc, p.size))
            if not p.through and len(faces):
                nrm = self.canvas.normals()[faces]
                faces = faces[nrm @ np.array(direction) < 0.2]
            faces = np.union1d(faces, [index]).astype(np.int64)
            if p.edges:
                allowed = np.zeros(len(self.canvas.mat), bool)
                allowed[faces] = True
                faces = self.canvas.region(index, angle=p.angle, allowed=allowed, same_color=False)
        self.last = (x, y)
        self.changed += self.canvas.paint(faces, color)

    def _line(self, context, x, y):
        """Shift + clic: línea recta de pincel desde el último punto pintado."""
        import math
        x0, y0 = self.last
        p = context.scene.color_retouch_props
        steps = max(1, int(math.hypot(x - x0, y - y0) / max(1.0, p.size * 0.5)))
        h = self.canvas.history
        n0 = len(h)
        for i in range(1, steps + 1):
            t = i / steps
            self._apply(context, x0 + (x - x0) * t, y0 + (y - y0) * t)
        if len(h) - n0 > 1:            # toda la línea se deshace de una vez
            parts = h[n0:]
            del h[n0:]
            h.append((np.concatenate([f for f, _o in parts]), np.concatenate([o for _f, o in parts])))

    def _finish(self, context, cancelled=False):
        bpy.types.SpaceView3D.draw_handler_remove(self.handle, 'WINDOW')
        context.area.header_text_set(None)
        context.window.cursor_set('DEFAULT')
        context.area.tag_redraw()
        p = context.scene.color_retouch_props
        p.done = f'Retoque terminado: {self.changed} caras cambiadas' if self.changed else 'Sin cambios'
        self.report({'INFO'}, p.done)
        return {'FINISHED'}

    def modal(self, context, event):
        p = context.scene.color_retouch_props
        inside = context.region is not None and 0 <= event.mouse_region_x < context.region.width \
            and 0 <= event.mouse_region_y < context.region.height
        if event.type == 'MIDDLEMOUSE':
            self.orbit = event.value == 'PRESS'
            return {'PASS_THROUGH'}
        if self.orbit and event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            return {'PASS_THROUGH'}
        if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            if event.shift:
                p.size = int(p.size * (1.15 if event.type == 'WHEELUPMOUSE' else 1 / 1.15))
                context.area.tag_redraw()
                return {'RUNNING_MODAL'}
            return {'PASS_THROUGH'}
        if event.type in {'TRACKPADPAN', 'TRACKPADZOOM', 'NDOF_MOTION'} or \
                (event.type.startswith('NUMPAD') and event.value == 'PRESS'):
            return {'PASS_THROUGH'}
        if event.type in {'ESC', 'RIGHTMOUSE', 'RET'} and event.value == 'PRESS':
            return self._finish(context)
        if event.value == 'PRESS':
            if event.type == 'Z' and event.ctrl:
                if self.canvas.undo():
                    self.changed = max(0, self.changed - 1)
                return {'RUNNING_MODAL'}
            if event.type == 'E':
                p.edges = not p.edges
                if p.tool == 'FILL':
                    p.fill_until = 'COLOR' if p.fill_until == 'EDGE' else 'EDGE'
                self.report({'INFO'}, 'Parar en aristas: ' + ('sí' if p.edges else 'no'))
                return {'RUNNING_MODAL'}
            keys = {'B': 'BRUSH', 'F': 'FILL', 'I': 'PICK'}
            if event.type in keys:
                p.tool = keys[event.type]
                self._header(context)
                return {'RUNNING_MODAL'}
            if event.type in {'LEFT_BRACKET', 'RIGHT_BRACKET'}:
                p.size = int(p.size * (1.2 if event.type == 'RIGHT_BRACKET' else 1 / 1.2))
                context.area.tag_redraw()
                return {'RUNNING_MODAL'}
            numbers = ['ONE', 'TWO', 'THREE', 'FOUR', 'FIVE', 'SIX', 'SEVEN', 'EIGHT', 'NINE']
            if event.type in numbers:
                i = numbers.index(event.type)
                if i < len(self.obj.material_slots):
                    self.obj.active_material_index = i
                    context.area.tag_redraw()
                return {'RUNNING_MODAL'}
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            self.mouse = (event.mouse_region_x, event.mouse_region_y) if inside else None
            if self.painting and inside and p.tool == 'BRUSH':
                self._apply(context, *self.mouse)
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE':
            if not inside:
                return {'PASS_THROUGH'}         # clic en el panel: botones de la paleta
            if event.value == 'PRESS':
                self.painting = p.tool == 'BRUSH'
                if event.shift and p.tool == 'BRUSH' and self.last is not None:
                    self._line(context, event.mouse_region_x, event.mouse_region_y)
                else:
                    self._apply(context, event.mouse_region_x, event.mouse_region_y)
            elif event.value == 'RELEASE':
                self.painting = False
            return {'RUNNING_MODAL'}
        return {'RUNNING_MODAL'}


class PAINT_OT_color_retouch_use(bpy.types.Operator):
    """Pintar con este color"""
    bl_idname = 'paint.color_retouch_use'
    bl_label = 'Usar color'
    index: bpy.props.IntProperty()

    def execute(self, context):
        obj = target(context)
        if obj is not None and self.index < len(obj.material_slots):
            obj.active_material_index = self.index
        return {'FINISHED'}


class PAINT_OT_color_retouch_add(bpy.types.Operator):
    """Añade el color nuevo a la paleta de la pieza"""
    bl_idname = 'paint.color_retouch_add'
    bl_label = 'Añadir color'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return common.poll_object_mode(cls, context) and target(context) is not None

    def execute(self, context):
        p = context.scene.color_retouch_props
        obj = paint.working_copy(context, target(context))
        obj.active_material_index = paint.add_color(obj, tuple(p.new_color), p.new_name)
        p.new_name = ''
        return {'FINISHED'}


class PAINT_OT_color_retouch_save(bpy.types.Operator):
    """Guarda el archivo"""
    bl_idname = 'paint.color_retouch_save'
    bl_label = 'Guardar'

    def execute(self, context):
        if not bpy.data.filepath:
            return bpy.ops.wm.save_as_mainfile('INVOKE_DEFAULT')
        bpy.ops.wm.save_mainfile()
        return {'FINISHED'}


# ---------------------------------------------------------------- panel
class VIEW3D_PT_color_retouch(bpy.types.Panel):
    bl_label = 'Color Retouch'
    bl_idname = 'VIEW3D_PT_color_retouch'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Color Retouch'

    def draw(self, context):
        layout = self.layout
        p = context.scene.color_retouch_props
        if common.draw_mode_warning(layout, context):
            return
        obj = target(context)
        if obj is None:
            r = layout.row(); r.alert = True
            r.label(text='Selecciona la pieza', icon='ERROR')
            return

        box = layout.box()
        box.label(text='1 · Colores de la pieza', icon='COLOR')
        if not obj.material_slots:
            box.label(text='Sin colores: se crea uno al empezar', icon='INFO')
        for i, slot in enumerate(obj.material_slots):
            mat = slot.material
            row = box.row(align=True)
            active = i == obj.active_material_index
            op = row.operator('paint.color_retouch_use', text=str(i + 1) if i < 9 else '·',
                              depress=active, icon='RADIOBUT_ON' if active else 'RADIOBUT_OFF')
            op.index = i
            if mat is None:
                row.label(text='(vacío)')
                continue
            node = paint.bsdf(mat)
            if node is not None:
                row.prop(node.inputs['Base Color'], 'default_value', text='')
            else:
                row.prop(mat, 'diffuse_color', text='')
            row.prop(mat, 'name', text='')

        sub = box.box()
        sub.label(text='Color nuevo', icon='ADD')
        r = sub.row(align=True)
        r.prop(p, 'new_color', text='')
        r.prop(p, 'new_name', text='')
        sub.operator('paint.color_retouch_add', icon='ADD')

        box = layout.box()
        box.label(text='2 · Herramienta', icon='BRUSH_DATA')
        box.row().prop(p, 'tool', expand=True)
        if p.tool == 'BRUSH':
            box.prop(p, 'size', slider=True)
            r = box.row(align=True)
            r.prop(p, 'through', toggle=True)
            r.prop(p, 'edges', toggle=True)
            if p.edges:
                box.prop(p, 'angle', slider=True)
        elif p.tool == 'FILL':
            box.label(text='Rellenar hasta:')
            box.row().prop(p, 'fill_until', expand=True)
            if p.fill_until == 'EDGE':
                box.prop(p, 'angle', slider=True)
        col = box.column(); col.scale_y = 1.6
        col.operator('paint.color_retouch', text='Retocar', icon='BRUSH_DATA')
        tips = box.column(align=True); tips.scale_y = .8
        tips.label(text='Clic izquierdo pinta · botón central gira')
        tips.label(text='Shift+clic: línea recta desde el último punto')
        tips.label(text='Ctrl+Z deshace · Esc termina')

        layout.operator('paint.color_retouch_save', icon='FILE_TICK')
        if p.done:
            col = layout.column(); col.scale_y = .8
            col.label(text=p.done, icon='CHECKMARK')
        if not paint.is_working_copy(obj):
            layout.label(text='Se pintará en una copia: el original se oculta.', icon='INFO')


@persistent
def _sync(scene, _graph=None):
    """Mantiene el color de la vista Sólido igual al color elegido en la paleta."""
    obj = bpy.context.view_layer.objects.active if bpy.context.view_layer else None
    if obj is None or obj.type != 'MESH':
        return
    for slot in obj.material_slots:
        if slot.material is not None:
            paint.sync_viewport(slot.material)


classes = (ColorRetouchProps, PAINT_OT_color_retouch, PAINT_OT_color_retouch_use, PAINT_OT_color_retouch_add,
           PAINT_OT_color_retouch_save, VIEW3D_PT_color_retouch)


def register():
    common.register_classes(classes, 'color_retouch_props', ColorRetouchProps)
    if _sync not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_sync)


def unregister():
    while _sync in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_sync)
    _canvas['key'] = _canvas['canvas'] = None
    common.unregister_classes(classes, 'color_retouch_props')
