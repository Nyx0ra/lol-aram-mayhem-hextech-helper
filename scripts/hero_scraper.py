import time
import random
import os
import glob
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
try:
    from scripts.update_control import UpdateCancelled, check_cancelled, wait_or_cancel
except ImportError:
    from update_control import UpdateCancelled, check_cancelled, wait_or_cancel

# ==========================================
# ChromeDriver 查找与初始化
# ==========================================
def _find_cached_chromedriver():
    """在本地 .wdm 缓存目录中查找最新版本的 chromedriver.exe，免去联网检查。"""
    wdm_dir = os.path.join(os.path.expanduser("~"), ".wdm", "drivers", "chromedriver")
    if not os.path.isdir(wdm_dir):
        return None
    
    # 递归查找所有 chromedriver.exe
    pattern = os.path.join(wdm_dir, "**", "chromedriver.exe")
    candidates = glob.glob(pattern, recursive=True)
    if not candidates:
        return None
    
    # 按文件修改时间排序，取最新的
    candidates.sort(key=os.path.getmtime, reverse=True)
    return candidates[0]


def _init_driver_with_fallback(chrome_options):
    """
    尝试初始化 ChromeDriver，包含降级和缓存失效处理策略。
    """
    # 1. 尝试使用本地缓存
    cached_path = _find_cached_chromedriver()
    if cached_path:
        print(f"   [Driver] 尝试使用本地缓存: {cached_path}")
        try:
            service = Service(cached_path)
            return webdriver.Chrome(service=service, options=chrome_options)
        except WebDriverException as e:
            if "session not created" in str(e).lower() or "version of chromedriver" in str(e).lower():
                print(f"   [Driver] ⚠ 本地缓存版本与当前浏览器不匹配，将重新下载...")
            else:
                raise
    
    # 2. 尝试使用 webdriver_manager 联网下载最新版
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        print("   [Driver] 尝试联网下载最新的 ChromeDriver...")
        path = ChromeDriverManager().install()
        print(f"   [Driver] 下载成功: {path}")
        service = Service(path)
        return webdriver.Chrome(service=service, options=chrome_options)
    except Exception as e:
        print(f"   [Driver] 联网下载失败 (可能需要代理/梯子): {e}")
        
    # 3. 降级: 由 Selenium 自动查找 ChromeDriver
    print("   [Driver] 降级: 由 Selenium 自动查找 ChromeDriver...")
    return webdriver.Chrome(options=chrome_options)


def setup_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    # 强制中文环境
    chrome_options.add_argument("--lang=zh-CN")
    # 性能优化：禁用不必要的特性
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.page_load_strategy = 'eager'  # 不等待所有资源加载完毕
    chrome_options.add_experimental_option('prefs', {
        'intl.accept_languages': 'zh-CN,zh;q=0.9',
        # 禁用图片加载，大幅提升速度
        'profile.managed_default_content_settings.images': 2,
    })
    chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])

    driver = _init_driver_with_fallback(chrome_options)
    try:
        driver.set_page_load_timeout(15)
        driver.set_script_timeout(10)
    except Exception:
        driver.quit()
        raise

    # 不设置隐式等待，全部使用显式等待 (WebDriverWait)，避免超时累加
    return driver

# ==========================================
# 常量
# ==========================================
_AUGMENT_SELECTOR = "strong.text-sm.text-gray-900"

# 一次性 JS 脚本：提取当前 Tab 下所有海克斯名称
_JS_EXTRACT_NAMES = """
    var names = [];
    document.querySelectorAll('strong.text-sm.text-gray-900').forEach(function(el) {
        var t = el.textContent.trim();
        if (t && t.length >= 2) names.push(t);
    });
    return names;
"""

# 一次性 JS 脚本：点击指定文本的 Tab 按钮，返回是否成功
_JS_CLICK_TAB = """
    var target = arguments[0];
    var buttons = document.querySelectorAll('button');
    for (var i = 0; i < buttons.length; i++) {
        if (buttons[i].textContent.trim() === target) {
            buttons[i].click();
            return true;
        }
    }
    return false;
"""

