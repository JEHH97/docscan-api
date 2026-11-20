"""Export the API contract for downstream clients."""

import json
from pathlib import Path

from app.main import app

output = Path(__file__).resolve().parents[1] / "docs" / "openapi.json"
output.write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")
print(f"Wrote {output}")
