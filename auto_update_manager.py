import json
import csv
import os
import requests
import time
from pypinyin import lazy_pinyin
# 引入上面的爬虫模块
import aram_crawler_final as crawler

# ================= 配置 =================
CHAMPION_ID_FILE = "champion_ids.json"
PINYIN_FILE = "output_pinyin.json"
CSV_FILE = "aram_augments_final.csv"
TEMP_FILE = "temp_crawl_backup.csv"  # 对应爬虫生成的临时文件名
CSV_HEADER = ["中文名", "英文名", "序号", "海克斯名称"]

# ================= 1. 数据真理同步 =================
def sync_official_data():
    """从官方API获取最新英雄列表，并更新本地JSON"""
    print(">>> [1/5] 正在同步官方英雄数据...")
    try:
        # 1. 获取游戏最新版本号
        ver_url = "https://ddragon.leagueoflegends.com/api/versions.json"
        version = requests.get(ver_url).json()[0]
        print(f"    当前游戏版本: {version}")

        # 2. 获取该版本的英雄列表
        champ_url = f"https://ddragon.leagueoflegends.com/cdn/{version}/data/zh_CN/champion.json"
        data = requests.get(champ_url).json()['data']

        # 3. 构建映射字典 {中文名: 英文ID}
        new_champion_map = {}
        for en_id, info in data.items():
            cn_name = info['name']
            new_champion_map[cn_name] = en_id

        # 4. 读取本地旧数据
        old_keys = set()
        if os.path.exists(CHAMPION_ID_FILE):
            with open(CHAMPION_ID_FILE, 'r', encoding='utf-8') as f:
                old_data = json.load(f)
                old_keys = set(old_data.keys())

        # 5. 保存
        with open(CHAMPION_ID_FILE, 'w', encoding='utf-8') as f:
            json.dump(new_champion_map, f, indent=4, ensure_ascii=False)
        
        # 6. 计算新增
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
    """根据最新的英雄列表生成拼音映射"""
    print(">>> [2/5] 更新拼音检索文件...")
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
    """读取现有CSV到内存"""
    print(">>> [3/5] 读取本地历史数据 (数据保护)...")
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
    """合并新旧数据并保存到主 CSV"""
    print(">>> [5/5] 执行数据合并与持久化...")
    final_rows = []
    missing_data_champions = []

    for cn_name, en_name in champion_map.items():
        rows_to_write = []

        # 策略A：本次爬取成功，使用新数据
        if cn_name in new_crawl_data:
            for item in new_crawl_data[cn_name]:
                rows_to_write.append({
                    "中文名": cn_name,
                    "英文名": en_name,
                    "序号": item['index'],
                    "海克斯名称": item['name']
                })
        
        # 策略B：使用历史数据
        elif cn_name in history_data:
            rows_to_write = history_data[cn_name]
            # 更新英文名以防万一
            for row in rows_to_write:
                row['英文名'] = en_name
        
        # 策略C：缺失
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
        return True # 返回成功标志
    except Exception as e:
        print(f"❌ 写入主文件失败: {e}")
        return False

# ================= 主程序 =================
def main():
    print("=== ARAM 数据自动维护管理器 v4.1 (自动清理版) ===\n")

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
        print(f"\n>>> [4/5] 准备爬取 {len(target_list)} 个英雄...")
        # 调用爬虫模块 (现在返回两个值了)
        new_crawl_data, failed_list = crawler.crawl_champions(target_list)
        
        if failed_list:
            print(f"\n⚠️ 本次爬取失败: {failed_list}")
    else:
        print("    没有需要爬取的目标。")

    # 5. 合并并保存
    success = merge_and_save(champion_map, history_data, new_crawl_data)

    # 6. 【新增】自动清理临时文件逻辑
    if success:
        if os.path.exists(TEMP_FILE):
            try:
                os.remove(TEMP_FILE)
                print(f">>> [清理] 临时备份文件 ({TEMP_FILE}) 已自动删除。")
            except Exception as e:
                print(f"⚠️ 无法删除临时文件: {e}")
    else:
        print(f"⚠️ 数据合并似乎有问题，保留临时文件 {TEMP_FILE} 以防万一。")

if __name__ == "__main__":
    main()