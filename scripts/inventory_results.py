"""Create a hash inventory for an immutable Harbor run directory."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from common import ROOT, relative_path, sha256_file, utc_now, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    args = parser.parse_args()

    run_directory = args.run_directory.resolve()
    if not run_directory.is_dir():
        print(f"Run directory does not exist: {run_directory}", file=sys.stderr)
        return 1

    artifacts = []
    for path in sorted(run_directory.rglob("*")):
        if not path.is_file() or path.name == "artifact-index.json":
            continue
        artifacts.append(
            {
                "path": path.relative_to(run_directory).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )

    write_json(
        run_directory / "artifact-index.json",
        {
            "schema_version": "1.0",
            "created_at": utc_now(),
            "run_directory": relative_path(run_directory),
            "artifacts": artifacts,
        },
    )
    print(f"Indexed {len(artifacts)} artifacts in {relative_path(run_directory)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
