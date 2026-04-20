import time
import json
import csv
import os
import sys
import threading
import queue
import tkinter as tk
import ctypes
import msvcrt  # 用于清除输入缓冲区
import numpy as np
from PIL import Image
import mss
import keyboard
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from thefuzz import process, fuzz
from scripts.config import BASE_DIR, DATA_DIR
from scripts.lcu_connector import LCUConnector
from rapidocr_onnxruntime import RapidOCR

# ================= 配置与常量 =================

def get_regions():
    """根据当前屏幕分辨率动态计算海克斯文字截取区域 (以 2K 2560x1440 为基准等比缩放)"""
    with mss.mss() as sct:
        mon = sct.monitors[1]  # 主显示器
        W, H = mon['width'], mon['height']
    return {
        "hex_1": {'top': int(H * 0.375),  'left': int(W * 0.2539), 'width': int(W * 0.125), 'height': int(H * 0.0417)},
        "hex_2": {'top': int(H * 0.375),  'left': int(W * 0.4414), 'width': int(W * 0.125), 'height': int(H * 0.0417)},
        "hex_3": {'top': int(H * 0.375),  'left': int(W * 0.625),  'width': int(W * 0.125), 'height': int(H * 0.0417)},
    }

REGIONS = get_regions()

COLORS = {
    "normal": "#00FF00",  # 绿色
    "best":   "#FFD700",  # 金色
    "status": "yellow",   # 黄色
    "error":  "#FF3333",  # 红色
    "bg":     "#000000"   # 背景黑
}

# ================= 1. 数据管理 (Model) =================

