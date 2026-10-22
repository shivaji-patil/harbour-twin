"""Build docs/OVERVIEW.html: a plain-language, self-contained overview (Monokai Pro colours).

    python docs/overview/build_overview.py          # HTML
    python docs/overview/build_overview.py --pdf    # + OVERVIEW.pdf (needs Chrome): one long page, for reading in Drive

Diagrams are inline SVG drawn here; screenshots are embedded as base64, so the
page has no external files, fonts or scripts and opens from a download.
"""
import base64
import html
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IMG = ROOT / "docs" / "img"

# Monokai Pro (filter: default)
BG, BG2, BG3, LINE, DIM, FG = "#2D2A2E", "#221F22", "#19181A", "#403E41", "#939293", "#FCFCFA"
RED, ORANGE, YELLOW, GREEN, BLUE, PURPLE = "#FF6188", "#FC9867", "#FFD866", "#A9DC76", "#78DCE8", "#AB9DF2"

REBUILD_MIN = "about 4"   # clean copy, 21 Oct 2026: make data took 4 min 5 s


# ------------------------------------------------------------------ SVG helpers
def esc(s):
    return html.escape(str(s), quote=False)


def svg(w, h, body, title):
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{esc(title)}" xmlns="http://www.w3.org/2000/svg">'
            f'<title>{esc(title)}</title><defs>'
            + "".join(f'<marker id="a{c[1:]}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
                      f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>'
                      for c in (DIM, GREEN, RED, YELLOW, BLUE, PURPLE, ORANGE))
            + f'</defs>{"".join(body)}</svg>')


def text(x, y, lines, color=FG, size=13, weight=400, anchor="middle"):
    lines = [lines] if isinstance(lines, str) else lines
    dy = size * 1.3
    y0 = y - dy * (len(lines) - 1) / 2
    spans = "".join(f'<tspan x="{x}" y="{y0 + i * dy:.1f}">{esc(t)}</tspan>' for i, t in enumerate(lines))
    return (f'<text text-anchor="{anchor}" dominant-baseline="middle" fill="{color}" font-size="{size}" '
            f'font-weight="{weight}">{spans}</text>')


def box(x, y, w, h, title, sub=(), color=BLUE, fill=BG2):
    sub = [sub] if isinstance(sub, str) else list(sub)
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" fill="{fill}" stroke="{color}" stroke-width="1.6"/>']
    if sub:
        n = len(sub)
        th = 16 + 15 * n
        top = y + (h - th) / 2
        out.append(text(x + w / 2, top + 8, title, color, 14, 700))
        out.append(text(x + w / 2, top + 16 + 15 * (n / 2) + 1, sub, FG, 12))
    else:
        out.append(text(x + w / 2, y + h / 2, title, color, 14, 700))
    return "".join(out)


def diamond(cx, cy, w, h, lines, color=YELLOW):
    pts = f"{cx},{cy - h / 2} {cx + w / 2},{cy} {cx},{cy + h / 2} {cx - w / 2},{cy}"
    return (f'<polygon points="{pts}" fill="{BG2}" stroke="{color}" stroke-width="1.6"/>'
            + text(cx, cy, lines, color, 12.5, 700))


def arrow(pts, color=DIM, label=None, lx=None, ly=None, dash=False):
    d = "M" + " L".join(f"{x},{y}" for x, y in pts)
    s = (f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.6" '
         f'{"stroke-dasharray=\"5 4\" " if dash else ""}marker-end="url(#a{color[1:]})"/>')
    if label:
        x = lx if lx is not None else (pts[0][0] + pts[-1][0]) / 2
        y = ly if ly is not None else (pts[0][1] + pts[-1][1]) / 2 - 9
        s += text(x, y, label, color, 12, 700)
    return s


def heading(x, y, t, color=DIM):
    return text(x, y, t.upper(), color, 11.5, 700)


