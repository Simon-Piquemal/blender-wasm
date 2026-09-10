# SPDX-FileCopyrightText: 2009-2023 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

import bpy
from bpy.types import Panel, Menu, Operator
from bpy.app.translations import (
    pgettext_n as n_,
)


# WEB BUILD -- see blender/LOCAL_CHANGES.md.
#
# Does the bundled "essentials" asset library ship with this build?
#
# It does not. Staging copies only blender/scripts and blender/release/datafiles
# (scripts/link_blender_web.sh, scripts/restage_assets.sh); blender/assets/ --
# which upstream installs as <datafiles>/assets -- is left out, and the brush
# half of it is opt-in behind BLENDER_WEB_BUNDLE_BRUSHES. So
# essentials_directory_path() (asset_system/intern/library_types/
# essentials_library.cc) resolves to "" and the ESSENTIALS library is empty.
#
# Two visible consequences in the Add Modifier menu, both fixed by the guards
# below rather than by shipping 6+ MB of node-group assets:
#
# 1. Every operator_modifier_add_asset() entry -- "Array", "Curve to Tube",
#    "Scatter on Surface" -- runs object.modifier_add_node_group against
#    nodes/geometry_nodes_essentials.blend, get_node_group() returns nullptr and
#    the operator CANCELs. The report would normally land in the status bar, but
#    webgpu-hide-global-bars.patch deletes it: the entry is a dead button that
#    does nothing at all, with the working modifier hidden below it under the
#    discouraging label "Array (Legacy)".
# 2. menu_contents("OBJECT_MT_modifier_add_root_catalogs") reaches
#    root_catalogs_draw() (editors/object/add_modifier_assets.cc), whose
#    all_loading_finished() is asset::list::is_loaded() -- permanently false
#    here, because nothing in this build ever fetches the asset list. The menu
#    therefore always ends with a "Loading Asset Libraries" line, which reads as
#    "modifiers are broken".
#
# Flip back to True together with the asset staging if the essentials node
# groups are ever bundled.
WEBAPP_MODIFIER_ASSETS = False


class ModifierButtonsPanel:
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "modifier"
    bl_options = {'HIDE_HEADER'}


class ModifierAddMenu:
    MODIFIER_TYPES_TO_LABELS = {
        enum_it.identifier: enum_it.name
        for enum_it in bpy.types.Modifier.bl_rna.properties["type"].enum_items_static
    }
    MODIFIER_TYPES_TO_ICONS = {
        enum_it.identifier: enum_it.icon
        for enum_it in bpy.types.Modifier.bl_rna.properties["type"].enum_items_static
    }
    MODIFIER_TYPES_I18N_CONTEXT = bpy.types.Modifier.bl_rna.properties["type"].translation_context

    @classmethod
    def operator_modifier_add(cls, layout, mod_type, text=None, no_icon=False):
        label = text if text else cls.MODIFIER_TYPES_TO_LABELS[mod_type]
        layout.operator(
            "object.modifier_add",
            text=label,
            # Although these are operators, the label actually comes from an (enum) property,
            # so the property's translation context must be used here.
            text_ctxt=cls.MODIFIER_TYPES_I18N_CONTEXT,
            icon='NONE' if no_icon else cls.MODIFIER_TYPES_TO_ICONS[mod_type],
        ).type = mod_type

    @classmethod
    def operator_modifier_add_asset(cls, layout, name, icon='NONE'):
        if not WEBAPP_MODIFIER_ASSETS:
            return
        props = layout.operator(
            "object.modifier_add_node_group",
            text=name,
            text_ctxt=cls.MODIFIER_TYPES_I18N_CONTEXT,
            icon=icon,
        )
        props.asset_library_type = 'ESSENTIALS'
        props.asset_library_identifier = ""
        props.relative_asset_identifier = "nodes/geometry_nodes_essentials.blend/NodeTree/" + name


class DATA_PT_modifiers(ModifierButtonsPanel, Panel):
    bl_label = "Modifiers"

    @classmethod
    def poll(cls, context):
        ob = context.object
        return ob

    def draw(self, _context):
        layout = self.layout
        layout.operator("wm.call_menu", text="Add Modifier", icon='ADD').name = "OBJECT_MT_modifier_add"
        layout.template_modifiers()


