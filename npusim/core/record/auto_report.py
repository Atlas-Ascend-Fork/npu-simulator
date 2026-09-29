# -*- coding: utf-8 -*-
#!/usr/bin/env python3
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

"""Auto-report generation for successful cannsim record runs."""

from pathlib import Path

from npusim.core.public.comm import print_title
from npusim.core.report.report import report


def generate_auto_report(
    record_dir: Path,
    target_dir: Path,
    gen_report: str,
    core_id: str,
    object_file: str = "",
) -> bool:
    export_vf_json = gen_report == "vf"
    print()
    print_title("(Report) NPU SIM auto generate report...")
    export_path = str(record_dir)
    output_report = str(Path(target_dir) / "report")
    report_core_id = core_id if core_id else "0"
    gen_ok = report(
        export=export_path,
        output=output_report,
        core_id=report_core_id,
        object_file=object_file or None,
        export_vf=export_vf_json,
    )
    if gen_ok:
        print_title("(Report) NPU SIM auto generate report succeeded!")
        print(f"Report saved at:\n └─{output_report}")
    else:
        print_title("(Report) NPU SIM auto generate report failed!")
    return gen_ok
