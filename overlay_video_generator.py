#!/usr/bin/env python3
"""
overlay_video_generator.py
Brainrot Video Generator — Overlay Mode

Pipeline:
  story input → evaluate → auto-trim → TTS → hook card → captions → gameplay → compose → MP4

Usage:
  python overlay_video_generator.py --story input/stories/my_story.txt
  python overlay_video_generator.py --reddit https://reddit.com/r/AmItheAsshole/comments/...
  python overlay_video_generator.py --scrape AITA --count 5
  python overlay_video_generator.py --batch input/stories/ --count 10
  python overlay_video_generator.py --paste
"""

import os, sys, json, re, math, asyncio, subprocess, shutil, argparse, textwrap, time, urllib.request
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# ── Folder layout ──────────────────────────────────────────────────────────────
ROOT      = Path(__file__).parent
GAMEPLAY  = ROOT / "assets" / "gameplay"
MUSIC     = ROOT / "assets" / "music"
FONTS     = ROOT / "assets" / "fonts"
INPUT     = ROOT / "input" / "stories"
VID_OUT   = ROOT / "output" / "videos"
AUD_OUT   = ROOT / "output" / "audio"
SUB_OUT   = ROOT / "output" / "subtitles"
TEMP      = ROOT / "temp"

for _d in [GAMEPLAY, MUSIC, FONTS, INPUT, VID_OUT, AUD_OUT, SUB_OUT, TEMP]:
    _d.mkdir(parents=True, exist_ok=True)

# ── Data objects ───────────────────────────────────────────────────────────────

@dataclass
class Story:
    title:        str
    raw_text:     str
    cleaned_text: str = ""
    source:       str = "manual"
    url:          str = ""
    subreddit:    str = ""

@dataclass
class EvalResult:
    score:          float
    hook_score:     float
    clarity_score:  float
    drama_score:    float
    length_score:   float
    recommended:    bool
    notes:          list = field(default_factory=list)

@dataclass
class CaptionChunk:
    text:  str
    start: float
    end:   float

@dataclass
class RenderConfig:
    resolution:    tuple = (1080, 1920)
    fps:           int   = 30
    font_size:     int   = 72
    max_words:     int   = 5
    max_lines:     int   = 2
    bottom_margin: int   = 220
    voice:         str   = "en-US-AriaNeural"
    crf:           int   = 18
    max_duration:  int   = 90
    hook_duration: float = 2.5   # seconds the title card is shown
    music_volume:  float = 0.0   # 0.0 = off, 0.08 = subtle background


# ── 1. Story loading ───────────────────────────────────────────────────────────

def load_story_from_file(path: str) -> Story:
    p = Path(path)
    text  = p.read_text(encoding="utf-8").strip()
    title = p.stem.replace("_", " ").replace("-", " ").title()
    return Story(title=title, raw_text=text, source="file")


def load_story_from_json(path: str) -> Story:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Story(
        title     = data.get("title", "Untitled"),
        raw_text  = data.get("text", data.get("body", "")),
        source    = data.get("source", "json"),
        url       = data.get("url", ""),
        subreddit = data.get("subreddit", ""),
    )


def load_story_from_paste() -> Story:
    print("\nPaste your Reddit story. Enter END on a blank line to finish:\n")
    lines = []
    while True:
        line = input()
        if line.strip().upper() == "END":
            break
        lines.append(line)
    text  = "\n".join(lines).strip()
    title = input("\nEnter a title: ").strip() or "Untitled"
    return Story(title=title, raw_text=text, source="paste")


