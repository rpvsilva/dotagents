"""Sync: merge the local store with a remote store, last write wins per item."""
from todo.sync.merge import SyncResult, merge
from todo.sync.remote import remote_store
from todo.sync.sync import sync

__all__ = ["SyncResult", "merge", "remote_store", "sync"]
