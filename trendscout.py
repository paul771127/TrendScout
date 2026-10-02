#!/usr/bin/env python3
"""TrendScout:各國熱門搜尋關鍵字 + GitHub 熱門專案成長率排行。

只使用 Python 標準函式庫,不需安裝套件。

資料來源
  - 各國搜尋關鍵字:Google Trends「每日搜尋趨勢」RSS(公開,免金鑰)
  - 年齡層:Google 不公開年齡層資料,這裡以「主題分類 → 主要年齡層」的
    規則推估(見 AGE_GROUPS),報表中會標示為推估值
  - GitHub:GitHub Search API(可設 GITHUB_TOKEN 提高速率上限)
      * 新星榜:最近 N 天建立的專案,依「每日平均星數」排序
      * 加速榜:與上次執行的快照比較,依「星數成長率(%/天)」排序
        (第一次執行會建立快照,之後再跑就能算出成長率)

用法
  python trendscout.py                       # 預設國家 + GitHub
  python trendscout.py --geo TW JP US KR     # 指定國家
  python trendscout.py --days 14 --top 30    # GitHub 新星榜取最近 14 天
  python trendscout.py --skip-github         # 只看搜尋趨勢
  python trendscout.py --skip-trends         # 只看 GitHub
輸出:終端表格 + out/report-YYYYMMDD-HHMM.html + 同名 .json
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "out"
SNAPSHOT_FILE = HERE / "out" / "github_snapshots.json"
UA = "Mozilla/5.0 (TrendScout; +https://github.com)"

COUNTRIES = {
    "TW": "台灣", "JP": "日本", "KR": "韓國", "US": "美國", "GB": "英國",
    "DE": "德國", "FR": "法國", "IN": "印度", "BR": "巴西", "ID": "印尼",
    "HK": "香港", "SG": "新加坡", "TH": "泰國", "VN": "越南", "PH": "菲律賓",
    "MY": "馬來西亞", "AU": "澳洲", "CA": "加拿大", "MX": "墨西哥", "ES": "西班牙",
    "IT": "義大利",
}
DEFAULT_GEOS = ["TW", "JP", "KR", "US", "GB", "IN", "BR", "DE"]

# 主題分類規則:比對「關鍵字 + 相關新聞標題」(不分大小寫)
CATEGORIES: dict[str, list[str]] = {
    "遊戲/動漫": ["video game", "gaming", "nintendo", "switch", "playstation", "ps5", "xbox", "steam",
                "anime", "manga", "pokemon", "genshin", "esports", "roblox", "minecraft",
                "fortnite", "valorant", "league of legends", "ゲーム", "アニメ", "遊戲", "動漫",
                "게임", "애니"],
    "音樂/明星/影視": ["concert", "album", "song", "singer", "kpop", "k-pop", "idol", "movie",
                   "film", "netflix", "series", "drama", "actor", "actress", "tour", "trailer",
                   "box office", "spotify", "bts", "blackpink", "taylor swift", "演唱會", "電影",
                   "劇", "歌手", "映画", "ドラマ", "드라마", "영화", "가수"],
    "體育": ["vs", "match", "league", "world cup", "nba", "nfl", "mlb", "fifa", "uefa", "premier league",
           "football", "soccer", "basketball", "baseball", "tennis", "f1", "grand prix",
           "olympic", "goal", "score", "棒球", "足球", "籃球", "野球", "サッカー", "야구", "축구"],
    "科技/AI": ["iphone", "apple", "android", "samsung", "google", "openai", "chatgpt",
              "ai", "claude", "nvidia", "microsoft", "tesla", "update", "ios", "chip", "app",
              "smartphone", "科技", "手機"],
    "財經/投資": ["stock", "stocks", "market", "nasdaq", "dow", "s&p", "bitcoin", "crypto",
              "fed", "rate", "inflation", "earnings", "ipo", "price", "economy", "股", "匯率",
              "株", "주식", "jobs report"],
    "政治/社會": ["election", "president", "minister", "government", "parliament", "congress",
              "senate", "court", "law", "policy", "protest", "war", "選舉", "總統", "政府",
              "選挙", "大統領", "선거", "대통령"],
    "健康/生活": ["health", "covid", "virus", "flu", "vaccine", "hospital", "weather", "typhoon",
              "earthquake", "storm", "hurricane", "recipe", "holiday", "颱風", "地震", "天氣",
              "台風", "天気", "태풍", "날씨"],
}

# 主題 → 推估主要搜尋年齡層(規則型推估,可依需求自行調整)
AGE_GROUPS: dict[str, list[str]] = {
    "13-24 歲": ["遊戲/動漫", "音樂/明星/影視"],
    "25-44 歲": ["科技/AI", "財經/投資", "體育"],
    "45 歲以上": ["政治/社會", "健康/生活", "財經/投資"],
}


# ---------------------------------------------------------------- HTTP

def http_get(url: str, headers: dict | None = None, timeout: int = 25) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404, 422):
                raise
            last_err = e
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
        time.sleep(2 ** attempt)
    raise RuntimeError(f"GET {url} 失敗: {last_err}")


# ---------------------------------------------------------------- 搜尋趨勢

NS = {"ht": "https://trends.google.com/trending/rss"}


def parse_traffic(s: str) -> int:
    m = re.match(r"([\d,.]+)\s*([KkMm萬万]?)", s or "")
    if not m:
        return 0
    n = float(m.group(1).replace(",", ""))
    mult = {"k": 1e3, "m": 1e6, "萬": 1e4, "万": 1e4}.get(m.group(2).lower(), 1)
    return int(n * mult)


def _matcher(words: list[str]) -> re.Pattern:
    # 英文字詞要完整比對(避免 "vs" 命中 "news"),中日韓字詞直接子字串比對
    alts = [rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])" if w.isascii() else re.escape(w)
            for w in words]
    return re.compile("|".join(alts))


_MATCHERS = {cat: _matcher([w.strip() for w in words]) for cat, words in CATEGORIES.items()}


def classify(text: str) -> list[str]:
    t = text.lower()
    hits = [cat for cat, rx in _MATCHERS.items() if rx.search(t)]
    return hits or ["其他"]


def fetch_trends(geo: str) -> list[dict]:
    raw = http_get(f"https://trends.google.com/trending/rss?geo={urllib.parse.quote(geo)}")
    root = ET.fromstring(raw)
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        news = [
            {"title": n.findtext("ht:news_item_title", "", NS),
             "url": n.findtext("ht:news_item_url", "", NS),
             "source": n.findtext("ht:news_item_source", "", NS)}
            for n in it.findall("ht:news_item", NS)
        ]
        traffic_s = it.findtext("ht:approx_traffic", "", NS)
        cats = classify(title + " " + " ".join(n["title"] for n in news))
        items.append({
            "keyword": title,
            "traffic": traffic_s,
            "traffic_n": parse_traffic(traffic_s),
            "pub_date": it.findtext("pubDate", ""),
            "categories": cats,
            "news": news[:3],
        })
    items.sort(key=lambda x: x["traffic_n"], reverse=True)
    return items


def group_by_age(items_by_geo: dict[str, list[dict]]) -> dict[str, dict[str, list[dict]]]:
    """回傳 {年齡層: {國家: [關鍵字...]}}"""
    out: dict[str, dict[str, list[dict]]] = {g: defaultdict(list) for g in AGE_GROUPS}
    for geo, items in items_by_geo.items():
        for it in items:
            for age, cats in AGE_GROUPS.items():
                if any(c in cats for c in it["categories"]):
                    out[age][geo].append(it)
    return {age: dict(v) for age, v in out.items()}


# ---------------------------------------------------------------- GitHub

def gh_headers() -> dict:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def gh_search(query: str, per_page: int = 100, pages: int = 1) -> list[dict]:
    repos = []
    for page in range(1, pages + 1):
        url = ("https://api.github.com/search/repositories?"
               + urllib.parse.urlencode({"q": query, "sort": "stars", "order": "desc",
                                         "per_page": per_page, "page": page}))
        try:
            data = json.loads(http_get(url, gh_headers()))
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("message", "")
            except Exception:
                msg = ""
            hint = "超過速率限制,建議設定 GITHUB_TOKEN" if "rate limit" in msg.lower() else msg
            print(f"  ! GitHub API {e.code}:{hint or '請求被拒'}", file=sys.stderr)
            break
        repos.extend(data.get("items", []))
        if len(data.get("items", [])) < per_page:
            break
    return repos


def slim(r: dict) -> dict:
    return {
        "full_name": r["full_name"],
        "url": r["html_url"],
        "description": (r.get("description") or "")[:160],
        "language": r.get("language") or "",
        "stars": r["stargazers_count"],
        "forks": r["forks_count"],
        "created_at": r["created_at"],
        "topics": r.get("topics", [])[:6],
    }


def rising_new_repos(days: int, top: int, now: datetime) -> list[dict]:
    """最近 N 天建立的專案,依每日平均星數排序。"""
    since = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    for r in gh_search(f"created:>={since} stars:>=20", pages=2):
        s = slim(r)
        created = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
        age_days = max((now - created).total_seconds() / 86400, 0.5)
        s["age_days"] = round(age_days, 1)
        s["stars_per_day"] = round(s["stars"] / age_days, 1)
        out.append(s)
    out.sort(key=lambda x: x["stars_per_day"], reverse=True)
    return out[:top]


def accelerating_repos(top: int, now: datetime) -> tuple[list[dict], str | None]:
    """近期活躍的熱門專案,和上次快照比較星數成長率。"""
    since = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    seen: dict[str, dict] = {}
    for q in (f"pushed:>={since} stars:>=1000", f"pushed:>={since} stars:300..5000"):
        for r in gh_search(q, pages=2):
            seen.setdefault(r["full_name"], slim(r))

    if not seen:
        return [], "GitHub API 沒有回傳資料(可能被限速),本次不更新快照。"

    snaps = {}
    if SNAPSHOT_FILE.exists():
        try:
            snaps = json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            snaps = {}
    prev = snaps.get("latest")
    prev_time = None
    results = []
    if prev:
        prev_time = datetime.fromisoformat(prev["time"])
        dt_days = max((now - prev_time).total_seconds() / 86400, 1 / 24)
        for name, s in seen.items():
            old = prev["stars"].get(name)
            if not old:
                continue
            delta = s["stars"] - old
            s["stars_gained"] = delta
            s["growth_pct_per_day"] = round(delta / old * 100 / dt_days, 3)
            s["stars_gained_per_day"] = round(delta / dt_days, 1)
            results.append(s)
        results.sort(key=lambda x: x["growth_pct_per_day"], reverse=True)

    # 合併(保留舊快照中這次沒抓到的專案,下次仍可比較)
    merged = dict(prev["stars"]) if prev else {}
    merged.update({n: s["stars"] for n, s in seen.items()})
    snaps["latest"] = {"time": now.isoformat(), "stars": merged}
    SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_FILE.write_text(json.dumps(snaps, ensure_ascii=False), encoding="utf-8")

    note = None
    if not prev:
        note = f"已建立第一份快照({len(seen)} 個專案),之後再執行一次即可計算成長率。"
    else:
        note = f"與 {prev_time.astimezone().strftime('%Y-%m-%d %H:%M')} 的快照比較"
    return results[:top], note


def try_github_trending(top: int) -> list[dict]:
    """抓 github.com/trending 的「今日星數」;網頁被擋時回傳空清單。"""
    try:
        page = http_get("https://github.com/trending?since=daily").decode("utf-8", "replace")
    except Exception:
        return []
    out = []
    for art in re.findall(r"<article class=\"Box-row\">(.*?)</article>", page, re.S):
        m = re.search(r'<h2[^>]*>\s*<a[^>]*href="/([^"]+)"', art)
        stars = re.search(r'href="/[^"]+/stargazers"[^>]*>.*?([\d,]+)\s*</a>', art, re.S)
        today = re.search(r"([\d,]+)\s+stars today", art)
        if not (m and stars and today):
            continue
        total = int(stars.group(1).replace(",", ""))
        gained = int(today.group(1).replace(",", ""))
        base = max(total - gained, 1)
        out.append({"full_name": m.group(1).strip(), "url": f"https://github.com/{m.group(1).strip()}",
                    "stars": total, "stars_today": gained,
                    "growth_pct_today": round(gained / base * 100, 2)})
    out.sort(key=lambda x: x["growth_pct_today"], reverse=True)
    return out[:top]


# ---------------------------------------------------------------- 輸出

def print_table(rows: list[list], headers: list[str]) -> None:
    def w(s: str) -> int:  # 中日韓字元算兩格寬
        return sum(2 if ord(c) > 0x2E80 else 1 for c in s)

    rows = [[str(c) for c in r] for r in rows]
    widths = [max([w(h)] + [w(r[i]) for r in rows]) for i, h in enumerate(headers)]
    pad = lambda s, n: s + " " * (n - w(s))
    print("  " + "  ".join(pad(h, widths[i]) for i, h in enumerate(headers)))
    print("  " + "  ".join("-" * n for n in widths))
    for r in rows:
        print("  " + "  ".join(pad(c, widths[i]) for i, c in enumerate(r)))


def cut(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def render_html(report: dict) -> str:
    e = html.escape
    parts = []
    trends = report.get("trends", {})
    if trends:
        parts.append("<h2>各國熱門搜尋關鍵字</h2><div class='grid'>")
        for geo, items in trends.items():
            rows = "".join(
                f"<tr><td>{i}</td><td><b>{e(it['keyword'])}</b>"
                + (f"<div class='sub'><a href='{e(it['news'][0]['url'])}' target='_blank'>"
                   f"{e(cut(it['news'][0]['title'], 70))}</a></div>" if it["news"] else "")
                + f"</td><td>{e(it['traffic'])}</td><td>{e('、'.join(it['categories']))}</td></tr>"
                for i, it in enumerate(items, 1))
            parts.append(f"<section><h3>{e(COUNTRIES.get(geo, geo))} ({geo})</h3>"
                         f"<table><tr><th>#</th><th>關鍵字</th><th>搜尋量</th><th>主題</th></tr>{rows}</table></section>")
        parts.append("</div>")
    ages = report.get("age_groups", {})
    if ages:
        parts.append("<h2>依年齡層(推估)</h2><p class='note'>Google 未公開各年齡層搜尋資料,"
                     "以下依關鍵字主題推估主要搜尋族群,僅供參考。</p><div class='grid'>")
        for age, by_geo in ages.items():
            cats = "、".join(AGE_GROUPS[age])
            lis = "".join(
                f"<li><b>{e(COUNTRIES.get(g, g))}</b>:" + "、".join(e(x['keyword']) for x in its[:6]) + "</li>"
                for g, its in by_geo.items())
            parts.append(f"<section><h3>{e(age)}</h3><div class='sub'>{e(cats)}</div><ul>{lis or '<li>無</li>'}</ul></section>")
        parts.append("</div>")

    def repo_table(title, note, repos, cols):
        if not repos and not note:
            return
        parts.append(f"<h2>{e(title)}</h2>")
        if note:
            parts.append(f"<p class='note'>{e(note)}</p>")
        if repos:
            head = "".join(f"<th>{e(c[0])}</th>" for c in cols)
            body = "".join(
                "<tr><td>%d</td><td><a href='%s' target='_blank'>%s</a><div class='sub'>%s</div></td>%s</tr>" % (
                    i, e(r["url"]), e(r["full_name"]), e(r.get("description", "")),
                    "".join(f"<td>{e(str(r.get(k, '')))}</td>" for _, k in cols[2:]))
                for i, r in enumerate(repos, 1))
            parts.append(f"<table class='wide'><tr>{head}</tr>{body}</table>")

    gh = report.get("github", {})
    repo_table("GitHub 新星榜(依每日平均星數)", gh.get("rising_note"), gh.get("rising", []),
               [("#", ""), ("專案", ""), ("語言", "language"), ("星數", "stars"),
                ("天數", "age_days"), ("星/天", "stars_per_day")])
    repo_table("GitHub 加速榜(依星數成長率)", gh.get("accel_note"), gh.get("accelerating", []),
               [("#", ""), ("專案", ""), ("語言", "language"), ("星數", "stars"),
                ("新增星", "stars_gained"), ("成長率 %/天", "growth_pct_per_day")])
    repo_table("GitHub Trending 今日成長率", None, gh.get("trending_page", []),
               [("#", ""), ("專案", ""), ("星數", "stars"), ("今日星", "stars_today"),
                ("今日成長率 %", "growth_pct_today")])

    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>TrendScout 報表</title>
<style>
:root{{--bg:#fafaf9;--fg:#1c1917;--mut:#78716c;--card:#fff;--line:#e7e5e4;--acc:#2563eb}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1c1917;--fg:#f5f5f4;--mut:#a8a29e;--card:#292524;--line:#44403c;--acc:#60a5fa}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,"Noto Sans TC",sans-serif;margin:0;padding:24px 16px;max-width:1200px;margin:auto}}
h1{{margin:0}} h2{{margin-top:36px;border-bottom:2px solid var(--line);padding-bottom:4px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px}}
section{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;overflow:auto}}
table{{border-collapse:collapse;width:100%}} td,th{{padding:5px 6px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
table.wide{{background:var(--card);border:1px solid var(--line);border-radius:10px;display:block;overflow-x:auto}}
.sub,.note{{color:var(--mut);font-size:12px}} a{{color:var(--acc);text-decoration:none}}
</style></head><body>
<h1>TrendScout 報表</h1><div class="sub">產生時間:{e(report['generated_at'])}</div>
{''.join(parts)}
</body></html>"""


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="各國熱門搜尋關鍵字 + GitHub 熱門專案成長率")
    ap.add_argument("--geo", nargs="+", default=DEFAULT_GEOS,
                    help=f"國家代碼(預設 {' '.join(DEFAULT_GEOS)});可用:{' '.join(COUNTRIES)}")
    ap.add_argument("--per-geo", type=int, default=10, help="每國顯示幾個關鍵字")
    ap.add_argument("--days", type=int, default=7, help="GitHub 新星榜:最近幾天建立的專案")
    ap.add_argument("--top", type=int, default=20, help="GitHub 各榜顯示幾名")
    ap.add_argument("--skip-trends", action="store_true")
    ap.add_argument("--skip-github", action="store_true")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    report: dict = {"generated_at": now.astimezone().strftime("%Y-%m-%d %H:%M %Z")}

    if not args.skip_trends:
        print("\n=== 各國熱門搜尋關鍵字 (Google Trends) ===")
        trends = {}
        for geo in [g.upper() for g in args.geo]:
            try:
                items = fetch_trends(geo)[: args.per_geo]
            except Exception as ex:
                print(f"\n[{geo}] 取得失敗:{ex}", file=sys.stderr)
                continue
            trends[geo] = items
            print(f"\n[{COUNTRIES.get(geo, geo)} {geo}]")
            print_table([[i, cut(it["keyword"], 28), it["traffic"], "、".join(it["categories"])]
                         for i, it in enumerate(items, 1)], ["#", "關鍵字", "搜尋量", "主題"])
        report["trends"] = trends
        report["age_groups"] = group_by_age(trends)
        print("\n=== 依年齡層(依主題推估,非官方資料) ===")
        for age, by_geo in report["age_groups"].items():
            print(f"\n[{age}]  ({'、'.join(AGE_GROUPS[age])})")
            for g, its in by_geo.items():
                print(f"  {COUNTRIES.get(g, g)}: " + "、".join(cut(x['keyword'], 20) for x in its[:6]))

    if not args.skip_github:
        gh: dict = {}
        print(f"\n=== GitHub 新星榜:最近 {args.days} 天建立,依每日平均星數 ===")
        gh["rising"] = rising_new_repos(args.days, args.top, now)
        gh["rising_note"] = f"最近 {args.days} 天建立的專案"
        print_table([[i, cut(r["full_name"], 40), r["language"], r["stars"], r["age_days"], r["stars_per_day"]]
                     for i, r in enumerate(gh["rising"], 1)], ["#", "專案", "語言", "星數", "天數", "星/天"])

        print("\n=== GitHub 加速榜:星數成長率(與上次執行比較) ===")
        gh["accelerating"], gh["accel_note"] = accelerating_repos(args.top, now)
        print(f"  {gh['accel_note']}")
        if gh["accelerating"]:
            print_table([[i, cut(r["full_name"], 40), r["stars"], r["stars_gained"], r["growth_pct_per_day"]]
                         for i, r in enumerate(gh["accelerating"], 1)],
                        ["#", "專案", "星數", "新增星", "成長率%/天"])

        gh["trending_page"] = try_github_trending(args.top)
        if gh["trending_page"]:
            print("\n=== GitHub Trending 今日成長率 ===")
            print_table([[i, cut(r["full_name"], 40), r["stars"], r["stars_today"], r["growth_pct_today"]]
                         for i, r in enumerate(gh["trending_page"], 1)],
                        ["#", "專案", "星數", "今日星", "今日成長率%"])
        report["github"] = gh

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now.astimezone().strftime("%Y%m%d-%H%M")
    (OUT_DIR / f"report-{stamp}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path = OUT_DIR / f"report-{stamp}.html"
    html_path.write_text(render_html(report), encoding="utf-8")
    print(f"\n報表已輸出:{html_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
