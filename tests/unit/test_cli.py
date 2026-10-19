"""`xcore-doctype install | doctor | check | path` : poser le plugin dans une instance xcore et la vérifier."""

from __future__ import annotations

from importlib import metadata

import pytest
import yaml

from xcore_doctype import __version__, plugin_dir
from xcore_doctype.cli import diagnose, install, main

GOOD_MAIN = "app = FastAPI()\nxcore.setup(app)\n"
BAD_MAIN = "app = FastAPI()\n"  # ce que génère `xcli init`


def instance(tmp_path, *, db="sqlite+aio", main_py=GOOD_MAIN, tenancy=None, strict=False, directory="./app", isolate=None):
    """Une instance xcore comme `xcli init` la génère, avec ce qu'il faut changer pour chaque cas."""
    config = {
        "app": {"plugin_prefix": "/app"},
        "plugins": {"directory": directory, "strict_trusted": strict},
        "services": {"databases": {"default": {"type": db, "url": "sqlite+aiosqlite:///./xcore.db"}}},
    }
    if tenancy is not None:
        config["tenancy"] = {"enabled": tenancy, **({"isolate_db": isolate} if isolate is not None else {})}
    (tmp_path / "integration.yaml").write_text(yaml.safe_dump(config))
    (tmp_path / "main.py").write_text(main_py)
    (tmp_path / "app").mkdir(exist_ok=True)
    return tmp_path


def levels(findings):
    return {(f.level, f.message.split(" ")[0]) for f in findings}


def errors(findings):
    return [f.message for f in findings if f.level == "error"]


def warns(findings):
    return [f.message for f in findings if f.level == "warn"]


class TestPackaging:
    def test_the_package_ships_a_complete_plugin_folder(self):
        d = plugin_dir()
        assert (d / "plugin.yaml").is_file() and (d / "src" / "main.py").is_file()
        manifest = yaml.safe_load((d / "plugin.yaml").read_text())
        assert manifest["name"] == "xcore-doctype" and manifest["entry_point"] == "src/main.py"
        assert manifest["execution_mode"] == "trusted"

    def test_the_manifest_version_follows_the_package_version(self):
        """Une version qui diverge ferait croire à `install` que le manifeste installé est périmé."""
        assert yaml.safe_load((plugin_dir() / "plugin.yaml").read_text())["version"] == metadata.version("xcore-doctype")

    def test_the_xcore_plugins_entry_point_leads_to_that_folder(self):
        (ep,) = [e for e in metadata.entry_points(group="xcore.plugins") if e.name == "xcore-doctype"]
        assert ep.load()() == plugin_dir()

    def test_the_console_script_is_declared(self):
        scripts = {e.name: e.value for e in metadata.entry_points(group="console_scripts")}
        assert scripts["xcore-doctype"] == "xcore_doctype.cli:main"

    def test_the_shipped_manifest_has_no_operator_doctypes_dir_by_default(self):
        assert yaml.safe_load((plugin_dir() / "plugin.yaml").read_text())["doctype"]["doctypes_dirs"] == []

    def test_pyproject_lists_the_built_desk_so_a_git_checkout_still_ships_it(self):
        """`dist/` est dans .gitignore : sans `include`, Poetry l'écarte d'un dépôt git (mesuré : 0 fichier du Desk dans le wheel, 24 avec)."""
        import tomllib
        from pathlib import Path

        config = tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text())
        includes = config["tool"]["poetry"]["include"]
        assert {"path": "src/xcore_doctype/ui/dist", "format": ["sdist", "wheel"]} in includes

    def test_the_built_desk_is_inside_the_package(self):
        assert (plugin_dir().parent / "ui" / "dist" / "index.html").is_file(), "pnpm build n'a pas été lancé"


