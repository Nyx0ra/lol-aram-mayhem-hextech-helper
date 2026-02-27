# --- START OF FILE tier_scraper.py ---
import time
import json
import os
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# ==========================================
# 1. 浏览器初始化
# ==========================================
def setup_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless") 
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    # 强制中文环境
    chrome_options.add_argument("--lang=zh-CN")
    chrome_options.add_experimental_option('prefs', {'intl.accept_languages': 'zh-CN,zh;q=0.9'})
    chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

# ==========================================
# 2. 全量海克斯抓取逻辑 (含数据保护逻辑)
# ==========================================
def scrape_all_augments(output_file="data/tiers.json"):
    url = "https://blitz.gg/lol/aram-mayhem-augments"
    print(f"\n--- 开始拉取海克斯分级: 正在访问 {url} ---")
    
    driver = setup_driver()
    results = {
        "prismatic": [],
        "gold":[],
        "silver":[]
    }
    
    # 【新增】成功标识符，用于判断是否要覆盖写入文件
    success_flag = False 
    
    try:
        driver.get(url)
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(2)

        # A. 滚动加载
        print("   > 正在滚动加载页面...")
        last_height = driver.execute_script("return document.body.scrollHeight")
        for i in range(20):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.5)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                print("   > 页面加载完成。")
                break
            last_height = new_height
        
        # B. 解析与分类
        print("   > 正在解析数据...")
        target_map =[
            ("prismatic", "Prismatic ARAM Mayhem Augments"),
            ("gold", "Gold ARAM Mayhem Augments"),
            ("silver", "Silver ARAM Mayhem Augments")
        ]
        
        for key, search_text in target_map:
            print(f"     -> 正在提取分类: {key} ...")
            tier_keyword = search_text.split()[0] 
            
            xpath_query = (
                f"//section[descendant::*[contains(text(), '{tier_keyword}') "
                f"and contains(text(), 'Augments')]]"
                f"//h4[contains(@class, 'augment-name')]"
            )
            
            elements = driver.find_elements(By.XPATH, xpath_query)
            extracted_names =[]
            for el in elements:
                text = el.text.strip()
                if text and text not in extracted_names:
                    extracted_names.append(text)
            
            if not extracted_names:
                print(f"     [警告] 未能在 {key} 分类下找到数据。")
            else:
                print(f"     ✅ 成功提取 {len(extracted_names)} 个海克斯。")
                results[key] = extracted_names

        # 【新增】检查是否真正抓到了数据（总数大于0就算成功）
        total_extracted = sum(len(v) for v in results.values())
        if total_extracted > 0:
            success_flag = True

    except Exception as e:
        print(f"!!! 发生异常: {e}")
        
    finally:
        driver.quit()

    # C. 输出结果 (数据保护核心拦截点)
    if success_flag:
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        print(f"   > 正在保存最新结果到 {output_file} ...")
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=4)
        print("--- 海克斯分级字典更新完成 ---\n")
    else:
        print(f"   > ❌ 拉取失败或未获取到任何数据！")
        print(f"   > 🛡️ 已触发数据保护，直接跳过保存，原有 {output_file} 数据安全保留。")
        print("--- 海克斯分级字典更新中止 ---\n")

if __name__ == "__main__":
    # 本地单独测试时，放在当前目录下
    scrape_all_augments("tiers_test.json")