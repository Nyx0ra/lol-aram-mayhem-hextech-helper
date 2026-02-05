import json
import os
from pypinyin import lazy_pinyin

# 定义文件名
INPUT_FILE = 'champion_ids.json'    # 你的源文件
OUTPUT_FILE = 'output_pinyin.json'  # 输出文件

def generate_pinyin_dict():
    # 1. 检查源文件是否存在
    if not os.path.exists(INPUT_FILE):
        print(f"错误：未找到文件 '{INPUT_FILE}'。请确保该文件在当前目录下。")
        return

    # 2. 读取 JSON 文件
    try:
        with open(INPUT_FILE, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)
            print(f"成功读取 {len(raw_data)} 个英雄数据。")
    except json.JSONDecodeError:
        print(f"错误：'{INPUT_FILE}' 不是有效的 JSON 格式。")
        return

    # 3. 处理数据：中文名 -> 拼音首字母
    new_data = {}
    
    for cn_name in raw_data.keys():
        # 获取拼音列表，例如 ['an', 'yi', 'jian', 'mo']
        pinyin_list = lazy_pinyin(cn_name)
        
        # 提取首字母并转小写
        # 增加判断以防空字符串
        initials = "".join([p[0].lower() for p in pinyin_list if p])
        
        new_data[cn_name] = initials

    # 4. 打印结果预览
    print("-" * 30)
    print("转换结果预览 (前5个):")
    # 只打印前5个作为示例
    for i, (k, v) in enumerate(new_data.items()):
        if i < 5:
            print(f"{k}: {v}")
    print("-" * 30)

    # 5. 保存到新文件
    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(new_data, f, indent=4, ensure_ascii=False)
        print(f"成功！已将结果保存至 '{OUTPUT_FILE}'")
    except Exception as e:
        print(f"保存文件时出错: {e}")

if __name__ == "__main__":
    generate_pinyin_dict()