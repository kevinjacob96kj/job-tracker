"""Local job tracker.

Storage: data/jobs.json
UI:      static/index.html, static/styles.css, static/app.js
Engine:  this file

Run: job-tracker.bat
Or:  python server.py
Then open http://127.0.0.1:8766
"""

from __future__ import annotations

import json
import re
import threading
import traceback
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error, request
from urllib.parse import parse_qs, urlparse
import html as html_lib

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "jobs.json"
STATIC = ROOT / "static"
HOST = "127.0.0.1"
PORT = 8766

STATUSES = ("saved", "applied", "interview", "offer", "rejected")
LOCK = threading.Lock()

TITLE_PATTERNS = (
    r'class="[^"]*top-card-layout__title[^"]*"[^>]*>\s*([^<]+)',
    r'class="[^"]*job-details-jobs-unified-top-card__job-title[^"]*"[^>]*>\s*([^<]+)',
    r'class="[^"]*topcard__title[^"]*"[^>]*>\s*([^<]+)',
)
COMPANY_PATTERNS = (
    r'class="[^"]*topcard__org-name-link[^"]*"[^>]*>\s*([^<]+)',
    r'class="[^"]*job-details-jobs-unified-top-card__company-name[^"]*"[^>]*>\s*(?:<a[^>]*>)?\s*([^<]+)',
)
LOCATION_PATTERNS = (
    r'class="[^"]*topcard__flavor--bullet[^"]*"[^>]*>\s*([^<]+)',
    r'class="[^"]*job-details-jobs-unified-top-card__bullet[^"]*"[^>]*>\s*([^<]+)',
    r'class="[^"]*topcard__flavor--metadata[^"]*"[^>]*>\s*([^<]+)',
)
JOB_ID_RE = re.compile(r"(?:currentJobId=|/jobs/view/).*?(\d{6,})")
INDEED_KEY_RE = re.compile(r"^[0-9a-f]{8,32}$", re.I)
SIGN_IN_MARKERS = ("sign in", "sign up", "join linkedin", "login", "security verification")
LOCATION_NOISE = re.compile(r"\b(?:ago|applicants?|reposted|promoted|easy apply)\b", re.I)
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


