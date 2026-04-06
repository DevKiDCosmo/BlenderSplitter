# BlenderSplitter — Protocol Sequence Diagrams

_Last updated: 2026-04-06_

All diagrams use [Mermaid](https://mermaid.js.org/) syntax and render directly
in GitHub.

---

## 1  Normal Job Distribution

Covers the full lifecycle of a single distributed render: workers connect,
master starts a render, tiles are distributed, results are collected, and the
final image is stitched.

```mermaid
sequenceDiagram
    participant M  as Master (server)
    participant W1 as Worker 1
    participant W2 as Worker 2

    W1->>M: MSG_REGISTER_WORKER
    M->>W1: MSG_REGISTERED
    W2->>M: MSG_REGISTER_WORKER
    M->>W2: MSG_REGISTERED

    Note over M: start_distributed_render()
    Note over M: generate_tiles() → N tiles
    Note over M: pre-distribute ceil(N/3)/targets jobs

    M->>W1: MSG_RENDER_TILE (tile_0)
    M->>W2: MSG_RENDER_TILE (tile_1)
    M->>M: _task_queue ← tile_2  (MASTER renders locally)

    par worker renders happen concurrently
        W1->>M: MSG_TILE_RESULT (tile_0 PNG)
        W2->>M: MSG_TILE_RESULT (tile_1 PNG)
    and master renders locally
        Note over M: _render_tile_local(tile_2)
    end

    Note over M: _consume_tile_result() × 3
    M->>W1: MSG_RENDER_TILE (tile_3)
    M->>W2: MSG_RENDER_TILE (tile_4)

    loop until all N tiles complete
        W1->>M: MSG_TILE_RESULT
        M->>W1: MSG_RENDER_TILE (next)
        W2->>M: MSG_TILE_RESULT
        M->>W2: MSG_RENDER_TILE (next)
    end

    Note over M: _finalize_render()
    Note over M: tile audit pass (re-queue any missing)
    Note over M: stitch_tiles() → final PNG
```

---

## 2  Worker Failure and Tile Reassignment

Shows what happens when a worker disconnects mid-render (connection drop,
crash, or stale-worker expiry).

```mermaid
sequenceDiagram
    participant M  as Master
    participant W1 as Worker 1 (healthy)
    participant W2 as Worker 2 (fails)

    M->>W1: MSG_RENDER_TILE (tile_A)
    M->>W2: MSG_RENDER_TILE (tile_B)

    W2--xM: (connection drops)

    Note over M: WebSocket context exits → _handle_worker_socket finally block
    Note over M: _reassign_jobs_from_worker(W2)
    Note over M: _reassign_tile(tile_B, attempt+1)
    Note over M: dispatch tile_B to W1 (or MASTER)

    M->>W1: MSG_RENDER_TILE (tile_B retry 1)
    W1->>M: MSG_TILE_RESULT (tile_A)
    W1->>M: MSG_TILE_RESULT (tile_B retry 1)

    Note over M: all tiles complete → _finalize_render()
```

### Stale-Worker Expiry (BUG-18 fix)

Workers send `MSG_HEARTBEAT` every 30 s.  The master's timer callback runs
`_purge_stale_workers()` every 30 s.  Any worker whose `last_seen` is more than
90 s old is treated as disconnected and its pending tiles are reassigned.

```mermaid
sequenceDiagram
    participant M   as Master
    participant W   as Worker (stale)

    M->>W: MSG_RENDER_TILE (tile_X)
    Note over W: Worker hangs / network partition
    Note over M: 30s timer fires → _purge_stale_workers()
    Note over M: W.last_seen > 90s ago → mark stale
    Note over M: _reassign_jobs_from_worker(W)
    Note over M: tile_X → MASTER queue
    Note over M: W removed from connected_workers
```

---

## 3  Project Sync / Parallel Download

The master sends the `.blend` bundle to **all workers simultaneously** using
`asyncio.gather`.  Previously this was sequential — total transfer time was
O(n·size).  After the fix it is O(1·size) (bounded by the slowest link).

```mermaid
sequenceDiagram
    participant M  as Master
    participant W1 as Worker 1
    participant W2 as Worker 2
    participant W3 as Worker 3

    Note over M: _sync_project_to_workers_async()
    Note over M: asyncio.gather(_send_to_worker × N)

    par parallel chunk streams
        M->>W1: MSG_PROJECT_SYNC_START
        M->>W1: MSG_PROJECT_SYNC_CHUNK × K
        M->>W1: MSG_PROJECT_SYNC_COMPLETE
    and
        M->>W2: MSG_PROJECT_SYNC_START
        M->>W2: MSG_PROJECT_SYNC_CHUNK × K
        M->>W2: MSG_PROJECT_SYNC_COMPLETE
    and
        M->>W3: MSG_PROJECT_SYNC_START
        M->>W3: MSG_PROJECT_SYNC_CHUNK × K
        M->>W3: MSG_PROJECT_SYNC_COMPLETE
    end

    W1->>M: MSG_PROJECT_SYNC_ACK {ok: true}
    W2->>M: MSG_PROJECT_SYNC_ACK {ok: true}
    W3->>M: MSG_PROJECT_SYNC_ACK {ok: true}

    Note over M: all ACKs received → proceed to render
```

### Sync Failure Handling

If a worker's connection drops mid-transfer or the ACK times out, its result
is marked `ok: false` and the sync is reported as partially failed.  The
master can retry (by calling `sync_project_files()` again) or abort.

```mermaid
sequenceDiagram
    participant M  as Master
    participant W1 as Worker 1 (ok)
    participant W2 as Worker 2 (fails)

    par
        M->>W1: MSG_PROJECT_SYNC_START + CHUNKs + COMPLETE
        W1->>M: MSG_PROJECT_SYNC_ACK {ok: true}
    and
        M--xW2: MSG_PROJECT_SYNC_START (connection error)
        Note over M: project_sync_results[W2] = {ok:false, error:"..."}
    end

    Note over M: failed = [W2]
    Note over M: status = "Projekt-Sync fehlgeschlagen: 1/2"
    Note over M: master can re-run sync_project_files()
```

---

## 4  Render Finalization and Tile Audit

Before stitching, `_finalize_render` runs a **tile audit pass** to detect tiles
that were claimed by a now-dead worker but never completed.  Those tiles are
re-queued and the dispatch loop continues until all are resolved.

```mermaid
sequenceDiagram
    participant M  as Master

    Note over M: all expected_jobs done (len(completed_jobs) >= expected_jobs)
    Note over M: _finalize_render() called

    rect rgb(255,240,200)
        Note over M: Tile audit pass
        loop for each tile_id in job_attempts
            alt tile in completed_jobs
                Note over M: OK — skip
            else tile in pending_jobs
                Note over M: _reassign_tile(tile_id, "audit: no result")
            else tile lost (not in queue or pending)
                Note over M: re-append job to job_queue
            end
        end
    end

    alt missing tiles remain
        Note over M: return early — dispatch loop continues
    else all tiles complete
        Note over M: stitch_tiles() → final PNG
        Note over M: _advance_batch_camera() (if batch render)
    end
```

---

## 5  Force Server / Master Takeover

A worker can request to take over the server role.  The old master broadcasts
the new master's address to all remaining workers before handing off.

```mermaid
sequenceDiagram
    participant W_A as Worker A (wants master)
    participant M_B as Old Master B
    participant W_C as Worker C

    W_A->>M_B: MSG_SERVER_TAKEOVER
    Note over M_B: _handle_worker_socket processes MSG_SERVER_TAKEOVER

    M_B->>W_C: MSG_NEW_MASTER {host: W_A.ip, port: W_A.port}
    Note over M_B: closes socket for W_A → grants role

    Note over W_A: _start_server() → becomes new master
    Note over W_C: receives MSG_NEW_MASTER
    Note over W_C: _pending_new_master = {host, port}
    Note over W_C: sleep MSG_NEW_MASTER_DELAY_S (2s)
    W_C->>W_A: WebSocket connect (new master)
    W_C->>W_A: MSG_REGISTER_WORKER
    W_A->>W_C: MSG_REGISTERED
```

---

## 6  UDP Discovery

```mermaid
sequenceDiagram
    participant N  as New Node
    participant LAN as LAN Broadcast
    participant M  as Existing Master

    N->>LAN: UDP "BLENDER_SPLITTER_DISCOVERY_V3" → 255.255.255.255:8766
    LAN-->>N: (no reply if no master)
    Note over N: timeout → becomes master (self-host)

    Note over N: --- later, M already running ---
    N->>LAN: UDP "BLENDER_SPLITTER_DISCOVERY_V3"
    M->>N: UDP "BLENDER_SPLITTER_SERVER_V3{host, port, version:v3}"
    Note over N: checks version == "v3" (BUG-22 fix)
    N->>M: WebSocket connect
    N->>M: MSG_REGISTER_WORKER
    M->>N: MSG_REGISTERED
```

---

## 7  Integrity Check

Before starting a distributed render the master verifies that all workers have
the same render configuration (engine, resolution, seed).

```mermaid
sequenceDiagram
    participant M  as Master
    participant W1 as Worker 1
    participant W2 as Worker 2

    Note over M: run_integrity_check()
    Note over M: compute render_signature (engine + resolution + seed)

    par broadcast
        M->>W1: MSG_INTEGRITY_PROBE {render_signature}
        M->>W2: MSG_INTEGRITY_PROBE {render_signature}
    end

    W1->>M: MSG_INTEGRITY_RESULT {ok: true}
    W2->>M: MSG_INTEGRITY_RESULT {ok: true}

    Note over M: all ok → proceed to render
```
