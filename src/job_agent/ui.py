import hashlib
import re
import time
import threading
import uuid
import html
from pathlib import Path
from urllib.parse import urlsplit
from gradio import Request
from concurrent.futures import ThreadPoolExecutor

from .engine import Engine
from .models import Checkpoint, Status, now
from .boss_cdp import BossCDP
from .providers import Snapshot
from .tencent import TencentCareers


class WorkspaceUI:
    def __init__(self, engine: Engine, initial_session='personal', restore_preferences=False):
        self.engine, self.store = engine, engine.store
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="job-agent")
        self.futures = {}
        self.lock = threading.Lock()
        self.browser_ready = False
        self.browser_checked_at = 0.0
        self.browser_status = "先打开本机BOSS浏览器，扫码登录，再检查登录状态。"
        self.browser_error_seen = set()
        self.initial_session = initial_session
        self.title_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='conversation-title')
        self.title_requested = set()
        self.browser_provider = engine.provider if isinstance(engine.provider, BossCDP) else BossCDP()
        self.source_mode = ('实时 BOSS' if isinstance(engine.provider, BossCDP) else
                            '腾讯官网' if isinstance(engine.provider, TencentCareers) else
                            '本地快照')
        self.last_views = {}
        self.setup_running = False
        self.active_sessions = {}
        self.source_epoch = None
        if restore_preferences:
            saved = self.store.preference('ui_source')
            if saved in ('实时 BOSS', '腾讯官网'):
                self.switch_mode(saved)
                self.source_epoch = None
            elif saved == '本地快照':
                path = self.store.preference('ui_snapshot')
                if path and Path(path).is_file():
                    from .providers import load_jobs
                    from .onboarding import DeferredReasoner
                    try:
                        jobs = load_jobs(path)
                        for job in jobs:
                            try:
                                cached = self.store.job(job.id)
                            except KeyError:
                                cached = None
                            if cached is None or cached.model_dump() != job.model_dump():
                                self.store.save_job(job)
                        self.engine.provider = Snapshot(jobs)
                        self.engine.reasoner = DeferredReasoner(self.store, 150000)
                        self.source_mode = '本地快照'
                    except Exception:
                        pass  # Invalid/missing old snapshots never prevent opening setup.

    def browser_step(self, check=False):
        if self.setup_running:
            return '组件还在准备，请等待完成后再打开浏览器。'
        self.browser_ready = False
        result = self.browser_provider.check_login() if check else self.browser_provider.open_login()
        self.browser_ready = check and result.status == Status.OK
        if self.browser_ready:
            self.browser_checked_at = time.monotonic()
        self.browser_status = result.message or f"浏览器连接状态：{result.status}"
        return self.browser_status

    def send(self, message: str, sid: str):
        if not message.strip():
            return ""
        from .onboarding import check_environment
        if not check_environment(self.browser_provider)['model_configured']:
            import gradio as gr
            raise gr.Error('请打开左侧「使用设置」，配置模型API Key。你的输入不会清空。')
        if isinstance(self.engine.provider, BossCDP) and not self.browser_ready:
            # A process restart loses the in-memory check, not browser cookies.
            # Check the existing window before asking the user to log in again.
            self.browser_step(check=True)
        if isinstance(self.engine.provider, BossCDP) and not self.browser_ready:
            import gradio as gr
            raise gr.Error('已有浏览器的登录检查未通过：' + self.browser_status +
                           '。请打开左侧「使用设置与连接」处理连接或扫码。你的需求仍保留在输入框。')
        # Start happens on the UI thread: supersede old work immediately, not after queueing.
        rid, state = self.engine.start(sid, message)
        self.store.event(rid, 'ui_source', {'mode': self.source_mode})
        meta = self.store.conversation_title(sid)
        if not meta or meta['title_source'] == 'draft':
            first = next((m['content'] for m in state.history if m['role'] == 'user'), message)
            self.store.name_conversation(sid, ' '.join(first.split())[:24], 'fallback')
            if sid not in self.title_requested:
                self.title_requested.add(sid)
                self.title_pool.submit(self._generate_title, sid, first)
        with self.lock:
            self.futures[rid] = self.pool.submit(self.engine.run, rid, state)
        return ""

    def _generate_title(self, sid, message):
        """Separate title run never advances or supersedes the user's conversation."""
        try:
            from .models import ConversationTitle, Intent
            from .reasoning import CloudReasoner
            from .store import Store
            parent = self.engine.reasoner
            titles = Store(Path(self.store.path).with_name('conversation-titles.sqlite'))
            model = CloudReasoner(titles, token_limit=4000, budget_store=parent.budget_store,
                provider={'model': parent.model, 'base_url': parent.base_url, 'api_key': parent.client.api_key,
                          'input_rate': parent.input_rate, 'output_rate': parent.output_rate})
            model.client = model.client.with_options(timeout=8)
            rid, state = titles.begin('title-' + sid, message[:500])
            output = model.call(rid, '仅根据首条需求生成简洁中文会话标题，12到20字，不回答问题，不加引号或Markdown。',
                                {'first_message': message[:500]}, ConversationTitle)
            self.store.name_conversation(sid, output.title, 'model', only_fallback=True)
            titles.commit(rid, Checkpoint(state=state, intent=Intent(), completed=True), 'completed')
        except Exception:
            # A name is cosmetic: retain the first-message fallback and never fail the job task.
            pass

    def conversation_choices(self, sid=None, archived=False):
        choices = [('◌ ' + c['title'] if c['archived'] else c['title'], c['id'])
                   for c in self.store.list_conversations(archived)]
        if sid and sid not in {value for _, value in choices}:
            meta = self.store.conversation_title(sid)
            choices.insert(0, (meta['title'] if meta else '新会话', sid))
        return choices

    def _idle_required(self):
        if self.setup_running or any(not f.done() for f in self.futures.values()):
            raise ValueError('请先暂停任务，并等待正在进行的请求结束后再更改设置。')

    def configure_model(self, service, key):
        import gradio as gr
        from .onboarding import save_model_settings, DeferredReasoner
        try:
            self._idle_required()
            status = save_model_settings(service, key)
            self.engine.reasoner = DeferredReasoner(self.store, 150000)
            return '', status
        except (ValueError, OSError):
            raise gr.Error('无法保存：请确认密钥非空、没有换行，且没有执行中的任务。')

    def configure_browser(self, choice, path, runtime):
        from .onboarding import persist
        self._idle_required()
        if path.strip() and not Path(path.strip()).is_file():
            return '指定的浏览器路径不存在，请填写本机可执行文件。'
        if runtime.strip() and not Path(runtime.strip()).is_file():
            return '运行环境路径不存在，请检查Python可执行文件。'
        provider = self.browser_provider
        provider.browser = {'自动选择': 'auto', 'Chrome': 'chrome', 'Edge': 'edge'}[choice]
        provider.browser_path = path.strip()
        if runtime.strip():
            provider.python = runtime.strip()
        persist({'JOB_AGENT_BROWSER': provider.browser, 'JOB_AGENT_BROWSER_PATH': provider.browser_path,
                 'JOB_AGENT_CDP_PYTHON': provider.python})
        self.browser_ready = False
        return '已保存。更换浏览器前请关闭原助手专用窗口，再打开并检查登录；不会关闭普通浏览器。'

    def install_tools(self, managed=False):
        from .onboarding import prepare_browser_tools
        try:
            self._idle_required()
            self.setup_running = True
            self.browser_ready = False
            return prepare_browser_tools(self.browser_provider, managed)
        except ValueError as exc:
            return str(exc)
        except Exception:
            return '准备未完成。可能是网络、权限或Python环境问题，请按手动指引继续；当前会话与登录数据保留。'
        finally:
            self.setup_running = False

    def switch_mode(self, mode):
        from .onboarding import DeferredReasoner
        self._idle_required()
        if mode == '实时 BOSS':
            self.engine.provider = self.browser_provider
            self.engine.reasoner = DeferredReasoner(self.store, 150000)
        elif mode == '腾讯官网':
            self.engine.provider = TencentCareers()
            self.engine.reasoner = DeferredReasoner(self.store, 150000)
        else:
            return '本地快照模式请先在下方导入岗位JSON文件。'
        self.source_mode = mode
        self.store.save_preference('ui_source', mode)
        self.source_epoch = now()
        if mode == '实时 BOSS' and time.monotonic() - self.browser_checked_at > 300:
            self.browser_ready = False
        self.last_views.clear()
        return '已切换到' + mode + '。会话保留，岗位会按新的数据来源重新检查。'

    def import_snapshot(self, file):
        from .providers import load_jobs
        from .onboarding import DeferredReasoner
        if not file:
            return '请先选择JSON文件。'
        self._idle_required()
        path = Path(file)
        if path.stat().st_size > 10 * 1024 * 1024:
            return '文件超过10MB，请拆分后导入。'
        try:
            jobs = load_jobs(path)
        except Exception:
            return '文件格式不正确。请使用本项目导出的标准岗位JSON；不会修改原文件。'
        if not jobs:
            return '文件没有岗位记录。'
        target = Path('workspace/imports') / (uuid.uuid4().hex + '.json')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(path.read_text())
        for job in jobs:
            self.store.save_job(job)
        self.engine.provider = Snapshot(jobs)
        self.engine.reasoner = DeferredReasoner(self.store, 150000)
        self.source_mode = '本地快照'
        self.store.save_preference('ui_source', '本地快照')
        self.store.save_preference('ui_snapshot', str(target.resolve()))
        self.source_epoch = now()
        self.last_views.clear()
        return f'已导入{len(jobs)}条岗位快照。此模式不访问招聘平台；配置模型后即可分析。'

    def job_detail(self, sid, jid, request: Request = None):
        state = self.store.session(sid)
        if not jid or jid not in state.known_ids:
            return '选择上方岗位，查看原文与匹配依据。'
        job = self.store.job(jid)
        a = state.assessments.get(jid)
        lines = [f'### {html.escape(job.title)}', f'**{html.escape(job.company)}** · {html.escape(job.city)} · {html.escape(job.salary)}']
        if a and a.version == state.version:
            labels = {'satisfied': '满足', 'violated': '不符合', 'unknown': '待确认'}
            lines += [f"- **{html.escape(state.constraints[e.key].text)}**：{labels[e.verdict]}"
                      + (f'\n  > {html.escape(e.quote)}' if e.quote else '')
                      for e in a.evidence if e.key in state.constraints]
        else:
            lines.append('匹配结论尚未按最新需求核验，原文保留供比较。')
        if urlsplit(job.url).scheme in ('http', 'https'):
            lines.append(f'[打开原始岗位]({job.url})')
        description = job.description.replace('```', '｀｀｀')
        lines += [f'获取时间：{job.fetched_at}', '---',
                  '```text\n' + description + '\n```' if description else '尚未取得完整JD，列表摘要不能当成完整详情。']
        if request and self.active_sessions.get(request.session_hash, sid) != sid:
            import gradio as gr
            return gr.skip()
        return '\n\n'.join(lines)

    def resume(self, sid: str):
        if isinstance(self.engine.provider, BossCDP) and not self.browser_ready:
            self.browser_step(check=True)
        if isinstance(self.engine.provider, BossCDP) and not self.browser_ready:
            import gradio as gr
            raise gr.Error("请先扫码登录并检查登录状态，再恢复任务。")
        with self.store.db() as db:
            row = db.execute("SELECT id,time FROM runs WHERE session_id=? AND status IN ('paused','running') "
                             "ORDER BY time DESC LIMIT 1", (sid,)).fetchone()
        if row:
            if self.source_epoch and row['time'] < self.source_epoch:
                import gradio as gr
                raise gr.Error('这个任务在旧数据来源下暂停，请在当前模式发送新的需求。原会话和证据仍保留。')
            origin = self.store.events(row['id'], kinds=('ui_source',))
            if origin and origin[-1]['data']['mode'] != self.source_mode:
                import gradio as gr
                raise gr.Error('这个任务的数据来源与当前模式不同，请重新发送需求，不跨来源恢复。')
            rid = row[0]
            with self.lock:
                future = self.futures.get(rid)
                if not future or future.done():
                    self.futures[rid] = self.pool.submit(self.engine.run, rid)

    def render(self, sid: str):
        state = self.store.session(sid)
        rows = []
        current = [state.assessments[jid] for jid in state.display if jid in state.assessments
                   and state.assessments[jid].version == state.version]
        common_unknown = [key for key, c in state.constraints.items() if c.kind == 'hard'
                          and len(current) == len(state.display) and current and all(
            any(e.key == key and e.verdict == 'unknown' for e in a.evidence) for a in current)]
        for i, jid in enumerate(state.display, 1):
            job = self.store.job(jid)
            a = state.assessments.get(jid)
            category = "更新中" if not a or a.version != state.version else {
                "recommended": "符合已核查条件", "uncertain": "待确认", "excluded": "已排除"}[a.category]
            if a and a.version == state.version and a.category == 'uncertain' and common_unknown:
                category = '候选（共同限制见回复）'
            labels = {"satisfied": "满足", "violated": "不符合", "unknown": "待确认"}
            basis = "；".join(f"{state.constraints[e.key].text}：{labels[e.verdict]}「{e.quote or '未说明'}」"
                             for e in a.evidence if e.key in state.constraints and e.key not in common_unknown) if a else ""
            rows.append([i, job.title, job.company, job.city, category, basis, job.url, job.provenance])
        with self.store.db() as db:
            latest = db.execute("SELECT * FROM runs WHERE session_id=? ORDER BY time DESC LIMIT 1", (sid,)).fetchone()
        status = "等待输入"
        trace = []
        comparisons = {}
        if latest:
            status_name = {"running": "执行中", "paused": "已暂停", "completed": "已完成",
                           "failed": "执行失败", "superseded": "已由新需求替换"}.get(latest['status'], latest['status'])
            status = f"需求版本 {state.version} · {status_name}"
            if latest["checkpoint"]:
                cp = Checkpoint.model_validate_json(latest["checkpoint"])
                status += f" · 已完成 {cp.steps} 步 · 工具调用 {cp.tool_calls} 次"
                if cp.progress.assessed:
                    status += f"\n{cp.progress.summary}"
            trace = self.store.events(latest["id"], kinds=(
                'delivery', 'action', 'error', 'superseded', 'action_quota_capped', 'model_error'))
            with self.store.db() as db:
                tool_summary = db.execute('''SELECT json_extract(data,'$.action.kind') action,
                    json_extract(data,'$.result.status') status,COUNT(*) amount FROM events
                    WHERE run_id=? AND kind='tool' AND json_valid(data)
                    GROUP BY action,status''', (latest['id'],)).fetchall()
            delivered = [e['data'] for e in trace if e['kind'] == 'delivery']
            if delivered:
                comparisons = {item['job_id']: item for item in delivered[-1].get('items', [])}
            detail_count = sum(r['amount'] for r in tool_summary if r['action'] == 'detail' and r['status'] == Status.OK)
            status += f" · 本轮已读 {detail_count} 条详情"
            actions = [e for e in trace if e["kind"] == "action"]
            if latest['status'] == "running" and actions:
                phase = {"search_batch": "搜索岗位", "search": "搜索岗位", "detail_batch": "逐条读取详情",
                         "detail": "读取详情", "assess_batch": "核验条件", "assess": "核验条件",
                         "stop": "整理回复"}.get(actions[-1]['data']['kind'], "处理需求")
                status += f" · 当前：{phase}"
            if latest['status'] == "paused" and any(e['kind'] == "error" for e in trace):
                status += " · 处理异常，任务已停止；已读数据保留，可恢复任务"
            if isinstance(self.engine.provider, BossCDP) and latest["id"] not in self.browser_error_seen:
                if any(r['status'] in (Status.LOGIN, Status.BLOCKED) for r in tool_summary):
                    self.browser_ready = False
                    self.browser_status = "登录已失效或页面需要验证。请完成浏览器验证后重新检查，再发送需求继续。"
                    self.browser_error_seen.add(latest["id"])
            # UI shows short decisions and errors, not raw upstream payloads.
            trace = [e for e in trace if e["kind"] in ("action", "error", "superseded", "action_quota_capped")]
        constraints = {"岗位条件": [c.model_dump() for c in state.constraints.values()],
                       "用户背景": [f.model_dump() for f in state.background.values()],
                       "交付要求": [f.model_dump() for f in state.output.values()],
                       "共同待核实条件": [state.constraints[k].text for k in common_unknown],
                       "岗位比较依据": [{'岗位': self.store.job(jid).title,
                           '主要顾虑': comparisons[jid].get('concern', ''),
                           '原文': comparisons[jid].get('supporting_quotes', [])}
                           for jid in state.display if jid in comparisons],
                       "逐岗完整证据": [{'岗位': self.store.job(a.job_id).title,
                           '来源': self.store.job(a.job_id).url,
                           '获取时间': self.store.job(a.job_id).fetched_at,
                           '需求版本': a.version, '证据': [e.model_dump() for e in a.evidence]} for a in current]}
        future = self.futures.get(latest['id']) if latest else None
        active = bool(future and not future.done())
        constraints['_ui_run'] = {'status': latest['status'] if latest else '',
            'pause': bool(latest and latest['status']=='running' and active),
            'resume': bool(latest and latest['status'] in ('running','paused') and not active)}
        return state.history, rows, status, constraints, trace[-20:]

    def render_with_browser(self, sid):
        return (*self.render(sid), self.browser_status)

    def render_workspace(self, sid, archived=False, selected=None, last_signature=None, request: Request = None):
        import gradio as gr
        history, rows, status, constraints, trace = self.render(sid)
        run = constraints.pop('_ui_run', {})
        if request:
            active = self.active_sessions.setdefault(request.session_hash, sid)
            if active != sid:
                return tuple(gr.skip() for _ in range(17))
        state = self.store.session(sid)
        source_reset = False
        if self.source_epoch:
            with self.store.db() as db:
                latest = db.execute('SELECT MAX(time) FROM runs WHERE session_id=?', (sid,)).fetchone()[0]
            source_reset = latest is None or latest < self.source_epoch
            if source_reset:
                rows = []
                status = '数据来源已切换，请发送需求开始检索。'
                constraints['来源提示'] = '旧证据保留，但不是当前数据源的新检索结果。'
                run = {}
        choices = self.conversation_choices(sid, archived)
        title = next((label for label, value in choices if value == sid), '新会话')
        compact = []
        for row in rows:
            job = self.store.job(state.display[row[0] - 1])
            label = job.title.replace('[', '［').replace(']', '］')
            link = f'[{label}]({job.url})' if urlsplit(job.url).scheme in ('https', 'http') else label
            conclusion = {'符合已核查条件': '条件已核验', '候选（共同限制见回复）': '待核实',
                          '更新中': '待重新核验'}.get(row[4], row[4])
            company = job.company + (' · 合成样本' if job.provenance == 'synthetic' else '')
            compact.append([row[0], link + '\n\n' + company, job.salary or '未标注', conclusion])
        display = [] if source_reset else state.display
        detail_choices = [(f'{i}. {self.store.job(jid).company} · {self.store.job(jid).title}', jid)
                          for i, jid in enumerate(display, 1)]
        selected = selected if selected in display else (display[0] if display else None)
        label = ('运行中' if '执行中' in status else '已暂停' if '已暂停' in status else
                 '已完成' if '已完成' in status else '等待输入')
        if run.get('status') == 'failed':
            label = '任务未完成'
            if history and history[-1]['role'] == 'user':
                history = history + [{'role': 'assistant', 'content': '系统提示：这次任务未完成，需求已保留。请在使用设置中检查模型服务或数据连接，再重试；这不是“没有合适岗位”。'}]
        elif run.get('resume'):
            label = '可恢复任务'
            if (run.get('status') == 'paused' and any(e['kind'] == 'error' for e in trace)
                    and history and history[-1]['role'] == 'user'):
                label = '任务异常暂停'
                history = history + [{'role': 'assistant', 'content':
                    '系统提示：这次任务因处理异常已暂停，不是在继续搜索，也不代表没有合适岗位。'
                    '你的需求和已取得的岗位数据已保留；可以点击“继续任务”重试，或发送新消息调整要求。'}]
        phase = re.search(r'当前：([^·\n]+)', status)
        details = re.search(r'本轮已读 \d+ 条详情', status)
        short = ' · '.join(v for v in (phase[1].strip() if phase else '', details[0] if details else '') if v)
        status_html = ('<span class="status-pill"><span class="status-dot"></span>' + label + '</span>'
                       + '<div class="status-copy">' + html.escape(short) + '</div>')
        source_label = {'实时 BOSS': '实时 BOSS · ' + ('登录已检查' if self.browser_ready else '等待检查登录'),
                        '本地快照': '本地快照 · 不访问招聘平台'}.get(self.source_mode, self.source_mode)
        if self.source_mode == '腾讯官网':
            source_label = '腾讯招聘官网 · 仅腾讯职位，无需浏览器或扫码'
        if self.source_mode == '本地快照' and isinstance(self.engine.provider, Snapshot) and any(
                j.provenance == 'synthetic' for j in self.engine.provider.catalog.values()):
            source_label += ' · 含合成样本'
        heading = '<div class="page-heading">' + html.escape(title) + '</div><div class="page-sub">' + html.escape(source_label) + '</div>'
        chips = ''.join('<span class="condition-chip">' + html.escape(c.text) +
                        (' · 偏好' if c.kind == 'soft' else '') + '</span>' for c in state.constraints.values())
        detail = self.job_detail(sid, selected)
        def stamp(value):
            return hashlib.sha256(repr(value).encode()).hexdigest()
        signature = stamp((sid, history, compact, status, constraints, trace, self.browser_status, choices,
                           selected, source_label, detail, run))
        previous = last_signature if isinstance(last_signature, dict) and last_signature.get('sid') == sid else {}
        cache = {'sid': sid, 'view': signature, 'chat': stamp(history), 'rows': stamp(compact),
                 'detail': stamp(detail), 'picker': stamp((detail_choices, selected))}
        if previous.get('view') == signature:
            return tuple(gr.skip() for _ in range(17))
        chat_update = gr.skip() if previous.get('chat') == cache['chat'] else gr.update(value=history, visible=bool(history))
        return (chat_update, gr.skip() if previous.get('rows') == cache['rows'] else compact,
                status_html, constraints, trace, self.browser_status,
                gr.update(choices=choices, value=sid), heading, gr.update(visible=not bool(history)),
                gr.update(visible=not bool(rows)),
                gr.skip() if previous.get('picker') == cache['picker'] else gr.update(choices=detail_choices, value=selected),
                gr.skip() if previous.get('detail') == cache['detail'] else detail,
                '<div class="section-heading">岗位工作区<span class="count-badge">' + str(len(rows)) + ' 个候选</span></div>',
                chips or '<span class="page-sub">你的岗位条件会在对话中逐步形成。</span>',
                gr.update(visible=run.get('pause', False)), gr.update(visible=run.get('resume', False)), cache)

    def build(self):
        from .ui_layout import build_workspace
        return build_workspace(self)
