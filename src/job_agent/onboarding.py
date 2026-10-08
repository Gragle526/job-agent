"""Local setup checks and explicitly requested, pinned browser-tool preparation."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import urllib.request
import venv
import sys

from .config import api_key

REVISION = '46e2965de42c6c44a18021c1f8602a62b838297c'
FILES = {
    'scripts/boss_cdp_raw.py': '574abc5291d5a08052f65c44c80a21e070f6a57b2a8d0a1dd0c6dbe36b96a8e3',
    'data/city_codes.json': 'ea3cc88f9520244e2659df77b1496312f2c713e3c1126ad1a81566490fcfdd5f',
    'LICENSE': 'f48cc447f5cc8a2f76770567defc0e1f9a61e2f234a8fa5c417e451fa382d0e6',
}
MODELS = {
    'Qwen': ('qwen3.7-flash', 'https://maas.qianwenaiapi.com/compatible-mode/v1', '0.2', '0.8'),
    'DeepSeek': ('deepseek-flash', 'https://api.deepseek.com', '2', '8'),
}


def native_browsers():
    candidates = []
    for label, executable in [('Chrome', 'google-chrome'), ('Chromium', 'chromium'),
                               ('Chromium', 'chromium-browser'), ('Edge', 'microsoft-edge')]:
        if path := shutil.which(executable):
            candidates.append((label, path))
    if platform.system() == 'Darwin':
        candidates += [('Chrome', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),
                       ('Edge', '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge')]
    for base in [os.environ.get('PROGRAMFILES'), os.environ.get('PROGRAMFILES(X86)'),
                 os.environ.get('LOCALAPPDATA'), '/mnt/c/Program Files', '/mnt/c/Program Files (x86)']:
        if base:
            candidates += [('Chrome', str(Path(base) / 'Google/Chrome/Application/chrome.exe')),
                           ('Edge', str(Path(base) / 'Microsoft/Edge/Application/msedge.exe'))]
    return list(dict.fromkeys((label, path) for label, path in candidates if Path(path).is_file()))


def missing_browser_libraries(executable):
    if platform.system() != 'Linux' or executable.lower().endswith('.exe') or not shutil.which('ldd'):
        return []
    try:
        result = subprocess.run(['ldd', executable], capture_output=True, text=True, timeout=5)
        return [line.strip().split('=>')[0].strip() for line in result.stdout.splitlines() if 'not found' in line]
    except (OSError, subprocess.TimeoutExpired):
        return []


def check_environment(provider=None):
    upstream = Path(getattr(provider, 'upstream', 'workspace/tools/boss-cdp/scripts/boss_cdp_raw.py'))
    root = upstream.parent.parent
    try:
        verified = all((root / relative).is_file() and hashlib.sha256((root / relative).read_bytes()).hexdigest()==expected
                       for relative, expected in FILES.items())
    except OSError:
        verified = False
    runtime = getattr(provider, 'python', '')
    native_ready = all(importlib.util.find_spec(p) for p in ('requests', 'websocket'))
    runtime_ready = bool(runtime and Path(runtime).is_file()) or native_ready
    browsers = native_browsers()
    managed = Path('workspace/browser-runtime/browser.json')
    if managed.is_file():
        try:
            path = json.loads(managed.read_text()).get('executable', '')
            if isinstance(path, str) and Path(path).is_file():
                browsers.insert(0, ('独立 Chromium', path))
        except (ValueError, OSError, AttributeError):
            pass
    custom = getattr(provider, 'browser_path', '')
    if custom and Path(custom).is_file():
        browsers.insert(0, ('指定浏览器', custom))
    display = platform.system() in ('Windows', 'Darwin') or bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))
    bridge = bool(runtime and runtime.lower().endswith('.exe') and platform.system() != 'Windows')
    if platform.system() != 'Windows' and not bridge:
        browsers = [(label, path) for label, path in browsers if not path.lower().endswith('.exe')]
    missing = missing_browser_libraries(browsers[0][1]) if browsers else []
    return {'model_configured': bool(api_key(os.getenv('JOB_AGENT_BASE_URL', 'https://api.deepseek.com'))),
            'model': os.getenv('JOB_AGENT_MODEL', '未配置'), 'platform': platform.system(),
            'wsl': 'microsoft' in platform.release().lower(), 'tools_verified': verified,
            'runtime_ready': runtime_ready, 'browser_choices': browsers,
            'has_desktop': display or bridge, 'windows_bridge': bridge, 'missing_libraries': missing}


def setup_status(provider=None):
    info = check_environment(provider)
    labels = [('模型密钥', info['model_configured']), ('采集组件', info['tools_verified']),
              ('运行环境', info['runtime_ready']), ('兼容浏览器', bool(info['browser_choices'])),
              ('可交互桌面', info['has_desktop'])]
    lines = ['### 本机准备情况', *[f"{'✓' if ready else '○'} **{label}**：{'已就绪' if ready else '待准备'}" for label, ready in labels]]
    if info['browser_choices']:
        lines.append('\n已发现：' + '、'.join(dict.fromkeys(label for label, _ in info['browser_choices'])))
    if not info['has_desktop']:
        lines.append('\n当前没有可交互桌面，无法扫码。请在有桌面的电脑运行，或先使用离线模式。')
    if info['missing_libraries']:
        lines.append('\n浏览器文件已找到，但系统库缺失：' + '、'.join(info['missing_libraries'])
                     + '。Linux需管理员准备系统依赖；可按Playwright官方浏览器依赖指引安装，本助手不会自动sudo。')
    if info['wsl'] and not info['windows_bridge']:
        lines.append('\nWSL用户：优先在Windows原生Python启动本项目；已有Windows运行环境时可在高级设置填写python.exe路径。')
    lines.append('\n本机检测不产生模型调用，也不代表BOSS已经登录。')
    return '\n\n'.join(lines)


def save_model_settings(choice, key):
    if choice not in MODELS:
        raise ValueError('请选择支持的模型服务')
    if not key.strip():
        raise ValueError('请输入API Key；不要把密钥发进聊天')
    if '\n' in key.strip() or '\r' in key.strip():
        raise ValueError('密钥不能包含换行')
    secret = Path('workspace/secrets/ui-model-api-key')
    secret.parent.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        secret.parent.chmod(0o700)
    # Restrictive mode applies before writing the credential, including replacement.
    fd = os.open(secret, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(key.strip())
    if os.name != 'nt':
        secret.chmod(0o600)
    model, url, input_rate, output_rate = MODELS[choice]
    values = {'JOB_AGENT_MODEL': model, 'JOB_AGENT_BASE_URL': url,
              'JOB_AGENT_API_KEY_ENV': 'JOB_AGENT_UI_API_KEY', 'JOB_AGENT_API_KEY_FILE': str(secret.resolve()),
              'JOB_AGENT_INPUT_CNY_PER_MILLION': input_rate, 'JOB_AGENT_OUTPUT_CNY_PER_MILLION': output_rate}
    persist(values)
    return '模型配置已保存到本机。密钥不会展示；联网调用是否可用会在首次请求中验证。'


def persist(values):
    from dotenv import set_key
    env = Path('.env')
    if not env.exists():
        fd = os.open(env, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
    for name, value in values.items():
        set_key(env, name, str(value))
        os.environ[name] = str(value)
    if os.name != 'nt':
        env.chmod(0o600)


def download_tools(root=Path('workspace/tools/boss-cdp'), fetch=None):
    fetch = fetch or (lambda url: urllib.request.urlopen(url, timeout=30).read())
    # Fetch/check every file before installing any of them.
    payloads = {}
    for relative, expected in FILES.items():
        path = root / relative
        data = path.read_bytes() if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected else fetch(
            f'https://raw.githubusercontent.com/eatmoreduck/boss-zhipin-scraper/{REVISION}/{relative}')
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('采集组件版本校验失败，没有安装；请重试或按文档手动准备')
        payloads[relative] = data
    for relative, data in payloads.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_bytes(data)
        temporary.replace(path)
    return str((root / 'scripts/boss_cdp_raw.py').resolve())


def prepare_browser_tools(provider, managed=False):
    """Called only by an explicit setup button; never on launch or doctor."""
    folder = Path('workspace/browser-runtime')
    folder.mkdir(parents=True, exist_ok=True)
    upstream = download_tools()
    runtime = getattr(provider, 'python', '')
    if runtime and Path(runtime).is_file() and runtime.lower().endswith('.exe') and os.name != 'nt':
        if managed:
            return '当前使用Windows桥接环境。请安装Windows版Chrome或Edge，再选择该浏览器；不在WSL中下载另一套浏览器。'
        provider.upstream = Path(upstream)
        probe = subprocess.run([runtime, '-c', 'import requests, websocket'], capture_output=True, timeout=10)
        if probe.returncode:
            return '采集脚本已准备，但Windows运行环境缺requests/websocket-client。请在该独立环境执行python -m pip install requests websocket-client，再检查。'
        return '采集组件已校验。继续使用已有Windows运行环境，请打开浏览器扫码并检查登录。'
    if not managed and all(importlib.util.find_spec(p) for p in ('requests', 'websocket')):
        provider.python, provider.upstream = sys.executable, Path(upstream)
        persist({'JOB_AGENT_CDP_PYTHON': sys.executable, 'JOB_AGENT_CDP_UPSTREAM': upstream})
        return '采集组件已校验，直接复用当前Python依赖。请选择浏览器、扫码并检查登录。'
    env_folder = folder / '.venv'
    executable = env_folder / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not executable.is_file():
        if uv := shutil.which('uv'):
            with (folder / 'install.log').open('a') as log:
                subprocess.run([uv, 'venv', str(env_folder), '--python', sys.executable],
                               stdout=log, stderr=log, timeout=180, check=True)
        else:
            venv.EnvBuilder(with_pip=True).create(env_folder)
    packages = ['requests>=2.28,<3', 'websocket-client>=1.6,<2']
    if managed:
        if not check_environment(provider)['has_desktop']:
            return '没有可交互桌面，暂不能运行扫码浏览器。请在桌面电脑运行；采集脚本已准备。'
        packages.append('playwright>=1.56,<2')
    commands = [[shutil.which('uv'), 'pip', 'install', '--python', str(executable), *packages]] if shutil.which('uv') else [
        [str(executable), '-m', 'pip', 'install', *packages]]
    if managed:
        commands.append([str(executable), '-m', 'playwright', 'install', 'chromium'])
    env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str((folder / 'browsers').resolve()))
    with (folder / 'install.log').open('a') as log:
        for cmd in commands:
            result = subprocess.run(cmd, stdout=log, stderr=log, env=env, timeout=600, check=False)
            if result.returncode:
                return '准备失败。网络、Python包源或系统库可能不可用；日志保存在workspace/browser-runtime/install.log。可按指引手动安装。'
    provider.python, provider.upstream = str(executable.resolve()), Path(upstream)
    values = {'JOB_AGENT_CDP_PYTHON': provider.python, 'JOB_AGENT_CDP_UPSTREAM': upstream}
    if managed:
        result = subprocess.run([provider.python, '-c',
            'from playwright.sync_api import sync_playwright; p=sync_playwright().start(); print(p.chromium.executable_path); p.stop()'],
            capture_output=True, text=True, env=env, timeout=30, check=True)
        browser = result.stdout.strip()
        if not Path(browser).is_file():
            return '浏览器下载未完成，请重试或选择已有Chrome/Edge。'
        provider.browser_path = browser
        values['JOB_AGENT_BROWSER_PATH'] = browser
        (folder / 'browser.json').write_text(json.dumps({'executable': browser}))
    persist(values)
    if managed and missing_browser_libraries(provider.browser_path):
        return '下载已完成，但Linux系统库还未齐备。请运行专用环境的python -m playwright install-deps chromium（可能需要管理员权限），再重新检测；本助手不会自动提权。'
    return '组件已准备并保存。下一步打开专用浏览器、本人扫码，再检查登录。'


class DeferredReasoner:
    """Allow an unconfigured new user to open setup before making paid requests."""
    name = 'cloud'

    def __init__(self, store, token_limit):
        self.store, self.token_limit, self._model = store, token_limit, None

    def reset(self):
        self._model = None

    def __getattr__(self, name):
        from .reasoning import CloudReasoner
        if self._model is None:
            self._model = CloudReasoner(self.store, token_limit=self.token_limit)
        return getattr(self._model, name)
