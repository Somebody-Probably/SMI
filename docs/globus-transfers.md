# Globus Transfers

`research-globus` automates repeatable input staging and output harvest through the official Globus CLI.

## Setup

Install and authenticate:

```bash
python -m pip install globus-cli
globus login
```

Create a config:

```bash
cp config/globus.example.json config/globus.json
```

Edit endpoint IDs and roots for your MacBook, cluster scratch, and group project storage.

## Commands

Render a transfer plan:

```bash
research-globus plan --config config/globus.json --transfer mac-to-cluster
```

Submit a transfer:

```bash
research-globus submit --config config/globus.json --transfer mac-to-cluster
```

Check status:

```bash
research-globus status <task-id>
research-globus wait <task-id> --timeout 3600
```

Transfer submissions are appended to `runs/globus-tasks.jsonl` by default so an SMI run can retain custody of staging and harvest events.

