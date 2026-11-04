# Cloud-Claw Video Production Orchestrator Rules

This document defines the agent routing, safety constraints, state machine, and bridge contracts for executing dual-skill video production inside the Cloud-Claw Linux Cloudflare Container.

---

## 1. Role Division

```
                      ┌────────────────────────────────────────┐
                      │               User Request             │
                      └───────────────────┬────────────────────┘
                                          │
                                          ▼
                      ┌────────────────────────────────────────┐
                      │          Cloud-Claw Orchestrator       │
                      │  - Human dialog & approval gate        │
                      │  - D1 job state & R2 pre-signed URLs   │
                      │  - Sandboxed execution bridge          │
                      └──────────────┬──────────────────┬──────┘
                                     │                  │
               Phase 3, 4, 5         │                  │ Phase 2, 6, 7
                                     ▼                  ▼
┌──────────────────────────────────────┐      ┌──────────────────────────────────────┐
│        browser-use/video-use         │      │         kajisho5/ffmpeg-skill        │
│  "Producer / Rough-Cut & Motion"     │      │   "Finishing & Mastering Engineer"   │
│  - Multi-take transcription ingestion│      │  - Source preflight & stream probe   │
│  - Scribe/Whisper phrase packing     │      │  - Multicam audio sync (pre-edit)    │
│  - Story strategy & EDL proposal     │      │  - HDR→SDR tone mapping & color LUTs │
│  - Motion overlays (HyperFrames/     │      │  - EBU R128 loudness normalization   │
│    Remotion/Manim/PIL)               │      │  - Platform export & spec compliance │
│  - Waveform/filmstrip cut check      │      │  - Contact sheet inspection (look.py)│
└──────────────────────────────────────┘      └──────────────────────────────────────┘
```

- **Cloud-Claw**: The sole user-facing agent and coordinator. Manages tenant isolation, job lifecycle, human approvals, and D1/R2 persistence. **Never exposes raw bash/shell execution to user prompts.**
- **`video-use`**: Creative rough-cut director. Synthesizes footage transcripts, identifies best takes, drafts narrative story outlines, defines edit decision lists (EDLs), generates animated overlays, and assembles the rough cut.
- **`ffmpeg-skill`**: Deterministic technical mastering. Probes media properties, syncs multicam clocks, normalizes audio to target broadcast/social specs (e.g. -14 LUFS), applies HDR→SDR tonemapping, validates platform specs via `check.py`, and renders visual contact sheets via `look.py`.

---

## 2. Hard Security & Execution Boundaries

1. **Sandboxed Subprocess Invocation Only:**
   - Cloud-Claw must NEVER run user strings as shell commands (`exec(user_input)` is forbidden).
   - All operations must be invoked as structured argument lists (`argv`) pointing to verified scripts:
     - Python venv: `/opt/cloudclaw/video-venv/bin/python`
     - Video-use helpers: `/opt/cloudclaw/skills/video-use/helpers/<name>.py`
     - Local Whisper: `/opt/cloudclaw/bin/whisper_transcribe.py`
     - FFmpeg-Skill: `/usr/bin/python3 /opt/cloudclaw/skills/ffmpeg-skill/scripts/<name>.py`
2. **Strict Job Path Confinement:**
   - All source and intermediate media must reside strictly inside `/work/jobs/<job_id>/`.
   - Never allow path traversal (`../`) or absolute paths outside the assigned tenant workspace.
3. **No Unauthenticated External Leakage:**
   - Default `TRANSCRIBE_PROVIDER=local-whisper`. Do not upload customer footage or audio to external APIs (ElevenLabs, OpenAI) unless explicitly authorized by tenant configuration and injected via secure runtime secret bindings.
4. **Per-Task Timeouts:**
   - Every subprocess call must be wrapped in an explicit timeout (e.g. 300s for transcription, 600s for render). Containers have inactivity caps; long tasks must heartbeat job progress to D1.

---

## 3. Project Workflow & State Transitions

Each job progresses through explicit states stored in D1:

```
[INGESTED] ──> [PROBED] ──> [TRANSCRIBED] ──> [STRATEGY_REVIEW] ──> (Human Approval)
                                                                          │
[DELIVERED] <── [QC] <── [FINISHING] <── [ROUGH_RENDER] <─────────────────┘
```

### State 1: `INGESTED`
- Tenant creates job via Cloud-Claw API.
- Cloud-Claw generates scoped, signed PUT URLs to R2 under `tenants/<tenant_id>/jobs/<job_id>/sources/`.
- Media is downloaded into container scratch at `/work/jobs/<job_id>/sources/`.

