"""Three-column workspace and explicit first-run setup."""
import os
import uuid

import gradio as gr

from .onboarding import check_environment, setup_status


def build_workspace(ui):
    os.environ.setdefault('GRADIO_ANALYTICS_ENABLED', 'False')
    ready = check_environment(ui.browser_provider)
    sid_value = ui.initial_session
    with gr.Blocks(title='Job Agent · 求职工作区', fill_width=True, analytics_enabled=False) as app:
        sid = gr.State(sid_value)
        drafts = gr.State({})
        view_signature = gr.State('')
        route = gr.Textbox(value=sid_value, visible=False)
        with gr.Row(elem_id='workspace'):
            with gr.Column(scale=0, min_width=224, elem_id='sidebar'):
                gr.HTML('<div class="brand"><span class="brand-mark">J</span>Job Agent</div>')
                fresh = gr.Button('＋  新建会话', elem_id='new-chat')
                gr.HTML('<div class="eyebrow">最近会话</div>')
                sessions = gr.Radio(ui.conversation_choices(sid_value), value=sid_value,
                                    show_label=False, elem_id='sessions')
                with gr.Accordion('管理会话', open=False):
                    rename = gr.Textbox(placeholder='新的会话名称', show_label=False)
                    with gr.Row(elem_id='rename-row'):
                        rename_button = gr.Button('改名', size='sm')
                        archive_button = gr.Button('归档', size='sm')
                    archived = gr.Checkbox(label='显示归档会话', value=False)
                    restore_button = gr.Button('恢复选中会话', size='sm')
                settings_button = gr.Button('⚙  使用设置与连接', size='sm')
                gr.HTML('<div class="page-sub">本机个人工作区<br>会话与岗位保存在本机</div>')
            with gr.Column(scale=5, min_width=340, elem_id='chat-column'):
                heading = gr.HTML()
                with gr.Group(elem_id='welcome') as welcome:
                    gr.HTML('<div class="welcome-icon">↗</div><div class="welcome-title">把想找的工作告诉我。</div>'
                            '<div class="welcome-text">说说岗位方向、城市和你在意的条件。<br>我们一起检索、读懂要求，再比较值得了解的机会。</div>')
                    with gr.Row(elem_id='suggestions'):
                        example1 = gr.Button('试试找岗\n线上 Agent 实习，每周最多三天')
                        example2 = gr.Button('试试调整\n更偏好规划、工具调用与评测')
                chat = gr.Chatbot(height='calc(100dvh - 280px)', show_label=False, container=False,
                                  elem_id='chat', buttons=['copy'], allow_file_downloads=False,
                                  layout='bubble', placeholder='')
                status = gr.HTML()
                with gr.Group(elem_id='composer'):
                    message = gr.Textbox(placeholder='输入求职需求，或继续追问某个岗位…', lines=2,
                                         max_lines=6, show_label=False, container=False, elem_id='message')
                    with gr.Row(elem_id='composer-actions'):
                        pause = gr.Button('暂停', size='sm')
                        resume = gr.Button('继续任务', size='sm')
                        send = gr.Button('发送  ↑', variant='primary', size='sm', elem_id='send')
                gr.HTML('<div class="page-sub">原文没写的条件会标为待确认。筛选结果供比较，投递前请核实。</div>')
            with gr.Column(scale=3, min_width=365, elem_id='jobs-column'):
                jobs_heading = gr.HTML()
                condition_chips = gr.HTML()
                empty = gr.HTML('<div class="empty-jobs">岗位会整理到这里<br><span style="font-size:11px">开始对话后，查看候选、来源和关键条件。</span></div>')
                candidates = gr.Dataframe(headers=['#', '岗位 / 公司', '薪资', '状态'],
                    datatype=['number', 'markdown', 'str', 'str'], type='array',
                    interactive=False, wrap=True, max_height=370, show_label=False,
                    elem_id='jobs-table', column_widths=[26, 180, 70, 90])
                # Polls can carry a selection from the previous shortlist. Let
                # WorkspaceUI validate it against the current session/display,
                # rather than rejecting the entire refresh before it reaches us.
                selected = gr.Dropdown(choices=[], label='查看岗位详情', interactive=True,
                                       allow_custom_value=True)
                job_detail = gr.Markdown(elem_id='job-detail')
                with gr.Accordion('需求与执行记录', open=False):
                    constraints = gr.JSON(label='保存的需求与证据')
                    trace = gr.JSON(label='最近执行记录')
        missing = (ui.source_mode == '实时 BOSS' and not ui.browser_ready) or (
            ui.source_mode in ('本地快照', '腾讯官网') and not ready['model_configured'])
        with gr.Group(visible=missing, elem_id='setup-panel') as setup:
            with gr.Row():
                gr.Markdown('## 准备你的求职工作区\n配置一次，之后继续使用。无需安装浏览器插件。')
                close_setup = gr.Button('完成 / 返回会话', size='sm', scale=0)
            with gr.Tabs():
                with gr.Tab('① 模型与数据'):
                    gr.Markdown('选择真实岗位数据来源：'
                                '**实时BOSS**需要模型API和本人登录；**腾讯官网**只查腾讯公开职位，无需浏览器或登录；'
                                '**本地快照**分析已导出的岗位，不需要浏览器。')
                    mode = gr.Radio(['实时 BOSS', '腾讯官网', '本地快照'], value=ui.source_mode
                        if ui.source_mode in ('实时 BOSS', '腾讯官网', '本地快照') else '本地快照', label='数据模式')
                    apply_mode = gr.Button('使用此模式')
                    gr.Markdown('### 模型服务\n密钥只保存到本机私有文件，不进入聊天。云模型会接收到需求和用于分析的岗位原文，'
                                'API可能产生费用。')
                    gr.Markdown('当前：' + ('模型已配置，可直接继续浏览器步骤，无需重复填写密钥。' if ready['model_configured']
                                          else '还未配置模型，请先配置API Key。'))
                    service = gr.Dropdown(['Qwen', 'DeepSeek'], value='DeepSeek' if ready['model'].startswith('deepseek') else 'Qwen', label='模型服务')
                    key = gr.Textbox(label='API Key', type='password', placeholder='粘贴密钥，保存后自动清空')
                    save_key = gr.Button('保存模型配置', variant='primary')
                    gr.Markdown('[千问API平台](https://platform.qianwenai.com/) · [DeepSeek API平台](https://platform.deepseek.com/)')
                    with gr.Group(visible=ui.source_mode == '本地快照') as snapshot_group:
                        gr.Markdown('点击或拖入本项目导出的岗位JSON，之后不需要浏览器。')
                        snapshot = gr.File(label='导入标准岗位JSON（最多10MB）', file_types=['.json'], type='filepath')
                        import_button = gr.Button('导入并使用快照')
                    setup_message = gr.Markdown()
                with gr.Tab('② 浏览器与组件'):
                    doctor = gr.Markdown(setup_status(ui.browser_provider))
                    refresh_doctor = gr.Button('重新检测本机环境', size='sm')
                    gr.Markdown('**有Chrome或Edge**：直接选择已有浏览器。\n'
                        '**没有兼容浏览器**：可下载助手专用Chromium，或自行安装'
                        '[Chrome](https://www.google.com/chrome/)／[Edge](https://www.microsoft.com/edge/download)。\n'
                        'Firefox/Safari可以打开本界面，但BOSS采集需要Chrome、Edge或Chromium。')
                    browser_choice = gr.Radio(['自动选择', 'Chrome', 'Edge'], value='自动选择', label='采集使用的浏览器')
                    with gr.Row():
                        prepare = gr.Button('准备采集组件', variant='primary')
                        managed = gr.Button('下载组件与独立 Chromium')
                    gr.Markdown('点击才下载：固定版本脚本、Python依赖；Chromium选项还会下载较大的浏览器。'
                        '不安装研发插件，不投递，不复制普通浏览器Cookie。下载时请保持页面打开。')
                    install_status = gr.Markdown()
                    with gr.Accordion('高级设置 / 手动准备', open=False):
                        browser_path = gr.Textbox(label='浏览器可执行文件（可选）', value=ui.browser_provider.browser_path)
                        runtime = gr.Textbox(label='采集Python可执行文件（可选，WSL可指定Windows python.exe）',
                                             value=ui.browser_provider.python)
                        apply_browser = gr.Button('保存浏览器设置')
                        gr.Markdown('Windows原生、macOS和有桌面的Linux最直接。无桌面服务器无法扫码，'
                            '请使用快照或在桌面电脑运行。WSL桥接的Python与浏览器需位于Windows。\n'
                            '手动方式：`uv sync --extra setup`；完整步骤见 `docs/guides/first-run.md`。')
                with gr.Tab('③ 扫码并连接'):
                    gr.Markdown('### 打开助手专用浏览器\n在BOSS网页用本人账号扫码，保持窗口打开，再检查登录。'
                        '打开窗口不是登录成功；遇到验证码请在浏览器完成。')
                    with gr.Row():
                        open_browser = gr.Button('打开专用浏览器', variant='primary')
                        check_browser = gr.Button('已扫码 / 检查登录')
                    browser_status = gr.Markdown(ui.browser_status)
                    gr.Markdown('登录过期后回到这里重连，会话和已读岗位保留。切换到实时BOSS模式后才使用该连接。')
        outputs = [chat, candidates, status, constraints, trace, browser_status, sessions, heading,
                   welcome, empty, selected, job_detail, jobs_heading, condition_chips, pause, resume, view_signature]

        def fresh_chat(current, text, saved, request: gr.Request = None):
            saved = dict(saved or {})
            saved[current] = text
            new = uuid.uuid4().hex[:12]
            ui.store.name_conversation(new, '新会话', 'draft')
            if request:
                ui.active_sessions[request.session_hash] = new
            ui.last_views.clear()
            return new, gr.update(choices=ui.conversation_choices(new), value=new), '', saved, new

        def switch_chat(current, target, text, saved, request: gr.Request = None):
            saved = dict(saved or {})
            if target and target != current:
                if request:
                    ui.active_sessions[request.session_hash] = target
                saved[current] = text
                ui.last_views.clear()
                return target, saved.get(target, ''), saved, target
            return gr.skip(), gr.skip(), gr.skip(), gr.skip()

        def rename_chat(current, title):
            if title.strip():
                ui.store.name_conversation(current, title)
            ui.last_views.clear()

        def archive_chat(current, text, saved, request: gr.Request = None):
            try:
                ui._idle_required()
            except ValueError as exc:
                raise gr.Error(str(exc))
            ui.store.archive_conversation(current)
            return fresh_chat(current, text, saved, request)

        def apply_source(mode):
            try:
                return ui.switch_mode(mode)
            except ValueError as exc:
                raise gr.Error(str(exc))

        def apply_browser_settings(choice, path, runtime):
            try:
                return ui.configure_browser(choice, path, runtime)
            except ValueError as exc:
                raise gr.Error(str(exc))

        def restore_route(requested, request: gr.Request = None):
            known = {c['id'] for c in ui.store.list_conversations(True)}
            target = requested if requested in known or requested == sid_value or ui.store.conversation_title(requested) else sid_value
            if request:
                ui.active_sessions[request.session_hash] = target
            return target, gr.update(choices=ui.conversation_choices(target), value=target), target

        fresh.click(fresh_chat, [sid, message, drafts], [sid, sessions, message, drafts, route], queue=False).success(
            ui.render_workspace, [sid, archived, selected, view_signature], outputs, queue=False)
        sessions.input(switch_chat, [sid, sessions, message, drafts], [sid, message, drafts, route], queue=False).success(
            ui.render_workspace, [sid, archived, selected, view_signature], outputs, queue=False)
        rename_button.click(rename_chat, [sid, rename], queue=False)
        archive_button.click(archive_chat, [sid, message, drafts], [sid, sessions, message, drafts, route], queue=False).success(
            ui.render_workspace, [sid, archived, selected, view_signature], outputs, queue=False)
        restore_button.click(lambda current: ui.store.archive_conversation(current, False), sid, queue=False)
        selected.input(ui.job_detail, [sid, selected], job_detail, queue=False)
        send.click(ui.send, [message, sid], message, queue=False)
        message.submit(ui.send, [message, sid], message, queue=False)
        pause.click(ui.store.pause, sid, queue=False)
        resume.click(ui.resume, sid, queue=False)
        example1.click(lambda: '找线上Agent研发实习，每周最多三天。', outputs=message, queue=False)
        example2.click(lambda: '找Agent研发实习，每周最多三天，更偏好规划、工具调用与评测机制研发。', outputs=message, queue=False)
        settings_button.click(lambda: gr.update(visible=True), outputs=setup, queue=False)
        close_setup.click(lambda: gr.update(visible=False), outputs=setup, queue=False)
        apply_mode.click(apply_source, mode, setup_message)
        mode.change(lambda value: gr.update(visible=value=='本地快照'), mode, snapshot_group, queue=False)
        save_key.click(ui.configure_model, [service, key], [key, setup_message])
        import_button.click(ui.import_snapshot, snapshot, setup_message)
        refresh_doctor.click(lambda: setup_status(ui.browser_provider), outputs=doctor, queue=False)
        prepare.click(ui.install_tools, outputs=install_status, concurrency_id='setup', concurrency_limit=1)
        managed.click(lambda: ui.install_tools(True), outputs=install_status, concurrency_id='setup', concurrency_limit=1)
        apply_browser.click(apply_browser_settings, [browser_choice, browser_path, runtime], install_status)
        open_browser.click(ui.browser_step, outputs=browser_status, concurrency_id='boss-login', concurrency_limit=1)
        check_browser.click(lambda: ui.browser_step(check=True), outputs=browser_status,
                            concurrency_id='boss-login', concurrency_limit=1)
        timer = gr.Timer(1)
        timer.tick(ui.render_workspace, [sid, archived, selected, view_signature], outputs, queue=False)
        route.change(None, route, js="(id) => { if(id) { try { localStorage.setItem('job-agent-current',id); } catch(e) {} history.replaceState(null,'','#chat='+encodeURIComponent(id)); } }", queue=False)
        app.load(restore_route, route, [sid, sessions, route],
            js="(fallback) => { let saved=''; try { saved=localStorage.getItem('job-agent-current'); } catch(e) {} return [new URLSearchParams(location.hash.slice(1)).get('chat') || saved || fallback]; }").success(
            ui.render_workspace, [sid, archived, selected, view_signature], outputs, queue=False)
    return app
