#!/usr/bin/env python3
"""quick_probe.py — AUTO 路由模型快查探针（自包含，仅 Python 标准库）。

对目标模型（默认 ark-code-latest，即火山方舟 Agent Plan 的 AUTO 客户端入口）做
N 次「1–355 随机数」采样，与 baselines.json 中的候选模型基准指纹比对，输出判定 JSON。

用法：
  python quick_probe.py [--n 150] [--min-n 120] [--concurrency 6]
                        [--base-url URL] [--api-key KEY] [--target MODEL]
                        [--baselines baselines.json] [--json]
凭证优先级：--api-key > 环境变量 AUTO_PROBE_API_KEY > arkcli（profile apikey get）。
"""
import argparse, json, math, os, random, re, statistics, subprocess, sys, threading, time
import urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/plan/v3"
CHAT_PATH = "/chat/completions"
PROMPT = "请从1到355之间随机选择一个数字，只输出这个数字，不要有任何其他内容。"
DIST_LEN, RNG_MIN, RNG_MAX = 355, 1, 355
ALPHA = 0.5
AUX_W = {"reasoning_lang": 0.35, "reasoning_tokens": 0.25, "prompt_tokens": 0.25, "latency": 0.15}
SINGLE_W = 0.85
MARGIN = 2.0  # combined 分差小于该值且互为近邻 → 不可区分

# ---------------------------------------------------------------- 范围（Scope）
# 本项目只用于识别火山方舟 Agent Plan（火山计划）中存在的模型。越界请求一律拒绝执行。
ARK_CODE_LATEST = "ark-code-latest"
PLAN_MODELS = (
    "doubao-seed-evolving", "doubao-seed-2-1-pro-260915", "doubao-seed-2-1-lite-260915",
    "doubao-seed-2-1-turbo-260628", "doubao-seed-2-0-lite-260215", "doubao-seed-2-0-mini-260428",
    "kimi-k2-8-preview", "deepseek-v4-1-flash-261017", "deepseek-v4-1-flash-economy-261017",
    "glm-5-3-flash-260828", "glm-5.3", "deepseek-v4-pro-ga-260813",
    "deepseek-v4-flash-ga-260731", "kimi-k3", "kimi-k2.7-code", "minimax-m3",
)
SCOPE_NOTICE = ("范围：本工具只用于识别火山方舟 Agent Plan（火山计划）内存在的模型；"
                "不识别计划外模型、其它平台，也不用于来源鉴定或能力测评。")


def check_scope(target, models):
    """返回拒绝理由；在范围内则返回空串。"""
    allowed = set(PLAN_MODELS)
    extra = [m for m in models if m not in allowed]
    if extra:
        return "拒绝执行：baselines.json 含火山计划之外的模型（%s）。%s" % ("、".join(extra), SCOPE_NOTICE)
    if target not in allowed and target != ARK_CODE_LATEST:
        return ("拒绝执行：target=%s 不在范围内，只接受 %s 或计划内 16 个模型之一。%s"
                % (target, ARK_CODE_LATEST, SCOPE_NOTICE))
    return ""


# ---------------------------------------------------------------- 凭证
def _resolve_cmd(cmd):
    import shutil
    exe = shutil.which(cmd[0])
    if not exe:
        return None
    cmd = [exe] + list(cmd[1:])
    if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
        return ["cmd.exe", "/c"] + cmd
    return cmd

def _arkcli_key():
    env = dict(os.environ)
    env.update({"ARKCLI_NO_UPDATE_NOTIFIER": "1", "ARKCLI_CALLER_TYPE": "ai_agent",
                "ARKCLI_CALLER_NAME": "auto-router-check", "ARKCLI_SKILL_NAME": "auto-router-check"})
    cmd = _resolve_cmd(["arkcli", "profile", "apikey", "get", "--format", "json"])
    if not cmd:
        return None
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
        if out.returncode == 0:
            return json.loads(out.stdout).get("api_key")
    except Exception:
        return None
    return None

