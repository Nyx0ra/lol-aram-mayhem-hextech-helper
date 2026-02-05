import time
import random
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, WebDriverException

# ==========================================
# 1. 浏览器初始化
# ==========================================
def setup_driver():
    chrome_options = Options()
    # 生产环境开启无头模式，不仅速度快，而且不受本地显示器分辨率影响
    chrome_options.add_argument("--headless") 
    # 固定使用 1080P 逻辑分辨率，保证网页元素布局是桌面版，防止按钮被折叠
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

# ==========================================
# 2. 单个英雄抓取逻辑
# ==========================================
def scrape_single_champion(driver, cn_name, en_name):
    """
    爬取单个英雄数据。
    返回: (data_list, status_code)
    """
    # 这里的 en_name 必须是内部ID (如 MonkeyKing 而非 Wukong)
    url = f"https://blitz.gg/lol/champions/{en_name}/aram-mayhem"
    print(f"[{cn_name}] 正在处理: {url}")
    
    status_code = "unknown"
    
    try:
        driver.get(url)
        # 等待页面主体加载
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(1.5)

        # A. 点击展开 "Show All"
        try:
            # 兼容中英文界面
            show_all_xpath = "//button[contains(text(), 'Show All') or contains(text(), '显示全部')]"
            expand_btn = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.XPATH, show_all_xpath)))
            driver.execute_script("arguments[0].click();", expand_btn)
            time.sleep(1)
        except TimeoutException:
            pass # 有些英雄可能数据少，不需要展开，直接跳过

        # B. 滚动加载 (模拟用户下滑)
        last_height = driver.execute_script("return document.body.scrollHeight")
        for _ in range(15):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(0.8)
            # 如果看到折叠按钮，说明到底了
            if driver.find_elements(By.XPATH, "//button[contains(text(), 'Collapse') or contains(text(), '收起')]"):
                break
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height

        # C. 解析数据
        # 抓取所有可能包含海克斯名称的文本标签
        elements = driver.find_elements(By.XPATH, "//p | //span | //div[not(*)] | //button")
        raw_texts = [el.text.strip() for el in elements if el.text.strip()]
        
        # 定位海克斯列表的起始位置
        start_index = 0
        for i, text in enumerate(raw_texts):
            if "Tier List" in text or "wild augments" in text:
                start_index = i + 1
            if "build, from augments to skill order" in text:
                 start_index = i + 1
        
        # 如果没找到标志性词语，默认从第15行开始尝试（经验值）
        if start_index == 0: start_index = 15

        valid_augments = []
        seen_texts = set()
        
        # 停止信号关键词
        EXACT_STOP_MARKERS = ["Collapse", "收起"]
        # 保险丝：如果遇到这些词，说明已经滚到了下方的出装推荐区域，必须强制停止
        PHRASE_STOP_MARKERS = ["物品构建路径", "Item Build Path", "Skill Order", "Core Item", "Summoner Spells"]
        # 垃圾词过滤
        IGNORE_WORDS = ["S", "A", "B", "C", "D", "Tier", "%", "Win Rate", "Show All"]

        status_code = "eof" # 默认为“读到文件末尾”

        for i in range(start_index, len(raw_texts)):
            txt = raw_texts[i]
            
            # 1. 完美停止
            if txt in EXACT_STOP_MARKERS:
                status_code = "clean"
                break
            
            # 2. 保险丝停止
            should_break = False
            for marker in PHRASE_STOP_MARKERS:
                if marker in txt: should_break = True; break
            # 遇到类似 "第1步" 这种引导文案也要停
            if txt.startswith("第") and txt[1:].isdigit() and len(txt) < 5: should_break = True
            
            if should_break:
                status_code = "safety"
                break 

            # 3. 数据清洗
            if len(txt) < 2 or len(txt) > 40: continue
            if any(bad in txt for bad in IGNORE_WORDS): continue
            if txt[0].isdigit(): continue # 过滤纯数字
            if txt == cn_name or txt == en_name: continue # 过滤英雄名本身

            # 去重并添加
            if txt not in seen_texts:
                valid_augments.append(txt)
                seen_texts.add(txt)

        # 格式化输出
        results = []
        for idx, name in enumerate(valid_augments, 1):
            results.append({"index": idx, "name": name})
            
        return results, status_code

    except Exception as e:
        print(f"[{cn_name}] 异常: {e}")
        return [], "error"

# ==========================================
# 3. 批量抓取入口 (含重试逻辑)
# ==========================================
def crawl_champions(target_list):
    """
    接收目标列表 [(cn, en), ...]
    返回字典: { cn_name: [{'index':1, 'name':'xxx'}, ...] }
    及失败列表: [cn_name, ...]
    """
    driver = setup_driver()
    success_data = {}
    failed_list = []
    
    MAX_RETRIES = 3 # 每个英雄最大重试次数

    try:
        total = len(target_list)
        for i, (cn_name, en_name) in enumerate(target_list, 1):
            print(f"--- 进度 [{i}/{total}] : {cn_name} ({en_name}) ---")
            
            data = []
            status = "init"
            
            # 开始重试循环
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    data, status = scrape_single_champion(driver, cn_name, en_name)
                    
                    # 成功判断：状态码正常(clean/safety) 且 抓到了数据
                    if (status in ["clean", "safety"]) and data:
                        print(f"   > 成功 (尝试第 {attempt} 次)")
                        success_data[cn_name] = data
                        break # 成功则跳出循环，处理下一个英雄
                    else:
                        print(f"   > 警告: 数据可能不完整或为空 (状态: {status})，准备重试...")
                
                except WebDriverException:
                    print(f"   > 浏览器崩溃，正在重启驱动...")
                    try:
                        driver.quit()
                    except:
                        pass
                    driver = setup_driver() # 重启
                
                # 如果没成功，且还有重试机会
                if attempt < MAX_RETRIES:
                    time.sleep(2) # 冷却一下
                else:
                    print(f"   > ❌ 最终失败: {cn_name}")
                    failed_list.append(cn_name)

            # 英雄之间的随机间隔，防止被封 IP
            time.sleep(random.uniform(1.5, 2.5))
            
    finally:
        driver.quit()
        
    return success_data, failed_list