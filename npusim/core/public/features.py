# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
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

"""Report features and the helper collection categories they require."""

import argparse
import os
import shlex

# Existing base analyses consume instruction/dispatch data, SIMT addresses and SPR writes.
# Optional BiProf features add their dependencies, never CANNSIM_COLLECT=all.
BASE_COLLECTION = ("instr", "ccu", "simt", "spr")
FEATURES = {
    "scalar-ipc": (),
    "vec-ipc": (),
    "simd-perf": ("pmu",),
    "simd-sampling": ("simd",),
    "memory-utilization": ("mem",),
    "roofline": ("ub",),
    "sol": ("ddr",),
    "smart-pipes": ("ccu",),
    "critical-path": ("ccu",),
    "bubble": ("icache",),
    "vf-report-json": (),
}
# Keep the default policy separate from the dependency map.
DEFAULT_FEATURES = (
    "scalar-ipc",
    "vec-ipc",
    "simd-perf",
    "simd-sampling",
    "memory-utilization",
    "roofline",
    "sol",
    "bubble",
)
DEFAULT_FEATURE_LIST = ",".join(DEFAULT_FEATURES)
_SINGLE_CHIP_FEATURES = {"scalar-ipc", "vec-ipc", "smart-pipes"}
# Existing backend flags remain available through BIPROF_OPTS, but are not
# advertised as supported feature-list entries.
_LEGACY_FEATURES = {"call-stack": (), "matrix-copy": ("mem",)}
COLLECTION_ORDER = ("instr", "mem", "ub", "ccu", "pmu", "simd", "simt", "spr", "icache", "ddr")


def normalize_feature_list(value):
    if not value.strip():
        return ""
    names = list(dict.fromkeys(name.strip() for name in value.split(",")))
    invalid = [name for name in names if name not in FEATURES]
    if invalid:
        raise argparse.ArgumentTypeError(
            "Unknown feature(s): %s. Available: %s" % (", ".join(invalid), ",".join(FEATURES))
        )
    return ",".join(names)


def add_feature_list_argument(parser):
    parser.add_argument(
        "--feature-list",
        type=normalize_feature_list,
        default=None,
        metavar="FEATURES",
        help=(
            "Comma-separated report features (default: %s). An explicit list replaces "
            "the feature preset; use '' for base analysis only. Collection is selected "
            "automatically during record. Explicit CANNSIM_COLLECT is preserved."
        )
        % DEFAULT_FEATURE_LIST,
    )


def feature_list_from_args(args):
    parser = argparse.ArgumentParser(add_help=False)
    add_feature_list_argument(parser)
    return parser.parse_known_args(args)[0].feature_list


def _features_from_options(value):
    tokens = shlex.split(value)
    feature_list = feature_list_from_args(tokens)
    names = feature_list.split(",") if feature_list else []
    names.extend(name for name in (*FEATURES, *_LEGACY_FEATURES) if "--" + name in tokens)
    return tuple(dict.fromkeys(names))


def selected_features(feature_list=None, environ=None, multi_chip=False):
    env = os.environ if environ is None else environ
    if feature_list is None and "BIPROF_OPTS" in env:
        return _features_from_options(env["BIPROF_OPTS"])
    if feature_list is None:
        return tuple(name for name in DEFAULT_FEATURES if not multi_chip or name not in _SINGLE_CHIP_FEATURES)
    normalized = normalize_feature_list(feature_list)
    return tuple(normalized.split(",")) if normalized else ()


def collection_for_features(feature_list=None, environ=None, multi_chip=False):
    categories = set(BASE_COLLECTION)
    for name in selected_features(feature_list, environ, multi_chip):
        categories.update(FEATURES[name] if name in FEATURES else _LEGACY_FEATURES[name])
    return ",".join(name for name in COLLECTION_ORDER if name in categories)


def report_feature_defaults(feature_list=None, environ=None, multi_chip=False):
    names = set(selected_features(feature_list, environ, multi_chip))
    env = os.environ if environ is None else environ
    if "vf-report-json" in names and (not multi_chip or feature_list is not None or "BIPROF_OPTS" in env):
        names.add("vec-ipc")
    return {name.replace("-", "_"): name in names for name in (*FEATURES, *_LEGACY_FEATURES)}


def feature_environment_args(feature_list, environ=None):
    env = os.environ if environ is None else environ
    tokens = shlex.split(env.get("BIPROF_OPTS", ""))
    if feature_list is not None:
        flags = {"--" + name for name in (*FEATURES, *_LEGACY_FEATURES)}
        tokens = [token for token in tokens if token not in flags]
    return tokens
