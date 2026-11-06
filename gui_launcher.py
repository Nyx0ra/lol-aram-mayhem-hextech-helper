"""
ARAM 海克斯助手 - GUI 启动器
独立 EXE 入口点，提供图形化界面与系统托盘支持
"""
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import queue
import os
import sys
import io
import time
import datetime
import math
import traceback

# ============ 路径初始化 (兼容 PyInstaller 打包) ============

from scripts.config import get_base_dir, BASE_DIR
os.chdir(BASE_DIR)
sys.path.insert(0, BASE_DIR)

# ============ 延迟导入 (需要 path 已设置) ============

import keyboard
from PIL import Image, ImageDraw
import pystray


# ============ 统一配色方案 ============

class Theme:
    """GitHub Dark Theme 配色常量 (单一来源)"""
    BG          = "#0d1117"
    BG_CARD     = "#161b22"
    BG_INPUT    = "#0d1117"
    ACCENT      = "#58a6ff"
    ACCENT_HVR  = "#79c0ff"
    SUCCESS     = "#3fb950"
    WARNING     = "#d29922"
    ERROR       = "#f85149"
    TEXT        = "#e6edf3"
    TEXT_DIM    = "#8b949e"
    BORDER      = "#30363d"


# ================= 日志重定向 =================

class LogRedirector(io.TextIOBase):
    """将 stdout/stderr 重定向到 Queue, 供 GUI 日志面板使用"""

    def __init__(self, log_queue, original_stream=None):
        super().__init__()
        self.log_queue = log_queue
        self.original = original_stream

    def write(self, text):
        if text and text.strip():
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            self.log_queue.put(f"[{ts}] {text.rstrip()}")
        if self.original:
            try:
                self.original.write(text)
                self.original.flush()
            except Exception:
                pass
        return len(text) if text else 0

    def flush(self):
        if self.original:
            try:
                self.original.flush()
            except Exception:
                pass


# ================= 后台控制器 (全自动引擎) =================

def _close_engine_resources(analyzer, lcu):
    try:
        if analyzer is not None:
            analyzer.executor.shutdown()
    finally:
        if lcu is not None:
            lcu.lcu_session.close()
            lcu.live_session.close()