# 一次性 JS 脚本：关闭弹窗
_JS_DISMISS_POPUP = """
    document.querySelectorAll('[class*="consent"], [class*="cookie"]').forEach(el => el.remove());
    document.querySelectorAll('button').forEach(b => {
        var t = (b.textContent || '').toLowerCase();
        if (t.includes('agree') || t.includes('accept') || t.includes('同意')) b.click();
    });
"""

# ==========================================
# 从当前页面提取海克斯名称列表（JS批量提取）
# ==========================================
def _wait_for_augments(driver, seconds, cancel_event):
    def ready(browser):
        check_cancelled(cancel_event)
        return EC.presence_of_element_located((By.CSS_SELECTOR, _AUGMENT_SELECTOR))(browser)
    return WebDriverWait(driver, seconds).until(ready)


def extract_augment_names_fast(driver, cancel_event=None):
    """用单次 JS 调用批量提取当前 Tab 下所有海克斯名称"""
    try:
        # 等待至少一个海克斯元素出现
        _wait_for_augments(driver, 8, cancel_event)
        # 短暂等待确保 DOM 更新完成（Tab 切换后内容渲染）
        wait_or_cancel(0.3, cancel_event)
        # 单次 JS 调用提取所有名称，避免逐个 element.text 的 round-trip
        names = driver.execute_script(_JS_EXTRACT_NAMES)
        return names if names else []
    except TimeoutException:
        return []
    except UpdateCancelled:
        raise
    except Exception as e:
        print(f"   > 提取海克斯名称异常: {e}")
        return []

# ==========================================
# 点击 Tab 并等待内容刷新
# ==========================================
def click_tab_and_wait(driver, tab_text, prev_names=None, cancel_event=None):
    """
    点击指定 Tab 并智能等待内容刷新。
    通过对比前后内容变化来判断刷新完成，而非固定等待。
    """
    try:
        check_cancelled(cancel_event)
        clicked = driver.execute_script(_JS_CLICK_TAB, tab_text)
        if not clicked:
            print(f"   > 未找到按钮: {tab_text}")
            return False

        if prev_names is not None:
            # 智能等待：等内容变化或最多 3 秒
            for _ in range(15):
                wait_or_cancel(0.2, cancel_event)
                current = driver.execute_script(_JS_EXTRACT_NAMES)
                if current != prev_names:
                    return True
            # 超时也返回 True，可能内容就是相同的
        else:
            wait_or_cancel(0.5, cancel_event)  # 首次无对比基准，短暂等待即可

        return True
    except UpdateCancelled:
        raise
    except Exception as e:
        print(f"   > 点击Tab异常 ({tab_text}): {e}")
        return False

# ==========================================
# 单个英雄抓取逻辑 (数据源: OP.GG) — 优化版
# ==========================================
def scrape_single_champion(driver, cn_name, en_name, is_first_page=False, cancel_event=None):
    url = f"https://op.gg/zh-cn/lol/modes/aram-mayhem/{en_name}/augments"
    print(f"[{cn_name}] 正在处理: {url}")

    try:
        check_cancelled(cancel_event)
        driver.get(url)
        # 等待海克斯列表出现（而非仅等 body 标签）
        _wait_for_augments(driver, 12, cancel_event)

        # 仅首页处理弹窗（cookie 同意在 session 内只出现一次）
        if is_first_page:
            try:
                driver.execute_script(_JS_DISMISS_POPUP)
            except:
                pass
            wait_or_cancel(0.3, cancel_event)

        # 1. 提取「全部」Tab 数据（页面默认就是全部 Tab）
        print(f"   > 提取「全部」排名...")
        # 先确保在全部 Tab
        click_tab_and_wait(driver, "全部", cancel_event=cancel_event)
        all_names = extract_augment_names_fast(driver, cancel_event=cancel_event)
        print(f"   > 「全部」共 {len(all_names)} 个")

        # 构建总排名映射
        overall_rank_map = {}
        for idx, name in enumerate(all_names, 1):
            if name not in overall_rank_map:
                overall_rank_map[name] = idx

        # 2. 依次切换等级 Tab，提取等级内排名
        tier_data = {}
        prev_names = all_names  # 用于智能等待对比

        tier_mapping = {
            "白银": "银",
            "黄金": "黄金",
            "棱彩": "棱镜"
        }

        for internal_tier, tab_name in tier_mapping.items():
            if click_tab_and_wait(driver, tab_name, prev_names, cancel_event=cancel_event):
                tier_names = extract_augment_names_fast(driver, cancel_event=cancel_event)
                print(f"   > 「{internal_tier}」共 {len(tier_names)} 个")
                for idx, name in enumerate(tier_names, 1):
                    if name not in tier_data:
                        tier_data[name] = {"tier": internal_tier, "t_rank": idx}
                prev_names = tier_names  # 更新对比基准

        # 3. 合并数据
        valid_augments = []
        seen = set()

        for name in all_names:
            if name in seen:
                continue
            seen.add(name)
            info = tier_data.get(name, {"tier": "未知", "t_rank": 999})
            o_rank = overall_rank_map.get(name, 999)
            valid_augments.append({
                "name": name,
                "tier": info["tier"],
                "overall_rank": o_rank,
                "t_rank": info["t_rank"]
            })

        # 补充只出现在等级Tab但不在「全部」中的海克斯
        for name, info in tier_data.items():
            if name not in seen:
                seen.add(name)
                valid_augments.append({
                    "name": name,
                    "tier": info["tier"],
                    "overall_rank": 999,
                    "t_rank": info["t_rank"]
                })

        status_code = "clean" if valid_augments else "empty"
        return valid_augments, status_code

    except UpdateCancelled:
        raise
    except Exception as e:
        print(f"[{cn_name}] 异常: {e}")
        return [], "error"

