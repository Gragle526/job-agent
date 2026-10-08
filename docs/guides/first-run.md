# 首次使用

本项目在本机运行。找岗需要可用云模型；实时BOSS还需兼容桌面浏览器和本人登录。无需浏览器插件。

## 1. 安装和打开

准备Python 3.12+及[uv](https://docs.astral.sh/uv/getting-started/installation/)，在项目根目录运行：

```bash
uv sync --locked
uv run job-agent serve
```

打开终端显示的本机地址，默认 `http://127.0.0.1:7860`。未配置模型仍能打开界面，但不能开始找岗。本仓库不附带岗位数据或规则演示。

## 2. 配置模型

左侧「使用设置与连接」选择Qwen或DeepSeek，在密码框填写该服务API Key并保存。保存后密钥框清空，写入本机私有文件；已有配置无需重复填写。模型调用会将需求和相关岗位信息发送给配置的服务，可能产生费用。

也可在本地`.env`配置，见[模型配置](model.md)。配置可读取不等于余额、权限或网络可用。

## 3. 选择真实数据来源

- 实时BOSS：模型、采集组件、Chrome/Edge/Chromium及本人登录。
- 腾讯官网：模型和网络；无需浏览器或登录，只提供腾讯公开职位。
- 本地快照：模型及自己的标准岗位JSON，文件不超过10MB。

快照支持本项目导出的JSON；不是任意文档或简历解析功能。导入格式见下方。切换数据来源时旧证据保留，但不会当成新来源刚检索的结果。

## 4. BOSS浏览器准备与登录

在「浏览器与组件」查看检测状态，有Chrome/Edge时点击「准备采集组件」；没有兼容浏览器可点击「下载组件与独立Chromium」。只有点击才下载，固定版本源码与许可证校验通过后才使用。

打开助手专用浏览器，本人扫码和处理页面验证，再点击登录检查。发送消息时也会检查未确认的已有连接。重启丢失内存检查标记不会直接要求重复扫码；检查确实失败后再处理连接或登录。

Firefox/Safari可查看界面，但BOSS接入需要Chromium系桌面浏览器。Linux需要可交互桌面及浏览器系统库；WSL推荐在Windows原生运行，或在高级设置指定安装了requests/websocket-client的Windows Python。助手不自动提权、复制普通浏览器Cookie或关闭普通浏览器。细节见[BOSS接入](boss-cdp.md)。

## 5. 使用会话

左侧管理会话，中间输入方向、地点及条件，右侧比较候选并查看完整JD。反馈可以新增或改变条件，也可以追问已发布清单中的岗位。

“条件已核验”只说明当前检查结果，不保证所有资格或录用。缺失信息保持未知。暂停在当前请求完成后生效，不能撤销已发出请求；可从完成的步骤恢复。执行中的任务结束前不能修改全局配置。

## 自己的岗位文件

JSON最小格式如下，这是字段说明，不是提供的岗位样例：

```text
数组中的每条记录包含：source、source_id、url、title、company、description。
city、salary、job_type为可选字符串；provenance使用imported。
url为真实原始链接；description为未经改写的完整JD。
```

```bash
uv run job-agent --db workspace/imported.sqlite import /path/to/jobs.json
uv run job-agent --db workspace/imported.sqlite serve --source snapshot --jobs /path/to/jobs.json
uv run job-agent --db workspace/imported.sqlite export workspace/my-jobs.json
```

数据库、岗位文件和密钥默认留在被Git忽略的`workspace/`。
