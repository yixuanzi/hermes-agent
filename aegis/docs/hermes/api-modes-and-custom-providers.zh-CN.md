# API Mode 与自定义模型来源

Hermes 通过 `api_mode` 选择模型服务使用的线路协议。不同模型名称并不一定对应同一种 API：同一个模型通过不同 provider 或中转站提供时，可能分别支持 Chat Completions 或 Responses API。

## 支持的 `api_mode`

| `api_mode` | 请求协议 | 典型请求路径/接口 | 适用场景 |
| --- | --- | --- | --- |
| `chat_completions` | OpenAI Chat Completions | `/v1/chat/completions` | OpenAI 兼容服务、OpenRouter、大多数自定义中转站、本地模型服务 |
| `codex_responses` | OpenAI Responses API | `/v1/responses` | OpenAI Codex、OpenAI Responses API、xAI Responses API，以及明确要求 Responses API 的 GPT-5.x 中转模型 |
| `anthropic_messages` | Anthropic Messages API | `/v1/messages` | Anthropic 原生 API、Anthropic 兼容代理、MiniMax/部分 Claude 中转端点 |
| `bedrock_converse` | AWS Bedrock Converse | Bedrock `Converse` 接口 | AWS Bedrock provider |
| `codex_app_server` | Codex App Server | 由本地 Codex App Server 进程处理 | 特定 OpenAI/Codex App Server 配置，不适用于普通 HTTP 自定义中转站 |

## 不同模型来源的推荐配置

| 模型来源或 provider | 常见模型示例 | 推荐 `api_mode` | 是否通常需要手动配置 | 说明 |
| --- | --- | --- | --- | --- |
| OpenAI 官方 Responses API | `gpt-5.x`、o 系列 | `codex_responses` | 否 | `api.openai.com` 会自动识别为 Responses API |
| OpenAI Codex | Codex 模型 | `codex_responses` | 否 | `openai-codex` provider 默认使用 Responses API |
| xAI | Grok 系列 | `codex_responses` | 否 | xAI provider 和 `api.x.ai` 会自动识别 |
| OpenRouter | GPT、Claude、Gemini、DeepSeek 等 | 通常 `chat_completions`；以实际端点能力为准 | 通常否 | GPT-5.x 可能自动升级到 Responses API，但应以 provider 支持情况为准 |
| Anthropic 官方 | Claude 系列 | `anthropic_messages` | 否 | `anthropic` provider 或 `api.anthropic.com` 会自动识别 |
| Anthropic 兼容代理 | Claude、MiniMax 等 | `anthropic_messages` | URL 可识别时否 | URL 以 `/anthropic` 或 `/anthropic/v1` 结尾时会自动识别；非标准路径建议显式配置 |
| AWS Bedrock | Claude、Nova 等 Bedrock 模型 | `bedrock_converse` | 否 | `bedrock` provider 或 Bedrock runtime 地址会自动识别 |
| 普通 OpenAI 兼容自定义服务 | Llama、Qwen、DeepSeek 等 | `chat_completions` | 否 | 未匹配到特殊 provider 或 URL 时的默认模式 |
| 命名自定义 Responses 中转站 | `gpt-5.6-terra` 等 | `codex_responses` | 是 | 第三方域名不会仅凭模型名自动探测，需在 provider 条目中配置 |
| 命名自定义 Anthropic 中转站 | Claude 兼容模型 | `anthropic_messages` | URL 非标准时是 | 建议显式配置，避免请求格式误判 |

## 自动判断规则

Hermes 有自动判断逻辑，但它是基于配置、provider、URL 和模型名的静态判断，不会先发送探测请求，也不会在收到 HTTP 400 后自动把 `/chat/completions` 重试为 `/responses`。

主要规则如下：

1. 显式传入的 `api_mode` 优先级最高。
2. 已知 provider 使用 provider 自己声明的协议，例如 `openai-codex` 使用 `codex_responses`、`anthropic` 使用 `anthropic_messages`。
3. 特殊 URL 会触发识别：
   - `api.openai.com`、`api.x.ai` → `codex_responses`
   - `api.anthropic.com` → `anthropic_messages`
   - URL 以 `/anthropic` 或 `/anthropic/v1` 结尾 → `anthropic_messages`
   - Kimi Coding 特定地址 → `anthropic_messages`
   - Bedrock provider/地址 → `bedrock_converse`
4. 普通自定义 provider 保守地默认使用 `chat_completions`。
5. GPT-5.x 在部分已知 provider 上会自动升级到 `codex_responses`；但 `custom` provider 不会仅凭模型名称升级，以免误伤只支持 Chat Completions 的中转服务。

因此，第三方服务明确要求 `/v1/responses` 时，应显式写入：

```yaml
api_mode: codex_responses
```

## 配置多个 `custom_providers`

每个自定义来源使用唯一的 `name`，并独立配置自己的密钥、默认模型和 API 协议：

```yaml
custom_providers:
  - name: terra
    base_url: https://chatai-api.amberainsider.com/v1
    key_env: CHATAI_API_KEY
    model: gpt-5.6-terra
    api_mode: codex_responses

  - name: deepseek
    base_url: https://api.deepseek.com/v1
    key_env: DEEPSEEK_API_KEY
    model: deepseek-chat
    api_mode: chat_completions

  - name: local
    base_url: http://127.0.0.1:8080/v1
    model: qwen3
    api_mode: chat_completions

model:
  provider: custom:terra
  default: gpt-5.6-terra
```

密钥建议放在 `~/.hermes/.env`：

```dotenv
CHATAI_API_KEY=your-chatai-key
DEEPSEEK_API_KEY=your-deepseek-key
```

## 运行时切换

在 CLI 或支持 slash command 的会话中，可以用以下格式切换：

```text
/model custom:terra:gpt-5.6-terra
/model custom:deepseek:deepseek-chat
/model custom:local:qwen3
```

也可以执行 `/model`，从交互式模型选择器中选择 provider 和模型。切换时会重新解析对应的 `base_url`、凭据和 `api_mode`；一个 custom provider 的协议配置不会影响其他 custom provider。