# ------------------------------------------------------------------ diagrams
def big_picture():
    b = [heading(130, 22, "Free public data"), heading(500, 22, "Harbour Twin"), heading(860, 22, "What you get")]
    srcs = [("Lidar scans", "NRCan, 1 m detail", GREEN), ("Satellite elevation", "Copernicus DEM", GREEN),
            ("Satellite photos", "Sentinel-2", GREEN), ("Satellite radar", "Sentinel-1", ORANGE),
            ("Building outlines", "Overture Maps / OSM", GREEN), ("Ship positions", "AIS, recorded live", ORANGE),
            ("Tides", "Canadian Hydrographic Service", GREEN)]
    for i, (t, s, c) in enumerate(srcs):
        y = 40 + i * 56
        b.append(box(20, y, 220, 46, t, s, c))
        b.append(arrow([(240, y + 23), (330, 210 + (i - 3) * 14)], DIM))
    b.append(box(335, 40, 330, 382, "", (), PURPLE))
    steps = ["Line up every height on the same reference", "Score each dataset against the lidar",
             "Measure every building from the lidar", "Find ships in the radar picture",
             "Match radar ships to AIS ships", "Work out where the sea reaches in a flood",
             "Package and catalogue the results"]
    b.append(text(500, 66, "Python pipeline", PURPLE, 15, 700))
    for i, s in enumerate(steps):
        y = 98 + i * 45
        b.append(f'<rect x="355" y="{y - 16}" width="290" height="34" rx="6" fill="{BG3}" stroke="{LINE}"/>')
        b.append(text(500, y + 1, s, FG, 12.5))
    outs = [("3D viewer in a browser", "city, ships, radar, floods", BLUE), ("Quality report", "pass / fail against specs", YELLOW),
            ("Offline map files", "for devices without internet", BLUE), ("Catalogue + web APIs", "STAC, OGC API, I3S, CityJSON", BLUE)]
    for i, (t, s, c) in enumerate(outs):
        y = 52 + i * 92
        b.append(box(750, y, 230, 62, t, s, c))
        b.append(arrow([(665, 230), (750, y + 31)], DIM))
    return svg(1000, 440, b, "Big picture: public data in, checked products out")


def run_flow():
    steps = [("1  Lidar", ["Ground and rooftop", "heights, 1 m detail"]), ("2  Quality check", ["Score satellite", "elevation vs lidar"]),
             ("3  Terrain + flood", ["3D ground and", "where water reaches"]), ("4  Satellite photo", ["Cloud-free summer", "colour image"]),
             ("5  Buildings", ["Measured heights →", "3D Tiles, I3S, CityJSON"]), ("6  Ships (AIS)", ["Clean recorded", "tracks for replay"]),
             ("7  Radar ships", ["Find ships in", "Sentinel-1, match AIS"]), ("8  Tides", ["Observed water", "level, same clock"]),
             ("9  Offline package", ["DTED + GeoPackage", "for field devices"]), ("10  Catalogue", ["Describe every", "product (STAC)"])]
    b = []
    w, h, gap = 170, 74, 30
    for i, (t, s) in enumerate(steps):
        row, col = divmod(i, 5)
        col = col if row == 0 else 4 - col
        x, y = 15 + col * (w + gap), 20 + row * 130
        color = ORANGE if i in (5, 6) else BLUE
        b.append(box(x, y, w, h, t, s, color))
        if i < 9:
            if row == 0 and col < 4:
                b.append(arrow([(x + w, y + h / 2), (x + w + gap, y + h / 2)]))
            elif row == 0 and col == 4:
                b.append(arrow([(x + w / 2, y + h), (x + w / 2, y + 130)]))
            else:
                b.append(arrow([(x, y + h / 2), (x - gap, y + h / 2)]))
    b.append(text(500, 248, "make data  runs every step in order; each step can also be re-run on its own", DIM, 12.5))
    return svg(1000, 262, b, "What happens when you run make data")


def sar_flow():
    b = []
    b.append(box(20, 30, 200, 70, "Radar picture", ["Sentinel-1 satellite,", "10 m detail, day or night"], ORANGE))
    b.append(arrow([(220, 65), (260, 65)]))
    b.append(box(260, 30, 200, 70, "Keep open water", ["remove land, piers,", "bridges, 40 m shore strip"], BLUE))
    b.append(arrow([(460, 65), (500, 65)]))
    b.append(box(500, 30, 220, 70, "Find bright spots", ["much brighter than the", "sea around them (CFAR)"], BLUE))
    b.append(arrow([(720, 65), (760, 65)]))
    b.append(box(760, 30, 220, 70, "Group into ships", ["merge pieces, drop specks,", "one point per ship"], BLUE))
    b.append(arrow([(870, 100), (870, 140), (560, 140), (560, 160)]))
    b.append(diamond(560, 205, 280, 86, ["Was AIS being recorded", "when the satellite passed?"]))
    b.append(arrow([(420, 205), (250, 205), (250, 250)], DIM, "No", 335, 195))
    b.append(box(130, 250, 240, 64, "Show ships only", ["no cross-check possible", "(today's bundled data)"], DIM))
    b.append(arrow([(560, 248), (560, 285)], GREEN, "Yes", 584, 266))
    b.append(box(440, 285, 240, 64, "Where was each AIS ship", ["at that exact moment?", "(between its reports)"], BLUE))
    b.append(arrow([(560, 349), (560, 372)]))
    b.append(diamond(560, 418, 300, 88, ["Is there an AIS ship close", "to each radar ship?"]))
    b.append(arrow([(410, 418), (250, 418), (250, 470)], RED, "No", 330, 408))
    b.append(box(110, 470, 280, 70, "Dark-vessel candidate", ["radar sees it, AIS silent:", "a person reviews it"], RED))
    b.append(arrow([(710, 418), (850, 418), (850, 470)], GREEN, "Yes", 780, 408))
    b.append(box(730, 470, 240, 70, "Matched", ["radar and AIS agree", "on this ship"], GREEN))
    b.append(arrow([(560, 462), (560, 470)], YELLOW))
    b.append(box(430, 470, 260, 70, "AIS ship, no radar match", ["usually a small boat", "below radar detail"], YELLOW))
    return svg(1000, 555, b, "Finding ships that do not announce themselves")


