import os
import sys
import shutil
import json
import re
import time
import socket
import struct
import base64
import sqlite3
import tempfile
import webbrowser
import urllib.request
import urllib.parse
import threading
import subprocess
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path

VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.ts', '.mkv', '.m4v', '.wmv'}

if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
    if "Contents/MacOS" in APP_DIR.replace("\\", "/"):
        APP_DIR = os.path.abspath(os.path.join(APP_DIR, "../../.."))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

APP_TITLE = "救護影像抓取小幫手 v4.4 (智慧多裝置相容版)"
CONFIG_FILE = os.path.join(APP_DIR, "backup_config.json")

DEFAULT_CAR_CHROME_EMAIL = "AUTO"
DEFAULT_CAR_SYSTEM_URL = "http://car.tccf.gov.tw/car/"
CAR_SYSTEM_URL_PRESETS = [
    "http://car.tccf.gov.tw/car/",
    "http://car.fire.taichung.gov.tw/car/login.php",
    "https://fems.taichung.gov.tw/HPG01000/Index",
]

DEFAULT_CAR_TARGET_LICENSE = "BVX-0039"  # 十九甲分隊主力救護車
DEFAULT_CAR_LICENSE_PRESETS = [
    "BVX-0039",
    "BPY-0620",
    "KEP-7273",
    "EPE-3306",
    "638-NVM",
    "KEP-0578",
    "KES-8219",
    "BXJ-2183",
    "BTB-2181",
    "KEP-7283",
]


def get_car_mileage_url(base_url: str = DEFAULT_CAR_SYSTEM_URL) -> str:
    """取得車輛管理系統里程登記頁面 (pages/mileage_nw.php) 絕對網址"""
    base = (base_url or "").strip()
    if not base:
        return "http://car.tccf.gov.tw/car/pages/mileage_nw.php"
    if "pages/mileage_nw.php" in base:
        return base
    parsed = urllib.parse.urlparse(base)
    scheme = parsed.scheme or "http"
    netloc = parsed.netloc or "car.tccf.gov.tw"
    path = parsed.path or "/car/"
    if "/car" in path:
        return f"{scheme}://{netloc}/car/pages/mileage_nw.php"
    return f"{scheme}://{netloc}/pages/mileage_nw.php"

# 消防分隊同仁名冊預設清單（供下拉選單與字元自動過濾，讀取勤務表後自動更新為實際同仁名冊）
DEFAULT_OFFICER_NAMES = [
    "隊員A", "隊員B", "隊員C", "隊員D",
    "隊員E", "隊員F", "隊員G", "隊員H",
    "隊員I", "隊員J", "隊員K", "隊員L",
    "隊員M", "隊員N", "隊員O", "隊員P",
    "隊員Q", "隊員R", "隊員S", "隊員T"
]

# 未錄影備註原因自訂清單（供預檢視窗 0 部影片時秒選）
DEFAULT_MISSING_REASONS = [
    "設備沒電",
    "同仁未配戴",
    "未開機",
    "設備故障",
    "記憶卡讀取異常",
    "鏡頭髒污無效"
]

# 智慧設備指紋演算法官方預設規則（使用者可於快速設定自訂調整，系統將 100% 嚴格遵循此設定）
DEFAULT_DASHCAM_KEYWORDS = "CAREC, CARDV, NORMAL, EVENT, PARK, MOVIE, TS, EMR, FRONT, REAR, CAR_DVR"
DEFAULT_DASHCAM_FN_PATTERNS = "_F., _R., NORMAL_, EVENT_"
DEFAULT_BODYCAM_KEYWORDS = "DPB30, DPB10, DPB60, DPB70, DCIM, 100MEDIA, 100DSCIM, RECORD, VIDEO, AUDIO, PHOTO, BODYCAM, POLICE, AEE"
DEFAULT_TIME_BUFFER_MINUTES = 15

def get_duty_date(date_str, start_hour):
    """根據出勤日期與出勤時段計算對應之消防勤務排班日期
    消防勤務以「當日 08:00 至 隔日 08:00」為一個勤務天。
    若出勤時間為 00:00 ~ 07:59 (即 start_hour < 8)，則對應至「前一日」之勤務排班。
    回傳: (duty_year, duty_month, duty_day, is_previous_day)
    """
    if not date_str:
        return None, None, None, False
    s = date_str.strip()
    year = datetime.now().year
    m, d = None, None

    parts = [p for p in re.split(r'[-/.\s]+', s) if p]
    if len(parts) >= 3:
        try:
            y_val = int(parts[0])
            year = y_val + 1911 if y_val < 1900 else y_val
            m = int(parts[1])
            d = int(parts[2])
        except:
            pass
    elif len(parts) == 2:
        try:
            m = int(parts[0])
            d = int(parts[1])
        except:
            pass

    if m is None or d is None:
        digits = re.sub(r'\D', '', s)
        if len(digits) == 8:
            try:
                year = int(digits[:4])
                m = int(digits[4:6])
                d = int(digits[6:8])
            except:
                pass
        elif len(digits) == 7:
            try:
                year = int(digits[:3]) + 1911
                m = int(digits[3:5])
                d = int(digits[5:7])
            except:
                pass
        elif len(digits) == 4:
            try:
                m = int(digits[:2])
                d = int(digits[2:])
            except:
                pass
        elif len(digits) == 3:
            try:
                if int(digits[:2]) <= 12 and int(digits[2]) > 0:
                    m = int(digits[:2])
                    d = int(digits[2])
                else:
                    m = int(digits[0])
                    d = int(digits[1:])
            except:
                pass

    if m is None or d is None or not (1 <= m <= 12 and 1 <= d <= 31):
        return None, None, None, False

    try:
        dt = datetime(year, m, d)
    except ValueError:
        return None, None, None, False

    if start_hour is not None and 0 <= start_hour < 8:
        duty_dt = dt - timedelta(days=1)
        return duty_dt.year, duty_dt.month, duty_dt.day, True
    return dt.year, dt.month, dt.day, False

def detect_roster_folder_month(folder):
    """偵測指定勤務表目錄所屬之月份 (例如 10 月回傳 10)
    判斷來源：
      1. 目錄名稱包含「10月」、「10月份」、「11310」等
      2. 目錄內含之「分隊-MMDD.xlsm」或「勤務表-MMDD.xlsm」檔案
    """
    if not folder or not os.path.exists(folder):
        return None
    from collections import Counter
    target_dir = os.path.dirname(folder) if os.path.isfile(folder) else folder

    # 1. 檢查目錄名稱
    base = os.path.basename(os.path.abspath(target_dir))
    m = re.search(r'(\d{1,2})\s*月', base)
    if m:
        month = int(m.group(1))
        if 1 <= month <= 12:
            return month
    m = re.search(r'(?:20\d{2}|1\d{2})(\d{2})$', base)
    if m:
        month = int(m.group(1))
        if 1 <= month <= 12:
            return month

    # 2. 檢查目錄內勤務表檔案名稱
    months = []
    try:
        for f in os.listdir(target_dir):
            if f.startswith('~$'):
                continue
            if f.lower().endswith(('.xlsm', '.xlsx')):
                m = re.search(r'(?:.+分隊|分隊|勤務表)[-_](\d{2})\d{2}', f)
                if m:
                    mo = int(m.group(1))
                    if 1 <= mo <= 12:
                        months.append(mo)
    except:
        pass

    if months:
        return Counter(months).most_common(1)[0][0]
    return None

def find_sibling_month_folder(current_folder, target_month):
    """當目前目錄與出勤月份不符時，嘗試在父層目錄尋找對應 target_month 的資料夾 (例如 11月、11月份、11311 等)"""
    if not current_folder or not os.path.exists(current_folder) or target_month is None:
        return None
    target_dir = os.path.dirname(current_folder) if os.path.isfile(current_folder) else current_folder
    parent = os.path.dirname(os.path.abspath(target_dir))
    if not os.path.exists(parent):
        return None
    try:
        for entry in os.listdir(parent):
            full_p = os.path.join(parent, entry)
            if os.path.isdir(full_p) and os.path.abspath(full_p) != os.path.abspath(target_dir):
                mo = detect_roster_folder_month(full_p)
                if mo == target_month:
                    return full_p
    except Exception:
        pass
    return None

def extract_month_day(date_str):
    """從使用者輸入的日期字串中解析出 (月份, 日期)
    支援格式：
      - 8碼西元：20261020 -> (10, 20)
      - 7碼民國：1151020 / 1131002 -> (10, 20) / (10, 2)
      - 4碼月日：1020 / 1002 -> (10, 20) / (10, 2)
      - 帶分隔符：2026-10-20, 115/10/02, 10-02 -> (10, 2)
    """
    if not date_str:
        return None, None
    s = date_str.strip()
    parts = [p for p in re.split(r'[-/.\s]+', s) if p]
    if len(parts) >= 3:
        try:
            m = int(parts[1])
            d = int(parts[2])
            if 1 <= m <= 12 and 1 <= d <= 31:
                return m, d
        except:
            pass
    elif len(parts) == 2:
        try:
            m = int(parts[0])
            d = int(parts[1])
            if 1 <= m <= 12 and 1 <= d <= 31:
                return m, d
        except:
            pass

    digits = re.sub(r'\D', '', s)
    if len(digits) == 8:
        try:
            m = int(digits[4:6])
            d = int(digits[6:8])
            if 1 <= m <= 12 and 1 <= d <= 31:
                return m, d
        except:
            pass
    elif len(digits) == 7:
        try:
            m = int(digits[3:5])
            d = int(digits[5:7])
            if 1 <= m <= 12 and 1 <= d <= 31:
                return m, d
        except:
            pass
    elif len(digits) == 4:
        try:
            m = int(digits[:2])
            d = int(digits[2:])
            if 1 <= m <= 12 and 1 <= d <= 31:
                return m, d
        except:
            pass
    elif len(digits) == 3:
        try:
            m = int(digits[:2])
            d = int(digits[2])
            if 1 <= m <= 12 and 1 <= d <= 31:
                return m, d
        except:
            pass
        try:
            m = int(digits[0])
            d = int(digits[1:])
            if 1 <= m <= 12 and 1 <= d <= 31:
                return m, d
        except:
            pass
    return None, None

def extract_hour(time_str):
    """從使用者輸入的時間字串中解析出起始時段 (0~23)
    支援格式：0830 -> 8, 1200 -> 12, 0015 -> 0, 08:30 -> 8, 2355 -> 23
    """
    if not time_str or not time_str.strip():
        return None
    s = time_str.strip()
    if ':' in s:
        parts = s.split(':')
        try:
            h = int(parts[0])
            if 0 <= h <= 23:
                return h
        except:
            pass
    digits = re.sub(r'\D', '', s)
    if len(digits) >= 3:
        try:
            h = int(digits[:-2])
            if 0 <= h <= 23:
                return h
        except:
            pass
    elif len(digits) in (1, 2):
        try:
            h = int(digits)
            if 0 <= h <= 23:
                return h
        except:
            pass
    return None

def find_roster_file_for_date(folder_or_file, month, day):
    """根據月份與日期在指定路徑尋找對應的勤務表檔案 (格式如：分隊-1020.xlsm)
    若該天有多筆檔案，則優先選擇建立/修改日期最新者。
    回傳: (檔案絕對路徑, 檔案名稱, 錯誤訊息字串)
    """
    if not folder_or_file or not os.path.exists(folder_or_file):
        return None, None, f"指定的勤務表路徑不存在：\n{folder_or_file}"

    # 若傳入的是單一檔案
    if os.path.isfile(folder_or_file):
        fname = os.path.basename(folder_or_file)
        token_mmdd = f"{month:02d}{day:02d}"
        token_alt = f"{month}{day:02d}" if month < 10 else f"{month:02d}{day}"
        if token_mmdd in fname or token_alt in fname:
            return folder_or_file, fname, None
        # 若該檔案不符合，改由其父目錄搜尋
        folder = os.path.dirname(folder_or_file)
    else:
        folder = folder_or_file

    token_mmdd = f"{month:02d}{day:02d}"
    token_alt = f"{month}{day:02d}" if month < 10 else f"{month:02d}{day}"

    candidates = []
    # 搜尋當前目錄與第一層子目錄
    search_dirs = [folder]
    try:
        for entry in os.listdir(folder):
            sub_p = os.path.join(folder, entry)
            if os.path.isdir(sub_p) and not entry.startswith('.'):
                search_dirs.append(sub_p)
    except:
        pass

    for d in search_dirs:
        try:
            for fname in os.listdir(d):
                if fname.startswith('~$'):
                    continue
                lower_name = fname.lower()
                if not (lower_name.endswith('.xlsm') or lower_name.endswith('.xlsx') or lower_name.endswith('.xls')):
                    continue
                full_p = os.path.join(d, fname)
                if not os.path.isfile(full_p):
                    continue

                if token_mmdd in fname or token_alt in fname:
                    score = 0
                    if f"-{token_mmdd}" in fname:
                        score += 20
                    elif token_mmdd in fname:
                        score += 10
                    if "分隊" in fname or "勤務表" in fname:
                        score += 5
                    try:
                        latest_time = max(os.path.getmtime(full_p), os.path.getctime(full_p))
                    except:
                        latest_time = 0
                    candidates.append((score, latest_time, full_p, fname))
        except:
            continue

    if not candidates:
        return None, None, f"在目錄【{folder}】中未找到包含【{token_mmdd}】之當日勤務表檔案！\n\n請確認該日的排班 Excel 檔案（如：分隊-{token_mmdd}.xlsm）是否已存放至該目錄。"

    # 先依評分、再依最新建立/修改時間排序
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best_file = candidates[0]
    return best_file[2], best_file[3], None

def parse_roster_excel(file_path):
    """使用純 Python 標準函式庫 (zipfile + xml) 高速解析 .xlsm / .xlsx 勤務表
    回傳: {
        'id_to_name': { '1': '隊員A', '10': '隊員J', ... },
        'hour_to_col': { 8: 'C', 9: 'D', ..., 23: 'R', 0: 'S', ..., 7: 'Z' },
        'row_91': 4,
        'rows': { row_idx: { cell_ref: val, ... }, ... }
    }
    """
    with zipfile.ZipFile(file_path, 'r') as z:
        # 1. 讀取共用字串 sharedStrings.xml
        shared_strings = []
        if 'xl/sharedStrings.xml' in z.namelist():
            tree = ET.fromstring(z.read('xl/sharedStrings.xml'))
            for si in tree:
                if si.tag.split('}')[-1] == 'si':
                    text = ''.join(t.text for t in si.iter() if t.tag.split('}')[-1] == 't' and t.text)
                    shared_strings.append(text)

        # 2. 找到第一個工作表 (例如 sheet1.xml)
        sheet_names = [f for f in z.namelist() if f.startswith('xl/worksheets/sheet') and f.endswith('.xml')]
        sheet_names.sort()
        if not sheet_names:
            raise ValueError("在 Excel 檔案中找不到任何工作表！")

        sheet_tree = ET.fromstring(z.read(sheet_names[0]))
        sheet_data = None
        for el in sheet_tree:
            if el.tag.split('}')[-1] == 'sheetData':
                sheet_data = el
                break

        if sheet_data is None:
            raise ValueError("工作表中找不到有效資料區域 (sheetData)！")

        rows = {}
        for row in sheet_data:
            if row.tag.split('}')[-1] == 'row':
                r_idx = int(row.attrib.get('r', 0))
                rows[r_idx] = {}
                for c in row:
                    if c.tag.split('}')[-1] == 'c':
                        r_coord = c.attrib.get('r', '')
                        c_type = c.attrib.get('t', '')
                        val = ''
                        for child in c:
                            t_tag = child.tag.split('}')[-1]
                            if t_tag == 'v' and child.text:
                                val = child.text
                            elif t_tag == 'is':
                                for sub in child.iter():
                                    if sub.tag.split('}')[-1] == 't' and sub.text:
                                        val = sub.text
                        if c_type == 's' and val:
                            try:
                                val = shared_strings[int(val)]
                            except:
                                pass
                        rows[r_idx][r_coord] = val

    # 3. 解析名冊 (編號 -> 姓名)，掃描 C18:Z24 等區域
    id_to_name = {}
    standard_pairs = [('C','D'), ('F','G'), ('I','J'), ('L','M'), ('O','P'), ('R','S'), ('U','V'), ('X','Y')]
    for r in range(17, 26):
        row_cells = rows.get(r, {})
        # 依標準成對欄位掃描
        for c1, c2 in standard_pairs:
            num_v = row_cells.get(f'{c1}{r}', '').strip()
            name_v = row_cells.get(f'{c2}{r}', '').strip()
            if num_v and name_v and num_v != '編號' and name_v != '姓名':
                try:
                    num_k = str(int(float(num_v)))
                except:
                    num_k = num_v
                id_to_name[num_k] = name_v

        # 額外彈性掃描：任何左格為數字、右格為 2~4 字中文人名之配對
        for col_ord in range(ord('C'), ord('Y')):
            col1 = chr(col_ord)
            col2 = chr(col_ord + 1)
            v1 = row_cells.get(f'{col1}{r}', '').strip()
            v2 = row_cells.get(f'{col2}{r}', '').strip()
            if v1.isdigit() and re.fullmatch(r'[\u4e00-\u9fa5]{2,4}', v2):
                if v2 not in {'編號', '姓名', '主管', '小隊長', '隊員', '司機', '救護員'}:
                    id_to_name[v1] = v2

    # 4. 解析時段對應 (第二列，C2為08-09，以此類推)
    hour_to_col = {}
    row2 = rows.get(2, {})
    for col_ord in range(ord('C'), ord('Z') + 1):
        col_letter = chr(col_ord)
        val = row2.get(f'{col_letter}2', '').strip()
        m = re.match(r'(\d{1,2})\s*-\s*(\d{1,2})', val)
        if m:
            start_h = int(m.group(1))
            hour_to_col[start_h] = col_letter

    # 若第二列未抓齊 24 小時，使用標準對照預設補齊
    std_cols = {8: 'C', 9: 'D', 10: 'E', 11: 'F', 12: 'G', 13: 'H', 14: 'I', 15: 'J', 16: 'K', 17: 'L', 18: 'M', 19: 'N', 20: 'O', 21: 'P', 22: 'Q', 23: 'R', 0: 'S', 1: 'T', 2: 'U', 3: 'V', 4: 'W', 5: 'X', 6: 'Y', 7: 'Z'}
    for h, c_let in std_cols.items():
        if h not in hour_to_col:
            hour_to_col[h] = c_let

    # 5. 尋找「緊急救護91」所在列
    row_91 = None
    for r in sorted(rows.keys()):
        for col_letter in ['A', 'B', 'C']:
            val = rows[r].get(f'{col_letter}{r}', '').replace(' ', '')
            if '緊急救護91' in val:
                row_91 = r
                break
        if row_91:
            break

    # 若未找到完整字樣，搜尋包含 91 的救護列
    if not row_91:
        for r in sorted(rows.keys()):
            for col_letter in ['A', 'B']:
                val = rows[r].get(f'{col_letter}{r}', '').replace(' ', '')
                if '91' in val and '救護' in val:
                    row_91 = r
                    break
            if row_91:
                break

    return {
        'id_to_name': id_to_name,
        'hour_to_col': hour_to_col,
        'row_91': row_91,
        'rows': rows
    }

def get_all_connected_disks():
    """跨平台取得目前連接之所有磁碟/隨身碟清單 [(路徑, 磁碟名稱/標籤)]"""
    disks = []
    if sys.platform == "win32":
        try:
            import ctypes
            import string
            kernel32 = ctypes.windll.kernel32
            # 避免無媒體之磁碟機（如未放光碟之光碟機、空讀卡機）跳出系統硬體錯誤警告
            try:
                kernel32.SetErrorMode(0x0001 | 0x8000)
            except Exception:
                pass
            bitmask = kernel32.GetLogicalDrives()
            for letter in string.ascii_uppercase:
                if bitmask & 1:
                    drive_path = f"{letter}:\\"
                    try:
                        vol_buf = ctypes.create_unicode_buffer(1024)
                        fs_buf = ctypes.create_unicode_buffer(1024)
                        res = kernel32.GetVolumeInformationW(
                            ctypes.c_wchar_p(drive_path),
                            vol_buf, len(vol_buf),
                            None, None, None,
                            fs_buf, len(fs_buf)
                        )
                        vol_name = vol_buf.value if res else ""
                        disks.append((drive_path, vol_name))
                    except Exception:
                        pass
                bitmask >>= 1
        except Exception:
            pass
    elif sys.platform == "darwin":
        vol_dir = "/Volumes"
        if os.path.exists(vol_dir):
            for item in os.listdir(vol_dir):
                item_path = os.path.join(vol_dir, item)
                if os.path.isdir(item_path):
                    disks.append((item_path, item))
    else:
        for md in ["/media", "/mnt", f"/run/media/{os.getenv('USER', '')}"]:
            if os.path.exists(md):
                for item in os.listdir(md):
                    p = os.path.join(md, item)
                    if os.path.isdir(p):
                        disks.append((p, item))
    return disks

def find_drive_by_label(target_label, connected_disks=None):
    """根據預設的硬碟標籤名稱尋找對應的磁碟路徑，支援複用已查詢的磁碟快取清單以大幅提速"""
    if not target_label or not target_label.strip():
        return None, None
    clean_target = target_label.strip().upper()
    disks = connected_disks if connected_disks is not None else get_all_connected_disks()
    for path, name in disks:
        if clean_target == name.upper() or clean_target in name.upper():
            return path, name
    return None, None

def get_volume_label(path):
    """跨平台取得指定路徑所在磁碟之 Volume Label (硬碟標籤名稱)"""
    if not path or not os.path.exists(path):
        return None
    if sys.platform == "win32":
        try:
            import ctypes
            drive = os.path.splitdrive(os.path.abspath(path))[0]
            if not drive:
                return None
            drive_path = drive + "\\"
            vol_buf = ctypes.create_unicode_buffer(1024)
            fs_buf = ctypes.create_unicode_buffer(1024)
            kernel32 = ctypes.windll.kernel32
            try:
                kernel32.SetErrorMode(0x0001 | 0x8000)
            except Exception:
                pass
            res = kernel32.GetVolumeInformationW(
                ctypes.c_wchar_p(drive_path),
                vol_buf, len(vol_buf),
                None, None, None,
                fs_buf, len(fs_buf)
            )
            return vol_buf.value if res else ""
        except Exception:
            return ""
    elif sys.platform == "darwin":
        abs_p = os.path.abspath(path)
        if abs_p.startswith("/Volumes/"):
            parts = abs_p.split(os.sep)
            if len(parts) >= 3:
                return parts[2]
        return "Macintosh HD"
    else:
        for prefix in ["/media", "/mnt", f"/run/media/{os.getenv('USER', '')}"]:
            if path.startswith(prefix):
                rel = os.path.relpath(path, prefix)
                return rel.split(os.sep)[0]
        return ""

def get_dialog_initialdir(raw_path, fallback_dir=""):
    """智慧取得檔案/資料夾對話框之預設目錄（優先使用輸入框所顯示路徑之現存目錄）
    遵循 station-utility-developer 規範 3.4
    """
    if raw_path and str(raw_path).strip():
        cur = os.path.abspath(str(raw_path).strip())
        while cur:
            if os.path.exists(cur):
                return cur if os.path.isdir(cur) else os.path.dirname(cur)
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
    if fallback_dir and str(fallback_dir).strip():
        fb = os.path.abspath(str(fallback_dir).strip())
        while fb:
            if os.path.exists(fb):
                return fb if os.path.isdir(fb) else os.path.dirname(fb)
            parent = os.path.dirname(fb)
            if parent == fb:
                break
            fb = parent
    return os.getcwd()

def scan_all_ems_devices(start_dt=None, end_dt=None, config=None, quick_mode=False, exclude_paths=None):
    """
    【一鍵全裝置智慧掃描與特徵指紋辨識（單一真相來源 ＆ 100% 嚴格遵循使用者特徵設定）】
    核心設計原則：
    1. 絕不偷塞硬編碼關鍵字：100% 嚴格依照使用者在【系統設定】所設定之「標籤」與「目錄/特徵關鍵字」
       進行辨識！使用者刪掉某個特徵字（例如 TRANSCEND）就會立刻生效！
    2. 自動排除本機系統槽 C:\、含 Windows/Users 之系統硬碟，以及存放目標目錄所在硬碟（不論固定或外接）。
    3. 未符合任何使用者設定之行車紀錄器或密錄器特徵之磁碟，絕不硬抓進密錄器清單，並將原因記錄於 diagnostics。
    """
    # 1. 整理需排除之磁碟代號
    # (a) 系統槽與程式本身所在槽（僅排除固定磁碟 DRIVE_FIXED）
    excluded_fixed_letters = {"C"}
    # (b) 存放目標目錄 / 勤務表目錄所在磁碟（無論 FIXED 或 REMOVABLE，絕不當作攝影機來源！）
    excluded_target_letters = set()

    if sys.platform == "win32":
        for p in (os.getcwd(), os.path.abspath(__file__) if '__file__' in globals() else ""):
            if p:
                try:
                    drv = os.path.splitdrive(os.path.abspath(str(p)))[0]
                    if drv and len(drv) >= 1:
                        excluded_fixed_letters.add(drv[0].upper())
                except Exception:
                    pass

        target_check_paths = []
        if config:
            for k in ("dashcam_target", "bodycam_target", "roster_path", "update_dir"):
                v = config.get(k, "")
                if v:
                    target_check_paths.append(v)
        if exclude_paths:
            target_check_paths.extend([p for p in exclude_paths if p])

        for p in target_check_paths:
            try:
                drv = os.path.splitdrive(os.path.abspath(str(p)))[0]
                if drv and len(drv) >= 1:
                    ltr = drv[0].upper()
                    excluded_target_letters.add(ltr)
                    excluded_fixed_letters.add(ltr)
            except Exception:
                pass

    disks = []
    diagnostics = []

    if sys.platform == "win32":
        try:
            import ctypes
            import string
            kernel32 = ctypes.windll.kernel32
            try:
                kernel32.SetErrorMode(0x0001 | 0x8000)
            except Exception:
                pass
            bitmask = kernel32.GetLogicalDrives()
            for letter in string.ascii_uppercase:
                if bitmask & 1:
                    drive_path = f"{letter}:\\"
                    try:
                        drv_type = kernel32.GetDriveTypeW(ctypes.c_wchar_p(drive_path))
                    except Exception:
                        drv_type = 0

                    # 存放目錄/勤務表所在磁碟（無論固定或外接）一律無條件排除，絕不會自己讀自己
                    if letter in excluded_target_letters and letter != "C":
                        diagnostics.append(f"{drive_path} (存放目標/勤務表所在硬碟，自動排除)")
                    elif drv_type == 3 and letter in excluded_fixed_letters:
                        diagnostics.append(f"{drive_path} (本機系統/程式磁碟，自動排除)")
                    elif drv_type in (2, 3):
                        vol_name = ""
                        try:
                            vol_buf = ctypes.create_unicode_buffer(1024)
                            fs_buf = ctypes.create_unicode_buffer(1024)
                            res = kernel32.GetVolumeInformationW(
                                ctypes.c_wchar_p(drive_path),
                                vol_buf, len(vol_buf),
                                None, None, None,
                                fs_buf, len(fs_buf)
                            )
                            if res:
                                vol_name = vol_buf.value
                        except Exception:
                            vol_name = ""
                        disks.append((drive_path, vol_name, drv_type))
                bitmask >>= 1
        except Exception:
            pass
    elif sys.platform == "darwin":
        vol_dir = "/Volumes"
        if os.path.exists(vol_dir):
            for item in sorted(os.listdir(vol_dir)):
                if item == "Macintosh HD":
                    continue
                item_path = os.path.join(vol_dir, item)
                if os.path.isdir(item_path):
                    disks.append((item_path, item, 2))
    else:
        for md in ["/media", "/mnt", f"/run/media/{os.getenv('USER', '')}"]:
            if os.path.exists(md):
                for item in sorted(os.listdir(md)):
                    p = os.path.join(md, item)
                    if os.path.isdir(p):
                        disks.append((p, item, 2))

    # 2. 100% 嚴格讀取使用者設定的關鍵字與標籤（絕不偷塞已刪除的硬編碼字詞！）
    raw_dash_kw = config.get("dashcam_keywords") if (config and "dashcam_keywords" in config) else DEFAULT_DASHCAM_KEYWORDS
    raw_dash_fn = config.get("dashcam_fn_patterns") if (config and "dashcam_fn_patterns" in config) else DEFAULT_DASHCAM_FN_PATTERNS
    raw_body_kw = config.get("bodycam_keywords") if (config and "bodycam_keywords" in config) else DEFAULT_BODYCAM_KEYWORDS

    raw_dash_kw = raw_dash_kw if raw_dash_kw is not None else ""
    raw_dash_fn = raw_dash_fn if raw_dash_fn is not None else ""
    raw_body_kw = raw_body_kw if raw_body_kw is not None else ""

    try:
        buf_mins = int((config.get("time_buffer_minutes") if config else None) or DEFAULT_TIME_BUFFER_MINUTES)
    except Exception:
        buf_mins = DEFAULT_TIME_BUFFER_MINUTES

    buf_start = (start_dt - timedelta(minutes=buf_mins)) if start_dt else None
    buf_end = ((end_dt or (start_dt + timedelta(hours=3))) + timedelta(minutes=buf_mins)) if start_dt else None

    def _split_tokens(raw_str):
        return [k.strip().upper() for k in re.split(r'[,，、;；|\s]+', str(raw_str or "")) if k.strip()]

    cfg_dash_labels = _split_tokens(config.get("label_dashcam", "DASHCAM") if config else "DASHCAM")
    cfg_body_labels = []
    for k_lbl, def_lbl in (("label_bodycam1", "BODYCAM1"), ("label_bodycam2", "BODYCAM2"), ("label_bodycam3", "BODYCAM3")):
        cfg_body_labels.extend(_split_tokens(config.get(k_lbl, def_lbl) if config else def_lbl))

    DASH_KEYWORDS = set(_split_tokens(raw_dash_kw))
    DASH_FN_PATTERNS = _split_tokens(raw_dash_fn)
    BODY_KEYWORDS = set(_split_tokens(raw_body_kw))

    # 共用弱目錄字（避免新密錄器裡單獨一個 EVENT/MOVIE 被誤搶成行車紀錄器）
    AMBIGUOUS_DIRS = {"MOVIE", "NORMAL", "EVENT", "EMR", "TS"}
    STRONG_DASH_DIRS = DASH_KEYWORDS - AMBIGUOUS_DIRS
    WEAK_DASH_DIRS = DASH_KEYWORDS & AMBIGUOUS_DIRS
    OS_SYSTEM_DIRS = {"WINDOWS", "PROGRAM FILES", "PROGRAM FILES (X86)", "USERS", "PROGRAMDATA", "RECOVERY", "PERFLOGS"}

    dashcam_candidates = []
    bodycam_candidates = []

    for drive_path, vol_name, drv_type in disks:
        norm_label = (vol_name or "").strip().upper()
        top_dirs = set()
        top_files = []
        try:
            entries = os.listdir(drive_path)
            for e in entries:
                ep = os.path.join(drive_path, e)
                if os.path.isdir(ep):
                    top_dirs.add(e.upper())
                else:
                    top_files.append(e)
        except Exception:
            # 空讀卡機槽（無記憶卡插入）或無讀取權限，安全略過
            continue

        # 系統硬碟保護
        if top_dirs & OS_SYSTEM_DIRS:
            diagnostics.append(f"{drive_path} [{vol_name or '無標籤'}] (含系統資料夾，自動排除)")
            continue

        # 收集頂層與深層目錄名稱及影片資訊
        all_dir_names = set(top_dirs)
        max_walk_depth = 2 if quick_mode else 3
        max_video_scan = 30 if quick_mode else 400

        matched_videos = []
        all_videos_count = 0
        has_ts_files = False
        has_fr_suffix = False

        try:
            drive_root_depth = drive_path.rstrip(os.sep).count(os.sep)
            for root, dirs, files in os.walk(drive_path):
                cur_depth = root.count(os.sep) - drive_root_depth
                dirs[:] = [d for d in dirs if not d.startswith(".") and d.upper() not in ("SYSTEM VOLUME INFORMATION", "$RECYCLE.BIN")]
                if cur_depth > max_walk_depth:
                    dirs.clear()
                    continue

                r_upper = os.path.basename(root).upper()
                if r_upper:
                    all_dir_names.add(r_upper)

                for f in files:
                    sfx = Path(f).suffix.lower()
                    if sfx in VIDEO_EXTENSIONS:
                        all_videos_count += 1
                        if sfx == '.ts':
                            has_ts_files = True
                        f_upper = f.upper()
                        if any(pat in f_upper for pat in DASH_FN_PATTERNS):
                            has_fr_suffix = True

                        if not quick_mode:
                            full_f = os.path.join(root, f)
                            try:
                                fmtime = datetime.fromtimestamp(os.path.getmtime(full_f))
                                if buf_start and fmtime < buf_start:
                                    continue
                                if buf_end and fmtime > buf_end:
                                    continue
                                matched_videos.append((full_f, fmtime))
                            except Exception:
                                pass
                        if all_videos_count >= max_video_scan:
                            break
                if all_videos_count >= max_video_scan:
                    break
        except Exception:
            pass

        # 3. 100% 依使用者設定之標籤與目錄特徵計分
        score_dash = 0
        score_body = 0

        # (a) 磁碟名稱 (Volume Label) 比對
        dash_label_hit = bool(norm_label and any(lbl in norm_label for lbl in cfg_dash_labels))
        dash_kw_label_hit = bool(norm_label and any(kw in norm_label for kw in DASH_KEYWORDS))
        body_label_hit = bool(norm_label and any(lbl in norm_label for lbl in cfg_body_labels))
        body_kw_label_hit = bool(norm_label and any(kw in norm_label for kw in BODY_KEYWORDS))

        if dash_label_hit:
            score_dash += 60
        elif dash_kw_label_hit:
            score_dash += 50

        if body_label_hit:
            score_body += 60
        elif body_kw_label_hit:
            score_body += 50

        # (b) 資料夾名稱特徵比對（嚴格以使用者設定的關鍵字為準！）
        strong_dash_dir_hits = all_dir_names & STRONG_DASH_DIRS
        weak_dash_dir_hits = all_dir_names & WEAK_DASH_DIRS
        body_dir_hits = all_dir_names & BODY_KEYWORDS

        if strong_dash_dir_hits:
            score_dash += min(60, 30 * len(strong_dash_dir_hits))
        if body_dir_hits:
            score_body += min(60, 30 * len(body_dir_hits))

        # (c) 行車紀錄器影片副檔名與檔名特徵比對
        if has_ts_files and ("TS" in DASH_KEYWORDS):
            score_dash += 20
        if has_fr_suffix:
            score_dash += 25

        # (d) 弱行車目錄字（如 MOVIE/NORMAL/EVENT/EMR）：
        # 若無密錄器標籤且命中 >=2 個弱行車目錄，或完全無密錄器分數但有命中弱行車目錄，才給予行車分數
        if weak_dash_dir_hits:
            if not (body_label_hit or body_kw_label_hit) and len(weak_dash_dir_hits) >= 2:
                score_dash += 25
            elif score_body == 0 and len(weak_dash_dir_hits) >= 1:
                score_dash += 15

        # (e) 嚴格特徵過濾：完全未命中任何行車或密錄器特徵的磁碟，絕不硬抓進密錄器！
        if score_dash == 0 and score_body == 0:
            sample_dirs = ", ".join(sorted(list(top_dirs))[:5]) if top_dirs else "(無資料夾)"
            diagnostics.append(f"{drive_path} [{vol_name or '無標籤'}] (未符合設定特徵，已略過；頂層目錄: {sample_dirs})")
            continue

        cand_info = {
            'path': drive_path,
            'label': vol_name or os.path.splitdrive(drive_path)[0] or os.path.basename(drive_path.rstrip(os.sep)),
            'drv_type': drv_type,
            'matched_files': matched_videos,
            'all_videos_count': all_videos_count,
            'score_dash': score_dash,
            'score_body': score_body,
        }

        if score_dash > score_body:
            dashcam_candidates.append(cand_info)
        else:
            bodycam_candidates.append(cand_info)

    # 排序候選清單：
    # 行車紀錄器：依行車得分最高者優先；若超過 1 台被判為行車，其餘改歸入密錄器避免設備消失
    dashcam_candidates.sort(key=lambda c: (c['score_dash'], len(c['matched_files']), c['path'].upper()), reverse=True)
    detected_dashcam = None
    if dashcam_candidates:
        detected_dashcam = dashcam_candidates[0]
        if len(dashcam_candidates) > 1:
            bodycam_candidates.extend(dashcam_candidates[1:])

    # 密錄器：依磁碟路徑字母順序穩定排序（例如 I:\ -> 第一台, J:\ -> 第二台）
    bodycam_candidates.sort(key=lambda c: c['path'].upper())

    return {
        'dashcam': detected_dashcam,
        'bodycams': bodycam_candidates,
        'all_scanned': disks,
        'diagnostics': diagnostics
    }

def is_device_connected(path, expected_label, dev_type=None):
    """【硬碟連線雙重驗證 ＆ 智慧寬容相容】：
    1. 指定路徑不存在或為空 -> 判定未連線
    2. 若路徑存在：
       - 若標籤相符 (DASHCAM、BODYCAM1~3) -> 判定連線成功
       - 若標籤不符但路徑存在且為有效裝置/目錄 -> 寬容認定連線成功，避免因磁碟槽代碼動態指派或原廠標籤名稱導致同仁無法操作
    回傳: (is_connected: bool, status_message: str)
    """
    if not path or not os.path.exists(path):
        return False, "未連線 (路徑不存在)"
    actual_label = get_volume_label(path)
    drv_letter = os.path.splitdrive(os.path.abspath(path))[0]
    display_name = actual_label if actual_label else drv_letter

    if not expected_label or not expected_label.strip() or expected_label.strip().upper() == "AUTO":
        return True, f"已連線 [{display_name}]"

    exp = expected_label.strip().upper()
    act = (actual_label or "").strip().upper()
    if exp == act or exp in act:
        return True, f"已連線 [{actual_label}]"

    # 寬容相容：路徑存在但標籤不同（例如 Windows 動態分派或原廠格式化為 NO NAME/TRANSCEND）
    # 只要此目錄有效，判定為已連線，徹底防止封鎖同仁輸入姓名！
    return True, f"已連線 [{display_name}]"

def open_folder_in_explorer(folder_path):
    """跨平台開啟資料夾"""
    if not os.path.exists(folder_path):
        return
    try:
        if sys.platform == "win32":
            os.startfile(folder_path)
        elif sys.platform == "darwin":
            subprocess.run(["open", folder_path])
        else:
            subprocess.run(["xdg-open", folder_path])
    except Exception as e:
        print(f"無法開啟資料夾: {e}")


# ==============================================================================
# 車輛系統登入 ＆ Chrome 設定檔掛載與背景心跳保活模組 (100% 零第三方套件依賴)
# ==============================================================================

def get_chrome_user_data_dir() -> str:
    if sys.platform.startswith("win"):
        return os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")
    elif sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Google/Chrome")
    else:
        return os.path.expanduser("~/.config/google-chrome")


def get_chrome_profiles_list() -> list:
    """
    掃描本機 Chrome Local State 與實體目錄，回傳所有使用者帳號設定檔清單：
    [(顯示名稱, 設定檔目錄名)]
    """
    user_data = get_chrome_user_data_dir()
    local_state_file = os.path.join(user_data, "Local State")
    profiles = []
    seen_dirs = set()

    if os.path.exists(local_state_file):
        try:
            with open(local_state_file, "r", encoding="utf-8", errors="ignore") as f:
                ls = json.load(f)
            info_cache = ls.get("profile", {}).get("info_cache", {})
            for prof_dir, info in sorted(info_cache.items(), key=lambda x: x[0]):
                if not os.path.exists(os.path.join(user_data, prof_dir)):
                    continue
                name = info.get("name", "") or info.get("gaia_name", "")
                email = info.get("user_name", "")
                if email and name:
                    display = f"{name} ({email}) [{prof_dir}]"
                elif email:
                    display = f"{email} [{prof_dir}]"
                elif name:
                    display = f"{name} [{prof_dir}]"
                else:
                    display = f"{prof_dir} [{prof_dir}]"
                profiles.append((display, prof_dir))
                seen_dirs.add(prof_dir)
        except Exception:
            pass

    if os.path.exists(user_data):
        try:
            for item in sorted(os.listdir(user_data)):
                if item in seen_dirs:
                    continue
                if item == "Default" or item.startswith("Profile "):
                    pref_p = os.path.join(user_data, item, "Preferences")
                    if os.path.exists(pref_p):
                        name = item
                        email = ""
                        try:
                            with open(pref_p, "r", encoding="utf-8", errors="ignore") as pf:
                                pdata = json.load(pf)
                            name = pdata.get("profile", {}).get("name", item)
                            acct = pdata.get("account_info", [])
                            if acct and isinstance(acct, list):
                                email = acct[0].get("email", "")
                                if not name or name == "你的 Chrome":
                                    name = acct[0].get("full_name") or acct[0].get("given_name") or name
                        except Exception:
                            pass
                        display = f"{name} ({email}) [{item}]" if email else f"{name} [{item}]"
                        profiles.append((display, item))
                        seen_dirs.add(item)
        except Exception:
            pass

    profiles.append(("🆕 獨立空白沙盒視窗 (免綁定帳號) [CLEAN_SANDBOX]", "NONE"))
    return profiles


def find_chrome_profile_for_user(target_email: str = DEFAULT_CAR_CHROME_EMAIL, default_profile: str = "NONE") -> str:
    """
    自動偵測最合適的分隊 Chrome 設定檔目錄（如 Profile X 或 Default）：
    1. 若有指定設定檔名稱或關鍵字，優先匹配
    2. 自動掃描本機各設定檔的雲端/本機密碼庫與書籤，優先選擇已存有車輛管理系統 (car.tccf.gov.tw) 帳密或名稱含「分隊」的設定檔
    """
    profiles = get_chrome_profiles_list()
    if target_email and target_email.upper() != "AUTO":
        for disp, pdir in profiles:
            if pdir == "NONE":
                continue
            if target_email.lower() in disp.lower() or target_email.lower() == pdir.lower():
                return pdir

    user_data = get_chrome_user_data_dir()
    best_pdir = ""
    best_score = -1

    for disp, pdir in profiles:
        if pdir == "NONE":
            continue
        score = 0
        if "分隊" in disp or "救護" in disp or "消防" in disp:
            score += 50
        prof_path = os.path.join(user_data, pdir)
        for db_name in ("Login Data For Account", "Login Data", "Bookmarks", "AccountBookmarks"):
            fpath = os.path.join(prof_path, db_name)
            if os.path.exists(fpath):
                try:
                    with open(fpath, "rb") as bf:
                        raw_bytes = bf.read(2 * 1024 * 1024)
                    if b"car.tccf.gov.tw" in raw_bytes or b"car.fire.taichung.gov.tw" in raw_bytes:
                        score += 100
                except Exception:
                    pass
        if score > best_score:
            best_score = score
            best_pdir = pdir

    if best_pdir and best_score > 0:
        return best_pdir

    for disp, pdir in profiles:
        if pdir != "NONE":
            return pdir
    return default_profile


def resolve_chrome_profile(user_input: str) -> str:
    """將選單顯示字串、Email 或 Profile X 轉換為本機實際 Chrome 設定檔目錄名"""
    if not user_input:
        return find_chrome_profile_for_user()
    user_input = user_input.strip()
    if (
        user_input.upper() in ("NONE", "CLEAN_SANDBOX", "CAR_CLEAN_SANDBOX")
        or "獨立空白沙盒" in user_input
        or "CLEAN_SANDBOX" in user_input
    ):
        return "NONE"

    profiles = get_chrome_profiles_list()
    avail_dirs = {pdir for _, pdir in profiles if pdir != "NONE"}
    avail_lower_map = {pdir.lower(): pdir for pdir in avail_dirs}

    for disp, pdir in profiles:
        if pdir != "NONE" and user_input == disp:
            return pdir

    target_dir = ""
    m = re.search(r"\[([^\[\]]+)\]\s*$", user_input)
    if m:
        target_dir = m.group(1).strip()
    elif user_input.lower() in avail_lower_map:
        target_dir = avail_lower_map[user_input.lower()]
    elif user_input.startswith("Profile ") or user_input.lower() == "default":
        target_dir = "Default" if user_input.lower() == "default" else user_input
    else:
        for disp, pdir in profiles:
            if pdir == "NONE":
                continue
            if user_input.lower() in disp.lower():
                target_dir = pdir
                break

    if target_dir:
        if target_dir in avail_dirs:
            return target_dir
        if target_dir.lower() in avail_lower_map:
            return avail_lower_map[target_dir.lower()]

    fallback = find_chrome_profile_for_user()
    if fallback and fallback in avail_dirs:
        return fallback
    return "NONE"


def get_chrome_executable() -> str:
    """自動偵測作業系統中的 Chrome 執行檔路徑"""
    if sys.platform == "darwin":
        paths = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
        for p in paths:
            if os.path.exists(p):
                return p
        return "Google Chrome"
    elif sys.platform.startswith("win"):
        paths = [
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        ]
        for p in paths:
            if os.path.exists(p):
                return p
        return "chrome.exe"
    return "google-chrome"


def get_car_local_secure_profile_root() -> str:
    """
    將車輛系統受控瀏覽器設定檔存放於本機使用者專屬 AppData 目錄（而非程式所在資料夾 APP_DIR），
    確保打包或分享程式資料夾時絕不會外流 Chrome 設定檔或密碼庫。
    """
    if sys.platform.startswith("win"):
        base = os.path.expandvars(r"%LOCALAPPDATA%")
        if not base or "%" in base:
            base = os.path.expanduser(r"~\AppData\Local")
        return os.path.join(base, "TCCFD_EMS_Backup", "chrome_car_profile")
    elif sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/TCCFD_EMS_Backup/chrome_car_profile")
    else:
        return os.path.expanduser("~/.local/share/TCCFD_EMS_Backup/chrome_car_profile")


def activate_chrome_window():
    """將現有的 Chrome 視窗喚醒至最上層"""
    if sys.platform == "darwin":
        subprocess.run(["osascript", "-e", 'tell application "Google Chrome" to activate'], capture_output=True)
    elif sys.platform.startswith("win"):
        cmd = 'powershell -WindowStyle Hidden -Command "$w = New-Object -ComObject WScript.Shell; $w.AppActivate(\'Google Chrome\')"'
        try:
            subprocess.Popen(cmd, shell=True)
        except Exception:
            pass


def open_chrome_url(url: str, profile_dir: str = None, ext_dir: str = None):
    """使用日常原生 Chrome 與指定設定檔開啟網址（支援掛載小幫手專用原生擴充套件）"""
    prof = resolve_chrome_profile(profile_dir) if profile_dir else find_chrome_profile_for_user()
    insecure_flag = "--unsafely-treat-insecure-origin-as-secure=http://car.tccf.gov.tw,http://car.fire.taichung.gov.tw,http://inside.fire.taichung.gov.tw,http://fixnew.fire.taichung.gov.tw,http://training.fire.taichung.gov.tw"
    extra_args = []
    if ext_dir and os.path.exists(ext_dir):
        extra_args.extend([f"--load-extension={ext_dir}", f"--disable-extensions-except={ext_dir}"])

    if prof == "NONE":
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-a", "Google Chrome", url, "--args", insecure_flag] + extra_args)
        elif sys.platform.startswith("win"):
            exe = get_chrome_executable()
            if os.path.exists(exe):
                subprocess.Popen([exe, insecure_flag] + extra_args + [url])
            else:
                try:
                    subprocess.Popen(f'start chrome "{url}"', shell=True)
                except Exception:
                    webbrowser.open(url)
        else:
            webbrowser.open(url)
        return

    if sys.platform == "darwin":
        subprocess.Popen([
            "open", "-a", "Google Chrome", url,
            "--args", f"--profile-directory={prof}", insecure_flag
        ] + extra_args)
    elif sys.platform.startswith("win"):
        exe = get_chrome_executable()
        if os.path.exists(exe):
            subprocess.Popen([exe, f"--profile-directory={prof}", insecure_flag] + extra_args + [url])
        else:
            try:
                subprocess.Popen(f'start chrome --profile-directory="{prof}" {insecure_flag} "{url}"', shell=True)
            except Exception:
                webbrowser.open(url)
    else:
        webbrowser.open(url)


def build_car_heartbeat_bookmarklet() -> str:
    """建立可於任何瀏覽器執行的車輛系統防斷線心跳書籤腳本"""
    return (
        "(function(){"
        "function sendPing(manual){"
        "fetch(window.location.href,{method:'GET',cache:'no-store',credentials:'include'})"
        ".then(function(r){"
        "var t=new Date().toLocaleTimeString();"
        "var b=document.getElementById('__car_heartbeat_pill');"
        "if(!b){"
        "b=document.createElement('div');b.id='__car_heartbeat_pill';"
        "b.style.cssText='position:fixed;bottom:16px;right:16px;background:rgba(15,23,42,0.9);color:#f8fafc;padding:6px 14px;border-radius:20px;font-size:12px;z-index:999999;box-shadow:0 4px 14px rgba(0,0,0,0.3);font-family:sans-serif;cursor:pointer;';"
        "b.onclick=function(){sendPing(true);};"
        "(document.body||document.documentElement).appendChild(b);"
        "}"
        "b.innerHTML='🟢 <strong>車輛系統保活中</strong> <span style=\"color:#94a3b8;font-size:11px\">'+t+'</span>';"
        "if(manual)alert('💓 車輛系統心跳封包已成功送出！('+t+')');"
        "}).catch(function(){});"
        "}"
        "if(!window.__car_heartbeat_inpage){"
        "window.__car_heartbeat_inpage=setInterval(function(){sendPing(false);},180000);"
        "}"
        "sendPing(true);"
        "})();"
    )


def build_car_mileage_bookmarklet(target_license: str = DEFAULT_CAR_TARGET_LICENSE) -> str:
    """建立可於任何瀏覽器執行的一鍵里程登記 (BVX-0039) 書籤腳本（支援全 Frame / iframe 深度穿透）"""
    lic = (target_license or DEFAULT_CAR_TARGET_LICENSE).strip()
    return (
        f"(function(){{\n"
        f"var targetLic='{lic}';\n"
        "function getAllFrames(w){\n"
        "  var res=[];\n"
        "  function rec(win){\n"
        "    try{\n"
        "      if(win&&win.document&&res.indexOf(win)===-1){\n"
        "        res.push(win);\n"
        "        for(var i=0;i<win.frames.length;i++){\n"
        "          var sub=win.frames[i];\n"
        "          if(sub&&sub!==win)rec(sub);\n"
        "        }\n"
        "      }\n"
        "    }catch(e){}\n"
        "  }\n"
        "  rec(w||window);\n"
        "  return res;\n"
        "}\n"
        "var frames=getAllFrames(window);\n"
        "var isLogin=false,u=null,p=null,subBtn=null;\n"
        "for(var i=0;i<frames.length;i++){\n"
        "  try{\n"
        "    var d=frames[i].document;\n"
        "    var pw=d.querySelector('input[name=\"rt_admin_password\"], input[type=\"password\"]');\n"
        "    if(pw){\n"
        "      isLogin=true;p=pw;\n"
        "      u=d.querySelector('input[name=\"rt_admin_username\"], input[type=\"text\"]');\n"
        "      subBtn=d.querySelector('input[name=\"Submit\"], input[type=\"submit\"][value*=\"登入\"], .ConWebButton, button[type=\"submit\"]');\n"
        "      break;\n"
        "    }\n"
        "  }catch(e){}\n"
        "}\n"
        "if(isLogin){\n"
        "  if(u&&p&&subBtn){\n"
        "    try{if(typeof rememberMe==='function')rememberMe();}catch(e){}\n"
        "    try{u.focus();p.focus();subBtn.focus();}catch(e){}\n"
        "    try{var evt=new MouseEvent('click',{bubbles:true,cancelable:true,view:window});subBtn.dispatchEvent(evt);}catch(e){}\n"
        "    try{subBtn.click();}\n"
        "    try{var f=d.forms['frmLogin']||subBtn.form||d.querySelector('form');if(f){if(typeof onSubmitCheck==='function')onSubmitCheck(f,false,false);HTMLFormElement.prototype.submit.call(f);}}catch(e){}\n"
        "    return;\n"
        "  }else{\n"
        "    alert('🔑 請先確認帳號與密碼已填妥，再點擊此書籤！');return;\n"
        "  }\n"
        "}\n"
        "var onMileage=false;\n"
        "for(var j=0;j<frames.length;j++){\n"
        "  try{\n"
        "    if((frames[j].location.href||'').indexOf('mileage_nw.php')!==-1){onMileage=true;break;}\n"
        "  }catch(e){}\n"
        "}\n"
        "if(!onMileage){\n"
        "  var base=window.location.origin+(window.location.pathname.indexOf('/car/')!==-1?'/car/':'/');\n"
        "  window.location.href=base+'pages/mileage_nw.php';\n"
        "  return;\n"
        "}\n"
        "function fillDataGrid(){\n"
        "  var allF=getAllFrames(window);\n"
        "  var selLic=null,selDate=null,addBtn=null,actWin=window;\n"
        "  for(var k=0;k<allF.length;k++){\n"
        "    try{\n"
        "      var fd=allF[k].document;\n"
        "      if(!selLic)selLic=fd.getElementById('ryylicense')||fd.querySelector('select[name=\"ryylicense\"]');\n"
        "      if(!selDate)selDate=fd.getElementById('ryymdate')||fd.querySelector('select[name=\"ryymdate\"]');\n"
        "      if(!addBtn){\n"
        "        var b=fd.querySelector('img[title*=\"添加新記錄\"], img[alt*=\"添加新記錄\"], img[src*=\"add.gif\"], img[onclick*=\"dr5__doPostBack\"][onclick*=\"add\"], a[onclick*=\"dr5__doPostBack\"][onclick*=\"add\"]');\n"
        "        if(b)addBtn={el:b,win:allF[k]};\n"
        "        else if(typeof allF[k].dr5__doPostBack==='function')addBtn={fn:allF[k].dr5__doPostBack,win:allF[k]};\n"
        "      }\n"
        "      if(selLic)actWin=allF[k];\n"
        "    }catch(e){}\n"
        "  }\n"
        "  if(!selLic){\n"
        "    if(addBtn){\n"
        "      var nowT=Date.now();\n"
        "      if(!addBtn.win.__last_add_t||nowT-addBtn.win.__last_add_t>2500){\n"
        "        addBtn.win.__last_add_t=nowT;\n"
        "        if(addBtn.el){try{addBtn.el.click();}catch(e){}}\n"
        "        else if(addBtn.fn){try{addBtn.fn('add','-1','');}catch(e){}}\n"
        "      }\n"
        "    }\n"
        "    setTimeout(fillDataGrid,400);return;\n"
        "  }\n"
        "  var matchedVal='';\n"
        "  for(var i=0;i<selLic.options.length;i++){\n"
        "    var opt=selLic.options[i];\n"
        "    if(opt.value.indexOf(targetLic)!==-1||opt.text.indexOf(targetLic)!==-1){\n"
        "      selLic.selectedIndex=i;selLic.value=opt.value;matchedVal=opt.value;break;\n"
        "    }\n"
        "  }\n"
        "  if(matchedVal){\n"
        "    try{selLic.dispatchEvent(new Event('input',{bubbles:true}));}catch(e){}\n"
        "    try{selLic.dispatchEvent(new Event('change',{bubbles:true}));}catch(e){}\n"
        "    if(typeof selLic.onchange==='function')try{selLic.onchange();}catch(e){}\n"
        "    if(typeof actWin.search_car_mileage==='function')try{actWin.search_car_mileage(matchedVal);}catch(e){}\n"
        "  }\n"
        "  var now=new Date();\n"
        "  var todayStr=now.getFullYear()+'-'+String(now.getMonth()+1).padStart(2,'0')+'-'+String(now.getDate()).padStart(2,'0');\n"
        "  if(selDate){\n"
        "    for(var m=0;m<selDate.options.length;m++){\n"
        "      var dOpt=selDate.options[m];\n"
        "      if(dOpt.value===todayStr||dOpt.text.indexOf(todayStr)!==-1){\n"
        "        selDate.selectedIndex=m;selDate.value=dOpt.value;break;\n"
        "      }\n"
        "    }\n"
        "    try{selDate.dispatchEvent(new Event('input',{bubbles:true}));}catch(e){}\n"
        "    try{selDate.dispatchEvent(new Event('change',{bubbles:true}));}catch(e){}\n"
        "    if(typeof selDate.onchange==='function')try{selDate.onchange();}catch(e){}\n"
        "  }\n"
        "  var pill=document.getElementById('__car_mileage_done_bar');\n"
        "  if(!pill){\n"
        "    pill=document.createElement('div');pill.id='__car_mileage_done_bar';\n"
        "    pill.style.cssText='position:fixed;top:0;left:0;right:0;background:linear-gradient(90deg,#065f46,#047857);color:#ffffff;padding:12px 20px;font-size:14px;font-weight:bold;z-index:999999;box-shadow:0 4px 16px rgba(0,0,0,0.3);display:flex;align-items:center;justify-content:space-between;border-bottom:3px solid #34d399;font-family:sans-serif;';\n"
        "    pill.innerHTML='<span>🚑 <strong>【救護小幫手】已為您自動添加新記錄 ＆ 預選車牌【'+(matchedVal||targetLic)+'】與今日日期【'+todayStr+'】！</strong><br><span style=\"font-size:12px;font-weight:normal;color:#a7f3d0\">👉 請接續輸入今日出勤里程並按儲存即可。</span></span><button onclick=\"this.parentElement.remove();\" style=\"background:#059669;color:#fff;border:1px solid #6ee7b7;border-radius:4px;padding:4px 10px;cursor:pointer;\">✕ 關閉</button>';\n"
        "    document.body.appendChild(pill);document.body.style.paddingTop='56px';\n"
        "  }\n"
        "}\n"
        "fillDataGrid();\n"
        "}})();"
    )


class VehicleCDPController:
    """
    車輛系統專用受控瀏覽器管理器 (Chrome DevTools Protocol & 原生本地擴充套件雙軌雙保險架構)
    - 雙軌雙保險：結合 Chrome 原生本地擴充套件 (Content Script) 與本機 CDP 控制埠 9223，保證 100% 免手動登入與導向
    - 採用獨立 Port 9223 與本機 AppData 隔離區，與日常 Chrome 及人事差勤助手 (Port 9222) 完全共存不衝突
    - 自動尋找並同步掛載分隊 Chrome 設定檔的雲端書籤與雲端密碼庫 (Login Data For Account)
    - 透過原生 socket RFC 6455 WebSocket 雙向協議在背景靜默發送防斷線心跳，100% 零第三方套件依賴
    """
    @classmethod
    def _local_http_open(cls, req_or_url, timeout: float = 2.0):
        """保證 100% 繞過任何 Windows 系統 Proxy / PAC / VPN / 環境變數直連 127.0.0.1 本機除錯埠"""
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return opener.open(req_or_url, timeout=timeout)

    def ensure_car_helper_extension(
        self,
        target_url: str = DEFAULT_CAR_SYSTEM_URL,
        target_license: str = DEFAULT_CAR_TARGET_LICENSE,
        preferred_account: str = "",
        auto_submit: bool = True,
        auto_mileage: bool = True,
    ) -> str:
        """
        建立或動態更新車輛管理系統專用之 Chrome 原生本地擴充套件 (Manifest V3)：
        - 完全由 Chrome 內核 Content Script 原生驅動，0 延遲、免 WebSocket、不受任何本機 Proxy 或除錯埠限制
        - 支援全 Frame / iframe 深度遞迴穿透
        - 登入頁：自動填入帳密並全自動四重點擊「登入」
        - 登入後：自動偵測並秒級跳轉至 http://car.tccf.gov.tw/car/pages/mileage_nw.php (里程登記)
        - 里程頁：自動點擊「添加新記錄」圖片按鈕、自動選取目標車牌 (BVX-0039)、自動選取今日日期，並顯示完成綠色橫幅
        - 全時段：常駐每 3 分鐘背景心跳保活
        """
        ext_dir = os.path.join(self.profile_dir, "car_helper_extension")
        try:
            os.makedirs(ext_dir, exist_ok=True)
            manifest_path = os.path.join(ext_dir, "manifest.json")
            content_path = os.path.join(ext_dir, "content.js")

            manifest_data = {
                "manifest_version": 3,
                "name": "車輛系統自動登記小幫手",
                "version": "1.4",
                "description": "救護影像抓取小幫手專用：車輛系統自動登入與里程登記輔助",
                "content_scripts": [
                    {
                        "matches": ["http://*/*"],
                        "js": ["content.js"],
                        "run_at": "document_end",
                        "all_frames": True,
                    }
                ],
            }
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest_data, f, ensure_ascii=False, indent=2)

            pref_clean = (preferred_account or self.active_preferred_account or "").strip()
            if pref_clean.startswith("⚡") or pref_clean.upper() == "AUTO":
                pref_clean = ""

            direct_user = ""
            direct_pass = ""
            if pref_clean and pref_clean in self._decrypted_car_creds:
                direct_user = pref_clean
                direct_pass = self._decrypted_car_creds[pref_clean]
            elif self._decrypted_car_creds:
                direct_user = list(self._decrypted_car_creds.keys())[0]
                direct_pass = self._decrypted_car_creds[direct_user]

            mileage_url = get_car_mileage_url(target_url)
            cfg_obj = {
                "target_url": target_url or DEFAULT_CAR_SYSTEM_URL,
                "mileage_url": mileage_url,
                "target_license": (target_license or DEFAULT_CAR_TARGET_LICENSE).strip(),
                "preferred_account": pref_clean,
                "direct_user": direct_user,
                "direct_pass": direct_pass,
                "auto_submit": bool(auto_submit),
                "auto_mileage": bool(auto_mileage),
            }
            cfg_json = json.dumps(cfg_obj, ensure_ascii=False)

            content_js_code = f"""(function() {{
    var host = (window.location.hostname || '').toLowerCase();
    var href = (window.location.href || '').toLowerCase();
    if (
        host.indexOf('car.tccf.gov.tw') === -1 &&
        host.indexOf('car.fire.taichung.gov.tw') === -1 &&
        href.indexOf('/car/') === -1
    ) {{
        return;
    }}

    var CFG = {cfg_json};

    function getAllFrames(w) {{
        var res = [];
        function rec(win) {{
            try {{
                if (win && win.document && res.indexOf(win) === -1) {{
                    res.push(win);
                    for (var i = 0; i < win.frames.length; i++) {{
                        var sub = win.frames[i];
                        if (sub && sub !== win) rec(sub);
                    }}
                }}
            }} catch(e) {{}}
        }}
        rec(w || window);
        return res;
    }}

    function checkPageStage() {{
        var frames = getAllFrames(window);
        for (var i = 0; i < frames.length; i++) {{
            try {{
                var fHref = (frames[i].location.href || '').toLowerCase();
                if (fHref.indexOf('check_login') !== -1) return 'TRANSITION';
            }} catch(e) {{}}
        }}
        for (var j = 0; j < frames.length; j++) {{
            try {{
                var d = frames[j].document;
                var pw = d.querySelector('input[name="rt_admin_password"], input[type="password"]');
                if (pw && (pw.offsetParent !== null || pw.offsetWidth > 0 || pw.offsetHeight > 0)) return 'LOGIN';
                var fHref2 = (frames[j].location.href || '').toLowerCase();
                if (fHref2.indexOf('login.php') !== -1 || fHref2.indexOf('/account/login') !== -1) return 'LOGIN';
            }} catch(e) {{}}
        }}
        return 'SYSTEM';
    }}

    function runAutomation() {{
        var stage = checkPageStage();
        if (stage === 'TRANSITION') {{
            return;
        }}
        if (stage === 'LOGIN') {{
            handleLoginPage();
        }} else {{
            handleSystemPage();
        }}
    }}

    function handleLoginPage() {{
        var frames = getAllFrames(window);
        var u = null, p = null, subBtn = null, targetDoc = document, targetWin = window;
        for (var i = 0; i < frames.length; i++) {{
            try {{
                var d = frames[i].document;
                var pw = d.querySelector('input[name="rt_admin_password"], input[type="password"]');
                if (pw) {{
                    p = pw;
                    u = d.querySelector('input[name="rt_admin_username"], input[type="text"]');
                    subBtn = d.querySelector('input[name="Submit"], input[type="submit"][value*="登入"], input.ConWebButton, button[type="submit"]');
                    targetDoc = d;
                    targetWin = frames[i];
                    break;
                }}
            }} catch(e) {{}}
        }}
        if (!u && !p) return;

        if (CFG.direct_user && CFG.direct_pass) {{
            if (u && !u.value) {{
                u.value = CFG.direct_user;
                u.dispatchEvent(new Event('input', {{ bubbles: true }}));
                u.dispatchEvent(new Event('change', {{ bubbles: true }}));
            }}
            if (p && !p.value) {{
                p.value = CFG.direct_pass;
                p.dispatchEvent(new Event('input', {{ bubbles: true }}));
                p.dispatchEvent(new Event('change', {{ bubbles: true }}));
            }}
        }}

        var bar = window.document.getElementById('__car_ext_banner');
        if (!bar) {{
            bar = window.document.createElement('div');
            bar.id = '__car_ext_banner';
            bar.style.cssText = 'position:fixed;top:0;left:0;right:0;background:linear-gradient(90deg,#0f172a,#1e3a8a);color:#f8fafc;padding:9px 16px;font-size:13px;z-index:999999;box-shadow:0 3px 12px rgba(0,0,0,0.3);font-family:sans-serif;display:flex;align-items:center;justify-content:space-between;border-bottom:2px solid #38bdf8;';
            (window.document.body || window.document.documentElement).appendChild(bar);
            if (window.document.body) window.document.body.style.paddingTop = '48px';
        }}

        if (CFG.auto_submit) {{
            bar.innerHTML = '<div style="display:flex;align-items:center;gap:8px;font-weight:bold;"><span style="font-size:16px">⏳</span> <span>【救護小幫手】正在等待帳密就緒並自動登入...</span></div><div style="font-size:12px;color:#7dd3fc">登入後自動切換至里程登記</div>';

            if (window.__car_ext_sub_timer) return;
            var attempts = 0;
            window.__car_ext_sub_timer = setInterval(function() {{
                attempts++;
                var uVal = u ? (u.value || '').trim() : '';
                var pVal = p ? (p.value || '').trim() : '';
                var uAuto = false, pAuto = false;
                try {{ uAuto = Boolean(u && u.matches && u.matches(':-webkit-autofill')); }} catch(e) {{}}
                try {{ pAuto = Boolean(p && p.matches && p.matches(':-webkit-autofill')); }} catch(e) {{}}
                var isReady = Boolean((uVal || uAuto) && (pVal || pAuto));

                if (isReady) {{
                    clearInterval(window.__car_ext_sub_timer);
                    window.__car_ext_sub_timer = null;
                    submitLoginForm(u, p, subBtn, targetDoc, targetWin);
                }} else if (attempts >= 20) {{
                    clearInterval(window.__car_ext_sub_timer);
                    window.__car_ext_sub_timer = null;
                    bar.innerHTML = '<div style="display:flex;align-items:center;gap:8px;font-weight:bold;"><span style="font-size:16px">🔑</span> <span>【救護小幫手】請先選取或填入帳密，再點擊「登入」（登入後會自動導向里程登記）。</span></div>';
                }}
            }}, 350);
        }} else {{
            bar.innerHTML = '<div style="display:flex;align-items:center;gap:8px;font-weight:bold;"><span style="font-size:16px">🔑</span> <span>【救護小幫手】車輛系統已就緒，請點擊「登入」完成登入。</span></div>';
        }}
    }}

    function submitLoginForm(u, p, subBtn, targetDoc, targetWin) {{
        var nowTs = Date.now();
        var lastTop = window.__car_login_submit_lock_ts || 0;
        var lastFrm = (targetWin && targetWin.__car_login_submit_lock_ts) || 0;
        if (nowTs - Math.max(lastTop, lastFrm) < 4000) {{
            return;
        }}
        window.__car_login_submit_lock_ts = nowTs;
        if (targetWin) targetWin.__car_login_submit_lock_ts = nowTs;

        var bar = window.document.getElementById('__car_ext_banner');
        if (bar) {{
            bar.innerHTML = '<div style="display:flex;align-items:center;gap:8px;font-weight:bold;"><span style="font-size:16px">🚀</span> <span>【救護小幫手】帳密已就緒！正在送出登入，請稍候...</span></div><div style="font-size:12px;color:#7dd3fc">登入後自動切換至里程登記</div>';
        }}

        try {{
            if (targetWin && typeof targetWin.rememberMe === 'function') targetWin.rememberMe();
            else if (typeof rememberMe === 'function') rememberMe();
        }} catch(e){{}}

        if (subBtn) {{
            try {{ subBtn.focus(); }} catch(e){{}}
            try {{ subBtn.click(); }} catch(e){{}}
        }} else {{
            try {{
                var f = targetDoc.forms['frmLogin'] || targetDoc.querySelector('form');
                if (f) {{
                    var ok = true;
                    if (targetWin && typeof targetWin.onSubmitCheck === 'function') ok = targetWin.onSubmitCheck(f, false, false);
                    else if (typeof onSubmitCheck === 'function') ok = onSubmitCheck(f, false, false);
                    if (ok !== false) {{
                        HTMLFormElement.prototype.submit.call(f);
                    }}
                }}
            }} catch(e){{}}
        }}
    }}

    function handleSystemPage() {{
        initHeartbeatPill();

        if (CFG.auto_mileage) {{
            var frames = getAllFrames(window);
            var onMileage = false;
            for (var j = 0; j < frames.length; j++) {{
                try {{
                    var fUrl = (frames[j].location.href || '').toLowerCase();
                    if (fUrl.indexOf('check_login') !== -1 || fUrl.indexOf('login.php') !== -1) return;
                    if (fUrl.indexOf('mileage_nw.php') !== -1) {{
                        onMileage = true;
                        break;
                    }}
                }} catch(e) {{}}
            }}

            if (!onMileage) {{
                var nowNav = Date.now();
                if (window.__car_nav_mileage_ts && nowNav - window.__car_nav_mileage_ts < 3000) {{
                    return;
                }}
                window.__car_nav_mileage_ts = nowNav;
                var navBar = window.document.getElementById('__car_ext_nav_bar');
                if (!navBar) {{
                    navBar = window.document.createElement('div');
                    navBar.id = '__car_ext_nav_bar';
                    navBar.style.cssText = 'position:fixed;top:0;left:0;right:0;background:linear-gradient(90deg,#047857,#065f46);color:#ffffff;padding:10px 18px;font-size:14px;font-weight:bold;z-index:999999;box-shadow:0 4px 14px rgba(0,0,0,0.3);display:flex;align-items:center;justify-content:space-between;border-bottom:2px solid #34d399;font-family:sans-serif;';
                    navBar.innerHTML = '<span>🚑 <strong>【救護小幫手】登入成功！正在為您自動導向【里程登記】...</strong></span>';
                    (window.document.body || window.document.documentElement).appendChild(navBar);
                    if (window.document.body) window.document.body.style.paddingTop = '50px';
                }}
                setTimeout(function() {{
                    window.location.href = CFG.mileage_url || (window.location.origin + '/car/pages/mileage_nw.php');
                }}, 250);
                return;
            }}

            handleMileagePage();
        }}
    }}

    function handleMileagePage() {{
        var mAttempts = 0;
        function checkMileageForm() {{
            mAttempts++;
            var allF = getAllFrames(window);
            var selLic = null, selDate = null, addBtn = null, actWin = window;

            for (var k = 0; k < allF.length; k++) {{
                try {{
                    var fd = allF[k].document;
                    if (!selLic) selLic = fd.getElementById('ryylicense') || fd.querySelector('select[name="ryylicense"]');
                    if (!selDate) selDate = fd.getElementById('ryymdate') || fd.querySelector('select[name="ryymdate"]');
                    if (!addBtn) {{
                        var b = fd.querySelector('img[title*="添加新記錄"], img[alt*="添加新記錄"], img[src*="add.gif"], img[onclick*="dr5__doPostBack"][onclick*="add"], a[onclick*="dr5__doPostBack"][onclick*="add"]');
                        if (b) addBtn = {{ el: b, win: allF[k] }};
                        else if (typeof allF[k].dr5__doPostBack === 'function') addBtn = {{ fn: allF[k].dr5__doPostBack, win: allF[k] }};
                    }}
                    if (selLic) actWin = allF[k];
                }} catch(e) {{}}
            }}

            if (!selLic) {{
                if (addBtn) {{
                    var nowT = Date.now();
                    if (!addBtn.win.__last_add_t || nowT - addBtn.win.__last_add_t > 2500) {{
                        addBtn.win.__last_add_t = nowT;
                        if (addBtn.el) {{
                            try {{ addBtn.el.focus(); }} catch(e) {{}}
                            try {{ addBtn.el.click(); }} catch(e) {{}}
                        }} else if (addBtn.fn) {{
                            try {{ addBtn.fn('add', '-1', ''); }} catch(e) {{}}
                        }}
                    }}
                }}
                if (mAttempts < 35) {{
                    setTimeout(checkMileageForm, 350);
                }}
                return;
            }}

            var targetLic = (CFG.target_license || 'BVX-0039').trim();
            var matchedLic = '';
            for (var i = 0; i < selLic.options.length; i++) {{
                var opt = selLic.options[i];
                if (opt.value.indexOf(targetLic) !== -1 || opt.text.indexOf(targetLic) !== -1) {{
                    selLic.selectedIndex = i;
                    selLic.value = opt.value;
                    matchedLic = opt.value;
                    break;
                }}
            }}
            if (matchedLic) {{
                try {{ selLic.dispatchEvent(new Event('input', {{ bubbles: true }})); }} catch(e) {{}}
                try {{ selLic.dispatchEvent(new Event('change', {{ bubbles: true }})); }} catch(e) {{}}
                if (typeof selLic.onchange === 'function') {{
                    try {{ selLic.onchange(); }} catch(e) {{}}
                }}
                if (typeof actWin.search_car_mileage === 'function') {{
                    try {{ actWin.search_car_mileage(matchedLic); }} catch(e) {{}}
                }} else if (typeof window.search_car_mileage === 'function') {{
                    try {{ window.search_car_mileage(matchedLic); }} catch(e) {{}}
                }}
            }}

            var now = new Date();
            var yyyy = now.getFullYear();
            var mm = String(now.getMonth() + 1).padStart(2, '0');
            var dd = String(now.getDate()).padStart(2, '0');
            var todayStr = yyyy + '-' + mm + '-' + dd;
            var matchedDate = '';
            if (selDate) {{
                for (var j = 0; j < selDate.options.length; j++) {{
                    var dOpt = selDate.options[j];
                    if (dOpt.value === todayStr || dOpt.text.indexOf(todayStr) !== -1) {{
                        selDate.selectedIndex = j;
                        selDate.value = dOpt.value;
                        matchedDate = dOpt.value;
                        break;
                    }}
                }}
                if (!matchedDate && selDate.options.length > 1) {{
                    for (var k = 0; k < selDate.options.length; k++) {{
                        if (selDate.options[k].value) {{
                            selDate.selectedIndex = k;
                            selDate.value = selDate.options[k].value;
                            matchedDate = selDate.options[k].value;
                            break;
                        }}
                    }}
                }}
                try {{ selDate.dispatchEvent(new Event('input', {{ bubbles: true }})); }} catch(e) {{}}
                try {{ selDate.dispatchEvent(new Event('change', {{ bubbles: true }})); }} catch(e) {{}}
                if (typeof selDate.onchange === 'function') {{
                    try {{ selDate.onchange(); }} catch(e) {{}}
                }}
            }}

            var topDoc = window.document;
            var doneBar = topDoc.getElementById('__car_ext_done_bar');
            if (!doneBar) {{
                doneBar = topDoc.createElement('div');
                doneBar.id = '__car_ext_done_bar';
                doneBar.style.cssText = 'position:fixed;top:0;left:0;right:0;background:linear-gradient(90deg,#065f46,#047857);color:#ffffff;padding:12px 20px;font-size:14px;font-weight:bold;z-index:999999;box-shadow:0 4px 16px rgba(0,0,0,0.3);display:flex;align-items:center;justify-content:space-between;border-bottom:3px solid #34d399;font-family:sans-serif;';
                doneBar.innerHTML = '<span>🚑 <strong>【救護小幫手】已為您自動添加新記錄 ＆ 預選車牌【' + (matchedLic || targetLic) + '】與今日日期【' + (matchedDate || todayStr) + '】！</strong><br><span style="font-size:12px;font-weight:normal;color:#a7f3d0">👉 請接續輸入今日出勤里程並按儲存。</span></span><button onclick="this.parentElement.remove();" style="background:#059669;color:#fff;border:1px solid #6ee7b7;border-radius:4px;padding:4px 10px;cursor:pointer;font-weight:bold;">關閉提示 ✕</button>';
                (topDoc.body || topDoc.documentElement).appendChild(doneBar);
                if (topDoc.body) topDoc.body.style.paddingTop = '58px';
            }}
        }}
        checkMileageForm();
    }}

    function initHeartbeatPill() {{
        function updateBadge(timeStr, color) {{
            var b = window.document.getElementById('__car_heartbeat_pill');
            if (!b) {{
                b = window.document.createElement('div');
                b.id = '__car_heartbeat_pill';
                b.style.cssText = 'position:fixed;bottom:14px;right:14px;background:rgba(15,23,42,0.88);color:#f8fafc;padding:6px 14px;border-radius:20px;font-size:12px;z-index:999999;box-shadow:0 4px 14px rgba(0,0,0,0.3);font-family:sans-serif;display:flex;align-items:center;gap:8px;backdrop-filter:blur(4px);border:1px solid rgba(255,255,255,0.18);cursor:pointer;user-select:none;';
                b.title = '車輛系統防斷線背景心跳運作中 (每 3 分鐘自動維持連線)';
                (window.document.body || window.document.documentElement).appendChild(b);
            }}
            b.innerHTML = '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + (color || '#10b981') + ';box-shadow:0 0 6px ' + (color || '#10b981') + '"></span> <strong>車輛系統保活中</strong> <span style="color:#94a3b8;font-size:11px">' + timeStr + '</span>';
        }}

        function doPing() {{
            fetch(window.location.href, {{ method: 'GET', cache: 'no-store', credentials: 'include' }})
                .then(function() {{
                    updateBadge(new Date().toLocaleTimeString(), '#10b981');
                }})
                .catch(function() {{
                    fetch(window.location.origin + '/car/', {{ method: 'HEAD', cache: 'no-store', credentials: 'include' }})
                        .then(function() {{ updateBadge(new Date().toLocaleTimeString() + ' (備援)', '#38bdf8'); }})
                        .catch(function(){{}});
                }});
        }}

        if (!window.__car_ext_hb_timer) {{
            window.__car_ext_hb_timer = setInterval(doPing, 180000);
            doPing();
        }}
    }}

    if (document.readyState === 'loading') {{
        document.addEventListener('DOMContentLoaded', runAutomation);
    }} else {{
        runAutomation();
    }}
}})();
"""
            with open(content_path, "w", encoding="utf-8") as f:
                f.write(content_js_code)
            return ext_dir
        except Exception as e:
            print("ensure_car_helper_extension warning:", e)
            return ""

    def __init__(self, port: int = 9223, profile_dir: str = None, initial_url: str = DEFAULT_CAR_SYSTEM_URL):
        self.port = port
        self.profile_dir = profile_dir or get_car_local_secure_profile_root()
        self.clean_profile_dir = os.path.join(self.profile_dir, "clean_mode")
        self.user_profile_dir = os.path.join(self.profile_dir, "user_profiles")
        self.active_mode_file = os.path.join(self.profile_dir, "active_profile_mode.txt")
        self.active_acct_file = os.path.join(self.profile_dir, "active_preferred_account.txt")
        self.initial_url = initial_url
        self.process = None
        self.active_profile_subdir = "Default"
        self.active_profile_mode = self._read_saved_active_mode()
        self.active_preferred_account = self._read_saved_preferred_account()
        self._decrypted_car_creds = {}

    def _read_saved_active_mode(self) -> str:
        try:
            if os.path.exists(self.active_mode_file):
                with open(self.active_mode_file, "r", encoding="utf-8") as f:
                    return f.read().strip() or "NONE"
        except Exception:
            pass
        return "NONE"

    def _write_saved_active_mode(self, mode: str):
        self.active_profile_mode = mode or "NONE"
        try:
            os.makedirs(self.profile_dir, exist_ok=True)
            with open(self.active_mode_file, "w", encoding="utf-8") as f:
                f.write(self.active_profile_mode)
        except Exception:
            pass

    def _read_saved_preferred_account(self) -> str:
        try:
            if os.path.exists(self.active_acct_file):
                with open(self.active_acct_file, "r", encoding="utf-8") as f:
                    return f.read().strip()
        except Exception:
            pass
        return ""

    def _write_saved_preferred_account(self, acct: str):
        self.active_preferred_account = (acct or "").strip()
        try:
            os.makedirs(self.profile_dir, exist_ok=True)
            with open(self.active_acct_file, "w", encoding="utf-8") as f:
                f.write(self.active_preferred_account)
        except Exception:
            pass

    def get_active_profile_mode(self) -> str:
        return self.active_profile_mode or self._read_saved_active_mode()

    def is_running(self) -> bool:
        try:
            with self._local_http_open(f"http://127.0.0.1:{self.port}/json", timeout=1.2) as resp:
                return resp.status == 200
        except Exception:
            return False

    def get_tabs(self) -> list:
        try:
            with self._local_http_open(f"http://127.0.0.1:{self.port}/json", timeout=2.0) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return []

    def get_page_tabs(self) -> list:
        valid = []
        for t in self.get_tabs():
            if t.get("type") != "page":
                continue
            u = t.get("url", "")
            if any(u.startswith(prefix) for prefix in ("chrome://", "chrome-extension://", "devtools://", "edge://")):
                continue
            valid.append(t)
        return valid

    def get_car_tabs(self, target_url: str = DEFAULT_CAR_SYSTEM_URL) -> list:
        """取得所有屬於車輛管理系統或目標網址的分頁"""
        target_host = ""
        try:
            target_host = (urllib.parse.urlparse(target_url or DEFAULT_CAR_SYSTEM_URL).hostname or "").lower()
        except Exception:
            pass

        matched = []
        for t in self.get_page_tabs():
            u = t.get("url", "").lower()
            if (
                (target_host and target_host in u)
                or "car.tccf.gov.tw" in u
                or "car.fire.taichung.gov.tw" in u
                or "fems.taichung.gov.tw" in u
                or "/car/" in u
            ):
                matched.append(t)
        return matched

    def close_browser(self):
        if self.is_running():
            tabs = self.get_tabs()
            for t in tabs:
                ws = t.get("webSocketDebuggerUrl")
                if ws:
                    try:
                        self._ws_call(ws, "Browser.close", {}, timeout=1.5)
                        break
                    except Exception:
                        pass
            for t in tabs:
                tid = t.get("id")
                if tid:
                    try:
                        self._local_http_open(f"http://127.0.0.1:{self.port}/json/close/{tid}", timeout=1.0)
                    except Exception:
                        pass
        if self.process:
            try:
                self.process.terminate()
            except Exception:
                pass
            self.process = None
        for _ in range(12):
            if not self.is_running():
                break
            time.sleep(0.2)

    @staticmethod
    def _win_shared_copy(src: str, dst: str) -> bool:
        """支援 Windows 下當日常 Chrome 開啟鎖定資料庫時，以共享模式安全讀取複製"""
        if not os.path.exists(src):
            return False
        try:
            shutil.copy2(src, dst)
            return True
        except Exception:
            pass
        if sys.platform.startswith("win"):
            try:
                import ctypes
                from ctypes import wintypes
                kernel32 = ctypes.windll.kernel32
                GENERIC_READ = 0x80000000
                FILE_SHARE_ALL = 0x00000001 | 0x00000002 | 0x00000004
                OPEN_EXISTING = 3
                FILE_ATTRIBUTE_NORMAL = 0x80
                INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

                h_file = kernel32.CreateFileW(
                    wintypes.LPCWSTR(src),
                    GENERIC_READ,
                    FILE_SHARE_ALL,
                    None,
                    OPEN_EXISTING,
                    FILE_ATTRIBUTE_NORMAL,
                    None,
                )
                if h_file != INVALID_HANDLE_VALUE and h_file != 0:
                    try:
                        chunks = []
                        buf = ctypes.create_string_buffer(65536)
                        bytes_read = wintypes.DWORD(0)
                        while kernel32.ReadFile(h_file, buf, 65536, ctypes.byref(bytes_read), None):
                            if bytes_read.value == 0:
                                break
                            chunks.append(buf.raw[:bytes_read.value])
                        with open(dst, "wb") as wf:
                            for c in chunks:
                                wf.write(c)
                        return True
                    finally:
                        kernel32.CloseHandle(h_file)
            except Exception:
                pass
        return False

    def get_isolated_user_data_dir(self, prof_subdir: str, is_clean_mode: bool = False) -> str:
        if is_clean_mode or not prof_subdir or prof_subdir in ("NONE", "CLEAN_SANDBOX", "CAR_Clean_Sandbox"):
            return self.clean_profile_dir
        safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", prof_subdir)
        return os.path.join(self.user_profile_dir, f"isolated_{safe_name}")

    @staticmethod
    def _win_dpapi_unprotect(raw_bytes: bytes) -> bytes:
        """Windows 原生 CryptUnprotectData 解密 (100% 內建 ctypes，零第三方套件)"""
        if not sys.platform.startswith("win") or not raw_bytes:
            return b""
        try:
            import ctypes
            from ctypes import wintypes

            class DATA_BLOB(ctypes.Structure):
                _fields_ = [
                    ("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char)),
                ]

            crypt32 = ctypes.windll.crypt32
            kernel32 = ctypes.windll.kernel32
            buf = ctypes.create_string_buffer(raw_bytes, len(raw_bytes))
            blob_in = DATA_BLOB(len(raw_bytes), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
            blob_out = DATA_BLOB()
            if crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
                try:
                    return ctypes.string_at(blob_out.pbData, blob_out.cbData)
                finally:
                    kernel32.LocalFree(blob_out.pbData)
        except Exception:
            pass
        return b""

    @classmethod
    def _win_decrypt_chrome_blob(cls, encrypted_value: bytes, master_key: bytes) -> str:
        """Windows 原生 bcrypt.dll AES-256-GCM / DPAPI 解密 Chrome Login Data 密碼 (100% 標準庫)"""
        if not sys.platform.startswith("win") or not encrypted_value:
            return ""
        try:
            if encrypted_value[:3] in (b"v10", b"v11") and master_key and len(encrypted_value) > 31:
                import ctypes
                from ctypes import wintypes

                class BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO(ctypes.Structure):
                    _fields_ = [
                        ("cbSize", wintypes.ULONG),
                        ("dwInfoVersion", wintypes.ULONG),
                        ("pbNonce", ctypes.c_void_p),
                        ("cbNonce", wintypes.ULONG),
                        ("pbAuthData", ctypes.c_void_p),
                        ("cbAuthData", wintypes.ULONG),
                        ("pbTag", ctypes.c_void_p),
                        ("cbTag", wintypes.ULONG),
                        ("pbMacContext", ctypes.c_void_p),
                        ("cbMacContext", wintypes.ULONG),
                        ("cbAAD", wintypes.ULONG),
                        ("cbData", ctypes.c_ulonglong),
                        ("dwFlags", wintypes.ULONG),
                    ]

                nonce = encrypted_value[3:15]
                ciphertext = encrypted_value[15:-16]
                tag = encrypted_value[-16:]

                bcrypt = ctypes.windll.bcrypt
                h_alg = ctypes.c_void_p()
                if bcrypt.BCryptOpenAlgorithmProvider(ctypes.byref(h_alg), ctypes.c_wchar_p("AES"), None, 0) != 0:
                    return ""
                try:
                    mode_str = ctypes.create_unicode_buffer("ChainingModeGCM")
                    if bcrypt.BCryptSetProperty(
                        h_alg,
                        ctypes.c_wchar_p("ChainingMode"),
                        ctypes.cast(mode_str, ctypes.c_void_p),
                         ctypes.sizeof(mode_str),
                        0,
                    ) != 0:
                        return ""
                    h_key = ctypes.c_void_p()
                    key_buf = ctypes.create_string_buffer(master_key, len(master_key))
                    if bcrypt.BCryptGenerateSymmetricKey(
                        h_alg, ctypes.byref(h_key), None, 0, key_buf, len(master_key), 0
                    ) != 0:
                        return ""
                    try:
                        nonce_buf = ctypes.create_string_buffer(nonce, len(nonce))
                        tag_buf = ctypes.create_string_buffer(tag, len(tag))
                        auth_info = BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO()
                        auth_info.cbSize = ctypes.sizeof(BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO)
                        auth_info.dwInfoVersion = 1
                        auth_info.pbNonce = ctypes.cast(nonce_buf, ctypes.c_void_p)
                        auth_info.cbNonce = len(nonce)
                        auth_info.pbTag = ctypes.cast(tag_buf, ctypes.c_void_p)
                        auth_info.cbTag = len(tag)

                        ct_len = len(ciphertext)
                        ct_buf = ctypes.create_string_buffer(ciphertext, ct_len)
                        pt_buf = ctypes.create_string_buffer(max(ct_len, 1))
                        cb_result = wintypes.ULONG(0)
                        status = bcrypt.BCryptDecrypt(
                            h_key,
                            ct_buf,
                            ct_len,
                            ctypes.byref(auth_info),
                            None,
                            0,
                            pt_buf,
                            ct_len,
                            ctypes.byref(cb_result),
                            0,
                        )
                        if status == 0:
                            return pt_buf.raw[:cb_result.value].decode("utf-8", errors="ignore")
                    finally:
                        bcrypt.BCryptDestroyKey(h_key)
                finally:
                    bcrypt.BCryptCloseAlgorithmProvider(h_alg, 0)
            elif not encrypted_value.startswith(b"v1") and not encrypted_value.startswith(b"v2"):
                raw = cls._win_dpapi_unprotect(encrypted_value)
                if raw:
                    return raw.decode("utf-8", errors="ignore")
        except Exception:
            pass
        return ""

    def get_saved_car_accounts(self, prof_subdir: str = None) -> list:
        """
        掃描指定 Chrome 設定檔 (Login Data 與 Login Data For Account) 內已儲存的所有車輛系統帳號清單
        （依最後使用時間排序，並於支援環境快取解密密碼供瞬間切換）
        """
        prof = resolve_chrome_profile(prof_subdir) if prof_subdir else (
            self.active_profile_subdir if self.active_profile_subdir not in ("Default", "CAR_Clean_Sandbox")
            else find_chrome_profile_for_user()
        )
        if not prof or prof in ("NONE", "CLEAN_SANDBOX", "CAR_Clean_Sandbox"):
            return []

        sys_ud = get_chrome_user_data_dir()
        src_prof = os.path.join(sys_ud, prof)
        base_ud_dir = self.get_isolated_user_data_dir(prof, is_clean_mode=False)
        dst_prof = os.path.join(base_ud_dir, prof)

        master_key = b""
        if sys.platform.startswith("win"):
            for ls_path in (os.path.join(sys_ud, "Local State"), os.path.join(base_ud_dir, "Local State")):
                if os.path.exists(ls_path):
                    try:
                        with open(ls_path, "r", encoding="utf-8", errors="ignore") as f:
                            ls_obj = json.load(f)
                        enc_key_b64 = ls_obj.get("os_crypt", {}).get("encrypted_key", "")
                        if enc_key_b64:
                            enc_key = base64.b64decode(enc_key_b64)
                            if enc_key.startswith(b"DPAPI"):
                                master_key = self._win_dpapi_unprotect(enc_key[5:])
                                if master_key:
                                    break
                    except Exception:
                        pass

        db_candidates = [
            os.path.join(dst_prof, "Login Data"),
            os.path.join(dst_prof, "Login Data For Account"),
            os.path.join(src_prof, "Login Data For Account"),
            os.path.join(src_prof, "Login Data"),
        ]

        acct_meta = {}
        tmp_dir = None
        try:
            tmp_dir = tempfile.mkdtemp(prefix="car_acct_scan_")
            for idx, db_path in enumerate(db_candidates):
                if not os.path.exists(db_path) or os.path.getsize(db_path) < 4096:
                    continue
                tmp_db = os.path.join(tmp_dir, f"scan_{idx}.db")
                if not self._win_shared_copy(db_path, tmp_db):
                    continue
                conn = None
                try:
                    conn = sqlite3.connect(tmp_db, timeout=2.0)
                    rows = conn.execute(
                        """
                        SELECT username_value, password_value, date_last_used, times_used
                        FROM logins
                        WHERE (
                            origin_url LIKE '%car.tccf.gov.tw%'
                            OR origin_url LIKE '%car.fire.taichung.gov.tw%'
                            OR action_url LIKE '%car/check_login.php%'
                            OR username_element = 'rt_admin_username'
                        )
                        AND username_value != ''
                        AND blacklisted_by_user = 0
                        ORDER BY date_last_used DESC, times_used DESC
                        """
                    ).fetchall()
                    for u_val, pw_blob, d_used, t_used in rows:
                        u_clean = (u_val or "").strip()
                        if not u_clean:
                            continue
                        score = (int(d_used or 0), int(t_used or 0))
                        if u_clean not in acct_meta or score > acct_meta[u_clean]:
                            acct_meta[u_clean] = score
                        if sys.platform.startswith("win") and pw_blob and u_clean not in self._decrypted_car_creds:
                            dec_pw = self._win_decrypt_chrome_blob(bytes(pw_blob), master_key)
                            if dec_pw:
                                self._decrypted_car_creds[u_clean] = dec_pw
                except Exception:
                    pass
                finally:
                    if conn:
                        try:
                            conn.close()
                        except Exception:
                            pass
        except Exception:
            pass
        finally:
            if tmp_dir and os.path.exists(tmp_dir):
                shutil.rmtree(tmp_dir, ignore_errors=True)

        sorted_accts = [k for k, _ in sorted(acct_meta.items(), key=lambda item: item[1], reverse=True)]
        return sorted_accts

    def sync_from_system_profile(self, prof_subdir: str, preferred_account: str = "", target_url: str = ""):
        """同步系統指定 Chrome 設定檔之雲端書籤、雲端密碼庫與金鑰至受控隔離容器"""
        if not prof_subdir or prof_subdir in ("NONE", "CLEAN_SANDBOX", "CAR_Clean_Sandbox"):
            return
        try:
            sys_ud = get_chrome_user_data_dir()
            src_prof = os.path.join(sys_ud, prof_subdir)
            if not os.path.exists(src_prof):
                return

            base_ud_dir = self.get_isolated_user_data_dir(prof_subdir, is_clean_mode=False)
            os.makedirs(base_ud_dir, exist_ok=True)
            dst_prof = os.path.join(base_ud_dir, prof_subdir)
            os.makedirs(dst_prof, exist_ok=True)

            first_run_sentinel = os.path.join(base_ud_dir, "First Run")
            if not os.path.exists(first_run_sentinel):
                try:
                    with open(first_run_sentinel, "w", encoding="utf-8") as frf:
                        frf.write("")
                except Exception:
                    pass

            src_ls = os.path.join(sys_ud, "Local State")
            dst_ls = os.path.join(base_ud_dir, "Local State")
            if os.path.exists(src_ls):
                if not os.path.exists(dst_ls):
                    self._win_shared_copy(src_ls, dst_ls)
                try:
                    with open(src_ls, "r", encoding="utf-8", errors="ignore") as sf:
                        s_ls_data = json.load(sf)
                    d_ls_data = {}
                    if os.path.exists(dst_ls):
                        try:
                            with open(dst_ls, "r", encoding="utf-8", errors="ignore") as df:
                                d_ls_data = json.load(df)
                        except Exception:
                            d_ls_data = {}
                    if "os_crypt" in s_ls_data:
                        d_ls_data["os_crypt"] = s_ls_data["os_crypt"]
                    s_cache = s_ls_data.get("profile", {}).get("info_cache", {})
                    prof_section = d_ls_data.setdefault("profile", {})
                    if prof_subdir in s_cache:
                        prof_section["info_cache"] = {prof_subdir: s_cache[prof_subdir]}
                    elif s_cache:
                        prof_section.setdefault("info_cache", {}).update(s_cache)
                    prof_section["last_used"] = prof_subdir
                    prof_section["last_active_profiles"] = [prof_subdir]
                    prof_section["show_picker_on_startup"] = False
                    with open(dst_ls, "w", encoding="utf-8") as wf:
                        json.dump(d_ls_data, wf, ensure_ascii=False)
                except Exception:
                    pass

            marker_file = os.path.join(dst_prof, ".car_synced_v1")
            first_sync = not os.path.exists(marker_file)

            # 注意：不可複製 -journal / -wal 暫存鎖定檔，避免覆寫合併後引發 SQLite 回滾或鎖表
            init_once_files = [
                "Preferences",
                "Login Data",
                "Login Data For Account",
                "Cookies",
                "Web Data",
                "Account Web Data",
                "Favicons",
                "Google Profile Picture.png",
            ]
            for fname in init_once_files:
                s_file = os.path.join(src_prof, fname)
                d_file = os.path.join(dst_prof, fname)
                if os.path.exists(s_file) and (first_sync or not os.path.exists(d_file)):
                    self._win_shared_copy(s_file, d_file)

            if first_sync:
                sec_pref = os.path.join(dst_prof, "Secure Preferences")
                if os.path.exists(sec_pref):
                    try:
                        os.remove(sec_pref)
                    except Exception:
                        pass

            src_net = os.path.join(src_prof, "Network")
            dst_net = os.path.join(dst_prof, "Network")
            if os.path.exists(src_net):
                os.makedirs(dst_net, exist_ok=True)
                for n_fname in ("Cookies", "TransportSecurity", "Trust Tokens"):
                    sn_file = os.path.join(src_net, n_fname)
                    dn_file = os.path.join(dst_net, n_fname)
                    if os.path.exists(sn_file) and (first_sync or not os.path.exists(dn_file)):
                        self._win_shared_copy(sn_file, dn_file)

            for bm_name in ("Bookmarks", "AccountBookmarks"):
                s_bm = os.path.join(src_prof, bm_name)
                d_bm = os.path.join(dst_prof, bm_name)
                if os.path.exists(s_bm) and (first_sync or not os.path.exists(d_bm)):
                    self._win_shared_copy(s_bm, d_bm)

            if first_sync:
                try:
                    with open(marker_file, "w", encoding="utf-8") as mf:
                        mf.write(prof_subdir)
                except Exception:
                    pass

            self._merge_login_databases(
                src_prof,
                dst_prof,
                preferred_account=preferred_account,
                target_url=target_url,
            )
        except Exception as e:
            print("sync_from_system_profile warning:", e)

    def _merge_login_databases(self, src_prof: str, dst_prof: str, preferred_account: str = "", target_url: str = ""):
        """
        將系統設定檔的 Login Data 與 Login Data For Account 完整合併至受控目錄的 Login Data，
        並自動將車輛系統所有帳號雙向鏡像對齊至 car.tccf.gov.tw 與 car.fire.taichung.gov.tw 雙網域，
        同時支援指定預設優先帶入之同仁帳號 (preferred_account)。
        """
        tmp_dir = None
        try:
            tmp_dir = tempfile.mkdtemp(prefix="car_login_merge_")
            dst_login = os.path.join(dst_prof, "Login Data")
            dst_acct = os.path.join(dst_prof, "Login Data For Account")
            src_login = os.path.join(src_prof, "Login Data")
            src_acct = os.path.join(src_prof, "Login Data For Account")

            # 先清除目標目錄殘留的 -journal / -wal，避免 SQLite 鎖定或回滾
            for base_f in (dst_login, dst_acct):
                for suffix in ("-journal", "-wal", "-shm"):
                    stale_f = base_f + suffix
                    if os.path.exists(stale_f):
                        try:
                            os.remove(stale_f)
                        except Exception:
                            pass

            if not os.path.exists(dst_login) or os.path.getsize(dst_login) < 4096:
                if os.path.exists(src_login) and os.path.getsize(src_login) >= 4096:
                    self._win_shared_copy(src_login, dst_login)
                elif os.path.exists(src_acct) and os.path.getsize(src_acct) >= 4096:
                    self._win_shared_copy(src_acct, dst_login)

            if not os.path.exists(dst_login):
                return

            source_dbs = []
            for idx, base_name in enumerate(("Login Data", "Login Data For Account")):
                s_db = os.path.join(src_prof, base_name)
                if os.path.exists(s_db) and os.path.getsize(s_db) >= 4096:
                    t_db = os.path.join(tmp_dir, f"src_{idx}.db")
                    if self._win_shared_copy(s_db, t_db):
                        for suffix in ("-wal", "-journal"):
                            s_sub = s_db + suffix
                            if os.path.exists(s_sub):
                                self._win_shared_copy(s_sub, t_db + suffix)
                        source_dbs.append(t_db)

            conn = sqlite3.connect(dst_login, timeout=3.0)
            try:
                cols_main = [r[1] for r in conn.execute("PRAGMA main.table_info(logins)").fetchall()]
                if cols_main:
                    for idx, t_db in enumerate(source_dbs):
                        alias = f"src_db_{idx}"
                        try:
                            conn.execute(f"ATTACH DATABASE ? AS {alias}", (t_db,))
                            cols_src = [r[1] for r in conn.execute(f"PRAGMA {alias}.table_info(logins)").fetchall()]
                            common_cols = [c for c in cols_main if c in cols_src and c.lower() != "id"]
                            if common_cols:
                                col_list = ", ".join(f'"{c}"' for c in common_cols)
                                conn.execute(
                                    f"INSERT OR IGNORE INTO main.logins ({col_list}) "
                                    f"SELECT {col_list} FROM {alias}.logins"
                                )
                                conn.commit()
                        except Exception:
                            pass
                        finally:
                            try:
                                conn.execute(f"DETACH DATABASE {alias}")
                            except Exception:
                                pass

                    # 跨網域雙向對齊：確保 car.tccf.gov.tw 與 car.fire.taichung.gov.tw 皆擁有完整 15+ 組車輛系統帳密
                    non_id_cols = [c for c in cols_main if c.lower() != "id"]
                    col_idx = {c: i for i, c in enumerate(non_id_cols)}
                    sel_col_sql = ", ".join(f'"{c}"' for c in non_id_cols)

                    car_rows = conn.execute(
                        f"""
                        SELECT {sel_col_sql}
                        FROM main.logins
                        WHERE (
                            origin_url LIKE '%car.tccf.gov.tw%'
                            OR origin_url LIKE '%car.fire.taichung.gov.tw%'
                            OR action_url LIKE '%car/check_login.php%'
                            OR username_element = 'rt_admin_username'
                        )
                        AND username_value != ''
                        AND length(password_value) > 0
                        ORDER BY date_last_used DESC, times_used DESC
                        """
                    ).fetchall()

                    if car_rows:
                        target_domains = [
                            (
                                "http://car.tccf.gov.tw/car/login.php",
                                "http://car.tccf.gov.tw/car/check_login.php",
                                "http://car.tccf.gov.tw/",
                            ),
                            (
                                "http://car.fire.taichung.gov.tw/car/login.php",
                                "http://car.fire.taichung.gov.tw/car/check_login.php",
                                "http://car.fire.taichung.gov.tw/",
                            ),
                        ]
                        if target_url:
                            try:
                                p_u = urllib.parse.urlparse(target_url)
                                if p_u.scheme and p_u.netloc:
                                    custom_realm = f"{p_u.scheme}://{p_u.netloc}/"
                                    if all(r[2] != custom_realm for r in target_domains):
                                        target_domains.append((
                                            f"{p_u.scheme}://{p_u.netloc}/car/login.php",
                                            f"{p_u.scheme}://{p_u.netloc}/car/check_login.php",
                                            custom_realm,
                                        ))
                            except Exception:
                                pass

                        # 整理每個帳號最新且有效的模板資料列
                        best_row_by_user = {}
                        for r in car_rows:
                            u_val = (r[col_idx["username_value"]] or "").strip()
                            if u_val and u_val not in best_row_by_user:
                                best_row_by_user[u_val] = list(r)

                        now_webkit = int(time.time() * 1000000) + 11644473600000000
                        placeholders = ", ".join(["?"] * len(non_id_cols))

                        for u_val, base_vals in best_row_by_user.items():
                            for orig_u, act_u, realm_u in target_domains:
                                exists = conn.execute(
                                    "SELECT 1 FROM main.logins WHERE signon_realm = ? AND username_value = ? LIMIT 1",
                                    (realm_u, u_val),
                                ).fetchone()
                                if not exists:
                                    new_vals = list(base_vals)
                                    if "origin_url" in col_idx:
                                        new_vals[col_idx["origin_url"]] = orig_u
                                    if "action_url" in col_idx:
                                        new_vals[col_idx["action_url"]] = act_u
                                    if "signon_realm" in col_idx:
                                        new_vals[col_idx["signon_realm"]] = realm_u
                                    if "username_element" in col_idx:
                                        new_vals[col_idx["username_element"]] = "rt_admin_username"
                                    if "password_element" in col_idx:
                                        new_vals[col_idx["password_element"]] = "rt_admin_password"
                                    if "blacklisted_by_user" in col_idx:
                                        new_vals[col_idx["blacklisted_by_user"]] = 0
                                    if "scheme" in col_idx:
                                        new_vals[col_idx["scheme"]] = 0
                                    if "times_used" in col_idx and not new_vals[col_idx["times_used"]]:
                                        new_vals[col_idx["times_used"]] = 1
                                    if "date_last_used" in col_idx and not new_vals[col_idx["date_last_used"]]:
                                        new_vals[col_idx["date_last_used"]] = now_webkit - 86400000000
                                    try:
                                        conn.execute(
                                            f"INSERT OR REPLACE INTO main.logins ({sel_col_sql}) VALUES ({placeholders})",
                                            tuple(new_vals),
                                        )
                                    except Exception:
                                        pass

                        # 確保所有車輛系統紀錄之欄位名稱與啟用狀態一致，使 Chrome 自動填入引擎 100% 匹配
                        conn.execute(
                            """
                            UPDATE main.logins
                            SET username_element = 'rt_admin_username',
                                password_element = 'rt_admin_password',
                                blacklisted_by_user = 0,
                                times_used = CASE WHEN times_used IS NULL OR times_used < 1 THEN 1 ELSE times_used END,
                                date_last_used = CASE WHEN date_last_used IS NULL OR date_last_used < 1000 THEN ? ELSE date_last_used END
                            WHERE (
                                origin_url LIKE '%car.tccf.gov.tw%'
                                OR origin_url LIKE '%car.fire.taichung.gov.tw%'
                                OR signon_realm LIKE '%car.tccf.gov.tw%'
                                OR signon_realm LIKE '%car.fire.taichung.gov.tw%'
                            )
                            AND username_value != ''
                            """,
                            (now_webkit - 86400000000,),
                        )

                        # 若使用者指定了優先帶入之帳號，將該帳號之 date_last_used 提升至最高，讓 Chrome 原生自動填入該組帳密
                        pref_clean = (preferred_account or "").strip()
                        if pref_clean and not pref_clean.startswith("⚡") and pref_clean.upper() != "AUTO":
                            max_row = conn.execute("SELECT MAX(date_last_used) FROM main.logins").fetchone()
                            cur_max = int(max_row[0] or 0) if max_row else 0
                            top_ts = max(cur_max, now_webkit) + 10000000
                            if "date_last_filled" in col_idx:
                                conn.execute(
                                    """
                                    UPDATE main.logins
                                    SET date_last_used = ?, date_last_filled = ?, times_used = COALESCE(times_used, 1) + 1
                                    WHERE LOWER(username_value) = LOWER(?)
                                    """,
                                    (top_ts, top_ts, pref_clean),
                                )
                            else:
                                conn.execute(
                                    """
                                    UPDATE main.logins
                                    SET date_last_used = ?, times_used = COALESCE(times_used, 1) + 1
                                    WHERE LOWER(username_value) = LOWER(?)
                                    """,
                                    (top_ts, pref_clean),
                                )

                        conn.commit()
            finally:
                conn.close()

            for suffix in ("-journal", "-wal", "-shm"):
                for base_f in (dst_login, dst_acct):
                    stale_f = base_f + suffix
                    if os.path.exists(stale_f):
                        try:
                            os.remove(stale_f)
                        except Exception:
                            pass

            self._win_shared_copy(dst_login, dst_acct)
        except Exception as e:
            print("_merge_login_databases warning:", e)
        finally:
            if tmp_dir and os.path.exists(tmp_dir):
                shutil.rmtree(tmp_dir, ignore_errors=True)

    def setup_profile_bookmarks(self, prof_subdir: str = "CAR_Clean_Sandbox", is_clean_mode: bool = False):
        """合併 AccountBookmarks 與 Bookmarks，保留分隊常用書籤列並加入車輛系統快速連結與心跳書籤"""
        if is_clean_mode or not prof_subdir or prof_subdir in ("NONE", "CLEAN_SANDBOX", "CAR_Clean_Sandbox"):
            prof_subdir = "CAR_Clean_Sandbox"
            is_clean_mode = True
        base_ud_dir = self.get_isolated_user_data_dir(prof_subdir, is_clean_mode=is_clean_mode)
        try:
            os.makedirs(base_ud_dir, exist_ok=True)
            target_dir = os.path.join(base_ud_dir, prof_subdir)
            os.makedirs(target_dir, exist_ok=True)
            bm_file = os.path.join(target_dir, "Bookmarks")
            acct_bm_file = os.path.join(target_dir, "AccountBookmarks")
            pref_file = os.path.join(target_dir, "Preferences")

            real_prof_name = "🆕 車輛系統沙盒" if is_clean_mode else ""
            if not is_clean_mode:
                try:
                    sys_ls_file = os.path.join(get_chrome_user_data_dir(), "Local State")
                    if os.path.exists(sys_ls_file):
                        with open(sys_ls_file, "r", encoding="utf-8", errors="ignore") as lsf:
                            sys_ls = json.load(lsf)
                        p_info = sys_ls.get("profile", {}).get("info_cache", {}).get(prof_subdir, {})
                        real_prof_name = p_info.get("name") or p_info.get("gaia_name") or ""
                except Exception:
                    pass

            pref_data = {}
            if os.path.exists(pref_file):
                try:
                    with open(pref_file, "r", encoding="utf-8", errors="ignore") as f:
                        pref_data = json.load(f)
                except Exception:
                    pass
            pref_data.setdefault("bookmark_bar", {})["show_on_all_tabs"] = True
            pref_data["credentials_enable_service"] = True
            pref_data["credentials_enable_autosignin"] = True
            prof_obj = pref_data.setdefault("profile", {})
            prof_obj["password_manager_enabled"] = True
            prof_obj["exit_type"] = "Normal"
            prof_obj["exited_cleanly"] = True
            if real_prof_name:
                prof_obj["name"] = real_prof_name
                prof_obj["is_using_default_name"] = False
            with open(pref_file, "w", encoding="utf-8") as f:
                json.dump(pref_data, f, ensure_ascii=False)

            car_bookmarks = [
                ("🚗 車輛管理系統", "http://car.tccf.gov.tw/car/"),
                ("🚑 一鍵里程登記(BVX-0039)", "javascript:" + build_car_mileage_bookmarklet()),
                ("🚒 裝備器材系統", "https://fems.taichung.gov.tw/HPG01000/Index"),
                ("💓 車輛系統心跳保活", "javascript:" + build_car_heartbeat_bookmarklet()),
            ]
            car_titles = {t for t, _ in car_bookmarks}
            now_ts = str(int(time.time() * 1000000) + 11644473600000000)
            car_children = [
                {
                    "date_added": now_ts,
                    "date_last_used": "0",
                    "id": str(950 + idx),
                    "name": title,
                    "type": "url",
                    "url": url,
                }
                for idx, (title, url) in enumerate(car_bookmarks)
            ]

            preserved_bar = []
            preserved_other = []
            seen_bar_keys = set()
            seen_other_keys = set()
            base_bm_template = {}

            bm_sources = []
            if not is_clean_mode:
                src_prof_dir = os.path.join(get_chrome_user_data_dir(), prof_subdir)
                bm_sources.extend([
                    os.path.join(src_prof_dir, "AccountBookmarks"),
                    os.path.join(src_prof_dir, "Bookmarks"),
                    acct_bm_file,
                ])
            bm_sources.append(bm_file)

            for src_bm_path in bm_sources:
                if not os.path.exists(src_bm_path):
                    continue
                try:
                    with open(src_bm_path, "r", encoding="utf-8", errors="ignore") as f:
                        bdata = json.load(f)
                    if not base_bm_template and isinstance(bdata, dict) and "roots" in bdata:
                        base_bm_template = bdata
                    r_obj = bdata.get("roots", {})
                    for c in r_obj.get("bookmark_bar", {}).get("children", []):
                        cname = c.get("name", "")
                        if cname in car_titles:
                            continue
                        ckey = (c.get("type", ""), cname, c.get("url", ""))
                        if ckey not in seen_bar_keys:
                            seen_bar_keys.add(ckey)
                            preserved_bar.append(c)
                    for c in r_obj.get("other", {}).get("children", []):
                        cname = c.get("name", "")
                        ckey = (c.get("type", ""), cname, c.get("url", ""))
                        if ckey not in seen_other_keys:
                            seen_other_keys.add(ckey)
                            preserved_other.append(c)
                except Exception:
                    pass

            existing_bm = base_bm_template if base_bm_template else {}
            roots = existing_bm.get("roots", {})
            bar = roots.get("bookmark_bar", {"children": [], "name": "書籤列", "type": "folder"})
            other_folder = roots.get("other", {"children": [], "name": "其他書籤", "type": "folder"})

            bar["children"] = car_children + preserved_bar
            if preserved_other:
                other_folder["children"] = preserved_other

            roots["bookmark_bar"] = bar
            roots["other"] = other_folder
            roots.setdefault("synced", {"children": [], "name": "行動版書籤", "type": "folder"})
            existing_bm["roots"] = roots
            existing_bm.setdefault("version", 1)

            with open(bm_file, "w", encoding="utf-8") as f:
                json.dump(existing_bm, f, ensure_ascii=False, indent=2)
            if not is_clean_mode:
                with open(acct_bm_file, "w", encoding="utf-8") as f:
                    json.dump(existing_bm, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print("setup_profile_bookmarks warning:", e)

    def ensure_car_tab(self, target_url: str = DEFAULT_CAR_SYSTEM_URL) -> dict:
        car_tabs = self.get_car_tabs(target_url)
        if car_tabs:
            return car_tabs[0]

        nav_url = target_url or DEFAULT_CAR_SYSTEM_URL
        try:
            encoded_u = urllib.parse.quote(nav_url, safe=":/?&=#")
            req = urllib.request.Request(f"http://127.0.0.1:{self.port}/json/new?{encoded_u}", method="PUT")
            with self._local_http_open(req, timeout=2.5) as resp:
                new_tab = json.loads(resp.read().decode("utf-8"))
                time.sleep(0.5)
                return new_tab
        except Exception:
            pass

        page_tabs = self.get_page_tabs()
        if page_tabs:
            ws = page_tabs[0].get("webSocketDebuggerUrl")
            if ws:
                self._ws_eval(ws, f"window.open({json.dumps(nav_url)}, '_blank'); 'OPEN'", timeout=2.5)
                time.sleep(0.6)
                car_tabs = self.get_car_tabs(target_url)
                if car_tabs:
                    return car_tabs[0]
        return None

    def activate_window(self, target_url: str = DEFAULT_CAR_SYSTEM_URL):
        car_tabs = self.get_car_tabs(target_url)
        tab = car_tabs[0] if car_tabs else None
        if tab and "id" in tab:
            try:
                self._local_http_open(f"http://127.0.0.1:{self.port}/json/activate/{tab['id']}", timeout=1.5)
            except Exception:
                pass
        activate_chrome_window()

    @staticmethod
    def _build_insecure_origins_flag(target_url: str = DEFAULT_CAR_SYSTEM_URL) -> str:
        """
        建立 --unsafely-treat-insecure-origin-as-secure 參數：
        因車輛管理系統 (http://car.tccf.gov.tw 與 http://car.fire.taichung.gov.tw) 採用純 HTTP 協定，
        Chrome 預設將 HTTP 視為不安全來源 (IsOriginSecure=false, wait_for_username=true) 而封鎖自動填入密碼。
        加入此安全白名單旗標後，Chrome 即會將車輛系統視為安全來源，開網頁瞬間自動填入帳號與密碼並啟用下拉選單。
        """
        origins = [
            "http://car.tccf.gov.tw",
            "http://car.fire.taichung.gov.tw",
            "http://inside.fire.taichung.gov.tw",
            "http://fixnew.fire.taichung.gov.tw",
            "http://training.fire.taichung.gov.tw",
        ]
        if target_url:
            try:
                p = urllib.parse.urlparse(target_url)
                if p.scheme == "http" and p.netloc:
                    custom_orig = f"http://{p.netloc}"
                    if custom_orig not in origins:
                        origins.append(custom_orig)
            except Exception:
                pass
        return "--unsafely-treat-insecure-origin-as-secure=" + ",".join(origins)

    def launch_or_activate(
        self,
        target_url: str = DEFAULT_CAR_SYSTEM_URL,
        profile_name: str = None,
        preferred_account: str = "",
        force_restart: bool = False,
        target_license: str = DEFAULT_CAR_TARGET_LICENSE,
        auto_submit: bool = True,
        auto_mileage: bool = True,
    ) -> bool:
        prof = resolve_chrome_profile(profile_name) if profile_name else find_chrome_profile_for_user()
        is_clean_mode = (prof == "NONE")
        prof_subdir = "CAR_Clean_Sandbox" if is_clean_mode else prof
        target_user_data_dir = self.get_isolated_user_data_dir(prof_subdir, is_clean_mode=is_clean_mode)

        pref_acct = (preferred_account or "").strip()
        if pref_acct.startswith("⚡") or pref_acct.upper() == "AUTO":
            pref_acct = ""

        # 先掃描設定檔中的車輛系統帳號與可用快取
        if not is_clean_mode:
            self.get_saved_car_accounts(prof_subdir)

        # 預先生成或更新本地擴充套件設定
        ext_dir = self.ensure_car_helper_extension(
            target_url=target_url,
            target_license=target_license,
            preferred_account=pref_acct,
            auto_submit=auto_submit,
            auto_mileage=auto_mileage,
        )

        if self.is_running():
            need_restart = (
                force_restart
                or (self.get_active_profile_mode() != prof)
                or (pref_acct and pref_acct != self.active_preferred_account and pref_acct not in self._decrypted_car_creds)
            )
            if need_restart:
                self.close_browser()
            else:
                if pref_acct:
                    self._write_saved_preferred_account(pref_acct)
                self.ensure_car_tab(target_url)
                if pref_acct and pref_acct in self._decrypted_car_creds:
                    accts = self.get_saved_car_accounts(prof_subdir)
                    self.assist_car_login_page(
                        target_url=target_url,
                        accounts=accts,
                        preferred_account=pref_acct,
                        force_fill_account=pref_acct,
                        auto_submit=auto_submit,
                    )
                self.activate_window(target_url)
                return True

        self.active_profile_subdir = prof_subdir
        self._write_saved_active_mode(prof)
        self._write_saved_preferred_account(pref_acct)

        if not is_clean_mode:
            self.sync_from_system_profile(
                prof_subdir,
                preferred_account=pref_acct,
                target_url=target_url,
            )
        self.setup_profile_bookmarks(prof_subdir, is_clean_mode=is_clean_mode)

        cmd = [
            get_chrome_executable(),
            f"--user-data-dir={target_user_data_dir}",
            f"--profile-directory={prof_subdir}",
            f"--remote-debugging-port={self.port}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-allow-origins=*",
            self._build_insecure_origins_flag(target_url),
        ]
        if ext_dir and os.path.exists(ext_dir):
            cmd.extend([
                f"--load-extension={ext_dir}",
                f"--disable-extensions-except={ext_dir}",
            ])
        cmd.extend([
            "--no-first-run",
            "--no-default-browser-check",
            target_url or DEFAULT_CAR_SYSTEM_URL,
        ])
        try:
            self.process = subprocess.Popen(cmd)
        except Exception as e:
            print("Failed to launch Chrome for vehicle system:", e)
            return False

        for _ in range(25):
            time.sleep(0.3)
            if self.is_running():
                return True
        return False

    def assist_car_login_page(
        self,
        target_url: str = DEFAULT_CAR_SYSTEM_URL,
        accounts: list = None,
        preferred_account: str = "",
        force_fill_account: str = "",
        auto_submit: bool = True,
    ) -> str:
        """
        在車輛管理系統登入頁 (login.php) 提供智慧帳密輔助與上方快速切換列：
        1. 自動補齊 autocomplete="username" / "current-password"，保留 Chrome 原生 :-webkit-autofill 預填密碼狀態
        2. 於登入頁頂部顯示「🔑 Google 設定檔已同步 N 組車輛系統帳密」與帳號快速切換選單
        3. 若使用者在網頁頂部切換其他同仁帳號，回傳 'SWITCH:<帳號>' 由主程式自動切換並填入該同仁帳密
        """
        if not self.is_running():
            return "NOT_RUNNING"
        car_tabs = self.get_car_tabs(target_url)
        if not car_tabs:
            return "NO_TAB"

        login_tab = None
        for t in car_tabs:
            if "login" in (t.get("url", "") or "").lower():
                login_tab = t
                break
        if not login_tab:
            login_tab = car_tabs[0]

        ws = login_tab.get("webSocketDebuggerUrl")
        if not ws:
            return "NO_WS"

        acct_list = accounts if accounts is not None else self.get_saved_car_accounts(self.active_profile_subdir)
        pref_clean = (preferred_account or self.active_preferred_account or "").strip()
        if pref_clean.startswith("⚡") or pref_clean.upper() == "AUTO":
            pref_clean = ""

        direct_user = ""
        direct_pass = ""
        target_fill = (force_fill_account or pref_clean).strip()
        if target_fill and target_fill in self._decrypted_car_creds:
            direct_user = target_fill
            direct_pass = self._decrypted_car_creds[target_fill]
        elif not target_fill and self._decrypted_car_creds:
            first_u = acct_list[0] if acct_list and acct_list[0] in self._decrypted_car_creds else next(iter(self._decrypted_car_creds))
            direct_user = first_u
            direct_pass = self._decrypted_car_creds[first_u]

        payload_json = json.dumps({
            "accounts": acct_list,
            "preferred": pref_clean,
            "force_fill": bool(force_fill_account),
            "direct_user": direct_user,
            "direct_pass": direct_pass,
            "profile_mode": self.get_active_profile_mode(),
            "auto_submit": bool(auto_submit),
        }, ensure_ascii=False)

        js_assist = f"""(function(cfg) {{
            try {{
                if (window.__car_requested_switch_acct) {{
                    var req = window.__car_requested_switch_acct;
                    window.__car_requested_switch_acct = '';
                    return 'SWITCH:' + req;
                }}

                var targetDoc = document;
                var u = document.querySelector('input[name="rt_admin_username"], input[type="text"]');
                var p = document.querySelector('input[name="rt_admin_password"], input[type="password"]');
                if (!p) {{
                    for (var i = 0; i < window.frames.length; i++) {{
                        try {{
                            var fd = window.frames[i].document;
                            if (fd) {{
                                var fp = fd.querySelector('input[name="rt_admin_password"], input[type="password"]');
                                if (fp) {{
                                    targetDoc = fd;
                                    p = fp;
                                    u = fd.querySelector('input[name="rt_admin_username"], input[type="text"]');
                                    break;
                                }}
                            }}
                        }} catch(e) {{}}
                    }}
                }}
                if (!u || !p) return 'NOT_LOGIN_DOM';

                if (u.getAttribute('autocomplete') !== 'username') {{
                    u.setAttribute('autocomplete', 'username');
                }}
                if (p.getAttribute('autocomplete') !== 'current-password') {{
                    p.setAttribute('autocomplete', 'current-password');
                }}

                var uAuto = false, pAuto = false;
                try {{ uAuto = u.matches(':-webkit-autofill'); }} catch(e) {{}}
                try {{ pAuto = p.matches(':-webkit-autofill'); }} catch(e) {{}}

                if (cfg.direct_user && cfg.direct_pass) {{
                    if (cfg.force_fill || (!uAuto && !pAuto && !u.value && !p.value)) {{
                        u.value = cfg.direct_user;
                        p.value = cfg.direct_pass;
                        u.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        u.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        p.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        p.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    }}
                }}

                var curUser = (u.value || '').trim();
                var hasRealPass = pAuto || (p.value && p.value.length > 0);
                var hasPass = hasRealPass || (curUser.length > 0 && p !== null);
                var accts = cfg.accounts || [];

                var bar = targetDoc.getElementById('__car_acct_helper_bar');
                if (!bar) {{
                    bar = targetDoc.createElement('div');
                    bar.id = '__car_acct_helper_bar';
                    bar.style.cssText = 'position:fixed;top:0;left:0;right:0;background:linear-gradient(90deg,#0f172a,#1e3a8a);color:#f8fafc;padding:8px 16px;font-size:13px;z-index:999999;box-shadow:0 3px 12px rgba(0,0,0,0.28);font-family:sans-serif;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;border-bottom:2px solid #38bdf8;';

                    var leftDiv = targetDoc.createElement('div');
                    leftDiv.id = '__car_acct_status_text';
                    leftDiv.style.cssText = 'display:flex;align-items:center;gap:8px;font-weight:bold;';
                    bar.appendChild(leftDiv);

                    var rightDiv = targetDoc.createElement('div');
                    rightDiv.style.cssText = 'display:flex;align-items:center;gap:8px;';

                    if (accts.length > 0) {{
                        var lbl = targetDoc.createElement('span');
                        lbl.style.cssText = 'color:#bae6fd;font-size:12px;';
                        lbl.textContent = '切換設定檔內其他同仁帳號：';
                        rightDiv.appendChild(lbl);

                        var sel = targetDoc.createElement('select');
                        sel.id = '__car_acct_select';
                        sel.style.cssText = 'padding:3px 8px;border-radius:5px;border:1px solid #7dd3fc;background:#ffffff;color:#0f172a;font-size:13px;font-weight:bold;cursor:pointer;';
                        for (var j = 0; j < accts.length; j++) {{
                            var opt = targetDoc.createElement('option');
                            opt.value = accts[j];
                            opt.textContent = (j + 1) + '. ' + accts[j];
                            sel.appendChild(opt);
                        }}
                        sel.onchange = function() {{
                            var chosen = this.value;
                            if (chosen) {{
                                var st = targetDoc.getElementById('__car_acct_status_text');
                                if (st) st.innerHTML = '⏳ 正在從 Google 設定檔切換並帶入 <strong>' + chosen + '</strong> 的帳號與密碼，請稍候...';
                                window.__car_requested_switch_acct = chosen;
                            }}
                        }};
                        rightDiv.appendChild(sel);

                        var btn = targetDoc.createElement('button');
                        btn.type = 'button';
                        btn.textContent = '⚡ 帶入此帳密';
                        btn.style.cssText = 'padding:3px 10px;border-radius:5px;border:none;background:#0284c7;color:#ffffff;font-size:12px;font-weight:bold;cursor:pointer;box-shadow:0 1px 4px rgba(0,0,0,0.2);';
                        btn.onclick = function() {{
                            var s = targetDoc.getElementById('__car_acct_select');
                            if (s && s.value) {{
                                var st = targetDoc.getElementById('__car_acct_status_text');
                                if (st) st.innerHTML = '⏳ 正在從 Google 設定檔切換並帶入 <strong>' + s.value + '</strong> 的帳號與密碼，請稍候...';
                                window.__car_requested_switch_acct = s.value;
                            }}
                        }};
                        rightDiv.appendChild(btn);
                    }}

                    bar.appendChild(rightDiv);
                    (targetDoc.body || targetDoc.documentElement).appendChild(bar);
                    if (targetDoc.body) {{
                        targetDoc.body.style.paddingTop = '46px';
                    }}
                }}

                var stNode = targetDoc.getElementById('__car_acct_status_text');
                if (stNode && !window.__car_requested_switch_acct) {{
                    if (hasPass && curUser) {{
                        stNode.innerHTML = '🟢 <span>Google 設定檔已同步 <strong>' + accts.length + '</strong> 組帳密 ｜ 已自動帶入帳號：<strong style="color:#4ade80">' + curUser + '</strong>（密碼已填入，可直接按「登入」或點帳號框展開清單）</span>';
                    }} else if (accts.length > 0) {{
                        stNode.innerHTML = '🔑 <span>Google 設定檔已同步 <strong>' + accts.length + '</strong> 組車輛系統帳密 ｜ 點擊下方「帳號」輸入框即可展開密碼清單，或由右側選單切換帶入</span>';
                    }} else {{
                        stNode.innerHTML = '🔑 <span>車輛系統登入輔助運作中（首次登入後 Chrome 將自動詢問儲存帳號密碼）</span>';
                    }}
                }}

                var selNode = targetDoc.getElementById('__car_acct_select');
                if (selNode && document.activeElement !== selNode) {{
                    var matchVal = curUser || cfg.preferred;
                    if (matchVal) {{
                        for (var k = 0; k < selNode.options.length; k++) {{
                            if (selNode.options[k].value.toLowerCase() === matchVal.toLowerCase()) {{
                                selNode.selectedIndex = k;
                                break;
                            }}
                        }}
                    }}
                }}

                if (cfg.auto_submit && (curUser || uAuto) && hasRealPass) {{
                    var subBtn = targetDoc.querySelector('input[name="Submit"], input[type="submit"][value*="登入"], input.ConWebButton, button[type="submit"]');
                    if (subBtn) {{
                        var targetWin = targetDoc.defaultView || window;
                        var nowSubTs = Date.now();
                        var lastSubLock = Math.max(window.__car_login_submit_lock_ts || 0, targetWin.__car_login_submit_lock_ts || 0);
                        if (nowSubTs - lastSubLock < 4000) {{
                            return 'SUBMIT_COOLDOWN:' + (curUser || 'AUTO');
                        }}
                        window.__car_login_submit_lock_ts = nowSubTs;
                        targetWin.__car_login_submit_lock_ts = nowSubTs;

                        if (stNode) {{
                            stNode.innerHTML = '🚀 <span>已帶入帳號 <strong>' + (curUser || '自動填入') + '</strong>，<strong>正在送出登入，請稍候...</strong></span>';
                        }}

                        try {{
                            if (typeof targetWin.rememberMe === 'function') targetWin.rememberMe();
                            else if (typeof rememberMe === 'function') rememberMe();
                        }} catch(e) {{}}

                        // 單次正規點擊登入按鈕（完整攜帶 Chrome Autofill 密碼與 onclick 驗證，不重複連點中斷 POST）
                        try {{ subBtn.focus(); }} catch(e) {{}}
                        try {{ subBtn.click(); }} catch(e) {{}}

                        return 'AUTO_SUBMITTING:' + (curUser || 'AUTO');
                    }}
                }}

                return (hasPass ? 'FILLED:' : 'READY:') + (curUser || '');
            }} catch(e) {{
                return 'ERR:' + e;
            }}
        }})({payload_json})"""

        ok, res = self._ws_eval(ws, js_assist, timeout=2.0)
        if ok and isinstance(res, str):
            return res
        return "IDLE"

    def check_car_login_state(self, target_url: str = DEFAULT_CAR_SYSTEM_URL) -> tuple:
        """
        檢查受控瀏覽器中車輛系統分頁狀態：
        回傳 (has_car_tab: bool, is_logged_in: bool, is_login_page: bool, tab_title: str, tab_url: str)
        """
        car_tabs = self.get_car_tabs(target_url)
        if not car_tabs:
            return False, False, False, "", ""

        js_check = """(function(){
            try {
                var href = (window.location.href || '').toLowerCase();
                // 0. 若正在登入驗證過渡頁 (check_login.php) 或 DOM 尚未載入完成，回傳 LOADING 安靜等待
                if (href.indexOf('check_login') !== -1) {
                    return 'LOADING';
                }
                if (document.readyState === 'loading') {
                    return 'LOADING';
                }

                // 1. 檢查是否存在可見的密碼輸入框，只要存在密碼框就100%是登入頁面
                var pw = document.querySelector('input[name="rt_admin_password"], input[type="password"]');
                if (pw && (pw.offsetParent !== null || pw.offsetWidth > 0 || pw.offsetHeight > 0)) {
                    return 'LOGIN_PAGE';
                }
                for (var i = 0; i < window.frames.length; i++) {
                    try {
                        var fdoc = window.frames[i].document;
                        if (fdoc) {
                            var fpw = fdoc.querySelector('input[name="rt_admin_password"], input[type="password"]');
                            if (fpw && (fpw.offsetParent !== null || fpw.offsetWidth > 0 || fpw.offsetHeight > 0)) {
                                return 'LOGIN_PAGE';
                            }
                        }
                    } catch(e) {}
                }

                // 2. 檢查網址特徵
                if (href.indexOf('login.php') !== -1 || href.indexOf('/account/login') !== -1 || href.indexOf('login.asp') !== -1) {
                    return 'LOGIN_PAGE';
                }

                // 3. 檢查登入後特徵 (如里程頁、登出按鈕等)
                if (href.indexOf('mileage') !== -1 || href.indexOf('main') !== -1 || href.indexOf('index.php') !== -1 || document.querySelector('a[href*="logout"], a[href*="login_out"], input[value*="登出"]')) {
                    return 'LOGGED_IN';
                }

                if (document.readyState !== 'complete') {
                    return 'LOADING';
                }

                // 沒有密碼框且DOM載入完成，判定為已登入
                return 'LOGGED_IN';
            } catch(e) {
                return 'UNKNOWN';
            }
        })()"""

        first_tab = car_tabs[0]
        tab_url = first_tab.get("url", "")
        tab_title = first_tab.get("title", "") or "車輛管理系統"

        # 優先檢查所有車輛分頁狀態
        for t in car_tabs:
            u = t.get("url", "")
            ws = t.get("webSocketDebuggerUrl")
            if ws:
                ok, state = self._ws_eval(ws, js_check, timeout=1.5)
                if ok:
                    if state == "LOGIN_PAGE":
                        return True, False, True, (t.get("title") or tab_title), u
                    elif state == "LOGGED_IN":
                        return True, True, False, (t.get("title") or tab_title), u
                    elif state == "LOADING":
                        return True, False, False, (t.get("title") or tab_title), u

        # 若 WebSocket 暫時無回應，依 URL 謹慎判斷（未確定時回傳載入中，絕不誤判已登入而亂跳轉）
        t_low = tab_url.lower()
        if "check_login" in t_low:
            return True, False, False, tab_title, tab_url
        if "login.php" in t_low or "/account/login" in t_low or "login.asp" in t_low:
            return True, False, True, tab_title, tab_url
        if "mileage" in t_low or "main" in t_low or "index.php" in t_low:
            return True, True, False, tab_title, tab_url

        # 根目錄 /car/ 在 DOM 尚未確認前視為載入中（待下一拍確認是登入頁還是已登入）
        return True, False, False, tab_title, tab_url

    def evaluate_in_background(self, js_code: str, target_url: str = DEFAULT_CAR_SYSTEM_URL, timeout: float = 5.0) -> tuple:
        """在背景靜默對車輛系統分頁執行 JavaScript，不切換分頁、不喚醒視窗、不搶焦點"""
        if not self.is_running():
            return False, "BROWSER_NOT_RUNNING"
        tabs = self.get_car_tabs(target_url) or self.get_page_tabs()
        if not tabs:
            return False, "NO_CAR_PAGE_FOUND"
        # 優先選擇已登入的分頁
        target_tab = tabs[0]
        for t in tabs:
            if "login" not in t.get("url", "").lower():
                target_tab = t
                break
        ws_url = target_tab.get("webSocketDebuggerUrl")
        if not ws_url:
            return False, "NO_WS_DEBUGGER_URL"
        return self._ws_eval(ws_url, js_code, timeout=timeout)

    def _ws_call(self, ws_url: str, method: str, params: dict, timeout: float = 6.0) -> tuple:
        try:
            u = urllib.parse.urlparse(ws_url)
            host = u.hostname or "127.0.0.1"
            port = u.port or self.port
            s = socket.create_connection((host, port), timeout=timeout)
            key = base64.b64encode(os.urandom(16)).decode().strip()
            req = (
                f"GET {u.path} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                f"Origin: http://{host}:{port}\r\n\r\n"
            )
            s.sendall(req.encode("latin1"))

            resp = b""
            while b"\r\n\r\n" not in resp:
                chunk = s.recv(1024)
                if not chunk:
                    break
                resp += chunk
            if b"101" not in resp:
                s.close()
                return False, "WebSocket 交握失敗"

            payload = json.dumps({
                "id": int(time.time() * 1000) % 1000000,
                "method": method,
                "params": params,
            }).encode("utf-8")
            mask_key = os.urandom(4)
            masked_payload = bytearray(b ^ mask_key[i % 4] for i, b in enumerate(payload))
            length = len(payload)

            header = bytearray([0x81])
            if length <= 125:
                header.append(0x80 | length)
            elif length <= 65535:
                header.append(0x80 | 126)
                header.extend(struct.pack(">H", length))
            else:
                header.append(0x80 | 127)
                header.extend(struct.pack(">Q", length))
            header.extend(mask_key)
            header.extend(masked_payload)
            s.sendall(header)

            data = b""
            while len(data) < 2:
                chunk = s.recv(1024)
                if not chunk:
                    break
                data += chunk
            if len(data) < 2:
                s.close()
                return True, "Executed"

            pay_len = data[1] & 0x7F
            idx = 2
            if pay_len == 126:
                while len(data) < 4:
                    data += s.recv(1024)
                pay_len = struct.unpack(">H", data[2:4])[0]
                idx = 4
            elif pay_len == 127:
                while len(data) < 10:
                    data += s.recv(1024)
                pay_len = struct.unpack(">Q", data[2:10])[0]
                idx = 10

            while len(data) < idx + pay_len:
                chunk = s.recv(min(8192, idx + pay_len - len(data)))
                if not chunk:
                    break
                data += chunk
            s.close()

            res_obj = json.loads(data[idx:idx + pay_len].decode("utf-8", "replace"))
            if "error" in res_obj:
                return False, str(res_obj["error"])
            return True, res_obj.get("result", {})
        except Exception as e:
            return False, str(e)

    def _ws_eval(self, ws_url: str, js_code: str, timeout: float = 6.0) -> tuple:
        ok, res = self._ws_call(
            ws_url,
            "Runtime.evaluate",
            {"expression": js_code, "returnByValue": True, "awaitPromise": True},
            timeout=timeout,
        )
        if not ok:
            return False, str(res)
        if isinstance(res, dict):
            val = res.get("result", {}).get("value")
            return True, str(val) if val is not None else "OK"
        return True, str(res)

    def assist_car_mileage_flow(
        self,
        target_url: str = DEFAULT_CAR_SYSTEM_URL,
        target_license: str = DEFAULT_CAR_TARGET_LICENSE,
        target_date: str = "",
    ) -> str:
        """
        在已登入之車輛系統中執行一鍵里程登記全自動化（支援全 Frame / iframe 深度穿透）：
        1. 若網址尚未在 mileage_nw.php，自動將頂層視窗或主內容區切換至 http://car.tccf.gov.tw/car/pages/mileage_nw.php
        2. 進入頁面後，跨所有 Frame 自動尋找並點擊「添加新記錄」(add.gif / dr5__doPostBack)
        3. 自動於牌照選單中選取 BVX-0039 並觸發 search_car_mileage('BVX-0039')
        4. 自動於日期選單中選取今日日期 (YYYY-MM-DD) 並觸發選取事件
        5. 建立頂部顯眼之成功提示浮動列
        """
        if not self.is_running():
            return "NOT_RUNNING"
        car_tabs = self.get_car_tabs(target_url)
        if not car_tabs:
            return "NO_TAB"

        target_tab = None
        for t in car_tabs:
            u = (t.get("url", "") or "").lower()
            if "mileage_nw.php" in u:
                target_tab = t
                break
        if not target_tab:
            for t in car_tabs:
                if "login" not in (t.get("url", "") or "").lower():
                    target_tab = t
                    break
        if not target_tab:
            target_tab = car_tabs[0]

        ws = target_tab.get("webSocketDebuggerUrl")
        if not ws:
            return "NO_WS"

        mileage_url = get_car_mileage_url(target_url)
        payload_json = json.dumps({
            "mileage_url": mileage_url,
            "target_license": (target_license or DEFAULT_CAR_TARGET_LICENSE).strip(),
            "target_date": (target_date or "").strip(),
        }, ensure_ascii=False)

        js_code = f"""(function(cfg) {{
            try {{
                function getAllFrames(w) {{
                    var res = [];
                    function rec(win) {{
                        try {{
                            if (win && win.document && res.indexOf(win) === -1) {{
                                res.push(win);
                                for (var i = 0; i < win.frames.length; i++) {{
                                    var sub = win.frames[i];
                                    if (sub && sub !== win) rec(sub);
                                }}
                            }}
                        }} catch(e) {{}}
                    }}
                    rec(w || window);
                    return res;
                }}

                var frames = getAllFrames(window);
                // 嚴格守門：只要任意 Frame 還在登入頁或登入驗證過渡頁 (check_login)，絕不強行跳轉 mileage_nw.php
                for (var g = 0; g < frames.length; g++) {{
                    try {{
                        var gHref = (frames[g].location.href || '').toLowerCase();
                        if (gHref.indexOf('login.php') !== -1 || gHref.indexOf('/account/login') !== -1 || gHref.indexOf('check_login') !== -1) {{
                            return 'ON_LOGIN_PAGE';
                        }}
                        var gPw = frames[g].document.querySelector('input[name="rt_admin_password"], input[type="password"]');
                        if (gPw && (gPw.offsetParent !== null || gPw.offsetWidth > 0 || gPw.offsetHeight > 0)) {{
                            return 'ON_LOGIN_PAGE';
                        }}
                    }} catch(e) {{}}
                }}

                var onMileage = false;
                for (var j = 0; j < frames.length; j++) {{
                    try {{
                        if ((frames[j].location.href || '').toLowerCase().indexOf('mileage_nw.php') !== -1) {{
                            onMileage = true;
                            break;
                        }}
                    }} catch(e) {{}}
                }}

                if (!onMileage) {{
                    var nowNavT = Date.now();
                    if (window.__car_nav_mileage_ts && nowNavT - window.__car_nav_mileage_ts < 3000) {{
                        return 'NAVIGATING_COOLDOWN';
                    }}
                    window.__car_nav_mileage_ts = nowNavT;
                    window.location.href = cfg.mileage_url;
                    return 'NAVIGATING_TO_MILEAGE';
                }}

                var selLic = null, selDate = null, addBtn = null, actWin = window;
                for (var k = 0; k < frames.length; k++) {{
                    try {{
                        var fd = frames[k].document;
                        if (!selLic) selLic = fd.getElementById('ryylicense') || fd.querySelector('select[name="ryylicense"]');
                        if (!selDate) selDate = fd.getElementById('ryymdate') || fd.querySelector('select[name="ryymdate"]');
                        if (!addBtn) {{
                            var b = fd.querySelector('img[title*="添加新記錄"], img[alt*="添加新記錄"], img[src*="add.gif"], img[onclick*="dr5__doPostBack"][onclick*="add"], a[onclick*="dr5__doPostBack"][onclick*="add"]');
                            if (b) addBtn = {{ el: b, win: frames[k] }};
                            else if (typeof frames[k].dr5__doPostBack === 'function') addBtn = {{ fn: frames[k].dr5__doPostBack, win: frames[k] }};
                        }}
                        if (selLic) actWin = frames[k];
                    }} catch(e) {{}}
                }}

                if (!selLic) {{
                    if (addBtn) {{
                        var nowT = Date.now();
                        if (!addBtn.win.__last_add_t || nowT - addBtn.win.__last_add_t > 2500) {{
                            addBtn.win.__last_add_t = nowT;
                            if (addBtn.el) {{
                                try {{ addBtn.el.focus(); }} catch(e) {{}}
                                try {{ addBtn.el.click(); }} catch(e) {{}}
                            }} else if (addBtn.fn) {{
                                try {{ addBtn.fn('add', '-1', ''); }} catch(e) {{}}
                            }}
                            return 'CLICKED_ADD';
                        }}
                        return 'WAITING_AFTER_CLICK';
                    }}
                    return 'WAITING_ADD_BUTTON';
                }}

                var targetLic = cfg.target_license || 'BVX-0039';
                var matchedLic = '';
                for (var i = 0; i < selLic.options.length; i++) {{
                    var opt = selLic.options[i];
                    if (opt.value.indexOf(targetLic) !== -1 || opt.text.indexOf(targetLic) !== -1) {{
                        selLic.selectedIndex = i;
                        selLic.value = opt.value;
                        matchedLic = opt.value;
                        break;
                    }}
                }}
                if (matchedLic) {{
                    try {{ selLic.dispatchEvent(new Event('input', {{ bubbles: true }})); }} catch(e) {{}}
                    try {{ selLic.dispatchEvent(new Event('change', {{ bubbles: true }})); }} catch(e) {{}}
                    if (typeof selLic.onchange === 'function') {{
                        try {{ selLic.onchange(); }} catch(e) {{}}
                    }}
                    if (typeof actWin.search_car_mileage === 'function') {{
                        try {{ actWin.search_car_mileage(matchedLic); }} catch(e) {{}}
                    }} else if (typeof window.search_car_mileage === 'function') {{
                        try {{ window.search_car_mileage(matchedLic); }} catch(e) {{}}
                    }}
                }}

                var tDate = cfg.target_date;
                if (!tDate) {{
                    var now = new Date();
                    var yyyy = now.getFullYear();
                    var mm = String(now.getMonth() + 1).padStart(2, '0');
                    var dd = String(now.getDate()).padStart(2, '0');
                    tDate = yyyy + '-' + mm + '-' + dd;
                }}

                var matchedDate = '';
                if (selDate) {{
                    for (var m = 0; m < selDate.options.length; m++) {{
                        var dOpt = selDate.options[m];
                        if (dOpt.value === tDate || dOpt.text.indexOf(tDate) !== -1) {{
                            selDate.selectedIndex = m;
                            selDate.value = dOpt.value;
                            matchedDate = dOpt.value;
                            break;
                        }}
                    }}
                    if (!matchedDate && selDate.options.length > 1) {{
                        for (var n = 0; n < selDate.options.length; n++) {{
                            if (selDate.options[n].value) {{
                                selDate.selectedIndex = n;
                                selDate.value = selDate.options[n].value;
                                matchedDate = selDate.options[n].value;
                                break;
                            }}
                        }}
                    }}
                    try {{ selDate.dispatchEvent(new Event('input', {{ bubbles: true }})); }} catch(e) {{}}
                    try {{ selDate.dispatchEvent(new Event('change', {{ bubbles: true }})); }} catch(e) {{}}
                    if (typeof selDate.onchange === 'function') {{
                        try {{ selDate.onchange(); }} catch(e) {{}}
                    }}
                }}

                var topDoc = window.document;
                var bar = topDoc.getElementById('__car_mileage_done_bar');
                if (!bar) {{
                    bar = topDoc.createElement('div');
                    bar.id = '__car_mileage_done_bar';
                    bar.style.cssText = 'position:fixed;top:0;left:0;right:0;background:linear-gradient(90deg,#065f46,#047857);color:#ffffff;padding:12px 20px;font-size:14px;font-weight:bold;z-index:999999;box-shadow:0 4px 16px rgba(0,0,0,0.3);display:flex;align-items:center;justify-content:space-between;border-bottom:3px solid #34d399;font-family:sans-serif;';
                    bar.innerHTML = '<span>🚑 <strong>【救護小幫手】已為您自動添加新記錄 ＆ 預選車牌【' + (matchedLic || targetLic) + '】與今日日期【' + (matchedDate || tDate) + '】！</strong><br><span style="font-size:12px;font-weight:normal;color:#a7f3d0">👉 請接續輸入今日出勤里程並按儲存。</span></span><button onclick="this.parentElement.remove();" style="background:#059669;color:#fff;border:1px solid #6ee7b7;border-radius:4px;padding:4px 10px;cursor:pointer;font-weight:bold;">關閉提示 ✕</button>';
                    (topDoc.body || topDoc.documentElement).appendChild(bar);
                    if (topDoc.body) topDoc.body.style.paddingTop = '56px';
                }}

                return 'SUCCESS_FILLED:' + (matchedLic || targetLic) + ':' + (matchedDate || tDate);
            }} catch(e) {{
                return 'ERR:' + e;
            }}
        }})({payload_json})"""

        ok, res = self._ws_eval(ws, js_code, timeout=3.0)
        if ok and isinstance(res, str):
            return res
        return "IDLE"


class ArchiveTaskTab(ttk.Frame):
    """獨立的勤務影像歸檔作業分頁，支援多任務並行與狀態隔離"""
    def __init__(self, parent, app, tab_id=1):
        super().__init__(parent)
        self.app = app
        self.tab_id = tab_id
        self.bg_color = app.bg_color

        self.stop_requested = threading.Event()
        self.is_copying = False
        self.active_created_folders = []

        # 需求 1: 記憶上次程式開啟時的鎖定/解鎖狀態
        self.is_dashcam_locked = self.app.config.get("is_dashcam_locked", False)
        self.is_bodycam_locked = self.app.config.get("is_bodycam_locked", False)

        # 建立防裁切平滑捲動 Canvas 容器 (自適應各尺寸筆電螢幕，支援滑鼠滾輪)
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0, bg=self.app.bg_color)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.content_frame = ttk.Frame(self.canvas)

        self.content_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas_win = self.canvas.create_window((0, 0), window=self.content_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfig(self.canvas_win, width=e.width))

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.create_widgets()
        self._bind_mousewheel(self.content_frame)

        if self.is_dashcam_locked:
            self.apply_lock_dashcam(True)
        if self.is_bodycam_locked:
            self.apply_lock_bodycam(True)
        # 🚀 啟動性能優化：非同步延遲 50ms 檢測硬體連線，讓主視窗第一時間秒顯在螢幕上
        self.after(50, lambda: self.refresh_device_connections(show_toast=False))

    def _bind_mousewheel(self, widget):
        widget.bind("<MouseWheel>", self._on_mousewheel, add="+")
        widget.bind("<Button-4>", self._on_mousewheel, add="+")
        widget.bind("<Button-5>", self._on_mousewheel, add="+")
        for child in widget.winfo_children():
            self._bind_mousewheel(child)

    def _on_mousewheel(self, event):
        if sys.platform == "darwin":
            self.canvas.yview_scroll(int(-1 * event.delta), "units")
        elif event.num == 5:
            self.canvas.yview_scroll(2, "units")
        elif event.num == 4:
            self.canvas.yview_scroll(-2, "units")
        else:
            self.canvas.yview_scroll(int(-1 * (event.delta / 60)), "units")

    def create_widgets(self):
        pad = {'padx': 8, 'pady': 3}

        # 1. 步驟 ❶：案號與出勤時段
        frame_step1 = ttk.LabelFrame(self.content_frame, text="【步驟 ❶：案號與出勤時段】", padding=6)
        frame_step1.pack(fill="x", **pad)
        self.frame_step1 = frame_step1

        # 1-1. 案件案號 (必須 17 碼)
        f_case_line = ttk.Frame(frame_step1)
        f_case_line.pack(fill="x", pady=2)
        ttk.Label(f_case_line, text="案件案號 (必填 17 碼)：", font=('Helvetica', 10, 'bold')).pack(side="left")
        self.entry_case_no = ttk.Entry(f_case_line, width=22, font=('Helvetica', 10))
        self.entry_case_no.pack(side="left", padx=4)
        self.entry_case_no.bind("<KeyRelease>", self.on_case_no_changed)

        self.lbl_case_len = ttk.Label(f_case_line, text="（需 17 碼，目前 0 碼）", foreground="#666666")
        self.lbl_case_len.pack(side="left", padx=4)

        ttk.Separator(frame_step1, orient="horizontal").pack(fill="x", pady=6)

        # 1-2. 時段過濾核取方塊
        self.var_filter_time = tk.BooleanVar(value=True)
        chk_time = ttk.Checkbutton(
            frame_step1,
            text="啟用勤務時段過濾 (自動連續抓取此區間內的多個分割影片)",
            variable=self.var_filter_time
        )
        chk_time.pack(anchor="w", pady=(0, 4))

        # 1-3. 直式排列的日期與時間輸入區塊 (Grid)
        f_time_grid = ttk.Frame(frame_step1)
        f_time_grid.pack(fill="x", padx=6, pady=2)

        now = datetime.now()
        dispatch_ref = now - timedelta(hours=1)
        default_roc_year = str(dispatch_ref.year - 1911)
        default_mmdd = dispatch_ref.strftime("%m%d")
        default_start_4 = dispatch_ref.strftime("%H%M")
        default_end_4 = ""

        # 第一行：年份（民國）（三碼）
        ttk.Label(f_time_grid, text="出勤年份 (民國 3 碼)：", font=('Helvetica', 9, 'bold'), width=20, anchor="w").grid(row=0, column=0, sticky="w", pady=2)
        self.entry_year = ttk.Entry(f_time_grid, width=10, font=('Helvetica', 9))
        self.entry_year.insert(0, default_roc_year)
        self.entry_year.grid(row=0, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(f_time_grid, text="（例如：113 或 115，民國年份三碼）", foreground="#666666").grid(row=0, column=2, sticky="w", pady=2)

        # 第二行：月份日期（四碼）
        ttk.Label(f_time_grid, text="月份日期 (4 碼 MMDD)：", font=('Helvetica', 9, 'bold'), width=20, anchor="w").grid(row=1, column=0, sticky="w", pady=2)
        self.entry_month_day = ttk.Entry(f_time_grid, width=10, font=('Helvetica', 9))
        self.entry_month_day.insert(0, default_mmdd)
        self.entry_month_day.grid(row=1, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(f_time_grid, text="（例如：1023 表示 10 月 23 日）", foreground="#666666").grid(row=1, column=2, sticky="w", pady=2)

        # 第三行：出勤時間
        ttk.Label(f_time_grid, text="出勤時間 (4 碼 HHMM)：", font=('Helvetica', 9, 'bold'), width=20, anchor="w").grid(row=2, column=0, sticky="w", pady=2)
        self.entry_start_time = ttk.Entry(f_time_grid, width=10, font=('Helvetica', 9))
        self.entry_start_time.insert(0, default_start_4)
        self.entry_start_time.grid(row=2, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(f_time_grid, text="（24小時制，例如：1900、0830）", foreground="#666666").grid(row=2, column=2, sticky="w", pady=2)

        # 第四行：返隊時間
        ttk.Label(f_time_grid, text="返隊時間 (選填 4 碼)：", font=('Helvetica', 9, 'bold'), width=20, anchor="w").grid(row=3, column=0, sticky="w", pady=2)
        self.entry_end_time = ttk.Entry(f_time_grid, width=10, font=('Helvetica', 9))
        self.entry_end_time.insert(0, default_end_4)
        self.entry_end_time.grid(row=3, column=1, sticky="w", padx=6, pady=2)
        ttk.Label(f_time_grid, text="（選填，留空自動抓到出勤後最新影片）", foreground="#666666").grid(row=3, column=2, sticky="w", pady=2)

        # 綁定即時事件
        self.entry_year.bind('<KeyRelease>', lambda e: self.on_date_time_changed())
        self.entry_year.bind('<FocusOut>', lambda e: self.on_date_time_changed())
        self.entry_month_day.bind('<KeyRelease>', lambda e: self.on_date_time_changed())
        self.entry_month_day.bind('<FocusOut>', lambda e: self.on_date_time_changed())
        self.entry_start_time.bind('<KeyRelease>', lambda e: self.on_date_time_changed())
        self.entry_start_time.bind('<FocusOut>', lambda e: self.on_date_time_changed())

        f_note_line = ttk.Frame(frame_step1)
        f_note_line.pack(fill="x", pady=(3, 1))
        self.lbl_note = ttk.Label(
            f_note_line,
            text=f"↳ 備註：今日外層目錄代碼自動為 [{default_mmdd}]，內層依 17 碼案號與同仁姓名分類。",
            foreground="#007ACC",
            font=('Helvetica', 9)
        )
        self.lbl_note.pack(side="left")

        # 換插預約與排班暫存變數
        self.is_swap_reserved_b2 = False
        self.is_swap_reserved_b3 = False
        self.pending_roster_names = []

        # 需求 2: 軟體啟動時先依據前一個任務的紀錄帶入同仁姓名
        # 若已有其他分頁（例如點擊 [ ➕ ] 新增），則優先繼承前一個分頁的同仁
        init_name1 = ""
        init_name2 = ""
        init_name3 = ""
        init_has_third = False
        if self.app.task_tabs:
            prev_t = self.app.task_tabs[-1]
            init_name1 = prev_t.entry_name1.get().strip() if hasattr(prev_t, 'entry_name1') else ""
            init_name2 = prev_t.entry_name2.get().strip() if hasattr(prev_t, 'entry_name2') else ""
            if hasattr(prev_t, 'entry_name3'):
                init_name3 = prev_t.entry_name3.get().strip()
            if hasattr(prev_t, 'var_third_person'):
                init_has_third = prev_t.var_third_person.get()

        # 若前一個分頁為空，或軟體初次啟動時，先依據前一個任務的紀錄帶入
        if not init_name1:
            init_name1 = self.app.config.get("last_name1", "") or self.app.config.get("name1", "")
        if not init_name2:
            init_name2 = self.app.config.get("last_name2", "") or self.app.config.get("name2", "")
        if not init_name3:
            init_name3 = self.app.config.get("last_name3", "") or self.app.config.get("name3", "")
        if not init_has_third:
            init_has_third = self.app.config.get("last_has_third", False)

        # 2. 步驟 ❷：設備連線與出勤人員
        frame_step2 = ttk.LabelFrame(self.content_frame, text="【步驟 ❷：設備連線與出勤人員】", padding=6)
        frame_step2.pack(fill="x", **pad)
        self.frame_step2 = frame_step2

        # 頂部操作列：核心整合按鈕（一鍵智慧偵測連線 ＆ 刷新排班）
        f_top_btns = ttk.Frame(frame_step2)
        f_top_btns.pack(fill="x", pady=(2, 5))

        self.btn_smart_sync = ttk.Button(
            f_top_btns,
            text="⚡ 一鍵智慧偵測連線 ＆ 刷新排班",
            command=self.smart_detect_and_sync_roster
        )
        self.btn_smart_sync.pack(side="left", padx=(0, 8))

        self.lbl_roster_hint = ttk.Label(
            f_top_btns,
            text="(智慧指紋免對齊槽位，自動偵測連線並自動比對排班同仁姓名)",
            foreground="#007ACC",
            font=('Helvetica', 9)
        )
        self.lbl_roster_hint.pack(side="left")

        # 列 0: 行車紀錄器連線狀態 (按需求：不顯示路徑字眼，僅顯示狀態燈號)
        f_dash_status = ttk.Frame(frame_step2)
        f_dash_status.pack(fill="x", pady=2)
        ttk.Label(f_dash_status, text="行車紀錄器：", width=15).pack(side="left")
        self.lbl_status_dashcam = ttk.Label(f_dash_status, text="⚪ 未檢測", width=16, anchor="w", foreground="#888888", font=('Helvetica', 9))
        self.lbl_status_dashcam.pack(side="left", padx=2)
        ttk.Label(f_dash_status, text="↳ (USB 讀卡機；3人出勤時與密錄器看哪台先好就拔哪台交換)", foreground="#666666", font=('Helvetica', 9)).pack(side="left", padx=6)

        # 列 1: 密錄器 第一台 (狀態燈號移至前面與行車標齊，後接出勤同仁)
        f_name1 = ttk.Frame(frame_step2)
        f_name1.pack(fill="x", pady=2)
        ttk.Label(f_name1, text="密錄器：第一台", width=15).pack(side="left")
        self.lbl_status_name1 = ttk.Label(f_name1, text="⚪ 未檢測", width=16, anchor="w", foreground="#888888", font=('Helvetica', 9))
        self.lbl_status_name1.pack(side="left", padx=2)
        ttk.Label(f_name1, text="出勤同仁：").pack(side="left", padx=(4, 2))
        self.entry_name1 = ttk.Combobox(f_name1, width=15, values=self.app.get_all_officer_names())
        if init_name1:
            self.entry_name1.set(init_name1)
        self.entry_name1.pack(side="left", padx=2)
        self.bind_officer_autocomplete(self.entry_name1)
        self.btn_open_folder_b1 = ttk.Button(f_name1, text="👤 查看人員", command=lambda: self.open_device_video_folder(1))

        # 列 2: 密錄器 第二台 (狀態燈號移至前面與行車標齊，後接出勤同仁與預先開放換插核取方塊)
        f_name2 = ttk.Frame(frame_step2)
        f_name2.pack(fill="x", pady=2)
        ttk.Label(f_name2, text="密錄器：第二台", width=15).pack(side="left")
        self.lbl_status_name2 = ttk.Label(f_name2, text="⚪ 未檢測", width=16, anchor="w", foreground="#888888", font=('Helvetica', 9))
        self.lbl_status_name2.pack(side="left", padx=2)
        ttk.Label(f_name2, text="出勤同仁：").pack(side="left", padx=(4, 2))
        self.entry_name2 = ttk.Combobox(f_name2, width=15, values=self.app.get_all_officer_names())
        if init_name2:
            self.entry_name2.set(init_name2)
        self.entry_name2.pack(side="left", padx=2)
        self.bind_officer_autocomplete(self.entry_name2)
        self.btn_open_folder_b2 = ttk.Button(f_name2, text="👤 查看人員", command=lambda: self.open_device_video_folder(2))
        self.var_preopen_b2 = tk.BooleanVar(value=False)
        self.chk_preopen_b2 = ttk.Checkbutton(f_name2, text="預先開放換插", variable=self.var_preopen_b2, command=self.on_toggle_preopen_b2)
        self.chk_preopen_b2.pack(side="left", padx=6)
        self.btn_swap_b2 = self.chk_preopen_b2  # 向後相容屬性參照

        # 列 3: 密錄器 第三台 (狀態燈號移至前面與行車標齊，後接出勤同仁與預先開放換插核取方塊)
        f_name3 = ttk.Frame(frame_step2)
        f_name3.pack(fill="x", pady=2)
        ttk.Label(f_name3, text="密錄器：第三台", width=15).pack(side="left")
        self.lbl_status_name3 = ttk.Label(f_name3, text="⚪ 未檢測", width=16, anchor="w", foreground="#888888", font=('Helvetica', 9))
        self.lbl_status_name3.pack(side="left", padx=2)
        ttk.Label(f_name3, text="出勤同仁：").pack(side="left", padx=(4, 2))
        self.entry_name3 = ttk.Combobox(f_name3, width=15, values=self.app.get_all_officer_names())
        if init_name3:
            self.entry_name3.set(init_name3)
        self.entry_name3.pack(side="left", padx=2)
        self.bind_officer_autocomplete(self.entry_name3)
        self.btn_open_folder_b3 = ttk.Button(f_name3, text="👤 查看人員", command=lambda: self.open_device_video_folder(3))
        self.var_preopen_b3 = tk.BooleanVar(value=False)
        self.chk_preopen_b3 = ttk.Checkbutton(f_name3, text="預先開放換插", variable=self.var_preopen_b3, command=self.on_toggle_preopen_b3)
        self.chk_preopen_b3.pack(side="left", padx=6)
        self.btn_swap_b3 = self.chk_preopen_b3  # 向後相容屬性參照
        self.lbl_hint_b3 = ttk.Label(f_name3, text="↳ (3人出勤換插；電腦3孔，任一設備完成後自動提示換插)", foreground="#007ACC", font=('Helvetica', 9))
        self.lbl_hint_b3.pack(side="left", padx=2)

        # 列 4: 姓名快速對調區 (含 1⇄2、2⇄3、1⇄3、1➔2➔3 輪替)
        f_swap_line = ttk.Frame(frame_step2)
        f_swap_line.pack(fill="x", pady=(3, 2))
        ttk.Label(f_swap_line, text="姓名快速對調：", width=14).pack(side="left")
        ttk.Button(f_swap_line, text="⇅ 1 ⇄ 2 號", command=self.swap_1_2, width=10).pack(side="left", padx=2)
        ttk.Button(f_swap_line, text="⇅ 2 ⇄ 3 號", command=self.swap_2_3, width=10).pack(side="left", padx=2)
        ttk.Button(f_swap_line, text="⇅ 1 ⇄ 3 號", command=self.swap_1_3, width=10).pack(side="left", padx=2)
        ttk.Button(f_swap_line, text="🔄 1➔2➔3 輪替", command=self.rotate_names, width=13).pack(side="left", padx=2)
        ttk.Label(f_swap_line, text="↳ (單鍵互換或輪替順移)", foreground="#666666", font=('Helvetica', 9)).pack(side="left", padx=2)

        # 3. 步驟 ❸：開始比對並自動複製 (移至路徑設定上方)
        frame_step3 = ttk.LabelFrame(self.content_frame, text="【步驟 ❸：開始比對並自動複製】", padding=6)
        frame_step3.pack(fill="x", **pad)
        self.frame_step3 = frame_step3

        f_btns = ttk.Frame(frame_step3)
        f_btns.pack(fill="x", pady=(2, 3))

        self.btn_start = ttk.Button(f_btns, text="🚀 開始比對時段並自動複製", command=self.start_process, style="Action.TButton")
        self.btn_start.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 4))

        self.btn_pause = ttk.Button(f_btns, text="⛔ 暫停並放棄 (刪除已複製檔案)", command=self.pause_and_cleanup, state="disabled")
        self.btn_pause.pack(side="right", ipady=4, padx=(4, 0))

        self.progress_bar = ttk.Progressbar(frame_step3, orient="horizontal", mode="determinate")
        self.progress_bar.pack(fill="x", pady=2)

        self.lbl_status = ttk.Label(frame_step3, text="狀態：準備就緒", foreground="#0066CC", font=('Helvetica', 9, 'bold'))
        self.lbl_status.pack(anchor="w")

        # 4. 基本設定大區域 (影像儲存目標目錄，支援隱藏/展開；隨身碟來源已由智慧指紋全自動接管)
        frame_base_settings = ttk.LabelFrame(self.content_frame, text="【基本常設設定：影像儲存目標目錄】", padding=6)
        frame_base_settings.pack(fill="x", **pad)
        self.frame_base_settings = frame_base_settings

        f_base_header = ttk.Frame(frame_base_settings)
        f_base_header.pack(fill="x", pady=(1, 3))

        ttk.Label(
            f_base_header,
            text="💡 設備來源隨身碟已由「智慧特徵指紋」全自動偵測接管，無須指定！此處僅需確認影像存放目標目錄：",
            font=('Helvetica', 9),
            foreground="#007ACC"
        ).pack(side="left")

        # 讀取使用者折疊偏好（預設為展開 False，使用者點擊後記住狀態）
        self.is_base_settings_hidden = self.app.config.get("hide_base_settings", False)

        self.btn_toggle_base = ttk.Button(
            f_base_header,
            text="🔼 隱藏此區域 (目錄設定)" if not self.is_base_settings_hidden else "🔽 展開此區域 (目錄設定)",
            command=self.toggle_base_settings,
            width=18
        )
        self.btn_toggle_base.pack(side="right")

        # 內部折疊容器 (僅包覆行車紀錄器與密錄器存放目錄)
        self.f_base_content = ttk.Frame(frame_base_settings)
        if not self.is_base_settings_hidden:
            self.f_base_content.pack(fill="x", pady=(2, 0))

        # 4-1. 行車紀錄器存放目錄 (含「🔒 鎖定 / 🔓 解鎖」按鈕)
        frame_dashcam = ttk.LabelFrame(self.f_base_content, text="【行車紀錄器存放目錄】", padding=6)
        frame_dashcam.pack(fill="x", pady=(2, 4))
        self.frame_dashcam = frame_dashcam

        f_dash_target = ttk.Frame(frame_dashcam)
        f_dash_target.pack(fill="x", pady=2)
        ttk.Label(f_dash_target, text="存放目錄：", width=12).pack(side="left")
        self.entry_dashcam_target = ttk.Entry(f_dash_target, width=38)
        self.entry_dashcam_target.insert(0, self.app.config.get("dashcam_target", ""))
        self.entry_dashcam_target.pack(side="left", padx=4)
        self.btn_dash_target = ttk.Button(f_dash_target, text="變更目錄", command=lambda: self.browse_path(self.entry_dashcam_target, "選擇行車紀錄器存放目錄"))
        self.btn_dash_target.pack(side="left")
        self.btn_lock_dashcam = ttk.Button(f_dash_target, text="🔒 鎖定", command=self.toggle_lock_dashcam, width=8)
        self.btn_lock_dashcam.pack(side="right")

        # 4-2. 密錄器存放目錄 (含「🔒 鎖定 / 🔓 解鎖」按鈕)
        frame_bodycam = ttk.LabelFrame(self.f_base_content, text="【密錄器存放目錄】", padding=6)
        frame_bodycam.pack(fill="x", pady=(2, 2))
        self.frame_bodycam = frame_bodycam

        f_body_target = ttk.Frame(frame_bodycam)
        f_body_target.pack(fill="x", pady=2)
        ttk.Label(f_body_target, text="存放目錄：", width=12).pack(side="left")
        self.entry_bodycam_target = ttk.Entry(f_body_target, width=38)
        self.entry_bodycam_target.insert(0, self.app.config.get("bodycam_target", ""))
        self.entry_bodycam_target.pack(side="left", padx=4)
        self.btn_body_target = ttk.Button(f_body_target, text="變更目錄", command=lambda: self.browse_path(self.entry_bodycam_target, "選擇密錄器存放目錄"))
        self.btn_body_target.pack(side="left")
        self.btn_lock_bodycam = ttk.Button(f_body_target, text="🔒 鎖定", command=self.toggle_lock_bodycam, width=8)
        self.btn_lock_bodycam.pack(side="right")

        # 來源隨身碟變數 (不 pack 到 UI 上，一律由即時智慧偵測引擎動態接管，不載入過期 USB 代號)
        self.entry_dashcam_src = ttk.Entry(self.f_base_content)
        self.entry_bodycam1_src = ttk.Entry(self.f_base_content)
        self.entry_bodycam2_src = ttk.Entry(self.f_base_content)
        self.entry_bodycam3_src = ttk.Entry(self.f_base_content)

        # 5. 詳細紀錄視窗
        frame_log = ttk.LabelFrame(self.content_frame, text="【傳輸紀錄】", padding=4)
        frame_log.pack(fill="both", expand=True, **pad)

        self.txt_log = tk.Text(
            frame_log, height=5, font=("Courier", 10),
            bg="#FFFFFF", fg="#111111", insertbackground="#111111",
            relief="solid", bd=1
        )
        self.txt_log.pack(fill="both", expand=True)

    def get_parsed_date_info(self):
        """解析當前分頁輸入之民國年份與月日
        回傳: (ad_year, month, day, error_msg)
        """
        year_str = self.entry_year.get().strip() if hasattr(self, 'entry_year') else ""
        md_str = self.entry_month_day.get().strip() if hasattr(self, 'entry_month_day') else ""

        if not year_str or not md_str:
            return None, None, None, "請填寫出勤年份（民國3碼）與月份日期（4碼）！"

        # 驗證年份 (民國 2 或 3 碼數字，例如 113、115)
        if not (year_str.isdigit() and len(year_str) in (2, 3)):
            return None, None, None, f"出勤年份格式錯誤：【{year_str}】\n請輸入民國 3 碼數字（例如：113 或 115）！"

        # 驗證月日 (4 碼數字 MMDD)
        if not (md_str.isdigit() and len(md_str) == 4):
            return None, None, None, f"月份日期格式錯誤：【{md_str}】\n請輸入 4 碼數字 MMDD（例如：1023 表示 10 月 23 日）！"

        roc_year = int(year_str)
        ad_year = roc_year + 1911
        month = int(md_str[:2])
        day = int(md_str[2:])

        if not (1 <= month <= 12 and 1 <= day <= 31):
            return None, None, None, f"月份日期數值不正確：【{month}月{day}日】\n請檢查月份 (01~12) 與日期 (01~31)！"

        return ad_year, month, day, None

    @property
    def entry_date(self):
        """相容性屬性：提供 8 碼 YYYYMMDD 字串"""
        class _PseudoDateEntry:
            def __init__(self, tab):
                self.tab = tab
            def get(self):
                ad_y, m, d, _ = self.tab.get_parsed_date_info()
                if ad_y and m and d:
                    return f"{ad_y:04d}{m:02d}{d:02d}"
                return ""
        return _PseudoDateEntry(self)

    def on_case_no_changed(self, event=None):
        """當輸入案號時，即時更新字數統計與分頁標題"""
        case_no = self.entry_case_no.get().strip()
        c_len = len(case_no)
        if hasattr(self, 'lbl_case_len'):
            if c_len == 0:
                self.lbl_case_len.config(text="（需 17 碼，目前 0 碼）", foreground="#666666")
            elif c_len < 17:
                self.lbl_case_len.config(text=f"⚠️ 需 17 碼，目前 {c_len} 碼 (尚差 {17 - c_len} 碼)", foreground="#D32F2F")
            elif c_len == 17:
                self.lbl_case_len.config(text="🟢 已達 17 碼 (格式正確)", foreground="#2E7D32")
            else:
                self.lbl_case_len.config(text=f"⚠️ 超過 17 碼，目前 {c_len} 碼 (多出 {c_len - 17} 碼)", foreground="#D32F2F")
        self.app.update_tab_labels()

    def on_date_time_changed(self):
        """當出勤日期或時間變更時，依程式邏輯判斷對應之勤務表檔案，即時更新下拉名冊與備註提示"""
        if hasattr(self, 'lbl_note') and hasattr(self, 'entry_month_day'):
            md = self.entry_month_day.get().strip()
            code_str = md if len(md) == 4 and md.isdigit() else datetime.now().strftime("%m%d")
            self.lbl_note.config(text=f"↳ 備註：今日外層目錄代碼自動為 [{code_str}]，內層依 17 碼案號與同仁姓名分類。")
        self.update_combobox_values()

    def get_current_tab_officer_names(self):
        """依當前分頁的出勤日期與時間，依程式邏輯判斷所讀取之勤務表檔案為主取得同仁名單"""
        ad_year, m, d, _ = self.get_parsed_date_info()
        date_input = f"{ad_year:04d}{m:02d}{d:02d}" if (m and d) else ""
        time_input = self.entry_start_time.get().strip() if hasattr(self, 'entry_start_time') else ""
        start_hour = extract_hour(time_input) if time_input else 8

        roster_path = self.app.config.get("roster_path", "").strip()
        if roster_path and os.path.exists(roster_path):
            # 1. 若指定的路徑直接是單一檔案 (.xlsm / .xlsx)
            if os.path.isfile(roster_path):
                names = self.app.get_roster_names_from_file(roster_path)
                if names:
                    return names
            # 2. 若為資料夾，依出勤日期搜尋對應之當日勤務表檔案
            if date_input:
                duty_y, duty_m, duty_d, is_prev = get_duty_date(date_input, start_hour if start_hour is not None else 8)
                if duty_m and duty_d:
                    fp, _, _ = find_roster_file_for_date(roster_path, duty_m, duty_d)
                    if fp and os.path.isfile(fp):
                        names = self.app.get_roster_names_from_file(fp)
                        if names:
                            return names
            # 3. 若當日檔案未找到，但最新載入之名冊有效，直接採用該名冊
            if hasattr(self.app, 'officer_names') and self.app.officer_names:
                return self.app.officer_names
        return self.app.get_all_officer_names()

    def bind_officer_autocomplete(self, cb):
        """為同仁姓名 Combobox 綁定即時字元過濾與下拉選單自動彈出（依出勤日期時間對應之勤務表為主）"""
        def on_key_release(event):
            if event.keysym in ('Up', 'Down', 'Left', 'Right', 'Return', 'Escape', 'Tab',
                                'Shift_L', 'Shift_R', 'Control_L', 'Control_R', 'Alt_L', 'Alt_R'):
                return
            typed = cb.get().strip()
            all_names = self.get_current_tab_officer_names()
            if typed:
                matches = [n for n in all_names if typed in n]
                cb['values'] = matches if matches else all_names
            else:
                cb['values'] = all_names
            # 若有輸入字元且有比對到結果，自動展開下拉清單供快速點選
            if matches and typed:
                try:
                    cb.tk.eval(f'catch {{ttk::combobox::Post {cb}}}')
                except:
                    pass

        def on_dropdown_open(event=None):
            # 點擊下拉箭頭或點擊欄位時，永遠重設為名冊完整名單，避免被目前已填人名過濾只剩1人
            all_names = self.get_current_tab_officer_names()
            cb['values'] = all_names

        cb.bind('<KeyRelease>', on_key_release)
        cb.bind('<FocusIn>', on_dropdown_open)
        cb.bind('<Button-1>', on_dropdown_open)
        try:
            cb.configure(postcommand=on_dropdown_open)
        except:
            pass

    def update_combobox_values(self):
        """更新本分頁所有同仁姓名下拉選單的選項（以當前出勤日期時間所對應之勤務表檔案為主）"""
        tab_names = self.get_current_tab_officer_names()
        for cb in (self.entry_name1, self.entry_name2, self.entry_name3):
            if hasattr(self, 'entry_name1') and cb:
                cb['values'] = tab_names

    def swap_1_2(self):
        """互換密錄器 1 與 2 之同仁姓名"""
        if str(self.entry_name1.cget("state")) == "disabled" or str(self.entry_name2.cget("state")) == "disabled":
            messagebox.showinfo("無法對調", "密錄器 1 或 密錄器 2 尚未連線（反灰關閉），無法對調姓名！\n請先插上設備並完成連線。")
            return
        n1 = self.entry_name1.get().strip()
        n2 = self.entry_name2.get().strip()
        self.entry_name1.set(n2)
        self.entry_name2.set(n1)
        self.log(f"🔄 [同仁姓名互換] 密錄器 1 ⇄ 密錄器 2 姓名已成功對調！({n1 or '空'} ⇄ {n2 or '空'})")
        self.app.save_config()

    def swap_2_3(self):
        """互換密錄器 2 與 3 之同仁姓名"""
        if str(self.entry_name2.cget("state")) == "disabled" or str(self.entry_name3.cget("state")) == "disabled":
            messagebox.showinfo("無法對調", "密錄器 2 或 密錄器 3 尚未連線（反灰關閉），無法對調姓名！\n請先插上設備並完成連線。")
            return
        n2 = self.entry_name2.get().strip()
        n3 = self.entry_name3.get().strip()
        self.entry_name2.set(n3)
        self.entry_name3.set(n2)
        self.log(f"🔄 [同仁姓名互換] 密錄器 2 ⇄ 密錄器 3 姓名已成功對調！({n2 or '空'} ⇄ {n3 or '空'})")
        self.app.save_config()

    def swap_1_3(self):
        """互換密錄器 1 與 3 之同仁姓名"""
        if str(self.entry_name1.cget("state")) == "disabled" or str(self.entry_name3.cget("state")) == "disabled":
            messagebox.showinfo("無法對調", "密錄器 1 或 密錄器 3 尚未連線（反灰關閉），無法對調姓名！\n請先插上設備並完成連線。")
            return
        n1 = self.entry_name1.get().strip()
        n3 = self.entry_name3.get().strip()
        self.entry_name1.set(n3)
        self.entry_name3.set(n1)
        self.log(f"🔄 [同仁姓名互換] 密錄器 1 ⇄ 密錄器 3 姓名已成功對調！({n1 or '空'} ⇄ {n3 or '空'})")
        self.app.save_config()

    def rotate_names(self):
        """密錄器 1 ➔ 2 ➔ 3 順時針姓名順序輪替"""
        if (str(self.entry_name1.cget("state")) == "disabled" or
            str(self.entry_name2.cget("state")) == "disabled" or
            str(self.entry_name3.cget("state")) == "disabled"):
            messagebox.showinfo("無法輪替", "3 人輪替需 3 部密錄器皆連線開放！\n目前有部分設備未連線（反灰關閉），請先使用雙人對調按鈕。")
            return
        n1 = self.entry_name1.get().strip()
        n2 = self.entry_name2.get().strip()
        n3 = self.entry_name3.get().strip()
        new_1, new_2, new_3 = n3, n1, n2
        self.entry_name1.set(new_1)
        self.entry_name2.set(new_2)
        self.entry_name3.set(new_3)
        self.log(f"🔄 [姓名順序輪替] 密錄器 1➔2➔3 輪替完畢！({n1 or '空'}➔{n2 or '空'}➔{n3 or '空'})")
        self.app.save_config()

    def swap_names(self):
        """互換密錄器 1 與 2 之同仁姓名 (相容舊呼叫)"""
        self.swap_1_2()

    def toggle_third_person(self):
        """相容舊版介面"""
        pass

    def toggle_manual_unlock3(self):
        """相容舊版手動解鎖按鈕呼叫"""
        self.toggle_swap_b3()

    def on_toggle_preopen_b2(self):
        """【由使用者決定是否預先開】密錄器 第二台 預先開放換插核取方塊切換"""
        is_checked = self.var_preopen_b2.get()
        names = getattr(self, 'pending_roster_names', [])
        target_name = names[1] if len(names) >= 2 else ""

        if is_checked:
            self.is_swap_reserved_b2 = True
            self.entry_name2.config(state="normal")
            if target_name and not self.entry_name2.get().strip():
                self.entry_name2.set(target_name)
            self.lbl_status_name2.config(text="⏳ 待換插", foreground="#E65100")
            chk_txt = f"已預先開放 (換插: {target_name})" if target_name else "已預先開放 (待換插)"
            self.chk_preopen_b2.config(text=chk_txt)
            curr_val = self.entry_name2.get().strip()
            self.log(f"⏳ [預先開放] 密錄器 第二台 已開放為待換插 (出勤同仁: {curr_val or '待選'})。")
            self.update_status(f"狀態：密錄器 第二台 已預先開放【{curr_val or '接續同仁'}】", "#E65100")
        else:
            self.is_swap_reserved_b2 = False
            self.entry_name2.config(state="normal")
            self.entry_name2.set("")
            self.entry_name2.config(state="disabled")
            self.lbl_status_name2.config(text="⚪ 未連線", foreground="#888888")
            chk_txt = f"預先開放換插 ({target_name})" if target_name else "預先開放換插"
            self.chk_preopen_b2.config(text=chk_txt)
            self.log("ℹ️ [關閉開放] 密錄器 第二台 已關閉，恢復反灰。")
            self.update_status("狀態：密錄器 第二台 已關閉", "#555555")

    def on_toggle_preopen_b3(self):
        """【由使用者決定是否預先開】密錄器 第三台 預先開放換插核取方塊切換"""
        is_checked = self.var_preopen_b3.get()
        names = getattr(self, 'pending_roster_names', [])
        target_name = ""
        if len(names) >= 3:
            target_name = names[2]
        elif len(names) == 2 and not getattr(self, 'is_swap_reserved_b2', False):
            target_name = names[1]

        if is_checked:
            self.is_swap_reserved_b3 = True
            self.entry_name3.config(state="normal")
            if target_name and not self.entry_name3.get().strip():
                self.entry_name3.set(target_name)
            self.lbl_status_name3.config(text="⏳ 待換插", foreground="#E65100")
            chk_txt = f"已預先開放 (換插: {target_name})" if target_name else "已預先開放 (待換插)"
            self.chk_preopen_b3.config(text=chk_txt)
            curr_val = self.entry_name3.get().strip()
            self.log(f"⏳ [預先開放] 密錄器 第三台 已開放為待換插 (出勤同仁: {curr_val or '待選'})。")
            self.update_status(f"狀態：密錄器 第三台 已預先開放【{curr_val or '接續同仁'}】", "#E65100")
        else:
            self.is_swap_reserved_b3 = False
            self.entry_name3.config(state="normal")
            self.entry_name3.set("")
            self.entry_name3.config(state="disabled")
            self.lbl_status_name3.config(text="⚪ 未連線", foreground="#888888")
            chk_txt = f"預先開放換插 ({target_name})" if target_name else "預先開放換插"
            self.chk_preopen_b3.config(text=chk_txt)
            self.log("ℹ️ [關閉開放] 密錄器 第三台 已關閉，恢復反灰。")
            self.update_status("狀態：密錄器 第三台 已關閉", "#555555")

    def toggle_swap_b2(self):
        """向後相容呼叫"""
        self.var_preopen_b2.set(not self.var_preopen_b2.get())
        self.on_toggle_preopen_b2()

    def toggle_swap_b3(self):
        """向後相容呼叫"""
        self.var_preopen_b3.set(not self.var_preopen_b3.get())
        self.on_toggle_preopen_b3()

    def set_src_path(self, entry, path):
        """安全寫入來源設備路徑至 Entry（自動防禦 disabled 狀態並確保內容精確更新）"""
        if entry is None:
            return
        cur_state = str(entry.cget("state"))
        if cur_state == "disabled":
            entry.config(state="normal")
        entry.delete(0, tk.END)
        if path:
            entry.insert(0, str(path).strip())
        if cur_state == "disabled":
            entry.config(state="disabled")

    def open_device_video_folder(self, slot_idx):
        """【👤 查看人員】直接進入該密錄器影片所在資料夾並開啟，供同仁確認佩戴者"""
        src_entry_map = {
            1: getattr(self, 'entry_bodycam1_src', None),
            2: getattr(self, 'entry_bodycam2_src', None),
            3: getattr(self, 'entry_bodycam3_src', None),
        }
        slot_name_map = {1: "第一台", 2: "第二台", 3: "第三台"}
        entry = src_entry_map.get(slot_idx)
        slot_name = slot_name_map.get(slot_idx, "密錄器")

        if not entry:
            return

        raw_path = entry.get().strip()
        # 防呆即時刷新：若 Entry 內路徑為空或不存在，自動即時刷新設備連線重新鎖定
        if not raw_path or not os.path.exists(raw_path):
            self.refresh_device_connections(show_toast=False)
            raw_path = entry.get().strip()

        if not raw_path or not os.path.exists(raw_path):
            messagebox.showwarning(
                "設備未連線",
                f"【密錄器：{slot_name}】目前尚未連線或隨身碟已被移除！\n\n請確認設備是否插好後再試。"
            )
            return

        target_folder = self.get_device_video_directory(raw_path)
        if target_folder and os.path.exists(target_folder):
            self.log(f"📂 [開啟人員影片] 正在開啟【密錄器：{slot_name}】：{target_folder}")
            open_folder_in_explorer(target_folder)
        else:
            self.log(f"📂 [開啟隨身碟根目錄] 正在開啟【密錄器：{slot_name}】：{raw_path}")
            open_folder_in_explorer(raw_path)

    def get_device_video_directory(self, device_path):
        """智慧尋找密錄器隨身碟內實際存放影片之最深層資料夾"""
        if not device_path or not os.path.exists(device_path):
            return None

        # 優先檢查常見密錄器影片目錄層級（含創見 DrivePro Body DPB30/10/60/70）
        candidates = [
            os.path.join(device_path, "DPB30", "VIDEO"),
            os.path.join(device_path, "DPB30", "MOVIE"),
            os.path.join(device_path, "DPB30", "NORMAL"),
            os.path.join(device_path, "DPB60", "VIDEO"),
            os.path.join(device_path, "DPB70", "VIDEO"),
            os.path.join(device_path, "DPB10", "VIDEO"),
            os.path.join(device_path, "DCIM", "100MEDIA"),
            os.path.join(device_path, "DCIM", "100DSCIM"),
            os.path.join(device_path, "DCIM", "100VIDEO"),
            os.path.join(device_path, "DCIM", "MOVIE"),
            os.path.join(device_path, "DCIM"),
            os.path.join(device_path, "VIDEO"),
            os.path.join(device_path, "VIDEOS"),
            os.path.join(device_path, "RECORD"),
            os.path.join(device_path, "MOVIE"),
            os.path.join(device_path, "NORMAL"),
        ]
        for c in candidates:
            if os.path.exists(c) and os.path.isdir(c):
                return c

        # 若上述常見結構不存在，快速在 3 層目錄內搜尋含有影片檔之資料夾
        try:
            for root, dirs, files in os.walk(device_path):
                rel = os.path.relpath(root, device_path)
                depth = len(rel.split(os.sep)) if rel != "." else 0
                if any(Path(f).suffix.lower() in VIDEO_EXTENSIONS for f in files):
                    return root
                if depth >= 3:
                    dirs.clear()
        except Exception:
            pass

        # 最後保底：若有 DPB30 等品牌目錄，直接開啟該目錄
        for fallback_dir in ("DPB30", "DPB60", "DPB70", "DPB10"):
            fp = os.path.join(device_path, fallback_dir)
            if os.path.exists(fp) and os.path.isdir(fp):
                return fp

        return device_path

    def update_swap_buttons_visibility(self):
        """動態更新第二台與第三台之預先開放換插核取方塊文字與顯示"""
        names = getattr(self, 'pending_roster_names', [])
        name2 = names[1] if len(names) >= 2 else ""
        name3 = names[2] if len(names) >= 3 else ""

        # 第二台勾選框文字
        if hasattr(self, 'chk_preopen_b2'):
            if self.var_preopen_b2.get():
                self.chk_preopen_b2.config(text=f"已預先開放 (換插: {name2})" if name2 else "已預先開放 (待換插)")
            else:
                self.chk_preopen_b2.config(text=f"預先開放換插 ({name2})" if name2 else "預先開放換插")

        # 第三台勾選框文字
        if hasattr(self, 'chk_preopen_b3'):
            if self.var_preopen_b3.get():
                self.chk_preopen_b3.config(text=f"已預先開放 (換插: {name3})" if name3 else "已預先開放 (待換插)")
            else:
                self.chk_preopen_b3.config(text=f"預先開放換插 ({name3})" if name3 else "預先開放換插")

    def get_scan_exclude_paths(self):
        """取得目前設定的本機存放目錄與勤務表路徑，供掃描引擎排除本機硬碟分割區"""
        paths = []
        if hasattr(self, 'entry_dashcam_target'):
            p = self.entry_dashcam_target.get().strip()
            if p:
                paths.append(p)
        if hasattr(self, 'entry_bodycam_target'):
            p = self.entry_bodycam_target.get().strip()
            if p:
                paths.append(p)
        rp = self.app.config.get("roster_path", "").strip()
        if rp:
            paths.append(rp)
        return paths

    def refresh_device_connections(self, show_toast=False, pre_scanned=None):
        """【出勤設備連線狀態刷新 ＆ 依連線順序開啟反白防呆（單一真相來源）】
        核心原則：
        1. 統一由 scan_all_ems_devices() 智慧掃描引擎辨識所有外接設備（支援 pre_scanned 避免重複掃描），
           絕不再用殘缺規則覆蓋結果，也絕不盲信過期 config.json 殘留之 USB 磁碟機代號！
        2. 密錄器不限定死板硬體代號 (BODYCAM1/2/3)，依連線順序開啟反白：
           連線 1 台 ➔ 開啟第一台；連線 2 台 ➔ 依序開啟第一、二台；連線 3 台 ➔ 全數開啟。
        3. 未連線之槽位：確實清空內部路徑並反灰關閉，由使用者透過「預先開放換插」核取方塊自主決定是否預先開！
        4. 已連線之槽位：後方提供「👤 查看人員」按鈕，方便同仁一鍵開啟該設備影片資料夾確認佩戴同仁！
        """
        if pre_scanned is not None:
            scan_res = pre_scanned
        else:
            if hasattr(self.app, 'sync_fingerprint_settings_to_config'):
                self.app.sync_fingerprint_settings_to_config()
            scan_res = scan_all_ems_devices(
                config=self.app.config,
                quick_mode=True,
                exclude_paths=self.get_scan_exclude_paths()
            )

        dash_info = scan_res.get('dashcam')
        body_list = scan_res.get('bodycams', [])
        connected_bodycams = [b['path'] for b in body_list if b.get('path') and os.path.exists(b['path'])]

        # 1. 行車紀錄器獨立狀態更新
        if dash_info and dash_info.get('path') and os.path.exists(dash_info['path']):
            dash_path = dash_info['path']
            self.set_src_path(self.entry_dashcam_src, dash_path)
            _, msg_dash = is_device_connected(dash_path, "")
            if hasattr(self, 'lbl_status_dashcam'):
                self.lbl_status_dashcam.config(text=f"🟢 {msg_dash}", foreground="#2E7D32")
        else:
            self.set_src_path(self.entry_dashcam_src, "")
            if hasattr(self, 'lbl_status_dashcam'):
                self.lbl_status_dashcam.config(text="⚪ 未連線", foreground="#888888")

        # 2. 密錄器依連線順序開啟反白，連線幾台就依序開
        # 第一台
        if len(connected_bodycams) >= 1:
            p1 = connected_bodycams[0]
            self.set_src_path(self.entry_bodycam1_src, p1)
            _, msg1 = is_device_connected(p1, "")
            self.lbl_status_name1.config(text=f"🟢 {msg1}", foreground="#2E7D32")
            self.entry_name1.config(state="normal")
            if hasattr(self, 'btn_open_folder_b1') and self.btn_open_folder_b1.winfo_manager() != "pack":
                self.btn_open_folder_b1.pack(side="left", padx=6)
        else:
            self.set_src_path(self.entry_bodycam1_src, "")
            self.lbl_status_name1.config(text="⚪ 未連線", foreground="#888888")
            if str(self.entry_name1.cget("state")) != "normal":
                self.entry_name1.config(state="normal")
            self.entry_name1.set("")
            self.entry_name1.config(state="disabled")
            if hasattr(self, 'btn_open_folder_b1'):
                self.btn_open_folder_b1.pack_forget()

        # 第二台
        if len(connected_bodycams) >= 2:
            p2 = connected_bodycams[1]
            self.set_src_path(self.entry_bodycam2_src, p2)
            _, msg2 = is_device_connected(p2, "")
            self.lbl_status_name2.config(text=f"🟢 {msg2}", foreground="#2E7D32")
            self.entry_name2.config(state="normal")
            self.is_swap_reserved_b2 = False
            self.var_preopen_b2.set(False)
            if hasattr(self, 'chk_preopen_b2'):
                self.chk_preopen_b2.pack_forget()
            if hasattr(self, 'btn_open_folder_b2') and self.btn_open_folder_b2.winfo_manager() != "pack":
                self.btn_open_folder_b2.pack(side="left", padx=6)
        else:
            self.set_src_path(self.entry_bodycam2_src, "")
            # 第二台未連線：隱藏查看資料夾，顯示勾選方塊由使用者決定是否預先開
            if hasattr(self, 'btn_open_folder_b2'):
                self.btn_open_folder_b2.pack_forget()
            if hasattr(self, 'chk_preopen_b2') and self.chk_preopen_b2.winfo_manager() != "pack":
                self.chk_preopen_b2.pack(side="left", padx=6)
            if self.var_preopen_b2.get():
                self.lbl_status_name2.config(text="⏳ 待換插", foreground="#E65100")
                self.entry_name2.config(state="normal")
                self.is_swap_reserved_b2 = True
            else:
                self.lbl_status_name2.config(text="⚪ 未連線", foreground="#888888")
                if str(self.entry_name2.cget("state")) != "normal":
                    self.entry_name2.config(state="normal")
                self.entry_name2.set("")
                self.entry_name2.config(state="disabled")
                self.is_swap_reserved_b2 = False

        # 第三台
        if len(connected_bodycams) >= 3:
            p3 = connected_bodycams[2]
            self.set_src_path(self.entry_bodycam3_src, p3)
            _, msg3 = is_device_connected(p3, "")
            self.lbl_status_name3.config(text=f"🟢 {msg3}", foreground="#2E7D32")
            self.entry_name3.config(state="normal")
            self.is_swap_reserved_b3 = False
            self.var_preopen_b3.set(False)
            if hasattr(self, 'chk_preopen_b3'):
                self.chk_preopen_b3.pack_forget()
            if hasattr(self, 'btn_open_folder_b3') and self.btn_open_folder_b3.winfo_manager() != "pack":
                if hasattr(self, 'lbl_hint_b3') and self.lbl_hint_b3.winfo_manager() == "pack":
                    self.btn_open_folder_b3.pack(side="left", padx=6, before=self.lbl_hint_b3)
                else:
                    self.btn_open_folder_b3.pack(side="left", padx=6)
        else:
            self.set_src_path(self.entry_bodycam3_src, "")
            # 第三台未連線：隱藏查看資料夾，顯示勾選方塊由使用者決定是否預先開
            if hasattr(self, 'btn_open_folder_b3'):
                self.btn_open_folder_b3.pack_forget()
            if hasattr(self, 'chk_preopen_b3') and self.chk_preopen_b3.winfo_manager() != "pack":
                if hasattr(self, 'lbl_hint_b3') and self.lbl_hint_b3.winfo_manager() == "pack":
                    self.chk_preopen_b3.pack(side="left", padx=6, before=self.lbl_hint_b3)
                else:
                    self.chk_preopen_b3.pack(side="left", padx=6)
            if self.var_preopen_b3.get():
                self.lbl_status_name3.config(text="⏳ 待換插", foreground="#E65100")
                self.entry_name3.config(state="normal")
                self.is_swap_reserved_b3 = True
            else:
                self.lbl_status_name3.config(text="⚪ 未連線", foreground="#888888")
                if str(self.entry_name3.cget("state")) != "normal":
                    self.entry_name3.config(state="normal")
                self.entry_name3.set("")
                self.entry_name3.config(state="disabled")
                self.is_swap_reserved_b3 = False

        # 3. 更新勾選框文字（如帶入排班姓名）
        self.update_swap_buttons_visibility()

        if show_toast:
            dash_txt = self.lbl_status_dashcam.cget("text") if hasattr(self, 'lbl_status_dashcam') else ""
            summary_parts = []
            if dash_txt:
                summary_parts.append(f"行車: {dash_txt}")
            summary_parts.append(f"第一台: {self.lbl_status_name1.cget('text')}")
            summary_parts.append(f"第二台: {self.lbl_status_name2.cget('text')}")
            summary_parts.append(f"第三台: {self.lbl_status_name3.cget('text')}")
            self.log(f"🔄 [設備連線刷新] {'、'.join(summary_parts)}")

    def apply_lock_dashcam(self, is_locked):
        """套用行車紀錄器區域之鎖定/解鎖狀態至元件（不鎖定內部資料容器 entry_dashcam_src）"""
        state = "disabled" if is_locked else "normal"
        if hasattr(self, 'entry_dashcam_target'):
            self.entry_dashcam_target.config(state=state)
        if hasattr(self, 'btn_dash_target'):
            self.btn_dash_target.config(state=state)
        if hasattr(self, 'btn_quick_dash'):
            self.btn_quick_dash.config(state=state)
        if hasattr(self, 'btn_browse_dash'):
            self.btn_browse_dash.config(state=state)
        if hasattr(self, 'btn_lock_dashcam'):
            self.btn_lock_dashcam.config(text="🔓 解鎖" if is_locked else "🔒 鎖定")

    def toggle_lock_dashcam(self):
        """切換行車紀錄器設定鎖定狀態並儲存至設定檔"""
        self.is_dashcam_locked = not self.is_dashcam_locked
        self.apply_lock_dashcam(self.is_dashcam_locked)
        self.app.config["is_dashcam_locked"] = self.is_dashcam_locked
        self.app.save_config()

        if self.is_dashcam_locked:
            self.log("🔒 [行車紀錄器] 區域已鎖定，防止誤觸修改 (已儲存狀態)。")
        else:
            self.log("🔓 [行車紀錄器] 區域已解鎖，恢復可編輯狀態 (已儲存狀態)。")

    def apply_lock_bodycam(self, is_locked):
        """套用密錄器區域之鎖定/解鎖狀態至元件（不鎖定內部資料容器 entry_bodycam1/2/3_src）"""
        state = "disabled" if is_locked else "normal"
        if hasattr(self, 'entry_bodycam_target'):
            self.entry_bodycam_target.config(state=state)
        if hasattr(self, 'btn_body_target'):
            self.btn_body_target.config(state=state)
        if hasattr(self, 'btn_quick_b1'):
            self.btn_quick_b1.config(state=state)
        if hasattr(self, 'btn_browse_b1'):
            self.btn_browse_b1.config(state=state)
        if hasattr(self, 'btn_quick_b2'):
            self.btn_quick_b2.config(state=state)
        if hasattr(self, 'btn_browse_b2'):
            self.btn_browse_b2.config(state=state)
        if hasattr(self, 'btn_quick_b3'):
            self.btn_quick_b3.config(state=state)
        if hasattr(self, 'btn_browse_b3'):
            self.btn_browse_b3.config(state=state)
        if hasattr(self, 'btn_lock_bodycam'):
            self.btn_lock_bodycam.config(text="🔓 解鎖" if is_locked else "🔒 鎖定")

    def toggle_lock_bodycam(self):
        """切換密錄器設定鎖定狀態並儲存至設定檔"""
        self.is_bodycam_locked = not self.is_bodycam_locked
        self.apply_lock_bodycam(self.is_bodycam_locked)
        self.app.config["is_bodycam_locked"] = self.is_bodycam_locked
        self.app.save_config()

        if self.is_bodycam_locked:
            self.log("🔒 [密錄器] 區域已鎖定，防止誤觸修改 (已儲存狀態)。")
        else:
            self.log("🔓 [密錄器] 區域已解鎖，恢復可編輯狀態 (已儲存狀態)。")

    def toggle_base_settings(self):
        """切換基本設定大區域 (行車紀錄器、密錄器) 之隱藏與展開狀態"""
        if getattr(self, "is_base_settings_hidden", False):
            # 目前為隱藏狀態 -> 展開
            self.f_base_content.pack(fill="x", pady=(2, 0))
            self.btn_toggle_base.config(text="🔼 隱藏此區域 (路徑設定)")
            self.is_base_settings_hidden = False
            self.log("👁️ [基本設定] 區域已展開顯示。")
        else:
            # 目前為展開狀態 -> 隱藏
            self.f_base_content.pack_forget()
            self.btn_toggle_base.config(text="🔽 展開此區域 (路徑設定)")
            self.is_base_settings_hidden = True
            self.log("🙈 [基本設定] 區域已收合隱藏。")

        # 儲存偏好使所有分頁與下次啟動時維持相同狀態
        self.app.config["hide_base_settings"] = self.is_base_settings_hidden
        self.app.save_config()

        # 同步更新其他分頁按鈕與顯示狀態
        for tab in self.app.task_tabs:
            if tab != self and hasattr(tab, 'btn_toggle_base') and hasattr(tab, 'f_base_content'):
                tab.is_base_settings_hidden = self.is_base_settings_hidden
                if self.is_base_settings_hidden:
                    tab.f_base_content.pack_forget()
                    tab.btn_toggle_base.config(text="🔽 展開此區域 (路徑設定)")
                else:
                    tab.f_base_content.pack(fill="x", pady=(2, 0))
                    tab.btn_toggle_base.config(text="🔼 隱藏此區域 (路徑設定)")
                try:
                    tab.content_frame.update_idletasks()
                    tab.canvas.configure(scrollregion=tab.canvas.bbox("all"))
                except Exception:
                    pass

        try:
            self.content_frame.update_idletasks()
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        except Exception:
            pass

    def browse_path(self, target_entry, title):
        init_dir = get_dialog_initialdir(
            target_entry.get().strip() if target_entry else "",
            fallback_dir=self.app.config.get("bodycam_target", "") if hasattr(self, "app") else ""
        )
        selected = filedialog.askdirectory(title=title, initialdir=init_dir)
        if selected:
            target_entry.delete(0, tk.END)
            target_entry.insert(0, selected)
            self.app.save_config()
            self.refresh_device_connections()

    def log(self, message):
        def _do():
            try:
                self.txt_log.insert(tk.END, message + "\n")
                self.txt_log.see(tk.END)
            except Exception:
                pass
        try:
            if threading.current_thread() is threading.main_thread():
                _do()
            else:
                self.app.root.after(0, _do)
        except Exception:
            pass

    def update_status(self, text, fg=None):
        def _do():
            try:
                if fg:
                    self.lbl_status.config(text=text, foreground=fg)
                else:
                    self.lbl_status.config(text=text)
            except Exception:
                pass
        try:
            if threading.current_thread() is threading.main_thread():
                _do()
            else:
                self.app.root.after(0, _do)
        except Exception:
            pass

    def update_progress_bar(self, maximum=None, value=None):
        def _do():
            try:
                if maximum is not None:
                    self.progress_bar["maximum"] = maximum
                if value is not None:
                    self.progress_bar["value"] = value
            except Exception:
                pass
        try:
            if threading.current_thread() is threading.main_thread():
                _do()
            else:
                self.app.root.after(0, _do)
        except Exception:
            pass

    def show_error_async(self, title, msg):
        def _do():
            try:
                messagebox.showerror(title, msg)
            except Exception:
                pass
        try:
            if threading.current_thread() is threading.main_thread():
                _do()
            else:
                self.safe_root_after(0, _do)
        except Exception:
            pass

    def safe_root_after(self, delay, func):
        try:
            self.app.root.after(delay, func)
        except Exception:
            pass

    def quick_detect_dashcam(self):
        label = self.app.config.get("label_dashcam", "DASHCAM")
        path, matched_name = find_drive_by_label(label)
        if not path:
            scan_res = scan_all_ems_devices(config=self.app.config, quick_mode=True, exclude_paths=self.get_scan_exclude_paths())
            d_info = scan_res.get('dashcam')
            if d_info:
                path = d_info['path']
                matched_name = d_info['label']
        if path:
            self.set_src_path(self.entry_dashcam_src, path)
            self.log(f"⚡ [行車紀錄器] 成功快速讀取：{path} (名稱: {matched_name})")
            self.app.save_config()
            self.refresh_device_connections()
        else:
            messagebox.showwarning("未找到硬碟", f"未找到行車紀錄器隨身碟/記憶卡！\n\n請確認讀卡機是否已插妥。")

    def quick_detect_b1(self):
        label = self.app.config.get("label_bodycam1", "BODYCAM1")
        path, matched_name = find_drive_by_label(label)
        if not path:
            scan_res = scan_all_ems_devices(config=self.app.config, quick_mode=True, exclude_paths=self.get_scan_exclude_paths())
            b_list = scan_res.get('bodycams', [])
            if len(b_list) >= 1:
                path = b_list[0]['path']
                matched_name = b_list[0]['label']
        if path:
            self.set_src_path(self.entry_bodycam1_src, path)
            self.log(f"⚡ [密錄器：第一台] 成功快速讀取：{path} (名稱: {matched_name})")
            self.app.save_config()
            self.refresh_device_connections()
        else:
            messagebox.showwarning("未找到硬碟", f"未找到密錄器：第一台隨身碟！\n\n請確認傳輸線是否已插妥。")

    def quick_detect_b2(self):
        label = self.app.config.get("label_bodycam2", "BODYCAM2")
        path, matched_name = find_drive_by_label(label)
        if not path:
            scan_res = scan_all_ems_devices(config=self.app.config, quick_mode=True, exclude_paths=self.get_scan_exclude_paths())
            b_list = scan_res.get('bodycams', [])
            if len(b_list) >= 2:
                path = b_list[1]['path']
                matched_name = b_list[1]['label']
            elif len(b_list) == 1 and self.entry_bodycam1_src.get().strip() != b_list[0]['path']:
                path = b_list[0]['path']
                matched_name = b_list[0]['label']
        if path:
            self.set_src_path(self.entry_bodycam2_src, path)
            self.log(f"⚡ [密錄器：第二台] 成功快速讀取：{path} (名稱: {matched_name})")
            self.app.save_config()
            self.refresh_device_connections()
        else:
            messagebox.showwarning("未找到硬碟", f"未找到密錄器：第二台隨身碟！\n\n請確認傳輸線是否已插妥。")

    def quick_detect_b3(self):
        label = self.app.config.get("label_bodycam3", "BODYCAM3")
        path, matched_name = find_drive_by_label(label)
        if not path:
            scan_res = scan_all_ems_devices(config=self.app.config, quick_mode=True, exclude_paths=self.get_scan_exclude_paths())
            b_list = scan_res.get('bodycams', [])
            if len(b_list) >= 3:
                path = b_list[2]['path']
                matched_name = b_list[2]['label']
            else:
                existing_p = {self.entry_bodycam1_src.get().strip(), self.entry_bodycam2_src.get().strip()}
                for b_cand in b_list:
                    if b_cand['path'] not in existing_p:
                        path = b_cand['path']
                        matched_name = b_cand['label']
                        break
        if path:
            self.set_src_path(self.entry_bodycam3_src, path)
            self.log(f"⚡ [密錄器：第三台] 成功快速讀取：{path} (名稱: {matched_name})")
            self.app.save_config()
            self.refresh_device_connections()
        else:
            messagebox.showwarning("未找到硬碟", f"未找到密錄器：第三台隨身碟！\n\n若因現場電腦 3 孔限制尚未插入，可待前階段複製完成後依提示換插。")

    def quick_detect_all_devices(self, show_toast=True):
        """【⚡ 一鍵智慧偵測所有設備】
        自動掃描所有外接 USB 插槽、記憶卡與密錄器，自動識別行車紀錄器與密錄器第一台、第二台、第三台。
        徹底解決公務電腦僅 3 個 USB 孔、新舊傳輸線（Type-C / 2.5mm）換插導致磁碟槽代碼亂跳與標籤不符之痛點！
        """
        start_dt = None
        end_dt = None
        try:
            if hasattr(self, 'var_filter_time') and self.var_filter_time.get():
                ad_year, month, day, err_msg = self.get_parsed_date_info()
                if not err_msg:
                    date_part = f"{ad_year:04d}{month:02d}{day:02d}"
                    sp = self.entry_start_time.get().strip() if hasattr(self, 'entry_start_time') else ""
                    ep = self.entry_end_time.get().strip() if hasattr(self, 'entry_end_time') else ""
                    if sp and len(sp) == 4 and sp.isdigit():
                        start_dt = datetime.strptime(f"{date_part}{sp}", "%Y%m%d%H%M")
                    if ep and len(ep) == 4 and ep.isdigit() and start_dt:
                        end_dt = datetime.strptime(f"{date_part}{ep}", "%Y%m%d%H%M")
                        if end_dt <= start_dt:
                            end_dt += timedelta(days=1)
        except Exception:
            pass

        if hasattr(self.app, 'sync_fingerprint_settings_to_config'):
            self.app.sync_fingerprint_settings_to_config()

        scan_res = scan_all_ems_devices(
            start_dt, end_dt,
            config=self.app.config,
            quick_mode=False,
            exclude_paths=self.get_scan_exclude_paths()
        )
        dash_info = scan_res.get('dashcam')
        body_list = scan_res.get('bodycams', [])

        assigned_dash = False
        assigned_b1 = False
        assigned_b2 = False
        assigned_b3 = False
        summary_lines = []

        if dash_info:
            p = dash_info['path']
            cnt = len(dash_info['matched_files'])
            info_str = f"含時段影像 {cnt} 部" if start_dt else f"偵測到 {dash_info['all_videos_count']} 部影片"
            summary_lines.append(f"🚗 行車紀錄器：已鎖定 {p} ({dash_info['label']}，{info_str})")
            assigned_dash = True
        else:
            summary_lines.append("🚗 行車紀錄器：⚪ 未偵測到 (待插上讀卡機)")

        if len(body_list) >= 1:
            p1 = body_list[0]['path']
            cnt1 = len(body_list[0]['matched_files'])
            info1 = f"含時段影像 {cnt1} 部" if start_dt else f"{body_list[0]['all_videos_count']} 部影片"
            summary_lines.append(f"📹 密錄器：第一台：已鎖定 {p1} ({body_list[0]['label']}，{info1})")
            assigned_b1 = True
        else:
            summary_lines.append("📹 密錄器：第一台：⚪ 未偵測到")

        if len(body_list) >= 2:
            p2 = body_list[1]['path']
            cnt2 = len(body_list[1]['matched_files'])
            info2 = f"含時段影像 {cnt2} 部" if start_dt else f"{body_list[1]['all_videos_count']} 部影片"
            summary_lines.append(f"📹 密錄器：第二台：已鎖定 {p2} ({body_list[1]['label']}，{info2})")
            assigned_b2 = True
        else:
            summary_lines.append("📹 密錄器：第二台：⚪ 未偵測到")

        if len(body_list) >= 3:
            p3 = body_list[2]['path']
            cnt3 = len(body_list[2]['matched_files'])
            info3 = f"含時段影像 {cnt3} 部" if start_dt else f"{body_list[2]['all_videos_count']} 部影片"
            summary_lines.append(f"📹 密錄器：第三台：已鎖定 {p3} ({body_list[2]['label']}，{info3})")
            assigned_b3 = True
        else:
            summary_lines.append("📹 密錄器：第三台：⚪ 未偵測到")

        # 統一交由 refresh_device_connections 使用同一份 scan_res 同步路徑與 UI，徹底杜絕二次掃描覆蓋！
        self.refresh_device_connections(show_toast=False, pre_scanned=scan_res)
        self.app.save_config()

        self.log("=" * 45)
        self.log("⚡ [一鍵智慧偵測所有設備結果]")
        for line in summary_lines:
            self.log(f"  • {line}")
        diag_list = scan_res.get('diagnostics', [])
        if diag_list:
            self.log(f"  ℹ️ 已排除非隨身碟磁碟：{'；'.join(diag_list)}")
        self.log("=" * 45)

        if show_toast:
            total_devs = (1 if assigned_dash else 0) + (1 if assigned_b1 else 0) + (1 if assigned_b2 else 0) + (1 if assigned_b3 else 0)
            if total_devs > 0:
                self.update_status(f"狀態：⚡ 智慧偵測成功，已自動鎖定 {total_devs} 個設備！", "#008800")
            else:
                self.update_status("狀態：未找到外接攝影設備隨身碟，請確認插槽", "#C62828")

    def clear_all_officers(self):
        """【🧹 清空全體人員】將密錄器 1、2、3 之出勤同仁姓名全數清空並重設換插預約"""
        self.is_swap_reserved_b2 = False
        self.is_swap_reserved_b3 = False
        self.pending_roster_names = []
        for entry in (getattr(self, 'entry_name1', None), getattr(self, 'entry_name2', None), getattr(self, 'entry_name3', None)):
            if entry:
                st = str(entry.cget("state"))
                if st == "disabled":
                    entry.config(state="normal")
                    entry.set("")
                    entry.config(state="disabled")
                else:
                    entry.set("")
        self.refresh_device_connections(show_toast=False)
        self.log("🧹 [清空人員] 已全數清空密錄器 1、2、3 之出勤同仁姓名與換插預約。")
        self.update_status("狀態：已清空全體出勤同仁姓名", "#555555")

    def smart_detect_and_sync_roster(self):
        """【⚡ 一鍵智慧偵測連線 ＆ 刷新排班】整合按鈕
        1. 自動執行智慧指紋偵測所有隨身碟（行車紀錄器、密錄器1/2/3）並刷新狀態燈號。
        2. 若出勤日期與時間已輸入，自動比對當月勤務表排班並填入同仁姓名。
        """
        # 1. 執行智慧特徵偵測所有設備
        self.quick_detect_all_devices(show_toast=False)

        # 2. 嘗試自動讀取排班同仁姓名
        ad_year, month, day, err_msg = self.get_parsed_date_info()
        time_input = self.entry_start_time.get().strip() if hasattr(self, 'entry_start_time') else ""
        if err_msg or not time_input:
            self.update_status("狀態：⚡ 設備智慧偵測完成！(輸入出勤時間可再自動帶入排班同仁)", "#008800")
            return

        # 呼叫排班表讀取 (skip_detect 避免重複掃描)
        self.quick_read_roster(detect_first=False)

    def quick_read_roster(self, detect_first=True):
        """【一鍵連線並讀取排班】
        執行順序：
        1. 執行前，先把密錄 1、2、3 的姓名全數清空。
        2. 自動執行一鍵智慧偵測所有設備，更新各隨身碟狀態 (若 detect_first 為 True)。
        3. 再讀取當天排班 Excel，從當天勤務表比對救護班同仁，並依序填入已連線的密錄器。
        """
        # 1. 執行前，先把密錄 1、2、3 姓名清空
        self.clear_all_officers()

        # 2. 自動執行一鍵智慧偵測所有設備
        if detect_first:
            self.quick_detect_all_devices(show_toast=False)

        # 3. 再讀取班
        ad_year, month, day, err_msg = self.get_parsed_date_info()
        if err_msg:
            messagebox.showwarning("缺少或錯誤的出勤日期", err_msg)
            return

        date_input = f"{ad_year:04d}{month:02d}{day:02d}"
        time_input = self.entry_start_time.get().strip()
        if not time_input:
            messagebox.showwarning("缺少出勤時間", "請先輸入「出勤時間」（4碼 24小時制，例如 0830、1200、0330），\n系統才能比對該時段之排班同仁！")
            return

        start_hour = extract_hour(time_input)
        if start_hour is None:
            messagebox.showwarning("出勤時間格式錯誤", f"無法辨識出勤時間：【{time_input}】\n請輸入 4 碼時間（例如 0830、1200、0015）。")
            return

        duty_year, duty_month, duty_day, is_prev = get_duty_date(date_input, start_hour)
        if duty_month is None or duty_day is None:
            messagebox.showwarning("日期格式錯誤", f"無法辨識出勤日期：【{date_input}】！")
            return

        roster_path = self.app.config.get("roster_path", "").strip()
        if not roster_path or not os.path.exists(roster_path):
            if messagebox.askyesno(
                "未設定勤務表目錄",
                "尚未指定當月勤務表資料夾路徑！\n\n是否立即選取存放當月勤務表（如：分隊-1020.xlsm）的資料夾？"
            ):
                init_roster = self.app.config.get("roster_path", "")
                if not init_roster or not os.path.exists(init_roster):
                    init_roster = os.getcwd()
                dir_selected = filedialog.askdirectory(title="選取存放當月勤務表之資料夾", initialdir=init_roster)
                if dir_selected:
                    self.app.config["roster_path"] = dir_selected
                    if hasattr(self.app, 'entry_roster_path'):
                        self.app.entry_roster_path.delete(0, tk.END)
                        self.app.entry_roster_path.insert(0, dir_selected)
                    self.app.save_config()
                    roster_path = dir_selected
                else:
                    return
            else:
                return

        # 需求 5: 出勤月份與勤務表目錄月份不符時進行智慧切換或防呆提醒
        folder_month = detect_roster_folder_month(roster_path)
        if folder_month is not None and folder_month != duty_month:
            reason = f"\n（出勤時間為凌晨 {start_hour:02d} 點，依消防勤務交接對應前一日 {duty_month:02d}/{duty_day:02d} 排班）" if is_prev else ""
            sibling_dir = find_sibling_month_folder(roster_path, duty_month)
            if sibling_dir:
                switch_msg = (
                    f"📅 偵測到出勤排班為【{duty_month} 月】，目前設定為【{folder_month} 月】目錄。{reason}\n\n"
                    f"系統已在同層目錄自動找到【{duty_month} 月】勤務表目錄：\n"
                    f"📂 {sibling_dir}\n\n"
                    f"是否立即自動切換為【{duty_month} 月】目錄並載入最新排班與同仁名冊？"
                )
                if messagebox.askyesno("發現對應月份資料夾 - 自動切換", switch_msg):
                    self.app.config["roster_path"] = sibling_dir
                    if hasattr(self.app, 'entry_roster_path'):
                        self.app.entry_roster_path.delete(0, tk.END)
                        self.app.entry_roster_path.insert(0, sibling_dir)
                    self.app.save_config()
                    self.app.load_officer_names_from_roster()
                    self.app.update_comboboxes_all_tabs()
                    roster_path = sibling_dir
                    folder_month = duty_month
                    self.log(f"⚡ [月份切換] 已自動切換勤務表目錄至 {duty_month} 月：{sibling_dir}")
                else:
                    return
            else:
                warn_msg = (
                    f"⚠️ 出勤勤務月份與目前設定的勤務表資料夾月份不符！\n\n"
                    f"• 本次出勤排班月份：【{duty_month} 月】（排班日期：{duty_month:02d}/{duty_day:02d}）{reason}\n"
                    f"• 目前設定之勤務表目錄：【{folder_month} 月】\n"
                    f"  目錄路徑：{roster_path}\n\n"
                    f"是否繼續在此目錄搜尋？\n"
                    f"（若需更換為【{duty_month} 月】勤務表目錄，請按「否」至【⚙️ 快速設定】重新指定）"
                )
                if not messagebox.askyesno("勤務月份不符提醒", warn_msg):
                    return

        if os.path.isfile(roster_path):
            file_path = roster_path
            file_name = os.path.basename(roster_path)
        else:
            file_path, file_name, err = find_roster_file_for_date(roster_path, duty_month, duty_day)
            if not file_path:
                latest_p = None
                latest_fn = None
                try:
                    candidates = []
                    search_dirs = [roster_path]
                    for entry in os.listdir(roster_path):
                        sub_p = os.path.join(roster_path, entry)
                        if os.path.isdir(sub_p) and not entry.startswith('.'):
                            search_dirs.append(sub_p)
                    for d in search_dirs:
                        for fn in os.listdir(d):
                            if not fn.startswith('~$') and fn.lower().endswith(('.xlsm', '.xlsx', '.xls')):
                                fp = os.path.join(d, fn)
                                if os.path.isfile(fp):
                                    candidates.append((os.path.getmtime(fp), fp, fn))
                    if candidates:
                        candidates.sort(key=lambda x: x[0], reverse=True)
                        latest_p, latest_fn = candidates[0][1], candidates[0][2]
                except:
                    pass

                if latest_p and messagebox.askyesno(
                    "未找到當日勤務表",
                    f"在目錄中未找到對應 {duty_month:02d}/{duty_day:02d} 的勤務表檔案。\n\n"
                    f"是否改用該目錄中最新的勤務表【{latest_fn}】以取得排班人員？"
                ):
                    file_path = latest_p
                    file_name = latest_fn
                else:
                    if is_prev:
                        messagebox.showwarning(
                            "找不到前一日排班檔案",
                            f"出勤時間為凌晨 00~08 點時段，依消防勤務交接規定對應前一日（{duty_month:02d}/{duty_day:02d}）之排班。\n\n"
                            f"但在目錄【{roster_path}】中未找到包含【{duty_month:02d}{duty_day:02d}】之勤務表！\n\n"
                            f"請確認前一日之排班檔案是否存在該目錄中。"
                        )
                    else:
                        messagebox.showwarning("找不到勤務表檔案", err)
                    return

        shift_desc = f"（00~08點 跨日前一日排班）" if is_prev else ""
        self.log(f"📖 正在讀取勤務表：{file_name} {shift_desc} ...")

        try:
            roster_data = parse_roster_excel(file_path)
        except Exception as e:
            messagebox.showerror("解析失敗", f"讀取勤務表時發生錯誤：\n{file_name}\n\n詳細資訊：{str(e)}")
            return

        # 更新全域已知同仁名單至下拉清單
        if roster_data.get('id_to_name'):
            sorted_ids = sorted(roster_data['id_to_name'].keys(), key=lambda x: int(x) if x.isdigit() else 999)
            roster_names = [roster_data['id_to_name'][k] for k in sorted_ids if roster_data['id_to_name'][k]]
            self.app.officer_names = list(roster_names)
            self.app.config["cached_officer_names"] = list(roster_names)
            self.app.update_comboboxes_all_tabs()

        hour_to_col = roster_data.get('hour_to_col', {})
        col_letter = hour_to_col.get(start_hour)
        if not col_letter:
            std_cols = {8: 'C', 9: 'D', 10: 'E', 11: 'F', 12: 'G', 13: 'H', 14: 'I', 15: 'J', 16: 'K', 17: 'L', 18: 'M', 19: 'N', 20: 'O', 21: 'P', 22: 'Q', 23: 'R', 0: 'S', 1: 'T', 2: 'U', 3: 'V', 4: 'W', 5: 'X', 6: 'Y', 7: 'Z'}
            col_letter = std_cols.get(start_hour, 'C')

        row_91 = roster_data.get('row_91')
        if not row_91:
            messagebox.showwarning("未找到救護91排班列", f"在勤務表【{file_name}】中未找到「緊急救護91」的排班勤務列！")
            return

        cell_ref = f"{col_letter}{row_91}"
        cell_val = roster_data['rows'].get(row_91, {}).get(cell_ref, '').strip()
        time_slot_str = f"{start_hour:02d}:00~{(start_hour+1)%24:02d}:00"

        if not cell_val:
            messagebox.showinfo(
                "該時段無排班資料",
                f"勤務表【{file_name}】中，\n時段 [{time_slot_str}]（欄位 {cell_ref}）之「緊急救護91」未填寫人員番號。"
            )
            return

        id_to_name = roster_data.get('id_to_name', {})
        raw_ids = [x.strip() for x in re.split(r'[,，、/\s]+', cell_val) if x.strip()]
        names = []
        for rid in raw_ids:
            try:
                clean_id = str(int(float(rid)))
            except:
                clean_id = rid
            if clean_id in id_to_name:
                names.append(id_to_name[clean_id])
            else:
                names.append(f"番號{clean_id}")

        if not names:
            messagebox.showinfo("無對應同仁", f"勤務表欄位 {cell_ref} 內容為【{cell_val}】，但無法比對出對應同仁。")
            return

        # 暫存最新排班人員名單，重置換插預約
        self.pending_roster_names = list(names)
        self.is_swap_reserved_b2 = False
        self.is_swap_reserved_b3 = False

        # 1. 先刷新設備連線，確保取得各密錄器最新連線與開放狀態
        # 嚴格遵循：看使用者插入哪幾個密錄器，才開放有連線的密錄器；未連線者直接關閉（反灰）不給點選
        self.refresh_device_connections(show_toast=False)

        # 2. 取得所有目前「開放／已連線」之密錄器插槽 (按 1 -> 2 -> 3 順序)
        open_slots = []
        for idx, entry, lbl_status in [
            (1, self.entry_name1, self.lbl_status_name1),
            (2, self.entry_name2, self.lbl_status_name2),
            (3, self.entry_name3, self.lbl_status_name3),
        ]:
            if str(entry.cget("state")) == "normal":
                open_slots.append((idx, entry))

        if not open_slots:
            messagebox.showwarning(
                "未偵測到連線設備",
                "目前未偵測到任何已插上且連線之密錄器！\n\n請先將密錄器插入電腦 USB 孔後，\n再點擊【⚡ 一鍵智慧偵測連線 ＆ 刷新排班】。"
            )
            self.update_status("狀態：未找到已連線之密錄器設備", "#C62828")
            return

        # 3. 針對有開放（連線）之設備依序填入勤務人員：
        # 核心防呆邏輯：若插進的設備只有 1 個，但有 2 名勤務人員，則只填入連線的 1 位即可，其餘反灰關閉
        assigned_records = []
        slot_names = {1: "第一台", 2: "第二台", 3: "第三台"}
        for i, (idx, entry) in enumerate(open_slots):
            if i < len(names):
                person_name = names[i]
                entry.set(person_name)
                assigned_records.append(f"密錄器：{slot_names.get(idx, '設備')}【{person_name}】")
            else:
                entry.set("")

        # 4. 動態更新勾選方塊文字（如帶入未連線之排班人員姓名）
        self.update_swap_buttons_visibility()

        prev_label = f" (前一日 {duty_month:02d}/{duty_day:02d} 凌晨勤務)" if is_prev else ""
        if len(names) > len(open_slots):
            unassigned_name = names[len(open_slots)]
            log_msg = (
                f"⚡ 成功自勤務表【{file_name}】取得 {len(names)} 位排班同仁（{'、'.join(names)}），"
                f"因目前電腦僅連線 {len(open_slots)} 部密錄器，已依序填入已連線之設備：{'、'.join(assigned_records)} (時段 [{time_slot_str}]{prev_label})"
            )
        else:
            log_msg = (
                f"⚡ 成功自勤務表【{file_name}】依序帶入時段 [{time_slot_str}]{prev_label} 救護班："
                f"{'、'.join(assigned_records)}"
            )
        self.log(log_msg)

        if hasattr(self, 'lbl_roster_hint'):
            hint_clean = "、".join([r.replace("密錄器：", "").replace("【", "").replace("】", "") for r in assigned_records])
            more_hint = f" (尚有【{names[len(open_slots)]}】若需換插可勾選預先開放)" if len(names) > len(open_slots) else ""
            self.lbl_roster_hint.config(
                text=f"✅ 已帶入 {time_slot_str}{prev_label}：{hint_clean}{more_hint}",
                foreground="#2E7D32"
            )

    def find_videos_in_range(self, source_path, start_dt, end_dt):
        matched = []
        if not source_path or not os.path.exists(source_path):
            return matched

        buffered_start = start_dt - timedelta(minutes=3) if start_dt else None

        for root, _, files in os.walk(source_path):
            if self.stop_requested.is_set():
                break
            for file in files:
                if Path(file).suffix.lower() in VIDEO_EXTENSIONS:
                    full_path = os.path.join(root, file)
                    try:
                        file_mtime = datetime.fromtimestamp(os.path.getmtime(full_path))
                        if buffered_start and file_mtime < buffered_start:
                            continue
                        if end_dt and file_mtime > end_dt:
                            continue
                        matched.append((full_path, file_mtime))
                    except Exception:
                        pass

        matched.sort(key=lambda x: x[1])
        return matched

    def pause_and_cleanup(self):
        """按下暫停後，中斷複製並刪除本次已建立之資料夾"""
        if not self.is_copying:
            return

        if messagebox.askyesno("確認暫停與刪除", "確定要【暫停並放棄】本次複製任務嗎？\n\n注意：暫停後將會自動刪除本次已建立的所有資料夾與未完成檔案！"):
            self.lbl_status.config(text="狀態：🛑 正在暫停並清理資料夾...", foreground="#CC0000")
            self.log("🛑 收到使用者暫停指令，正在終止作業並刪除已建立之資料夾...")
            self.stop_requested.set()

    def cleanup_active_folders(self):
        """清理本次建立的資料夾"""
        for folder in self.active_created_folders:
            if os.path.exists(folder):
                try:
                    self.log(f"🗑️ 正在刪除資料夾: {folder}")
                    shutil.rmtree(folder, ignore_errors=True)
                except Exception as e:
                    self.log(f"⚠️ 刪除失敗: {str(e)}")

        for folder in self.active_created_folders:
            parent_day_dir = os.path.dirname(folder)
            try:
                if os.path.exists(parent_day_dir) and not os.listdir(parent_day_dir):
                    os.rmdir(parent_day_dir)
                    self.log(f"🗑️ 清除空目錄: {parent_day_dir}")
            except Exception:
                pass
        self.active_created_folders = []

    def start_process(self):
        # 關鍵修復：每次開始比對前，立即重置中斷旗標，確保隨身碟搜尋不會被殘留中斷旗標提前中斷！
        self.stop_requested.clear()

        case_no = self.entry_case_no.get().strip()
        dashcam_target = self.entry_dashcam_target.get().strip()
        bodycam_target = self.entry_bodycam_target.get().strip()

        dashcam_src = self.entry_dashcam_src.get().strip()
        name1 = self.entry_name1.get().strip()
        bodycam1_src = self.entry_bodycam1_src.get().strip()
        name2 = self.entry_name2.get().strip()
        bodycam2_src = self.entry_bodycam2_src.get().strip()
        name3 = self.entry_name3.get().strip()
        bodycam3_src = self.entry_bodycam3_src.get().strip()

        if not case_no:
            messagebox.showwarning("欄位缺少", "請填寫【案件案號】（案號規定必須為 17 碼）！")
            self.entry_case_no.focus()
            return

        if len(case_no) != 17:
            messagebox.showerror(
                "案號長度錯誤",
                f"案件案號規定【必須為 17 碼】！\n\n"
                f"• 目前輸入長度：{len(case_no)} 碼\n"
                f"• 目前輸入內容：【{case_no}】\n\n"
                f"請檢查並補齊 17 碼完整派遣案號後再繼續。"
            )
            self.entry_case_no.focus()
            return

        start_dt = None
        end_dt = None
        if self.var_filter_time.get():
            ad_year, month, day, err_msg = self.get_parsed_date_info()
            if err_msg:
                messagebox.showerror("出勤日期格式錯誤", err_msg)
                return

            date_part = f"{ad_year:04d}{month:02d}{day:02d}"
            start_part = self.entry_start_time.get().strip()
            end_part = self.entry_end_time.get().strip()

            try:
                if len(start_part) != 4 or not start_part.isdigit():
                    raise ValueError
                start_dt = datetime.strptime(f"{date_part}{start_part}", "%Y%m%d%H%M")
            except ValueError:
                messagebox.showerror("時間格式錯誤", "出勤時間格式錯誤！\n格式需為 4 碼 24 小時制（例：1900）")
                return

            if end_part:
                try:
                    if len(end_part) != 4 or not end_part.isdigit():
                        raise ValueError
                    end_dt = datetime.strptime(f"{date_part}{end_part}", "%Y%m%d%H%M")
                    if end_dt <= start_dt:
                        end_dt += timedelta(days=1)
                except ValueError:
                    messagebox.showerror("時間格式錯誤", "返隊時間格式錯誤！\n格式需為 4 碼 24 小時制（例：2103）")
                    return

        self.app.save_config()

        # -------------------------------------------------------------
        # 【硬碟連線雙重驗證 ＆ 共用讀卡機偵測】
        # -------------------------------------------------------------
        self.lbl_status.config(text="狀態：正在比對各設備隨身碟與時段影片...", foreground="#0066CC")
        self.log("🔍 [預檢確認] 正在快速比對各設備隨身碟連線狀態與時段影片...")

        label_dash = self.app.config.get("label_dashcam", "DASHCAM").strip()

        # 雙重驗證各設備（密錄器不拘泥於硬體標籤名稱，只要目前路徑存在即判定連線）
        dash_conn, dash_msg = is_device_connected(dashcam_src, label_dash)
        b1_conn, b1_msg = is_device_connected(bodycam1_src, "")
        b2_conn, b2_msg = is_device_connected(bodycam2_src, "")
        b3_conn, b3_msg = is_device_connected(bodycam3_src, "")

        # 防呆過濾：若未真實連線且未預約換插，強制將該槽位視為無效，徹底防止背景殘留路徑引發幽靈連線
        is_swap2 = getattr(self, 'is_swap_reserved_b2', False)
        is_swap3 = getattr(self, 'is_swap_reserved_b3', False)
        if not b2_conn and not is_swap2:
            bodycam2_src = ""
        if not b3_conn and not is_swap3:
            bodycam3_src = ""

        req_dash = bool(dashcam_src)
        # 密錄器需求判定：
        # 1. 實際連線中 (bX_conn)
        # 2. 處於預約換插狀態且有填寫同仁姓名 (is_swap_reserved_bX and nameX)
        # 若現場實際只有 1 台錄、只帶 1 台、或未點擊換插，完全排除後續槽位，直接複製結案！
        req_b1 = bool(name1 or b1_conn)
        req_b2 = bool(b2_conn or (is_swap2 and name2))
        req_b3 = bool(b3_conn or (is_swap3 and name3))

        needed_devices = []
        if req_dash:
            needed_devices.append(("dashcam", "🚗 行車紀錄器", dashcam_src, dash_conn, dash_msg, ""))
        if req_b1:
            needed_devices.append(("bodycam1", f"📹 密錄器：第一台 ({name1 if name1 else '未填姓名'})", bodycam1_src, b1_conn, b1_msg, name1))
        if req_b2:
            needed_devices.append(("bodycam2", f"📹 密錄器：第二台 ({name2 if name2 else '未填姓名'})", bodycam2_src, b2_conn, b2_msg, name2))
        if req_b3:
            needed_devices.append(("bodycam3", f"📹 密錄器：第三台 ({name3 if name3 else '未填姓名'})", bodycam3_src, b3_conn, b3_msg, name3))

        # 計算已連線設備數量
        connected_count = sum(1 for dev in needed_devices if dev[3])
        total_needed = len(needed_devices)

        # 找出哪些設備是待換插的 (Phase 2)
        swap_dev_types = set()
        if getattr(self, 'is_swap_reserved_b2', False) and not b2_conn:
            swap_dev_types.add("bodycam2")
        if getattr(self, 'is_swap_reserved_b3', False) and not b3_conn:
            swap_dev_types.add("bodycam3")
        # 若需要複製 4 台設備，但電腦僅 3 孔：
        if total_needed > 3 and connected_count <= 3:
            for dev in needed_devices:
                if not dev[3]:  # 未連線
                    swap_dev_types.add(dev[0])
                    break

        precheck_results = []
        for dev_type, dev_label, dev_src, is_conn, conn_msg, off_name in needed_devices:
            if dev_type in swap_dev_types and not is_conn:
                precheck_results.append({
                    "type": dev_type,
                    "label": dev_label,
                    "src": dev_src,
                    "connected": True,
                    "is_swap_phase2": True,
                    "files": [],
                    "name": off_name,
                    "status_desc": "⏳ 待換插 (預約續傳) — 任一設備完成後自動提示插上"
                })
            else:
                files = self.find_videos_in_range(dev_src, start_dt, end_dt) if is_conn else []
                precheck_results.append({
                    "type": dev_type,
                    "label": dev_label,
                    "src": dev_src,
                    "connected": is_conn,
                    "is_swap_phase2": False,
                    "files": files,
                    "name": off_name,
                    "status_desc": f"{'🟢 已連線' if is_conn else '🔴 ' + conn_msg}"
                })

        # 彈出預檢確認視窗
        confirmed = self.show_precheck_dialog(case_no, start_dt, end_dt, precheck_results)
        if not confirmed:
            self.lbl_status.config(text="狀態：已取消本次複製作業", foreground="#666666")
            self.log("⚠️ 使用者已取消複製作業，未建立任何資料夾。")
            return

        total_files = sum(len(item["files"]) for item in precheck_results if item.get("connected") and not item.get("is_swap_phase2"))
        has_empty_folder_task = any(item.get("create_empty_folder") for item in precheck_results)
        has_phase2_task = any(item.get("is_swap_phase2") for item in precheck_results)

        if total_files == 0 and not has_empty_folder_task and not has_phase2_task:
            messagebox.showinfo("無符合影片", "預計複製之影片數量為 0 部，且未勾選建立備註空資料夾！\n請確認設備隨身碟是否插妥，或擴大時間範圍後再試。")
            self.lbl_status.config(text="狀態：準備就緒 (無檔案可複製)", foreground="#CC6600")
            return

        # 正式開始多線程背景複製
        self.stop_requested.clear()
        self.is_copying = True
        self.active_created_folders = []

        self.btn_start.config(state="disabled")
        self.btn_pause.config(state="normal")
        self.txt_log.delete(1.0, tk.END)

        thread = threading.Thread(
            target=self.run_copy_task,
            args=(case_no, dashcam_target, bodycam_target, start_dt, end_dt, precheck_results)
        )
        thread.daemon = True
        thread.start()

    def show_precheck_dialog(self, case_no, start_dt, end_dt, precheck_results):
        """彈出預檢確認視窗，供同仁清楚核對設備連線、影片數量及未錄影空資料夾備註"""
        dlg = tk.Toplevel(self.app.root)
        dlg.title(f"📋 複製前設備與影片確認 - 案件【{case_no}】")
        dlg.geometry("600x540")
        dlg.resizable(False, False)
        if not getattr(self.app, 'is_minimized_to_tray', False):
            dlg.transient(self.app.root)
            dlg.grab_set()

        dlg.update_idletasks()
        x = self.app.root.winfo_x() + (self.app.root.winfo_width() - 600) // 2
        y = self.app.root.winfo_y() + (self.app.root.winfo_height() - 540) // 2
        dlg.geometry(f"+{max(0, x)}+{max(0, y)}")

        user_choice = [False]

        f_content = ttk.Frame(dlg, padding=16)
        f_content.pack(fill="both", expand=True)

        time_str = "全部檔案"
        if start_dt:
            time_str = f"{start_dt.strftime('%H:%M')} 起"
            if end_dt:
                time_str += f" 至 {end_dt.strftime('%H:%M')}"
            else:
                time_str += " 之後全部 (抓最新)"

        ttk.Label(f_content, text=f"案件案號：{case_no}", font=('Helvetica', 12, 'bold')).pack(anchor="w", pady=(0, 2))
        ttk.Label(f_content, text=f"出勤時段：{time_str} (含提前3分鐘分割緩衝)", foreground="#0066CC").pack(anchor="w", pady=(0, 8))

        ttk.Separator(f_content, orient="horizontal").pack(fill="x", pady=4)
        ttk.Label(f_content, text="【各設備連線驗證與影片偵測結果】", font=('Helvetica', 10, 'bold')).pack(anchor="w", pady=(2, 4))

        total_files = 0
        has_disconnected_warning = False

        frame_list = ttk.Frame(f_content)
        frame_list.pack(fill="both", expand=True, pady=4)

        missing_reasons = self.app.config.get("missing_reasons", DEFAULT_MISSING_REASONS)

        for item in precheck_results:
            row_box = ttk.Frame(frame_list, padding=3)
            row_box.pack(fill="x", pady=2)

            count = len(item["files"])
            is_phase2 = item.get("is_swap_phase2", False)

            if is_phase2:
                status_txt = item.get("status_desc", "⏳ 待換卡")
                status_color = "#6A1B9A"
            elif not item["src"]:
                status_txt = "⚪ 來源路徑留空 (略過不備份)"
                status_color = "#888888"
            elif not item["connected"]:
                status_txt = f"{item.get('status_desc', '🔴 未連線')}"
                status_color = "#CC0000"
                has_disconnected_warning = True
            elif count > 0:
                earliest = item["files"][0][1].strftime('%H:%M:%S')
                latest = item["files"][-1][1].strftime('%H:%M:%S')
                status_txt = f"🟢 已連線 — 找到 {count} 個影片片段 ({earliest} ~ {latest})"
                status_color = "#008800"
                total_files += count
            else:
                status_txt = "🟡 已連線 — 但在出勤時段內未找到任何影片 (0 部)"
                status_color = "#CC8800"

            ttk.Label(row_box, text=f"• {item['label']}：", font=('Helvetica', 10, 'bold')).pack(anchor="w")
            ttk.Label(row_box, text=f"    {status_txt}", foreground=status_color).pack(anchor="w")

            # 若已連線但影片為 0 部，提供「建立空資料夾並備註原因」
            if item["connected"] and not is_phase2 and count == 0 and item["src"]:
                f_opt = ttk.Frame(row_box)
                f_opt.pack(fill="x", padx=18, pady=2)
                var_create = tk.BooleanVar(value=True)
                chk = ttk.Checkbutton(f_opt, text="建立空資料夾並備註原因：", variable=var_create)
                chk.pack(side="left")
                cb_r = ttk.Combobox(f_opt, values=missing_reasons, width=16)
                cb_r.set(missing_reasons[0] if missing_reasons else "設備沒電")
                cb_r.pack(side="left", padx=4)
                item["_var_create_empty"] = var_create
                item["_cb_reason"] = cb_r

        ttk.Separator(f_content, orient="horizontal").pack(fill="x", pady=6)

        if has_disconnected_warning:
            ttk.Label(f_content, text="⚠️ 注意：偵測到有設備尚未連線或標籤不符！請確認是否要略過該設備。", foreground="#CC0000", font=('Helvetica', 9, 'bold')).pack(anchor="w", pady=(0, 2))

        ttk.Label(f_content, text=f"📊 預計複製總數：共 {total_files} 個影片檔案 (不含換卡後設備)", font=('Helvetica', 11, 'bold'), foreground="#111111").pack(anchor="w", pady=(0, 8))

        f_dlg_btns = ttk.Frame(f_content)
        f_dlg_btns.pack(fill="x", pady=(4, 0))

        def on_confirm():
            for itm in precheck_results:
                if "_var_create_empty" in itm:
                    itm["create_empty_folder"] = itm["_var_create_empty"].get()
                    itm["empty_reason"] = itm["_cb_reason"].get().strip() or "未錄影"
            user_choice[0] = True
            dlg.destroy()

        def on_cancel():
            user_choice[0] = False
            dlg.destroy()

        btn_ok = ttk.Button(f_dlg_btns, text="🚀 確認無誤，開始複製", command=on_confirm)
        btn_ok.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 6))

        btn_no = ttk.Button(f_dlg_btns, text="❌ 取消返回 (檢查隨身碟/時段)", command=on_cancel)
        btn_no.pack(side="right", ipady=6, padx=(6, 0))

        dlg.wait_window()
        return user_choice[0]

    def prompt_card_swap(self, reader_path, target_dev_type, target_name, target_label, start_dt, end_dt, completed_dev_names=""):
        """【電腦 3 孔換插獨立引導對話框】(在背景線程中呼叫，透過 Event 與主線程 UI 同步)
        任一設備（行車紀錄器或密錄器）複製完成後即可拔除該設備，插上待換插設備。
        支援智慧自動掃描，換插後自動識別新隨身碟與時段影片，無須對齊硬碟代碼與標籤。
        """
        swap_event = threading.Event()
        swap_confirmed = [False]
        scanned_files = []

        dev_name_map = {
            "dashcam": "行車紀錄器",
            "bodycam1": "密錄器：第一台",
            "bodycam2": "密錄器：第二台",
            "bodycam3": "密錄器：第三台"
        }
        base_name = dev_name_map.get(target_dev_type, "待換插設備")
        dev_title = f"{base_name} ({target_name})" if target_name else base_name
        done_text = completed_dev_names if completed_dev_names else "已有設備"

        def show_dialog():
            dlg = tk.Toplevel(self.app.root)
            dlg.title(f"🔔 請插上【{dev_title}】 (電腦 3 孔換插指引)")
            dlg.geometry("590x420")
            dlg.resizable(False, False)
            dlg.transient(self.app.root)
            dlg.grab_set()

            dlg.update_idletasks()
            x = self.app.root.winfo_x() + (self.app.root.winfo_width() - 590) // 2
            y = self.app.root.winfo_y() + (self.app.root.winfo_height() - 420) // 2
            dlg.geometry(f"+{max(0, x)}+{max(0, y)}")

            f = ttk.Frame(dlg, padding=16)
            f.pack(fill="both", expand=True)

            ttk.Label(f, text=f"🎉 {done_text} 已複製完成！", font=('Helvetica', 12, 'bold'), foreground="#008800").pack(anchor="w", pady=(0, 4))
            ttk.Label(f, text="💡 電腦僅 3 個 USB 插槽：已釋出插槽，現在看哪台先好就拔哪台換插！", foreground="#0066CC", font=('Helvetica', 10, 'bold')).pack(anchor="w", pady=(0, 6))

            ttk.Separator(f, orient="horizontal").pack(fill="x", pady=4)

            msg = (
                f"【簡易操作指引】：\n\n"
                f"1. 🔌 請從電腦 USB 孔拔下任一台【已複製完成之設備】（如：{done_text}，讀卡機或線路皆可拔）。\n"
                f"2. 📹 將【{dev_title}】傳輸線或讀卡機插入剛釋出的 USB 孔。\n"
                f"3. ⚡ 插妥後稍候 2~3 秒，點擊下方【⚡ 我已插妥，自動偵測並繼續】按鈕。\n\n"
                f"系統將全自動智慧掃描新插入之隨身碟與時段影像，無須對齊硬碟代碼或選取路徑！\n"
                f"（其他尚未完成的設備仍在背景繼續複製，完全不受影響）"
            )
            ttk.Label(f, text=msg, justify="left", font=('Helvetica', 10)).pack(anchor="w", pady=4)

            lbl_verify_err = ttk.Label(f, text="", foreground="#C62828", font=('Helvetica', 9, 'bold'))
            lbl_verify_err.pack(anchor="w", pady=2)

            f_btns = ttk.Frame(f)
            f_btns.pack(fill="x", pady=(10, 0))

            def on_continue():
                # 1. 優先使用全域智慧掃描找尋
                scan_res = scan_all_ems_devices(
                    start_dt, end_dt,
                    config=self.app.config,
                    exclude_paths=self.get_scan_exclude_paths()
                )
                actual_path = None

                if target_dev_type == "dashcam":
                    d_info = scan_res.get('dashcam')
                    if d_info:
                        actual_path = d_info['path']
                else:
                    b_list = scan_res.get('bodycams', [])
                    for b_item in b_list:
                        if b_item.get('matched_files'):
                            actual_path = b_item['path']
                            break
                    if not actual_path and b_list:
                        actual_path = b_list[0]['path']

                # 2. 回退備援檢查
                if not actual_path and reader_path and os.path.exists(reader_path):
                    actual_path = reader_path
                if not actual_path and target_label:
                    alt_path, _ = find_drive_by_label(target_label)
                    if alt_path:
                        actual_path = alt_path

                if not actual_path or not os.path.exists(actual_path):
                    lbl_verify_err.config(text=f"❌ 尚未偵測到新插入的【{dev_title}】！\n請確認線路是否插緊，稍候 2 秒後再點一次按鈕。")
                    return

                files = self.find_videos_in_range(actual_path, start_dt, end_dt)
                scanned_files.extend(files)
                swap_confirmed[0] = True
                self.log(f"⚡ [換插偵測成功] 已成功對接【{dev_title}】：{actual_path}，找到時段內影像 {len(files)} 部！")
                dlg.destroy()
                swap_event.set()

            def on_skip():
                if messagebox.askyesno("略過換插", f"確定要略過本次【{dev_title}】的複製作業嗎？", parent=dlg):
                    swap_confirmed[0] = False
                    dlg.destroy()
                    swap_event.set()

            btn_ok = ttk.Button(f_btns, text=f"⚡ 我已插妥，自動偵測並繼續【{dev_title}】", command=on_continue)
            btn_ok.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 6))

            btn_cancel = ttk.Button(f_btns, text="略過此設備", command=on_skip)
            btn_cancel.pack(side="right", ipady=6, padx=(6, 0))

            dlg.protocol("WM_DELETE_WINDOW", on_skip)

        self.safe_root_after(0, show_dialog)
        swap_event.wait()
        return swap_confirmed[0], scanned_files

    def run_copy_task(self, case_no, dashcam_target, bodycam_target, start_dt, end_dt, precheck_results):
        try:
            today_mmdd = start_dt.strftime("%m%d") if start_dt else datetime.now().strftime("%m%d")
            opened_folders = []
            device_copied_counts = {
                "dashcam": 0,
                "bodycam1": 0,
                "bodycam2": 0,
                "bodycam3": 0
            }
            empty_folder_reasons = {}

            # 1. 處理預檢已勾選之「未錄影建立空資料夾」
            for item in precheck_results:
                if item.get("create_empty_folder"):
                    reason = item.get("empty_reason", "未錄影")
                    dev_type = item["type"]
                    name = item.get("name", "")
                    empty_folder_reasons[dev_type] = reason

                    if dev_type == "dashcam":
                        target_folder = os.path.join(dashcam_target, today_mmdd, f"{case_no}({reason})")
                    else:
                        folder_name = f"{case_no}{name}({reason})" if name else f"{case_no}({reason})"
                        target_folder = os.path.join(bodycam_target, today_mmdd, case_no, folder_name)

                    os.makedirs(target_folder, exist_ok=True)
                    self.active_created_folders.append(target_folder)
                    opened_folders.append(target_folder)

                    # 建立規範之 說明.txt
                    readme_path = os.path.join(target_folder, "說明.txt")
                    try:
                        time_str = f"{start_dt.strftime('%H:%M')} 起" if start_dt else "全部"
                        if end_dt:
                            time_str += f" 至 {end_dt.strftime('%H:%M')}"
                        with open(readme_path, "w", encoding="utf-8") as rf:
                            st_name = getattr(self.app, 'config', {}).get("station_name", "消防分隊")
                            rf.write("========================================\n")
                            rf.write(f"{st_name} 救護影像歸檔備註說明\n")
                            rf.write("========================================\n")
                            rf.write(f"案件案號：{case_no}\n")
                            rf.write(f"設備名稱：{item['label']}\n")
                            rf.write(f"執勤同仁：{name if name else '（無）'}\n")
                            rf.write(f"建檔時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                            rf.write(f"出勤時段：{time_str}\n")
                            rf.write(f"未錄影原因：{reason}\n")
                            rf.write(f"說明備註：本梯次該設備因【{reason}】，於出勤時段內無影像錄製存檔，特此建立規範存查。\n")
                            rf.write("========================================\n")
                    except Exception as fe:
                        self.log(f"⚠️ 建立說明.txt失敗: {fe}")

                    self.log(f"📁 [空資料夾建立] 已為【{item['label']}】建立規範資料夾：{os.path.basename(target_folder)} (原因: {reason})")

            # 2. 規劃複製任務與多線程拷貝
            init_total = sum(len(x["files"]) for x in precheck_results if x.get("connected") and not x.get("is_swap_phase2"))
            total_files_counter = [init_total]
            copied_files_counter = [0]
            progress_lock = threading.Lock()
            completed_lock = threading.Lock()
            completed_devices = []
            first_completed_event = threading.Event()

            self.update_progress_bar(maximum=max(1, total_files_counter[0]), value=0)
            self.update_status(f"狀態：正在複製 (0/{total_files_counter[0]}) 部影片...", "#0066CC")
            self.log(f"🚀 開始多線程獨立複製程序，預計寫入 {total_files_counter[0]} 個影片...")

            def copy_files_worker(src_files, dst_dir, dev_type, dev_label):
                os.makedirs(dst_dir, exist_ok=True)
                if dst_dir not in self.active_created_folders:
                    self.active_created_folders.append(dst_dir)
                if dst_dir not in opened_folders:
                    opened_folders.append(dst_dir)

                for src_file, _ in src_files:
                    if self.stop_requested.is_set():
                        return
                    fname = os.path.basename(src_file)
                    dst_file = os.path.join(dst_dir, fname)

                    shutil.copy2(src_file, dst_file)
                    with progress_lock:
                        copied_files_counter[0] += 1
                        cur_copied = copied_files_counter[0]
                        total_all = total_files_counter[0]
                        device_copied_counts[dev_type] += 1

                        def update_progress(c=cur_copied, t=total_all, fn=fname, dt=dev_type):
                            self.progress_bar["maximum"] = max(1, t)
                            self.progress_bar["value"] = c
                            self.update_status(f"複製中 ({c}/{t}): {fn}")
                            self.log(f"⏳ [{c}/{t}] 複製完成: {fn} ({dt})")
                        self.safe_root_after(0, update_progress)

                # 該設備檔案已全數複製完成
                with completed_lock:
                    if dev_label not in completed_devices:
                        completed_devices.append(dev_label)
                    first_completed_event.set()
                self.log(f"🎉【{dev_label}】複製完成！已釋出 USB 插槽。")

            worker_threads = []
            phase1_threads = []

            # 找出需要執行的第一階段已連線設備，與待換插設備 (Phase 2)
            phase1_items = [x for x in precheck_results if x.get("connected") and not x.get("is_swap_phase2") and x.get("files")]
            pending_swap_item = next((x for x in precheck_results if x.get("is_swap_phase2")), None)

            # 為所有第一階段已連線設備啟動背景獨立線程平行複製
            for p1 in phase1_items:
                p1_type = p1["type"]
                p1_label = p1["label"]
                if p1_type == "dashcam":
                    dst = os.path.join(dashcam_target, today_mmdd, case_no)
                else:
                    name = p1.get("name", "")
                    folder_name = f"{case_no}{name}" if name else f"{case_no}"
                    dst = os.path.join(bodycam_target, today_mmdd, case_no, folder_name)
                    case_root = os.path.join(bodycam_target, today_mmdd, case_no)
                    if case_root not in opened_folders:
                        opened_folders.append(case_root)

                t = threading.Thread(target=copy_files_worker, args=(p1["files"], dst, p1_type, p1_label))
                t.daemon = True
                worker_threads.append(t)
                phase1_threads.append(t)

            # 若有待換插設備（電腦 3 孔限制）：啟動動態換插監控線程，看哪台先好就拔哪台換插！
            if pending_swap_item:
                def swap_watcher_flow():
                    self.log(f"⏳ [{pending_swap_item['label']} 換插佇列] 電腦 3 孔限制：已連線之設備平行複製中，看哪台先好就拔哪台換插...")

                    # 等待任一第一階段設備完成
                    while not self.stop_requested.is_set():
                        if first_completed_event.wait(timeout=0.5):
                            break
                        if phase1_threads and all(not th.is_alive() for th in phase1_threads):
                            break
                        if not phase1_threads:
                            break

                    if self.stop_requested.is_set():
                        return

                    with completed_lock:
                        done_names = "、".join([f"【{x}】" for x in completed_devices]) if completed_devices else "已完成設備"

                    self.log(f"⚡ {done_names} 已複製完成！已釋出 USB 插槽。")
                    self.log(f"📢 提示換插：看哪台先好就拔哪台，請拔下已完成之設備，換插【{pending_swap_item['label']}】！")

                    p_type = pending_swap_item["type"]
                    p_name = pending_swap_item.get("name", "")
                    p_label = self.app.config.get(f"label_{p_type}", "BODYCAM3" if p_type == "bodycam3" else "DASHCAM")

                    confirmed, new_files = self.prompt_card_swap(
                        reader_path="",
                        target_dev_type=p_type,
                        target_name=p_name,
                        target_label=p_label,
                        start_dt=start_dt,
                        end_dt=end_dt,
                        completed_dev_names=done_names
                    )

                    if confirmed and not self.stop_requested.is_set():
                        if new_files:
                            with progress_lock:
                                total_files_counter[0] += len(new_files)
                                cur_t = total_files_counter[0]
                                self.update_progress_bar(maximum=cur_t)

                            if p_type == "dashcam":
                                p_dst = os.path.join(dashcam_target, today_mmdd, case_no)
                            else:
                                folder_name = f"{case_no}{p_name}" if p_name else f"{case_no}"
                                p_dst = os.path.join(bodycam_target, today_mmdd, case_no, folder_name)
                                case_root = os.path.join(bodycam_target, today_mmdd, case_no)
                                if case_root not in opened_folders:
                                    opened_folders.append(case_root)

                            self.log(f"⚡ [換插接續] 成功對接【{pending_swap_item['label']}】，開始複製 (共 {len(new_files)} 部影片)...")
                            copy_files_worker(new_files, p_dst, p_type, pending_swap_item['label'])
                        else:
                            self.log(f"🟡 [換插作業] 【{pending_swap_item['label']}】在時段內無影片 (0 部)。")
                            missing_reasons = self.app.config.get("missing_reasons", DEFAULT_MISSING_REASONS)
                            reason = missing_reasons[0] if missing_reasons else "未錄影"
                            empty_folder_reasons[p_type] = reason

                            if p_type == "dashcam":
                                empty_dir = os.path.join(dashcam_target, today_mmdd, f"{case_no}({reason})")
                            else:
                                folder_name = f"{case_no}{p_name}({reason})" if p_name else f"{case_no}({reason})"
                                empty_dir = os.path.join(bodycam_target, today_mmdd, case_no, folder_name)

                            os.makedirs(empty_dir, exist_ok=True)
                            self.active_created_folders.append(empty_dir)
                            opened_folders.append(empty_dir)

                            readme_p = os.path.join(empty_dir, "說明.txt")
                            try:
                                with open(readme_p, "w", encoding="utf-8") as rf:
                                    st_name = getattr(self.app, 'config', {}).get("station_name", "消防分隊")
                                    rf.write("========================================\n")
                                    rf.write(f"{st_name} 救護影像歸檔備註說明\n")
                                    rf.write("========================================\n")
                                    rf.write(f"案件案號：{case_no}\n")
                                    rf.write(f"設備名稱：{pending_swap_item['label']}\n")
                                    rf.write(f"執勤同仁：{p_name if p_name else '（無）'}\n")
                                    rf.write(f"建檔時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                                    rf.write(f"未錄影原因：{reason}\n")
                                    rf.write(f"說明備註：本梯次該設備因【{reason}】，於出勤時段內無影像錄製存檔，特此建立規範存查。\n")
                                    rf.write("========================================\n")
                            except:
                                pass
                            self.log(f"📁 已為【{pending_swap_item['label']}】自動建立規範備註空資料夾：{os.path.basename(empty_dir)}")
                    else:
                        self.log(f"⚠️ 使用者已略過【{pending_swap_item['label']}】換插作業。")

                t_swap = threading.Thread(target=swap_watcher_flow)
                t_swap.daemon = True
                worker_threads.append(t_swap)

            for t in worker_threads:
                t.start()

            for t in worker_threads:
                t.join()

            if self.stop_requested.is_set():
                self.handle_abort()
                return

            total_all_done = sum(device_copied_counts.values())
            self.update_status(f"狀態：🎉 已完整處理完畢！共寫入 {total_all_done} 個影片檔案", "#008800")
            self.log(f"✅ 成功！全數設備複製程序已完整結束。")
            self.active_created_folders = []

            # 若視窗目前處於最小化至系統匣狀態，彈出 Windows 氣球通知提醒出勤同仁
            if getattr(self.app, 'is_minimized_to_tray', False):
                if hasattr(self.app, 'tray_manager') and self.app.tray_manager:
                    self.app.tray_manager.show_balloon(
                        "🎉【備份完成】救護影像抓取小幫手",
                        f"案件【{case_no}】出勤影像已全數複製完成！共寫入 {total_all_done} 部影片，點擊右下角圖示即可查看成果。"
                    )

            self.safe_root_after(0, lambda: self.show_completion_dialog(
                case_no, today_mmdd, device_copied_counts, opened_folders, precheck_results, empty_folder_reasons
            ))

        except Exception as e:
            self.update_status("狀態：❌ 發生錯誤", "#CC0000")
            self.log(f"❌ 錯誤: {str(e)}")
            self.show_error_async("錯誤", f"複製過程中發生問題：\n{str(e)}")
        finally:
            self.is_copying = False
            self.stop_requested.clear()
            def _reset_btns():
                try:
                    self.btn_start.config(state="normal")
                    self.btn_pause.config(state="disabled")
                except:
                    pass
            try:
                if threading.current_thread() is threading.main_thread():
                    _reset_btns()
                else:
                    self.safe_root_after(0, _reset_btns)
            except:
                pass

    def show_completion_dialog(self, case_no, today_mmdd, counts, opened_folders, precheck_results, empty_folder_reasons=None):
        """彈出複製完成成果統計對話框，含一鍵開啟資料夾與確認重置欄位"""
        if empty_folder_reasons is None:
            empty_folder_reasons = {}

        dlg = tk.Toplevel(self.app.root)
        dlg.title(f"🎉 歸檔完成 - 案件【{case_no}】")
        dlg.geometry("560x460")
        dlg.resizable(False, False)
        dlg.transient(self.app.root)
        dlg.grab_set()

        dlg.update_idletasks()
        x = self.app.root.winfo_x() + (self.app.root.winfo_width() - 560) // 2
        y = self.app.root.winfo_y() + (self.app.root.winfo_height() - 460) // 2
        dlg.geometry(f"+{max(0, x)}+{max(0, y)}")

        f_content = ttk.Frame(dlg, padding=16)
        f_content.pack(fill="both", expand=True)

        ttk.Label(f_content, text=f"🎉 案件【{case_no}】影像歸檔成功！", font=('Helvetica', 13, 'bold'), foreground="#008800").pack(anchor="w", pady=(0, 4))
        ttk.Label(f_content, text=f"今日目錄代碼：[{today_mmdd}]，所有影片皆已妥善保存歸檔。", foreground="#555555").pack(anchor="w", pady=(0, 8))

        ttk.Separator(f_content, orient="horizontal").pack(fill="x", pady=4)
        ttk.Label(f_content, text="📊 各設備複製成果統計：", font=('Helvetica', 10, 'bold')).pack(anchor="w", pady=(4, 6))

        frame_stats = ttk.Frame(f_content)
        frame_stats.pack(fill="both", expand=True, pady=4)

        for item in precheck_results:
            dev_type = item["type"]
            copied = counts.get(dev_type, 0)
            if dev_type in empty_folder_reasons:
                ttk.Label(frame_stats, text=f"• {item['label']}：📁 已建立備註空資料夾 ({empty_folder_reasons[dev_type]})", font=('Helvetica', 10), foreground="#007ACC").pack(anchor="w", pady=2)
            elif copied > 0:
                ttk.Label(frame_stats, text=f"• {item['label']}：共複製 {copied} 個檔案", font=('Helvetica', 10), foreground="#008800").pack(anchor="w", pady=2)
            elif item.get("connected"):
                ttk.Label(frame_stats, text=f"• {item['label']}：未複製 (0 部影片)", font=('Helvetica', 10), foreground="#888888").pack(anchor="w", pady=2)

        total_all = sum(counts.values())
        ttk.Separator(f_content, orient="horizontal").pack(fill="x", pady=6)
        ttk.Label(f_content, text=f"總計複製：{total_all} 個分割影片檔案", font=('Helvetica', 11, 'bold'), foreground="#0066CC").pack(anchor="w", pady=(0, 10))

        f_btns = ttk.Frame(f_content)
        f_btns.pack(fill="x", pady=(6, 0))

        def open_target():
            for f in set(opened_folders):
                open_folder_in_explorer(f)
            dlg.destroy()
            self.reset_case_fields()

        def on_confirm_reset():
            dlg.destroy()
            self.reset_case_fields()

        dlg.protocol("WM_DELETE_WINDOW", on_confirm_reset)

        btn_open = ttk.Button(f_btns, text="📂 一鍵開啟目標資料夾", command=open_target)
        btn_open.pack(side="left", ipady=6, padx=(0, 6))

        btn_close = ttk.Button(f_btns, text="確定", command=on_confirm_reset)
        btn_close.pack(side="right", fill="x", expand=True, ipady=6, padx=(6, 0))

        dlg.wait_window()

    def reset_case_fields(self):
        """使用者按確認後，該分頁的案號、出勤時間、返隊時間、出勤人員姓名皆恢復預設，並重新刷新連線狀態"""
        now = datetime.now()
        dispatch_ref = now - timedelta(hours=1)
        self.entry_case_no.delete(0, tk.END)
        self.on_case_no_changed()

        if hasattr(self, 'entry_year'):
            self.entry_year.delete(0, tk.END)
            self.entry_year.insert(0, str(dispatch_ref.year - 1911))

        if hasattr(self, 'entry_month_day'):
            self.entry_month_day.delete(0, tk.END)
            self.entry_month_day.insert(0, dispatch_ref.strftime("%m%d"))

        self.entry_start_time.delete(0, tk.END)
        self.entry_start_time.insert(0, dispatch_ref.strftime("%H%M"))

        self.entry_end_time.delete(0, tk.END)
        self.entry_name1.delete(0, tk.END)
        self.entry_name2.delete(0, tk.END)
        self.entry_name3.delete(0, tk.END)

        self.on_date_time_changed()
        self.refresh_device_connections()
        self.app.update_tab_labels()

        self.progress_bar["value"] = 0
        self.lbl_status.config(text="狀態：準備就緒 (欄位已重置)", foreground="#0066CC")
        self.log("✨ 本次任務已完成，案件資訊欄位已自動恢復空白預設值。")

    def handle_abort(self):
        """處理暫停終止與檔案清理流程"""
        self.lbl_status.config(text="狀態：已暫停並刪除資料夾", foreground="#CC0000")
        self.log("🛑 作業已終止，正在執行資料夾清除動作...")
        self.cleanup_active_folders()
        self.stop_requested.clear()
        self.log("✨ 清理完畢！未保留任何殘留檔案。")
        self.progress_bar["value"] = 0
        messagebox.showinfo("已暫停並清除", "複製作業已成功暫停！\n本次已建立之資料夾與未完成檔案已全數刪除還原。")


_GLOBAL_APP_REF = None
_GLOBAL_SINGLE_INSTANCE_HANDLE = None

def release_single_instance_handle():
    """釋放單一執行個體鎖定（供自動重啟更新前呼叫，避免新進程被誤判重複執行）"""
    global _GLOBAL_SINGLE_INSTANCE_HANDLE
    if _GLOBAL_SINGLE_INSTANCE_HANDLE:
        try:
            if sys.platform == "win32":
                import ctypes
                ctypes.windll.kernel32.CloseHandle(_GLOBAL_SINGLE_INSTANCE_HANDLE)
            else:
                _GLOBAL_SINGLE_INSTANCE_HANDLE.close()
        except Exception:
            pass
        _GLOBAL_SINGLE_INSTANCE_HANDLE = None

def check_single_instance():
    """檢查是否已有相同程式執行中。
    若已有執行個體：
    - Windows: 透過 Win32 Mutex + FindWindow 喚醒現有視窗，並通知現有視窗還原至最上層，本進程立刻退出。
    - Mac/Linux: 透過本機 Loopback Socket (45192) 通知現有實例喚醒並退出。
    若為第一個執行個體：正常啟動並保留常駐把手。
    """
    global _GLOBAL_SINGLE_INSTANCE_HANDLE
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            MUTEX_NAME = r"Local\EMS_VideoBackup_SingleInstance_Mutex"
            CLASS_NAME = "EMS_VideoBackup_Tray_Class"
            WM_USER = 0x0400
            WM_ACTIVATE_INSTANCE = WM_USER + 50

            kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            kernel32.GetLastError.restype = wintypes.DWORD

            mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
            ERROR_ALREADY_EXISTS = 183

            if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
                # 已有執行中的實例！
                user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
                user32.FindWindowW.restype = wintypes.HWND
                user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
                user32.PostMessageW.restype = wintypes.BOOL
                user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
                user32.AllowSetForegroundWindow.restype = wintypes.BOOL

                # 授予既有視窗切換至最前景之授權 (ASFW_ANY: 0xFFFFFFFF)
                try:
                    user32.AllowSetForegroundWindow(0xFFFFFFFF)
                except Exception:
                    pass

                # 尋找已在背景運作的系統匣隱藏視窗
                hwnd = user32.FindWindowW(CLASS_NAME, None)
                if not hwnd:
                    import time
                    for _ in range(10):
                        time.sleep(0.1)
                        hwnd = user32.FindWindowW(CLASS_NAME, None)
                        if hwnd:
                            break

                if hwnd:
                    user32.PostMessageW(hwnd, WM_ACTIVATE_INSTANCE, 0, 0)
                else:
                    hwnd_main = user32.FindWindowW(None, APP_TITLE) or user32.FindWindowW(None, "救護影像抓取小幫手 v4.0")
                    if hwnd_main:
                        user32.ShowWindow(hwnd_main, 9)
                        user32.SetForegroundWindow(hwnd_main)

                sys.exit(0)

            _GLOBAL_SINGLE_INSTANCE_HANDLE = mutex
            return mutex
        except Exception as e:
            print(f"[SingleInstance] Windows 檢查失敗: {e}")
            return None
    else:
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(('127.0.0.1', 45192))
                s.listen(1)

                def _listen_worker():
                    while True:
                        try:
                            conn, _ = s.accept()
                            data = conn.recv(64)
                            if b"ACTIVATE" in data:
                                global _GLOBAL_APP_REF
                                if _GLOBAL_APP_REF:
                                    _GLOBAL_APP_REF.root.after(0, _GLOBAL_APP_REF.activate_and_bring_to_front)
                            conn.close()
                        except Exception:
                            break

                t = threading.Thread(target=_listen_worker, daemon=True)
                t.start()
                _GLOBAL_SINGLE_INSTANCE_HANDLE = s
                return s
            except socket.error:
                try:
                    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    client.connect(('127.0.0.1', 45192))
                    client.sendall(b"ACTIVATE")
                    client.close()
                except Exception:
                    pass
                sys.exit(0)
        except Exception as e:
            print(f"[SingleInstance] Unix 檢查失敗: {e}")
            return None

# ==============================================================================
# Windows 系統專屬：系統匣 (通知區域 / 右下角) 原生 Win32 API 圖示管理模組
# ==============================================================================
_GLOBAL_TRAY_MGR = None
_GLOBAL_WNDPROC_REF = None

def _global_wnd_proc(hwnd, msg, wparam, lparam):
    global _GLOBAL_TRAY_MGR
    try:
        WM_USER = 0x0400
        WM_TRAYICON = WM_USER + 20
        WM_ACTIVATE_INSTANCE = WM_USER + 50

        if msg == WM_ACTIVATE_INSTANCE:
            if _GLOBAL_TRAY_MGR and _GLOBAL_TRAY_MGR.app:
                _GLOBAL_TRAY_MGR.root.after(0, _GLOBAL_TRAY_MGR.app.activate_and_bring_to_front)
            return 0

        if msg == WM_TRAYICON:
            # 取得低 16 位元事件代碼 (跨 32-bit / 64-bit Windows 與所有版本相容)
            evt = lparam & 0xFFFF
            # 僅捕捉使用者的主動點擊事件：
            # 0x0202: WM_LBUTTONUP (左鍵放開)
            # 0x0203: WM_LBUTTONDBLCLK (左鍵雙擊)
            # 0x0400: NIN_SELECT (V4 滑鼠點擊選取或鍵盤 Space/Enter)
            # 0x0401: NIN_KEYSELECT (V4 鍵盤選取)
            # 0x0405: NIN_BALLOONUSERCLICK (使用者主動點擊氣球通知)
            # ⚠️ 關鍵防護：絕不可包含 0x0402 (NIN_BALLOONSHOW)！因 Windows 彈出氣球通知瞬間會發送 0x0402，
            # 若包含 0x0402 會導致第一次最小化時氣球剛顯示就被誤判為點擊而立即把視窗彈回桌面！
            if evt in (0x0202, 0x0203, 0x0400, 0x0401, 0x0405):
                if _GLOBAL_TRAY_MGR and _GLOBAL_TRAY_MGR.app:
                    _GLOBAL_TRAY_MGR.root.after(0, _GLOBAL_TRAY_MGR.app.restore_from_tray)
                return 0
            # 右鍵放開 (0x0205)、內容選單 (0x007B)
            elif evt in (0x0205, 0x007B):
                if _GLOBAL_TRAY_MGR:
                    _GLOBAL_TRAY_MGR._show_context_menu()
                return 0
        elif _GLOBAL_TRAY_MGR and _GLOBAL_TRAY_MGR.wm_taskbar_created and msg == _GLOBAL_TRAY_MGR.wm_taskbar_created:
            if _GLOBAL_TRAY_MGR.is_icon_visible:
                _GLOBAL_TRAY_MGR.show_tray_icon()
            return 0
        elif msg == 0x0010: # WM_CLOSE
            import ctypes
            ctypes.windll.user32.PostQuitMessage(0)
            return 0
    except Exception:
        pass

    try:
        import ctypes
        return ctypes.windll.user32.DefWindowProcW(hwnd, msg, wparam, lparam)
    except Exception:
        return 0


class WindowsTrayManager:
    """Windows 系統匣（右下角通知區域）圖示管理器
    使用純 Win32 API (ctypes) 獨立背景線程訊息佇列實作，零第三方套件依賴。
    - 最小化自動隱藏工作列並常駐至右下角系統匣
    - 單擊/雙擊/通知點擊即時秒還原主視窗
    - 64 位元型別完整宣告，徹底根除存取違規與無預警關閉
    - 還原保護鎖防反覆最小化
    """
    def __init__(self, app):
        global _GLOBAL_TRAY_MGR, _GLOBAL_WNDPROC_REF
        self.app = app
        self.root = app.root
        self.hwnd = None
        self.hicon = None
        self.is_icon_visible = False
        self.class_name = "EMS_VideoBackup_Tray_Class"
        self.wm_taskbar_created = 0
        self._thread = None
        self._ready_event = threading.Event()
        _GLOBAL_TRAY_MGR = self

        if sys.platform == "win32":
            self._init_win32_api()
            self._start_thread()

    def _init_win32_api(self):
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            shell32 = ctypes.windll.shell32

            LRESULT = ctypes.c_ssize_t
            WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
            WNDPROC = WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

            global _GLOBAL_WNDPROC_REF
            _GLOBAL_WNDPROC_REF = WNDPROC(_global_wnd_proc)

            # 明確宣告 Win32 API 參數與回傳型別，杜絕 64 位元暫存器截斷崩潰
            user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            user32.DefWindowProcW.restype = LRESULT

            user32.CreateWindowExW.argtypes = [
                wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID
            ]
            user32.CreateWindowExW.restype = wintypes.HWND

            user32.DestroyWindow.argtypes = [wintypes.HWND]
            user32.DestroyWindow.restype = wintypes.BOOL

            user32.RegisterClassW.argtypes = [ctypes.c_void_p]
            user32.RegisterClassW.restype = wintypes.ATOM

            user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
            user32.UnregisterClassW.restype = wintypes.BOOL

            user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            user32.PostMessageW.restype = wintypes.BOOL

            user32.PostQuitMessage.argtypes = [ctypes.c_int]
            user32.PostQuitMessage.restype = None

            user32.GetMessageW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT]
            user32.GetMessageW.restype = wintypes.BOOL

            user32.TranslateMessage.argtypes = [ctypes.c_void_p]
            user32.TranslateMessage.restype = wintypes.BOOL

            user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
            user32.DispatchMessageW.restype = LRESULT

            shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.c_void_p]
            shell32.Shell_NotifyIconW.restype = wintypes.BOOL
        except Exception as e:
            print(f"[WindowsTrayManager] API 宣告錯誤: {e}")

    def _start_thread(self):
        self._thread = threading.Thread(target=self._tray_thread_worker, daemon=True)
        self._thread.start()
        # 等待視窗與圖示建立完成 (最多等 1.5 秒)
        self._ready_event.wait(timeout=1.5)

    def _tray_thread_worker(self):
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            hinstance = kernel32.GetModuleHandleW(None)

            class WNDCLASSW(ctypes.Structure):
                _fields_ = [
                    ("style", wintypes.UINT),
                    ("lpfnWndProc", ctypes.c_void_p),
                    ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE),
                    ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HICON),
                    ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR),
                    ("lpszClassName", wintypes.LPCWSTR),
                ]

            global _GLOBAL_WNDPROC_REF
            wc = WNDCLASSW()
            wc.style = 0
            wc.lpfnWndProc = ctypes.cast(_GLOBAL_WNDPROC_REF, ctypes.c_void_p).value
            wc.hInstance = hinstance
            wc.lpszClassName = self.class_name

            user32.RegisterClassW(ctypes.byref(wc))

            self.hwnd = user32.CreateWindowExW(
                0, self.class_name, "EMSBackupTrayMsgWnd", 0,
                0, 0, 0, 0, None, None, hinstance, None
            )

            self.hicon = self._load_icon()
            self.wm_taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
            self._ready_event.set()

            class MSG(ctypes.Structure):
                _fields_ = [
                    ("hwnd", wintypes.HWND),
                    ("message", wintypes.UINT),
                    ("wParam", wintypes.WPARAM),
                    ("lParam", wintypes.LPARAM),
                    ("time", wintypes.DWORD),
                    ("pt", wintypes.POINT),
                ]

            msg = MSG()
            p_msg = ctypes.byref(msg)
            while user32.GetMessageW(p_msg, None, 0, 0) > 0:
                user32.TranslateMessage(p_msg)
                user32.DispatchMessageW(p_msg)

        except Exception as e:
            print(f"[WindowsTrayManager] 執行緒異常: {e}")
        finally:
            try:
                if self.hwnd:
                    user32.DestroyWindow(self.hwnd)
                    user32.UnregisterClassW(self.class_name, hinstance)
            except Exception:
                pass
            self.hwnd = None

    def _load_icon(self):
        try:
            import ctypes
            user32 = ctypes.windll.user32
            shell32 = ctypes.windll.shell32
            kernel32 = ctypes.windll.kernel32
            hinstance = kernel32.GetModuleHandleW(None)

            # 1. 優先載入同目錄下的 app.ico
            for ico_name in ("app.ico", "icon.ico", "favicon.ico"):
                ico_path = os.path.join(APP_DIR, ico_name)
                if os.path.exists(ico_path):
                    try:
                        h = user32.LoadImageW(
                            None, ico_path, 1, 16, 16, 0x00000010 | 0x00008000
                        )
                        if h:
                            return h
                    except Exception:
                        pass

            # 2. 若為 PyInstaller 打包的 EXE，嘗試從自身執行檔提取圖示
            if getattr(sys, "frozen", False):
                try:
                    h = shell32.ExtractIconW(hinstance, sys.executable, 0)
                    if h and int(ctypes.cast(h, ctypes.c_void_p).value or 0) > 1:
                        return h
                except Exception:
                    pass

            # 3. 預設系統應用程式圖示 (IDI_APPLICATION: 32512)
            return user32.LoadIconW(None, 32512)
        except Exception:
            return None

    def _show_context_menu(self):
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32

            POINT = wintypes.POINT
            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))

            hMenu = user32.CreatePopupMenu()
            ID_RESTORE = 1001
            ID_EXIT = 1002
            MF_STRING = 0x0000
            MF_SEPARATOR = 0x0800
            TPM_RIGHTBUTTON = 0x0002
            TPM_RETURNCMD = 0x0100

            user32.AppendMenuW(hMenu, MF_STRING, ID_RESTORE, "🖥️ 開啟主視窗")
            user32.AppendMenuW(hMenu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(hMenu, MF_STRING, ID_EXIT, "❌ 結束程式")

            user32.SetForegroundWindow(self.hwnd)

            cmd = user32.TrackPopupMenuEx(
                hMenu,
                TPM_RIGHTBUTTON | TPM_RETURNCMD,
                pt.x,
                pt.y,
                self.hwnd,
                None
            )
            user32.PostMessageW(self.hwnd, 0, 0, 0)
            user32.DestroyMenu(hMenu)

            if cmd == ID_RESTORE:
                self.root.after(0, self.app.restore_from_tray)
            elif cmd == ID_EXIT:
                self.root.after(0, self.app.on_app_close)
        except Exception as e:
            print(f"[WindowsTrayManager] 彈出選單異常: {e}")

    def _create_nid(self, flags=None):
        import ctypes
        from ctypes import wintypes

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT),
                ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128),
                ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256),
                ("uTimeoutOrVersion", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", ctypes.c_byte * 16),
                ("hBalloonIcon", wintypes.HICON),
            ]

        NIF_MESSAGE = 0x00000001
        NIF_ICON = 0x00000002
        NIF_TIP = 0x00000004
        WM_USER = 0x0400
        WM_TRAYICON = WM_USER + 20

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1001
        nid.uFlags = flags if flags is not None else (NIF_MESSAGE | NIF_ICON | NIF_TIP)
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = self.hicon
        tip = "救護影像抓取小幫手 (點擊還原視窗)"
        nid.szTip = tip[:127]
        return nid

    def show_tray_icon(self, balloon_title=None, balloon_msg=None):
        if not self.hwnd:
            return
        try:
            import ctypes
            shell32 = ctypes.windll.shell32
            NIM_ADD = 0x00000000
            NIM_MODIFY = 0x00000001
            NIF_MESSAGE = 0x00000001
            NIF_ICON = 0x00000002
            NIF_TIP = 0x00000004
            NIF_INFO = 0x00000010
            NIIF_INFO = 0x00000001

            flags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            if balloon_msg:
                flags |= NIF_INFO

            nid = self._create_nid(flags=flags)
            if balloon_msg:
                nid.szInfo = str(balloon_msg)[:255]
                nid.szInfoTitle = str(balloon_title or "救護影像抓取小幫手")[:63]
                nid.dwInfoFlags = NIIF_INFO

            if not self.is_icon_visible:
                res = shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
                if not res:
                    nid.cbSize = 504 if ctypes.sizeof(ctypes.c_void_p) == 8 else 488
                    res = shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
                self.is_icon_visible = True
            else:
                shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
        except Exception as e:
            print(f"[WindowsTrayManager] 顯示系統匣圖示失敗: {e}")

    def hide_tray_icon(self):
        if not self.hwnd or not self.is_icon_visible:
            return
        try:
            import ctypes
            shell32 = ctypes.windll.shell32
            NIM_DELETE = 0x00000002
            nid = self._create_nid()
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            self.is_icon_visible = False
        except Exception as e:
            print(f"[WindowsTrayManager] 移除系統匣圖示失敗: {e}")

    def show_balloon(self, title, message):
        if not self.hwnd:
            return
        self.show_tray_icon(balloon_title=title, balloon_msg=message)

    def destroy(self):
        self.hide_tray_icon()
        if self.hwnd:
            try:
                import ctypes
                ctypes.windll.user32.PostMessageW(self.hwnd, 0x0012, 0, 0) # WM_QUIT
            except Exception:
                pass


class VideoBackupApp:
    def __init__(self, root):
        global _GLOBAL_APP_REF
        _GLOBAL_APP_REF = self
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("750x920")
        self.root.minsize(720, 680)

        self.bg_color = "#F5F5F7"
        self.root.configure(bg=self.bg_color)

        self.next_tab_id = 1
        self.task_tabs = []
        self.roster_cache = {}

        # 車輛系統登入與背景心跳保活控制器 (獨立 Port 9223，與日常 Chrome 及差勤助手互不干擾)
        self.car_cdp = VehicleCDPController(port=9223)
        self.last_car_heartbeat_time = 0.0
        self.last_car_heartbeat_str = "尚未發送"
        self.car_heartbeat_interval_sec = 180  # 每 3 分鐘自動發送防斷線心跳
        self.car_mileage_flow_active = False
        self.car_mileage_flow_completed = False

        self.config = self.load_config()

        # 需求 2: 軟體啟動時，下拉選單名冊先依據前一個任務的快取紀錄
        cached = self.config.get("cached_officer_names")
        if cached and isinstance(cached, list) and len(cached) > 0:
            self.officer_names = list(cached)
        else:
            self.officer_names = list(DEFAULT_OFFICER_NAMES)

        self.setup_styles()
        self.create_widgets()
        self.update_comboboxes_all_tabs()
        self.root.update_idletasks()
        self.root.resizable(True, True)
        self.root.protocol("WM_DELETE_WINDOW", self.on_app_close)

        # Windows 專屬：右下角系統匣最小化與圖示管理
        self.is_minimized_to_tray = False
        self._restoring = False
        self.last_window_state = "normal"
        self._shown_first_tray_balloon = False

        if sys.platform == "win32":
            ico_path = os.path.join(APP_DIR, "app.ico")
            if os.path.exists(ico_path):
                try:
                    self.root.iconbitmap(ico_path)
                except Exception:
                    pass

            self.tray_manager = WindowsTrayManager(self)
            self.root.bind("<Unmap>", self.on_window_unmap)
            self.root.bind("<Configure>", self.on_window_configure)

        # 🚀 啟動性能優化：非同步延遲 150ms 載入勤務表，讓視窗秒開，不卡主執行緒
        self.root.after(150, self.load_officer_names_from_roster)
        # 🚀 啟動車輛系統背景心跳監控執行緒
        self.root.after(600, self._start_car_cdp_monitor)
        # 🚀 自動更新檢查：非同步延遲 1200ms 檢查指定資料夾是否有新版本檔案
        self.root.after(1200, self.auto_check_update_on_startup)

    def hide_to_tray(self):
        """將視窗最小化隱藏至 Windows 右下角系統匣 (通知區域)"""
        if getattr(self, "is_minimized_to_tray", False) or getattr(self, "_restoring", False):
            return
        self.is_minimized_to_tray = True
        try:
            self.root.withdraw()
        except Exception:
            pass

        if hasattr(self, 'tray_manager') and self.tray_manager:
            first_time = getattr(self, '_shown_first_tray_balloon', False)
            if not first_time:
                self._shown_first_tray_balloon = True
                self.tray_manager.show_tray_icon(
                    balloon_title="救護影像抓取小幫手",
                    balloon_msg="已自動縮小至右下角系統匣，點擊此圖示即可隨時還原視窗！"
                )
            else:
                self.tray_manager.show_tray_icon()

    def activate_and_bring_to_front(self):
        """當使用者重複點擊開啟程式時，喚醒既有實例並切換至最前景"""
        if getattr(self, "is_minimized_to_tray", False):
            # 若原本為縮小至右下角系統匣狀態，還原為正常視窗
            self.restore_from_tray()
        else:
            # 若原本為一般視窗或一般工作列縮小狀態，直接還原並置頂
            self._restoring = True
            try:
                self.root.deiconify()
            except Exception:
                pass

            target_state = getattr(self, "last_window_state", "normal")
            if target_state not in ("normal", "zoomed"):
                target_state = "normal"
            try:
                self.root.state(target_state)
            except Exception:
                pass

            try:
                self.root.lift()
                self.root.attributes("-topmost", True)
                self.root.attributes("-topmost", False)
                self.root.focus_force()
            except Exception:
                pass

            # 在 Windows 底層調用 SetForegroundWindow
            if sys.platform == "win32":
                try:
                    import ctypes
                    user32 = ctypes.windll.user32
                    hwnd_main = user32.FindWindowW(None, APP_TITLE) or user32.FindWindowW(None, "救護影像抓取小幫手 v4.0")
                    if hwnd_main:
                        user32.ShowWindow(hwnd_main, 9)
                        user32.SetForegroundWindow(hwnd_main)
                except Exception:
                    pass

            # 若有任何子視窗 (如確認視窗或完成視窗)，一併提升至最上層
            try:
                for child in self.root.winfo_children():
                    if isinstance(child, tk.Toplevel) and child.state() != "withdrawn":
                        try:
                            child.lift()
                            child.grab_set()
                        except Exception:
                            pass
            except Exception:
                pass

            def _clear_restoring():
                self._restoring = False
            self.root.after(500, _clear_restoring)

    def restore_from_tray(self):
        """從 Windows 右下角系統匣還原主視窗"""
        # 還原保護旗標：避免還原動畫期間觸發重複最小化
        self._restoring = True
        self.is_minimized_to_tray = False

        if hasattr(self, 'tray_manager') and self.tray_manager:
            self.tray_manager.hide_tray_icon()

        try:
            self.root.deiconify()
        except Exception:
            pass

        target_state = getattr(self, "last_window_state", "normal")
        if target_state not in ("normal", "zoomed"):
            target_state = "normal"
        try:
            self.root.state(target_state)
        except Exception:
            pass

        try:
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.attributes("-topmost", False)
            self.root.focus_force()
        except Exception:
            pass

        # 若有子視窗 (如歸檔完成對話框)，一併還原並置頂
        try:
            for child in self.root.winfo_children():
                if isinstance(child, tk.Toplevel) and child.state() != "withdrawn":
                    try:
                        child.lift()
                        child.grab_set()
                    except Exception:
                        pass
        except Exception:
            pass

        # 延遲 500ms 解除還原保護鎖
        def _clear_restoring():
            self._restoring = False
        self.root.after(500, _clear_restoring)

    def on_window_unmap(self, event):
        """監聽視窗 Unmap (最小化) 事件"""
        if sys.platform != "win32":
            return
        if event.widget != self.root:
            return
        if getattr(self, "is_minimized_to_tray", False) or getattr(self, "_restoring", False):
            return
        self.root.after(35, self._check_minimized)

    def on_window_configure(self, event):
        """監聽視窗配置改變，記憶還原時的視窗狀態 (一般或最大化)"""
        if sys.platform != "win32":
            return
        if event.widget == self.root:
            if not getattr(self, "_restoring", False) and not getattr(self, "is_minimized_to_tray", False):
                try:
                    st = self.root.state()
                    if st in ("normal", "zoomed"):
                        self.last_window_state = st
                    elif st == "iconic":
                        self.root.after(35, self._check_minimized)
                except Exception:
                    pass

    def _check_minimized(self):
        """檢查是否觸發最小化至系統匣"""
        if sys.platform != "win32":
            return
        if getattr(self, "is_minimized_to_tray", False) or getattr(self, "_restoring", False):
            return
        try:
            st = self.root.state()
            if st == "iconic":
                if self.config.get("minimize_to_tray", True):
                    self.hide_to_tray()
        except Exception:
            pass

    def on_app_close(self):
        """軟體關閉時自動儲存當前設定與任務同仁紀錄"""
        active_copying = [t for t in self.task_tabs if getattr(t, 'is_copying', False)]
        if active_copying:
            if not messagebox.askyesno("確認關閉", "目前有備份任務正在複製檔案中！\n確定要強制結束程式並放棄進行中的備份嗎？"):
                return
            for t in active_copying:
                try:
                    t.stop_requested.set()
                except Exception:
                    pass

        if hasattr(self, 'tray_manager') and self.tray_manager:
            try:
                self.tray_manager.destroy()
            except Exception:
                pass
        try:
            self.save_config()
        except:
            pass
        self.root.destroy()

    def get_roster_names_from_file(self, file_path):
        """讀取指定勤務表檔案的同仁名冊 (C18:Z24)，支援快速記憶快取"""
        if not file_path or not os.path.isfile(file_path):
            return None
        if not hasattr(self, 'roster_cache'):
            self.roster_cache = {}
        try:
            mtime = os.path.getmtime(file_path)
            cache_key = (file_path, mtime)
            if cache_key in self.roster_cache:
                return self.roster_cache[cache_key]

            res = parse_roster_excel(file_path)
            if res.get('id_to_name'):
                sorted_ids = sorted(res['id_to_name'].keys(), key=lambda x: int(x) if x.isdigit() else 999)
                excel_names = [res['id_to_name'][k] for k in sorted_ids if res['id_to_name'][k]]
                if excel_names:
                    self.roster_cache[cache_key] = excel_names
                    self.officer_names = list(excel_names)
                    self.config["cached_officer_names"] = list(excel_names)
                    return excel_names
        except Exception:
            pass
        return None

    def load_officer_names_from_roster(self):
        """從設定的勤務表檔案或資料夾中，自動讀取最新勤務表的 C18:Z24 名冊更新下拉選單與快取"""
        path = self.config.get("roster_path", "").strip()
        if not path or not os.path.exists(path):
            return
        target_file = None
        if os.path.isfile(path):
            target_file = path
        else:
            candidates = []
            try:
                search_dirs = [path]
                for entry in os.listdir(path):
                    sub_p = os.path.join(path, entry)
                    if os.path.isdir(sub_p) and not entry.startswith('.'):
                        search_dirs.append(sub_p)

                for d in search_dirs:
                    for fname in os.listdir(d):
                        if fname.startswith('~$'):
                            continue
                        if fname.lower().endswith(('.xlsm', '.xlsx', '.xls')):
                            fp = os.path.join(d, fname)
                            if os.path.isfile(fp):
                                try:
                                    t = max(os.path.getmtime(fp), os.path.getctime(fp))
                                    candidates.append((t, fp))
                                except:
                                    pass
            except:
                pass
            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                target_file = candidates[0][1]

        if target_file:
            excel_names = self.get_roster_names_from_file(target_file)
            if excel_names:
                self.officer_names = list(excel_names)
                self.config["cached_officer_names"] = list(excel_names)
                self.update_comboboxes_all_tabs()

    def get_all_officer_names(self):
        """取得目前所有已知的同仁名單（用於下拉選單）"""
        if not hasattr(self, 'officer_names') or not self.officer_names:
            self.officer_names = list(DEFAULT_OFFICER_NAMES)
        return self.officer_names

    def update_comboboxes_all_tabs(self):
        """將目前同仁名單同步更新至所有任務分頁之下拉選單"""
        for tab in self.task_tabs:
            tab.update_combobox_values()

    def update_officer_names(self, new_names):
        """新增或更新已知同仁名冊至全域下拉選單"""
        if not hasattr(self, 'officer_names'):
            self.officer_names = list(DEFAULT_OFFICER_NAMES)
        updated = False
        for n in new_names:
            if n and isinstance(n, str) and n not in self.officer_names and len(n) <= 6:
                self.officer_names.append(n)
                updated = True
        if updated:
            self.config["cached_officer_names"] = list(self.officer_names)
        self.update_comboboxes_all_tabs()

    def setup_styles(self):
        self.style = ttk.Style(self.root)
        if sys.platform == "win32":
            if 'vista' in self.style.theme_names():
                self.style.theme_use('vista')
            elif 'xpnative' in self.style.theme_names():
                self.style.theme_use('xpnative')
        else:
            self.style.theme_use('default')
            self.style.configure('.', background=self.bg_color, foreground='#111111')
            self.style.configure('TFrame', background=self.bg_color)
            self.style.configure('TLabel', background=self.bg_color, foreground='#111111')
            self.style.configure('TLabelframe', background=self.bg_color, foreground='#111111')
            self.style.configure('TLabelframe.Label', background=self.bg_color, foreground='#111111', font=('Helvetica', 10, 'bold'))
            self.style.configure('TButton', background='#E5E5EA', foreground='#111111', relief='groove')
            self.style.configure('TEntry', fieldbackground='#FFFFFF', foreground='#111111')
            self.style.configure('TCheckbutton', background=self.bg_color, foreground='#111111')
            self.style.configure('TNotebook', background=self.bg_color)
            self.style.configure('TNotebook.Tab', padding=[12, 5], font=('Helvetica', 10, 'bold'))

        self.style.configure('Action.TButton', font=('Helvetica', 10, 'bold'))
        self.style.configure('TLabelframe.Label', font=('Helvetica', 10, 'bold'))

    def load_config(self):
        default_cfg = {
            "dashcam_target": str(Path.home() / "Desktop" / "行車紀錄器影像"),
            "dashcam_src": "",
            "bodycam_target": str(Path.home() / "Desktop" / "密錄器影像"),
            "bodycam1_src": "",
            "bodycam2_src": "",
            "bodycam3_src": "",
            "is_dashcam_locked": False,
            "is_bodycam_locked": False,
            "last_name1": "",
            "last_name2": "",
            "last_name3": "",
            "last_has_third": False,
            "cached_officer_names": list(DEFAULT_OFFICER_NAMES),
            "missing_reasons": list(DEFAULT_MISSING_REASONS),
            "name1": "",
            "name2": "",
            "name3": "",
            "label_dashcam": "DASHCAM",
            "label_bodycam1": "BODYCAM1",
            "label_bodycam2": "BODYCAM2",
            "label_bodycam3": "BODYCAM3",
            "dashcam_keywords": DEFAULT_DASHCAM_KEYWORDS,
            "dashcam_fn_patterns": DEFAULT_DASHCAM_FN_PATTERNS,
            "bodycam_keywords": DEFAULT_BODYCAM_KEYWORDS,
            "time_buffer_minutes": DEFAULT_TIME_BUFFER_MINUTES,
            "roster_path": "",
            "update_dir": "",
            "hide_base_settings": False,
            "car_system_url": DEFAULT_CAR_SYSTEM_URL,
            "car_chrome_profile": DEFAULT_CAR_CHROME_EMAIL,
            "car_preferred_account": "AUTO",
            "car_heartbeat_enabled": True,
            "car_target_license": DEFAULT_CAR_TARGET_LICENSE,
            "car_auto_submit_login": True,
            "car_auto_mileage": True
        }
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    return {**default_cfg, **json.load(f)}
            except Exception:
                pass
        return default_cfg

    def sync_fingerprint_settings_to_config(self):
        """即時將【系統設定】分頁中已編輯的標籤與特徵指紋同步至 self.config，確保即改即採納"""
        if hasattr(self, 'entry_label_dashcam'):
            self.config["label_dashcam"] = self.entry_label_dashcam.get().strip()
            self.config["label_bodycam1"] = self.entry_label_b1.get().strip()
            self.config["label_bodycam2"] = self.entry_label_b2.get().strip()
            self.config["label_bodycam3"] = self.entry_label_b3.get().strip()
        if hasattr(self, 'entry_dash_keywords'):
            self.config["dashcam_keywords"] = self.entry_dash_keywords.get().strip()
        if hasattr(self, 'entry_dash_fn_patterns'):
            self.config["dashcam_fn_patterns"] = self.entry_dash_fn_patterns.get().strip()
        if hasattr(self, 'entry_body_keywords'):
            self.config["bodycam_keywords"] = self.entry_body_keywords.get().strip()
        if hasattr(self, 'entry_time_buffer_minutes'):
            try:
                self.config["time_buffer_minutes"] = int(self.entry_time_buffer_minutes.get().strip())
            except Exception:
                self.config["time_buffer_minutes"] = DEFAULT_TIME_BUFFER_MINUTES

    def save_config(self):
        try:
            # 1. 從設定分頁抓取最新設定 (含車輛系統、勤務表路徑、設備硬碟標籤與特徵指紋)
            if hasattr(self, 'cb_car_system_url'):
                self.config["car_system_url"] = self.cb_car_system_url.get().strip() or DEFAULT_CAR_SYSTEM_URL
            if hasattr(self, 'cb_car_chrome_profile'):
                self.config["car_chrome_profile"] = resolve_chrome_profile(self.cb_car_chrome_profile.get().strip())
            if hasattr(self, 'cb_car_account'):
                sel_acct = self.get_selected_car_account()
                self.config["car_preferred_account"] = sel_acct if sel_acct else "AUTO"
            if hasattr(self, 'var_car_heartbeat'):
                self.config["car_heartbeat_enabled"] = bool(self.var_car_heartbeat.get())
            if hasattr(self, 'cb_car_target_license'):
                self.config["car_target_license"] = self.cb_car_target_license.get().strip() or DEFAULT_CAR_TARGET_LICENSE
            if hasattr(self, 'var_car_auto_submit_login'):
                self.config["car_auto_submit_login"] = bool(self.var_car_auto_submit_login.get())
            if hasattr(self, 'var_car_auto_mileage'):
                self.config["car_auto_mileage"] = bool(self.var_car_auto_mileage.get())
            if hasattr(self, 'entry_roster_path'):
                self.config["roster_path"] = self.entry_roster_path.get().strip()
            if hasattr(self, 'entry_update_dir'):
                self.config["update_dir"] = self.entry_update_dir.get().strip()
            self.sync_fingerprint_settings_to_config()
            if hasattr(self, 'txt_missing_reasons'):
                raw_r = self.txt_missing_reasons.get("1.0", tk.END)
                r_list = [line.strip() for line in raw_r.splitlines() if line.strip()]
                if r_list:
                    self.config["missing_reasons"] = r_list
            if hasattr(self, 'var_min_to_tray'):
                self.config["minimize_to_tray"] = self.var_min_to_tray.get()

            # 2. 從當前活躍分頁或最後一個任務分頁抓取路徑設定與最新同仁姓名
            active_tab = None
            try:
                sel = self.notebook.select()
                for t in self.task_tabs:
                    if str(t) == str(sel):
                        active_tab = t
                        break
            except Exception:
                pass
            if not active_tab and self.task_tabs:
                active_tab = self.task_tabs[-1]

            if active_tab:
                self.config["dashcam_target"] = active_tab.entry_dashcam_target.get().strip()
                self.config["dashcam_src"] = active_tab.entry_dashcam_src.get().strip()
                self.config["bodycam_target"] = active_tab.entry_bodycam_target.get().strip()
                self.config["bodycam1_src"] = active_tab.entry_bodycam1_src.get().strip()
                self.config["bodycam2_src"] = active_tab.entry_bodycam2_src.get().strip()
                if hasattr(active_tab, 'entry_bodycam3_src'):
                    self.config["bodycam3_src"] = active_tab.entry_bodycam3_src.get().strip()

                # 需求 2: 記憶前一個任務的同仁姓名與第三人紀錄
                n1 = active_tab.entry_name1.get().strip() if hasattr(active_tab, 'entry_name1') else ""
                n2 = active_tab.entry_name2.get().strip() if hasattr(active_tab, 'entry_name2') else ""
                n3 = active_tab.entry_name3.get().strip() if hasattr(active_tab, 'entry_name3') else ""
                has_3rd = active_tab.var_third_person.get() if hasattr(active_tab, 'var_third_person') else False
                if n1:
                    self.config["last_name1"] = n1
                if n2:
                    self.config["last_name2"] = n2
                if n3:
                    self.config["last_name3"] = n3
                self.config["last_has_third"] = has_3rd

                # 需求 1: 記憶行車紀錄器與密錄器之鎖定/解鎖狀態
                if hasattr(active_tab, 'is_dashcam_locked'):
                    self.config["is_dashcam_locked"] = active_tab.is_dashcam_locked
                if hasattr(active_tab, 'is_bodycam_locked'):
                    self.config["is_bodycam_locked"] = active_tab.is_bodycam_locked

            # 需求 2: 儲存快取同仁名冊（供啟動時先依據前一個任務的名冊紀錄）
            if hasattr(self, 'officer_names') and self.officer_names:
                self.config["cached_officer_names"] = list(self.officer_names)

            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def create_widgets(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=6, pady=6)

        # 1. 「➕」新增分頁標籤（固定置於最左側，點擊時立即新增任務分頁）
        self.dummy_plus_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.dummy_plus_tab, text=" ➕ ")

        # 2. 初始第 1 個勤務作業分頁
        first_tab = self.add_new_task_tab()

        # 3. 快速設定分頁（固定置於最右側）
        self.tab_settings = ttk.Frame(self.notebook)
        self.setup_settings_tab(self.tab_settings)
        self.notebook.add(self.tab_settings, text=" ⚙️ 快速設定 ")

        # 預設選取第 1 個勤務作業分頁（避免一開程式選在最左側的 ➕）
        self.notebook.select(first_tab)

        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)
        self.notebook.bind("<Button-1>", self.on_notebook_click)
        self.update_tab_labels()

    def update_tab_labels(self):
        """更新分頁標籤顯示：當前選取的分頁標題左側顯示『✕』，其餘顯示『📁』"""
        try:
            sel_widget = self.notebook.select()
        except Exception:
            sel_widget = None

        for tab in self.task_tabs:
            cno = tab.entry_case_no.get().strip()
            name = cno if cno else f"任務 #{tab.tab_id}"
            if str(tab) == str(sel_widget):
                self.notebook.tab(tab, text=f" ✕ {name} ")
            else:
                self.notebook.tab(tab, text=f" 📁 {name} ")

    def get_tab_left_edge(self, click_x, click_y, tab_idx):
        """二分搜尋快速取得該分頁標籤在螢幕上的左側邊界 X 座標"""
        low, high = 0, click_x
        left = click_x
        while low <= high:
            mid = (low + high) // 2
            try:
                if self.notebook.index(f"@{mid},{click_y}") == tab_idx:
                    left = mid
                    high = mid - 1
                else:
                    low = mid + 1
            except Exception:
                low = mid + 1
        return left

    def on_notebook_click(self, event):
        """點擊標籤左側『✕』時關閉該分頁"""
        try:
            clicked_idx = self.notebook.index(f"@{event.x},{event.y}")
            clicked_tab_widget = self.notebook.tabs()[clicked_idx]
        except Exception:
            return

        try:
            sel_widget = self.notebook.select()
        except Exception:
            return

        # 若點擊最左側「➕」標籤，直接建立新任務分頁並選取
        if hasattr(self, 'dummy_plus_tab') and str(clicked_tab_widget) == str(self.dummy_plus_tab):
            new_tab = self.add_new_task_tab()
            self.notebook.select(new_tab)
            return "break"

        # 若點擊的是當前已選取中的任務分頁
        if str(clicked_tab_widget) == str(sel_widget):
            matched_tab = None
            for t in self.task_tabs:
                if str(t) == str(clicked_tab_widget):
                    matched_tab = t
                    break
            if matched_tab:
                left = self.get_tab_left_edge(event.x, event.y, clicked_idx)
                # 檢查是否點擊在標籤左側的「✕」區域（左邊界起算 38 像素內）
                if event.x - left <= 38:
                    cno = matched_tab.entry_case_no.get().strip()
                    title = cno if cno else f"任務 #{matched_tab.tab_id}"

                    if matched_tab.is_copying:
                        if not messagebox.askyesno("任務正在進行", f"【{title}】目前正在複製檔案中！\n確定要強制關閉此分頁並放棄任務嗎？"):
                            return "break"
                        matched_tab.stop_requested.set()
                        matched_tab.cleanup_active_folders()

                    self.remove_task_tab(matched_tab)
                    return "break"

    def on_tab_changed(self, event):
        """當切換分頁時，若切換到「➕」，自動建立新任務並選取"""
        try:
            selected_widget = self.notebook.select()
            selected_index = self.notebook.index(selected_widget)
        except Exception:
            return

        if hasattr(self, 'dummy_plus_tab'):
            plus_index = self.notebook.index(self.dummy_plus_tab)
            if selected_index == plus_index:
                new_tab = self.add_new_task_tab()
                self.notebook.select(new_tab)
                return

        self.update_tab_labels()

        # 當切換到任務分頁時，自動執行連線刷新
        try:
            for tab in self.task_tabs:
                if str(tab) == str(selected_widget):
                    tab.refresh_device_connections(show_toast=False)
                    break
        except Exception:
            pass

    def add_new_task_tab(self):
        """動態新增一個歸檔任務分頁"""
        new_tab = ArchiveTaskTab(self.notebook, self, tab_id=self.next_tab_id)
        self.task_tabs.append(new_tab)

        # 保持「➕」固定在最左邊 (Index 0)，設定分頁在最右邊，新任務分頁插入在設定分頁之前
        if hasattr(self, 'tab_settings') and str(self.tab_settings) in self.notebook.tabs():
            settings_idx = self.notebook.index(self.tab_settings)
            self.notebook.insert(settings_idx, new_tab, text=f" 📁 任務 #{self.next_tab_id} ")
        else:
            self.notebook.add(new_tab, text=f" 📁 任務 #{self.next_tab_id} ")

        self.next_tab_id += 1
        self.update_tab_labels()
        return new_tab

    def remove_task_tab(self, tab):
        """移除指定的任務分頁"""
        if tab in self.task_tabs:
            idx = self.task_tabs.index(tab)
            self.task_tabs.remove(tab)
            self.notebook.forget(tab)
            tab.destroy()

            # 若所有任務分頁都被關閉，自動開一個新的第 1 個分頁
            if not self.task_tabs:
                new_tab = self.add_new_task_tab()
                self.notebook.select(new_tab)
            else:
                next_idx = min(idx, len(self.task_tabs) - 1)
                self.notebook.select(self.task_tabs[next_idx])

            self.update_tab_labels()

    def setup_settings_tab(self, outer_parent):
        # 建立防裁切平滑捲動 Canvas 容器，適應各尺寸筆電與 DPI 縮放
        self.settings_canvas = tk.Canvas(outer_parent, borderwidth=0, highlightthickness=0, bg=self.bg_color)
        self.settings_scrollbar = ttk.Scrollbar(outer_parent, orient="vertical", command=self.settings_canvas.yview)
        parent = ttk.Frame(self.settings_canvas)

        parent.bind(
            "<Configure>",
            lambda e: self.settings_canvas.configure(scrollregion=self.settings_canvas.bbox("all"))
        )
        self.settings_canvas_win = self.settings_canvas.create_window((0, 0), window=parent, anchor="nw")
        self.settings_canvas.configure(yscrollcommand=self.settings_scrollbar.set)
        self.settings_canvas.bind('<Configure>', lambda e: self.settings_canvas.itemconfig(self.settings_canvas_win, width=e.width))

        self.settings_canvas.pack(side="left", fill="both", expand=True)
        self.settings_scrollbar.pack(side="right", fill="y")

        pad = {'padx': 12, 'pady': 6}

        # 需求：快速設定新增「車輛系統登入」按鈕與背景心跳保活
        frame_car = ttk.LabelFrame(parent, text="【車輛系統登入 ＆ 背景心跳保活】(自動開啟分隊 Chrome 設定檔並維持連線)", padding=10)
        frame_car.pack(fill="x", **pad)
        self.frame_car = frame_car

        f_car_top = ttk.Frame(frame_car)
        f_car_top.pack(fill="x", pady=(2, 6))

        target_lic_cfg = self.config.get('car_target_license', DEFAULT_CAR_TARGET_LICENSE)
        self.btn_car_main_action = ttk.Button(
            f_car_top,
            text=f"🚑 一鍵全自動：車輛登入 ＆ 里程登記 ({target_lic_cfg})",
            style="Action.TButton",
            command=self.on_click_car_all_in_one
        )
        self.btn_car_main_action.pack(side="left", ipadx=8, ipady=5, padx=(0, 6))
        self.btn_car_login = self.btn_car_main_action
        self.btn_car_mileage = self.btn_car_main_action

        ttk.Button(
            f_car_top,
            text="👁️ 喚醒視窗 / 重試",
            command=self.on_click_wake_car_browser
        ).pack(side="left", ipady=3, padx=2)

        ttk.Button(
            f_car_top,
            text="💓 立即發送心跳",
            command=lambda: self.send_car_heartbeat(manual=True)
        ).pack(side="left", ipady=3, padx=2)

        ttk.Button(
            f_car_top,
            text="🚀 一般 Chrome 開啟",
            command=self.open_car_in_normal_chrome
        ).pack(side="left", ipady=3, padx=2)

        self.lbl_car_status = ttk.Label(
            frame_car,
            text="🔴 狀態：尚未啟動（點擊上方「🚗 車輛系統登入」開啟分隊 Chrome 設定檔並啟動背景心跳）",
            foreground="#C62828",
            font=('Helvetica', 9, 'bold')
        )
        self.lbl_car_status.pack(anchor="w", pady=(0, 6))

        f_car_prof = ttk.Frame(frame_car)
        f_car_prof.pack(fill="x", pady=2)
        ttk.Label(f_car_prof, text="Chrome 設定檔：", width=16).pack(side="left")

        prof_list = get_chrome_profiles_list()
        prof_displays = [disp for disp, _ in prof_list]
        saved_prof_cfg = self.config.get("car_chrome_profile", DEFAULT_CAR_CHROME_EMAIL)
        resolved_prof = resolve_chrome_profile(saved_prof_cfg)
        initial_prof_disp = self.find_car_profile_display(resolved_prof)

        self.cb_car_chrome_profile = ttk.Combobox(f_car_prof, values=prof_displays, width=38)
        self.cb_car_chrome_profile.set(initial_prof_disp)
        self.cb_car_chrome_profile.pack(side="left", padx=4)
        self.cb_car_chrome_profile.bind("<<ComboboxSelected>>", lambda e: self.refresh_car_accounts_list())
        ttk.Button(f_car_prof, text="🔄 重新掃描帳號", command=self.refresh_car_chrome_profiles).pack(side="left", padx=2)

        f_car_acct = ttk.Frame(frame_car)
        f_car_acct.pack(fill="x", pady=2)
        ttk.Label(f_car_acct, text="預設帶入帳號：", width=16).pack(side="left")
        self.cb_car_account = ttk.Combobox(f_car_acct, values=[], width=38)
        self.cb_car_account.pack(side="left", padx=4)
        self.cb_car_account.bind("<<ComboboxSelected>>", self.on_car_account_selected)
        ttk.Button(
            f_car_acct,
            text="⚡ 切換並帶入此帳號",
            command=self.on_switch_car_account_clicked
        ).pack(side="left", padx=2)
        self.lbl_car_acct_count = ttk.Label(f_car_acct, text="", foreground="#1565C0", font=('Helvetica', 9, 'bold'))
        self.lbl_car_acct_count.pack(side="left", padx=4)
        self.refresh_car_accounts_list()

        f_car_url = ttk.Frame(frame_car)
        f_car_url.pack(fill="x", pady=2)
        ttk.Label(f_car_url, text="車輛系統網址：", width=16).pack(side="left")
        self.cb_car_system_url = ttk.Combobox(f_car_url, values=CAR_SYSTEM_URL_PRESETS, width=38)
        self.cb_car_system_url.set(self.config.get("car_system_url", DEFAULT_CAR_SYSTEM_URL))
        self.cb_car_system_url.pack(side="left", padx=4)
        ttk.Label(f_car_url, text="(可下拉選取或自行修改)", foreground="#666666").pack(side="left", padx=2)

        f_car_hb = ttk.Frame(frame_car)
        f_car_hb.pack(fill="x", pady=(4, 0))
        self.var_car_heartbeat = tk.BooleanVar(value=self.config.get("car_heartbeat_enabled", True))
        ttk.Checkbutton(
            f_car_hb,
            text="💓 登入後自動進行背景心跳運作（每 3 分鐘靜默發送保活封包，防止公務電腦閒置登出、不搶焦點）",
            variable=self.var_car_heartbeat,
            command=self.on_toggle_car_heartbeat
        ).pack(anchor="w")

        f_car_lic = ttk.Frame(frame_car)
        f_car_lic.pack(fill="x", pady=2)
        ttk.Label(f_car_lic, text="里程登記車牌：", width=16).pack(side="left")
        self.cb_car_target_license = ttk.Combobox(f_car_lic, values=DEFAULT_CAR_LICENSE_PRESETS, width=38)
        self.cb_car_target_license.set(self.config.get("car_target_license", DEFAULT_CAR_TARGET_LICENSE))
        self.cb_car_target_license.pack(side="left", padx=4)
        self.cb_car_target_license.bind("<<ComboboxSelected>>", self.on_car_target_license_changed)
        ttk.Label(f_car_lic, text="(預設十九甲主力救護車 BVX-0039)", foreground="#666666").pack(side="left", padx=2)

        f_car_auto = ttk.Frame(frame_car)
        f_car_auto.pack(fill="x", pady=(4, 0))
        self.var_car_auto_submit_login = tk.BooleanVar(value=self.config.get("car_auto_submit_login", True))
        ttk.Checkbutton(
            f_car_auto,
            text="⚡ 帳密填入後自動點擊「登入」按鈕（全自動化登入免手按）",
            variable=self.var_car_auto_submit_login,
            command=self.save_config
        ).pack(anchor="w")

        self.var_car_auto_mileage = tk.BooleanVar(value=self.config.get("car_auto_mileage", True))
        ttk.Checkbutton(
            f_car_auto,
            text="🚑 登入後自動導向【里程登記】並新增選取救護車 (BVX-0039) 與今天日期",
            variable=self.var_car_auto_mileage,
            command=self.save_config
        ).pack(anchor="w")

        frame_roster = ttk.LabelFrame(parent, text="【當月勤務表設定】(依出勤時段快速自動帶入同仁姓名)", padding=10)
        frame_roster.pack(fill="x", **pad)
        self.frame_roster = frame_roster

        f_r_line = ttk.Frame(frame_roster)
        f_r_line.pack(fill="x", pady=3)
        ttk.Label(f_r_line, text="勤務表路徑 (檔案或資料夾)：", width=22).pack(side="left")
        self.entry_roster_path = ttk.Entry(f_r_line, width=30)
        self.entry_roster_path.insert(0, self.config.get("roster_path", ""))
        self.entry_roster_path.pack(side="left", padx=4)
        ttk.Button(f_r_line, text="📄 選擇檔案", command=self.browse_roster_file).pack(side="left", padx=2)
        ttk.Button(f_r_line, text="📁 選擇資料夾", command=self.browse_roster_dir).pack(side="left", padx=2)
        ttk.Button(f_r_line, text="🔍 測試解析最新勤務表", command=self.test_roster_parse).pack(side="left", padx=2)

        ttk.Label(
            frame_roster,
            text="💡 支援直接選取單一勤務表「檔案」（如：分隊-1020.xlsm）或存放當月排班的「資料夾」。\n選取後系統立即自動載入名冊並更新下拉選單！若出勤月份不同，排班時將主動提示切換確認。",
            foreground="#555555"
        ).pack(anchor="w", pady=(3, 0))

        # 需求：智慧設備指紋特徵與搜尋寬限期自訂 (v4.4 防呆免對齊，可自由增修)
        frame_fingerprint = ttk.LabelFrame(parent, text="【智慧設備特徵指紋 ＆ 搜尋演算法自訂】(v4.4 防呆免對齊，可自由增修)", padding=10)
        frame_fingerprint.pack(fill="x", **pad)

        ttk.Label(
            frame_fingerprint,
            text="💡 系統依據隨身碟目錄特徵、檔名與磁碟名稱智慧辨識。若分隊採購新型號設備，可於下方增加關鍵字（以逗號或空格分隔）：",
            foreground="#555555"
        ).pack(anchor="w", pady=(0, 4))

        f_fp1 = ttk.Frame(frame_fingerprint)
        f_fp1.pack(fill="x", pady=2)
        ttk.Label(f_fp1, text="行車紀錄器目錄/特徵：", width=20).pack(side="left")
        self.entry_dash_keywords = ttk.Entry(f_fp1, width=42)
        self.entry_dash_keywords.insert(0, self.config.get("dashcam_keywords", DEFAULT_DASHCAM_KEYWORDS))
        self.entry_dash_keywords.pack(side="left", padx=4)
        ttk.Label(f_fp1, text="例: CAREC, CARDV, NORMAL, EVENT", foreground="#666666", font=('Helvetica', 9)).pack(side="left", padx=2)

        f_fp2 = ttk.Frame(frame_fingerprint)
        f_fp2.pack(fill="x", pady=2)
        ttk.Label(f_fp2, text="行車紀錄器檔名特徵：", width=20).pack(side="left")
        self.entry_dash_fn_patterns = ttk.Entry(f_fp2, width=42)
        self.entry_dash_fn_patterns.insert(0, self.config.get("dashcam_fn_patterns", DEFAULT_DASHCAM_FN_PATTERNS))
        self.entry_dash_fn_patterns.pack(side="left", padx=4)
        ttk.Label(f_fp2, text="例: _F., _R., NORMAL_, EVENT_", foreground="#666666", font=('Helvetica', 9)).pack(side="left", padx=2)

        f_fp3 = ttk.Frame(frame_fingerprint)
        f_fp3.pack(fill="x", pady=2)
        ttk.Label(f_fp3, text="密錄器目錄/特徵：", width=20).pack(side="left")
        self.entry_body_keywords = ttk.Entry(f_fp3, width=42)
        self.entry_body_keywords.insert(0, self.config.get("bodycam_keywords", DEFAULT_BODYCAM_KEYWORDS))
        self.entry_body_keywords.pack(side="left", padx=4)
        ttk.Label(f_fp3, text="例: DPB30, VIDEO, DCIM (嚴格依此比對)", foreground="#666666", font=('Helvetica', 9)).pack(side="left", padx=2)

        f_fp4 = ttk.Frame(frame_fingerprint)
        f_fp4.pack(fill="x", pady=2)
        ttk.Label(f_fp4, text="時間搜尋前後寬限(分)：", width=20).pack(side="left")
        self.entry_time_buffer_minutes = ttk.Entry(f_fp4, width=8)
        self.entry_time_buffer_minutes.insert(0, str(self.config.get("time_buffer_minutes", DEFAULT_TIME_BUFFER_MINUTES)))
        self.entry_time_buffer_minutes.pack(side="left", padx=4)
        ttk.Label(f_fp4, text="預設 15 分鐘 (擴展出勤搜尋區間，避免設備時鐘誤差)", foreground="#666666", font=('Helvetica', 9)).pack(side="left", padx=4)

        ttk.Button(
            f_fp4,
            text="🔄 恢復官方預設特徵規則",
            command=self.reset_fingerprint_defaults
        ).pack(side="right", padx=6)

        # 需求：軟體自動更新來源資料夾設定
        frame_update = ttk.LabelFrame(parent, text="【軟體自動更新設定】(放置新版檔案即可全自動偵測升級)", padding=10)
        frame_update.pack(fill="x", **pad)

        f_u_line = ttk.Frame(frame_update)
        f_u_line.pack(fill="x", pady=3)
        ttk.Label(f_u_line, text="更新來源資料夾：", width=18).pack(side="left")
        self.entry_update_dir = ttk.Entry(f_u_line, width=28)
        self.entry_update_dir.insert(0, self.config.get("update_dir", ""))
        self.entry_update_dir.pack(side="left", padx=4)
        ttk.Button(f_u_line, text="📁 選擇資料夾", command=self.browse_update_dir).pack(side="left", padx=2)
        ttk.Button(f_u_line, text="🔄 立即檢查更新", command=lambda: self.check_and_apply_update(manual=True)).pack(side="left", padx=2)

        ttk.Label(
            frame_update,
            text="💡 提示：可指定為隨身碟、分隊 NAS 共用槽或個人「下載」資料夾。\n"
                 "只要將最新檔案（video_backup.pyw、video_backup (1).pyw、或小幫手專用包 .zip）放入該目錄，程式每次啟動時會自動比對並提示升級！",
            foreground="#555555"
        ).pack(anchor="w", pady=(2, 0))

        # 需求 1: 「💾 儲存所有設定」獨立移至所有設定下方，明確涵蓋當月勤務表路徑與設備標籤名稱
        # 需求：未錄影空資料夾備註原因自訂清單
        frame_reasons = ttk.LabelFrame(parent, text="【未錄影空資料夾備註原因設定】(每行一筆，供預檢視窗 0 部影片時秒選)", padding=10)
        frame_reasons.pack(fill="x", **pad)

        ttk.Label(frame_reasons, text="請在下方文字框自由增減常見原因（每行代表一個選項）：", foreground="#555555").pack(anchor="w", pady=(0, 4))
        self.txt_missing_reasons = tk.Text(frame_reasons, height=4, font=("Helvetica", 10), bg="#FFFFFF", fg="#222222", relief="solid", bd=1)
        self.txt_missing_reasons.pack(fill="x", pady=2)
        reasons_list = self.config.get("missing_reasons", DEFAULT_MISSING_REASONS)
        self.txt_missing_reasons.insert("1.0", "\n".join(reasons_list))

        # Windows 專屬設定：最小化至右下角系統匣
        if sys.platform == "win32":
            frame_win = ttk.LabelFrame(parent, text="【Windows 系統設定】", padding=10)
            frame_win.pack(fill="x", **pad)
            self.var_min_to_tray = tk.BooleanVar(value=self.config.get("minimize_to_tray", True))
            chk_tray = ttk.Checkbutton(
                frame_win,
                text="按視窗縮小（最小化）時，自動縮小至右下角通知區域 (系統匣)，避免佔用工作列",
                variable=self.var_min_to_tray
            )
            chk_tray.pack(anchor="w", pady=2)
            ttk.Label(
                frame_win,
                text="💡 縮小至右下角後，點擊右下角圖示即可隨時還原視窗；亦可對圖示按右鍵選擇功能。",
                foreground="#555555"
            ).pack(anchor="w", pady=(2, 0))

        frame_save_bar = ttk.Frame(parent)
        frame_save_bar.pack(fill="x", padx=12, pady=(6, 8))

        self.btn_save_cfg = ttk.Button(
            frame_save_bar,
            text="💾 儲存所有設定 (含車輛系統、更新資料夾、勤務表路徑、特徵指紋與原因)",
            command=self.save_settings_clicked
        )
        self.btn_save_cfg.pack(side="left", ipady=3)

        self.lbl_save_status = ttk.Label(
            frame_save_bar,
            text="",
            foreground="#2E7D32",
            font=('Helvetica', 9)
        )
        self.lbl_save_status.pack(side="left", padx=10)

        def _on_settings_mousewheel(event):
            if sys.platform == "darwin":
                self.settings_canvas.yview_scroll(int(-1 * event.delta), "units")
            elif event.num == 4:
                self.settings_canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                self.settings_canvas.yview_scroll(1, "units")
            else:
                self.settings_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _bind_settings_wheel(w):
            w.bind("<MouseWheel>", _on_settings_mousewheel, add="+")
            w.bind("<Button-4>", _on_settings_mousewheel, add="+")
            w.bind("<Button-5>", _on_settings_mousewheel, add="+")
            for c in w.winfo_children():
                _bind_settings_wheel(c)

        _bind_settings_wheel(parent)

    def reset_fingerprint_defaults(self):
        """將智慧設備特徵指紋與搜尋寬限期還原為官方預設值"""
        if hasattr(self, 'entry_dash_keywords'):
            self.entry_dash_keywords.delete(0, tk.END)
            self.entry_dash_keywords.insert(0, DEFAULT_DASHCAM_KEYWORDS)
        if hasattr(self, 'entry_dash_fn_patterns'):
            self.entry_dash_fn_patterns.delete(0, tk.END)
            self.entry_dash_fn_patterns.insert(0, DEFAULT_DASHCAM_FN_PATTERNS)
        if hasattr(self, 'entry_body_keywords'):
            self.entry_body_keywords.delete(0, tk.END)
            self.entry_body_keywords.insert(0, DEFAULT_BODYCAM_KEYWORDS)
        if hasattr(self, 'entry_time_buffer_minutes'):
            self.entry_time_buffer_minutes.delete(0, tk.END)
            self.entry_time_buffer_minutes.insert(0, str(DEFAULT_TIME_BUFFER_MINUTES))
        messagebox.showinfo("還原預設值", "已成功將設備特徵指紋與時間寬限期還原為官方預設規則！\n請記得點擊「💾 儲存所有設定」使其生效。")

    def find_car_profile_display(self, profile_key: str) -> str:
        """根據 Chrome 設定檔目錄名反查下拉選單顯示字串"""
        profiles = get_chrome_profiles_list()
        for disp, pdir in profiles:
            if pdir == profile_key:
                return disp
        auto_prof = find_chrome_profile_for_user()
        for disp, pdir in profiles:
            if pdir == auto_prof:
                return disp
        return profiles[0][0] if profiles else "Default [Default]"

    def get_selected_car_chrome_profile(self) -> str:
        if hasattr(self, 'cb_car_chrome_profile'):
            return resolve_chrome_profile(self.cb_car_chrome_profile.get().strip())
        return resolve_chrome_profile(self.config.get("car_chrome_profile", DEFAULT_CAR_CHROME_EMAIL))

    def get_selected_car_url(self) -> str:
        if hasattr(self, 'cb_car_system_url'):
            u = self.cb_car_system_url.get().strip()
            if u:
                return u
        return self.config.get("car_system_url", DEFAULT_CAR_SYSTEM_URL) or DEFAULT_CAR_SYSTEM_URL

    def get_selected_car_account(self) -> str:
        if hasattr(self, 'cb_car_account'):
            raw = self.cb_car_account.get().strip()
            if not raw or raw.startswith("⚡") or raw.upper() == "AUTO":
                return ""
            return raw.split()[0].strip()
        saved = (self.config.get("car_preferred_account") or "").strip()
        if not saved or saved.startswith("⚡") or saved.upper() == "AUTO":
            return ""
        return saved

    def refresh_car_accounts_list(self):
        """從選定的 Chrome 設定檔掃描所有已儲存的車輛系統帳號，更新下拉選單"""
        prof = self.get_selected_car_chrome_profile()
        accts = self.car_cdp.get_saved_car_accounts(prof)
        auto_label = f"⚡ 自動帶入最近使用帳號 ({accts[0]})" if accts else "⚡ 自動帶入最近使用帳號"
        values = [auto_label] + accts
        if hasattr(self, 'cb_car_account'):
            self.cb_car_account['values'] = values
            saved_pref = (self.config.get("car_preferred_account") or "").strip()
            if saved_pref and saved_pref != "AUTO" and saved_pref in accts:
                self.cb_car_account.set(saved_pref)
            else:
                self.cb_car_account.set(auto_label)
        if hasattr(self, 'lbl_car_acct_count'):
            if accts:
                self.lbl_car_acct_count.config(
                    text=f"🔑 已同步 {len(accts)} 組帳密",
                    foreground="#1565C0"
                )
            else:
                self.lbl_car_acct_count.config(
                    text="(尚無已存帳密)",
                    foreground="#757575"
                )

    def on_car_account_selected(self, event=None):
        self.save_config()

    def get_car_target_license(self) -> str:
        if hasattr(self, 'cb_car_target_license'):
            val = self.cb_car_target_license.get().strip()
            if val:
                return val
        return (self.config.get("car_target_license") or DEFAULT_CAR_TARGET_LICENSE).strip()

    def on_car_target_license_changed(self, event=None):
        lic = self.get_car_target_license()
        self.config["car_target_license"] = lic
        if hasattr(self, 'btn_car_mileage'):
            self.btn_car_mileage.config(text=f"🚑 一鍵里程登記 ({lic})")
        self.save_config()

    def on_switch_car_account_clicked(self):
        """點擊「⚡ 切換並帶入此帳號」：立即切換優先帶入之同仁帳號並開啟/重新載入車輛系統登入頁"""
        self.save_config()
        prof = self.get_selected_car_chrome_profile()
        target_url = self.get_selected_car_url()
        pref_acct = self.get_selected_car_account()
        disp_name = self.find_car_profile_display(prof)
        acct_disp = pref_acct if pref_acct else "最近使用帳號"

        if hasattr(self, 'lbl_car_status'):
            self.lbl_car_status.config(
                text=f"⏳ 正在切換並帶入帳號 [{acct_disp}] 之密碼至車輛系統，請稍候...",
                foreground="#E65100"
            )
            self.root.update_idletasks()

        def _switch_worker():
            force_restart = bool(
                self.car_cdp.is_running()
                and pref_acct
                and pref_acct not in self.car_cdp._decrypted_car_creds
            )
            auto_sub = self.var_car_auto_submit_login.get() if hasattr(self, 'var_car_auto_submit_login') else True
            auto_mil = self.var_car_auto_mileage.get() if hasattr(self, 'var_car_auto_mileage') else True
            target_lic = self.get_car_target_license()
            ok = self.car_cdp.launch_or_activate(
                target_url,
                profile_name=prof,
                preferred_account=pref_acct,
                force_restart=force_restart,
                target_license=target_lic,
                auto_submit=auto_sub,
                auto_mileage=auto_mil,
            )
            self.root.after(0, lambda: self._after_car_browser_launched(ok, disp_name, target_url, pref_acct))

        threading.Thread(target=_switch_worker, daemon=True).start()

    def _apply_switched_car_account(self, acct: str):
        if not acct:
            return
        self.config["car_preferred_account"] = acct
        if hasattr(self, 'cb_car_account'):
            self.cb_car_account.set(acct)
        self.save_config()

    def refresh_car_chrome_profiles(self):
        profiles = get_chrome_profiles_list()
        prof_displays = [disp for disp, _ in profiles]
        if hasattr(self, 'cb_car_chrome_profile'):
            self.cb_car_chrome_profile['values'] = prof_displays
            cur_prof = self.get_selected_car_chrome_profile()
            self.cb_car_chrome_profile.set(self.find_car_profile_display(cur_prof))
        self.refresh_car_accounts_list()

    def on_click_car_all_in_one(self):
        """【一鍵全自動整合入口】：開瀏覽器 ➔ 自動登入 ➔ 自動切換里程登記 ➔ 自動添加記錄 ➔ 自動選 BVX-0039 救護車 ➔ 自動選今日日期"""
        self.save_config()
        self.car_mileage_flow_active = True
        self.car_mileage_flow_completed = False
        prof = self.get_selected_car_chrome_profile()
        target_url = self.get_selected_car_url()
        pref_acct = self.get_selected_car_account()
        target_lic = self.get_car_target_license()
        disp_name = self.find_car_profile_display(prof)

        if hasattr(self, 'lbl_car_status'):
            self.lbl_car_status.config(
                text=f"🚑 正在為您一鍵全自動執行【車輛系統登入 ＆ 里程登記 ({target_lic})】，請稍候...",
                foreground="#0D47A1"
            )
            self.root.update_idletasks()

        self._car_all_in_one_seq = getattr(self, '_car_all_in_one_seq', 0) + 1
        my_seq = self._car_all_in_one_seq

        def _all_in_one_worker():
            force_restart = bool(
                self.car_cdp.is_running()
                and pref_acct
                and pref_acct not in self.car_cdp._decrypted_car_creds
            )
            ok = self.car_cdp.launch_or_activate(
                target_url,
                profile_name=prof,
                preferred_account=pref_acct,
                force_restart=force_restart,
                target_license=target_lic,
                auto_submit=True,
                auto_mileage=True,
            )
            if ok:
                self.car_cdp.activate_window(target_url)
                # 有序狀態機推進：先確認登入完成，絕不於登入頁強行跳轉導致閃爍
                for _ in range(40):
                    if getattr(self, '_car_all_in_one_seq', 0) != my_seq:
                        break
                    time.sleep(0.8)
                    has_tab, is_logged_in, is_login_page, _, _ = self.car_cdp.check_car_login_state(target_url)
                    if not has_tab:
                        continue
                    if is_login_page:
                        # 仍在登入頁：呼叫登入輔助（內含 4 秒冷卻鎖，帳密就緒才單次點擊，不連點狂閃）
                        self.car_cdp.assist_car_login_page(
                            target_url=target_url,
                            preferred_account=pref_acct,
                            auto_submit=True,
                        )
                        continue
                    if is_logged_in:
                        # 確認已登入成功，才進入里程登記自動化
                        m_res = self.car_cdp.assist_car_mileage_flow(target_url=target_url, target_license=target_lic)
                        if isinstance(m_res, str) and m_res.startswith("SUCCESS_FILLED"):
                            self.car_mileage_flow_completed = True
                            self.car_mileage_flow_active = False
                            parts = m_res.split(":")
                            l_val = parts[1] if len(parts) > 1 else target_lic
                            d_val = parts[2] if len(parts) > 2 else ""
                            self.root.after(0, lambda l=l_val, d=d_val: self._on_mileage_flow_success_ui(l, d))
                            break
            else:
                ext_dir = self.car_cdp.ensure_car_helper_extension(
                    target_url=target_url,
                    target_license=target_lic,
                    preferred_account=pref_acct,
                    auto_submit=True,
                    auto_mileage=True,
                )
                open_chrome_url(target_url, prof, ext_dir=ext_dir)

        threading.Thread(target=_all_in_one_worker, daemon=True).start()

    def on_click_wake_car_browser(self):
        """喚醒現有車輛系統視窗並重新執行一次里程填選"""
        target_url = self.get_selected_car_url()
        target_lic = self.get_car_target_license()
        self.car_mileage_flow_active = True
        self.car_mileage_flow_completed = False
        self.car_cdp.activate_window(target_url)
        threading.Thread(target=lambda: self.car_cdp.assist_car_mileage_flow(target_url=target_url, target_license=target_lic), daemon=True).start()

    def on_click_car_mileage_shortcut(self):
        """點擊「🚑 一鍵里程登記 (BVX-0039)」：全自動啟動受控 Chrome、自動登入、自動切換至里程登記、自動點擊添加新記錄並預選車牌與今日日期"""
        self.save_config()
        self.car_mileage_flow_active = True
        self.car_mileage_flow_completed = False
        prof = self.get_selected_car_chrome_profile()
        target_url = self.get_selected_car_url()
        pref_acct = self.get_selected_car_account()
        target_lic = self.get_car_target_license()
        disp_name = self.find_car_profile_display(prof)

        if hasattr(self, 'lbl_car_status'):
            self.lbl_car_status.config(
                text=f"🚑 正在為您啟動【車輛里程登記 (車號 {target_lic})】全自動流程，請稍候...",
                foreground="#0D47A1"
            )
            self.root.update_idletasks()

        def _worker():
            force_restart = bool(
                self.car_cdp.is_running()
                and pref_acct
                and pref_acct not in self.car_cdp._decrypted_car_creds
            )
            ok = self.car_cdp.launch_or_activate(
                target_url,
                profile_name=prof,
                preferred_account=pref_acct,
                force_restart=force_restart,
                target_license=target_lic,
                auto_submit=True,
                auto_mileage=True,
            )
            if ok:
                self.car_cdp.activate_window(target_url)
                time.sleep(0.6)
                has_tab, is_logged_in, is_login_page, _, _ = self.car_cdp.check_car_login_state(target_url)
                if has_tab and is_login_page:
                    self.car_cdp.assist_car_login_page(
                        target_url=target_url,
                        preferred_account=pref_acct,
                        auto_submit=True,
                    )
                elif is_logged_in and not is_login_page:
                    m_res = self.car_cdp.assist_car_mileage_flow(target_url=target_url, target_license=target_lic)
                    if isinstance(m_res, str) and m_res.startswith("SUCCESS_FILLED"):
                        self.car_mileage_flow_completed = True
                        self.car_mileage_flow_active = False
                        parts = m_res.split(":")
                        l_val = parts[1] if len(parts) > 1 else target_lic
                        d_val = parts[2] if len(parts) > 2 else ""
                        self.root.after(0, lambda: self._on_mileage_flow_success_ui(l_val, d_val))
            else:
                ext_dir = self.car_cdp.ensure_car_helper_extension(
                    target_url=target_url,
                    target_license=target_lic,
                    preferred_account=pref_acct,
                    auto_submit=True,
                    auto_mileage=True,
                )
                open_chrome_url(target_url, prof, ext_dir=ext_dir)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_mileage_flow_success_ui(self, lic_str: str, date_str: str):
        if hasattr(self, 'lbl_car_status'):
            self.lbl_car_status.config(
                text=f"🟢 里程登記就緒 ｜ 🚑 已自動添加【{lic_str}】記錄（日期已選今日 {date_str}，請填寫出勤里程後按儲存）",
                foreground="#2E7D32"
            )

    def on_click_car_system_login(self):
        """點擊「🚗 車輛系統登入」：啟動或喚醒綁定分隊 Chrome 設定檔的受控 Chrome 並開啟車輛管理系統"""
        self.save_config()
        if hasattr(self, 'var_car_auto_mileage') and self.var_car_auto_mileage.get():
            self.car_mileage_flow_active = True
            self.car_mileage_flow_completed = False
        else:
            self.car_mileage_flow_active = False
        prof = self.get_selected_car_chrome_profile()
        target_url = self.get_selected_car_url()
        pref_acct = self.get_selected_car_account()
        disp_name = self.find_car_profile_display(prof)

        if (
            self.car_cdp.is_running()
            and self.car_cdp.get_active_profile_mode() == prof
            and (not pref_acct or pref_acct == self.car_cdp.active_preferred_account or pref_acct in self.car_cdp._decrypted_car_creds)
        ):
            self.car_cdp.ensure_car_tab(target_url)
            if pref_acct and pref_acct in self.car_cdp._decrypted_car_creds:
                auto_sub = self.var_car_auto_submit_login.get() if hasattr(self, 'var_car_auto_submit_login') else True
                self.car_cdp.assist_car_login_page(
                    target_url=target_url,
                    preferred_account=pref_acct,
                    force_fill_account=pref_acct,
                    auto_submit=auto_sub,
                )
            self.car_cdp.activate_window(target_url)
            has_tab, is_logged_in, _, _, _ = self.car_cdp.check_car_login_state(target_url)
            if is_logged_in and self.var_car_heartbeat.get():
                self.send_car_heartbeat(manual=False)
            return

        if hasattr(self, 'lbl_car_status'):
            self.lbl_car_status.config(
                text=f"⏳ 正在開啟 Chrome 設定檔 [{disp_name}] 並同步車輛系統帳密，請稍候...",
                foreground="#E65100"
            )
            self.root.update_idletasks()

        def _launch_worker():
            auto_sub = self.var_car_auto_submit_login.get() if hasattr(self, 'var_car_auto_submit_login') else True
            auto_mil = self.var_car_auto_mileage.get() if hasattr(self, 'var_car_auto_mileage') else True
            target_lic = self.get_car_target_license()
            ok = self.car_cdp.launch_or_activate(
                target_url,
                profile_name=prof,
                preferred_account=pref_acct,
                target_license=target_lic,
                auto_submit=auto_sub,
                auto_mileage=auto_mil,
            )
            if ok:
                time.sleep(0.6)
                has_tab, is_logged_in, is_login_page, _, _ = self.car_cdp.check_car_login_state(target_url)
                if has_tab and is_login_page:
                    self.car_cdp.assist_car_login_page(
                        target_url=target_url,
                        preferred_account=pref_acct,
                        auto_submit=auto_sub,
                    )
            else:
                ext_dir = self.car_cdp.ensure_car_helper_extension(
                    target_url=target_url,
                    target_license=target_lic,
                    preferred_account=pref_acct,
                    auto_submit=auto_sub,
                    auto_mileage=auto_mil,
                )
                open_chrome_url(target_url, prof, ext_dir=ext_dir)
            self.root.after(0, lambda: self._after_car_browser_launched(ok, disp_name, target_url, pref_acct))

        threading.Thread(target=_launch_worker, daemon=True).start()

    def _after_car_browser_launched(self, ok: bool, disp_name: str, target_url: str, pref_acct: str = ""):
        self.refresh_car_accounts_list()
        acct_hint = f"（已自動帶入帳號 {pref_acct}）" if pref_acct else "（已自動帶入設定檔帳密）"
        if ok:
            auto_sub = self.var_car_auto_submit_login.get() if hasattr(self, 'var_car_auto_submit_login') else True
            if auto_sub:
                status_text = f"🚀 已開啟 [{disp_name}]{acct_hint}！正在全自動點擊「登入」..."
            else:
                status_text = f"🟡 已開啟 [{disp_name}]{acct_hint}！請點擊「登入」，登入後即自動每 3 分鐘背景心跳保活。"
            self.lbl_car_status.config(
                text=status_text,
                foreground="#0D47A1" if auto_sub else "#E65100"
            )
        else:
            self.lbl_car_status.config(
                text=f"🟢 已透過一般 Chrome 開啟 [{disp_name}]（已將防斷線心跳腳本複製至剪貼簿）",
                foreground="#2E7D32"
            )

    def open_car_in_normal_chrome(self):
        """使用一般日常 Chrome 開啟車輛系統（同步掛載小幫手專用原生擴充套件，100% 自動登入與導向）"""
        self.save_config()
        prof = self.get_selected_car_chrome_profile()
        target_url = self.get_selected_car_url()
        pref_acct = self.get_selected_car_account()
        target_lic = self.get_car_target_license()
        auto_sub = self.var_car_auto_submit_login.get() if hasattr(self, 'var_car_auto_submit_login') else True
        auto_mil = self.var_car_auto_mileage.get() if hasattr(self, 'var_car_auto_mileage') else True
        ext_dir = self.car_cdp.ensure_car_helper_extension(
            target_url=target_url,
            target_license=target_lic,
            preferred_account=pref_acct,
            auto_submit=auto_sub,
            auto_mileage=auto_mil,
        )
        try:
            bookmarklet = "javascript:" + build_car_heartbeat_bookmarklet()
            self.root.clipboard_clear()
            self.root.clipboard_append(bookmarklet)
            self.root.update()
        except Exception:
            pass
        open_chrome_url(target_url, prof, ext_dir=ext_dir)
        if hasattr(self, 'lbl_car_status'):
            self.lbl_car_status.config(
                text=f"🚀 已以日常 Chrome [{prof}] 開啟車輛系統！（防斷線心跳腳本已同步複製至剪貼簿）",
                foreground="#1565C0"
            )

    def on_toggle_car_heartbeat(self):
        self.save_config()
        enabled = self.var_car_heartbeat.get()
        if enabled:
            target_url = self.get_selected_car_url()
            if self.car_cdp.is_running():
                _, is_logged_in, _, _, _ = self.car_cdp.check_car_login_state(target_url)
                if is_logged_in:
                    self.send_car_heartbeat(manual=False)

    def send_car_heartbeat(self, manual: bool = False) -> bool:
        """
        向車輛管理系統送出背景防斷線心跳封包：
        - 於分頁內部安裝每 3 分鐘常駐 fetch 心跳定時器 + 立即送出一次 GET/HEAD 保活請求
        - 於網頁右下角顯示「🟢 車輛系統保活中」即時狀態膠囊
        - 完全在背景靜默執行，不切換分頁、不喚醒視窗、不搶焦點
        """
        target_url = self.get_selected_car_url()
        if not self.car_cdp.is_running():
            if manual:
                messagebox.showwarning(
                    "車輛系統尚未啟動",
                    "車輛系統受控 Chrome 尚未啟動！\n請先點擊【🚗 車輛系統登入】按鈕開啟瀏覽器並完成登入。"
                )
            return False

        has_tab, is_logged_in, is_login_page, _, _ = self.car_cdp.check_car_login_state(target_url)
        if not has_tab:
            if manual:
                messagebox.showwarning("無車輛系統分頁", "目前瀏覽器中未開啟車輛系統分頁！\n請點擊【🚗 車輛系統登入】重新導向。")
            return False
        if not is_logged_in or is_login_page:
            if manual:
                messagebox.showinfo("請先完成登入", "偵測到車輛系統目前仍在「登入畫面」！\n請先在 Chrome 視窗登入帳號密碼，登入後系統即會自動啟動背景心跳運作。")
            return False

        js_ping = """
        (() => {
            try {
                function updateCarBadge(msg, color) {
                    try {
                        let targetDoc = document;
                        if (document.body && document.body.tagName && document.body.tagName.toLowerCase() === 'frameset') {
                            for (let i = 0; i < window.frames.length; i++) {
                                try {
                                    if (window.frames[i].document && window.frames[i].document.body) {
                                        targetDoc = window.frames[i].document;
                                    }
                                } catch(e) {}
                            }
                        }
                        let b = targetDoc.getElementById('__car_heartbeat_pill');
                        if (!b) {
                            b = targetDoc.createElement('div');
                            b.id = '__car_heartbeat_pill';
                            b.style.cssText = 'position:fixed;bottom:14px;right:14px;background:rgba(15,23,42,0.88);color:#f8fafc;padding:6px 14px;border-radius:20px;font-size:12px;z-index:999999;box-shadow:0 4px 14px rgba(0,0,0,0.3);font-family:sans-serif;display:flex;align-items:center;gap:8px;backdrop-filter:blur(4px);border:1px solid rgba(255,255,255,0.18);cursor:pointer;user-select:none;';
                            b.title = '車輛系統防斷線背景心跳運作中 (每 3 分鐘自動維持連線)';
                            (targetDoc.body || targetDoc.documentElement).appendChild(b);
                        }
                        b.innerHTML = '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + (color || '#10b981') + ';box-shadow:0 0 6px ' + (color || '#10b981') + '"></span> <strong>車輛系統保活中</strong> <span style="color:#94a3b8;font-size:11px">' + (msg || new Date().toLocaleTimeString()) + '</span>';
                    } catch(e) {}
                }

                function doPing() {
                    let pingUrl = window.location.href;
                    fetch(pingUrl, {
                        method: 'GET',
                        cache: 'no-store',
                        credentials: 'include'
                    }).then(res => {
                        let t = new Date().toLocaleTimeString();
                        console.log('[車輛系統心跳保活] 成功維持連線: ' + t + ' (HTTP ' + res.status + ')');
                        updateCarBadge(t, '#10b981');
                    }).catch(() => {
                        fetch(window.location.origin + '/car/', {
                            method: 'HEAD',
                            cache: 'no-store',
                            credentials: 'include'
                        }).then(() => {
                            let t = new Date().toLocaleTimeString();
                            updateCarBadge(t + ' (備援)', '#38bdf8');
                        }).catch(() => {});
                    });
                }

                if (!window.__car_heartbeat_inpage) {
                    window.__car_heartbeat_inpage = setInterval(doPing, 180000);
                }
                doPing();
                return "OK";
            } catch(e) {
                return "ERR:" + e;
            }
        })()
        """
        ok, _ = self.car_cdp.evaluate_in_background(js_ping, target_url=target_url)
        if ok:
            self.last_car_heartbeat_time = time.time()
            self.last_car_heartbeat_str = datetime.now().strftime("%H:%M:%S")
            if hasattr(self, 'lbl_car_status'):
                prof = self.car_cdp.get_active_profile_mode()
                self.lbl_car_status.config(
                    text=f"🟢 車輛系統已登入 [{prof}] ｜ 💓 背景心跳運作中 (最後發送: {self.last_car_heartbeat_str}，每 3 分鐘自動保活)",
                    foreground="#2E7D32"
                )
            if manual:
                messagebox.showinfo(
                    "心跳發送成功",
                    f"💓 已成功向車輛管理系統發送背景防斷線心跳封包！\n\n"
                    f"• 發送時間：{self.last_car_heartbeat_str}\n"
                    f"• 自動保活：每 3 分鐘定時於背景維持連線，閒置不會被登出。"
                )
        return ok

    def _start_car_cdp_monitor(self):
        threading.Thread(target=self._car_cdp_monitor_worker, daemon=True).start()

    def _car_cdp_monitor_worker(self):
        while True:
            try:
                running = self.car_cdp.is_running()
                if running:
                    target_url = self.get_selected_car_url()
                    has_tab, is_logged_in, is_login_page, tab_title, tab_url = self.car_cdp.check_car_login_state(target_url)
                    if has_tab and is_login_page:
                        pref_acct = self.get_selected_car_account()
                        auto_sub = self.var_car_auto_submit_login.get() if hasattr(self, 'var_car_auto_submit_login') else True
                        assist_res = self.car_cdp.assist_car_login_page(
                            target_url=target_url,
                            preferred_account=pref_acct,
                            auto_submit=auto_sub,
                        )
                        if isinstance(assist_res, str) and assist_res.startswith("SWITCH:"):
                            new_acct = assist_res.split("SWITCH:", 1)[1].strip()
                            if new_acct:
                                self.root.after(0, lambda a=new_acct: self._apply_switched_car_account(a))
                                prof = self.get_selected_car_chrome_profile()
                                force_restart = (new_acct not in self.car_cdp._decrypted_car_creds)
                                auto_sub = self.var_car_auto_submit_login.get() if hasattr(self, 'var_car_auto_submit_login') else True
                                auto_mil = self.var_car_auto_mileage.get() if hasattr(self, 'var_car_auto_mileage') else True
                                target_lic = self.get_car_target_license()
                                self.car_cdp.launch_or_activate(
                                    target_url,
                                    profile_name=prof,
                                    preferred_account=new_acct,
                                    force_restart=force_restart,
                                    target_license=target_lic,
                                    auto_submit=auto_sub,
                                    auto_mileage=auto_mil,
                                )
                    elif has_tab and is_logged_in and not is_login_page:
                        should_run_mileage = (
                            getattr(self, 'car_mileage_flow_active', False)
                            or (hasattr(self, 'var_car_auto_mileage') and self.var_car_auto_mileage.get())
                        )
                        if should_run_mileage and not getattr(self, 'car_mileage_flow_completed', False):
                            target_lic = self.get_car_target_license()
                            m_res = self.car_cdp.assist_car_mileage_flow(
                                target_url=target_url,
                                target_license=target_lic,
                            )
                            if isinstance(m_res, str) and m_res.startswith("SUCCESS_FILLED"):
                                self.car_mileage_flow_completed = True
                                self.car_mileage_flow_active = False
                                parts = m_res.split(":")
                                l_val = parts[1] if len(parts) > 1 else target_lic
                                d_val = parts[2] if len(parts) > 2 else ""
                                self.root.after(0, lambda l=l_val, d=d_val: self._on_mileage_flow_success_ui(l, d))

                    hb_enabled = self.var_car_heartbeat.get() if hasattr(self, 'var_car_heartbeat') else True
                    if hb_enabled and has_tab and is_logged_in:
                        now_t = time.time()
                        if now_t - self.last_car_heartbeat_time >= self.car_heartbeat_interval_sec:
                            self.send_car_heartbeat(manual=False)
                    self.root.after(0, lambda ht=has_tab, li=is_logged_in, lp=is_login_page, tt=tab_title: self._update_car_cdp_ui(True, ht, li, lp, tt))
                else:
                    self.root.after(0, lambda: self._update_car_cdp_ui(False, False, False, False, ""))
            except Exception:
                pass
            time.sleep(0.8 if (is_login_page or getattr(self, 'car_mileage_flow_active', False)) else 2.0)

    def _update_car_cdp_ui(self, is_online: bool, has_tab: bool, is_logged_in: bool, is_login_page: bool, tab_title: str):
        if not hasattr(self, 'lbl_car_status') or not hasattr(self, 'btn_car_login'):
            return
        active_prof = self.car_cdp.get_active_profile_mode()
        sel_prof = self.get_selected_car_chrome_profile()
        target_lic = self.get_car_target_license()

        if hasattr(self, 'btn_car_mileage'):
            self.btn_car_mileage.config(text=f"🚑 一鍵里程登記 ({target_lic})")

        if not is_online:
            if hasattr(self, 'btn_car_main_action'):
                self.btn_car_main_action.config(text=f"🚑 一鍵全自動：車輛登入 ＆ 里程登記 ({target_lic})")
            self.lbl_car_status.config(
                text=f"🔴 狀態：尚未啟動（點擊「🚑 一鍵全自動」將以 [{sel_prof}] 自動登入並完成里程登記）",
                foreground="#C62828"
            )
            return

        if has_tab:
            if is_logged_in and not is_login_page:
                hb_enabled = self.var_car_heartbeat.get() if hasattr(self, 'var_car_heartbeat') else True
                hb_info = f"💓 背景心跳運作中 (最後發送: {self.last_car_heartbeat_str})" if hb_enabled else "⏸️ 背景心跳已暫停"
                if hasattr(self, 'btn_car_main_action'):
                    self.btn_car_main_action.config(text=f"🚑 一鍵全自動：車輛登入 ＆ 里程登記 ({target_lic})")
                if getattr(self, 'car_mileage_flow_completed', False):
                    self.lbl_car_status.config(
                        text=f"🟢 里程登記就緒 ｜ 🚑 已自動添加【{target_lic}】（日期已選今日，請填寫出勤里程後按儲存）",
                        foreground="#2E7D32"
                    )
                elif getattr(self, 'car_mileage_flow_active', False):
                    self.lbl_car_status.config(
                        text=f"⏳ 正在為您自動導向【里程登記】並新增選取 {target_lic} 與今日日期...",
                        foreground="#0D47A1"
                    )
                else:
                    self.lbl_car_status.config(
                        text=f"🟢 車輛系統已登入 [{active_prof}] ｜ {hb_info}",
                        foreground="#2E7D32"
                    )
            else:
                self.btn_car_login.config(text="🔑 前往完成車輛登入")
                auto_sub_hint = "（已啟用帳密自動填寫 ＆ 自動登入）" if (hasattr(self, 'var_car_auto_submit_login') and self.var_car_auto_submit_login.get()) else "（等待手動登入）"
                self.lbl_car_status.config(
                    text=f"🟡 車輛系統已開啟 [{active_prof}]：{auto_sub_hint}，登入後自動轉至【{target_lic} 里程登記】",
                    foreground="#E65100"
                )
        else:
            self.btn_car_login.config(text="🚗 開啟車輛系統分頁")
            self.lbl_car_status.config(
                text=f"🟡 Chrome [{active_prof}] 已連線（目前無車輛系統分頁，點擊「🚗 開啟車輛系統分頁」自動導向）",
                foreground="#E65100"
            )

    def browse_roster_file(self):
        """提供直接選取單一勤務表檔案 (.xlsm / .xlsx) 功能"""
        file_selected = filedialog.askopenfilename(
            title="選取當日勤務表檔案 (格式如：分隊-1020.xlsm)",
            filetypes=[("Excel 勤務表", "*.xlsm;*.xlsx;*.xls"), ("所有檔案", "*.*")]
        )
        if file_selected:
            self.entry_roster_path.delete(0, tk.END)
            self.entry_roster_path.insert(0, file_selected)
            self.save_config()
            self.load_officer_names_from_roster()
            self.update_comboboxes_all_tabs()
            self.test_roster_parse(show_success=True)

    def browse_roster_dir(self):
        """選取存放當月勤務表之資料夾"""
        init_dir = get_dialog_initialdir(
            self.entry_roster_path.get().strip() if hasattr(self, 'entry_roster_path') else "",
            fallback_dir=self.config.get("roster_path", "")
        )
        dir_selected = filedialog.askdirectory(title="選取存放當月勤務表之資料夾 (內含 分隊-月份日期.xlsm)", initialdir=init_dir)
        if dir_selected:
            self.entry_roster_path.delete(0, tk.END)
            self.entry_roster_path.insert(0, dir_selected)
            self.save_config()
            self.load_officer_names_from_roster()
            self.update_comboboxes_all_tabs()
            self.test_roster_parse(show_success=True)

    def test_roster_parse(self, show_success=True):
        """測試解析勤務表目錄並顯示診斷報告與月份確認"""
        path = self.entry_roster_path.get().strip() if hasattr(self, 'entry_roster_path') else self.config.get("roster_path", "")
        if not path or not os.path.exists(path):
            messagebox.showwarning("路徑無效", "請先選取有效的當月勤務表資料夾或檔案！")
            return

        folder_month = detect_roster_folder_month(path)
        folder_month_str = f"【{folder_month} 月】" if folder_month else "（未能由目錄直接識別月份）"

        target_file = None
        if os.path.isfile(path):
            target_file = path
        else:
            candidates = []
            try:
                search_dirs = [path]
                for entry in os.listdir(path):
                    sub_p = os.path.join(path, entry)
                    if os.path.isdir(sub_p) and not entry.startswith('.'):
                        search_dirs.append(sub_p)

                for d in search_dirs:
                    for fname in os.listdir(d):
                        if fname.startswith('~$'):
                            continue
                        if fname.lower().endswith(('.xlsm', '.xlsx', '.xls')):
                            fp = os.path.join(d, fname)
                            if os.path.isfile(fp):
                                try:
                                    t = max(os.path.getmtime(fp), os.path.getctime(fp))
                                except:
                                    t = 0
                                candidates.append((t, fp, fname))
            except Exception as e:
                messagebox.showerror("讀取目錄失敗", f"無法存取目錄：\n{e}")
                return

            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                target_file = candidates[0][1]

        if not target_file:
            messagebox.showwarning("找不到勤務表", f"在路徑【{path}】中未找到任何 .xlsm 或 .xlsx 勤務表檔案！\n請確認檔案是否存在。")
            return

        try:
            res = parse_roster_excel(target_file)
            id_count = len(res.get('id_to_name', {}))
            row_91 = res.get('row_91')
            has_91 = f"有 (第 {row_91} 列)" if row_91 else "❌ 未找到 (請確認A欄勤務名稱)"
            hours_count = len(res.get('hour_to_col', {}))
            mod_time = datetime.fromtimestamp(os.path.getmtime(target_file)).strftime('%Y/%m/%d %H:%M:%S')

            if res.get('id_to_name'):
                sorted_ids = sorted(res['id_to_name'].keys(), key=lambda x: int(x) if x.isdigit() else 999)
                new_roster_names = [res['id_to_name'][k] for k in sorted_ids if res['id_to_name'][k]]
                self.officer_names = list(new_roster_names)
                self.config["cached_officer_names"] = list(new_roster_names)
                self.update_comboboxes_all_tabs()

            if show_success:
                names_preview = "、".join(self.officer_names[:8])
                if len(self.officer_names) > 8:
                    names_preview += f" ... 等共 {len(self.officer_names)} 人"
                messagebox.showinfo(
                    "勤務表測試解析成功",
                    f"✅ 成功辨識並解析勤務表檔案！\n\n"
                    f"• 所在目錄月份：{folder_month_str}\n"
                    f"• 測試檔案：【{os.path.basename(target_file)}】\n"
                    f"• 檔案時間：{mod_time}\n"
                    f"• 同仁名冊：成功解析 {id_count} 位同仁姓名\n"
                    f"  名單預覽：{names_preview}\n"
                    f"• 緊急救護91：{has_91}\n"
                    f"• 勤務時段欄位：共 {hours_count} 個時段\n\n"
                    f"同仁姓名下拉選單已同步更新！任務分頁之各下拉選單與【⚡ 快速讀取】按鈕已可正常運作。"
                )
        except Exception as e:
            messagebox.showerror("測試解析失敗", f"解析檔案【{os.path.basename(target_file)}】時發生錯誤：\n{e}")

    def browse_update_dir(self):
        """提供選取存放更新檔之資料夾"""
        init_dir = get_dialog_initialdir(
            self.entry_update_dir.get().strip() if hasattr(self, 'entry_update_dir') else "",
            fallback_dir=self.config.get("update_dir", "")
        )
        dir_selected = filedialog.askdirectory(title="選取存放更新檔之資料夾 (可為隨身碟、NAS共用槽或下載資料夾)", initialdir=init_dir)
        if dir_selected:
            self.entry_update_dir.delete(0, tk.END)
            self.entry_update_dir.insert(0, dir_selected)
            self.save_config()
            self.check_and_apply_update(manual=True)

    def auto_check_update_on_startup(self):
        """程式啟動後背景延遲檢查是否有新版本更新"""
        try:
            self.check_and_apply_update(manual=False)
        except Exception as e:
            print(f"[AutoUpdate] 啟動檢查異常: {e}")

    def check_and_apply_update(self, manual=False):
        """檢查自動更新來源資料夾，並執行版本比對與自動更新替換"""
        import hashlib, zipfile, shutil

        update_dir = self.config.get("update_dir", "").strip()
        if hasattr(self, 'entry_update_dir'):
            entered = self.entry_update_dir.get().strip()
            if entered:
                update_dir = entered

        if not update_dir:
            if manual:
                messagebox.showwarning("路徑未設定", "請先點擊【📁 選擇資料夾】指定自動更新來源資料夾！")
            return

        if not os.path.exists(update_dir) or not os.path.isdir(update_dir):
            if manual:
                messagebox.showerror("路徑無效", f"指定的更新資料夾不存在或無法存取：\n{update_dir}")
            return

        # 取得目前運行的主腳本檔案路徑
        current_script = None
        for cand in [os.path.join(APP_DIR, "video_backup.pyw"), os.path.join(APP_DIR, "video_backup.py"), sys.argv[0]]:
            if cand and os.path.isfile(cand):
                current_script = os.path.abspath(cand)
                break

        if not current_script or not os.path.isfile(current_script):
            if manual:
                messagebox.showwarning("更新提示", "未能定位目前運行的腳本主程式檔案，無法執行自動替換。")
            return

        def get_bytes_md5(data_bytes):
            return hashlib.md5(data_bytes).hexdigest()

        try:
            with open(current_script, "rb") as f:
                current_bytes = f.read()
            current_md5 = get_bytes_md5(current_bytes)
            current_mtime = os.path.getmtime(current_script)
        except Exception as e:
            if manual:
                messagebox.showerror("讀取失敗", f"讀取目前程式檔案時發生錯誤：\n{e}")
            return

        # 搜尋更新資料夾中符合條件的檔案：
        # 1. 獨立 Python 檔：video_backup*.pyw 或 video_backup*.py
        # 2. ZIP 壓縮包：內含 video_backup.pyw 或 video_backup.py
        candidates = []
        try:
            for fname in os.listdir(update_dir):
                if fname.startswith('~$') or fname.endswith(('.bak', '.tmp')):
                    continue
                full_p = os.path.join(update_dir, fname)
                if not os.path.isfile(full_p):
                    continue
                # 排除自身
                if os.path.abspath(full_p) == current_script:
                    continue

                lower_name = fname.lower()
                # 模式 A: 獨立 Python 檔案
                if (lower_name.startswith("video_backup") or "小幫手" in lower_name) and (lower_name.endswith(".pyw") or lower_name.endswith(".py")):
                    try:
                        mtime = os.path.getmtime(full_p)
                        size = os.path.getsize(full_p)
                        if size > 1000:
                            candidates.append((mtime, full_p, fname, size, None))
                    except Exception:
                        pass
                # 模式 B: ZIP 壓縮檔案
                elif lower_name.endswith(".zip"):
                    try:
                        mtime = os.path.getmtime(full_p)
                        size = os.path.getsize(full_p)
                        with zipfile.ZipFile(full_p, 'r') as zf:
                            for zinfo in zf.infolist():
                                z_base = os.path.basename(zinfo.filename).lower()
                                if z_base in ("video_backup.pyw", "video_backup.py") and zinfo.file_size > 1000:
                                    candidates.append((mtime, full_p, f"{fname} (內含 {z_base})", size, zinfo.filename))
                                    break
                    except Exception:
                        pass
        except Exception as e:
            if manual:
                messagebox.showerror("掃描失敗", f"掃描更新資料夾時發生錯誤：\n{e}")
            return

        if not candidates:
            if manual:
                messagebox.showinfo(
                    "無更新檔",
                    f"在更新資料夾【{update_dir}】中未發現任何可用之更新檔案。\n\n"
                    f"💡 支援格式：\n"
                    f"1. video_backup*.pyw 或 video_backup*.py（如 video_backup (1).pyw）\n"
                    f"2. 包含程式之 .zip 壓縮包（如 救護影像抓取小幫手_Windows專用包.zip）"
                )
            return

        # 依最後修改時間降冪排序，挑選最新的一個候選檔案
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_mtime, best_path, best_display_name, best_size, zip_subpath = candidates[0]

        # 提取新版本的二進位內容與計算 MD5
        try:
            if zip_subpath:
                with zipfile.ZipFile(best_path, 'r') as zf:
                    new_bytes = zf.read(zip_subpath)
            else:
                with open(best_path, "rb") as f:
                    new_bytes = f.read()
            new_md5 = get_bytes_md5(new_bytes)
        except Exception as e:
            if manual:
                messagebox.showerror("讀取失敗", f"讀取更新檔內容時發生錯誤：\n{e}")
            return

        # 若內容與目前運行中程式完全相同，代表已是最新版
        if current_md5 == new_md5:
            if manual:
                messagebox.showinfo("已是最新版本", f"目前程式與更新檔【{best_display_name}】內容完全一致，已是最新版本！無需更新。")
            return

        # 若是自動檢查，但更新檔的修改時間比目前運行的檔案舊，則不自動打擾同仁
        if not manual and best_mtime < current_mtime:
            return

        time_str = datetime.fromtimestamp(best_mtime).strftime("%Y/%m/%d %H:%M:%S")
        prompt_msg = (
            f"🎉 發現小幫手新版本程式！\n\n"
            f"• 來源更新檔：【{best_display_name}】\n"
            f"• 檔案大小：{len(new_bytes) // 1024} KB\n"
            f"• 檔案時間：{time_str}\n\n"
            f"是否立即套用此更新並重新啟動小幫手？\n"
            f"（原本儲存之所有設定與路徑將 100% 完整保留）"
        )

        confirm = messagebox.askyesno("發現新版本更新", prompt_msg)
        if not confirm:
            return

        # 執行安全備份與覆蓋
        try:
            backup_script = current_script + ".bak"
            shutil.copy2(current_script, backup_script)

            with open(current_script, "wb") as f:
                f.write(new_bytes)

            # 同步覆蓋同目錄下對應的 .py 或 .pyw 備份檔
            for target_name in ["video_backup.pyw", "video_backup.py"]:
                target_p = os.path.join(APP_DIR, target_name)
                if os.path.isfile(target_p) and target_p != current_script:
                    try:
                        with open(target_p, "wb") as f:
                            f.write(new_bytes)
                    except Exception:
                        pass

            messagebox.showinfo(
                "更新成功",
                f"✅ 已成功升級至新版本！\n\n"
                f"舊版本已安全備份為【{os.path.basename(backup_script)}】。\n"
                f"點擊「確定」後，程式將立即為您自動重新啟動！"
            )

            # 釋放單一執行個體 Mutex / Socket 鎖定，確保重啟不會被擋
            release_single_instance_handle()

            # 清理右下角系統匣圖示
            if hasattr(self, 'tray_manager') and self.tray_manager:
                try:
                    self.tray_manager.cleanup()
                except Exception:
                    pass

            import subprocess, time
            time.sleep(0.3)

            vbs_launcher = os.path.join(APP_DIR, "啟動影像備份(無黑屏).vbs")
            if sys.platform == "win32" and os.path.isfile(vbs_launcher):
                os.startfile(vbs_launcher)
            else:
                subprocess.Popen([sys.executable, current_script] + sys.argv[1:])

            self.root.destroy()
            sys.exit(0)
        except Exception as e:
            messagebox.showerror("更新失敗", f"替換更新檔時發生錯誤：\n{e}\n\n請確認檔案未被防毒軟體鎖定，或以手動複製替換。")

    def refresh_disk_list(self):
        if not hasattr(self, 'txt_disks'):
            return
        self.txt_disks.delete(1.0, tk.END)
        disks = get_all_connected_disks()
        if not disks:
            self.txt_disks.insert(tk.END, "目前未偵測到任何外接隨身碟或硬碟。\n")
            return
        self.txt_disks.insert(tk.END, f"偵測到 {len(disks)} 個磁碟裝置：\n")
        self.txt_disks.insert(tk.END, "-" * 55 + "\n")
        for idx, (path, name) in enumerate(disks, 1):
            name_display = f"標籤名稱: [{name}]" if name else "(無標籤名稱)"
            self.txt_disks.insert(tk.END, f"{idx}. 磁碟路徑: {path:<15} -> {name_display}\n")
        self.txt_disks.insert(tk.END, "-" * 55 + "\n")
        self.txt_disks.insert(tk.END, "提示：將上述方括弧 [] 中的名稱複製至上方設定框即可！\n")

    def save_settings_clicked(self):
        """儲存快速設定分頁中的所有項目（包含當月勤務表路徑、設備標籤名稱與未錄影原因），並同步更新全部分頁之同仁選單"""
        if hasattr(self, 'txt_missing_reasons'):
            raw_text = self.txt_missing_reasons.get("1.0", tk.END)
            reasons = [line.strip() for line in raw_text.splitlines() if line.strip()]
            if not reasons:
                reasons = list(DEFAULT_MISSING_REASONS)
            self.config["missing_reasons"] = reasons

        self.save_config()
        self.load_officer_names_from_roster()
        self.update_comboboxes_all_tabs()
        # 儲存特徵/排除設定後，立即套用最新規則重新掃描全部分頁之設備連線狀態
        for t in self.task_tabs:
            try:
                t.refresh_device_connections(show_toast=False)
            except Exception:
                pass

        roster_p = self.config.get("roster_path", "")
        roster_name = os.path.basename(roster_p) if roster_p else "未指定"
        update_p = self.config.get("update_dir", "")
        car_url = self.config.get("car_system_url", DEFAULT_CAR_SYSTEM_URL)
        car_prof = self.config.get("car_chrome_profile", DEFAULT_CAR_CHROME_EMAIL)
        if hasattr(self, 'lbl_save_status'):
            self.lbl_save_status.config(
                text=f"✅ 設定已儲存！(勤務表: {roster_name}，車輛系統與下拉選單已同步更新)",
                foreground="#2E7D32"
            )

        messagebox.showinfo(
            "設定成功",
            "✅ 所有設定已成功儲存！\n\n"
            f"• 車輛系統網址：{car_url}\n"
            f"• Chrome 設定檔：{car_prof}\n"
            f"• 自動更新資料夾：{update_p if update_p else '（未指定）'}\n"
            f"• 當月勤務表目錄：{roster_p if roster_p else '（未指定）'}\n"
            "• 設備特徵指紋規則：已更新自訂特徵與寬限期 (支援各廠牌設備)\n"
            "• 未錄影備註原因：已更新自訂清單\n"
            "• 同仁姓名下拉選單：已同步更新最新名冊\n\n"
            "返回【主要備份作業】分頁即可直接使用。"
        )


if __name__ == "__main__":
    # 單一執行個體檢查：若程式已在執行，直接喚醒既有視窗並退出新進程
    _instance_handle = check_single_instance()
    try:
        root = tk.Tk()
        app = VideoBackupApp(root)
        root.mainloop()
    except Exception as e:
        import traceback
        err_trace = traceback.format_exc()
        try:
            log_dir = os.path.dirname(os.path.abspath(__file__)) if not getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(sys.executable))
            err_file = os.path.join(log_dir, "startup_error.log")
            with open(err_file, "w", encoding="utf-8") as f:
                f.write(err_trace)
        except Exception:
            pass
        try:
            messagebox.showerror(
                "程式啟動失敗",
                f"【救護影像抓取小幫手】啟動時發生錯誤：\n\n{str(e)}\n\n詳細錯誤資訊已自動記錄於同資料夾內的 startup_error.log。"
            )
        except Exception:
            pass
        raise
