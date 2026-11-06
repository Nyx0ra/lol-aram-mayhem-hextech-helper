import ctypes
from ctypes import wintypes
import unittest
from unittest.mock import Mock, patch

from main import GameAnalyzer, PopupDetector
from scripts import window_layout as win


class WindowLayoutTests(unittest.TestCase):
    def test_client_origin_and_ultrawide_centering_are_shared(self):
        layout = win.GameLayout(42, win.ScreenRect(-3440, -200, 3440, 1440))
        self.assertEqual(layout.content, win.ScreenRect(-3000, -200, 2560, 1440))
        self.assertEqual(layout.title_region("hex_3"), win.ScreenRect(-1400, 340, 320, 60))
        self.assertEqual(layout.overlay_anchor("hex_3"), (2040, 420))
        normal = win.GameLayout(42, win.ScreenRect(23, 40, 1920, 1080))
        self.assertEqual(normal.content, normal.client)
        self.assertEqual(normal.title_region("hex_3"), win.ScreenRect(1223, 445, 240, 45))

    def test_client_snapshot_uses_both_transformed_corners(self):
        def client_rect(hwnd, rect):
            value = ctypes.cast(rect, ctypes.POINTER(wintypes.RECT)).contents
            value.right, value.bottom = 1280, 720
            return True

        def to_screen(hwnd, point):
            value = ctypes.cast(point, ctypes.POINTER(wintypes.POINT)).contents
            value.x, value.y = -1920 + value.x * 3 // 2, -100 + value.y * 3 // 2
            return True

        with patch.multiple(win, _find_window=Mock(return_value=42), _foreground=Mock(return_value=42),
                            _is_iconic=Mock(return_value=False), _is_visible=Mock(return_value=True),
                            _client_rect=client_rect, _client_to_screen=to_screen):
            self.assertEqual(win.get_game_layout(), win.GameLayout(42, win.ScreenRect(-1920, -100, 1920, 1080)))
            win._foreground.return_value = 7
            self.assertIsNone(win.get_game_layout())

    def test_capture_discards_a_frame_if_layout_changes_mid_grab(self):
        layout = win.GameLayout(42, win.ScreenRect(-1920, 0, 1920, 1080))
        region = layout.title_region("hex_1")
        raw = Mock(width=region.width, height=region.height, raw=bytes(region.width * region.height * 4))
        with patch("main.get_game_layout", side_effect=[layout, None]), patch("main.mss.mss") as mss:
            grab = mss.return_value.__enter__.return_value.grab
            grab.return_value = raw
            self.assertEqual(GameAnalyzer.capture_window_frame(["hex_1"]), (None, {}))
            grab.assert_called_once_with(
                (region.left, region.top, region.left + region.width, region.top + region.height))

    def test_layout_change_clears_detector_baseline_before_next_sample(self):
        a = win.GameLayout(42, win.ScreenRect(0, 0, 1920, 1080))
        b = win.GameLayout(42, win.ScreenRect(-1920, 0, 1920, 1080))
        analyzer = Mock()
        analyzer.capture_window_frame.side_effect = [(a, {}), (b, {}), (None, {})]
        detector = PopupDetector(analyzer)
        with patch.object(detector, "sample", return_value=None) as sample:
            detector.sample_window("hero", 1)
            detector.confirmed = {"hex_1": object()}
            self.assertEqual(detector.sample_window("hero", 2), {"cmd": "CLEAR"})
            self.assertFalse(detector.confirmed)
            self.assertEqual(detector.layout, b)
            self.assertEqual(detector.sample_window("hero", 3), {"cmd": "CLEAR"})
            self.assertEqual(sample.call_count, 2)
