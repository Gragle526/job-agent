# 第三方组件

本项目自研代码使用MIT许可证；第三方软件与招聘数据遵循各自授权。

- 工程依赖包括Pydantic、OpenAI Python SDK、Gradio等，清单见 `pyproject.toml` 和 `uv.lock`。
- BOSS接入复用 [eatmoreduck/boss-zhipin-scraper](https://github.com/eatmoreduck/boss-zhipin-scraper/tree/46e2965de42c6c44a18021c1f8602a62b838297c)，固定该提交。用户点击准备组件后下载到本地工作区；保留上游MIT许可证并校验SHA-256，上游源码不随仓库分发。
- 腾讯公开职位由本项目适配器读取；招聘内容不因程序读取而成为本项目自有或MIT授权内容。
