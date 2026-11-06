"""
拼音映射生成工具 (独立运行入口)
运行: python -m scripts.utils
功能与 updater.update_pinyin_file() 相同，可独立使用。
"""
import sys
import os
import json

# 兼容直接运行和包导入
try:
    from scripts.config import CHAMPION_ID_FILE
    from scripts.updater import update_pinyin_file
except ImportError:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(current_dir)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)
    from scripts.config import CHAMPION_ID_FILE
    from scripts.updater import update_pinyin_file


def main():
    """从已保存的英雄映射重新生成拼音索引。"""
    try:
        with open(CHAMPION_ID_FILE, encoding="utf-8") as source:
            cn_to_en = json.load(source)
        update_pinyin_file(cn_to_en)
        print("✅ 拼音映射已更新")
    except (OSError, ValueError) as error:
        print(f"❌ 拼音映射更新失败: {error}")


if __name__ == "__main__":
    main()
