# Session data format

Each log creates a folder `<participant_id>_<YYYYMMDD_HHMMSS>` (local time; `_2`, `_3` on
collision) under the output folder:

| File | Content |
|---|---|
| `HR_<participant_id>_<YYYYMMDD_HHMMSS>.csv` | Primary data: one row per RR interval, plus event rows. |
| `raw.jsonl` | Every Bluetooth notification, lossless, for replay/debugging. |
| `summary.csv` | `metric,value` pairs for the whole log. Values that cannot be computed are blank. |
| `session.json` | Metadata (schema 3): participant, device, status, end reason, counts, recent events. |
| `app.log` | Application log for this session. |

## HR CSV columns

UTF-8, comma separated, LF line endings. A packet with no RR writes one row with blank RR
fields. Event rows have blank signal fields.

| Column | Meaning |
|---|---|
| `session_id` | Folder name. |
| `participant_id` | Research code entered at Start logging. |
| `pc_time_iso` | PC receive time, local time with UTC offset, milliseconds. |
| `pc_time_utc_iso` | Same instant in UTC (`...Z`). |
| `pc_time_unix_ms` | Same instant as Unix milliseconds (UTC). |
| `elapsed_ms` | Monotonic milliseconds since the log started. |
| `packet_seq` | 1-based HR packet counter; blank on event rows. |
| `segment` | Connection segment; increases after each disconnect. |
| `hr_bpm` | Heart rate reported by the H10. |
| `rr_index` | Position of the RR value inside its packet (0-based). |
| `rr_ms` | RR interval in ms (H10 value in 1/1024 s, 3 decimals). |
| `beat_time_est_unix_ms` | Estimated beat time (the last RR in a packet ends at receive time). |
| `beat_elapsed_est_ms` | Same estimate on the `elapsed_ms` axis. |
| `contact` | `1`/`0` when the sensor reports contact; blank otherwise (normal for the H10). |
| `event` | `start`, `stop`, `disconnect`, `reconnect`, `battery`, `warning`, `error`. |
| `detail` | Event detail (for `stop`: the end reason). |

Beat times are estimates: the standard Heart Rate service carries no sensor timestamp. RR
durations are exactly what the H10 reports.

## Metrics

Descriptive, uncorrected time-domain values. RR outside 300-2000 ms is kept in the CSV but
excluded from HRV; successive differences never cross an excluded interval or a disconnect.
SDNN is the sample standard deviation; RMSSD and pNN50 use accepted successive differences.
Not a medical or diagnostic measurement.
