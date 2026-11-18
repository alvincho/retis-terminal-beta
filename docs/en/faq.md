# FAQ

## Mac says it can't verify the developer

Open **System Settings → Privacy & Security**, find Retis Terminal near the bottom and choose **Open Anyway**. Only the first time.

## Windows protected your PC

Choose **More info** → **Run anyway**. Only the first time.

## No confirmation email

Check your spam folder. If it still hasn't arrived after a few minutes, choose **Send the confirmation email again** on Retis Terminal's sign-in screen.

## I closed the browser tab

Choose **Open** from the Retis Terminal icon in the menu bar (Mac) or system tray (Windows).

## Where is my data?

Workspaces, layouts and kept results stay on your computer. tw-001 keeps only your account, points and access records. See [Privacy](../../legal/PRIVACY.en.md).

## Where do the prices come from?

- **Taiwan stock quotes** in Taiwan Semiconductor Watch come from TWSE and TPEx through tw-001. Free.
- **Price history** (charts, backtests, paper trading), **news** and **company data** come from [Local yfinance](local-yfinance.md), which runs on your computer, or from a source you add your own key for, such as Fugle or FinMind. News comes only from Local yfinance.

## Why doesn't tw-001 provide yfinance any more?

Yahoo provides its Finance data for personal use, and Retis does not provide or redistribute it. From 0.1.5-beta, yfinance runs on your own computer as [Local yfinance](local-yfinance.md), and you read the data for yourself. Install it once in Retis MAS Manager. Strategies you kept switch to it by themselves.

## Do I need to enter a broker or data-provider password?

No. The beta paper trades on public data. If you have a Fugle or FinMind API key, you can add it under **Terminal settings → Data sources**; the key stays on your computer and goes only to that provider. No Fugle key yet? See [Fugle MarketData API plans](https://developer.fugle.tw/docs/pricing/?utm_source=attas&utm_medium=docs&utm_campaign=attas_pricing) (partner link).

## The data source I want isn't listed

You can request any data source. If the one you want isn't listed under **Terminal settings → Data sources**, email [datasources@retis.ai](mailto:datasources@retis.ai) with the provider's name and the data you need (for example markets, instruments or fields). Don't include your key in the email.

## Does it place real orders?

No. The beta paper trades only.
