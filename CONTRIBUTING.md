# 开发说明

本仓库只维护求职Agent及其运行所需的组件。实验题库、基线、裁判和个人运行资料不提交。

```bash
uv sync --locked --extra dev
uv run pytest -q
uv run ruff check src tests
```

修改时说明触发条件、原有行为、改动后行为和验证方法。需求处理要保留用户未改的硬条件；执行器要检查非法参数、旧任务失效与预算；工具要区分空结果、登录失效、受阻、超时和解析失败。

必要单元测试使用最小临时对象和模拟接口，不运行付费模型或真实招聘采集。不要提交密钥、真实JD、个人会话和登录资料。文档遵循 [docs/documentation-style.md](docs/documentation-style.md)。
