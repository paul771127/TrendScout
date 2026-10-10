"""TrendScout 分析:統計報告(一定產生)+ AI 深入分析(有 Claude API 金鑰時)。

統計部分只用標準函式庫;AI 部分需要 `pip install anthropic` 並設定 ANTHROPIC_API_KEY。
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

HISTORY_DAYS = 30
AI_MODEL = "claude-opus-5-5"


# ---------------------------------------------------------------- 工具

def _norm(text: str) -> str:
    """跨國比對用:小寫、去掉空白與標點。"""
    return re.sub(r"[\s\-–—_.,:;!?'\"()（）·・]+", "", text.lower())


def _zh(report: dict, text: str) -> str:
    return report.get("translations", {}).get(text, text)


def _all_keywords(report: dict) -> list[dict]:
    rows = []
    for geo, items in report.get("trends", {}).items():
        for pos, it in enumerate(items):
            rows.append({**it, "geo": geo, "pos": pos})
    rows.sort(key=lambda r: (-r["traffic_n"], r["pos"]))
    return rows


# ---------------------------------------------------------------- 歷史紀錄

def load_history(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_history(path: Path, history: dict, day: str, entry: dict) -> None:
    history[day] = entry
    for old in sorted(history)[:-HISTORY_DAYS]:
        del history[old]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, ensure_ascii=False), encoding="utf-8")


def history_entry(report: dict, stats: dict) -> dict:
    return {
        "topics": stats["search"]["topic_counts"],
        "keywords": sorted({_norm(_zh(report, r["keyword"])) for r in _all_keywords(report)}),
        "gh_categories": stats["github"].get("category_counts", {}),
    }


# ---------------------------------------------------------------- 統計

def compute_stats(report: dict, countries: dict, history: dict, today: str) -> dict:
    rows = _all_keywords(report)
    n = len(rows)

    topic_counts = Counter(c for r in rows for c in r["categories"])
    topic_traffic = Counter()
    for r in rows:
        for c in r["categories"]:
            topic_traffic[c] += r["traffic_n"]

    # 各國最常見的主題(不含「其他」)
    by_country = {}
    for geo, items in report.get("trends", {}).items():
        cnt = Counter(c for it in items for c in it["categories"] if c != "其他")
        if cnt:
            top, k = cnt.most_common(1)[0]
            by_country[geo] = {"country": countries.get(geo, geo), "topic": top, "count": k, "total": len(items)}

    # 跨國同時熱門:中文翻譯或原文相同
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[_norm(_zh(report, r["keyword"]))].append(r)
    cross = []
    for key, rs in groups.items():
        geos = sorted({r["geo"] for r in rs})
        if len(geos) >= 2 and key:
            cross.append({"keyword": rs[0]["keyword"], "zh": _zh(report, rs[0]["keyword"]),
                          "countries": [countries.get(g, g) for g in geos],
                          "traffic_n": sum(r["traffic_n"] for r in rs), "categories": rs[0]["categories"]})
    cross.sort(key=lambda x: (-len(x["countries"]), -x["traffic_n"]))

    # 主題在幾個國家出現(擴散度)
    topic_geos: dict[str, set] = defaultdict(set)
    for r in rows:
        for c in r["categories"]:
            topic_geos[c].add(r["geo"])

    # 和前一天比較
    prev_days = sorted(d for d in history if d < today)
    change = None
    if prev_days:
        prev = history[prev_days[-1]]
        prev_kw = set(prev.get("keywords", []))
        new_rows = [r for r in rows if _norm(_zh(report, r["keyword"])) not in prev_kw]
        deltas = {c: topic_counts.get(c, 0) - prev.get("topics", {}).get(c, 0)
                  for c in set(topic_counts) | set(prev.get("topics", {}))}
        change = {
            "compared_with": prev_days[-1],
            "new_count": len(new_rows),
            "new_top": [{"keyword": r["keyword"], "zh": _zh(report, r["keyword"]),
                         "country": countries.get(r["geo"], r["geo"]), "traffic": r["traffic"]}
                        for r in new_rows[:10]],
            "rising_topics": sorted([(c, d) for c, d in deltas.items() if d > 0 and c != "其他"],
                                    key=lambda x: -x[1])[:5],
            "falling_topics": sorted([(c, d) for c, d in deltas.items() if d < 0 and c != "其他"],
                                     key=lambda x: x[1])[:5],
        }

    return {
        "search": {
            "keyword_count": n,
            "country_count": len(report.get("trends", {})),
            "classified_share": round(100 * sum(1 for r in rows if r["categories"] != ["其他"]) / n, 1) if n else 0,
            "topic_counts": dict(topic_counts.most_common()),
            "topic_traffic": dict(topic_traffic.most_common()),
            "topic_spread": {c: len(g) for c, g in sorted(topic_geos.items(), key=lambda x: -len(x[1]))},
            "top_keywords": [{"keyword": r["keyword"], "zh": _zh(report, r["keyword"]),
                              "country": countries.get(r["geo"], r["geo"]), "traffic": r["traffic"],
                              "categories": r["categories"]} for r in rows[:10]],
            "by_country": by_country,
            "cross_country": cross[:15],
        },
        "github": _github_stats(report),
        "change": change,
    }


def _github_stats(report: dict) -> dict:
    gh = report.get("github", {})
    rising = gh.get("rising", [])[:100]
    out: dict = {"rising_count": len(gh.get("rising", [])), "accelerating_count": len(gh.get("accelerating", []))}
    if not rising and not gh.get("accelerating"):
        return out
    cat = Counter(c for r in rising for c in r.get("categories", ["其他"]))
    cat_speed = defaultdict(list)
    for r in rising:
        for c in r.get("categories", ["其他"]):
            cat_speed[c].append(r.get("stars_per_day", 0))
    out.update({
        "category_counts": dict(cat.most_common()),
        "category_avg_stars_per_day": {c: round(sum(v) / len(v), 1) for c, v in
                                       sorted(cat_speed.items(), key=lambda x: -sum(x[1]) / len(x[1]))},
        "languages": dict(Counter(r["language"] for r in rising if r.get("language")).most_common(8)),
        "tags": dict(Counter(t for r in rising for t in r.get("topics", [])).most_common(15)),
        "top_rising": [{"name": r["full_name"], "stars_per_day": r.get("stars_per_day"),
                        "categories": r.get("categories", []),
                        "description": _zh(report, r.get("description", ""))} for r in rising[:10]],
    })
    acc = gh.get("accelerating", [])[:100]
    if acc:
        acc_cat = defaultdict(list)
        for r in acc:
            for c in r.get("categories", ["其他"]):
                acc_cat[c].append(r.get("growth_pct_per_day", 0))
        out["accelerating_by_category"] = {c: round(sum(v) / len(v), 3) for c, v in
                                           sorted(acc_cat.items(), key=lambda x: -sum(x[1]) / len(x[1]))[:8]}
        out["top_accelerating"] = [{"name": r["full_name"], "growth_pct_per_day": r.get("growth_pct_per_day"),
                                    "stars": r.get("stars")} for r in acc[:10]]
    return out


# ---------------------------------------------------------------- 自動摘要

def _label(k: dict) -> str:
    """中文翻譯後面附原文,機器翻譯偶爾會翻錯。"""
    return k["zh"] if k["zh"] == k["keyword"] else f"{k['zh']}({k['keyword']})"


def auto_summary(stats: dict) -> list[str]:
    """把統計結果寫成幾句話,不需要 AI。"""
    s, g, ch = stats["search"], stats["github"], stats["change"]
    lines = []
    topics = [(c, n) for c, n in s["topic_counts"].items() if c != "其他"]
    if topics:
        (t1, n1), *rest = topics
        msg = f"今天 {s['country_count']} 國共 {s['keyword_count']} 個熱搜關鍵字,最多的主題是「{t1}」({n1} 個,出現在 {s['topic_spread'].get(t1, 0)} 個國家)"
        if rest:
            msg += ",其次是" + "、".join(f"「{c}」({n})" for c, n in rest[:2])
        lines.append(msg + "。")
    if s["top_keywords"]:
        k = s["top_keywords"][0]
        lines.append(f"搜尋量最高的是{k['country']}的「{_label(k)}」,搜尋量 {k['traffic']}。")
    if s["cross_country"]:
        c = s["cross_country"][0]
        lines.append(f"跨國話題:「{_label(c)}」同時在{'、'.join(c['countries'])}上榜"
                     + (f",另有 {len(s['cross_country']) - 1} 個關鍵字在兩國以上同時出現。" if len(s["cross_country"]) > 1 else "。"))
    if ch:
        msg = f"和 {ch['compared_with']} 相比,有 {ch['new_count']} 個關鍵字是新上榜"
        if ch["rising_topics"]:
            msg += ";升溫最多的主題是" + "、".join(f"「{c}」(+{d})" for c, d in ch["rising_topics"][:3])
        if ch["falling_topics"]:
            msg += ";降溫的有" + "、".join(f"「{c}」({d})" for c, d in ch["falling_topics"][:2])
        lines.append(msg + "。")
    if g.get("category_counts"):
        cats = [(c, n) for c, n in g["category_counts"].items() if c != "其他"][:3]
        fastest = next(iter(g["category_avg_stars_per_day"].items()), None)
        msg = "GitHub 新星榜前 100 名以" + "、".join(f"「{c}」({n})" for c, n in cats) + "最多"
        if fastest:
            msg += f";平均每天新增星數最高的分類是「{fastest[0]}」({fastest[1]} 星/天)"
        if g.get("languages"):
            msg += ";主要語言是" + "、".join(list(g["languages"])[:3])
        lines.append(msg + "。")
    elif g.get("rising_count") == 0:
        lines.append("這次沒有抓到 GitHub 資料。")
    if s["keyword_count"]:
        lines.append(f"註:{s['classified_share']}% 的關鍵字有對應主題,其餘(多為人名)歸在「其他」。")
    return lines


# ---------------------------------------------------------------- AI 深入分析

AI_SYSTEM = """你是一位熟悉網路趨勢、流行文化與科技產業的分析師,為台灣讀者撰寫每日趨勢分析報告。

