from job_agent.config import api_key


def test_private_file_is_only_used_for_official_host(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("JOB_AGENT_API_KEY_ENV", raising=False)
    folder = tmp_path / "workspace/secrets"
    folder.mkdir(parents=True)
    (folder / "deepseek-api-key").write_text("local-test-placeholder")
    assert api_key("https://api.deepseek.com") == "local-test-placeholder"
    assert api_key("https://different-provider.example") == ""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "env-test-placeholder")
    assert api_key("https://api.deepseek.com") == "env-test-placeholder"


def test_explicit_provider_key_file_and_environment_override(tmp_path, monkeypatch):
    path = tmp_path / 'qwen-key'
    path.write_text('local-test-placeholder')
    monkeypatch.setenv('JOB_AGENT_API_KEY_ENV', 'DASHSCOPE_API_KEY')
    monkeypatch.delenv('DASHSCOPE_API_KEY', raising=False)
    monkeypatch.setenv('JOB_AGENT_API_KEY_FILE', str(path))
    assert api_key('https://dashscope.aliyuncs.com/compatible-mode/v1') == 'local-test-placeholder'
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'env-placeholder')
    assert api_key('https://dashscope.aliyuncs.com/compatible-mode/v1') == 'env-placeholder'
