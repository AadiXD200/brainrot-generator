#!/usr/bin/env python3
"""
overlay_video_generator.py
Brainrot Video Generator — Overlay Mode

Pipeline:
  story input → evaluate → TTS → captions → gameplay → compose → MP4

Usage:
  python overlay_video_generator.py --story input/stories/my_story.txt
  python overlay_video_generator.py --story input/stories/my_story.txt --gameplay assets/gameplay/subway.mp4
  python overlay_video_generator.py --reddit https://reddit.com/r/AmItheAsshole/comments/...
  python overlay_video_generator.py --paste   (interactive paste mode)
"""

import os, sys, json, re, math, asyncio, subprocess, shutil, argparse, textwrap, time
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

# ── Folder layout ──────────────────────────────────────────────────────────────
ROOT      = Path(__file__).parent
GAMEPLAY  = ROOT / "assets" / "gameplay"
FONTS     = ROOT / "assets" / "fonts"
INPUT     = ROOT / "input" / "stories"
VID_OUT   = ROOT / "output" / "videos"
AUD_OUT   = ROOT / "output" / "audio"
SUB_OUT   = ROOT / "output" / "subtitles"
TEMP      = ROOT / "temp"

for _d in [GAMEPLAY, FONTS, INPUT, VID_OUT, AUD_OUT, SUB_OUT, TEMP]:
    _d.mkdir(parents=True, exist_ok=True)

# ── Data objects ───────────────────────────────────────────────────────────────

@dataclass
class Story:
    title:        str
    raw_text:     str
    cleaned_text: str  = ""
    source:       str  = "manual"
    url:          str  = ""
    subreddit:    str  = ""

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
    start: float   # seconds
    end:   float   # seconds

@dataclass
class RenderConfig:
    resolution:     tuple = (1080, 1920)
    fps:            int   = 30
    font_size:      int   = 72
    max_words:      int   = 5   # words per caption line
    max_lines:      int   = 2   # lines per caption
    bottom_margin:  int   = 220
    voice:          str   = "en-US-AriaNeural"
    crf:            int   = 18
    max_duration:   int   = 90  # seconds, hard cap


# ── 1. Story loading ───────────────────────────────────────────────────────────

def load_story_from_file(path: str) -> Story:
    p = Path(path)
    text = p.read_text(encoding="utf-8").strip()
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
    print("\nPaste your Reddit story below. Enter a blank line followed by END to finish:\n")
    lines = []
    while True:
        line = input()
        if line.strip().upper() == "END":
            break
        lines.append(line)
    text = "\n".join(lines).strip()
    title = input("\nEnter a title for this story: ").strip() or "Untitled"
    return Story(title=title, raw_text=text, source="paste")


def load_story_from_reddit(url: str) -> Story:
    try:
        import praw
    except ImportError:
        print("praw not installed — run: pip install praw")
        sys.exit(1)

    import os
    reddit = praw.Reddit(
        client_id     = os.getenv("REDDIT_CLIENT_ID", ""),
        client_secret = os.getenv("REDDIT_CLIENT_SECRET", ""),
        user_agent    = "brainrot-generator/1.0",
    )
    submission = reddit.submission(url=url)
    submission.comments.replace_more(limit=0)
    return Story(
        title     = submission.title,
        raw_text  = submission.selftext,
        source    = "reddit",
        url       = url,
        subreddit = str(submission.subreddit),
    )


def clean_story_text(story: Story) -> Story:
    """Strip Reddit formatting, usernames, update noise, normalize for TTS."""
    text = story.raw_text

    # Remove "Edit:" / "Update:" sections (common Reddit padding)
    text = re.sub(r'\n+(Edit|Update|EDIT|UPDATE):.*', '', text, flags=re.DOTALL)

    # Remove markdown links [text](url)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)

    # Remove markdown bold/italic
    text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)

    # Remove u/username mentions
    text = re.sub(r'u/\S+', '', text)

    # Remove r/subreddit mentions
    text = re.sub(r'r/\S+', '', text)

    # Collapse multiple newlines
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Collapse multiple spaces
    text = re.sub(r' {2,}', ' ', text)

    story.cleaned_text = text.strip()
    return story


# ── 2. Story evaluator ────────────────────────────────────────────────────────

