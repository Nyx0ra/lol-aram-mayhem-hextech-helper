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
import cv2
from PIL import Image
import mss
import keyboard
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from thefuzz import process, fuzz
from scripts.config import BASE_DIR, DATA_DIR
from scripts.lcu_connector import LCUConnector, SessionPoller
from scripts.overlay_inbox import OverlayInbox
from scripts.window_layout import (
    CARD_LEFTS, ScreenRect, configure_overlay, enable_per_monitor_dpi,
    get_game_layout, position_overlay,
)
from rapidocr_onnxruntime import RapidOCR

# ================= 配置与常量 =================

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

    def compute_thumbnails(self, images, size=(32, 10)):
        """将截图区域降采样为微型灰度特征矩阵，用于毫秒级特征检测与指纹比对 (OpenCV加速)"""
        thumbs = {}
        for k, img_arr in images.items():
            if img_arr is None:
                continue
            small = cv2.resize(img_arr, size, interpolation=cv2.INTER_AREA)
            thumbs[k] = small.astype(np.float32)
        return thumbs

    def check_visual_gate(self, curr_thumbs, prev_thumbs=None, stability_thresh=8.0):
        """
        轻量级视觉门控 (<2ms):
        1. 帧间稳定度: 海克斯选择界面是静态弹窗，画面剧烈变动时不触发 OCR
        2. 文字对比度与梯度: 文字区域具有明显的边缘交替，纯黑/纯色/地图背景将被过滤
        返回 (passed: bool, reason: str)
        """
        if not curr_thumbs or len(curr_thumbs) < 3:
            return False, "insufficient_regions"

        # 1. 帧间稳定度检查
        if prev_thumbs:
            diffs = []
            for k in curr_thumbs:
                if k in prev_thumbs:
                    diffs.append(np.mean(np.abs(curr_thumbs[k] - prev_thumbs[k])))
            if diffs:
                avg_diff = float(np.mean(diffs))
                # 画面变化过大 (例如处于移动走位或团战动画中)，尚未稳定
                if avg_diff > stability_thresh:
                    return False, f"moving (diff={avg_diff:.1f})"

        # 2. 空间文字特征检查 (标准差与水平笔画梯度)
        valid_regions = 0
        for k, th in curr_thumbs.items():
            std_val = float(np.std(th))
            mean_val = float(np.mean(th))
            # 太黑 (<15) 或太亮 (>245) 均非正常卡牌标题区
            if mean_val < 15 or mean_val > 245:
                continue
            # 水平方向相邻像素梯度 (反映文字笔画交替)
            grad_h = float(np.mean(np.abs(th[:, 1:] - th[:, :-1])))
            if std_val >= 10.0 and grad_h >= 2.5:
                valid_regions += 1

        # 至少2个区域呈现文字对比度特征
        if valid_regions >= 2:
            return True, "passed"
        return False, f"low_contrast ({valid_regions}/3 regions)"

    def is_same_thumbs(self, thumbs_a, thumbs_b, threshold=5.0):
        """比对两组缩略图指纹，返回 (是否完全一致, 发生变化的键列表)"""
        if not thumbs_a or not thumbs_b:
            return False, list(thumbs_a.keys() if thumbs_a else [])
        diff_keys = []
        for k in thumbs_a:
            if k not in thumbs_b:
                diff_keys.append(k)
                continue
            diff = float(np.mean(np.abs(thumbs_a[k] - thumbs_b[k])))
            if diff > threshold:
                diff_keys.append(k)
        return len(diff_keys) == 0, diff_keys

    def capture_all_regions(self, target_keys=None):
        return self.capture_window_frame(target_keys)[1]

    @staticmethod
    def capture_window_frame(target_keys=None):
        """Capture title slices and return the layout used for those pixels."""
        layout = get_game_layout()
        if layout is None:
            return None, {}
        images = {}
        try:
            with mss.mss() as sct:
                for key in CARD_LEFTS if target_keys is None else target_keys:
                    r = layout.title_region(key)
                    raw = sct.grab((r.left, r.top, r.left + r.width, r.top + r.height))
                    bgra = np.frombuffer(raw.raw, dtype=np.uint8).reshape((raw.height, raw.width, 4))
                    images[key] = GameAnalyzer.prepare_title(bgra)
        except Exception as e:
            print(f"批量截图失败: {e}")
            return None, {}
        if get_game_layout() != layout:
            return None, {}
        return layout, images

    @staticmethod
    def prepare_title(bgra):
        gray = cv2.cvtColor(bgra, cv2.COLOR_BGRA2GRAY)
        return cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_LINEAR)

    def manual_update(self, hero):
        capture_started = time.perf_counter()
        layout, images = self.capture_window_frame()
        if layout is None:
            return {"cmd": "STATUS", "data": "请将游戏窗口切到前台"}
        captured = time.perf_counter()
        results = self.analyze(hero, images=images)
        return {"cmd": "UPDATE", "data": results, "layout": layout, "duration": 8000,
                "timing": ("manual", capture_started, captured, time.perf_counter())}

    def _ocr_and_match(self, key, img, hero_cn):
        """对单张已截取的图片执行 OCR 识别 + 数据匹配"""
        try:
            if img is None:
                return {"key": key, "text": "截图错误", "error": True, "score": 0}

            res_ocr, _ = self.ocr(img)
            txt = "".join([line[1] for line in res_ocr]) if res_ocr else ""
            txt = txt.replace(" ", "").replace(".", "")

            res = {
                "key": key, "valid": False, "rank": 999, 
                "text": "", "highlight": False, "error": False, "score": 0
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
            match_score = 0
            
            # 1. 精确匹配 (O(1))
            if txt in hero_augments:
                match_name = txt
                match_score = 100
            else:
                # 2. 模糊匹配 (使用精确比例 fuzz.ratio，避免子串过分匹配)
                match, score = process.extractOne(txt, list(hero_augments.keys()), scorer=fuzz.ratio)
                if score > 60:
                    match_name = match
                    match_score = score

            if match_name:
                info = hero_augments[match_name]
                tier = info.get('tier', '?')
                t_rank = info.get('t_rank', '?')
                overall_rank = info.get('overall_rank', '?')
                # 格式化显示内容: 方案A
                res["text"] = f"【{match_name}】\n总No.{overall_rank} | {tier} No.{t_rank}"
                res["valid"] = True
                res["tier"] = tier
                res["score"] = match_score
                res["match_name"] = match_name
                res["t_rank"] = info.get('t_rank', 999)
                res["overall_rank"] = info.get('overall_rank', 999)
            else:
                res["text"] = "❌ 未识别"
                res["error"] = True
            
            return res
            
        except Exception as e:
            print(f"处理异常 ({key}): {e}")
            return {"key": key, "text": "Error", "error": True, "score": 0}

    def analyze(self, hero_cn, images=None):
        if not hero_cn: return {}
        print(f"正在分析: {hero_cn}...")
        
        # 阶段1: 批量截图 (若外部未提供则现场截图)
        if images is None:
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

    def analyze_partial(self, hero_cn, images, existing_results, target_keys):
        """
        局部增量分析 (用于单卡刷新 Reroll):
        仅对 target_keys 中的区域重新跑 OCR，其余卡牌复用已有结果，然后统一重新计算胜率排名。
        单卡刷新重算速度提升 300%！
        """
        if not hero_cn: return {}
        results = {key: item.copy() for key, item in existing_results.items()} if existing_results else {}
        for key in target_keys:
            img = images.get(key)
            if img is not None:
                results[key] = self._ocr_and_match(key, img, hero_cn)

        # 统一重新计算最优推荐
        valid_matches = [v for v in results.values() if v.get("valid")]
        for item in results.values():
            item["highlight"] = False

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

class PopupDetector:
    """Advance the opening and per-card refresh state once per captured frame."""

    def __init__(self, analyzer):
        self.analyzer = analyzer
        self.reset()

    def reset(self, opening=False):
        self.opening = opening
        self.layout = None
        self.fast_opening = opening
        self.first_sample_at = None
        self.previous = None
        self.negative = None
        self.retry_opening_at = 0.0
        self.confirmed = {}
        self.results = {}
        self.candidates = {}
        self.missing_frames = 0

    @property
    def interval(self):
        return 0.045 if self.fast_opening else 0.08 if self.candidates else 0.12

    @staticmethod
    def title_difference(current, previous):
        # Ignore overall brightness shifts and cap sparse particle outliers.
        delta = current.astype(np.float32) - previous
        delta -= np.median(delta)
        return float(np.mean(np.minimum(np.abs(delta), 24.0)))

    def sample_window(self, hero, now):
        capture_started = time.perf_counter()
        layout, images = self.analyzer.capture_window_frame()
        captured = time.perf_counter()
        changed = layout != self.layout
        if changed:
            self.reset(opening=self.opening)
            self.layout = layout
        if layout is not None:
            message = self.sample(hero, images, now)
            # CLEAR from the detector also resets its state. Keep the geometry
            # so the next frame can use the baseline established by _refresh.
            self.layout = layout
            if message:
                if message["cmd"] == "UPDATE":
                    kind = "reroll" if message.get("refresh_keys") else "popup"
                    message["timing"] = (kind, capture_started, captured, time.perf_counter())
                return {**message, "layout": layout}
        if changed:
            return {"cmd": "CLEAR"}
        return None

    def sample(self, hero, images, now):
        if self.first_sample_at is None:
            self.first_sample_at = now
        elif now - self.first_sample_at >= 12.0:
            self.fast_opening = False
        thumbs = self.analyzer.compute_thumbnails(images)
        if thumbs.keys() != CARD_LEFTS.keys():
            self.previous = None
            self.candidates.clear()
            self.missing_frames = 0
            return None

        previous, self.previous = self.previous, thumbs
        if self.confirmed:
            return self._refresh(hero, images, thumbs, now)

        # Use consecutive captured frames instead of sleeping and taking an
        # unchecked screenshot. The first frame only establishes a baseline.
        if previous is None or not self.analyzer.check_visual_gate(thumbs, previous)[0]:
            return None
        if self.negative is not None and now < self.retry_opening_at:
            if self.analyzer.is_same_thumbs(thumbs, self.negative, threshold=4.5)[0]:
                return None

        results = self.analyzer.analyze(hero, images=images)
        scores = [item.get("score", 0) for item in results.values() if item.get("valid")]
        if len(scores) >= 2 and sum(scores) / len(scores) >= 65:
            opening, self.opening = self.opening, False
            self.fast_opening = False
            self.results, self.confirmed = results, thumbs
            self.negative = None
            return {"cmd": "UPDATE", "data": results, "duration": 20000, "opening": opening}

        self.negative = thumbs
        retry_delay = 0.32 if self.fast_opening else 1.0
        self.retry_opening_at = now + retry_delay if self.opening else float("inf")
        return None

    def _refresh(self, hero, images, thumbs, now):
        changed = [key for key in thumbs if self.title_difference(thumbs[key], self.confirmed[key]) > 2.5]
        if len(changed) == len(CARD_LEFTS) or not self.analyzer.check_visual_gate(thumbs)[0]:
            self.candidates.clear()
            self.missing_frames += 1
            if self.missing_frames >= 2:
                self.reset()
                self.previous = thumbs
                return {"cmd": "CLEAR"}
            return None

        self.missing_frames = 0
        self.candidates = {key: state for key, state in self.candidates.items() if key in changed}
        refreshed = []
        for key in changed:
            frame = thumbs[key]
            anchor, stable_since, retry_at = self.candidates.get(key, (frame, now, now))
            if self.title_difference(frame, anchor) > 2.4:
                anchor, stable_since, retry_at = frame, now, now
            self.candidates[key] = (anchor, stable_since, retry_at)
            if now - stable_since < 0.075 or now < retry_at:
                continue

            results = self.analyzer.analyze_partial(hero, images, self.results, [key])
            item = results.get(key, {})
            if not item.get("valid") or item.get("score", 0) < 65:
                self.candidates[key] = (anchor, stable_since, now + 0.18)
                continue
            if item.get("match_name") == self.results.get(key, {}).get("match_name"):
                # Hover and intermediate animation can both read as the old
                # title. Keep the confirmed baseline and retry at a lower rate.
                self.candidates[key] = (anchor, stable_since, now + 0.45)
                continue

            self.results = results
            self.confirmed[key] = frame
            del self.candidates[key]
            refreshed.append(key)

        if refreshed:
            return {"cmd": "UPDATE", "data": self.results, "duration": 20000, "refresh_keys": refreshed}
        return None


# ================= 3. UI 界面 (View) =================

class OverlayApp:
    def __init__(self, root):
        self.root = root
        self.labels = {}
        self.hide_timer = None
        self.layout_timer = None
        self.layout = None
        self.latency_samples = defaultdict(lambda: deque(maxlen=100))
        
        # 先隐藏窗口，避免配置透明前闪白框
        self.root.withdraw()
        self._setup_window()
        self._setup_labels()
        self.root.deiconify()
        self.root.update_idletasks()
        self.hwnd = configure_overlay(self.root.winfo_id())
        
        self.queue = OverlayInbox(self.root, self.process_queue)

    def close(self):
        self.queue.close()
        self.clear_display()

    def _check_display_layout(self):
        self.layout_timer = None
        if self.layout is None:
            return
        if self.layout != get_game_layout():
            self.clear_display()
        else:
            self.layout_timer = self.root.after(50, self._check_display_layout)

    def _setup_window(self):
        self.root.title("ARAM Overlay")
        self.root.overrideredirect(True) # 无边框
        self.root.attributes("-topmost", True) # 置顶
        self.root.config(bg=COLORS["bg"])
        self.root.attributes("-transparentcolor", COLORS["bg"]) # 背景透明
        
    def _setup_labels(self):
        font_style = ("Microsoft YaHei", 14, "bold")
        for key in CARD_LEFTS:
            lbl = tk.Label(self.root, text="", font=font_style, bg=COLORS["bg"], justify="left")
            self.labels[key] = lbl

    def process_queue(self):
        """在 Tk 主线程消费窗口消息唤醒的命令。"""
        if self.layout is not None and self.layout != get_game_layout():
            self.clear_display()
        try:
            while True:
                msg = self.queue.get_nowait()
                cmd = msg.get("cmd")
                data = msg.get("data")
                
                if cmd == "UPDATE":
                    dequeued = time.perf_counter()
                    layout = msg.get("layout")
                    if layout is None or layout != get_game_layout():
                        continue
                    if self.layout != layout:
                        self.clear_display()
                        if not position_overlay(self.hwnd, layout.client):
                            continue
                        self.layout = layout
                    self.update_display(data, duration=msg.get("duration", 5000))
                    if msg.get("timing") and any(item.get("text") for item in data.values()):
                        # Flush Tk layout/redraw work. This is not a compositor
                        # or physical display timestamp.
                        self.root.update_idletasks()
                        self._report_latency(msg["timing"], dequeued, time.perf_counter())
                elif cmd == "STATUS":
                    duration = msg.get("duration", 2000)
                    self.show_status(data, duration=duration)
                elif cmd == "CLEAR":
                    self.clear_display()
        except queue.Empty:
            pass
        finally:
            # Only watch geometry while positioned over a game window.
            # This timer never reads or dispatches overlay commands.
            if self.layout is not None and self.layout_timer is None:
                self.layout_timer = self.root.after(50, self._check_display_layout)

    def clear_display(self):
        if self.layout_timer is not None:
            self.root.after_cancel(self.layout_timer)
            self.layout_timer = None
        if self.hide_timer:
            self.root.after_cancel(self.hide_timer)
            self.hide_timer = None
        for lbl in self.labels.values():
            lbl.place_forget()
        self.layout = None

    def _report_latency(self, timing, dequeued, rendered):
        kind, started, captured, ready = timing
        capture, recognition, queue_ms, ui, total = (
            (end - start) * 1000 for start, end in (
                (started, captured), (captured, ready), (ready, dequeued),
                (dequeued, rendered), (started, rendered),
            )
        )
        samples = self.latency_samples[kind]
        samples.append(total)
        p50, p95 = np.percentile(samples, [50, 95])
        print(f"[耗时/{kind}] 截图={capture:.1f}ms 识别={recognition:.1f}ms "
              f"队列={queue_ms:.1f}ms Tk更新={ui:.1f}ms 合计={total:.1f}ms "
              f"(最近 n={len(samples)} P50={p50:.1f}ms P95={p95:.1f}ms)")

    def show_status(self, text, duration=2000):
        self.clear_display()
        self.layout = get_game_layout()
        rect = (self.layout.client if self.layout else
                ScreenRect(0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()))
        if position_overlay(self.hwnd, rect):
            self._place_status(text, duration)

    def _place_status(self, text, duration):
        lbl = self.labels['hex_2']
        lbl.config(text=text, fg=COLORS["status"])
        lbl.place(relx=0.5, rely=0.5, anchor="center")
        if duration > 0:
            self.hide_timer = self.root.after(duration, self.clear_display)

    def update_display(self, results, duration=5000):
        layout = self.layout
        self.clear_display()
        self.layout = layout
        if layout is None:
            return
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
            
            x, y = layout.overlay_anchor(key)
            lbl.place(x=x, y=y, anchor="nw")
            lbl.lift()

        # duration <= 0 时不设置定时器，等待外部主动 CLEAR
        if duration > 0:
            self.hide_timer = self.root.after(duration, self.clear_display)

# ================= 4. 控制逻辑 (Controller) =================

class InputController(threading.Thread):
    def __init__(self, app_queue, data_manager, analyzer, lcu_connector=None, *, auto_popup=False):
        super().__init__(daemon=True)
        self.queue = app_queue
        self.dm = data_manager
        self.analyzer = analyzer
        self.lcu = lcu_connector
        self.auto_popup_enabled = auto_popup
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
    # 阶段2: 监听热键与自动感知
    # ==========================================

    def listening_phase(self):
        self.flush_input()
        print(f"[监听中...] 当前英雄: {self.current_hero} | F6分析 / F7刷新 / F8手动")
        print("英雄自动同步，海克斯弹窗打开后按 F6。")
        poller = SessionPoller(self.lcu) if self.lcu else None
        if poller:
            poller.start()
        try:
            self._listen(poller)
        finally:
            if poller:
                poller.stop()
                poller.join()

    def _listen(self, poller):
        detector = PopupDetector(self.analyzer)
        phase = "Unknown"
        snapshot = None
        refresh_pending = False
        last_popup = float("-inf")

        while True:
            now = time.monotonic()
            latest = poller.snapshot if poller else None
            if latest is not None and latest is not snapshot:
                snapshot = latest
                old_phase = phase
                phase, hero, source, _ = latest
                if phase == "InProgress" and old_phase != phase:
                    detector.reset(opening=True)
                    last_popup = float("-inf")
                elif old_phase == "InProgress" and phase != old_phase:
                    self.queue.put({"cmd": "CLEAR"})
                    detector.reset()
                validated = self._validate_hero(hero) if hero else None
                if validated and validated != self.current_hero:
                    self.current_hero = validated
                    self.queue.put({"cmd": "CLEAR"})
                    detector.reset(opening=detector.opening)
                    print(f">>> 英雄已切换 ({source}): {validated}")
                    if phase != "InProgress":
                        self.queue.put({"cmd": "STATUS", "data": f"已切换: {validated}", "duration": 2500})
                elif refresh_pending:
                    self.queue.put({
                        "cmd": "STATUS", "data": f"当前: {self.current_hero or '未识别'}", "duration": 2000,
                    })
                refresh_pending = False

            if keyboard.is_pressed('f6') and now - self._last_f6 > 1.0:
                self._last_f6 = now
                if not self.current_hero:
                    self.queue.put({"cmd": "STATUS", "data": "⚠ 尚未锁定英雄", "duration": 2500})
                else:
                    self.queue.put({"cmd": "STATUS", "data": f"🔎 正在分析 [{self.current_hero}]...", "duration": 1500})
                    message = self.analyzer.manual_update(self.current_hero)
                    detector.reset(opening=detector.opening)
                    self.queue.put(message)

            if keyboard.is_pressed('f7') and now - self._last_f7 > 1.0:
                self._last_f7 = now
                self.queue.put({"cmd": "STATUS", "data": "刷新英雄...", "duration": 1500})
                if poller:
                    refresh_pending = True
                    poller.refresh()

            if keyboard.is_pressed('f8') and now - self._last_f8 > 1.0:
                self._last_f8 = now
                self.queue.put({"cmd": "CLEAR"})
                return

            if (self.auto_popup_enabled and self.current_hero and phase == "InProgress"
                    and now - last_popup >= detector.interval):
                last_popup = now
                message = detector.sample_window(self.current_hero, now)
                if message:
                    self.queue.put(message)

            time.sleep(0.015)


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
    enable_per_monitor_dpi()
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
    app = OverlayApp(root)
    
    # 4. 启动后台控制线程
    controller = InputController(app.queue, dm, analyzer, lcu_connector=lcu)
    root.after(0, controller.start)
    
    # 4. 进入 UI 主循环
    print("程序已启动...")
    try:
        root.mainloop()
    except KeyboardInterrupt:
        os._exit(0)

if __name__ == "__main__":
    main()
