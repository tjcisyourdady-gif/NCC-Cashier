"""Locate bundled Tcl/Tk before the GUI imports tkinter."""
import os
import sys


if getattr(sys, 'frozen', False):
    runtime_dir = sys._MEIPASS
    if os.name == 'nt':
        try:
            import ctypes

            tcl_dll_path = os.path.join(runtime_dir, 'tcl86t.dll')
            tcl_dll = ctypes.WinDLL(tcl_dll_path)
            tcl_dll.Tcl_FindExecutable.argtypes = (ctypes.c_char_p,)
            tcl_dll.Tcl_FindExecutable(
                tcl_dll_path.encode('mbcs', errors='replace')
            )
        except Exception:
            pass
    os.environ['TCL_LIBRARY'] = os.path.join(runtime_dir, '_tcl_data')
    os.environ['TK_LIBRARY'] = os.path.join(runtime_dir, '_tk_data')
