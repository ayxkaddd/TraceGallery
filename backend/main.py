from __future__ import annotations

import asyncio
import argparse
import configparser
import hashlib
import json
import mimetypes
import os
import platform
import re
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


APP_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = APP_ROOT / "frontend"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4000

LOCAL_ORIGINS = [
    "*"
]

app = FastAPI(title="gallery-dl OSINT Archive Helper")
app.add_middleware(
    CORSMiddleware,
    allow_origins=LOCAL_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


CookieMode = Literal["none", "file", "browser"]
FolderMode = Literal["flat", "case-target", "case-target-date", "case-target-datetime"]


class DownloadOptions(BaseModel):
    images: bool = True
    videos: bool = True
    metadata: bool = True
    infoJson: bool = True
    captions: bool = False
    archive: bool = True
    continueArchive: bool = True
    skipExisting: bool = True
    restrictFilenames: bool = True
    saveUnsupported: bool = True


class AdvancedOptions(BaseModel):
    userAgent: str = ""
    proxy: str = ""
    sleepDelay: str = ""
    rateLimit: str = ""
    filenameTemplate: str = ""
    directoryTemplate: str = ""
    extraArgs: str = ""


class BrowserProfile(BaseModel):
    browser: str
    profile: str = ""


class JobConfig(BaseModel):
    urls: list[str] = Field(default_factory=list)
    outputDir: str = ""
    createOutputDir: bool = True
    caseName: str = ""
    targetLabel: str = ""
    notes: str = ""
    folderMode: FolderMode = "case-target-date"
    cookieMode: CookieMode = "none"
    cookiesFile: str = ""
    browserProfile: BrowserProfile | None = None
    options: DownloadOptions = Field(default_factory=DownloadOptions)
    advanced: AdvancedOptions = Field(default_factory=AdvancedOptions)


class PathRequest(BaseModel):
    path: str
    create: bool = False


class SaveConfigRequest(BaseModel):
    config: JobConfig
    filename: str = "gallery-dl.conf"


class LibraryScanRequest(BaseModel):
    path: str
    maxFiles: int = 3000
    maxDepth: int = 8


class Job:
    def __init__(self, job_id: str, config: JobConfig, command: list[str], output_dir: Path) -> None:
        self.id = job_id
        self.config = config
        self.command = command
        self.output_dir = output_dir
        self.status = "queued"
        self.exit_code: int | None = None
        self.created_at = datetime.now().isoformat(timespec="seconds")
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.process: asyncio.subprocess.Process | None = None
        self.logs: list[str] = []
        self.subscribers: set[asyncio.Queue[str]] = set()

    async def emit(self, message: str) -> None:
        self.logs.append(message)
        if len(self.logs) > 2000:
            self.logs = self.logs[-2000:]
        for queue in list(self.subscribers):
            await queue.put(message)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "exitCode": self.exit_code,
            "createdAt": self.created_at,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "outputDir": str(self.output_dir),
        }


jobs: dict[str, Job] = {}
library_roots: dict[str, Path] = {}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".ogg", ".wav", ".flac", ".opus"}
TEXT_EXTENSIONS = {".json", ".txt", ".md", ".log", ".csv", ".html"}