def qc_flow():
    b = []
    b.append(box(15, 50, 180, 70, "A dataset", ["e.g. satellite", "elevation"], BLUE))
    b.append(arrow([(195, 85), (235, 85)]))
    b.append(box(235, 50, 190, 70, "Line up heights", ["convert to the same", "vertical reference"], BLUE))
    b.append(arrow([(425, 85), (465, 85)]))
    b.append(box(465, 50, 180, 70, "Compare to lidar", ["error numbers on", "400,000 land cells"], BLUE))
    b.append(arrow([(645, 85), (690, 85)]))
    b.append(diamond(785, 85, 190, 96, ["Within the", "published spec?"]))
    b.append(arrow([(880, 85), (920, 85), (920, 30), (940, 30)], GREEN, "Yes", 900, 20))
    b.append(box(940, 8, 50, 44, "PASS", (), GREEN))
    b.append(arrow([(880, 85), (920, 85), (920, 140), (940, 140)], RED, "No", 900, 158))
    b.append(box(940, 118, 50, 44, "FAIL", (), RED))
    b.append(f'<rect x="235" y="175" width="530" height="56" rx="9" fill="{BG3}" stroke="{YELLOW}" stroke-dasharray="5 4"/>')
    b.append(text(500, 203, ["Deliberate-mistake test: feed in heights with the wrong reference.",
                             "The check must say FAIL. It does (16.5 m error), so the check is proven to work."], YELLOW, 12.5))
    return svg(1000, 245, b, "How each dataset is checked before it is trusted")