### State 2: `PROBED`
- Cloud-Claw invokes `ffmpeg-skill`'s `probe.py`:
  ```bash
  python3 /opt/cloudclaw/skills/ffmpeg-skill/scripts/probe.py \
    /work/jobs/<job_id>/sources/take1.mp4 --json
  ```
- Collects duration, resolution, fps, codecs, pixel format, and audio tracks.
- If multiple camera angles or separate mic tracks exist, invoke `sync.py` to establish time offsets before rough cutting.

### State 3: `TRANSCRIBED`
- Run local Whisper adapter for each source clip:
  ```bash
  /opt/cloudclaw/video-venv/bin/python /opt/cloudclaw/bin/whisper_transcribe.py \
    /work/jobs/<job_id>/sources/take1.mp4 \
    --edit-dir /work/jobs/<job_id>/edit \
    --model small --device cpu --compute-type int8
  ```
- Pack Scribe-compatible word-level JSON files into readable markdown:
  ```bash
  /opt/cloudclaw/video-venv/bin/python /opt/cloudclaw/skills/video-use/helpers/pack_transcripts.py \
    --edit-dir /work/jobs/<job_id>/edit
  ```
- Produces `/work/jobs/<job_id>/edit/takes_packed.md`.

### State 4: `STRATEGY_REVIEW` *(Mandatory Human Gate)*
- `video-use` reads `takes_packed.md`, identifies takes, retakes, and pauses.
- Cloud-Claw presents the **Plain-English Story Outline & Cut Plan** to the user:
  - Sequence of selected phrases / takes.
  - Proposed cuts and retake eliminations.
  - Planned motion graphics or overlay animation slots.
  - Intended total duration and pacing.
- **HALT EXECUTION:** Cloud-Claw waits for user confirmation. **Never render the rough cut without explicit user sign-off.**

### State 5: `ROUGH_RENDER`
- Generate `edl.json` and any required overlay assets (HyperFrames / Remotion / Manim / PIL cards).
- Execute `video-use` render pipeline:
  ```bash
  /opt/cloudclaw/video-venv/bin/python /opt/cloudclaw/skills/video-use/helpers/render.py \
    /work/jobs/<job_id>/edit/edl.json \
    -o /work/jobs/<job_id>/edit/rough.mp4 \
    --build-subtitles
  ```
- Check cut transitions using `timeline_view.py` for filmstrip + waveform validation at boundary points.

### State 6: `FINISHING`
- Hand over `rough.mp4` to `ffmpeg-skill` for technical mastering:
  - Loudness normalization (e.g. YouTube/Reels standard -14 LUFS, -1 dBFS TP):
    ```bash
    python3 /opt/cloudclaw/skills/ffmpeg-skill/scripts/loudness.py \
      /work/jobs/<job_id>/edit/rough.mp4 \
      -o /work/jobs/<job_id>/edit/mastered.mp4 \
      --target -14 --json
    ```
  - Format / Aspect / Platform export:
    ```bash
    python3 /opt/cloudclaw/skills/ffmpeg-skill/scripts/export.py \
      /work/jobs/<job_id>/edit/mastered.mp4 \
      -o /work/jobs/<job_id>/edit/final.mp4 \
      --platform reels --json
    ```

### State 7: `QC` & `DELIVERED`
- Run automated platform compliance verification:
  ```bash
  python3 /opt/cloudclaw/skills/ffmpeg-skill/scripts/check.py \
    /work/jobs/<job_id>/edit/final.mp4 \
    --platform reels --json
  ```
- Generate visual contact sheet for agent verification:
  ```bash
  python3 /opt/cloudclaw/skills/ffmpeg-skill/scripts/look.py \
    /work/jobs/<job_id>/edit/final.mp4 \
    -o /work/jobs/<job_id>/edit/qc_sheet.png \
    --tiles 3x2 --json
  ```
- Upload final deliverables (`final.mp4`, `master.srt`, `qc_sheet.png`, `edl.json`) to R2.
- Update D1 job record to `DELIVERED` with signed download URLs.
- Delete local `/work/jobs/<job_id>` scratch workspace to conserve ephemeral disk.

---

## 4. Ephemeral Container Persistence & Recovery

1. **State First, Then Queue:**
   - Commit state transitions in D1 before triggering asynchronous next steps.
2. **Container Restarts & Evictions:**
   - Container disk is ephemeral. Any task killed by SIGTERM or container eviction must be resumable.
   - Intermediate outputs (transcripts, rough cut, EDL) should be synced to R2 upon completion of each phase.
3. **Lease Heartbeat:**
   - Active jobs update `updated_at` in D1 every 30 seconds. If an instance dies mid-render, the watchdog marks the job `FAILED` or re-queues from the last checkpointed state.
