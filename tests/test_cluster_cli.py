import json
from pathlib import Path

import pytest

from research_cluster_smi.cluster_cli import ConfigError, expand, sbatch_defaults_for


def test_sbatch_profile_expansion() -> None:
    cluster = {
        "name": "trillium",
        "account": "rrg-ipaci-ab",
        "user": "fouquet",
        "default_sbatch_profile": "debug",
        "sbatch_profiles": {
            "debug": {
                "sbatch_defaults": [
                    "--account={account}",
                    "--nodes=1",
                    "--ntasks=1",
                    "--cpus-per-task=8",
                    "--mem=8G",
                ]
            }
        },
    }

    defaults = [expand(flag, cluster) for flag in sbatch_defaults_for(cluster)]

    assert defaults == [
        "--account=rrg-ipaci-ab",
        "--nodes=1",
        "--ntasks=1",
        "--cpus-per-task=8",
        "--mem=8G",
    ]


def test_unknown_sbatch_profile_fails() -> None:
    cluster = {"sbatch_profiles": {"account-only": {"sbatch_defaults": []}}}
    with pytest.raises(ConfigError):
        sbatch_defaults_for(cluster, "missing")


def test_example_config_has_current_alliance_clusters() -> None:
    config = json.loads(Path("config/clusters.example.json").read_text(encoding="utf-8"))

    assert {"trillium", "fir", "nibi", "narval", "rorqual"} <= set(config["clusters"])
    assert config["legacy_aliases"]["niagara"] == "trillium"
    assert config["legacy_aliases"]["cedar"] == "fir"
    assert config["clusters"]["trillium"]["sbatch_profiles"]["qff-trillium-debug"]["sbatch_defaults"] == [
        "--account={account}",
        "--time=00:05:00",
        "--nodes=1",
        "--ntasks=1",
        "--cpus-per-task=8",
        "--mem=8G",
        "--partition=debug",
    ]
