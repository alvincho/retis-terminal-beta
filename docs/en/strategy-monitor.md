# Strategy Monitor basics

Build a strategy, watch its signals, backtest it and practise with paper trades. **Paper trading only: no real orders.**

**Pairs trading** watches two stocks that usually move together, such as TSMC (2330.TW) and UMC (2303.TW). When their spread is unusually small (a low **z-score**) the strategy enters; when it returns to normal it exits. **Entry/Exit z-score** set those levels, and the **Calculation period** is how many recent prices the average uses.

- **New strategy:** pick a template, two stocks and a price source, then **Create**.
- **Refresh:** latest prices and signals (1 point).
- **Backtest:** replay past prices; start with six months or less.
- **Start (paper trading):** updates automatically, 10 points a step, about every 2 minutes by default (about 300 points an hour). **Stop** when you aren't watching (free).

More help is behind **How to use** in Strategy Monitor. This is for learning and research, not investment advice.
