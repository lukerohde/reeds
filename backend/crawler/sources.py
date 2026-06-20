"""
Pluggable content sources for the reeds crawler.

A Source knows how to do two things:

    discover()            → list candidate item dicts (url, author, title, ...)
    fetch_content(item)   → the item's full text ('' if unavailable)

Everything else — dedup, the DynamoDB item schema, content truncation,
word counts, the store/retry loop — lives in the crawler handler and is
identical for every source. Add a new source by writing one small class.
"""
import os
from datetime import datetime, timezone, timedelta

import feedparser
import requests
from bs4 import BeautifulSoup
from googleapiclient.discovery import build
from youtube_transcript_api import YouTubeTranscriptApi


# ── Blog helpers ──────────────────────────────────────────────────────────────

def parse_feed(blog):
    """Parse an RSS/Atom feed and return article metadata items."""
    feed = feedparser.parse(blog['feed'])
    items = []
    for entry in feed.entries:
        url = entry.get('link', '')
        if not url:
            continue
        published = entry.get('published_parsed') or entry.get('updated_parsed')
        published_date = (
            datetime(*published[:6], tzinfo=timezone.utc).isoformat()
            if published
            else datetime.now(timezone.utc).isoformat()
        )
        items.append({
            'url':            url,
            'author':         blog['author'],
            'title':          entry.get('title', 'Untitled'),
            'published_date': published_date,
        })
    return items


def fetch_article(url):
    """Fetch and clean article text. Returns text ('' on failure)."""
    try:
        r = requests.get(url, timeout=10, headers={'User-Agent': 'reeds-digest/1.0'})
        soup = BeautifulSoup(r.text, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
            tag.decompose()
        return soup.get_text(separator=' ', strip=True)
    except Exception:
        return ''


# ── YouTube helpers ───────────────────────────────────────────────────────────

def get_recent_videos(youtube, channel_id, since, max_videos):
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
        if len(videos) >= max_videos:
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


# ── Sources ───────────────────────────────────────────────────────────────────

class Source:
    """A content source. Subclasses implement discover() and fetch_content()."""

    name = 'source'

    def discover(self):
        """Return a list of item dicts (url, author, title, published_date, ...)."""
        raise NotImplementedError

    def fetch_content(self, item):
        """Return the full text for an item ('' if unavailable)."""
        raise NotImplementedError


class BlogSource(Source):
    name = 'blog'

    def __init__(self, blogs):
        self.blogs = blogs

    def discover(self):
        items = []
        for blog in self.blogs:
            items.extend(parse_feed(blog))
        return items

    def fetch_content(self, item):
        return fetch_article(item['url'])


class YouTubeSource(Source):
    name = 'youtube'

    def __init__(self, youtubers, api_key, lookback_days, max_per_channel):
        self.youtubers       = youtubers
        self.api_key         = api_key
        self.lookback_days   = lookback_days
        self.max_per_channel = max_per_channel

    def discover(self):
        if not self.youtubers or not self.api_key:
            return []
        youtube = build('youtube', 'v3', developerKey=self.api_key)
        since   = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
        items   = []
        for channel in self.youtubers:
            try:
                videos = get_recent_videos(youtube, channel['channel_id'], since, self.max_per_channel)
            except Exception as e:
                print(f"  [error] {channel['name']}: {e}")
                continue
            for v in videos:
                items.append({
                    'url':            v['url'],
                    'author':         channel['name'],
                    'title':          v['title'],
                    'published_date': v['published_date'],
                    'source':         'youtube',
                    'video_id':       v['video_id'],
                })
        return items

    def fetch_content(self, item):
        return get_transcript(item['video_id'])
