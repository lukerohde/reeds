"""
Set up two fake digest pages for local infinite scroll testing.
Run via: make dev-scroll-test
"""
import re
from pathlib import Path

preview = Path('/tmp/reeds-digest-preview.html').read_text()

root   = Path('/tmp/scroll-test')
latest = root / 'digest/latest'
prev   = root / 'digest/2026-06-23'
latest.mkdir(parents=True, exist_ok=True)
prev.mkdir(parents=True, exist_ok=True)

# Page 1: today's digest, sentinel points to prev date
p1 = preview.replace(
    '<div class="load-more" data-prev="">',
    '<div class="load-more" data-prev="/digest/2026-06-23/">'
)
(latest / 'index.html').write_text(p1)

# Page 2: previous day — swap heading, no further prev
p2 = re.sub(r'Daily Digest — \S+', 'Daily Digest — 2026-06-23', preview)
(prev / 'index.html').write_text(p2)

print('Test pages written.')
print('Open: http://localhost:8080/digest/latest/')
print('Scroll to bottom — the 2026-06-23 digest should load inline.')
