# Brainrot Video Generator — Overlay Mode

Turns Reddit stories into vertical short-form videos (TikTok / Reels / Shorts).
Gameplay fills the full screen, story is narrated via AI voice, captions appear on top (also automatically uploads to your youtube account).
Check the vids out here - https://www.youtube.com/@Threadfeed/shorts 

## Pipeline

```
story input → quality eval → TTS → word-timed captions → gameplay → ffmpeg compose → MP4
```

## Setup

### 1. Install system deps
```bash
brew install ffmpeg
```

### 2. Install Python deps
```bash
pip install -r requirements.txt
```

### 3. Add gameplay footage
Drop a vertical (or any) `.mp4` into `assets/gameplay/`. Subway Surfers, Minecraft parkour, etc.
The generator will scale + crop it to 1080×1920 automatically.

### 4. (Optional) Set API keys
```bash
# For LLM-based story scoring (falls back to rules-based if not set)
export OPENAI_API_KEY=sk-...

# For scraping Reddit directly
export REDDIT_CLIENT_ID=...
export REDDIT_CLIENT_SECRET=...
```

---

## Usage

```bash
# From a text file
python overlay_video_generator.py --story input/stories/my_story.txt

# From a JSON file
python overlay_video_generator.py --story input/stories/story.json

# Paste directly in terminal
python overlay_video_generator.py --paste

# From a Reddit URL
python overlay_video_generator.py --reddit https://reddit.com/r/AmItheAsshole/comments/...

# Specify gameplay clip
python overlay_video_generator.py --story story.txt --gameplay assets/gameplay/subway.mp4

# Skip quality check
python overlay_video_generator.py --story story.txt --no-eval

# Force generate even if story scores low
python overlay_video_generator.py --story story.txt --force

# List available voices
python overlay_video_generator.py --list-voices
```

---

## Story input formats

**Plain text** (`.txt`) — just the story body, filename becomes the title.

**JSON** (`.json`):
```json
{
  "title": "My roommate stole my food",
  "text": "I found out my roommate...",
  "subreddit": "AmItheAsshole",
  "url": "https://reddit.com/..."
}
```

---

## Story quality scoring

Before generating, every story is scored on:

| Dimension | What it checks |
|---|---|
| Hook | Is the first sentence immediately grabbing? |
| Drama | Conflict, emotional stakes, tension keywords |
| Clarity | Readable aloud? No heavy Reddit jargon? |
| Length | 80–350 words = ideal for 30–90s content |

Stories scoring below **6.5/10** are rejected by default. Use `--force` to override.
Set `OPENAI_API_KEY` for smarter LLM-based scoring; otherwise falls back to rule-based.

---

## Output

Files land in:
```
output/
  videos/     → final .mp4 ready to post
  audio/      → narration .mp3
  subtitles/  → .ass subtitle file
```

---

## Folder structure

```
brainrot-generator/
├── overlay_video_generator.py   ← all core logic lives here
├── requirements.txt
├── assets/
│   ├── gameplay/                ← drop .mp4 clips here
│   └── fonts/                   ← optional custom fonts
├── input/
│   └── stories/                 ← .txt or .json story files
├── output/
│   ├── videos/
│   ├── audio/
│   └── subtitles/
└── temp/
```

---

## Extending later

| Feature | Where to add |
|---|---|
| Reddit batch scraper | Add `scrape_subreddit()` to story input section |
| Word-level captions | `build_caption_chunks()` already uses word boundaries — shrink `max_words` |
| Background music | Add `-i music.mp3 -filter_complex amix` to ffmpeg cmd in `compose_overlay_video()` |
| Split-screen mode | New file: `splitscreen_video_generator.py` |
| Batch mode | Loop `run_pipeline()` over a folder of story files |
| Hook title card | Prepend a 2s title TextClip before gameplay in ffmpeg filter |
