"""Install pinned ROSVOT source and its separately downloaded weights on Windows."""
from __future__ import annotations
import hashlib
import html.parser
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

REVISION = "3c8332bf43adae35f6e4d64971862f2f6139b310"
ARCHIVE_ID = "1JNtNT37KiLq9uFQqHk7JFs-3trxd3bRh"
APPDATA = Path(os.environ.get("HEARPITCH_HOME", Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Documents" / "HearPitchLocal")).expanduser()
ROOT = APPDATA / "models" / "rosvot"
SOURCE = ROOT / "source"
VENV = ROOT / ".venv"
PYTHON = VENV / "Scripts" / "python.exe"
CHUNK = 1024 * 1024

class DriveFormParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = {}
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("name") and attrs.get("value"):
            self.fields[attrs["name"]] = attrs["value"]


def run(args, *, cwd=None):
    print("[HearPitch ROSVOT]", " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=cwd, check=True)


def download(url: str, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "HearPitch-Local/ROSVOT-installer"})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as out:
        total = int(response.headers.get("Content-Length", "0") or 0)
        done = 0
        last = 0
        while True:
            block = response.read(CHUNK)
            if not block:
                break
            out.write(block)
            done += len(block)
            now = time.monotonic()
            if now - last > 2:
                if total:
                    print(f"  {destination.name}: {done / total * 100:.1f}% ({done / 1024**2:.0f}/{total / 1024**2:.0f} MiB)", flush=True)
                else:
                    print(f"  {destination.name}: {done / 1024**2:.0f} MiB", flush=True)
                last = now
    if destination.stat().st_size < 1024:
        raise RuntimeError(f"下载内容太小，可能拿到错误页面：{destination}")
    digest = hashlib.sha256()
    with destination.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK), b""):
            digest.update(block)
    print(f"  SHA-256 {destination.name}: {digest.hexdigest()}", flush=True)


def download_drive_archive(destination: Path):
    url = f"https://drive.google.com/uc?export=download&id={ARCHIVE_ID}"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 HearPitch-Local"})
    with urllib.request.urlopen(request, timeout=60) as response:
        content_type = response.headers.get("Content-Type", "")
        if "zip" in content_type or "octet-stream" in content_type:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("wb") as out:
                shutil.copyfileobj(response, out, CHUNK)
            return
        page = response.read().decode("utf-8", "replace")
    parser = DriveFormParser()
    parser.feed(page)
    fields = parser.fields
    if not fields.get("uuid"):
        raise RuntimeError("无法读取 Google Drive 大文件确认表单；请打开 ROSVOT 官方仓库手动下载 checkpoints.zip。")
    query = urllib.parse.urlencode({"id": ARCHIVE_ID, "export": "download", "confirm": "t", "uuid": fields["uuid"]})
    final = urllib.request.Request(
        "https://drive.usercontent.google.com/download?" + query,
        headers={"User-Agent": "Mozilla/5.0 HearPitch-Local"},
    )
    with urllib.request.urlopen(final, timeout=120) as response:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as out:
            total = int(response.headers.get("Content-Length", "0") or 0)
            copied = 0
            last = 0
            while True:
                block = response.read(CHUNK)
                if not block:
                    break
                out.write(block)
                copied += len(block)
                now = time.monotonic()
                if now - last > 2:
                    if total:
                        print(f"  checkpoints.zip: {copied / total * 100:.1f}% ({copied / 1024**2:.0f}/{total / 1024**2:.0f} MiB)", flush=True)
                    else:
                        print(f"  checkpoints.zip: {copied / 1024**2:.0f} MiB", flush=True)
                    last = now
    if destination.stat().st_size < 500 * 1024 * 1024:
        raise RuntimeError("检查点压缩包小于预期的约 557 MiB，下载可能未完整。")
    digest = hashlib.sha256()
    with destination.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK), b""):
            digest.update(block)
    print(f"  SHA-256 checkpoints.zip: {digest.hexdigest()}", flush=True)


