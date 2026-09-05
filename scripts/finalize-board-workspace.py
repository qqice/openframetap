"""One-time non-destructive merge and authority handoff on the owner's board."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import venv

old=Path('/home/qqice/openframetap-runtime')
new=Path('/home/qqice/Workspace/openframetap')
assert Path.home()==Path('/home/qqice')
assert new.resolve()==new and old.resolve()==old and not old.is_symlink()
assert (new/'.git').is_dir() and (new/'src/openframetap').is_dir()
assert not (old/'runtime/normal-network.json').exists(), 'pending network rollback'
manifest=new/'artifacts/migration'
manifest.mkdir(parents=True,exist_ok=True)
# Do not lose Windows-only runtime files or differing versions of raw evidence.
if (new/'runtime').exists() and not (manifest/'windows-runtime').exists():
    (new/'runtime').rename(manifest/'windows-runtime')
subprocess.run(['rsync','-a','--checksum','--backup','--backup-dir='+str(manifest/'windows-conflicts'),
                str(old/'artifacts')+'/',str(new/'artifacts')+'/'],check=True)
subprocess.run(['rsync','-a',str(old/'runtime')+'/',str(new/'runtime')+'/'],check=True)

# Reuse identical Linux dependency versions offline. Recreate activation/scripts
# for the new prefix rather than copying a Windows environment or downloading.
if not (new/'.venv').exists():
    shutil.copytree(old/'.venv',new/'.venv',symlinks=True)
venv.EnvBuilder(system_site_packages=True,with_pip=True,symlinks=True).create(new/'.venv')
for folder in (new/'.venv/bin',new/'.venv/lib/python3.12/site-packages'):
    for p in folder.iterdir():
        if p.is_file() and not p.is_symlink() and (folder.name=='bin' or p.suffix=='.pth'):
            try:text=p.read_text()
            except (UnicodeError,OSError):continue
            if str(old) in text:p.write_text(text.replace(str(old),str(new)))
subprocess.run([str(new/'.venv/bin/python'),'-m','pip','install','--no-deps','--no-build-isolation','-e',str(new)],check=True)
subprocess.run(['git','fsck','--no-progress'],cwd=new,check=True)
subprocess.run([str(new/'.venv/bin/python'),'-m','pytest','-q'],cwd=new,check=True)
subprocess.run([str(new/'.venv/bin/python'),str(new/'scripts/install-desktop.py')],cwd=new,check=True)
subprocess.run(['git','diff','--exit-code'],cwd=new,check=True)

# Existing commands remain a compatibility path, never an independent source.
backup=Path('/home/qqice/openframetap-runtime-pre-migration')
assert not backup.exists()
old.rename(backup)
old.symlink_to(new,target_is_directory=True)
(new/'runtime/board-canonical').write_text('Canonical source is /home/qqice/Workspace/openframetap\n')
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=new,text=True).strip()
files=[]
for p in sorted(new.rglob('*')):
    if not p.is_file() or p.is_symlink() or '.venv' in p.parts or '.git' in p.parts or '__pycache__' in p.parts:continue
    if p==manifest/'handoff.json' or p==manifest/'workspace-sha256.txt':continue
    digest=hashlib.sha256()
    with p.open('rb') as stream:
        while chunk:=stream.read(1048576):digest.update(chunk)
    files.append(f'{digest.hexdigest()}  {p.relative_to(new).as_posix()}')
(manifest/'workspace-sha256.txt').write_text('\n'.join(files)+'\n')
(manifest/'handoff.json').write_text(json.dumps(dict(canonical=str(new),old_runtime_backup=str(backup),
    compatibility_symlink=str(old),head=head,hashed_files=len(files),dependencies='existing Linux versions, relocated offline'),indent=2)+'\n')
print(json.dumps(dict(canonical=str(new),head=head,hashed_files=len(files)),indent=2))