class _JobRedirects(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not site_of(newurl):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = request.build_opener(_JobRedirects)


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def site_of(url: str) -> str:
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in ("http", "https") or parsed.username:
        return ""
    if host == "indeed.com" or host.endswith(".indeed.com"):
        return "indeed"
    if host in ("linkedin.com", "lnkd.in") or host.endswith(".linkedin.com") or host.endswith(".lnkd.in"):
        return "linkedin"
    return ""


def indeed_job_key(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for name in ("vjk", "jk"):
        for value in query.get(name, []):
            value = value.strip()
            if INDEED_KEY_RE.fullmatch(value):
                return value.lower()
    match = re.search(r"/viewjob/([0-9a-f]{8,32})", parsed.path, re.I)
    return match.group(1).lower() if match else ""


def clean_field(value, limit: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("Expected text.")
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > limit:
        raise ValueError("One of the fields is too long.")
    return value


def clean_notes(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("Expected text.")
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(value) > 4000:
        raise ValueError("Notes are too long.")
    return value


def clean_url(value) -> str:
    value = clean_field(value, 2000)
    if not value:
        raise ValueError("Paste an Indeed job link.")
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    site = site_of(value)
    if site == "indeed":
        key = indeed_job_key(value)
        if not key:
            raise ValueError(
                "That Indeed link doesn't point at one job. Choose the job so the address includes vjk, then copy it again."
            )
        return f"https://www.indeed.com/viewjob?jk={key}"
    if site == "linkedin":
        return value
    raise ValueError("Paste an Indeed job link.")


def present(job: dict) -> dict:
    shown = dict(job)
    shown["job_url"] = shown.get("job_url") or shown.get("linkedin_url") or ""
    shown.pop("linkedin_url", None)
    return shown


def clean_status(value) -> str:
    if value in (None, ""):
        return "saved"
    if not isinstance(value, str) or value not in STATUSES:
        raise ValueError("Choose a valid status.")
    return value


def load_jobs() -> list:
    if not DATA.exists():
        return []
    try:
        with DATA.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError("data/jobs.json is not valid JSON.") from exc
    if not isinstance(data, list):
        raise ValueError("data/jobs.json must be a list.")
    return [item for item in data if isinstance(item, dict) and item.get("id")]


def save_jobs(jobs: list) -> None:
    DATA.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(jobs, indent=2) + "\n"
    tmp = DATA.with_suffix(".tmp")
    tmp.write_text(payload, encoding="utf-8")
    try:
        tmp.replace(DATA)
    except OSError:
        DATA.write_text(payload, encoding="utf-8")
        tmp.unlink(missing_ok=True)


def create_job(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Expected a listing.")
    title = clean_field(payload.get("title", ""), 140)
    if not title:
        raise ValueError("Add a job title.")
    job = {
        "id": str(uuid.uuid4()),
        "job_url": clean_url(payload.get("job_url") or payload.get("linkedin_url") or ""),
        "title": title,
        "company": clean_field(payload.get("company", ""), 140),
        "location": clean_field(payload.get("location", ""), 140),
        "status": clean_status(payload.get("status")),
        "notes": clean_notes(payload.get("notes", "")),
        "created_at": now(),
        "updated_at": now(),
    }
    with LOCK:
        jobs = load_jobs()
        jobs.insert(0, job)
        save_jobs(jobs)
    return present(job)


def update_job(job_id: str, payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Expected a listing.")
    with LOCK:
        jobs = load_jobs()
        for job in jobs:
            if job.get("id") != job_id:
                continue
            if "job_url" in payload or "linkedin_url" in payload:
                job["job_url"] = clean_url(payload.get("job_url") or payload.get("linkedin_url") or "")
                job.pop("linkedin_url", None)
            if "title" in payload:
                title = clean_field(payload.get("title", ""), 140)
                if not title:
                    raise ValueError("Add a job title.")
                job["title"] = title
            if "company" in payload:
                job["company"] = clean_field(payload.get("company", ""), 140)
            if "location" in payload:
                job["location"] = clean_field(payload.get("location", ""), 140)
            if "status" in payload:
                job["status"] = clean_status(payload.get("status"))
            if "notes" in payload:
                job["notes"] = clean_notes(payload.get("notes", ""))
            job["updated_at"] = now()
            save_jobs(jobs)
            return present(job)
    raise KeyError(job_id)


def delete_job(job_id: str) -> None:
    with LOCK:
        jobs = load_jobs()
        kept = [job for job in jobs if job.get("id") != job_id]
        if len(kept) == len(jobs):
            raise KeyError(job_id)
        save_jobs(kept)


def extract_job_id(url: str) -> str:
    match = JOB_ID_RE.search(url)
    return match.group(1) if match else ""


def clean_text(value: str) -> str:
    value = html_lib.unescape(value or "")
    value = re.sub(r"\s+", " ", value).strip(" \t\r\n-|")
    return value


def usable(value: str, limit: int = 180) -> str:
    value = clean_text(value)
    if not value or len(value) > limit:
        return ""
    lowered = value.lower()
    if any(marker in lowered for marker in SIGN_IN_MARKERS):
        return ""
    return value


def usable_location(value: str) -> str:
    value = usable(value, 140)
    if LOCATION_NOISE.search(value):
        return ""
    return value


def first_match(patterns, page: str, *, location: bool = False) -> str:
    for pattern in patterns:
        match = re.search(pattern, page, re.I | re.S)
        if not match:
            continue
        text = usable_location(match.group(1)) if location else usable(match.group(1))
        if text:
            return text
    return ""


def split_linkedin_title(title: str) -> tuple[str, str]:
    title = re.sub(r"\s*\|\s*LinkedIn\s*$", "", title, flags=re.I).strip()
    match = re.match(r"^(?P<title>.+?)\s+at\s+(?P<company>.+)$", title)
    if match and len(match.group("company")) < 80:
        return match.group("title").strip(), match.group("company").strip()
    if "|" in title:
        left, right = [part.strip() for part in title.split("|", 1)]
        return left, right
    return title, ""


def og_title(page: str) -> str:
    patterns = (
        r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"',
        r'<meta[^>]+content="([^"]+)"[^>]+property="og:title"',
        r"<title>(.*?)</title>",
    )
    return first_match(patterns, page)


def find_job_posting(data):
    if isinstance(data, list):
        for item in data:
            found = find_job_posting(item)
            if found:
                return found
        return {}
    if not isinstance(data, dict):
        return {}
    kind = data.get("@type", "")
    kinds = kind if isinstance(kind, list) else [kind]
    if any(str(item).lower() == "jobposting" for item in kinds):
        org = data.get("hiringOrganization") or {}
        company = org.get("name", "") if isinstance(org, dict) else ""
        location = data.get("jobLocation")
        if isinstance(location, list):
            location = location[0] if location else {}
        place = ""
        if isinstance(location, dict):
            address = location.get("address") or {}
            if isinstance(address, dict):
                place = address.get("addressLocality") or address.get("addressRegion") or ""
            elif isinstance(address, str):
                place = address
        return {
            "title": str(data.get("title") or ""),
            "company": str(company or ""),
            "location": str(place or ""),
        }
    graph = data.get("@graph")
    if graph:
        return find_job_posting(graph)
    return {}


def parse_jsonld(page: str) -> dict:
    for match in re.finditer(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
        page,
        re.I | re.S,
    ):
        blob = html_lib.unescape(match.group(1)).strip()
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        found = find_job_posting(data)
        if found:
            return found
    return {}


def parse_job_html(page: str) -> dict:
    title = first_match(TITLE_PATTERNS, page)
    company = first_match(COMPANY_PATTERNS, page)
    location = first_match(LOCATION_PATTERNS, page, location=True)
    linked = parse_jsonld(page)
    title = title or usable(str(linked.get("title", "")))
    company = company or usable(str(linked.get("company", "")))
    location = location or usable_location(str(linked.get("location", "")))
    shared = og_title(page)
    if shared:
        shared_title, shared_company = split_linkedin_title(shared)
        title = title or usable(shared_title)
        company = company or usable(shared_company)
    title = re.sub(r"\s*\|\s*LinkedIn\s*$", "", title, flags=re.I).strip()
    return {"title": title, "company": company, "location": location}


def clip(value: str, limit: int) -> str:
    value = clean_text(value)
    if not value:
        return ""
    lowered = value.lower()
    if any(marker in lowered for marker in SIGN_IN_MARKERS):
        return ""
    if len(value) > limit:
        value = value[:limit].rstrip(" -|,(;")
    return value


def json_string_field(page: str, key: str) -> str:
    pattern = re.compile(rf'"{re.escape(key)}"\s*:\s*"((?:\\.|[^"\\])*)"')
    for match in pattern.finditer(page):
        try:
            value = json.loads(f'"{match.group(1)}"')
        except json.JSONDecodeError:
            continue
        text = clip(str(value), 140)
        if text:
            return text
    return ""


def indeed_blocked(page: str) -> bool:
    head = page[:15000].lower()
    if "indeed_cloudflare_static_page" in head:
        return True
    return "security check" in head and '"jobtitle"' not in head


def parse_indeed_html(page: str) -> dict:
    if not page or indeed_blocked(page):
        return {"title": "", "company": "", "location": ""}
    title = json_string_field(page, "jobTitle")
    company = json_string_field(page, "companyName")
    location = usable_location(json_string_field(page, "jobLocation"))
    if title.lower() in {"security check", "just a moment...", "access denied"}:
        title = ""
    if not title:
        shared = re.sub(r"\s*-\s*Indeed(?:\.com)?\s*$", "", og_title(page), flags=re.I).strip()
        if shared.lower() not in {"security check", "just a moment...", "access denied"}:
            title = clip(shared, 140)
    return {"title": title, "company": company, "location": location}


def fetch_html(url: str) -> str:
    if not site_of(url):
        return ""
    mobile = site_of(url) == "indeed"
    req = request.Request(
        url,
        headers={
            "User-Agent": MOBILE_UA if mobile else DESKTOP_UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with _OPENER.open(req, timeout=12 if mobile else 6) as resp:
            raw = resp.read(1_500_000)
            charset = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, "replace")
    except error.HTTPError as exc:
        try:
            return exc.read(1_500_000).decode("utf-8", "replace")
        except Exception:
            return ""
    except (error.URLError, TimeoutError, ValueError, OSError):
        return ""


def preview_linkedin(url: str) -> dict:
    details = {"title": "", "company": "", "location": ""}
    pages = []
    job_id = extract_job_id(url)
    if job_id:
        pages.append(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}")
    pages.append(url)
    for page_url in pages:
        html = fetch_html(page_url)
        if not html:
            continue
        parsed = parse_job_html(html)
        for key in details:
            if not details[key] and parsed.get(key):
                details[key] = parsed[key]
        if details["title"] and details["company"]:
            break
    if details["title"]:
        message = "Filled in what LinkedIn shared. Change anything before saving."
    else:
        message = "LinkedIn didn't share public details for this link. Type them in below."
    return {"job_url": url, "message": message, **details}


def preview_indeed(url: str) -> dict:
    key = indeed_job_key(url)
    html = fetch_html(f"https://www.indeed.com/m/viewjob?jk={key}") if key else ""
    blocked = bool(html) and indeed_blocked(html)
    details = parse_indeed_html(html) if html else {"title": "", "company": "", "location": ""}
    if details["title"]:
        message = "Filled in the Indeed job from that link. Change anything before saving."
    elif blocked:
        message = "Indeed blocked the details for this job. Type the title and company below."
    else:
        message = "Indeed didn't share public details for this job. Type them in below."
    return {"job_url": url, "message": message, **details}


def preview_listing(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Expected a link.")
    url = clean_url(payload.get("url") or payload.get("job_url") or payload.get("linkedin_url") or "")
    if site_of(url) == "indeed":
        return preview_indeed(url)
    return preview_linkedin(url)


def read_body(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length") or "0")
    if length < 0 or length > 100_000:
        raise ValueError("Request is too large.")
    raw = handler.rfile.read(length) if length else b""
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Request body must be JSON.") from exc
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object.")
    return data


class Handler(BaseHTTPRequestHandler):
    server_version = "JobTracker/1.0"

    def do_GET(self):
        try:
            self._get()
        except ValueError as exc:
            self._send_json(500, {"error": str(exc)})
        except Exception:
            traceback.print_exc()
            self._send_json(500, {"error": "Something went wrong."})

    def do_POST(self):
        try:
            self._post()
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception:
            traceback.print_exc()
            self._send_json(500, {"error": "Something went wrong."})

    def do_PUT(self):
        try:
            self._put()
        except KeyError:
            self._send_json(404, {"error": "Listing not found."})
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception:
            traceback.print_exc()
            self._send_json(500, {"error": "Something went wrong."})

    def do_DELETE(self):
        try:
            self._delete()
        except KeyError:
            self._send_json(404, {"error": "Listing not found."})
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception:
            traceback.print_exc()
            self._send_json(500, {"error": "Something went wrong."})

    def _get(self):
        path = urlparse(self.path).path
        if path == "/api/jobs":
            jobs = [present(job) for job in load_jobs()]
            jobs.sort(key=lambda job: job.get("created_at", ""), reverse=True)
            self._send_json(200, jobs)
            return
        files = {
            "/": STATIC / "index.html",
            "/index.html": STATIC / "index.html",
            "/styles.css": STATIC / "styles.css",
            "/app.js": STATIC / "app.js",
        }
        target = files.get(path)
        if target is None or not target.is_file():
            self._send_json(404, {"error": "Not found."})
            return
        kind = "text/html" if target.suffix == ".html" else "text/css" if target.suffix == ".css" else "text/javascript"
        self._send(200, target.read_bytes(), f"{kind}; charset=utf-8")

    def _post(self):
        path = urlparse(self.path).path
        payload = read_body(self)
        if path == "/api/jobs":
            self._send_json(201, create_job(payload))
            return
        if path == "/api/preview":
            self._send_json(200, preview_listing(payload))
            return
        self._send_json(404, {"error": "Not found."})

    def _put(self):
        job_id = self._job_id()
        self._send_json(200, update_job(job_id, read_body(self)))

    def _delete(self):
        delete_job(self._job_id())
        self._send_json(200, {"ok": True})

    def _job_id(self) -> str:
        path = urlparse(self.path).path
        match = re.fullmatch(r"/api/jobs/([0-9a-fA-F-]{32,36})", path)
        if not match:
            raise KeyError(path)
        return match.group(1)

    def _send_json(self, status: int, payload) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))


def print_listen_target() -> None:
    """Print url|host|port for job-tracker.bat."""
    print(f"http://{HOST}:{PORT}|{HOST}|{PORT}")


def main() -> None:
    DATA.parent.mkdir(parents=True, exist_ok=True)
    if not DATA.exists():
        save_jobs([])
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Job tracker running at http://{HOST}:{PORT}")
    print(f"Listings are stored in {DATA}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
