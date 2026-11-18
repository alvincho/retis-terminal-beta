# 常見問題

## Mac 無法打開

如果 macOS 說「無法打開，因為無法驗證開發者」，請到 **系統設定 → 隱私權與安全性**，在下方找到 Retis Terminal，按 **強制打開**（或 **仍要打開**）。只有第一次需要。

## Windows 已保護您的電腦

如果出現「Windows 已保護您的電腦」，按 **其他資訊** → **仍要執行**。只有第一次需要。

## 收不到驗證信

請看垃圾郵件匣。幾分鐘後仍沒收到，在 Retis Terminal 的登入畫面按 **重新寄送確認信**。

## 瀏覽器分頁關掉了怎麼辦？

點選單列（Mac）或系統匣（Windows）的 Retis Terminal 圖示 → **開啟**。

## 分頁顯示「這個 Terminal 已停止。」

Retis Terminal 已結束。Mac 從「應用程式」、Windows 從「開始」功能表再次開啟 Retis Terminal，它會在新分頁開啟，工作區與策略都在。

## 舊分頁顯示「Retis Terminal 已重新啟動，並在新分頁開啟了這個視窗。」

在新分頁繼續使用，關閉舊分頁即可。

## 我的資料存在哪裡？

工作區、版面與保留的結果存在您的電腦上。tw-001 只保存帳號、點數與使用權紀錄。請看 [隱私](../../legal/PRIVACY.zh-TW.md)。

## 價格資料從哪裡來？

- 「台灣半導體觀察」的 **台灣股票報價** 來自證交所與櫃買中心，經由 tw-001 提供，不扣點。
- **歷史價格**（走勢圖、回測、模擬交易）、**新聞** 與 **公司資料** 來自在您電腦上執行的 [本機 yfinance](local-yfinance.md)，或您以自己金鑰加入的其他資料來源。
- 「地球檢視」的 **街道地圖** 來自同樣在您電腦上執行的 [本機 OpenStreetMap](local-openstreetmap.md)，或您以自己金鑰加入的地圖圖磚供應商。

## 需要輸入券商或資料商的密碼嗎？

不需要。測試版只用公開資料做模擬交易。若您有資料商的 API 金鑰，可以在 **終端設定 → 資料來源** 輸入。清單上已經有許多資料來源，例如富果（[富果行情 API 方案](https://developer.fugle.tw/docs/pricing/?utm_source=attas&utm_medium=docs&utm_campaign=attas_pricing&utm_content=faq_zh)，合作推廣連結）；清單上沒有的，也可以要求加入（見下一題）。金鑰只存在您的電腦，只會直接送到該資料商。

## 我要的資料來源不在清單上怎麼辦？

您可以要求加入任何資料來源：有 API 的資料商，例如 Bloomberg、LSEG、Alpha Vantage、FinMind 等，或像 yfinance 這樣在您電腦上安裝的套件。若 **終端設定 → 資料來源** 沒有列出您要的資料來源，請寫信到 [datasources@retis.ai](mailto:datasources@retis.ai)，寫下資料商名稱與需要的資料（例如市場、商品或欄位）。請不要在信中附上金鑰。

## 會真的下單嗎？

不會。測試版只做模擬交易。

## 合作夥伴說明

- 期貨歷史資料需要富果行情 API 的開發者或進階用戶方案；富果的免費方案沒有期貨歷史 K 線。開發者方案的期貨與選擇權歷史資料目前為體驗開放（至 2026/12/31），在富果的文件上標示為 BETA。
- 「合作推廣連結」是 Retis 與富果的合作推廣，帶有來源標記；使用 Retis Terminal 不需要富果帳號。
- Retis Terminal 由 Retis AI 開發與營運，富果僅提供行情資料 API，不負責 Retis Terminal 的內容與功能。
