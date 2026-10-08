"""Local-only visual theme; no external fonts or analytics."""
CSS = r'''
:root {--ja-ink:#182b2b;--ja-muted:#71817d;--ja-accent:#087f6f;--ja-line:#e4ebe7;}
.gradio-container {max-width:none!important;padding:0!important;background:#fafbf9!important;
 font-family:Inter,"PingFang SC","Microsoft YaHei",system-ui,sans-serif!important;color:var(--ja-ink);}
footer {display:none!important;}
.gradio-container > .main, .gradio-container > .wrap, main {padding:0!important;margin:0!important;}
.main.fill_width {padding:0!important;}
#sidebar, #chat-column, #jobs-column {flex-wrap:nowrap!important;}
#jobs-column > * {flex-shrink:0!important;}
#welcome, #welcome .form, #composer .form, #setup-panel .form {background:transparent!important;}
#workspace .form, #workspace .styler, #setup-panel .form, #setup-panel .styler {background:transparent!important;}
#welcome {border:none!important;padding:0!important;}
#chat button[aria-label*="Delete"], #chat button[aria-label*="delete"] {display:none!important;}
#workspace {gap:0!important;min-height:100dvh;}
#sidebar {background:#f0f4f1;border-right:1px solid var(--ja-line);padding:28px 18px!important;
 width:224px!important;min-width:224px!important;max-width:224px;min-height:100dvh;}
.brand {display:flex;align-items:center;gap:10px;font-weight:700;font-size:19px;letter-spacing:-.5px;margin-bottom:25px;}
.brand-mark {background:#087f6f;color:white;border-radius:12px;width:35px;height:35px;display:grid;place-items:center;font-size:19px;}
.eyebrow {font-size:11px;font-weight:600;letter-spacing:1.7px;color:#83928a;text-transform:uppercase;margin:22px 0 10px;}
#sessions {background:transparent!important;border:none!important;}
#sessions .wrap {display:flex!important;flex-direction:column!important;gap:5px;}
#sessions label {border:0!important;background:transparent!important;border-radius:9px!important;padding:11px 10px!important;
 width:100%!important;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:13px!important;}
#sessions label:has(input:checked) {background:#dfeae3!important;color:#086354!important;font-weight:600;}
#sessions input {display:none!important;}
#sessions .wrap {max-height:calc(100dvh - 370px);overflow-y:auto;}
#sidebar button {border-radius:9px!important;font-size:13px!important;}
#new-chat {background:#087f6f!important;color:white!important;border:none!important;min-height:43px;}
#chat-column {padding:28px 32px 18px!important;min-width:340px!important;gap:15px!important;}
.page-heading {font-size:23px;font-weight:650;letter-spacing:-.5px;margin:0;}
.page-sub {font-size:12px;color:var(--ja-muted);margin-top:6px;line-height:1.7;}
#chat {border:none!important;background:transparent!important;box-shadow:none!important;}
#chat .bubble {border-radius:16px!important;box-shadow:none!important;}
#chat .user {background:#e6efe9!important;}
#chat .bot {background:white!important;}
#chat .message {font-size:14px!important;line-height:1.85!important;}
#chat .prose {font-size:14px;line-height:1.85;}
#welcome {margin:45px auto 24px;max-width:560px;}
.welcome-icon {width:52px;height:52px;border-radius:17px;background:#e6efe9;color:#087f6f;display:grid;place-items:center;font-size:28px;margin-bottom:18px;}
.welcome-title {font-size:28px;font-weight:650;letter-spacing:-1px;margin-bottom:12px;}
.welcome-text {color:#788580;font-size:14px;line-height:1.9;}
#suggestions button {border:1px solid #e0e7e1!important;border-radius:12px!important;background:white!important;
 text-align:left;min-height:62px;font-size:12px!important;color:#4c645d!important;}
#composer {border:1px solid #dce6de!important;border-radius:17px!important;background:white!important;
 box-shadow:0 5px 24px #183d2810;padding:12px!important;margin-top:auto;}
#message {border:none!important;box-shadow:none!important;background:transparent!important;}
#message textarea {border:none!important;background:transparent!important;font-size:14px!important;line-height:1.6;}
#composer-actions {justify-content:flex-end;gap:8px!important;}
#composer-actions button {min-width:70px!important;max-width:110px!important;border-radius:9px!important;font-size:12px!important;}
#send {background:#087f6f!important;color:white!important;border:none!important;}
.status-pill {display:inline-flex;align-items:center;gap:7px;background:#eef3ed;color:#65796c;
 font-size:12px;border-radius:20px;padding:6px 12px;}
.status-dot {width:6px;height:6px;background:#3e9475;border-radius:50%;}
.status-copy {font-size:12px;color:#78887d;line-height:1.65;margin-top:8px;}
#jobs-column {background:#fff;border-left:1px solid var(--ja-line);padding:28px 20px!important;
 min-width:365px!important;max-width:510px;gap:15px!important;}
.section-heading {display:flex;justify-content:space-between;align-items:center;font-size:17px;font-weight:600;}
.count-badge {font-size:11px;background:#edf5ed;color:#47755c;border-radius:6px;padding:4px 8px;}
.empty-jobs {padding:34px 18px;text-align:center;border:1px dashed #dfe7df;border-radius:14px;color:#87928a;font-size:13px;line-height:1.9;}
#jobs-table {border:1px solid #e6ece7!important;border-radius:12px!important;overflow:hidden;}
#jobs-table table {font-size:12px!important;}
#jobs-table th {background:#f5f8f4!important;font-size:11px!important;color:#839087!important;}
#jobs-table td {line-height:1.6!important;}
#jobs-table a {color:#087f6f!important;text-decoration:none!important;font-weight:550;}
#job-detail {font-size:12px;line-height:1.9;background:#f8faf7;padding:14px;border-radius:12px;}
.condition-chip {display:inline-block;border:1px solid #dfe9df;border-radius:7px;padding:4px 8px;margin:3px 4px 3px 0;font-size:11px;color:#4e6858;}
.setup-hint {background:#eaf2eb;border:1px solid #dce8df;padding:13px;border-radius:11px;font-size:12px;line-height:1.8;color:#52705d;}
#setup-panel {position:fixed!important;inset:4vh 9vw!important;z-index:50;overflow:auto;
 background:#fff!important;border:1px solid #dae6dd!important;border-radius:20px!important;
 box-shadow:0 0 0 100vmax #11291d66,0 20px 80px #11291d44;padding:28px!important;}
#setup-panel .tab-nav {font-size:13px;}
#setup-panel button {border-radius:9px!important;}
.setup-step {font-size:11px;color:#087f6f;letter-spacing:1px;margin-bottom:6px;}
#rename-row {gap:5px!important;}
#rename-row button {min-width:50px!important;}
@media(min-width:1150px) {#chat-column{height:100dvh;}#chat{flex:1;min-height:0;height:calc(100dvh - 285px)!important;}#jobs-column{height:100dvh;overflow:auto;}}
@media(max-width:1149px) {#jobs-column{min-width:300px!important;max-width:none;}#chat-column{padding:24px 20px!important;}}
@media(max-width:900px) {#sidebar{width:185px!important;min-width:185px!important;}#workspace{flex-wrap:wrap!important;}
 #jobs-column{border-left:0;border-top:1px solid var(--ja-line);max-width:none;width:100%;}
 #setup-panel{inset:2vh 3vw!important;}#chat{height:450px!important;}}
@media(max-width:600px) {#sidebar{width:100%!important;min-width:100%!important;max-width:none;min-height:auto;
 padding:16px!important;}#sidebar .brand{margin-bottom:12px;}#sessions .wrap{max-height:145px;}
 #chat-column{min-width:0!important;}#jobs-column{min-width:0!important;}#welcome{margin-top:20px;}}
'''


def theme():
    import gradio as gr
    return gr.themes.Soft(primary_hue='emerald', secondary_hue='slate', neutral_hue='slate',
                          font=[gr.themes.Font('system-ui')], font_mono=[gr.themes.Font('monospace')]).set(
        body_background_fill='#fafbf9', body_background_fill_dark='#fafbf9',
        background_fill_primary='white', background_fill_primary_dark='white',
        block_background_fill='white', block_background_fill_dark='white',
        background_fill_secondary='transparent', background_fill_secondary_dark='transparent',
        panel_background_fill='transparent', panel_background_fill_dark='transparent',
        block_label_background_fill='transparent', block_label_background_fill_dark='transparent',
        block_label_text_color='#71817d', block_label_text_color_dark='#71817d',
        block_label_shadow='none', block_shadow='none', block_shadow_dark='none',
        button_primary_background_fill='#087f6f', button_primary_background_fill_hover='#066b5d',
        body_text_color='#182b2b', body_text_color_dark='#182b2b',
        block_border_color='#e4ebe7', block_border_color_dark='#e4ebe7',
        input_background_fill='white', input_background_fill_dark='white')
