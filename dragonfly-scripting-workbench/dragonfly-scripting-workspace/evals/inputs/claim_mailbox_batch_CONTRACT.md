# Q7 — `claim_mailbox_batch.lua` invocation contract (verified 2026-09-16)

The script under test is `lab/variants/claim_mailbox_batch.orig.lua` (296 lines).

All keys carry the single hashtag `{inbound}`, so
`catalog:` prefix = `catalog:{inbound}`, `imap:` prefix = `imap:{inbound}`.

## KEYS — exactly N = 14

| # | literal key |
|---|---|
| 1 | `catalog:{inbound}:active_generation` |
| 2 | `catalog:{inbound}:cursor_coverage_generation` |
| 3 | `catalog:{inbound}:reconciled_server_run_id` |
| 4 | `catalog:{inbound}:last_reconciliation_started_ms` |
| 5 | `catalog:{inbound}:generation:<GEN>:mailbox_versions` |
| 6 | `imap:{inbound}:due` |
| 7 | `imap:{inbound}:leases` |
| 8 | `imap:{inbound}:lease_receipts` |
| 9 | `imap:{inbound}:diagnostic_owners` |
| 10 | `imap:{inbound}:poll_starts` |
| 11 | `catalog:{inbound}:active_grants_descriptor` |
| 12 | `catalog:{inbound}:active_mailbox_versions` |
| 13 | `catalog:{inbound}:mailbox_records` |
| 14 | `imap:{inbound}:lease_records` |

The caller passes KEYS and ARGV as listed; it reads `INFO replication` only to get `run_id`.

## ARGV — `#ARGV` must be exactly `8 + batch_size` (lua:74)

| # | value |
|---|---|
| 1 | `"1"` schema version |
| 2 | generation string; must equal the value stored at KEYS[1] |
| 3 | `run_id` = server `master_replid` from `INFO replication` |
| 4 | `process_id`, non-empty |
| 5 | `120000` exactly — script rejects anything else |
| 6 | candidate_window (the benchmark uses 1024) |
| 7 | `600000` freshness limit |
| 8 | `batch_size`, 1..=32 (**spec: 32**) |
| 9..8+batch | one non-empty UUIDv4 lease token per slot, matched by `^[0-9a-f-]+$` |

## Key types and seeding shapes

| key | type | field/member -> value |
|---|---|---|
| 1,2,3,4 | string | see fences |
| 5 (legacy) / 12 (mutable_v2) | hash | `<mailbox_id>` -> `<version>`; script picks KEYS[5] unless the descriptor says `mutable_v2` (lua:151-168) |
| 6 due | zset | member `<mailbox_id>`, score = due-at ms; must be `<= now` to be a candidate (lua:174-182) |
| 7 leases | hash | `<mailbox_id>` -> `"<token>:<deadline_ms>"`, regex `^([0-9a-f-]+):(%d+)$` (lua:65-73). Leave ABSENT for a free mailbox (spec: no leases) |
| 8 receipts | hash | `<token>` -> `"<mailbox_id>:<token>:<deadline_ms>"` (lua:71-81). Must be ABSENT for the request tokens, else the script short-circuits to RESOLVED (lua:95-118) |
| 9 diagnostic_owners | hash | `<mailbox_id>` -> `"<token>:<process_id>"` (lua:274) |
| 10 poll_starts | hash | `<mailbox_id>` -> `"<token>:<now_ms>"` (lua:275) |
| 11 descriptor | hash | fields `storage_format`, `state`, `generation`. Simplest seeding: DO NOT CREATE IT — all three nil means legacy layout and versions come from KEYS[5] |
| 13 mailbox_records | hash | field `"<mailbox_id>:<version>"` -> JSON record containing a matching `mailbox_id`; this is the ~2 KB payload (lua:213-217) |
| 14 lease_records | hash | `<token>` -> same record JSON (lua:276) |

`mailbox_id` must be lowercase hex `[0-9a-f]+` (receipt regex).
The only constraint on the record is "valid JSON whose `mailbox_id` field matches".
Pad it to ~2 KB with a filler field.

## Fences — all must pass for a claim to succeed

| fence | requirement | lua line |
|---|---|---|
| active generation | `GET KEYS[1] == ARGV[2]` | 133-136 |
| cursor coverage | `GET KEYS[2] == GET KEYS[1]` | 137-139 |
| server run | `GET KEYS[3] == ARGV[3]` (current `master_replid`) | 140-142 |
| freshness | `KEYS[4]` numeric ms, `<= now` and `now - it <= ARGV[7]`; `now` from server `TIME` | 60-63, 143-147 |
| descriptor | absent hash = OK; if `storage_format` set it must be `mutable_v2` + `state=committed` + `generation=active_generation`; `state` without `storage_format` -> PROTOCOL | 150-172 |
| receipts | no request token may already exist in KEYS[8] | 95-118 |
| record present | `HGET KEYS[13] "<mailbox_id>:<version>"` must exist for a claimable candidate | 213-216 |

## Reply shape

Flat array of bulk strings:
`["RESOLVED", now_ms, earliest_due_or_"", batch_size, <batch_size x 5 fields>]` where each
5-tuple is `[status, token, mailbox_id, deadline, record_json]` and status is
`CLAIMED` / `RECEIPT_EXPIRED` / `UNUSED` (lua:278-284, 306-310).
Other terminal forms: `{'NOT_DUE', now, earliest}`, `{'RETRY_IMMEDIATELY', now}`,
`{'CLEANUP_REQUIRED', now, mailbox_id, token, deadline}`, `{'FENCED', reason}`,
`{'PROTOCOL', reason}`.
