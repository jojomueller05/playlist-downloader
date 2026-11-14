#!/usr/bin/env python3
"""Download the songs from songs.json as .mp4 from YouTube (skips videos > 10 min).

Setup:   pip install -U "yt-dlp[default]"   + ffmpeg + deno (see notes)
Usage:   python download_songs.py [songs.json] [-w 6] [-q 720]

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


def build_format(has_ffmpeg, max_height):
    h = f"[height<={max_height}]" if max_height else ""
    if has_ffmpeg:
        return f"bv*[ext=mp4]{h}+ba[ext=m4a]/bv*{h}+ba/b{h}/b"
    return f"b[ext=mp4]{h}/b{h}/b"  # single-file only, no merging possible


def make_opts(fmt):
    return {
        "logger": QuietLogger(),
        "noprogress": True,
        "format": fmt,
        "merge_output_format": "mp4",
        "outtmpl": os.path.join(OUT_DIR, "%(title).150B [%(id)s].%(ext)s"),
        "match_filter": yt_dlp.utils.match_filter_func(f"duration <= {MAX_SECONDS}"),
        "concurrent_fragment_downloads": 4,
        "retries": 3,
    }


def process(song, fmt):
    title, author = song["title"], song["author"]
    try:
        # 1) fast flat search (no per-video extraction)
        with yt_dlp.YoutubeDL({"logger": QuietLogger(), "extract_flat": True}) as ydl:
            res = ydl.extract_info(f"ytsearch5:{title}", download=False)
        entries = res.get("entries", [])
        video, too_long = pick_video(entries, title, author)
        if not video:
            return title, author, "failed", f"no match <= 10 min ({too_long} of {len(entries)} results too long)"

        found = os.path.join(OUT_DIR, f"*[[]{video['id']}[]].mp4")
        if glob.glob(found):
            return title, author, "exists", video["id"]

        # 2) download
        with yt_dlp.YoutubeDL(make_opts(fmt)) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={video['id']}"])

        if glob.glob(found):
            return title, author, "ok", video["id"]
        return title, author, "failed", "no mp4 produced (filtered or merge failed)"
    except Exception as e:  # noqa: BLE001
        return title, author, "failed", clean_error(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json_file", nargs="?", default="songs.json")
    ap.add_argument("-w", "--workers", type=int, default=6, help="parallel downloads")
    ap.add_argument("-q", "--max-height", type=int, default=720,
                    help="max video height, 0 = best available")
    args = ap.parse_args()

    has_ffmpeg = shutil.which("ffmpeg") is not None
    if not has_ffmpeg:
        print("WARNING: ffmpeg not found. YouTube mostly serves video and audio separately, "
              "so many downloads will fail. Install ffmpeg.\n")
    fmt = build_format(has_ffmpeg, args.max_height)

    os.makedirs(OUT_DIR, exist_ok=True)
    open(FAIL_LOG, "w").close()
    with open(args.json_file, encoding="utf-8") as f:
        songs = json.load(f)

    total = len(songs)
    counts = {"ok": 0, "exists": 0, "failed": 0}
    pool = ThreadPoolExecutor(max_workers=args.workers)
    try:
        futures = [pool.submit(process, s, fmt) for s in songs]
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