def extract_securely(archive: zipfile.ZipFile, destination: Path):
    root = destination.resolve()
    for info in archive.infolist():
        relative = Path(info.filename.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError(f"压缩包包含不安全路径：{info.filename}")
        target = (destination / relative).resolve()
        if not target.is_relative_to(root):
            raise RuntimeError(f"压缩包路径越界：{info.filename}")
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def probe_python(command: list[str]) -> tuple[str, tuple[int, int, int]] | None:
    try:
        probe = subprocess.run(
            command + ["-c", "import sys; print(sys.executable); print('.'.join(map(str, sys.version_info[:3])))"],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if probe.returncode:
        return None
    lines = [line.strip() for line in probe.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    try:
        version = tuple(int(part) for part in lines[-1].split("."))
    except ValueError:
        return None
    if version < (3, 9, 0) or version >= (3, 12, 0):
        return None
    return lines[-2], version


def select_base_python() -> tuple[str, tuple[int, int, int], str]:
    """Prefer the upstream Python 3.9, but support the common 3.10/3.11 installs."""
    launcher = shutil.which("py")
    if launcher:
        for tag in ("-3.9-64", "-3.9", "-3.10-64", "-3.10", "-3.11-64", "-3.11"):
            result = probe_python([launcher, tag])
            if result:
                path, version = result
                return path, version, f"Windows Python Launcher {tag}"
    current = probe_python([sys.executable])
    if current:
        path, version = current
        return path, version, "当前 HearPitch Python"
    raise SystemExit(
        "找不到兼容的 64 位 Python。请安装 Python 3.9、3.10 或 3.11 x64，"
        "并勾选 Python Launcher，然后重新运行本脚本。"
    )


def main():
    if os.name != "nt":
        raise SystemExit("此安装器只支持 Windows。")
    base_python, python_version, python_source = select_base_python()
    print(
        f"[HearPitch ROSVOT] 使用 {python_source}: Python {'.'.join(map(str, python_version))}（独立环境）",
        flush=True,
    )
    if VENV.exists() and not PYTHON.exists():
        raise SystemExit(f"检测到不完整的 ROSVOT 环境：{VENV}。请删除该 .venv 文件夹后重试。")
    if PYTHON.exists() and SOURCE.exists():
        required = ["checkpoints/rosvot/model.pt", "checkpoints/rosvot/config.yaml", "checkpoints/rwbd/model.pt", "checkpoints/rwbd/config.yaml", "checkpoints/rmvpe/model.pt"]
        if all((SOURCE / path).is_file() for path in required):
            print("ROSVOT 源码与模型已完整安装；跳过重量下载，只验证 CUDA。", flush=True)
            check = subprocess.run([PYTHON, "-c", "import torch; print('torch',torch.__version__); print('cuda',torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NO_CUDA')"], text=True, capture_output=True)
            print(check.stdout, check.stderr)
            if check.returncode or "True" not in check.stdout:
                raise SystemExit("CUDA 检查失败；请检查驱动或重装 ROSVOT 专用环境。")
            return
    ROOT.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix="hearpitch-rosvot-install-", dir=ROOT))
    try:
        if not PYTHON.exists():
            run([base_python, "-m", "venv", VENV])
        print("[HearPitch ROSVOT] 安装 PyTorch 2.1.1 + CUDA 11.8（Windows x64）…", flush=True)
        run([PYTHON, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"])
        run([PYTHON, "-m", "pip", "install", "torch==2.1.1+cu118", "torchaudio==2.1.1+cu118", "--index-url", "https://download.pytorch.org/whl/cu118"])
        packages = [
            "numpy<2", "scipy", "librosa==0.10.1", "tqdm", "matplotlib>=3.7,<3.9",
            "PyYAML", "pretty_midi", "pyworld==0.3.4", "pyloudnorm",
            "six", "packaging", "soundfile", "numba<0.60",
        ]
        run([PYTHON, "-m", "pip", "install", *packages])
        print("[HearPitch ROSVOT] 下载固定版本源代码…", flush=True)
        source_zip = temp / "rosvot-source.zip"
        download(f"https://github.com/RickyL-2000/ROSVOT/archive/{REVISION}.zip", source_zip)
        staging = temp / "source"
        staging.mkdir()
        with zipfile.ZipFile(source_zip) as archive:
            extract_securely(archive, staging)
            children = list(staging.iterdir())
            if len(children) == 1 and children[0].is_dir():
                unpacked = children[0]
                nested = temp / "source-root"
                unpacked.replace(nested)
                staging.rmdir()
                staging = nested
        if SOURCE.exists():
            shutil.rmtree(SOURCE)
        staging.replace(SOURCE)
        print("[HearPitch ROSVOT] 下载官方说明提供的检查点（约 557 MiB）…", flush=True)
        checkpoint_zip = temp / "checkpoints.zip"
        download_drive_archive(checkpoint_zip)
        with zipfile.ZipFile(checkpoint_zip) as archive:
            bad = archive.testzip()
            if bad:
                raise RuntimeError(f"模型 ZIP 校验失败：{bad}")
            names = [name.replace("\\", "/") for name in archive.namelist()]
            required_names = ["checkpoints/rosvot/model.pt", "checkpoints/rosvot/config.yaml", "checkpoints/rwbd/model.pt", "checkpoints/rwbd/config.yaml", "checkpoints/rmvpe/model.pt"]
            for needed in required_names:
                if needed not in names:
                    raise RuntimeError(f"检查点压缩包缺少必要文件：{needed}")
            extract_securely(archive, SOURCE)
        for relative in required_names:
            if not (SOURCE / relative).is_file():
                raise RuntimeError(f"模型解压后缺少文件：{relative}")
        license_text = (SOURCE / "LICENSE").read_text(encoding="utf-8", errors="replace")
        if not license_text.strip():
            raise RuntimeError("未找到上游 ROSVOT LICENSE 文件。")
        (ROOT / "INSTALL_MANIFEST.txt").write_text(
            f"ROSVOT revision: {REVISION}\nPython: {base_python}\nPyTorch: 2.1.1+cu118; torchaudio: 2.1.1+cu118\n"
            f"Checkpoint archive: Google Drive ID {ARCHIVE_ID}\nCheckpoint size: {checkpoint_zip.stat().st_size} bytes\n"
            "Checkpoint archive SHA-256 was printed during install. The archive is downloaded separately and is not bundled with HearPitch.\n",
            encoding="utf-8",
        )
        print("[HearPitch ROSVOT] 检查 CUDA 是否可用…", flush=True)
        check = subprocess.run([PYTHON, "-c", "import torch; print('torch',torch.__version__); print('cuda',torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NO_CUDA')"], text=True, capture_output=True)
        print(check.stdout, check.stderr)
        if check.returncode or "True" not in check.stdout:
            raise RuntimeError("PyTorch 安装后仍无法访问 CUDA。请检查 NVIDIA 驱动；保留已下载环境后可重新运行安装器。")
        print("ROSVOT model/config smoke check…", flush=True)
        smoke = subprocess.run([PYTHON, "-c", "import torch,librosa,pretty_midi,pyworld,matplotlib,yaml,soundfile; import inference.rosvot; print('inference dependencies import OK')"], cwd=SOURCE, text=True, capture_output=True)
        print(smoke.stdout, smoke.stderr)
        if smoke.returncode:
            raise RuntimeError("ROSVOT 推理依赖自检失败。")
        print("\nROSVOT + RMVPE CUDA 安装完成。请关闭此窗口并重新启动 HearPitch。", flush=True)
    finally:
        shutil.rmtree(temp, ignore_errors=True)

if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"安装命令失败，退出代码 {exc.returncode}。请查看上方错误；主 HearPitch 环境未被修改。")
    except Exception as exc:
        raise SystemExit(f"安装失败：{exc}\n请保留屏幕上的错误信息以便排查。")
