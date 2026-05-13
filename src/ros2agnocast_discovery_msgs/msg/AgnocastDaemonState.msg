# Snapshot of the Agnocast state of a single IPC namespace, published by
# the discovery agent. One message per NS; the Epic per-IPC daemon will emit
# the same shape from each NS without further changes.
#
# Schema is versioned (see CROSS_NS_OBSERVABILITY_ja.md §5.3). v1 is the
# initial form; future bumps add fields without breaking existing readers
# (CLI checks `schema_version` and degrades gracefully on unknown values).

uint32 schema_version
string host_uuid                   # ECU identity; /etc/machine-id recommended
string host_hostname               # Human-readable hostname
builtin_interfaces/Time timestamp  # For stale detection
uint64 ipc_ns_inode                # NS this message describes
AgnocastTopic[] topics