class GUIController(threading.Thread):
    """
    后台全自动引擎:
      1. 官方 API 全生命周期监听: 选人换人 / 局内开局 / 换局自动同步英雄 (免按 F7)
      2. 局内海克斯弹窗自感知: 两阶段视觉门控 + 毫秒级防抖，弹窗自动推荐，选完自动隐去 (免按 F6)
      3. 全局热键 F6/F7/F8 手动兜底
    """

    def __init__(self, overlay_queue, gui_queue, data_manager, analyzer, lcu_connector, auto_popup=False, generation=None):
        super().__init__(daemon=True)
        self.overlay_queue = overlay_queue
        self.gui_queue = gui_queue
        self.dm = data_manager
        self.analyzer = analyzer
        self.lcu = lcu_connector
        self.generation = generation
        self.data_updates = queue.Queue()

        self.current_hero = None
        self.current_phase = "Unknown"
        self.is_manual_hero = False
        self.auto_popup_enabled = auto_popup
        self.running = True

        from main import PopupDetector
        from scripts.lcu_connector import SessionPoller

        self.detector = PopupDetector(analyzer)
        self.session_poller = SessionPoller(lcu_connector) if lcu_connector else None
        self._session_snapshot = None
        self._refresh_pending = False
        self._reset_popup = threading.Event()
        self._overlay_is_showing = False
        self._last_popup_check = float("-inf")

        self._last_f6 = 0.0
        self._last_f7 = 0.0
        self._last_f8 = 0.0

    def stop(self):
        self.running = False

    def set_auto_popup(self, enabled: bool):
        self.auto_popup_enabled = bool(enabled)
        if not self.auto_popup_enabled:
            self._reset_popup.set()
            if self._overlay_is_showing:
                self.overlay_queue.put({"cmd": "CLEAR"})
                self._overlay_is_showing = False

    def _gui(self, **kwargs):
        """发送消息到 GUI"""
        self.gui_queue.put(dict(kwargs, generation=self.generation))

    def _validate_hero(self, name):
        """验证英雄名是否在数据库中，尝试模糊映射"""
        return self.dm.validate_hero(name)

    def set_hero(self, hero_name):
        """手动设置英雄 (供 GUI 输入框调用)"""
        validated = self._validate_hero(hero_name)
        if validated:
            self.current_hero = validated
            self.is_manual_hero = True
            print(f"✅ 已手动锁定英雄: {validated}")
            self._gui(event="hero_found", hero=validated, source="手动输入")
            self.overlay_queue.put({"cmd": "STATUS", "data": f"当前: {validated}\n弹窗打开后按 F6", "duration": 2500})
            return validated
        return None

    def run(self):
        try:
            if self.session_poller:
                self.session_poller.start()
            self._run_loop()
        except Exception:
            if self.running:
                traceback.print_exc()
                self._gui(event="engine_error")
        finally:
            if self.session_poller and self.session_poller.ident is not None:
                self.session_poller.stop()
                self.session_poller.join()
            _close_engine_resources(self.analyzer, self.lcu)

    def _apply_data_updates(self):
        data = None
        while True:
            try:
                data = self.data_updates.get_nowait()
            except queue.Empty:
                break
        if data is None:
            return
        self.dm = self.analyzer.dm = data
        if self.session_poller:
            self.session_poller.refresh(reload_champions=True)
        if self.current_hero:
            self.current_hero = data.validate_hero(self.current_hero)
            if not self.current_hero:
                self.is_manual_hero = False
            self._gui(event="hero_confirmed", hero=self.current_hero or "—")
        self._clear_detection(opening=self.detector.opening, clear=True)

    def _clear_detection(self, opening=False, clear=False):
        if clear or self._overlay_is_showing:
            self.overlay_queue.put({"cmd": "CLEAR"})
        self.detector.reset(opening=opening)
        self._overlay_is_showing = False
        self._last_popup_check = float("-inf")

    def _run_loop(self):
        """处理热键和截图，通过快照读取会话状态。"""
        print("识别引擎已启动（英雄自动同步，海克斯按 F6 识别）")
        self._gui(event="status", status="connecting")

        while self.running:
            self._apply_data_updates()
            if self._reset_popup.is_set():
                self._reset_popup.clear()
                self._clear_detection(opening=self.detector.opening)
            now = time.monotonic()
            self._sync_session_and_hero()
            self._handle_hotkeys(now)

            if self.auto_popup_enabled and self.current_hero and self.current_phase == "InProgress":
                if now - self._last_popup_check >= self.detector.interval:
                    self._last_popup_check = now
                    self._check_in_game_popup()

            time.sleep(0.015)

    def _handle_hotkeys(self, now):
        """热键监听逻辑 (F6=强制分析, F7=强制刷新, F8=重置)"""
        # F6: 强制分析
        if keyboard.is_pressed('f6') and now - self._last_f6 > 1.0:
            self._last_f6 = now
            if not self.current_hero:
                self.overlay_queue.put({"cmd": "STATUS", "data": "⚠ 尚未锁定英雄\n请按 F7 获取或手动输入", "duration": 2500})
                self._gui(event="status", status="no_hero_warning")
            else:
                self._gui(event="status", status="analyzing", hero=self.current_hero)
                self.overlay_queue.put({"cmd": "STATUS", "data": f"🔎 分析 [{self.current_hero}]...", "duration": 1500})
                message = self.analyzer.manual_update(self.current_hero)
                self.detector.reset(opening=self.detector.opening)
                self.overlay_queue.put(message)
                self._overlay_is_showing = message["cmd"] == "UPDATE"
                status = "analyzed" if self._overlay_is_showing else "game_window_unavailable"
                self._gui(event="status", status=status, hero=self.current_hero)

        # F7: 强制刷新英雄与客户端连接
        if keyboard.is_pressed('f7') and now - self._last_f7 > 1.0:
            self._last_f7 = now
            self._gui(event="status", status="refreshing")
            self.overlay_queue.put({"cmd": "STATUS", "data": "刷新英雄中...", "duration": 1500})
            self.is_manual_hero = False
            if self.session_poller:
                self._refresh_pending = True
                self.session_poller.refresh()

        # F8: 重置当前英雄与检测状态
        if keyboard.is_pressed('f8') and now - self._last_f8 > 1.0:
            self._last_f8 = now
            print("F8: 重置状态并重新自动侦测")
            self._gui(event="status", status="resetting")
            self.current_hero = None
            self.is_manual_hero = False
            self._clear_detection(opening=self.detector.opening, clear=True)
            if self.session_poller:
                self.session_poller.refresh()

    def _sync_session_and_hero(self):
        """只消费最新快照，不在截图线程访问 LCU。"""
        snapshot = self.session_poller.snapshot if self.session_poller else None
        if snapshot is None or snapshot is self._session_snapshot:
            return
        self._session_snapshot = snapshot
        phase, raw_hero, source, _ = snapshot
        old_phase, self.current_phase = self.current_phase, phase

        if phase == "InProgress" and old_phase != "InProgress":
            self._clear_detection(opening=True)
        elif old_phase == "InProgress" and phase != "InProgress":
            self._clear_detection()
            self._gui(event="status", status="listening")

        if phase in ("ChampSelect", "Lobby", "Matchmaking") and old_phase in ("InProgress", "GameStart"):
            self.is_manual_hero = False

        refresh, self._refresh_pending = self._refresh_pending, False
        validated = self._validate_hero(raw_hero) if raw_hero else None
        if validated and (not self.current_hero or (validated != self.current_hero and not self.is_manual_hero)):
            self.current_hero = validated
            self._clear_detection(opening=self.detector.opening)
            print(f"✅ 自动锁定英雄 ({source}): {validated}")
            self._gui(event="hero_found", hero=validated, source=source)
            if phase != "InProgress":
                self.overlay_queue.put({
                    "cmd": "STATUS", "data": f"当前: {validated}\n弹窗打开后按 F6", "duration": 2500,
                })
        elif refresh:
            self.overlay_queue.put({
                "cmd": "STATUS", "data": f"当前: {self.current_hero or '未识别'}\n阶段: {phase}", "duration": 2000,
            })

    def _check_in_game_popup(self):
        message = self.detector.sample_window(self.current_hero, self._last_popup_check)
        if message and self.running and self.auto_popup_enabled:
            self._overlay_is_showing = message["cmd"] == "UPDATE"
            self.overlay_queue.put(message)
            if self._overlay_is_showing:
                if message.get("refresh_keys"):
                    print(f"🔄 重掷已确认: {', '.join(message['refresh_keys'])}")
                else:
                    print("✨ 自动识别到海克斯选择弹窗")
                self._gui(event="status", status="auto_detected", hero=self.current_hero)


# ================= 系统托盘管理 =================

class TrayManager:
    """系统托盘图标管理"""

    def __init__(self, app):
        self.app = app
        self.icon = None
        self._thread = None

    def _create_tray_image(self):
        """程序化创建托盘图标 (蓝色六边形)"""
        size = 64
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        cx, cy = size // 2, size // 2
        r = size // 2 - 4
        points = []
        for i in range(6):
            angle = math.radians(60 * i - 30)
            points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
        draw.polygon(points, fill=(13, 17, 23, 255), outline=(88, 166, 255, 255))
        # 内部小六边形
        r2 = r * 0.55
        inner = []
        for i in range(6):
            angle = math.radians(60 * i - 30)
            inner.append((cx + r2 * math.cos(angle), cy + r2 * math.sin(angle)))
        draw.polygon(inner, fill=(88, 166, 255, 200))
        return img

    def start(self):
        """启动托盘图标 (后台线程)"""
        image = self._create_tray_image()
        menu = pystray.Menu(
            pystray.MenuItem("显示窗口", self._on_show),
            pystray.MenuItem("退出程序", self._on_quit),
        )
        self.icon = pystray.Icon("ARAM助手", image, "ARAM 海克斯助手", menu)
        self._thread = threading.Thread(target=self.icon.run, daemon=True)
        self._thread.start()

    def stop(self):
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass

    def notify(self, title, message):
        """托盘气泡通知"""
        if self.icon:
            try:
                self.icon.notify(message, title)
            except Exception:
                pass

    def _on_show(self, icon=None, item=None):
        self.app.gui_queue.put({"event": "tray_show"})

    def _on_quit(self, icon=None, item=None):
        self.app.gui_queue.put({"event": "tray_quit"})


