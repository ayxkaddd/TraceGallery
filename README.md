# gallery-dl OSINT Archive Helper

A local-first analyst dashboard for building and running `gallery-dl` archive jobs from a browser. It provides target queues, case-oriented folder paths, cookie options, command/config previews, presets, live logs, and stop controls.

## Install

Create a Python environment and install backend dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Install `gallery-dl` if it is not already available:

```bash
pip install gallery-dl
gallery-dl --version
```

## Run

Start the app:

```bash
python -m backend.main
```

Open `http://127.0.0.1:4000` in a browser.

To choose a different port:

```bash
python -m backend.main --port 5000
```

## Workflow

1. Enter or paste one or more target URLs.
2. Enter the output directory path, for example `/home/user/osint-archives/case-name/target-name/`.
3. Fill in case name, target label, notes, and folder naming mode.
4. Choose the cookie mode.
5. Enable metadata, archive, skip, and filename options.
6. Review the generated command and JSON config preview.
7. Click **Run archive**.
8. Watch live logs.
9. Stop the job or open the output folder when supported by the backend OS.

## Cookies

Cookie handling is explicit:

- **No cookies** runs `gallery-dl` without cookie arguments.
- **Select cookies.txt** passes `--cookies /path/to/cookies.txt`.
- **Browser cookies** only becomes usable after clicking **Detect browser profiles** and selecting a browser/profile. The backend returns profile labels only and relies on `gallery-dl --cookies-from-browser`.

The backend never returns or prints raw cookie values.

## Backend API

- `GET /api/health`
- `GET /api/gallery-dl/version`
- `POST /api/paths/validate`
- `POST /api/config/render`
- `POST /api/config/save`
- `POST /api/jobs`
- `GET /api/jobs/{job_id}`
- `GET /api/jobs/{job_id}/events`
- `POST /api/jobs/{job_id}/stop`
- `POST /api/paths/open`
- `POST /api/library/scan`
- `GET /api/library/{root_id}/files/{relative_path}`
- `GET /api/library/{root_id}/metadata/{relative_path}`
- `GET /api/browser-profiles`