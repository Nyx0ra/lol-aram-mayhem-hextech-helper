"""
Live Client Data & LCU API 探测工具
在英雄联盟游戏运行中（或选人阶段、海克斯三选一弹窗弹出时）运行本脚本，
将自动抓取当前官方接口暴露的全部 JSON 数据并保存到本地，便于开发者分析最新客户端数据结构。
"""
import os
import sys
import json
import urllib3
import requests

# 兼容路径
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(CURRENT_DIR)
sys.path.insert(0, BASE_DIR)

from scripts.lcu_connector import LCUConnector

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def probe_live_api():
    print("=" * 60)
    print("1. 正在探测 127.0.0.1:2999 Live Client Data API (局内免密接口)...")
    print("=" * 60)

    live_session = requests.Session()
    endpoints = [
        ("allgamedata", "https://127.0.0.1:2999/liveclientdata/allgamedata"),
        ("activeplayer", "https://127.0.0.1:2999/liveclientdata/activeplayer"),
        ("playerlist", "https://127.0.0.1:2999/liveclientdata/playerlist"),
        ("eventdata", "https://127.0.0.1:2999/liveclientdata/eventdata"),
        ("gamestats", "https://127.0.0.1:2999/liveclientdata/gamestats"),
        ("openapi_v3", "https://127.0.0.1:2999/swagger/v3/openapi.json"),
    ]

    dump_dir = os.path.join(BASE_DIR, "data", "api_dumps")
    os.makedirs(dump_dir, exist_ok=True)

    live_online = False
    for name, url in endpoints:
        try:
            resp = live_session.get(url, verify=False, timeout=1.5)
            if resp.status_code == 200:
                live_online = True
                data = resp.json()
                save_path = os.path.join(dump_dir, f"live_{name}.json")
                with open(save_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print(f"  [OK] {name} ({resp.status_code}) -> 已保存至 {save_path}")
            else:
                print(f"  [--] {name} (HTTP {resp.status_code})")
        except requests.exceptions.ConnectionError:
            print(f"  [FAIL] {name} (无法连接，说明当前游戏对局尚未开始)")
            break
        except Exception as e:
            print(f"  [ERR] {name} ({e})")

    if not live_online:
        print("  提示: 2999 端口仅在对局加载完成、进入召唤师峡谷/嚎哭深渊后才开放。")

    print("\n" + "=" * 60)
    print("2. 正在探测 LCU API (客户端桌面与选人接口)...")
    print("=" * 60)

    champions_path = os.path.join(BASE_DIR, "data", "champions.json")
    lcu = LCUConnector(champions_path)
    if lcu.connect():
        print(f"  [OK] LCU 已成功连通 (端口: {lcu.port})")
        phase, hero, source, details = lcu.get_session_state()
        print(f"  当前游戏阶段: {phase}")
        print(f"  识别到的英雄: {hero} (数据源: {source})")

        lcu_endpoints = [
            ("gameflow_phase", "/lol-gameflow/v1/gameflow-phase"),
            ("champ_select", "/lol-champ-select/v1/session"),
            ("gameflow_session", "/lol-gameflow/v1/session"),
            ("current_summoner", "/lol-summoner/v1/current-summoner"),
        ]

        for name, ep in lcu_endpoints:
            resp = lcu._request("GET", ep)
            if resp and resp.status_code == 200:
                try:
                    data = resp.json()
                    save_path = os.path.join(dump_dir, f"lcu_{name}.json")
                    with open(save_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    print(f"  [OK] {name} -> 已保存至 {save_path}")
                except Exception:
                    pass
            else:
                code = resp.status_code if resp else "None"
                print(f"  [--] {name} (HTTP {code})")
    else:
        print("  [FAIL] 未检测到运行中的英雄联盟桌面客户端 (LeagueClientUx.exe)")

    print("\n" + "=" * 60)
    print(f"探测完成! 所有抓取到的 JSON 文件已输出到: {dump_dir}")
    print("=" * 60)

if __name__ == '__main__':
    probe_live_api()
