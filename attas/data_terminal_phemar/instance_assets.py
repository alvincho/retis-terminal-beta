"""Capture one instance's UI without changing assets of running hosts."""
from pathlib import Path
import shutil
import uuid

from .contracts import WEB


def capture_ui(directory, *, source=WEB):
    directory, source = Path(directory).resolve(), Path(source).resolve()
    if directory.is_relative_to(source):raise ValueError('Keep installed UI releases outside the source assets.')
    if directory.exists():raise ValueError('An installed UI release cannot be overwritten.')
    directory.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging=directory.parent/('.capture-'+uuid.uuid4().hex)
    try:
        shutil.copytree(source,staging,ignore=shutil.ignore_patterns('__pycache__','*.test.js'))
        # Captured releases may be read-only. Only our new private copy is
        # writable while the per-instance adapters are installed.
        for item in staging.rglob('*'):
            if item.is_file():item.chmod(item.stat().st_mode | 0o200)
        # Existing imports keep their name. Only this private release changes;
        # hosts watching the source tree see no global revision change.
        (staging/'phema-reload.js').write_bytes((source/'phema-reload-manual.mjs').read_bytes())
        frame=source/'phema-frame-refresh.mjs'
        if frame.exists():(staging/'phema-frame-refresh.js').write_bytes(frame.read_bytes())
        app=staging/'app.js'
        if app.exists():
            code=app.read_text()
            # A publication check applies only to the active Phema in this
            # document. Other windows and hidden workspaces are left intact.
            code=code.replace("const browsers=ui.workspaces.flatMap(w=>w.browsers),result=await refreshSavedLayouts(browsers,api);",
                              "const browsers=ui.browsers.filter(b=>b.id===ui.activeId),result=await refreshSavedLayouts(browsers,api);")
            app.write_text(code)
        from .browser_reload_assets import install_browser_reload
        install_browser_reload(staging)
        from phemacast.appearance_candidate.assets import install as install_appearance
        install_appearance(staging, panel_adapter=Path(__file__).with_name('ui_candidate')/'phema-browser-appearance.mjs')
        staging.rename(directory)
    finally:
        if staging.exists():shutil.rmtree(staging)
    return directory
