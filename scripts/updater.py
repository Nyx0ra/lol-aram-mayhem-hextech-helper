# --- START OF FILE updater.py ---

import json
import csv
import os
import requests
import sys
import re
import random
import shutil
import subprocess
import time
import glob
from pypinyin import lazy_pinyin 

# 1. 解决同级导入问题 (兼容直接运行和包导入)
try:
    from scripts import hero_scraper as crawler
    from scripts.config import BASE_DIR, DATA_DIR, CHAMPION_ID_FILE, PINYIN_FILE, CSV_FILE
except ImportError:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(current_dir)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)
    if current_dir not in sys.path:
        sys.path.insert(0, current_dir)
    import hero_scraper as crawler
    from config import BASE_DIR, DATA_DIR, CHAMPION_ID_FILE, PINYIN_FILE, CSV_FILE

# GitHub 仓库地址 (用于在线下载)
GITHUB_RAW_BASE    = "https://raw.githubusercontent.com/Nyx0ra/lol-aram-mayhem-hextech-helper/main"
AUTHOR_GITHUB_USER = "Nyx0ra"
AUTHOR_REPO_NAME   = "lol-aram-mayhem-hextech-helper"
AUTHOR_LOCAL_REPO  = r"D:\workspace\1_Projects\Personal\LOL_Tools\lol-aram-mayhem-hextech-helper"

CSV_HEADER       =["中文名", "英文名", "等级", "总排名", "等级内序号", "海克斯名称"]


# ================= 0. 作者环境检测与 GitHub 自动同步 =================
def _find_git_executable():
    """查找系统中的 git 可执行文件"""
    git_exe = shutil.which("git")
    if git_exe:
        return git_exe
    for candidate in [
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files\Git\bin\git.exe",
        r"C:\Program Files (x86)\Git\cmd\git.exe",
    ]:
        if os.path.isfile(candidate):
            return candidate
    return None


def _run_git_cmd(args, cwd, timeout=60):
    """在指定目录静默执行 git 命令 (不弹出黑框、不阻塞交互提示)"""
    git_exe = _find_git_executable()
    if not git_exe:
        return -1, "", "git executable not found"

    safe_dir = os.path.abspath(cwd).replace("\\", "/")
    cmd = [git_exe, "-c", f"safe.directory={safe_dir}"] + list(args)

    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
    try:
        res = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            creationflags=creationflags,
        )
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return -1, "", str(e)


def _find_author_repo_root():
    """
    定位本地 Git 仓库根目录。
    支持从源码目录运行，或从 dist/build_xxx/ARAMHelper/ARAMHelper.exe (桌面快捷方式) 运行。
    """
    candidates = []
    # 1. 从 BASE_DIR 向上回溯最多 6 层 (dist/build_xxx/ARAMHelper -> repo_root 为向上 3 层)
    curr = os.path.abspath(BASE_DIR)
    for _ in range(6):
        candidates.append(curr)
        parent = os.path.dirname(curr)
        if parent == curr:
            break
        curr = parent

    # 2. 源码目录本身
    script_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if script_repo not in candidates:
        candidates.append(script_repo)

    # 3. 作者本机固定工作区路径兜底
    if AUTHOR_LOCAL_REPO not in candidates:
        candidates.append(AUTHOR_LOCAL_REPO)

    for path in candidates:
        if os.path.isdir(path) and os.path.exists(os.path.join(path, ".git")):
            return path
    return None


def is_author_environment():
    """
    检查当前运行环境是否为作者本人 (Nyx0ra) 的受权仓库环境。
    非作者环境 (其他用户下载 Release 或普通 clone) 返回 False，使自动上传功能对其他用户完全无感。
    """
    repo_root = _find_author_repo_root()
    if not repo_root:
        return False

    if not _find_git_executable():
        return False

    # 校验 git config user.name 是否为作者本人
    code, user_name, _ = _run_git_cmd(["config", "user.name"], cwd=repo_root, timeout=10)
    if code != 0 or user_name.lower() != AUTHOR_GITHUB_USER.lower():
        return False

    # 校验远程 origin 是否指向目标仓库
    code, remote_url, _ = _run_git_cmd(["remote", "get-url", "origin"], cwd=repo_root, timeout=10)
    expected_slug = f"{AUTHOR_GITHUB_USER}/{AUTHOR_REPO_NAME}".lower()
    if code != 0 or expected_slug not in remote_url.lower():
        return False

    return True


