"""Install bounded optional appearance assets into a fresh captured release."""
from pathlib import Path

NAMES = {'appearance.mjs':'appearance.js','appearance.style':'appearance.css',
         'composition-renderers.mjs':'composition-renderers.js',
         'graph.mjs':'graph.js','graph.style':'graph.css',
         'graph-renderer-adapter.mjs':'graph-renderer-adapter.js','graph-position-bridge.mjs':'graph-position-bridge.js',
         'owner-busy.mjs':'owner-busy.js','owner-busy.style':'owner-busy.css'}

def install(directory, *, panel_adapter):
    directory=Path(directory);source=Path(__file__).parent
    for original,installed in NAMES.items():
        target=directory/installed
        if target.exists():target.chmod(target.stat().st_mode|0o200)
        target.write_bytes((source/original).read_bytes())
    target=directory/'phema-browser.js'
    if target.exists():target.chmod(target.stat().st_mode|0o200)
    target.write_bytes(Path(panel_adapter).read_bytes())
    adapter=Path(panel_adapter).parent/'graph-client.mjs'
    if adapter.is_file():
        (directory/'graph-client.js').write_bytes(adapter.read_bytes())
        connected=directory/'connected.js'
        connected.chmod(connected.stat().st_mode|0o200)
        text=connected.read_text()
        if "import './graph-client.js';" not in text: connected.write_text("import './graph-client.js';\n"+text)
    index=directory/'index.html'
    if index.exists():
        index.chmod(index.stat().st_mode|0o200);html=index.read_text()
        for name in ('appearance.css','owner-busy.css','graph.css'):
            if 'href="'+name+'"' not in html:html=html.replace('</head>','<link rel="stylesheet" href="'+name+'"></head>')
        index.write_text(html)
