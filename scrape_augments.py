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
# 1. 浏览器初始化 (复用原代码)
# ==========================================
def setup_driver():
    chrome_options = Options()
    # 生产环境开启无头模式
    chrome_options.add_argument("--headless") 
    # 固定使用 1080P 逻辑分辨率
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

# ==========================================
# 2. 全量海克斯抓取逻辑
# ==========================================
def scrape_all_augments():
    url = "https://blitz.gg/lol/aram-mayhem-augments"
    print(f"--- 开始任务: 正在访问 {url} ---")
    
    driver = setup_driver()
    results = {
        "prismatic": [],
        "gold": [],
        "silver": []
    }
    
    try:
        driver.get(url)
        # 等待页面基础加载
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(2)

        # -------------------------------------------------
        # A. 滚动加载 (模拟用户下滑以触发懒加载)
        # -------------------------------------------------
        print(">>> 正在滚动加载页面...")
        last_height = driver.execute_script("return document.body.scrollHeight")
        
        # 尝试滚动最多 20 次，或者直到高度不再变化
        for i in range(20):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.5) # 给懒加载留足时间
            
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                print("   > 页面高度不再变化，假定加载完成。")
                break
            last_height = new_height
        
        # -------------------------------------------------
        # B. 解析与分类
        # -------------------------------------------------
        print(">>> 正在解析数据...")
        
        # 定义类别与页面上标题关键词的映射关系
        # 注意：这里使用的是英文关键词，因为类名通常不会变，
        # 如果页面内容是中文，text() 匹配可能会失效，所以我们结合结构定位。
        # 策略：先找到包含特定文本的 Header，再找其父级 Section，再找里面的 Card
        
        target_map = [
            ("prismatic", "Prismatic ARAM Mayhem Augments"),
            ("gold", "Gold ARAM Mayhem Augments"),
            ("silver", "Silver ARAM Mayhem Augments")
        ]
        
        for key, search_text in target_map:
            print(f"   > 正在提取分类: {key} ({search_text})...")
            
            # XPath 逻辑解释：
            # 1. //section: 查找所有 section 标签
            # 2. [descendant::*[contains(text(), '...')]]: 筛选出子孙节点包含特定标题文本的 section
            # 3. //div[contains(@class, 'augment-card')]: 在该 section 下找海克斯卡片
            # 4. //h4[contains(@class, 'augment-name')]: 在卡片下找名字
            
            # 备注：为了兼容中文界面，如果 text() 找不到，可能需要依赖顺序，
            # 但通常 blitz.gg 的 URL 结构下的标题包含英文或特定结构。
            # 这里假设页面包含这些英文标题或者通过类名辅助。
            
            # 尝试定位包含特定标题的 Section
            # 我们放宽一点条件，查找包含 "Prismatic", "Gold", "Silver" 且包含 "Augments" 的标题
            tier_keyword = search_text.split()[0] # Prismatic / Gold / Silver
            
            # 构造 XPath：找到包含 关键词 的 Header (h4 或 p)，向上找最近的 section
            xpath_query = (
                f"//section[descendant::*[contains(text(), '{tier_keyword}') "
                f"and contains(text(), 'Augments')]]"
                f"//h4[contains(@class, 'augment-name')]"
            )
            
            elements = driver.find_elements(By.XPATH, xpath_query)
            
            # 提取文本
            extracted_names = []
            for el in elements:
                text = el.text.strip()
                if text and text not in extracted_names:
                    extracted_names.append(text)
            
            # 如果没抓到，尝试备用方案（中文环境适配）
            # 假设顺序是固定的：Prismatic -> Gold -> Silver
            # 但最好还是依赖 DOM 结构。这里如果 extracted_names 为空，打印警告。
            if not extracted_names:
                print(f"     [警告] 未能在 {key} 分类下找到数据，可能是页面语言导致标题不匹配。")
            else:
                print(f"     成功提取 {len(extracted_names)} 个海克斯。")
                results[key] = extracted_names

    except Exception as e:
        print(f"!!! 发生异常: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        driver.quit()
        print("--- 浏览器已关闭 ---")

    # -------------------------------------------------
    # C. 输出结果
    # -------------------------------------------------
    output_file = "augments.json"
    print(f">>> 正在保存结果到 {output_file} ...")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=4)
    print("--- 完成 ---")

if __name__ == "__main__":
    scrape_all_augments()