"""Crawl arbitrary web pages and run the same pixel-truth smoke evaluation.

Unlike ``web_smoke.py`` (the deterministic local regression corpus), this tool
accepts real HTTP(S) or local file URLs.  For each page it:

1. warms up the page by scrolling to the bottom (lazy images/content),
2. detects fixed/sticky elements attached to a viewport edge from computed CSS,
3. captures scrolling viewport frames plus a full-page truth image with those
   edge elements hidden,
4. evaluates automatic reconstruction and, optionally, known geometry.

Real sites can contain animations, ads, consent overlays and lazy widgets that
do not have a stable document-space truth.  Treat this as a diagnostic smoke
suite, not as a claim that every production site should pass pixel-exactly.

Usage::

    python tools/web_crawl.py https://example.com https://example.org \
        --root output/web_crawl --viewport 1280x800 --explicit

    python tools/web_crawl.py --urls-file urls.txt --max-pages 40 \
        --root output/web_crawl
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != 'a':
            return
        for name, value in attrs:
            if name.lower() == 'href' and value:
                self.links.append(value)


def _discover(seeds: list[str], max_pages: int, depth: int) -> list[str]:
    """Breadth-first same-origin link discovery using the standard library."""
    queue = [(url, 0) for url in seeds]
    seen: set[str] = set()
    discovered: list[str] = []
    allowed_origins = {urlparse(seed).netloc for seed in seeds}
    while queue and len(discovered) < max_pages:
        url, level = queue.pop(0)
        url = urldefrag(url).url
        if url in seen or not url.startswith(('http://', 'https://')):
            continue
        seen.add(url)
        if urlparse(url).netloc not in allowed_origins:
            continue
        try:
            request = Request(url, headers={'User-Agent': 'ScrollWeave smoke crawler/1.0'})
            with urlopen(request, timeout=15) as response:
                if 'text/html' not in response.headers.get_content_type():
                    continue
                body = response.read(4_000_000)
                final = response.geturl()
        except Exception:
            continue
        discovered.append(final)
        if level >= depth:
            continue
        parser = _Links()
        try:
            parser.feed(body.decode('utf-8', errors='replace'))
        except Exception:
            continue
        for href in parser.links:
            candidate = urldefrag(urljoin(final, href)).url
            if candidate.startswith(('http://', 'https://')):
                queue.append((candidate, level + 1))
    return discovered


def _parse_viewport(text: str) -> tuple[int, int]:
    match = re.fullmatch(r'(\d+)[x:,](\d+)', text.strip())
    if not match:
        raise SystemExit(f'invalid viewport {text!r}; expected WIDTHxHEIGHT')
    width, height = int(match.group(1)), int(match.group(2))
    if not (240 <= width <= 4096 and 240 <= height <= 4096):
        raise SystemExit('viewport must be between 240 and 4096 pixels per side')
    return width, height


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('urls', nargs='*', help='HTTP(S) or file:// URLs')
    parser.add_argument('--urls-file', type=Path)
    parser.add_argument('--root', type=Path, default=ROOT / 'output' / 'web_crawl')
    parser.add_argument('--viewport', default='1280x800')
    parser.add_argument('--max-pages', type=int, default=30)
    parser.add_argument('--discover-depth', type=int, default=0)
    parser.add_argument('--wait-ms', type=int, default=700)
    parser.add_argument('--step-fraction', type=float, default=0.55)
    parser.add_argument('--jitter', type=float, default=0.02)
    parser.add_argument('--no-capture', action='store_true')
    parser.add_argument('--explicit', action='store_true')
    args = parser.parse_args(argv)

    for path in (str(ROOT), str(ROOT / 'tools')):
        if path not in sys.path:
            sys.path.insert(0, path)
    from web_smoke import _evaluate_worker, write_report

    urls = list(args.urls)
    if args.urls_file:
        urls.extend(
            line.strip()
            for line in args.urls_file.read_text(encoding='utf-8').splitlines()
            if line.strip() and not line.lstrip().startswith('#')
        )
    if not urls:
        parser.error('supply at least one URL or --urls-file')
    if args.discover_depth > 0:
        urls = _discover(urls, args.max_pages, args.discover_depth)
    if not urls:
        raise SystemExit('no URLs found')
    urls = urls[: args.max_pages]
    viewport = _parse_viewport(args.viewport)
    root = args.root
    root.mkdir(parents=True, exist_ok=True)
    pages = []
    for index, url in enumerate(urls):
        pages.append(
            {
                'id': f'site_{index:03d}',
                'index': index,
                'url': url,
                'viewport': list(viewport),
                'step_fraction': args.step_fraction,
                'jitter': args.jitter,
                'wait_ms': args.wait_ms,
            }
        )
    manifest = {'schema': 1, 'count': len(pages), 'pages': pages}
    (root / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    if not args.no_capture:
        job = {
            'root': str(root.resolve()),
            'pages': [
                {
                    'id': page['id'],
                    'url': page['url'],
                    'viewport': page['viewport'],
                    'step_fraction': page['step_fraction'],
                    'jitter': page['jitter'],
                    'wait_ms': page['wait_ms'],
                    'index': page['index'],
                }
                for page in pages
            ],
        }
        job_path = root / 'capture_jobs.json'
        job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding='utf-8')
        completed = subprocess.run(
            ['node', str(ROOT / 'tools' / 'web_capture.mjs'), str(job_path)],
            cwd=ROOT,
            env=dict(os.environ),
        )
        if completed.returncode != 0:
            raise SystemExit('browser capture failed')

    records = []
    started = time.perf_counter()
    for index, page in enumerate(pages, 1):
        print(f'[crawl] {index}/{len(pages)} {page["url"]}', flush=True)
        capture_path = root / 'pages' / page['id'] / 'capture' / 'capture.json'
        if not capture_path.is_file():
            records.append(
                {
                    'id': page['id'],
                    'url': page['url'],
                    'chrome': [],
                    'viewport': page['viewport'],
                    'error': 'capture.json missing',
                    'auto': {'pass': False, 'metrics': {}},
                }
            )
            continue
        capture = json.loads(capture_path.read_text(encoding='utf-8'))
        kinds = []
        for rect in capture.get('rects', []):
            if rect.get('kind') and rect['kind'] not in kinds:
                kinds.append(rect['kind'])
        record_page = {
            'id': page['id'],
            'chrome': kinds,
            'viewport': list(capture.get('inner') or page['viewport']),
            'expected': {
                'left': 0,
                'top': 0,
                'right': page['viewport'][0],
                'bottom': page['viewport'][1],
            },
        }
        record = _evaluate_worker((str(root), record_page, args.explicit))
        record['url'] = page['url']
        records.append(record)
    elapsed = time.perf_counter() - started
    known_evaluated = sum(1 for record in records if 'known' in record)
    summary = {
        'schema': 1,
        'count': len(records),
        'corpus_count': len(pages),
        'seconds': elapsed,
        'auto_pass': sum(1 for record in records if record.get('auto', {}).get('pass')),
        'known_evaluated': known_evaluated,
        'known_pass': sum(1 for record in records if record.get('known', {}).get('pass')),
        'known_failures': [
            record for record in records if 'known' in record and not record['known']['pass']
        ],
        'pages': records,
    }
    (root / 'summary.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    write_report(root, summary)
    print(
        f'CRAWL auto {summary["auto_pass"]}/{summary["count"]}'
        + (f', known {summary["known_pass"]}/{known_evaluated}' if known_evaluated else '')
    )
    return 0 if summary['auto_pass'] == summary['count'] else 1


if __name__ == '__main__':
    sys.exit(main())
