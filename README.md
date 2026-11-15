# Playlist Downloader

Two small Python scripts that rebuild a playlist from an old Android music app (package name `com.freemusic.offline.bus`) and download the audio of the songs from YouTube as `.m4a` (audio only).

The app has no export function, and the playlist is probably stored in the app's private storage, which can't be read without root. So `playlist_scraper.py` reads the song list straight from the screen with `adb` instead.

```
Phone (playlist open) --adb--> playlist_scraper.py --> songs.json --> download_songs.py --> ./download/*.m4a
```

## Files

| File | Purpose |
|------|---------|
| `playlist_scraper.py` | Scrolls the playlist on the phone, dumps each screen, extracts title and author, writes `songs.json` |
| `download_songs.py` | Reads `songs.json`, finds each video on YouTube, downloads it to `./download` |
| `songs.json` | Generated. List of `{ "title": ..., "author": ... }` |
| `download/` | Generated. The downloaded audio files (`.m4a`) |
| `failed.log` | Generated. Songs that couldn't be downloaded, with the reason |

## Requirements

- Python 3.9+
- [`adb`](https://developer.android.com/tools/adb) (Android platform-tools) on your PC
- A phone with Developer options and **USB debugging** enabled
- For downloading:
  - `yt-dlp`: `pip install -U "yt-dlp[default]"`
  - `ffmpeg` (converts the audio if a video has no native m4a stream; without it, only videos that already have an m4a stream work)
  - A JavaScript runtime, which yt-dlp needs to solve YouTube's challenges: [Deno](https://deno.com) 2.3.0 or newer (used by default), or Node.js 22 or newer (then add `--js-runtimes node`, see below)
  - A browser where you are logged into YouTube (Firefox works best), to avoid bot checks

## Step 1: Scrape the playlist

1. Plug in the phone, accept the USB debugging prompt, and check the connection:
   ```
   adb devices
   ```
2. On the phone, open the playlist (for example Favorites) in the app and **scroll to the top**.
3. Keep the phone unlocked and the screen on, then run:
   ```
   python playlist_scraper.py
   ```

The script dumps the screen with `uiautomator`, reads the song title (`text_title`) and channel name (`text_channel_title`), swipes down, and repeats. It stops when two dumps in a row show nothing new. It prints every song as it goes, then the number of records processed, the number of unique songs, and writes `songs.json`.

**Options**

```
python playlist_scraper.py -o favorites.json        # different output file
python playlist_scraper.py --max-scrolls 500        # safety limit (default 300)
python playlist_scraper.py ui1.xml ui2.xml          # parse existing dumps, no phone needed
```

**Tips**

- If songs are missing, the swipe skips rows. In `live_screens`, raise `0.35` (the swipe end position) to about `0.45` so screens overlap more.
- To fit more rows per screen and speed things up, temporarily shrink the display: `adb shell wm density 300`. Restore it afterwards with `adb shell wm density reset`.
- The "author" is the YouTube channel name, and titles may contain tags such as "(Lyrics)".

## Step 2: Download the songs

```
python download_songs.py -w 2 --cookies-from-browser firefox
```

If you use Node instead of Deno, add `--js-runtimes node`. YouTube often blocks plain, anonymous downloads, so the cookies flag is recommended (see Troubleshooting).

For each entry the script searches YouTube for the title, looks at the top 5 results, and picks the best match by fuzzy title plus channel name. Videos **longer than 10 minutes are never downloaded**. Only the **audio** is downloaded, as a small `.m4a` file (YouTube's native AAC stream, so usually there is no re-encoding, and there is no video). Downloads run in parallel and land in `./download` as `Title [videoid].m4a`.

**Options**

```
python download_songs.py favorites.json               # use another JSON file
python download_songs.py -w 2                         # parallel downloads (default 6)
python download_songs.py --cookies-from-browser firefox   # use YouTube cookies from a browser
python download_songs.py --cookies cookies.txt            # or a cookies.txt file
python download_songs.py --js-runtimes node               # use Node instead of Deno (also bun, quickjs)
```

To try it on a few songs first:

```
python -c "import json;json.dump(json.load(open('songs.json'))[:5],open('test.json','w'))"
python download_songs.py test.json -w 1 --cookies-from-browser firefox
```

- If `./download` still contains full videos from an earlier run, delete them first, otherwise those songs are skipped as "already there".
- Songs already in `./download` are detected by video ID and skipped, so you can re-run safely to retry failures or continue after Ctrl+C.
- Failures are printed with a ✗ and written to `failed.log` as `title | author | reason`. The log is overwritten on every run, so it only shows the failures of the latest run.
- Going far above about 8 workers can get you rate-limited by YouTube. If you get bot checks, use `-w 2`. The script also waits 1 to 3 seconds before each download.
- Cookies from your browser come from your own YouTube login. Automated downloading while signed in can get an account flagged, so consider a separate browser profile with a spare account.

## Troubleshooting

| Problem | Likely cause and fix |
|---------|----------------------|
| `Sign in to confirm you're not a bot` | YouTube blocked anonymous requests. Add `--cookies-from-browser firefox` (while logged into YouTube in that browser) and use fewer workers (`-w 2`) |
| `Requested format is not available`, or warnings like `Signature solving failed` / `Only images are available` | yt-dlp has no JavaScript runtime or solver scripts. Install Deno 2.3.0+ (or Node 22+ and add `--js-runtimes node`), run `pip install -U "yt-dlp[default]"` (installs `yt-dlp-ejs`), and test with `yt-dlp --cookies-from-browser firefox -F <url>`. Otherwise update yt-dlp and check that ffmpeg is installed |
| `no match <= 10 min` | No search result matched the title, or the matches were over 10 minutes (the message says how many). Check the title in `failed.log` and download it manually if needed |
| Scraper finds 0 songs | The playlist isn't open on screen, or the app was updated and the view IDs changed. Run `adb shell uiautomator dump /sdcard/ui.xml`, pull it, and check the `resource-id` values |
| `adb: no devices` | Re-plug the cable, re-accept the USB debugging prompt, and run `adb devices` |
| Missing songs in the JSON | See the swipe tip above |

## Notes

- Only download content you have the right to download.
- The original plan was to copy the app's SQLite database, but `.persistence.db` only contains a `download_streams` table (download tracking), not the playlists, and the WAL/SHM files couldn't be read over MTP (input/output error) or with `adb pull` (permission denied). Reading them would probably need root.
- `parse_dump.py` and `scrape_playlist.py` from earlier are superseded by `playlist_scraper.py` and can be deleted.
