"""Normalize exports from the pinned, external BOSS CDP scraper."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .models import Job
from .providers import canonical_url, identity


def normalize_exports(listing: dict, details: list[dict], listed_at: str,
                      detailed_at: str) -> list[Job]:
    rows = listing["jobs"]
    by_url = {canonical_url(row["job_link"]): row for row in rows}
    detail_by_url = {}
    for detail in details:
        url = canonical_url(detail["job_link"])
        if url not in by_url or detail["title"] != by_url[url]["title"]:
            raise ValueError("detail does not match a listed job")
        if not detail.get("jd", "").strip():
            raise ValueError("empty JD is not a successful detail")
        detail_by_url[url] = detail
    jobs = []
    for url, row in by_url.items():
        parsed = urlsplit(url)
        if parsed.hostname != "www.zhipin.com" or not parsed.path.startswith("/job_detail/"):
            raise ValueError("unexpected BOSS job URL")
        sid = parsed.path.removeprefix("/job_detail/").removesuffix(".html")
        if not sid or "/" in sid or row.get("encrypt_job_id", sid) != sid:
            raise ValueError("job ID differs from URL")
        detail = detail_by_url.get(url)
        jobs.append(Job(
            id=identity("boss", sid), source="boss", source_id=sid, url=url,
            title=row["title"], company=row.get("boss_name", ""),
            city=row.get("location", ""), salary=row.get("salary", ""),
            description=detail["jd"] if detail else "", provenance="live",
            fetched_at=detailed_at if detail else listed_at,
            locator={key: str(row[original]) for key, original in
                     (("securityId", "security_id"), ("lid", "lid")) if row.get(original)},
            raw={"collector": "boss-zhipin-scraper", "list_export": row,
                 "detail_export": detail, "listed_at": listed_at,
                 "detailed_at": detailed_at if detail else None},
        ))
    return jobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("listing", type=Path)
    parser.add_argument("details", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    # Upstream timestamps are naive local times; file mtimes give UTC evidence.
    def modified(path):
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    jobs = normalize_exports(
        json.loads(args.listing.read_text(encoding="utf-8")),
        json.loads(args.details.read_text(encoding="utf-8")),
        modified(args.listing), modified(args.details),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps([j.model_dump() for j in jobs], ensure_ascii=False,
                                     indent=2), encoding="utf-8")
    print(json.dumps({"jobs": len(jobs), "with_detail": sum(bool(j.description) for j in jobs)}))


if __name__ == "__main__":
    main()
