# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for npusim.core.report.detections.vector_clock."""

import unittest

from npusim.core.report.detections.vector_clock import (
    barrier_sync,
    has_happens_before_relation,
    is_concurrent_partial,
    signal_acquire,
    signal_store,
    update_logic_time,
    update_vector_time,
    vector_clock_le,
)


class UpdateLogicTimeTests(unittest.TestCase):
    def test_increments_the_given_index(self):
        vt = [0, 1, 2]
        update_logic_time(vt, 1)
        self.assertEqual(vt, [0, 2, 2])

    def test_out_of_range_index_is_a_no_op(self):
        vt = [1, 2]
        update_logic_time(vt, 5)
        update_logic_time(vt, -1)
        self.assertEqual(vt, [1, 2])


class UpdateVectorTimeTests(unittest.TestCase):
    def test_takes_component_wise_max_into_dst(self):
        src = [5, 0, 3]
        dst = [1, 4, 3]
        update_vector_time(src, dst)
        self.assertEqual(dst, [5, 4, 3])
        self.assertEqual(src, [5, 0, 3])  # src untouched

    def test_shorter_vectors_are_truncated(self):
        update_vector_time([9, 9, 9], [1, 1])
        self.assertEqual([1, 1], [1, 1])  # dst unchanged beyond its length


class IsConcurrentPartialTests(unittest.TestCase):
    def test_concurrent_when_each_pipe_leads_on_one_dimension(self):
        # t1 leads on p1, t2 leads on p2 -> neither happens-before
        self.assertTrue(is_concurrent_partial((1, 0), (0, 1), p1=0, p2=1))
        self.assertTrue(is_concurrent_partial((0, 1), (1, 0), p1=0, p2=1))

    def test_happens_before_is_not_concurrent(self):
        # t1 <= t2 on both dimensions -> ordered
        self.assertFalse(is_concurrent_partial((1, 0), (2, 0), p1=0, p2=1))
        self.assertFalse(is_concurrent_partial((2, 0), (1, 0), p1=0, p2=1))

    def test_same_pipe_is_never_concurrent(self):
        self.assertFalse(is_concurrent_partial((5, 0), (0, 5), p1=1, p2=1))

    def test_out_of_range_pipe_is_not_concurrent(self):
        self.assertFalse(is_concurrent_partial((1, 0), (0, 1), p1=0, p2=9))


class VectorClockLeTests(unittest.TestCase):
    def test_leq_holds_component_wise(self):
        self.assertTrue(vector_clock_le([1, 2], [1, 3]))
        self.assertTrue(vector_clock_le([1, 2], [1, 2]))
        self.assertFalse(vector_clock_le([2, 2], [1, 3]))

    def test_shorter_vectors_compare_only_common_prefix(self):
        self.assertTrue(vector_clock_le([1, 2, 9], [1, 2]))


class HasHappensBeforeRelationTests(unittest.TestCase):
    def test_none_snapshots_report_no_relation(self):
        self.assertFalse(has_happens_before_relation(None, [1]))
        self.assertFalse(has_happens_before_relation([1], None))
        self.assertFalse(has_happens_before_relation(None, None))

    def test_either_direction_counts(self):
        self.assertTrue(has_happens_before_relation([1, 0], [2, 0]))
        self.assertTrue(has_happens_before_relation([2, 0], [1, 0]))

    def test_concurrent_has_no_relation(self):
        self.assertFalse(has_happens_before_relation([1, 0], [0, 1]))


class SignalAcquireTests(unittest.TestCase):
    def test_merges_all_participant_snapshots(self):
        signal = {}
        signal_store(signal, flag_id=7, participant=0, vc=[3, 0])
        signal_store(signal, flag_id=7, participant=1, vc=[0, 4])
        vc = [1, 1]
        self.assertTrue(signal_acquire(signal, 7, vc))
        self.assertEqual(vc, [3, 4])

    def test_missing_flag_returns_false_and_leaves_vc_untouched(self):
        vc = [1, 1]
        self.assertFalse(signal_acquire({}, 99, vc))
        self.assertEqual(vc, [1, 1])


class BarrierSyncTests(unittest.TestCase):
    def test_all_participants_merge_to_component_wise_max_and_advance(self):
        vc = {0: [2, 1], 1: [1, 3], 2: [0, 0]}
        idx = {0: 0, 1: 1, 2: 2}
        barrier_sync(vc, idx, participants=[0, 1])
        # participants merge to [2, 3] and each advances its own dimension
        self.assertEqual(vc[0], [3, 3])
        self.assertEqual(vc[1], [2, 4])
        # non-participant keeps its own clock
        self.assertEqual(vc[2], [0, 0])

    def test_empty_participants_is_a_no_op(self):
        vc = {0: [1]}
        barrier_sync(vc, {0: 0}, participants=[])
        self.assertEqual(vc[0], [1])

    def test_missing_participant_clock_is_tolerated(self):
        vc = {}
        barrier_sync(vc, {}, participants=[0])
        self.assertEqual(vc[0], [1])


if __name__ == "__main__":
    unittest.main()
