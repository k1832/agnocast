// SPDX-License-Identifier: GPL-2.0-only OR BSD-2-Clause
#include "agnocast_kunit_procfs.h"

#include "../agnocast.h"

#include <kunit/test.h>
#include <linux/file.h>
#include <linux/fs.h>
#include <linux/string.h>

#define READ_BUF_SIZE 8192

// Read a /proc/agnocast/* file into a buffer. Returns the number of bytes
// read (always NUL-terminated on success) or a negative errno.
static ssize_t read_proc_file(const char * path, char * buf, size_t buf_size)
{
  struct file * f;
  ssize_t n;
  loff_t pos = 0;

  f = filp_open(path, O_RDONLY, 0);
  if (IS_ERR(f)) return PTR_ERR(f);

  n = kernel_read(f, buf, buf_size - 1, &pos);
  filp_close(f, NULL);
  if (n < 0) return n;
  buf[n] = '\0';
  return n;
}

static int count_data_lines(const char * buf)
{
  int count = 0;
  const char * p = buf;
  while (*p) {
    if (*p != '#') {
      // Move to end of this line; count it if it has any non-whitespace.
      const char * line_start = p;
      while (*p && *p != '\n') p++;
      if (p > line_start) count++;
    } else {
      // Skip a comment line.
      while (*p && *p != '\n') p++;
    }
    if (*p == '\n') p++;
  }
  return count;
}

void test_case_procfs_topics_empty(struct kunit * test)
{
  char * buf = kunit_kzalloc(test, READ_BUF_SIZE, GFP_KERNEL);
  ssize_t n;

  KUNIT_ASSERT_NOT_NULL(test, buf);

  n = read_proc_file("/proc/agnocast/topics", buf, READ_BUF_SIZE);
  KUNIT_ASSERT_GT(test, n, 0);
  KUNIT_EXPECT_NOT_NULL(test, strstr(buf, "schema_version=1"));
  KUNIT_EXPECT_NOT_NULL(test, strstr(buf, "ipc_ns_inode topic_name pub_count sub_count"));
  KUNIT_EXPECT_EQ(test, count_data_lines(buf), 0);
}

void test_case_procfs_topics_with_pub_and_sub(struct kunit * test)
{
  const char * topic_name = "/kunit_procfs_topic";
  const char * pub_node = "/kunit_procfs_pub_node";
  const char * sub_node = "/kunit_procfs_sub_node";
  const struct ipc_namespace * ipc_ns = current->nsproxy->ipc_ns;
  union ioctl_add_publisher_args pa;
  union ioctl_add_subscriber_args sa;
  char * buf;
  ssize_t n;
  char expect[128];

  KUNIT_ASSERT_EQ(
    test,
    agnocast_ioctl_add_publisher(
      topic_name, ipc_ns, pub_node, /*pid=*/1000, /*qos_depth=*/1,
      /*qos_is_transient_local=*/false, /*is_bridge=*/false, &pa),
    0);
  KUNIT_ASSERT_EQ(
    test,
    agnocast_ioctl_add_subscriber(
      topic_name, ipc_ns, sub_node, /*pid=*/1001, /*qos_depth=*/1,
      /*qos_is_transient_local=*/false, /*qos_is_reliable=*/true,
      /*is_take_sub=*/false, /*ignore_local_publications=*/false,
      /*is_bridge=*/false, &sa),
    0);

  buf = kunit_kzalloc(test, READ_BUF_SIZE, GFP_KERNEL);
  KUNIT_ASSERT_NOT_NULL(test, buf);
  n = read_proc_file("/proc/agnocast/topics", buf, READ_BUF_SIZE);
  KUNIT_ASSERT_GT(test, n, 0);

  KUNIT_EXPECT_EQ(test, count_data_lines(buf), 1);
  // " <topic> <pub_count> <sub_count>" — must be 1 1 (both endpoints in same NS).
  scnprintf(expect, sizeof(expect), " %s 1 1\n", topic_name);
  KUNIT_EXPECT_NOT_NULL_MSG(
    test, strstr(buf, expect), "expected '%s' in:\n%s", expect, buf);
}

