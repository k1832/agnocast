"""Unit tests for the discovery helper used by the CLI verbs.

Procfs row parsing is covered by the kunit suite on the kernel side and by
the discovery_agent's own pytest on the userspace side; here we focus on
the bits that only this module owns: the AgnocastDaemonState aggregator
(``merge_topics``) and the conversion from an announcement list to the
shared row shape (``discovery_to_rows``).
"""

import pytest

from ros2agnocast import discovery


# ---------- merge_topics ------------------------------------------------------


def _make_endpoint(node_name, pid, *, is_bridge=False):
    from ros2agnocast_discovery_msgs.msg import AgnocastEndpoint
    return AgnocastEndpoint(
        node_name=node_name,
        pid=pid,
        qos_depth=1,
        qos_is_transient_local=False,
        qos_is_reliable=False,
        is_bridge=is_bridge,
    )


def _make_state(*, topic_name="/t", publishers=(), subscribers=(), ns_inode=1):
    from ros2agnocast_discovery_msgs.msg import (
        AgnocastDaemonState,
        AgnocastTopic,
    )
    return AgnocastDaemonState(
        schema_version=1,
        host_uuid="uuid",
        host_hostname="host",
        ipc_ns_inode=ns_inode,
        topics=[AgnocastTopic(
            topic_name=topic_name,
            domain_id=0,
            publishers=list(publishers),
            subscribers=list(subscribers),
        )],
    )


def test_merge_topics_collapses_same_topic_across_namespaces():
    msgs = [
        _make_state(
            topic_name="/chatter",
            publishers=[_make_endpoint("/talker", 100)],
            ns_inode=4026531839,
        ),
        _make_state(
            topic_name="/chatter",
            subscribers=[_make_endpoint("/listener", 200)],
            ns_inode=4026532000,
        ),
    ]
    merged = discovery.merge_topics(msgs)
    assert set(merged) == {"/chatter"}
    entry = merged["/chatter"]
    assert {p.node_name for p in entry.publishers} == {"/talker"}
    assert {s.node_name for s in entry.subscribers} == {"/listener"}


def test_merge_topics_empty_input_is_empty():
    assert discovery.merge_topics([]) == {}


def test_merge_topics_preserves_bridge_flag():
    msgs = [
        _make_state(
            topic_name="/chatter",
            publishers=[
                _make_endpoint("/talker", 100, is_bridge=False),
                _make_endpoint("/agnocast_bridge_node_1", 200, is_bridge=True),
            ],
        )
    ]
    merged = discovery.merge_topics(msgs)
    bridges = [p for p in merged["/chatter"].publishers if p.is_bridge]
    plain = [p for p in merged["/chatter"].publishers if not p.is_bridge]
    assert len(bridges) == 1
    assert len(plain) == 1


# ---------- discovery_to_rows -------------------------------------------------


def test_discovery_to_rows_shape_matches_parse_proc_topic_info(tmp_path, monkeypatch):
    # parse_proc_topic_info returns dicts keyed on the same names that
    # discovery_to_rows produces, so a caller can concatenate the two and
    # process them through a single loop. This test pins that contract.
    proc_file = tmp_path / "topic_info"
    proc_file.write_text(
        "4026531839 /t pub /n 100 1 0 0 0\n"
    )
    monkeypatch.setattr(discovery, "PROC_TOPIC_INFO", str(proc_file))
    procfs_rows = discovery.parse_proc_topic_info()
    discovery_rows = discovery.discovery_to_rows([
        _make_state(
            topic_name="/t",
            publishers=[_make_endpoint("/n", 100)],
            ns_inode=4026531839,
        ),
    ])
    assert procfs_rows and discovery_rows
    assert set(procfs_rows[0]) == set(discovery_rows[0])
    # Same primitive values for the columns both sources can produce.
    for k in ("ipc_ns_inode", "topic_name", "direction", "node_name", "pid",
              "qos_depth", "qos_is_transient_local", "qos_is_reliable",
              "is_bridge"):
        assert procfs_rows[0][k] == discovery_rows[0][k], k


def test_discovery_to_rows_emits_pub_and_sub_directions():
    msgs = [_make_state(
        topic_name="/t",
        publishers=[_make_endpoint("/p", 1)],
        subscribers=[_make_endpoint("/s", 2)],
    )]
    rows = discovery.discovery_to_rows(msgs)
    dirs = {r["direction"] for r in rows}
    assert dirs == {"pub", "sub"}
