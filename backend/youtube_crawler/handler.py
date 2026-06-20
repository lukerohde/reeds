import os
import yaml
import boto3
from pathlib import Path
from datetime import datetime, timezone, timedelta
from googleapiclient.discovery import build
from youtube_transcript_api import YouTubeTranscriptApi

_cfg = yaml.safe_load((Path(__file__).parent / 'config.yaml').read_text())

YOUTUBE_API_KEY        = os.environ['YOUTUBE_API_KEY']
TABLE_NAME             = os.environ['DYNAMODB_TABLE']
YOUTUBERS              = _cfg.get('youtubers', [])
LOOKBACK_DAYS          = _cfg['settings'].get('youtube_lookback_days', 7)
MAX_VIDEOS_PER_CHANNEL = _cfg['settings'].get('max_videos_per_channel', 3)
CONTENT_LIMIT          = _cfg['settings'].get('content_limit', 8000)

dynamodb = boto3.resource('dynamodb')
table    = dynamodb.Table(TABLE_NAME)


def get_recent_videos(youtube, channel_id, since):
    """Fetch recent videos from a channel's uploads playlist.

    The uploads playlist ID is derived by replacing the 'UC' prefix of a
    channel ID with 'UU'. This is a well-known YouTube convention.
    """
    uploads_playlist = 'UU' + channel_id[2:]
    response = youtube.playlistItems().list(
        part='snippet',
        playlistId=uploads_playlist,
        maxResults=50,
    ).execute()

    videos = []
    for item in response.get('items', []):
        snippet       = item['snippet']
        published_str = snippet['publishedAt']
        published_dt  = datetime.fromisoformat(published_str.rstrip('Z')).replace(tzinfo=timezone.utc)
        if published_dt < since:
            break
        video_id = snippet['resourceId']['videoId']
        videos.append({
            'video_id':       video_id,
            'url':            f'https://www.youtube.com/watch?v={video_id}',
            'title':          snippet['title'],
            'published_date': published_str,
        })
        if len(videos) >= MAX_VIDEOS_PER_CHANNEL:
            break
    return videos


def get_transcript(video_id):
    """Fetch captions; return joined text or '' if unavailable.

    Tries fetch() first (fastest path). Falls back to list() if that returns
    empty — handles videos where the default language lookup fails but captions
    exist under an explicit language code. Logs errors so failures are diagnosable.
    """
    try:
        snippets = YouTubeTranscriptApi().fetch(video_id)
        text = ' '.join(s.text for s in snippets)
        if text.strip():
            return text
    except Exception as e:
        print(f'  [transcript] fetch() error for {video_id}: {type(e).__name__}: {e}')
    try:
        for transcript in YouTubeTranscriptApi().list(video_id):
            snippets = transcript.fetch()
            text = ' '.join(s.text for s in snippets)
            if text.strip():
                return text
    except Exception as e:
        print(f'  [transcript] list() error for {video_id}: {type(e).__name__}: {e}')
    return ''


def handler(event, context):
    if not YOUTUBERS:
        print('[youtube_crawler] No youtubers configured — done')
        return {'stored': 0}

    youtube      = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
    since        = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    fetched_date = datetime.now(timezone.utc).isoformat()
    stored       = 0

    for channel in YOUTUBERS:
        name       = channel['name']
        channel_id = channel['channel_id']
        print(f'[youtube_crawler] {name} ({channel_id})')

        try:
            videos = get_recent_videos(youtube, channel_id, since)
        except Exception as e:
            print(f'  [error] {name}: {e}')
            continue

        for v in videos:
            existing = table.get_item(Key={'url': v['url']}).get('Item')
            if existing:
                # Retry transcript for unserved videos that were stored without one.
                if existing.get('served_date') == '' and not existing.get('content', '').strip():
                    transcript = get_transcript(v['video_id'])
                    if transcript:
                        table.update_item(
                            Key={'url': v['url']},
                            UpdateExpression='SET content = :c, word_count = :w, #s = :st, summary = :sm',
                            ExpressionAttributeNames={'#s': 'status'},
                            ExpressionAttributeValues={
                                ':c':  transcript[:CONTENT_LIMIT],
                                ':w':  len(transcript.split()),
                                ':st': '',   # clear so digest re-processes
                                ':sm': '',
                            },
                        )
                        print(f'  [transcript] {v["title"]} — {len(transcript.split())} words (retry)')
                    else:
                        print(f'  [skip]   {v["title"]} (no transcript)')
                else:
                    print(f'  [skip]   {v["title"]}')
                continue
            transcript = get_transcript(v['video_id'])
            wc = len(transcript.split()) if transcript else 0
            table.put_item(Item={
                'url':            v['url'],
                'author':         name,
                'title':          v['title'],
                'published_date': v['published_date'],
                'fetched_date':   fetched_date,
                'served_date':    '',
                'source':         'youtube',
                'video_id':       v['video_id'],
                'content':        transcript[:CONTENT_LIMIT] if transcript else '',
                'word_count':     wc,
            })
            status = f'{wc} words' if transcript else 'no transcript — will retry next crawl'
            print(f'  [stored] {v["title"]} ({status})')
            stored += 1

    return {'stored': stored}
