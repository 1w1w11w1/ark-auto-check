# ark-auto-check

适用于 [DeepSeek Harness](https://github.com/deepseek-ai)（DSH）的个人技能仓库。

## 技能列表

### auto-router-check — 火山方舟 AUTO 路由模型快查

检测火山方舟 Agent Plan 的 **AUTO**（客户端模型名 `ark-code-latest`）当前实际路由到哪个候选模型。

**背景**：Agent Plan 控制台选择 Auto 后，API 响应的 `model` 字段只回 `"auto"`，官方不透露真实路由目标。
本技能用「1–355 随机数」分布指纹（约 150 次采样）与内置 16 个候选模型的基准指纹包比对，
主信号为分布相似度（cos·e^-JSD + G 检验），辅以推理语言 / 推理长度 / tokenizer 签名交叉验证，
并用 EM + BIC 检测多模型混合路由。判不下去时诚实输出 `indistinguishable`。

**触发方式**（安装启用后，在任意 DSH 会话中）：
> 「auto 现在路由到哪个模型」「跑一下随机数指纹测试」

会话会自动派出子代理执行探针（约 3–10 分钟、150 次短请求），回报结论 / 置信度 / top5 排名。

## 安装

方式一（推荐）：在 DSH 的技能管理（Skills Manager）中添加仓库 `1w1w11w1/ark-auto-check`，启用 `auto-router-check`。

方式二（手动）：把 `skills/auto-router-check/` 整个目录复制到 `~/.dsh/skills/` 下（Windows：`%USERPROFILE%\.dsh\skills\`），重启会话即可。

## 使用前提

- 本机已安装并登录 [arkcli](https://www.volcengine.com/) 且持有火山方舟 **Agent Plan**（探针凭证在运行时从
  `arkcli profile apikey get` 读取）；也可用 `--api-key` / 环境变量 `AUTO_PROBE_API_KEY` 显式传入。
- Python 3.10+（探针仅用标准库）。

## 隐私说明

- 仓库内**不含任何凭证 / API Key / 本机路径 / 用户信息**；凭证仅在运行时所执行的本机上从 arkcli 临时读取，不回传。
- 探针只向模型发送「请从 1 到 355 之间随机选择一个数字」提示词（附带随机 nonce 防缓存），不携带任何会话或用户数据。
- `baselines.json` 为各候选模型公开行为的统计指纹（分布计数与延迟/token 统计），无个人信息。

## 维护

- 判定是「当下时点」结果；官方调整路由后重跑一次即可。
- `baselines.json` 采集于 2026-10-23（16 模型 × 约 400 次主探针 + 辅助探针）。
  若结论异常（所有候选得分都低），可能是指纹库过期，拉取本仓库更新即可。

## License

MIT
