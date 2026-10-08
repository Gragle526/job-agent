import os
from pathlib import Path


def load_project_env() -> None:
    """Read only the current project's .env; explicit process variables take precedence."""
    from dotenv import load_dotenv

    load_dotenv(Path.cwd() / ".env", override=False)


def api_key(base_url: str) -> str:
    """Environment wins; the project-local fallback is only for the official DeepSeek host."""
    value = os.environ.get(os.environ.get("JOB_AGENT_API_KEY_ENV", "DEEPSEEK_API_KEY"), "")
    if value:
        return value
    explicit_file = os.environ.get('JOB_AGENT_API_KEY_FILE')
    if explicit_file:
        path = Path(explicit_file)
        return path.read_text().strip() if path.is_file() else ''
    if base_url.rstrip("/") != "https://api.deepseek.com":
        return ""
    path = Path("workspace/secrets/deepseek-api-key")
    return path.read_text().strip() if path.is_file() else ""
