from __future__ import annotations

import inspect
import sys
from pathlib import Path


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    src_dir = project_root / "src"

    # Permet à "audio.xxx" de fonctionner si audio/ est sous src/ (src/audio)
    # et permet aussi d'importer "src.services...." normalement.
    sys.path.insert(0, str(project_root))
    sys.path.insert(0, str(src_dir))

    from src.services.recorder_service import RecorderService  # noqa: E402

    print("RecorderService methods:")
    for n in dir(RecorderService):
        if not n.startswith("_"):
            print(" -", n)

    print("\ninit:", inspect.signature(RecorderService.__init__))


if __name__ == "__main__":
    main()
