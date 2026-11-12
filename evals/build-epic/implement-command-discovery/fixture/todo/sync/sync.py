"""todo sync: pull remote changes into the local store, then push the result."""
from todo.storage import Store
from todo.sync.merge import SyncResult, merge


def sync(local: Store, remote: Store) -> SyncResult:
    # Load both before writing anything, so a bad file on either side
    # (StoreError) leaves both untouched.
    local_items, remote_items = local.load(), remote.load()
    result = merge(local_items, remote_items)
    if result.pulled:
        local.save(result.items)
    if result.pushed:
        remote.save(result.items)
    return result