def slug(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return cleaned or fallback


def split_extra_args(value: str) -> list[str]:
    if not value.strip():
        return []
    import shlex

    return shlex.split(value)


def classify_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    if suffix == ".json":
        return "metadata"
    if suffix in TEXT_EXTENSIONS:
        return "text"
    return "other"


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def first_http_url(value: Any) -> str:
    if isinstance(value, str) and re.match(r"^https?://", value):
        return value
    if isinstance(value, list):
        for item in value:
            url = first_http_url(item)
            if url:
                return url
    if isinstance(value, dict):
        for item in value.values():
            url = first_http_url(item)
            if url:
                return url
    return ""


def metadata_value_for_key(value: Any, target_keys: set[str]) -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in target_keys:
                return item
        for item in value.values():
            found = metadata_value_for_key(item, target_keys)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = metadata_value_for_key(item, target_keys)
            if found:
                return found
    return None


def metadata_text(value: Any, *keys: str) -> str:
    found = metadata_value_for_key(value, {key.lower() for key in keys})
    if isinstance(found, (str, int)):
        return str(found).strip()
    return ""


def filename_post_id(path: Path | None) -> str:
    if not path:
        return ""
    match = re.match(r"^(\d{16,22})(?:\D|$)", path.name)
    return match.group(1) if match else ""


def reconstructed_original_url(metadata: dict[str, Any]) -> str:
    category = metadata_text(metadata, "category", "extractor_key", "extractor").lower()
    subcategory = metadata_text(metadata, "subcategory").lower()
    username = metadata_text(metadata, "uniqueId", "username", "user", "owner", "screen_name", "authorName")
    post_id = metadata_text(metadata, "post_id", "tweet_id", "status_id", "shortcode", "id")

    if ("twitter" in category or category == "x") and username and post_id:
        return f"https://x.com/{username}/status/{post_id}"
    if "instagram" in category and post_id:
        if subcategory in {"reel", "reels"}:
            return f"https://www.instagram.com/reel/{post_id}/"
        return f"https://www.instagram.com/p/{post_id}/"
    if "tiktok" in category and username and post_id:
        return f"https://www.tiktok.com/@{username}/video/{post_id}"
    if "reddit" in category and post_id:
        return f"https://www.reddit.com/comments/{post_id}/"
    return ""


def metadata_original_url(metadata: Any, media_path: Path | None = None) -> str:
    if not isinstance(metadata, dict):
        return ""

    preferred_keys = (
        "post_url",
        "webpage_url",
        "original_url",
        "permalink",
        "link",
        "page_url",
        "gallery_url",
        "parent_url",
        "referer",
    )
    for key in preferred_keys:
        url = first_http_url(metadata_value_for_key(metadata, {key}))
        if url:
            return url

    if filename_post_id(media_path) and not metadata_text(metadata, "post_id", "tweet_id", "status_id", "shortcode", "id"):
        metadata = {**metadata, "id": filename_post_id(media_path)}

    reconstructed = reconstructed_original_url(metadata)
    if reconstructed:
        return reconstructed

    fallback_keys = ("url", "source", "urls")
    for key in fallback_keys:
        url = first_http_url(metadata_value_for_key(metadata, {key}))
        if url:
            return url
    return ""


def load_metadata_summary(path: Path, media_path: Path | None = None) -> dict[str, str]:
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    original_url = metadata_original_url(metadata, media_path)
    return {"originalUrl": original_url} if original_url else {}


def metadata_sidecar_for(path: Path) -> Path | None:
    candidates = (
        path.with_name(path.name + ".json"),
        path.with_name(path.name + ".info.json"),
        path.with_suffix(path.suffix + ".json"),
        path.with_suffix(".json"),
        path.with_suffix(".info.json"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def directory_metadata_for(path: Path) -> Path | None:
    candidate = path.parent / "info.json"
    return candidate if candidate.is_file() else None


def safe_file_path(root_id: str, relative_path: str) -> Path:
    root = library_roots.get(root_id)
    if not root:
        raise HTTPException(status_code=404, detail="Library root not found. Scan the folder again.")
    path = (root / relative_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid file path.") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found.")
    return path


def iter_files_bounded(root: Path, max_files: int, max_depth: int):
    stack: list[tuple[Path, int]] = [(root, 0)]
    yielded = 0
    truncated = False
    while stack:
        directory, depth = stack.pop()
        if depth > max_depth:
            truncated = True
            continue
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                stack.append((entry, depth + 1))
                continue
            if not entry.is_file():
                continue
            if yielded >= max_files:
                iter_files_bounded.truncated = True  # type: ignore[attr-defined]
                return
            yielded += 1
            yield entry
    iter_files_bounded.truncated = truncated  # type: ignore[attr-defined]


def scan_library(path_value: str, max_files: int = 3000, max_depth: int = 8) -> dict[str, Any]:
    root = ensure_local_path(path_value, False).resolve()
    root_id = uuid.uuid4().hex
    library_roots[root_id] = root

    items: list[dict[str, Any]] = []
    counts = {"image": 0, "video": 0, "audio": 0, "metadata": 0, "text": 0, "other": 0}
    total_size = 0
    iter_files_bounded.truncated = False  # type: ignore[attr-defined]
    for path in iter_files_bounded(root, max_files, max_depth):
        kind = classify_file(path)
        counts[kind] += 1
        stat = path.stat()
        total_size += stat.st_size
        relative = path.relative_to(root).as_posix()
        sidecar = metadata_sidecar_for(path)
        sidecar_relative = sidecar.relative_to(root).as_posix() if sidecar else ""
        summary_source = sidecar if sidecar else path if kind == "metadata" else directory_metadata_for(path)
        encoded_relative = quote(relative, safe="/")
        items.append({
            "name": path.name,
            "relativePath": relative,
            "kind": kind,
            "size": stat.st_size,
            "sizeLabel": format_size(stat.st_size),
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            "mime": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "sha256": sha256_file(path),
            "url": f"/api/library/{root_id}/files/{encoded_relative}",
            "sidecar": sidecar_relative,
            "summary": load_metadata_summary(summary_source, path) if summary_source else {},
        })

    return {
        "rootId": root_id,
        "path": str(root),
        "counts": counts,
        "totalSize": total_size,
        "totalSizeLabel": format_size(total_size),
        "truncated": bool(getattr(iter_files_bounded, "truncated", False)),
        "maxFiles": max_files,
        "items": items,
    }


def ensure_local_path(path_value: str, create: bool, materialize: bool = True) -> Path:
    if not path_value.strip():
        raise HTTPException(status_code=400, detail="Output directory is required.")

    path = Path(path_value).expanduser().resolve()
    if path.exists() and not path.is_dir():
        raise HTTPException(status_code=400, detail="Output path exists but is not a directory.")
    if not path.exists():
        if not create:
            raise HTTPException(status_code=400, detail="Output directory does not exist.")
        if materialize:
            path.mkdir(parents=True, exist_ok=True)
    return path


def open_with_default_app(path: Path) -> str:
    system = platform.system()
    if system == "Linux":
        for command in (["xdg-open", str(path)], ["gio", "open", str(path)], ["kde-open5", str(path)], ["kde-open", str(path)], ["gnome-open", str(path)], ["exo-open", str(path)]):
            if shutil.which(command[0]):
                subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                return command[0]
    elif system == "Darwin":
        subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return "open"
    elif system == "Windows":
        os.startfile(str(path))  # type: ignore[attr-defined]
        return "startfile"

    raise HTTPException(status_code=400, detail="No supported default folder opener was found.")


def pick_directory_with_native_dialog() -> tuple[Path, str]:
    system = platform.system()
    if system == "Linux":
        commands = [
            ["kdialog", "--getexistingdirectory", str(Path.home())],
            ["zenity", "--file-selection", "--directory", "--title=Select output directory"],
            ["qarma", "--file-selection", "--directory", "--title=Select output directory"],
        ]
        for command in commands:
            if not shutil.which(command[0]):
                continue
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            selected = result.stdout.strip()
            if result.returncode == 0 and selected:
                return Path(selected).expanduser().resolve(), command[0]
            if result.returncode not in (0, 1):
                continue
        return pick_directory_with_tk()

    if system == "Darwin":
        script = 'POSIX path of (choose folder with prompt "Select output directory")'
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=False)
        selected = result.stdout.strip()
        if result.returncode == 0 and selected:
            return Path(selected).expanduser().resolve(), "osascript"
        raise HTTPException(status_code=400, detail="Directory selection was cancelled.")

    if system == "Windows":
        return pick_directory_with_tk()

    raise HTTPException(status_code=400, detail="Native directory picker is not supported on this OS.")


def pick_directory_with_tk() -> tuple[Path, str]:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"No native directory picker is available: {exc}") from exc

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    selected = filedialog.askdirectory(title="Select output directory", initialdir=str(Path.home()))
    root.destroy()
    if not selected:
        raise HTTPException(status_code=400, detail="Directory selection was cancelled.")
    return Path(selected).expanduser().resolve(), "tkinter"


def existing_profile_dirs(base: Path) -> list[Path]:
    if not base.is_dir():
        return []
    names = ["Default", "Profile *", "Guest Profile"]
    paths: list[Path] = []
    for name in names:
        paths.extend(base.glob(name))
    return [path for path in paths if path.is_dir()]


def env_path(name: str, *parts: str) -> Path | None:
    value = os.environ.get(name)
    if not value:
        return None
    return Path(value).joinpath(*parts)


def detect_firefox_profiles(home: Path) -> list[dict[str, str]]:
    roots: list[Path] = [
        home / ".mozilla" / "firefox",
        home / ".var" / "app" / "org.mozilla.firefox" / ".mozilla" / "firefox",
        home / "Library" / "Application Support" / "Firefox",
    ]
    windows_root = env_path("APPDATA", "Mozilla", "Firefox")
    if windows_root:
        roots.append(windows_root)

    profiles: list[dict[str, str]] = []
    for root in roots:
        ini_path = root / "profiles.ini"
        if not ini_path.is_file():
            continue
        parser = configparser.ConfigParser()
        parser.read(ini_path)
        for section in parser.sections():
            if not section.lower().startswith("profile"):
                continue
            raw_path = parser.get(section, "Path", fallback="")
            if not raw_path:
                continue
            profile_path = root / raw_path if parser.get(section, "IsRelative", fallback="1") == "1" else Path(raw_path)
            if not profile_path.is_dir():
                continue
            name = parser.get(section, "Name", fallback=profile_path.name)
            profiles.append({
                "browser": "firefox",
                "profile": str(profile_path),
                "label": f"Firefox - {name}",
                "path": str(profile_path),
            })
    return profiles


def detect_chromium_profiles(home: Path) -> list[dict[str, str]]:
    candidates: list[tuple[str, str, Path]] = [
        ("chromium", "Chromium", home / ".config" / "chromium"),
        ("chrome", "Google Chrome", home / ".config" / "google-chrome"),
        ("brave", "Brave", home / ".config" / "BraveSoftware" / "Brave-Browser"),
        ("edge", "Microsoft Edge", home / ".config" / "microsoft-edge"),
        ("chromium", "Chromium Flatpak", home / ".var" / "app" / "org.chromium.Chromium" / "config" / "chromium"),
        ("chrome", "Google Chrome Flatpak", home / ".var" / "app" / "com.google.Chrome" / "config" / "google-chrome"),
        ("brave", "Brave Flatpak", home / ".var" / "app" / "com.brave.Browser" / "config" / "BraveSoftware" / "Brave-Browser"),
        ("chrome", "Google Chrome", home / "Library" / "Application Support" / "Google" / "Chrome"),
        ("chromium", "Chromium", home / "Library" / "Application Support" / "Chromium"),
        ("brave", "Brave", home / "Library" / "Application Support" / "BraveSoftware" / "Brave-Browser"),
        ("edge", "Microsoft Edge", home / "Library" / "Application Support" / "Microsoft Edge"),
    ]
    windows_candidates = [
        ("chrome", "Google Chrome", env_path("LOCALAPPDATA", "Google", "Chrome", "User Data")),
        ("chromium", "Chromium", env_path("LOCALAPPDATA", "Chromium", "User Data")),
        ("brave", "Brave", env_path("LOCALAPPDATA", "BraveSoftware", "Brave-Browser", "User Data")),
        ("edge", "Microsoft Edge", env_path("LOCALAPPDATA", "Microsoft", "Edge", "User Data")),
    ]
    candidates.extend((browser, label, path) for browser, label, path in windows_candidates if path)

    profiles: list[dict[str, str]] = []
    for browser, label, root in candidates:
        for profile_path in existing_profile_dirs(root):
            profiles.append({
                "browser": browser,
                "profile": profile_path.name,
                "label": f"{label} - {profile_path.name}",
                "path": str(profile_path),
            })
    return profiles


def detect_browser_profiles() -> list[dict[str, str]]:
    home = Path.home()
    profiles = detect_firefox_profiles(home) + detect_chromium_profiles(home)
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, str]] = []
    for profile in profiles:
        key = (profile["browser"], profile["profile"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(profile)
    return unique


def validate_url(url: str) -> str:
    value = url.strip()
    if not value:
        raise HTTPException(status_code=400, detail="Empty URLs are not allowed.")
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        raise HTTPException(status_code=400, detail=f"URL does not include a scheme: {value}")
    return value


def final_output_dir(config: JobConfig, materialize: bool = True) -> Path:
    base = ensure_local_path(config.outputDir, config.createOutputDir, materialize)
    now = datetime.now()
    case_name = slug(config.caseName, "case")
    target = slug(config.targetLabel, "target")

    if config.folderMode == "flat":
        return base
    if config.folderMode == "case-target":
        return base / case_name / target
    if config.folderMode == "case-target-date":
        return base / case_name / target / now.strftime("%Y-%m-%d")
    return base / case_name / target / now.strftime("%Y-%m-%d_%H-%M-%S")


def build_gallery_dl_config(config: JobConfig, output_dir: Path) -> dict[str, Any]:
    extractor: dict[str, Any] = {"base-directory": str(output_dir)}
    downloader: dict[str, Any] = {}
    output: dict[str, Any] = {}

    if config.options.metadata:
        extractor["write-metadata"] = True
    if config.options.infoJson:
        extractor["write-info-json"] = True
    if config.options.captions:
        extractor["write-tags"] = True
    if config.options.archive:
        extractor["archive"] = str(output_dir / "archive.sqlite3")
    if config.options.skipExisting:
        extractor["skip"] = True
    if config.options.restrictFilenames:
        extractor["restrict-filenames"] = "ascii"
    if config.options.saveUnsupported:
        extractor["write-unsupported"] = str(output_dir / "unsupported-urls.txt")
    if config.advanced.userAgent:
        extractor["user-agent"] = config.advanced.userAgent
    if config.advanced.proxy:
        extractor["proxy"] = config.advanced.proxy
    if config.advanced.sleepDelay:
        extractor["sleep"] = config.advanced.sleepDelay
    if config.advanced.rateLimit:
        downloader["rate"] = config.advanced.rateLimit
    if config.advanced.filenameTemplate:
        extractor["filename"] = config.advanced.filenameTemplate
    if config.advanced.directoryTemplate:
        extractor["directory"] = config.advanced.directoryTemplate

    output["mode"] = "terminal"
    output["progress"] = True

    return {
        "extractor": extractor,
        "downloader": {"*": downloader},
        "output": output,
        "notes": {
            "case": config.caseName,
            "target": config.targetLabel,
            "analystNotes": config.notes,
        },
    }


def build_command(config: JobConfig, materialize: bool = True) -> tuple[list[str], Path, dict[str, Any]]:
    urls = [validate_url(url) for url in config.urls]
    if not urls:
        raise HTTPException(status_code=400, detail="At least one target URL is required.")

    output_dir = final_output_dir(config, materialize)
    if materialize:
        output_dir.mkdir(parents=True, exist_ok=True)

    args = ["gallery-dl", "--directory", str(output_dir)]

    if config.cookieMode == "file":
        cookie_path = Path(config.cookiesFile).expanduser().resolve()
        if not cookie_path.is_file():
            raise HTTPException(status_code=400, detail="Selected cookies.txt file does not exist.")
        args.extend(["--cookies", str(cookie_path)])
    elif config.cookieMode == "browser":
        if not config.browserProfile or not config.browserProfile.browser:
            raise HTTPException(status_code=400, detail="Select a browser profile before using browser cookies.")
        browser_value = config.browserProfile.browser
        if config.browserProfile.profile:
            browser_value = f"{browser_value}:{config.browserProfile.profile}"
        args.extend(["--cookies-from-browser", browser_value])

    if config.options.metadata:
        args.append("--write-metadata")
    if config.options.infoJson:
        args.append("--write-info-json")
    if config.options.captions:
        args.append("--write-tags")
    if config.options.archive:
        args.extend(["--download-archive", str(output_dir / "archive.sqlite3")])
    if config.options.restrictFilenames:
        args.extend(["--restrict-filenames", "ascii"])
    if config.options.saveUnsupported:
        args.extend(["--write-unsupported", str(output_dir / "unsupported-urls.txt")])
    if not config.options.images and not config.options.videos:
        args.append("--no-download")
    if config.advanced.userAgent:
        args.extend(["--user-agent", config.advanced.userAgent])
    if config.advanced.proxy:
        args.extend(["--proxy", config.advanced.proxy])
    if config.advanced.sleepDelay:
        args.extend(["--sleep", config.advanced.sleepDelay])
    if config.advanced.rateLimit:
        args.extend(["--limit-rate", config.advanced.rateLimit])
    if config.advanced.filenameTemplate:
        args.extend(["--filename", config.advanced.filenameTemplate])
    if config.advanced.directoryTemplate:
        args.extend(["--option", f"directory={config.advanced.directoryTemplate}"])

    args.extend(split_extra_args(config.advanced.extraArgs))
    args.extend(urls)
    return args, output_dir, build_gallery_dl_config(config, output_dir)


def shell_preview(args: list[str]) -> str:
    import shlex

    if not args:
        return ""
    lines = [shlex.quote(args[0])]
    for arg in args[1:]:
        lines[-1] += " \\"
        lines.append(f"  {shlex.quote(arg)}")
    return "\n".join(lines)


def redact_args(args: list[str]) -> list[str]:
    redacted = []
    skip_next = False
    sensitive = {"--cookies", "--cookies-from-browser"}
    for index, arg in enumerate(args):
        if skip_next:
            redacted.append("[redacted]")
            skip_next = False
            continue
        redacted.append(arg)
        if arg in sensitive and index + 1 < len(args):
            skip_next = True
    return redacted


async def run_job(job: Job) -> None:
    if shutil.which("gallery-dl") is None:
        job.status = "failed"
        job.finished_at = datetime.now().isoformat(timespec="seconds")
        await job.emit("ERROR: gallery-dl was not found on PATH.")
        return

    job.status = "running"
    job.started_at = datetime.now().isoformat(timespec="seconds")
    await job.emit(f"Command started: {shell_preview(redact_args(job.command))}")

    try:
        job.process = await asyncio.create_subprocess_exec(
            *job.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(job.output_dir),
        )
        assert job.process.stdout is not None
        while True:
            line = await job.process.stdout.readline()
            if not line:
                break
            await job.emit(line.decode(errors="replace").rstrip())
        job.exit_code = await job.process.wait()
        job.status = "finished" if job.exit_code == 0 else "failed"
        await job.emit(f"Finished with exit code {job.exit_code}.")
    except asyncio.CancelledError:
        job.status = "stopped"
        raise
    except Exception as exc:
        job.status = "failed"
        await job.emit(f"ERROR: {exc}")
    finally:
        job.finished_at = datetime.now().isoformat(timespec="seconds")
        for queue in list(job.subscribers):
            await queue.put("__DONE__")


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "host": "127.0.0.1"}


@app.get("/api/gallery-dl/version")
async def gallery_dl_version() -> dict[str, str]:
    binary = shutil.which("gallery-dl")
    if not binary:
        return {"installed": "false", "version": "not found"}
    result = subprocess.run([binary, "--version"], capture_output=True, text=True, check=False)
    return {"installed": "true", "version": result.stdout.strip() or result.stderr.strip()}


@app.post("/api/paths/validate")
async def validate_path(payload: PathRequest) -> dict[str, Any]:
    path = ensure_local_path(payload.path, payload.create)
    return {"ok": True, "path": str(path), "exists": path.exists()}


@app.post("/api/config/render")
async def render_config(config: JobConfig) -> dict[str, Any]:
    args, output_dir, rendered = build_command(config, materialize=False)
    return {
        "command": shell_preview(args),
        "safeArgs": redact_args(args),
        "config": rendered,
        "configJson": json.dumps(rendered, indent=2),
        "finalOutputDir": str(output_dir),
    }


@app.post("/api/config/save")
async def save_config(payload: SaveConfigRequest) -> dict[str, str]:
    _, output_dir, rendered = build_command(payload.config)
    filename = slug(payload.filename, "gallery-dl.conf")
    path = output_dir / filename
    path.write_text(json.dumps(rendered, indent=2), encoding="utf-8")
    return {"path": str(path)}


@app.post("/api/jobs")
async def start_job(config: JobConfig) -> dict[str, str]:
    command, output_dir, _ = build_command(config)
    job_id = uuid.uuid4().hex
    job = Job(job_id, config, command, output_dir)
    jobs[job_id] = job
    asyncio.create_task(run_job(job))
    return {"jobId": job_id}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job.snapshot()


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    queue: asyncio.Queue[str] = asyncio.Queue()
    job.subscribers.add(queue)

    async def stream():
        try:
            for line in job.logs[-200:]:
                yield f"data: {json.dumps(line)}\n\n"
            while True:
                line = await queue.get()
                if line == "__DONE__":
                    yield f"event: done\ndata: {json.dumps(job.snapshot())}\n\n"
                    break
                yield f"data: {json.dumps(line)}\n\n"
        finally:
            job.subscribers.discard(queue)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/jobs/{job_id}/stop")
async def stop_job(job_id: str) -> dict[str, str]:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if not job.process or job.process.returncode is not None:
        job.status = "stopped"
        return {"status": job.status}

    job.status = "stopping"
    await job.emit("Stopping job...")
    job.process.terminate()
    try:
        await asyncio.wait_for(job.process.wait(), timeout=8)
    except asyncio.TimeoutError:
        job.process.kill()
        await job.process.wait()
    job.status = "stopped"
    job.exit_code = job.process.returncode
    await job.emit(f"Stopped with exit code {job.exit_code}.")
    return {"status": job.status}


@app.post("/api/paths/open")
async def open_path(payload: PathRequest) -> dict[str, str]:
    path = ensure_local_path(payload.path, payload.create)
    opener = open_with_default_app(path)
    return {"status": "opened", "path": str(path), "opener": opener}


@app.post("/api/paths/pick")
async def pick_path() -> dict[str, str]:
    path, picker = await asyncio.to_thread(pick_directory_with_native_dialog)
    if not path.is_dir():
        raise HTTPException(status_code=400, detail="Selected path is not a directory.")
    return {"path": str(path), "picker": picker}


@app.post("/api/library/scan")
async def library_scan(payload: LibraryScanRequest) -> dict[str, Any]:
    max_files = min(max(payload.maxFiles, 1), 10000)
    max_depth = min(max(payload.maxDepth, 0), 20)
    return await asyncio.to_thread(scan_library, payload.path, max_files, max_depth)


@app.get("/api/library/{root_id}/files/{relative_path:path}")
async def library_file(root_id: str, relative_path: str) -> FileResponse:
    path = safe_file_path(root_id, relative_path)
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0])


@app.get("/api/library/{root_id}/metadata/{relative_path:path}")
async def library_metadata(root_id: str, relative_path: str) -> dict[str, Any]:
    path = safe_file_path(root_id, relative_path)
    if path.suffix.lower() != ".json":
        raise HTTPException(status_code=400, detail="Metadata file must be JSON.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse JSON: {exc}") from exc


@app.get("/api/browser-profiles")
async def browser_profiles() -> dict[str, Any]:
    profiles = detect_browser_profiles()
    if not profiles:
        return {
            "profiles": [],
            "notice": "No supported local browser profiles were found.",
        }
    return {
        "profiles": profiles,
        "notice": "Choose only accounts/profiles you own or have permission to use. Cookies stay local.",
    }


if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


def run_server() -> None:
    parser = argparse.ArgumentParser(description="Run the gallery-dl OSINT Archive Helper.")
    parser.add_argument("--host", default=os.environ.get("GALLERY_DL_FRONTEND_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", os.environ.get("GALLERY_DL_FRONTEND_PORT", DEFAULT_PORT))))
    parser.add_argument("--reload", action="store_true", help="Restart the server when source files change.")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run("backend.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    run_server()
