"""
ARAM 海克斯助手 - 一键打包脚本
运行: python build.py
输出: dist/build_<timestamp>/ARAMHelper/ARAMHelper.exe
"""
import subprocess
import shutil
import os
import sys
import time
import glob
import re
import stat
from pathlib import Path


APP_NAME = "ARAMHelper"
ENTRY_POINT = "gui_launcher.py"
ICON_PATH = os.path.join("assets", "icon.ico")
DIST_DIR = os.path.join("dist", APP_NAME)


def check_dependencies():
    """检查打包依赖"""
    try:
        import PyInstaller
        print(f"✅ PyInstaller {PyInstaller.__version__}")
    except ImportError:
        print("❌ PyInstaller 未安装。运行: pip install pyinstaller")
        return False

    if not os.path.exists(ENTRY_POINT):
        print(f"❌ 入口文件不存在: {ENTRY_POINT}")
        return False

    if not os.path.exists(ICON_PATH):
        print(f"⚠ 图标文件不存在: {ICON_PATH}，将使用默认图标")

    return True


def build():
    """执行 PyInstaller 打包"""
    global DIST_DIR
    print(f"\n{'='*50}")
    print(f"  打包 {APP_NAME}")
    print(f"  入口: {ENTRY_POINT}")
    print(f"  模式: --onedir (单目录)")
    print(f"{'='*50}\n")

    # 排除不需要的大型包 (Anaconda 环境中容易被拖进来)
    EXCLUDES = [
        "torch", "torchvision", "torchaudio",
        "scipy", "pandas", "matplotlib",
        "PyQt5", "PyQt6", "PySide2", "PySide6", "qtpy",
        "IPython", "jupyter", "notebook", "nbformat", "nbconvert",
        "pytest", "black", "yapf", "jedi", "parso",
        "dask", "distributed", "numba", "llvmlite",
        "h5py", "tables", "sqlalchemy", "botocore", "boto3",
        "pyarrow", "openpyxl", "lxml",
        "docutils", "sphinx", "pygments",
        "zmq", "tornado",
        "lib2to3", "blib2to3",
        "nacl", "cloudpickle",
        "onnxruntime.transformers",      # 这个子包依赖 torch
    ]

    # 使用唯一的时间戳生成一个新的分发父目录, 彻底避开旧目录的文件锁定
    timestamp = int(time.time())
    unique_dist = os.path.join("dist", f"build_{timestamp}")
    DIST_DIR = os.path.join(unique_dist, APP_NAME)

    # 构建命令
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onedir",
        "--noconsole",
        "--uac-admin",           # 默认请求管理员权限
        "--name", APP_NAME,
        "--noconfirm",       # 不确认覆盖
        "--clean",           # 清理缓存
        "--distpath", unique_dist,
        
        # 收集 rapidocr 完整包 (含 ONNX 模型)
        "--collect-all", "rapidocr_onnxruntime",

        # numpy: 完整收集 (Anaconda 环境需要)
        "--collect-all", "numpy",

        # onnxruntime: 只收集数据文件和二进制, 不收集子模块 (避免拖入 torch)
        "--collect-data", "onnxruntime",
        "--collect-binaries", "onnxruntime",

        # 运行时 hook: 修复 numpy 冻结环境检测
        "--runtime-hook", os.path.join("runtime_hooks", "fix_numpy.py"),

        # 隐式导入
        "--hidden-import", "pystray._win32",
        "--hidden-import", "PIL._tkinter_finder",
        "--hidden-import", "thefuzz",
        "--hidden-import", "rapidfuzz",
        "--hidden-import", "onnxruntime",
        "--hidden-import", "scripts",
        "--hidden-import", "scripts.lcu_connector",
        "--hidden-import", "scripts.hero_scraper",
        "--hidden-import", "scripts.updater",
        "--hidden-import", "scripts.utils",

        ENTRY_POINT,
    ]

    # 添加排除项
    for pkg in EXCLUDES:
        cmd.insert(-1, "--exclude-module")
        cmd.insert(-1, pkg)

    # 添加图标
    if os.path.exists(ICON_PATH):
        cmd.insert(-1, "--icon")
        cmd.insert(-1, ICON_PATH)

    # (原预清理逻辑已经上移)

    print("执行命令:")
    print(" ".join(cmd))
    print()

    result = subprocess.run(cmd, cwd=os.path.dirname(os.path.abspath(__file__)) or ".")
    if result.returncode != 0:
        print(f"\n❌ PyInstaller 打包失败 (exit code: {result.returncode})")
        return False

    print("\n✅ PyInstaller 打包完成")
    return True


