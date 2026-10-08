import hashlib
import json
import re
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from .models import Job, Status, ToolResult, now


class Provider(Protocol):
    def search(self, query: str, filters: dict[str, str], cursor: str | None = None) -> ToolResult: ...
    def fetch_detail(self, job: Job) -> ToolResult: ...


def canonical_url(value: str) -> str:
    p = urlsplit(value)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ValueError("job source URL must be http(s)")
    # Only BOSS tracking queries are stripped; other sites may identify a job in the query.
    query = "" if p.hostname in ("www.zhipin.com", "zhipin.com") else p.query
    return urlunsplit((p.scheme, p.netloc, p.path, query, ""))


def identity(source: str, source_id: str) -> str:
    return hashlib.sha256(f"{source}:{source_id}".encode()).hexdigest()[:24]


def normalize_boss(raw: dict, previous: Job | None = None) -> Job:
    data = raw.get("jobInfo", raw)
    if not isinstance(data, dict):
        raise ValueError("jobInfo is not an object")
    sid = str(data.get("encryptJobId") or data.get("encryptId") or "")
    url = data.get("jobUrl") or data.get("url") or ""
    if sid and not url:
        url = f"https://www.zhipin.com/job_detail/{sid}.html"
    if url.startswith("/"):
        url = "https://www.zhipin.com" + url
    if not sid and url:
        match = re.search(r"/job_detail/([^/?]+)\.html", url)
        sid = match.group(1) if match else canonical_url(url)
    if previous:
        if sid and sid != previous.source_id:
            raise ValueError("detail identity differs from requested job")
        sid, url = previous.source_id, previous.url
    if not sid or not url:
        raise ValueError("missing durable job ID or URL; securityId alone is insufficient")
    brand = raw.get("brandComInfo") or {}
    locator = dict(previous.locator) if previous else {}
    for key in ("securityId", "lid"):
        if data.get(key) or raw.get(key):
            locator[key] = str(data.get(key) or raw[key])
    return Job(
        id=identity("boss", sid), source="boss", source_id=sid, url=canonical_url(url),
        title=data.get("jobName") or (previous.title if previous else ""),
        company=brand.get("brandName") or data.get("brandName") or (previous.company if previous else ""),
        city=data.get("locationName") or data.get("cityName") or (previous.city if previous else ""),
        salary=data.get("salaryDesc") or (previous.salary if previous else ""),
        job_type=data.get("jobTypeName") or (previous.job_type if previous else ""),
        description=data.get("postDescription") or data.get("jobDesc") or raw.get("jobDesc") or "",
        provenance="live", locator=locator, raw=raw,
    )


def import_jobs(records: list[dict], provenance="imported") -> ToolResult:
    jobs = []
    try:
        for record in records:
            d = dict(record)
            d["url"] = canonical_url(d["url"])
            d.setdefault("source", "import")
            d.setdefault("source_id", d["url"])
            d["id"] = identity(d["source"], d["source_id"])
            d["provenance"] = "synthetic" if d.get("provenance") == "synthetic" else provenance
            d.setdefault("raw", dict(record))
            d.setdefault("fetched_at", now())
            jobs.append(Job.model_validate(d))
    except (ValueError, TypeError, KeyError):
        return ToolResult(status=Status.PARSE, message="invalid import; no records imported")
    return ToolResult(status=Status.OK if jobs else Status.EMPTY, jobs=jobs)


def load_jobs(path: str | Path) -> list[Job]:
    data = json.loads(Path(path).read_text())
    result = import_jobs(data)
    if result.status == Status.PARSE:
        raise ValueError(result.message)
    return result.jobs


class Snapshot:
    supported_filters = {"city", "job_type"}
    filter_notes = "任职类型缺失保留候选，交给详情核验。"
    """Deterministic local retrieval. Search exposes metadata, never hidden full JDs."""
    def __init__(self, jobs: list[Job], page_size=10):
        self.catalog = {j.id: j for j in jobs}
        self.page_size = page_size

    def search(self, query, filters=None, cursor=None) -> ToolResult:
        filters = filters or {}
        terms = re.findall(r"[a-zA-Z0-9+#]+|[\u4e00-\u9fff]+", query.lower())
        ranked = []
        for job in self.catalog.values():
            city = filters.get("city", "全国")
            if city != "全国" and city not in job.city:
                continue
            if filters.get("job_type") and job.job_type and filters["job_type"] not in job.job_type:
                continue
            text = " ".join([job.title, job.company, job.city, job.job_type]).lower()
            score = sum(term in text for term in terms)
            if score:
                ranked.append((score, job.id, job))
        ranked.sort(key=lambda x: (-x[0], x[1]))
        start = int(cursor or 0)
        selected = [j.model_copy(update={"description": "", "raw": {}, "locator": {}})
                    for _, _, j in ranked[start:start + self.page_size]]
        return ToolResult(status=Status.OK if selected else Status.EMPTY, jobs=selected,
                          cursor=str(start + self.page_size) if start + self.page_size < len(ranked) else None)

    def fetch_detail(self, job: Job) -> ToolResult:
        full = self.catalog.get(job.id)
        if not full or not full.description.strip():
            return ToolResult(status=Status.PARSE, message="detail unavailable in imported snapshot")
        return ToolResult(status=Status.OK, jobs=[full])
