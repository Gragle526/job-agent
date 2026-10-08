# GitHub 发布文件检查

本指南用于把本地研究工作区整理为公开源码。提交代码与发布应用是两件事；这些步骤不启动公网服务，也不上传个人招聘资料。

## 公开与本地文件

公开内容包括源码、测试、配置模板和运行/机制文档。`workspace/`、`.env`、密钥、浏览器profile、数据库、真实采集数据和个人会话不提交。

本地运行环境 `.venv/` 保留但忽略；可重建缓存清理。研究材料及旧输出移到仓库外的私有备份，公开仓库只保留Agent本身。

## 提交前检查

```bash
uv sync --locked --extra dev
uv run pytest -q
uv run ruff check src tests

git status --short
git ls-files --cached --others --exclude-standard
```

检查列出的文件没有 `.env`、`workspace/`、数据库、私人岗位文件和凭据。`.gitignore`不会取消已经跟踪的文件；如果此前误提交过密钥，仅删除文件不足以撤回泄露，需更换密钥并清理历史。

确认文件后在本地暂存，再检查即将提交的内容：

```bash
git add README.md .env.example .gitignore LICENSE THIRD_PARTY_NOTICES.md CONTRIBUTING.md
git add pyproject.toml uv.lock src tests docs .github
git diff --cached --stat
git diff --cached --check
```

GitHub仓库地址和可见性由项目维护者决定；配置remote、commit和push应在确认实际发布内容后执行。本指南不预设账号或仓库地址。

## 备份恢复

完整整理前备份保存在仓库外，可能包含密钥、登录和真实岗位，属于私有备份，不能当作源码包上传。恢复时先解压到独立目录检查，避免覆盖正在运行的工作区。

公开源码包应根据Git公开文件清单生成，排除 `.git/` 与本地忽略文件；它与完整私有备份用途不同。
