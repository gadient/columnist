from __future__ import annotations

import json
from pathlib import Path

from app.main import app


def main() -> None:
    output_path = Path(__file__).resolve().parent.parent / "openapi.json"
    output_path.write_text(json.dumps(app.openapi(), indent=2), encoding="utf-8")
    print(f"Wrote OpenAPI spec to {output_path}")


if __name__ == "__main__":
    main()
