# Globus Transfers

`research-globus` automates repeatable input staging and output harvest through the official Globus CLI. The package installs `globus-cli` with the normal editable install.

## Setup

Install and authenticate:

```bash
python -m pip install -e .
research-globus doctor
globus login
research-globus doctor --check-login
```

Create a config:

```bash
cp config/globus.example.json config/globus.json
```

Edit endpoint IDs and roots for your MacBook, cluster scratch, and group project storage. Keep endpoint ids out of committed files; `config/globus.json` is ignored by git.

Useful Alliance mapped collection IDs observed during setup:

| Cluster | Globus display name | Collection ID |
| --- | --- | --- |
| Trillium | `alliancecan#trillium` | `ad462f99-8436-42b4-adc6-3644e36c1b67` |
| Fir | `computecanada#cedar-globus & alliancecan#fir-globus` | `8dec4129-9ab4-451d-a45f-5b4b8471f7a3` |
| Fir IPv6 | `alliancecan#fir-globus-ipv6` | `d6a86f93-b5de-4d26-ae5a-bcbec9cc6600` |
| Nibi | `alliancecan#nibi (formerly computecanada#graham-globus)` | `07baf15f-d7fd-4b6a-bf8a-5b5ef2e229d3` |
| Narval | `Compute Canada - Narval` | `a1713da6-098f-40e6-b3aa-034efe8b6e5b` |
| Rorqual | `alliancecan#rorqual` | `f19f13f5-5553-40e3-ba30-6c151b9d35d4` |

Each mapped collection may require one-time data-access consent before `ls` or
transfer commands work.

## Commands

Render a transfer plan:

```bash
research-globus plan --config config/globus.json --transfer mac-to-cluster
```

Submit a transfer:

```bash
research-globus submit --config config/globus.json --transfer mac-to-cluster
```

Harvest outputs:

```bash
research-globus submit --config config/globus.json --transfer cluster-to-mac
```

Check status:

```bash
research-globus status <task-id>
research-globus wait <task-id> --timeout 3600
```

Transfer submissions are appended to `runs/globus-tasks.jsonl` by default so an SMI run can retain custody of staging and harvest events.