class OBJECT_MT_modifier_add(ModifierAddMenu, Menu):
    bl_label = "Add Modifier"
    bl_options = {'SEARCH_ON_KEY_PRESS'}

    def draw(self, context):
        layout = self.layout
        ob = context.object
        if not ob:
            return
        ob_type = ob.type
        geometry_nodes_supported = ob_type in {
            'EMPTY', 'MESH', 'CURVE', 'CURVES',
            'FONT', 'VOLUME', 'POINTCLOUD', 'GREASEPENCIL'
        }

        if layout.operator_context == 'EXEC_REGION_WIN':
            layout.operator_context = 'INVOKE_REGION_WIN'
            layout.operator(
                "WM_OT_search_single_menu",
                text="Search...",
                icon='VIEWZOOM',
            ).menu_idname = "OBJECT_MT_modifier_add"
            layout.separator()

        layout.operator_context = 'INVOKE_REGION_WIN'

        if ob_type in {'MESH', 'CURVE', 'CURVES', 'FONT', 'SURFACE', 'LATTICE', 'GREASEPENCIL', 'POINTCLOUD'}:
            layout.menu("OBJECT_MT_modifier_add_edit")
        if ob_type in {'MESH', 'CURVE', 'FONT', 'SURFACE', 'VOLUME', 'GREASEPENCIL'}:
            layout.menu("OBJECT_MT_modifier_add_generate")
        if ob_type in {'MESH', 'CURVE', 'FONT', 'SURFACE', 'LATTICE', 'VOLUME', 'GREASEPENCIL'}:
            layout.menu("OBJECT_MT_modifier_add_deform")
        if ob_type in {'MESH'}:
            layout.menu("OBJECT_MT_modifier_add_normals")
        if ob_type in {'MESH', 'CURVE', 'FONT', 'SURFACE', 'LATTICE'}:
            layout.menu("OBJECT_MT_modifier_add_physics")
        if ob_type in {'GREASEPENCIL'}:
            layout.menu("OBJECT_MT_modifier_add_color")

        if geometry_nodes_supported and WEBAPP_MODIFIER_ASSETS:
            layout.menu_contents("OBJECT_MT_modifier_add_root_catalogs")

        if geometry_nodes_supported:
            layout.separator()
            self.operator_modifier_add(layout, 'NODES')


class OBJECT_MT_modifier_add_edit(ModifierAddMenu, Menu):
    bl_label = "Edit"
    bl_options = {'SEARCH_ON_KEY_PRESS'}

    def draw(self, context):
        layout = self.layout
        ob_type = context.object.type
        if ob_type == 'MESH':
            self.operator_modifier_add(layout, 'DATA_TRANSFER')
        if ob_type in {'MESH', 'CURVE', 'FONT', 'SURFACE', 'LATTICE'}:
            self.operator_modifier_add(layout, 'MESH_CACHE')
        if ob_type in {'MESH', 'CURVE', 'CURVES', 'FONT', 'POINTCLOUD'}:
            self.operator_modifier_add(layout, 'MESH_SEQUENCE_CACHE')
        if ob_type == 'MESH':
            self.operator_modifier_add(layout, 'UV_PROJECT')
            self.operator_modifier_add(layout, 'UV_WARP')
            self.operator_modifier_add(layout, 'VERTEX_WEIGHT_EDIT')
            self.operator_modifier_add(layout, 'VERTEX_WEIGHT_MIX')
            self.operator_modifier_add(layout, 'VERTEX_WEIGHT_PROXIMITY')
        if ob_type == 'GREASEPENCIL':
            self.operator_modifier_add(layout, 'GREASE_PENCIL_TEXTURE')
            self.operator_modifier_add(layout, 'GREASE_PENCIL_TIME')
            self.operator_modifier_add(layout, 'GREASE_PENCIL_VERTEX_WEIGHT_PROXIMITY')
            self.operator_modifier_add(layout, 'GREASE_PENCIL_VERTEX_WEIGHT_ANGLE')
        if WEBAPP_MODIFIER_ASSETS:
            layout.template_modifier_asset_menu_items(catalog_path=self.bl_label)