def copy_runtime_files():
    """复制运行时需要的数据文件到 dist 目录"""
    print("\n--- 复制运行时文件 ---")

    # 先检查旧的 dist/build_*/ARAMHelper/data 下是否有比源码目录更新的数据，避免打包时丢失更新
    src_data = "data"
    if os.path.exists(src_data):
        current_build_parent = os.path.abspath(os.path.dirname(DIST_DIR))
        for old_data_dir in glob.glob(os.path.join("dist", "build_*", APP_NAME, "data")):
            old_parent = os.path.abspath(os.path.dirname(os.path.dirname(old_data_dir)))
            if os.path.normcase(old_parent) == os.path.normcase(current_build_parent):
                continue
            for fname in ("champions.json", "pinyin_map.json", "hero_augments.csv"):
                old_file = os.path.join(old_data_dir, fname)
                repo_file = os.path.join(src_data, fname)
                if os.path.isfile(old_file):
                    if not os.path.isfile(repo_file) or os.path.getmtime(old_file) > os.path.getmtime(repo_file):
                        shutil.copy2(old_file, repo_file)
                        print(f"🔄 已从旧构建同步较新的数据文件回仓库: {fname}")

    # 复制 data/ 目录 (CSV, JSON - 用户可更新)
    dst_data = os.path.join(DIST_DIR, "data")
    if os.path.exists(src_data):
        if os.path.exists(dst_data):
            shutil.rmtree(dst_data)
        shutil.copytree(src_data, dst_data)
        print(f"✅ data/ → {dst_data}")
    else:
        print(f"⚠ data/ 不存在，跳过")

    # 复制 assets/ 目录 (图标)
    src_assets = "assets"
    dst_assets = os.path.join(DIST_DIR, "assets")
    if os.path.exists(src_assets):
        if os.path.exists(dst_assets):
            shutil.rmtree(dst_assets)
        shutil.copytree(src_assets, dst_assets)
        print(f"✅ assets/ → {dst_assets}")
    else:
        print(f"⚠ assets/ 不存在，跳过")

    print("✅ 运行时文件复制完成")


def cleanup_bloat():
    """移除不必要的大型 DLL (CUDA/TensorRT/MKL 等)"""
    import glob
    internal = os.path.join(DIST_DIR, "_internal")
    if not os.path.exists(internal):
        return

    print("\n--- 清理不必要的大型文件 ---")

    patterns = [
        # CUDA / TensorRT (不需要, 仅用 CPU 推理)
        "onnxruntime/capi/onnxruntime_providers_cuda*",
        "onnxruntime/capi/onnxruntime_providers_tensorrt*",
        "nvinfer*", "nvcuda*", "cudnn*", "cublas*",
        "cufft*", "curand*", "cusparse*", "cusolver*",
        # MKL MPI 相关 (不需要)
        "mkl_blacs_*.dll",
        "mkl_cdft_*.dll",
        "mkl_scalapack_*.dll",
        "mkl_pgi_thread*.dll",
        "mkl_tbb_thread*.dll",
        "mkl_msg.dll",
        # 不需要的 MKL 微架构变体 (保留 mkl_core, mkl_rt, mkl_def, mkl_intel_thread)
        "mkl_avx.2.dll",
        "mkl_avx2.2.dll",
        "mkl_avx512.2.dll",
        "mkl_mc.2.dll",
        "mkl_mc3.2.dll",
        "mkl_vml_avx.2.dll",
        "mkl_vml_avx2.2.dll",
        "mkl_vml_avx512.2.dll",
        "mkl_vml_mc.2.dll",
        "mkl_vml_mc3.2.dll",
        # OpenCV ffmpeg (不需要视频)
        "cv2/opencv_videoio_ffmpeg*",
        # onnxruntime training 相关
        "onnxruntime/training/**",
        # numpy 文档和 f2py
        "numpy/f2py/**",
        "numpy/doc/**",
        # selenium 不需要的浏览器驱动
        "selenium/webdriver/firefox/**",
        "selenium/webdriver/edge/**",
        "selenium/webdriver/safari/**",
    ]

    freed = 0
    for pat in patterns:
        for f in glob.glob(os.path.join(internal, pat)):
            sz = os.path.getsize(f)
            for attempt in range(5):
                try:
                    if os.path.isfile(f):
                        os.remove(f)
                    elif os.path.isdir(f):
                        shutil.rmtree(f, ignore_errors=True)
                    freed += sz
                    break
                except PermissionError:
                    time.sleep(1)
            else:
                print(f"⚠ 无法删除文件 (已被锁定): {f}")

    if freed > 0:
        print(f"✅ 已清理 {freed / 1024 / 1024:.0f} MB 不必要的文件 (CUDA/MKL/ffmpeg)")

    # 清理 numpy 内部的 setup.py (防止"source directory"误检测)
    numpy_dir = os.path.join(internal, "numpy")
    if os.path.exists(numpy_dir):
        count = 0
        for root, dirs, files in os.walk(numpy_dir):
            for f in files:
                if f in ('setup.py', 'setup.cfg'):
                    full_p = os.path.join(root, f)
                    for attempt in range(5):
                        try:
                            os.remove(full_p)
                            count += 1
                            break
                        except PermissionError:
                            time.sleep(1)
            # 也删除 tests 目录减小体积
            if 'tests' in dirs:
                full_test_dir = os.path.join(root, 'tests')
                for attempt in range(5):
                    try:
                        shutil.rmtree(full_test_dir, ignore_errors=True)
                        break
                    except Exception:
                        time.sleep(1)
                dirs.remove('tests')
        if count > 0:
            print(f"✅ 已清理 numpy 内部 {count} 个 setup 文件")


