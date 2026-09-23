# Data Sanitization Policy

The public CoreView Cloud Lab must contain synthetic data only.

## Never commit

- Production PRTG URLs or ports
- Credentials or passhash values
- Customer or employee names
- Production hostnames
- Internal or public production IP addresses
- Runtime sensor snapshots derived from production
- Database files or audit logs
- Uploaded alert audio
- Screenshots that expose production identifiers, URLs, tickets or topology

## Repository controls

The repository ignores local `.env`, `servers.json`, runtime JSON, SQLite databases and uploaded files. Public examples live in `.env.example` and `servers.example.json`.

## Synthetic address space

The simulator uses RFC 5737 documentation networks (`192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`) and localhost endpoints. These values are intentionally non-production.