def folder_map():
    b = [box(380, 15, 240, 50, "harbour-twin/", "the shared folder", PURPLE)]
    top = [("00_START_HERE.pdf", "2-page summary", YELLOW), ("OVERVIEW .html/.pdf", "this page", YELLOW),
           ("demo.mp4", "30-second tour", YELLOW), ("code/", "everything to run it", BLUE), ("products/", "large files, optional", GREEN)]
    for i, (t, s, c) in enumerate(top):
        x = 15 + i * 197
        b.append(arrow([(500, 65), (500, 85), (x + 88, 85), (x + 88, 105)]))
        b.append(box(x, 105, 176, 56, t, s, c))
    kids = [("twin/", "the pipeline (Python)"), ("web/", "the 3D viewer + its data"), ("tests/", "70 automated checks"),
            ("docs/", "architecture, data, images"), ("config/", "one file per city"), ("data/", "reports, catalogue")]
    for i, (t, s) in enumerate(kids):
        x, y = 15 + (i % 3) * 240, 205 + (i // 3) * 64
        b.append(box(x, y, 222, 52, t, s, BLUE))
    b.append(arrow([(679, 161), (679, 185), (370, 185), (370, 203)], BLUE, dash=True))
    b.append(box(760, 205, 225, 116, "products/data/", ["elevation rasters, DTED,", "GeoPackage per city.", "Copy into code/data/ to",
                                                         "skip the rebuild"], GREEN))
    b.append(arrow([(876, 161), (876, 203)], GREEN, dash=True))
    return svg(1000, 335, b, "What is in the folder")


def reproduce_flow():
    b = [diamond(500, 55, 300, 80, ["What do you", "want to do?"], PURPLE)]
    paths = [("A. Just look", ["2 minutes", "Python 3 + internet"], GREEN), ("B. Run the checks", ["5 minutes", "+ Node.js, make"], BLUE),
             ("C. Rebuild everything", [f"{REBUILD_MIN} minutes", "downloads only what it needs"], ORANGE),
             ("D. Record your own ships", ["2 hours of recording", "free aisstream.io key"], RED),
             ("E. Serve it (Docker)", ["object storage,", "OGC API, STAC Browser"], PURPLE)]
    for i, (t, s, c) in enumerate(paths):
        x = 10 + i * 198
        b.append(arrow([(500, 95), (500, 115), (x + 90, 115), (x + 90, 140)], c))
        b.append(box(x, 140, 182, 76, t, s, c))
    return svg(1000, 230, b, "Five ways to reproduce the work")


# ------------------------------------------------------------------ page
def img(name, alt, caption):
    data = base64.b64encode((IMG / name).read_bytes()).decode()
    return (f'<figure><img src="data:image/jpeg;base64,{data}" alt="{esc(alt)}" loading="lazy">'
            f'<figcaption>{caption}</figcaption></figure>')


def code(cmds):
    return f'<pre><code>{esc(cmds.strip())}</code></pre>'


CSS = f"""
:root{{--bg:{BG};--bg2:{BG2};--bg3:{BG3};--line:{LINE};--dim:{DIM};--fg:{FG};--red:{RED};--orange:{ORANGE};
--yellow:{YELLOW};--green:{GREEN};--blue:{BLUE};--purple:{PURPLE}}}
*{{box-sizing:border-box}}
html{{scroll-behavior:smooth}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 -apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}}
main{{max-width:1080px;margin:0 auto;padding:0 20px 60px}}
header.hero{{background:var(--bg3);border-bottom:2px solid var(--purple);padding:44px 20px 34px}}
header.hero .in{{max-width:1080px;margin:0 auto}}
.kicker{{color:var(--yellow);font:600 13px/1 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;letter-spacing:.12em;text-transform:uppercase}}
h1{{font-size:42px;line-height:1.1;margin:10px 0 12px;color:var(--fg)}}
h1 span{{color:var(--green)}}
.lede{{font-size:19px;color:#E1DFE2;max-width:820px;margin:0}}
.by{{color:var(--dim);margin-top:14px;font-size:14px}}
nav{{position:sticky;top:0;z-index:5;background:rgba(25,24,26,.94);border-bottom:1px solid var(--line);backdrop-filter:blur(6px)}}
nav .in{{max-width:1080px;margin:0 auto;padding:10px 20px;display:flex;gap:18px;overflow-x:auto;white-space:nowrap}}
nav a{{color:var(--dim);text-decoration:none;font-size:14px}}
nav a:hover{{color:var(--yellow)}}
section{{padding-top:44px}}
h2{{font-size:27px;margin:0 0 6px;color:var(--fg)}}
h2 .n{{color:var(--purple);font-family:ui-monospace,Menlo,Consolas,monospace;margin-right:10px}}
h3{{color:var(--blue);margin:26px 0 8px;font-size:19px}}
p{{margin:10px 0;max-width:860px}}
.sub{{color:var(--dim);margin-top:0}}
a{{color:var(--blue)}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin:22px 0}}
.card{{background:var(--bg2);border:1px solid var(--line);border-top:3px solid var(--c,var(--green));border-radius:10px;padding:16px 18px}}
.card b{{display:block;font-size:30px;line-height:1.15;color:var(--c,var(--green))}}
.card span{{color:#D6D4D7;font-size:14.5px}}
.diagram{{background:var(--bg2);border:1px solid var(--line);border-radius:12px;padding:16px;margin:20px 0}}
.diagram svg{{display:block;width:100%;height:auto;font-family:-apple-system,"Segoe UI",Roboto,Arial,sans-serif}}
.diagram .cap{{color:var(--dim);font-size:13.5px;margin:10px 4px 0}}
figure{{margin:20px 0;background:var(--bg2);border:1px solid var(--line);border-radius:12px;overflow:hidden}}
figure img{{display:block;width:100%;height:auto}}
figcaption{{padding:10px 16px;color:#D6D4D7;font-size:14px}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
.two figure{{margin:0}}
ul{{max-width:860px;padding-left:22px}} li{{margin:6px 0}}
.found li::marker{{color:var(--yellow)}}
pre{{background:var(--bg3);border:1px solid var(--line);border-left:3px solid var(--green);border-radius:8px;padding:14px 16px;overflow-x:auto;margin:12px 0}}
code{{font:14px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:var(--fg)}}
p code,li code,td code{{background:var(--bg3);border:1px solid var(--line);border-radius:4px;padding:1px 5px;font-size:13.5px;color:var(--yellow)}}
.path{{background:var(--bg2);border:1px solid var(--line);border-left:4px solid var(--c);border-radius:10px;padding:6px 20px 12px;margin:18px 0}}
.path h3{{color:var(--c)}}
.need{{color:var(--dim);font-size:14px;margin-top:-4px}}
.expect{{color:var(--green);font-size:14.5px}}
.note{{background:var(--bg3);border:1px dashed var(--yellow);border-radius:10px;padding:12px 16px;color:#EDEBEE;max-width:none}}
table{{border-collapse:collapse;width:100%;margin:14px 0;font-size:15px}}
th,td{{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:top}}
th{{color:var(--purple);font-weight:600;background:var(--bg2)}}
td:first-child{{color:var(--yellow);white-space:nowrap}}
dl.gloss{{display:grid;grid-template-columns:200px 1fr;gap:8px 20px;max-width:900px}}
dl.gloss dt{{color:var(--orange);font-weight:600}} dl.gloss dd{{margin:0;color:#E1DFE2}}
footer{{border-top:1px solid var(--line);margin-top:50px;padding-top:18px;color:var(--dim);font-size:13px}}
@media print{{*{{-webkit-print-color-adjust:exact;print-color-adjust:exact}}@page{{size:Letter;margin:0}}html,body{{background:var(--bg)}}nav{{display:none}}main{{padding:0 36px 30px}}header.hero{{padding:34px 36px 26px}}section{{padding-top:26px}}.diagram,figure,.card,.path,pre,tr,.note{{break-inside:avoid}}h2,h3{{break-after:avoid}}.two{{grid-template-columns:1fr 1fr}}a{{text-decoration:none}}}}
@media (max-width:760px){{h1{{font-size:31px}}.two{{grid-template-columns:1fr}}dl.gloss{{grid-template-columns:1fr}}
td:first-child{{white-space:normal}}.lede{{font-size:17px}}}}
"""


def page():
    S = []
    S.append(f"""
<section id="summary"><h2><span class="n">01</span>In one minute</h2>
<p>A <b>digital twin</b> is a working computer copy of a real place. Harbour Twin is a 3D copy of
Vancouver Harbour (and St. John's Harbour) made <b>only from free public data</b>: laser scans of the ground,
satellite photos and radar, building outlines, tide gauges and the radio messages ships broadcast about
where they are (<b>AIS</b>).</p>
<p>It answers three practical questions, and shows its working for each:</p>
<ul>
<li><b>Which ships are out there, and are any of them silent?</b> It compares ships seen by satellite radar with
ships announcing themselves over AIS. A ship the radar sees but that is not announcing itself is worth a closer look.</li>
<li><b>Where would the sea reach in a flood?</b> It works out, building by building, the water level at which the sea
actually reaches it, not just "everything below a line".</li>
<li><b>Can the data be trusted?</b> Every dataset is scored against the most accurate one available (laser scans)
and must pass published accuracy specifications before it is used.</li>
</ul>
<div class="cards">
<div class="card" style="--c:{ORANGE}"><b>77</b><span>ships found by satellite radar in one Vancouver pass, including the anchored freighters in English Bay</span></div>
<div class="card" style="--c:{BLUE}"><b>347</b><span>vessels recorded live over 4 hours: 7,854 cleaned position reports</span></div>
<div class="card" style="--c:{GREEN}"><b>98.7%</b><span>of 5,848 building heights measured by laser, not guessed</span></div>
<div class="card" style="--c:{YELLOW}"><b>70</b><span>automated checks that must pass before results are published</span></div>
</div>
</section>

<section id="picture"><h2><span class="n">02</span>The big picture</h2>
<p class="sub">Public data goes in on the left, a checked set of results comes out on the right.</p>
<div class="diagram">{big_picture()}<p class="cap">Orange inputs are the maritime ones: satellite radar and ship positions.
Everything is read directly from the organisations that publish it; nothing is copied by hand.</p></div>
</section>

<section id="see"><h2><span class="n">03</span>What it looks like</h2>
{img("hero.jpg", "Downtown Vancouver in 3D with ships in the harbour", "Downtown Vancouver from False Creek. Buildings are coloured by measured height; coloured dots and trails in Burrard Inlet are ships replaying their recorded movements, with the water level following the real tide on the same clock.")}
<div class="two">
{img("sar.jpg", "Radar view of English Bay with ships circled", "Satellite radar over English Bay. Ships show up as bright crosses; each ring is a ship the software found. One is selected (top right).")}
{img("stjohns_flood.jpg", "St. John's harbour with flood risk colours", "St. John's harbour with the sea 1 m above its highest recorded level. Red buildings are reached by the water: 86 of 7,308.")}
</div>
<p>A 30-second tour is in the folder as <code>demo.mp4</code>.</p>
</section>

<section id="run"><h2><span class="n">04</span>How a run works</h2>
<p class="sub">One command builds the whole twin, step by step. Each step saves its results, so any step can be re-run alone.</p>
<div class="diagram">{run_flow()}<p class="cap">Steps 6 and 7 (orange) are the ship work. Step 2 is the quality check that decides
which elevation data is good enough to use.</p></div>
</section>

<section id="ships"><h2><span class="n">05</span>Finding ships that do not announce themselves</h2>
<p>Large ships must broadcast AIS: who they are and where they are, every few seconds. Some don't: their equipment is off,
broken, or deliberately switched off. Satellite radar sees ships whether or not they broadcast, day or night, through cloud.
Comparing the two is a core task in <b>maritime domain awareness</b>.</p>
<div class="diagram">{sar_flow()}<p class="cap">The comparison needs ship positions recorded at the moment the satellite
passes overhead (about 02:02 and 14:21 UTC over Vancouver, each repeating every 12 days). The software predicts the next pass.</p></div>
{img("sar_detections_qc.jpg", "All radar ship detections ringed on the radar image", "Every ship the software found in the 10 October 2026 pass, ringed on the radar image (English Bay left, the inner harbour right). Looking at this picture caught a mistake: the Lions Gate Bridge had been counted as a ship. Bridges are now excluded.")}
<p class="note"><b>Honest status.</b> The ship finding works on real radar images. The matching step is tested with
made-up data so far, because the bundled ship recording does not overlap a satellite pass. Recording ships during the
next pass (path D below) produces the first real comparison.</p>
</section>

<section id="trust"><h2><span class="n">06</span>How we know the results are right</h2>
<div class="diagram">{qc_flow()}<p class="cap">Every elevation dataset goes through this before it is used. The results are in
<code>code/data/vancouver/QC_REPORT.md</code>.</p></div>
<h3>What the checks caught</h3>
<ul class="found">
<li><b>A standard tool was wrong by up to a metre.</b> The common mapping library (PROJ) converts Canadian heights to
the global reference with an error of 0.30 m in Vancouver and 1.05 m in St. John's. The pipeline does the conversion
explicitly, and a test makes sure it stays right.</li>
<li><b>Published building heights are often wrong.</b> A 190.8 m tower was listed as 10 m tall in OpenStreetMap.
717 such disagreements are flagged for a person to review rather than silently "fixed".</li>
<li><b>A bridge looked like a ship</b> to the radar detector, found by looking at the results on the radar image.</li>
<li><b>A second city broke two assumptions</b>: a missing software dependency, and a tide gauge in St. John's that
does not publish the usual height reference. Its level is now converted another way, and cross-checked.</li>
</ul>
</section>

<section id="folder"><h2><span class="n">07</span>What is in the folder</h2>
<div class="diagram">{folder_map()}<p class="cap">Start with <code>00_START_HERE.pdf</code> or this page. The <code>code/</code> folder
is complete on its own; <code>products/</code> only saves you the rebuild of the large files.</p></div>
</section>

<section id="reproduce"><h2><span class="n">08</span>Reproduce it yourself</h2>
<p class="sub">Pick the path that matches what you want. Each one builds on the ones before it.</p>
<div class="diagram">{reproduce_flow()}</div>
<p><b>Before you start:</b> download the whole <code>harbour-twin</code> folder (Google Drive gives you a .zip; unzip it).
Open a terminal in the unzipped folder. Linux and macOS work directly; on Windows, use WSL (Windows Subsystem for Linux).</p>

<div class="path" style="--c:{GREEN}"><h3>A. Just look at it</h3>
<p class="need">Needs: Python 3 (any recent version) and an internet connection for the 3D library and base map.</p>
{code('''cd code
python3 -m http.server 8765 --directory web''')}
<p>Open <a href="http://127.0.0.1:8765">http://127.0.0.1:8765</a> in Chrome, Edge or Firefox.
For St. John's open <code>http://127.0.0.1:8765/?aoi=stjohns</code>. Stop it with <code>Ctrl+C</code>.</p>
<p class="expect">Expect: downtown Vancouver in 3D, ships moving in the harbour, a panel on the left with layers,
flood planner and data-quality numbers. Tick "SAR ship detections" to see the radar layer.</p>
<p>Opening <code>web/index.html</code> by double-clicking does not work: browsers block a page from reading its own
data files unless it is served, which is what the first command does.</p></div>

<div class="path" style="--c:{BLUE}"><h3>B. Run the checks</h3>
<p class="need">Needs: Python 3.11 or newer, Node.js 18 or newer, and <code>make</code>.</p>
{code('''cd code
make setup        # installs Python packages and the 3D-model validator (about 30 s)
make test         # the automated checks
make validate     # checks every 3D building tile against the official standard''')}
<p class="expect">Expect: <code>67 passed, 3 skipped</code>, then <code>{{"files":69,"errors":0,"warnings":0}}</code>.
The 3 skipped checks need the large files. To include them, copy them in and run the checks again:</p>
{code('''cp -r ../products/data/* data/
make test         # now 70 passed
make serve        # same viewer as path A, using the installed Python''')}</div>

<div class="path" style="--c:{ORANGE}"><h3>C. Rebuild everything from the original sources</h3>
<p class="need">Needs: path B done, and a normal internet connection. It reads only the parts of each dataset it
needs, not whole files.</p>
{code(f'''make data                 # Vancouver: every step, {REBUILD_MIN} minutes
make test validate        # the same checks, now on what you just built
make data AOI=stjohns     # St. John's, same pipeline, different settings file
make test AOI=stjohns''')}
<p class="expect">Expect: progress messages for each step ("step lidar done in 13 s" and so on), then the same
checks passing. Numbers can move slightly if a source publishes newer data.</p></div>

<div class="path" style="--c:{RED}"><h3>D. Record your own ships, and compare them with radar</h3>
<p class="need">Needs: path B done and a free key from <a href="https://aisstream.io">aisstream.io</a> (sign in, copy the API key).</p>
{code('''echo "AISSTREAM_API_KEY=paste-your-key-here" > .env
make passes               # when the radar satellite next passes over
make record MIN=120       # start about an hour before that pass; records 2 hours
make ais                  # cleans the tracks, finds radar ships, matches them''')}
<p class="expect">Expect: after the satellite image appears in the public catalogue (usually 1 to 3 days after the pass),
<code>make ais</code> reports how many radar ships were matched, how many are dark-vessel candidates, and how many
AIS ships the radar did not see. The viewer's SAR panel shows the same.</p>
<p>Recording adds to any earlier recording. To keep them apart, move the old one first:
<code>mv data/cache/vancouver/ais_raw.jsonl data/cache/vancouver/ais_raw_old.jsonl</code>.</p></div>

<div class="path" style="--c:{PURPLE}"><h3>E. Serve it like a product (Docker)</h3>
<p class="need">Needs: the large files in <code>data/</code> (path B or C) and Docker with the Compose plugin.</p>
{code("docker compose up --build        # first run downloads the images (a few minutes)")}
<p class="expect">Expect, once the publish step has exited: the viewer at
<code>http://localhost:8333/harbour-twin/web/index.html</code>, served from object storage; the data as a web service
and map tiles (OGC API) at <code>http://localhost:5000</code>; and the catalogue at <code>http://localhost:8080</code>.
Stop with <code>Ctrl+C</code>, then <code>docker compose down</code>.</p></div>

<h3>If something goes wrong</h3>
<table>
<tr><th>What you see</th><th>What it means and what to do</th></tr>
<tr><td>Could not resolve host</td><td>A short network hiccup reaching one of the data providers. Run the same command again; every step can be re-run safely.</td></tr>
<tr><td>3 skipped</td><td>The large files are not in <code>data/</code>. Copy them from <code>products/</code> (path B) or rebuild them (path C).</td></tr>
<tr><td>Blank viewer page</td><td>The page was opened by double-clicking. Use the <code>python3 -m http.server</code> command from path A.</td></tr>
<tr><td>make: command not found</td><td>Install it (<code>sudo apt install make</code> on Ubuntu; Xcode command-line tools on macOS), or use WSL on Windows.</td></tr>
<tr><td>npm: command not found</td><td>Install Node.js 18+ from nodejs.org. It is only needed for <code>make validate</code>.</td></tr>
<tr><td>Set AISSTREAM_API_KEY</td><td>Only recording needs a key (path D). Everything else works without one.</td></tr>
</table>
</section>

<section id="limits"><h2><span class="n">09</span>Limits worth knowing</h2>
<ul>
<li>The flood planner is a planning screen: it uses still water and the shape of the ground, not waves, wind or drainage.</li>
<li>The radar matching has not yet been run on a real ship recording made during a satellite pass (see section 5).</li>
<li>How bright a spot must be to count as a ship was set by inspecting real images, not from a labelled training set.</li>
<li>AIS comes from volunteer receivers, so coverage is uneven and many vessels never report their type.</li>
<li>Buildings newer than the laser scan fall back to published heights, and are labelled as such.</li>
</ul>
</section>

<section id="glossary"><h2><span class="n">10</span>Words used here</h2>
<dl class="gloss">
<dt>AIS</dt><dd>Automatic Identification System: radio messages in which ships report their identity, position, speed and course.</dd>
<dt>SAR (radar)</dt><dd>Synthetic Aperture Radar: a satellite sends microwaves and records the echo. Works at night and through cloud; metal ships echo brightly against a dark sea.</dd>
<dt>Sentinel-1 / -2</dt><dd>European Space Agency satellites: Sentinel-1 carries radar, Sentinel-2 takes colour images. Their data is free.</dd>
<dt>Lidar</dt><dd>Laser scanning from aircraft. Measures the ground and rooftops to within centimetres; the most accurate height data used here.</dd>
<dt>Dark vessel</dt><dd>A ship seen by sensors such as radar but not reporting over AIS. Here always a <i>candidate</i>, for a person to review.</dd>
<dt>CFAR</dt><dd>Constant False Alarm Rate: a standard radar method that calls a spot a target only if it is much brighter than its own surroundings.</dd>
<dt>Vertical datum</dt><dd>The "zero" that heights are measured from. Different datasets use different zeros, metres apart; mixing them is a common, silent error.</dd>
<dt>Digital twin</dt><dd>A working computer copy of a real place, built from measurements and kept checkable.</dd>
<dt>STAC</dt><dd>SpatioTemporal Asset Catalog: a standard way to describe and find map and satellite data; used here both to find inputs and describe outputs.</dd>
<dt>DTED, GeoPackage</dt><dd>Standard map-file formats used on devices that work without internet, such as ships and drones.</dd>
<dt>CityJSON</dt><dd>An open format for 3D city models (the JSON form of CityGML), read by city-planning tools. The buildings are exported to it as simple closed blocks.</dd>
<dt>OGC API - Features</dt><dd>A standard web interface for map data: any GIS can ask it for "buildings in this box" or "flagged buildings" and get them back.</dd>
<dt>Object storage</dt><dd>Cloud file storage such as Amazon S3. Publishing the products there lets anyone read them over the web without a special server.</dd>
<dt>I3S</dt><dd>The 3D format ArcGIS uses for scene layers. The buildings are also delivered as an I3S package, checked down to individual roof heights.</dd>
<dt>3D Tiles</dt><dd>An open standard for streaming 3D city models to a web browser.</dd>
</dl>
</section>
""")
    body = "".join(S)
    nav = "".join(f'<a href="#{i}">{t}</a>' for i, t in (
        ("summary", "Summary"), ("picture", "Big picture"), ("see", "Screens"), ("run", "How it runs"), ("ships", "Ships"),
        ("trust", "Checks"), ("folder", "Folder"), ("reproduce", "Reproduce"), ("limits", "Limits"), ("glossary", "Glossary")))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Harbour Twin overview</title>
<meta name="description" content="Plain-language overview of Harbour Twin: what it does, how it works, how it is checked, and how to reproduce it.">
<style>{CSS}</style></head>
<body>
<header class="hero"><div class="in">
<div class="kicker">Project overview · plain language</div>
<h1>Harbour <span>Twin</span></h1>
<p class="lede">A 3D computer copy of a harbour, built only from free public data, that tracks ships, finds the ones that are not
announcing themselves, plans for floods, and checks its own accuracy.</p>
<p class="by">Shivaji Patil · shivaji@shivajipatil.com · October 2026 · Vancouver and St. John's, Canada</p>
</div></header>
<nav><div class="in">{nav}</div></nav>
<main>{body}
<footer>Data: NRCan (Open Government Licence – Canada) · Copernicus DEM © DLR/Airbus · Copernicus Sentinel-1 and Sentinel-2 (ESA; Sentinel-1 RTC via
Microsoft Planetary Computer, CC BY 4.0) · Overture Maps (ODbL / CDLA) · © OpenStreetMap contributors · aisstream.io · Fisheries and Oceans Canada (CHS) ·
ECCC · Open-Meteo · CARTO. Code: PolyForm Noncommercial 1.0.0 (noncommercial use only; data keeps its sources' licences). Built with an AI coding agent (Claude Code) under the author's direction; every result checked by
automated tests and by review. Technical detail: <code>code/docs/ARCHITECTURE.md</code>.</footer>
</main></body></html>"""


def pdf(src: Path, out: Path, width=1100, height=10200):
    """Print `src` as one continuous page (Drive's viewer scrolls it; no page breaks to split diagrams)."""
    chrome = next(filter(None, map(shutil.which, ("google-chrome", "chromium", "chromium-browser"))), None)
    if not chrome:
        sys.exit("--pdf needs Google Chrome or Chromium on PATH")
    with tempfile.TemporaryDirectory() as tmp:
        while True:  # grow the page until everything fits on one
            page_css = f"<style>@media print{{@page{{size:{width}px {height}px;margin:0}}html,body{{min-height:100%}}}}</style>"
            tmp_html = Path(tmp) / "single.html"
            tmp_html.write_text(src.read_text().replace("</head>", page_css + "</head>", 1))
            subprocess.run([chrome, "--headless=new", "--disable-gpu", f"--user-data-dir={Path(tmp) / 'profile'}",
                            "--no-pdf-header-footer", f"--print-to-pdf={out}", tmp_html.as_uri()], check=True, capture_output=True)
            if out.read_bytes().count(b"/Type /Page\n") <= 1 and b"/Count 1" in out.read_bytes() or height > 20000:
                return
            height += 400


if __name__ == "__main__":
    out = ROOT / "docs" / "OVERVIEW.html"
    out.write_text(page())
    print(out, f"{out.stat().st_size / 1e3:.0f} kB")
    if "--pdf" in sys.argv:
        pdf(out, out.with_suffix(".pdf"))
        print(out.with_suffix(".pdf"), f"{out.with_suffix('.pdf').stat().st_size / 1e3:.0f} kB")
