import json
import csv
import os
import requests
import sys
# 【修复】补上了这个关键的 import
from pypinyin import lazy_pinyin 

# 1. 解决同级导入问题
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)
import hero_scraper as crawler

# 2. 解决路径问题
BASE_DIR = os.path.dirname(current_dir)
DATA_DIR = os.path.join(BASE_DIR, 'data')

# 配置路径 (移除了 TEMP_FILE)
CHAMPION_ID_FILE = os.path.join(DATA_DIR, "champions.json")
PINYIN_FILE      = os.path.join(DATA_DIR, "pinyin_map.json")
CSV_FILE         = os.path.join(DATA_DIR, "hero_augments.csv")
CSV_HEADER       = ["中文名", "英文名", "序号", "海克斯名称"]

# ================= 1. 数据真理同步 =================
def sync_official_data():
    print(">>> [1/4] 正在同步官方英雄数据...")
    try:
        ver_url = "https://ddragon.leagueoflegends.com/api/versions.json"
        version = requests.get(ver_url).json()[0]
        print(f"    当前游戏版本: {version}")

        champ_url = f"https://ddragon.leagueoflegends.com/cdn/{version}/data/zh_CN/champion.json"
        data = requests.get(champ_url).json()['data']

        new_champion_map = {}
        for en_id, info in data.items():
            cn_name = info['name']
            new_champion_map[cn_name] = en_id

        old_keys = set()
        if os.path.exists(CHAMPION_ID_FILE):
            with open(CHAMPION_ID_FILE, 'r', encoding='utf-8') as f:
                old_data = json.load(f)
                old_keys = set(old_data.keys())

        with open(CHAMPION_ID_FILE, 'w', encoding='utf-8') as f:
            json.dump(new_champion_map, f, indent=4, ensure_ascii=False)
        
        new_keys = set(new_champion_map.keys())
        diff = new_keys - old_keys
        
        print(f"    同步完成。共 {len(new_champion_map)} 个英雄。")
        if diff:
            print(f"    发现 {len(diff)} 个新增/改名英雄: {', '.join(diff)}")
        else:
            print("    无新增英雄。")
            
        return new_champion_map, list(diff)

    except Exception as e:
        print(f"!!! 官方数据同步失败，请检查网络: {e}")
        return {}, []

# ================= 2. 拼音生成 =================
def update_pinyin_file(champion_map):
    print(">>> [2/4] 更新拼音检索文件...")
    pinyin_data = {}
    for cn_name in champion_map.keys():
        pinyin_list = lazy_pinyin(cn_name)
        initials = "".join([p[0].lower() for p in pinyin_list if p])
        pinyin_data[cn_name] = initials
    
    with open(PINYIN_FILE, 'w', encoding='utf-8') as f:
        json.dump(pinyin_data, f, indent=4, ensure_ascii=False)
    print("    拼音文件已更新。")

# ================= 3. 数据保护逻辑 (读CSV) =================
def load_csv_history():
    """读取现有CSV到内存，用于在爬取失败时保留旧数据"""
    print(">>> [3/4] 读取本地历史数据 (数据保护)...")
    history = {}
    if not os.path.exists(CSV_FILE):
        return history

    try:
        with open(CSV_FILE, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                cn_name = row.get('中文名')
                if cn_name:
                    if cn_name not in history:
                        history[cn_name] = []
                    history[cn_name].append(row)
        print(f"    已加载 {len(history)} 个英雄的历史数据。")
    except Exception as e:
        print(f"⚠️ 读取历史CSV时出错 (可能是空文件): {e}")
    
    return history

# ================= 4. 合并与保存 =================
def merge_and_save(champion_map, history_data, new_crawl_data):
    """
    核心逻辑：
    1. 遍历最新的 champion_map。
    2. 优先使用本次爬取的新数据 (new_crawl_data)。
    3. 如果没有新数据，回退使用历史数据 (history_data)。
    4. 都没有？记录为缺失。
    """
    print(">>> [4/4] 执行数据合并与持久化...")
    final_rows = []
    missing_data_champions = []

    for cn_name, en_name in champion_map.items():
        rows_to_write = []

        # 策略A：本次爬取成功，使用新数据（覆盖旧的）
        if cn_name in new_crawl_data:
            for item in new_crawl_data[cn_name]:
                rows_to_write.append({
                    "中文名": cn_name,
                    "英文名": en_name,
                    "序号": item['index'],
                    "海克斯名称": item['name']
                })
        
        # 策略B：本次未爬取或失败，保留旧数据（数据保护）
        elif cn_name in history_data:
            rows_to_write = history_data[cn_name]
            # 顺便更新一下旧数据里的英文名，防止官方改了英文ID导致不一致
            for row in rows_to_write:
                row['英文名'] = en_name
        
        # 策略C：既无新数据也无旧数据
        else:
            missing_data_champions.append(cn_name)
        
        if rows_to_write:
            final_rows.extend(rows_to_write)

    # 写入 CSV
    try:
        with open(CSV_FILE, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
            writer.writeheader()
            writer.writerows(final_rows)
        print(f"✅ 写入完成！主文件: {CSV_FILE} (共 {len(final_rows)} 条数据)")
    except Exception as e:
        print(f"❌ 写入主文件失败: {e}")

# ================= 主程序 =================
def main():
    print("=== ARAM 数据自动维护管理器 v5.0 (无缓存版) ===\n")

    # 1. 同步官方数据
    champion_map, new_champs = sync_official_data()
    if not champion_map:
        return

    # 2. 更新拼音
    update_pinyin_file(champion_map)

    # 3. 加载历史数据
    history_data = load_csv_history()

    # 4. 选择爬取模式
    print("\n请选择爬取策略:")
    print("   [1] 增量模式 (新英雄 + 本地缺失数据的英雄)")
    print("   [2] 全量模式 (强制重新爬取所有英雄)")
    print("   [3] 补漏模式 (仅爬取 CSV 中不存在的英雄)")
    
    choice = input("请输入选项 (默认1): ").strip()
    
    target_list = [] 

    if choice == '2':
        target_list = list(champion_map.items())
    elif choice == '3':
        for cn, en in champion_map.items():
            if cn not in history_data:
                target_list.append((cn, en))
    else:
        # 默认增量
        for cn, en in champion_map.items():
            if (cn in new_champs) or (cn not in history_data):
                target_list.append((cn, en))

    new_crawl_data = {}
    if target_list:
        print(f"\n>>> 准备爬取 {len(target_list)} 个英雄...")
        # 爬虫现在直接返回数据字典，不写文件
        new_crawl_data, failed_list = crawler.crawl_champions(target_list)
        
        if failed_list:
            print(f"\n⚠️ 本次爬取失败: {failed_list}")
            print("    (不用担心，旧数据会被自动保留)")
    else:
        print("    没有需要爬取的目标。")

    # 5. 合并并保存 (这里会处理新旧数据的优先级)
    merge_and_save(champion_map, history_data, new_crawl_data)

if __name__ == "__main__":
    main()