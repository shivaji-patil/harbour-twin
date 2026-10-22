"""Render docs/diagrams/*.mmd to docs/img/arch_<name>.png with headless Chrome and Mermaid.

    python tools/render_diagrams.py            # all diagrams
    python tools/render_diagrams.py sar        # one
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
MERMAID = "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.esm.min.mjs"
PAGE = """<!doctype html><meta charset="utf-8">
<style>html,body{margin:0;background:#fff}#d{display:inline-block;padding:24px}</style><div id="d"></div>
<script type="module">
import mermaid from "%s";
mermaid.initialize({startOnLoad: false, theme: "neutral", fontFamily: "Helvetica, Arial, sans-serif",
  flowchart: {htmlLabels: true, curve: "basis", useMaxWidth: false}, themeVariables: {fontSize: "17px"}});
const {svg} = await mermaid.render("g", SOURCE);
document.getElementById("d").innerHTML = svg;
</script>"""


def render(src: Path, chrome: str, tmp: Path, max_width: int = 3000) -> Path:
    page = tmp / f"{src.stem}.html"
    page.write_text(PAGE.replace("%s", MERMAID).replace("SOURCE", json.dumps(src.read_text())))
    shot = tmp / f"{src.stem}.png"
    subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={tmp / 'profile'}",
                    "--window-size=3600,1800", "--force-device-scale-factor=2", "--virtual-time-budget=8000",
                    f"--screenshot={shot}", page.as_uri()], check=True, capture_output=True)
    im = Image.open(shot).convert("RGB")
    x0, y0, x1, y1 = ImageChops.difference(im, Image.new("RGB", im.size, "white")).getbbox()
    im = im.crop((max(0, x0 - 30), max(0, y0 - 30), min(im.width, x1 + 30), min(im.height, y1 + 30)))
    if im.width > max_width:
        im = im.resize((max_width, round(im.height * max_width / im.width)), Image.LANCZOS)
    out = ROOT / "docs" / "img" / f"arch_{src.stem}.png"
    im.save(out, optimize=True)
    return out


if __name__ == "__main__":
    chrome = next(filter(None, map(shutil.which, ("google-chrome", "chromium", "chromium-browser"))), None)
    if not chrome:
        sys.exit("needs Google Chrome or Chromium on PATH")
    names = sys.argv[1:] or [p.stem for p in sorted((ROOT / "docs" / "diagrams").glob("*.mmd"))]
    with tempfile.TemporaryDirectory() as tmp:
        for n in names:
            print(render(ROOT / "docs" / "diagrams" / f"{n}.mmd", chrome, Path(tmp)))
