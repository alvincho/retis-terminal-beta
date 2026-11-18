# FAQ

## Mac says it can't verify the developer

Open **System Settings → Privacy & Security**, find Retis Terminal near the bottom and choose **Open Anyway**. Only the first time.

## Windows protected your PC

Choose **More info** → **Run anyway**. Only the first time.

## No confirmation email

Check your spam folder. If it still hasn't arrived after a few minutes, choose **Send the confirmation email again** on Retis Terminal's sign-in screen.

## I closed the browser tab

Choose **Open** from the Retis Terminal icon in the menu bar (Mac) or system tray (Windows).

## A tab says "This Terminal has stopped."

Retis Terminal has quit. Open it again from Applications (Mac) or the Start menu (Windows); it opens in a new tab with your workspace and strategies.

## An old tab says "Retis Terminal restarted and opened this window in a new tab."

Carry on in the new tab and close the old one.

## Where is my data?

Workspaces, layouts and kept results stay on your computer. tw-001 keeps only your account, points and access records. See [Privacy](../../legal/PRIVACY.en.md).

## Where do the prices come from?

- **Taiwan stock quotes** in Taiwan Semiconductor Watch come from TWSE and TPEx through tw-001. Free.
- **Price history** (charts, backtests, paper trading), **news** and **company data** come from [Local yfinance](local-yfinance.md), which runs on your computer, or from another source you add your own key for.
- **Street maps** in Globe View come from [Local OpenStreetMap](local-openstreetmap.md), which also runs on your computer, or from a map tile provider you add your own key for.

## Do I need to enter a broker or data-provider password?

No. The beta paper trades on public data. If you have a data provider's API key, add it under **Terminal settings → Data sources**. Many sources are already on the list, such as Fugle ([Fugle MarketData API plans](https://developer.fugle.tw/docs/pricing/?utm_source=attas&utm_medium=docs&utm_campaign=attas_pricing&utm_content=faq_en), partner link), and you can ask for one that isn't (see the next question). The key stays on your computer and goes only to that provider.

## The data source I want isn't listed

You can request any data source: a provider with an API, such as Bloomberg, LSEG, Alpha Vantage or FinMind, or a package installed on your computer like yfinance. If the one you want isn't listed under **Terminal settings → Data sources**, email [datasources@retis.ai](mailto:datasources@retis.ai) with the provider's name and the data you need (for example markets, instruments or fields). Don't include your key in the email.

## Does it place real orders?

No. The beta paper trades only.

## Partner notes

- Futures history needs the Developer or Advanced plan of the Fugle MarketData API; Fugle's free plan has no futures history candles. Futures and options history on the Developer plan is a trial until 2026/12/31 and is marked BETA in Fugle's documentation.
- Fugle links marked "partner link" are promotional links from the Retis–Fugle partnership and carry a source tag; Retis Terminal does not need a Fugle account.
- Retis Terminal is developed and operated by Retis AI; Fugle only provides the market data API and is not responsible for Retis Terminal's content or features.
