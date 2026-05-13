"""Unit tests for the pure-Python parts of the discovery agent.

Targets the procfs row parser, the per-namespace message builder, and the
change-detection signature. The rclpy-bound bits (timers, publishers,
shutdown handling) belong in an integration test, not here.
"""

from unittest.mock import patch

import pytest

from builtin_interfaces.msg import Time

from ros2agnocast_discovery_agent import agent


# ---------- parse_topic_info --------------------------------------------------


def test_parse_topic_info_groups_by_ns_and_topic(tmp_path):
    proc_file = tmp_path / "topic_info"
    proc_file.write_text(
        "# schema_version=1\n"
        "# ipc_ns_inode topic_name direction node_name pid qos_depth tl rel bridge\n"
        "4026531839 /chatter pub /talker 100 1 0 0 0\n"
        "4026531839 /chatter sub /listener 101 1 0 1 0\n"
        "4026532000 /chatter pub /other_talker 200 1 0 0 0\n"
    )
    with patch.object(agent, "PROC_TOPIC_INFO", str(proc_file)):
        grouped = agent.parse_topic_info()

    assert set(grouped.keys()) == {
        (4026531839, "/chatter"),
        (4026532000, "/chatter"),
    }
    same_ns = grouped[(4026531839, "/chatter")]
    assert len(same_ns["publishers"]) == 1
    pub = same_ns["publishers"][0]
    assert pub.node_name == "/talker"
    assert pub.pid == 100
    assert pub.qos_depth == 1
    assert pub.qos_is_transient_local is False
    assert pub.qos_is_reliable is False

    assert len(same_ns["subscribers"]) == 1
    sub = same_ns["subscribers"][0]
    assert sub.node_name == "/listener"
    assert sub.qos_is_reliable is True


def test_parse_topic_info_missing_file_returns_empty():
    with patch.object(agent, "PROC_TOPIC_INFO", "/proc/agnocast/__nonexistent__"):
        assert agent.parse_topic_info() == {}


def test_parse_topic_info_skips_comments_and_malformed_lines(tmp_path):
    proc_file = tmp_path / "topic_info"
    proc_file.write_text(
        "# header line\n"
        "# another header\n"
        "\n"
        "this is malformed\n"
        "4026531839 /a pub /node 100 1 0 0 0\n"
        "not enough columns 1 2\n"
        "4026531839 /a sub /node 101 1 0 1 0\n"
    )
    with patch.object(agent, "PROC_TOPIC_INFO", str(proc_file)):
        grouped = agent.parse_topic_info()
    assert (4026531839, "/a") in grouped
    bucket = grouped[(4026531839, "/a")]
    assert len(bucket["publishers"]) == 1
    assert len(bucket["subscribers"]) == 1


# ---------- build_messages ----------------------------------------------------


def test_build_messages_emits_one_msg_per_ns(tmp_path):
    proc_file = tmp_path / "topic_info"
    proc_file.write_text(
        "4026531839 /a pub /node1 100 1 0 0 0\n"
        "4026532000 /b pub /node2 200 1 0 0 0\n"
        "4026531839 /a sub /node3 101 1 0 1 0\n"
    )
    now = Time(sec=12345, nanosec=0)

    with patch.object(agent, "PROC_TOPIC_INFO", str(proc_file)):
        msgs = agent.build_messages("test-uuid", "test-host", now)

    assert len(msgs) == 2
    ns_to_msg = {m.ipc_ns_inode: m for m in msgs}
    assert set(ns_to_msg) == {4026531839, 4026532000}
    for m in msgs:
        assert m.schema_version == agent.SCHEMA_VERSION
        assert m.host_uuid == "test-uuid"
        assert m.host_hostname == "test-host"
        assert m.timestamp.sec == 12345

    msg_ns_a = ns_to_msg[4026531839]
    assert len(msg_ns_a.topics) == 1
    assert msg_ns_a.topics[0].topic_name == "/a"
    assert msg_ns_a.topics[0].domain_id == 0
    assert len(msg_ns_a.topics[0].publishers) == 1
    assert len(msg_ns_a.topics[0].subscribers) == 1


def test_build_messages_empty_procfs(tmp_path):
    # Procfs missing → empty list (no message goes out).
    with patch.object(agent, "PROC_TOPIC_INFO", str(tmp_path / "nope")):
        assert agent.build_messages("u", "h", Time(sec=0, nanosec=0)) == []


# ---------- DiscoveryAgent._signature ----------------------------------------


def _make_state(*, node_name="/n", pid=100, depth=1, ns_inode=4026531839):
    from ros2agnocast_discovery_msgs.msg import (
        AgnocastDaemonState,
        AgnocastTopic,
        AgnocastEndpoint,
    )

    ep = AgnocastEndpoint(
        node_name=node_name,
        pid=pid,
        qos_depth=depth,
        qos_is_transient_local=False,
        qos_is_reliable=False,
        is_bridge=False,
    )
    topic = AgnocastTopic(
        topic_name="/t",
        domain_id=0,
        publishers=[ep],
        subscribers=[],
    )
    return AgnocastDaemonState(
        schema_version=1,
        host_uuid="uuid",
        host_hostname="host",
        ipc_ns_inode=ns_inode,
        topics=[topic],
    )


def test_signature_stable_for_identical_state():
    sig = agent.DiscoveryAgent._signature
    assert sig(_make_state()) == sig(_make_state())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"pid": 101},
        {"node_name": "/m"},
        {"depth": 5},
    ],
)
def test_signature_changes_when_endpoint_changes(kwargs):
    sig = agent.DiscoveryAgent._signature
    base = sig(_make_state())
    assert sig(_make_state(**kwargs)) != base


def test_signature_ignores_timestamp():
    # _signature only walks topics, so changing the wall-clock timestamp on
    # the message must not alter the result — otherwise the heartbeat would
    # masquerade as a state change and bloat the gossip bandwidth.
    sig = agent.DiscoveryAgent._signature
    s1 = _make_state()
    s2 = _make_state()
    s2.timestamp = Time(sec=99999, nanosec=0)
    assert sig(s1) == sig(s2)
