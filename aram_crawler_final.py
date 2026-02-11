import time
import random
import os
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, WebDriverException

# ==========================================
# 配置与初始化
# ==========================================
# 爬虫不再直接操作主文件，而是操作临时备份文件
TEMP_FILENAME = "temp_crawl_backup.csv"

def setup_driver():
    chrome_options = Options()
    # 生产环境开启无头模式 (后台运行)
    chrome_options.add_argument("--headless") 
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

# ==========================================
# 单个英雄抓取逻辑 (保持不变)
# ==========================================
def scrape_single_champion(driver, cn_name, en_name):
    url = f"https://blitz.gg/lol/champions/{en_name}/aram-mayhem"
    print(f"[{cn_name}] 正在处理: {url}")
    
    try:
        driver.get(url)
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(1.5)

        # 1. 点击 "显示全部"
        try:
            show_all_xpath = "//button[contains(., 'Show All') or contains(., '显示全部')]"
            expand_btn = WebDriverWait(driver, 4).until(EC.element_to_be_clickable((By.XPATH, show_all_xpath)))
            driver.execute_script("arguments[0].click();", expand_btn)
            time.sleep(1)
        except TimeoutException:
            pass 

        # 2. 滚动加载
        last_height = driver.execute_script("return document.body.scrollHeight")
        for _ in range(12): 
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)
            if len(driver.find_elements(By.XPATH, "//button[contains(., 'Collapse') or contains(., '收起')]")) > 0:
                break
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height

        # 3. 精准提取
        target_xpath = (
            "//div[contains(@class, 'AugmentCard')]//span[contains(@class, 'type-caption--bold')] | "
            "//div[contains(@class, 'augment')]//div[contains(@class, 'info')]//span[contains(@class, 'type-caption--bold')]"
        )
        
        elements = driver.find_elements(By.XPATH, target_xpath)
        valid_augments = []
        seen_texts = set()
        
        for el in elements:
            txt = el.text.strip()
            if not txt or len(txt) < 2: continue
            if txt in [cn_name, en_name]: continue 

            if txt not in seen_texts:
                valid_augments.append(txt)
                seen_texts.add(txt)

        results = [{"index": i, "name": n} for i, n in enumerate(valid_augments, 1)]
        status_code = "clean" if results else "empty"
        return results, status_code

    except Exception as e:
        print(f"[{cn_name}] 异常: {e}")
        return [], "error"

# ==========================================
# 批量抓取入口 (修复返回值和文件逻辑)
# ==========================================
def crawl_champions(target_list):
    """
    供外部脚本调用。
    返回: (success_data, failed_list)
    success_data 结构: { "中文名": [{'index':1, 'name':'xxx'}, ...] }
    """
    
    # 初始化临时文件 (防止主程序崩溃导致数据全丢)
    with open(TEMP_FILENAME, "w", encoding="utf-8-sig") as f:
        f.write("中文名,英文名,序号,海克斯名称\n") # 写入临时表头

    print(f"--- 开始抓取 {len(target_list)} 个英雄 ---")
    print(f"--- (安全起见，实时数据备份至 {TEMP_FILENAME}) ---")
    
    driver = setup_driver()
    failed_list = []
    success_data = {} # 【修复1】这里存储抓取到的数据，以便返回给管理器
    
    MAX_RETRIES = 3 

    try:
        total = len(target_list)
        for i, (cn_name, en_name) in enumerate(target_list, 1):
            print(f"--- 进度 [{i}/{total}] : {cn_name} ---")
            
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    data, status = scrape_single_champion(driver, cn_name, en_name)
                    
                    if status == "clean" and data:
                        # 【修复2】存入字典
                        success_data[cn_name] = data
                        
                        # 【修复3】写入临时文件 (而不是主文件)
                        with open(TEMP_FILENAME, "a", encoding="utf-8-sig") as f:
                            for item in data:
                                f.write(f"{cn_name},{en_name},{item['index']},{item['name']}\n")
                        
                        print(f"   > 成功抓取 {len(data)} 条 (已备份)")
                        break 
                    else:
                        print(f"   > 数据为空 (状态: {status})，重试 ({attempt})")
                
                except WebDriverException:
                    print(f"   > 驱动异常，重启中...")
                    try: driver.quit()
                    except: pass
                    driver = setup_driver()
                
                if attempt < MAX_RETRIES:
                    time.sleep(2)
                else:
                    print(f"   > ❌ {cn_name} 失败")
                    failed_list.append(cn_name)

            time.sleep(random.uniform(1.2, 2.0))
            
    finally:
        driver.quit()
        print(f"--- 爬取阶段结束 ---")
        
    # 【修复4】返回两个值，满足 auto_update_manager.py 的解包需求
    return success_data, failed_list

if __name__ == "__main__":
    # 测试代码
    crawl_champions([("暗裔剑魔", "Aatrox")])