# Local yfinance

Local yfinance gives Retis Terminal price history, news and company data from Yahoo Finance. It runs on your own computer. **Retis MAS Manager**, which comes with Retis Terminal, installs and runs it.

- **What needs it:** charts, news and company data in Taiwan Semiconductor Watch, and price history in Strategy Monitor (the example, backtests and paper trading).
- **No points for the data.** Local yfinance reads the data on your computer. Backtests and paper-trading steps still use points.
- **Only for this computer.** It answers only programs on this computer. Your Retis Terminal finds it by itself: no sign-in, pairing or key.
- **Macs with Apple silicon, and Windows.** It isn't offered on Macs with an Intel chip yet: Retis MAS Manager shows **Not offered for this computer**. Retis Terminal on iPhone or iPad can't run it either; see [Without Local yfinance](#without-local-yfinance).

## Install it

You need Retis Terminal 0.1.5-beta or later and an internet connection. You do this once.

1. Update Retis Terminal to 0.1.5-beta or later: [Mac](install-mac.md), [Windows](install-windows.md). Retis MAS Manager comes with it and starts by itself.
2. In Retis Terminal, choose **New Data Browser** in the sidebar. Under **On this computer** at the top, pick **Retis MAS Manager**, then choose **Open Retis MAS Manager**.
3. On the **Local yfinance** row, choose **Install**.
4. Read the note below. If you agree, choose **Accept and install**.
5. Wait until the row says **Running**. The download takes a few minutes, depending on your connection.
6. On Windows, if Windows asks about network access, see [Windows firewall](#windows-firewall).

Then [check it](#check-it).

### The note

Before it installs anything, Retis MAS Manager shows **Accept these terms first:** with this note, headed "Yahoo Finance data and yfinance (2026-11-18)":

> Local yfinance uses yfinance, an open-source tool (Apache-2.0) that is not affiliated with or endorsed by Yahoo. It reads Yahoo Finance's publicly available data, which Yahoo provides for personal use. Installing it downloads yfinance and the libraries it needs from the Python Package Index (PyPI) to this computer. Retis does not provide or redistribute Yahoo data; your use is subject to Yahoo's terms, and Yahoo may limit or change access at any time.

### What downloads, and from where

- Retis Terminal already carries Local yfinance's own small program. Nothing downloads from Retis.
- When you choose **Accept and install**, Retis MAS Manager downloads yfinance and the libraries it needs, such as pandas, numpy, lxml and curl-cffi, from the Python Package Index (PyPI, `pypi.org`): about 50 MB, about 200 MB once installed. **Licences** shows their licences.
- Each file must match the exact version Retis tested. A file that doesn't match isn't installed.
- The files stay on this computer, in Retis Terminal's folder, and only Local yfinance uses them.
- After that, Local yfinance reads Yahoo Finance from your computer.

If your network blocks PyPI (for example a company proxy), Install fails. Try another network, or use [your own key](#without-local-yfinance).

## Windows firewall

The first time Retis Terminal opens, or the first time Local yfinance starts, Windows may show a **Windows Security Alert** naming **Python**. It's the Python that comes with Retis Terminal (in `%LOCALAPPDATA%\Programs\Retis Terminal\python`): Retis Terminal, Retis MAS Manager and Local yfinance all run with it, so Windows usually asks only once.

Tick **Private networks**, untick **Public networks**, and choose **Allow access**. Local yfinance answers only programs on this computer; Windows asks because of the way your Terminal finds it on this computer.

## Check it

1. Open **Terminal settings** (bottom left) → **Connections**.
2. Under **On this computer**, **Local yfinance** shows **Running**. If it isn't there yet, choose **Discover**.

**Retis MAS Manager** is listed there too. In Strategy Monitor, **Try the example** now reads prices from Local yfinance by itself.

## Stop or remove it

Open **Retis MAS Manager** (**New Data Browser** → **On this computer**) and use the **Local yfinance** row:

- **Stop** stops it until you choose **Start**.
- **Remove** uninstalls it. It keeps its data unless you untick **Keep data**. With **Keep data** ticked, installing it again doesn't ask you to accept the same note again.
- **Logs** shows when it started and one line for each data request. A line with `curl: (16)` is a network error between this computer and Yahoo. Local yfinance tries such a request again by itself, so there's nothing to do. If every request fails with it, a program that inspects web traffic, such as an antivirus's web protection or a company proxy, may be in the way: try another network.

While it's stopped, panels and strategies that use it say your local data source is not responding. Start it again in Retis MAS Manager, or choose another source.

## Without Local yfinance

If you can't run Local yfinance (on a Mac with an Intel chip, on iPhone or iPad, or on a computer where you can't install it), add your own key for a source that gives price history, such as Fugle:

1. Get a key from the provider. For Fugle, see [Fugle MarketData API plans](https://developer.fugle.tw/docs/pricing/?utm_source=attas&utm_medium=docs&utm_campaign=attas_pricing&utm_content=local_yfinance_en) (partner link).
2. Open **Terminal settings → Data sources**, open the provider's row (for example **FugleDataSource**), paste the key in **API key** and choose **Save source**.
3. In Strategy Monitor, choose that source as the **Price source**.

The key stays in your Terminal and goes only to that provider. Fugle gives Taiwan stocks and futures, but no news; news can come from Local yfinance or any other source that provides news.

Without either, Taiwan Semiconductor Watch still shows quotes for Taiwan stocks, from TWSE and TPEx.

## Partner notes

- Futures history needs the Developer or Advanced plan of the Fugle MarketData API; Fugle's free plan has no futures history candles. Futures and options history on the Developer plan is a trial until 2026/12/31 and is marked BETA in Fugle's documentation.
- Fugle links marked "partner link" are promotional links from the Retis–Fugle partnership and carry a source tag; Retis Terminal does not need a Fugle account.
- Retis Terminal is developed and operated by Retis AI; Fugle only provides the market data API and is not responsible for Retis Terminal's content or features.
