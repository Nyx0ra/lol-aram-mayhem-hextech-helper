"""
自动化单元测试: 验证 LCU 接口解析、视觉门控与防抖比对
"""
import os
import queue
import sys
from unittest.mock import Mock, patch
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from scripts.lcu_connector import LCUConnector
from gui_launcher import GUIController
from main import DataManager, GameAnalyzer, InputController

def test_champion_resolver():
    print(">>> 测试 1: LCUConnector 英雄名称解析兼容性")
    champ_json = os.path.join(BASE_DIR, "data", "champions.json")
    connector = LCUConnector(champ_json)

    test_cases = [
        ("game_character_displayname_Ezreal", "探险家"),
        ("game_character_displayname_Aatrox", "暗裔剑魔"),
        ("MonkeyKing", "齐天大圣"),
        ("monkeyking", "齐天大圣"),
        ("DrMundo", "祖安狂人"),
        ("AurelionSol", "铸星龙王"),
        ("暗裔剑魔", "暗裔剑魔"),
        ("探险家", "探险家"),
        ("   Ezreal   ", "探险家"),
        ("", None),
        (None, None),
    ]

    for raw, expected in test_cases:
        res = connector._resolve_champion_name(raw)
        assert res == expected, f"Failed for {raw}: got {res}, expected {expected}"
        print(f"  [PASS] {raw} -> {res}")
    print("  -> 英雄名称解析全部通过!\n")


def test_visual_gate_and_fingerprints():
    print(">>> 测试 2: 视觉门控与特征比对流水线")
    dm = DataManager()
    analyzer = GameAnalyzer(dm)

    # 1. 构造纯黑图像 (加载中或全黑界面)
    black_img = np.zeros((80, 200), dtype=np.uint8)
    black_images = {"hex_1": black_img, "hex_2": black_img, "hex_3": black_img}
    black_thumbs = analyzer.compute_thumbnails(black_images)
    passed, reason = analyzer.check_visual_gate(black_thumbs)
    assert not passed, f"纯黑画面应被拦截，但门控通过: {reason}"
    print(f"  [PASS] 纯黑画面成功拦截: {reason}")

    # 2. 构造文字条带 (模拟海克斯卡牌深底白字)
    def make_text_strip():
        arr = np.full((80, 200), 30, dtype=np.uint8)
        # 添加高对比度文字笔画交替
        for x in range(30, 170, 8):
            arr[25:55, x:x+4] = 230
        return arr

    text_img = make_text_strip()
    text_images = {"hex_1": text_img, "hex_2": text_img, "hex_3": text_img}
    text_thumbs_1 = analyzer.compute_thumbnails(text_images)

    # 首次采样文字静止画面 (无上一帧)
    passed, reason = analyzer.check_visual_gate(text_thumbs_1)
    assert passed, f"模拟海克斯文字应通过门控: {reason}"
    print(f"  [PASS] 模拟海克斯文字顺利通过门控: {reason}")

    # 3. 构造高速运动画面 (两帧差异极大，模拟走位/团战技能动画)
    noise_img = np.random.randint(0, 255, (80, 200), dtype=np.uint8)
    noise_images = {"hex_1": noise_img, "hex_2": noise_img, "hex_3": noise_img}
    noise_thumbs = analyzer.compute_thumbnails(noise_images)
    passed_motion, reason_motion = analyzer.check_visual_gate(noise_thumbs, text_thumbs_1)
    assert not passed_motion, "剧烈变动的画面应被稳定度拦截"
    print(f"  [PASS] 剧烈运动画面成功拦截: {reason_motion}")

    # 4. 指纹比对测试 (相同画面判等)
    is_same, diffs = analyzer.is_same_thumbs(text_thumbs_1, text_thumbs_1)
    assert is_same and len(diffs) == 0, "相同画面指纹比对必须为一致"
    print("  [PASS] 静止画面指纹一致性比对通过")

    # 5. 单卡 Reroll 刷新测试 (改变 hex_2)
    reroll_images = {
        "hex_1": text_img,
        "hex_2": np.rot90(text_img),  # 修改第二张卡
        "hex_3": text_img
    }
    reroll_thumbs = analyzer.compute_thumbnails(reroll_images)
    is_same_reroll, diff_keys = analyzer.is_same_thumbs(reroll_thumbs, text_thumbs_1)
    assert not is_same_reroll and diff_keys == ["hex_2"], f"应准确检测出 hex_2 被刷新，但得到: {diff_keys}"
    print(f"  [PASS] 单卡刷新精准识别: 差异卡为 {diff_keys}")

    # 6. 单卡局部增量重算测试 (analyze_partial)
    mock_results = {
        "hex_1": {"key": "hex_1", "valid": True, "overall_rank": 10, "tier": "黄金", "t_rank": 1, "text": "卡1", "highlight": True},
        "hex_2": {"key": "hex_2", "valid": True, "overall_rank": 20, "tier": "白银", "t_rank": 2, "text": "卡2", "highlight": False},
        "hex_3": {"key": "hex_3", "valid": True, "overall_rank": 30, "tier": "棱彩", "t_rank": 3, "text": "卡3", "highlight": False},
    }
    # 模拟 hex_2 变动，重新局部重算
    updated = analyzer.analyze_partial(
        "暗裔剑魔",
        images={"hex_2": np.zeros((80, 200), dtype=np.uint8)},
        existing_results=mock_results,
        target_keys=["hex_2"]
    )
    assert updated["hex_1"]["text"] == "卡1", "hex_1 应该被完整复用"
    assert updated["hex_3"]["text"] == "卡3", "hex_3 应该被完整复用"
    print("  [PASS] analyze_partial 单卡增量复用与重新排名通过")
    print("  -> 视觉门控与防抖测试全部通过!\n")


