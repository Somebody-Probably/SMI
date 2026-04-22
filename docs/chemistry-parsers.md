# Chemistry Parsers And Retry Policies

`research-chem parse-output` reads QE and ORCA output files and reports:

- completion sentinel;
- convergence state;
- final energy when available;
- recognized warnings and errors;
- retry policy and recommendation.

## QE

```bash
research-chem parse-output --program qe --output-file outputs/si.scf.out
```

Common retry policies:

- `none`: output completed cleanly.
- `fix_missing_pseudopotential`: input or staged files must be fixed before retry.
- `resubmit_with_more_time`: scheduler or walltime likely stopped the job.
- `retry_relaxed_scf_settings`: SCF convergence issue.
- `inspect_or_resubmit`: unclassified incomplete output.

## ORCA

```bash
research-chem parse-output --program orca --output-file outputs/water.out
```

Common retry policies:

- `none`: ORCA terminated normally.
- `fix_basis_or_input`: basis or input definition problem.
- `resubmit_with_more_memory`: memory limit issue.
- `retry_scf_settings`: SCF convergence issue.
- `inspect_or_resubmit`: unclassified incomplete output.

## Retry Orders

Create a new SMI seed order from a retryable failure:

```bash
research-chem make-retry-order \
  --program qe \
  --output-file outputs/si.scf.out \
  --input-file inputs/si.scf.in \
  --task-id qe-si-retry-001 \
  --output runs/qe-demo/orders/retry-si.json
```

For non-retryable failures, the command exits without writing an order unless `--force` is supplied.

