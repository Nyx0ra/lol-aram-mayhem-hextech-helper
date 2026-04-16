# LOL ARAM Mayhem Hextech Helper (大乱斗海克斯助手)

![Python](https://img.shields.io/badge/Python-3.9%2B-blue)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D6)
![License](https://img.shields.io/badge/License-MIT-green)

一个基于 **计算机视觉 (OCR)** 和 **大数据分析** 的英雄联盟极地大乱斗 (ARAM) 辅助工具。
它能自动识别游戏内的海克斯强化符文，并根据胜率数据提供最佳选择建议。

 <p align="center">
      <img src="./docs/demo.png" width="600" alt="功能演示">
      <br>
      <em>图：海克斯识别与颜色提示效果展示</em>
    </p>

> **数据来源说明**: 本项目的数据抓取自 [OP.GG](https://op.gg/zh-cn/lol/modes/aram-mayhem)。本工具仅供学习交流使用。

---

## ⚠️ 核心前置条件 (Prerequisites)

1.  **管理员身份运行**: 程序涉及全局热键监听和截取客户端进程信息，**建议以管理员身份**运行 Python 或终端（便于 `psutil` 读取游戏进程状态）。
2.  **游戏显示模式**: 必须设置为 **“无边框” (Borderless)**。
3.  **游戏进程状态**: **推荐先启动英雄联盟客户端并登录**，程序能够自动连接游戏本地服务 (LCU API) 以获取当前英雄。
4.  **屏幕分辨率**: 当前版本默认适配 **2560 x 1440 (2K)** 分辨率。

---

## ✨ 功能特性 (Features)

*   **🛡️ 实时遮罩**: 在游戏界面上直接显示推荐结果，无需切屏。
*   **🔌 本地 API 联通**: 自动扫描并连接英雄联盟客户端 (LCU)，自动识别你在选人阶段摇到的英雄。
*   **👁️ 自动识别**: 使用 `RapidOCR` 毫秒级识别屏幕上的三个海克斯选项。
*   **🤖 智能推荐**: 自动计算“白银/黄金/棱彩”海克斯的优先级。
*   **🎹 极简交互**: 全程依托键盘快捷键 (`F6`, `F7`, `F8`) 完成。
*   **🔄 数据自动维护**: 内置爬虫脚本，支持增量更新数据。

---

## 🛠️ 安装指南 (Installation)

1.  **克隆仓库**:
    ```bash
    git clone https://github.com/Nyx0ra/lol-aram-mayhem-hextech-helper.git
    cd lol-aram-mayhem-hextech-helper
    ```

2.  **安装依赖**:
    ```bash
    pip install -r requirements.txt
    ```

> [!NOTE]
> **关于客户端路径配置**：
> 绝大多数情况下程序能够通过 `psutil` 跨盘符自动扫描到你的游戏。但如果你启动后发现**无法自动识别英雄**，可能是由于权限受限，此时请打开 `scripts/lcu_connector.py`，并在文件开头的 `COMMON_INSTALL_PATHS` 列表中添加你的真实英雄联盟安装路径（例如：`r"E:\Game\英雄联盟"`）。

---

## 🚀 使用教程 (Usage)

### 第一步：启动程序
右键点击你的 IDE (如 VS Code) 或终端，选择 **“以管理员身份运行”**（获取正确的权限以扫描进程），执行：
```bash
python main.py
```

### 第二步：识别/选择英雄
程序启动后会弹出一个控制台窗口，进行如下处理：
1. **自动识别（推荐）**：系统会自动尝试利用 LCU API 连接游戏客户端。若在英雄选择界面中检测到英雄，会直接自动锁定并最小化控制台。
2. **手动输入（备用）**：若不在选人阶段或连接失败，则切换到手动模式。输入英雄名称（支持中文拼音首字母，如 `探险家 伊泽瑞尔`为`txj`），回车确认即可自动模糊匹配并锁定。
3. 锁定后，控制台会自动最小化，你可以切回游戏了。

### 第三步：游戏中操作
* **`F6` - 识别并分析**: 当弹出海克斯选择界面时按下 **F6**。Overlay 中心会显示当前正在分析的英雄名。
    * <span style="color:gold">**金色文字**</span>：最优推荐。
    * <span style="color:green">**绿色文字**</span>：普通推荐。
    * <span style="color:red">**红色文字**</span>：未识别/无数据。

* **`F7` - 刷新英雄**: 按下 **F7** 查看当前英雄并展示在顶层。
    * 在选人阶段：如果骰子或交换了英雄，会自动刷新为最新英雄。
    * 在加载中/游戏内：展示当前已锁定的英雄名（此时到 LCU 选人 API 已关闭）。

* **`F8` - 重置/下一局**: 想手动更换英雄或重新进入输入流程时按下 **F8**，控制台会重新弹出。

---

## ⚙️ 高级配置 (分辨率适配)

如果你使用非 2K 分辨率，需修改 `main.py` 顶部的 `REGIONS`：

```python
REGIONS = {
    "hex_1": {'top': 540, 'left': 650,  'width': 320, 'height': 60},
    "hex_2": {'top': 540, 'left': 1130, 'width': 320, 'height': 60},
    "hex_3": {'top': 540, 'left': 1600, 'width': 320, 'height': 60}
}
```
*提示：请使用截图工具测量你屏幕上海克斯文字的坐标并替换。*

---

## 📂 文件结构说明

* `main.py`: 主程序（GUI 遮罩、按键监听、程序逻辑）。
* `scripts/lcu_connector.py`: 英雄联盟本地 API 通信模块。
* `scripts/hero_scraper.py`: 爬虫脚本（基于 Selenium 抓取数据）。
* `scripts/updater.py`: 数据同步工具（手动触发更新、合并数据）。
* `data/hero_augments.csv`: 核心数据库。

## 📄 License

MIT License.
