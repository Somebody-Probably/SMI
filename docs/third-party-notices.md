# Third-Party Notices

## Cluster-FRUC

This repository's QE manifest-worker pattern is inspired by Cluster-FRUC:

- Repository: <https://github.com/jgibb-s/Cluster-FRUC>
- Author: Josh Gibbs
- License: MIT

Cluster-FRUC's core ideas are a queue of QE inputs, lock-based job claiming, completion detection using QE output sentinels, and retry generation for failed geometries. This repo reimplements those ideas in a config-driven form for a broader computational chemistry workflow.

