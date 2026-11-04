"""CI-only build/qualification driver. Never run against source on operator Mac."""
import collections
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import zipfile


def run(*args, cwd=None, capture=False):
    return subprocess.run(list(map(str,args)), cwd=cwd, check=True,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None,
                          text=capture, timeout=1200)


def files(root):
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            assert path.resolve().is_relative_to(root.resolve()), 'Bundle symlink escapes its root'
        elif path.is_file():
            yield path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect(roots, scratch, scanner):
    destination=scratch/'privacy-strings';destination.mkdir()
    rows=[];home=[];forbidden=[]
    allowed_accounts={'runner','runneradmin','Shared','Public','Guest','brew','build','builder'}
    total=0
    for root in roots:
        for path in files(root):
            assert len(rows)<5000 and path.stat().st_size<80*1024**2
            body=path.read_bytes();total+=len(body);assert total<300*1024**2
            if path.name=='.DS_Store' or path.name=='.env' or path.name.startswith('.env.') or path.suffix in ['.wav','.aiff','.aif','.flac','.mp3','.m4a','.ogg','.caf','.pyc']:
                forbidden.append(str(path.relative_to(root)))
            text='\n'.join(m.group().decode('ascii') for m in re.finditer(rb'[\x20-\x7e]{6,}',body))
            for pattern,encoding in [(rb'(?:[\x20-\x7e]\x00){6,}','utf-16-le'),(rb'(?:\x00[\x20-\x7e]){6,}','utf-16-be')]:
                text+='\n'+'\n'.join(m.group().decode(encoding) for m in re.finditer(pattern,body))
            unknown=0;generic=0
            for match in re.finditer(r'/(?:Users|home)/([^/\s\"\x00<>]{1,100})',text):
                account=match.group(1)
                if account in allowed_accounts:generic+=1
                else:unknown+=1
            if unknown:home.append({'file_ordinal':len(rows),'unknown_home_candidates':unknown})
            (destination/(str(len(rows))+'.txt')).write_text(text)
            rows.append({'bytes':len(body),'sha256':sha(path),'generic_CI_or_vendor_home_markers':generic})
    config=scratch/'gitleaks.toml';config.write_text('[extend]\nuseDefault = true\n')
    report=scratch/'gitleaks-redacted.json'
    with (scratch/'gitleaks.log').open('wb') as log:
        scan=subprocess.run([str(scanner),'dir',str(destination),'--config',str(config),'--ignore-gitleaks-allow','--redact=100','--no-banner','--max-target-megabytes=200','--report-format=json','--report-path',str(report)],stdout=log,stderr=log,timeout=300)
    assert scan.returncode in [0,1]
    candidates=json.loads(report.read_text())
    summary={'files':len(rows),'bytes':total,'unknown_home_candidates':sum(x['unknown_home_candidates'] for x in home),'generic_CI_or_vendor_home_markers':sum(x['generic_CI_or_vendor_home_markers'] for x in rows),'forbidden_file_count':len(forbidden),'secret_candidates':len(candidates),'secret_rules':dict(collections.Counter(x['RuleID'] for x in candidates)),'generic_home_exception':'Known ephemeral CI/system/vendor build accounts only; no arbitrary /Users allowance'}
    (scratch/'privacy-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    assert not home and not forbidden and not candidates, 'Privacy qualification failed; counts only in private CI summary, no payload upload'
    return summary


def archive(bundle,target):
    with zipfile.ZipFile(target,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for path in sorted(bundle.rglob('*')):
            relative=Path(bundle.name)/path.relative_to(bundle)
            if path.is_symlink():
                assert path.resolve().is_relative_to(bundle.resolve())
                info=zipfile.ZipInfo(str(relative));info.create_system=3;info.external_attr=(stat.S_IFLNK|0o777)<<16
                z.writestr(info,os.readlink(path).encode())
            elif path.is_file():
                info=zipfile.ZipInfo(str(relative));info.create_system=3;info.external_attr=(stat.S_IFREG|(0o755 if os.access(path,os.X_OK) else 0o644))<<16
                z.writestr(info,path.read_bytes(),compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)


def main():
    assert os.environ.get('GITHUB_ACTIONS')=='true' and sys.platform=='darwin', 'Disposable macOS CI only'
    os.umask(0o077)
    source=Path(os.environ['GITHUB_WORKSPACE'])/'release-source'
    assert run('git','rev-parse','HEAD',cwd=source,capture=True).stdout.strip()==os.environ['RELEASE_SOURCE']
    scratch=Path(os.environ['RUNNER_TEMP'])/'oram-clean-candidates';scratch.mkdir()
    version=tomllib.loads((source/'pyproject.toml').read_text())['project']['version'];assert version=='0.4.2'
    workspace=str(Path(os.environ['GITHUB_WORKSPACE']))
    compiler_maps=[f'-ffile-prefix-map={workspace}=/oram-source',f'-fdebug-prefix-map={workspace}=/oram-source',f'-ffile-prefix-map={scratch}=/oram-build',f'-fdebug-prefix-map={scratch}=/oram-build']
    plugin_build=scratch/'plugin-build'
    run('cmake','-S',source/'plugins/oram-plugin','-B',plugin_build,'-DCMAKE_BUILD_TYPE=Release','-DCMAKE_OSX_ARCHITECTURES=arm64','-DCMAKE_OSX_DEPLOYMENT_TARGET=13.0','-DCMAKE_CXX_FLAGS='+' '.join(compiler_maps),'-DCMAKE_C_FLAGS='+' '.join(compiler_maps))
    run('cmake','--build',plugin_build,'--config','Release','--target','ORAMPlugin_All','--parallel','3')
    artifacts=plugin_build/'ORAMPlugin_artefacts/Release'
    bundles=[artifacts/'AU/ORAM.component',artifacts/'VST3/ORAM.vst3',artifacts/'Standalone/ORAM.app']
    for bundle in bundles:
        assert bundle.is_dir()
        run('codesign','--force','--deep','--sign','-',bundle)
        run('codesign','--verify','--deep','--strict',bundle)
    assert (bundles[1]/'Contents/Resources/moduleinfo.json').is_file()
    # Existing source documents AU validation as a release gate. This writes only
    # the disposable runner's normal plug-in folder; HOME is never repurposed.
    installed=Path.home()/'Library/Audio/Plug-Ins/Components/ORAM.component'
    installed.parent.mkdir(parents=True,exist_ok=True)
    assert not installed.exists()
    shutil.copytree(bundles[0],installed,symlinks=True)
    run('auval','-v','aufx','Oram','Sflb')
    swift_scratch=scratch/'swift-build'
    swift_args=['swift','build','--package-path',str(source/'apps/macos'),'--scratch-path',str(swift_scratch),'-c','release']
    for option in ['-debug-prefix-map','-file-prefix-map']:
        for actual,replacement in [(workspace,'/oram-source'),(str(scratch),'/oram-build')]:
            swift_args+=['-Xswiftc',option,'-Xswiftc',actual+'='+replacement]
    run(*swift_args)
    bin_directory=Path(run('swift','build','--package-path',source/'apps/macos','--scratch-path',swift_scratch,'-c','release','--show-bin-path',capture=True).stdout.strip())
    staging=scratch/'disk-staging';app=staging/'ORAM.app';macos=app/'Contents/MacOS';resources=app/'Contents/Resources'
    macos.mkdir(parents=True);resources.mkdir()
    shutil.copy2(bin_directory/'ORAM',macos/'ORAM')
    for resource in bin_directory.glob('*.bundle'):shutil.copytree(resource,resources/resource.name,symlinks=True)
    for src,dst in [(source/'apps/macos/Sources/ORAMApp/Resources/logo-oram.png',resources/'logo-oram.png'),(source/'apps/macos/Assets/AppIcon.icns',resources/'AppIcon.icns')]:
        if src.exists():shutil.copy2(src,dst)
    python=resources/'oram-python';python.mkdir()
    for filename in ['pyproject.toml','uv.lock','README.md','LICENSE','engines.yaml']:
        shutil.copy2(source/filename,python/filename)
    # Whitelist source package files; never copy repository receipts, sessions,
    # local audio, dotenv files, provider credentials or build/virtualenv caches.
    excluded={'__pycache__','.venv','.git','.pytest_cache','output','vendor','oram_sessions'}
    suffixes={'.py','.json','.html','.css','.js','.svg','.txt','.yaml','.yml','.md','.toml','.png','.ico'}
    for path in sorted((source/'src').rglob('*')):
        if path.is_symlink() or not path.is_file() or any(x in excluded for x in path.relative_to(source/'src').parts):continue
        if path.suffix not in suffixes:continue
        target=python/'src'/path.relative_to(source/'src');target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
    vendor_bin=resources/'bin';vendor_bin.mkdir()
    uv=Path(shutil.which('uv'));assert uv.is_file()
    assert run(uv,'--version',capture=True).stdout.startswith('uv 0.13.6 ')
    shutil.copy2(uv,vendor_bin/'uv')
    # Preserve the authenticated vendor binary unchanged; do not patch bytes or
    # strip/re-sign an already signed third-party executable to hide a path.
    assert sha(uv)==sha(vendor_bin/'uv')
    info={'CFBundleExecutable':'ORAM','CFBundleIdentifier':'com.sonicfieldlabs.oram','CFBundleName':'ORAM','CFBundlePackageType':'APPL','CFBundleShortVersionString':version,'CFBundleVersion':version,'LSMinimumSystemVersion':'13.0','NSPrincipalClass':'NSApplication','NSMicrophoneUsageDescription':'ORAM records local audio into layers when you press record.','CFBundleIconFile':'AppIcon'}
    (app/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    run('codesign','--force','--sign','-',app)
    assert sha(uv)==sha(vendor_bin/'uv'), 'Outer signing changed the vendor binary'
    run('codesign','--verify','--deep','--strict',app)
    scanner=Path(os.environ['GITLEAKS_BIN'])
    privacy=inspect(bundles+[app],scratch,scanner)
    qualified=Path(os.environ['RUNNER_TEMP'])/'oram-qualified-public-candidates';qualified.mkdir()
    names=['ORAM-AU-Release.zip','ORAM-VST3-Release.zip','ORAM-Standalone-Release.zip']
    for bundle,name in zip(bundles,names):archive(bundle,qualified/name)
    image=qualified/'ORAM.dmg'
    run('hdiutil','create','-volname','ORAM','-srcfolder',staging,'-format','UDZO',image)
    run('hdiutil','verify',image)
    mounted=scratch/'verify-mounted';mounted.mkdir()
    try:
        run('hdiutil','attach','-readonly','-nobrowse','-mountpoint',mounted,image)
        assert (mounted/'ORAM.app/Contents/Info.plist').is_file()
        wanted={str(p.relative_to(app)):sha(p) for p in files(app)}
        actual={str(p.relative_to(mounted/'ORAM.app')):sha(p) for p in files(mounted/'ORAM.app')}
        assert wanted==actual, 'Actual disk-image payload differs from qualified app'
        run('codesign','--verify','--deep','--strict',mounted/'ORAM.app')
    finally:
        run('hdiutil','detach',mounted)
    payload=[{'path':p.name,'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(qualified.iterdir())]
    (qualified/'checksums.txt').write_text(''.join(x['sha256']+'  '+x['path']+'\n' for x in payload))
    report={'schema':1,'source_commit':os.environ['RELEASE_SOURCE'],'workflow_head':os.environ['WORKFLOW_HEAD'],'run_id':os.environ['GITHUB_RUN_ID'],'version':version,'JUCE_commit':os.environ['JUCE_SOURCE'],'architecture':'arm64','minimum_macos':'13.0','owner_MAC_execution':False,'provider_credentials_supplied':False,'published_or_replaced':False,'codesign':'ad-hoc, matching existing alpha distribution; not Developer ID/notarized','AU_validation':'passed','VST3_moduleinfo':'present','disk_image_actual_payload_hashes_verified':True,'privacy':privacy,'files':payload,'human_DAW_and_GUI_acceptance':'still required before publishing','uv_vendor_sha256':sha(uv),'source_features':'current audited v0.4.2 source, native plug-in code unchanged from v0.4'}
    (qualified/'qualification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'qualified_files':len(payload),'privacy':privacy,'published':False}))


if __name__=='__main__':main()
