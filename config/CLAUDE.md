# localAIStack Config Guidance

This directory contains local AI stack configuration and dashboards.

- Keep RE/PE endpoint defaults aligned with the instance registry contract.
- Do not assume static config matches the live universe; verify when testing.
- Avoid committing secrets or local-only runtime values.
- Use YAML/JSON schema support where applicable.

## Standing rules

The standing rules are in
`../../RealityEngine_CI/docs/ENGINEERING_CONTRACT.md`, which is
authoritative. They are not listed or summarised here, so this file never
falls behind the contract. Read it before changing anything here.