class TestInstall:
    def test_it_lays_the_plugin_folder_down(self, tmp_path):
        root = instance(tmp_path, tenancy=True)
        findings = install(root / "app")
        manifest = root / "app" / "xcore-doctype" / "plugin.yaml"
        assert manifest.read_text() == (plugin_dir() / "plugin.yaml").read_text()  # tenancy active : manifeste du paquet tel quel
        assert (root / "app" / "xcore-doctype" / "src" / "main.py").read_text() == (plugin_dir() / "src" / "main.py").read_text()
        assert not errors(findings)

    def test_a_fresh_xcli_instance_gets_every_pitfall_named(self, tmp_path):
        root = instance(tmp_path, db="sqlite", main_py=BAD_MAIN)  # tenancy absente : désactivée
        findings = install(root / "app")
        text = "\n".join(errors(findings))
        assert "adaptateur SYNCHRONE" in text and "sqlite+aio" in text
        assert "xcore.setup(app)" in text and "tenant_middleware_missing" in text
        assert len(errors(findings)) == 2

    def test_a_disabled_tenancy_turns_the_binding_off_in_the_installed_manifest(self, tmp_path):
        root = instance(tmp_path, tenancy=None)
        install(root / "app")
        manifest = yaml.safe_load((root / "app" / "xcore-doctype" / "plugin.yaml").read_text())
        assert manifest["doctype"]["enforce_tenant_binding"] is False
        assert (plugin_dir() / "plugin.yaml").read_text().count("enforce_tenant_binding: true") == 1  # le paquet, lui, reste fermé

    def test_an_enabled_tenancy_keeps_the_binding(self, tmp_path):
        root = instance(tmp_path, tenancy=True)
        install(root / "app")
        assert yaml.safe_load((root / "app" / "xcore-doctype" / "plugin.yaml").read_text())["doctype"]["enforce_tenant_binding"] is True

    def test_tenancy_can_be_forced(self, tmp_path):
        root = instance(tmp_path, tenancy=True)
        install(root / "app", tenancy="off")
        assert yaml.safe_load((root / "app" / "xcore-doctype" / "plugin.yaml").read_text())["doctype"]["enforce_tenant_binding"] is False

    def test_without_a_config_it_installs_and_says_it_could_not_check(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # le repli est ./integration.yaml : on ne veut pas celui du dépôt
        (tmp_path / "app").mkdir()
        findings = install(tmp_path / "app")
        assert any("aucun integration.yaml" in m for m in warns(findings))
        assert (tmp_path / "app" / "xcore-doctype" / "plugin.yaml").is_file()
        # Sans configuration lisible, le défaut est le sûr : le tenant reste lié à l'identité.
        assert yaml.safe_load((tmp_path / "app" / "xcore-doctype" / "plugin.yaml").read_text())["doctype"]["enforce_tenant_binding"] is True

    def test_running_it_again_keeps_the_operators_settings_but_refreshes_the_shim(self, tmp_path):
        root = instance(tmp_path, tenancy=True)
        install(root / "app")
        manifest = root / "app" / "xcore-doctype" / "plugin.yaml"
        edited = manifest.read_text().replace("timezone: UTC", "timezone: Europe/Paris")
        manifest.write_text(edited)
        shim = root / "app" / "xcore-doctype" / "src" / "main.py"
        shim.write_text("# périmé\n")
        findings = install(root / "app")
        assert manifest.read_text() == edited and "conservés" in " ".join(f.message for f in findings)
        assert shim.read_text() == (plugin_dir() / "src" / "main.py").read_text()

    def test_force_rewrites_the_manifest(self, tmp_path):
        root = instance(tmp_path, tenancy=True)
        install(root / "app")
        manifest = root / "app" / "xcore-doctype" / "plugin.yaml"
        manifest.write_text(manifest.read_text().replace("timezone: UTC", "timezone: Europe/Paris"))
        install(root / "app", force=True)
        assert "timezone: UTC" in manifest.read_text()

    def test_an_older_installed_manifest_is_flagged(self, tmp_path):
        root = instance(tmp_path, tenancy=True)
        install(root / "app")
        manifest = root / "app" / "xcore-doctype" / "plugin.yaml"
        manifest.write_text(manifest.read_text().replace(f"version: {__version__}", "version: 0.0.1", 1))
        assert any("version 0.0.1" in m for m in warns(install(root / "app")))


class TestDiagnose:
    def test_a_correct_instance_has_no_error_and_no_warning(self, tmp_path):
        root = instance(tmp_path, tenancy=True)
        install(root / "app")
        (root / "app" / "auth").mkdir()  # le plugin d'authentification attendu par le Desk (`app/auth` / xauth)
        findings = diagnose(root / "integration.yaml", root / "app")
        assert not errors(findings) and not warns(findings)

    def test_a_desk_without_an_authentication_plugin_is_flagged(self, tmp_path):
        root = instance(tmp_path, tenancy=True)
        install(root / "app")
        assert any("plugin d'authentification" in m for m in warns(diagnose(root / "integration.yaml", root / "app")))

    def test_tenancy_off_with_a_strict_binding_is_an_error(self, tmp_path):
        root = instance(tmp_path, tenancy=None)
        install(root / "app", tenancy="on")  # l'opérateur a forcé le lien alors qu'xcore n'a pas de tenancy
        assert any("claim de tenant que rien ne fournit" in m for m in errors(diagnose(root / "integration.yaml", root / "app")))

    def test_tenancy_on_with_a_loose_binding_is_a_warning(self, tmp_path):
        root = instance(tmp_path, tenancy=True)
        install(root / "app", tenancy="off")
        assert any("non authentifié" in m for m in warns(diagnose(root / "integration.yaml", root / "app")))

    def test_strict_trusted_asks_for_a_signature(self, tmp_path):
        root = instance(tmp_path, tenancy=True, strict=True)
        install(root / "app")
        (msg,) = [m for m in warns(diagnose(root / "integration.yaml", root / "app")) if m.startswith("plugins.strict_trusted")]
        assert "xcli plugin security sign" in msg and str(root / "app" / "xcore-doctype") in msg

    def test_the_absent_strict_trusted_key_means_strict(self, tmp_path):
        root = instance(tmp_path, tenancy=True)
        config = yaml.safe_load((root / "integration.yaml").read_text())
        del config["plugins"]["strict_trusted"]
        (root / "integration.yaml").write_text(yaml.safe_dump(config))
        install(root / "app")
        assert any(m.startswith("plugins.strict_trusted") for m in warns(diagnose(root / "integration.yaml", root / "app")))

    def test_isolate_db_is_flagged(self, tmp_path):
        root = instance(tmp_path, tenancy=True, isolate=True)
        install(root / "app")
        assert any("isolate_db" in m for m in warns(diagnose(root / "integration.yaml", root / "app")))

    def test_a_plugin_installed_where_xcore_does_not_look_is_flagged(self, tmp_path):
        root = instance(tmp_path, tenancy=True, directory="./plugins")
        install(root / "app")
        assert any("xcore ne le verra pas" in m for m in warns(diagnose(root / "integration.yaml", root / "app")))

    def test_an_unreadable_config_is_an_error_not_a_crash(self, tmp_path):
        (tmp_path / "integration.yaml").write_text("services: [unclosed")
        assert any("illisible" in m for m in errors(diagnose(tmp_path / "integration.yaml")))


class TestCommandLine:
    def test_install_prints_what_it_did_and_exits_zero(self, tmp_path, capsys):
        root = instance(tmp_path, db="sqlite", main_py=BAD_MAIN)
        assert main(["install", str(root / "app")]) == 0
        out = capsys.readouterr().out
        assert "✔" in out and "✖" in out and "2 point(s) à corriger" in out

    def test_doctor_exit_code_follows_the_errors(self, tmp_path, capsys):
        root = instance(tmp_path, db="sqlite", main_py=BAD_MAIN)
        install(root / "app")
        assert main(["doctor", "--config", str(root / "integration.yaml"), "--plugins-dir", str(root / "app")]) == 1
        fixed = instance(tmp_path, tenancy=None)
        assert main(["doctor", "--config", str(fixed / "integration.yaml"), "--plugins-dir", str(fixed / "app")]) == 0

    def test_doctor_without_a_config_is_an_error(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        assert main(["doctor"]) == 2
        assert "introuvable" in capsys.readouterr().err

    def test_path_prints_the_packaged_folder(self, capsys):
        assert main(["path"]) == 0
        assert capsys.readouterr().out.strip() == str(plugin_dir())

    def test_version(self, capsys):
        with pytest.raises(SystemExit) as e:
            main(["--version"])
        assert e.value.code == 0 and __version__ in capsys.readouterr().out

    def test_a_command_is_required(self):
        with pytest.raises(SystemExit) as e:
            main([])
        assert e.value.code == 2


class TestCheck:
    def write(self, d, **docs):
        d.mkdir(parents=True, exist_ok=True)
        for name, doc in docs.items():
            (d / f"{name}.yaml").write_text(yaml.safe_dump(doc))
        return d

    def dt(self, name, module="sales", **extra):
        return {"schema_version": 1, "name": name, "module": module, "fields": [{"fieldname": "x", "fieldtype": "Text"}], **extra}

    def test_a_valid_folder_lists_doctypes_grants_and_hooks(self, tmp_path, capsys):
        d = self.write(tmp_path / "d", a=self.dt("Order", hooks={"validate": ["sales.check"]}))
        assert main(["check", str(d), "--owner", "sales"]) == 0
        out = capsys.readouterr().out
        assert "1 DocType(s) valides" in out and "Order" in out
        assert "sales:order:read" in out and "sales.check" in out

    def test_the_sample_plugins_are_valid(self, capsys):
        # `sales` cite l'article du plugin `stock` (requires) : sans `--with`, sa cible de Link est inconnue.
        assert main(["check", "app/sales/doctypes", "--owner", "sales"]) == 1
        assert "Item" in capsys.readouterr().err
        assert main(["check", "app/sales/doctypes", "--owner", "sales", "--with", "app/stock/doctypes"]) == 0
        out = capsys.readouterr().out
        assert "sales.invoice_totals" in out and "4 DocType(s) valides" in out and "Stock Entry" not in out  # les dépendances ne sont pas listées
        assert "espace(s) de travail valides : sales" in out  # l'espace du plugin se valide avec ses DocTypes
        assert main(["check", "app/stock/doctypes", "--owner", "stock"]) == 0
        out = capsys.readouterr().out
        assert "stock.post_entry" in out and "stock:stock_entry:submit" in out and "stock:stock_entry:cancel" in out

    def test_the_owner_rule_is_applied(self, tmp_path, capsys):
        d = self.write(tmp_path / "d", a=self.dt("Order", module="stock"))
        assert main(["check", str(d), "--owner", "sales"]) == 1
        assert "`module` doit être 'sales'" in capsys.readouterr().err

    def test_without_an_owner_any_module_goes(self, tmp_path):
        assert main(["check", str(self.write(tmp_path / "d", a=self.dt("Order", module="stock")))]) == 0

    def test_a_broken_link_is_reported(self, tmp_path, capsys):
        link = self.dt("Order", fields=[{"fieldname": "c", "fieldtype": "Link", "options": "Nope"}])
        assert main(["check", str(self.write(tmp_path / "d", a=link))]) == 1
        assert "DocType cible inconnu 'Nope'" in capsys.readouterr().err

    def test_a_missing_folder_is_reported(self, tmp_path, capsys):
        assert main(["check", str(tmp_path / "nope")]) == 1
        assert "introuvable" in capsys.readouterr().err