資料說明:
- 搜尋資料來自 Google Trends 各國「每日搜尋趨勢」,每國約 10 個關鍵字;搜尋量是 Google 給的級距(如 5000+),不是精確數字。
- 主題分類與年齡層是規則推估,可能有誤判。
- 新聞標題是 Google 附在關鍵字旁的相關新聞,是你理解「為什麼上榜」的主要線索。
- GitHub 資料是最近建立、星數成長最快的專案。

寫作要求:
- 使用繁體中文,用 Markdown 標題(##)分段,條列為主,段落簡短。
- 只根據提供的資料推論;資料沒有的事情不要編造。不確定時說「可能」並說明依據。
- 每個結論都要指出是哪些關鍵字、國家或專案支持它。
- 依序寫這幾段:
  ## 今日重點(3–5 條最值得知道的事)
  ## 跨國與區域觀察(哪些話題跨國、各區域關注的差異)
  ## 主題深入(挑 2–3 個最熱的主題,說明背後原因與脈絡)
  ## GitHub 技術趨勢(有資料才寫:哪類專案在爆紅、反映什麼開發者需求)
  ## 值得追蹤(接下來幾天可能持續發酵的話題,以及內容創作或產品上的切入點)
"""


def _ai_payload(report: dict, stats: dict, countries: dict) -> str:
    rows = _all_keywords(report)
    keywords = []
    for r in rows[:120]:
        news = r["news"][0]["title"] if r.get("news") else ""
        keywords.append({"關鍵字": r["keyword"], "中文": _zh(report, r["keyword"]),
                         "國家": countries.get(r["geo"], r["geo"]), "搜尋量": r["traffic"],
                         "主題": r["categories"], "相關新聞": _zh(report, news)})
    gh = report.get("github", {})
    repos = [{"專案": r["full_name"], "說明": _zh(report, r.get("description", "")), "語言": r.get("language"),
              "星數": r.get("stars"), "每天新增星數": r.get("stars_per_day"), "分類": r.get("categories"),
              "標籤": r.get("topics", [])[:6]} for r in gh.get("rising", [])[:40]]
    payload = {"日期": report.get("generated_at"), "統計": stats, "熱搜關鍵字(依搜尋量排序)": keywords,
               "GitHub 新星榜前 40 名": repos}
    return json.dumps(payload, ensure_ascii=False)


def ai_analysis(report: dict, stats: dict, countries: dict) -> tuple[str | None, str]:
    """回傳 (分析文字, 狀態說明)。沒有套件或金鑰時回傳 (None, 原因)。"""
    try:
        import anthropic
    except ImportError:
        return None, "未安裝 anthropic 套件(pip install anthropic),略過 AI 分析。"

    try:
        client = anthropic.Anthropic()
        with client.beta.messages.stream(
            model=AI_MODEL,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            # 被安全機制拒絕時,伺服器自動改用合適的模型重跑
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=AI_SYSTEM,
            messages=[{"role": "user", "content": "以下是今天的趨勢資料(JSON),請寫出分析報告。\n\n"
                       + _ai_payload(report, stats, countries)}],
        ) as stream:
            msg = stream.get_final_message()
    except anthropic.AuthenticationError:
        return None, "沒有可用的 Claude API 金鑰(請設定 ANTHROPIC_API_KEY),略過 AI 分析。"
    except anthropic.RateLimitError:
        return None, "Claude API 請求過於頻繁,稍後再試。"
    except anthropic.APIStatusError as e:
        return None, f"Claude API 錯誤({e.status_code}):{e.message}"
    except anthropic.APIConnectionError:
        return None, "連不到 Claude API,略過 AI 分析。"
    except TypeError:  # 沒有任何憑證時,SDK 在送出請求前就會報錯
        return None, "沒有設定 Claude API 金鑰(ANTHROPIC_API_KEY),略過 AI 分析。"

    if msg.stop_reason == "refusal":
        return None, "AI 這次沒有產生分析(請求被安全機制拒絕)。"
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    if msg.stop_reason == "max_tokens":
        text += "\n\n(分析長度達上限,後段被截斷。)"
    return (text or None), f"由 {msg.model} 產生"


# ---------------------------------------------------------------- Markdown 報告

def to_markdown(report: dict, stats: dict, summary: list[str], ai_text: str | None, ai_status: str) -> str:
    s, g, ch = stats["search"], stats["github"], stats["change"]
    out = [f"# TrendScout 趨勢報告", "", f"產生時間:{report.get('generated_at')}", "", "## 摘要", ""]
    out += [f"- {line}" for line in summary]
    if ai_text:
        out += ["", "---", "", f"# AI 深入分析", "", f"_{ai_status}_", "", ai_text]
    out += ["", "---", "", "# 統計資料", "", "## 主題分布", "", "| 主題 | 關鍵字數 | 出現國家數 |", "|---|---:|---:|"]
    out += [f"| {c} | {n} | {s['topic_spread'].get(c, 0)} |" for c, n in s["topic_counts"].items()]
    out += ["", "## 搜尋量前 10 名", "", "| # | 關鍵字 | 國家 | 搜尋量 | 主題 |", "|---:|---|---|---|---|"]
    out += [f"| {i} | {k['zh']}" + (f"({k['keyword']})" if k["zh"] != k["keyword"] else "")
            + f" | {k['country']} | {k['traffic']} | {'、'.join(k['categories'])} |"
            for i, k in enumerate(s["top_keywords"], 1)]
    if s["cross_country"]:
        out += ["", "## 跨國同時熱門", ""]
        out += [f"- {_label(c)}:{'、'.join(c['countries'])}" for c in s["cross_country"]]
    if s["by_country"]:
        out += ["", "## 各國最熱主題", "", "| 國家 | 主題 | 關鍵字數 |", "|---|---|---:|"]
        out += [f"| {v['country']} | {v['topic']} | {v['count']}/{v['total']} |" for v in s["by_country"].values()]
    if ch:
        out += ["", f"## 和 {ch['compared_with']} 相比", "", f"新上榜關鍵字 {ch['new_count']} 個。", ""]
        out += [f"- {_label(k)}:{k['country']},{k['traffic']}" for k in ch["new_top"]]
    if g.get("category_counts"):
        out += ["", "## GitHub 新星榜前 100 名", "", "| 分類 | 專案數 | 平均星/天 |", "|---|---:|---:|"]
        out += [f"| {c} | {n} | {g['category_avg_stars_per_day'].get(c, '')} |" for c, n in g["category_counts"].items()]
        out += ["", "熱門標籤:" + "、".join(f"{t}({n})" for t, n in g["tags"].items())]
    return "\n".join(out) + "\n"


def run(report: dict, countries: dict, out_dir: Path, use_ai: bool, now: datetime) -> tuple[dict, str]:
    """計算統計、(選用)AI 分析,更新歷史紀錄。回傳 (report['analysis'], markdown)。"""
    hist_path = out_dir / "history.json"
    history = load_history(hist_path)
    today = now.astimezone().strftime("%Y-%m-%d")
    stats = compute_stats(report, countries, history, today)
    summary = auto_summary(stats)
    ai_text, ai_status = (ai_analysis(report, stats, countries) if use_ai
                          else (None, "已用 --no-ai 關閉 AI 分析。"))
    if report.get("trends"):
        save_history(hist_path, history, today, history_entry(report, stats))
    md = to_markdown(report, stats, summary, ai_text, ai_status)
    return {"stats": stats, "summary": summary, "ai": ai_text, "ai_status": ai_status}, md
