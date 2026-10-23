# ark-auto-check

DeepSeek Harness（DSH）插件：查询火山方舟 **Agent Plan** 的 **AUTO** 当前实际路由到哪个具体模型。

## 它做什么

在 Agent Plan 里选择 Auto 后，API 响应的 `model` 字段只回 `"auto"`，官方不透露真实路由目标。
本插件装好后提供一个技能 `auto-router-check`：对 AUTO 入口（`ark-code-latest`）做约 150 次
「1–355 随机数」采样，用分布指纹与内置的 16 个候选模型基准指纹比对（主信号 cos·e^-JSD + G 检验，
辅助推理语言 / 推理长度 / tokenizer 签名，EM + BIC 检测混合路由），给出「路由到哪个模型」的结论与置信度。

在任意 DSH 会话中问「auto 现在路由到哪个模型」，会话就会派出子代理执行探针（约 3–10 分钟、150 次短请求），
回报结论 / 置信度 / top5 排名。

## 安装

```sh
dsh plugin add 1w1w11w1/ark-auto-check
```

或在 DSH 的插件市场 / Skills 界面按仓库地址安装。插件以 `dsh.bundle` 形式注册技能，装完即生效（重启会话）。
手动方式：把 `skills/auto-router-check/` 复制到 `~/.dsh/skills/` 同样可用（Windows：`%USERPROFILE%\.dsh\skills\`）。

## 使用前提

- 本机已安装并登录 [arkcli](https://www.volcengine.com/) 且持有火山方舟 **Agent Plan**——探针在运行时从
  `arkcli profile apikey get` 读取凭证；也可用 `--api-key` 或环境变量 `AUTO_PROBE_API_KEY` 显式传入。
- Python 3.10+（探针仅用标准库）。

## 隐私说明

- 仓库内**不含任何凭证 / API Key / 本机路径 / 用户信息**；凭证只在运行该技能的本机上从 arkcli 临时读取，不回传。
- 探针只向模型发送「请从 1 到 355 之间随机选择一个数字」提示词（附随机 nonce 防缓存），不携带任何会话或用户数据。
- `baselines.json` 为各候选模型公开行为的统计指纹（分布计数与延迟 / token 统计），无个人信息。

## 维护

- 判定是「当下时点」结果；官方调整路由后重跑一次即可。
- `baselines.json` 采集于 2026-10-23（16 模型 × 约 400 次主探针 + 辅助探针）。若结论异常（所有候选得分都低），
  可能是指纹库过期，拉取本仓库更新即可。

## License

MIT
