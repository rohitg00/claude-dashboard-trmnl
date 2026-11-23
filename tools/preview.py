import argparse
import json
import sys
from pathlib import Path

from liquid import Environment

ROOT = Path(__file__).resolve().parent.parent
HEAD = (
    '<!doctype html><meta charset="utf-8">'
    '<link rel="stylesheet" href="https://trmnl.com/css/latest/plugins.css">'
    '<script src="https://trmnl.com/js/latest/plugins.js"></script>'
    '<body class="environment trmnl" style="display:flex;flex-direction:column;gap:24px;padding:24px;background:#bbb">'
)
LAYOUTS = {
    "full": ("view--full", None, 1),
    "half_horizontal": ("view--half_horizontal", "mashup--1Tx1B", 2),
    "half_vertical": ("view--half_vertical", "mashup--1Lx1R", 2),
    "quadrant": ("view--quadrant", "mashup--2x2", 4),
}


def template_dir(name):
    return ROOT / "trmnl_plugin" / "fleet" if name == "fleet" else ROOT / "trmnl_plugin"


def main():
    parser = argparse.ArgumentParser(description="Render TRMNL templates from `python3 -m agentdash --dry-run` output")
    parser.add_argument("payloads", help="JSON file written by --dry-run")
    parser.add_argument("out", help="output folder")
    args = parser.parse_args()

    payloads = json.loads(Path(args.payloads).read_text())
    env = Environment()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    names = list(payloads)
    for layout, (view, mashup, slots) in LAYOUTS.items():
        screens = []
        for start in range(0, len(names), slots):
            views = []
            for name in names[start : start + slots]:
                source = (template_dir(name) / f"markup_{layout}.html").read_text()
                views.append(f'<div class="view {view}">{env.from_string(source).render(**payloads[name])}</div>')
            body = "".join(views)
            if mashup:
                body = f'<div class="mashup {mashup}">{body}</div>'
            screens.append(f'<div class="screen">{body}</div>')
        target = out / f"{layout}.html"
        target.write_text(HEAD + "".join(screens))
        print(target)


if __name__ == "__main__":
    sys.exit(main())
