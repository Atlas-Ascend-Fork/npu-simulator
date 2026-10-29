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

from typing import Sequence, Type

from npusim.prof.src.backend.base.hint_manager_base import HintManagerBase
from npusim.prof.src.backend.hints.base.hint_base import IHint
from npusim.prof.src.backend.hints.shared_bank_conflicts_hint import (
    SharedBankConflictsHint,
)
from npusim.prof.src.backend.hints.mte_calls_hint import MTECallsHint
from npusim.prof.src.backend.hints.vector_utilization_hint import VectorUtilizationHint
from npusim.prof.src.backend.hints.mix_gm_hint import MixGMHint
from npusim.prof.src.backend.hints.access_pattern_hint import AccessPatternHint
from npusim.prof.src.backend.hints.small_grid_hint import SmallGridHint
from npusim.prof.src.backend.hints.tail_effect_inter_core_hint import TailEffectInterCoreHint
from npusim.prof.src.backend.hints.tail_effect_intra_core_hint import TailEffectIntraCoreHint
from npusim.prof.src.backend.hints.single_core_hint import SingleCoreHint
from npusim.prof.src.backend.hints.aic_underutilization_hint import (
    AicUnderutilizationHint,
)
from npusim.prof.src.backend.hints.aiv_underutilization_hint import (
    AivUnderutilizationHint,
)
from npusim.prof.src.backend.hints.low_vec_mte2_overlap_hint import (
    LowVecMte2OverlapHint,
)
from npusim.prof.src.backend.hints.low_vec_ipc_hint import LowVecIpcHint
from npusim.prof.src.backend.hints.branch_divergence_hint import (
    BranchDivergenceHint,
)
from npusim.prof.src.backend.hints.memory_access_hint import MemoryAccessHint
from npusim.prof.src.backend.hints.smem_bar_hint import SmemBarHint
from npusim.prof.src.backend.hints.warp_occupancy_hint import WarpOccupancyHint
from npusim.prof.src.backend.hints.vector_fusion_hint import VectorFusionHint
from npusim.prof.src.backend.hints.vf_pmu_bottleneck_hint import VfPmuBottleneckHint
from npusim.prof.src.backend.hints.vf_pmu_reg_pressure_hint import VfPmuRegPressureHint
from npusim.prof.src.backend.hints.vf_pmu_fetch_stall_hint import VfPmuFetchStallHint
from npusim.prof.src.backend.hints.vf_pmu_dep_stall_hint import VfPmuDepStallHint
from npusim.prof.src.backend.hints.vf_pmu_membar_stall_hint import (
    VfPmuMembarStallHint,
)
from npusim.prof.src.backend.hints.vf_pmu_valu_cflt_hint import VfPmuValuCfltHint
from npusim.prof.src.backend.hints.vf_pmu_queue_full_hint import VfPmuQueueFullHint
from npusim.prof.src.backend.hints.vf_pmu_exq_imbalance_hint import (
    VfPmuExqImbalanceHint,
)
from npusim.prof.src.backend.hints.vf_pmu_ub_bw_hint import VfPmuUbBwHint
from npusim.prof.src.backend.hints.vf_pmu_gather_scatter_stall_hint import (
    VfPmuGatherScatterStallHint,
)
from npusim.prof.src.backend.hints.vf_pmu_worst_vf_hint import VfPmuWorstVfHint


class HintManager(HintManagerBase):
    # Static methods instead of simple class-level sequences
    # allow to use conditional elements
    @staticmethod
    def david_hints() -> Sequence[Type[IHint]]:
        _hints = (
            SharedBankConflictsHint,
            MTECallsHint,
            VectorUtilizationHint,
            MixGMHint,
            AccessPatternHint,
            SmallGridHint,
            TailEffectIntraCoreHint,
            TailEffectInterCoreHint,
            SingleCoreHint,
            AicUnderutilizationHint,
            AivUnderutilizationHint,
            LowVecMte2OverlapHint,
            LowVecIpcHint,
            BranchDivergenceHint,
            MemoryAccessHint,
            SmemBarHint,
            WarpOccupancyHint,
            VectorFusionHint,
            VfPmuBottleneckHint,
            VfPmuRegPressureHint,
            VfPmuFetchStallHint,
            VfPmuDepStallHint,
            VfPmuMembarStallHint,
            VfPmuValuCfltHint,
            VfPmuQueueFullHint,
            VfPmuExqImbalanceHint,
            VfPmuUbBwHint,
            VfPmuGatherScatterStallHint,
            VfPmuWorstVfHint,
        )
        return _hints

    def __init__(self) -> None:
        super().__init__()
        self.supported_hints = list(self.david_hints())
