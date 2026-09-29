"""Two-input companion merger UI; does not import or mutate the SDK root."""
import os
import re
import uuid

import bpy
from bpy.props import BoolProperty, CollectionProperty, IntProperty, StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ExportHelper, ImportHelper

from .runtime_group import write_group
from .runtime_group_paths import absolute_path, output_path, validate_inputs


def _absolute(value):
    return absolute_path(value, bpy.path.abspath)


def _normalize_setting(name):
    def update(settings, _context):
        value = getattr(settings, name)
        normalized = _absolute(value)
        if value != normalized:
            setattr(settings, name, normalized)
    return update


# Plain strings plus dedicated browser buttons intentionally avoid Blender's
# built-in path picker (which may store relative paths and cannot fill both rows).
SETTINGS_PROPERTIES = {
    'IndependentRuntimeGroupExpanded': BoolProperty(name='合并 Mod 配套组', default=False),
    'IndependentRuntimeGroupPathA': StringProperty(
        name='配套文件 1', update=_normalize_setting('IndependentRuntimeGroupPathA')),
    'IndependentRuntimeGroupPathB': StringProperty(
        name='配套文件 2', update=_normalize_setting('IndependentRuntimeGroupPathB')),
    'IndependentRuntimeGroupName': StringProperty(name='Mod 组名称', maxlen=80),
}


def draw_runtime_group(layout, settings):
    box = layout.box()
    expanded = settings.IndependentRuntimeGroupExpanded
    box.prop(settings, 'IndependentRuntimeGroupExpanded', emboss=False,
             icon='TRIA_DOWN' if expanded else 'TRIA_RIGHT')
    if not expanded:
        return
    box.label(text='身体 / 头盔顺序不限；可一次选择两个配套 ZIP', icon='INFO')
    for index, name in enumerate(('IndependentRuntimeGroupPathA', 'IndependentRuntimeGroupPathB')):
        row = box.row(align=True)
        row.prop(settings, name)
        row.operator('helldiver2.independent_runtime_group_pick', text='', icon='FILE_FOLDER').slot = index
    box.prop(settings, 'IndependentRuntimeGroupName')
    box.operator('helldiver2.independent_runtime_group', text='合并 Mod 配套组', icon='PACKAGE')


class IndependentRuntimeGroupPickOperator(Operator, ImportHelper):
    bl_idname = 'helldiver2.independent_runtime_group_pick'
    bl_label = '选择独立配套 ZIP'
    bl_description = '选一个文件填入当前路径栏；同时选两个文件填入两栏，身体或头盔顺序不限'
    filter_glob: StringProperty(default='*.zip', options={'HIDDEN'})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement)
    directory: StringProperty(subtype='DIR_PATH')
    slot: IntProperty(default=0, min=0, max=1, options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        settings = context.scene.Hd2ToolPanelSettings
        self.filepath = getattr(settings, ('IndependentRuntimeGroupPathA', 'IndependentRuntimeGroupPathB')[self.slot])
        return ImportHelper.invoke(self, context, event)

    def execute(self, context):
        try:
            paths = [_absolute(os.path.join(self.directory, row.name)) for row in self.files]
            if not paths and self.filepath:
                paths = [_absolute(self.filepath)]
            if len(paths) not in (1, 2):
                raise ValueError('请一次选择一个或两个配套文件；超过两个不会修改路径栏')
            paths = validate_inputs(paths, count=len(paths))
            settings = context.scene.Hd2ToolPanelSettings
            # Validate every selection before touching either field.
            names = ('IndependentRuntimeGroupPathA', 'IndependentRuntimeGroupPathB')
            if len(paths) == 1:
                setattr(settings, names[self.slot], paths[0])
            else:
                for name, path in zip(names, paths):
                    setattr(settings, name, path)
            return {'FINISHED'}
        except (ValueError, OSError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class IndependentRuntimeGroupOperator(Operator, ExportHelper):
    bl_idname = 'helldiver2.independent_runtime_group'
    bl_label = '合并 Mod 配套组'
    bl_description = '合并两套独立运行时配套，不包含模型或材质；在下一窗口指定输出文件名'
    filename_ext = '.hd2irpack.zip'
    filter_glob: StringProperty(default='*.zip', options={'HIDDEN'})
    input_a: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    input_b: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    group_name: StringProperty(name='Mod 组名称', maxlen=80)
    # Only an invoked file browser may authorize overwrite via its native prompt.
    browser_invoked: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})

    def check(self, _context):
        normalized = output_path(_absolute(self.filepath))
        if normalized == self.filepath:
            return False
        self.filepath = normalized
        return True

    def invoke(self, context, event):
        settings = context.scene.Hd2ToolPanelSettings
        try:
            self.input_a, self.input_b = validate_inputs([
                _absolute(settings.IndependentRuntimeGroupPathA),
                _absolute(settings.IndependentRuntimeGroupPathB)])
        except (ValueError, OSError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.group_name = (settings.IndependentRuntimeGroupName.strip() or
                           settings.IndependentProjectName.strip() or 'Mod 配套组')
        leaf = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', self.group_name).rstrip('. ') or 'Mod 配套组'
        self.filepath = output_path(os.path.join(os.path.dirname(self.input_a), leaf))
        self.check_existing = True
        self.browser_invoked = True
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        try:
            settings = context.scene.Hd2ToolPanelSettings
            paths = validate_inputs([
                _absolute(self.input_a or settings.IndependentRuntimeGroupPathA),
                _absolute(self.input_b or settings.IndependentRuntimeGroupPathB)])
            if not self.filepath.strip():
                raise ValueError('请指定输出配套文件路径')
            target = output_path(_absolute(self.filepath))
            # If an execute caller bypassed check(), do not overwrite a different
            # normalized target than the one the file browser confirmed.
            overwrite = self.browser_invoked and self.check_existing and target == self.filepath
            identity = context.scene.get('HD2IR_RuntimeGroupId') or uuid.uuid4().hex
            name = self.group_name.strip() or settings.IndependentRuntimeGroupName.strip() or settings.IndependentProjectName.strip() or 'Mod 配套组'
            manifest = write_group(paths, target, identity=identity, name=name, overwrite=overwrite)
            context.scene['HD2IR_RuntimeGroupId'] = identity
            self.report({'INFO'}, f"已合并 {len(manifest['members'])} 套配套；头身保存域及作者声明保持独立")
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


CLASSES = (IndependentRuntimeGroupPickOperator, IndependentRuntimeGroupOperator)