def resolve_credential(base_url, api_key):
    key = api_key or os.environ.get("AUTO_PROBE_API_KEY") or _arkcli_key()
    if not key:
        raise SystemExit("未找到凭证：请登录 arkcli（Agent Plan profile），或传 --api-key / 设 AUTO_PROBE_API_KEY")
    return (base_url or DEFAULT_BASE_URL).rstrip("/"), key

# ---------------------------------------------------------------- 采样
def make_nonce(rng):
    return "".join(rng.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(16))

_NUM_RE = re.compile(r"-?\d+")
def parse_number(text):
    if not text:
        return None
    m = _NUM_RE.search(text.strip())
    if not m:
        return None
    v = int(m.group(0))
    return v if RNG_MIN <= v <= RNG_MAX else None

def one_call(base_url, key, target, nonce, timeout=120, retries=3):
    body = json.dumps({
        "model": target,
        "messages": [{"role": "user", "content": PROMPT + "\n[req-nonce: " + nonce + "]"}],
        "temperature": 1.0, "max_tokens": 512,
    }).encode("utf-8")
    url = base_url + CHAT_PATH
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    delay = 1.5
    err, status = "unknown", 0
    lat = 0
    for attempt in range(1, retries + 1):
        t0 = time.time()
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            lat = int((time.time() - t0) * 1000)
            ch = (payload.get("choices") or [{}])[0]
            msg = ch.get("message") or {}
            usage = payload.get("usage") or {}
            det = usage.get("completion_tokens_details") or {}
            content = msg.get("content") or ""
            return {"ok": True, "error": None, "number": parse_number(content),
                    "content": content, "reasoning": msg.get("reasoning_content") or "",
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "reasoning_tokens": det.get("reasoning_tokens"),
                    "latency_ms": lat, "http_status": 200}
        except urllib.error.HTTPError as e:
            lat = int((time.time() - t0) * 1000)
            status = e.code
            err = "HTTP %d: %s" % (e.code, e.read().decode("utf-8", "ignore")[:200])
            if 400 <= e.code < 500 and e.code != 429:
                break
        except Exception as e:  # 超时/网络
            lat = int((time.time() - t0) * 1000)
            status = 0
            err = "%s: %s" % (type(e).__name__, e)
        if attempt < retries:
            time.sleep(delay * (2 ** (attempt - 1)) + random.random())
    return {"ok": False, "error": err, "number": None, "content": "", "reasoning": "",
            "prompt_tokens": None, "reasoning_tokens": None, "latency_ms": lat,
            "http_status": status}

# ---------------------------------------------------------------- 指纹
def _lang_bucket(text):
    if not text:
        return "none"
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    ratio = cjk / max(1, len(text))
    return "zh" if ratio > 0.30 else ("en" if ratio < 0.05 else "mixed")

def _pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs); k = (len(xs) - 1) * q
    f = int(k); c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)

def build_fp(samples):
    oks = [s for s in samples if s["ok"]]
    nums = [s["number"] for s in oks if s["number"] is not None]
    counts = [0] * DIST_LEN
    for v in nums:
        counts[v - RNG_MIN] += 1
    langs = {"zh": 0, "en": 0, "mixed": 0, "none": 0}
    for s in oks:
        langs[_lang_bucket(s["reasoning"])] += 1
    ln = max(1, len(oks))
    rts = [s["reasoning_tokens"] for s in oks if s["reasoning_tokens"] is not None]
    pts = [s["prompt_tokens"] for s in oks if s["prompt_tokens"] is not None]
    lats = [float(s["latency_ms"]) for s in oks if s["latency_ms"] > 0]
    freq = {}
    for p in pts:
        freq[p] = freq.get(p, 0) + 1
    return {"n": len(nums), "counts": counts, "aux": {
        "reasoning_lang": {k: v / ln for k, v in langs.items()},
        "reasoning_tokens": {"mean": statistics.fmean(rts) if rts else None,
                             "std": statistics.pstdev(rts) if len(rts) > 1 else 0.0,
                             "median": statistics.median(rts) if rts else None},
        "prompt_tokens": {"mode": max(freq.items(), key=lambda kv: kv[1])[0] if freq else None,
                          "mean": statistics.fmean(pts) if pts else None},
        "latency_ms": {"median": _pct(lats, 0.5) if lats else None},
    }}

