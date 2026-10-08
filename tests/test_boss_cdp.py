import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from job_agent.boss_cdp import BossCDP
from job_agent.models import Status


def setup(tmp_path, monkeypatch, payload=None, error=None):
    monkeypatch.chdir(tmp_path)
    upstream = tmp_path / "upstream.py"
    upstream.write_text("# stub, never executed")
    calls = []

    def run(args, **kwargs):
        calls.append(json.loads(Path(args[-2]).read_text()))
        assert "shell" not in kwargs
        if error:
            raise error
        Path(args[-1]).write_text(json.dumps(payload))
        return SimpleNamespace(returncode=0, stderr="")

    return BossCDP(python="python", upstream=upstream, runner=run, min_interval=0), calls


@pytest.mark.parametrize("status", ["needs_login", "blocked", "timeout", "empty", "parse_error"])
def test_failures_do_not_generate_jobs(tmp_path, monkeypatch, status):
    provider, calls = setup(tmp_path, monkeypatch, {"status": status})
    result = provider.search("Agent")
    assert result.status.value == status
    assert result.jobs == [] and len(calls) == 1


def test_worker_timeout_and_unsupported_filters(tmp_path, monkeypatch):
    provider, calls = setup(tmp_path, monkeypatch, error=subprocess.TimeoutExpired("python", 1))
    assert provider.search("Agent", {"salary": "10K"}).status == Status.PARSE
    assert not calls
    assert provider.search("Agent").status == Status.TIMEOUT


def test_roundtrip_preserves_jd_and_labels_keyword_filter(tmp_path, monkeypatch):
    row = {"job_link": "https://www.zhipin.com/job_detail/abc.html",
           "encrypt_job_id": "abc", "title": "Agent", "security_id": "temporary"}
    payload = {"status": "ok", "listing": {"jobs": [row]}, "details": [],
               "fetched_at": "2026-10-01T00:00:00+00:00"}
    provider, calls = setup(tmp_path, monkeypatch, payload)
    result = provider.search("Agent", {"city": "北京", "job_type": "实习"})
    assert calls[0]["query"] == "Agent 实习"
    assert result.status == Status.OK and result.message
    assert result.jobs[0].description == ""
    assert result.jobs[0].locator["securityId"] == "temporary"
    payload["details"] = [{"job_link": row["job_link"], "title": "Agent", "jd": "正文" * 1000}]
    payload["detail_pages"] = [{"text": "Agent\n上海 5天/周 6个月 本科\n职位描述\n正文\n猜你喜欢\n北京 2天/周"}]
    detail = provider.fetch_detail(result.jobs[0])
    assert detail.status == Status.OK
    assert detail.jobs[0].id == result.jobs[0].id
    assert len(detail.jobs[0].description) == 2000
    assert detail.jobs[0].detail_metadata == "上海 5天/周 6个月 本科"


def test_login_actions_do_not_require_job_payload(tmp_path, monkeypatch):
    payload = {"status": "ok", "message": "browser opened"}
    provider, calls = setup(tmp_path, monkeypatch, payload)
    assert provider.open_login().status == Status.OK
    assert calls[-1] == {"action": "open_login"}
    payload.update(status="needs_login", message="scan QR")
    result = provider.check_login()
    assert result.status == Status.LOGIN and result.message == "scan QR"
    assert not result.jobs