class DataManager:
    """负责加载和管理静态数据"""
    def __init__(self):
        self.hero_data = {}
        # 拼音映射改为 defaultdict(list)，支持一个拼音对应多个英雄
        self.pinyin_map = defaultdict(list)

        self.base_dir = BASE_DIR
        self.data_dir = DATA_DIR
        self._load_data()

    def _load_data(self):
        print("--- 正在加载数据资源 ---")



        # 2. 加载英雄数据 (CSV)
        csv_path = os.path.join(self.data_dir, 'hero_augments.csv')
        if not os.path.exists(csv_path):
            print(f"❌ 错误: 找不到文件 {csv_path}")
            print(f"   请确认该文件位于: {self.data_dir}")
        else:
            try:
                encoding = 'utf-8-sig'
                
                raw_hero_list = defaultdict(list)
                with open(csv_path, 'r', encoding=encoding) as f:
                    reader = csv.reader(f)
                    header = next(reader, None) # 跳过表头
                    is_new_format = header and "等级" in header
                    has_overall_rank = header and "总排名" in header
                    
                    for row in reader:
                        if not row: continue
                        hero = row[0].strip()
                        
                        if has_overall_rank and len(row) >= 6:
                            # 最新格式: 中文名,英文名,等级,总排名,等级内序号,海克斯名称
                            tier = row[2].strip()
                            try: overall_rank = int(row[3])
                            except (ValueError, IndexError): overall_rank = 999
                            try: t_rank = int(row[4])
                            except (ValueError, IndexError): t_rank = 999
                            name = row[5].strip()
                            
                            if hero not in self.hero_data: self.hero_data[hero] = {}
                            self.hero_data[hero][name] = {
                                "tier": tier,
                                "overall_rank": overall_rank,
                                "t_rank": t_rank
                            }
                        elif is_new_format and len(row) >= 5:
                            # 旧新格式: 中文名,英文名,等级,等级内序号,海克斯名称 (无总排名)
                            tier = row[2].strip()
                            try: t_rank = int(row[3])
                            except (ValueError, IndexError): t_rank = 999
                            name = row[4].strip()
                            
                            if hero not in self.hero_data: self.hero_data[hero] = {}
                            self.hero_data[hero][name] = {
                                "tier": tier,
                                "overall_rank": 999,
                                "t_rank": t_rank
                            }
                        elif not is_new_format and len(row) >= 4:
                            try: rank = int(row[2])
                            except (ValueError, IndexError): rank = 999
                            aug = row[3].strip()
                            raw_hero_list[hero].append((rank, aug))
                
                # 如果存在旧格式的数据，走旧的合并逻辑
                if raw_hero_list:
                    for hero, aug_list in raw_hero_list.items():
                        if hero in self.hero_data: continue # 跳过已被新格式处理的
                        aug_list.sort(key=lambda x: x[0])
                        counters = {"白银": 1, "黄金": 1, "棱彩": 1, "未知": 1}
                        h_dict = {}
                        for rank, name in aug_list:
                            tier = "未知"
                            h_dict[name] = {
                                "tier": tier, 
                                "overall_rank": 999,
                                "t_rank": counters.get(tier, 1)
                            }
                            if tier in counters: counters[tier] += 1
                        self.hero_data[hero] = h_dict
                
                print(f"✅ 英雄数据加载完毕: 共 {len(self.hero_data)} 个英雄")
            except Exception as e:
                print(f"❌ CSV 读取严重失败: {e}")

        # 3. 加载拼音映射 (构建一对多关系)
        pinyin_file = os.path.join(self.data_dir, 'pinyin_map.json')
        if os.path.exists(pinyin_file):
            try:
                with open(pinyin_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for cn, py in data.items():
                        if cn not in self.pinyin_map[py]:
                            self.pinyin_map[py].append(cn)
                        if cn not in self.pinyin_map[cn]:
                            self.pinyin_map[cn].append(cn)
            except Exception as e:
                print(f"⚠️ {pinyin_file} 加载异常: {e}")
        
        print("-> 数据初始化完成")

    def search_hero(self, query):
        """
        英雄搜索逻辑 (增强模糊匹配)
        返回: (匹配列表, 是否精确匹配)
        """
        query = query.strip().lower()
        
        # 1. 尝试拼音/中文直接匹配 (O(1))，返回的是一个列表
        if query in self.pinyin_map:
            return self.pinyin_map[query], True
        
        # 2. 如果没找到，在数据Key中模糊搜索
        if self.hero_data:
            result = process.extractOne(query, list(self.hero_data.keys()))
            if result and result[1] > 60:
                return [result[0]], False

        return [], False

    def validate_hero(self, name, threshold=80):
        """验证英雄名是否在数据库中，尝试模糊映射"""
        if name in self.hero_data:
            return name
        if not self.hero_data:
            return None
        result = process.extractOne(name, list(self.hero_data.keys()))
        if result and result[1] > threshold:
            return result[0]
        return None

# ================= 2. 图像分析 (Core Logic) =================

class GameAnalyzer:
    """负责 OCR 和 图像处理"""
    TIER_PRIORITY = {"棱彩": 0, "黄金": 1, "白银": 2, "未知": 3}

    def __init__(self, data_manager):
        self.dm = data_manager
        # OCR 引擎: 降低 det_limit_side_len (默认736→480)
        # 截取区域 2x 上采样后最大 640px, 480 足以覆盖, 减少检测模型不必要计算
        try:
            self.ocr = RapidOCR(use_angle_cls=False, det_limit_side_len=480)
        except (KeyError, TypeError, Exception) as e:
            py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
            print(f"\n❌ OCR 引擎初始化失败: {e}")
            print(f"   当前 Python 版本: {py_ver}")
            if sys.version_info >= (3, 13):
                print(f"   ⚠ rapidocr_onnxruntime 仅支持 Python <3.13，请使用 Python 3.10~3.12")
            else:
                print(f"   请尝试: pip install rapidocr_onnxruntime<=1.4.4")
            raise
        # 根据 CPU 逻辑核心数决定并发策略
        self._cpu_count = os.cpu_count() or 4
        self._use_parallel = self._cpu_count >= 12
        self.executor = ThreadPoolExecutor(max_workers=3)
        # 预热 OCR 引擎 (消除首次推理的模型加载和内存分配延迟)
        self._warmup()

    def _warmup(self):
        """用小图预热 OCR 引擎, 消除首次 F6 的冷启动延迟"""
        try:
            dummy = np.zeros((48, 320), dtype=np.uint8)
            self.ocr(dummy)
            print(f"OCR 引擎预热完成 (CPU: {self._cpu_count} 线程, {'并发' if self._use_parallel else '串行'}模式)")
        except Exception:
            pass

    def capture_all_regions(self):
        """批量截图: 复用单个 mss 上下文, 避免重复初始化开销"""
        images = {}
        try:
            with mss.mss() as sct:
                for key, region in REGIONS.items():
                    monitor = {
                        "top": int(region["top"]),
                        "left": int(region["left"]),
                        "width": int(region["width"]),
                        "height": int(region["height"]),
                        "mon": 0
                    }
                    raw = sct.grab(monitor)
                    img = Image.frombytes("RGB", raw.size, raw.rgb)
                    gray = img.convert("L")
                    w, h = gray.size
                    # 2倍上采样提高文字清晰度
                    resized = gray.resize((w * 2, h * 2), Image.BICUBIC)
                    images[key] = np.array(resized)
        except Exception as e:
            print(f"批量截图失败: {e}")
        return images

    def _ocr_and_match(self, key, img, hero_cn):
        """对单张已截取的图片执行 OCR 识别 + 数据匹配"""
        try:
            if img is None:
                return {"key": key, "text": "截图错误", "error": True}

            res_ocr, _ = self.ocr(img)
            txt = "".join([line[1] for line in res_ocr]) if res_ocr else ""
            txt = txt.replace(" ", "").replace(".", "")

            res = {
                "key": key, "valid": False, "rank": 999, 
                "text": "", "highlight": False, "error": False
            }

            if not txt:
                res["text"] = "❌ 无文字"
                res["error"] = True
                return res

            hero_augments = self.dm.hero_data.get(hero_cn, {})
            if not hero_augments:
                res["text"] = "无数据"
                res["error"] = True
                return res

            match_name = None
            
            # 1. 精确匹配 (O(1))
            if txt in hero_augments:
                match_name = txt
            else:
                # 2. 模糊匹配 (使用精确比例 fuzz.ratio，避免子串过分匹配)
                match, score = process.extractOne(txt, list(hero_augments.keys()), scorer=fuzz.ratio)
                if score > 60:
                    match_name = match

            if match_name:
                info = hero_augments[match_name]
                tier = info.get('tier', '?')
                t_rank = info.get('t_rank', '?')
                overall_rank = info.get('overall_rank', '?')
                # 格式化显示内容: 方案A
                res["text"] = f"【{match_name}】\n总No.{overall_rank} | {tier} No.{t_rank}"
                res["valid"] = True
                res["tier"] = tier
                res["t_rank"] = info.get('t_rank', 999)
                res["overall_rank"] = info.get('overall_rank', 999)
            else:
                res["text"] = "❌ 未识别"
                res["error"] = True
            
            return res
            
        except Exception as e:
            print(f"处理异常 ({key}): {e}")
            return {"key": key, "text": "Error", "error": True}

    def analyze(self, hero_cn):
        if not hero_cn: return {}
        print(f"正在分析: {hero_cn}...")
        
        # 阶段1: 批量截图 (复用 mss 上下文, 总耗时 ~18ms)
        images = self.capture_all_regions()
        
        # 阶段2: OCR 识别 + 匹配 (自适应并发策略)
        results = {}
        valid_matches = []

        if self._use_parallel:
            # 高端 CPU (>=12 线程): 并发 OCR, 充分利用多核
            futures = []
            for key in images:
                futures.append(self.executor.submit(self._ocr_and_match, key, images[key], hero_cn))
            for f in futures:
                try:
                    data = f.result()
                    results[data["key"]] = data
                    if data.get("valid"): valid_matches.append(data)
                except Exception as e:
                    print(f"并发任务异常: {e}")
        else:
            # 低端 CPU (<12 线程): 串行 OCR, 避免缓存争抢和线程切换开销
            for key, img in images.items():
                data = self._ocr_and_match(key, img, hero_cn)
                results[data["key"]] = data
                if data.get("valid"): valid_matches.append(data)

        # 计算最优推荐：总排名优先（越小越好），总排名相同则按等级排序
        if valid_matches:
            def sort_key(item):
                o_rank = item.get('overall_rank', 999)
                tp = self.TIER_PRIORITY.get(item.get('tier', '未知'), 3)
                tr = item.get('t_rank', 999)
                return (o_rank, tp, tr)
            
            best = min(valid_matches, key=sort_key)
            best_key = sort_key(best)
            for item in valid_matches:
                if sort_key(item) == best_key:
                    results[item['key']]["highlight"] = True
        
        return results

# ================= 3. UI 界面 (View) =================

class OverlayApp:
    def __init__(self, root, queue):
        self.root = root
        self.queue = queue
        self.labels = {}
        self.hide_timer = None
        
        # 先隐藏窗口，避免配置透明前闪白框
        self.root.withdraw()
        self._setup_window()
        self._setup_labels()
        self.root.deiconify()
        
        # 启动队列消息监听
        self.root.after(100, self.process_queue)

    def _setup_window(self):
        self.root.title("ARAM Overlay")
        self.root.overrideredirect(True) # 无边框
        self.root.attributes("-topmost", True) # 置顶
        self.root.config(bg=COLORS["bg"])
        self.root.attributes("-transparentcolor", COLORS["bg"]) # 背景透明
        
        # 鼠标穿透设置 (Windows API)
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            old_style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            # WS_EX_LAYERED | WS_EX_TRANSPARENT
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, old_style | 0x80000 | 0x20)
        except Exception as e:
            print(f"穿透设置警告: {e}")

        # 获取主屏幕坐标，用于相对定位
        with mss.mss() as sct:
            m = sct.monitors[0]
            self.offset_x, self.offset_y = m['left'], m['top']
            self.root.geometry(f"{m['width']}x{m['height']}+{m['left']}+{m['top']}")

    def _setup_labels(self):
        font_style = ("Microsoft YaHei", 14, "bold")
        for key in REGIONS:
            lbl = tk.Label(self.root, text="", font=font_style, bg=COLORS["bg"], justify="left")
            self.labels[key] = lbl

    def process_queue(self):
        """主线程轮询：处理来自后台线程的指令"""
        try:
            while True:
                msg = self.queue.get_nowait()
                cmd = msg.get("cmd")
                data = msg.get("data")
                
                if cmd == "UPDATE":
                    self.update_display(data)
                elif cmd == "STATUS":
                    self.show_status(data)
                elif cmd == "CLEAR":
                    self.clear_display()
        except queue.Empty:
            pass
        finally:
            self.root.after(50, self.process_queue)

    def clear_display(self):
        if self.hide_timer:
            self.root.after_cancel(self.hide_timer)
            self.hide_timer = None
        for lbl in self.labels.values():
            lbl.place_forget()

    def show_status(self, text):
        self.clear_display()
        lbl = self.labels['hex_2']
        lbl.config(text=text, fg=COLORS["status"])
        lbl.place(relx=0.5, rely=0.5, anchor="center")
        # 状态提示2秒后消失
        self.hide_timer = self.root.after(2000, self.clear_display)

    def update_display(self, results):
        self.clear_display()
        
        # 强制对齐 Y 轴
        base_y_abs = REGIONS['hex_1']['top']
        fixed_rel_y = base_y_abs - self.offset_y - 120

        for key, info in results.items():
            if not info.get("text"): continue
            
            lbl = self.labels[key]
            # 颜色逻辑
            if info["error"]:
                fg = COLORS["error"]
            elif info["highlight"]:
                fg = COLORS["best"]
            else:
                fg = COLORS["normal"]
            
            lbl.config(text=info["text"], fg=fg)
            
            r_left = REGIONS[key]['left'] - self.offset_x
            lbl.place(x=r_left, y=fixed_rel_y, anchor="nw")
            lbl.lift()

        # 结果显示5秒后消失
        self.hide_timer = self.root.after(5000, self.clear_display)

