"""Thin subprocess adapter for the user-authenticated BOSS browser."""
import json
import os
import re
from pathlib import Path
import subprocess
import threading
import time
import uuid

from .boss_cdp_import import normalize_exports
from .models import Status, ToolResult


class BossCDP:
    supported_filters = {"city", "job_type"}
    filter_notes = "仅首屏；job_type加入查询词，不能据此认定任职类型满足。"
    # Serialize browser work across sessions in this process.
    lock = threading.Lock()
    last_call_finished = 0.0

    def __init__(self, python=None, upstream=None, runner=subprocess.run, timeout=150, min_interval=2):
        import sys
        import importlib.util
        native = all(importlib.util.find_spec(p) for p in ('requests', 'websocket'))
        self.python = python or os.environ.get("JOB_AGENT_CDP_PYTHON", "") or (sys.executable if native else '')
        self.browser = os.environ.get('JOB_AGENT_BROWSER', 'auto')
        self.browser_path = os.environ.get('JOB_AGENT_BROWSER_PATH', '')
        managed = Path('workspace/browser-runtime/browser.json')
        if self.browser == 'auto' and not self.browser_path and managed.is_file():
            try:
                path = json.loads(managed.read_text()).get('executable', '')
                if isinstance(path, str) and Path(path).is_file():
                    self.browser_path = path
            except (ValueError, OSError, AttributeError):
                pass
        self.upstream = Path(upstream or os.environ.get(
            "JOB_AGENT_CDP_UPSTREAM", "workspace/tools/boss-cdp/scripts/boss_cdp_raw.py"))
        self.runner, self.timeout = runner, timeout
        self.min_interval = max(0, min_interval)

    def _path(self, path):
        path = str(Path(path).resolve())
        if os.name != "nt" and self.python.lower().endswith(".exe"):
            return subprocess.check_output(["wslpath", "-w", path], text=True).strip()
        return path

    def _invoke(self, request):
        if not self.python or not self.upstream.is_file():
            return ToolResult(status=Status.UNAVAILABLE, message="CDP runtime not configured")
        if self.browser != 'auto' or self.browser_path:
            request = {**request, 'browser': self.browser,
                       'browser_path': self._path(self.browser_path) if self.browser_path else ''}
        folder = Path("workspace/cdp-calls") / uuid.uuid4().hex
        folder.mkdir(parents=True)
        input_path, output_path = folder / "request.json", folder / "result.json"
        input_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        try:
            with self.lock:
                delay = max(0, self.min_interval - (time.monotonic() - BossCDP.last_call_finished))
                if delay:
                    time.sleep(delay)
                try:
                    process = self.runner([
                        self.python, self._path(Path(__file__).with_name("cdp_worker.py")),
                        self._path(self.upstream), self._path(input_path), self._path(output_path),
                    ], capture_output=True, encoding="utf-8", errors="replace", timeout=self.timeout, check=False)
                finally:
                    BossCDP.last_call_finished = time.monotonic()
            (folder / "diagnostic.log").write_text(process.stderr or "", encoding="utf-8")
            if not output_path.exists():
                return ToolResult(status=Status.UNAVAILABLE, message="CDP worker failed; private log retained")
            result = json.loads(output_path.read_text(encoding="utf-8"))
            status = Status(result["status"])
            if status != Status.OK:
                return ToolResult(status=status, raw=result, message=result.get("message", f"CDP: {status.value}"))
            if process.returncode:
                return ToolResult(status=Status.UNAVAILABLE, message="CDP worker exited unsuccessfully")
            if request["action"] in ("open_login", "check_login"):
                return ToolResult(status=status, raw=result, message=result.get("message", ""))
            jobs = normalize_exports(result["listing"], result["details"],
                                     result["fetched_at"], result["fetched_at"])
            if request["action"] == "detail" and jobs:
                pages = result.get("detail_pages", [])
                if pages:
                    # Preserve exact attendance/duration header lines omitted by JD extraction.
                    header = re.split(r'职位描述|看过该职位|精选职位|猜你喜欢', pages[-1]['text'], maxsplit=1)[0]
                    jobs[0].detail_metadata = "\n".join(line.strip() for line in header.splitlines()
                        if re.search(r"[1-7]天/周|双休|单休|大小周|全职|正式|远程", line)
                        and not re.search(r'看过该职位|精选职位|猜你喜欢', line))
            if request["action"] == "detail" and (len(jobs) != 1 or not jobs[0].description.strip()):
                return ToolResult(status=Status.PARSE, message="empty or invalid detail")
            return ToolResult(status=Status.OK if jobs else Status.EMPTY, jobs=jobs, raw=result)
        except subprocess.TimeoutExpired:
            return ToolResult(status=Status.TIMEOUT, message="CDP worker exceeded time limit")
        except (OSError, subprocess.CalledProcessError):
            return ToolResult(status=Status.UNAVAILABLE, message="CDP runtime unavailable")
        except (ValueError, KeyError, TypeError):
            return ToolResult(status=Status.PARSE, message="invalid CDP result")

    def search(self, query, filters=None, cursor=None):
        filters = filters or {}
        if not query.strip() or cursor is not None or set(filters) - {"city", "job_type"}:
            return ToolResult(status=Status.PARSE, message="CDP supports first page, city and job-type keywords only")
        effective_query = " ".join(filter(None, [query, filters.get("job_type")]))
        result = self._invoke({"action": "search", "query": effective_query,
                               "city": filters.get("city", "全国")})
        if filters.get("job_type") and result.status == Status.OK:
            result.message = "job_type was added to the query; it is not a strict platform filter"
        return result

    def open_login(self):
        return self._invoke({"action": "open_login"})

    def check_login(self):
        return self._invoke({"action": "check_login"})

    def fetch_detail(self, job):
        if job.source != "boss":
            return ToolResult(status=Status.PARSE, message="not a BOSS job")
        row = {"job_link": job.url, "title": job.title, "boss_name": job.company,
               "location": job.city, "salary": job.salary, "encrypt_job_id": job.source_id,
               "security_id": job.locator.get("securityId", ""), "lid": job.locator.get("lid", "")}
        result = self._invoke({"action": "detail", "job": row})
        if result.jobs and any(j.id != job.id for j in result.jobs):
            return ToolResult(status=Status.PARSE, message="detail identity changed")
        return result
