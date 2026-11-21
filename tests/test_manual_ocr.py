import json
import queue
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np

from gui_launcher import GUIController
from main import DataManager, GameAnalyzer
from scripts.window_layout import CARD_LEFTS, GameLayout, ScreenRect


def ocr_box(text, left=60, top=30, width=90, height=35, confidence=0.99):
    return ([[left, top], [left + width, top],
             [left + width, top + height], [left, top + height]], text, confidence)


class ManualOCRTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = GameAnalyzer.__new__(GameAnalyzer)
        self.analyzer._use_parallel = False
        self.analyzer.dm = DataManager.__new__(DataManager)
        self.analyzer.dm.hero_data = {
            "英雄": {
                "珠光护手": {"tier": "黄金", "overall_rank": 2, "t_rank": 1},
                "心灵净化": {"tier": "黄金", "overall_rank": 1, "t_rank": 2},
                "升级：耀光": {"tier": "白银", "overall_rank": 3, "t_rank": 1},
                "！！！": {"tier": "白银", "overall_rank": 5, "t_rank": 3},
                "？？？": {"tier": "白银", "overall_rank": 4, "t_rank": 2},
            },
            "另一英雄": {"珠光手镯": {"tier": "棱彩", "overall_rank": 1, "t_rank": 1}},
        }
        self.image = np.zeros((120, 640), dtype=np.uint8)

    def recognize(self, *boxes):
        self.analyzer.ocr = Mock(return_value=(list(boxes), None))
        return self.analyzer.analyze("英雄", {"hex_1": self.image})["hex_1"]

    def test_title_fragments_share_a_row_but_tags_are_below_it(self):
        result = self.recognize(ocr_box("净化", left=300), ocr_box("心灵", left=210),
                                ocr_box("伤害", left=290, top=82, height=16),
                                ocr_box("功能", left=390, top=82, height=16))
        self.assertEqual(result.get("match_name"), "心灵净化")
        self.assertEqual(result["score"], 100)

    def test_global_exact_name_without_hero_rank_is_not_recommended(self):
        result = self.recognize(ocr_box("珠光手镯"))
        self.assertEqual(result.get("match_name"), "珠光手镯")
        self.assertIn("暂无本英雄排名", result["text"])
        self.assertFalse(result["valid"])
        self.assertFalse(result["highlight"])

    def test_normalizes_title_and_database_punctuation_before_exact_match(self):
        for observed, expected in [("升级: 耀光", "升级：耀光"), ("? ??", "？？？")]:
            with self.subTest(observed=observed):
                result = self.recognize(ocr_box(observed))
                self.assertEqual(result.get("match_name"), expected)
                self.assertEqual(result["score"], 100)

    def test_tied_fuzzy_candidates_are_rejected(self):
        result = self.recognize(ocr_box("珠光手手"))
        self.assertFalse(result["valid"])
        self.assertNotIn("match_name", result)

    def test_f6_diagnostics_are_opt_in_and_save_actual_ocr_inputs(self):
        layout = GameLayout(1, ScreenRect(-2560, 20, 2560, 1440))
        expanded = np.zeros((176, 640), dtype=np.uint8)
        expanded[40:100, 80:160] = 120
        controller = GUIController(queue.Queue(), queue.Queue(), self.analyzer.dm, self.analyzer, None)
        controller.current_hero = "英雄"
        self.analyzer.ocr = lambda image: (
            ([ocr_box("心灵净化"), ocr_box("伤害", top=82, height=16)] if image.shape[0] == 176 else None),
            None)

        with TemporaryDirectory() as temp, \
                patch("gui_launcher.F6_DIAGNOSTICS_DIR", Path(temp), create=True), \
                patch("gui_launcher.keyboard.is_pressed", side_effect=lambda key: key == "f6"), \
                patch.object(self.analyzer, "capture_window_frame", return_value=(layout, {"hex_1": expanded})):
            controller._handle_hotkeys(2.0)
            self.assertEqual(list(Path(temp).iterdir()), [])
            controller.save_f6_diagnostics = True
            controller._handle_hotkeys(4.0)
            bundles = list(Path(temp).iterdir())
            self.assertEqual(len(bundles), 1)
            report = json.loads((bundles[0] / "report.json").read_text(encoding="utf-8"))
            card = report["cards"]["hex_1"]
            self.assertEqual(report["hero"], "英雄")
            self.assertEqual(card["title_region"]["height"], 74)
            self.assertEqual(card["result"]["match_name"], "心灵净化")
            self.assertEqual(card["attempts"][0]["ocr"], [])
            self.assertEqual([box["text"] for box in card["attempts"][1]["ocr"]], ["心灵净化", "伤害"])
            for attempt, expected in zip(card["attempts"], (expanded[28:],
                    cv2.normalize(expanded, None, 0, 255, cv2.NORM_MINMAX))):
                encoded = np.frombuffer((bundles[0] / attempt["image"]).read_bytes(), np.uint8)
                np.testing.assert_array_equal(cv2.imdecode(encoded, cv2.IMREAD_GRAYSCALE), expected)

    def test_f6_retries_only_failed_cards_from_same_capture(self):
        layout = GameLayout(1, ScreenRect(-2560, 20, 2560, 1440))
        frame = np.zeros((1440, 2560, 4), dtype=np.uint8)
        for i, key in enumerate(CARD_LEFTS, 1):
            rect = layout.title_region(key)
            x, y = rect.left - layout.client.left, rect.top - layout.client.top
            frame[y:y + rect.height, x:x + rect.width, :3] = i * 40

        def grab(bounds):
            left, top, right, bottom = bounds
            crop = frame[top - layout.client.top:bottom - layout.client.top,
                         left - layout.client.left:right - layout.client.left]
            return SimpleNamespace(height=crop.shape[0], width=crop.shape[1], raw=crop.tobytes())

        def read(image):
            if image.shape[0] == 176:
                return [ocr_box("珠光护手"), ocr_box("伤害", top=90, height=16)], None
            if image[60, 320] == 80:
                return None, None
            return [ocr_box("心灵净化")], None

        with ThreadPoolExecutor(max_workers=3) as executor:
            self.analyzer.executor = executor
            for parallel in (False, True):
                with self.subTest(parallel=parallel), patch("main.get_game_layout", return_value=layout), \
                        patch("main.mss.mss") as mss:
                    self.analyzer._use_parallel = parallel
                    self.analyzer.ocr = Mock(side_effect=read)
                    capture = mss.return_value.__enter__.return_value.grab
                    capture.side_effect = grab
                    result = self.analyzer.manual_update("英雄")["data"]
                    self.assertEqual([row.get("match_name") for row in result.values()],
                                     ["心灵净化", "珠光护手", "心灵净化"])
                    self.assertEqual([row["highlight"] for row in result.values()], [True, False, True])
                    self.assertEqual(self.analyzer.ocr.call_count, 4)
                    capture.assert_called_once()


if __name__ == "__main__":
    unittest.main()
