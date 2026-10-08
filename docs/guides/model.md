# 模型配置与预算

本项目通过 OpenAI 兼容 API 调用模型，不要求 OpenAI 账号。离线 `demo` 模式无需密钥；开放自然语言需求和云模型评测需要配置。

## 界面配置

打开「使用设置与连接」→「模型与数据」，选择Qwen或DeepSeek，输入该服务的API Key并保存。密钥存于 `workspace/secrets/ui-model-api-key`，输入框清空；已有配置无需重复填写。设置中的“已配置”只确认本地能读取密钥，不保证服务余额、权限或网络。

模型账户与服务地址必须匹配。额度或权限被拒绝时，检查密钥所属服务的控制台；这不是岗位无结果。

## 环境配置

在项目根目录复制 `.env.example` 为 `.env`，修改以下变量：

|变量|作用|
|---|---|
|`JOB_AGENT_MODEL`|服务实际支持的模型标识|
|`JOB_AGENT_BASE_URL`|兼容API的地址|
|`JOB_AGENT_API_KEY_ENV`|存放密钥的环境变量名|
|`JOB_AGENT_API_KEY_FILE`|可选的私有密钥文件路径|
|`JOB_AGENT_INPUT_CNY_PER_MILLION`、`JOB_AGENT_OUTPUT_CNY_PER_MILLION`|用于本地估算的输入/输出费率，单位元/百万token|
|`JOB_AGENT_BUDGET_CNY`|活动账本的本地估算上限|
|`JOB_AGENT_BUDGET_DB`|可选，自定义账本路径|

密钥环境变量优先于指定文件。未指定文件且使用DeepSeek官方地址时，保留读取 `workspace/secrets/deepseek-api-key` 的兼容路径；其他服务不会自动使用该密钥。CLI只读取当前项目 `.env`，进程环境优先。

```bash
# 检查配置是否可读取；不调用模型，也不验证登录。
uv run job-agent doctor

uv run job-agent --db workspace/cloud.sqlite serve --mode cloud --source replay
```

示例模型与费率是项目曾使用过的配置，服务可用标识和当前价格需在对应服务确认。改变服务时应同时修改地址、模型、密钥变量与估算费率。

## 费用与失败

每次模型调用先申请保守预算，成功后按返回用量结算。SDK不进行隐式重试；仅少数结构输出错误有明确的有限纠正。明确被服务拒绝的请求释放预留，无法确认是否计费的网络失败保留预留。

Qwen默认账本为 `workspace/qwen-budget.sqlite`，其他默认账本为 `workspace/budget.sqlite`；显式指定账本优先。Qwen账本会按收费ID导入旧默认账本中的Qwen记录，防止重复计费，避免其他服务费用误占求职模型预算。当前Qwen3.7-Flash实现包含长输入倍率估算，见 `CloudReasoner.rates`。

本地账本是估算，不等于官方实扣；缓存、服务定价变化及不确定失败都可能造成差异。不要仅凭离线演示的零费用估计云模型任务费用。运行中可以在工作区查看任务状态和执行记录。
