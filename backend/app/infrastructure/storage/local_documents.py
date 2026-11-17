"""Private POSIX local storage; authorization belongs to the application.

Directory-relative operations and O_NOFOLLOW prevent pathname/symlink escapes.
A complete, fsynced file is published with an exclusive hard link, never replaced.
The explicit root must be owned by this process user and private (0700).
"""
from contextlib import contextmanager
from functools import wraps
import os
from pathlib import Path
import re
import secrets
import stat

from app.application.dtos.invoice_documents import ValidatedDocument
from app.application.ports.invoice_documents import DocumentStorageError


def _sanitized(operation):
    @wraps(operation)
    def call(*args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except (OSError, ValueError):
            pass
        # Do not keep even an implicit exception context with filesystem paths.
        raise DocumentStorageError("Private document storage operation failed")
    return call


class LocalDocumentStorage:
    @_sanitized
    def __init__(self, root: Path):
        self.root = Path(os.path.abspath(root))
        with self._directory(create=True):
            pass

    @contextmanager
    def _directory(self, *, create=False):
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            parts = self.root.parts[1:]
            if not parts:
                raise ValueError()
            for index, part in enumerate(parts):
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
                if index == len(parts) - 1:
                    info = os.fstat(fd)
                    if stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.geteuid():
                        raise ValueError()
            yield fd
        finally:
            os.close(fd)

    @staticmethod
    def _key(key: str) -> None:
        if not isinstance(key, str) or re.fullmatch(r"[0-9a-f]{64}", key) is None:
            raise ValueError()

    @staticmethod
    def _regular(info):
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.geteuid() or info.st_nlink != 1):
            raise ValueError()

    @_sanitized
    def put(self, document: ValidatedDocument) -> str:
        key = secrets.token_hex(32)
        self._key(key)
        temporary = ".pending-" + secrets.token_hex(32)
        with self._directory() as directory:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
            published = False
            try:
                with os.fdopen(fd, "wb") as output:
                    os.fchmod(output.fileno(), 0o600)
                    output.write(document.content)
                    output.flush()
                    os.fsync(output.fileno())
                os.link(temporary, key, src_dir_fd=directory, dst_dir_fd=directory,
                        follow_symlinks=False)
                published = True
                os.unlink(temporary, dir_fd=directory)
                os.fsync(directory)
            except (OSError, ValueError):
                # Only a successful exclusive link gives this attempt ownership
                # of the final name. A collision must never remove an old file.
                for name in ([key] if published else []) + [temporary]:
                    try:
                        os.unlink(name, dir_fd=directory)
                    except OSError:
                        pass
                try:
                    os.fsync(directory)
                except OSError:
                    pass
                # Cleanup failures cannot replace the original operation error;
                # the outer boundary removes its message and exception context.
                raise
        return key

    @_sanitized
    def read(self, storage_key: str) -> bytes:
        self._key(storage_key)
        with self._directory() as directory:
            fd = os.open(storage_key, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, "rb") as source:
                self._regular(os.fstat(source.fileno()))
                return source.read()

    @_sanitized
    def delete(self, storage_key: str) -> None:
        self._key(storage_key)
        with self._directory() as directory:
            try:
                info = os.stat(storage_key, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                return
            self._regular(info)
            os.unlink(storage_key, dir_fd=directory)
            os.fsync(directory)