void test_case_procfs_nodes_dedupe_same_node_multiple_topics(struct kunit * test)
{
  const char * node_name = "/kunit_procfs_shared_node";
  const char * topic_a = "/kunit_procfs_topic_a";
  const char * topic_b = "/kunit_procfs_topic_b";
  const struct ipc_namespace * ipc_ns = current->nsproxy->ipc_ns;
  union ioctl_add_publisher_args pa1, pa2;
  char * buf;
  ssize_t n;
  const char * p;
  int hits = 0;
  size_t name_len;

  KUNIT_ASSERT_EQ(
    test,
    agnocast_ioctl_add_publisher(
      topic_a, ipc_ns, node_name, /*pid=*/2000, 1, false, false, &pa1),
    0);
  KUNIT_ASSERT_EQ(
    test,
    agnocast_ioctl_add_publisher(
      topic_b, ipc_ns, node_name, /*pid=*/2000, 1, false, false, &pa2),
    0);

  buf = kunit_kzalloc(test, READ_BUF_SIZE, GFP_KERNEL);
  KUNIT_ASSERT_NOT_NULL(test, buf);
  n = read_proc_file("/proc/agnocast/nodes", buf, READ_BUF_SIZE);
  KUNIT_ASSERT_GT(test, n, 0);

  // The (ipc_ns, pid=2000, node_name) tuple should appear exactly once.
  name_len = strlen(node_name);
  p = buf;
  while ((p = strstr(p, node_name)) != NULL) {
    hits++;
    p += name_len;
  }
  KUNIT_EXPECT_EQ_MSG(
    test, hits, 1, "node '%s' should appear once after publishing on 2 topics; got:\n%s",
    node_name, buf);
}

void test_case_procfs_topic_info_direction_columns(struct kunit * test)
{
  const char * topic_name = "/kunit_procfs_direction_topic";
  const char * pub_node = "/kunit_procfs_dir_pub";
  const char * sub_node = "/kunit_procfs_dir_sub";
  const struct ipc_namespace * ipc_ns = current->nsproxy->ipc_ns;
  union ioctl_add_publisher_args pa;
  union ioctl_add_subscriber_args sa;
  char * buf;
  ssize_t n;
  char expect_pub[256];
  char expect_sub[256];

  KUNIT_ASSERT_EQ(
    test,
    agnocast_ioctl_add_publisher(
      topic_name, ipc_ns, pub_node, /*pid=*/3000, /*qos_depth=*/2,
      /*qos_is_transient_local=*/true, /*is_bridge=*/false, &pa),
    0);
  KUNIT_ASSERT_EQ(
    test,
    agnocast_ioctl_add_subscriber(
      topic_name, ipc_ns, sub_node, /*pid=*/3001, /*qos_depth=*/3,
      /*qos_is_transient_local=*/false, /*qos_is_reliable=*/true,
      /*is_take_sub=*/false, /*ignore_local_publications=*/false,
      /*is_bridge=*/false, &sa),
    0);

  buf = kunit_kzalloc(test, READ_BUF_SIZE, GFP_KERNEL);
  KUNIT_ASSERT_NOT_NULL(test, buf);
  n = read_proc_file("/proc/agnocast/topic_info", buf, READ_BUF_SIZE);
  KUNIT_ASSERT_GT(test, n, 0);

  // Columns: ipc_ns_inode topic_name direction node_name pid qos_depth tl rel bridge
  // Publishers report qos_is_reliable as 0 (publishers don't carry that QoS).
  scnprintf(
    expect_pub, sizeof(expect_pub), " %s pub %s 3000 2 1 0 0\n", topic_name, pub_node);
  scnprintf(
    expect_sub, sizeof(expect_sub), " %s sub %s 3001 3 0 1 0\n", topic_name, sub_node);

  KUNIT_EXPECT_NOT_NULL_MSG(
    test, strstr(buf, expect_pub), "expected '%s' in:\n%s", expect_pub, buf);
  KUNIT_EXPECT_NOT_NULL_MSG(
    test, strstr(buf, expect_sub), "expected '%s' in:\n%s", expect_sub, buf);
}

void test_case_procfs_schema_version_header(struct kunit * test)
{
  const char * paths[] = {
    "/proc/agnocast/topics",
    "/proc/agnocast/nodes",
    "/proc/agnocast/topic_info",
  };
  char * buf = kunit_kzalloc(test, READ_BUF_SIZE, GFP_KERNEL);
  size_t i;
  ssize_t n;

  KUNIT_ASSERT_NOT_NULL(test, buf);

  for (i = 0; i < ARRAY_SIZE(paths); i++) {
    memset(buf, 0, READ_BUF_SIZE);
    n = read_proc_file(paths[i], buf, READ_BUF_SIZE);
    KUNIT_ASSERT_GT_MSG(test, n, 0, "read of %s returned %zd", paths[i], n);
    // First line must declare schema_version=1.
    KUNIT_EXPECT_TRUE_MSG(
      test, strncmp(buf, "# schema_version=1", 18) == 0,
      "first line of %s should declare schema_version=1, got: %.40s", paths[i], buf);
  }
}
