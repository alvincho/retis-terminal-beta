# 本機 yfinance

「本機 yfinance」（Local yfinance）為 Retis Terminal 提供 Yahoo 財經的歷史價格、新聞與公司資料。它在您自己的電腦上執行，不在 tw-001 上。Retis Terminal 0.1.5-beta 附帶的 **Retis MAS 管理員** 負責安裝與執行它。

- **哪些功能需要它：** 「台灣半導體觀察」的走勢圖、新聞與公司資料，以及「策略監看」的歷史價格（試用範例、回測與模擬交易）。
- **資料不扣點。** 本機 yfinance 在您的電腦上讀取資料。回測與模擬交易的每一步仍照常扣點。
- **只供這台電腦使用。** 它只回應這台電腦上的程式。Retis Terminal 會自己找到它，不需要登入、配對或金鑰。
- **Apple 晶片的 Mac 與 Windows。** 使用 Intel 晶片的 Mac 目前還不能安裝：Retis MAS 管理員會顯示 **此電腦無可用版本**。iPhone、iPad 上的 Retis Terminal，以及 tw-001 網站上的 Terminal 也無法執行它，請看 [沒有本機 yfinance 時](#沒有本機-yfinance-時)。

## 安裝

需要 Retis Terminal 0.1.5-beta 以上版本與網路連線。只需要做一次。

1. 把 Retis Terminal 更新到 0.1.5-beta 以上：[Mac](install-mac.md)、[Windows](install-windows.md)。Retis MAS 管理員隨附在內，會自己啟動。
2. 在 Retis Terminal 側邊欄按 **新增資料瀏覽器**。在最上方的 **這台電腦** 下選 **Retis MAS Manager**，按 **開啟 Retis MAS Manager**，就會開啟「Retis MAS 管理員」。
3. 在 **本機 yfinance** 那一列按 **安裝**。
4. 閱讀下方的說明。同意的話，按 **同意並安裝**。
5. 等這一列顯示 **執行中**。下載約需幾分鐘，視您的網路速度而定。
6. 在 Windows 上，如果 Windows 詢問網路存取權限，請看 [Windows 防火牆](#windows-防火牆)。

接著 [確認安裝結果](#確認安裝結果)。

### 安裝前的說明

Retis MAS 管理員在安裝前會顯示 **請先同意以下條款：**，以及標題為「Yahoo 財經資料與 yfinance（2026-11-18）」的這段說明：

> 本機 yfinance 使用開源工具 yfinance（Apache-2.0 授權），與 Yahoo 無關，也未經 Yahoo 認可。它讀取 Yahoo 財經公開的資料，Yahoo 提供這些資料供個人使用。安裝時會從 Python 套件索引（PyPI）把 yfinance 及其所需程式庫下載到這台電腦。Retis 不提供也不轉發 Yahoo 的資料；您的使用須遵守 Yahoo 的條款，Yahoo 可能隨時限制或變更存取方式。

### 下載什麼、從哪裡下載

- Retis Terminal 已附帶本機 yfinance 自己的小程式，不會從 Retis 下載任何東西。
- 按 **同意並安裝** 後，Retis MAS 管理員會從 Python 套件索引（PyPI，`pypi.org`）下載 yfinance 及其所需的程式庫，例如 pandas、numpy、lxml 與 curl-cffi：約 50 MB，安裝後約 200 MB。按 **授權條款** 可查看它們的授權。
- 每個檔案都必須和 Retis 測試過的版本完全相同，不符的檔案不會安裝。
- 這些檔案存在這台電腦上 Retis Terminal 的資料夾，只有本機 yfinance 會使用。
- 之後，本機 yfinance 從您的電腦讀取 Yahoo 財經的資料。

如果您的網路擋住 PyPI（例如公司的 Proxy），安裝會失敗。請換一個網路，或改用 [自己的金鑰](#沒有本機-yfinance-時)。

## Windows 防火牆

本機 yfinance 第一次啟動時，Windows 可能會顯示「Windows 安全性警訊」，上面可能寫著 **Python**：Retis MAS 管理員用 Retis Terminal 附帶的 Python 執行本機 yfinance。

請勾選 **私人網路**、取消勾選 **公用網路**，再按 **允許存取**。本機 yfinance 只回應這台電腦上的程式；Windows 會詢問，是因為 Retis Terminal 在這台電腦上尋找它的方式。

## 確認安裝結果

1. 開啟左下角的 **終端設定** → **連線**。
2. 在 **這台電腦** 下，**Local yfinance** 顯示 **執行中**。如果還沒出現，按 **探索**。

這裡也會列出 **Retis MAS Manager**。在「策略監看」按 **試用範例**，會自動使用本機 yfinance 的價格。

## 停止或移除

開啟 **Retis MAS 管理員**（**新增資料瀏覽器** → **這台電腦**），在 **本機 yfinance** 那一列：

- **停止**：停止執行，直到您按 **啟動**。
- **移除**：解除安裝。除非您取消勾選 **保留資料**，它的資料會保留。

停止期間，使用它的面板與策略會顯示您的本機資料來源沒有回應。請在 Retis MAS 管理員重新啟動它，或改選其他來源。

## 沒有本機 yfinance 時

如果您無法執行本機 yfinance（在 Intel 晶片的 Mac、iPhone 或 iPad 上、在 tw-001 網站上的 Terminal，或在無法安裝程式的電腦上），請為提供歷史價格的資料來源加入您自己的金鑰，例如富果（Fugle）：

1. 向資料商取得金鑰。富果請參考 [富果行情 API 方案](https://developer.fugle.tw/docs/pricing/?utm_source=attas&utm_medium=docs&utm_campaign=attas_pricing)（合作推廣連結）。
2. 開啟 **終端設定 → 資料來源**，展開該資料商那一列（例如 **FugleDataSource**），在 **API 金鑰** 貼上金鑰，按 **儲存來源設定**。
3. 在「策略監看」把它選為 **價格來源**。

金鑰只存在您的 Terminal，只會直接送到該資料商。富果提供台灣股票與期貨，但沒有新聞：「台灣半導體觀察」的新聞需要本機 yfinance。

兩者都沒有時，「台灣半導體觀察」仍會顯示台灣股票的報價，資料來自證交所與櫃買中心。
