"""Seed for claim_mailbox_batch.orig.lua / .hmget.lua in this folder. argv: host port.
Prints the spec fragment (keys/argv/ignore_reply_indices) on stdout."""
import json, sys, time, redis

host, port = sys.argv[1], int(sys.argv[2])
r = redis.Redis(host=host, port=port)
TAG = "{inbound}"; GEN = "gen-q7"; PROC = "q7-process"
LEASE_TTL = 120000; WINDOW = 1024; BATCH = 32; N = 1024; VERSION = "7"
DUE = 1_700_000_000_000
K = [f"catalog:{TAG}:active_generation", f"catalog:{TAG}:cursor_coverage_generation",
     f"catalog:{TAG}:reconciled_server_run_id", f"catalog:{TAG}:last_reconciliation_started_ms",
     f"catalog:{TAG}:generation:{GEN}:mailbox_versions", f"imap:{TAG}:due",
     f"imap:{TAG}:leases", f"imap:{TAG}:lease_receipts", f"imap:{TAG}:diagnostic_owners",
     f"imap:{TAG}:poll_starts", f"catalog:{TAG}:active_grants_descriptor",
     f"catalog:{TAG}:active_mailbox_versions", f"catalog:{TAG}:mailbox_records",
     f"imap:{TAG}:lease_records"]
M = [f"{i:032x}" for i in range(N)]

def record(m):
    b = {"mailbox_id": m, "version": VERSION, "name": "INBOX", "uid_validity": 1,
         "captured_at_ms": 0, "filler": ""}
    b["filler"] = "p" * max(2048 - len(json.dumps(b)), 0)
    return json.dumps(b)

def run_id():
    return r.info("replication")["master_replid"]

if not r.exists(K[12]):
    now = int(time.time() * 1000)
    r.set(K[0], GEN); r.set(K[1], GEN); r.set(K[3], str(now - 1000)); r.delete(K[10])
    pipe = r.pipeline()
    for i in range(0, N, 256):
        c = M[i:i + 256]
        pipe.hset(K[4], mapping={m: VERSION for m in c})
        pipe.hset(K[12], mapping={f"{m}:{VERSION}": record(m) for m in c})
    pipe.execute()
r.set(K[2], run_id())
pipe = r.pipeline()
pipe.delete(K[5], K[6], K[7], K[8], K[9], K[13])
for i in range(0, N, 512):
    pipe.zadd(K[5], {m: DUE for m in M[i:i + 512]})
pipe.execute()

toks = [f"00000000-0000-4000-8000-{i:012d}" for i in range(BATCH)]
argv = ["1", GEN, run_id(), PROC, str(LEASE_TTL), str(WINDOW), "600000", str(BATCH)] + toks
print(json.dumps({"keys": K, "argv": argv,
                  "ignore_reply_indices": [1] + [7 + 5 * i for i in range(BATCH)]}))