def load_story_from_reddit_url(url: str) -> Story:
    """
    Fetch a Reddit post via the public JSON API — no credentials needed.
    Appends .json to the URL and parses the response.
    """
    clean = url.split("?")[0].rstrip("/")
    json_url = clean + ".json"
    req = urllib.request.Request(json_url, headers={"User-Agent": "brainrot-generator/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    post = data[0]["data"]["children"][0]["data"]
    return Story(
        title     = post["title"],
        raw_text  = post["selftext"],
        source    = "reddit",
        url       = url,
        subreddit = post["subreddit"],
    )


def scrape_subreddit(subreddit: str, count: int = 10, sort: str = "top",
                     time_filter: str = "week") -> list[Story]:
    """
    Pull top posts from a subreddit via the public JSON API — no credentials needed.
    Returns up to `count` Story objects.
    """
    url = f"https://www.reddit.com/r/{subreddit}/{sort}.json?limit={count}&t={time_filter}"
    req = urllib.request.Request(url, headers={"User-Agent": "brainrot-generator/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())

    stories = []
    for child in data["data"]["children"]:
        post = child["data"]
        if not post.get("selftext") or post["selftext"] in ("[removed]", "[deleted]", ""):
            continue
        stories.append(Story(
            title     = post["title"],
            raw_text  = post["selftext"],
            source    = "reddit",
            url       = f"https://reddit.com{post['permalink']}",
            subreddit = subreddit,
        ))
        if len(stories) >= count:
            break

    print(f"  Fetched {len(stories)} posts from r/{subreddit}")
    return stories


def clean_story_text(story: Story) -> Story:
    text = story.raw_text

    # Remove Edit/Update sections
    text = re.sub(r'\n+(Edit|Update|EDIT|UPDATE):.*', '', text, flags=re.DOTALL)
    # Markdown links
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # Bold/italic
    text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)
    # u/username and r/subreddit
    text = re.sub(r'[ur]/\S+', '', text)
    # Collapse whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)

    story.cleaned_text = text.strip()
    return story


# ── 2. Auto-trim long stories ─────────────────────────────────────────────────

def trim_story_to_limit(story: Story, max_words: int = 350) -> Story:
    """
    If the story exceeds max_words, cut at the last sentence boundary before
    the limit so the narration ends cleanly rather than mid-sentence.
    """
    words = story.cleaned_text.split()
    if len(words) <= max_words:
        return story

    # Find the last sentence-ending punctuation before the word limit
    truncated = " ".join(words[:max_words])
    last_end  = max(truncated.rfind(". "), truncated.rfind("! "), truncated.rfind("? "))

    if last_end > len(truncated) // 2:
        # Cut at the clean sentence boundary
        story.cleaned_text = truncated[:last_end + 1].strip()
    else:
        # No good boundary found — just use the word limit as-is
        story.cleaned_text = truncated.strip()

    trimmed_count = len(story.cleaned_text.split())
    print(f"  Auto-trimmed: {len(words)} → {trimmed_count} words")
    return story


# ── 3. Story evaluator ────────────────────────────────────────────────────────

def evaluate_story_rules(story: Story) -> EvalResult:
    text       = story.cleaned_text or story.raw_text
    words      = text.split()
    word_count = len(words)
    sentences  = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]
    notes      = []

    # Length
    if word_count < 60:
        length_score = 3.0; notes.append("Too short")
    elif word_count < 100:
        length_score = 6.0; notes.append("Short — may feel rushed")
    elif word_count <= 350:
        length_score = 9.5; notes.append("Good length")
    elif word_count <= 500:
        length_score = 7.0; notes.append("Slightly long")
    else:
        length_score = 4.0; notes.append("Too long — will be auto-trimmed")

    # Hook
    first      = sentences[0] if sentences else ""
    hook_score = 5.0
    if any(w in first.lower() for w in ["i", "my", "found out", "told me", "yesterday", "today"]):
        hook_score += 2.0; notes.append("Strong personal hook")
    if any(w in first.lower() for w in ["never", "lied", "cheated", "fired", "broke", "called"]):
        hook_score += 1.5; notes.append("Dramatic opener")
    if len(first.split()) < 6:
        hook_score += 1.0; notes.append("Punchy first sentence")
    hook_score = min(hook_score, 10.0)

    # Drama
    drama_keywords = [
        "cheated", "lied", "betrayed", "kicked out", "fired", "broke up", "divorce",
        "confronted", "screamed", "cried", "refused", "threatened", "blocked", "ghosted",
        "exposed", "caught", "admitted", "confessed", "furious", "devastated", "shocked",
        "hurt", "angry", "jealous", "cut off", "family", "boyfriend", "girlfriend",
        "husband", "wife", "mom", "dad", "sister", "brother",
    ]
    hits        = sum(1 for kw in drama_keywords if kw in text.lower())
    drama_score = min(4.0 + hits * 0.5, 10.0)
    notes.append("High drama" if hits >= 6 else "Moderate drama" if hits >= 3 else "Low drama")

    # Clarity
    reddit_refs   = len(re.findall(r'\b(OP|NTA|YTA|ESH|NAH|AITA|WIBTA|TLDR)\b', text))
    clarity_score = max(4.0, 9.0 - reddit_refs * 0.5)
    if reddit_refs > 3:
        notes.append("Heavy Reddit jargon")

    score = (hook_score * 0.25 + drama_score * 0.35 +
             clarity_score * 0.20 + length_score * 0.20)

    return EvalResult(
        score         = round(score, 1),
        hook_score    = round(hook_score, 1),
        drama_score   = round(drama_score, 1),
        clarity_score = round(clarity_score, 1),
        length_score  = round(length_score, 1),
        recommended   = score >= 6.5,
        notes         = notes,
    )


def evaluate_story_llm(story: Story) -> EvalResult:
    try:
        import openai
    except ImportError:
        return evaluate_story_rules(story)

    if not os.getenv("OPENAI_API_KEY"):
        return evaluate_story_rules(story)

    client = openai.OpenAI()
    prompt = f"""Evaluate this Reddit story for TikTok brainrot narration. Return ONLY valid JSON:
{{
  "score": <0-10>, "hook_score": <0-10>, "clarity_score": <0-10>,
  "drama_score": <0-10>, "length_score": <0-10>,
  "recommended": <bool — true if score >= 6.5>,
  "notes": [<strings>]
}}
Story: {story.cleaned_text[:2000]}"""

    try:
        resp = client.chat.completions.create(
            model           = "gpt-4o-mini",
            messages        = [{"role": "user", "content": prompt}],
            response_format = {"type": "json_object"},
        )
        return EvalResult(**json.loads(resp.choices[0].message.content))
    except Exception as e:
        print(f"  LLM eval failed ({e}), falling back to rules")
        return evaluate_story_rules(story)


def evaluate_story(story: Story) -> EvalResult:
    return evaluate_story_llm(story) if os.getenv("OPENAI_API_KEY") else evaluate_story_rules(story)


# ── 4. TTS generation ─────────────────────────────────────────────────────────

async def _generate_tts(text: str, output_path: Path, voice: str) -> list[dict]:
    try:
        import edge_tts
    except ImportError:
        print("edge-tts not installed — run: pip install edge-tts")
        sys.exit(1)

    communicate = edge_tts.Communicate(text, voice)
    raw         = []

    with open(output_path, "wb") as fh:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                fh.write(chunk["data"])
            elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                start = chunk["offset"] / 10_000_000
                dur   = chunk.get("duration", 0) / 10_000_000
                raw.append({"word": chunk.get("text", ""), "start": round(start, 3), "dur": round(dur, 3)})

    boundaries = []
    for i, b in enumerate(raw):
        end = raw[i + 1]["start"] if i + 1 < len(raw) else b["start"] + max(b["dur"], 0.3)
        boundaries.append({"word": b["word"], "start": b["start"], "end": round(end, 3)})
    return boundaries


def generate_voiceover(text: str, output_path: Path, voice: str = "en-US-AriaNeural") -> list[dict]:
    return asyncio.run(_generate_tts(text, output_path, voice))


def get_audio_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    return float(r.stdout.strip())


# ── 5. Caption generation ─────────────────────────────────────────────────────

def _estimate_chunks(text: str, duration: float, max_words: int, max_lines: int) -> list[CaptionChunk]:
    words      = text.split()
    chunk_size = max_words * max_lines
    total      = len(words)
    chunks     = []
    i          = 0
    while i < total:
        group = words[i : i + chunk_size]
        chunks.append(CaptionChunk(
            text  = " ".join(group),
            start = round((i / total) * duration, 3),
            end   = round((min(i + len(group), total) / total) * duration, 3),
        ))
        i += chunk_size
    return chunks


def build_caption_chunks(boundaries: list[dict], text: str,
                          duration: float, cfg: RenderConfig) -> list[CaptionChunk]:
    if boundaries:
        chunks = [CaptionChunk(b["word"].strip(), b["start"], b["end"])
                  for b in boundaries if b["word"].strip()]
        if chunks:
            return chunks
    return _estimate_chunks(text, duration, cfg.max_words, cfg.max_lines)


def _t(s: float) -> str:
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{sec:05.2f}"


def build_ass_subtitles(chunks: list[CaptionChunk], cfg: RenderConfig,
                         hook_duration: float = 0.0) -> str:
    """
    Generate ASS subtitle file. If hook_duration > 0, captions are shifted
    forward to account for the title card at the start.
    """
    W, H = cfg.resolution
    fs   = cfg.font_size
    vm   = cfg.bottom_margin

    header = textwrap.dedent(f"""\
        [Script Info]
        ScriptType: v4.00+
        PlayResX: {W}
        PlayResY: {H}
        WrapStyle: 1

        [V4+ Styles]
        Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
        Style: Default,Arial,{fs},&H00FFFFFF,&H000000FF,&H00000000,&HA0000000,-1,0,0,0,100,100,1,0,1,4,2,2,40,40,{vm},1

        [Events]
        Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
    """)

    events = []
    for chunk in chunks:
        start = chunk.start + hook_duration
        end   = chunk.end   + hook_duration
        words = chunk.text.split()
        mid   = math.ceil(len(words) / 2)
        text  = " ".join(words[:mid]) + r"\N" + " ".join(words[mid:]) if len(words) > cfg.max_words else chunk.text
        events.append(f"Dialogue: 0,{_t(start)},{_t(end)},Default,,0,0,0,,{text}")

    return header + "\n".join(events) + "\n"


def write_ass_file(chunks: list[CaptionChunk], cfg: RenderConfig,
                   output_path: Path, hook_duration: float = 0.0) -> Path:
    output_path.write_text(build_ass_subtitles(chunks, cfg, hook_duration), encoding="utf-8")
    return output_path


# ── 6. Gameplay handling ──────────────────────────────────────────────────────

# Search queries rotated randomly so videos vary between runs
GAMEPLAY_SEARCHES = [
    "subway surfers gameplay no commentary vertical",
    "minecraft parkour gameplay satisfying vertical",
    "geometry dash gameplay vertical no commentary",
    "temple run gameplay vertical no commentary",
    "stack ball gameplay satisfying vertical",
    "helix jump gameplay satisfying vertical",
    "infinite runner mobile gameplay vertical",
    "satisfying minecraft build timelapse vertical",
]


def fetch_gameplay_from_youtube(query: Optional[str] = None) -> Path:
    """
    Download a gameplay clip from YouTube using yt-dlp.
    Picks a random search query from GAMEPLAY_SEARCHES if none given.
    Caches to assets/gameplay/ so it's reused on the next run.
    """
    if not shutil.which("yt-dlp"):
        raise RuntimeError(
            "yt-dlp not installed.\n"
            "Run: pip install yt-dlp   or   brew install yt-dlp"
        )

    import random
    search = query or random.choice(GAMEPLAY_SEARCHES)
    slug   = re.sub(r'[^a-z0-9]+', '_', search.lower())[:40]
    out    = GAMEPLAY / f"{slug}.mp4"

    if out.exists():
        print(f"  Using cached gameplay: {out.name}")
        return out

    print(f"  Downloading gameplay: \"{search}\"")
    cmd = [
        "yt-dlp",
        f"ytsearch1:{search}",          # grab the top result
        "--format", "bestvideo[ext=mp4][height<=1920]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--output", str(out),
        "--no-playlist",
        "--quiet", "--no-warnings",
        "--max-filesize", "200M",        # skip huge files
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not out.exists():
        raise RuntimeError(f"yt-dlp failed:\n{result.stderr[-500:]}")

    print(f"  Saved → {out.name}")
    return out


def find_gameplay_video(requested: Optional[str] = None, auto_fetch: bool = True) -> Path:
    """
    Return a gameplay video path. Priority:
      1. Explicitly requested path
      2. Cached file in assets/gameplay/
      3. Auto-download from YouTube (if auto_fetch=True)
    """
    if requested:
        p = Path(requested)
        if p.exists():
            return p
        raise FileNotFoundError(f"Gameplay video not found: {requested}")

    candidates = list(GAMEPLAY.glob("*.mp4")) + list(GAMEPLAY.glob("*.mov"))
    if candidates:
        import random
        return random.choice(candidates)   # rotate through cached clips

    if auto_fetch:
        return fetch_gameplay_from_youtube()

    raise FileNotFoundError(
        f"No gameplay videos in {GAMEPLAY}/\n"
        "Run with --auto-gameplay or drop a .mp4 there manually."
    )


def find_music_file(requested: Optional[str] = None) -> Optional[Path]:
    if requested:
        p = Path(requested)
        return p if p.exists() else None
    candidates = list(MUSIC.glob("*.mp3")) + list(MUSIC.glob("*.m4a")) + list(MUSIC.glob("*.wav"))
    return candidates[0] if candidates else None


def get_video_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    return float(r.stdout.strip())


# ── 7. Hook title card ────────────────────────────────────────────────────────

def render_hook_card(title: str, gameplay_path: Path, duration: float,
                     cfg: RenderConfig, output_path: Path) -> Path:
    """
    Render a hook title card: gameplay background + large centered title text
    for `duration` seconds. Uses ffmpeg drawtext filter.
    """
    W, H = cfg.resolution

    # Escape special characters for ffmpeg drawtext
    safe_title = title.replace("'", "\\'").replace(":", "\\:").replace(",", "\\,")
    # Wrap at ~25 chars
    words, lines, cur = title.split(), [], []
    for w in words:
        cur.append(w)
        if len(" ".join(cur)) > 25:
            lines.append(" ".join(cur[:-1]))
            cur = [w]
    if cur:
        lines.append(" ".join(cur))
    display = r"\n".join(lines)
    safe_display = display.replace("'", "\\'").replace(":", "\\:").replace(",", "\\,")

    vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
        f"drawtext=text='{safe_display}':fontsize=80:fontcolor=white:"
        f"borderw=4:bordercolor=black:shadowx=3:shadowy=3:"
        f"x=(w-text_w)/2:y=(h-text_h)/2:line_spacing=20,"
        f"drawtext=text='AITA?':fontsize=48:fontcolor=yellow:"
        f"borderw=3:bordercolor=black:x=(w-text_w)/2:y=(h/2)+100"
    )

    loop = max(1, int(math.ceil(duration / get_video_duration(gameplay_path))) + 1)

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", str(loop),
        "-i", str(gameplay_path),
        "-vf", vf,
        "-t", str(duration),
        "-c:v", "libx264", "-crf", str(cfg.crf), "-preset", "fast",
        "-an",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Hook card render failed:\n{result.stderr[-1000:]}")
    return output_path


# ── 8. Final composition ──────────────────────────────────────────────────────

def compose_overlay_video(
    gameplay_path: Path,
    audio_path:    Path,
    subs_path:     Path,
    output_path:   Path,
    audio_dur:     float,
    cfg:           RenderConfig,
    hook_path:     Optional[Path] = None,
    music_path:    Optional[Path] = None,
) -> Path:
    W, H     = cfg.resolution
    loop     = int(math.ceil(audio_dur / get_video_duration(gameplay_path))) + 1
    body_dur = min(audio_dur + 0.5, cfg.max_duration)

    # ── Step A: render the narrated gameplay body
    body_path = TEMP / f"body_{output_path.stem}.mp4"
    vf_body   = (
        f"scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},"
        f"subtitles='{subs_path}':force_style=''"
    )

    # Build audio filter — mix narration with optional background music
    if music_path and cfg.music_volume > 0:
        audio_inputs = ["-i", str(audio_path), "-i", str(music_path)]
        # Loop music and mix at low volume
        af = (
            f"[1:a]volume={cfg.music_volume},aloop=loop=-1:size=2e+09[music];"
            f"[0:a][music]amix=inputs=2:duration=first[aout]"
        )
        audio_map = ["-filter_complex", af, "-map", "0:v:0", "-map", "[aout]"]
    else:
        audio_inputs = ["-i", str(audio_path)]
        audio_map    = ["-map", "0:v:0", "-map", "1:a:0"]

    cmd_body = [
        "ffmpeg", "-y",
        "-stream_loop", str(loop), "-i", str(gameplay_path),
        *audio_inputs,
        "-vf", vf_body,
        *audio_map,
        "-c:v", "libx264", "-crf", str(cfg.crf), "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k",
        "-t", str(body_dur),
        "-movflags", "+faststart",
        "-r", str(cfg.fps),
        str(body_path),
    ]
    result = subprocess.run(cmd_body, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg body render failed:\n{result.stderr[-2000:]}")

    # ── Step B: if no hook card, body is the final output
    if hook_path is None:
        shutil.move(str(body_path), str(output_path))
        return output_path

    # ── Step C: concatenate hook card + body
    list_file = TEMP / f"concat_{output_path.stem}.txt"
    list_file.write_text(
        f"file '{hook_path.resolve()}'\nfile '{body_path.resolve()}'\n"
    )
    cmd_concat = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]
    result = subprocess.run(cmd_concat, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed:\n{result.stderr[-1000:]}")

    # Clean up temp files
    body_path.unlink(missing_ok=True)
    list_file.unlink(missing_ok=True)
    return output_path


# ── 9. Checks ─────────────────────────────────────────────────────────────────

def check_dependencies():
    missing = []
    if not shutil.which("ffmpeg"):
        missing.append("ffmpeg  →  brew install ffmpeg")
    if not shutil.which("ffprobe"):
        missing.append("ffprobe →  included with ffmpeg")
    try:
        import edge_tts  # noqa
    except ImportError:
        missing.append("edge-tts  →  pip install edge-tts")
    if missing:
        print("Missing dependencies:")
        for m in missing:
            print(f"  • {m}")
        sys.exit(1)


# ── 10. Single-story pipeline ─────────────────────────────────────────────────

def run_pipeline(
    story:         Story,
    gameplay_path: Optional[str] = None,
    music_path:    Optional[str] = None,
    cfg:           RenderConfig  = None,
    skip_eval:     bool          = False,
    force:         bool          = False,
    hook:          bool          = True,
) -> Optional[Path]:

    if cfg is None:
        cfg = RenderConfig()

    slug = re.sub(r'[^a-z0-9]+', '_', story.title.lower())[:40].strip('_')
    name = f"{slug}_{int(time.time())}"

    print(f"\n{'='*60}")
    print(f"  Story : {story.title}")
    print(f"  Source: {story.source}")
    print(f"{'='*60}")

    # 1. Clean
    print("\n[1/7] Cleaning story text...")
    story = clean_story_text(story)
    print(f"  {len(story.cleaned_text.split())} words")

    # 2. Evaluate
    if not skip_eval:
        print("\n[2/7] Evaluating story quality...")
        result = evaluate_story(story)
        print(f"  Score: {result.score}/10  |  Recommended: {result.recommended}")
        for note in result.notes:
            print(f"    • {note}")
        if not result.recommended and not force:
            print(f"  Skipping (score {result.score} < 6.5). Use --force to override.")
            return None
    else:
        print("\n[2/7] Skipping evaluation")

    # 3. Auto-trim
    print("\n[3/7] Checking story length...")
    story = trim_story_to_limit(story, max_words=350)

    # 4. TTS
    print(f"\n[4/7] Generating voiceover ({cfg.voice})...")
    audio_path = AUD_OUT / f"{name}.mp3"
    boundaries = generate_voiceover(story.cleaned_text, audio_path, cfg.voice)
    audio_dur  = get_audio_duration(audio_path)
    print(f"  {audio_dur:.1f}s audio  |  {len(boundaries)} caption boundaries")

    # 5. Captions
    print("\n[5/7] Building captions...")
    chunks    = build_caption_chunks(boundaries, story.cleaned_text, audio_dur, cfg)
    subs_path = SUB_OUT / f"{name}.ass"
    hook_dur  = cfg.hook_duration if hook else 0.0
    write_ass_file(chunks, cfg, subs_path, hook_duration=hook_dur)
    print(f"  {len(chunks)} chunks → {subs_path.name}")

    # 6. Gameplay + optional hook card
    print("\n[6/7] Preparing video assets...")
    gp_path    = find_gameplay_video(gameplay_path, auto_fetch=True)
    music_file = find_music_file(music_path)
    gp_dur     = get_video_duration(gp_path)
    print(f"  Gameplay: {gp_path.name} ({gp_dur:.1f}s)")
    if music_file:
        print(f"  Music:    {music_file.name} @ volume {cfg.music_volume}")
    else:
        print("  Music:    none (drop an mp3 in assets/music/ to enable)")

    hook_card = None
    if hook and cfg.hook_duration > 0:
        print(f"  Rendering hook title card ({cfg.hook_duration}s)...")
        hook_card = TEMP / f"hook_{name}.mp4"
        render_hook_card(story.title, gp_path, cfg.hook_duration, cfg, hook_card)

    # 7. Compose
    print("\n[7/7] Composing final video...")
    out_path = VID_OUT / f"{name}.mp4"
    compose_overlay_video(
        gameplay_path = gp_path,
        audio_path    = audio_path,
        subs_path     = subs_path,
        output_path   = out_path,
        audio_dur     = audio_dur,
        cfg           = cfg,
        hook_path     = hook_card,
        music_path    = music_file,
    )

    if hook_card:
        hook_card.unlink(missing_ok=True)

    size_mb = out_path.stat().st_size / 1_000_000
    total   = min(audio_dur + hook_dur, cfg.max_duration + hook_dur)
    print(f"\n{'='*60}")
    print(f"  Done! → {out_path}")
    print(f"  Size: {size_mb:.1f} MB  |  Duration: {total:.1f}s")
    print(f"{'='*60}\n")
    return out_path


# ── 11. Batch pipeline ────────────────────────────────────────────────────────

def run_batch(stories: list[Story], **kwargs) -> list[Path]:
    """
    Run the pipeline over a list of stories, skipping ones that fail evaluation.
    Returns list of successfully generated video paths.
    """
    total   = len(stories)
    outputs = []
    print(f"\n Batch mode: {total} stories queued\n")

    for i, story in enumerate(stories, 1):
        print(f"[{i}/{total}] {story.title[:60]}")
        try:
            out = run_pipeline(story, **kwargs)
            if out:
                outputs.append(out)
        except Exception as e:
            print(f"  ERROR: {e} — skipping\n")

    print(f"\n Batch complete: {len(outputs)}/{total} videos generated")
    for p in outputs:
        print(f"  → {p}")
    return outputs


# ── 12. CLI ───────────────────────────────────────────────────────────────────

def main():
    check_dependencies()

    parser = argparse.ArgumentParser(
        description="Brainrot Video Generator — Overlay Mode",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python overlay_video_generator.py --story input/stories/aita.txt
              python overlay_video_generator.py --reddit https://reddit.com/r/AITA/comments/...
              python overlay_video_generator.py --scrape AITA --count 5
              python overlay_video_generator.py --batch input/stories/
              python overlay_video_generator.py --paste --no-hook
              python overlay_video_generator.py --story story.txt --music assets/music/lofi.mp3 --music-vol 0.08
        """),
    )

    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--story",   metavar="FILE",      help=".txt or .json story file")
    src.add_argument("--reddit",  metavar="URL",        help="Reddit post URL (no credentials needed)")
    src.add_argument("--scrape",  metavar="SUBREDDIT",  help="Scrape top posts from a subreddit")
    src.add_argument("--batch",   metavar="FOLDER",     help="Run on all .txt/.json files in a folder")
    src.add_argument("--paste",   action="store_true",  help="Paste story interactively")

    parser.add_argument("--gameplay",        metavar="FILE",  default=None, help="Specific gameplay video file")
    parser.add_argument("--gameplay-query", metavar="QUERY", default=None, help="YouTube search query for gameplay (e.g. 'subway surfers vertical')")
    parser.add_argument("--music",      metavar="FILE",  default=None,  help="Background music file")
    parser.add_argument("--music-vol",  type=float,      default=0.08,  help="Music volume 0.0–1.0 (default: 0.08)")
    parser.add_argument("--count",      type=int,        default=10,    help="Stories to fetch/batch (default: 10)")
    parser.add_argument("--voice",      default="en-US-AriaNeural")
    parser.add_argument("--font-size",  type=int,        default=72)
    parser.add_argument("--max-dur",    type=int,        default=90)
    parser.add_argument("--no-eval",    action="store_true")
    parser.add_argument("--force",      action="store_true")
    parser.add_argument("--no-hook",    action="store_true",            help="Skip hook title card")
    parser.add_argument("--list-voices", action="store_true")

    args = parser.parse_args()

    if args.list_voices:
        import edge_tts
        async def _list():
            for v in await edge_tts.list_voices():
                if "en-" in v["ShortName"].lower():
                    print(f"  {v['ShortName']:40s}  {v['Gender']}")
        asyncio.run(_list())
        return

    cfg = RenderConfig(
        voice        = args.voice,
        font_size    = args.font_size,
        max_duration = args.max_dur,
        music_volume = args.music_vol,
    )

    # Pre-fetch gameplay from YouTube if a query was given
    resolved_gameplay = args.gameplay
    if args.gameplay_query and not resolved_gameplay:
        print(f"\nFetching gameplay: \"{args.gameplay_query}\"")
        resolved_gameplay = str(fetch_gameplay_from_youtube(args.gameplay_query))

    pipeline_kwargs = dict(
        gameplay_path = resolved_gameplay,
        music_path    = args.music,
        cfg           = cfg,
        skip_eval     = args.no_eval,
        force         = args.force,
        hook          = not args.no_hook,
    )

    if args.story:
        story = load_story_from_json(args.story) if args.story.endswith(".json") else load_story_from_file(args.story)
        run_pipeline(story, **pipeline_kwargs)

    elif args.reddit:
        story = load_story_from_reddit_url(args.reddit)
        run_pipeline(story, **pipeline_kwargs)

    elif args.scrape:
        stories = scrape_subreddit(args.scrape, count=args.count)
        run_batch(stories, **pipeline_kwargs)

    elif args.batch:
        folder  = Path(args.batch)
        files   = list(folder.glob("*.txt")) + list(folder.glob("*.json"))[:args.count]
        stories = [load_story_from_json(str(f)) if f.suffix == ".json"
                   else load_story_from_file(str(f)) for f in files]
        run_batch(stories, **pipeline_kwargs)

    else:
        story = load_story_from_paste()
        run_pipeline(story, **pipeline_kwargs)


if __name__ == "__main__":
    main()
