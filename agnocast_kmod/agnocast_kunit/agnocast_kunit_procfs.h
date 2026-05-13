/* SPDX-License-Identifier: GPL-2.0-only OR BSD-2-Clause */
#pragma once
#include <kunit/test.h>

#define TEST_CASES_PROCFS                                                              \
  KUNIT_CASE(test_case_procfs_topics_empty),                                           \
    KUNIT_CASE(test_case_procfs_topics_with_pub_and_sub),                              \
    KUNIT_CASE(test_case_procfs_nodes_dedupe_same_node_multiple_topics),               \
    KUNIT_CASE(test_case_procfs_topic_info_direction_columns),                         \
    KUNIT_CASE(test_case_procfs_schema_version_header)

void test_case_procfs_topics_empty(struct kunit * test);
void test_case_procfs_topics_with_pub_and_sub(struct kunit * test);
void test_case_procfs_nodes_dedupe_same_node_multiple_topics(struct kunit * test);
void test_case_procfs_topic_info_direction_columns(struct kunit * test);
void test_case_procfs_schema_version_header(struct kunit * test);
