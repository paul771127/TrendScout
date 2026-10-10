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
  python trendscout.py --no-ai               # 不做 AI 深入分析
輸出:終端表格 + out/report-YYYYMMDD-HHMM.html + 同名 .json
"""
from __future__ import annotations

import argparse
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

import analysis

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
DEFAULT_GEOS = list(COUNTRIES)

# 主題分類規則:比對「關鍵字 + 相關新聞標題」(不分大小寫)。
# 英文等拉丁字母詞要完整比對,中日韓字詞用子字串比對。可自行增減。
CATEGORIES: dict[str, list[str]] = {
    "AI 人工智慧": ["ai", "a.i.", "chatgpt", "openai", "claude", "anthropic", "gemini", "copilot", "llm",
                  "chatbot", "deepseek", "grok", "人工智慧", "生成式", "人工知能", "生成ai", "인공지능",
                  "챗gpt", "inteligência artificial", "künstliche intelligenz", "ki"],
    "手機/3C": ["iphone", "ipad", "apple watch", "airpods", "samsung", "galaxy", "pixel", "android", "ios",
               "macbook", "mac", "laptop", "smartphone", "xiaomi", "huawei", "手機", "平板", "筆電",
               "スマホ", "아이폰", "갤럭시", "celular", "handy"],
    "科技/網路": ["apple", "google", "microsoft", "meta", "nvidia", "tesla", "spacex", "starlink", "software",
                "app", "update", "outage", "internet", "chip", "gpu", "semiconductor", "computing", "computer",
                "youtube", "tiktok", "instagram", "whatsapp", "facebook", "doodle", "半導體", "晶片", "輝達",
                "台積電", "半導体", "반도체", "科技"],
    "電玩/電競": ["video game", "gaming", "nintendo", "switch 2", "playstation", "ps5", "xbox", "steam",
                "esports", "roblox", "minecraft", "fortnite", "valorant", "league of legends", "gta",
                "pokemon", "pokémon", "genshin", "ゲーム", "遊戲", "電玩", "電競", "게임", "videogame"],
    "動漫": ["anime", "manga", "アニメ", "漫画", "動漫", "漫畫", "声優", "コスプレ", "애니", "웹툰", "鬼滅",
           "one piece", "ジャンプ"],
    "音樂": ["concert", "album", "song", "singer", "kpop", "k-pop", "tour", "spotify", "grammy", "festival",
           "bts", "blackpink", "taylor swift", "演唱會", "歌手", "專輯", "新歌", "ライブ", "フェス", "가수",
           "콘서트", "앨범", "música", "cantor", "cantora", "show", "konzert", "sänger", "sängerin"],
    "影視/綜藝": ["movie", "film", "netflix", "series", "drama", "trailer", "box office", "episode", "season",
                "tv", "documentary", "disney", "hbo", "oscar", "emmy", "電影", "影集", "劇", "綜藝", "映画",
                "ドラマ", "番組", "드라마", "영화", "예능", "novela", "filme", "série", "serie", "tatort"],
    "明星/名人": ["celebrity", "actor", "actress", "idol", "prince", "princess", "royal", "kardashian",
                "wedding", "divorce", "dating", "藝人", "偶像", "女星", "男星", "女優", "俳優", "芸能",
                "タレント", "アイドル", "写真集", "グラドル", "배우", "아이돌", "열애", "ator", "atriz",
                "schauspieler", "schauspielerin", "prinz", "prinzessin", "scheidung", "結婚", "離婚", "熱愛"],
    "足球": ["football", "soccer", "fifa", "uefa", "premier league", "champions league", "la liga",
           "world cup", "futebol", "fußball", "bundesliga", "serie a", "brasileirão", "libertadores",
           "足球", "國足", "サッカー", "축구", "dfb"],
    "籃球": ["nba", "wnba", "basketball", "籃球", "バスケ", "bリーグ", "농구"],
    "棒球": ["mlb", "baseball", "npb", "kbo", "world series", "dodgers", "yankees", "棒球", "中職", "野球",
           "甲子園", "球団", "巨人", "阪神", "大谷", "야구"],
    "其他運動": ["tennis", "f1", "formula 1", "grand prix", "golf", "boxing", "ufc", "nfl", "nhl", "hockey",
             "eishockey", "cricket", "marathon", "olympic", "olympics", "wta", "atp", "rugby", "volleyball",
             "網球", "排球", "羽球", "亞運", "奧運", "アジア大会", "五輪", "テニス", "테니스", "배구", "마라톤"],
    "股市/投資": ["stock", "stocks", "nasdaq", "dow", "s&p", "earnings", "ipo", "dividend", "etf", "股市",
              "股價", "台股", "美股", "上市", "首次公開發行", "株価", "日経", "配当", "주식", "코스피",
              "배당", "ações", "bolsa", "ibovespa", "aktie", "börse", "dax"],
    "加密貨幣": ["bitcoin", "btc", "ethereum", "crypto", "cryptocurrency", "solana", "dogecoin",
             "比特幣", "加密貨幣", "虛擬貨幣", "ビットコイン", "暗号資産", "비트코인", "코인"],
    "經濟/就業": ["economy", "inflation", "fed", "interest rate", "rate cut", "jobs report", "unemployment",
              "gdp", "tariff", "tariffs", "recession", "經濟", "通膨", "利率", "關稅", "匯率", "景気",
              "円安", "金利", "경제", "금리", "환율", "economia", "inflação", "wirtschaft",
              "arbeitslosigkeit", "zinsen", "erträge"],
    "商業/購物": ["deals", "sale", "black friday", "amazon", "costco", "walmart", "shopping", "好市多",
              "好 市 多", "百貨", "三越", "優惠", "折扣", "セール", "할인", "하이마트", "promoção",
              "angebot", "海運", "長榮", "shipping"],
    "政治/選舉": ["election", "president", "minister", "government", "parliament", "congress", "senate",
              "trump", "harris", "biden", "vote", "poll", "pentagon", "選舉", "總統", "政府", "立委",
              "立法院", "國防", "選挙", "首相", "大統領", "国会", "선거", "대통령", "국회", "정부",
              "국방부", "보훈", "eleição", "eleito", "eleitoral", "deputado", "senado", "candidatos",
              "wahl", "regierung", "bundestag", "kanzler", "protests", "protest"],
    "國際/戰爭": ["war", "ukraine", "russia", "israel", "gaza", "iran", "missile", "nato", "embassy",
              "戰爭", "烏克蘭", "以色列", "飛彈", "共軍", "民兵", "戦争", "北朝鮮", "전쟁", "북한",
              "guerra", "krieg", "embaixadas"],
    "社會/案件": ["crime", "police", "shooting", "murder", "arrest", "court", "trial", "lawsuit", "accident",
              "crash", "fire", "犯罪", "警察", "逮捕", "事件", "法院", "車禍", "火災", "殺人", "事故",
              "범죄", "화재", "사고", "경찰", "내란", "구치소", "polícia", "polizei", "verbrechen",
              "schüsse", "unfall"],
    "天氣/災害": ["weather", "storm", "hurricane", "typhoon", "tornado", "earthquake", "flood", "snow",
              "heatwave", "颱風", "地震", "天氣", "豪雨", "季風", "雨", "寒流", "台風", "天気", "大雨",
              "熱帯低気圧", "태풍", "날씨", "지진", "chuva", "tempestade", "temporal", "previsão do tempo",
              "wetter", "unwetter", "sturm"],
    "健康/醫療": ["health", "covid", "virus", "flu", "vaccine", "hospital", "cancer", "depression",
              "disease", "outbreak", "strain", "疫苗", "醫院", "流感", "癌", "健康", "傷口", "病院",
              "介護", "병원", "입원", "건강", "saúde", "krebs", "gesundheit"],
    "生活/節日": ["holiday", "recipe", "food", "coffee", "restaurant", "travel", "flight", "airline",
              "daylight saving", "sommerzeit", "lottery", "powerball", "halloween", "thanksgiving",
              "christmas", "oktoberfest", "unity day", "咖啡", "美食", "旅遊", "連假", "門票", "祝日",
              "연휴", "feriado", "natal"],
    "教育/文化": ["nobel", "literature", "poet", "book", "novel", "exam", "university", "school", "museum",
              "palace", "諾貝爾", "文學", "考試", "大學", "學測", "ノーベル", "試験", "노벨", "문학",
              "수능", "대학", "경복궁", "literatura", "literatur", "prêmio"],
}
# 中文詞庫:分類時也會比對關鍵字的中文翻譯,讓各種語言都能套用同一套規則
for _cat, _words in {
    'AI 人工智慧': ['人工智慧', '聊天機器人'],
    '手機/3C': ['手機', '三星', '筆電', '平板', '耳機'],
    '科技/網路': ['網路', '軟體', '應用程式', '當機', '谷歌', '微軟', '特斯拉', '晶片'],
    '電玩/電競': ['遊戲', '電玩', '電競', '玩家', '任天堂'],
    '動漫': ['動畫', '漫畫', '動漫', '聲優'],
    '音樂': ['演唱會', '歌手', '專輯', '歌曲', '樂團', '音樂', '巡演', '音樂節', '饒舌'],
    '影視/綜藝': ['電影', '影集', '電視劇', '劇集', '節目', '預告', '票房', '首映', '紀錄片', '主演', '演員', '影評'],
    '明星/名人': ['明星', '藝人', '女演員', '男演員', '偶像', '王子', '公主', '王室', '戀情', '網紅', '名人'],
    '足球': ['足球', '英超', '西甲', '德甲', '意甲', '法甲', '歐冠', '世界盃', '世足', '足協', '國家隊'],
    '籃球': ['籃球'],
    '棒球': ['棒球', '大聯盟', '職棒', '投手', '打者', '全壘打'],
    '其他運動': ['網球', '高爾夫', '拳擊', '賽車', '冰球', '冰上曲棍球', '板球', '馬拉松', '橄欖球', '排球', '羽球', '奧運', '亞運', '美式足球', '一級方程式', '摔角', '自行車', '游泳'],
    '股市/投資': ['股市', '股票', '股價', '股息', '指數', '證券', '投資', '漲停', '大漲', '大跌', '收盤', '上市'],
    '加密貨幣': ['比特幣', '加密貨幣', '以太坊', '虛擬貨幣'],
    '經濟/就業': ['經濟', '通膨', '通貨膨脹', '利率', '降息', '升息', '關稅', '失業', '就業', '薪資', '匯率', '景氣', '央行'],
    '商業/購物': ['購物', '優惠', '折扣', '特賣', '促銷', '百貨', '超市', '門市', '開幕', '電商'],
    '政治/選舉': ['選舉', '總統', '政府', '部長', '國會', '議員', '參議院', '眾議院', '總理', '首相', '投票', '民調', '候選人', '政黨', '抗議', '示威', '內閣'],
    '國際/戰爭': ['戰爭', '軍隊', '軍方', '飛彈', '空襲', '停火', '大使館', '制裁', '北約', '烏克蘭', '俄羅斯', '以色列', '加薩', '伊朗'],
    '社會/案件': ['犯罪', '警方', '警察', '逮捕', '槍擊', '謀殺', '法院', '判決', '起訴', '審判', '車禍', '事故', '火災', '遇害', '嫌犯', '死亡'],
    '天氣/災害': ['天氣', '颱風', '地震', '暴雨', '豪雨', '大雨', '下雨', '降雨', '風暴', '颶風', '龍捲風', '洪水', '氣溫', '寒流', '熱浪', '降雪', '氣象', '天氣預報'],
    '健康/醫療': ['健康', '醫院', '醫生', '疾病', '病毒', '疫苗', '癌症', '流感', '新冠', '憂鬱', '醫療', '藥物'],
    '生活/節日': ['節日', '假期', '連假', '美食', '餐廳', '咖啡', '食譜', '旅遊', '航班', '航空', '夏令時間', '彩券', '樂透', '萬聖節', '聖誕節', '慶典', '紀念日', '國慶'],
    '教育/文化': ['諾貝爾', '文學', '詩人', '小說', '考試', '大學', '學校', '學生', '教育', '博物館', '歷史', '文化'],
}.items():
    CATEGORIES[_cat] += _words

for _cat, _words in {
    "科技/網路": ["伺服器", "server"],
    "影視/綜藝": ["哥斯拉", "廣播", "radio"],
    "足球": ["fc", "cf", "sk", "arsenal", "real madrid", "barcelona", "boca juniors", "兵工廠"],
    "股市/投資": ["mdax", "nikkei", "kospi"],
    "生活/節日": ["祈禱", "禮拜", "齋戒", "sholat", "jadwal"],
    "教育/文化": ["太空", "天文", "月亮", "土星", "nasa", "世代"],
}.items():
    CATEGORIES[_cat] += _words

# 只說明「有比賽」但看不出項目的詞:沒命中其他運動分類時,歸到「其他運動」
SPORTS_FALLBACK = ["vs", "x", "match", "選手", "比賽", "경기", "선수", "盃", "杯", "賽", "game", "spiel",
                   "對陣", "球隊", "聯賽", "決賽", "擊敗", "戰勝", "賽事", "冠軍"]
SPORTS = {"足球", "籃球", "棒球", "其他運動"}

# 主題 → 推估主要搜尋年齡層(規則型推估,可依需求自行調整)
AGE_GROUPS: dict[str, list[str]] = {
    "13-24 歲": ["電玩/電競", "動漫", "音樂", "影視/綜藝", "明星/名人"],
    "25-44 歲": ["AI 人工智慧", "手機/3C", "科技/網路", "股市/投資", "加密貨幣", "足球", "籃球", "棒球",
                "其他運動", "商業/購物", "生活/節日"],
    "45 歲以上": ["政治/選舉", "國際/戰爭", "社會/案件", "天氣/災害", "健康/醫療", "經濟/就業", "教育/文化"],
}

# GitHub 專案分類:比對「專案名稱 + 說明 + topics 標籤」
GH_CATEGORIES: dict[str, list[str]] = {
    "AI Agent": ["agent", "agents", "agentic", "mcp", "mcp-server", "model context protocol", "multi-agent",
                 "autonomous", "computer-use", "browser-use", "智能体"],
    "LLM/聊天": ["llm", "llms", "gpt", "chatgpt", "openai", "claude", "claude-code", "anthropic", "gemini",
               "llama", "ollama", "deepseek", "qwen", "rag", "chatbot", "prompt", "prompts", "langchain",
               "大模型", "大语言模型"],
    "機器學習": ["machine-learning", "machine learning", "deep-learning", "deep learning", "pytorch",
             "tensorflow", "jax", "transformer", "transformers", "neural", "training", "fine-tuning",
             "finetuning", "inference", "cuda", "reinforcement-learning", "dataset", "benchmark"],
    "影像/影音": ["stable-diffusion", "diffusion", "comfyui", "image-generation", "text-to-image",
              "video-generation", "tts", "text-to-speech", "speech", "whisper", "voice", "audio", "music",
              "ocr", "computer-vision", "yolo", "image", "video", "3d", "gaussian-splatting", "camera"],
    "網頁前端": ["react", "vue", "svelte", "nextjs", "next.js", "angular", "frontend", "css", "tailwind",
             "tailwindcss", "ui", "component", "components", "design-system", "website", "web"],
    "後端/資料庫": ["backend", "api", "rest", "graphql", "database", "sql", "postgres", "postgresql", "mysql",
               "sqlite", "redis", "orm", "microservice", "microservices"],
    "開發工具": ["cli", "terminal", "devtools", "developer-tools", "ide", "vscode", "neovim", "vim", "editor",
             "git", "lint", "linter", "formatter", "compiler", "testing", "debugger", "sdk", "plugin",
             "extension", "code-review", "coding", "grep"],
    "DevOps/雲端": ["docker", "kubernetes", "k8s", "devops", "cloud", "aws", "gcp", "azure", "serverless",
                  "terraform", "deploy", "deployment", "self-hosted", "selfhosted", "homelab", "monitoring",
                  "observability"],
    "資安/隱私": ["security", "pentest", "penetration-testing", "vulnerability", "exploit", "ctf", "malware",
              "osint", "privacy", "encryption", "red-team", "hacking", "cybersecurity"],
    "行動 App": ["ios", "android", "swiftui", "kotlin", "flutter", "react-native", "mobile", "iphone"],
    "桌面/系統": ["macos", "windows", "linux", "desktop", "electron", "tauri", "menubar", "shell", "kernel",
              "operating-system", "productivity", "automation", "dotfiles"],
    "遊戲開發": ["game", "games", "gamedev", "game-engine", "unity", "godot", "unreal", "minecraft", "emulator",
             "mod", "modding", "roblox"],
    "資料/爬蟲": ["scraper", "scraping", "crawler", "crawl", "spider", "data", "analytics", "dashboard", "etl",
              "visualization", "pandas", "spreadsheet", "firecrawl"],
    "學習資源": ["awesome", "awesome-list", "tutorial", "tutorials", "course", "learning", "interview",
             "roadmap", "book", "books", "guide", "cheatsheet", "education", "resources", "教程", "学习",
             "面试"],
    "硬體/IoT": ["embedded", "iot", "arduino", "esp32", "raspberry-pi", "hardware", "robot", "robotics",
               "firmware", "drone"],
    "區塊鏈/Web3": ["blockchain", "web3", "crypto", "ethereum", "solana", "bitcoin", "defi", "nft",
                  "smart-contract"],
    "金融/量化": ["trading", "quant", "stock", "stocks", "finance", "fintech", "backtest", "backtesting",
              "investment"],
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


# ---------------------------------------------------------------- 翻譯

TRANSLATION_CACHE = OUT_DIR / "translations.json"
_HAN = re.compile(r"[\u4e00-\u9fff]")
_FOREIGN = re.compile(r"[A-Za-z\u00c0-\u024f\u3040-\u30ff\uac00-\ud7af\u0400-\u04ff\u0e00-\u0e7f\u0900-\u097f]")


def needs_translation(text: str) -> bool:
    """已經是中文(有漢字、沒有假名/韓文/拉丁字母等)就不用翻。"""
    return bool(text.strip()) and bool(_FOREIGN.search(text) or not _HAN.search(text))


def translate_one(text: str) -> str:
    url = ("https://translate.googleapis.com/translate_a/single?"
           + urllib.parse.urlencode({"client": "gtx", "sl": "auto", "tl": "zh-TW", "dt": "t", "q": text}))
    data = json.loads(http_get(url, timeout=15))
    return "".join(seg[0] for seg in data[0] if seg and seg[0]).strip()


def translate_all(texts: set[str]) -> dict[str, str]:
    """把文字翻成繁體中文;結果存在 out/translations.json,下次不用重翻。"""
    from concurrent.futures import ThreadPoolExecutor

    cache: dict[str, str] = {}
    if TRANSLATION_CACHE.exists():
        try:
            cache = json.loads(TRANSLATION_CACHE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cache = {}
    todo = [t for t in texts if needs_translation(t) and t not in cache]

    def work(t: str) -> tuple[str, str | None]:
        try:
            return t, translate_one(t)
        except Exception:
            return t, None

    failed: list[str] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for t, zh in pool.map(work, todo):
            if zh:
                cache[t] = zh
            else:
                failed.append(t)
    if failed:  # 多半是請求太密集被拒,放慢速度再試一次
        time.sleep(3)
        retry, failed = failed, []
        for t in retry:
            _, zh = work(t)
            if zh:
                cache[t] = zh
            else:
                failed.append(t)
            time.sleep(0.3)
    failed = len(failed)
    if todo:
        print(f"  翻譯 {len(todo) - failed} 筆" + (f",{failed} 筆失敗(保留原文)" if failed else ""))
    TRANSLATION_CACHE.parent.mkdir(parents=True, exist_ok=True)
    TRANSLATION_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return {t: cache[t] for t in texts if t in cache}


def collect_texts(report: dict) -> set[str]:
    texts: set[str] = set()
    for items in report.get("trends", {}).values():
        for it in items:
            texts.add(it["keyword"])
            if it["news"]:
                texts.add(it["news"][0]["title"])
    for key in ("rising", "accelerating", "trending_page"):
        for r in report.get("github", {}).get(key, [])[:300]:
            if r.get("description"):
                texts.add(r["description"])
    return texts


# ---------------------------------------------------------------- 搜尋趨勢

NS = {"ht": "https://trends.google.com/trending/rss"}


def parse_traffic(s: str) -> int:
    m = re.match(r"([\d,.]+)\s*([KkMm萬万]?)", s or "")
    if not m:
        return 0
    n = float(m.group(1).replace(",", ""))
    mult = {"k": 1e3, "m": 1e6, "萬": 1e4, "万": 1e4}.get(m.group(2).lower(), 1)
    return int(n * mult)


def _is_latin(w: str) -> bool:
    return all(ord(c) < 0x2E80 for c in w)


def _matcher(words: list[str]) -> re.Pattern:
    # 英文字詞要完整比對(避免 "vs" 命中 "news"),中日韓字詞直接子字串比對
    alts = [rf"(?<!\w){re.escape(w)}(?!\w)" if _is_latin(w) else re.escape(w) for w in words]
    return re.compile("|".join(alts))


_MATCHERS = {cat: _matcher(words) for cat, words in CATEGORIES.items()}
_SPORTS_FALLBACK = _matcher(SPORTS_FALLBACK)
_GH_MATCHERS = {cat: _matcher(words) for cat, words in GH_CATEGORIES.items()}


def classify(text: str) -> list[str]:
    t = text.lower()
    hits = [cat for cat, rx in _MATCHERS.items() if rx.search(t)]
    if not SPORTS.intersection(hits) and _SPORTS_FALLBACK.search(t):
        hits.append("其他運動")
    return hits or ["其他"]


def classify_repo(r: dict) -> list[str]:
    name = r["full_name"].split("/")[-1].replace("-", " ").replace("_", " ")
    t = " ".join([name, r.get("description", ""), " ".join(r.get("topics", []))]).lower()
    hits = [cat for cat, rx in _GH_MATCHERS.items() if rx.search(t)]
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
        "topics": r.get("topics", [])[:12],
    }


def rising_new_repos(days: int, top: int, now: datetime) -> list[dict]:
    """最近 N 天建立的專案,依每日平均星數排序。"""
    since = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    for r in gh_search(f"created:>={since} stars:>=20", pages=3):
        s = slim(r)
        created = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
        age_days = max((now - created).total_seconds() / 86400, 0.5)
        s["age_days"] = round(age_days, 1)
        s["stars_per_day"] = round(s["stars"] / age_days, 1)
        out.append(s)
    out.sort(key=lambda x: x["stars_per_day"], reverse=True)
    return out[:top] if top else out


def accelerating_repos(top: int, now: datetime) -> tuple[list[dict], str | None]:
    """近期活躍的熱門專案,和上次快照比較星數成長率。"""
    since = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    seen: dict[str, dict] = {}
    for q in (f"pushed:>={since} stars:>=1000", f"pushed:>={since} stars:300..5000"):
        for r in gh_search(q, pages=2):
            seen.setdefault(r["full_name"], slim(r))

    if not seen:
        return [], "GitHub API 沒有回傳資料(被拒或被限速),本次不更新快照。"

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
    return (results[:top] if top else results), note


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
                    "description": "", "language": "", "topics": [],
                    "stars": total, "stars_today": gained,
                    "growth_pct_today": round(gained / base * 100, 2)})
    out.sort(key=lambda x: x["growth_pct_today"], reverse=True)
    return out[:top] if top else out


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


TEMPLATE_FILE = HERE / "template.html"


def render_html(report: dict) -> str:
    """把報表資料嵌進 template.html;網頁上的篩選、排名、翻譯切換都由頁面裡的 JS 處理。"""
    payload = {
        **report,
        "countries": COUNTRIES,
        "categories": list(CATEGORIES) + ["其他"],
        "gh_categories": list(GH_CATEGORIES) + ["其他"],
        "age_groups": AGE_GROUPS,
    }
    data = (json.dumps(payload, ensure_ascii=False)
            .replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
    return TEMPLATE_FILE.read_text(encoding="utf-8").replace("/*__REPORT__*/null", data)


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="各國熱門搜尋關鍵字 + GitHub 熱門專案成長率")
    ap.add_argument("--geo", nargs="+", default=DEFAULT_GEOS,
                    help=f"國家代碼(預設 {' '.join(DEFAULT_GEOS)});可用:{' '.join(COUNTRIES)}")
    ap.add_argument("--per-geo", type=int, default=10, help="終端機上每國顯示幾個關鍵字")
    ap.add_argument("--days", type=int, default=7, help="GitHub 新星榜:最近幾天建立的專案")
    ap.add_argument("--top", type=int, default=20, help="終端機上 GitHub 各榜顯示幾名(網頁可選到前 100)")
    ap.add_argument("--skip-trends", action="store_true")
    ap.add_argument("--skip-github", action="store_true")
    ap.add_argument("--no-translate", action="store_true", help="不產生中文翻譯")
    ap.add_argument("--no-ai", action="store_true", help="不呼叫 Claude API 做 AI 深入分析")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    report: dict = {"generated_at": now.astimezone().strftime("%Y-%m-%d %H:%M %Z")}

    if not args.skip_trends:
        print("\n=== 各國熱門搜尋關鍵字 (Google Trends) ===")
        trends = {}
        for geo in [g.upper() for g in args.geo]:
            try:
                items = fetch_trends(geo)
            except Exception as ex:
                print(f"\n[{geo}] 取得失敗:{ex}", file=sys.stderr)
                continue
            trends[geo] = items
        if not args.no_translate:
            print("  翻譯關鍵字與新聞標題…")
            zh = translate_all(collect_texts({"trends": trends}))
            for items in trends.values():
                for it in items:
                    texts = [it["keyword"]] + [n["title"] for n in it["news"]]
                    it["categories"] = classify(" ".join(texts + [zh.get(t, "") for t in texts]))
        for geo, items in trends.items():
            print(f"\n[{COUNTRIES.get(geo, geo)} {geo}]")
            print_table([[i, cut(it["keyword"], 28), it["traffic"], "、".join(it["categories"])]
                         for i, it in enumerate(items[: args.per_geo], 1)], ["#", "關鍵字", "搜尋量", "主題"])
        report["trends"] = trends
        print("\n=== 依年齡層(依主題推估,非官方資料) ===")
        for age, by_geo in group_by_age(trends).items():
            print(f"\n[{age}]  ({'、'.join(AGE_GROUPS[age])})")
            for g, its in by_geo.items():
                print(f"  {COUNTRIES.get(g, g)}: " + "、".join(cut(x['keyword'], 20) for x in its[:6]))

    if not args.skip_github:
        gh: dict = {}
        print(f"\n=== GitHub 新星榜:最近 {args.days} 天建立,依每日平均星數 ===")
        gh["rising"] = rising_new_repos(args.days, 0, now)
        gh["rising_note"] = f"最近 {args.days} 天建立的專案"
        print_table([[i, cut(r["full_name"], 40), r["language"], r["stars"], r["age_days"], r["stars_per_day"]]
                     for i, r in enumerate(gh["rising"][: args.top], 1)], ["#", "專案", "語言", "星數", "天數", "星/天"])

        print("\n=== GitHub 加速榜:星數成長率(與上次執行比較) ===")
        gh["accelerating"], gh["accel_note"] = accelerating_repos(0, now)
        print(f"  {gh['accel_note']}")
        if gh["accelerating"]:
            print_table([[i, cut(r["full_name"], 40), r["stars"], r["stars_gained"], r["growth_pct_per_day"]]
                         for i, r in enumerate(gh["accelerating"][: args.top], 1)],
                        ["#", "專案", "星數", "新增星", "成長率%/天"])

        gh["trending_page"] = try_github_trending(0)
        if gh["trending_page"]:
            print("\n=== GitHub Trending 今日成長率 ===")
            print_table([[i, cut(r["full_name"], 40), r["stars"], r["stars_today"], r["growth_pct_today"]]
                         for i, r in enumerate(gh["trending_page"][: args.top], 1)],
                        ["#", "專案", "星數", "今日星", "今日成長率%"])
        for key in ("rising", "accelerating", "trending_page"):
            for r in gh[key]:
                r["categories"] = classify_repo(r)
        report["github"] = gh

    if not args.no_translate:
        print("\n=== 翻譯成中文 ===")
        report["translations"] = translate_all(collect_texts(report))

    print("\n=== 分析報告 ===")
    report["analysis"], analysis_md = analysis.run(report, COUNTRIES, OUT_DIR, not args.no_ai, now)
    for line in report["analysis"]["summary"]:
        print(f"  {line}")
    print(f"  AI 分析:{report['analysis']['ai_status']}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = now.astimezone().strftime("%Y%m%d-%H%M")
    (OUT_DIR / f"analysis-{stamp}.md").write_text(analysis_md, encoding="utf-8")
    (OUT_DIR / f"report-{stamp}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path = OUT_DIR / f"report-{stamp}.html"
    html_path.write_text(render_html(report), encoding="utf-8")
    print(f"\n報表已輸出:{html_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
