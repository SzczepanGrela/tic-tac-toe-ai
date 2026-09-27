"""Reject runtime requirements that disagree with the deployed dependency lock."""

from __future__ import annotations

import argparse
from importlib import metadata
from pathlib import Path
import re

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def read_requirements(path: Path, parents: tuple[Path, ...] = ()):
    path = path.resolve()
    if path in parents:
        raise ValueError(f"Circular requirements include: {path.name}")
    for line in path.read_text().replace("\\\n", " ").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-r "):
            yield from read_requirements(path.parent / line[3:].strip(), (*parents, path))
            continue
        line = re.sub(r"\s+--hash=\S+", "", line)
        requirement = Requirement(line)
        if requirement.url:
            raise ValueError(f"Use a versioned package requirement: {requirement.name}")
        if requirement.marker is None or requirement.marker.evaluate():
            yield requirement


def check_lock(requirements: Path, lock: Path, *, installed: bool = False) -> None:
    locked = {}
    for requirement in read_requirements(lock):
        name = canonicalize_name(requirement.name)
        specs = list(requirement.specifier)
        if len(specs) != 1 or specs[0].operator != "==" or "*" in specs[0].version:
            raise ValueError(f"Lock must contain exact versions: {name}")
        if name in locked:
            raise ValueError(f"Duplicate active lock entry: {name}")
        locked[name] = (specs[0].version, requirement.extras)

    for requirement in read_requirements(requirements):
        name = canonicalize_name(requirement.name)
        if name not in locked:
            raise ValueError(f"Missing runtime dependency in lock: {name}")
        version, extras = locked[name]
        if not requirement.specifier.contains(version):
            raise ValueError(f"Locked {name}=={version} does not satisfy {requirement}")
        if not requirement.extras <= extras:
            raise ValueError(f"Missing locked extras for {requirement}")

    if installed:
        for name, (version, _) in locked.items():
            actual = metadata.version(name)
            if actual != version:
                raise ValueError(f"Installed {name}=={actual} differs from locked {version}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    check_lock(root / "requirements-web.txt", root / "requirements-web.lock", installed=args.installed)
    print("Runtime requirements and lock agree" + ("; installed versions match." if args.installed else "."))


if __name__ == "__main__":
    main()
