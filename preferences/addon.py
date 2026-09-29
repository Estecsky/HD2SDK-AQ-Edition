import bpy
from .access import AQ_PublicClass
from ..updates import addon_updater_ops
from bpy.props import BoolProperty, IntProperty


@addon_updater_ops.make_annotations
class HD2_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = AQ_PublicClass.AQ_ADDON_NAME
    
   # addon updater preferences
    auto_check_update: bpy.props.BoolProperty(
        name="Auto-check for Update",
        description="If enabled, auto-check for updates using an interval",
        default=False,
    )  # type: ignore

    updater_interval_months: bpy.props.IntProperty(
        name="Months",
        description="Number of months between checking for updates",
        default=0,
        min=0,
    )  # type: ignore
    updater_interval_days: bpy.props.IntProperty(
        name="Days",
        description="Number of days between checking for updates",
        default=7,
        min=0,
    )  # type: ignore
    updater_interval_hours: bpy.props.IntProperty(
        name="Hours",
        description="Number of hours between checking for updates",
        default=0,
        min=0,
        max=23,
    )  # type: ignore
    updater_interval_minutes: bpy.props.IntProperty(
        name="Minutes",
        description="Number of minutes between checking for updates",
        default=0,
        min=0,
        max=59,
    )  # type: ignore
    adv_decompress_path : bpy.props.StringProperty(
        name="adv_decompress_path",
        description = "自定义解压导出路径，留空则使用默认缓存路径",
        default = "",
        subtype='DIR_PATH'
    )
    adv_full_package_list_export_path : bpy.props.StringProperty(
        name="adv_full_package_list_export_path",
        description="自定义完整Package列表导出路径",
        default="",
        subtype="FILE_PATH"
    )
    
    
    MakeCollections  : BoolProperty(name="Make Collections", description = "Make new collection when importing meshes", default = False)
    ShowAnimations   : BoolProperty(name="Animations", description = "Show Animations", default = False)
    ShowParticles    : BoolProperty(name="Particles",description="显示粒子",default=True)
    tga_Tex_Import_Switch : BoolProperty(name="TGA_Tex_Import_Switch",default = False,description = "开启TGA纹理导入开关，tga会自动转换为dds,文件输出目录为软件缓存目录")
    png_Tex_Import_Switch : BoolProperty(name="PNG_Tex_Import_Switch",default = False,description = "开启PNG纹理导入开关，png会自动转换为dds,文件输出目录为软件缓存目录")
    ShowArchivePatchPath : BoolProperty(name="ShowArchivePatchPath",default = False,description = "实时显示活动Archive和Patch的路径")
    ShowQuickSwitch : BoolProperty(name="ShowQuickSwitch",default = True ,description = "显示快捷设置按钮，开启此项会将导入Lods、导入静态物体、自动Lods按钮直接显示在主面板")
    ShowQuickTestButton : BoolProperty(name="ShowQuickTestButton", default=True, description="显示测试Mod按钮；检索游戏data目录最大Patch序号并递增写入，独立配套会先校验并安全部署")
    ShowZipPatchButton : BoolProperty(name="ShowZipPatchButton",default = False,description = "显示打包Patch为Zip功能")
    DisplayRenameButton : BoolProperty(name="DisplayRenameButton",default = True,description = "网格条目中显示重命名按钮")
    DisplayFriendlyName_Mesh_Skel : BoolProperty(name="DisplayFriendlyName",default = True,description = "在导入网格和骨架时赋予可读名称（如果有）")
    Layout_search_New : BoolProperty(name="Layout_search_New",default = True ,description = "显示搜索已知Archive为主的布局")
    ShadeSmooth      : BoolProperty(name="Shade Smooth", description = "导入模型时平滑着色,开启此项将关闭自动平滑", default = True)
    SaveUseAutoSmooth : BoolProperty(name="SaveUseAutoSmooth",default = True,description = "保存网格时使用自动平滑并将平滑角度设为180度，视觉上将与平滑着色一致，关闭则不对保存网格做任何调整")
    ImportStatic : BoolProperty(name="ImportStatic",default = False,description = "导入静态网格（无权重）")
    ShowshaderVariables_CN : BoolProperty(name="DisplayRenameButton",default = True,description = "显示着色器参数中文翻译")
    CustomGamePath : bpy.props.BoolProperty(name="CustomGamePath",default = False,description = "自定义游戏文件目录，如果你只是将游戏文件完整复制到其他位置，可以使用此选项来解除限制，不会强制检查steamapp目录")
    advanced_mode : BoolProperty(name="Advanced Mode", description = "显示高级选项", default = False)
    ShowLuaMenu : BoolProperty(name="ShowLuaMenu", default=False, description="显示Lua与Boot Patch相关功能")
    AutoCleanArmor : BoolProperty(name="AutoCleanArmor", default=True, description="独立身体保存时自动清理本次计划未覆盖的甲片；失败整次回滚，头盔与普通保存不受影响")
    def draw(self, context):

        # layout = self.layout
        layout = self.layout
        layout.prop(self, "tga_Tex_Import_Switch",text="TGA纹理导入开关")
        layout.prop(self, "png_Tex_Import_Switch",text="PNG纹理导入开关")
        layout.prop(self, "ShowArchivePatchPath",text="实时显示活动Archive和Patch的路径")
        layout.prop(self, "ShowQuickTestButton",text="显示测试Mod按钮")
        layout.prop(self, "ShowZipPatchButton",text="显示打包Patch为Zip功能")
        layout.prop(self, "Layout_search_New",text="显示搜索已知Archive为主的布局")
        layout.prop(self, "ShowQuickSwitch",text="显示快捷设置按钮")
        layout.prop(self, "DisplayFriendlyName_Mesh_Skel",text="在导入网格和骨架时赋予可读名称（如果有）")
        layout.prop(self, "SaveUseAutoSmooth",text="保存网格时开启自动平滑")
        layout.prop(self,"CustomGamePath",text="开启自定义游戏文件目录")
        layout.prop(self, "advanced_mode",text="开启高级选项")
        addon_updater_ops.update_settings_ui(self, context)

def register():
    bpy.utils.register_class(HD2_AddonPreferences)


def unregister():
    bpy.utils.unregister_class(HD2_AddonPreferences)
