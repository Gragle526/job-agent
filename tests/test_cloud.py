from types import SimpleNamespace

import pytest

from job_agent.models import Intent
from job_agent.reasoning import CloudReasoner
from job_agent.store import BudgetExceeded, Store


def reasoner(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "local-fake-key-not-valid")
    monkeypatch.setenv("JOB_AGENT_BUDGET_DB", str(tmp_path / "budget.sqlite"))
    store = Store(tmp_path / "session.sqlite")
    rid, _ = store.begin("test", "找岗位")
    r = CloudReasoner(store)
    return store, rid, r


def test_cloud_schema_prices_and_no_thinking(tmp_path, monkeypatch):
    store, rid, r = reasoner(tmp_path, monkeypatch)
    seen = {}

    def create(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20, total_tokens=120),
                               model="deepseek-flash", choices=[SimpleNamespace(finish_reason="stop",
                                      message=SimpleNamespace(content='{"queries":["Python"]}'))])

    r.client.chat.completions.create = create
    result = r.call(rid, "return json", {}, Intent)
    assert result.queries == ["Python"]
    assert seen["extra_body"]["thinking"]["type"] == "disabled"
    assert r.budget_store.usage()["cost_cny"] == pytest.approx(0.00036)
    assert r.budget_store.usage()["tokens"] == 120




def test_qwen_disables_thinking_and_settles_actual_price_band(tmp_path, monkeypatch):
    monkeypatch.setenv('JOB_AGENT_MODEL', 'qwen3.7-flash')
    monkeypatch.setenv('JOB_AGENT_BASE_URL', 'https://dashscope.aliyuncs.com/compatible-mode/v1')
    monkeypatch.setenv('JOB_AGENT_INPUT_CNY_PER_MILLION', '0.2')
    monkeypatch.setenv('JOB_AGENT_OUTPUT_CNY_PER_MILLION', '0.8')
    _, rid, r = reasoner(tmp_path, monkeypatch)
    seen = {}
    def create(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=33000, completion_tokens=20,
            total_tokens=33020), model='qwen3.7-flash', choices=[SimpleNamespace(finish_reason='stop',
            message=SimpleNamespace(content='{"queries":["前端"]}'))])
    r.client.chat.completions.create = create
    r.call(rid, 'return json', {}, Intent)
    assert seen['extra_body'] == {'enable_thinking': False}
    assert r.budget_store.usage(rid)['cost_cny'] == pytest.approx((33000 * 0.6 + 20 * 2.4) / 1e6)




def test_uncertain_failed_call_retains_cost_and_token_reservation(tmp_path, monkeypatch):
    _, rid, r = reasoner(tmp_path, monkeypatch)

    def fail(**kwargs):
        raise RuntimeError("do not expose private transport details")

    r.client.chat.completions.create = fail
    with pytest.raises(RuntimeError, match="model request failed"):
        r.call(rid, "return json", {}, Intent)
    assert r.budget_store.usage()["cost_cny"] > 0
    assert r.budget_store.usage()["tokens"] > 0


def test_explicit_quota_rejection_is_visible_and_not_recorded_as_consumed_tokens(tmp_path, monkeypatch):
    store, rid, r = reasoner(tmp_path, monkeypatch)
    class QuotaError(Exception):
        status_code = 403
        body = {'code': 'AllocationQuota.FreeTierOnly'}
    def fail(**kwargs):
        raise QuotaError('sensitive provider detail must not be exposed')
    r.client.chat.completions.create = fail
    with pytest.raises(RuntimeError, match='免费额度已用完'):
        r.call(rid, 'return json', {}, Intent)
    assert r.budget_store.usage(rid)['tokens'] == 0
    assert r.budget_store.usage(rid)['cost_cny'] == 0
    event = store.events(rid)[0]['data']
    assert event['provider_code'] == 'AllocationQuota.FreeTierOnly'
    assert event['http_status'] == 403


def test_exhausted_budget_prevents_network_call(tmp_path, monkeypatch):
    _, rid, r = reasoner(tmp_path, monkeypatch)
    r.cap = 0.00000001
    r.client.chat.completions.create = lambda **kw: pytest.fail("network called")
    with pytest.raises(BudgetExceeded):
        r.call(rid, "return json", {}, Intent)




def test_invalid_model_json_is_not_a_success(tmp_path, monkeypatch):
    _, rid, r = reasoner(tmp_path, monkeypatch)
    r.client.chat.completions.create = lambda **kw: SimpleNamespace(
        usage=None, model="deepseek-flash", choices=[SimpleNamespace(finish_reason="stop",
                                                                    message=SimpleNamespace(content=""))])
    with pytest.raises(ValueError):
        r.call(rid, "return json", {}, Intent)


def test_action_selection_preserves_program_owned_job_id(tmp_path, monkeypatch):
    from job_agent.models import Action, ActionSelection, Checkpoint, State

    _, rid, r = reasoner(tmp_path, monkeypatch)
    r.call = lambda *a: ActionSelection(action_index=0, query="irrelevant text", reason="补充详情")
    action = r.choose(rid, Checkpoint(intent=Intent(), state=State()),
                      [Action(kind="detail", job_id="durable-id")], [])
    assert action.job_id == "durable-id"
    assert action.query == ""
    r.call = lambda *a: ActionSelection(action_index=2)
    with pytest.raises(ValueError, match="out-of-range"):
        r.choose(rid, Checkpoint(intent=Intent(), state=State()),
                 [Action(kind="stop"), Action(kind="search", query="保洁")], [])


