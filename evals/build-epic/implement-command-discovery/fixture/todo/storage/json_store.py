"""JSON-file backend for the Store interface."""
import json
import os
import stat
import tempfile
from datetime import datetime, timedelta, timezone
from dataclasses import asdict, fields
from pathlib import Path

from todo.storage.base import Store
from todo.storage.model import DATE_FORMAT, MAX_ID, Item, StoreError, normalize_rule, normalize_tags, normalize_title, parse_anchor_day, parse_date, parse_occurrence, parse_priority, parse_series_id, show, utc_now


ITEM_FIELDS = {f.name for f in fields(Item)}
# How far past the real clock a stored stamp may be: room for clock skew between machines,
# while a stamp far in the future (9999) cannot win every later sync.
MAX_CLOCK_SKEW = timedelta(days=30)
# Largest todo file load reads, so a huge remote fails cleanly instead of exhausting memory.
MAX_FILE_BYTES = 16 * 2**20


def item_from_row(row: dict, source: Path, latest: datetime | None = None) -> Item:
    """Build an Item from a stored JSON row; raises StoreError on a bad stored value.

    The file may come from a shared remote, so the row's shape is checked too, and an
    `updated_at` later than `latest` is capped at `latest`.
    """
    if not isinstance(row, dict):
        raise StoreError(f"invalid row {show(row)} in {source}, expected an object")
    row = dict(row)
    unknown = sorted(row.keys() - ITEM_FIELDS)
    if unknown:
        raise StoreError(f"item {show(row.get('id'))}: unknown fields {', '.join(map(show, unknown[:4]))}{', ...' if len(unknown) > 4 else ''} in {source}")
    if type(row.get("id")) is not int or not 1 <= row["id"] <= MAX_ID:
        raise StoreError(f"item {show(row.get('id'))}: invalid id in {source}, expected a whole number 1..{MAX_ID}")
    if type(row.get("done", False)) is not bool:
        raise StoreError(f"item {show(row['id'])}: invalid done {show(row['done'])} in {source}, expected true or false")
    try:
        row["title"] = normalize_title(row.get("title"))
    except ValueError as e:
        raise StoreError(f"item {show(row['id'])}: {e} in {source}") from None
    if row.get("due") is not None:
        try:
            row["due"] = parse_date(row["due"])
        except ValueError:
            raise StoreError(
                f"item {show(row.get('id'))}: invalid due date {show(row['due'])}"
                f" in {source}, expected YYYY-MM-DD"
            ) from None
    if row.get("updated_at") is not None:
        try:
            stamp = datetime.fromisoformat(row["updated_at"])
        except (TypeError, ValueError):
            raise StoreError(
                f"item {show(row.get('id'))}: invalid updated_at {show(row['updated_at'])}"
                f" in {source}, expected an ISO 8601 timestamp"
            ) from None
        # A stamp without a zone is taken as UTC so stamps always compare.
        stamp = stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
        row["updated_at"] = min(stamp, latest) if latest else stamp
    tags = row.get("tags")
    if tags is None:
        row["tags"] = []
    else:
        try:
            if not isinstance(tags, list):
                raise ValueError(f"not a list: {show(tags)}")
            row["tags"] = normalize_tags(tags)
        except ValueError as e:
            raise StoreError(f"item {show(row.get('id'))}: invalid tags in {source}, {e}") from None
    try:
        row["priority"] = parse_priority(row.get("priority"))
    except ValueError as e:
        raise StoreError(f"item {show(row.get('id'))}: {e} in {source}") from None
    try:
        row["every"] = normalize_rule(row.get("every"))
    except ValueError as e:
        raise StoreError(f"item {show(row.get('id'))}: {e} in {source}") from None
    try:
        row["anchor_day"] = parse_anchor_day(row.get("anchor_day"))
    except ValueError as e:
        raise StoreError(f"item {show(row.get('id'))}: {e} in {source}") from None
    for key, parse in (("series_id", parse_series_id), ("occurrence", parse_occurrence)):
        try:
            row[key] = parse(row.get(key))
        except ValueError as e:
            raise StoreError(f"item {show(row.get('id'))}: {e} in {source}") from None
    return Item(**row)


