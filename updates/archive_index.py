import json
import os
from datetime import datetime
AddonPath = os.path.dirname(os.path.dirname(__file__))
import subprocess

Global_ArchiveIDS = []
Global_UpdateTime = {} # Global_UpdateTime["timestamp"]
Global_updatearchivelistCN = f"{AddonPath}\\hashlists\\update_archive_listCN.txt"
Global_kdoc_result_path = f"{AddonPath}\\get_update_lists\\kdoc_result.json"
Global_UpdateEXE = f"{AddonPath}\\get_update_lists\\GetArchiveIDS_AQ.exe"
Global_ErrorLog = f"{AddonPath}\\get_update_lists\\kdoc_log.txt"

now = datetime.now()
today = now.date()

def checkErrorLog():
    """
    Purpose: 
    """
    with open(Global_ErrorLog, "r",encoding="utf-8") as f:
        line = f.readlines()[0]
        if "请求成功" in line:
            return True
        else:
            return False

# end def

def GetAndUpdateArchivelistCN():
    subprocess.run([Global_UpdateEXE,AddonPath], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,cwd=os.path.dirname(Global_UpdateEXE))
    if checkErrorLog():
        with open(Global_kdoc_result_path , "r",encoding="utf-8") as f:
            data = json.load(f)
            Global_ArchiveIDS = data["data"]["result"]
            Global_UpdateTime = data["data"]["logs"][0]
            
        with open( Global_updatearchivelistCN , "w",encoding="utf-8") as f:
            f.write("本地更新时间："+ str(today) +" "+ str(Global_UpdateTime["timestamp"]) +"\n")
            for i in Global_ArchiveIDS:
                ArchiveID = str(i[0]).replace("\n", "")
                Classify  = str(i[1]).replace("\n", "")
                Description = str(i[2]).replace("\n", "")
                each_line = ArchiveID.strip() + "#" + Classify.strip() + "#" + Description.strip()

                f.write(each_line +"\n")
        return True
    else:
        return False
