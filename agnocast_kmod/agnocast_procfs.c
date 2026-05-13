// SPDX-License-Identifier: GPL-2.0-only OR BSD-2-Clause
#include "agnocast_internal.h"

#include <linux/proc_fs.h>
#include <linux/seq_file.h>

#define AGNOCAST_PROC_DIR "agnocast"
#define AGNOCAST_PROC_SCHEMA_VERSION 1

static struct proc_dir_entry * agnocast_proc_dir;

static unsigned int ipc_ns_inode(const struct ipc_namespace * ipc_ns)
{
  return ipc_ns ? ipc_ns->ns.inum : 0;
}

static int topics_show(struct seq_file * s, void * v)
{
  struct topic_wrapper * wrapper;
  int bkt_topic;

  seq_printf(s, "# schema_version=%u\n", AGNOCAST_PROC_SCHEMA_VERSION);
  seq_puts(s, "# ipc_ns_inode topic_name pub_count sub_count\n");

  down_read(&global_htables_rwsem);

  hash_for_each(topic_hashtable, bkt_topic, wrapper, node)
  {
    uint32_t pub_count = 0;
    uint32_t sub_count = 0;
    struct publisher_info * pub_info;
    struct subscriber_info * sub_info;
    int bkt;

    down_read(&wrapper->topic_rwsem);

    hash_for_each(wrapper->topic.pub_info_htable, bkt, pub_info, node) pub_count++;
    hash_for_each(wrapper->topic.sub_info_htable, bkt, sub_info, node) sub_count++;

    up_read(&wrapper->topic_rwsem);

    seq_printf(
      s, "%u %s %u %u\n", ipc_ns_inode(wrapper->ipc_ns), wrapper->key, pub_count, sub_count);
  }

  up_read(&global_htables_rwsem);
  return 0;
}

static int topic_info_show(struct seq_file * s, void * v)
{
  struct topic_wrapper * wrapper;
  int bkt_topic;

  seq_printf(s, "# schema_version=%u\n", AGNOCAST_PROC_SCHEMA_VERSION);
  seq_puts(
    s,
    "# ipc_ns_inode topic_name direction node_name pid qos_depth "
    "qos_is_transient_local qos_is_reliable is_bridge\n");

  down_read(&global_htables_rwsem);

  hash_for_each(topic_hashtable, bkt_topic, wrapper, node)
  {
    struct publisher_info * pub_info;
    struct subscriber_info * sub_info;
    int bkt;
    unsigned int inum = ipc_ns_inode(wrapper->ipc_ns);

    down_read(&wrapper->topic_rwsem);

    hash_for_each(wrapper->topic.pub_info_htable, bkt, pub_info, node)
    {
      seq_printf(
        s, "%u %s pub %s %d %u %d %d %d\n", inum, wrapper->key,
        pub_info->node_name ? pub_info->node_name : "", pub_info->pid, pub_info->qos_depth,
        pub_info->qos_is_transient_local ? 1 : 0,
        /* publishers do not have qos_is_reliable; emit 0 for parity */ 0,
        pub_info->is_bridge ? 1 : 0);
    }

    hash_for_each(wrapper->topic.sub_info_htable, bkt, sub_info, node)
    {
      seq_printf(
        s, "%u %s sub %s %d %u %d %d %d\n", inum, wrapper->key,
        sub_info->node_name ? sub_info->node_name : "", sub_info->pid, sub_info->qos_depth,
        sub_info->qos_is_transient_local ? 1 : 0, sub_info->qos_is_reliable ? 1 : 0,
        sub_info->is_bridge ? 1 : 0);
    }

    up_read(&wrapper->topic_rwsem);
  }

  up_read(&global_htables_rwsem);
  return 0;
}

int agnocast_init_procfs(void)
{
  agnocast_proc_dir = proc_mkdir(AGNOCAST_PROC_DIR, NULL);
  if (!agnocast_proc_dir) {
    pr_warn("agnocast: failed to create /proc/%s\n", AGNOCAST_PROC_DIR);
    return -ENOMEM;
  }

  if (!proc_create_single("topics", 0444, agnocast_proc_dir, topics_show)) goto err;
  if (!proc_create_single("topic_info", 0444, agnocast_proc_dir, topic_info_show)) goto err;

  return 0;

err:
  proc_remove(agnocast_proc_dir);
  agnocast_proc_dir = NULL;
  return -ENOMEM;
}

void agnocast_exit_procfs(void)
{
  if (agnocast_proc_dir) {
    proc_remove(agnocast_proc_dir);
    agnocast_proc_dir = NULL;
  }
}
