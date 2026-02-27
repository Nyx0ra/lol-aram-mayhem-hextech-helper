# --- START OF FILE updater.py ---

import json
import csv
import os
import requests
import sys
import re
from pypinyin import lazy_pinyin 

# 1. 解决同级导入问题
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)
import hero_scraper as crawler

# 2. 解决路径问题
BASE_DIR = os.path.dirname(current_dir)
DATA_DIR = os.path.join(BASE_DIR, 'data')

# 配置路径
CHAMPION_ID_FILE = os.path.join(DATA_DIR, "champions.json")
PINYIN_FILE      = os.path.join(DATA_DIR, "pinyin_map.json")
CSV_FILE         = os.path.join(DATA_DIR, "hero_augments.csv")
CSV_HEADER       = ["中文名", "英文名", "序号", "海克斯名称"]

# ================= 1. 数据真理同步 =================
def sync_official_data():
    """
    从官方获取最新数据。
    返回: 
        official_en_to_cn: {英文ID: 中文名} (用于内部逻辑的主键字典)
        official_cn_to_en: {中文名: 英文ID} (用于保存champions.json)
        new_champs:[英文ID] (全新英雄)
        renamed_champs: [英文ID] (改名英雄)
    """
    print(">>> [1/4] 正在同步官方英雄数据...")
    try:
        ver_url = "https://ddragon.leagueoflegends.com/api/versions.json"
        version = requests.get(ver_url).json()[0]
        print(f"    当前游戏版本: {version}")

        champ_url = f"https://ddragon.leagueoflegends.com/cdn/{version}/data/zh_CN/champion.json"
        data = requests.get(champ_url).json()['data']

        official_en_to_cn = {}
        official_cn_to_en = {}
        for en_id, info in data.items():
            cn_name = info['name']
            official_en_to_cn[en_id] = cn_name
            official_cn_to_en[cn_name] = en_id

        # 读取本地旧数据，以 英文ID 作为主键进行对比
        old_en_to_cn = {}
        if os.path.exists(CHAMPION_ID_FILE):
            with open(CHAMPION_ID_FILE, 'r', encoding='utf-8') as f:
                old_cn_to_en = json.load(f)
                old_en_to_cn = {en: cn for cn, en in old_cn_to_en.items()}

        # 覆盖保存为最新的 champions.json
        with open(CHAMPION_ID_FILE, 'w', encoding='utf-8') as f:
            json.dump(official_cn_to_en, f, indent=4, ensure_ascii=False)
        
        # 精准计算增量：新英雄 & 改名英雄
        new_champs = []
        renamed_champs =[]
        
        for en_id, cn_name in official_en_to_cn.items():
            if en_id not in old_en_to_cn:
                new_champs.append(en_id)
            elif old_en_to_cn[en_id] != cn_name:
                renamed_champs.append(en_id)
        
        print(f"    同步完成。共 {len(official_en_to_cn)} 个英雄。")
        if new_champs:
            print(f"    🌟 发现 {len(new_champs)} 个全新英雄: {', '.join([official_en_to_cn[en] for en in new_champs])}")
        if renamed_champs:
            print(f"    ✏️ 发现 {len(renamed_champs)} 个改名英雄: {', '.join([official_en_to_cn[en] for en in renamed_champs])}")
            
        return official_en_to_cn, official_cn_to_en, new_champs, renamed_champs

    except Exception as e:
        print(f"!!! 官方数据同步失败，请检查网络: {e}")
        return {}, {}, [],[]

# ================= 2. 拼音生成 =================
def update_pinyin_file(official_cn_to_en):
    print(">>>[2/4] 更新拼音检索文件...")
    pinyin_data = {}
    for cn_name in official_cn_to_en.keys():
        pinyin_list = lazy_pinyin(cn_name)
        initials = "".join([p[0].lower() for p in pinyin_list if p])
        pinyin_data[cn_name] = initials
    
    with open(PINYIN_FILE, 'w', encoding='utf-8') as f:
        json.dump(pinyin_data, f, indent=4, ensure_ascii=False)
    print("    拼音文件已更新。")

