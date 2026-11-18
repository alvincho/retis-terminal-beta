# Strategy Monitor basics

Build a strategy, watch its signals, backtest it and practise with paper trades. **Paper trading only: no real orders.**

**Pairs trading** watches two stocks that usually move together, such as TSMC (2330.TW) and UMC (2303.TW). When their spread is unusually small (a low **z-score**) the strategy enters; when it returns to normal it exits. **Entry/Exit z-score** set those levels, and the **Calculation period** is how many recent prices the average uses.

- **New strategy:** pick a template, two stocks and a price source, then **Create**. The price source is usually [Local yfinance](local-yfinance.md), which runs on your computer (free); with a Fugle key you can pick Fugle instead, which also covers futures such as TAIFEX contracts ([Fugle MarketData API plans](https://developer.fugle.tw/docs/pricing/?utm_source=attas&utm_medium=docs&utm_campaign=attas_pricing), partner link).
- **Refresh:** latest prices and signals (free).
- **Backtest:** replay past prices; start with six months or less.
- **Start (paper trading):** updates automatically, 10 points a step, about every 2 minutes by default (about 300 points an hour). **Stop** when you aren't watching (free).

**Price source.** Price history comes from Local yfinance on your computer, or from a source you added your own key for. With neither, **Try the example** and **New strategy** say: install Local yfinance in Retis MAS Manager, or add your own key for a price-history source (for example Fugle) in **Terminal settings → Data sources**.

**Strategies kept from before 0.1.5-beta** read tw-001's yfinance, which is gone. Once your Terminal lists Local yfinance, each one switches to it by itself the next time it runs or reads prices, and shows a short note. As when you apply new settings, open paper positions are squared at the latest price, or the paper simulation is cleared if its prices are out of date. A strategy that was running keeps running on Local yfinance.

More help is behind **How to use** in Strategy Monitor. This is for learning and research, not investment advice.
