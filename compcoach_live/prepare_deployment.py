"""Install missing repository-root hosting files without replacing user config."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def prepare(root: Path, *, templates: Path | None = None) -> list[str]:
    templates = templates or Path(__file__).resolve().parent / "deployment" / "repository_root"
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    messages: list[str] = []
    for relative in (
        Path(".streamlit/config.toml"),
        Path(".streamlit/secrets.example.toml"),
        Path("requirements.txt"),
    ):
        target = root / relative
        if target.exists():
            messages.append(f"Conservato: {relative.as_posix()}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents a concurrent user's file from being
        # overwritten. No actual secrets.toml is ever opened or generated.
        with target.open("xb") as output, (templates / relative).open("rb") as source:
            shutil.copyfileobj(source, output)
        messages.append(f"Creato: {relative.as_posix()}")
    for relative in (Path(".gitignore"), Path("packages.txt")):
        target = root / relative
        existing = target.read_text() if target.exists() else ""
        existing_lines = {line.strip() for line in existing.splitlines()}
        required = [
            line for line in (templates / relative).read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        missing = [line for line in required if line.strip() not in existing_lines]
        if missing:
            prefix = "\n" if existing and not existing.endswith("\n") else ""
            with target.open("a") as output:
                output.write(prefix + "\n# CompCoach deployment\n" + "\n".join(missing) + "\n")
            messages.append(f"Integrato: {relative.as_posix()}")
        else:
            messages.append(f"Già pronto: {relative.as_posix()}")
    return messages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent,
                        help="Repository root; default: parent of compcoach_live.")
    args = parser.parse_args()
    for message in prepare(args.root):
        print(message)
    print("Ora controlla Source Control prima di Commit/Sync. I Secrets reali vanno inseriti privatamente.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