def sync_data_to_github(log_func=None, quiet_if_unchanged=False):
    """
    在作者本机环境下，自动将最新更新的数据文件同步至本地仓库 data/ 目录并提交推送到 GitHub。
    对于非作者环境直接静默返回 False，不影响其他用户使用。
    """
    if not is_author_environment():
        return False

    _log = log_func or print
    repo_root = _find_author_repo_root()
    if not repo_root:
        return False

    repo_data_dir = os.path.join(repo_root, "data")
    data_filenames = ["champions.json", "pinyin_map.json", "hero_augments.csv"]

    try:
        # 1. 如果当前运行在打包目录 (如 dist/build_xxx/ARAMHelper)，先将最新数据同步回仓库根目录 data/
        abs_data_dir = os.path.normcase(os.path.abspath(DATA_DIR))
        abs_repo_data = os.path.normcase(os.path.abspath(repo_data_dir))
        if abs_data_dir != abs_repo_data:
            os.makedirs(repo_data_dir, exist_ok=True)
            for fname in data_filenames:
                src = os.path.join(DATA_DIR, fname)
                dst = os.path.join(repo_data_dir, fname)
                if os.path.exists(src):
                    shutil.copy2(src, dst)
        else:
            # 如果从源码目录运行，也顺便同步给 dist 下现有的打包目录，保持两边数据一致
            for dist_data in glob.glob(os.path.join(repo_root, "dist", "build_*", "ARAMHelper", "data")):
                if os.path.isdir(dist_data):
                    for fname in data_filenames:
                        src = os.path.join(repo_data_dir, fname)
                        dst = os.path.join(dist_data, fname)
                        if os.path.exists(src):
                            try:
                                shutil.copy2(src, dst)
                            except Exception:
                                pass

        # 2. 检查仓库 data/ 目录下的数据文件是否有变更
        rel_files = [f"data/{fname}" for fname in data_filenames]
        code, status_out, err = _run_git_cmd(["status", "--porcelain", "--"] + rel_files, cwd=repo_root)
        if code != 0:
            _log(f"⚠ Git 状态检查失败: {err}")
            return False

        committed_new = False
        if status_out:
            _log("🚀 正在将最新数据自动同步至 GitHub 仓库...")
            code, _, err = _run_git_cmd(["add", "--"] + rel_files, cwd=repo_root)
            if code != 0:
                _log(f"❌ Git 暂存数据文件失败: {err}")
                return False

            # 再次确认暂存区是否存在实质内容变更 (排除仅时间戳变动)
            code, staged_files, _ = _run_git_cmd(["diff", "--cached", "--name-only", "--"] + rel_files, cwd=repo_root)
            if staged_files:
                if "champions.json" in staged_files:
                    commit_msg = "update: 英雄和海克斯的更新"
                else:
                    commit_msg = "Update hero_augments.csv"

                code, commit_out, err = _run_git_cmd(["commit", "-m", commit_msg, "--"] + rel_files, cwd=repo_root)
                if code != 0:
                    _log(f"❌ Git 提交数据失败: {err or commit_out}")
                    return False
                committed_new = True
                _log(f"✅ 已生成数据提交: {commit_msg}")

        # 3. 检查是否有需要推送到远程 origin/main 的提交
        code, ahead_str, _ = _run_git_cmd(["rev-list", "--count", "origin/main..HEAD"], cwd=repo_root)
        ahead_count = int(ahead_str) if (code == 0 and ahead_str.isdigit()) else 0

        if not committed_new and ahead_count == 0:
            if not quiet_if_unchanged:
                _log("ℹ️ GitHub 仓库数据已是最新，无需重复推送")
            return True

        if not committed_new and ahead_count > 0:
            _log("🚀 检测到本地有未推送的数据更新，正在同步至 GitHub 仓库...")

        last_err = ""
        for attempt in range(1, 4):
            code, _, err = _run_git_cmd(["push", "origin", "main"], cwd=repo_root, timeout=90)
            if code == 0:
                _log("✅ 最新数据已成功同步更新至 GitHub 仓库!")
                return True
            last_err = err
            if any(k in err.lower() for k in ("non-fast-forward", "fetch first", "rejected")):
                _log("🔄 检测到远程仓库有新提交，正在自动拉取合并后重试...")
                _run_git_cmd(["pull", "--rebase", "--autostash", "origin", "main"], cwd=repo_root, timeout=60)
            elif attempt < 3:
                _log(f"⚠ 推送 GitHub 失败，正在重试 ({attempt}/3)...")
                time.sleep(2)

        _log(f"❌ 推送至 GitHub 仓库失败: {last_err}")
        return False

    except Exception as e:
        _log(f"❌ 同步至 GitHub 仓库异常: {e}")
        return False

