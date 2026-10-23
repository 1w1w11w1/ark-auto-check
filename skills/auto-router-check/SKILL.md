---
name: auto-router-check
description: 检测火山方舟 Agent Plan 的 AUTO（客户端模型 ark-code-latest）当前实际路由到哪个候选模型。当用户问「auto 现在路由到哪个模型 / AUTO 是谁 / 查一下路由 / 跑一下随机数指纹测试」等意图时使用。派出子代理执行随附的 quick_probe.py 随机数指纹探针（约 150 次 API 调用），与内置 16 模型基准指纹包比对后给出结论。
---

# auto-router-check：AUTO 路由模型快查

## 何时使用
用户想确认火山方舟 Agent Plan 的 AUTO（客户端模型名 ark-code-latest）**当前**实际路由到哪个具体模型时。
路由可能随官方策略漂移，本 skill 给的是「当下这一时点」的判定。


## 范围（Scope）：只识别火山计划内模型

**本 skill 只用于识别火山方舟 Agent Plan（火山计划）中存在的模型**：
把 AUTO 入口 `ark-code-latest` 的随机数分布指纹，与内置 16 个计划候选模型基准比对，
回答「这次 AUTO 被路由到了计划里的哪个模型」。

- ✅ 可回答：AUTO 当前路由到哪个计划内模型；计划内某模型与 AUTO 像不像（`--target` 抽查）；
  指纹库覆盖计划内哪些模型。
- ❌ 不可用于：识别计划外模型（其它厂商 / Coding Plan / 按量平台 / 第三方中转站 / 自建服务）、
  判断某个中转站是否偷换模型、来源鉴定与维权、能力测评或榜单。

`quick_probe.py` 已内置该断言：`--target` 只接受 `ark-code-latest` 或 16 个计划内模型；
baselines.json 若含计划外模型会直接拒绝执行（exit 3，stderr 说明范围）。
遇到越界请求时：**先说明范围限制，再拒绝越界部分**，不要改装探针去测计划外模型。

## 原理（一句话）
AUTO 的响应不透露真实模型（响应体 model 字段只回 "auto"），因此对 ark-code-latest 做约 150 次
「1–355 随机数」采样，把分布指纹与本目录 baselines.json 的 16 个候选模型基准指纹比对
（主信号：分布相似度 cos·e^-JSD + G 检验；辅助：推理语言/推理长度/tokenizer 签名；混合检测：EM 权重 + BIC）。

## 资源文件（与本 SKILL.md 同目录，即 skill 的 resourceBase 目录）
- quick_probe.py —— 自包含探针脚本（仅 Python 标准库，3.10+）
- baselines.json —— 16 个候选模型的基准指纹包（2026-10-23 采集，每模型约 400 次主探针样本）

## 执行步骤（必须派出子代理，不要在本会话直接跑，避免阻塞与上下文污染）
1. 先确定本 skill 的目录（skill 工具返回的 resourceBase.path；通常为 ~/.dsh/skills/auto-router-check）。
2. 用 subagent 工具派出子代理，任务提示词模板如下（把 <SKILL_DIR> 替换为实际目录）：

```
运行 AUTO 路由指纹探针并汇报结果。步骤：
1. 执行命令（Windows 用 pwsh）：
   python "<SKILL_DIR>\quick_probe.py" --n 150 --concurrency 6 --json
   该命令约需 3–10 分钟（约 150 次真实 API 调用，走本机 arkcli 的 Agent Plan 凭证）。
   用 run_in_background 或足够大的超时等待完成；期间每 2 分钟可查看一次输出。
2. 若报凭证错误（401/key format/未找到凭证）：提示用户需要 arkcli 已登录 Agent Plan
   （arkcli auth status 应显示 agent-plan profile）；或让用户提供 key 后改用
   --base-url <url> --api-key <key> 参数重跑。
3. 命令成功后 stdout 最后一行是 JSON verdict，解析并把以下字段汇报回来：
   conclusion、headline、confidence、ranking 前 5 名（model_key/combined/rng_score/rng_p）、
   mixture 权重、n、n_fail、elapsed_s、warnings。
4. 原样转述结论，不要自行改写判定逻辑；若 conclusion 是 indistinguishable 或
   mixture，如实说明（这本身就是有效结论）。
```

3. 收到子代理结果后，向用户汇报：结论（路由到谁/混合/不可区分）、top3 得分、样本量、耗时；
   并附两条说明：
   - 这是判定时点的结果；官方调整路由或更新 doubao-seed-evolving 后需重跑。
   - 基准指纹采集于 2026-10-23；若距今较久或结论异常（如全部低分），建议更新指纹库：
     从 skill 仓库拉取最新 baselines.json 覆盖本目录同名文件即可（维护者会定期重建）。

## 注意
- quick_probe.py 凭证默认从 arkcli 读取（arkcli profile apikey get --format json），不会打印完整 key。
- 探针只发「选数字」提示词，不含任何用户数据；成本约 150 次短请求。
- 用户只想「快速瞄一眼」时可把 --n 降到 60（--min-n 60），置信度会下降，汇报时必须注明。
- 目标可换：--target 可指定任何模型名，用于抽查某个直连模型是否与指纹库一致（验证指纹库未过期）。