def test_new_search_batch_is_proposed_without_changing_job_or_filters(tmp_path, monkeypatch):
    from job_agent.models import Action, ActionSelection, Checkpoint, State
    _, rid, r = reasoner(tmp_path, monkeypatch)
    r.call = lambda *args: ActionSelection(action_index=0, queries=['LLM后端', 'Agent平台'])
    cp = Checkpoint(intent=Intent(), state=State(filters={'city': '上海'}))
    action = r.choose(rid, cp, [Action(kind='search_batch')], [])
    assert action.queries == ['LLM后端', 'Agent平台']
    assert not action.job_ids and cp.state.filters == {'city': '上海'}


def test_extra_fields_are_removed_without_changing_evidence_or_another_call(tmp_path, monkeypatch):
    from job_agent.models import BatchMatchOutput
    store, rid, r = reasoner(tmp_path, monkeypatch)
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(usage=None, model='deepseek-flash', choices=[SimpleNamespace(
            finish_reason='stop', message=SimpleNamespace(content=
                '{"jobs":[{"job_id":"stable-id","evidence":[{"key":"days","verdict":"violated",'
                '"quote":"5天/周","text":"多余字段"}]}]}'))])
    r.client.chat.completions.create = create
    result = r.call(rid, 'return json', {}, BatchMatchOutput)
    assert result.jobs[0].job_id == 'stable-id'
    assert result.jobs[0].evidence[0].verdict == 'violated'
    assert result.jobs[0].evidence[0].quote == '5天/周'
    assert len(calls) == 1
    assert any(e['kind'] == 'model_schema_projection' for e in store.events(rid))


def test_invalid_verdict_cannot_be_fixed_by_extra_field_projection(tmp_path, monkeypatch):
    from job_agent.models import BatchMatchOutput
    _, rid, r = reasoner(tmp_path, monkeypatch)
    r.client.chat.completions.create = lambda **kw: SimpleNamespace(usage=None, model='deepseek-flash',
        choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content=
            '{"jobs":[{"job_id":"id","evidence":[{"key":"days","verdict":"maybe","extra":0}]}]}'))])
    with pytest.raises(ValueError):
        r.call(rid, 'return json', {}, BatchMatchOutput)


@pytest.mark.parametrize('operation,scope,allowed', [
    ('remove', 'condition', True), ('set', 'background', True), ('set', 'condition', False)])
@pytest.mark.parametrize('invalid_kind', [None, ''])
def test_null_kind_only_projects_fields_that_have_no_matching_semantics(tmp_path, monkeypatch,
                                                                       operation, scope, allowed, invalid_kind):
    import json
    _, rid, r = reasoner(tmp_path, monkeypatch)
    content = json.dumps({'changes': [{'op': operation, 'scope': scope, 'key': 'role',
                                      'text': '后端', 'quote': '改找后端', 'kind': invalid_kind}]})
    r.client.chat.completions.create = lambda **kw: SimpleNamespace(usage=None, model='deepseek-flash',
        choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content=content))])
    if allowed:
        assert r.call(rid, 'return json', {}, Intent).changes[0].text == '后端'
    else:
        with pytest.raises(ValueError):
            r.call(rid, 'return json', {}, Intent)


def test_detail_selection_uses_only_scoped_unread_indices(tmp_path, monkeypatch):
    from job_agent.models import Action, ActionSelection, Checkpoint, Job, State
    _, rid, r = reasoner(tmp_path, monkeypatch)
    read = Job(id='cached', source='boss', source_id='cached', url='https://example.invalid/cached',
               title='已读岗位', description='完整JD', provenance='live')
    unread = read.model_copy(update={'id': 'eligible', 'source_id': 'eligible', 'title': '未读岗位', 'description': ''})
    captured = []
    def choose(*args):
        captured.append(args[2])
        return ActionSelection(action_index=0, detail_indices=[0])
    r.call = choose
    allowed = [Action(kind='detail_batch', job_ids=[unread.id])]
    cp = Checkpoint(intent=Intent(), state=State())
    assert r.choose(rid, cp, allowed, [read, unread]).job_ids == [unread.id]
    assert captured[0]['detail_candidates'] == [{'index': 0, 'title': '未读岗位', 'city': '',
                                                'salary': '', 'job_type': '', 'metadata': ''}]
    r.call = lambda *args: ActionSelection(action_index=0, detail_indices=[1])
    with pytest.raises(ValueError, match='detail indices'):
        r.choose(rid, cp, allowed, [read, unread])


def test_detail_selection_caps_valid_choices_at_remaining_quota(tmp_path, monkeypatch):
    from job_agent.models import Action, ActionSelection, Checkpoint, Job, State
    store, rid, r = reasoner(tmp_path, monkeypatch)
    jobs = [Job(id=f'job-{i}', source='boss', source_id=str(i),
                url=f'https://example.invalid/{i}', title='前端开发', provenance='live')
            for i in range(3)]
    r.call = lambda *args: ActionSelection(action_index=0, detail_indices=[2, 0, 1])
    action = r.choose(rid, Checkpoint(intent=Intent(), state=State()),
                      [Action(kind='detail_batch', job_ids=['job-0'])], jobs)
    assert action.job_ids == ['job-2']
    assert any(e['kind'] == 'action_quota_capped' and e['data'] == {'requested': 3, 'accepted': 1}
               for e in store.events(rid))
    r.call = lambda *args: ActionSelection(action_index=0, detail_indices=[0, 0])
    with pytest.raises(ValueError, match='detail indices'):
        r.choose(rid, Checkpoint(intent=Intent(), state=State()),
                 [Action(kind='detail_batch', job_ids=['job-0'])], jobs)
