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
# 单个英雄抓取逻辑
# ==========================================
def scrape_single_champion(driver, cn_name, en_name):
    url = f"https://blitz.gg/zh-CN/lol/champions/{en_name}/aram-mayhem"
    print(f"[{cn_name}] 正在处理: {url}")
    
    try:
        driver.get(url)
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(1.5)

        # 1. 关闭可能的弹窗/Cookie 同意框
        try:
            driver.execute_script("""
            document.querySelectorAll('[class*="consent"], [class*="cookie"]').forEach(el => el.remove());
            document.querySelectorAll('button').forEach(b => {
                var t = (b.textContent || '').toLowerCase();
                if (t.includes('agree') || t.includes('accept') || t.includes('同意')) b.click();
            });
            """)
        except: pass
        
        # 2. 点击 augments-toggle 按钮展开所有海克斯
        time.sleep(2)
        for attempt in range(3):
            try:
                toggle_btn = driver.find_element(By.CSS_SELECTOR, "button.augments-toggle")
                btn_text = toggle_btn.text.strip()
                print(f"   > 找到按钮: '{btn_text}'")
                if "显示所有" in btn_text or "Show All" in btn_text:
                    driver.execute_script("arguments[0].scrollIntoView(true);", toggle_btn)
                    time.sleep(0.3)
                    driver.execute_script("arguments[0].click();", toggle_btn)
                    time.sleep(2)
                    print(f"   > 已点击展开")
                break
            except Exception:
                # 滚动触发懒加载后重试
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1.5)
                driver.execute_script("window.scrollTo(0, 0);")
                time.sleep(1)
        
        # 3. 滚动加载剩余内容
        for _ in range(8): 
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)

        # 3. 按等级提取
        rarity_blocks = driver.find_elements(By.XPATH, "//div[contains(@class, 'rarity')]")
        
        valid_augments = []
        seen_texts = set()
        
        # 如果没有找到 rarity 块，可能需要回退到旧版抓取或报错，但根据新截图，会有 rarity 块
        for block in rarity_blocks:
            try:
                # 获取该区块的等级名称
                tier_name_el = block.find_element(By.XPATH, ".//span[contains(@class, 'rarity-name')]")
                tier_text = tier_name_el.text.strip()
                
                # 根据文本判断是哪种等级
                tier = "未知"
                if "棱彩" in tier_text or "Prismatic" in tier_text: tier = "棱彩"
                elif "金" in tier_text or "Gold" in tier_text: tier = "黄金"
                elif "银" in tier_text or "Silver" in tier_text: tier = "白银"
                
                # 获取该区块下的所有海克斯名字
                augment_elements = block.find_elements(By.XPATH, ".//span[contains(@class, 'name') and contains(@class, 'type-caption--bold')]")
                
                t_rank_counter = 1
                for el in augment_elements:
                    txt = el.text.strip()
                    if not txt or len(txt) < 2: continue
                    if txt in [cn_name, en_name]: continue 

                    if txt not in seen_texts:
                        valid_augments.append({
                            "tier": tier,
                            "t_rank": t_rank_counter,
                            "name": txt
                        })
                        seen_texts.add(txt)
                        t_rank_counter += 1
                        
            except Exception as inner_e:
                print(f"[{cn_name}] 解析等级区块异常: {inner_e}")
                continue

        status_code = "clean" if valid_augments else "empty"
        return valid_augments, status_code

    except Exception as e:
        print(f"[{cn_name}] 异常: {e}")
        return [], "error"

# ==========================================
# 批量抓取入口
# ==========================================
def crawl_champions(target_list):
    """
    直接返回内存字典，不再写临时文件
    """
    print(f"--- 开始抓取 {len(target_list)} 个英雄 ---")
    
    driver = setup_driver()
    failed_list = []
    success_data = {} 
    
    MAX_RETRIES = 3 

    try:
        total = len(target_list)
        for i, (cn_name, en_name) in enumerate(target_list, 1):
            print(f"--- 进度 [{i}/{total}] : {cn_name} ---")
            
            # 定期重启浏览器释放内存，防止 OOM 崩溃
            if i > 1 and i % 15 == 0:
                print("   > [系统] 定期重启浏览器释放资源...")
                try: driver.quit()
                except: pass
                driver = setup_driver()
            
            for attempt in range(1, MAX_RETRIES + 1):
                data, status = scrape_single_champion(driver, cn_name, en_name)
                
                if status == "clean" and data:
                    # 成功！直接存入内存字典
                    success_data[cn_name] = data
                    print(f"   > 成功抓取 {len(data)} 条")
                    break 
                else:
                    print(f"   > 数据为空 (状态: {status})，重试 ({attempt})")
                    # 检查浏览器是否存活，如果不能连了就直接重启
                    try:
                        _ = driver.title
                    except Exception:
                        print(f"   > 浏览器连接断开，重启中...")
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
        
    return success_data, failed_list

if __name__ == "__main__":
    # 测试代码
    res, fail = crawl_champions([("暗裔剑魔", "Aatrox")])
    print(res)