class OBJECT_MT_modifier_add_generate(ModifierAddMenu, Menu):
    bl_label = "Generate"
    bl_options = {'SEARCH_ON_KEY_PRESS'}

    def draw(self, context):
        layout = self.layout
        ob_type = context.object.type
        if ob_type in {'MESH', 'CURVE', 'FONT', 'SURFACE'}:
            self.operator_modifier_add_asset(layout, 'Array', icon='MOD_ARRAY')
            if WEBAPP_MODIFIER_ASSETS:
                self.operator_modifier_add(layout, 'ARRAY', text=n_("Array (Legacy)"), no_icon=True)
            else:
                # No asset "Array" above it to disambiguate from: this IS Array.
                self.operator_modifier_add(layout, 'ARRAY')
            self.operator_modifier_add(layout, 'BEVEL')
        if ob_type == 'MESH':
            self.operator_modifier_add(layout, 'BOOLEAN')
        if ob_type in {'MESH', 'CURVE', 'FONT', 'SURFACE'}:
            self.operator_modifier_add(layout, 'BUILD')
            self.operator_modifier_add_asset(layout, n_('Curve to Tube'), icon='MOD_CURVE_TO_TUBE')
            self.operator_modifier_add(layout, 'DECIMATE')
            self.operator_modifier_add(layout, 'EDGE_SPLIT')
        if ob_type == 'MESH':
            self.operator_modifier_add(layout, 'MASK')
        if ob_type in {'MESH', 'CURVE', 'FONT', 'SURFACE'}:
            self.operator_modifier_add(layout, 'MIRROR')
        if ob_type == 'VOLUME':
            self.operator_modifier_add(layout, 'MESH_TO_VOLUME')
        if ob_type == 'MESH':
            self.operator_modifier_add(layout, 'MULTIRES')
        if ob_type in {'MESH', 'CURVE', 'FONT', 'SURFACE'}:
            self.operator_modifier_add(layout, 'REMESH')
            self.operator_modifier_add_asset(layout, n_('Scatter on Surface'), icon='MOD_SCATTER_ON_SURFACE')
            self.operator_modifier_add(layout, 'SCREW')
        if ob_type == 'MESH':
            self.operator_modifier_add(layout, 'SKIN')
        if ob_type in {'MESH', 'CURVE', 'FONT', 'SURFACE'}:
            self.operator_modifier_add(layout, 'SOLIDIFY')
            self.operator_modifier_add(layout, 'SUBSURF')
            self.operator_modifier_add(layout, 'TRIANGULATE')
        if ob_type == 'MESH':
            self.operator_modifier_add(layout, 'VOLUME_TO_MESH')
        if ob_type in {'MESH', 'CURVE', 'FONT', 'SURFACE'}:
            self.operator_modifier_add(layout, 'WELD')
        if ob_type == 'MESH':
            self.operator_modifier_add(layout, 'WIREFRAME')
        if ob_type == 'GREASEPENCIL':
            self.operator_modifier_add(layout, 'GREASE_PENCIL_ARRAY')
            self.operator_modifier_add(layout, 'GREASE_PENCIL_BUILD')
            self.operator_modifier_add(layout, 'GREASE_PENCIL_DASH')
            self.operator_modifier_add(layout, 'GREASE_PENCIL_ENVELOPE')
            self.operator_modifier_add(layout, 'GREASE_PENCIL_LENGTH')
            self.operator_modifier_add(layout, 'LINEART')
            self.operator_modifier_add(layout, 'GREASE_PENCIL_MIRROR')
            self.operator_modifier_add(layout, 'GREASE_PENCIL_MULTIPLY')
            self.operator_modifier_add(layout, 'GREASE_PENCIL_OUTLINE')
            self.operator_modifier_add(layout, 'GREASE_PENCIL_SIMPLIFY')
            self.operator_modifier_add(layout, 'GREASE_PENCIL_SUBDIV')
        if WEBAPP_MODIFIER_ASSETS:
            layout.template_modifier_asset_menu_items(catalog_path=self.bl_label, skip_essentials=True)


class OBJECT_MT_modifier_add_deform(ModifierAddMenu, Menu):
    bl_label = "Deform"
    bl_options = {'SEARCH_ON_KEY_PRESS'}

    def draw(self, context):
        layout = self.layout
        ob_type = context.object.type
        if ob_type in {'MESH', 'CURVE', 'FONT', 'SURFACE', 'LATTICE'}:
            self.operator_modifier_add(layout, 'ARMATURE')
            self.operator_modifier_add(layout, 'CAST')
            self.operator_modifier_add(layout, 'CURVE')
        if ob_type == 'MESH':
            self.operator_modifier_add(layout, 'DISPLACE')
        if ob_type in {'MESH', 'CURVE', 'FONT', 'SURFACE', 'LATTICE'}:
            self.operator_modifier_add(layout, 'HOOK')
        if ob_type == 'MESH':
            self.operator_modifier_add(layout, 'LAPLACIANDEFORM')
        if ob_type in {'MESH', 'CURVE', 'FONT', 'SURFACE', 'LATTICE'}:
            self.operator_modifier_add(layout, 'LATTICE')
            self.operator_modifier_add(layout, 'MESH_DEFORM')
            self.operator_modifier_add(layout, 'SHRINKWRAP')
            self.operator_modifier_add(layout, 'SIMPLE_DEFORM')
        if ob_type in {'MESH', 'CURVE', 'FONT', 'SURFACE'}:
            self.operator_modifier_add(layout, 'SMOOTH')
        if ob_type == 'MESH':
            self.operator_modifier_add(layout, 'CORRECTIVE_SMOOTH')
            self.operator_modifier_add(layout, 'LAPLACIANSMOOTH')
            self.operator_modifier_add(layout, 'SURFACE_DEFORM')
        if ob_type in {'MESH', 'CURVE', 'FONT', 'SURFACE', 'LATTICE'}:
            self.operator_modifier_add(layout, 'WARP')
            self.operator_modifier_add(layout, 'WAVE')
        if ob_type == 'VOLUME':
            self.operator_modifier_add(layout, 'VOLUME_DISPLACE')
        if ob_type == 'GREASEPENCIL':
            self.operator_modifier_add(layout, 'GREASE_PENCIL_ARMATURE')
            self.operator_modifier_add(layout, 'GREASE_PENCIL_HOOK')
            self.operator_modifier_add(layout, 'GREASE_PENCIL_LATTICE')
            self.operator_modifier_add(layout, 'GREASE_PENCIL_NOISE')
            self.operator_modifier_add(layout, 'GREASE_PENCIL_OFFSET')
            self.operator_modifier_add(layout, 'GREASE_PENCIL_SHRINKWRAP')
            self.operator_modifier_add(layout, 'GREASE_PENCIL_SMOOTH')
            self.operator_modifier_add(layout, 'GREASE_PENCIL_THICKNESS')
        if WEBAPP_MODIFIER_ASSETS:
            layout.template_modifier_asset_menu_items(catalog_path=self.bl_label)


