# SQL through `docker exec` stays gated by the destructive hook

Decision (2026-10-23): a SQL client run through `docker exec <container>` is classified like any other client run; no local-container exemption.

Why. A replay of the hook's recorded denials found a group of 43 refusals of SQL sent into local scratch database containers (all labelled noise). Three exemption forms were measured against that group:

| Exemption form | Refusals of the 43 it clears | Why it lost |
|---|---|---|
| any container on a local daemon | 43 | the text does not prove the container is the agent's: a local copy of a production dump, an app container whose environment points at a remote server, or a client `--host` all take the write unasked |
| container carries the session's ours label (`orch.session`), read with `docker inspect` | 0 | no agent labelled its containers, so it adds a hook-side docker call and a trust boundary for no cleared refusal |
| `docker run --name X` earlier in the same command | 4 | the container usually starts in an earlier command |

Kept from the same work: docker global options (`-H`, `--host`, `--context`, `--config`, `--tls*`) and compose options are skipped before `exec`, so a remote-daemon `docker -H tcp://... exec` is classified instead of passing unseen.

Resume signal: agents start throwaway database containers with an ours label (`--label orch.session=<task>`) and this group still costs refusals. Then the labelled form clears it, with the daemon endpoint checked local (unix:// or npipe://), no `docker exec -e/--env/--env-file`, and an allowlist of local-only client options.
