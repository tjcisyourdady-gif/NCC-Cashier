"""兼容旧版 Tcl 的中文开发目录；只用于运行离线验证。"""
import ctypes
import os
from pathlib import Path
import sys
import shutil
import tempfile
import unittest


def short_path(path):
    buffer=ctypes.create_unicode_buffer(32768)
    result=ctypes.windll.kernel32.GetShortPathNameW(str(path),buffer,len(buffer))
    return buffer.value if result else str(path)


if os.name=='nt':
    root=Path(sys.base_prefix)
    lib=ctypes.WinDLL(str(root/'DLLs'/'tcl86t.dll'))
    lib.Tcl_FindExecutable.argtypes=(ctypes.c_char_p,)
    lib.Tcl_FindExecutable(short_path(root/'python.exe').encode('utf-8'))
    _tk_temp=tempfile.TemporaryDirectory(prefix='ncc_tk_')
    for name in ('tcl8.6','tk8.6'):
        shutil.copytree(str(root/'tcl'/name),str(Path(_tk_temp.name)/name))
    os.environ['TCL_LIBRARY']=str(Path(_tk_temp.name)/'tcl8.6')
    os.environ['TK_LIBRARY']=str(Path(_tk_temp.name)/'tk8.6')

if __name__=='__main__':
    names=sys.argv[1:]
    suite=unittest.defaultTestLoader.loadTestsFromNames(names) if names else unittest.defaultTestLoader.discover(str(Path(__file__).parent))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(not result.wasSuccessful())