class OBJECT_MT_modifier_add_normals(ModifierAddMenu, Menu):
    bl_label = "Normals"
    bl_options = {'SEARCH_ON_KEY_PRESS'}

    def draw(self, context):
        layout = self.layout
        ob_type = context.object.type
        if ob_type == 'MESH':
            self.operator_modifier_add(layout, 'NORMAL_EDIT')
            self.operator_modifier_add(layout, 'WEIGHTED_NORMAL')
        if WEBAPP_MODIFIER_ASSETS:
            layout.template_modifier_asset_menu_items(catalog_path=self.bl_label)


class OBJECT_MT_modifier_add_physics(ModifierAddMenu, Menu):
    bl_label = "Physics"
    bl_options = {'SEARCH_ON_KEY_PRESS'}

    def draw(self, context):
        layout = self.layout
        ob_type = context.object.type
        if ob_type == 'MESH':
            self.operator_modifier_add(layout, 'CLOTH')
            self.operator_modifier_add(layout, 'COLLISION')
            self.operator_modifier_add(layout, 'DYNAMIC_PAINT')
            self.operator_modifier_add(layout, 'EXPLODE')
            self.operator_modifier_add(layout, 'FLUID')
            self.operator_modifier_add(layout, 'OCEAN')
            self.operator_modifier_add(layout, 'PARTICLE_INSTANCE')
            self.operator_modifier_add(layout, 'PARTICLE_SYSTEM')
        if ob_type in {'MESH', 'CURVE', 'FONT', 'SURFACE', 'LATTICE'}:
            self.operator_modifier_add(layout, 'SOFT_BODY')
        if WEBAPP_MODIFIER_ASSETS:
            layout.template_modifier_asset_menu_items(catalog_path=self.bl_label)


class OBJECT_MT_modifier_add_color(ModifierAddMenu, Menu):
    bl_label = "Color"
    bl_options = {'SEARCH_ON_KEY_PRESS'}

    def draw(self, context):
        layout = self.layout
        ob_type = context.object.type
        if ob_type == 'GREASEPENCIL':
            self.operator_modifier_add(layout, 'GREASE_PENCIL_COLOR')
            self.operator_modifier_add(layout, 'GREASE_PENCIL_OPACITY')
            self.operator_modifier_add(layout, 'GREASE_PENCIL_TINT')
        if WEBAPP_MODIFIER_ASSETS:
            layout.template_modifier_asset_menu_items(catalog_path=self.bl_label)


class AddModifierMenu(Operator):
    bl_idname = "object.add_modifier_menu"
    bl_label = "Add Modifier"

    @classmethod
    def poll(cls, context):
        # NOTE: This operator only exists to add a poll to the add modifier shortcut in the property editor.
        space = context.space_data
        return space and space.type == 'PROPERTIES' and space.context == 'MODIFIER'

    def invoke(self, _context, _event):
        return bpy.ops.wm.call_menu(name="OBJECT_MT_modifier_add")


classes = (
    DATA_PT_modifiers,
    OBJECT_MT_modifier_add,
    OBJECT_MT_modifier_add_edit,
    OBJECT_MT_modifier_add_generate,
    OBJECT_MT_modifier_add_deform,
    OBJECT_MT_modifier_add_normals,
    OBJECT_MT_modifier_add_physics,
    OBJECT_MT_modifier_add_color,
    AddModifierMenu,
)

if __name__ == "__main__":  # only for live edit.
    from bpy.utils import register_class

    for cls in classes:
        register_class(cls)
