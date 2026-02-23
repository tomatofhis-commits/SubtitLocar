import os
import sys
import subprocess

try:
    import av
    import ctranslate2
    import faster_whisper
except ImportError:
    print("Error: 'av', 'ctranslate2' or 'faster_whisper' package is not installed in this environment.")
    sys.exit(1)

# PyAV、CTranslate2、faster_whisperのインストールディレクトリを動的に取得
av_dir = os.path.dirname(av.__file__)
ct2_dir = os.path.dirname(ctranslate2.__file__)
fw_assets_dir = os.path.join(os.path.dirname(faster_whisper.__file__), "assets")

# torch/lib ディレクトリおよび nvidia/*/bin ディレクトリを確実に見つける
import site
import sys
import glob

# コピー元のディレクトリ候補リスト
dll_source_dirs = []

site_pkgs = site.getsitepackages()
if hasattr(site, 'getusersitepackages'):
    site_pkgs.append(site.getusersitepackages())
    
# global venv fallback
site_pkgs.append(os.path.join(sys.base_prefix, "Lib", "site-packages"))

for sp in site_pkgs:
    for sub_path in [
        os.path.join("nvidia", "cublas", "bin"),
        os.path.join("nvidia", "cudnn", "bin"),
        os.path.join("nvidia", "nvrtc", "bin"),
        os.path.join("nvidia", "cufft", "bin"),
        os.path.join("nvidia", "curand", "bin"),
        os.path.join("nvidia", "cusparse", "bin"),
        os.path.join("torch", "lib"),
    ]:
        candidate_dir = os.path.join(sp, sub_path)
        if os.path.exists(candidate_dir) and candidate_dir not in dll_source_dirs:
            dll_source_dirs.append(candidate_dir)
            print(f"Found CUDA DLL dir: {candidate_dir}")

if not dll_source_dirs:
    print("Warning: Could not find 'torch/lib' or 'nvidia/*/bin' directory! STT Engine might fail to use CUDA/GPU.")

nuitka_cmd = [
    sys.executable, "-m", "nuitka",
    "--standalone",
    "--windows-console-mode=disable",
    "--windows-icon-from-ico=subtitlocar.ico",
    "--assume-yes-for-downloads",
    "--plugin-enable=tk-inter",
    "--include-data-dir=frontend=frontend",
    "--include-data-file=config.yaml=config.yaml",
    "--include-data-file=subtitlocar.ico=subtitlocar.ico",
    "--include-package=webrtcvad",
    "--include-package=sounddevice",
    "--include-package=faster_whisper",
    "--include-package=ctranslate2",
    "--include-package=websockets",
    "--include-package=httpx",
    "--include-package=yaml",
    "--include-package=rich",
    "--nofollow-import-to=torch",
    # 諸悪の根源である av をNuitkaのコンパイル(C言語化)対象から完全に除外する
    "--nofollow-import-to=av",
    # その代わり、実行時に必要な機能として av 等をそのままコピーしてExeに同封する
    f"--include-data-dir={av_dir}=av",
    f"--include-data-dir={ct2_dir}=ctranslate2",
    # faster-whisper起動に必要なVAD関連の .onnx モデルファイルも同梱する
    f"--include-data-dir={fw_assets_dir}=faster_whisper/assets",
]

nuitka_cmd.extend([
    "--output-dir=dist_folder",
    "src/main.py"
])

print("====================================")
print("Starting Nuitka Safe Build Process")
print("Target PyAV Directory:", av_dir)
print("Command:", " ".join(nuitka_cmd))
print("====================================")

# Nuitkaコマンドを実行
try:
    subprocess.run(nuitka_cmd, check=True)
except subprocess.CalledProcessError:
    print("Nuitka build failed.")
    sys.exit(1)

# --- 巨悪の回避パターン ---
# Nuitkaの --include-data-dir は巨大な .lib をコピーする反面、.dll を除外してしまうため、
# ビルド完了後にスクリプトで CUDA の DLL 群だけを exe 直下へ直接コピーします。
if dll_source_dirs:
    import shutil
    import glob
    target_dist_dir = os.path.join("dist_folder", "main.dist")
    print(f"\n====================================")
    print(f"Copying CUDA & NVIDIA DLLs directly to Executable Directory...")
    copied_dlls = set()
    for s_dir in dll_source_dirs:
        for dll_file in glob.glob(os.path.join(s_dir, "*.dll")):
            dll_name = os.path.basename(dll_file).lower()
            if dll_name not in copied_dlls:
                try:
                    shutil.copy(dll_file, target_dist_dir)
                    print(f" [+] Copied (First-Match): {os.path.basename(dll_file)}")
                    copied_dlls.add(dll_name)
                except Exception as e:
                    print(f" [-] Failed to copy {os.path.basename(dll_file)}: {e}")
            else:
                print(f" [~] Skipped (Already copied): {os.path.basename(dll_file)}")
    print(f"====================================\n")
