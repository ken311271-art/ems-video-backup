import os
import sys
import shutil
import json
import re
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

CONFIG_FILE = os.path.join(APP_DIR, "backup_config.json")

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

def is_device_connected(path, expected_label):
    """【硬碟連線雙重驗證】：
    指定路徑存在 ＋ 標籤名稱吻合 (DASHCAM、BODYCAM1~3) 才算連線！
    若路徑存在但名稱不符合，不算連線。徹底防止同仁認錯卡或插錯設備。
    回傳: (is_connected: bool, status_message: str)
    """
    if not path or not os.path.exists(path):
        return False, "未連線 (路徑不存在)"
    actual_label = get_volume_label(path)
    if not expected_label or not expected_label.strip():
        return True, f"已連線 [{actual_label or '無標籤'}]"
    exp = expected_label.strip().upper()
    act = (actual_label or "").strip().upper()
    if exp == act or exp in act:
        return True, f"已連線 [{actual_label}]"
    return False, f"標籤不符 (目前為 [{actual_label}]，應為 [{expected_label}])"

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
        default_roc_year = str(now.year - 1911)
        default_mmdd = now.strftime("%m%d")
        default_start_4 = (now - timedelta(hours=1)).strftime("%H%M")
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

        # 頂部操作列：單一核心按鈕（刷新連線並讀取排班）
        f_top_btns = ttk.Frame(frame_step2)
        f_top_btns.pack(fill="x", pady=(2, 5))

        self.btn_read_roster = ttk.Button(
            f_top_btns,
            text="⚡ 刷新連線並讀取排班",
            command=self.quick_read_roster
        )
        self.btn_read_roster.pack(side="left", padx=(0, 8))

        self.lbl_roster_hint = ttk.Label(
            f_top_btns,
            text="(自動清空姓名 ➔ 偵測隨身碟 ➔ 依時段帶入排班)",
            foreground="#666666",
            font=('Helvetica', 9)
        )
        self.lbl_roster_hint.pack(side="left")

        # 列 0: 行車紀錄器連線狀態 (按需求：不顯示路徑字眼，僅顯示狀態燈號)
        f_dash_status = ttk.Frame(frame_step2)
        f_dash_status.pack(fill="x", pady=1)
        ttk.Label(f_dash_status, text="行車紀錄器狀態：", width=14).pack(side="left")
        self.lbl_status_dashcam = ttk.Label(f_dash_status, text="⚪ 未檢測", foreground="#888888", font=('Helvetica', 9))
        self.lbl_status_dashcam.pack(side="left", padx=2)
        self.var_swap_dashcam = tk.BooleanVar(value=True)
        self.chk_swap_dashcam = ttk.Checkbutton(
            f_dash_status,
            text="本次需換卡複製行車紀錄器",
            variable=self.var_swap_dashcam
        )

        # 列 1: 密錄器 1 同仁
        f_name1 = ttk.Frame(frame_step2)
        f_name1.pack(fill="x", pady=1)
        ttk.Label(f_name1, text="密錄器 1 同仁：", width=14).pack(side="left")
        self.entry_name1 = ttk.Combobox(f_name1, width=15, values=self.app.get_all_officer_names())
        if init_name1:
            self.entry_name1.set(init_name1)
        self.entry_name1.pack(side="left", padx=2)
        self.bind_officer_autocomplete(self.entry_name1)
        self.lbl_status_name1 = ttk.Label(f_name1, text="⚪ 未檢測", foreground="#888888", font=('Helvetica', 9))
        self.lbl_status_name1.pack(side="left", padx=6)

        # 列 2: 密錄器 2 同仁
        f_name2 = ttk.Frame(frame_step2)
        f_name2.pack(fill="x", pady=1)
        ttk.Label(f_name2, text="密錄器 2 同仁：", width=14).pack(side="left")
        self.entry_name2 = ttk.Combobox(f_name2, width=15, values=self.app.get_all_officer_names())
        if init_name2:
            self.entry_name2.set(init_name2)
        self.entry_name2.pack(side="left", padx=2)
        self.bind_officer_autocomplete(self.entry_name2)
        self.lbl_status_name2 = ttk.Label(f_name2, text="⚪ 未檢測", foreground="#888888", font=('Helvetica', 9))
        self.lbl_status_name2.pack(side="left", padx=6)

        # 列 3: 密錄器 3 同仁（常態直接顯示）
        f_name3 = ttk.Frame(frame_step2)
        f_name3.pack(fill="x", pady=1)
        ttk.Label(f_name3, text="密錄器 3 同仁：", width=14).pack(side="left")
        self.entry_name3 = ttk.Combobox(f_name3, width=15, values=self.app.get_all_officer_names())
        if init_name3:
            self.entry_name3.set(init_name3)
        self.entry_name3.pack(side="left", padx=2)
        self.bind_officer_autocomplete(self.entry_name3)
        self.lbl_status_name3 = ttk.Label(f_name3, text="⚪ 未檢測", foreground="#888888", font=('Helvetica', 9))
        self.lbl_status_name3.pack(side="left", padx=6)

        self.btn_manual_unlock3 = ttk.Button(f_name3, text="🔓 手動解鎖", command=self.toggle_manual_unlock3, width=10)
        self.btn_manual_unlock3.pack(side="left", padx=4)
        ttk.Label(f_name3, text="(共用讀卡機或第三人可點擊解鎖)", foreground="#666666", font=('Helvetica', 9)).pack(side="left", padx=2)

        # 列 4: 姓名快速對調區 (含 1⇄2、2⇄3、1⇄3 及 1➔2➔3 順序輪替)
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

        # 4. 基本設定：行車紀錄器 (含「🔒 鎖定 / 🔓 解鎖」按鈕)
        frame_dashcam = ttk.LabelFrame(self.content_frame, text="【基本設定：行車紀錄器】", padding=6)
        frame_dashcam.pack(fill="x", **pad)
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

        f_dash_src = ttk.Frame(frame_dashcam)
        f_dash_src.pack(fill="x", pady=2)
        ttk.Label(f_dash_src, text="來源隨身碟：", width=12).pack(side="left")
        self.entry_dashcam_src = ttk.Entry(f_dash_src, width=38)
        self.entry_dashcam_src.insert(0, self.app.config.get("dashcam_src", ""))
        self.entry_dashcam_src.pack(side="left", padx=4)
        self.btn_quick_dash = ttk.Button(f_dash_src, text="⚡ 快速讀取", command=self.quick_detect_dashcam)
        self.btn_quick_dash.pack(side="left", padx=2)
        self.btn_browse_dash = ttk.Button(f_dash_src, text="瀏覽...", command=lambda: self.browse_path(self.entry_dashcam_src, "選擇行車紀錄器來源隨身碟"))
        self.btn_browse_dash.pack(side="left", padx=2)

        # 5. 基本設定：密錄器 (含「🔒 鎖定 / 🔓 解鎖」按鈕)
        frame_bodycam = ttk.LabelFrame(self.content_frame, text="【基本設定：密錄器】", padding=6)
        frame_bodycam.pack(fill="x", **pad)
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

        f_b1 = ttk.Frame(frame_bodycam)
        f_b1.pack(fill="x", pady=2)
        ttk.Label(f_b1, text="密錄器 1 來源：", width=12).pack(side="left")
        self.entry_bodycam1_src = ttk.Entry(f_b1, width=38)
        self.entry_bodycam1_src.insert(0, self.app.config.get("bodycam1_src", ""))
        self.entry_bodycam1_src.pack(side="left", padx=4)
        self.btn_quick_b1 = ttk.Button(f_b1, text="⚡ 快速讀取", command=self.quick_detect_b1)
        self.btn_quick_b1.pack(side="left", padx=2)
        self.btn_browse_b1 = ttk.Button(f_b1, text="瀏覽...", command=lambda: self.browse_path(self.entry_bodycam1_src, "選擇密錄器 1 來源槽"))
        self.btn_browse_b1.pack(side="left", padx=2)

        f_b2 = ttk.Frame(frame_bodycam)
        f_b2.pack(fill="x", pady=2)
        ttk.Label(f_b2, text="密錄器 2 來源：", width=12).pack(side="left")
        self.entry_bodycam2_src = ttk.Entry(f_b2, width=38)
        self.entry_bodycam2_src.insert(0, self.app.config.get("bodycam2_src", ""))
        self.entry_bodycam2_src.pack(side="left", padx=4)
        self.btn_quick_b2 = ttk.Button(f_b2, text="⚡ 快速讀取", command=self.quick_detect_b2)
        self.btn_quick_b2.pack(side="left", padx=2)
        self.btn_browse_b2 = ttk.Button(f_b2, text="瀏覽...", command=lambda: self.browse_path(self.entry_bodycam2_src, "選擇密錄器 2 來源槽"))
        self.btn_browse_b2.pack(side="left", padx=2)

        # 密錄器 3 來源（常態直接顯示）
        self.f_b3 = ttk.Frame(frame_bodycam)
        self.f_b3.pack(fill="x", pady=2)
        ttk.Label(self.f_b3, text="密錄器 3 來源：", width=12).pack(side="left")
        self.entry_bodycam3_src = ttk.Entry(self.f_b3, width=38)
        self.entry_bodycam3_src.insert(0, self.app.config.get("bodycam3_src", ""))
        self.entry_bodycam3_src.pack(side="left", padx=4)
        self.btn_quick_b3 = ttk.Button(self.f_b3, text="⚡ 快速讀取", command=self.quick_detect_b3)
        self.btn_quick_b3.pack(side="left", padx=2)
        self.btn_browse_b3 = ttk.Button(self.f_b3, text="瀏覽...", command=lambda: self.browse_path(self.entry_bodycam3_src, "選擇密錄器 3 來源槽"))
        self.btn_browse_b3.pack(side="left", padx=2)

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
        n1 = self.entry_name1.get().strip()
        n2 = self.entry_name2.get().strip()
        self.entry_name1.set(n2)
        self.entry_name2.set(n1)
        self.log(f"🔄 [同仁姓名互換] 密錄器 1 ⇄ 密錄器 2 姓名已成功對調！({n1 or '空'} ⇄ {n2 or '空'})")
        self.app.save_config()

    def swap_2_3(self):
        """互換密錄器 2 與 3 之同仁姓名"""
        n2 = self.entry_name2.get().strip()
        n3 = self.entry_name3.get().strip()
        # 若 3 號原為鎖定狀態，且換入有人名，自動解鎖 3 號以利使用
        if n2 and str(self.entry_name3.cget("state")) == "disabled":
            self.entry_name3.config(state="normal")
            self.lbl_status_name3.config(text="🔓 已開放/解鎖", foreground="#E65100")
            self.btn_manual_unlock3.config(text="🔒 手動鎖定")
        self.entry_name2.set(n3)
        self.entry_name3.set(n2)
        self.log(f"🔄 [同仁姓名互換] 密錄器 2 ⇄ 密錄器 3 姓名已成功對調！({n2 or '空'} ⇄ {n3 or '空'})")
        self.app.save_config()

    def swap_1_3(self):
        """互換密錄器 1 與 3 之同仁姓名"""
        n1 = self.entry_name1.get().strip()
        n3 = self.entry_name3.get().strip()
        if n1 and str(self.entry_name3.cget("state")) == "disabled":
            self.entry_name3.config(state="normal")
            self.lbl_status_name3.config(text="🔓 已開放/解鎖", foreground="#E65100")
            self.btn_manual_unlock3.config(text="🔒 手動鎖定")
        self.entry_name1.set(n3)
        self.entry_name3.set(n1)
        self.log(f"🔄 [同仁姓名互換] 密錄器 1 ⇄ 密錄器 3 姓名已成功對調！({n1 or '空'} ⇄ {n3 or '空'})")
        self.app.save_config()

    def rotate_names(self):
        """密錄器 1 ➔ 2 ➔ 3 順時針姓名順序輪替"""
        n1 = self.entry_name1.get().strip()
        n2 = self.entry_name2.get().strip()
        n3 = self.entry_name3.get().strip()
        # 輪替：1號到2號、2號到3號、3號到1號
        new_1, new_2, new_3 = n3, n1, n2
        if new_3 and str(self.entry_name3.cget("state")) == "disabled":
            self.entry_name3.config(state="normal")
            self.lbl_status_name3.config(text="🔓 已開放/解鎖", foreground="#E65100")
            self.btn_manual_unlock3.config(text="🔒 手動鎖定")
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
        """手動切換密錄器 3 同仁輸入欄位之鎖定/解鎖狀態"""
        curr_state = str(self.entry_name3.cget("state"))
        if curr_state == "disabled":
            self.entry_name3.config(state="normal")
            self.lbl_status_name3.config(text="🔓 已手動解鎖 (待換卡)", foreground="#E65100")
            self.btn_manual_unlock3.config(text="🔒 手動鎖定")
            self.log("ℹ️ 已手動解鎖密錄器 3 同仁輸入欄位 (待換卡)。")
        else:
            self.entry_name3.set("")
            self.entry_name3.config(state="disabled")
            self.btn_manual_unlock3.config(text="🔓 手動解鎖")
            self.lbl_status_name3.config(text="⚪ 未連線 (共用槽)", foreground="#888888")
            self.log("ℹ️ 已手動鎖定並清空密錄器 3 同仁輸入欄位。")
            self.refresh_device_connections()

    def refresh_device_connections(self, show_toast=False):
        """【出勤設備連線雙重驗證】
        快速分辨行車紀錄器與密錄器 1、2、3 是否正確連線（指定路徑存在 ＋ Volume Label 標籤相符）。
        智慧處理行車紀錄器與密錄器 3 共用讀卡機（如 E:\）之狀態辨識。
        若有連線的才能讓使用者 KEY 人員（未連線者自動鎖定 disabled）。
        """
        label_dash = self.app.config.get("label_dashcam", "DASHCAM").strip()
        label_b1 = self.app.config.get("label_bodycam1", "BODYCAM1").strip()
        label_b2 = self.app.config.get("label_bodycam2", "BODYCAM2").strip()
        label_b3 = self.app.config.get("label_bodycam3", "BODYCAM3").strip()

        # 1. 取得一次連線磁碟快取，避免重複呼叫底層系統 API
        all_disks = get_all_connected_disks()
        for src_entry, exp_lbl in [
            (self.entry_dashcam_src, label_dash),
            (self.entry_bodycam1_src, label_b1),
            (self.entry_bodycam2_src, label_b2),
            (self.entry_bodycam3_src, label_b3),
        ]:
            curr = src_entry.get().strip()
            if not curr or not os.path.exists(curr):
                fp, _ = find_drive_by_label(exp_lbl, connected_disks=all_disks)
                if fp:
                    src_entry.delete(0, tk.END)
                    src_entry.insert(0, fp)

        curr_dash = self.entry_dashcam_src.get().strip()
        curr_b1 = self.entry_bodycam1_src.get().strip()
        curr_b2 = self.entry_bodycam2_src.get().strip()
        curr_b3 = self.entry_bodycam3_src.get().strip()

        # 2. 密錄器 1 驗證 (獨立插槽)
        is_b1, msg_b1 = is_device_connected(curr_b1, label_b1)
        if is_b1:
            self.entry_name1.config(state="normal")
            self.lbl_status_name1.config(text="🟢 已連線", foreground="#2E7D32")
        else:
            self.entry_name1.config(state="disabled")
            if curr_b1 and os.path.exists(curr_b1):
                self.lbl_status_name1.config(text="🔴 標籤不符", foreground="#C62828")
            else:
                self.lbl_status_name1.config(text="⚪ 未連線", foreground="#888888")

        # 3. 密錄器 2 驗證 (獨立插槽)
        is_b2, msg_b2 = is_device_connected(curr_b2, label_b2)
        if is_b2:
            self.entry_name2.config(state="normal")
            self.lbl_status_name2.config(text="🟢 已連線", foreground="#2E7D32")
        else:
            self.entry_name2.config(state="disabled")
            if curr_b2 and os.path.exists(curr_b2):
                self.lbl_status_name2.config(text="🔴 標籤不符", foreground="#C62828")
            else:
                self.lbl_status_name2.config(text="⚪ 未連線", foreground="#888888")

        # 4. 行車紀錄器與密錄器 3（智慧辨識是否共用讀卡機）
        def get_drive_root(p):
            if not p:
                return ""
            try:
                drv = os.path.splitdrive(os.path.abspath(p))[0].upper()
                if drv:
                    return drv
            except Exception:
                pass
            return os.path.abspath(p).upper()

        drv_dash = get_drive_root(curr_dash)
        drv_b3 = get_drive_root(curr_b3)
        is_shared_reader = bool(drv_dash and drv_b3 and drv_dash == drv_b3)

        is_manual_unlocked3 = (hasattr(self, 'btn_manual_unlock3') and 
                               self.btn_manual_unlock3.cget("text") == "🔒 手動鎖定")

        if is_shared_reader:
            shared_p = curr_dash or curr_b3
            if not os.path.exists(shared_p):
                # 讀卡機未插入
                if hasattr(self, 'lbl_status_dashcam'):
                    self.lbl_status_dashcam.config(text="⚪ 未連線", foreground="#888888")
                if hasattr(self, 'chk_swap_dashcam'):
                    self.chk_swap_dashcam.pack_forget()
                if is_manual_unlocked3:
                    self.entry_name3.config(state="normal")
                    self.lbl_status_name3.config(text="🔓 已手動解鎖", foreground="#E65100")
                else:
                    self.entry_name3.config(state="disabled")
                    self.lbl_status_name3.config(text="⚪ 未連線", foreground="#888888")
            else:
                # 讀卡機存在，檢測其標籤
                is_dash_card, _ = is_device_connected(shared_p, label_dash)
                is_b3_card, _ = is_device_connected(shared_p, label_b3)

                if is_dash_card:
                    # 讀卡機目前插著行車紀錄器
                    if hasattr(self, 'lbl_status_dashcam'):
                        self.lbl_status_dashcam.config(text="🟢 已連線", foreground="#2E7D32")
                    if hasattr(self, 'chk_swap_dashcam'):
                        self.chk_swap_dashcam.pack_forget()

                    # 該槽放的是行車記錄器卡片，沒放密錄器3卡片不可填人名；需使用者手動解鎖或排班自動解鎖才開放
                    if is_manual_unlocked3:
                        self.entry_name3.config(state="normal")
                        self.lbl_status_name3.config(text="🔓 已手動解鎖 (待換卡)", foreground="#E65100")
                    else:
                        self.entry_name3.config(state="disabled")
                        self.lbl_status_name3.config(text="⚪ 未連線 (共用槽)", foreground="#888888")
                elif is_b3_card:
                    # 讀卡機目前插著密錄器 3
                    self.entry_name3.config(state="normal")
                    self.lbl_status_name3.config(text="🟢 已連線", foreground="#2E7D32")
                    if hasattr(self, 'lbl_status_dashcam'):
                        self.lbl_status_dashcam.config(text="⚪ 未連線 (共用槽)", foreground="#888888")
                    if hasattr(self, 'chk_swap_dashcam'):
                        self.chk_swap_dashcam.pack(side="left", padx=8)
                else:
                    # 插了非預期隨身碟（標籤皆不相符）
                    if hasattr(self, 'lbl_status_dashcam'):
                        self.lbl_status_dashcam.config(text="🔴 標籤不符", foreground="#C62828")
                    if hasattr(self, 'chk_swap_dashcam'):
                        self.chk_swap_dashcam.pack_forget()
                    if is_manual_unlocked3:
                        self.entry_name3.config(state="normal")
                        self.lbl_status_name3.config(text="🔓 已手動解鎖", foreground="#E65100")
                    else:
                        self.entry_name3.config(state="disabled")
                        self.lbl_status_name3.config(text="🔴 標籤不符", foreground="#C62828")
        else:
            if hasattr(self, 'chk_swap_dashcam'):
                self.chk_swap_dashcam.pack_forget()
            # 獨立插槽情況
            # 行車紀錄器獨立驗證
            is_dash, _ = is_device_connected(curr_dash, label_dash)
            if hasattr(self, 'lbl_status_dashcam'):
                if is_dash:
                    self.lbl_status_dashcam.config(text="🟢 已連線", foreground="#2E7D32")
                elif curr_dash and os.path.exists(curr_dash):
                    self.lbl_status_dashcam.config(text="🔴 標籤不符", foreground="#C62828")
                else:
                    self.lbl_status_dashcam.config(text="⚪ 未連線", foreground="#888888")

            # 密錄器 3 獨立驗證
            is_b3, _ = is_device_connected(curr_b3, label_b3)
            if is_b3:
                self.entry_name3.config(state="normal")
                self.lbl_status_name3.config(text="🟢 已連線", foreground="#2E7D32")
            elif is_manual_unlocked3:
                self.entry_name3.config(state="normal")
                self.lbl_status_name3.config(text="🔓 已手動解鎖", foreground="#E65100")
            elif curr_b3 and os.path.exists(curr_b3):
                self.entry_name3.config(state="disabled")
                self.lbl_status_name3.config(text="🔴 標籤不符", foreground="#C62828")
            else:
                self.entry_name3.config(state="disabled")
                self.lbl_status_name3.config(text="⚪ 未連線", foreground="#888888")

        if show_toast:
            dash_txt = self.lbl_status_dashcam.cget("text") if hasattr(self, 'lbl_status_dashcam') else ""
            summary_parts = []
            if dash_txt:
                summary_parts.append(f"行車: {dash_txt}")
            summary_parts.append(f"密錄 1: {self.lbl_status_name1.cget('text')}")
            summary_parts.append(f"密錄 2: {self.lbl_status_name2.cget('text')}")
            summary_parts.append(f"密錄 3: {self.lbl_status_name3.cget('text')}")
            self.log(f"🔄 [設備連線刷新] {'、'.join(summary_parts)}")

    def apply_lock_dashcam(self, is_locked):
        """套用行車紀錄器區域之鎖定/解鎖狀態至元件"""
        state = "disabled" if is_locked else "normal"
        if hasattr(self, 'entry_dashcam_target'):
            self.entry_dashcam_target.config(state=state)
        if hasattr(self, 'entry_dashcam_src'):
            self.entry_dashcam_src.config(state=state)
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
        """套用密錄器區域之鎖定/解鎖狀態至元件"""
        state = "disabled" if is_locked else "normal"
        if hasattr(self, 'entry_bodycam_target'):
            self.entry_bodycam_target.config(state=state)
        if hasattr(self, 'entry_bodycam1_src'):
            self.entry_bodycam1_src.config(state=state)
        if hasattr(self, 'entry_bodycam2_src'):
            self.entry_bodycam2_src.config(state=state)
        if hasattr(self, 'entry_bodycam3_src'):
            self.entry_bodycam3_src.config(state=state)
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

    def browse_path(self, target_entry, title):
        selected = filedialog.askdirectory(title=title)
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
        if path:
            self.entry_dashcam_src.delete(0, tk.END)
            self.entry_dashcam_src.insert(0, path)
            self.log(f"⚡ [行車紀錄器] 成功快速讀取：{path} (名稱: {matched_name})")
            self.app.save_config()
            self.refresh_device_connections()
        else:
            messagebox.showwarning("未找到硬碟", f"未找到名稱包含【{label}】的行車紀錄器隨身碟！\n\n請確認設備是否已插妥，或至【快速設定】分頁確認名稱。")

    def quick_detect_b1(self):
        label = self.app.config.get("label_bodycam1", "BODYCAM1")
        path, matched_name = find_drive_by_label(label)
        if path:
            self.entry_bodycam1_src.delete(0, tk.END)
            self.entry_bodycam1_src.insert(0, path)
            self.log(f"⚡ [密錄器 1] 成功快速讀取：{path} (名稱: {matched_name})")
            self.app.save_config()
            self.refresh_device_connections()
        else:
            messagebox.showwarning("未找到硬碟", f"未找到名稱包含【{label}】的密錄器 1 隨身碟！\n\n請確認設備是否已插妥，或至【快速設定】分頁確認名稱。")

    def quick_detect_b2(self):
        label = self.app.config.get("label_bodycam2", "BODYCAM2")
        path, matched_name = find_drive_by_label(label)
        if path:
            self.entry_bodycam2_src.delete(0, tk.END)
            self.entry_bodycam2_src.insert(0, path)
            self.log(f"⚡ [密錄器 2] 成功快速讀取：{path} (名稱: {matched_name})")
            self.app.save_config()
            self.refresh_device_connections()
        else:
            messagebox.showwarning("未找到硬碟", f"未找到名稱包含【{label}】的密錄器 2 隨身碟！\n\n請確認設備是否已插妥，或至【快速設定】分頁確認名稱。")

    def quick_detect_b3(self):
        label = self.app.config.get("label_bodycam3", "BODYCAM3")
        path, matched_name = find_drive_by_label(label)
        if path:
            self.entry_bodycam3_src.delete(0, tk.END)
            self.entry_bodycam3_src.insert(0, path)
            self.log(f"⚡ [密錄器 3] 成功快速讀取：{path} (名稱: {matched_name})")
            self.app.save_config()
            self.refresh_device_connections()
        else:
            messagebox.showwarning("未找到硬碟", f"未找到名稱包含【{label}】的密錄器 3 隨身碟！\n\n請確認設備是否已插妥，或至【快速設定】分頁確認名稱。")

    def quick_read_roster(self):
        """【一鍵連線並讀取排班】
        執行順序：
        1. 執行前，先把密錄 1、2、3 的姓名全數清空。
        2. 先執行設備連線偵測 (refresh_device_connections)，更新各隨身碟狀態與開放狀態。
        3. 再讀取當天排班 Excel，從當天勤務表比對救護班同仁，並依序填入已連線的密錄器。
        """
        # 1. 執行前，先把密錄 1、2、3 姓名清空
        self.entry_name1.set("")
        self.entry_name2.set("")
        was_disabled = str(self.entry_name3.cget("state")) == "disabled"
        if was_disabled:
            self.entry_name3.config(state="normal")
        self.entry_name3.set("")
        if was_disabled:
            self.entry_name3.config(state="disabled")
        self.log("🧹 [清空姓名] 已清除密錄器 1、2、3 目前之人員姓名。")

        # 2. 先執行連線
        self.refresh_device_connections(show_toast=False)
        self.log("🔄 [設備偵測] 已重新完成各設備連線狀態偵測。")

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
                dir_selected = filedialog.askdirectory(title="選取存放當月勤務表之資料夾")
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

        # 1. 先刷新設備連線，確保取得各密錄器最新連線與開放狀態
        # 嚴格遵循：看使用者插入哪幾個密錄器，才開放有連線的密錄器；未連線者保持鎖定
        self.refresh_device_connections(show_toast=False)

        # 2. 取得所有目前「開放／已連線」之密錄器插槽 (按 1 -> 2 -> 3 順序)
        bodycam_slots = [
            (1, self.entry_name1, self.lbl_status_name1),
            (2, self.entry_name2, self.lbl_status_name2),
            (3, self.entry_name3, self.lbl_status_name3),
        ]

        open_slots = []
        for idx, entry, lbl_status in bodycam_slots:
            if str(entry.cget("state")) == "normal":
                open_slots.append((idx, entry))
            else:
                # 未開放/未連線之密錄器清空並維持鎖定
                entry.config(state="normal")
                entry.set("")
                entry.config(state="disabled")

        if not open_slots:
            messagebox.showwarning(
                "無連線開放之密錄器",
                "目前未偵測到任何已連線或開放之密錄器！\n\n請先插入密錄器隨身碟並點擊【🔄 重新刷新連線】，或點擊密錄器 3 的【🔓 手動解鎖】後再帶入排班。"
            )
            return

        # 3. 依序填入有連接/開放的密錄器上
        # 若要填入的人員數 > 開放密錄器數，則只填有連接的密錄器即可
        assigned_records = []
        for i, (idx, entry) in enumerate(open_slots):
            if i < len(names):
                person_name = names[i]
                entry.set(person_name)
                assigned_records.append(f"密錄器 {idx}【{person_name}】")
            else:
                entry.set("")

        prev_label = f" (前一日 {duty_month:02d}/{duty_day:02d} 凌晨勤務)" if is_prev else ""
        if len(names) > len(open_slots):
            log_msg = (
                f"⚡ 成功自勤務表【{file_name}】取得 {len(names)} 位同仁，已依序填入已連線開放之 {len(open_slots)} 部密錄器："
                f"{'、'.join(assigned_records)} (時段 [{time_slot_str}]{prev_label})"
            )
        else:
            log_msg = (
                f"⚡ 成功自勤務表【{file_name}】依序帶入時段 [{time_slot_str}]{prev_label} 救護班："
                f"{'、'.join(assigned_records)}"
            )
        self.log(log_msg)

        if hasattr(self, 'lbl_roster_hint'):
            hint_clean = "、".join([r.replace("密錄器 ", "").replace("【", "").replace("】", "") for r in assigned_records])
            self.lbl_roster_hint.config(
                text=f"✅ 已帶入 {time_slot_str}{prev_label}：{hint_clean}",
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
        label_b1 = self.app.config.get("label_bodycam1", "BODYCAM1").strip()
        label_b2 = self.app.config.get("label_bodycam2", "BODYCAM2").strip()
        label_b3 = self.app.config.get("label_bodycam3", "BODYCAM3").strip()

        # 雙重驗證各設備
        dash_conn, dash_msg = is_device_connected(dashcam_src, label_dash)
        b1_conn, b1_msg = is_device_connected(bodycam1_src, label_b1)
        b2_conn, b2_msg = is_device_connected(bodycam2_src, label_b2)
        b3_conn, b3_msg = is_device_connected(bodycam3_src, label_b3)

        # 判斷行車紀錄器與密錄器 3 是否共用讀卡機
        is_shared_reader = False
        reader_phase1 = None
        reader_phase2 = None

        req_dash = bool(dashcam_src)
        req_b3 = bool(name3)  # 密錄器 3 僅在使用者有輸入人名時才視為需執行之任務

        dash_drive = os.path.splitdrive(os.path.abspath(dashcam_src))[0].upper() if dashcam_src else ""
        b3_drive = os.path.splitdrive(os.path.abspath(bodycam3_src))[0].upper() if bodycam3_src else ""
        same_drive = bool(dash_drive and b3_drive and dash_drive == b3_drive)

        if same_drive or (dashcam_src and bodycam3_src and os.path.abspath(dashcam_src).upper() == os.path.abspath(bodycam3_src).upper()):
            if dash_conn:
                reader_phase1 = "dashcam"
                # 狀況一：共用槽目前插著行車記錄器
                # 只有當使用者有 KEY 密錄器 3 人員，行車記錄器複製完後才排入第二階段換卡
                if name3:
                    reader_phase2 = "bodycam3"
                    is_shared_reader = True
                else:
                    reader_phase2 = None
                    is_shared_reader = False  # 若未填人員，則行車記錄器跑完就好，不用跳換卡
            elif b3_conn:
                reader_phase1 = "bodycam3"
                # 狀況二：共用槽只插密錄器 3
                # 依「本次需換卡複製行車紀錄器」勾選狀態決定
                want_dash = self.var_swap_dashcam.get() if hasattr(self, 'var_swap_dashcam') else True
                if want_dash:
                    reader_phase2 = "dashcam"
                    is_shared_reader = True
                    req_dash = True
                else:
                    reader_phase2 = None
                    is_shared_reader = False
                    req_dash = False  # 未勾選則密錄器 3 跑完就好，不用跳換卡
            else:
                is_shared_reader = False

        precheck_results = []

        # 1. 行車紀錄器
        if req_dash:
            if is_shared_reader and reader_phase2 == "dashcam":
                precheck_results.append({
                    "type": "dashcam",
                    "label": "🚗 行車紀錄器",
                    "src": bodycam3_src or dashcam_src,
                    "connected": True,
                    "is_swap_phase2": True,
                    "reader_path": bodycam3_src or dashcam_src,
                    "files": [],
                    "name": "",
                    "status_desc": "⏳ 待換卡 (共用讀卡機) — 待第一階段密錄器 3 完成後提示換卡"
                })
            else:
                files = self.find_videos_in_range(dashcam_src, start_dt, end_dt) if dash_conn else []
                precheck_results.append({
                    "type": "dashcam",
                    "label": "🚗 行車紀錄器",
                    "src": dashcam_src,
                    "connected": dash_conn,
                    "files": files,
                    "name": "",
                    "status_desc": f"{'🟢 已連線' if dash_conn else '🔴 ' + dash_msg}"
                })

        # 2. 密錄器 1
        if name1 or bodycam1_src:
            files = self.find_videos_in_range(bodycam1_src, start_dt, end_dt) if b1_conn else []
            precheck_results.append({
                "type": "bodycam1",
                "label": f"📹 密錄器 1 ({name1 if name1 else '未填姓名'})",
                "src": bodycam1_src,
                "connected": b1_conn,
                "files": files,
                "name": name1,
                "status_desc": f"{'🟢 已連線' if b1_conn else '🔴 ' + b1_msg}"
            })

        # 3. 密錄器 2
        if name2 or bodycam2_src:
            files = self.find_videos_in_range(bodycam2_src, start_dt, end_dt) if b2_conn else []
            precheck_results.append({
                "type": "bodycam2",
                "label": f"📹 密錄器 2 ({name2 if name2 else '未填姓名'})",
                "src": bodycam2_src,
                "connected": b2_conn,
                "files": files,
                "name": name2,
                "status_desc": f"{'🟢 已連線' if b2_conn else '🔴 ' + b2_msg}"
            })

        # 4. 密錄器 3
        if req_b3:
            if is_shared_reader and reader_phase2 == "bodycam3":
                precheck_results.append({
                    "type": "bodycam3",
                    "label": f"📹 密錄器 3 ({name3 if name3 else '未填姓名'})",
                    "src": dashcam_src or bodycam3_src,
                    "connected": True,
                    "is_swap_phase2": True,
                    "reader_path": dashcam_src or bodycam3_src,
                    "files": [],
                    "name": name3,
                    "status_desc": "⏳ 待換卡 (共用讀卡機) — 待第一階段行車紀錄器完成後提示換卡"
                })
            else:
                files = self.find_videos_in_range(bodycam3_src, start_dt, end_dt) if b3_conn else []
                precheck_results.append({
                    "type": "bodycam3",
                    "label": f"📹 密錄器 3 ({name3 if name3 else '未填姓名'})",
                    "src": bodycam3_src,
                    "connected": b3_conn,
                    "files": files,
                    "name": name3,
                    "status_desc": f"{'🟢 已連線' if b3_conn else '🔴 ' + b3_msg}"
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

    def prompt_card_swap(self, reader_path, target_dev_type, target_name, target_label, start_dt, end_dt):
        """共用讀卡機獨立換卡對話框 (在讀卡機線程中呼叫，透過 Event 與主線程 UI 同步)
        此時密錄器 1、2 仍在各自的背景線程中持續複製，完全不受影響！
        """
        swap_event = threading.Event()
        swap_confirmed = [False]
        scanned_files = []

        dev_title = "密錄器 3" if target_dev_type == "bodycam3" else "行車紀錄器"
        prev_title = "行車紀錄器" if target_dev_type == "bodycam3" else "密錄器 3"

        def show_dialog():
            dlg = tk.Toplevel(self.app.root)
            dlg.title(f"⚡ 共用讀卡機換卡提醒 - 請換插【{dev_title}】")
            dlg.geometry("540x360")
            dlg.resizable(False, False)
            dlg.transient(self.app.root)
            dlg.grab_set()

            dlg.update_idletasks()
            x = self.app.root.winfo_x() + (self.app.root.winfo_width() - 540) // 2
            y = self.app.root.winfo_y() + (self.app.root.winfo_height() - 360) // 2
            dlg.geometry(f"+{max(0, x)}+{max(0, y)}")

            f = ttk.Frame(dlg, padding=16)
            f.pack(fill="both", expand=True)

            ttk.Label(f, text=f"⚡ 第一階段【{prev_title}】已複製完成！", font=('Helvetica', 12, 'bold'), foreground="#008800").pack(anchor="w", pady=(0, 4))
            ttk.Label(f, text="💡 提示：密錄器 1、2 仍在背景持續拷貝中，完全不需等待，請立刻換卡！", foreground="#0066CC", font=('Helvetica', 10, 'bold')).pack(anchor="w", pady=(0, 10))

            ttk.Separator(f, orient="horizontal").pack(fill="x", pady=4)

            msg = (
                f"請依照以下步驟換卡：\n\n"
                f"1. 請從讀卡機拔下【{prev_title}】記憶卡。\n"
                f"2. 換插【{dev_title}】記憶卡 (標籤名稱應為: {target_label})。\n"
                f"3. 插妥後，點擊下方「我已換妥，繼續複製」按鈕。\n\n"
                f"系統將自動進行雙重驗證，確認標籤正確後立即接續抓取！"
            )
            ttk.Label(f, text=msg, justify="left", font=('Helvetica', 10)).pack(anchor="w", pady=4)

            lbl_verify_err = ttk.Label(f, text="", foreground="#C62828", font=('Helvetica', 9, 'bold'))
            lbl_verify_err.pack(anchor="w", pady=2)

            f_btns = ttk.Frame(f)
            f_btns.pack(fill="x", pady=(10, 0))

            def on_continue():
                actual_path = reader_path
                is_conn, status_msg = is_device_connected(actual_path, target_label)
                if not is_conn:
                    alt_path, _ = find_drive_by_label(target_label)
                    if alt_path:
                        actual_path = alt_path
                        is_conn, status_msg = is_device_connected(actual_path, target_label)

                if not is_conn:
                    lbl_verify_err.config(text=f"❌ 驗證未通過：{status_msg}\n請確認【{target_label}】記憶卡已插好！")
                    return

                files = self.find_videos_in_range(actual_path, start_dt, end_dt)
                scanned_files.extend(files)
                swap_confirmed[0] = True
                dlg.destroy()
                swap_event.set()

            def on_skip():
                if messagebox.askyesno("略過換卡", f"確定要略過本次【{dev_title}】的複製作業嗎？", parent=dlg):
                    swap_confirmed[0] = False
                    dlg.destroy()
                    swap_event.set()

            btn_ok = ttk.Button(f_btns, text=f"⚡ 我已換妥，繼續複製【{dev_title}】", command=on_continue)
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
            item_dash = next((x for x in precheck_results if x["type"] == "dashcam"), None)
            item_b1 = next((x for x in precheck_results if x["type"] == "bodycam1"), None)
            item_b2 = next((x for x in precheck_results if x["type"] == "bodycam2"), None)
            item_b3 = next((x for x in precheck_results if x["type"] == "bodycam3"), None)

            init_total = sum(len(x["files"]) for x in precheck_results if x.get("connected") and not x.get("is_swap_phase2"))
            total_files_counter = [init_total]
            copied_files_counter = [0]
            progress_lock = threading.Lock()

            self.update_progress_bar(maximum=max(1, total_files_counter[0]), value=0)
            self.update_status(f"狀態：正在複製 (0/{total_files_counter[0]}) 部影片...", "#0066CC")
            self.log(f"🚀 開始多線程獨立複製程序，預計寫入 {total_files_counter[0]} 個影片...")

            def copy_files_worker(src_files, dst_dir, dev_type):
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

            worker_threads = []

            # Thread 1: 密錄器 1 (獨立背景線程)
            if item_b1 and item_b1.get("connected") and item_b1.get("files"):
                name = item_b1.get("name", "")
                folder_name = f"{case_no}{name}" if name else f"{case_no}"
                b1_dst = os.path.join(bodycam_target, today_mmdd, case_no, folder_name)
                case_root = os.path.join(bodycam_target, today_mmdd, case_no)
                if case_root not in opened_folders:
                    opened_folders.append(case_root)

                t1 = threading.Thread(target=copy_files_worker, args=(item_b1["files"], b1_dst, "bodycam1"))
                t1.daemon = True
                worker_threads.append(t1)

            # Thread 2: 密錄器 2 (獨立背景線程)
            if item_b2 and item_b2.get("connected") and item_b2.get("files"):
                name = item_b2.get("name", "")
                folder_name = f"{case_no}{name}" if name else f"{case_no}"
                b2_dst = os.path.join(bodycam_target, today_mmdd, case_no, folder_name)
                case_root = os.path.join(bodycam_target, today_mmdd, case_no)
                if case_root not in opened_folders:
                    opened_folders.append(case_root)

                t2 = threading.Thread(target=copy_files_worker, args=(item_b2["files"], b2_dst, "bodycam2"))
                t2.daemon = True
                worker_threads.append(t2)

            # Thread 3: 共用讀卡機線程 (行車紀錄器與密錄器 3)
            phase1_item = None
            phase2_item = None

            if item_dash and item_dash.get("is_swap_phase2"):
                phase2_item = item_dash
                phase1_item = item_b3
            elif item_b3 and item_b3.get("is_swap_phase2"):
                phase2_item = item_b3
                phase1_item = item_dash
            else:
                standalone_dash = item_dash if (item_dash and item_dash.get("connected") and item_dash.get("files")) else None
                standalone_b3 = item_b3 if (item_b3 and item_b3.get("connected") and item_b3.get("files")) else None

                if standalone_dash:
                    d_dst = os.path.join(dashcam_target, today_mmdd, case_no)
                    td = threading.Thread(target=copy_files_worker, args=(standalone_dash["files"], d_dst, "dashcam"))
                    td.daemon = True
                    worker_threads.append(td)

                if standalone_b3:
                    name = standalone_b3.get("name", "")
                    folder_name = f"{case_no}{name}" if name else f"{case_no}"
                    b3_dst = os.path.join(bodycam_target, today_mmdd, case_no, folder_name)
                    case_root = os.path.join(bodycam_target, today_mmdd, case_no)
                    if case_root not in opened_folders:
                        opened_folders.append(case_root)
                    tb3 = threading.Thread(target=copy_files_worker, args=(standalone_b3["files"], b3_dst, "bodycam3"))
                    tb3.daemon = True
                    worker_threads.append(tb3)

            if phase1_item or phase2_item:
                def card_reader_flow():
                    # 執行 Phase 1
                    if phase1_item and phase1_item.get("files"):
                        p1_type = phase1_item["type"]
                        if p1_type == "dashcam":
                            p1_dst = os.path.join(dashcam_target, today_mmdd, case_no)
                        else:
                            name = phase1_item.get("name", "")
                            folder_name = f"{case_no}{name}" if name else f"{case_no}"
                            p1_dst = os.path.join(bodycam_target, today_mmdd, case_no, folder_name)
                            case_root = os.path.join(bodycam_target, today_mmdd, case_no)
                            if case_root not in opened_folders:
                                opened_folders.append(case_root)

                        self.log(f"🚗/📹 [共用讀卡機] 開始第一階段【{phase1_item['label']}】複製...")
                        copy_files_worker(phase1_item["files"], p1_dst, p1_type)

                    if self.stop_requested.is_set():
                        return

                    # 第一階段完成，若需要換卡
                    if phase2_item:
                        p1_title = phase1_item['label'] if phase1_item else '前一設備'
                        p2_type = phase2_item["type"]
                        p2_name = phase2_item.get("name", "")
                        p2_label = self.app.config.get("label_bodycam3" if p2_type == "bodycam3" else "label_dashcam", "BODYCAM3" if p2_type == "bodycam3" else "DASHCAM")
                        reader_p = phase2_item.get("reader_path") or (phase1_item.get("src") if phase1_item else "")

                        self.log(f"⚡ [共用讀卡機] 第一階段【{p1_title}】已完成！讀卡機已釋放。")
                        self.log(f"📢 提示換卡：密錄器 1、2 仍在背景持續複製中。請換插【{phase2_item['label']}】！")

                        confirmed, new_files = self.prompt_card_swap(
                            reader_path=reader_p,
                            target_dev_type=p2_type,
                            target_name=p2_name,
                            target_label=p2_label,
                            start_dt=start_dt,
                            end_dt=end_dt
                        )

                        if confirmed and not self.stop_requested.is_set():
                            if new_files:
                                with progress_lock:
                                    total_files_counter[0] += len(new_files)
                                    cur_t = total_files_counter[0]
                                    self.update_progress_bar(maximum=cur_t)

                                if p2_type == "dashcam":
                                    p2_dst = os.path.join(dashcam_target, today_mmdd, case_no)
                                else:
                                    folder_name = f"{case_no}{p2_name}" if p2_name else f"{case_no}"
                                    p2_dst = os.path.join(bodycam_target, today_mmdd, case_no, folder_name)
                                    case_root = os.path.join(bodycam_target, today_mmdd, case_no)
                                    if case_root not in opened_folders:
                                        opened_folders.append(case_root)

                                self.log(f"⚡ [共用讀卡機] 換卡成功，接續複製第二階段【{phase2_item['label']}】(共 {len(new_files)} 部影片)...")
                                copy_files_worker(new_files, p2_dst, p2_type)
                            else:
                                self.log(f"🟡 [共用讀卡機] 第二階段【{phase2_item['label']}】在時段內無影片 (0 部)。")
                                missing_reasons = self.app.config.get("missing_reasons", DEFAULT_MISSING_REASONS)
                                reason = missing_reasons[0] if missing_reasons else "未錄影"
                                empty_folder_reasons[p2_type] = reason

                                if p2_type == "dashcam":
                                    empty_dir = os.path.join(dashcam_target, today_mmdd, f"{case_no}({reason})")
                                else:
                                    folder_name = f"{case_no}{p2_name}({reason})" if p2_name else f"{case_no}({reason})"
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
                                        rf.write(f"設備名稱：{phase2_item['label']}\n")
                                        rf.write(f"執勤同仁：{p2_name if p2_name else '（無）'}\n")
                                        rf.write(f"建檔時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                                        rf.write(f"未錄影原因：{reason}\n")
                                        rf.write(f"說明備註：本梯次該設備因【{reason}】，於出勤時段內無影像錄製存檔，特此建立規範存查。\n")
                                        rf.write("========================================\n")
                                except:
                                    pass
                                self.log(f"📁 已為【{phase2_item['label']}】自動建立規範備註空資料夾：{os.path.basename(empty_dir)}")
                        else:
                            self.log(f"⚠️ 使用者已略過第二階段【{phase2_item['label']}】換卡作業。")

                t_reader = threading.Thread(target=card_reader_flow)
                t_reader.daemon = True
                worker_threads.append(t_reader)

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
        self.entry_case_no.delete(0, tk.END)
        self.on_case_no_changed()

        if hasattr(self, 'entry_year'):
            self.entry_year.delete(0, tk.END)
            self.entry_year.insert(0, str(now.year - 1911))

        if hasattr(self, 'entry_month_day'):
            self.entry_month_day.delete(0, tk.END)
            self.entry_month_day.insert(0, now.strftime("%m%d"))

        self.entry_start_time.delete(0, tk.END)
        self.entry_start_time.insert(0, (now - timedelta(hours=1)).strftime("%H%M"))

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


class VideoBackupApp:
    def __init__(self, root):
        self.root = root
        self.root.title("救護影像抓取小幫手 v3.9")
        self.root.geometry("750x920")
        self.root.minsize(720, 680)

        self.bg_color = "#F5F5F7"
        self.root.configure(bg=self.bg_color)

        self.next_tab_id = 1
        self.task_tabs = []
        self.roster_cache = {}

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

        # 🚀 啟動性能優化：非同步延遲 150ms 載入勤務表，讓視窗秒開，不卡主執行緒
        self.root.after(150, self.load_officer_names_from_roster)

    def on_app_close(self):
        """軟體關閉時自動儲存當前設定與任務同仁紀錄"""
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
            "roster_path": ""
        }
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    return {**default_cfg, **json.load(f)}
            except Exception:
                pass
        return default_cfg

    def save_config(self):
        try:
            # 1. 從設定分頁抓取最新設定 (含勤務表路徑與設備硬碟標籤)
            if hasattr(self, 'entry_roster_path'):
                self.config["roster_path"] = self.entry_roster_path.get().strip()
            if hasattr(self, 'entry_label_dashcam'):
                self.config["label_dashcam"] = self.entry_label_dashcam.get().strip()
                self.config["label_bodycam1"] = self.entry_label_b1.get().strip()
                self.config["label_bodycam2"] = self.entry_label_b2.get().strip()
                self.config["label_bodycam3"] = self.entry_label_b3.get().strip()
            if hasattr(self, 'txt_missing_reasons'):
                raw_r = self.txt_missing_reasons.get("1.0", tk.END)
                r_list = [line.strip() for line in raw_r.splitlines() if line.strip()]
                if r_list:
                    self.config["missing_reasons"] = r_list

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

    def setup_settings_tab(self, parent):
        pad = {'padx': 12, 'pady': 6}

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

        # 需求 9: 快速設定頁面常態包含「密錄器 3 硬碟名稱」
        frame_settings = ttk.LabelFrame(parent, text="【快速設定】各設備對應硬碟標籤名稱 (Volume Label)", padding=10)
        frame_settings.pack(fill="x", **pad)

        f_s1 = ttk.Frame(frame_settings)
        f_s1.pack(fill="x", pady=3)
        ttk.Label(f_s1, text="行車紀錄器硬碟名稱：", width=18).pack(side="left")
        self.entry_label_dashcam = ttk.Entry(f_s1, width=25)
        self.entry_label_dashcam.insert(0, self.config.get("label_dashcam", "DASHCAM"))
        self.entry_label_dashcam.pack(side="left", padx=4)
        ttk.Label(f_s1, text="例: DASHCAM 或 TRANSCEND", foreground="#666666").pack(side="left", padx=4)

        f_s2 = ttk.Frame(frame_settings)
        f_s2.pack(fill="x", pady=3)
        ttk.Label(f_s2, text="密錄器 1 硬碟名稱：", width=18).pack(side="left")
        self.entry_label_b1 = ttk.Entry(f_s2, width=25)
        self.entry_label_b1.insert(0, self.config.get("label_bodycam1", "BODYCAM1"))
        self.entry_label_b1.pack(side="left", padx=4)
        ttk.Label(f_s2, text="例: BODYCAM1 或 CAM1", foreground="#666666").pack(side="left", padx=4)

        f_s3 = ttk.Frame(frame_settings)
        f_s3.pack(fill="x", pady=3)
        ttk.Label(f_s3, text="密錄器 2 硬碟名稱：", width=18).pack(side="left")
        self.entry_label_b2 = ttk.Entry(f_s3, width=25)
        self.entry_label_b2.insert(0, self.config.get("label_bodycam2", "BODYCAM2"))
        self.entry_label_b2.pack(side="left", padx=4)
        ttk.Label(f_s3, text="例: BODYCAM2 或 CAM2", foreground="#666666").pack(side="left", padx=4)

        f_s4 = ttk.Frame(frame_settings)
        f_s4.pack(fill="x", pady=3)
        ttk.Label(f_s4, text="密錄器 3 硬碟名稱：", width=18).pack(side="left")
        self.entry_label_b3 = ttk.Entry(f_s4, width=25)
        self.entry_label_b3.insert(0, self.config.get("label_bodycam3", "BODYCAM3"))
        self.entry_label_b3.pack(side="left", padx=4)
        ttk.Label(f_s4, text="例: BODYCAM3 或 CAM3", foreground="#666666").pack(side="left", padx=4)

        # 需求 1: 「💾 儲存所有設定」獨立移至所有設定下方，明確涵蓋當月勤務表路徑與設備標籤名稱
                # 需求：未錄影空資料夾備註原因自訂清單
        frame_reasons = ttk.LabelFrame(parent, text="【未錄影空資料夾備註原因設定】(每行一筆，供預檢視窗 0 部影片時秒選)", padding=10)
        frame_reasons.pack(fill="x", **pad)

        ttk.Label(frame_reasons, text="請在下方文字框自由增減常見原因（每行代表一個選項）：", foreground="#555555").pack(anchor="w", pady=(0, 4))
        self.txt_missing_reasons = tk.Text(frame_reasons, height=4, font=("Helvetica", 10), bg="#FFFFFF", fg="#222222", relief="solid", bd=1)
        self.txt_missing_reasons.pack(fill="x", pady=2)
        reasons_list = self.config.get("missing_reasons", DEFAULT_MISSING_REASONS)
        self.txt_missing_reasons.insert("1.0", "\n".join(reasons_list))

        frame_save_bar = ttk.Frame(parent)
        frame_save_bar.pack(fill="x", padx=12, pady=(6, 8))

        self.btn_save_cfg = ttk.Button(
            frame_save_bar,
            text="💾 儲存所有設定 (含當月勤務表路徑、設備硬碟標籤與未錄影原因)",
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

        frame_detect = ttk.LabelFrame(parent, text="【診斷工具】當前已連接之硬碟與隨身碟清單", padding=10)
        frame_detect.pack(fill="both", expand=True, **pad)

        ttk.Button(frame_detect, text="🔍 重新整理並查看當前所有隨身碟名稱", command=self.refresh_disk_list).pack(anchor="w", pady=(0, 4))

        self.txt_disks = tk.Text(frame_detect, height=7, font=("Courier", 10), bg="#FFFFFF", fg="#222222", relief="solid", bd=1)
        self.txt_disks.pack(fill="both", expand=True)

        self.refresh_disk_list()

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
        dir_selected = filedialog.askdirectory(title="選取存放當月勤務表之資料夾 (內含 分隊-月份日期.xlsm)")
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

    def refresh_disk_list(self):
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

        roster_p = self.config.get("roster_path", "")
        roster_name = os.path.basename(roster_p) if roster_p else "未指定"
        if hasattr(self, 'lbl_save_status'):
            self.lbl_save_status.config(
                text=f"✅ 設定已儲存！(勤務表: {roster_name}，下拉選單與未錄影原因已同步更新)",
                foreground="#2E7D32"
            )

        messagebox.showinfo(
            "設定成功",
            "✅ 所有設定已成功儲存！\n\n"
            f"• 當月勤務表目錄：{roster_p if roster_p else '（未指定）'}\n"
            "• 設備隨身碟標籤：已更新記憶 (Volume Label 雙重驗證)\n"
            "• 未錄影備註原因：已更新自訂清單\n"
            "• 同仁姓名下拉選單：已同步更新最新名冊\n\n"
            "返回【主要備份作業】分頁即可直接使用。"
        )


if __name__ == "__main__":
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
