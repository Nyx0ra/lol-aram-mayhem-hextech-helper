import time
import json
import csv
import os
import threading
import queue
import tkinter as tk
import ctypes
import msvcrt  # 用于清除输入缓冲区
import numpy as np
import cv2
import mss
import keyboard
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from thefuzz import process
from rapidocr_onnxruntime import RapidOCR

# ================= 配置与常量 =================

REGIONS = {
    "hex_1": {'top': 540, 'left': 650,  'width': 320, 'height': 60},
    "hex_2": {'top': 540, 'left': 1130, 'width': 320, 'height': 60},
    "hex_3": {'top': 540, 'left': 1600, 'width': 320, 'height': 60}
}

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
        self.pinyin_map = {}
        self.tier_map = {}
        # 动态获取 data 文件夹的绝对路径
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.data_dir = os.path.join(self.base_dir, 'data')
        self._load_data()

    def _load_data(self):
        print("--- 正在加载数据资源 ---")

        # 1. 加载强化符文等级映射 (修正路径)
        tier_file = os.path.join(self.data_dir, 'tiers.json')
        if os.path.exists(tier_file):
            try:
                with open(tier_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                tier_cn_map = {"silver": "白银", "gold": "黄金", "prismatic": "棱彩"}
                for en_tier, cn_tier in tier_cn_map.items():
                    if en_tier in data:
                        for name in data[en_tier]: 
                            self.tier_map[name] = cn_tier
            except Exception as e:
                print(f"⚠️ {tier_file} 加载异常: {e}")

        # 2. 加载英雄数据 (CSV) (修正路径)
        csv_path = os.path.join(self.data_dir, 'hero_augments.csv')
        if not os.path.exists(csv_path):
            print(f"❌ 错误: 找不到文件 {csv_path}")
            print(f"   请确认该文件位于: {self.data_dir}")
        else:
            try:
                encoding = 'utf-8-sig'
                try:
                    with open(csv_path, 'r', encoding=encoding) as f: f.read(100)
                except UnicodeDecodeError:
                    encoding = 'gbk'
                
                raw_hero_list = defaultdict(list)
                with open(csv_path, 'r', encoding=encoding) as f:
                    reader = csv.reader(f)
                    next(reader, None) # 跳过表头
                    for row in reader:
                        if len(row) < 4: continue
                        hero = row[0].strip()
                        try: rank = int(row[2])
                        except: rank = 999
                        aug = row[3].strip()
                        raw_hero_list[hero].append((rank, aug))
                
                # 构建查询字典
                for hero, aug_list in raw_hero_list.items():
                    aug_list.sort(key=lambda x: x[0])
                    counters = {"白银": 1, "黄金": 1, "棱彩": 1, "未知": 1}
                    h_dict = {}
                    for g_rank, name in aug_list:
                        tier = self.tier_map.get(name, "未知")
                        h_dict[name] = {
                            "g_rank": g_rank, 
                            "tier": tier, 
                            "t_rank": counters.get(tier, 1)
                        }
                        if tier in counters: counters[tier] += 1
                    self.hero_data[hero] = h_dict
                
                print(f"✅ 英雄数据加载完毕: 共 {len(self.hero_data)} 个英雄")
            except Exception as e:
                print(f"❌ CSV 读取严重失败: {e}")

        # 3. 加载拼音映射 (修正路径)
        pinyin_file = os.path.join(self.data_dir, 'pinyin_map.json')
        if os.path.exists(pinyin_file):
            try:
                with open(pinyin_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for cn, py in data.items():
                        self.pinyin_map[py] = cn
                        self.pinyin_map[cn] = cn
            except Exception as e:
                print(f"⚠️ {pinyin_file} 加载异常: {e}")
        
        print("-> 数据初始化完成")

    def search_hero(self, query):
        """英雄搜索逻辑 (增强模糊匹配)"""
        query = query.strip().lower()
        
        # 1. 尝试拼音/中文直接匹配
        matched_name = self.pinyin_map.get(query)
        
        # 2. 如果没找到，在数据Key中模糊搜索
        if not matched_name and self.hero_data:
            guess, score = process.extractOne(query, list(self.hero_data.keys()))
            if score > 60:
                matched_name = guess
                return matched_name, False # 非精确匹配，需要确认

        # 3. 名称修正：如果找到了名字但数据里没Key (如 称号 vs 本名)
        if matched_name and matched_name not in self.hero_data:
            real_name, score = process.extractOne(matched_name, list(self.hero_data.keys()))
            if score > 80:
                print(f"ℹ️ 自动修正映射: {matched_name} -> {real_name}")
                matched_name = real_name
            else:
                print(f"❌ 警告: 找到了 '{matched_name}' 但数据库无此英雄数据")
                return None, False

        if matched_name:
            return matched_name, True
            
        return None, False

# ================= 2. 图像分析 (Core Logic) =================

class GameAnalyzer:
    """负责 OCR 和 图像处理 (解决线程安全问题)"""
    def __init__(self, data_manager):
        self.dm = data_manager
        # OCR 引擎是线程安全的
        self.ocr = RapidOCR(use_angle_cls=False)
        # 线程局部存储：解决 mss 在多线程下的崩溃问题
        self._thread_local = threading.local()
        # 线程池
        self.executor = ThreadPoolExecutor(max_workers=3)

    @property
    def sct(self):
        """获取当前线程专用的 mss 实例"""
        if not hasattr(self._thread_local, "instance"):
            self._thread_local.instance = mss.mss()
        return self._thread_local.instance

    def capture_region(self, region):
        try:
            # 必须转换为 int，防止浮点数导致 mss 报错
            monitor = {
                "top": int(region["top"]),
                "left": int(region["left"]),
                "width": int(region["width"]),
                "height": int(region["height"]),
                "mon": 0
            }
            img = np.array(self.sct.grab(monitor))
            gray = cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
            h, w = gray.shape
            # 2倍上采样提高文字清晰度
            return cv2.resize(gray, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)
        except Exception as e:
            print(f"截图失败: {e}")
            return None

    def _process_single(self, key, hero_cn):
        try:
            region = REGIONS[key]
            img = self.capture_region(region)
            
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
                # 2. 模糊匹配
                match, score = process.extractOne(txt, list(hero_augments.keys()))
                if score > 50:
                    match_name = match

            if match_name:
                info = hero_augments[match_name]
                # 格式化显示内容
                res["text"] = f"【{match_name}】\n{info.get('tier','?')}(No.{info.get('t_rank','?')})\n总No.{info.get('g_rank','?')}"
                res["valid"] = True
                res["rank"] = info.get('g_rank', 999)
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
        
        futures = []
        for key in ["hex_1", "hex_2", "hex_3"]:
            futures.append(self.executor.submit(self._process_single, key, hero_cn))
        
        results = {}
        valid_matches = []
        
        for f in futures:
            try:
                data = f.result()
                results[data["key"]] = data
                if data.get("valid"): valid_matches.append(data)
            except Exception as e:
                print(f"并发任务异常: {e}")

        # 计算最优推荐
        if valid_matches:
            min_rank = min(item['rank'] for item in valid_matches)
            for item in valid_matches:
                if item['rank'] == min_rank:
                    results[item['key']]["highlight"] = True
        
        return results

# ================= 3. UI 界面 (View) =================

class OverlayApp:
    def __init__(self, root, queue):
        self.root = root
        self.queue = queue
        self.labels = {}
        self.hide_timer = None
        
        self._setup_window()
        self._setup_labels()
        
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
    def __init__(self, app_queue, data_manager, analyzer):
        super().__init__(daemon=True)
        self.queue = app_queue
        self.dm = data_manager
        self.analyzer = analyzer
        self.current_hero = None

    def run(self):
        while True:
            self.select_hero_phase()
            self.listening_phase()

    def flush_input(self):
        """核心修复：强制清空标准输入缓冲区"""
        while msvcrt.kbhit():
            msvcrt.getch()

    def select_hero_phase(self):
        self.queue.put({"cmd": "CLEAR"})
        self.show_console_window()
        
        time.sleep(0.1)
        os.system('cls')
        self.flush_input() # 清空残留按键

        print("=== ARAM 助手 (F8重新输入) ===")
        print(">>> 请输入英雄名称 (拼音/中文):")

        while True:
            try:
                self.flush_input() # 再次清空，确保输入干净
                raw = input("Input: ").strip()
            except EOFError: continue
            
            if not raw: continue
            
            name, exact = self.dm.search_hero(raw)
            if name:
                if not exact:
                    print(f"   猜你是: {name}? (Enter确认 / n重输)")
                    self.flush_input()
                    if input().strip().lower() == 'n':
                        continue
                
                self.current_hero = name
                print(f"✅ 锁定: {name}")
                print(">>> 切回游戏，按 [F6] 分析")
                
                self.queue.put({"cmd": "STATUS", "data": f"当前: {name}\n按 F6 分析"})
                self.hide_console_window()
                break
            else:
                print("❌ 未找到，请重试")

    def listening_phase(self):
        self.flush_input() # 清除确认时的回车键残留
        
        is_selecting = False
        print("(监听中... 按 F8 重置)")
        
        while not is_selecting:
            if keyboard.is_pressed('f6'):
                self.queue.put({"cmd": "STATUS", "data": "🔎 正在分析..."})
                
                # 在后台线程执行分析，不阻塞UI
                results = self.analyzer.analyze(self.current_hero)
                
                self.queue.put({"cmd": "UPDATE", "data": results})
                time.sleep(1) # 防抖

            if keyboard.is_pressed('f8'):
                is_selecting = True
                time.sleep(0.5) # 防止 F8 连击
            
            time.sleep(0.05)

    @staticmethod
    def show_console_window():
        try:
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            ctypes.windll.user32.ShowWindow(hwnd, 5) # SW_SHOW
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except: pass

    @staticmethod
    def hide_console_window():
        try:
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            ctypes.windll.user32.ShowWindow(hwnd, 6) # SW_MINIMIZE (保持最小化比完全隐藏更安全)
        except: pass

# ================= 5. 主入口 =================

def main():
    # 【关键修复】强制设置工作目录为脚本所在目录
    # 解决快捷方式或不同终端启动导致找不到文件的问题
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    print(f"Working Directory: {script_dir}")

    # 1. 初始化核心数据与逻辑
    dm = DataManager()
    
    if not dm.hero_data:
        print("❌ 警告: 未加载到任何英雄数据，请检查CSV文件。")
        input("按任意键退出...")
        return

    analyzer = GameAnalyzer(dm)
    
    # 2. 初始化 UI 与 通信队列
    root = tk.Tk()
    msg_queue = queue.Queue()
    app = OverlayApp(root, msg_queue)
    
    # 3. 启动后台控制线程
    controller = InputController(msg_queue, dm, analyzer)
    controller.start()
    
    # 4. 进入 UI 主循环
    print("程序已启动...")
    try:
        root.mainloop()
    except KeyboardInterrupt:
        os._exit(0)

if __name__ == "__main__":
    main()