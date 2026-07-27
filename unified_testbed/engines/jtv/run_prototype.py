from pathlib import Path
import json
from jtv.experiments import run_all

if __name__ == "__main__":
    out = Path(__file__).resolve().parent / "outputs"
    summary = run_all(out)
    print(json.dumps(summary, indent=2))
