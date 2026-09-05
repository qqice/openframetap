"""Install only the current user's desktop/menu launchers; no system autostart."""
from datetime import datetime
from pathlib import Path
import shutil
import subprocess
import os

os.environ.setdefault('XDG_RUNTIME_DIR',f'/run/user/{os.getuid()}')
os.environ.setdefault('DBUS_SESSION_BUS_ADDRESS',f'unix:path=/run/user/{os.getuid()}/bus')

root=Path(__file__).resolve().parent.parent
launcher=root/'scripts/board-app.sh'
launcher.chmod(0o755)
desktop=subprocess.run(['xdg-user-dir','DESKTOP'],capture_output=True,text=True,check=True).stdout.strip()
entry='\n'.join(['[Desktop Entry]','Type=Application','Name=OpenFrameTap','Comment=Pocket 3 常规模式监看与控制',
                f'Exec="{launcher}" start',f'Path={root}','Icon=camera-video','Terminal=false',
                'StartupNotify=false','Categories=AudioVideo;Video;',''])
for folder in (Path.home()/'.local/share/applications',Path(desktop)):
    folder.mkdir(parents=True,exist_ok=True)
    target=folder/'openframetap.desktop'
    if target.exists() and target.read_text()!=entry:
        shutil.copy2(target,target.with_suffix('.desktop.backup-'+datetime.now().strftime('%Y%m%d%H%M%S')))
    target.write_text(entry)
    target.chmod(0o755)
    subprocess.run(['gio','set',str(target),'metadata::trusted','true'],capture_output=True)
    print(target)
