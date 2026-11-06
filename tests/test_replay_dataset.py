"""Record paired title/icon slices and replay titles through the production detector."""

import argparse
import json
from pathlib import Path
from statistics import median
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import Mock, patch

import cv2
import mss
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main import DataManager, GameAnalyzer, PopupDetector, get_game_layout
from scripts.config import BASE_DIR
from scripts.window_layout import CARD_LEFTS, GameLayout, ScreenRect, enable_per_monitor_dpi

DEFAULT_DATASET = Path(BASE_DIR) / "tests" / "replay_samples"


def load_clip(directory):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    times = manifest["frame_ms"]
    if (manifest["schema"] not in (1, 2) or not times or
            any(type(t) is not int or t < 0 for t in times) or
            any(a >= b for a, b in zip(times, times[1:]))):
        raise ValueError(f"{directory}: invalid schema or frame timestamps")
    if not isinstance(manifest["opening"], bool) or not manifest["hero"]:
        raise ValueError(f"{directory}: invalid hero or opening flag")
    previous_end = -1
    for event in manifest["events"]:
        start, end, cards = event["start_ms"], event["end_ms"], event["cards"]
        if (not previous_end < start <= end or
                not any(start <= t <= end for t in times) or
                event["type"] not in ("popup", "reroll") or
                not cards or not cards.keys() <= CARD_LEFTS.keys() or
                any(not isinstance(name, str) or not name for name in cards.values()) or
                (event["type"] == "popup" and cards.keys() != CARD_LEFTS.keys()) or
                (event["type"] == "reroll" and len(cards) != 1)):
            raise ValueError(f"{directory}: invalid or overlapping event: {event}")
        previous_end = end
    return manifest


