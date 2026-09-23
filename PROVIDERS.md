# 模型接入配置

模型调用依次经过 `APIRuntime → ModelProvider → RequestTransform → HTTP/SSE`。
`api/factory.py` 按协议选择 Provider，`api/transform.py` 根据服务商默认值和模型配置生成请求参数。
Agent、会话命名和模型列表共享 Runtime 管理的 Provider 和用量统计。

## 协议与服务商

在已有的 `[API_MANAGER]` 中加入：

```toml
PROTOCOL = "openai_compatible"
PROVIDER = "deepseek"
```

目前实现的协议是 `openai_compatible`，使用 Chat Completions 和 Models 接口。
其他协议需要在 `api/factory.py` 的 `PROTOCOL_FACTORIES` 中注册独立实现；仅修改配置不会自动支持其他协议。

`PROVIDER` 决定参数默认值，与 `BASE_URL` 分开设置，因此支持自定义代理地址。

| PROVIDER | 默认思考参数 |
| --- | --- |
| `openai_compatible` | 不添加思考开关 |
| `deepseek` | `thinking = { type = "enabled" / "disabled" }` |
| `siliconflow` | `enable_thinking = true / false` |
| 自定义名称 | 使用通用默认值，可通过模型配置指定参数 |
| `auto` 或省略 | 根据 URL 的主机名推断上述内置服务商，其他地址使用通用默认值 |

旧配置无需修改。代理地址无法可靠推断上游服务商，应显式设置 `PROVIDER`。
显式设置 `openai_compatible` 会关闭服务商自动推断。

`API_MANAGER.TEMPERATURE` 是可选的非负有限数值；删除或注释后，普通对话、流式请求和会话命名均不发送 `temperature`。旧拼写 `TEMPREATURE` 仍可用，两者同时配置时以 `TEMPERATURE` 为准。

## 默认配置和模型覆盖

以下是完整的配置结构示例，密钥和远端模型 ID 需要替换：

```toml
[API_MANAGER]
BASE_URL = "https://proxy.example/v1"
API_KEY = "your-api-key"
MODEL = "reasoner"
PROTOCOL = "openai_compatible"
PROVIDER = "deepseek"
STREAM = true
# TEMPERATURE = 0.2 # 可选；省略则不发送 temperature

[API_MANAGER.DEFAULTS.EXTRA_BODY]
max_tokens = 4096

[API_MANAGER.MODELS.reasoner]
API_MODEL = "remote-reasoning-model"
THINKING_FORMAT = "thinking"

[API_MANAGER.MODELS.reasoner.CAPABILITIES]
TEMPERATURE = false

[API_MANAGER.MODELS.basic]
API_MODEL = "remote-chat-model"
THINKING_FORMAT = "none"

[API_MANAGER.MODELS.basic.CAPABILITIES]
REASONING = false
TOOLS = false

[API_MANAGER.MODELS.basic.EXTRA_BODY]
max_tokens = 1024

[REASONING]
ENABLED = true
THINKING = true
EFFORT = "high"
```

`MODELS` 的键对应配置中的 `MODEL` 或 `/model` 命令选中的名称。
`API_MODEL` 是实际发送给服务器的模型 ID，省略时直接使用所选名称。
包含点号或斜杠的模型键可写成 `[API_MANAGER.MODELS."org/model.v1"]`。
`/model basic` 会在下一次请求时使用 basic 的能力和参数；未配置的模型继承服务商及 DEFAULTS。
`/model list` 仍返回服务端的模型列表，不会自动加入本地别名。

参数优先级为：服务商默认值 → `DEFAULTS` → 当前模型。
`CAPABILITIES` 按字段继承，`EXTRA_BODY` 的嵌套表递归合并，其他值由模型配置覆盖。

| CAPABILITIES 字段 | 设置为 false 的效果 |
| --- | --- |
| `TOOLS` | 不发送工具定义和 `tool_choice` |
| `REASONING` | 不发送适配层管理的思考开关和推理强度 |
| `REASONING_EFFORT` | 不发送 `reasoning_effort`，保留思考开关 |
| `TEMPERATURE` | 不发送 `temperature` |
| `STREAM_USAGE` | 不发送 `stream_options.include_usage` |

能力字段省略时继承上层，通用默认值为 true，以保持现有行为；这些配置不会自动探测服务端能力。
关闭 `STREAM_USAGE` 后仍会统计服务器实际返回的 usage，但不保证服务器会返回它。

如果上游报错不支持 `stream_options.include_usage`，可在配置中关闭：

```toml
[API_MANAGER.DEFAULTS.CAPABILITIES]
STREAM_USAGE = false
```

这会完全省略 `stream_options`，不影响流式输出。`config.toml` 和 `templates/config.toml.bak` 已显式关闭此选项；支持该参数的上游可改为 `true`。省略此配置时默认启用，单个模型仍可通过自己的 `CAPABILITIES.STREAM_USAGE` 覆盖。


`THINKING_FORMAT` 支持 `none`、`thinking` 和 `enable_thinking`。
`none` 表示不发送思考开关，不代表强制关闭服务端思考，也不禁止推理强度参数。
开关值由现有 `REASONING.THINKING` 和 Agent 的运行设置决定。

`EXTRA_BODY` 用于其他兼容 API 扩展参数，值必须可编码为 JSON。
它不能覆盖 `model`、`messages`、`stream`、工具参数、`temperature`、`stream_options`
以及适配层管理的思考和推理强度参数；这些字段应通过对应配置控制。
自定义扩展参数的含义和兼容性由所连接的服务决定。
