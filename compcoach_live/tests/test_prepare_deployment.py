from pathlib import Path

from compcoach_live.prepare_deployment import prepare


def test_prepare_preserves_user_config_and_private_secrets(tmp_path: Path):
    config_directory = tmp_path / ".streamlit"
    config_directory.mkdir()
    config = config_directory / "config.toml"
    config.write_text('[theme]\nbase = "dark"\n')
    secrets = config_directory / "secrets.toml"
    secrets.write_text('PRIVATE_SETTING = "kept-private"\n')
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("custom-dependency==1.0\n")
    ignores = tmp_path / ".gitignore"
    ignores.write_text("custom-output/\n")
    packages = tmp_path / "packages.txt"
    packages.write_text("custom-system-package\n")

    prepare(tmp_path)

    assert config.read_text() == '[theme]\nbase = "dark"\n'
    assert secrets.read_text() == 'PRIVATE_SETTING = "kept-private"\n'
    assert requirements.read_text() == "custom-dependency==1.0\n"
    assert "custom-output/" in ignores.read_text()
    assert "**/.streamlit/secrets.toml" in ignores.read_text()
    assert "*.db" in ignores.read_text()
    assert "**/backups/" in ignores.read_text()
    assert "custom-system-package" in packages.read_text()
    assert "libgl1" in packages.read_text()


def test_prepare_is_idempotent_and_never_creates_real_secrets(tmp_path: Path):
    prepare(tmp_path)
    first_files = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    prepare(tmp_path)
    second_files = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    assert first_files == second_files
    assert not (tmp_path / ".streamlit" / "secrets.toml").exists()
    assert (tmp_path / ".streamlit" / "secrets.example.toml").is_file()
    assert (tmp_path / "requirements.txt").read_text() == "-r compcoach_live/requirements.txt\n"
    assert (tmp_path / "packages.txt").read_text().split() == ["libgl1"]


def test_prepare_packages_preserves_dependencies_and_removes_comments(tmp_path: Path):
    packages = tmp_path / "packages.txt"
    packages.write_text("# Existing dependencies\nffmpeg\nlibglib2.0-0 # images\n")

    prepare(tmp_path)

    assert packages.read_text() == "ffmpeg\nlibglib2.0-0\nlibgl1\n"
    assert "# CompCoach deployment" in (tmp_path / ".gitignore").read_text()
    first_contents = packages.read_bytes()
    prepare(tmp_path)
    assert packages.read_bytes() == first_contents


def test_prepare_repairs_legacy_packages_when_required_package_already_exists(tmp_path: Path):
    packages = tmp_path / "packages.txt"
    packages.write_text("\n# CompCoach deployment\nlibgl1\n")

    prepare(tmp_path)

    assert packages.read_text() == "libgl1\n"
