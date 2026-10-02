# TrendScout

各國熱門搜尋關鍵字 + GitHub 熱門專案成長率排行。只用 Python 標準函式庫(3.9+),不需安裝套件。

```bash
python trendscout.py                        # 預設 8 國 + GitHub
python trendscout.py --geo TW JP KR US      # 指定國家
python trendscout.py --days 14 --top 30     # GitHub 新星榜取最近 14 天、前 30 名
python trendscout.py --skip-github          # 只看搜尋趨勢
export GITHUB_TOKEN=ghp_xxx                 # 選用:提高 GitHub API 速率上限
```

輸出:終端表格 + `out/report-*.html`(可用瀏覽器開)+ `out/report-*.json`。

| 區塊 | 資料來源 | 說明 |
|---|---|---|
| 各國關鍵字 | Google Trends 每日搜尋趨勢 RSS | 每國最新熱搜、搜尋量、相關新聞 |
| 年齡層 | 規則推估 | Google 不公開年齡層資料;依主題分類對應年齡層(`CATEGORIES`、`AGE_GROUPS` 可自行調整) |
| GitHub 新星榜 | GitHub Search API | 最近 N 天建立的專案,依「每日平均星數」排序 |
| GitHub 加速榜 | GitHub Search API + 本機快照 | 與上次執行比較的星數成長率(%/天);第一次執行只建立快照 |
| GitHub Trending | github.com/trending | 今日新增星數 ÷ 原有星數;網頁被擋時自動略過 |

建議用 cron 每天跑一次,加速榜就會是「每日成長率」:
`0 9 * * * cd /path/to/trendscout && python3 trendscout.py`
