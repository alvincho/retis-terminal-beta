# Local OpenStreetMap

Local OpenStreetMap gives Globe View its street map and topographic map: the OpenStreetMap standard map and OpenTopoMap, with no key. It runs on your own computer. **Retis MAS Manager**, which comes with Retis Terminal, installs and runs it.

- **What needs it:** the **Local OpenStreetMap** map layers in Globe View: **Standard**, the street map, down to street level, and **Topographic**, with contour lines and hill shading.
- **No points, no key.** The tiles come straight from OpenStreetMap's and OpenTopoMap's free tile servers to your computer.
- **Only for this computer.** It answers only programs on this computer. Your Retis Terminal finds it by itself: no sign-in, pairing or key.
- **Macs with Apple silicon, and Windows.** It isn't offered on Macs with an Intel chip yet: Retis MAS Manager shows **Not offered for this computer**. Retis Terminal on iPhone or iPad can't run it either; see [Without Local OpenStreetMap](#without-local-openstreetmap).

## Install it

You need a Retis Terminal newer than 0.1.5-beta, one whose Retis MAS Manager lists **Local OpenStreetMap**, and an internet connection. You do this once.

1. Update Retis Terminal: [Mac](install-mac.md), [Windows](install-windows.md). Retis MAS Manager comes with it and starts by itself.
2. In Retis Terminal, choose **New Data Browser** in the sidebar. Under **On this computer** at the top, pick **Retis MAS Manager**, then choose **Open Retis MAS Manager**.
3. On the **Local OpenStreetMap** row, choose **Install**.
4. Read the note below. If you agree, choose **Accept and install**.
5. Wait until the row says **Running**. This usually takes less than a minute.
6. On Windows, if Windows asks about network access, see [Windows firewall](#windows-firewall).

Then [check it](#check-it) and [add a map to Globe View](#add-a-map-to-globe-view).

### The note

Before it installs anything, Retis MAS Manager shows **Accept these terms first:** with this note, headed "OpenStreetMap tiles and their usage policy (2026-11-18)":

> Local OpenStreetMap shows the OpenStreetMap standard map, from the OpenStreetMap Foundation's tile servers, and OpenTopoMap. Map data © OpenStreetMap contributors, under the Open Database License (ODbL); the standard map's tiles are CC BY-SA 2.0 and OpenTopoMap's CC BY-SA 3.0 (https://www.openstreetmap.org/copyright). Retis is not affiliated with or endorsed by the OpenStreetMap Foundation or OpenTopoMap. Their servers are donated, so Local OpenStreetMap follows the Foundation's Tile Usage Policy (https://operations.osmfoundation.org/policies/tiles/): it asks only for the tiles you view, never downloads areas in advance or in bulk, keeps the tiles it fetched in a cache on this computer, and names Retis in every request. Maps that show these tiles credit OpenStreetMap contributors. Installing it downloads the libraries it needs from the Python Package Index (PyPI) to this computer. The tile servers may limit or block access at any time.

### What downloads, and from where

- Retis Terminal already carries Local OpenStreetMap's own small program. Nothing downloads from Retis.
- When you choose **Accept and install**, Retis MAS Manager downloads the libraries it needs from the Python Package Index (PyPI, `pypi.org`): about 15 MB, about 60 MB once installed. **Licences** shows their licences.
- Each file must match the exact version Retis tested. A file that doesn't match isn't installed.
- The files stay on this computer, in Retis Terminal's folder, and only Local OpenStreetMap uses them.
- After that, map tiles come from OpenStreetMap's and OpenTopoMap's tile servers to your computer.

If your network blocks PyPI (for example a company proxy), Install fails. Try another network, or see [Without Local OpenStreetMap](#without-local-openstreetmap).

## Windows firewall

The first time Local OpenStreetMap starts, Windows may show a **Windows Security Alert**. It may name **Python**: Retis MAS Manager runs Local OpenStreetMap with the Python that comes with Retis Terminal.

Tick **Private networks**, untick **Public networks**, and choose **Allow access**. Local OpenStreetMap answers only programs on this computer; Windows asks because of the way your Terminal finds it on this computer.

## Check it

1. Open **Terminal settings** (bottom left) → **Connections**.
2. Under **On this computer**, **Local OpenStreetMap** shows **Running**. If it isn't there yet, choose **Discover**.

## Add a map to Globe View

1. Choose **New Data Browser**, pick **Globe View**, then open it.
2. Choose **+ Layer**.
3. Under **Source**, pick **Local OpenStreetMap · Map tiles · Standard** for the street map, or **Local OpenStreetMap · Map tiles · Topographic** for the topographic map.
4. Choose **Add layer**.

The map appears on the globe, and Globe View keeps the layer in this Terminal. The line under the map credits OpenStreetMap contributors and OpenTopoMap; select it to read the licences.

### How it fetches tiles

- It asks only for the tiles you look at, never areas around them in advance.
- Each tile is kept on this computer for as long as the tile server allows: about a day for the street map, about a week for the topographic map. After that, Local OpenStreetMap asks the server whether the tile changed, and keeps the copy if it didn't.
- If a tile server doesn't answer, Globe View shows the copy kept on this computer.
- The cache holds up to 512 MB of tiles; the ones used least recently make room for new ones.
- The street map goes down to street level, the topographic map a little less far. Closer than that, the map stays at its most detailed level.
- The tile servers are free and ask for light use. If you move around the globe a lot very quickly, new tiles may pause for a moment. Tiles you have already seen keep showing.

## Stop or remove it

Open **Retis MAS Manager** (**New Data Browser** → **On this computer**) and use the **Local OpenStreetMap** row:

- **Stop** stops it until you choose **Start**.
- **Remove** uninstalls it. It keeps its data, including the tiles it kept, unless you untick **Keep data**.

While it's stopped, Globe View can't load new tiles for its Local OpenStreetMap layers. Start it again in Retis MAS Manager, or hide or remove those layers.

## Without Local OpenStreetMap

If you can't run Local OpenStreetMap (on a Mac with an Intel chip, on iPhone or iPad, or on a computer where you can't install it), Globe View still shows its own world map and its other layers. For a street map, add your own key for a map tile provider:

1. Get a key from **Stadia Maps**, **MapTiler** or **Thunderforest**. Their free plans are for non-commercial use; using them in Retis needs that provider's paid plan.
2. Open **Terminal settings → Data sources**, open the provider's row, paste the key in **API key** and choose **Save source**.
3. In Globe View, choose **+ Layer** and pick that provider's **Map tiles**. Until you add a key, the list marks it **needs your key**.

The key stays in your Terminal and goes only to that provider.