def test_popup_session_gate():
    print(">>> 测试 3: GUI/CLI 选人、加载和局内的自动弹窗控制")
    sessions = [
        ("ChampSelect", "探险家"),
        ("ChampSelect", "暗裔剑魔"),
        ("GameStart", "暗裔剑魔"),
        ("InProgress", "暗裔剑魔"),
        ("InProgress", "暗裔剑魔"),
        ("InProgress", "暗裔剑魔"),
        ("InProgress", "暗裔剑魔"),
        ("WaitingForStats", None),
        ("Disconnected", None),
    ]
    for controller_class in (GUIController, InputController):
        overlay = queue.Queue()
        dm = Mock(validate_hero=lambda hero: hero)
        analyzer = Mock()
        poller = Mock()
        phase = None
        captures = []
        commands = []
        frame = {f"hex_{i}": np.ones((10, 32), dtype=np.float32) for i in range(1, 4)}
        valid = {key: {"valid": True, "score": 100} for key in frame}

        def capture():
            captures.append(phase)
            return "stable-layout", frame

        analyzer.capture_window_frame.side_effect = capture
        analyzer.compute_thumbnails.side_effect = lambda images: images
        analyzer.check_visual_gate.return_value = (True, "passed")
        analyzer.is_same_thumbs.return_value = (True, [])
        analyzer.analyze.side_effect = [{}, valid]
        if controller_class is GUIController:
            controller = controller_class(overlay, queue.Queue(), dm, analyzer, None, auto_popup=True)
            controller.session_poller = poller
            listen = controller._run_loop
        else:
            controller = controller_class(overlay, dm, analyzer, None, auto_popup=True)
            listen = lambda: controller._listen(poller)

        ticks = iter(enumerate(sessions, 1))

        def next_tick():
            nonlocal phase
            tick, (phase, hero) = next(ticks)
            poller.snapshot = (phase, hero, "LCU", None)
            if controller_class is GUIController and tick == len(sessions):
                controller.stop()
            return float(tick)

        def put_overlay(message):
            commands.append((phase, message))

        module = sys.modules[controller_class.__module__]
        with patch.object(overlay, "put", side_effect=put_overlay), \
                patch.object(module, "time") as clock, \
                patch.object(module.keyboard, "is_pressed", return_value=False):
            clock.monotonic.side_effect = next_tick
            try:
                listen()
            except StopIteration:
                pass

        assert captures == ["InProgress"] * 4, captures
        updates = [(phase, msg) for phase, msg in commands if msg["cmd"] == "UPDATE"]
        assert len(updates) == 1 and updates[0][1]["opening"]
        assert updates[0][0] == "InProgress"
        assert analyzer.analyze.call_count == 2
        assert ("WaitingForStats", {"cmd": "CLEAR"}) in commands
        assert controller.current_hero == "暗裔剑魔"
        print(f"  [PASS] {controller_class.__name__} 仅在局内采样，拒绝静默，首次确认后推荐，退出后清屏")


if __name__ == '__main__':
    test_champion_resolver()
    test_visual_gate_and_fingerprints()
    test_popup_session_gate()
    print("🎉 全部自动化测试通过!")
