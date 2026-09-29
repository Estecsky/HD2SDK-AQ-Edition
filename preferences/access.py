import bpy
import os
from os.path import dirname, realpath, basename
from bpy.props import BoolProperty, IntProperty


class AQ_Prefs:

    @staticmethod
    def pref_():
        return bpy.context.preferences.addons[AQ_PublicClass.AQ_ADDON_NAME].preferences

    @property
    def pref(self):
        return self.pref_()

    @staticmethod
    def get_addon_prefs(addon_name=None):
        addon = AQ_PublicClass.AQ_ADDON_NAME if addon_name is None else addon_name
        return bpy.context.preferences.addons[addon].preferences


class AQ_PublicClass(AQ_Prefs):

    # This module lives in <installed-addon>.preferences, not the package root.
    AQ_ADDON_NAME = __package__.rsplit('.', 1)[0]
    
class AQ_StaticMeshError(Exception):
    def __init__(self, value):
        self.value = value
    def __str__(self): 
        return repr(self.value)
