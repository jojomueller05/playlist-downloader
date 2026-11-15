#!/usr/bin/env python3
"""Download the audio of the songs in songs.json from YouTube as .m4a (audio only).

Skips videos longer than 10 minutes.

Setup:   pip install -U "yt-dlp[default]"   + ffmpeg + deno (see README)
Usage:   python download_songs.py [songs.json] [-w 6] [--cookies-from-browser firefox] [--js-runtimes node]

Failures are written to failed.log.
"""
import argparse
import glob
import json
import os
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher

import yt_dlp

MAX_SECONDS = 10 * 60
OUT_DIR = "download"
FAIL_LOG = "failed.log"

print_lock = threading.Lock()
YT_OPTS = {}  # filled from --cookies-from-browser / --cookies / --js-runtimes


class QuietLogger:
    """Swallow yt-dlp's own output; we report errors ourselves."""
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def norm(s):
    return re.sub(r"\W+", " ", (s or "").lower()).strip()


def log(msg):
    with print_lock:
        print(msg, flush=True)


def log_failure(title, author, reason):
    with print_lock:
        with open(FAIL_LOG, "a", encoding="utf-8") as f:
            f.write(f"{title} | {author} | {reason}\n")


def clean_error(e):
    msg = str(e).strip().splitlines()[0]
    return re.sub(r"^ERROR:\s*", "", msg)[:200]


def find_file(video_id, ext):
    return glob.glob(os.path.join(OUT_DIR, f"*[[]{video_id}[]].{ext}"))


def pick_video(entries, title, author):
    """Best search hit <= 10 min by fuzzy title + channel match."""
    t, a = norm(title), norm(author)
    best, best_score, too_long = None, 0, 0
    for e in entries:
        if not e or not e.get("id"):
            continue
        dur = e.get("duration")
        if dur and dur > MAX_SECONDS:
            too_long += 1
            continue
        ratio = SequenceMatcher(None, norm(e.get("title")), t).ratio()
        chan = norm(e.get("channel") or e.get("uploader")) == a
        if ratio < 0.6 and not (chan and ratio >= 0.4):
            continue
        score = ratio + (0.5 if chan else 0)
        if score > best_score:
            best, best_score = e, score
    return best, too_long


def make_opts(has_ffmpeg):
    opts = {
        "logger": QuietLogger(),
        "noprogress": True,
        # prefer the native m4a (AAC) audio stream: no re-encoding, small and fast
        "format": "ba[ext=m4a]/ba/b",
        "outtmpl": os.path.join(OUT_DIR, "%(title).150B [%(id)s].%(ext)s"),
        "match_filter": yt_dlp.utils.match_filter_func(f"duration <= {MAX_SECONDS}"),
        "concurrent_fragment_downloads": 4,
        "retries": 3,
        "sleep_interval": 1,  # small pause before each download, looks less like a bot
        "max_sleep_interval": 3,
        **YT_OPTS,
    }
    if has_ffmpeg:
        # converts to m4a only if the stream isn't already AAC/m4a
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}]
    return opts


def process(song, has_ffmpeg):
    title, author = song["title"], song["author"]
    try:
        # 1) fast flat search (no per-video extraction)
        with yt_dlp.YoutubeDL({"logger": QuietLogger(), "extract_flat": True, **YT_OPTS}) as ydl:
            res = ydl.extract_info(f"ytsearch5:{title}", download=False)
        entries = res.get("entries", [])
        video, too_long = pick_video(entries, title, author)
        if not video:
            return title, author, "failed", f"no match <= 10 min ({too_long} of {len(entries)} results too long)"

        vid = video["id"]
        if find_file(vid, "m4a"):
            return title, author, "exists", vid

        # 2) download audio (saved as .m4a)
        with yt_dlp.YoutubeDL(make_opts(has_ffmpeg)) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={vid}"])

        if find_file(vid, "m4a"):
            return title, author, "ok", vid
        return title, author, "failed", "no audio file produced (filtered, or install ffmpeg)"
    except Exception as e:  # noqa: BLE001
        return title, author, "failed", clean_error(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json_file", nargs="?", default="songs.json")
    ap.add_argument("-w", "--workers", type=int, default=6, help="parallel downloads")
    ap.add_argument("--cookies-from-browser", metavar="BROWSER",
                    help="use YouTube cookies from a browser (firefox, chrome, ...) to avoid bot checks")
    ap.add_argument("--cookies", metavar="FILE", help="use a cookies.txt file instead")
    ap.add_argument("--js-runtimes", metavar="RUNTIME", default=None,
                    help="JavaScript runtime for YouTube challenges: deno (default), node, bun, quickjs")
    args = ap.parse_args()

    if args.js_runtimes:
        YT_OPTS["js_runtimes"] = {args.js_runtimes: {}}

    if args.cookies_from_browser:
        YT_OPTS["cookiesfrombrowser"] = (args.cookies_from_browser,)
    if args.cookies:
        YT_OPTS["cookiefile"] = args.cookies

    has_ffmpeg = shutil.which("ffmpeg") is not None
    if not has_ffmpeg:
        print("WARNING: ffmpeg not found. Only videos with a native m4a audio stream "
              "will work. Install ffmpeg for best results.\n")

    os.makedirs(OUT_DIR, exist_ok=True)
    open(FAIL_LOG, "w").close()
    with open(args.json_file, encoding="utf-8") as f:
        songs = json.load(f)

    total = len(songs)
    counts = {"ok": 0, "exists": 0, "failed": 0}
    pool = ThreadPoolExecutor(max_workers=args.workers)
    try:
        futures = [pool.submit(process, s, has_ffmpeg) for s in songs]
        for i, fut in enumerate(as_completed(futures), 1):
            title, author, status, info = fut.result()
            counts[status] += 1
            icon = {"ok": "✓", "exists": "=", "failed": "✗"}[status]
            log(f"[{i}/{total}] {icon} {title}" + (f"  ({info})" if status == "failed" else ""))
            if status == "failed":
                log_failure(title, author, info)
    except KeyboardInterrupt:
        print("\nInterrupted. Re-run to continue; finished songs are skipped.")
        os._exit(1)
    pool.shutdown()

    print(f"\nDownloaded: {counts['ok']}   Already there: {counts['exists']}   Failed: {counts['failed']}")
    if counts["failed"]:
        print(f"Failed songs logged in {FAIL_LOG}")


if __name__ == "__main__":
    main()
