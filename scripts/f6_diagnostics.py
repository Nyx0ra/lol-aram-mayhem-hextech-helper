"""Save local diagnostic crops and the OCR decisions from one press of F6."""

import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import cv2


def _save_png(path, image):
    encoded, png = cv2.imencode(".png", image)
    if not encoded:
        raise OSError(f"无法编码截图 {path.name}")
    path.write_bytes(png.tobytes())
    return path.name


def save_f6_diagnostics(directory, hero, layout, expanded, traces, results, timing):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now().astimezone()
    # Use the parent directory's permissions for files shared with this user.
    folder = root / (now.strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:12])
    folder.mkdir()
    _, started, captured, ready = timing
    report = {
        "time": now.isoformat(timespec="milliseconds"),
        "pid": os.getpid(),
        "hero": hero,
        "client": asdict(layout.client),
        "content": asdict(layout.content),
        "capture_ms": round((captured - started) * 1000, 1),
        "recognition_ms": round((ready - captured) * 1000, 1),
        "cards": {},
    }
    for key, image in expanded.items():
        card = {
            "title_region": asdict(layout.f6_title_region(key)),
            "expanded_region": asdict(layout.title_region(key, expanded=True)),
            "expanded_image": _save_png(folder / f"{key}_expanded.png", image),
            "attempts": [],
            "result": results.get(key),
        }
        for index, (ocr_image, lines, text, confidence, match, score, elapsed_ms) in enumerate(traces[key], 1):
            card["attempts"].append({
                "image": _save_png(folder / f"{key}_ocr_{index}.png", ocr_image),
                "ocr": [{"box": box, "text": label, "confidence": float(probability)}
                        for box, label, probability in lines],
                "title": text,
                "confidence": confidence,
                "match_name": match,
                "score": score,
                "elapsed_ms": elapsed_ms,
            })
        report["cards"][key] = card
    (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return folder
