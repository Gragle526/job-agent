"""External Python entry point. Only requests and websocket-client are required."""
import contextlib
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

def validate_detail_fields(extractor, extracted, pages, default_minimum):
    # A validated job page with an explicit JD section may have a legitimate short JD.
    # Keep upstream login/navigation/footer validation; do not discard by arbitrary length.
    explicit_section = bool(pages and '职位描述' in pages[-1].get('text', ''))
    return extractor(extracted, min_length=1 if explicit_section else default_minimum)


class BrowserBlocked(RuntimeError):
    pass


def read_detail(upstream, job, timeout=25, clock=time.monotonic, sleep=time.sleep):
    """Wait for a stable validated JD, reusing upstream extraction and cleanup rules."""
    started = clock()
    session = upstream.CDPSession(upstream.DEFAULT_CDP_PORT)
    target = None
    try:
        target, sid = upstream.create_page_session(session)
        session.send('Page.navigate', {'url': upstream.build_detail_url(job)}, sid)
        previous = None
        while clock() - started < timeout:
            page = json.loads(session.eval_js("JSON.stringify({url:location.href,state:document.readyState,"
                "text:document.body ? document.body.innerText : ''})", sid))
            text = page.get('text', '')
            if upstream.DETAIL_LOGIN_MARKER in text or '/web/user/' in page['url']:
                raise upstream.DetailLoginRequiredError('login required')
            if any(t in text for t in ('请完成安全验证', '访问过于频繁', '请先完成验证')):
                raise BrowserBlocked('browser verification required')
            if page['url'].split('?')[0] != job['job_link']:
                if page['url'] != 'about:blank' and page['state'] == 'complete':
                    raise ValueError('detail page redirected away from requested job')
            elif page['state'] == 'complete' and upstream.DETAIL_DESCRIPTION_MARKER in text:
                extracted = json.loads(session.eval_js(upstream.EXTRACT_DETAIL_JS, sid))
                try:
                    fields = upstream.extract_detail_fields(extracted)
                except upstream.DetailLoginRequiredError:
                    raise
                except upstream.DetailExtractionError:
                    fields = None
                if fields and fields['jd'] == previous:
                    extracted.update(fields)
                    detail = upstream.build_detail_record(job, extracted)
                    return detail, {'ready_and_extract_seconds': round(clock() - started, 3),
                                    'terminal_sleep_seconds': 0}
                previous = fields['jd'] if fields else None
            sleep(0.5)
        raise TimeoutError('detail page did not provide a stable validated JD')
    finally:
        try:
            if target:
                session.send('Target.closeTarget', {'targetId': target})
        finally:
            session.close()


