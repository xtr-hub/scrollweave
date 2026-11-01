"""Deterministic local web-page corpus for ScrollWeave browser smoke tests.

The corpus is ordinary HTML/CSS rendered by a real headless Chrome.  It is not a
collection of synthetic image crops: every page uses document flow, fixed chrome,
gradients, text, images and tables, exactly like a scrolling capture from a browser.

The generator deliberately keeps the ground truth cheap and exact.  Chrome captures
one full-page screenshot with the fixed chrome hidden; the scroll-frame capture hides
nothing.  ``meta.json`` records the browser geometry so the evaluator can crop the
truth to the content rectangle without guessing.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

VIEWPORTS = [
    (1280, 800),
    (1440, 900),
    (1024, 768),
    (900, 700),
    (390, 844),
    (430, 932),
    (1600, 900),
    (768, 1024),
]

CHROME_COMBOS = [
    (),
    ('top',),
    ('left',),
    ('right',),
    ('top', 'left'),
    ('top', 'right'),
    ('left', 'right'),
    ('top', 'left', 'right'),
    ('top', 'bottom'),
    ('top', 'left', 'bottom'),
    ('top', 'right', 'bottom'),
    ('top', 'left', 'right', 'bottom'),
    ('left', 'bottom'),
    ('right', 'bottom'),
]

THEMES = [
    # background, text, muted, panel, accent, code background
    ('#f6f4ef', '#24211c', '#6f6a62', '#fffdf8', '#b34a2f', '#282520'),
    ('#ffffff', '#172033', '#667085', '#f4f7fb', '#2457d6', '#0f172a'),
    ('#101418', '#e7edf3', '#9aa8b5', '#182027', '#4cc9f0', '#070b0e'),
    ('#f3f7f2', '#1d2a21', '#607065', '#ffffff', '#2d7a46', '#1d2a21'),
    ('#fdf6ee', '#3a2615', '#846b55', '#fffaf3', '#d97706', '#2f2115'),
    ('#eef2f7', '#17212b', '#5b6b7c', '#ffffff', '#8b5cf6', '#161b22'),
    ('#14110f', '#f5eee6', '#b9aa9b', '#211c18', '#f59e0b', '#0c0a08'),
    ('#f8f8fb', '#1c1b22', '#6e6b7a', '#ffffff', '#db2777', '#231f2b'),
]

FONT_STACKS = [
    'Segoe UI, Arial, sans-serif',
    'Georgia, "Times New Roman", serif',
    '"Trebuchet MS", Segoe UI, sans-serif',
    'Consolas, "Courier New", monospace',
    'system-ui, -apple-system, Arial, sans-serif',
]

WORDS = [
    'architecture',
    'browser',
    'capture',
    'document',
    'evidence',
    'geometry',
    'header',
    'interface',
    'journal',
    'knowledge',
    'layout',
    'motion',
    'navigation',
    'overlap',
    'page',
    'quality',
    'reconstruction',
    'scroll',
    'signal',
    'texture',
    'uncertainty',
    'viewport',
    'witness',
    'alignment',
    'boundary',
    'column',
]

ZH = [
    '滚动截屏',
    '页面重建',
    '固定页头',
    '内容区域',
    '重叠像素',
    '证据图',
    '视口边界',
    '长页面',
    '横向漂移',
    '光学匹配',
    '原始像素',
    '页面布局',
]


def _text(rng: random.Random, count: int) -> str:
    picked = []
    for _ in range(count):
        if rng.random() < 0.16:
            picked.append(rng.choice(ZH))
        else:
            picked.append(rng.choice(WORDS))
    sentence = ' '.join(picked)
    return sentence[0].upper() + sentence[1:] + '.'


def _paragraph(rng: random.Random) -> str:
    return ' '.join(_text(rng, rng.randint(8, 18)) for _ in range(rng.randint(2, 4)))


def _header(rng: random.Random, spec: dict) -> str:
    style = spec['header_style']
    accent = spec['accent']
    if style == 'dark':
        background = '#161b22'
        color = '#f0f6fc'
        border = '#30363d'
    elif style == 'glass':
        background = 'rgba(255,255,255,.72)'
        color = spec['fg']
        border = 'rgba(0,0,0,.08)'
    elif style == 'gradient':
        background = f'linear-gradient(100deg,{accent},#111827)'
        color = '#fff'
        border = 'transparent'
    elif style == 'plain':
        background = spec['bg']
        color = spec['fg']
        border = spec['muted']
    else:
        background = spec['panel']
        color = spec['fg']
        border = spec['muted']
    blur = 'backdrop-filter:blur(12px);' if style == 'glass' else ''
    nav = ''.join(
        f'<a style="color:{color};opacity:{0.95 - k * 0.12:.2f}" href="#">Section {k + 1}</a>'
        for k in range(rng.randint(3, 5))
    )
    height = spec['header_h']
    return f"""
