# -*- coding: utf-8 -*-
"""把扩展打成可上传 Web Store 的 zip：python extension/tools/pack.py [输出路径]

只收运行需要的文件（manifest、各脚本、lib/、icons/），测试夹具和工具不进去。
默认输出到仓库根的 dist/hb-recorder-<版本>.zip（dist/ 在 .gitignore 里）。
"""
import json
import sys
import zipfile
from pathlib import Path

EXT = Path(__file__).resolve().parent.parent
ROOTS = ['manifest.json', 'background.js', 'content.js', 'content.css', 'sidepanel.html', 'sidepanel.css', 'sidepanel.js',
         'lib', 'icons']


def collect():
    for root in ROOTS:
        p = EXT / root
        for f in sorted(p.rglob('*') if p.is_dir() else [p]):
            if f.is_file():
                yield f


def main(argv):
    version = json.loads((EXT / 'manifest.json').read_text(encoding='utf-8'))['version']
    out = Path(argv[1]) if len(argv) > 1 else EXT.parent / 'dist' / f'hb-recorder-{version}.zip'
    out.parent.mkdir(parents=True, exist_ok=True)
    files = list(collect())
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f.relative_to(EXT).as_posix())       # manifest 在 zip 根目录，路径用正斜杠
    print(f'packed {len(files)} files -> {out} ({out.stat().st_size} bytes)')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