# ================= 4. 控制逻辑 (Controller) =================

class InputController(threading.Thread):
    def __init__(self, app_queue, data_manager, analyzer, lcu_connector=None):
        super().__init__(daemon=True)
        self.queue = app_queue
        self.dm = data_manager
        self.analyzer = analyzer
        self.lcu = lcu_connector
        self.current_hero = None
        self._last_f6 = 0
        self._last_f7 = 0
        self._last_f8 = 0

    def run(self):
        while True:
            self.select_hero_phase()
            self.listening_phase()

    def flush_input(self):
        """强制清空标准输入缓冲区"""
        while msvcrt.kbhit():
            msvcrt.getch()

    def _validate_hero(self, name):
        """验证英雄名是否在数据库中，尝试模糊映射"""
        return self.dm.validate_hero(name)

    def _try_auto_detect(self):
        """使用 LCU 统一接口自动获取英雄，返回 (英雄中文名|None, 来源)"""
        if not self.lcu:
            return None, ""
        hero, source = self.lcu.get_champion_auto()
        if hero:
            validated = self._validate_hero(hero)
            if validated:
                return validated, source
        return None, source

    # ==========================================
    # 阶段1: 选择英雄 (自动轮询 + 手动备用)
    # ==========================================

    def select_hero_phase(self):
        self.queue.put({"cmd": "CLEAR"})
        self.show_console_window()

        time.sleep(0.1)
        os.system('cls')
        self.flush_input()

        print("=== ARAM Hextech Helper ===")
        print("    F6=分析 | F7=刷新英雄 | F8=手动输入\n")

        # ====== 尝试自动检测 (轮询最多30秒) ======
        if self.lcu:
            print("[Auto] 正在连接英雄联盟客户端...")

            for attempt in range(15):  # 每2秒检测，共30秒
                hero, source = self._try_auto_detect()
                if hero:
                    self.current_hero = hero
                    print(f"\n>>> 自动识别到英雄: [{hero}] (数据源: {source})")
                    print(f">>> F6=分析 | F7=刷新 | F8=手动")
                    self.queue.put({"cmd": "STATUS", "data": f"当前: {hero}\n按 F6 分析 | F7 刷新"})
                    self.hide_console_window()
                    return

                # F8 跳过自动检测并手动输入
                if keyboard.is_pressed('f8'):
                    print("\n[F8] 切换至手动输入...")
                    time.sleep(0.5)
                    break

                dots = "." * ((attempt % 3) + 1)
                print(f"\r[Auto] 等待选取英雄{dots}   ", end="", flush=True)
                time.sleep(2)
            else:
                # 30秒后没搜到，不锁在死循环里，直接进入监听模式
                print("\n[Auto] 暂未自动识别到英雄。将切入后台继续运行。")
                print(">>> 随时按 [F7] 重新获取，或按 [F8] 呼出控制台手动输入。\n")
                self.current_hero = None
                self.queue.put({"cmd": "STATUS", "data": "暂无英雄\n按 F7 自动获取本局英雄"})
                self.hide_console_window()
                return

        # ====== 手动输入 (仅在按下 F8 时，或 LCU 完全异常时进入) ======
        print(">>> 请输入英雄名称 (拼音/中文):")

        while True:
            try:
                self.flush_input()
                raw = input("Input: ").strip()
            except EOFError:
                continue
            if not raw:
                continue

            matches, is_exact = self.dm.search_hero(raw)
            selected_name = None

            if not matches:
                print("❌ 未找到，请重试")
                continue

            if len(matches) > 1:
                print(f"🤔 发现多个匹配项，请选择:")
                for idx, name in enumerate(matches):
                    print(f"   {idx + 1}. {name}")
                print(">>> 请输入序号:")
                self.flush_input()
                try:
                    idx = int(input("Select: ").strip()) - 1
                    if 0 <= idx < len(matches):
                        selected_name = matches[idx]
                    else:
                        print("无效选项，请重试")
                        continue
                except ValueError:
                    print("无效输入，请重试")
                    continue
            else:
                candidate = matches[0]
                if is_exact:
                    selected_name = candidate
                else:
                    print(f"   猜你是: {candidate}? (Enter确认 / n重输)")
                    self.flush_input()
                    if input().strip().lower() == 'n':
                        continue
                    selected_name = candidate

            if selected_name:
                validated = self._validate_hero(selected_name)
                if not validated:
                    print(f"数据库暂无【{selected_name}】的数据")
                    continue
                self.current_hero = validated
                print(f">>> 已锁定: {validated}")
                print(f">>> F6=分析 | F7=刷新 | F8=手动")
                self.queue.put({"cmd": "STATUS", "data": f"当前: {validated}\n按 F6 分析 | F7 刷新"})
                self.hide_console_window()
                break

    # ==========================================
    # 阶段2: 监听热键
    # ==========================================

    def listening_phase(self):
        self.flush_input()
        print(f"[监听中...] 当前英雄: {self.current_hero} | F6分析 / F7刷新 / F8手动")

        while True:
            now = time.time()

            if keyboard.is_pressed('f6') and now - self._last_f6 > 1.0:
                self._last_f6 = now
                if not self.current_hero:
                    self.queue.put({"cmd": "STATUS", "data": "⚠ 尚未锁定英雄\n请按 F7 自动获取或 F8 手动输入"})
                    continue
                
                self.queue.put({"cmd": "STATUS", "data": f"🔎 正在分析 [{self.current_hero}]..."})
                results = self.analyzer.analyze(self.current_hero)
                self.queue.put({"cmd": "UPDATE", "data": results})

            if keyboard.is_pressed('f7') and now - self._last_f7 > 1.0:
                self._last_f7 = now
                # F7: 全阶段刷新英雄 (ChampSelect / InProgress / LiveAPI)
                self.queue.put({"cmd": "STATUS", "data": "刷新英雄..."})
                hero, source = self._try_auto_detect()
                if hero and hero != self.current_hero:
                    old = self.current_hero
                    self.current_hero = hero
                    print(f">>> 英雄已切换 ({source}): {old} -> {hero}")
                    self.queue.put({"cmd": "STATUS", "data": f"已切换: {hero}\n按 F6 分析"})
                elif hero:
                    self.queue.put({"cmd": "STATUS", "data": f"当前英雄: {hero}\n按 F6 分析"})
                else:
                    self.queue.put({"cmd": "STATUS", "data": f"当前: {self.current_hero}\n按 F6 分析"})

            if keyboard.is_pressed('f8') and now - self._last_f8 > 1.0:
                self._last_f8 = now
                time.sleep(0.5)
                return  # 退出监听，回到 select_hero_phase

            time.sleep(0.05)


    @staticmethod
    def show_console_window():
        try:
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            ctypes.windll.user32.ShowWindow(hwnd, 5)  # SW_SHOW
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    @staticmethod
    def hide_console_window():
        try:
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
        except Exception:
            pass

# ================= 5. 主入口 =================

def main():
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)
    
    # 强制设置工作目录为应用根目录 (兼容打包)
    os.chdir(BASE_DIR)
    os.system('title ARAM 海克斯助手')
    os.system('chcp 65001 >nul')
    print(f"Working Directory: {BASE_DIR}")

    # 1. 初始化核心数据与逻辑
    dm = DataManager()
    
    if not dm.hero_data:
        print("❌ 警告: 未加载到任何英雄数据，请检查CSV文件。")
        input("按任意键退出...")
        return

    analyzer = GameAnalyzer(dm)
    
    # 2. 初始化 LCU 客户端连接器
    champions_json = os.path.join(dm.data_dir, 'champions.json')
    lcu = LCUConnector(champions_json)
    
    # 3. 初始化 UI 与 通信队列
    root = tk.Tk()
    msg_queue = queue.Queue()
    app = OverlayApp(root, msg_queue)
    
    # 4. 启动后台控制线程
    controller = InputController(msg_queue, dm, analyzer, lcu_connector=lcu)
    controller.start()
    
    # 4. 进入 UI 主循环
    print("程序已启动...")
    try:
        root.mainloop()
    except KeyboardInterrupt:
        os._exit(0)

if __name__ == "__main__":
    main()