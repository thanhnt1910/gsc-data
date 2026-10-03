#!/usr/bin/env python3
"""Referring domains for a list of sites, from the Common Crawl web graph.

Common Crawl publishes a free domain-level link graph a few times a year
(https://commoncrawl.org/web-graphs). This script streams it and writes,
for every domain in TARGETS:

  data/backlinks/<release>/summary.csv    target, referring domains, rank
  data/backlinks/<release>/referring.csv  target, referring domain, rank
  data/backlinks/<release>/gap.csv        domains linking to 2+ targets,
                                          with whether they link to us

"rank" is the domain's harmonic-centrality position in the same release
(1 = most central on the web); lower is stronger. The graph counts a link
once per domain pair, with no anchors or nofollow flag, and only covers
pages Common Crawl crawled, so counts are lower than commercial tools.

Env: RELEASE (e.g. cc-main-2026-jun-jul-aug; default: newest that exists),
TARGETS (comma-separated domains), OURS (our domain for the gap report).
Needs curl, gzip and mawk/awk on PATH. The edges file is several GB, so it
is streamed, never stored.
"""

import csv
import gzip
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

BASE = 'https://data.commoncrawl.org/projects/hyperlinkgraph'
CANDIDATE_RELEASES = [
    'cc-main-2026-jun-jul-aug',
    'cc-main-2026-apr-may-jun',
    'cc-main-2026-mar-apr-may',
    'cc-main-2025-dec-2026-jan-feb',
]
DEFAULT_TARGETS = [
    'mathlogicgames.com',
    'monkeymart.one', 'game-game.com', 'playagame.io', 'games144.com',
    'gamesonline.org', 'jopi.com', 'zapgames.io', 'allwebgames.com',
    'wellgames.com', 'gamaverse.com', 'plays.org',
    'poki.com', 'crazygames.com',
]
# Too many referring domains to list usefully; summary row only.
SUMMARY_ONLY = {'poki.com', 'crazygames.com'}
OUT_ROOT = Path(__file__).resolve().parent.parent / 'data' / 'backlinks'
WORK = Path(os.environ.get('RUNNER_TEMP', '/tmp')) / 'ccgraph'


def rev(domain: str) -> str:
    return '.'.join(reversed(domain.strip().lower().split('.')))


def fetch_text(url: str) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return r.read().decode('utf-8', 'replace')
    except Exception as e:  # noqa: BLE001 - any failure means "not there"
        print(f'  {url}: {e}')
        return None


def domain_files(release: str) -> dict[str, list[str]] | None:
    """URLs of the domain-level vertices, edges and ranks files."""
    index = fetch_text(f'{BASE}/{release}/index.html')
    if index is None:
        return None
    links = set(re.findall(r'href="([^"]+\.txt\.gz)"', index))
    links = {l if l.startswith('http') else f'{BASE}/{release}/{l.lstrip("./")}' for l in links}
    files = {kind: sorted(l for l in links if '/domain/' in l and f'domain-{kind}' in l)
             for kind in ('vertices', 'edges', 'ranks')}
    if not all(files.values()):
        # Fall back to the naming used by past releases.
        stem = f'{BASE}/{release}/domain/{release}-domain'
        files = {kind: [f'{stem}-{kind}.txt.gz'] for kind in ('vertices', 'edges', 'ranks')}
    return files


def download(url: str, dest: Path) -> None:
    subprocess.run(['curl', '-sSfL', '--retry', '5', '-C', '-', '-o', str(dest), url], check=True)


def read_vertices(path: Path, keep) -> dict[str, str]:
    """id -> reversed domain, for lines where keep(id, name) is true."""
    out = {}
    with gzip.open(path, 'rt', encoding='utf-8', errors='replace') as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 2 and keep(parts[0], parts[1]):
                out[parts[0]] = parts[1]
    return out


def stream_edges(urls: list[str], ids_file: Path, out_file: Path) -> None:
    awk = shutil.which('mawk') or 'awk'
    script = 'NR==FNR{t[$1];next} ($2 in t){print} END{print FNR " edges read" > "/dev/stderr"}'
    with out_file.open('w') as out:
        for url in urls:
            print(f'Streaming {url}', flush=True)
            cmd = f'set -o pipefail; curl -sSfL --retry 5 "{url}" | gzip -dc | {awk} \'{script}\' "{ids_file}" -'
            subprocess.run(['bash', '-c', cmd], stdout=out, check=True)


def read_ranks(urls: list[str], wanted: set[str]) -> dict[str, int]:
    ranks = {}
    for url in urls:
        print(f'Streaming {url}', flush=True)
        proc = subprocess.Popen(['bash', '-c', f'set -o pipefail; curl -sSfL --retry 5 "{url}" | gzip -dc'],
                                stdout=subprocess.PIPE, text=True)
        cols = None
        for line in proc.stdout:
            if line.startswith('#'):
                cols = [c.lstrip('#') for c in line.split()]
                continue
            parts = line.split()
            if cols is None:  # no header: harmonicc_pos is first, name is fifth
                pos, name = parts[0], parts[4]
            else:
                pos, name = parts[cols.index('harmonicc_pos')], parts[cols.index('host_rev')]
            if name in wanted:
                ranks[name] = int(pos)
        if proc.wait() != 0:
            sys.exit(f'Failed to read {url}')
    return ranks


