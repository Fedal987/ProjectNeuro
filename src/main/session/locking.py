"""Advisory local file locks using standard-library platform primitives."""
import os

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def lock_file(stream, *, shared=False, blocking=True):
    if os.name == "nt":
        stream.seek(0)
        mode = msvcrt.LK_RLCK if shared else msvcrt.LK_LOCK
        if not blocking:
            mode = msvcrt.LK_NBRLCK if shared else msvcrt.LK_NBLCK
        msvcrt.locking(stream.fileno(), mode, 1)
    else:
        mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        if not blocking:
            mode |= fcntl.LOCK_NB
        fcntl.flock(stream, mode)


def unlock_file(stream):
    if os.name == "nt":
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(stream, fcntl.LOCK_UN)
