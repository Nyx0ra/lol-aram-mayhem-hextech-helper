# ARAM 海克斯助手 · 现状、任务与踩坑记录 (STATUS.md)

> 本文档用于记录项目的**当前开发态、卡点与边界、下一步行动计划、重要架构决策与历史踩坑**。
> 长期技术约定与系统规范请参阅 [AGENTS.md](AGENTS.md)。
> 用户指南与快速启动请参阅 [README.md](README.md)。

---

## 1. 当前状态概览 (Current State)

* **v3.4.0 内容**：LCU 自动同步英雄、局内两阶段海克斯识别、单卡重掷增量 OCR，以及首次弹窗提示和悬停误刷新修复。
* **本地测试验证状态**:
  * 自动化测试套件 [`tests/test_auto_detect.py`](tests/test_auto_detect.py) **全部通过** (通过项：英雄名解析兼容性、纯黑画面拦截、文字梯度门控、高速运动画面拦截、静止画面指纹一致性、单卡 Reroll 刷新检测、`analyze_partial` 增量重算)。

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

- [ ] **T-2. 超宽屏 (21:9 / 32:9) 自动适配扩展**:
  - 探测物理分辨率宽高比，当非 16:9 时自动计算黑边偏移量或提供居中补偿。
- [ ] **T-3. 数据爬虫稳定性增强**:
  - 为 `scripts/hero_scraper.py` 增加 OP.GG 页面结构变动的降级 fallback。
  - 在无外网网络或 Chrome 驱动失效时，引导使用内置的 GitHub 备选数据源更新。

---

## 4. 决策记录 (Architecture Decision Records - ADR)

### ADR-1: 视觉感知采用“两阶段门控流水线”而非持续 OCR
* **背景**: 海克斯弹窗每次出现仅有一次选择机会，玩家希望“免按 F6 全自动弹出”。但 OCR 推理单次耗时 150~300ms 且占用 CPU，若每秒轮询 OCR 会造成游戏掉帧。
* **决策**: 引入轻量级两阶段门控：
  1. **Stage 1 (门控，<2ms)**: 将 3 区域降采样为 32×10 灰度缩略矩阵，快速计算帧间绝对差值（过滤运动团战）与水平边缘梯度（过滤无文字区域/全黑画面）。
  2. **Stage 2 (Fast-Track 极速确认)**: 门控通过且未命中负样本时，后台等待 60ms 再执行 OCR。达到卡牌匹配置信度后直接显示推荐。
* **成效**: 空闲监听时 CPU 占用 < 0.5%，弹窗弹出时从出现到识别推荐响应在 0.2s 内。

### ADR-2: 单卡 Reroll 增量局部重算
* **背景**: 玩家对其中一张卡牌点击“掷骰子”刷新时，如果全量 3 张卡重新截图识别，会导致界面重新闪烁且多耗费两倍时间。
* **决策**: 借助 `is_same_thumbs()` 对比三张卡牌的历史缩略图指纹，返回具体的差异卡牌键（如 `['hex_2']`）。只对变动卡截屏识别，复用其余未变动卡牌的旧文本与排名，最后统一由 `analyze_partial()` 重新计算综合排名。
* **成效**: 刷新单卡识别耗时减少约 70%，UI 体验丝滑无跳跃感。

### ADR-3: 打包构建的时间戳目录隔离防死锁策略
* **背景**: 在 Windows 系统上反复执行 PyInstaller 打包时，若旧的 `dist/ARAMHelper/` 正在被测试运行或被防病毒/资源管理器锁定，打包会直接报错 `PermissionError: [WinError 5] 拒绝访问`。
* **决策**: 在 `build.py` 中引入 `build_{timestamp}` 唯一分发目录构建，生成成功后再执行最终原子替换或输出独立版本包。
* **成效**: 彻底消除了打包过程中的文件占用报错。

---

## 5. 失败与踩坑实录 (Failures & Lessons Learned)

### 踩坑 1: Python 3.13 安装 `rapidocr_onnxruntime` 报 `KeyError: 'model_path'`
* **现象**: 在 Python 3.13 虚拟环境下启动程序时，OCR 初始化直接报错退出。
* **排查**: `rapidocr_onnxruntime` 内部依赖的资源文件定位在 3.13 中由于标准库模块导入变动无法正确定位内置 ONNX 模型文件。
* **解决**: 在 `requirements.txt` 中固定约束，代码中加入 `sys.version_info` 预检友好拦截，并在开发规范中明文约定使用 Python 3.12。

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
* OCR 确认首个海克斯弹窗后，由界面显示 650ms 的分析提示。直接读到对局中也会启用首轮提示。
* 重掷时等卡面稳定后核对卡名。鼠标悬停导致画面改变但卡名一致时，仅更新比对指纹。确认卡名变化后才显示刷新提示并更新推荐。

---

## 6. 复现与验证指南 (Reproduction & Verification)

### 6.1 依赖安装与环境初始化
```bash
# 推荐方式 (使用 uv 创建 3.12 干净隔离环境)
uv venv --python 3.12
uv pip install -r requirements.txt
```

### 6.2 运行全套单元测试
```powershell
# 运行自动化单元测试
uv run python tests/test_auto_detect.py
# 或使用已有环境直接执行:
.\.venv\Scripts\python.exe tests/test_auto_detect.py
```
**预期通过输出**:
```text
>>> 测试 1: LCUConnector 英雄名称解析兼容性
  [PASS] game_character_displayname_Ezreal -> 探险家
  [PASS] MonkeyKing -> 齐天大圣
  ...
  -> 英雄名称解析全部通过!

>>> 测试 2: 视觉门控与特征比对流水线
  [PASS] 纯黑画面成功拦截: low_contrast (0/3 regions)
  [PASS] 模拟海克斯文字顺利通过门控: passed
  [PASS] 剧烈运动画面成功拦截: moving (diff=75.1)
  [PASS] 静止画面指纹一致性比对通过
  [PASS] 单卡刷新精准识别: 差异卡为 ['hex_2']
  [PASS] analyze_partial 单卡增量复用与重新排名通过
  -> 视觉门控与防抖测试全部通过!

🎉 全部自动化测试通过!
```

### 6.3 启动本地开发界面
```powershell
# 以管理员权限打开终端后运行:
uv run python gui_launcher.py
```

### 6.4 执行打包验证
```powershell
uv run python build.py
```
构建产物将输出至 `dist/ARAMHelper/`（或当前时间戳目录），可直接运行 `ARAMHelper.exe` 验证启动与托盘功能。