def main() -> None:
    targets = [t.strip().lower() for t in (os.environ.get('TARGETS') or ','.join(DEFAULT_TARGETS)).split(',') if t.strip()]
    ours = (os.environ.get('OURS') or 'mathlogicgames.com').strip().lower()
    releases = [os.environ['RELEASE']] if os.environ.get('RELEASE') else CANDIDATE_RELEASES

    release = files = None
    for rel in releases:
        files = domain_files(rel)
        if files:
            release = rel
            break
    if not release:
        sys.exit(f'No web graph release found among {releases}')
    print(f'Release: {release}')
    for kind, urls in files.items():
        print(f'  {kind}: {urls}')

    WORK.mkdir(parents=True, exist_ok=True)
    vert_paths = []
    for i, url in enumerate(files['vertices']):
        dest = WORK / f'vertices-{i}.txt.gz'
        print(f'Downloading {url}', flush=True)
        download(url, dest)
        vert_paths.append(dest)

    wanted_rev = {rev(t): t for t in targets}
    target_ids = {}
    for p in vert_paths:
        target_ids.update(read_vertices(p, lambda _id, name: name in wanted_rev))
    missing = set(wanted_rev) - set(target_ids.values())
    for m in sorted(missing):
        print(f'  not in graph: {wanted_rev[m]}')
    if not target_ids:
        sys.exit('None of the targets are in this graph.')

    ids_file = WORK / 'target-ids.txt'
    ids_file.write_text(''.join(f'{i}\n' for i in target_ids))
    edges_file = WORK / 'edges-to-targets.tsv'
    stream_edges(files['edges'], ids_file, edges_file)

    links = {}  # target domain -> set of referring ids
    with edges_file.open() as f:
        for line in f:
            src, dst = line.split()[:2]
            if src != dst:
                links.setdefault(wanted_rev[target_ids[dst]], set()).add(src)
    all_src = set().union(*links.values()) if links else set()
    print(f'{len(all_src)} referring domains in total')

    names = {}
    for p in vert_paths:
        names.update(read_vertices(p, lambda _id, _name: _id in all_src))
    ranks = read_ranks(files['ranks'], set(names.values()) | set(wanted_rev))

    def dom(rev_name: str) -> str:
        return rev(rev_name)

    out = OUT_ROOT / release
    out.mkdir(parents=True, exist_ok=True)

    def kept_rows(name: str) -> list[list[str]]:
        # A run for a few TARGETS must not wipe earlier rows for other targets in this release.
        path = out / name
        if not path.exists():
            return []
        with path.open(newline='') as f:
            return [r for r in list(csv.reader(f))[1:] if r and r[0] not in targets]

    old_summary, old_referring = kept_rows('summary.csv'), kept_rows('referring.csv')
    with (out / 'summary.csv').open('w', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(['target', 'referring_domains', 'harmonic_rank'])
        for t in targets:
            w.writerow([t, len(links.get(t, ())), ranks.get(rev(t), '')])
        w.writerows(old_summary)

    with (out / 'referring.csv').open('w', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(['target', 'referring_domain', 'harmonic_rank'])
        for t in targets:
            if t in SUMMARY_ONLY:
                continue
            rows = [(dom(names[s]), ranks.get(names[s], 10**9)) for s in links.get(t, ()) if s in names]
            for d, r in sorted(rows, key=lambda x: (x[1], x[0])):
                w.writerow([t, d, r if r < 10**9 else ''])
        w.writerows(old_referring)

    competitors = [t for t in targets if t != ours and t not in SUMMARY_ONLY]
    ours_links = links.get(ours, set())
    counts = {}
    for t in competitors:
        for s in links.get(t, ()):
            counts.setdefault(s, []).append(t)
    gap_name = 'gap-custom.csv' if os.environ.get('TARGETS') else 'gap.csv'
    with (out / gap_name).open('w', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(['referring_domain', 'competitors_linked', 'links_to_us', 'harmonic_rank', 'competitors'])
        rows = [(s, c) for s, c in counts.items() if len(c) >= 2 and s in names]
        rows.sort(key=lambda x: (-len(x[1]), ranks.get(names[x[0]], 10**9)))
        for s, c in rows:
            r = ranks.get(names[s], '')
            w.writerow([dom(names[s]), len(c), 'yes' if s in ours_links else 'no', r, ' '.join(sorted(c))])

    print(f'\n## Summary ({release})')
    print((out / 'summary.csv').read_text())
    print('## Top of gap.csv')
    print(''.join((out / gap_name).read_text().splitlines(keepends=True)[:60]))


if __name__ == '__main__':
    main()
