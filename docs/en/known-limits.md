# Known limits

Things the beta doesn't do yet, or does differently from what you might expect. No need to report these again, but please tell us if one gets in your way.

- **No price history without Local yfinance or a key.** Charts, backtests and paper trading need [Local yfinance](local-yfinance.md) on your computer, or your own key for a price-history source such as Fugle. News needs Local yfinance.
- **Local yfinance runs only on Macs with Apple silicon and on Windows.** Macs with an Intel chip, Retis Terminal on iPhone or iPad, and the Terminal on tw-001's website can't run it. Use your own key there.
- **Installing Local yfinance needs PyPI.** Install downloads its libraries from PyPI; behind a proxy that blocks it, or offline, Install fails.
- **Yahoo may limit or change access.** Local yfinance can stop working when Yahoo changes its site. A fix comes with a new Retis Terminal version.
- **Long backtests may not finish.** A 12-month pairs backtest can stay on "Fetching history and replaying calculations…". Start with six months or less.
- **Download report saves no file.** Expand the content under the report and copy it instead.
- **Paper orders are manual.** Signals light up; you place and close the paper orders yourself.
- **Both stocks need a price at the same time.** Markets with different trading hours (for example a Taiwan stock paired with a US stock) may give no spread.
- **Data may be delayed or incomplete.** tw-001 is a test site: data may be delayed, missing, or reset after notice.
- **One site at a time.** Retis Terminal connects to tw-001 by default.