def evaluate_story_rules(story: Story) -> EvalResult:
    """
    Rule-based story scorer. Used when no OpenAI key is set.
    Returns an EvalResult with heuristic scores.
    """
    text = story.cleaned_text or story.raw_text
    words = text.split()
    word_count = len(words)
    sentences = re.split(r'[.!?]+', text)
    sentences = [s.strip() for s in sentences if s.strip()]

    notes = []

    # ── Length score (target: 100–350 words for 30–90s content)
    if word_count < 60:
        length_score = 3.0; notes.append("Too short")
    elif word_count < 100:
        length_score = 6.0; notes.append("Short — may feel rushed")
    elif word_count <= 350:
        length_score = 9.5; notes.append("Good length for short-form")
    elif word_count <= 500:
        length_score = 7.0; notes.append("Slightly long — may need trimming")
    else:
        length_score = 4.0; notes.append("Too long — consider splitting")

    # ── Hook score — first sentence quality
    first = sentences[0] if sentences else ""
    hook_score = 5.0
    hook_triggers = ["i", "my", "aita", "wibta", "found out", "told me", "she", "he",
                     "i can't believe", "yesterday", "today", "last night", "just found"]
    drama_openers = ["never", "always", "finally", "broke", "lied", "cheated",
                     "kicked out", "fired", "called", "said", "texted"]
    if any(w in first.lower() for w in hook_triggers):
        hook_score += 2.0; notes.append("Strong personal hook")
    if any(w in first.lower() for w in drama_openers):
        hook_score += 1.5; notes.append("Dramatic opener")
    if len(first.split()) < 6:
        hook_score += 1.0; notes.append("Punchy first sentence")
    hook_score = min(hook_score, 10.0)

    # ── Drama score — emotional/conflict keywords
    drama_keywords = [
        "cheated", "lied", "betrayed", "kicked out", "fired", "broke up",
        "divorce", "confronted", "screamed", "cried", "refused", "threatened",
        "blocked", "ghosted", "exposed", "caught", "admitted", "confessed",
        "furious", "devastated", "shocked", "hurt", "angry", "jealous",
        "never speak", "cut off", "family", "friend", "boyfriend", "girlfriend",
        "husband", "wife", "mom", "dad", "sister", "brother",
    ]
    hits = sum(1 for kw in drama_keywords if kw in text.lower())
    drama_score = min(4.0 + hits * 0.5, 10.0)
    if hits >= 6:
        notes.append("High drama — good engagement")
    elif hits >= 3:
        notes.append("Moderate drama")
    else:
        notes.append("Low drama — may not hold attention")

    # ── Clarity score — penalise jargon, inside references, acronyms
    jargon_hits = len(re.findall(r'\b[A-Z]{3,}\b', text))  # all-caps acronyms
    reddit_refs = len(re.findall(r'\b(OP|NTA|YTA|ESH|NAH|AITA|WIBTA|TLDR|TL;DR)\b', text))
    clarity_score = max(4.0, 9.0 - jargon_hits * 0.3 - reddit_refs * 0.5)
    if reddit_refs > 3:
        notes.append("Heavy Reddit jargon — narration may sound odd")

    # ── Aggregate
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
    """
    LLM-based story scorer using OpenAI. Falls back to rules if unavailable.
    """
    try:
        import openai
    except ImportError:
        return evaluate_story_rules(story)

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return evaluate_story_rules(story)

    client = openai.OpenAI(api_key=api_key)
    prompt = f"""You are evaluating a Reddit story for use as a TikTok brainrot video narration.
Score the following story and return ONLY valid JSON matching this schema exactly:
{{
  "score": <float 0-10>,
  "hook_score": <float 0-10>,
  "clarity_score": <float 0-10>,
  "drama_score": <float 0-10>,
  "length_score": <float 0-10>,
  "recommended": <bool>,
  "notes": [<string>, ...]
}}

Scoring criteria:
- hook_score: Is the first sentence immediately gripping?
- clarity_score: Is it easy to follow when read aloud? No confusing references?
- drama_score: Is there conflict, tension, emotional stakes?
- length_score: Is it 80-350 words? (ideal for 30-90s video)
- recommended: true if overall score >= 6.5

Story title: {story.title}
Story text:
{story.cleaned_text[:2000]}
"""
    try:
        resp = client.chat.completions.create(
            model    = "gpt-4o-mini",
            messages = [{"role": "user", "content": prompt}],
            response_format = {"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        return EvalResult(**data)
    except Exception as e:
        print(f"  LLM evaluation failed ({e}), using rules-based scorer")
        return evaluate_story_rules(story)


def evaluate_story(story: Story) -> EvalResult:
    if os.getenv("OPENAI_API_KEY"):
        return evaluate_story_llm(story)
    return evaluate_story_rules(story)


# ── 3. TTS generation ─────────────────────────────────────────────────────────

async def _generate_tts(text: str, output_path: Path, voice: str) -> list[dict]:
    """
    Run edge-tts and collect sentence boundary events for caption timing.
    Returns list of {word, start, end} dicts (one entry per sentence).
    Newer edge-tts builds emit SentenceBoundary; older emit WordBoundary — handles both.
    """
    try:
        import edge_tts
    except ImportError:
        print("edge-tts not installed — run: pip install edge-tts")
        sys.exit(1)

    communicate = edge_tts.Communicate(text, voice)
    boundaries  = []
    raw         = []   # collect all boundary events first, add durations after

    with open(output_path, "wb") as fh:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                fh.write(chunk["data"])
            elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                start = chunk["offset"] / 10_000_000      # 100-ns → seconds
                dur   = chunk.get("duration", 0) / 10_000_000
                raw.append({
                    "word":  chunk.get("text", ""),
                    "start": round(start, 3),
                    "dur":   round(dur, 3),
                })

    # Compute end times: each boundary ends where the next one starts
    for i, b in enumerate(raw):
        end = raw[i + 1]["start"] if i + 1 < len(raw) else b["start"] + max(b["dur"], 0.3)
        boundaries.append({"word": b["word"], "start": b["start"], "end": round(end, 3)})

    return boundaries


def generate_voiceover(text: str, output_path: Path, voice: str = "en-US-AriaNeural") -> list[dict]:
    """Synchronous wrapper around the async TTS call."""
    return asyncio.run(_generate_tts(text, output_path, voice))


def get_audio_duration(audio_path: Path) -> float:
    """Return audio duration in seconds via ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True,
    )
    return float(result.stdout.strip())


# ── 4. Caption generation ─────────────────────────────────────────────────────

def _group_words_into_chunks(boundaries: list[dict],
                              max_words: int, max_lines: int) -> list[CaptionChunk]:
    """
    Group word boundaries into display chunks of max_words * max_lines words.
    Each chunk becomes one on-screen caption.
    """
    if not boundaries:
        return []

    chunk_size = max_words * max_lines
    chunks = []
    i = 0
    while i < len(boundaries):
        group = boundaries[i : i + chunk_size]
        text  = " ".join(w["word"] for w in group)
        start = group[0]["start"]
        end   = group[-1]["end"]
        # Add a tiny gap before next chunk
        chunks.append(CaptionChunk(text=text, start=start, end=end + 0.05))
        i += chunk_size

    return chunks


def _estimate_chunks_from_text(text: str, audio_duration: float,
                                max_words: int, max_lines: int) -> list[CaptionChunk]:
    """
    Fallback: estimate timing proportionally from word count when
    word boundaries are unavailable.
    """
    words = text.split()
    total_words = len(words)
    chunk_size  = max_words * max_lines
    chunks      = []
    i           = 0

    while i < total_words:
        group      = words[i : i + chunk_size]
        chunk_text = " ".join(group)
        start_frac = i / total_words
        end_frac   = min((i + len(group)) / total_words, 1.0)
        chunks.append(CaptionChunk(
            text  = chunk_text,
            start = round(start_frac * audio_duration, 3),
            end   = round(end_frac   * audio_duration, 3),
        ))
        i += chunk_size

    return chunks


def build_caption_chunks(boundaries: list[dict], text: str,
                          audio_duration: float, cfg: RenderConfig) -> list[CaptionChunk]:
    if boundaries:
        # Sentence boundaries: each entry is already a full sentence chunk
        chunks = []
        for b in boundaries:
            if not b["word"].strip():
                continue
            chunks.append(CaptionChunk(text=b["word"].strip(),
                                       start=b["start"], end=b["end"]))
        if chunks:
            return chunks
    return _estimate_chunks_from_text(text, audio_duration, cfg.max_words, cfg.max_lines)


def _seconds_to_ass_time(s: float) -> str:
    h   = int(s // 3600)
    m   = int((s % 3600) // 60)
    sec = s % 60
    return f"{h}:{m:02d}:{sec:05.2f}"


def build_ass_subtitles(chunks: list[CaptionChunk], cfg: RenderConfig) -> str:
    """
    Generate an ASS subtitle file string with styled captions.
    - White bold text, black outline, drop shadow
    - Bottom-center, with bottom_margin from edge
    """
    W, H  = cfg.resolution
    fs    = cfg.font_size
    vm    = cfg.bottom_margin

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
        t_start = _seconds_to_ass_time(chunk.start)
        t_end   = _seconds_to_ass_time(chunk.end)
        # Wrap long chunks into two lines
        words   = chunk.text.split()
        mid     = math.ceil(len(words) / 2)
        if len(words) > cfg.max_words:
            display_text = " ".join(words[:mid]) + r"\N" + " ".join(words[mid:])
        else:
            display_text = chunk.text
        events.append(
            f"Dialogue: 0,{t_start},{t_end},Default,,0,0,0,,{display_text}"
        )

    return header + "\n".join(events) + "\n"


def write_ass_file(chunks: list[CaptionChunk], cfg: RenderConfig, output_path: Path) -> Path:
    content = build_ass_subtitles(chunks, cfg)
    output_path.write_text(content, encoding="utf-8")
    return output_path


# ── 5. Gameplay video handling ────────────────────────────────────────────────

def find_gameplay_video(requested: Optional[str] = None) -> Path:
    """Return a gameplay video path, searching assets/gameplay/ if not specified."""
    if requested:
        p = Path(requested)
        if p.exists():
            return p
        raise FileNotFoundError(f"Gameplay video not found: {requested}")

    candidates = list(GAMEPLAY.glob("*.mp4")) + list(GAMEPLAY.glob("*.mov"))
    if not candidates:
        raise FileNotFoundError(
            f"No gameplay videos found in {GAMEPLAY}/\n"
            "Drop a .mp4 file there (e.g. subway_surfers.mp4) and re-run."
        )
    return candidates[0]


def get_video_duration(video_path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
        capture_output=True, text=True,
    )
    return float(result.stdout.strip())


# ── 6. Final composition ──────────────────────────────────────────────────────

def compose_overlay_video(
    gameplay_path: Path,
    audio_path:    Path,
    subs_path:     Path,
    output_path:   Path,
    audio_dur:     float,
    cfg:           RenderConfig,
) -> Path:
    """
    Compose final video with ffmpeg:
    - Scale + crop gameplay to 9:16
    - Loop gameplay if shorter than narration
    - Burn in ASS subtitles
    - Replace audio with narration (mute gameplay)
    """
    W, H    = cfg.resolution
    loop    = int(math.ceil(audio_dur / get_video_duration(gameplay_path))) + 1
    dur     = min(audio_dur + 0.5, cfg.max_duration)

    # ffmpeg filter: scale to fill 9:16, then crop dead center
    vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},"
        f"subtitles='{subs_path}':force_style=''"
    )

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", str(loop),
        "-i",  str(gameplay_path),
        "-i",  str(audio_path),
        "-vf", vf,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "libx264",
        "-crf", str(cfg.crf),
        "-preset", "fast",
        "-c:a", "aac",
        "-b:a", "192k",
        "-t",  str(dur),
        "-movflags", "+faststart",
        "-r", str(cfg.fps),
        str(output_path),
    ]

    print(f"\n  Running ffmpeg...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("ffmpeg error:\n", result.stderr[-2000:])
        raise RuntimeError("ffmpeg composition failed")

    return output_path


# ── 7. Checks ─────────────────────────────────────────────────────────────────

def check_dependencies():
    missing = []
    if not shutil.which("ffmpeg"):
        missing.append("ffmpeg  →  brew install ffmpeg")
    if not shutil.which("ffprobe"):
        missing.append("ffprobe →  brew install ffmpeg  (included with ffmpeg)")
    try:
        import edge_tts  # noqa
    except ImportError:
        missing.append("edge-tts  →  pip install edge-tts")
    if missing:
        print("Missing dependencies:")
        for m in missing:
            print(f"  • {m}")
        sys.exit(1)


# ── 8. Main pipeline ──────────────────────────────────────────────────────────

def run_pipeline(
    story:         Story,
    gameplay_path: Optional[str] = None,
    cfg:           RenderConfig  = None,
    skip_eval:     bool          = False,
    force:         bool          = False,
) -> Path:
    if cfg is None:
        cfg = RenderConfig()

    slug = re.sub(r'[^a-z0-9]+', '_', story.title.lower())[:40].strip('_')
    ts   = int(time.time())
    name = f"{slug}_{ts}"

    print(f"\n{'='*60}")
    print(f"  Story: {story.title}")
    print(f"  Source: {story.source}")
    print(f"{'='*60}")

    # ── Clean
    print("\n[1/6] Cleaning story text...")
    story = clean_story_text(story)
    word_count = len(story.cleaned_text.split())
    print(f"  {word_count} words after cleaning")

    # ── Evaluate
    if not skip_eval:
        print("\n[2/6] Evaluating story quality...")
        result = evaluate_story(story)
        print(f"  Score: {result.score}/10  |  Recommended: {result.recommended}")
        for note in result.notes:
            print(f"    • {note}")

        if not result.recommended and not force:
            print(f"\n  Story scored {result.score}/10 (threshold: 6.5)")
            print("  Use --force to generate anyway.")
            sys.exit(0)
    else:
        print("\n[2/6] Skipping evaluation (--no-eval)")

    # ── TTS
    print(f"\n[3/6] Generating voiceover ({cfg.voice})...")
    audio_path = AUD_OUT / f"{name}.mp3"
    boundaries = generate_voiceover(story.cleaned_text, audio_path, cfg.voice)
    audio_dur  = get_audio_duration(audio_path)
    print(f"  Audio: {audio_dur:.1f}s  |  {len(boundaries)} word boundaries")

    # Cap duration
    if audio_dur > cfg.max_duration:
        print(f"  Warning: audio is {audio_dur:.0f}s, capping output at {cfg.max_duration}s")

    # ── Captions
    print("\n[4/6] Building captions...")
    chunks    = build_caption_chunks(boundaries, story.cleaned_text, audio_dur, cfg)
    subs_path = SUB_OUT / f"{name}.ass"
    write_ass_file(chunks, cfg, subs_path)
    print(f"  {len(chunks)} caption chunks → {subs_path.name}")

    # ── Gameplay
    print("\n[5/6] Loading gameplay footage...")
    gp_path  = find_gameplay_video(gameplay_path)
    gp_dur   = get_video_duration(gp_path)
    print(f"  {gp_path.name}  ({gp_dur:.1f}s)")
    if gp_dur < audio_dur:
        loops = math.ceil(audio_dur / gp_dur)
        print(f"  Will loop ×{loops} to cover {audio_dur:.1f}s narration")

    # ── Compose
    print("\n[6/6] Composing final video...")
    out_path = VID_OUT / f"{name}.mp4"
    compose_overlay_video(gp_path, audio_path, subs_path, out_path, audio_dur, cfg)

    size_mb = out_path.stat().st_size / 1_000_000
    print(f"\n{'='*60}")
    print(f"  Done! → {out_path}")
    print(f"  Size: {size_mb:.1f} MB  |  Duration: {min(audio_dur, cfg.max_duration):.1f}s")
    print(f"{'='*60}\n")
    return out_path


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    check_dependencies()

    parser = argparse.ArgumentParser(
        description="Brainrot Video Generator — Overlay Mode",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python overlay_video_generator.py --story input/stories/aita.txt
              python overlay_video_generator.py --story story.json --gameplay assets/gameplay/subway.mp4
              python overlay_video_generator.py --reddit https://reddit.com/r/AITA/comments/...
              python overlay_video_generator.py --paste --force
        """),
    )

    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--story",   metavar="FILE",  help=".txt or .json story file")
    src.add_argument("--reddit",  metavar="URL",   help="Reddit post URL (requires REDDIT_CLIENT_ID/SECRET)")
    src.add_argument("--paste",   action="store_true", help="Paste story interactively")

    parser.add_argument("--gameplay",  metavar="FILE",  default=None,  help="Gameplay video file (default: first in assets/gameplay/)")
    parser.add_argument("--voice",     default="en-US-AriaNeural",     help="edge-tts voice name")
    parser.add_argument("--font-size", type=int, default=72,           help="Caption font size (default: 72)")
    parser.add_argument("--max-dur",   type=int, default=90,           help="Max output duration in seconds (default: 90)")
    parser.add_argument("--no-eval",   action="store_true",            help="Skip story quality evaluation")
    parser.add_argument("--force",     action="store_true",            help="Generate even if story scores below threshold")
    parser.add_argument("--list-voices", action="store_true",          help="List available edge-tts voices and exit")

    args = parser.parse_args()

    if args.list_voices:
        import edge_tts
        async def _list():
            for v in await edge_tts.list_voices():
                if "en-" in v["ShortName"].lower():
                    print(f"  {v['ShortName']:40s}  {v['Gender']}")
        asyncio.run(_list())
        return

    # Load story
    if args.story:
        p = args.story
        story = load_story_from_json(p) if p.endswith(".json") else load_story_from_file(p)
    elif args.reddit:
        story = load_story_from_reddit(args.reddit)
    else:
        story = load_story_from_paste()

    cfg = RenderConfig(
        voice        = args.voice,
        font_size    = args.font_size,
        max_duration = args.max_dur,
    )

    run_pipeline(
        story         = story,
        gameplay_path = args.gameplay,
        cfg           = cfg,
        skip_eval     = args.no_eval,
        force         = args.force,
    )


if __name__ == "__main__":
    main()
