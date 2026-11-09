"""Le contrôle du paquet avant publication (scripts/check_dist.py) : il voit ce qui MANQUE et ce qui FUIT, et la version."""

import importlib.util
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("check_dist", ROOT / "scripts" / "check_dist.py")
check_dist = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_dist)

GOOD = [
    "xcore_doctype/__init__.py", "xcore_doctype/plugin.py", "xcore_doctype/ui/dist/index.html", "xcore_doctype/ui/dist/assets/app-1.js",
    "xcore_doctype/ui/dist/shared/vue.js", "xcore_doctype/ui/dist/shared/sdk.js", "xcore_doctype/plugin_dir/plugin.yaml",
    "xcore_doctype/plugin_dir/src/main.py", "xcore_doctype/system_plugin_dir/src/main.py",
    *(f"xcore_doctype/platform/{n}/plugin.yaml" for n in ("xflow", "xlicense", "xpulse", "xprint")),
    *(f"xcore_doctype/ext/{n}/__init__.py" for n in ("pubsub", "xstorage", "xwebsocket", "xpay")),
]
METADATA = "Metadata-Version: 2.4\nName: xcore-doctype\nVersion: 1.2.3\nDescription-Content-Type: text/markdown\n"


def build(tmp: Path, files: list[str], metadata: str = METADATA) -> Path:
    dist = tmp / "dist"
    dist.mkdir()
    with zipfile.ZipFile(dist / "xcore_doctype-1.2.3-py3-none-any.whl", "w") as wheel:
        for name in files:
            wheel.writestr(name, "")
        wheel.writestr("xcore_doctype-1.2.3.dist-info/METADATA", metadata)
    src = tmp / "xcore_doctype-1.2.3"
    (src / "src/xcore_doctype/ui/dist").mkdir(parents=True)
    (src / "src/xcore_doctype/ui/dist/index.html").write_text("")
    with tarfile.open(dist / "xcore_doctype-1.2.3.tar.gz", "w:gz") as sdist:
        sdist.add(src, arcname=src.name)
    return dist


def test_a_complete_package_is_ready(tmp_path, capsys):
    assert check_dist.main([str(build(tmp_path, GOOD)), "--version", "v1.2.3"]) == 0
    assert "prêt à publier" in capsys.readouterr().out


def test_a_missing_desk_a_leaked_secret_and_a_wrong_version_are_all_refused(tmp_path, capsys):
    files = [f for f in GOOD if "/ui/dist/" not in f] + [".env", "conf/private.pem", "xcore_doctype/__pycache__/a.pyc"]
    assert check_dist.main([str(build(tmp_path, files)), "--version", "2.0.0"]) == 1
    err = capsys.readouterr().err
    for said in ("ui/dist/index.html", "ne doit pas contenir .env", "private.pem", "__pycache__", "version 1.2.3 au lieu de 2.0.0"):
        assert said in err


def test_a_package_without_readme_has_no_pypi_page(tmp_path, capsys):
    assert check_dist.main([str(build(tmp_path, GOOD, METADATA.replace("Description-Content-Type: text/markdown\n", "")))]) == 1
    assert "README" in capsys.readouterr().err
