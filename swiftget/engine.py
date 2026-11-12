"""SwiftGet download engine.

Pure-Python (stdlib only) multi-connection download engine with:
  * segmented downloads over HTTP Range requests
  * dynamic segment splitting (idle connections take over half of the
    largest remaining segment)
  * pause / resume with state persisted to disk
  * per-segment retry with back-off
  * global speed limiter
  * download queue with a configurable number of simultaneous downloads

The GUI never gets callbacks from worker threads; it polls the manager.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

CHUNK = 64 * 1024
MIN_SPLIT = 256 * 1024          # don't split segments smaller than 2x this
MAX_RETRIES = 8
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 SwiftGet/1.0")

CATEGORIES = {
    "Compressed": ["zip", "rar", "7z", "gz", "tar", "bz2", "xz", "tgz", "iso",
                   "cab", "lz", "zst"],
    "Documents": ["pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt",
                  "rtf", "odt", "ods", "odp", "epub", "csv", "md"],
    "Music": ["mp3", "wav", "flac", "aac", "ogg", "m4a", "wma", "opus",
              "aif", "aiff"],
    "Programs": ["exe", "msi", "apk", "dmg", "deb", "rpm", "appimage", "bat",
                 "jar", "msix"],
    "Video": ["mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v", "mpg",
              "mpeg", "3gp", "ts"],
    "Images": ["jpg", "jpeg", "png", "gif", "bmp", "webp", "svg", "tiff",
               "ico", "heic"],
}
ALL_EXTS = {e for exts in CATEGORIES.values() for e in exts}

# statuses
QUEUED, CONNECTING, DOWNLOADING, PAUSED, COMPLETE, ERROR = (
    "Queued", "Connecting", "Downloading", "Paused", "Complete", "Error")
ACTIVE_STATES = (CONNECTING, DOWNLOADING)


# --------------------------------------------------------------- helpers
def category_for(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower().lstrip(".")
    for cat, exts in CATEGORIES.items():
        if ext in exts:
            return cat
    return "General"


def looks_like_download_url(text: str) -> bool:
    text = text.strip()
    if not re.match(r"^(https?|ftp)://\S+$", text, re.I):
        return False
    path = urllib.parse.urlparse(text).path
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    return ext in ALL_EXTS


def human_size(n) -> str:
    if n is None or n < 0:
        return "Unknown"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return str(n)


def human_speed(bps) -> str:
    if not bps:
        return ""
    return human_size(bps) + "/s"


def human_time(sec) -> str:
    if sec is None or sec < 0:
        return ""
    sec = int(sec)
    if sec >= 86400:
        return f"{sec // 86400}d {sec % 86400 // 3600}h"
    if sec >= 3600:
        return f"{sec // 3600}h {sec % 3600 // 60}m"
    if sec >= 60:
        return f"{sec // 60}m {sec % 60}s"
    return f"{sec}s"


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    return name[:200] or "download"


def unique_path(path: str) -> str:
    if not os.path.exists(path) and not os.path.exists(path + ".part"):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while True:
        p = f"{base} ({i}){ext}"
        if not os.path.exists(p) and not os.path.exists(p + ".part"):
            return p
        i += 1


def _filename_from_headers(headers, url: str) -> str:
    cd = headers.get("Content-Disposition", "") or ""
    m = re.search(r"filename\*\s*=\s*([^']*)'[^']*'([^;]+)", cd, re.I)
    if m:
        try:
            return sanitize_filename(urllib.parse.unquote(
                m.group(2).strip().strip('"'), encoding=m.group(1) or "utf-8"))
        except LookupError:
            pass
    m = re.search(r'filename\s*=\s*"([^"]+)"', cd, re.I) or \
        re.search(r"filename\s*=\s*([^;]+)", cd, re.I)
    if m:
        return sanitize_filename(m.group(1).strip())
    path = urllib.parse.urlparse(url).path
    name = urllib.parse.unquote(os.path.basename(path))
    return sanitize_filename(name) if name else "download"


def _open(url, start=None, end=None, timeout=30):
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*",
               "Accept-Encoding": "identity"}
    if start is not None:
        headers["Range"] = f"bytes={start}-" + ("" if end is None else str(end))
    req = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(req, timeout=timeout)


def probe(url: str, timeout=20) -> dict:
    """Find size, filename, and whether the server supports ranges."""
    resp = _open(url, 0, None, timeout)
    try:
        code = resp.status
        final_url = resp.geturl()
        size = -1
        resumable = False
        if code == 206:
            cr = resp.headers.get("Content-Range", "")
            m = re.search(r"/(\d+)", cr)
            if m:
                size = int(m.group(1))
                resumable = True
        else:
            cl = resp.headers.get("Content-Length")
            if cl and cl.isdigit():
                size = int(cl)
        return {
            "size": size,
            "resumable": resumable,
            "filename": _filename_from_headers(resp.headers, final_url),
            "final_url": final_url,
            "content_type": resp.headers.get("Content-Type", ""),
        }
    finally:
        resp.close()


class RateLimiter:
    """Shared token bucket. rate = bytes/second, 0 = unlimited."""

    def __init__(self, rate=0):
        self.rate = rate
        self._allow = 0.0
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def consume(self, n):
        if self.rate <= 0:
            return
        with self._lock:
            now = time.monotonic()
            self._allow = min(self.rate, self._allow + (now - self._last) * self.rate)
            self._last = now
            self._allow -= n
            wait = -self._allow / self.rate if self._allow < 0 else 0
        if wait > 0:
            time.sleep(min(wait, 2.0))


# --------------------------------------------------------------- model
class Download:
    def __init__(self, url, save_dir, filename=None, connections=8, did=None):
        self.id = did or uuid.uuid4().hex[:12]
        self.url = url
        self.save_dir = save_dir
        self.filename = filename or ""
        self.connections = connections
        self.size = -1
        self.resumable = False
        self.segments: list[list] = []   # [start, end_inclusive, pos]
        self.status = QUEUED
        self.error = ""
        self.added = time.time()
        self.last_try = None
        self.completed_at = None
        self.description = ""
        self.speed = 0.0
        self.active_conns = 0
        self.conn_info: dict[int, str] = {}
        self._dl_single = 0     # bytes for non-segmented downloads
        self.lock = threading.Lock()

    # derived
    @property
    def path(self):
        return os.path.join(self.save_dir, self.filename)

    @property
    def part_path(self):
        return self.path + ".part"

    @property
    def category(self):
        return category_for(self.filename)

    @property
    def downloaded(self):
        if self.status == COMPLETE and self.size > 0:
            return self.size
        if self.segments:
            return sum(max(0, s[2] - s[0]) for s in self.segments)
        return self._dl_single

    @property
    def progress(self):
        if self.size and self.size > 0:
            return min(1.0, self.downloaded / self.size)
        return 1.0 if self.status == COMPLETE else 0.0

    @property
    def eta(self):
        if self.size > 0 and self.speed > 0:
            return (self.size - self.downloaded) / self.speed
        return None

    def to_dict(self):
        with self.lock:
            return {
                "id": self.id, "url": self.url, "save_dir": self.save_dir,
                "filename": self.filename, "connections": self.connections,
                "size": self.size, "resumable": self.resumable,
                "segments": [list(s) for s in self.segments],
                "status": self.status if self.status not in ACTIVE_STATES else PAUSED,
                "error": self.error, "added": self.added,
                "last_try": self.last_try, "completed_at": self.completed_at,
                "description": self.description,
            }

    @classmethod
    def from_dict(cls, d):
        o = cls(d["url"], d["save_dir"], d.get("filename"),
                d.get("connections", 8), d.get("id"))
        o.size = d.get("size", -1)
        o.resumable = d.get("resumable", False)
        o.segments = [list(s) for s in d.get("segments", [])]
        o.status = d.get("status", PAUSED)
        o.error = d.get("error", "")
        o.added = d.get("added", time.time())
        o.last_try = d.get("last_try")
        o.completed_at = d.get("completed_at")
        o.description = d.get("description", "")
        return o


# --------------------------------------------------------------- worker
class Job(threading.Thread):
    def __init__(self, manager, dl: Download):
        super().__init__(daemon=True, name=f"job-{dl.id}")
        self.m = manager
        self.d = dl
        self.stop_evt = threading.Event()
        self.fail_evt = threading.Event()
        self.threads: list[threading.Thread] = []

    # ---- orchestration
    def run(self):
        d = self.d
        d.last_try = time.time()
        d.error = ""
        d.status = CONNECTING
        try:
            if not d.segments or not d.resumable or not os.path.exists(d.part_path):
                self._fresh_start()
            if self.stop_evt.is_set():
                d.status = PAUSED
                return
            d.status = DOWNLOADING
            if d.resumable and d.size > 0:
                self._run_segmented()
            else:
                self._run_single()
            if self.stop_evt.is_set():
                d.status = PAUSED
            elif self.fail_evt.is_set():
                d.status = ERROR
            else:
                self._finalize()
        except Exception as e:  # noqa: BLE001
            d.status = ERROR
            d.error = _err_text(e)
        finally:
            d.speed = 0
            d.active_conns = 0
            self.m._job_finished(self)

    def _fresh_start(self):
        d = self.d
        info = probe(d.url)
        d.size = info["size"]
        d.resumable = info["resumable"]
        if not d.filename:
            d.filename = info["filename"]
        if not d.save_dir:
            d.save_dir = self.m.dir_for(d.filename)
        os.makedirs(d.save_dir, exist_ok=True)
        if not os.path.exists(d.part_path):
            # pick final unique name now so the .part file matches
            d.filename = os.path.basename(unique_path(d.path))
        d.segments = []
        d._dl_single = 0
        if d.resumable and d.size > 0:
            n = max(1, min(d.connections, d.size // MIN_SPLIT or 1))
            step = d.size // n
            for i in range(n):
                s = i * step
                e = d.size - 1 if i == n - 1 else (i + 1) * step - 1
                d.segments.append([s, e, s])
            with open(d.part_path, "wb") as f:
                f.truncate(d.size)
        else:
            open(d.part_path, "wb").close()
        self.m.save()

    def _run_segmented(self):
        d = self.d
        with d.lock:
            pending = [s for s in d.segments if s[2] <= s[1]]
        idx = 0
        for seg in pending[: d.connections]:
            self._spawn(seg, idx)
            idx += 1
        # if fewer unfinished segments than connections, split to fill
        while idx < d.connections:
            seg = self._steal()
            if seg is None:
                break
            self._spawn(seg, idx)
            idx += 1
        self._monitor()

    def _spawn(self, seg, idx):
        t = threading.Thread(target=self._seg_worker, args=(seg, idx), daemon=True)
        self.threads.append(t)
        t.start()

    def _steal(self):
        """Split the biggest remaining segment; return new segment or None."""
        d = self.d
        with d.lock:
            best = None
            for s in d.segments:
                rem = s[1] - s[2] + 1
                if rem > 2 * MIN_SPLIT and (best is None or rem > best[1] - best[2] + 1):
                    best = s
            if best is None:
                return None
            rem = best[1] - best[2] + 1
            mid = best[2] + rem // 2
            new = [mid, best[1], mid]
            best[1] = mid - 1
            d.segments.append(new)
            d.segments.sort(key=lambda s: s[0])
            return new

    def _seg_worker(self, seg, idx):
        d = self.d
        retries = 0
        while not self.stop_evt.is_set() and not self.fail_evt.is_set():
            with d.lock:
                if seg[2] > seg[1]:
                    done = True
                else:
                    done = False
                    start, end = seg[2], seg[1]
            if done:
                d.conn_info[idx] = "Finished segment, looking for more…"
                nxt = self._steal()
                if nxt is None:
                    d.conn_info[idx] = "Done"
                    return
                seg = nxt
                retries = 0
                continue
            try:
                d.conn_info[idx] = "Connecting…"
                resp = _open(d.url, start, end)
                if resp.status != 206:
                    resp.close()
                    raise IOError(f"Server ignored range request (HTTP {resp.status})")
                d.conn_info[idx] = "Receiving data…"
                with d.lock:
                    d.active_conns += 1
                try:
                    with open(d.part_path, "r+b") as f:
                        f.seek(start)
                        while not self.stop_evt.is_set():
                            data = resp.read(CHUNK)
                            if not data:
                                break
                            with d.lock:
                                allowed = seg[1] - seg[2] + 1
                                if allowed <= 0:
                                    break
                                if len(data) > allowed:
                                    data = data[:allowed]
                                f.seek(seg[2])
                                f.write(data)
                                seg[2] += len(data)
                            self.m.limiter.consume(len(data))
                            retries = 0
                finally:
                    resp.close()
                    with d.lock:
                        d.active_conns -= 1
            except Exception as e:  # noqa: BLE001
                if self.stop_evt.is_set():
                    return
                retries += 1
                d.conn_info[idx] = f"Error: {_err_text(e)} (retry {retries})"
                if retries > MAX_RETRIES:
                    d.error = _err_text(e)
                    self.fail_evt.set()
                    return
                self.stop_evt.wait(min(30, 2 ** retries))

    def _run_single(self):
        d = self.d
        d.conn_info = {0: "Connecting…"}
        t = threading.Thread(target=self._single_worker, daemon=True)
        self.threads.append(t)
        t.start()
        self._monitor()

    def _single_worker(self):
        d = self.d
        try:
            resp = _open(d.url)
            d.conn_info[0] = "Receiving data (server does not support resume)…"
            d.active_conns = 1
            d._dl_single = 0
            with resp, open(d.part_path, "wb") as f:
                while not self.stop_evt.is_set():
                    data = resp.read(CHUNK)
                    if not data:
                        break
                    f.write(data)
                    d._dl_single += len(data)
                    self.m.limiter.consume(len(data))
            if not self.stop_evt.is_set() and d.size <= 0:
                d.size = d._dl_single
            d.conn_info[0] = "Done"
        except Exception as e:  # noqa: BLE001
            d.error = _err_text(e)
            self.fail_evt.set()

    def _monitor(self):
        d = self.d
        last_bytes = d.downloaded
        last_t = time.monotonic()
        last_save = last_t
        while any(t.is_alive() for t in self.threads):
            time.sleep(0.5)
            now = time.monotonic()
            cur = d.downloaded
            inst = (cur - last_bytes) / max(1e-6, now - last_t)
            d.speed = inst if d.speed == 0 else 0.7 * d.speed + 0.3 * inst
            last_bytes, last_t = cur, now
            if now - last_save > 3:
                self.m.save()
                last_save = now
        if self.fail_evt.is_set():
            self.stop_evt.set()

    def _finalize(self):
        d = self.d
        if d.segments and any(s[2] <= s[1] for s in d.segments):
            raise IOError("Download incomplete")
        final = d.path
        if os.path.exists(final):
            final = unique_path(final)
            d.filename = os.path.basename(final)
        os.replace(d.part_path, final)
        d.status = COMPLETE
        d.completed_at = time.time()
        d.segments = [[0, d.size - 1, d.size]] if d.size > 0 else []


def _err_text(e) -> str:
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code} {e.reason}"
    if isinstance(e, urllib.error.URLError):
        return str(e.reason)
    return str(e) or e.__class__.__name__


# --------------------------------------------------------------- manager
DEFAULT_SETTINGS = {
    "save_dir": os.path.join(os.path.expanduser("~"), "Downloads", "SwiftGet"),
    "sort_by_category": True,
    "connections": 8,
    "max_active": 3,
    "speed_limit_kb": 0,
    "monitor_clipboard": True,
    "show_progress_dialog": True,
    "show_complete_dialog": True,
}


class DownloadManager:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.state_file = os.path.join(data_dir, "downloads.json")
        self.settings_file = os.path.join(data_dir, "settings.json")
        self.downloads: list[Download] = []
        self.jobs: dict[str, Job] = {}
        self.queue_running = True
        self.lock = threading.RLock()
        self.settings = dict(DEFAULT_SETTINGS)
        self.limiter = RateLimiter()
        self.just_completed: list[Download] = []
        self._load()
        self.apply_settings()

    # ---- persistence
    def _load(self):
        try:
            with open(self.settings_file, encoding="utf-8") as f:
                self.settings.update(json.load(f))
        except (OSError, ValueError):
            pass
        try:
            with open(self.state_file, encoding="utf-8") as f:
                for item in json.load(f):
                    self.downloads.append(Download.from_dict(item))
        except (OSError, ValueError, KeyError):
            pass

    def save(self):
        with self.lock:
            data = [d.to_dict() for d in self.downloads]
        tmp = self.state_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
            os.replace(tmp, self.state_file)
        except OSError:
            pass

    def save_settings(self):
        try:
            with open(self.settings_file, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=1)
        except OSError:
            pass
        self.apply_settings()

    def apply_settings(self):
        self.limiter.rate = max(0, int(self.settings.get("speed_limit_kb", 0))) * 1024

    def dir_for(self, filename):
        base = self.settings["save_dir"]
        if self.settings.get("sort_by_category") and filename:
            return os.path.join(base, category_for(filename))
        return base

    # ---- operations
    def add(self, url, filename=None, save_dir=None, start=True,
            connections=None, info=None, description=""):
        if not save_dir:
            save_dir = self.dir_for(filename) if filename else ""   # "" = decide after probe
        d = Download(url, save_dir, filename,
                     connections or int(self.settings["connections"]))
        d.description = description
        if info:
            d.size = info.get("size", -1)
            d.resumable = info.get("resumable", False)
        d.status = QUEUED if start else PAUSED
        with self.lock:
            self.downloads.append(d)
        self.save()
        if start:
            self.schedule()
        return d

    def get(self, did):
        with self.lock:
            for d in self.downloads:
                if d.id == did:
                    return d
        return None

    def resume(self, d: Download, force=False):
        if d.status in ACTIVE_STATES or d.status == COMPLETE:
            return
        d.status = QUEUED
        if force:
            self._start(d)
        else:
            self.schedule()

    def pause(self, d: Download):
        job = self.jobs.get(d.id)
        if job:
            job.stop_evt.set()
        elif d.status == QUEUED:
            d.status = PAUSED
        self.save()

    def pause_all(self):
        with self.lock:
            for d in self.downloads:
                if d.status in ACTIVE_STATES or d.status == QUEUED:
                    self.pause(d)

    def resume_all(self):
        with self.lock:
            for d in self.downloads:
                if d.status in (PAUSED, ERROR):
                    d.status = QUEUED
        self.schedule()

    def redownload(self, d: Download):
        self.pause(d)
        job = self.jobs.get(d.id)
        if job:
            job.join(5)
        for p in (d.part_path,):
            try:
                os.remove(p)
            except OSError:
                pass
        d.segments = []
        d.status = QUEUED
        d.completed_at = None
        self.schedule()

    def remove(self, d: Download, delete_file=False):
        job = self.jobs.get(d.id)
        if job:
            job.stop_evt.set()
            job.join(5)
        with self.lock:
            if d in self.downloads:
                self.downloads.remove(d)
        try:
            if d.status != COMPLETE and os.path.exists(d.part_path):
                os.remove(d.part_path)
            if delete_file and d.status == COMPLETE and os.path.exists(d.path):
                os.remove(d.path)
        except OSError:
            pass
        self.save()

    def remove_completed(self):
        with self.lock:
            self.downloads = [d for d in self.downloads if d.status != COMPLETE]
        self.save()

    def schedule(self):
        """Start queued downloads up to max_active."""
        if not self.queue_running:
            return
        with self.lock:
            active = sum(1 for d in self.downloads if d.status in ACTIVE_STATES)
            for d in self.downloads:
                if active >= int(self.settings["max_active"]):
                    break
                if d.status == QUEUED and d.id not in self.jobs:
                    self._start(d)
                    active += 1

    def _start(self, d: Download):
        with self.lock:
            if d.id in self.jobs:
                return
            job = Job(self, d)
            self.jobs[d.id] = job
            d.status = CONNECTING
        job.start()

    def _job_finished(self, job: Job):
        with self.lock:
            self.jobs.pop(job.d.id, None)
            if job.d.status == COMPLETE:
                self.just_completed.append(job.d)
        self.save()
        self.schedule()

    def shutdown(self):
        for job in list(self.jobs.values()):
            job.stop_evt.set()
        for job in list(self.jobs.values()):
            job.join(3)
        self.save()