# ================= 更新选项对话框 =================

class UpdateDialog:
    """数据更新选项对话框"""

    BG       = Theme.BG
    BG_CARD  = Theme.BG_CARD
    ACCENT   = Theme.ACCENT
    SUCCESS  = Theme.SUCCESS
    TEXT     = Theme.TEXT
    TEXT_DIM = Theme.TEXT_DIM
    BORDER   = Theme.BORDER
    WARNING  = Theme.WARNING

    def __init__(self, app):
        self.app = app
        self.dlg = tk.Toplevel(app.root)
        self.dlg.title("数据更新")
        self.dlg.configure(bg=self.BG)
        self.dlg.transient(app.root)
        self.dlg.grab_set()

        # 设置图标
        icon_path = os.path.join(BASE_DIR, 'assets', 'icon.ico')
        if os.path.exists(icon_path):
            self.dlg.iconbitmap(icon_path)

        self._build_ui()

        # 强制完成所有子组件渲染，精确获取自身需要的高度
        self.dlg.update()
        w = max(440, self.dlg.winfo_reqwidth())
        min_h = self.dlg.winfo_reqheight()
        
        x = app.root.winfo_x() + (app.root.winfo_width() - w) // 2
        y = app.root.winfo_y() + (app.root.winfo_height() - min_h) // 2
        
        self.dlg.geometry(f"{w}x{min_h}+{x}+{y}")
        # 在设定好绝对尺寸后再禁用缩放，防止 Windows 过早锁死窗口尺寸导致元素被截住
        self.dlg.resizable(False, False)

    def _build_ui(self):
        main = tk.Frame(self.dlg, bg=self.BG, padx=24, pady=20)
        main.pack(fill=tk.BOTH, expand=True)

        # 标题
        tk.Label(main, text="选择更新方式", font=("Microsoft YaHei", 16, "bold"),
                 fg=self.TEXT, bg=self.BG).pack(anchor="w", pady=(0, 4))

        # ---- 爬虫选项区 ----
        try:
            from scripts.updater import is_author_environment
            is_author = is_author_environment()
        except Exception:
            is_author = False
        crawler_label = (
            "🌐 本地爬虫更新 (需要 Chrome · 更新后自动同步至 GitHub)"
            if is_author else
            "🌐 本地爬虫更新 (需要 Chrome 浏览器)"
        )
        tk.Label(main, text=crawler_label,
                 font=("Microsoft YaHei", 9), fg=self.WARNING,
                 bg=self.BG).pack(anchor="w", pady=(8, 6))

        self._option_row(main,
            icon="🔍", title="抽样校验", tag="推荐",
            desc="随机3英雄比对，有差异自动全量更新",
            command=lambda: self._select('spot_check'))

        self._option_row(main,
            icon="🧠", title="智能增量", tag=None,
            desc="自动爬取新英雄 + 改名英雄 + 缺失英雄",
            command=lambda: self._select('smart'))

        self._option_row(main,
            icon="🔄", title="全量更新", tag=None,
            desc="强制重爬所有英雄，耗时较长",
            command=lambda: self._select('full'))

        self._option_row(main,
            icon="🎯", title="精确更新", tag=None,
            desc="手动指定英雄名称进行更新",
            command=self._precise_input)

        # ---- 分隔线 ----
        sep_frame = tk.Frame(main, bg=self.BG, pady=8)
        sep_frame.pack(fill=tk.X)
        tk.Frame(sep_frame, bg=self.BORDER, height=1).pack(fill=tk.X)

        # ---- GitHub 下载 ----
        tk.Label(main, text="📦 在线下载 (无需浏览器)",
                 font=("Microsoft YaHei", 9), fg=self.TEXT_DIM,
                 bg=self.BG).pack(anchor="w", pady=(0, 6))

        self._option_row(main,
            icon="📥", title="GitHub 下载", tag=None,
            desc="从仓库下载预处理数据 (取决于仓库更新时间)",
            command=lambda: self._select('github'))

        # ---- 底部: 帮助按钮 ----
        bottom = tk.Frame(main, bg=self.BG)
        bottom.pack(fill=tk.X, pady=(8, 0))

        help_btn = tk.Label(bottom, text=" ？", font=("Microsoft YaHei", 12, "bold"),
                            fg=self.TEXT_DIM, bg=self.BG, cursor="hand2",
                            width=3, relief=tk.FLAT,
                            highlightbackground=self.BORDER, highlightthickness=1)
        help_btn.pack(side=tk.RIGHT)
        help_btn.bind("<Enter>", lambda e: help_btn.config(fg=self.ACCENT))
        help_btn.bind("<Leave>", lambda e: help_btn.config(fg=self.TEXT_DIM))
        help_btn.bind("<Button-1>", lambda e: self._show_help())

    def _option_row(self, parent, icon, title, tag, desc, command):
        """创建一个可点击的选项行"""
        row = tk.Frame(parent, bg=self.BG_CARD, cursor="hand2",
                       highlightbackground=self.BORDER, highlightthickness=1)
        row.pack(fill=tk.X, pady=(0, 6))

        inner = tk.Frame(row, bg=self.BG_CARD, padx=14, pady=10)
        inner.pack(fill=tk.X)

        # 标题行
        title_row = tk.Frame(inner, bg=self.BG_CARD)
        title_row.pack(fill=tk.X)

        tk.Label(title_row, text=f"{icon}  {title}",
                 font=("Microsoft YaHei", 11, "bold"),
                 fg=self.TEXT, bg=self.BG_CARD).pack(side=tk.LEFT)

        if tag:
            tag_frame = tk.Frame(title_row, bg=self.ACCENT, padx=6, pady=1)
            tag_frame.pack(side=tk.RIGHT)
            tk.Label(tag_frame, text=tag, font=("Microsoft YaHei", 8),
                     fg="white", bg=self.ACCENT).pack()

        # 描述
        tk.Label(inner, text=desc, font=("Microsoft YaHei", 9),
                 fg=self.TEXT_DIM, bg=self.BG_CARD, anchor="w").pack(fill=tk.X, pady=(2, 0))

        # 绑定点击事件到所有子组件
        def _on_enter(e):
            row.config(highlightbackground=self.ACCENT)
        def _on_leave(e):
            row.config(highlightbackground=self.BORDER)
        def _on_click(e):
            command()

        for widget in [row, inner, title_row] + list(inner.winfo_children()) + list(title_row.winfo_children()):
            widget.bind("<Enter>", _on_enter)
            widget.bind("<Leave>", _on_leave)
            widget.bind("<Button-1>", _on_click)

    def _select(self, mode):
        """选择更新模式并关闭对话框"""
        self.dlg.destroy()
        self.app._run_update(mode)

    def _precise_input(self):
        """精确更新: 弹出输入框"""
        input_dlg = tk.Toplevel(self.dlg)
        input_dlg.title("精确更新 - 输入英雄名")
        input_dlg.geometry("360x150")
        input_dlg.resizable(False, False)
        input_dlg.configure(bg=self.BG)
        input_dlg.transient(self.dlg)
        input_dlg.grab_set()

        frame = tk.Frame(input_dlg, bg=self.BG, padx=20, pady=16)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(frame, text="输入英雄名称 (多个用逗号分隔)",
                 font=("Microsoft YaHei", 10), fg=self.TEXT,
                 bg=self.BG).pack(anchor="w", pady=(0, 8))

        entry = tk.Entry(frame, font=("Microsoft YaHei", 11),
                         bg=self.BG_CARD, fg=self.TEXT,
                         insertbackground=self.TEXT,
                         highlightbackground=self.BORDER,
                         highlightthickness=1, relief=tk.FLAT, borderwidth=6)
        entry.pack(fill=tk.X, pady=(0, 12))
        entry.focus_set()

        def _submit():
            names = [n.strip() for n in entry.get().split(",") if n.strip()]
            if names:
                input_dlg.destroy()
                self.dlg.destroy()
                self.app._run_update('precise', hero_names=names)

        entry.bind("<Return>", lambda e: _submit())

        ttk.Button(frame, text="开始更新", style='Accent.TButton',
                   command=_submit).pack(fill=tk.X)

    def _show_help(self):
        """显示帮助信息"""
        help_text = (
            "📖 更新方式说明\n\n"
            "━━ 本地爬虫 (需要 Chrome) ━━\n\n"
            "🔍 抽样校验 [推荐]\n"
            "  从所有英雄中随机选取3个，爬取最新数据与本地\n"
            "  比对。如果发现差异，自动触发全量更新。\n"
            "  适合游戏版本更新后快速检测数据是否过期。\n\n"
            "🧠 智能增量\n"
            "  自动检测并爬取: 新出的英雄、近期改名的英雄、\n"
            "  以及本地缺失数据的英雄。不会重复爬取已有数据。\n\n"
            "🔄 全量更新\n"
            "  强制重新爬取全部英雄的海克斯数据。\n"
            "  耗时较长 (约10-20分钟)，适合数据严重过期时使用。\n\n"
            "🎯 精确更新\n"
            "  手动输入英雄名称 (支持中文名/英文名)，\n"
            "  仅更新指定英雄的数据。\n\n"
            "━━ 在线下载 (无需 Chrome) ━━\n\n"
            "📥 GitHub 下载\n"
            "  从项目仓库直接下载预处理好的数据文件。\n"
            "  ⚠ 注意: 仓库数据由开发者手动更新推送，\n"
            "  时效性不一定能保证。如果需要最新数据，\n"
            "  建议优先使用爬虫方式。"
        )
        messagebox.showinfo("更新方式说明", help_text, parent=self.dlg)