# ================= 1. 数据真理同步 =================
def sync_official_data():
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
            if en_id.startswith('Jade_'):
                continue
            cn_name = info['name']
            official_en_to_cn[en_id] = cn_name
            official_cn_to_en[cn_name] = en_id

        old_en_to_cn = {}
        if os.path.exists(CHAMPION_ID_FILE):
            with open(CHAMPION_ID_FILE, 'r', encoding='utf-8') as f:
                old_cn_to_en = json.load(f)
                old_en_to_cn = {en: cn for cn, en in old_cn_to_en.items()}

        with open(CHAMPION_ID_FILE, 'w', encoding='utf-8') as f:
            json.dump(official_cn_to_en, f, indent=4, ensure_ascii=False)
        
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
    print(">>> [3/4] 读取本地历史数据 (数据保护)...")
    history = {}
    if not os.path.exists(CSV_FILE):
        return history

    try:
        with open(CSV_FILE, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            is_old_format = reader.fieldnames and "序号" in reader.fieldnames and "等级" not in reader.fieldnames
            has_overall_rank = reader.fieldnames and "总排名" in reader.fieldnames
            
            for row in reader:
                en_name = row.get('英文名')
                if en_name:
                    if en_name not in history:
                        history[en_name] = []
                    
                    if is_old_format:
                        adapted_row = {
                            "中文名": row.get("中文名", ""),
                            "英文名": en_name,
                            "等级": "未知",
                            "总排名": 999,
                            "等级内序号": 999,
                            "海克斯名称": row.get("海克斯名称", "")
                        }
                        history[en_name].append(adapted_row)
                    elif not has_overall_rank:
                        # 旧新格式：有等级但无总排名
                        adapted_row = {
                            "中文名": row.get("中文名", ""),
                            "英文名": en_name,
                            "等级": row.get("等级", "未知"),
                            "总排名": 999,
                            "等级内序号": row.get("等级内序号", 999),
                            "海克斯名称": row.get("海克斯名称", "")
                        }
                        history[en_name].append(adapted_row)
                    else:
                        history[en_name].append(row)
        print(f"    已加载 {len(history)} 个英雄的历史数据。")
    except Exception as e:
        print(f"⚠️ 读取历史CSV时出错 (可能是空文件): {e}")
    
    return history

# ================= 4. 合并与保存 =================
def merge_and_save(official_en_to_cn, history_data, new_crawl_data):
    print("\n>>> [4/4] 执行数据合并与持久化...")
    final_rows = []
    missing_data_champions =[]

    official_cn_to_en = {cn: en for en, cn in official_en_to_cn.items()}
    crawl_by_en = {official_cn_to_en.get(cn, cn): data for cn, data in new_crawl_data.items()}

    for en_name, cn_name in official_en_to_cn.items():
        rows_to_write =[]

        if en_name in crawl_by_en:
            for item in crawl_by_en[en_name]:
                rows_to_write.append({
                    "中文名": cn_name,
                    "英文名": en_name,
                    "等级": item['tier'],
                    "总排名": item['overall_rank'],
                    "等级内序号": item['t_rank'],
                    "海克斯名称": item['name']
                })
        elif en_name in history_data:
            # 深拷贝避免修改原始 history_data
            rows_to_write = [dict(row) for row in history_data[en_name]]
            for row in rows_to_write:
                row['中文名'] = cn_name
        else:
            missing_data_champions.append(cn_name)
        
        if rows_to_write:
            final_rows.extend(rows_to_write)

    try:
        with open(CSV_FILE, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
            writer.writeheader()
            writer.writerows(final_rows)
        print(f"✅ 写入完成！主文件: {CSV_FILE} (共 {len(final_rows)} 条数据)")
    except Exception as e:
        print(f"❌ 写入主文件失败: {e}")
        return False
        
    if missing_data_champions:
        print(f"\n⚠️ 注意: 有 {len(missing_data_champions)} 个英雄完全没有任何数据: {', '.join(missing_data_champions)}")
    return True

# ================= 5. 抽样比对检查 =================
def compare_hero_data(history_rows, crawled_items):
    """比对单个英雄的本地历史数据与线上爬取数据，返回是否有差异"""
    # 将历史数据转为可比较的集合
    local_set = set()
    for row in history_rows:
        key = (row.get('海克斯名称', ''), row.get('等级', ''), str(row.get('总排名', '')), str(row.get('等级内序号', '')))
        local_set.add(key)
    
    # 将爬取数据转为可比较的集合
    remote_set = set()
    for item in crawled_items:
        key = (item['name'], item['tier'], str(item['overall_rank']), str(item['t_rank']))
        remote_set.add(key)
    
    return local_set != remote_set

def spot_check_and_update(official_en_to_cn, history_data, sample_size=3):
    """随机抽取英雄进行抽样比对，一旦发现有差异立即触发全量更新"""
    all_en_names = list(official_en_to_cn.keys())
    # 优先从有历史数据的英雄中抽样，这样比对才有意义
    candidates = [en for en in all_en_names if en in history_data]
    if len(candidates) < sample_size:
        candidates = all_en_names
    
    sampled = random.sample(candidates, min(sample_size, len(candidates)))
    sample_list = [(official_en_to_cn[en], en) for en in sampled]
    
    print(f"\n>>> 🎲 抽样比对: 随机选取 {len(sample_list)} 个英雄进行线上数据校验...")
    print(f"    抽中: {', '.join([cn for cn, _ in sample_list])}")
    
    official_cn_to_en = {cn: en for en, cn in official_en_to_cn.items()}
    has_diff = False

    def check_diff_callback(cn_name, crawled_items):
        nonlocal has_diff
        en_name = official_cn_to_en.get(cn_name, cn_name)
        local_rows = history_data.get(en_name, [])
        
        if not local_rows:
            print(f"    ⚡ [{cn_name}] 本地无数据 → 存在差异")
            has_diff = True
            return True  # 触发提前结束
            
        if compare_hero_data(local_rows, crawled_items):
            print(f"    ⚡ [{cn_name}] 数据有变动 → 存在差异")
            has_diff = True
            return True  # 触发提前结束
        else:
            print(f"    ✅ [{cn_name}] 数据一致")
            return False

    sample_data, failed = crawler.crawl_champions(sample_list, early_stop_func=check_diff_callback)
    
    if failed:
        print(f"\n⚠️ 抽样爬取失败的英雄: {failed}，跳过失败英雄继续比对。")
        # 不丢弃已成功的数据，只跳过失败的
    
    return has_diff, sample_data

# ================= 主程序 (命令行入口) =================
def main():
    print("=== ARAM 数据自动维护管理器 v8.1 ===\n")

    # 1. 自动执行基础设施同步（每次必执行，速度很快）
    official_en_to_cn, official_cn_to_en, new_champs, renamed_champs = sync_official_data()
    if not official_en_to_cn:
        return

    update_pinyin_file(official_cn_to_en)

    # 2. 核心菜单选择
    print("\n请选择要执行的任务:")
    print("   [1] 英雄数据：智能增量 (自动爬取: 全新英雄 + 改名英雄 + 本地无数据的英雄)")
    print("   [2] 英雄数据：全量更新 (强制重新爬取所有英雄，耗时较长)")
    print("   [3] 英雄数据：极速补漏 (仅爬取本地无数据的英雄)")
    print("   [4] 英雄数据：精确打击 (手动输入指定英雄名称进行更新)")
    print("   [5] 英雄数据：抽样校验 (随机抽取3个英雄比对，有差异则自动全量更新)")
    
    choice = input("\n请输入选项 (默认1): ").strip()
    if not choice:
        choice = '1'

    official_data = (official_en_to_cn, official_cn_to_en, new_champs, renamed_champs)

    if choice == '4':
        # 精确打击：手动输入英雄名
        user_input = input("请输入要更新的英雄名、拼音缩写或英文ID (多个用逗号或空格分隔): ").strip()
        names = [n.strip() for n in re.split(r'[,，\s]+', user_input) if n.strip()]
        if names:
            update_specific_heroes(names)
        else:
            print("未输入有效英雄名")
    else:
        mode_map = {'1': 'smart', '2': 'full', '3': 'patch', '5': 'spot_check'}
        mode = mode_map.get(choice, 'smart')
        run_update(mode=mode, official_data=official_data)

    print("\n✅ 任务结束。")


# ================= GUI API 接口 =================

def run_update(mode='smart', log_func=None, official_data=None):
    """
    供 GUI 和 CLI 调用的统一更新接口。
    
    Args:
        mode: 'smart' | 'full' | 'patch' | 'spot_check'
        log_func: 日志回调函数 log_func(message: str)
        official_data: (英文到中文, 中文到英文, 新英雄, 改名英雄) 元组，
                       如已提前同步可传入避免重复请求
    
    Returns:
        bool: 是否成功
    """
    _log = log_func or print
    
    try:
        # 1. 同步官方数据 (或使用已有数据)
        if official_data:
            official_en_to_cn, official_cn_to_en, new_champs, renamed_champs = official_data
        else:
            _log("正在同步官方英雄数据...")
            official_en_to_cn, official_cn_to_en, new_champs, renamed_champs = sync_official_data()
            if not official_en_to_cn:
                _log("❌ 官方数据同步失败")
                return False
            _log(f"✅ 同步完成: {len(official_en_to_cn)} 个英雄")
            update_pinyin_file(official_cn_to_en)
            _log("✅ 拼音文件已更新")
        
        # 2. 加载历史数据
        history_data = load_csv_history()
        missing_champs = [en for en in official_en_to_cn if en not in history_data]
        target_list = []
        new_crawl_data = {}
        
        # 3. 根据模式构建目标列表
        if mode == 'full':
            _log("模式: 全量更新")
            target_list = [(cn, en) for en, cn in official_en_to_cn.items()]
        
        elif mode == 'spot_check':
            _log("模式: 抽样校验")
            has_diff, sample_data = spot_check_and_update(official_en_to_cn, history_data)
            if not sample_data and official_en_to_cn:
                _log("❌ 抽样校验失败: 未能成功抓取任何抽样英雄数据，请检查网络")
                return False
            if has_diff:
                _log("🔄 检测到数据差异，触发全量更新...")
                new_crawl_data.update(sample_data)
                target_list = [(cn, en) for en, cn in official_en_to_cn.items() if cn not in sample_data]
            elif new_champs or renamed_champs or missing_champs:
                _log("🔄 抽样英雄一致，但检测到新增/改名/缺失英雄，触发增量补全...")
                new_crawl_data.update(sample_data)
                targets = set(new_champs + renamed_champs + missing_champs)
                target_list = [(official_en_to_cn[en], en) for en in targets if official_en_to_cn[en] not in sample_data]
            else:
                _log("✅ 抽样数据与本地一致，无需更新")
                sync_data_to_github(log_func=_log, quiet_if_unchanged=True)
                return True
        
        elif mode == 'patch':
            _log("模式: 极速补漏")
            target_list = [(official_en_to_cn[en], en) for en in missing_champs]
            if not target_list:
                _log("✅ 本地无缺失数据的英雄，无需补漏")
                sync_data_to_github(log_func=_log, quiet_if_unchanged=True)
                return True
        
        else:  # smart
            _log("模式: 智能增量")
            targets = set(new_champs + renamed_champs + missing_champs)
            target_list = [(official_en_to_cn[en], en) for en in targets]
            if not target_list:
                _log("✅ 未发现新英雄、改名英雄或缺失数据，无需更新")
                sync_data_to_github(log_func=_log, quiet_if_unchanged=True)
                return True
        
        # 4. 执行爬取
        if target_list:
            _log(f"准备爬取 {len(target_list)} 个英雄...")
            crawled_data, failed_list = crawler.crawl_champions(target_list)
            new_crawl_data.update(crawled_data)
            if failed_list:
                _log(f"⚠ 爬取失败的英雄: {', '.join(failed_list)}")
            if not new_crawl_data:
                _log("❌ 所有目标英雄爬取均失败，未更新数据")
                return False
        elif not new_crawl_data:
            _log("无需爬取")
        
        # 5. 合并保存
        if not merge_and_save(official_en_to_cn, history_data, new_crawl_data):
            return False
        _log("✅ 数据合并保存完成")
        sync_data_to_github(log_func=_log, quiet_if_unchanged=False)
        return True
        
    except Exception as e:
        _log(f"❌ 更新失败: {e}")
        return False


def download_from_github(log_func=None):
    """
    从 GitHub 仓库下载最新数据文件。
    
    Args:
        log_func: 日志回调函数
    
    Returns:
        bool: 是否全部成功
    """
    _log = log_func or print
    
    files_to_download = [
        ("data/hero_augments.csv", CSV_FILE, "英雄海克斯数据"),
        ("data/champions.json", CHAMPION_ID_FILE, "英雄名称映射"),
        ("data/pinyin_map.json", PINYIN_FILE, "拼音检索索引"),
    ]
    
    success_count = 0
    total = len(files_to_download)
    
    for idx, (remote_path, local_path, desc) in enumerate(files_to_download, 1):
        url = f"{GITHUB_RAW_BASE}/{remote_path}"
        _log(f"下载中 [{idx}/{total}]: {desc}...")
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code == 200:
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                with open(local_path, 'wb') as f:
                    f.write(resp.content)
                _log(f"✅ {desc} 下载成功 ({len(resp.content)} bytes)")
                success_count += 1
            else:
                _log(f"❌ {desc} 下载失败 (HTTP {resp.status_code})")
        except Exception as e:
            _log(f"❌ {desc} 下载异常: {e}")
    
    _log(f"下载完成: {success_count}/{total} 成功")
    return success_count == total


def update_specific_heroes(hero_names, log_func=None):
    """
    精确更新指定英雄的数据。
    
    Args:
        hero_names: 英雄名称列表 (中文或英文)
        log_func: 日志回调函数
    
    Returns:
        bool: 是否成功
    """
    _log = log_func or print
    
    try:
        _log("正在同步官方英雄数据...")
        official_en_to_cn, official_cn_to_en, _, _ = sync_official_data()
        if not official_en_to_cn:
            _log("❌ 官方数据同步失败")
            return False
        
        # 解析英雄名 (支持中文和英文)
        target_list = []
        for name in hero_names:
            name = name.strip()
            if name in official_cn_to_en:
                # 中文名
                en = official_cn_to_en[name]
                target_list.append((name, en))
                _log(f"  ✓ {name} ({en})")
            elif name in official_en_to_cn:
                # 英文名
                cn = official_en_to_cn[name]
                target_list.append((cn, name))
                _log(f"  ✓ {cn} ({name})")
            else:
                # 模糊匹配
                try:
                    from thefuzz import process
                    result_cn = process.extractOne(name, list(official_cn_to_en.keys()))
                    result_en = process.extractOne(name, list(official_en_to_cn.keys()))
                    candidates = [r for r in [result_cn, result_en] if r]
                    best = max(candidates, key=lambda x: x[1]) if candidates else None
                    if best and best[1] > 60:
                        matched = best[0]
                        if matched in official_cn_to_en:
                            en = official_cn_to_en[matched]
                            target_list.append((matched, en))
                            _log(f"  ✓ {matched} ({en}) [模糊匹配 '{name}']")
                        else:
                            cn = official_en_to_cn[matched]
                            target_list.append((cn, matched))
                            _log(f"  ✓ {cn} ({matched}) [模糊匹配 '{name}']")
                    else:
                        _log(f"  ✗ 未找到: {name}")
                except ImportError:
                    _log(f"  ✗ 未找到: {name} (提示: 安装 thefuzz 可启用模糊匹配)")
        
        if not target_list:
            _log("❌ 没有有效的英雄")
            return False
        
        _log(f"准备爬取 {len(target_list)} 个英雄...")
        history_data = load_csv_history()
        new_crawl_data, failed_list = crawler.crawl_champions(target_list)
        
        if failed_list:
            _log(f"⚠ 爬取失败: {', '.join(failed_list)}")
        
        if not new_crawl_data:
            _log("❌ 指定英雄爬取失败，未更新数据")
            return False

        if not merge_and_save(official_en_to_cn, history_data, new_crawl_data):
            return False
        _log("✅ 精确更新完成")
        sync_data_to_github(log_func=_log, quiet_if_unchanged=False)
        return True
        
    except Exception as e:
        _log(f"❌ 更新失败: {e}")
        return False


if __name__ == "__main__":
    main()