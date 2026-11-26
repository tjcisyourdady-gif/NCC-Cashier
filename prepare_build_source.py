"""创建不含内部文档地址的干净构建源码；不读取任何登录数据或日志。"""
import ast
from pathlib import Path
import shutil
import sys
from urllib.parse import parse_qs,urlsplit
import json
from dataclasses import asdict

import app_updater

def prepare(destination):
    project=Path(__file__).resolve().parent
    destination=Path(destination).resolve()
    destination.relative_to(project/'build')
    destination.mkdir()
    for name in app_updater.INSTALL_SOURCE_FILES:
        shutil.copyfile(project/name,destination/name)
    path=destination/'skip_list.py'
    source=path.read_text(encoding='utf-8-sig')
    assignments=[node for node in ast.parse(source).body if isinstance(node,ast.Assign)
        and any(isinstance(t,ast.Name) and t.id=='DEFAULT_SKIP_LIST_URL' for t in node.targets)]
    if len(assignments)!=1:
        raise RuntimeError('无法定位默认文档配置，未继续打包。')
    node=assignments[0]
    lines=source.splitlines(keepends=True)
    lines[node.lineno-1:node.end_lineno]=["DEFAULT_SKIP_LIST_URL = ''\n"]
    path.write_text(''.join(lines),encoding='utf-8')
    from skip_list import DEFAULT_SKIP_LIST_URL
    private_ids={v for values in parse_qs(urlsplit(DEFAULT_SKIP_LIST_URL).query).values() for v in values if len(v)>=10}
    for item in destination.iterdir():
        if item.suffix.lower() in ('.py','.js','.txt','.ps1','.json','.spec'):
            text=item.read_text(encoding='utf-8-sig')
            if any(value in text for value in private_ids):
                raise RuntimeError('构建源码仍有私人文档标识，未继续打包：'+item.name)
    return destination

if __name__=='__main__':
    if sys.argv[1]=='--settings':
        from settlement_core import Settings
        data=asdict(Settings())
        if '--public' in sys.argv: data['skip_list_url']=''
        Path(sys.argv[2]).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8-sig')
    else:
        prepare(sys.argv[1])