# ================= 主 GUI 应用 =================

class LauncherApp:
    """ARAM 海克斯助手 - 主界面"""

    # 配色方案 (引用统一主题)
    BG          = Theme.BG
    BG_CARD     = Theme.BG_CARD
    BG_INPUT    = Theme.BG_INPUT
    ACCENT      = Theme.ACCENT
    ACCENT_HVR  = Theme.ACCENT_HVR
    SUCCESS     = Theme.SUCCESS
    WARNING     = Theme.WARNING
    ERROR       = Theme.ERROR
    TEXT        = Theme.TEXT
    TEXT_DIM    = Theme.TEXT_DIM
    BORDER      = Theme.BORDER

    FONT_TITLE  = ("Microsoft YaHei", 18, "bold")
    FONT_SUB    = ("Microsoft YaHei", 10)
    FONT_HERO   = ("Microsoft YaHei", 22, "bold")
    FONT_STATUS = ("Microsoft YaHei", 11)
    FONT_BTN    = ("Microsoft YaHei", 11, "bold")
    FONT_LOG    = ("Consolas", 9)

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("ARAM 海克斯助手")
        self.root.geometry("540x660")
        self.root.minsize(500, 600)
        self.root.configure(bg=self.BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 设置窗口图标
        icon_path = os.path.join(BASE_DIR, 'assets', 'icon.ico')
        if os.path.exists(icon_path):
            self.root.iconbitmap(icon_path)

        # ttk 主题
        self.style = ttk.Style()
        self.style.theme_use('clam')
        self._configure_styles()

        # 状态变量
        self.engine_running = False
        self.engine_starting = False
        self._engine_generation = 0
        self._init_cancel = None
        self._update_running = False
        self._quit_after_update = False
        self.controller = None
        self.overlay = None
        self.overlay_window = None
        self.dm = None
        self.tray = TrayManager(self)

        # 通信队列
        self.gui_queue = queue.Queue()
        self.log_queue = queue.Queue()

        # UI 变量
        self.hero_var = tk.StringVar(value="—")
        self.status_var = tk.StringVar(value="等待启动")
        self.status_color = self.TEXT_DIM
        self._pulse_state = 0

        # 重定向日志
        self._orig_stdout = sys.stdout
        self._orig_stderr = sys.stderr
        sys.stdout = LogRedirector(self.log_queue, self._orig_stdout)
        sys.stderr = LogRedirector(self.log_queue, self._orig_stderr)

        # 构建 UI
        self._build_ui()

        # 启动队列轮询
        self.root.after(100, self._poll_queues)

        # 启动时加载数据
        self.root.after(300, self._load_data)

    # ==========================================
    # ttk 样式配置
    # ==========================================

    def _configure_styles(self):
        s = self.style

        # 主按钮 (蓝色)
        s.configure('Accent.TButton',
                     background=self.ACCENT,
                     foreground='white',
                     font=self.FONT_BTN,
                     padding=(16, 10),
                     borderwidth=0)
        s.map('Accent.TButton',
              background=[('active', self.ACCENT_HVR), ('disabled', self.BORDER)])

        # 次要按钮 (深灰)
        s.configure('Secondary.TButton',
                     background=self.BG_CARD,
                     foreground=self.TEXT,
                     font=self.FONT_BTN,
                     padding=(12, 8),
                     borderwidth=1)
        s.map('Secondary.TButton',
              background=[('active', self.BORDER), ('disabled', '#0d1117')])

        # 停止按钮 (红色)
        s.configure('Danger.TButton',
                     background=self.ERROR,
                     foreground='white',
                     font=self.FONT_BTN,
                     padding=(16, 10),
                     borderwidth=0)
        s.map('Danger.TButton',
              background=[('active', '#da3633')])

        # 链接按钮 (无背景)
        s.configure('Link.TButton',
                     background=self.BG,
                     foreground=self.TEXT_DIM,
                     font=self.FONT_SUB,
                     padding=(8, 4),
                     borderwidth=0)
        s.map('Link.TButton',
              foreground=[('active', self.ACCENT)],
              background=[('active', self.BG)])

    # ==========================================
    # 构建 UI
    # ==========================================

    def _build_ui(self):
        main = tk.Frame(self.root, bg=self.BG, padx=24, pady=16)
        main.pack(fill=tk.BOTH, expand=True)

        # ---- Header ----
        hdr = tk.Frame(main, bg=self.BG)
        hdr.pack(fill=tk.X, pady=(0, 16))

        # 标志六边形 (文字模拟)
        tk.Label(hdr, text="⬡", font=("Segoe UI", 28), fg=self.ACCENT,
                 bg=self.BG).pack(side=tk.LEFT, padx=(0, 12))

        title_frame = tk.Frame(hdr, bg=self.BG)
        title_frame.pack(side=tk.LEFT)
        tk.Label(title_frame, text="ARAM 海克斯助手",
                 font=self.FONT_TITLE, fg=self.TEXT, bg=self.BG).pack(anchor="w")
        tk.Label(title_frame, text="大乱斗海克斯推荐 · OCR 识别 · 自动选取",
                 font=self.FONT_SUB, fg=self.TEXT_DIM, bg=self.BG).pack(anchor="w")

        # ---- 分隔线 ----
        tk.Frame(main, bg=self.BORDER, height=1).pack(fill=tk.X, pady=(0, 16))

        # ---- 状态卡片 ----
        card = tk.Frame(main, bg=self.BG_CARD, padx=20, pady=16,
                        highlightbackground=self.BORDER, highlightthickness=1)
        card.pack(fill=tk.X, pady=(0, 8))

        # 英雄行
        hero_row = tk.Frame(card, bg=self.BG_CARD)
        hero_row.pack(fill=tk.X, pady=(0, 8))
        tk.Label(hero_row, text="当前英雄", font=self.FONT_SUB,
                 fg=self.TEXT_DIM, bg=self.BG_CARD).pack(side=tk.LEFT)
        self.hero_label = tk.Label(hero_row, textvariable=self.hero_var,
                                   font=self.FONT_HERO, fg=self.ACCENT, bg=self.BG_CARD)
        self.hero_label.pack(side=tk.RIGHT)

        # 状态行
        status_row = tk.Frame(card, bg=self.BG_CARD)
        status_row.pack(fill=tk.X)
        tk.Label(status_row, text="运行状态", font=self.FONT_SUB,
                 fg=self.TEXT_DIM, bg=self.BG_CARD).pack(side=tk.LEFT)
        self.status_dot = tk.Label(status_row, text="●", font=("Segoe UI", 10),
                                   fg=self.TEXT_DIM, bg=self.BG_CARD)
        self.status_dot.pack(side=tk.RIGHT, padx=(0, 6))
        self.status_label = tk.Label(status_row, textvariable=self.status_var,
                                     font=self.FONT_STATUS, fg=self.TEXT_DIM,
                                     bg=self.BG_CARD)
        self.status_label.pack(side=tk.RIGHT)

        # ---- 手动输入英雄 ----
        manual_frame = tk.Frame(main, bg=self.BG, pady=4)
        manual_frame.pack(fill=tk.X, pady=(0, 8))

        self.hero_entry = tk.Entry(manual_frame, font=("Microsoft YaHei", 11),
                                   bg=self.BG_CARD, fg=self.TEXT,
                                   insertbackground=self.TEXT,
                                   highlightbackground=self.BORDER,
                                   highlightthickness=1, relief=tk.FLAT,
                                   borderwidth=6)
        self.hero_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.hero_entry.insert(0, "输入英雄名/拼音...")
        self.hero_entry.config(fg=self.TEXT_DIM)
        self.hero_entry.bind("<FocusIn>", self._on_entry_focus_in)
        self.hero_entry.bind("<FocusOut>", self._on_entry_focus_out)
        self.hero_entry.bind("<Return>", lambda e: self._manual_set_hero())

        self.manual_btn = ttk.Button(manual_frame, text="锁定",
                                     style='Secondary.TButton',
                                     command=self._manual_set_hero)
        self.manual_btn.pack(side=tk.RIGHT)

        # ---- 热键提示 ----
        hotkey_frame = tk.Frame(main, bg=self.BG)
        hotkey_frame.pack(fill=tk.X, pady=(0, 8))
        hotkeys = [("F6", "分析海克斯"), ("F7", "识别英雄"), ("F8", "重置")]
        for key, desc in hotkeys:
            pill = tk.Frame(hotkey_frame, bg=self.BORDER, padx=1, pady=1)
            pill.pack(side=tk.LEFT, padx=(0, 10))
            inner = tk.Frame(pill, bg=self.BG_CARD, padx=8, pady=3)
            inner.pack()
            tk.Label(inner, text=key, font=("Consolas", 9, "bold"),
                     fg=self.ACCENT, bg=self.BG_CARD).pack(side=tk.LEFT, padx=(0, 4))
            tk.Label(inner, text=desc, font=("Microsoft YaHei", 9),
                     fg=self.TEXT_DIM, bg=self.BG_CARD).pack(side=tk.LEFT)

        # ---- 按钮区域 ----
        btn_frame = tk.Frame(main, bg=self.BG)
        btn_frame.pack(fill=tk.X, pady=(0, 12))

        # 开始/停止按钮
        self.start_btn = ttk.Button(btn_frame, text="▶  开始识别",
                                     style='Accent.TButton', command=self._start_engine)
        self.start_btn.pack(fill=tk.X, pady=(0, 8))

        self.stop_btn = ttk.Button(btn_frame, text="■  停止运行",
                                    style='Danger.TButton', command=self._stop_engine)
        # 停止按钮初始隐藏

        # 数据更新按钮
        self.update_btn = ttk.Button(btn_frame, text="📦  数据更新",
                                     style='Secondary.TButton',
                                     command=self._show_update_dialog)
        self.update_btn.pack(fill=tk.X, pady=(0, 8))

        # 托盘按钮
        self.tray_btn = ttk.Button(btn_frame, text="最小化到系统托盘",
                                    style='Link.TButton', command=self._minimize_to_tray)
        self.tray_btn.pack()

        # ---- 日志面板 ----
        log_header = tk.Frame(main, bg=self.BG)
        log_header.pack(fill=tk.X, pady=(4, 4))
        tk.Label(log_header, text="运行日志", font=self.FONT_SUB,
                 fg=self.TEXT_DIM, bg=self.BG).pack(side=tk.LEFT)

        self.log_text = scrolledtext.ScrolledText(
            main, font=self.FONT_LOG, bg=self.BG_INPUT, fg=self.TEXT_DIM,
            insertbackground=self.TEXT_DIM, selectbackground=self.ACCENT,
            relief=tk.FLAT, borderwidth=0, height=12, wrap=tk.WORD, state=tk.DISABLED,
            highlightbackground=self.BORDER, highlightthickness=1
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # 日志颜色标签
        self.log_text.tag_configure("success", foreground=self.SUCCESS)
        self.log_text.tag_configure("error", foreground=self.ERROR)
        self.log_text.tag_configure("warning", foreground=self.WARNING)
        self.log_text.tag_configure("info", foreground=self.TEXT_DIM)

    # ==========================================
    # 数据加载
    # ==========================================

    def _load_data(self, after_update=False):
        """后台加载数据"""
        def _load():
            try:
                from main import DataManager
                data = DataManager()
                if data.hero_data:
                    self._log_safe(f"✅ 数据加载完毕: {len(data.hero_data)} 个英雄")
                    self.gui_queue.put({"event": "data_loaded", "data": data, "after_update": after_update})
                else:
                    self._log_safe("❌ 未加载到英雄数据，请检查 data/ 目录")
                    self.gui_queue.put({"event": "data_error", "after_update": after_update})
            except Exception as e:
                self._log_safe(f"❌ 数据加载失败: {e}")
                self.gui_queue.put({"event": "data_error", "after_update": after_update})

        self._log("正在加载数据资源...")
        threading.Thread(target=_load, daemon=True).start()

    # ==========================================
    # 引擎控制
    # ==========================================

    def _start_engine(self):
        """启动识别引擎"""
        if self.engine_running or self.engine_starting:
            return
        if not self.dm or not self.dm.hero_data:
            messagebox.showwarning("提示", "数据尚未加载完成，请稍候")
            return

        self.start_btn.pack_forget()
        self.stop_btn.pack(fill=tk.X, pady=(0, 8))
        self.start_btn.config(state=tk.DISABLED)
        self._set_status("启动中...", self.WARNING)
        self._log("正在初始化 OCR 引擎...")
        self.engine_starting = True
        self._engine_generation += 1
        generation = self._engine_generation
        data = self.dm
        cancelled = threading.Event()
        self._init_cancel = cancelled

        def _init():
            analyzer = lcu = None
            try:
                from main import GameAnalyzer
                from scripts.lcu_connector import LCUConnector

                # 初始化分析器 (加载 OCR 模型)
                analyzer = GameAnalyzer(data)
                if cancelled.is_set():
                    _close_engine_resources(analyzer, None)
                    return
                self._log_safe("✅ OCR 引擎就绪")

                # 初始化 LCU 连接器
                champions_json = os.path.join(data.data_dir, 'champions.json')
                lcu = LCUConnector(champions_json)
                self._log_safe("✅ LCU 连接器就绪")

                # 在主线程创建 overlay
                self.gui_queue.put({
                    "event": "create_overlay", "generation": generation,
                    "analyzer": analyzer, "lcu": lcu,
                })

            except Exception as e:
                self._log_safe(f"❌ 引擎启动失败: {e}")
                traceback.print_exc()
                _close_engine_resources(analyzer, lcu)
                self.gui_queue.put({"event": "engine_error", "generation": generation})

        threading.Thread(target=_init, daemon=True).start()

    def _create_overlay_and_start(self, analyzer, lcu):
        """在主线程中创建 overlay 窗口并启动控制器"""
        try:
            from main import OverlayApp

            # 创建 overlay 作为 Toplevel
            self.overlay_window = tk.Toplevel(self.root)
            self.overlay = OverlayApp(self.overlay_window)

            # 启动正式版后台控制器
            controller = GUIController(
                self.overlay.queue, self.gui_queue, analyzer.dm, analyzer, lcu,
                generation=self._engine_generation,
            )
            if analyzer.dm is not self.dm:
                controller.data_updates.put(self.dm)
            controller.start()
            self.controller = controller

            self.engine_starting = False
            self._init_cancel = None
            self.engine_running = True
            self._set_status("运行中", self.SUCCESS)
            self._log("✅ 引擎已启动。英雄自动同步，海克斯弹窗打开后按 F6。")
            self._start_pulse()

            # 启动托盘
            self.tray.start()

        except Exception as e:
            self._log(f"❌ Overlay 创建失败: {e}")
            traceback.print_exc()
            if self.controller is None:
                _close_engine_resources(analyzer, lcu)
            self._engine_cleanup()
            self.stop_btn.pack_forget()
            self.start_btn.pack(fill=tk.X, pady=(0, 8))
            self.start_btn.config(state=tk.NORMAL)

    def _stop_engine(self):
        """停止识别引擎"""
        self._log("正在停止引擎...")
        self._engine_cleanup()
        self.stop_btn.pack_forget()
        self.start_btn.pack(fill=tk.X, pady=(0, 8))
        self.start_btn.config(state=tk.NORMAL)
        self._set_status("已停止", self.TEXT_DIM)
        self.hero_var.set("—")
        self._log("引擎已停止")

    def _engine_cleanup(self):
        """清理引擎资源"""
        self.engine_running = False
        self.engine_starting = False
        self._engine_generation += 1
        if self._init_cancel is not None:
            self._init_cancel.set()
            self._init_cancel = None
        if self.controller:
            self.controller.stop()
            self.controller = None
        if self.overlay_window:
            try:
                if self.overlay:
                    self.overlay.close()
                self.overlay_window.destroy()
            except Exception:
                pass
            self.overlay_window = None
            self.overlay = None
        self.tray.stop()

    # ==========================================
    # 数据更新
    # ==========================================

    def _show_update_dialog(self):
        """显示更新选项对话框"""
        UpdateDialog(self)

    def _run_update(self, mode, hero_names=None):
        """执行更新操作 (后台线程)"""
        if self._update_running:
            return
        self._update_running = True
        self.update_btn.config(state=tk.DISABLED)

        mode_labels = {
            'spot_check': '🔍 抽样校验',
            'smart':      '🧠 智能增量',
            'full':       '🔄 全量更新',
            'precise':    '🎯 精确更新',
            'github':     '📥 GitHub 下载',
        }
        self._log(f"{mode_labels.get(mode, mode)} 开始...")

        def _run():
            reload_data = False
            try:
                if mode == 'github':
                    from scripts.updater import download_from_github
                    success = download_from_github(log_func=self._log_safe)
                elif mode == 'precise' and hero_names:
                    from scripts.updater import update_specific_heroes
                    success = update_specific_heroes(hero_names, log_func=self._log_safe)
                else:
                    from scripts.updater import run_update
                    success = run_update(mode=mode, log_func=self._log_safe)

                if success:
                    self._log_safe("✅ 更新完成!")
                    reload_data = True
                else:
                    self._log_safe("⚠ 更新失败，未重新加载数据")
            except Exception as e:
                self._log_safe(f"❌ 更新失败: {e}")
                traceback.print_exc()
            finally:
                self.gui_queue.put({"event": "update_done", "reload": reload_data})

        threading.Thread(target=_run, daemon=True).start()

    # ==========================================
    # 系统托盘
    # ==========================================

    def _minimize_to_tray(self):
        """最小化到系统托盘"""
        if not self.engine_running:
            messagebox.showinfo("提示", "请先点击「开始识别」再最小化到托盘")
            return
        self.root.withdraw()
        self.tray.notify("ARAM 海克斯助手", "程序已最小化到系统托盘，热键仍然有效")
        # 确保 overlay 仍然可见
        if self.overlay_window:
            self.root.after(100, self._ensure_overlay_visible)

    def _ensure_overlay_visible(self):
        """确保 overlay 在 root 隐藏后仍然可见"""
        if self.overlay_window:
            try:
                self.overlay_window.deiconify()
                self.overlay_window.attributes("-topmost", True)
            except Exception:
                pass

    def _restore_from_tray(self):
        """从托盘恢复窗口"""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    # ==========================================
    # 队列消息处理
    # ==========================================

    def _poll_queues(self):
        """轮询所有消息队列"""
        # GUI 队列
        try:
            while True:
                msg = self.gui_queue.get_nowait()
                self._handle_gui_message(msg)
        except queue.Empty:
            pass

        # 日志队列
        try:
            while True:
                text = self.log_queue.get_nowait()
                self._append_log(text)
        except queue.Empty:
            pass

        self.root.after(80, self._poll_queues)

    def _handle_gui_message(self, msg):
        event = msg.get("event", "")
        if "generation" in msg and msg["generation"] != self._engine_generation:
            if event == "create_overlay":
                _close_engine_resources(msg["analyzer"], msg["lcu"])
            return

        if event == "data_loaded":
            self.dm = msg["data"]
            if self.controller:
                self.controller.data_updates.put(self.dm)
            elif not self.engine_starting:
                self.start_btn.config(state=tk.NORMAL)
            if msg.get("after_update"):
                self._finish_update()

        elif event == "data_error":
            if self.dm is None:
                self.start_btn.config(state=tk.DISABLED)
            if msg.get("after_update"):
                self._finish_update()

        elif event == "create_overlay":
            self._create_overlay_and_start(msg["analyzer"], msg["lcu"])

        elif event == "engine_error":
            self._stop_engine()

        elif event == "hero_found":
            hero = msg.get("hero", "")
            self.hero_var.set(hero)
            self._set_status("监听中", self.SUCCESS)
            self.tray.notify("英雄已识别", f"当前英雄: {hero}")

        elif event == "hero_confirmed":
            self.hero_var.set(msg.get("hero", ""))

        elif event == "status":
            status = msg.get("status", "")
            hero = msg.get("hero")
            if hero:
                self.hero_var.set(hero)
            status_map = {
                "connecting":       ("连接客户端...", self.WARNING),
                "waiting":          ("等待选取英雄...", self.WARNING),
                "listening":        ("监听中", self.SUCCESS),
                "analyzing":        ("分析中...", self.ACCENT),
                "analyzed":         ("分析完成", self.SUCCESS),
                "game_window_unavailable": ("请将游戏窗口切到前台", self.WARNING),
                "auto_detected":    ("已自动识别推荐", self.SUCCESS),
                "refreshing":       ("刷新英雄...", self.WARNING),
                "no_hero_warning":  ("未锁定英雄", self.ERROR),
                "idle":             ("运行中 (无英雄)", self.TEXT_DIM),
                "resetting":        ("重置中...", self.WARNING),
            }
            if status in status_map:
                text, color = status_map[status]
                self._set_status(text, color)

        elif event == "tray_show":
            self._restore_from_tray()

        elif event == "tray_quit":
            self._quit_app()

        elif event == "update_done":
            if self._quit_after_update or not msg.get("reload"):
                self._finish_update()
            else:
                self._load_data(after_update=True)

    def _finish_update(self):
        if self._update_running:
            self._update_running = False
            if self._quit_after_update:
                self._quit_app()
            else:
                self.update_btn.config(state=tk.NORMAL)

    # ==========================================
    # 手动英雄输入
    # ==========================================

    def _on_entry_focus_in(self, event):
        if self.hero_entry.get() == "输入英雄名/拼音...":
            self.hero_entry.delete(0, tk.END)
            self.hero_entry.config(fg=self.TEXT)

    def _on_entry_focus_out(self, event):
        if not self.hero_entry.get().strip():
            self.hero_entry.insert(0, "输入英雄名/拼音...")
            self.hero_entry.config(fg=self.TEXT_DIM)

    def _manual_set_hero(self):
        """手动输入英雄名并锁定"""
        query = self.hero_entry.get().strip()
        if not query or query == "输入英雄名/拼音...":
            return

        if not self.dm or not self.dm.hero_data:
            self._log("❌ 数据未加载")
            return

        # 搜索英雄
        matches, is_exact = self.dm.search_hero(query)

        if not matches:
            self._log(f"❌ 未找到英雄: {query}")
            return

        # 取第一个匹配
        hero_name = matches[0]

        # 如果控制器正在运行，通过控制器设置
        if self.controller and self.engine_running:
            result = self.controller.set_hero(hero_name)
            if result:
                self._log(f"✅ 已锁定: {result}")
                self.hero_entry.delete(0, tk.END)
                self.hero_entry.insert(0, "输入英雄名/拼音...")
                self.hero_entry.config(fg=self.TEXT_DIM)
                self.root.focus()
            else:
                self._log(f"❌ 英雄 [{hero_name}] 不在数据库中")
        else:
            self._log(f"⚠ 请先点击「开始识别」")

    # ==========================================
    # UI 辅助方法
    # ==========================================

    def _set_status(self, text, color):
        self.status_var.set(text)
        self.status_label.config(fg=color)
        self.status_dot.config(fg=color)
        self.status_color = color

    def _start_pulse(self):
        """启动状态指示灯脉冲动画"""
        generation = self._engine_generation
        def _pulse():
            if not self.engine_running or generation != self._engine_generation:
                return
            self._pulse_state = (self._pulse_state + 1) % 20
            # 呼吸灯效果
            brightness = abs(self._pulse_state - 10) / 10.0
            if self.status_color == self.SUCCESS:
                r = int(63 + brightness * 30)
                g = int(185 + brightness * 50)
                b = int(80 + brightness * 30)
                self.status_dot.config(fg=f"#{r:02x}{g:02x}{b:02x}")
            self.root.after(100, _pulse)
        _pulse()

    def _log(self, msg):
        """线程安全的日志方法 (主线程调用)"""
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._append_log(f"[{ts}] {msg}")

    def _log_safe(self, msg):
        """线程安全的日志方法 (后台线程调用)"""
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_queue.put(f"[{ts}] {msg}")

    def _append_log(self, text):
        """向日志面板追加文本"""
        self.log_text.config(state=tk.NORMAL)

        # 根据内容选择颜色
        tag = "info"
        if "✅" in text or "成功" in text:
            tag = "success"
        elif "❌" in text or "失败" in text or "错误" in text:
            tag = "error"
        elif "⚠" in text or "警告" in text:
            tag = "warning"

        self.log_text.insert(tk.END, text + "\n", tag)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    # ==========================================
    # 窗口事件
    # ==========================================

    def _on_close(self):
        """点击关闭按钮"""
        if self.engine_running:
            if messagebox.askyesno("关闭确认",
                    "引擎正在运行中。\n\n"
                    "• 点击「是」关闭程序\n"
                    "• 点击「否」最小化到托盘"):
                self._quit_app()
            else:
                self._minimize_to_tray()
        else:
            self._quit_app()

    def _quit_app(self):
        """完全退出程序"""
        if self._update_running:
            if not self._quit_after_update:
                self._quit_after_update = True
                self._log("数据更新完成后将自动退出。")
            return
        self._engine_cleanup()
        sys.stdout = self._orig_stdout
        sys.stderr = self._orig_stderr
        try:
            self.root.destroy()
        except Exception:
            pass
        os._exit(0)

    def run(self):
        self.root.mainloop()


# ================= 入口点 =================

def _check_admin():
    """检查是否以管理员身份运行"""
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def main():
    try:
        from scripts.window_layout import enable_per_monitor_dpi
        enable_per_monitor_dpi()

        # 管理员权限检查
        if not _check_admin():
            # 非管理员: 弹出提示但仍允许运行
            import ctypes
            result = messagebox.askyesno(
                "权限提示",
                "⚠ 当前未以管理员身份运行。\n\n"
                "以下功能可能无法正常工作:\n"
                "• 自动识别英雄联盟客户端\n"
                "• 全局热键 (F6/F7/F8)\n\n"
                "点击「是」以管理员身份重新启动\n"
                "点击「否」继续以普通用户运行"
            )
            if result:
                # 以管理员重启
                try:
                    exe = sys.executable
                    ctypes.windll.shell32.ShellExecuteW(
                        None, "runas", exe,
                        " ".join(sys.argv) if getattr(sys, 'frozen', False) else f'"{sys.argv[0]}"',
                        None, 1
                    )
                    sys.exit(0)
                except Exception:
                    pass  # 用户取消 UAC, 继续普通运行

        app = LauncherApp()
        app.run()

    except Exception as e:
        try:
            messagebox.showerror("ARAM 海克斯助手 - 启动错误",
                                 f"程序启动时发生错误:\n\n{traceback.format_exc()}")
        except Exception:
            print(f"FATAL: {e}")
            traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