def read_frames(directory, times, *, schema=1):
    for index, timestamp in enumerate(times):
        images = {}
        for key in CARD_LEFTS:
            path = directory / f"{index:05d}_{key}.png"
            # imdecode handles non-ASCII Windows paths.
            image = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            if image is None:
                raise ValueError(f"Invalid PNG: {path}")
            images[key] = image
            if schema == 2:
                icon = directory / f"{index:05d}_{key}_icon.png"
                if cv2.imdecode(np.frombuffer(icon.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR) is None:
                    raise ValueError(f"Invalid PNG: {icon}")
        yield timestamp, images


class TimedAnalyzer:
    def __init__(self, analyzer):
        self.analyzer = analyzer
        self.single_card_ms = []

    def __getattr__(self, name):
        return getattr(self.analyzer, name)

    def analyze_partial(self, hero, images, results, keys):
        started = time.perf_counter()
        updated = self.analyzer.analyze_partial(hero, images, results, keys)
        if len(keys) == 1:
            self.single_card_ms.append((time.perf_counter() - started) * 1000)
        return updated


def replay_clip(analyzer, manifest, frames):
    timed = TimedAnalyzer(analyzer)
    detector = PopupDetector(timed)
    detector.reset(opening=manifest["opening"])
    events = manifest["events"]
    matched = {}
    negative_frames = false_frames = unexpected = 0
    for timestamp, images in frames:
        message = detector.sample(manifest["hero"], images, timestamp / 1000)
        index = next((i for i, e in enumerate(events)
                      if e["start_ms"] <= timestamp <= e["end_ms"]), None)
        if index is None:
            negative_frames += 1
        if not message or message["cmd"] != "UPDATE":
            continue
        if index is None:
            false_frames += 1
            continue
        event = events[index]
        keys = set(message.get("refresh_keys", ()))
        names = {key: item.get("match_name") for key, item in message["data"].items()
                 if item.get("valid")}
        expected = event["cards"]
        correct = (keys == expected.keys() and all(names.get(k) == v for k, v in expected.items())
                   if event["type"] == "reroll" else not keys and names == expected)
        if correct and index not in matched:
            matched[index] = timestamp - event["start_ms"]
        else:
            unexpected += 1
    return {
        "negative_frames": negative_frames, "false_frames": false_frames,
        "expected_events": len(events), "misses": len(events) - len(matched),
        "missed_events": [event for i, event in enumerate(events) if i not in matched],
        "unexpected_updates": unexpected,
        "reroll_confirm_ms": [delay for i, delay in matched.items() if events[i]["type"] == "reroll"],
        "single_card_ms": timed.single_card_ms,
    }


def capture_replay_frame(sct):
    layout = get_game_layout()
    if layout is None:
        return None, {}
    titles = {key: layout.title_region(key) for key in CARD_LEFTS}
    icons = {f"{key}_icon": layout.icon_region(key) for key in CARD_LEFTS}
    regions = {**titles, **icons}
    left = min(r.left for r in regions.values())
    top = min(r.top for r in regions.values())
    right = max(r.left + r.width for r in regions.values())
    bottom = max(r.top + r.height for r in regions.values())
    raw = sct.grab((left, top, right, bottom))
    if get_game_layout() != layout:
        return None, {}
    frame = np.frombuffer(raw.raw, dtype=np.uint8).reshape((raw.height, raw.width, 4))
    images = {}
    for key, rect in regions.items():
        x, y = rect.left - left, rect.top - top
        roi = frame[y:y + rect.height, x:x + rect.width]
        images[key] = (GameAnalyzer.prepare_title(roi) if key in titles else
                       cv2.cvtColor(roi, cv2.COLOR_BGRA2BGR))
    return layout, images


def record(directory, hero, seconds, interval_ms):
    directory.mkdir(parents=True, exist_ok=False)
    enable_per_monitor_dpi()
    print("3 秒后开始录制，请切回游戏。", flush=True)
    time.sleep(3)
    started = time.monotonic()
    frame_ms = []
    clip_layout = None
    with mss.mss() as sct:
        while time.monotonic() - started < seconds:
            tick = time.monotonic()
            layout, images = capture_replay_frame(sct)
            if clip_layout is not None and layout != clip_layout:
                print("游戏窗口布局已改变或离开前台，结束当前片段。")
                break
            if layout is not None:
                clip_layout = layout
                timestamp = round((time.monotonic() - started) * 1000)
                for key, image in images.items():
                    ok, png = cv2.imencode(".png", image)
                    if not ok:
                        raise ValueError(f"PNG encoding failed for {key}")
                    (directory / f"{len(frame_ms):05d}_{key}.png").write_bytes(png.tobytes())
                frame_ms.append(timestamp)
            time.sleep(max(0, interval_ms / 1000 - (time.monotonic() - tick)))
    if not frame_ms:
        raise ValueError("未录到前台游戏窗口。")
    manifest = {"schema": 2, "hero": hero, "opening": False,
                "frame_ms": [t - frame_ms[0] for t in frame_ms], "events": []}
    (directory / "manifest.pending.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"录制 {len(frame_ms)} 帧。请标注 manifest.pending.json 后重命名为 manifest.json。")


def summarize(rows):
    totals = {k: sum(row[k] for row in rows) for k in (
        "negative_frames", "false_frames", "expected_events", "misses", "unexpected_updates")}
    for label, n, d in (("误触发率", "false_frames", "negative_frames"),
                        ("漏检率", "misses", "expected_events")):
        rate = f"{totals[n] / totals[d]:.2%}" if totals[d] else "N/A"
        print(f"{label}: {rate} ({totals[n]}/{totals[d]})")
    print(f"预期窗口内错误或重复更新: {totals['unexpected_updates']}")
    for key, label in (("reroll_confirm_ms", "单卡确认延迟（录制时间轴）"),
                       ("single_card_ms", "单卡 OCR 与重新排序（实际运行）")):
        values = [v for row in rows for v in row[key]]
        stats = (f"n={len(values)}, p50={median(values):.2f}ms, "
                 f"p95={np.percentile(values, 95):.2f}ms" if values else "N/A")
        print(f"{label}: {stats}")
    return bool(totals["misses"] or totals["false_frames"] or totals["unexpected_updates"])


class ReplayDatasetTests(unittest.TestCase):
    def test_record_writes_title_and_color_icon_pairs_from_one_grab(self):
        layout = GameLayout(42, ScreenRect(-1920, -100, 1920, 1080))
        sct = Mock()

        def grab(bounds):
            left, top, right, bottom = bounds
            pixels = np.zeros((bottom - top, right - left, 4), dtype=np.uint8)
            for i, key in enumerate(CARD_LEFTS, 1):
                r = layout.icon_region(key)
                x, y = r.left - left, r.top - top
                pixels[y:y + r.height, x:x + r.width] = [i * 20, 50, 100, 255]
            return Mock(raw=pixels.tobytes(), width=right - left, height=bottom - top)

        sct.grab.side_effect = grab
        module = sys.modules[__name__]
        with TemporaryDirectory() as temp, patch.object(module, "get_game_layout", return_value=layout), \
                patch.object(module, "enable_per_monitor_dpi"), patch.object(module, "time") as clock, \
                patch.object(mss, "mss") as factory:
            factory.return_value.__enter__.return_value = sct
            clock.monotonic.side_effect = [0, 0, 0, 0.005, 0.010, 0.050]
            directory = Path(temp) / "clip"
            record(directory, "hero", 0.04, 45)
            sct.grab.assert_called_once()
            (directory / "manifest.pending.json").rename(directory / "manifest.json")
            manifest = load_clip(directory)
            self.assertEqual((manifest["schema"], manifest["frame_ms"]), (2, [0]))
            [(_, titles)] = read_frames(directory, [0], schema=2)
            for i, key in enumerate(CARD_LEFTS, 1):
                title = layout.title_region(key)
                self.assertEqual(titles[key].shape, (title.height * 2, title.width * 2))
                icon = cv2.imread(str(directory / f"00000_{key}_icon.png"))
                np.testing.assert_array_equal(icon[0, 0], [i * 20, 50, 100])

    def test_png_replay_scores_new_card_and_reports_wrong_ground_truth(self):
        analyzer = GameAnalyzer.__new__(GameAnalyzer)
        analyzer._use_parallel = False
        analyzer.dm = Mock(hero_data={"hero": {"旧卡": {}, "新卡": {}}})
        analyzer.ocr = lambda image: ([[None, "旧卡" if image[0, 0] == 40 else "新卡"]], None)
        old = np.tile(np.array([40, 180] * 16, dtype=np.uint8), (10, 1))
        new = np.roll(old, 1, axis=1)
        frames = [
            {key: np.zeros_like(old) for key in CARD_LEFTS},
            {key: old for key in CARD_LEFTS},
            {key: old for key in CARD_LEFTS},
            dict.fromkeys(CARD_LEFTS, old) | {"hex_2": new},
            dict.fromkeys(CARD_LEFTS, old) | {"hex_2": new},
        ]
        manifest = {
            "schema": 1, "hero": "hero", "opening": True,
            "frame_ms": [0, 100, 200, 300, 400],
            "events": [
                {"type": "popup", "start_ms": 100, "end_ms": 200,
                 "cards": dict.fromkeys(CARD_LEFTS, "旧卡")},
                {"type": "reroll", "start_ms": 300, "end_ms": 400, "cards": {"hex_2": "新卡"}},
            ],
        }
        with TemporaryDirectory() as temp:
            directory = Path(temp)
            for index, images in enumerate(frames):
                for key, image in images.items():
                    (directory / f"{index:05d}_{key}.png").write_bytes(
                        cv2.imencode(".png", image)[1].tobytes())
            path = directory / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            loaded = load_clip(directory)
            replay = lambda: replay_clip(analyzer, loaded, read_frames(directory, loaded["frame_ms"]))
            row = replay()
            self.assertEqual((row["false_frames"], row["misses"], row["unexpected_updates"]), (0, 0, 0))
            self.assertEqual(row["reroll_confirm_ms"], [100])
            self.assertEqual(len(row["single_card_ms"]), 1)
            loaded["events"][1]["cards"]["hex_2"] = "旧卡"
            row = replay()
            self.assertEqual((row["misses"], row["unexpected_updates"]), (1, 1))
            loaded["events"].pop()
            self.assertEqual(replay()["false_frames"], 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", nargs="?", type=Path)
    parser.add_argument("--record", type=Path, metavar="CLIP_DIR")
    parser.add_argument("--hero")
    parser.add_argument("--seconds", type=float, default=12)
    parser.add_argument("--interval-ms", type=int, default=45)
    args = parser.parse_args()
    if args.record:
        if not args.hero or not 0 < args.seconds <= 60 or args.interval_ms < 20:
            parser.error("录制需提供 --hero，时长在 0～60 秒之间，间隔至少 20ms。")
        record(args.record, args.hero, args.seconds, args.interval_ms)
        return 0
    dataset = args.dataset or DEFAULT_DATASET
    clips = sorted(dataset.glob("*/manifest.json"))
    if not clips:
        print(f"未找到已标注样本，未运行真实回放: {dataset}")
        return 2 if args.dataset else 0
    manager = DataManager()
    analyzer = GameAnalyzer(manager)
    rows = []
    try:
        for manifest_path in clips:
            directory = manifest_path.parent
            manifest = load_clip(directory)
            if manifest["hero"] not in manager.hero_data:
                raise ValueError(f"{directory}: 未知英雄 {manifest['hero']}")
            frames = read_frames(directory, manifest["frame_ms"], schema=manifest["schema"])
            row = replay_clip(analyzer, manifest, frames)
            print(f"{directory.name}: {row['false_frames']} 次误触发，"
                  f"{row['misses']} 次漏检，{row['unexpected_updates']} 次窗口内异常更新")
            for event in row["missed_events"]:
                print(f"  漏检: {event}")
            rows.append(row)
    finally:
        analyzer.executor.shutdown()
    return int(summarize(rows))


if __name__ == "__main__":
    raise SystemExit(main())
