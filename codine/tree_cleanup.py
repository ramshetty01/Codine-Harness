"""Bounded no-follow removal of one explicitly approved managed subtree."""
import os
import stat


def remove_tree(parent, name, *, maximum=4096):
    remaining=[maximum]
    def walk(directory, leaf, depth):
        if depth>32: raise ValueError('Cleanup depth exceeds 32 directories; state retained.')
        remaining[0]-=1
        if remaining[0]<0: raise ValueError('Cleanup entry bound reached; state retained for retry.')
        try: info=os.stat(leaf,dir_fd=directory,follow_symlinks=False)
        except FileNotFoundError:return
        if stat.S_ISDIR(info.st_mode):
            child=os.open(leaf,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
            try:
                if (os.fstat(child).st_dev,os.fstat(child).st_ino)!=(info.st_dev,info.st_ino):
                    raise ValueError('Directory changed during cleanup.')
                with os.scandir(child) as entries:
                    for entry in entries:walk(child,entry.name,depth+1)
                now=os.stat(leaf,dir_fd=directory,follow_symlinks=False)
                if (now.st_dev,now.st_ino)!=(info.st_dev,info.st_ino):raise ValueError('Directory changed during cleanup.')
                os.rmdir(leaf,dir_fd=directory)
            finally:os.close(child)
        else:
            # Unlink links themselves, never open/traverse their targets.
            os.unlink(leaf,dir_fd=directory)
        os.fsync(directory)
    walk(parent,name,0)