<header class="site-header" data-chrome="top" style="top:0;left:0;right:0;height:{height}px;background:{background};
 color:{color};border-bottom:1px solid {border};{blur}">
  <div class="brand"><span class="mark" style="background:{accent}"></span><strong>ScrollWeave</strong></div>
  <nav>{nav}</nav>
  <div class="header-note">{rng.choice(ZH)}</div>
</header>"""


def _left_rail(rng: random.Random, spec: dict) -> str:
    width = spec['left_w']
    items = ''.join(
        f'<div class="rail-item{" active" if k == 1 else ""}">{rng.choice(WORDS).title()}</div>'
        for k in range(rng.randint(7, 13))
    )
    return f"""
<aside class="rail rail-left" data-chrome="left" style="left:0;width:{width}px">
  <div class="rail-title">Contents</div>{items}
  <div class="rail-card"><b>{rng.choice(ZH)}</b><br>{_text(rng, 5)}</div>
</aside>"""


def _right_rail(rng: random.Random, spec: dict) -> str:
    width = spec['right_w']
    items = ''.join(f'<li>{rng.choice(WORDS)}</li>' for _ in range(rng.randint(4, 8)))
    return f"""
<aside class="rail rail-right" data-chrome="right" style="right:0;width:{width}px">
  <div class="rail-title">On this page</div><ul>{items}</ul>
  <div class="rail-card" style="background:{spec['accent']};color:#fff">
    {rng.choice(ZH)}<br><small>fixed right rail</small>
  </div>
</aside>"""


def _footer(rng: random.Random, spec: dict) -> str:
    height = spec['footer_h']
    return f"""
<footer class="site-footer" data-chrome="bottom" style="bottom:0;left:0;right:0;height:{height}px">
  <span>ScrollWeave browser capture</span>
  <span>{rng.choice(ZH)} · {rng.randint(12, 99)} views</span>
</footer>"""


def _hero(rng: random.Random, spec: dict) -> str:
    return f"""
<section class="hero" style="background:linear-gradient(120deg,{spec['accent']}22,{spec['panel']})">
  <p class="eyebrow">FIELD NOTE {rng.randint(1, 99):02d}</p>
  <h1>{rng.choice(ZH)} / {rng.choice(WORDS).title()}</h1>
  <p>{_paragraph(rng)}</p>
  <div class="hero-meta"><span>8 min read</span><span>Updated {rng.randint(1, 28)} May</span></div>
</section>"""


def _cards(rng: random.Random, spec: dict) -> str:
    count = rng.randint(2, 4)
    cards = []
    for k in range(count):
        hue = (rng.randint(0, 320), rng.randint(40, 80), rng.randint(35, 62))
        color = f'hsl({hue[0]} {hue[1]}% {hue[2]}%)'
        cards.append(
            f'<article class="card"><div class="thumb" style="background:linear-gradient(145deg,{color},{spec["accent"]})">'
            f'<span>{k + 1:02d}</span></div><h3>{rng.choice(WORDS).title()}</h3>'
            f'<p>{_text(rng, rng.randint(7, 12))}</p><a href="#">Read more →</a></article>'
        )
    return f'<section><h2>{rng.choice(ZH)}</h2><div class="cards">{"".join(cards)}</div></section>'


def _code(rng: random.Random, spec: dict) -> str:
    lines = [
        'fn estimate(scroll: &[i32]) -> Vec<i32> {',
        '    let mut evidence = Vec::new();',
        f'    // {rng.choice(ZH)} / {rng.choice(WORDS)}',
        '    for pair in scroll.windows(2) {',
        '        evidence.push(pair[1] - pair[0]);',
        '    }',
        '    evidence',
        '}',
    ]
    return (
        f'<section><h2>Implementation</h2><pre class="code" style="background:{spec["code_bg"]}">'
        + '\n'.join(lines)
        + '</pre></section>'
    )


def _table(rng: random.Random, spec: dict) -> str:
    rows = ''.join(
        f'<tr><td>{rng.choice(WORDS)}</td><td>{rng.randint(4, 99)}</td>'
        f'<td>{rng.choice(["stable", "moving", "fixed", "verified"])}</td></tr>'
        for _ in range(rng.randint(4, 8))
    )
    return f"""
