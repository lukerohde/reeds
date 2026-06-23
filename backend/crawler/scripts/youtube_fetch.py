"""
Fetch recent videos for each configured channel and print them.
Used to verify YOUTUBE_API_KEY and channel IDs work before a full crawl.
No DynamoDB writes.

Usage:
    make test-youtube-fetch
"""
import os
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

import yaml
from googleapiclient.discovery import build

from sources import get_recent_videos

_cfg          = yaml.safe_load((Path(__file__).parent.parent / 'config.yaml').read_text())
YOUTUBERS     = _cfg.get('youtubers', [])
LOOKBACK_DAYS = _cfg['settings'].get('youtube_lookback_days', 7)
MAX_PER_CHAN  = _cfg['settings'].get('max_videos_per_channel', 3)

if not YOUTUBERS:
    print('No youtubers configured in config/config.yaml')
    sys.exit(0)

youtube = build('youtube', 'v3', developerKey=os.environ['YOUTUBE_API_KEY'])
since   = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)

print(f'Looking back {LOOKBACK_DAYS} days (since {since.strftime("%Y-%m-%d")}).\n')

any_found = False
for channel in YOUTUBERS:
    name = channel['name']
    cid  = channel['channel_id']
    print(f'--- {name} ({cid})')
    try:
        videos = get_recent_videos(youtube, cid, since, MAX_PER_CHAN)
    except Exception as e:
        print(f'  ERROR: {e}')
        continue
    if not videos:
        print('  (no videos in lookback window)')
    for v in videos:
        any_found = True
        print(f'  {v["published_date"][:10]}  {v["title"]}')
        print(f'    {v["url"]}')
    print()

if not any_found:
    print('No videos found. Try increasing youtube_lookback_days in config/config.yaml.')