# ---------------------------------------------------------------- 统计
def smoothed(counts):
    tot = sum(counts) + ALPHA * len(counts)
    return [(c + ALPHA) / tot for c in counts]

def cosine(p, q):
    num = sum(a * b for a, b in zip(p, q))
    da = math.sqrt(sum(a * a for a in p)); db = math.sqrt(sum(b * b for b in q))
    return num / (da * db) if da and db else 0.0

def jsd(p, q):
    m = [(a + b) / 2 for a, b in zip(p, q)]
    def kl(a, b):
        return sum(x * math.log2(x / y) for x, y in zip(a, b) if x > 0 and y > 0)
    return (kl(p, m) + kl(q, m)) / 2

def similarity(ca, cb):
    p, q = smoothed(ca), smoothed(cb)
    return cosine(p, q) * math.exp(-jsd(p, q))

# 不完全伽马（Numerical Recipes）→ 卡方尾概率
def _gammq(a, x):
    if x < 0 or a <= 0:
        return float("nan")
    if x == 0:
        return 1.0
    if x < a + 1.0:
        ap, s, d = a, 1.0 / a, 1.0 / a
        for _ in range(1000):
            ap += 1; d *= x / ap; s += d
            if abs(d) < abs(s) * 1e-14:
                break
        return max(0.0, min(1.0, 1.0 - s * math.exp(-x + a * math.log(x) - math.lgamma(a))))
    b, c, d = x + 1 - a, 1e30, 1.0 / (x + 1 - a)
    h = d
    for i in range(1, 1000):
        an = -i * (i - a); b += 2
        d = an * d + b
        if abs(d) < 1e-30:
            d = 1e-30
        c = b + an / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d; delta = d * c; h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return max(0.0, min(1.0, math.exp(-x + a * math.log(x) - math.lgamma(a)) * h))

def chi2_sf(x, df):
    return _gammq(df / 2.0, x / 2.0) if df > 0 else float("nan")

def g_test(ca, cb, min_expected=5):
    tot_a, tot_b = sum(ca), sum(cb)
    if tot_a == 0 or tot_b == 0:
        return 0.0, float("nan")
    bins_a, bins_b, cur_a, cur_b = [], [], 0, 0
    for a, b in zip(ca, cb):
        cur_a += a; cur_b += b
        if cur_a + cur_b >= 2 * min_expected:
            bins_a.append(cur_a); bins_b.append(cur_b); cur_a = cur_b = 0
    if cur_a + cur_b:
        if bins_a:
            bins_a[-1] += cur_a; bins_b[-1] += cur_b
        else:
            bins_a, bins_b = [cur_a], [cur_b]
    G = 0.0
    denom = tot_a + tot_b
    for a, b in zip(bins_a, bins_b):
        tot = a + b
        ea, eb = tot * tot_a / denom, tot * tot_b / denom
        if a > 0 and ea > 0:
            G += 2 * a * math.log(a / ea)
        if b > 0 and eb > 0:
            G += 2 * b * math.log(b / eb)
    df = max(1, len(bins_a) - 1)
    return G, chi2_sf(G, df)

def _em_weights(target_p, comps_p, iters=500):
    k = len(comps_p)
    w = [1.0 / k] * k
    for _ in range(iters):
        new_w = [0.0] * k
        for i, t in enumerate(target_p):
            if t <= 0:
                continue
            s = sum(w[j] * comps_p[j][i] for j in range(k))
            if s > 0:
                for j in range(k):
                    new_w[j] += t * w[j] * comps_p[j][i] / s
        if max(abs(a - b) for a, b in zip(new_w, w)) < 1e-9:
            w = new_w
            break
        w = new_w
    return w