def check_folders(path: Path) -> None:
    """Raise StoreError if a folder on the way to `path` is a symlink owned by someone else.

    Symlinks owned by root or the user are followed, so system links (`/tmp` on macOS) and the
    user's own links keep working; a link another user planted in a shared folder would send
    reads and writes to a folder of their choosing.
    """
    pending = list(reversed(path.absolute().parent.parts))
    folder = Path(pending.pop())
    links = 0
    while pending:
        part = pending.pop()
        if part == "..":
            folder = folder.parent
            continue
        nxt = folder / part
        try:
            st = os.lstat(nxt)
        except FileNotFoundError:
            return  # nothing below it exists, so there is nothing to be redirected to
        if not stat.S_ISLNK(st.st_mode):
            folder = nxt
            continue
        if st.st_uid not in (0, os.geteuid()):
            raise StoreError(f"{path}: folder {nxt} is a symlink owned by someone else; point at the real folder")
        links += 1
        if links > 40:
            raise StoreError(f"{path}: too many symlinks in its folders")
        target = Path(os.readlink(nxt))
        if target.is_absolute():
            folder = Path(target.anchor)
        pending.extend(reversed(target.parts[1:] if target.is_absolute() else target.parts))


def item_to_row(item: Item) -> dict:
    row = asdict(item)
    row["due"] = item.due.strftime(DATE_FORMAT) if item.due else None
    row["updated_at"] = item.updated_at.isoformat() if item.updated_at else None
    # One-off items keep the old row shape, so older versions can still read them.
    for key in ("every", "anchor_day", "series_id", "occurrence"):
        if row[key] is None:
            del row[key]
    return row


class JsonStore(Store):
    def __init__(self, path: Path, clock=None, follow_symlinks: bool = True):
        self.path = Path(path)
        # A shared remote passes False, so a symlink planted there (or at one of its folders, or a hard
        # link) cannot point us at another file, and a file another member saved keeps its mode.
        self.follow_symlinks = follow_symlinks
        if clock is not None:
            self.clock = clock

    def load(self) -> list[Item]:
        if not self.follow_symlinks:
            check_folders(self.path)
        if not self.path.exists():
            return []
        latest = utc_now() + MAX_CLOCK_SKEW
        data = self._read()
        try:
            rows = json.loads(data)
        except (ValueError, RecursionError) as e:
            raise StoreError(f"invalid JSON in {self.path}: {e}") from None
        if not isinstance(rows, list):
            raise StoreError(f"invalid {self.path}, expected a list of items")
        return [item_from_row(raw, self.path, latest) for raw in rows]

    def _read(self) -> bytes:
        # O_NONBLOCK: a FIFO swapped in at the path must not block the open; the checks below run on
        # what was actually opened, so nothing can change between check and read.
        flags = os.O_RDONLY | os.O_NONBLOCK
        if not self.follow_symlinks:
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(self.path, flags)
        except OSError as e:
            if not self.follow_symlinks and self.path.is_symlink():
                raise StoreError(f"{self.path} is a symlink; point at the file itself") from None
            raise
        with os.fdopen(fd, "rb") as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode):
                raise StoreError(f"{self.path} is not a regular file")
            # A hard link planted at a shared remote would pull in another of the user's files.
            if not self.follow_symlinks and st.st_nlink != 1:
                raise StoreError(f"{self.path} is a hard link to another file; point at a file of its own")
            data = f.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise StoreError(f"{self.path} is too large, a todo file holds at most {MAX_FILE_BYTES // 2**20} MiB")
        return data

    def save(self, items: list[Item]) -> None:
        # Write a temp file beside the target, then swap it in, so a reader never sees half a file.
        if not self.follow_symlinks:
            check_folders(self.path)
        text = json.dumps([item_to_row(i) for i in items], indent=2)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(text)
                self._keep_permissions(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            os.unlink(tmp)
            raise

    def _keep_permissions(self, fd: int) -> None:
        """Give the temp file at `fd` the target's read/write bits and group, so a shared remote stays shared.

        mkstemp makes the file 0600. Setuid, setgid, sticky and execute bits are dropped. Works on the
        descriptor, never the temp file's name, which a writer of the folder could swap for a symlink.
        """
        try:
            st = os.stat(self.path, follow_symlinks=self.follow_symlinks)
        except FileNotFoundError:
            return
        # A local file someone else planted (e.g. 0666 in a shared folder) must not open the user's list
        # to them. A shared remote is copied as it is: the last member to save it owns it.
        if self.follow_symlinks and st.st_uid != os.geteuid():
            return
        mode = stat.S_IMODE(st.st_mode) & 0o666
        if mode & 0o060:
            # On Linux a new file takes the saver's primary group, which could be wider than the file's.
            try:
                os.fchown(fd, -1, st.st_gid)
            except OSError as e:
                raise StoreError(f"cannot keep the group of {self.path} ({e.strerror}); nothing was saved") from None
        os.fchmod(fd, mode)
