# Brainrot Video Generator

Fully automated Reddit-to-YouTube Shorts pipeline. Run one command and it handles everything — scraping the best stories from Reddit, downloading gameplay footage from YouTube, generating AI voiceover with timed captions, composing the final vertical video, and uploading it directly to your channel.

Check the channel: [Threadfeed Shorts](https://www.youtube.com/@Threadfeed/shorts)

---

## How it works

```
Reddit scrape → quality filter → auto-trim → TTS voiceover
→ sentence-timed captions → gameplay download → hook title card
→ ffmpeg compose → YouTube Shorts upload
```

Every step is automatic. The only thing you set up once is your YouTube OAuth credentials.

---

## Full auto mode

The main feature. One command generates and uploads a batch of videos:

```bash
# Generate 3 videos from top subreddits and upload to YouTube Shorts
python overlay_video_generator.py --auto --upload

# Custom subreddits, custom count
python overlay_video_generator.py --auto --subreddits AITA,tifu,confession --count 5 --upload

# Generate locally without uploading
python overlay_video_generator.py --auto --count 3

# Custom gameplay style
python overlay_video_generator.py --auto --gameplay-query "minecraft parkour vertical" --upload
```

What `--auto` does behind the scenes:
1. Scrapes top posts from 5 subreddits (AITA, tifu, confession, offmychest, relationship_advice)
2. Scores every story on hook, drama, clarity, and length — rejects weak ones
3. Ranks passing stories by score, picks the best N
4. Downloads gameplay footage from YouTube via yt-dlp (cached after first run, rotated randomly)
5. Generates TTS audio with sentence-level caption timing
6. Renders a 2.5s hook title card over gameplay
7. Composes the final 9:16 video with burned-in captions and optional background music
8. Uploads to YouTube Shorts with auto-generated title, description, and tags

---

## Setup

### 1. System dependencies
```bash
brew install ffmpeg
pip install yt-dlp
```

### 2. Python dependencies
```bash
pip install -r requirements.txt
```

### 3. YouTube upload (one-time OAuth setup)

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a project → enable **YouTube Data API v3**
3. Create OAuth 2.0 credentials (Desktop app) → download as `client_secrets.json`
4. Place `client_secrets.json` in the project root
5. First `--upload` run opens a browser for consent — token is cached after that

### 4. Optional: smarter story scoring
```bash
export OPENAI_API_KEY=sk-...   # uses GPT-4o-mini for scoring instead of rules-based
```

---

## All commands

```bash
# ── Full auto ──────────────────────────────────────────────────────
python overlay_video_generator.py --auto
python overlay_video_generator.py --auto --upload
python overlay_video_generator.py --auto --subreddits AITA,tifu --count 5 --upload

# ── Single story ───────────────────────────────────────────────────
python overlay_video_generator.py --story input/stories/story.txt
python overlay_video_generator.py --reddit https://reddit.com/r/AITA/comments/...
python overlay_video_generator.py --paste

# ── Scrape one subreddit ───────────────────────────────────────────
python overlay_video_generator.py --scrape AmItheAsshole --count 10

# ── Batch from folder ──────────────────────────────────────────────
python overlay_video_generator.py --batch input/stories/ --count 20

# ── Options ────────────────────────────────────────────────────────
--gameplay-query "subway surfers vertical"   # custom gameplay search
--music assets/music/lofi.mp3               # background music
--music-vol 0.06                            # music volume (default 0.08)
--voice en-US-GuyNeural                     # different TTS voice
--max-dur 60                                # cap video at 60s
--no-hook                                   # skip title card
--force                                     # ignore quality filter
--no-eval                                   # skip scoring entirely
--list-voices                               # show all available voices
```

---

## Story quality filter

Every scraped story is automatically scored before a video is generated:

| Dimension | What it measures |
|---|---|
| Hook | Does the first sentence grab immediately? |
| Drama | Conflict, tension, emotional stakes |
| Clarity | Clean to narrate aloud — no heavy Reddit jargon |
| Length | 80–350 words = ideal 30–90s video |

Stories below **6.5/10** are skipped. The top-scoring stories are generated first.
Set `OPENAI_API_KEY` for GPT-4o-mini scoring; otherwise uses the built-in rule engine.

---

## Folder structure

```
brainrot-generator/
├── overlay_video_generator.py   ← entire pipeline in one file
├── client_secrets.json          ← YouTube OAuth (you add this)
├── requirements.txt
├── assets/
│   ├── gameplay/                ← auto-downloaded and cached here
│   └── music/                   ← drop .mp3 for background music
├── input/
│   └── stories/                 ← manual .txt / .json inputs
├── output/
│   ├── videos/                  ← final MP4s
│   ├── audio/                   ← narration files
│   └── subtitles/               ← .ass caption files
└── temp/
```

---

## Requirements

- Python 3.11+
- ffmpeg (`brew install ffmpeg`)
- yt-dlp (`pip install yt-dlp`)
- Internet connection (Reddit JSON API + YouTube search, no API keys needed for scraping)