# ==========================================
# 批量抓取入口
# ==========================================
def crawl_champions(target_list, early_stop_func=None, cancel_event=None):
    """
    直接返回内存字典，不再写临时文件
    early_stop_func: 接收 (cn_name, crawled_data) 返回 bool，若返回 True 则提前终止抓取
    """
    print(f"--- 开始抓取 {len(target_list)} 个英雄 ---")

    check_cancelled(cancel_event)
    driver = setup_driver()
    failed_list = []
    success_data = {}
    early_stopped = False

    MAX_RETRIES = 3

    try:
        total = len(target_list)
        for i, (cn_name, en_name) in enumerate(target_list, 1):
            check_cancelled(cancel_event)
            print(f"--- 进度 [{i}/{total}] : {cn_name} ---")

            # 定期重启浏览器释放内存（间隔从 15 提升到 30）
            if i > 1 and i % 30 == 0:
                print("   > [系统] 定期重启浏览器释放资源...")
                try: driver.quit()
                except: pass
                driver = setup_driver()

            is_first = (i == 1)

            for attempt in range(1, MAX_RETRIES + 1):
                check_cancelled(cancel_event)
                data, status = scrape_single_champion(driver, cn_name, en_name, is_first_page=is_first, cancel_event=cancel_event)
                check_cancelled(cancel_event)
                is_first = False  # 弹窗只需第一次处理

                if status == "clean" and data:
                    success_data[cn_name] = data
                    print(f"   > 成功抓取 {len(data)} 条")
                    if early_stop_func and early_stop_func(cn_name, data):
                        early_stopped = True
                    break
                else:
                    print(f"   > 数据为空 (状态: {status})，重试 ({attempt}/{MAX_RETRIES})")
                    try:
                        _ = driver.title
                    except Exception:
                        print(f"   > 浏览器连接断开，重启中...")
                        try: driver.quit()
                        except: pass
                        driver = setup_driver()

                if attempt < MAX_RETRIES:
                    wait_or_cancel(1, cancel_event)
                else:
                    print(f"   > ❌ {cn_name} 失败")
                    failed_list.append(cn_name)

            if early_stopped:
                print(f"   > ⚠️ 触发提前结束条件，停止后续抽样。")
                break

            # 英雄间短暂间隔，避免被反爬
            wait_or_cancel(random.uniform(0.3, 0.8), cancel_event)

    finally:
        driver.quit()
        print(f"--- 爬取阶段结束 ---")

    return success_data, failed_list

if __name__ == "__main__":
    import sys
    t0 = time.time()
    res, fail = crawl_champions([("复仇焰魂", "Brand")])
    elapsed = time.time() - t0
    print(f"\n--- 耗时: {elapsed:.1f}s ---")
    print(f"结果: {res}")
    if fail:
        print(f"失败: {fail}")
