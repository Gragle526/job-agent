"""Read-only adapter for the public endpoints used by Tencent Careers.

This is a website integration, not a promised stable public developer API.
No login, application submission or recruiter-contact endpoints are used.
"""
import re

import httpx

from .models import Job, Status, ToolResult
from .providers import canonical_url, identity


class TencentCareers:
    supported_filters = {"city", "job_type", "country"}
    filter_notes = "job_type只支持实习；城市与任职类型在结果中核验。"
    source = "tencent-careers"
    base = "https://careers.tencent.com"

    def __init__(self, client=None):
        self.client = client or httpx.Client(timeout=20, follow_redirects=True)

    def _get(self, endpoint, params):
        try:
            response = self.client.get(f"{self.base}/tencentcareer/api/post/{endpoint}",
                                       params={"language": "zh-cn", **params})
            if response.status_code in (401, 403, 429):
                return Status.BLOCKED, None, f"HTTP {response.status_code}"
            if response.status_code >= 400:
                return Status.UNAVAILABLE, None, f"HTTP {response.status_code}"
            raw = response.json()
            if not isinstance(raw, dict) or raw.get("Code") != 200:
                return Status.PARSE, raw, "unexpected response schema or business status"
            if not isinstance(raw.get("Data"), dict):
                return Status.PARSE, raw, "missing data object"
            return Status.OK, raw, ""
        except httpx.TimeoutException:
            return Status.TIMEOUT, None, "public source timed out"
        except httpx.HTTPError:
            return Status.UNAVAILABLE, None, "public source connection failed"
        except (ValueError, TypeError):
            return Status.PARSE, None, "public source returned non-JSON"

    def normalize(self, raw, detailed=False, previous=None):
        sid = str(raw.get("PostId") or "")
        if not sid.isdigit():
            raise ValueError("missing stable post ID")
        if previous and sid != previous.source_id:
            raise ValueError("detail identity mismatch")
        title = raw.get("RecruitPostName") or ""
        url = raw.get("PostURL") or f"{self.base}/jobdesc.html?postId={sid}"
        if url.startswith("http://careers.tencent.com/"):
            url = "https://" + url.removeprefix("http://")
        description = ""
        if detailed:
            responsibilities = raw.get("Responsibility") or ""
            requirements = raw.get("Requirement") or ""
            # Some global posts embed both sections in Responsibility; preserve without summarizing.
            if not responsibilities.strip():
                raise ValueError("empty responsibilities")
            sections = [("岗位职责", responsibilities), ("任职要求", requirements),
                        ("经验要求", raw.get("RequireWorkYearsName") or "")]
            description = "\n\n".join(f"{name}\n{value}" for name, value in sections if value.strip())
        job_type = "实习" if re.search(r"实习|\bintern(?:ship)?\b", title, re.I) else ""
        return Job(id=identity(self.source, sid), source=self.source, source_id=sid,
                   url=canonical_url(url), title=title, company="腾讯", city=raw.get("LocationName") or "",
                   job_type=job_type, description=description, provenance="live", raw=raw,
                   locator={"postId": sid})

    def search(self, query, filters=None, cursor=None):
        filters = filters or {}
        # Unknown filters must not silently disappear. Semantic criteria belong in matching.
        if set(filters) - {"city", "job_type", "country"}:
            return ToolResult(status=Status.PARSE, message="Tencent adapter supports city/job_type/country only")
        if filters.get("job_type") not in (None, "", "实习"):
            return ToolResult(status=Status.PARSE, message="non-intern employment type is not explicitly available")
        try:
            page = int(cursor or 1)
            if page < 1:
                raise ValueError()
        except (ValueError, TypeError):
            return ToolResult(status=Status.PARSE, message="invalid page")
        status, raw, message = self._get("Query", {"keyword": query, "pageIndex": page, "pageSize": 20, "area": "cn"})
        if status != Status.OK:
            return ToolResult(status=status, message=message, raw=raw)
        data = raw["Data"]
        if not isinstance(data.get("Posts"), list) or not isinstance(data.get("Count"), int):
            return ToolResult(status=Status.PARSE, message="missing Posts/Count", raw=raw)
        jobs, errors = [], 0
        for record in data["Posts"]:
            if record.get("IsValid") is False:
                continue
            try:
                job = self.normalize(record)
                city, country = filters.get("city", "全国"), filters.get("country")
                if city != "全国" and city not in job.city:
                    continue
                if country and country != record.get("CountryName"):
                    continue
                if filters.get("job_type") and job.job_type != filters["job_type"]:
                    continue
                jobs.append(job)
            except (ValueError, TypeError, AttributeError):
                errors += 1
        return ToolResult(status=Status.OK if jobs else (Status.PARSE if errors else Status.EMPTY), jobs=jobs,
                          cursor=str(page + 1) if page * 20 < data["Count"] else None, raw=raw,
                          message=f"{errors} rejected records" if errors else "")

    def fetch_detail(self, job):
        if job.source != self.source or not job.source_id.isdigit():
            return ToolResult(status=Status.PARSE, message="invalid Tencent job reference")
        status, raw, message = self._get("ByPostId", {"postId": job.source_id})
        if status != Status.OK:
            return ToolResult(status=status, message=message, raw=raw)
        try:
            full = self.normalize(raw["Data"], detailed=True, previous=job)
        except (ValueError, TypeError, AttributeError):
            return ToolResult(status=Status.PARSE, message="invalid or incomplete detail", raw=raw)
        return ToolResult(status=Status.OK, jobs=[full], raw=raw)
