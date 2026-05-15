"""Cross-`ROS_DOMAIN_ID` isolation test for the CLI subscribe path.

Verifies the non-goal stated in the design doc §2.4: a CLI running with
`ROS_DOMAIN_ID=X` must not receive `/_agnocast_discovery` announcements
published by a daemon (or any node) running with `ROS_DOMAIN_ID=Y` when
X != Y. DDS naturally isolates publishers/subscribers by domain, but we
test it end-to-end here so regressions in the discovery wiring surface
quickly.

A helper subprocess plays the role of a daemon: it spins up a ROS 2 node
in a chosen `ROS_DOMAIN_ID`, publishes one `AgnocastDaemonState` with
the discovery QoS, and sleeps so its Transient Local sample stays
discoverable. The test process subscribes via
`discovery.collect_announcements` with a *different* `ROS_DOMAIN_ID`
and asserts nothing is received.
"""

import os
import subprocess
import sys
import textwrap
import time

import pytest


_DAEMON_DOMAIN_ID = 91
_CLI_DOMAIN_ID = 92


_PUBLISHER_SCRIPT = textwrap.dedent(
    """\
    import sys
    import time

    import rclpy
    from builtin_interfaces.msg import Time
    from rclpy.duration import Duration
    from rclpy.qos import (
        DurabilityPolicy,
        HistoryPolicy,
        LivelinessPolicy,
        QoSProfile,
        ReliabilityPolicy,
    )
    from ros2agnocast_discovery_msgs.msg import (
        AgnocastDaemonState,
        AgnocastTopic,
    )

    qos = QoSProfile(
        depth=1,
        history=HistoryPolicy.KEEP_LAST,
        durability=DurabilityPolicy.TRANSIENT_LOCAL,
        reliability=ReliabilityPolicy.RELIABLE,
        liveliness=LivelinessPolicy.AUTOMATIC,
        liveliness_lease_duration=Duration(seconds=30.0),
    )

    rclpy.init()
    node = rclpy.create_node('xdomain_test_pub')
    pub = node.create_publisher(AgnocastDaemonState, '/_agnocast_discovery', qos)

    msg = AgnocastDaemonState(
        schema_version=1,
        host_uuid='xdomain-test',
        host_hostname='xdomain-test-host',
        timestamp=Time(sec=int(time.time()), nanosec=0),
        ipc_ns_inode=4242,
        topics=[AgnocastTopic(
            topic_name='/should_not_be_visible',
            domain_id=0,
            publishers=[],
            subscribers=[],
        )],
    )
    pub.publish(msg)
    # Let DDS finish handshakes; spin briefly so the sample sits in the
    # Transient Local cache before the subscriber appears on the other
    # domain.
    end = time.monotonic() + 3.0
    while time.monotonic() < end:
        rclpy.spin_once(node, timeout_sec=0.1)

    node.destroy_node()
    rclpy.shutdown()
    """
)


def _start_publisher(domain_id):
    env = os.environ.copy()
    env['ROS_DOMAIN_ID'] = str(domain_id)
    return subprocess.Popen(
        [sys.executable, '-c', _PUBLISHER_SCRIPT],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _try_import_msg():
    try:
        from ros2agnocast_discovery_msgs.msg import AgnocastDaemonState  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(
    not _try_import_msg(),
    reason="ros2agnocast_discovery_msgs not installed",
)
def test_cli_does_not_see_publisher_on_different_domain():
    """Negative case: publisher in domain X, subscriber in domain Y, expect no msgs."""
    publisher = _start_publisher(_DAEMON_DOMAIN_ID)
    try:
        # Give the publisher a head start so its Transient Local cache is
        # populated before we subscribe.
        time.sleep(1.0)

        # Now subscribe from a *different* domain.
        os.environ['ROS_DOMAIN_ID'] = str(_CLI_DOMAIN_ID)
        import rclpy
        from ros2agnocast import discovery

        rclpy.init()
        try:
            node = rclpy.create_node('xdomain_test_sub')
            received = discovery.collect_announcements(node, timeout_sec=1.5)
            node.destroy_node()
        finally:
            rclpy.shutdown()
        assert received == [], (
            'CLI in ROS_DOMAIN_ID=%d should not see a publisher in '
            'ROS_DOMAIN_ID=%d, but received %d announcement(s): %r'
            % (_CLI_DOMAIN_ID, _DAEMON_DOMAIN_ID, len(received), received)
        )
    finally:
        publisher.terminate()
        try:
            publisher.wait(timeout=3)
        except subprocess.TimeoutExpired:
            publisher.kill()
            publisher.wait()


@pytest.mark.skipif(
    not _try_import_msg(),
    reason="ros2agnocast_discovery_msgs not installed",
)
def test_cli_sees_publisher_on_same_domain():
    """Positive control: same test setup with matching domains receives the msg."""
    publisher = _start_publisher(_DAEMON_DOMAIN_ID)
    try:
        time.sleep(1.0)

        os.environ['ROS_DOMAIN_ID'] = str(_DAEMON_DOMAIN_ID)  # same as publisher
        import rclpy
        from ros2agnocast import discovery

        rclpy.init()
        try:
            node = rclpy.create_node('xdomain_test_sub_positive')
            received = discovery.collect_announcements(node, timeout_sec=1.5)
            node.destroy_node()
        finally:
            rclpy.shutdown()
        assert len(received) >= 1, (
            'Same-domain subscriber should receive the publisher Transient '
            'Local sample, but received none. (Sanity check for the negative '
            'test above — if this fails, the negative result is uninformative.)'
        )
        assert received[0].host_uuid == 'xdomain-test'
    finally:
        publisher.terminate()
        try:
            publisher.wait(timeout=3)
        except subprocess.TimeoutExpired:
            publisher.kill()
            publisher.wait()
