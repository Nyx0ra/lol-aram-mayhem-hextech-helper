# LOL ARAM Mayhem Hextech Helper (大乱斗海克斯助手)

![Python](https://img.shields.io/badge/Python-3.9~3.12-blue)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D6)
![License](https://img.shields.io/badge/License-MIT-green)

一个基于 **计算机视觉 (OCR)** 和 **大数据分析** 的英雄联盟极地大乱斗 (ARAM) 辅助工具。
它能自动同步当前英雄。游戏内出现海克斯三选一时按 `F6`，助手读取卡名并显示基于胜率的推荐。

 <p align="center">
      <img src="./docs/demo.png" width="800" alt="游戏内演示效果">
      <br>
      <em>图：海克斯识别与颜色提示效果展示（游戏内）</em>
    </p>

 <p align="center">
      <img src="./docs/demo_main.png" width="400" alt="主界面功能演示">
      <img src="./docs/demo_update.png" width="400" alt="高级更新界面演示">
      <br>
      <em>图：主界面与数据更新面板（旧版界面示意）</em>
    </p>

> **数据来源说明**: 本项目的数据抓取自 [OP.GG](https://op.gg/zh-cn/lol/modes/aram-mayhem)。本工具仅供学习交流使用。

---

## 📖 开发者与接手文档导航 (Docs Navigation)

* [AGENTS.md](AGENTS.md) 维护模块分工、环境约束和识别流程。
* [STATUS.md](STATUS.md) 记录当前发布状态、验证范围、待办和复现步骤。

---

## ⚠️ 核心前置条件 (Prerequisites)

1.  **管理员身份运行**: 程序涉及全局热键监听和截取客户端进程信息，**建议以管理员身份**运行解压后的 `ARAMHelper.exe`（开发者请以管理员权限运行 Python 或所在终端）。
2.  **游戏显示模式**: 必须设置为 **“无边框” (Borderless)**。
3.  **游戏进程状态**: **推荐先启动英雄联盟客户端并登录**，程序能够自动连接游戏本地服务 (LCU API) 以获取当前英雄。
4.  **屏幕分辨率**: 程序按前台游戏窗口的客户区定位截图和推荐，使用以 2K 为基准的比例坐标。

---

## ✨ 功能特性 (Features)

*   **⚡ 全自动对局与英雄感知**: 深度打通官方 **LCU API** 与 **Live Client Data API (2999 端口)**，在选人阶段摇骰子/队友换人、进入对局、跨局游戏全程自动无感同步英雄，**免去手动按 `F7` 的繁琐操作**。
*   **⚡ F6 海克斯推荐**：卡名可读时按 `F6` 识别，结果直接显示在卡牌上方。重掷后等新卡名显示，再按一次 `F6`。
*   **🛡️ 实时悬浮遮罩**: 在游戏界面中央无边框等比对齐显示推荐结果，支持 1080p / 2K / 4K 分辨率无缝适配。
*   **🤖 智能算法推荐**: 自动结合白银/黄金/棱彩阶位以及总胜率排名排序（基于 OP.GG 等综合胜率数据）。
*   **🎹 全局快捷键**: `F6` 识别海克斯，`F7` 手动刷新英雄，`F8` 重置并清空推荐。
*   **🔄 数据自动维护 (全新面板)**: 
    *   自带四维进化爬虫：**抽样校验**（极速防呆）、**智能增量**（补充新英雄）、**全量更新**、**精确打击**（支持中英拼音模糊搜找单英雄修补）。
    *   内置 GitHub 静态库在线下载兜底，无缝拯救本地无 Chrome 和网络受限的玩家环境。
    *   官方英雄接口暂时不可用时自动重试，持续失败则使用已校验的本地英雄数据继续更新；本次无法检查新英雄或改名。
    *   更新期间关闭程序会取消剩余抓取，保留已保存的数据；当前网络请求和文件保存结束后退出。

---

## 🛠️ 安装指南 (Installation)

1.  **克隆仓库**:
    ```bash
    git clone https://github.com/Nyx0ra/lol-aram-mayhem-hextech-helper.git
    cd lol-aram-mayhem-hextech-helper
    ```

2.  **创建环境与安装依赖**:
    * **推荐方式 (使用 `uv`，自动隔离并配置 Python 3.12)**:
      ```bash
      uv venv --python 3.12
      uv pip install -r requirements.txt
      ```
    * **传统方式 (`pip`)**:
      ```bash
      pip install -r requirements.txt
      ```

> [!WARNING]
> **Python 版本要求**：本项目依赖的 `rapidocr_onnxruntime` 仅支持 **Python 3.9 ~ 3.12**。使用 Python 3.13+ 会导致 OCR 引擎初始化报错 (`KeyError: 'model_path'`)。请使用 [Python 3.12](https://www.python.org/downloads/release/python-3129/) 或更低版本。

> [!NOTE]
> **关于客户端路径配置**：
> 绝大多数情况下程序能够通过 `psutil` 跨盘符自动扫描到你的游戏。但如果你启动后发现**无法自动识别英雄**，可能是由于权限受限，此时请打开 `scripts/lcu_connector.py`，并在文件开头的 `COMMON_INSTALL_PATHS` 列表中添加你的真实英雄联盟安装路径（例如：`r"E:\Game\英雄联盟"`）。

---

## 🚀 使用教程 (Usage)

本工具为不同需求的用户提供了两个版本的使用方式，请根据你的情况选择：

### 版本一：免安装直接运行版 (适合小白/绝大多数玩家)

不需要任何本地环境配置，直接下载即可：

1. **获取程序**：前往 [Releases 页面](https://github.com/Nyx0ra/lol-aram-mayhem-hextech-helper/releases) 下载最新版本的压缩包（例如 `ARAMHelper_vX.X.x.zip`）。
2. **解压及运行**：完整解压 ZIP，以管理员身份运行 `ARAMHelper.exe`，等待数据加载后点击「开始识别」。
3. **操作指南**：
   * **全自动英雄识别 (免按 F7)**：选人、换将和进入对局时，助手通过客户端接口自动同步英雄。
   * **海克斯推荐**：三选一弹窗打开后按 `F6`。如果刚开窗或刚重掷，请等新标题清晰可见再识别。推荐会自动隐藏。
   * **备选方案（手动锁定）**: 如果英雄联盟客户端权限受阻未连上，你也可以直接在界面输入框输入英雄**称号的首字母缩写**来秒猜锁定。例如输入 `txj`（探险家）、`jfjh`（疾风剑豪）、`xjch`（迅捷斥候）。
   * **`F6` - 手动强制分析**: 随时强制截屏并分析当前英雄的海克斯推荐。
   * **`F7` - 手动强制刷新**: 强制触发接口重新连接客户端并刷新当前英雄。
   * **`F8` - 重置状态**: 重置后台跟踪状态并清空遮罩。

*(注：如果你想要更新本程序的胜率数据库，只需在主界面点击“**数据更新**”。推荐直接使用“抽样校验”或者兜底的“Github下载”保持同频。)*

---

### 版本二：本地源码部署版 (适合开发者/极客使用)

如果你想自己参与研究并直接基于源码运行：

1. **准备环境**：请确保已依照上述“安装指南”克隆了代码仓库，并在本地配置好的 Python 环境内安装了 `requirements.txt`。
2. **启动程序**：右键点击你的 IDE终端或 CMD，选择 **“以管理员身份运行”**，执行全新的图形可视化客户端：
   ```bash
   # uv 方式 (推荐)
   uv run python gui_launcher.py

   # 传统方式
   python gui_launcher.py
   ```
   等待数据加载后点击「开始识别」。测试和打包步骤见 [STATUS.md](STATUS.md#6-复现与验证指南-reproduction--verification)。
3. **数据更新 (四维自动爬虫化)**：
    * 终端版内置了最前沿的爬虫代码。在主界面点击 **数据更新** 会呼出专用管理员面板：
      * **抽样校验 (推荐)**：随机抽取 3 名英雄进行云端数据比对，一旦发现版本落后自动触发批量升级！
      * **智能增量**：专门用于抓取新上线的英雄或发生改名的英雄。
      * **全量更新**：数据库清空时大更新专用。
      * **精确更新**：输入类似“ez”、“女警” 等简拼或者外号，后台将自动执行模糊匹配为你单抓一条数据。
      * 此外还有无浏览器的 **GitHub 本地下载兜底**供你救急调用。

---

## ⚙️ 高级配置 (分辨率适配)

程序按前台游戏窗口客户区换算截图和悬浮窗坐标。超宽客户区按居中的 16:9 内容区计算，实际游戏布局可能随显示模式变化。

> [!NOTE]
> 自动适配要求游戏以 **无边框窗口** 模式运行。若识别区域出现偏移，可检查上述设置。

---

## 📂 文件结构说明

* `gui_launcher.py`: 现代化 GUI 启动器（GitHub Dark 主题、托盘后台控制、独立打包入口）。
* `main.py`: 核心算法与悬浮窗实现（透明置顶遮罩、两阶段视觉门控、OCR 推理、状态控制）。
* `build.py`: PyInstaller 一键轻量化打包脚本（依赖修剪、资源收集与目录隔离）。
* `scripts/config.py`: 统一路径常量解析器（兼容源码与打包冻结运行环境）。
* `scripts/lcu_connector.py`: 英雄联盟本地 LCU 与 Live Client Data API 通信模块。
* `scripts/updater.py`: 四维数据自动同步与 GitHub 云端拉取工具。
* `scripts/hero_scraper.py`: 基于 Selenium 的海克斯符文爬虫核心。
* `data/hero_augments.csv`: 英雄海克斯胜率与梯队核心数据库。
* `tests/test_auto_detect.py`: 自动化单元测试套件（覆盖英雄解析、门控与增量算法）。
* `AGENTS.md`: 面向开发者/AI 的长期架构与开发约定。
* `STATUS.md`: 当前任务进度、卡点边界、决策记录与踩坑复盘。

## 📄 License

MIT License.
