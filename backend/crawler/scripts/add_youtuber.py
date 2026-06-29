"""
Resolve a YouTube handle (or channel URL) to its UC… channel ID and add it to
config/config.yaml under `youtubers`. No API key needed — the ID is read from
the channel page's HTML, which the YouTube UI no longer exposes directly.

Usage:
    make add-youtuber HANDLE=@buildwithdc
    make add-youtuber HANDLE=https://www.youtube.com/@buildwithdc/featured

Idempotent: a channel already in the config is reported and skipped.
"""
import re
import sys
from pathlib import Path

import requests

# Repo config (writable), not the read-only copy bind-mounted next to the handler.
# __file__ = backend/crawler/scripts/add_youtuber.py → parents[3] is the repo root.
CONFIG = Path(__file__).resolve().parents[3] / 'config' / 'config.yaml'

_CHANNEL_ID = re.compile(r'"(?:channelId|externalId)":"(UC[A-Za-z0-9_-]{22})"')
_OG_TITLE   = re.compile(r'<meta\s+property="og:title"\s+content="([^"]*)"')
_UC_DIRECT  = re.compile(r'(UC[A-Za-z0-9_-]{22})')


def _normalise(arg):
    """Turn whatever the user pasted into a fetchable channel URL."""
    arg = arg.strip()
    if _UC_DIRECT.fullmatch(arg):
        return f'https://www.youtube.com/channel/{arg}'
    if arg.startswith('http'):
        return arg
    if arg.startswith('@'):
        return f'https://www.youtube.com/{arg}'
    return f'https://www.youtube.com/@{arg}'


def resolve(arg):
    """Return (name, channel_id) for a handle/URL, or raise ValueError."""
    url  = _normalise(arg)
    html = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20).text
    m = _CHANNEL_ID.search(html)
    if not m:
        raise ValueError(f'could not find a channel ID in {url}')
    channel_id = m.group(1)
    title = _OG_TITLE.search(html)
    name  = title.group(1).removesuffix(' - YouTube').strip() if title else arg
    return name, channel_id


def _append(name, channel_id):
    """Insert a youtubers entry at the end of that block, preserving formatting.

    Returns False if the channel ID is already present."""
    lines = CONFIG.read_text().split('\n')
    if any(channel_id in line for line in lines):
        return False

    start = next(i for i, l in enumerate(lines) if l.strip() == 'youtubers:')
    end   = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i] and not lines[i][0].isspace():   # next top-level key
            end = i
            break
    while end > start + 1 and not lines[end - 1].strip():   # back over blank lines
        end -= 1

    safe = f'"{name}"' if any(c in name for c in ':#') else name
    lines[end:end] = [f'  - name: {safe}', f'    channel_id: {channel_id}']
    CONFIG.write_text('\n'.join(lines))
    return True


def main(args):
    if not args:
        print('Usage: make add-youtuber HANDLE=@handle  (or a channel URL / UC… ID)')
        return 1
    for arg in args:
        try:
            name, channel_id = resolve(arg)
        except Exception as e:
            print(f'  ✗  {arg}: {e}')
            continue
        if _append(name, channel_id):
            print(f'  ✓  added {name} → {channel_id}')
        else:
            print(f'  •  {name} → {channel_id} already configured')
    print('\nVerify with: make test-youtube-fetch')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