<section><h2>Measurements</h2><table><thead><tr><th>Signal</th><th>Value</th><th>State</th></tr></thead>
<tbody>{rows}</tbody></table></section>"""


def _figure(rng: random.Random, spec: dict) -> str:
    a = spec['accent']
    b = f'hsl({rng.randint(0, 320)} 60% 52%)'
    return f'''
<figure><svg viewBox="0 0 720 280" role="img" aria-label="abstract evidence map">
<defs><linearGradient id="g{rng.randint(1, 99999)}" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="{a}"/><stop offset="1" stop-color="{b}"/></linearGradient></defs>
<rect width="720" height="280" fill="url(#g{rng.randint(1, 99999)})"/>
<circle cx="{rng.randint(80, 640)}" cy="{rng.randint(60, 220)}" r="{rng.randint(30, 90)}" fill="#ffffff33"/>
<path d="M0 {rng.randint(180, 240)} Q 180 {rng.randint(20, 150)} 360 {rng.randint(100, 240)} T 720 {rng.randint(40, 200)}"
 fill="none" stroke="#ffffffaa" stroke-width="5"/>
</svg><figcaption>{rng.choice(ZH)} — {_text(rng, 7)}</figcaption></figure>'''


def _list(rng: random.Random, spec: dict) -> str:
    items = ''.join(f'<li>{_text(rng, rng.randint(6, 12))}</li>' for _ in range(rng.randint(4, 7)))
    return f'<section><h2>Checklist</h2><ul class="notes">{items}</ul></section>'


def _quote(rng: random.Random, spec: dict) -> str:
    return f'<blockquote><p>{_paragraph(rng)}</p><cite>— {rng.choice(WORDS).title()} notebook</cite></blockquote>'


def _details(rng: random.Random, spec: dict) -> str:
    rows = ''.join(
        f'<details><summary>{rng.choice(WORDS).title()}?</summary><p>{_text(rng, 12)}</p></details>'
        for _ in range(rng.randint(2, 4))
    )
    return f'<section><h2>Questions</h2>{rows}</section>'


def _content(rng: random.Random, spec: dict) -> str:
    blocks = [_hero(rng, spec)]
    makers = [_cards, _code, _table, _figure, _list, _quote, _details]
    target = spec['target_factor'] * spec['viewport'][1]
    rough = 320
    while rough < target:
        maker = rng.choice(makers)
        block = maker(rng, spec)
        blocks.append(block)
        rough += {
            _cards: rng.randint(280, 520),
            _code: rng.randint(220, 420),
            _table: rng.randint(260, 480),
            _figure: rng.randint(260, 440),
            _list: rng.randint(180, 360),
            _quote: rng.randint(120, 240),
            _details: rng.randint(160, 340),
        }.get(maker, 280)
    # A final visible block makes sure the page does not end with an accidental
    # two-pixel sliver after the last scroll position.
    blocks.append(
        f'<section class="end"><h2>End notes</h2><p>{_paragraph(rng)}</p>'
        f'<p>{_paragraph(rng)}</p></section>'
    )
    return '\n'.join(blocks)


def build_page(index: int) -> dict:
    rng = random.Random(7100 + index * 37)
    viewport = VIEWPORTS[index % len(VIEWPORTS)]
    vw, vh = viewport
    combo = list(CHROME_COMBOS[index % len(CHROME_COMBOS)])
    if vw < 700:
        combo = [kind for kind in combo if kind not in ('left', 'right')]
        if index % 9 == 0:
            combo = ['top', 'bottom']
        elif not combo:
            combo = ['top']
    combo = tuple(dict.fromkeys(combo))
    bg, fg, muted, panel, accent, code_bg = THEMES[index % len(THEMES)]
    header_h = rng.choice([56, 64, 72, 84, 96, 112]) if 'top' in combo else 0
    footer_h = rng.choice([40, 56, 64, 72]) if 'bottom' in combo else 0
    left_w = rng.choice([148, 176, 208, 232, 264]) if 'left' in combo else 0
    right_w = rng.choice([128, 160, 192, 224]) if 'right' in combo else 0
    if vw - left_w - right_w < 360:
        if right_w:
            right_w = 96
        if left_w:
            left_w = 112
    spec = {
        'index': index,
        'viewport': viewport,
        'chrome': combo,
        'bg': bg,
        'fg': fg,
        'muted': muted,
        'panel': panel,
        'accent': accent,
        'code_bg': code_bg,
        'header_h': header_h,
        'footer_h': footer_h,
        'left_w': left_w,
        'right_w': right_w,
        'header_style': rng.choice(['solid', 'glass', 'gradient', 'plain', 'dark', 'shadow']),
        'font': rng.choice(FONT_STACKS),
        'target_factor': rng.choice([2.2, 2.6, 3.1, 3.8, 4.4, 5.2, 6.3]),
        'step_fraction': rng.choice([0.46, 0.52, 0.58, 0.64, 0.68]),
        'jitter': rng.choice([0, 0, 0.02, 0.04, 0.06]),
    }
    body_padding = f'{header_h}px {right_w}px {footer_h}px {left_w}px'
    # The content rectangle in frame coordinates.  These values are re-measured
    # from the real DOM by the capture helper before evaluation.
    expected = {
        'left': left_w,
        'top': header_h,
        'right': vw - right_w,
        'bottom': vh - footer_h,
        'content_width': vw - left_w - right_w,
        'content_height': vh - header_h - footer_h,
    }
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ScrollWeave corpus {index:03d}</title>
<style>
:root{{--bg:{bg};--fg:{fg};--muted:{muted};--panel:{panel};--accent:{accent}}}
*{{box-sizing:border-box}}
html{{scroll-behavior:auto}}
body{{margin:0;background:var(--bg);color:var(--fg);font-family:{spec['font']};font-size:16px;line-height:1.58;
  padding:{body_padding};overflow-x:hidden}}
a{{color:var(--accent);text-decoration:none}}
.site-header{{position:fixed;z-index:100;display:flex;align-items:center;gap:28px;padding:0 26px;
  box-shadow:inset 0 -1px 0 rgba(0,0,0,.08)}}
@media (max-width:700px){{.site-header nav,.header-note{{display:none}} main{{padding:22px 18px 40px}}
  h1{{font-size:30px}}}}
.site-header .brand{{display:flex;align-items:center;gap:10px;white-space:nowrap}}
.site-header .mark{{display:inline-block;width:18px;height:18px;border-radius:5px;transform:rotate(12deg)}}
.site-header nav{{display:flex;gap:20px;font-size:13px;flex-wrap:wrap}}
.site-header .header-note{{margin-left:auto;font-size:12px;opacity:.72;white-space:nowrap}}
.rail{{position:fixed;z-index:90;background:var(--panel);border-color:var(--muted);
  padding:22px 18px;overflow:hidden}}
.rail-left{{top:{header_h}px;bottom:{footer_h}px;border-right:1px solid rgba(127,127,127,.25)}}
.rail-right{{top:{header_h}px;bottom:{footer_h}px;border-left:1px solid rgba(127,127,127,.25)}}
.rail-title{{font-size:11px;letter-spacing:.14em;text-transform:uppercase;opacity:.6;margin-bottom:16px}}
.rail-item{{padding:9px 10px;border-radius:7px;font-size:13px;opacity:.82}}
.rail-item.active{{background:var(--accent);color:#fff;opacity:1}}
.rail-card{{margin-top:22px;padding:12px;border-radius:9px;background:rgba(127,127,127,.09);font-size:12px}}
.rail ul{{padding-left:18px;margin:0;font-size:13px;line-height:2}}
.site-footer{{position:fixed;z-index:100;display:flex;align-items:center;justify-content:space-between;
  padding:0 26px;font-size:12px;background:var(--panel);border-top:1px solid rgba(127,127,127,.28)}}
main{{max-width:1040px;margin:0 auto;padding:34px 34px 56px}}
.hero{{padding:42px 38px;border-radius:16px;margin:0 0 30px}}
.eyebrow{{font-size:11px;letter-spacing:.18em;color:var(--accent);font-weight:700}}
h1{{font-size:40px;line-height:1.15;margin:8px 0 14px;letter-spacing:-.02em}}
h2{{font-size:25px;margin:38px 0 14px;letter-spacing:-.01em}}
h3{{font-size:17px;margin:0 0 8px}}
p{{margin:0 0 14px}} .hero-meta{{display:flex;gap:20px;font-size:12px;color:var(--muted)}}
section{{margin:0 0 24px}} .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:18px}}
.card{{background:var(--panel);border:1px solid rgba(127,127,127,.2);border-radius:12px;padding:16px;
  box-shadow:0 4px 18px rgba(0,0,0,.06)}}
.card p,.card a{{font-size:13px}} .thumb{{height:120px;border-radius:8px;margin-bottom:13px;
  display:flex;align-items:flex-end;padding:10px;color:#fff;font-weight:700}}
.code{{padding:20px;border-radius:12px;overflow:hidden;color:#d7e2ee;font:13px/1.7 Consolas,monospace;
  white-space:pre-wrap}}
table{{width:100%;border-collapse:collapse;background:var(--panel);border-radius:10px;overflow:hidden}}
th,td{{text-align:left;padding:11px 14px;border-bottom:1px solid rgba(127,127,127,.2);font-size:14px}}
th{{font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}}
figure{{margin:26px 0}} figure svg{{width:100%;height:auto;border-radius:12px;display:block}}
figcaption{{font-size:12px;color:var(--muted);margin-top:8px}}
.notes li{{margin:0 0 8px}} blockquote{{margin:28px 0;padding:10px 26px;border-left:4px solid var(--accent);
  background:var(--panel);border-radius:0 10px 10px 0}} blockquote p{{font-size:18px}}
cite{{font-size:12px;color:var(--muted)}} details{{background:var(--panel);padding:12px 16px;
  border-radius:9px;margin:0 0 10px}} summary{{cursor:pointer;font-weight:600}}
.end{{padding-top:10px;border-top:1px dashed rgba(127,127,127,.25)}}
</style></head>
<body>
{_header(rng, spec) if 'top' in combo else ''}
{_left_rail(rng, spec) if 'left' in combo else ''}
{_right_rail(rng, spec) if 'right' in combo else ''}
{_footer(rng, spec) if 'bottom' in combo else ''}
<main>
{_content(rng, spec)}
</main>
</body></html>"""
    return {'spec': spec, 'expected': expected, 'html': html}


def build_corpus(root: Path, count: int = 120) -> dict:
    root = Path(root)
    pages = []
    for index in range(count):
        built = build_page(index)
        page_dir = root / 'pages' / f'page_{index:03d}'
        page_dir.mkdir(parents=True, exist_ok=True)
        html_path = page_dir / 'index.html'
        html_path.write_text(built['html'], encoding='utf-8')
        spec = built['spec']
        entry = {
            'index': index,
            'id': f'page_{index:03d}',
            'html': str(html_path.relative_to(root).as_posix()),
            'viewport': list(spec['viewport']),
            'chrome': list(spec['chrome']),
            'header_h': spec['header_h'],
            'left_w': spec['left_w'],
            'right_w': spec['right_w'],
            'footer_h': spec['footer_h'],
            'step_fraction': spec['step_fraction'],
            'jitter': spec['jitter'],
            'target_factor': spec['target_factor'],
            'expected': built['expected'],
        }
        pages.append(entry)
    manifest = {
        'schema': 1,
        'generator': 'tools/web_corpus.py',
        'count': count,
        'pages': pages,
    }
    (root / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    return manifest
