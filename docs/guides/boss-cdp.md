# BOSS本人浏览器接入

接入复用 [eatmoreduck/boss-zhipin-scraper](https://github.com/eatmoreduck/boss-zhipin-scraper/tree/46e2965de42c6c44a18021c1f8602a62b838297c) 固定提交。上游代码不随仓库分发；在界面点击准备后下载至`workspace/tools/boss-cdp/`，保留MIT许可证并校验SHA-256。

## 准备和运行

```bash
uv sync --locked
uv run job-agent --db workspace/boss.sqlite serve --source boss-cdp
```

在使用设置中配置模型、准备组件，打开助手专用浏览器，本人扫码，再检查登录。组件下载及准备步骤由用户点击触发。已有Chrome、Edge可复用；也可下载独立Chromium。

CDP（浏览器调试连接）只连接本机loopback的9222端口。使用独立浏览器profile，不复制普通浏览器Cookie。打开窗口不等于登录成功；登录失效或验证页需要本人处理。

## 系统差异

实际完整链路验证过Windows Chrome和WSL桥接。Windows、macOS标准安装路径及Linux可执行文件有检测逻辑，但并非每个系统都完成过端到端验证。

- Windows优先用本机Python与浏览器。
- WSL桥接需在高级设置填写Windows独立Python的`python.exe`，该环境安装requests、websocket-client；也可直接在Windows运行整个项目。
- Linux需要可交互桌面和浏览器系统库；下载浏览器不等于系统库齐全，助手不自动提权。
- 不具备可交互浏览器时，可以切换腾讯官网或本地岗位文件。

可选配置变量为`JOB_AGENT_CDP_PYTHON`和`JOB_AGENT_CDP_UPSTREAM`，默认脚本路径`workspace/tools/boss-cdp/scripts/boss_cdp_raw.py`。当前上游文件版本与校验值由`onboarding.py`管理。

## 工具行为与限制

搜索每次只取一页，支持城市；任职类型加入关键词，不构成严格筛选。未知筛选或不支持的分页参数返回错误，不静默忽略。详情按需读取，完整JD缺失不能当作成功。

返回状态区分成功、空结果、需要登录、受阻、超时、解析失败与不可用。原始调用数据保存在`workspace/cdp-calls/`。浏览器调用串行执行，相邻调用至少间隔2秒；不要让两个服务同时操作同一专用浏览器。

页面加载后等待职位描述稳定再抽取，仍有CDP和进程超时。页面或登录机制变化可能导致接入失效；本项目不保证穷举全站或固定几秒内完成。只提供搜索和详情，不提供投递或打招呼。