# ================= 3. 数据保护逻辑 (读CSV) =================
def load_csv_history():
    """
    读取现有CSV到内存。
    【重要改动】：使用 英文名(en_name) 作为字典的 Key，防止中文改名导致找不到历史数据。
    返回结构: {en_name:[row_dict, ...]}
    """
    print(">>> [3/4] 读取本地历史数据 (主键: 英文ID)...")
    history = {}
    if not os.path.exists(CSV_FILE):
        return history

    try:
        with open(CSV_FILE, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                en_name = row.get('英文名')
                if en_name:
                    if en_name not in history:
                        history[en_name] = []
                    history[en_name].append(row)
        print(f"    已加载 {len(history)} 个英雄的历史数据。")
    except Exception as e:
        print(f"⚠️ 读取历史CSV时出错 (可能是空文件): {e}")
    
    return history

# ================= 4. 合并与保存 =================
def merge_and_save(official_en_to_cn, history_data, new_crawl_data):
    """
    以官方英文ID(en_name)为核心循环，合并新老数据。
    """
    print(">>> [4/4] 执行数据合并与持久化...")
    final_rows =[]
    missing_data_champions =[]

    # crawler 返回的字典 key 可能是 cn_name，为了稳定，我们通过官方映射把它转成以 en_name 为 key
    official_cn_to_en = {cn: en for en, cn in official_en_to_cn.items()}
    crawl_by_en = {official_cn_to_en.get(cn, cn): data for cn, data in new_crawl_data.items()}

    for en_name, cn_name in official_en_to_cn.items():
        rows_to_write =[]

        # 策略A：本次爬取成功，使用新数据
        if en_name in crawl_by_en:
            for item in crawl_by_en[en_name]:
                rows_to_write.append({
                    "中文名": cn_name, # 始终使用官方最新的中文名
                    "英文名": en_name,
                    "序号": item['index'],
                    "海克斯名称": item['name']
                })
        
        # 策略B：本次未爬取或爬取失败，保留旧数据（完美数据保护）
        elif en_name in history_data:
            rows_to_write = history_data[en_name]
            # 顺手把旧数据里的中文名更新为最新版，解决改名遗留问题
            for row in rows_to_write:
                row['中文名'] = cn_name
        
        # 策略C：彻底没有数据
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
        
    if missing_data_champions:
        print(f"\n⚠️ 注意: 有 {len(missing_data_champions)} 个英雄完全没有任何数据: {', '.join(missing_data_champions)}")

# ================= 主程序 =================
def main():
    print("=== ARAM 数据自动维护管理器 v6.0 (主键架构版) ===\n")

    # 1. 同步官方数据
    official_en_to_cn, official_cn_to_en, new_champs, renamed_champs = sync_official_data()
    if not official_en_to_cn:
        return

    # 2. 更新拼音
    update_pinyin_file(official_cn_to_en)

    # 3. 加载历史数据
    history_data = load_csv_history()
    
    # 计算缺失数据的英雄
    missing_champs =[en for en in official_en_to_cn if en not in history_data]

    # 4. 选择爬取模式
    print("\n请选择爬取策略:")
    print("   [1] 智能增量 (自动爬取: 全新英雄 + 改名英雄 + 本地无数据的英雄)")
    print("   [2] 全量更新 (强制重新爬取所有英雄，耗时较长)")
    print("   [3] 极速补漏 (仅爬取本地无数据的英雄)")
    print("   [4] 精确打击 (手动输入指定英雄名称进行更新)")
    
    choice = input("请输入选项 (默认1): ").strip()
    
    target_list =[] 

    if choice == '2':
        # 全量模式
        target_list = [(cn, en) for en, cn in official_en_to_cn.items()]
    elif choice == '3':
        # 极速补漏模式
        target_list =[(official_en_to_cn[en], en) for en in missing_champs]
    elif choice == '4':
        # 精确打击模式
        user_input = input("请输入要更新的英雄名或英文ID (多个用逗号或空格分隔): ").strip()
        query_names = re.split(r'[,，\s]+', user_input)
        for q in query_names:
            if not q: continue
            matched_en = None
            # 忽略大小写进行匹配
            for en, cn in official_en_to_cn.items():
                if q.lower() == en.lower() or q == cn:
                    matched_en = en
                    break
            if matched_en:
                target_list.append((official_en_to_cn[matched_en], matched_en))
            else:
                print(f"   [警告] 找不到对应的英雄: {q}")
        # 去重
        target_list = list(set(target_list))
    else:
        # 默认：智能增量模式
        # 合并集合并去重
        targets = set(new_champs + renamed_champs + missing_champs)
        target_list =[(official_en_to_cn[en], en) for en in targets]

    new_crawl_data = {}
    if target_list:
        print(f"\n>>> 准备爬取 {len(target_list)} 个目标英雄...")
        # 调用爬虫
        new_crawl_data, failed_list = crawler.crawl_champions(target_list)
        
        if failed_list:
            print(f"\n⚠️ 本次爬取遭遇失败的英雄: {failed_list}")
            print("    (无需担忧，程序会自动回退保留它们在 CSV 中的旧数据！)")
    else:
        print("\n>>> 检查完毕，没有需要执行爬取任务的目标。")

    # 5. 合并并保存
    merge_and_save(official_en_to_cn, history_data, new_crawl_data)

if __name__ == "__main__":
    main()