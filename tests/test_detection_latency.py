import queue
import sys
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np

from gui_launcher import GUIController
from main import GameAnalyzer, InputController, OverlayApp, PopupDetector
from scripts.lcu_connector import LCUConnector, SessionPoller
from scripts.overlay_inbox import OverlayInbox


class DetectionLatencyTests(unittest.TestCase):
    def test_release_defaults_sync_hero_and_scan_only_on_f6(self):
        for controller_type in (GUIController, InputController):
            with self.subTest(controller=controller_type.__name__):
                overlay = queue.Queue()
                dm = Mock(validate_hero=lambda hero: hero)
                analyzer = Mock()
                recommendation = {"cmd": "UPDATE", "data": {"hex_2": {"text": "推荐"}}}
                analyzer.manual_update.return_value = recommendation
                poller = Mock()
                if controller_type is GUIController:
                    controller = GUIController(overlay, queue.Queue(), dm, analyzer, None)
                    controller.session_poller = poller
                    run = controller._run_loop
                else:
                    controller = InputController(overlay, dm, analyzer)
                    run = lambda: controller._listen(poller)

                ticks = iter([
                    (1, "ChampSelect", "探险家", None),
                    (2, "GameStart", "暗裔剑魔", None),
                    (3, "InProgress", "暗裔剑魔", None),
                    (4, "InProgress", "暗裔剑魔", "f6"),
                    (5, "InProgress", "暗裔剑魔", None),
                ])
                key = None

                def tick():
                    nonlocal key
                    now, phase, hero, key = next(ticks)
                    poller.snapshot = (phase, hero, "LCU", None)
                    return float(now)

                module = sys.modules[controller_type.__module__]
                with patch.object(module, "time") as clock, \
                        patch.object(module.keyboard, "is_pressed", side_effect=lambda k: k == key):
                    clock.monotonic.side_effect = tick
                    with self.assertRaises(StopIteration):
                        run()
                self.assertEqual(controller.current_hero, "暗裔剑魔")
                poller.refresh.assert_not_called()
                analyzer.capture_window_frame.assert_not_called()
                analyzer.manual_update.assert_called_once_with("暗裔剑魔")
                updates = [msg for msg in list(overlay.queue) if msg["cmd"] == "UPDATE"]
                self.assertEqual(updates, [recommendation])

    def test_overlay_inbox_coalesces_notifications_and_ignores_stopped_producer(self):
        root = Mock()
        received = []

        def consume():
            while True:
                try:
                    received.append(inbox.get_nowait())
                except queue.Empty:
                    return

        inbox = OverlayInbox(root, consume)
        dispatch = root.bind.call_args.args[1]
        inbox.put("popup")
        inbox.put("reroll")
        root.event_generate.assert_called_once()
        dispatch(None)
        self.assertEqual(received, ["popup", "reroll"])
        inbox.put("clear")
        self.assertEqual(root.event_generate.call_count, 2)
        inbox.close()
        inbox.put("old update")
        dispatch(None)
        self.assertEqual(root.event_generate.call_count, 2)
        self.assertEqual(received, ["popup", "reroll"])

    def test_confirmed_popup_and_reroll_display_in_same_queue_pass(self):
        for flags in ({"opening": True}, {"refresh_keys": ["hex_2"]}):
            with self.subTest(flags=flags):
                app = OverlayApp.__new__(OverlayApp)
                app.root = Mock()
                app.queue = queue.Queue()
                app.layout = object()
                app.layout_timer = None
                app.update_display = Mock()
                app._report_latency = Mock()
                results = {"hex_2": {"text": "新卡"}}
                timing = ("popup", 1.0, 1.005, 1.020)
                app.queue.put({"cmd": "UPDATE", "layout": app.layout, "data": results,
                               "duration": 20000, "timing": timing, **flags})
                with patch("main.get_game_layout", return_value=app.layout), \
                        patch("main.time.perf_counter", side_effect=[1.030, 1.035]):
                    app.process_queue()
                app.update_display.assert_called_once_with(results, duration=20000)
                app.root.update_idletasks.assert_called_once()
                app._report_latency.assert_called_once_with(timing, 1.030, 1.035)
                app.root.after.assert_called_once_with(50, app._check_display_layout)

    def test_visual_scan_continues_while_lcu_request_is_blocked(self):
        entered, release, captured = (threading.Event() for _ in range(3))
        lcu = Mock()

        def poll(*args, **kwargs):
            entered.set()
            release.wait(3)
            return "InProgress", "探险家", "LCU", None

        lcu.get_session_state.side_effect = poll
        controller = GUIController(queue.Queue(), queue.Queue(), Mock(), Mock(), lcu, auto_popup=True)
        controller.current_phase = "InProgress"
        controller.current_hero = "探险家"
        controller._check_in_game_popup = Mock(side_effect=captured.set)
        with patch("gui_launcher.keyboard.is_pressed", return_value=False):
            controller.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(captured.wait(0.5), "LCU 请求期间应继续采样")
            finally:
                controller.stop()
                release.set()
                controller.join(4)
        self.assertFalse(controller.is_alive())

    def test_reroll_retries_unchanged_new_frame_after_ocr_miss(self):
        self._assert_reroll_recovers({"valid": False}, retry_at=0.36)

    def test_reroll_retries_after_old_title_without_changing_confirmed_card(self):
        self._assert_reroll_recovers(
            {"valid": True, "score": 100, "match_name": "旧卡"}, retry_at=0.60,
        )

    def _assert_reroll_recovers(self, first_ocr, retry_at):
        analyzer = GameAnalyzer.__new__(GameAnalyzer)
        analyzer.compute_thumbnails = lambda images: images
        analyzer.check_visual_gate = Mock(return_value=(True, "passed"))
        detector = PopupDetector(analyzer)
        key = "hex_2"
        old_pixels = np.tile(np.array([30, 80] * 16, dtype=np.float32), (10, 1))
        new_pixels = np.roll(old_pixels, 1, axis=1)
        old_frame = {f"hex_{i}": old_pixels for i in range(1, 4)}
        frame = dict(old_frame, hex_2=new_pixels)
        old = {key: {"valid": True, "score": 100, "match_name": "旧卡"}}
        new = {key: {"valid": True, "score": 100, "match_name": "新卡"}}
        detector.confirmed = dict(old_frame)
        detector.results = old
        analyzer.analyze_partial = Mock(side_effect=[{key: first_ocr}, new])

        self.assertIsNone(detector.sample("探险家", frame, 0.0))
        noisy_frame = dict(frame, hex_2=new_pixels + 5)
        noisy_frame[key][0, :4] += 60
        self.assertIsNone(detector.sample("探险家", noisy_frame, 0.12))
        np.testing.assert_array_equal(detector.confirmed[key], old_pixels)
        self.assertIsNone(detector.sample("探险家", frame, 0.24))
        message = detector.sample("探险家", frame, retry_at)
        self.assertEqual(message["data"], new)
        self.assertEqual(message["refresh_keys"], [key])
        np.testing.assert_array_equal(detector.confirmed[key], new_pixels)
        self.assertEqual([call.args[-1] for call in analyzer.analyze_partial.call_args_list], [[key], [key]])

    def test_poller_publishes_in_progress_before_slow_hero_lookup_finishes(self):
        connector = LCUConnector.__new__(LCUConnector)
        connector._connected = True
        connector.get_gameflow_phase = Mock(side_effect=["GameStart", "InProgress"])
        entered, release = threading.Event(), threading.Event()

        def hero_lookup():
            if connector.get_gameflow_phase.call_count == 2:
                entered.set()
                release.wait(3)
            return "探险家"

        connector.get_gameflow_champion = hero_lookup
        poller = SessionPoller(connector)
        poller.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertEqual(poller.snapshot[:2], ("InProgress", "探险家"))
        finally:
            poller.stop()
            release.set()
            poller.join(4)
        self.assertFalse(poller.is_alive())

    def test_first_popup_waits_for_stable_frame_and_retries_early_miss(self):
        analyzer = GameAnalyzer.__new__(GameAnalyzer)
        detector = PopupDetector(analyzer)
        detector.reset(opening=True)
        self.assertEqual(detector.interval, 0.045)
        title = np.tile(np.array([35, 180] * 16, dtype=np.uint8), (10, 1))
        settled = {f"hex_{i}": title for i in range(1, 4)}
        animated = {key: np.roll(img, 1, axis=1) for key, img in settled.items()}
        analyzer.analyze = Mock(side_effect=[{}, {
            key: {"valid": True, "score": 100} for key in settled
        }])
        self.assertIsNone(detector.sample("探险家", animated, 0.0))
        self.assertIsNone(detector.sample("探险家", settled, 0.045))
        analyzer.analyze.assert_not_called()
        self.assertIsNone(detector.sample("探险家", settled, 0.09))
        self.assertIsNone(detector.sample("探险家", settled, 0.135))
        analyzer.analyze.assert_called_once()
        message = detector.sample("探险家", settled, 0.42)
        self.assertTrue(message["opening"])
        self.assertEqual(detector.interval, 0.12)
