# Phase 3 media audit

## Implemented and credential-free tested

The Phase 3 code defines provider-neutral media configuration, normalized
`continuum.media.v1` transcript and artifact records, explicit Gemini Live audio,
transcription, server-VAD and JPEG policy, explicit LiveKit room I/O options,
defensive telemetry redaction, and atomic deterministic PCM16 provider-output WAV
recording. The smoke CLI fails closed unless all four credential names are present;
`--allow-skip` exists solely for credential-free auditing.

The provider-output WAV is captured before room playout. It does **not** prove that
all frames reached a remote participant. A remotely received WAV is a separate live
smoke artifact.

## External gate not executed

No LiveKit room or Google model request was made in this checkout. Therefore the
checked-in report remains `skipped_external_gate`, `executed: false`, and
`official_score: false`. Requested word timestamps use explicit provider options;
timestamps and confidence remain null unless actually exposed by the provider.
No cloud timing, confidence, remote-media, or service behavior is claimed.

The internal live smoke is not the official 100-example FDB evaluation. Credentials,
released recordings, and a genuine cloud run remain external. No Phase 4 score or
milestone is claimed.

## Live orchestration implementation

The real orchestration runtime now creates an isolated run directory, starts the
managed named worker without credentials in argv, creates a room with an explicit
`continuum-fdb-smoke` dispatch, joins a least-privilege caller, publishes PCM16 microphone
audio and deterministic RGBA camera frames, captures subscribed agent audio to a
separate atomic WAV, and performs bounded teardown and log scrubbing. Required
checks are derived only from the unique room's per-run files.

The orchestration boundary is exercised credential-free with official-shape fakes,
including success, early worker exit, join timeout, absent audio/events/tool logs,
stale artifacts, safe errors, cleanup order, argv safety, and log redaction. These
fake tests do not establish cloud connectivity. The checked-in report remains
`skipped_external_gate`; no official score exists. The SHA-pinned manual GitHub
workflow is the next external gate.

Reviewed workflow action revisions:

- `actions/checkout` at `fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09`
- `actions/setup-python` at `ece7cb06caefa5fff74198d8649806c4678c61a1`
- `astral-sh/setup-uv` at `94527f2e458b27549849d47d273a16bec83a01e9`
- `actions/upload-artifact` at `ea165f8d65b6e75b540449e92b4886f43607fa02`