def main():
    import websocket
    worker_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    upstream_path, request_path, output_path = map(Path, sys.argv[1:])
    expected = "574abc5291d5a08052f65c44c80a21e070f6a57b2a8d0a1dd0c6dbe36b96a8e3"
    if hashlib.sha256(upstream_path.read_bytes()).hexdigest() != expected:
        raise ValueError("unverified upstream version")
    request = json.loads(request_path.read_text(encoding="utf-8"))
    original_connect = websocket.create_connection

    def connect(url, **kwargs):
        if not url.startswith("ws://127.0.0.1:9222/"):
            raise ValueError("CDP must use loopback")
        return original_connect(url, suppress_origin=True, **kwargs)

    websocket.create_connection = connect
    spec = importlib.util.spec_from_file_location("boss_upstream", upstream_path)
    upstream = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = upstream
    spec.loader.exec_module(upstream)
    raw_responses = []
    detail_pages = []
    original_body = upstream.NetworkJoblistCapture._fetch_body
    original_eval = upstream.CDPSession.eval_js

    def body(capture, request_id):
        value = original_body(capture, request_id)
        if value is not None:
            raw_responses.append(value)
        return value

    upstream.NetworkJoblistCapture._fetch_body = body

    def evaluate(session, js, sid):
        value = original_eval(session, js, sid)
        if js == upstream.EXTRACT_DETAIL_JS:
            page = json.loads(original_eval(session, "JSON.stringify({url:location.href,"
                                            "title:document.title,text:document.body.innerText})", sid))
            detail_pages.append(page)
            if page["url"].split("?")[0] != request["job"]["job_link"]:
                raise ValueError("detail page redirected away from requested job")
        return value

    upstream.CDPSession.eval_js = evaluate
    original_extract = upstream.extract_detail_fields
    upstream.extract_detail_fields = lambda extracted, min_length=upstream.MIN_DETAIL_TEXT_LENGTH: validate_detail_fields(
        original_extract, extracted, detail_pages, min_length)
    result = {"status": "unavailable", "listing": {"jobs": []}, "details": []}
    try:
        with contextlib.redirect_stdout(sys.stderr):
            if request["action"] == "open_login":
                browser = request.get('browser', 'auto')
                executable = request.get('browser_path')
                if not executable:
                    paths = [upstream.DEFAULT_EDGE_PATH, upstream.DEFAULT_CHROME_PATH] if browser == 'edge' else [
                        upstream.DEFAULT_CHROME_PATH, upstream.DEFAULT_EDGE_PATH]
                    if browser == 'chrome':
                        paths = paths[:1]
                    executable = next((p for p in paths if Path(p).is_file()), '')
                if not executable or not Path(executable).is_file():
                    result.update(status='unavailable', message='未找到兼容浏览器。请在设置中选择Edge、安装Chrome或下载独立Chromium。')
                    output_path.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
                    return
                edge = 'edge' in Path(executable).name.lower()
                profile = (Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) /
                           'JobAgent' / ('EdgeProfile' if edge else 'ChromeProfile') if os.name == 'nt' else
                           Path.home() / ('.local/share/job-agent/edge-profile' if edge else '.local/share/job-agent/chrome-profile'))
                profile.mkdir(parents=True, exist_ok=True)
                import requests
                probe_client = requests.Session()
                probe_client.trust_env = False
                try:
                    probe = probe_client.get('http://127.0.0.1:9222/json/version', timeout=1)
                    if probe.ok and probe.json().get('webSocketDebuggerUrl'):
                        result.update(status='ok', message='已有本机浏览器连接，本次未新建窗口。请在该窗口完成登录并检查；更换浏览器前关闭原助手专用窗口。')
                        output_path.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
                        return
                except (requests.RequestException, ValueError):
                    pass
                subprocess.Popen([executable, "--remote-debugging-port=9222", "--remote-debugging-address=127.0.0.1",
                                  f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
                                  "https://www.zhipin.com/web/user/"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                deadline = time.monotonic() + 8
                result.update(status='unavailable', message='浏览器未能启动或调试连接不可用。请检查系统库、图形桌面和原助手窗口，再重试。')
                while time.monotonic() < deadline:
                    try:
                        probe = probe_client.get('http://127.0.0.1:9222/json/version', timeout=1)
                        if probe.ok and probe.json().get('webSocketDebuggerUrl'):
                            result.update(status='ok', message='已打开专用浏览器；请本人扫码登录，然后检查登录。')
                            break
                    except (requests.RequestException, ValueError):
                        pass
                    time.sleep(0.3)
            elif request["action"] == "check_login":
                probe = upstream.check_login_state()
                result["status"] = {
                    upstream.LoginProbeStatus.AVAILABLE: "ok",
                    upstream.LoginProbeStatus.UNAUTHENTICATED: "needs_login",
                    upstream.LoginProbeStatus.RESTRICTED: "blocked",
                    upstream.LoginProbeStatus.EMPTY: "empty",
                }.get(probe.status, "unavailable")
                result["message"] = {
                    "ok": "登录检查通过，可以开始找岗位。",
                    "needs_login": "请在BOSS专用浏览器扫码登录，然后重新检查。",
                    "blocked": "请在BOSS专用浏览器完成页面验证，然后重新检查。",
                    "empty": "浏览器可以访问，但登录探测搜索为空；请检查网页后重试。",
                }.get(result["status"], "无法连接BOSS专用浏览器；请先打开浏览器，再检查登录。")
            elif request["action"] == "search":
                result["listing"] = upstream.scrape_list(
                    request["query"], request["city"], 1, {},
                    str(output_path.parent / "list.json"))
                result["status"] = "ok" if result["listing"]["jobs"] else "empty"
                if not raw_responses:
                    result["status"] = "timeout"
            elif request["action"] == "detail":
                result["listing"] = {"jobs": [request["job"]]}
                detail, timings = read_detail(upstream, request['job'])
                result['details'] = [detail]
                result['timings'] = timings
                result["status"] = "ok" if result["details"] else "parse_error"
            else:
                raise ValueError("unsupported action")
    except upstream.LoginGateError:
        status = upstream.classify_login_probe_response(json.loads(raw_responses[-1])).status if raw_responses else None
        result["status"] = {
            upstream.LoginProbeStatus.UNAUTHENTICATED: "needs_login",
            upstream.LoginProbeStatus.RESTRICTED: "blocked",
        }.get(status, "unavailable")
    except upstream.DetailLoginRequiredError:
        result['status'] = 'needs_login'
    except BrowserBlocked:
        result['status'] = 'blocked'
    except (TimeoutError, websocket.WebSocketTimeoutException):
        result["status"] = "timeout"
    except (ValueError, KeyError, TypeError, upstream.DetailExtractionError):
        result["status"] = "parse_error"
    except RuntimeError as exc:
        result["status"] = "needs_login" if "login expired" in str(exc) else "unavailable"
    finally:
        result["worker_sha256"] = worker_sha256
        result["fetched_at"] = datetime.now(timezone.utc).isoformat()
        result["raw_responses"] = raw_responses
        result["detail_pages"] = detail_pages
        output_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
