"""Regression coverage for launcher lifecycle and updater persistence."""

import queue
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gui_launcher import GUIController, LauncherApp
from scripts import updater


class LauncherTests(unittest.TestCase):
    def test_close_during_update_cancels_and_hides_window(self):
        app = LauncherApp.__new__(LauncherApp)
        app._update_running = True
        app._quit_after_update = False
        app._update_cancel = threading.Event()
        app._log = Mock()
        app._engine_cleanup = Mock()
        app.root = Mock()
        app._quit_app()
        self.assertTrue(app._update_cancel.is_set())
        self.assertTrue(app._quit_after_update)
        app.root.withdraw.assert_called_once()
        app._engine_cleanup.assert_called_once()

    def test_reloaded_data_reaches_running_engine_and_clears_overlay(self):
        old_data = Mock()
        new_data = Mock()
        new_data.validate_hero.return_value = "探险家"
        analyzer = SimpleNamespace(dm=old_data, executor=Mock())
        lcu = Mock()
        lcu.get_session_state.return_value = ("Lobby", None, "", None)
        controller = GUIController(queue.Queue(), queue.Queue(), old_data, analyzer, lcu)
        controller.current_hero = "探险家"
        controller.detector.results = {"hex_1": "old recommendation"}
        app = LauncherApp.__new__(LauncherApp)
        app.controller = controller
        app.start_btn = Mock()

        app._handle_gui_message({"event": "data_loaded", "data": new_data})
        # Run one controller iteration with no I/O.
        controller._handle_hotkeys = Mock(side_effect=lambda _: controller.stop())
        controller._sync_session_and_hero = Mock()
        controller.run()

        self.assertIs(controller.dm, new_data)
        self.assertIs(analyzer.dm, new_data)
        self.assertFalse(controller.detector.results)
        self.assertEqual(controller.overlay_queue.get_nowait()["cmd"], "CLEAR")
        analyzer.executor.shutdown.assert_called_once()
        controller.lcu.live_session.close.assert_called_once()

    def test_late_initialization_does_not_start_a_stopped_engine(self):
        app = LauncherApp.__new__(LauncherApp)
        app._engine_generation = 2
        app._create_overlay_and_start = Mock()
        analyzer, lcu = Mock(), Mock()
        app._handle_gui_message({
            "event": "create_overlay", "generation": 1,
            "analyzer": analyzer, "lcu": lcu,
        })
        app._create_overlay_and_start.assert_not_called()
        analyzer.executor.shutdown.assert_called_once()
        lcu.lcu_session.close.assert_called_once()

    def test_old_engine_error_does_not_stop_new_engine(self):
        app = LauncherApp.__new__(LauncherApp)
        app._engine_generation = 3
        app._stop_engine = Mock()
        app._handle_gui_message({"event": "engine_error", "generation": 1})
        app._stop_engine.assert_not_called()
        app._handle_gui_message({"event": "engine_error", "generation": 3})
        app._stop_engine.assert_called_once()


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.paths = [Path(directory.name) / name for name in ("data.csv", "champions.json", "pinyin.json")]
        for path in self.paths:
            path.write_bytes(b"previous")
        paths = patch.multiple(
            updater, CSV_FILE=self.paths[0], CHAMPION_ID_FILE=self.paths[1], PINYIN_FILE=self.paths[2],
        )
        paths.start()
        self.addCleanup(paths.stop)

    def test_failed_download_keeps_all_previous_files(self):
        replies = [
            Mock(status_code=200, content=b"partial"),
            Mock(status_code=503),
            Mock(status_code=200, content=b"partial"),
        ]
        with patch.object(updater.requests, "get", side_effect=replies):
            self.assertFalse(updater.download_from_github(log_func=Mock()))
        self.assertEqual([path.read_bytes() for path in self.paths], [b"previous"] * 3)

    def test_invalid_success_response_keeps_previous_files(self):
        with patch.object(updater.requests, "get", return_value=Mock(status_code=200, content=b"<html>")):
            self.assertFalse(updater.download_from_github(log_func=Mock()))
        self.assertEqual([path.read_bytes() for path in self.paths], [b"previous"] * 3)

    def test_valid_download_replaces_all_three_files(self):
        contents = [
            ("中文名,英文名,等级,总排名,等级内序号,海克斯名称\n"
             "探险家,Ezreal,黄金,1,1,贪欲束缚\n").encode("utf-8-sig"),
            '{"探险家": "Ezreal"}'.encode(),
            '{"探险家": "txj"}'.encode(),
        ]
        replies = [Mock(status_code=200, content=content) for content in contents]
        with patch.object(updater.requests, "get", side_effect=replies):
            self.assertTrue(updater.download_from_github(log_func=Mock()))
        self.assertEqual([path.read_bytes() for path in self.paths], contents)

    def test_local_merge_rolls_back_if_second_replacement_fails(self):
        replace = updater.os.replace

        def fail_second(source, target):
            if target == self.paths[1]:
                raise PermissionError("simulated locked champions file")
            return replace(source, target)

        rows = {"探险家": [{"name": "贪欲束缚", "tier": "黄金", "overall_rank": 1, "t_rank": 1}]}
        with patch.object(updater.os, "replace", side_effect=fail_second) as replace_mock:
            self.assertFalse(updater.merge_and_save({"Ezreal": "探险家"}, {}, rows))
        self.assertEqual(replace_mock.call_count, 3)
        self.assertEqual([path.read_bytes() for path in self.paths], [b"previous"] * 3)

    def test_official_lookup_uses_timeouts_without_touching_files(self):
        self.paths[1].write_bytes(b'{"Old": "Ezreal"}')
        replies = [
            Mock(json=Mock(return_value=["test-version"])),
            Mock(json=Mock(return_value={"data": {"Ezreal": {"name": "探险家"}}})),
        ]
        with patch.object(updater.requests, "get", side_effect=replies) as get:
            en_to_cn, _, _, renamed = updater.sync_official_data()
        self.assertEqual(en_to_cn, {"Ezreal": "探险家"})
        self.assertEqual(renamed, ["Ezreal"])
        self.assertEqual(self.paths[1].read_bytes(), b'{"Old": "Ezreal"}')
        self.assertEqual(get.call_count, 2)
        for call in get.call_args_list:
            self.assertEqual(call.kwargs["timeout"], updater.REQUEST_TIMEOUT)

    def _write_valid_cache(self):
        self.paths[0].write_text(
            "中文名,英文名,等级,总排名,等级内序号,海克斯名称\n"
            "探险家,Ezreal,黄金,1,1,贪欲束缚\n", encoding="utf-8-sig",
        )
        self.paths[1].write_text('{"探险家": "Ezreal"}', encoding="utf-8")
        self.paths[2].write_text('{"探险家": "txj"}', encoding="utf-8")

    def test_official_timeout_retries_before_using_cache(self):
        self.paths[1].write_text('{"探险家": "Ezreal"}', encoding="utf-8")
        replies = [
            updater.requests.exceptions.ReadTimeout("slow official server"),
            Mock(json=Mock(return_value=["test-version"])),
            Mock(json=Mock(return_value={"data": {"Ahri": {"name": "九尾妖狐"}}})),
        ]
        with patch.object(updater.requests, "get", side_effect=replies), patch.object(updater.time, "sleep"):
            en_to_cn, _, added, _ = updater.sync_official_data()
        self.assertEqual(en_to_cn, {"Ahri": "九尾妖狐"})
        self.assertEqual(added, ["Ahri"])

    def test_official_unavailable_uses_valid_cache_without_writing(self):
        self._write_valid_cache()
        previous = [path.read_bytes() for path in self.paths]
        with patch.object(updater.requests, "get", side_effect=updater.requests.exceptions.ReadTimeout("offline")), \
                patch.object(updater.time, "sleep"), patch("builtins.print") as log:
            result = updater.sync_official_data()
        self.assertEqual(result, ({"Ezreal": "探险家"}, {"探险家": "Ezreal"}, [], []))
        self.assertEqual([path.read_bytes() for path in self.paths], previous)
        self.assertTrue(any("本地" in str(call) and "新英雄" in str(call) for call in log.call_args_list))

    def test_official_unavailable_rejects_invalid_cache(self):
        for invalid in (b"<html>", b"{}", b"[]", b'{"Bad": 1}', b'{"A": "Ezreal", "B": "Ezreal"}'):
            with self.subTest(cache=invalid):
                self._write_valid_cache()
                self.paths[1].write_bytes(invalid)
                previous = [path.read_bytes() for path in self.paths]
                with patch.object(updater.requests, "get", side_effect=updater.requests.exceptions.ConnectionError("offline")), \
                        patch.object(updater.time, "sleep"):
                    self.assertEqual(updater.sync_official_data(), ({}, {}, [], []))
                self.assertEqual([path.read_bytes() for path in self.paths], previous)

    def test_invalid_official_response_uses_cache(self):
        for payload in ({"data": {}}, {"data": {"Ezreal": {"name": ""}}}, {"data": []}):
            with self.subTest(payload=payload):
                self._write_valid_cache()
                def reply(url, **kwargs):
                    return Mock(json=Mock(return_value=["test-version"] if "/api/" in url else payload))
                with patch.object(updater.requests, "get", side_effect=reply), patch.object(updater.time, "sleep"):
                    result = updater.sync_official_data()
                self.assertEqual(result, ({"Ezreal": "探险家"}, {"探险家": "Ezreal"}, [], []))

    def test_update_continues_after_official_timeout(self):
        self._write_valid_cache()
        rows = {"探险家": [{"name": "新海克斯", "tier": "黄金", "overall_rank": 1, "t_rank": 1}]}
        with patch.object(updater.requests, "get", side_effect=updater.requests.exceptions.ReadTimeout("offline")), \
                patch.object(updater.time, "sleep"), \
                patch.object(updater.crawler, "crawl_champions", return_value=(rows, [])), \
                patch.object(updater, "sync_data_to_github"):
            self.assertTrue(updater.run_update(mode="full", log_func=Mock()))
        self.assertIn("新海克斯", self.paths[0].read_text(encoding="utf-8-sig"))

    def test_cancelled_crawl_does_not_commit_partial_update(self):
        self._write_valid_cache()
        previous = [p.read_bytes() for p in self.paths]
        cancel = threading.Event()
        def crawl(*args, **kwargs):
            cancel.set()
            return {"探险家": [{"name": "部分结果", "tier": "黄金", "overall_rank": 1, "t_rank": 1}]}, []
        with patch.object(updater.crawler, "crawl_champions", side_effect=crawl), \
                patch.object(updater, "sync_data_to_github") as sync:
            self.assertFalse(updater.run_update(
                mode="full", official_data=({"Ezreal": "探险家"}, {"探险家": "Ezreal"}, [], []),
                cancel_event=cancel, log_func=Mock(),
            ))
        self.assertEqual([p.read_bytes() for p in self.paths], previous)
        sync.assert_not_called()

    def test_crawler_cancellation_stops_remaining_heroes_and_closes_browser(self):
        cancel = threading.Event()
        driver = Mock()
        def scrape(*args, **kwargs):
            cancel.set()
            return [], "error"
        with patch.object(updater.crawler, "setup_driver", return_value=driver), \
                patch.object(updater.crawler, "scrape_single_champion", side_effect=scrape) as scrape_mock:
            with self.assertRaises(Exception) as raised:
                updater.crawler.crawl_champions([("探险家", "Ezreal"), ("九尾妖狐", "Ahri")], cancel_event=cancel)
        self.assertEqual(type(raised.exception).__name__, "UpdateCancelled")
        self.assertEqual(scrape_mock.call_count, 1)
        driver.quit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