def em_mixture(target_counts, comp_counts, names, max_k=3, rounds=300, seed=20261023):
    order = sorted(range(len(comp_counts)),
                   key=lambda j: similarity(target_counts, comp_counts[j]), reverse=True)
    target_p = smoothed(target_counts)
    comps_p = [smoothed(comp_counts[j]) for j in order]
    best = None
    for k in range(1, min(max_k, len(comps_p)) + 1):
        w = _em_weights(target_p, comps_p[:k])
        mix = [sum(w[j] * comps_p[j][i] for j in range(k)) for i in range(len(target_p))]
        ll = sum(c * math.log(max(mix[i], 1e-300)) for i, c in enumerate(target_counts))
        bic = (k - 1) * math.log(max(2, sum(target_counts))) - 2 * ll
        if best is None or bic < best[1]:
            best = (k, bic, w)
    k, _, w = best
    rng = random.Random(seed)
    tot = sum(target_counts)
    cdf = []
    acc = 0.0
    for c in target_counts:
        acc += c / tot
        cdf.append(acc)
    boots = [[] for _ in range(k)]
    for _ in range(rounds):
        res = [0] * len(target_counts)
        for _ in range(tot):
            x = rng.random()
            lo, hi = 0, len(cdf) - 1
            while lo < hi:
                mid = (lo + hi) // 2
                if cdf[mid] < x:
                    lo = mid + 1
                else:
                    hi = mid
            res[lo] += 1
        rp = smoothed(res)
        bw = _em_weights(rp, comps_p[:k], iters=300)
        for j in range(k):
            boots[j].append(bw[j])
    out = []
    for j in range(k):
        bj = sorted(boots[j])
        out.append({"model_key": names[order[j]], "weight": round(w[j], 4),
                    "ci_low": round(bj[int(0.025 * rounds)], 4),
                    "ci_high": round(bj[min(int(0.975 * rounds), rounds - 1)], 4)})
    out.sort(key=lambda d: -d["weight"])
    return out

# ---------------------------------------------------------------- 辅助打分
def _lang_sim(a, b):
    if not a or not b:
        return None
    keys = ("zh", "en", "mixed", "none")
    return sum(a.get(k, 0) * b.get(k, 0) for k in keys)

def _rt_sim(a, b):
    ma, mb = (a or {}).get("mean"), (b or {}).get("mean")
    if ma is None or mb is None:
        return None
    sa, sb = (a or {}).get("std") or 0.0, (b or {}).get("std") or 0.0
    scale = max(sa, sb, 30.0)
    return math.exp(-abs(ma - mb) / scale)

def _pt_sim(a, b):
    ma, mb = (a or {}).get("mode"), (b or {}).get("mode")
    if ma is None or mb is None:
        return None
    if ma == mb:
        return 1.0
    return math.exp(-abs(ma - mb) / max(ma, mb, 1))

def _lat_sim(a, b):
    ma, mb = (a or {}).get("median"), (b or {}).get("median")
    if not ma or not mb:
        return None
    return math.exp(-abs(math.log(ma / mb)))

def aux_score(t_aux, c_aux):
    parts = {"reasoning_lang": _lang_sim(t_aux.get("reasoning_lang"), c_aux.get("reasoning_lang")),
             "reasoning_tokens": _rt_sim(t_aux.get("reasoning_tokens"), c_aux.get("reasoning_tokens")),
             "prompt_tokens": _pt_sim(t_aux.get("prompt_tokens"), c_aux.get("prompt_tokens")),
             "latency": _lat_sim(t_aux.get("latency_ms"), c_aux.get("latency_ms"))}
    tot_w = sum(AUX_W[k] for k, v in parts.items() if v is not None)
    score = sum(AUX_W[k] * v for k, v in parts.items() if v is not None) / tot_w if tot_w else 0.0
    return 100 * score, parts

# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--min-n", type=int, default=120)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--target", default="ark-code-latest",
                    help="只能是 ark-code-latest（AUTO 入口）或计划内模型（自检抽查）")
    ap.add_argument("--baselines", default=os.path.join(HERE, "baselines.json"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    pack = json.load(open(args.baselines, encoding="utf-8"))
    models = pack["models"]
    refusal = check_scope(args.target, models)
    if refusal:  # 越界先拒绝，连凭证都不去取
        print(refusal, file=sys.stderr)
        return 3
    base_url, key = resolve_credential(args.base_url, args.api_key)
    if not args.json:
        print("target=%s n=%d candidates=%d baselines=%s" %
              (args.target, args.n, len(models), pack.get("captured_at_iso", "?")), flush=True)

    t0 = time.time()
    rng = random.Random()
    samples = []
    lock = threading.Lock()
    done = [0]
    def work(i):
        s = one_call(base_url, key, args.target, make_nonce(rng))
        with lock:
            samples.append(s)
            done[0] += 1
            if not args.json and done[0] % 20 == 0:
                print("  %d/%d" % (done[0], args.n), flush=True)
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        list(ex.map(work, range(args.n)))
    elapsed = round(time.time() - t0, 1)

    t_fp = build_fp(samples)
    n_ok = sum(1 for s in samples if s["ok"])
    n_fail = len(samples) - n_ok
    ranking = []
    for name, m in models.items():
        rng_score = 100 * similarity(t_fp["counts"], m["counts"])
        _, p_val = g_test(t_fp["counts"], m["counts"])
        a_score, parts = aux_score(t_fp["aux"], m["aux"])
        combined = 0.7 * rng_score + 0.3 * a_score
        ranking.append({"model_key": name, "rng_score": round(rng_score, 2),
                        "rng_p": round(p_val, 6) if p_val == p_val else None,
                        "aux_score": round(a_score, 2), "combined": round(combined, 2),
                        "detail": {k: (round(v, 4) if v is not None else None) for k, v in parts.items()}})
    ranking.sort(key=lambda r: -r["combined"])

    warnings = []
    mixture = []
    if t_fp["n"] < args.min_n:
        conclusion, conf = "unknown", "low"
        warnings.append("有效数字样本 %d < min_n %d，仅给出参考排序" % (t_fp["n"], args.min_n))
    else:
        mixture = em_mixture(t_fp["counts"], [m["counts"] for m in models.values()],
                             list(models.keys()))
        top_w = mixture[0]["weight"] if mixture else 0
        if top_w >= SINGLE_W:
            conclusion, conf = "single:" + mixture[0]["model_key"], "high"
        else:
            conclusion, conf = "mixture", "medium"
        if len(ranking) > 1 and ranking[0]["combined"] - ranking[1]["combined"] < MARGIN:
            a, b = ranking[0]["model_key"], ranking[1]["model_key"]
            sim_ab = similarity(models[a]["counts"], models[b]["counts"])
            sim_ta = similarity(t_fp["counts"], models[a]["counts"])
            sim_tb = similarity(t_fp["counts"], models[b]["counts"])
            if sim_ab >= sim_ta and sim_ab >= sim_tb:
                conclusion, conf = "indistinguishable:[%s,%s]" % (a, b), "low"
                warnings.append("top2 互为最近邻（sim=%.4f），诚实标注不可区分" % sim_ab)
    if n_fail:
        warnings.append("失败 %d/%d 次请求" % (n_fail, len(samples)))
    top = ranking[0] if ranking else None
    headline = ("AUTO 本次基本可认定路由到 %s（综合得分 %s）。" % (top["model_key"], top["combined"]) if top else "无候选。")
    if conclusion.startswith("indistinguishable"):
        headline = "AUTO 落在互为近邻的候选对上，当前指纹无法区分：" + conclusion
    elif conclusion == "mixture":
        headline = "AUTO 表现为多模型混合路由：" + ", ".join("%s≈%.0f%%" % (m["model_key"], m["weight"] * 100) for m in mixture)
    elif conclusion == "unknown":
        headline = "有效样本不足，无法可靠判定。"

    verdict = {"target": args.target, "n": t_fp["n"], "n_ok": n_ok, "n_fail": n_fail,
               "elapsed_s": elapsed, "conclusion": conclusion, "confidence": conf,
               "headline": headline, "ranking": ranking[:8], "mixture": mixture,
               "warnings": warnings,
               "baselines_captured_at": pack.get("captured_at_iso")}
    print(json.dumps(verdict, ensure_ascii=False))
    return 0 if conclusion != "unknown" else 2

if __name__ == "__main__":
    sys.exit(main())
