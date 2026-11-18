# 本機 OpenStreetMap

「本機 OpenStreetMap」（Local OpenStreetMap）為「地球檢視」提供街道地圖與地形圖：OpenStreetMap 標準地圖與 OpenTopoMap，不需要金鑰。它在您自己的電腦上執行。Retis Terminal 附帶的 **Retis MAS 管理員** 負責安裝與執行它。

- **哪些功能需要它：** 「地球檢視」裡的 **Local OpenStreetMap** 地圖圖層：**Standard**（街道地圖，可放大到街道）與 **Topographic**（含等高線與地形陰影的地形圖）。
- **不扣點，不需要金鑰。** 圖磚直接從 OpenStreetMap 與 OpenTopoMap 的免費圖磚伺服器送到您的電腦。
- **只供這台電腦使用。** 它只回應這台電腦上的程式。Retis Terminal 會自己找到它，不需要登入、配對或金鑰。
- **Apple 晶片的 Mac 與 Windows。** 使用 Intel 晶片的 Mac 目前還不能安裝：Retis MAS 管理員會顯示 **此電腦無可用版本**。iPhone、iPad 上的 Retis Terminal 也無法執行它，請看 [沒有本機 OpenStreetMap 時](#沒有本機-openstreetmap-時)。

## 安裝

需要比 0.1.5-beta 更新、Retis MAS 管理員裡有 **本機 OpenStreetMap** 的 Retis Terminal，以及網路連線。只需要做一次。

1. 更新 Retis Terminal：[Mac](install-mac.md)、[Windows](install-windows.md)。Retis MAS 管理員隨附在內，會自己啟動。
2. 在 Retis Terminal 側邊欄按 **新增資料瀏覽器**。在最上方的 **這台電腦** 下選 **Retis MAS Manager**，按 **開啟 Retis MAS Manager**，就會開啟「Retis MAS 管理員」。
3. 在 **本機 OpenStreetMap** 那一列按 **安裝**。
4. 閱讀下方的說明。同意的話，按 **同意並安裝**。
5. 等這一列顯示 **執行中**。通常不到一分鐘。
6. 在 Windows 上，如果 Windows 詢問網路存取權限，請看 [Windows 防火牆](#windows-防火牆)。

接著 [確認安裝結果](#確認安裝結果)，再 [在地球檢視加入地圖](#在地球檢視加入地圖)。

### 安裝前的說明

Retis MAS 管理員在安裝前會顯示 **請先同意以下條款：**，以及標題為「OpenStreetMap 圖磚與其使用政策（2026-11-18）」的這段說明：

> 本機 OpenStreetMap 顯示 OpenStreetMap 基金會圖磚伺服器提供的 OpenStreetMap 標準地圖，以及 OpenTopoMap 地形圖。地圖資料 © OpenStreetMap 貢獻者，依開放資料庫授權（ODbL）提供；標準地圖圖磚採 CC BY-SA 2.0 授權，OpenTopoMap 圖磚採 CC BY-SA 3.0 授權（https://www.openstreetmap.org/copyright）。Retis 與 OpenStreetMap 基金會及 OpenTopoMap 無關，也未經其認可。這些伺服器由捐助維持，因此本機 OpenStreetMap 遵守該基金會的圖磚使用政策（https://operations.osmfoundation.org/policies/tiles/）：只取得您正在檢視的圖磚，從不預先或大量下載區域，把取得的圖磚快取在這台電腦，並在每個請求中表明來自 Retis。顯示這些圖磚的地圖都會標示 OpenStreetMap 貢獻者。安裝時會從 Python 套件索引（PyPI）把所需程式庫下載到這台電腦。圖磚伺服器可能隨時限制或封鎖存取。

### 下載什麼、從哪裡下載

- Retis Terminal 已附帶本機 OpenStreetMap 自己的小程式，不會從 Retis 下載任何東西。
- 按 **同意並安裝** 後，Retis MAS 管理員會從 Python 套件索引（PyPI，`pypi.org`）下載所需的程式庫：約 15 MB，安裝後約 60 MB。按 **授權條款** 可查看它們的授權。
- 每個檔案都必須和 Retis 測試過的版本完全相同，不符的檔案不會安裝。
- 這些檔案存在這台電腦上 Retis Terminal 的資料夾，只有本機 OpenStreetMap 會使用。
- 之後，地圖圖磚從 OpenStreetMap 與 OpenTopoMap 的圖磚伺服器送到您的電腦。

如果您的網路擋住 PyPI（例如公司的 Proxy），安裝會失敗。請換一個網路，或看 [沒有本機 OpenStreetMap 時](#沒有本機-openstreetmap-時)。

## Windows 防火牆

本機 OpenStreetMap 第一次啟動時，Windows 可能會顯示「Windows 安全性警訊」，上面可能寫著 **Python**：Retis MAS 管理員用 Retis Terminal 附帶的 Python 執行本機 OpenStreetMap。

請勾選 **私人網路**、取消勾選 **公用網路**，再按 **允許存取**。本機 OpenStreetMap 只回應這台電腦上的程式；Windows 會詢問，是因為 Retis Terminal 在這台電腦上尋找它的方式。

## 確認安裝結果

1. 開啟左下角的 **終端設定** → **連線**。
2. 在 **這台電腦** 下，**Local OpenStreetMap** 顯示 **執行中**。如果還沒出現，按 **探索**。

## 在地球檢視加入地圖

1. 按 **新增資料瀏覽器**，選 **地球檢視**，再開啟它。
2. 按 **+ 圖層**。
3. 在 **資料來源** 選 **Local OpenStreetMap · Map tiles · Standard**（街道地圖），或 **Local OpenStreetMap · Map tiles · Topographic**（地形圖）。
4. 按 **加入圖層**。

地圖會出現在地球上，「地球檢視」會把這個圖層保存在這個 Terminal。地圖下方那一行會標示 OpenStreetMap 貢獻者與 OpenTopoMap；點選它可查看授權。

### 它如何取得圖磚

- 只取得您正在看的圖磚，從不預先下載周圍的區域。
- 每塊圖磚在圖磚伺服器允許的期間內保存在這台電腦：街道地圖約一天，地形圖約一週。期滿後，本機 OpenStreetMap 會詢問伺服器圖磚是否有變；沒變就繼續使用保存的那份。
- 圖磚伺服器沒有回應時，「地球檢視」會顯示這台電腦上保存的那份。
- 快取最多保存 512 MB 的圖磚；最久沒用的會讓位給新的圖磚。
- 街道地圖可放大到街道，地形圖可放大的程度稍少。再放大時，地圖會停在最詳細的那一層。
- 圖磚伺服器是免費的，請大家適量使用。如果您在短時間內大量移動地球，新的圖磚可能會暫停一下；已經看過的圖磚仍會顯示。

## 停止或移除

開啟 **Retis MAS 管理員**（**新增資料瀏覽器** → **這台電腦**），在 **本機 OpenStreetMap** 那一列：

- **停止**：停止執行，直到您按 **啟動**。
- **移除**：解除安裝。除非您取消勾選 **保留資料**，它的資料（包括保存的圖磚）會保留。

停止期間，「地球檢視」無法為本機 OpenStreetMap 圖層載入新的圖磚。請在 Retis MAS 管理員重新啟動它，或隱藏、移除這些圖層。

## 沒有本機 OpenStreetMap 時

如果您無法執行本機 OpenStreetMap（在 Intel 晶片的 Mac、iPhone 或 iPad 上，或在無法安裝程式的電腦上），「地球檢視」仍會顯示它自己的世界地圖與其他圖層。若要街道地圖，請為地圖圖磚供應商加入您自己的金鑰：

1. 向 **Stadia Maps**、**MapTiler** 或 **Thunderforest** 取得金鑰。這三家的免費方案限非商業使用，在 Retis 使用需要該供應商的付費方案。
2. 開啟 **終端設定 → 資料來源**，展開該供應商那一列，在 **API 金鑰** 貼上金鑰，按 **儲存來源設定**。
3. 在「地球檢視」按 **+ 圖層**，選該供應商的 **Map tiles**。加入金鑰之前，清單上會標示 **需要您的金鑰**。

金鑰只存在您的 Terminal，只會直接送到該供應商。
