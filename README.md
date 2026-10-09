# TrendScout

各國熱門搜尋關鍵字 + GitHub 熱門專案成長率排行。只用 Python 標準函式庫(3.9+),不需安裝套件。

```bash
python trendscout.py                        # 預設 21 國 + GitHub
python trendscout.py --geo TW JP KR US      # 指定國家
python trendscout.py --days 14 --top 30     # GitHub 新星榜取最近 14 天、前 30 名
python trendscout.py --skip-github          # 只看搜尋趨勢
python trendscout.py --no-translate         # 不產生中文翻譯
export GITHUB_TOKEN=ghp_xxx                 # 選用:提高 GitHub API 速率上限
```

輸出:終端表格 + `out/report-*.html`(可用瀏覽器開)+ `out/report-*.json`。

網頁報表(版面在 `template.html`)可以:

- 熱搜:依 23 種主題(AI、手機/3C、足球、股市、天氣…)和國家篩選,顯示前 10/20/50/100 名
- GitHub:新星榜/加速榜/今日 Trending,依 17 種專案分類和熱門標籤篩選,顯示前 10/20/50/100 名
- 右上角「全部翻中文」按鈕:關鍵字、新聞標題和 GitHub 專案說明會切換成繁體中文,再按一次回到原文。翻譯在產生報表時透過 Google 翻譯完成,存在 `out/translations.json`,翻過的不會重翻。

| 區塊 | 資料來源 | 說明 |
|---|---|---|
| 各國關鍵字 | Google Trends 每日搜尋趨勢 RSS | 每國最新熱搜、搜尋量、相關新聞 |
| 主題分類 | 規則比對 | 比對關鍵字、新聞標題和它們的中文翻譯;詞庫在 `CATEGORIES`(搜尋)和 `GH_CATEGORIES`(GitHub),可自行增減 |
| 年齡層 | 規則推估 | Google 不公開年齡層資料;依主題對應年齡層(`AGE_GROUPS` 可自行調整) |
| GitHub 新星榜 | GitHub Search API | 最近 N 天建立的專案,依「每日平均星數」排序 |
| GitHub 加速榜 | GitHub Search API + 本機快照 | 與上次執行比較的星數成長率(%/天);第一次執行只建立快照 |
| GitHub Trending | github.com/trending | 今日新增星數 ÷ 原有星數;網頁被擋時自動略過 |

建議用 cron 每天跑一次,加速榜就會是「每日成長率」:
`0 9 * * * cd /path/to/trendscout && python3 trendscout.py`
