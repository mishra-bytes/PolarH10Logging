# Session data format

Each log creates a folder `<participant_id>_<YYYYMMDD_HHMMSS>` (local time; `_2`, `_3` on
collision) under the output folder:

| File | Content |
|---|---|
| `HR_<participant_id>_<YYYYMMDD_HHMMSS>.csv` | Primary data: one row per RR interval, plus event rows. |
| `ECG_<participant_id>_<YYYYMMDD_HHMMSS>.csv` | Raw ECG, one row per sample. Only when ECG is enabled. |
| `ACC_<participant_id>_<YYYYMMDD_HHMMSS>.csv` | Accelerometer, one row per sample. Only when enabled. |
| `raw.jsonl` | Every Bluetooth notification (HR and ECG/ACC frames), lossless, for replay/debugging. |
| `summary.csv` | `metric,value` pairs for the whole log. Values that cannot be computed are blank. |
| `session.json` | Metadata (schema 4): participant, device, status, end reason, counts, stream settings, recent events. |
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
| `contact` | `1`/`0` when the sensor reports contact; blank otherwise. The H10 (fw 5.0.0) reports contact only while ECG or accelerometer streaming is on. |
| `event` | `start`, `stop`, `disconnect`, `reconnect`, `battery`, `stream`, `warning`, `error`. |
| `detail` | Event detail (for `stop`: the end reason). |

Beat times are estimates: the standard Heart Rate service carries no sensor timestamp. RR
durations are exactly what the H10 reports.

## ECG and accelerometer CSVs

Optional streams from Polar's measurement service (PMD). HR/RR logging is unchanged by them.

| Column | Meaning |
|---|---|
| `sample_time_utc_iso` | Estimated sample time in UTC (`...Z`), millisecond precision. |
| `sample_time_unix_ms` | Same instant as Unix milliseconds with 3 decimals (microseconds). |
| `sensor_time_ns` | The H10's own nanosecond clock for this sample (not set to real time). |
| `pc_received_unix_ms` | When the PC received the frame containing this sample. |
| `frame_seq` | 1-based frame counter per stream. |
| `segment` | Connection segment, as in the HR CSV. |
| `ecg_uv` | ECG CSV: voltage in microvolts (130 Hz, 14-bit). |
| `x_mg`, `y_mg`, `z_mg` | ACC CSV: acceleration per axis in milli-g (25/50/100/200 Hz; ±2/4/8 g; 16-bit). |

Sample times come from the H10's sensor clock, which spaces samples precisely. At the first
frame after each (re)connect, the sensor clock is anchored to the PC receive time, so absolute
times carry that frame's Bluetooth latency (typically tens of milliseconds). ECG frames hold
73 samples (about 0.56 s); accelerometer frames at 16 bit hold 36 samples.

`stream` event rows in the HR CSV record when each stream started or stopped and its
settings; `session.json` `streams` holds the latest settings.

Size guide: ECG adds about 35 MB per hour; the accelerometer about 15 MB per hour at 50 Hz and
60 MB per hour at 200 Hz.

## Metrics

Descriptive, uncorrected time-domain values. RR outside 300-2000 ms is kept in the CSV but
excluded from HRV; successive differences never cross an excluded interval or a disconnect.
SDNN is the sample standard deviation; RMSSD and pNN50 use accepted successive differences.
Not a medical or diagnostic measurement.
