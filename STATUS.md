# ARAM 海克斯助手 · 现状、任务与踩坑记录 (STATUS.md)

> 本文档用于记录项目的**当前开发态、卡点与边界、下一步行动计划、重要架构决策与历史踩坑**。
> 长期技术约定与系统规范请参阅 [AGENTS.md](AGENTS.md)。
> 用户指南与快速启动请参阅 [README.md](README.md)。

---

## 1. 当前状态概览 (Current State)

* **发布状态**：2026-11-06 将 `feat/auto-detection-fast-track` 合并到 `main`，合并提交 `995aad4` 是 `v3.4.0` 的发布基线。此版加入 LCU 英雄同步、局内自动推荐和单卡重掷识别。[GitHub Release v3.4.0](https://github.com/Nyx0ra/lol-aram-mayhem-hextech-helper/releases/tag/v3.4.0)
* **已验证**：发布基线在 Windows 和 Python 3.12 上通过 [自动化回归测试](tests/test_auto_detect.py)。源码下的 Tk 检查验证了首次提示切换到推荐，以及清屏能取消待显示的推荐。发布 ZIP 通过解包完整性检查，包含 EXE、英雄数据和 3 个 OCR 模型，GitHub 上传后的 SHA-256 与本地一致。
* **待验证**：最终发布包的 EXE 启动和实局表现待复测。此前曾启动较早的构建并收到实局反馈，问题及修复见下文。

---

## 2. 卡点、约束与已知边界 (Blockers & Boundaries)

| 序号 | 类别 | 问题 / 边界 | 影响范围 | 当前对策与规避方案 |
| :---: | :---: | :--- | :--- | :--- |
| **B-1** | 环境兼容 | Python 3.13+ 环境下 `rapidocr_onnxruntime` 崩溃 | 开发者在 Python 3.13 运行报错 | **严格限制 Python 3.9 ~ 3.12**。在 README、AGENTS 和 requirements.txt 中明确警示；推荐使用 `uv` 隔离环境。 |
| **B-2** | 系统权限 | Windows 下非管理员权限导致全局热键与客户端扫描失效 | 无法截取游戏按键 / LCU 进程读取被拒 | 程序内置打包参数 `--uac-admin`；启动说明中醒目提示“以管理员身份运行”。 |
| **B-3** | 游戏设置 | 游戏窗口处于“全屏独占模式”时置顶遮罩失效且截图黑屏 | 悬浮窗无法置顶显示推荐 | 必须设置为 **“无边框” (Borderless)** 窗口模式。 |
| **B-4** | 屏幕比例 | 超宽屏（如 21:9、32:9 带鱼屏）的非标准黑边渲染 | 识别区域可能发生横向偏移 | 目前基于 16:9 / 2K 坐标等比适配（涵盖 1080p/2K/4K），对于带鱼屏建议玩家在有黑边模式下居中运行，后续考虑增加宽高比自适应。 |

---

## 3. 下一步任务清单 (Next Steps)

- [ ] **T-1. v3.4.0 实局复测**。用发布 ZIP 按下文步骤验证开局、悬停和单卡重掷。
- [ ] **T-2. 超宽屏 (21:9 / 32:9) 自动适配扩展**:
  - 探测物理分辨率宽高比，当非 16:9 时自动计算黑边偏移量或提供居中补偿。
- [ ] **T-3. 数据爬虫稳定性增强**:
  - 为 `scripts/hero_scraper.py` 增加 OP.GG 页面结构变动的降级 fallback。
  - 在无外网网络或 Chrome 驱动失效时，引导使用内置的 GitHub 备选数据源更新。

---

## 4. 决策记录 (Architecture Decision Records - ADR)

### ADR-1: 视觉感知采用“两阶段门控流水线”而非持续 OCR
* 连续运行 OCR 会增加 CPU 开销，因此先用低分辨率指纹筛选候选画面，再做 OCR 确认。具体门控和显示规则见 [AGENTS.md](AGENTS.md#32-局内海克斯两阶段视觉门控流水线-fast-track)。

### ADR-2: 单卡 Reroll 增量局部重算
* 重掷只需识别变动卡牌，因此其余卡牌沿用已确认的结果，最后一起重新排序。悬停也会改变画面，故需要用 OCR 核对卡名。流程见 [AGENTS.md](AGENTS.md#33-单卡-reroll-局部增量刷新流水线)。

### ADR-3: 打包构建的时间戳目录隔离防死锁策略
* **背景**: 在 Windows 系统上反复执行 PyInstaller 打包时，若旧的 `dist/ARAMHelper/` 正在被测试运行或被防病毒/资源管理器锁定，打包会直接报错 `PermissionError: [WinError 5] 拒绝访问`。
* **决策**：每次构建使用带时间戳的独立分发目录，避免覆盖仍被占用的旧 EXE。脚本完成后会尝试清理旧构建，锁定的目录可能保留。

---

## 5. 失败与踩坑实录 (Failures & Lessons Learned)

### 踩坑 1: Python 3.13 安装 `rapidocr_onnxruntime` 报 `KeyError: 'model_path'`
* **现象**: 在 Python 3.13 虚拟环境下启动程序时，OCR 初始化直接报错退出。
* **排查**: `rapidocr_onnxruntime` 内部依赖的资源文件定位在 3.13 中由于标准库模块导入变动无法正确定位内置 ONNX 模型文件。
* **解决**：将 OCR 依赖限定为 `rapidocr_onnxruntime<=1.4.4`。初始化失败时输出 Python 版本和兼容提示。开发环境见 [AGENTS.md](AGENTS.md)。

### 踩坑 2: PyInstaller 打包时意外卷入 PyTorch 导致体积暴增数 GB
* **现象**: 本地安装过 PyTorch 时，打包产物急剧膨胀到 3GB 以上，打包时间超过 10 分钟。
* **排查**: `onnxruntime` 的某些非必要子模块（如 `onnxruntime.transformers`）隐式依赖了 torch。
* **解决**: 在 `build.py` 的 `EXCLUDES` 列表中强制排除 `torch`、`torchvision`、`scipy`、`pandas` 等大型科学计算库，并对 `onnxruntime` 仅收集数据文件与二进制，打包产物体积压降至 150MB 左右。

### 踩坑 3: LCU 返回前缀英雄名 `game_character_displayname_xxx` 导致匹配落空
* **现象**: 部分对局中通过 LCU 或 Live API 获取到的英雄名为 `game_character_displayname_Ezreal` 或 `MonkeyKing`，导致在数据库中查找“None”。
* **排查**: 英雄联盟客户端不同阶段（选人与局内）返回的英雄标识字段规范不统一。
* **解决**: 在 [`scripts/lcu_connector.py`](scripts/lcu_connector.py) 中编写统一解析函数 `_resolve_champion_name()`，支持前缀剥离、大小写归一化、下划线去除以及双向对照转换，并在 [`tests/test_auto_detect.py`](tests/test_auto_detect.py) 中补齐 11 种极端边界用例。

### 踩坑 4: 选人时误触发“正在分析推荐”
* **原因**: GUI 的阶段排除列表漏掉了选人和加载，CLI 未检查阶段。选人画面的文字能通过视觉预筛，在 OCR 确认前触发提示。OCR 拒绝后，GUI 主窗口也未恢复监听状态。
* **阶段修复**: 两个入口仅在 `InProgress` 阶段自动扫描，退出该阶段时清除遮罩和识别缓存。阶段未知或断连时可按 F6 手动分析。
* **实测反馈与后续修复**: 用户仍观察到重复提示。局内普通画面也可能通过视觉预筛，画面变化后又会绕过上一帧的负样本缓存。初次识别改为在后台确认，达到置信度后直接显示推荐。三张卡同时变化时丢弃旧结果，重新确认弹窗。
* **验证**: 自动化测试覆盖选人换英雄、加载、局内连续拒绝不弹窗、有效推荐、单卡重掷、整屏变化和离开对局清屏。

### 踩坑 5: 开局缺少进度提示，重掷时提示和结果交替闪烁
* **实测反馈**：首次提示曾在进游戏时提前出现，也曾在真正弹窗时缺失。重掷动画会让结果和提示交替闪烁，鼠标悬停也曾触发刷新提示。
* **修复**：把首次提示绑定到首个通过 OCR 确认的弹窗，并由界面计时。重掷等待画面稳定后核对卡名，再决定是否更新。现行规则见 [AGENTS.md](AGENTS.md#32-局内海克斯两阶段视觉门控流水线-fast-track)。

---

## 6. 复现与验证指南 (Reproduction & Verification)

### 6.1 准备输入与运行回归

按 [README.md](README.md) 配置 Windows 下的源码环境。从仓库根目录运行以下命令。测试使用仓库内的英雄与海克斯数据、合成图像及模拟会话。

```powershell
# 运行自动化单元测试
uv run python tests/test_auto_detect.py
# 或使用已有环境直接执行:
.\.venv\Scripts\python.exe tests/test_auto_detect.py
```
通过时退出码为 0，最后输出「全部自动化测试通过」。覆盖英雄解析、视觉门控、会话阶段、首次提示、悬停和重掷。

### 6.2 打包和检查

```powershell
uv run python build.py
```
产物位于 `dist/build_<timestamp>/ARAMHelper/`。检查其中的 `data/` 和 `_internal/rapidocr_onnxruntime/`，再启动 `ARAMHelper.exe`，点击「开始识别」检查主窗口、数据加载和 OCR 初始化。发布时压缩整个 `ARAMHelper` 目录。

### 6.3 实局复测

1. 完整解压发布 ZIP，以管理员身份启动并点击「开始识别」。游戏使用无边框窗口。
2. 选人时换一次英雄。预期英雄同步，选人和进游戏的过渡画面没有分析提示。
3. 等首个海克斯三选一出现。预期先显示一次分析提示，再显示推荐。
4. 将鼠标依次悬停在卡牌上，再重掷其中一张。预期悬停时推荐保持，重掷后仅变动卡显示刷新提示并更新推荐。
5. 选择一张卡关闭弹窗，确认遮罩清除。若有偏差，记录使用的构建、分辨率、显示模式及复现步骤。
