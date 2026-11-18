# Strategy Monitor basics

Build a strategy, watch its signals, backtest it and practise with paper trades. **Paper trading only: no real orders.**

For how each strategy template works, its parameters and worked backtests, and the full pairs trading settings, see the [Strategy Monitor handbook (PDF, in Chinese)](https://github.com/alvincho/retis-terminal-beta/releases/download/v0.1.5-beta/Retis-Terminal-strategy-monitor-handbook.zh-TW.pdf).

**Pairs trading** watches two stocks that usually move together, such as TSMC (2330.TW) and UMC (2303.TW). When their spread is unusually small (a low **z-score**) the strategy enters; when it returns to normal it exits. **Entry/Exit z-score** set those levels, and the **Calculation period** is how many recent prices the average uses.

- **New strategy:** pick a template, two stocks and a price source, then **Create**. The price source is usually [Local yfinance](local-yfinance.md), which runs on your computer (free); with a Fugle key you can pick Fugle instead, which also covers index and stock futures such as TAIFEX contracts (a stock-versus-futures pair defaults the price field to close; keep it; [Fugle MarketData API plans](https://developer.fugle.tw/docs/pricing/?utm_source=attas&utm_medium=docs&utm_campaign=attas_pricing&utm_content=strategy_monitor_en), partner link).
- **Refresh:** latest prices and signals (free).
- **Backtest:** replay past prices; start with six months or less.
- **Delete:** pick a strategy you no longer need in the strategy list and choose **Delete strategy**; for a saved backtest, pick it in the Backtest tab and choose **Delete saved backtest** next to **Download report**. Both ask first and can't be undone. Each Strategy Monitor keeps about 40 to 50 strategies with their backtests, and up to 20 saved backtests per strategy; a note appears when it's nearly full.
- **Start (paper trading):** updates automatically, 10 points a step, about every 2 minutes by default (about 300 points an hour). **Stop** when you aren't watching (free).

**Price source.** Price history comes from Local yfinance on your computer, or from a source you added your own key for. With neither, **Try the example** and **New strategy** say: install Local yfinance in Retis MAS Manager, or add your own key for a price-history source (for example Fugle) in **Terminal settings → Data sources**.

More help is behind **How to use** in Strategy Monitor. This is for learning and research, not investment advice.

## Partner notes

- Futures history needs the Developer or Advanced plan of the Fugle MarketData API; Fugle's free plan has no futures history candles. Futures and options history on the Developer plan is a trial until 2026/12/31 and is marked BETA in Fugle's documentation.
- Fugle links marked "partner link" are promotional links from the Retis–Fugle partnership and carry a source tag; Retis Terminal does not need a Fugle account.
- Retis Terminal is developed and operated by Retis AI; Fugle only provides the market data API and is not responsible for Retis Terminal's content or features.
