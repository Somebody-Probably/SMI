# Alliance Cluster Profiles

This repo keeps cluster defaults deliberately conservative. The safest generic
submission default is only:

```bash
--account={account}
```

The SLURM script should normally own `--time`, `--nodes`, `--ntasks`,
`--cpus-per-task`, memory, partition, and GPU requests. Named profiles are
available for lab workflows that have been checked against a real setup.

## Current National Systems

The 2025 Alliance renewal changes the names researchers will see in current
planning:

| Current system | Replaces | Notes |
| --- | --- | --- |
| `trillium` | Niagara and Mist | Large parallel CPU/GPU system at Toronto. |
| `fir` | Cedar | General-purpose CPU/GPU/storage system at SFU. |
| `nibi` | Graham | General-purpose CPU/GPU/storage system at Waterloo. |
| `rorqual` | Beluga | General-purpose CPU/GPU/storage system at McGill/Calcul Quebec. |
| `narval` | unchanged | General-purpose CPU/GPU/storage system. |

The example config also records legacy aliases so agents can map old names to
the current targets instead of silently choosing a stale cluster.

## QFF-Derived Profiles

The Trillium defaults in this repository now come from the in-use
`qff_research/trillium` scripts, not guesses:

- `qff-trillium-debug`: `test_job.sh`; debug partition, one node, one task,
  eight CPUs, 8 GB, five minutes.
- `qff-trillium-cpu-64`: `submit_64.sh`; one node, one task, 32 CPUs, 32 GB,
  one hour.
- `qff-trillium-full-node`: heavy render/test style; one node, one task,
  192 CPUs, `--mem=0`, up to 24 hours.

The Nibi and Narval GPU profiles are also QFF-derived examples. They remain
opt-in because partition and GRES names are site policy and should be verified:

```bash
sinfo -s
sinfo -o "%P %G %c %m %l"
sacctmgr show assoc user=$USER format=Account,Cluster,Partition,QOS
```

## Sources

- Alliance ARC refresh spring update:
  https://alliancecan.ca/en/latest/news/arc-refresh-spring-update
- Alliance available resources page:
  https://alliancecan.ca/en/services/advanced-research-computing/accessing-resources/resource-allocation-competition/available-resources
- SciNet Trillium announcement:
  https://scinethpc.ca/news/trillium-to-replace-niagara-and-mist-in-2025/
- SHARCNET 2025 migration notes:
  https://helpwiki.sharcnet.ca/wiki/Webinar_2025_Migrating_to_the_upgraded_national_systems
- Mila DRAC cluster notes:
  https://docs.mila.quebec/technical_reference/clusters/drac/
