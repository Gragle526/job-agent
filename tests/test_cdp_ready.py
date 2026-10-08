import json
from types import SimpleNamespace

import pytest

from job_agent.cdp_worker import BrowserBlocked, read_detail


class ExtractionError(ValueError):
    pass


class LoginError(ExtractionError):
    pass


def runtime(page, descriptions=('完整岗位内容', '完整岗位内容')):
    calls = []
    values = iter(descriptions)
    class Session:
        def send(self, method, *args):
            calls.append(method)
        def eval_js(self, js, sid):
            if js == 'EXTRACT':
                return json.dumps({'jd': next(values)})
            return json.dumps(page)
        def close(self):
            calls.append('close')
    upstream = SimpleNamespace(DEFAULT_CDP_PORT=9222, CDPSession=lambda port: Session(),
        create_page_session=lambda ws: ('target', 'session'),
        build_detail_url=lambda job: job['job_link'], EXTRACT_DETAIL_JS='EXTRACT',
        DETAIL_LOGIN_MARKER='登录查看完整内容', DETAIL_DESCRIPTION_MARKER='职位描述',
        DetailExtractionError=ExtractionError, DetailLoginRequiredError=LoginError,
        extract_detail_fields=lambda data: data,
        build_detail_record=lambda job, data: {'jd': data['jd']})
    ticks = [0.0]
    sleeps = []
    def sleep(seconds):
        ticks[0] += seconds
        sleeps.append(seconds)
    return upstream, calls, lambda: ticks[0], sleep, sleeps


def test_waits_for_stable_jd_and_has_no_terminal_delay():
    page = {'url': 'https://example.invalid/job', 'state': 'complete', 'text': '职位描述\n正文'}
    upstream, calls, clock, sleep, sleeps = runtime(page, ('初始内容', '完整内容', '完整内容'))
    detail, timings = read_detail(upstream, {'job_link': page['url']}, clock=clock, sleep=sleep)
    assert detail['jd'] == '完整内容'
    assert timings['terminal_sleep_seconds'] == 0
    assert sleeps == [0.5, 0.5]
    assert calls[-2:] == ['Target.closeTarget', 'close']


@pytest.mark.parametrize(('text', 'error'), [('登录查看完整内容', LoginError),
                                            ('请完成安全验证', BrowserBlocked)])
def test_login_and_verification_stop_without_returning_detail(text, error):
    page = {'url': 'https://example.invalid/job', 'state': 'complete', 'text': text}
    upstream, calls, clock, sleep, _ = runtime(page)
    with pytest.raises(error):
        read_detail(upstream, {'job_link': page['url']}, clock=clock, sleep=sleep)
    assert calls[-2:] == ['Target.closeTarget', 'close']


def test_loading_page_times_out_and_cleans_up():
    page = {'url': 'about:blank', 'state': 'loading', 'text': ''}
    upstream, calls, clock, sleep, _ = runtime(page)
    with pytest.raises(TimeoutError):
        read_detail(upstream, {'job_link': 'https://example.invalid/job'}, timeout=1, clock=clock, sleep=sleep)
    assert calls[-2:] == ['Target.closeTarget', 'close']


def test_redirect_cannot_supply_another_jobs_description():
    page = {'url': 'https://example.invalid/other', 'state': 'complete', 'text': '职位描述\n正文'}
    upstream, calls, clock, sleep, _ = runtime(page)
    with pytest.raises(ValueError, match='redirected'):
        read_detail(upstream, {'job_link': 'https://example.invalid/job'}, clock=clock, sleep=sleep)
    assert calls[-2:] == ['Target.closeTarget', 'close']
