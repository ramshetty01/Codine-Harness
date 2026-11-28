"""Atomic no-clobber directory publication on supported Linux and macOS hosts."""
import ctypes
import os
import sys


def rename_noreplace(source, destination, *, src_dir_fd, dst_dir_fd):
    # Never substitute os.rename: it can overwrite an existing empty directory.
    if any(not isinstance(name,str) or not name or name in ('.','..') or '/' in name or '\x00' in name
           for name in (source,destination)):
        raise ValueError('Publication names must be single directory entries.')
    if sys.platform == 'darwin':
        symbol, flag = 'renameatx_np', 0x00000004  # Darwin sys/stdio.h: RENAME_EXCL
    elif sys.platform.startswith('linux'):
        symbol, flag = 'renameat2', 1  # Linux uapi/linux/fs.h: RENAME_NOREPLACE
    else:
        raise OSError('Atomic no-replace directory publication requires Linux or macOS.')
    library = ctypes.CDLL(None, use_errno=True)
    try: function = getattr(library,symbol)
    except AttributeError:
        raise OSError('Host libc lacks atomic no-replace rename; package publication refused.') from None
    function.argtypes = [ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(src_dir_fd,os.fsencode(source),dst_dir_fd,os.fsencode(destination),flag) != 0:
        error = ctypes.get_errno()
        raise OSError(error,os.strerror(error),destination)
