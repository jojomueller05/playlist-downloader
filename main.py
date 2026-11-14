#!/usr/bin/env python3
"""Scrape the playlist from the app's screen via adb (dump + parse in one).

Live mode:   open the playlist in the app, scroll to the top, then
             python playlist_scraper.py [-o songs.json]
File mode:   parse existing uiautomator dumps instead of scrolling
             python playlist_scraper.py ui1.xml ui2.xml [-o songs.json]
"""
import argparse
import json
import subprocess
import time
import xml.etree.ElementTree as ET

PKG = "com.freemusic.offline.bus"
TITLE_ID = f"{PKG}:id/text_title"
AUTHOR_ID = f"{PKG}:id/text_channel_title"


def adb(*args):
    return subprocess.run(["adb", *args], capture_output=True, text=True, check=True).stdout


def parse_xml(xml):
    """Return [(title, author), ...] in on-screen order."""
    songs, title = [], None
    for node in ET.fromstring(xml).iter("node"):
        rid = node.get("resource-id")
        if rid == TITLE_ID:
            title = node.get("text", "").strip()
        elif rid == AUTHOR_ID and title is not None:
            songs.append((title, node.get("text", "").strip()))
            title = None
    return songs


def live_screens(max_scrolls):
    """Yield the parsed songs of each screen while scrolling down."""
    size = adb("shell", "wm", "size").strip().splitlines()[-1].split(":")[-1].strip()
    w, h = map(int, size.split("x"))
    x, y1, y2 = w // 2, int(h * 0.75), int(h * 0.35)

    for _ in range(max_scrolls):
        adb("shell", "uiautomator", "dump", "/sdcard/ui_tmp.xml")
        yield parse_xml(adb("exec-out", "cat", "/sdcard/ui_tmp.xml"))
        # slow swipe so the list doesn't fling past rows
        adb("shell", "input", "swipe", str(x), str(y1), str(x), str(y2), "800")
        time.sleep(0.8)


def file_screens(paths):
    for p in paths:
        with open(p, encoding="utf-8") as f:
            yield parse_xml(f.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", help="existing dumps (omit for live mode)")
    ap.add_argument("-o", "--output", default="songs.json")
    ap.add_argument("--max-scrolls", type=int, default=300)
    args = ap.parse_args()

    screens = file_screens(args.files) if args.files else live_screens(args.max_scrolls)

    seen, result, processed, empty_rounds = set(), [], 0, 0
    for screen in screens:
        processed += len(screen)
        new = 0
        for title, author in screen:
            if (title, author) not in seen:
                seen.add((title, author))
                result.append({"title": title, "author": author})
                new += 1
                print(f"{len(result):>3}. {title}  —  {author}")

        # live mode: two dumps in a row with nothing new = bottom of the list
        empty_rounds = empty_rounds + 1 if new == 0 else 0
        if not args.files and empty_rounds >= 2:
            break

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\nRecords processed: {processed}")
    print(f"Unique songs:      {len(result)}")
    print(f"Saved to:          {args.output}")


if __name__ == "__main__":
    main()