def update_desktop_shortcut():
    """如果桌面存在 ARAMHelper 快捷方式，自动将其指向最新打包出的可执行文件"""
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    shortcut_path = os.path.join(desktop, "ARAMHelper.exe - 快捷方式.lnk")
    if not os.path.exists(shortcut_path):
        return

    target_exe = os.path.abspath(os.path.join(DIST_DIR, f"{APP_NAME}.exe"))
    working_dir = os.path.abspath(DIST_DIR)

    try:
        ps_cmd = (
            f'$sh = New-Object -ComObject WScript.Shell; '
            f'$sc = $sh.CreateShortcut("{shortcut_path}"); '
            f'$sc.TargetPath = "{target_exe}"; '
            f'$sc.WorkingDirectory = "{working_dir}"; '
            f'$sc.Save()'
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            check=True,
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0,
        )
        print(f"\n✅ 已自动更新桌面快捷方式: {shortcut_path} → {target_exe}")
    except Exception as e:
        print(f"\n⚠ 更新桌面快捷方式失败: {e}")


def _is_local_directory(path):
    """拒绝符号链接和 Windows 目录联接，避免清理到 dist 之外。"""
    info = path.lstat()
    reparse = getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    return stat.S_ISDIR(info.st_mode) and not reparse and path.resolve() == path


def cleanup_old_builds():
    """删除旧的单目录产物，保留用户另外放在 dist 中的文件。"""
    dist_root = Path.cwd().resolve() / "dist"
    if not _is_local_directory(dist_root):
        print(f"⚠ 跳过旧构建清理，dist 不是本地普通目录: {dist_root}")
        return
    current_build_dir = Path(DIST_DIR).resolve().parent
    current = re.fullmatch(r"build_(\d+)", current_build_dir.name)
    if current_build_dir.parent != dist_root or current is None:
        print(f"⚠ 跳过旧构建清理，当前构建不在预期目录: {current_build_dir}")
        return
    for candidate in dist_root.iterdir():
        legacy = candidate.name == APP_NAME
        generated = re.fullmatch(r"build_(\d+)", candidate.name)
        if not legacy and (generated is None or int(generated[1]) >= int(current[1])):
            continue
        try:
            if not _is_local_directory(candidate):
                print(f"⚠ 跳过非普通构建目录: {candidate}")
                continue
            product = candidate if legacy else candidate / APP_NAME
            if legacy and not (product / f"{APP_NAME}.exe").is_file():
                continue
            if product.exists() or product.is_symlink():
                if not _is_local_directory(product):
                    print(f"⚠ 跳过非普通产物目录: {product}")
                    continue
                shutil.rmtree(product)
                print(f"🧹 已清理旧构建: {product}")
            if not legacy and not any(candidate.iterdir()):
                candidate.rmdir()
        except OSError as error:
            print(f"⚠ 旧构建未完全清理，下次构建会重试: {candidate} ({error})")


def print_summary():
    """打印打包结果摘要"""
    print(f"\n{'='*50}")
    print(f"  ✅ 打包完成!")
    print(f"{'='*50}")
    exe_path = Path(DIST_DIR).resolve() / f"{APP_NAME}.exe"
    print(f"\n  EXE: {exe_path}")
    print(f'  PowerShell 启动: & "{exe_path}"')

    # 计算目录大小
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(DIST_DIR):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            total_size += os.path.getsize(fp)
    size_mb = total_size / (1024 * 1024)
    print(f"  总大小: {size_mb:.1f} MB")


def main():
    # 修复 Windows 控制台编码
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    os.system('chcp 65001 >nul 2>&1')

    os.chdir(os.path.dirname(os.path.abspath(__file__)) or ".")

    if not check_dependencies():
        return 1

    if not build():
        return 1

    copy_runtime_files()
    cleanup_bloat()
    if not (Path(DIST_DIR) / f"{APP_NAME}.exe").is_file():
        print("❌ 构建未生成 EXE，跳过旧构建清理")
        return 1
    update_desktop_shortcut()
    cleanup_old_builds()
    print_summary()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
