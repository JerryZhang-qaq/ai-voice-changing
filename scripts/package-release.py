"""Build a reviewable source + compiled-web ZIP, with no runtime data."""
import hashlib
import json
from pathlib import Path
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {"node_modules", "__pycache__", ".pytest_cache", ".venv"}


def main():
    if not (ROOT / "apps/web/dist/index.html").is_file():
        raise SystemExit("请先执行 apps/web 中的 npm run build")
    files = []
    for folder in ("packages", "services", "scripts", "tests", "docs", "deploy", ".github", "apps/web"):
        for path in (ROOT / folder).rglob("*"):
            relative = path.relative_to(ROOT)
            if path.is_symlink() or not path.is_file() or any(part in EXCLUDED or part.endswith(".egg-info") for part in relative.parts):
                continue
            if path.suffix.lower() in {".wav", ".mp3", ".ogg", ".flac", ".pth", ".ckpt", ".pyc"}:
                raise SystemExit(f"发行路径意外包含音频/权重：{relative}")
            files.append(path)
    for name in ("README.md", "LICENSE", "THIRD_PARTY.md", "pyproject.toml", "requirements.lock", ".env.example", ".gitignore", ".dockerignore"):
        files.append(ROOT / name)
    files.extend(ROOT.glob("*-Windows.cmd"))
    manifest = {str(path.relative_to(ROOT)).replace("\\", "/"): {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size": path.stat().st_size} for path in sorted(set(files))}
    output = ROOT / "releases"
    output.mkdir(exist_ok=True)
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    target = output / f"VoiceWorkbench-{version}-Windows.zip"
    prefix = f"VoiceWorkbench-{version}/"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in manifest:
            archive.write(ROOT / relative, prefix + relative)
        archive.writestr(prefix + "RELEASE-MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix(".zip.sha256").write_text(f"{digest}  {target.name}\n")
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        assert prefix + "apps/web/dist/index.html" in archive.namelist()
        assert prefix + "packages/engines/src/voice_workbench_engines/resources.json" in archive.namelist()
        assert prefix + "Install-Windows.cmd" in archive.namelist()
    print(json.dumps({"zip":str(target),"size":target.stat().st_size,"sha256":digest,"files":len(manifest)},indent=2))


if __name__ == "__main__":
    main()
