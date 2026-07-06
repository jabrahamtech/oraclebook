# Security Policy

## Reporting a vulnerability

Preferred: use GitHub's [private vulnerability reporting](https://github.com/jabrahamtech/oraclebook/security/advisories/new)
(the **Security** tab on this repo). If that's not available to you, open a regular issue
asking for a private channel and we'll coordinate from there.

There is no bounty program.

## No secrets live in this repository

Plainly: **no private keys, API credentials, or fund custody exist anywhere in this
repository.** The live Polymarket executor reads credentials exclusively from environment
variables at runtime (see `.env` setup in [GETTING_STARTED.md](GETTING_STARTED.md)) — never
from a committed file. Treat any file in this repo that claims otherwise, or any fork that adds
one, as a red flag.

## Supported versions

oraclebook is pre-alpha. Only the latest `main` is supported; there are no maintained release
branches yet.
