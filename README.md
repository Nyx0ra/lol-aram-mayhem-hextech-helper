# LOL ARAM Mayhem Hextech Helper (大乱斗海克斯助手)

![Python](https://img.shields.io/badge/Python-3.9%2B-blue)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D6)
![License](https://img.shields.io/badge/License-MIT-green)

一个基于 **计算机视觉 (OCR)** 和 **大数据分析** 的英雄联盟极地大乱斗 (ARAM) 辅助工具。
它能自动识别游戏内的海克斯强化符文，并根据胜率数据提供最佳选择建议。

> **数据来源说明**: 本项目的数据抓取自 [Blitz.gg](https://blitz.gg/lol/tierlist/aram-mayhem)。本工具仅供学习交流使用。

---

## ⚠️ 核心前置条件 (Prerequisites)

1.  **管理员身份运行**: 程序涉及全局热键监听和截图，**必须以管理员身份**运行 Python 或终端。
2.  **游戏显示模式**: 必须设置为 **“无边框” (Borderless)**。
3.  **屏幕分辨率**: 当前版本默认适配 **2560 x 1440 (2K)** 分辨率。

---

## ✨ 功能特性 (Features)

*   **🛡️ 实时遮罩**: 在游戏界面上直接显示推荐结果，无需切屏。
*   **👁️ 自动识别**: 使用 `RapidOCR` 毫秒级识别屏幕上的三个海克斯选项。
*   **🤖 智能推荐**: 自动计算“白银/黄金/棱彩”海克斯的优先级。
*   **🎹 极简交互**: 仅需两个热键 (`F6`, `F8`) 即可完成所有操作。
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

---

## 🚀 使用教程 (Usage)

### 第一步：启动程序
右键点击你的 IDE (如 VS Code) 或终端，选择 **“以管理员身份运行”**，执行：
```bash
python main.py
```

### 第二步：选择英雄
程序启动后会弹出一个控制台窗口：
1. 输入英雄名称（支持中文拼音首字母，如 `伊泽瑞尔`为`yzre`）。
2. 回车确认，系统会自动模糊匹配并锁定英雄。
3. 控制台会自动最小化，你可以切回游戏了。

### 第三步：游戏中操作
* **`F6` - 识别并分析**: 当弹出海克斯选择界面时按下 **F6**。
    * <span style="color:gold">**金色文字**</span>：最优推荐。
    * <span style="color:green">**绿色文字**</span>：普通推荐。
    * <span style="color:red">**红色文字**</span>：未识别/无数据。
* **`F8` - 重置/下一局**: 想更换英雄时按下 **F8**，控制台会重新弹出。

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

* `main.py`: 主程序（GUI 遮罩、按键监听）。
* `aram_crawler_final.py`: 爬虫脚本（基于 Selenium 抓取数据）。
* `auto_update_manager.py`: 数据同步工具（合并数据）。
* `aram_augments_final.csv`: 核心数据库。

## 📄 License

MIT License.
