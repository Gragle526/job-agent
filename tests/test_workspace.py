"""Small production-interface checks; no model or recruitment requests."""
from types import SimpleNamespace

import pytest

from job_agent.boss_cdp import BossCDP
from job_agent.engine import Engine
from job_agent.models import Checkpoint, Intent, State, Status, ToolResult
from job_agent.providers import Snapshot
from job_agent.store import Store
from job_agent.ui import WorkspaceUI


def test_previous_checkpoint_remains_readable_without_execution_selector():
    value = Checkpoint(intent=Intent(), state=State()).model_dump()
    value['policy'] = 'adaptive'
    assert Checkpoint.model_validate(value).state.version == 0
    assert 'policy' not in Checkpoint.model_validate(value).model_dump()


def test_unexpected_worker_error_updates_task_status(tmp_path):
    def broken(*args):
        raise KeyError('private transport value')
    engine = Engine(Store(tmp_path / 'jobs.sqlite'), Snapshot([]), SimpleNamespace(understand=broken))
    rid, state = engine.start('s', '找工作')
    assert engine.run(rid, state) is None
    assert engine.store.run(rid)['status'] == 'failed'
    assert 'private transport value' not in engine.store.events(rid, kinds=('error',))[-1]['data']['message']


@pytest.mark.parametrize('status,accepted', [(Status.OK, True), (Status.LOGIN, False)])
def test_existing_browser_login_is_checked_before_accepting_request(tmp_path, monkeypatch, status, accepted):
    class Browser(BossCDP):
        def check_login(self):
            return ToolResult(status=status, message='login checked')
    monkeypatch.setattr('job_agent.onboarding.check_environment', lambda *args: {'model_configured': True})
    engine = Engine(Store(tmp_path / 'ui.sqlite'), Browser(), SimpleNamespace(name='cloud'))
    engine.run = lambda *args: None
    ui = WorkspaceUI(engine)
    ui.title_requested.add('s')
    if accepted:
        assert ui.send('找工作', 's') == ''
        for future in ui.futures.values():
            future.result(timeout=5)
        assert ui.store.session('s').version == 1
    else:
        import gradio as gr
        with pytest.raises(gr.Error):
            ui.send('找工作', 's')
        assert ui.store.session('s').version == 0
