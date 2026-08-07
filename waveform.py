"""
@Author         : Vedang
@Description    : Waveform analysis module - builds a Waveform from an exported Saleae raw CSV directory and computes edge/timing metrics and multi-format coordinate export.
@Input          : None
@Output         : None
@Note           : No plotting or protocol analyzers here; rise_time()/fall_time() require analog channel data and raise WaveformError on purely digital channels.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from exceptions import SaleaeException

logger = logging.getLogger("saleae_wrapper.waveform")


class WaveformError(SaleaeException):
    """
    @Author         : Vedang
    @Description    : Raised for waveform construction, parsing, or analysis failures (bad CSV, unknown channel, unmeasurable metric).
    @Input          : None
    @Output         : None
    @Note           : Subclasses SaleaeException; caught internally by Waveform.statistics()/_safe() to degrade gracefully to None.
    """


@dataclass
class _ChannelData:
    """
    @Author         : Vedang
    @Description    : Container for one channel's raw parsed samples (digital transition list or analog sample array).
    @Input          : channel         - configured channel index
                       kind            - "digital" or "analog"
                       times           - ascending timestamps in seconds
                       values          - sample values, 0/1 for digital or volts for analog
    @Output         : None
    @Note           : Internal to waveform.py; not part of the public API.
    """

    channel: int
    kind: str
    times: np.ndarray
    values: np.ndarray


# A resolved channel argument: a single channel id, an explicit list of
# ids, or None (meaning "all channels currently loaded").
ChannelArg = Union[int, Sequence[int], None]


class Waveform:
    """
    @Author         : Vedang
    @Description    : In-memory waveform analysis (edges, transitions, timing metrics, multi-format export) over one or more channels of a completed capture.
    @Input          : channels        - via constructor, mapping of channel index to parsed _ChannelData
                       sample_rate     - via constructor, configured digital sample rate in Hz, used as a bandwidth() fallback
    @Output         : None
    @Note           : Built via from_csv_directory(); period/frequency use consecutive rising edges, rise/fall time need analog data, and the final open-ended segment is excluded from timing stats.
    """

    def __init__(
        self,
        channels: Dict[int, _ChannelData],
        sample_rate: Optional[float] = None,
    ) -> None:
        """
        @Author         : Vedang
        @Description    : Wraps already-parsed per-channel data into a Waveform and initializes the logical-edge cache.
        @Input          : channels        - mapping of channel index to _ChannelData, must be non-empty
                           sample_rate     - configured digital sample rate in Hz, optional
        @Output         : None            - populates self._channels, self.sample_rate, self._logical_cache
        @Note           : Raises WaveformError if channels is empty.
        """
        if not channels:
            raise WaveformError("Waveform requires at least one channel of data.")
        self._channels: Dict[int, _ChannelData] = channels
        self.sample_rate = sample_rate
        self._logical_cache: Dict[int, Tuple[np.ndarray, np.ndarray]] = {}
        logger.info(
            "Waveform initialized: channels=%s, sample_rate=%s",
            sorted(self._channels),
            sample_rate,
        )

    @property
    def channels(self) -> List[int]:
        """
        @Author         : Vedang
        @Description    : Returns the sorted list of channel indices currently loaded into this waveform.
        @Input          : None
        @Output         : channels        - sorted list of int channel indices
        @Note           : Read-only property; use _resolve_channels() internally to validate a requested channel argument.
        """
        return sorted(self._channels)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    @classmethod
    def from_csv_directory(
        cls,
        directory: Union[str, Path],
        digital_channels: Optional[Sequence[int]] = None,
        analog_channels: Optional[Sequence[int]] = None,
        sample_rate: Optional[float] = None,
    ) -> "Waveform":
        """
        @Author         : Vedang
        @Description    : Builds a Waveform by parsing a Logic 2 raw CSV export directory for the requested digital/analog channels.
        @Input          : directory       - export directory produced by Capture.export_raw_data_csv()
                           digital_channels - digital channel indices to load
                           analog_channels - analog channel indices to load
                           sample_rate     - configured digital sample rate in Hz, used only as a bandwidth() fallback
        @Output         : waveform        - a populated Waveform instance
        @Note           : Raises WaveformError if the directory is missing, no channels are requested, or no usable data is found.
        """
        directory = Path(directory)
        if not directory.is_dir():
            raise WaveformError(f"Export directory not found: {directory}")

        digital_channels = list(digital_channels or [])
        analog_channels = list(analog_channels or [])
        if not digital_channels and not analog_channels:
            raise WaveformError("At least one digital or analog channel must be specified.")

        channels: Dict[int, _ChannelData] = {}
        if digital_channels:
            channels.update(cls._load_channel_group(directory, digital_channels, "digital"))
        if analog_channels:
            channels.update(cls._load_channel_group(directory, analog_channels, "analog"))

        if not channels:
            raise WaveformError(f"No usable channel data found under {directory}.")

        return cls(channels, sample_rate=sample_rate)

    @classmethod
    def _load_channel_group(
        cls, directory: Path, channels: Sequence[int], kind: str
    ) -> Dict[int, _ChannelData]:
        """
        @Author         : Vedang
        @Description    : Loads one kind (digital or analog) of requested channels, trying a combined CSV file first and per-channel files as fallback.
        @Input          : directory       - export directory to search
                           channels        - requested channel indices for this kind
                           kind            - "digital" or "analog"
        @Output         : result          - mapping of channel index to parsed _ChannelData for channels that were found
        @Note           : Channels with no matching CSV file are logged as a warning and omitted rather than raising.
        """
        remaining = list(channels)
        result: Dict[int, _ChannelData] = {}

        combined = directory / f"{kind}.csv"
        if combined.is_file():
            result.update(cls._parse_combined_csv(combined, remaining, kind))
            remaining = [ch for ch in remaining if ch not in result]

        for ch in remaining:
            candidates = [
                directory / f"{kind}_{ch}.csv",
                directory / f"{kind}-{ch}.csv",
                directory / f"channel_{ch}.csv",
            ]
            found = next((p for p in candidates if p.is_file()), None)
            if found is None:
                logger.warning(
                    "No exported CSV found for %s channel %d under %s; skipping.",
                    kind,
                    ch,
                    directory,
                )
                continue
            result[ch] = cls._parse_single_channel_csv(found, ch, kind)

        return result

    @staticmethod
    def _find_time_column(header: Sequence[str]) -> int:
        """
        @Author         : Vedang
        @Description    : Locates the time column in a CSV header by matching "time" case-insensitively, defaulting to column 0.
        @Input          : header          - CSV header row as a list of column name strings
        @Output         : index           - integer index of the time column
        @Note           : Defensive fallback for SDK versions whose export column naming differs.
        """
        for i, name in enumerate(header):
            if "time" in name.lower():
                return i
        return 0

    @staticmethod
    def _match_channel_columns(
        header: Sequence[str], channels: Sequence[int], time_idx: int
    ) -> Dict[int, int]:
        """
        @Author         : Vedang
        @Description    : Maps requested channel indices to CSV column indices by matching the trailing number in each header name, falling back to positional order for unmatched channels.
        @Input          : header          - CSV header row
                           channels        - requested channel indices
                           time_idx        - index of the time column, excluded from matching
        @Output         : mapping         - dict of channel index to column index
        @Note           : Positional fallback preserves ascending channel order over the remaining unmatched data columns.
        """
        mapping: Dict[int, int] = {}
        remaining = set(channels)

        for i, name in enumerate(header):
            if i == time_idx:
                continue
            match = re.search(r"(\d+)\s*$", name.strip())
            if match:
                ch_num = int(match.group(1))
                if ch_num in remaining:
                    mapping[ch_num] = i
                    remaining.discard(ch_num)

        if remaining:
            data_cols = [
                i for i in range(len(header)) if i != time_idx and i not in mapping.values()
            ]
            for ch_num, col in zip(sorted(remaining), data_cols):
                mapping[ch_num] = col
                remaining.discard(ch_num)

        return mapping

    @staticmethod
    def _dedupe_consecutive(
        times: List[float], values: List[float]
    ) -> Tuple[List[float], List[float]]:
        """
        @Author         : Vedang
        @Description    : Collapses consecutive equal values in a combined CSV export back down to a sparse per-channel transition list.
        @Input          : times           - timestamps for one channel, as parsed from a combined row-per-any-change CSV file
                           values          - values paired with times
        @Output         : result          - tuple (deduped_times, deduped_values) keeping the first sample plus every genuine state change
        @Note           : Needed because a combined digital.csv repeats every channel's current state on every row.
        """
        if not times:
            return times, values
        out_t = [times[0]]
        out_v = [values[0]]
        for t, v in zip(times[1:], values[1:]):
            if v != out_v[-1]:
                out_t.append(t)
                out_v.append(v)
        return out_t, out_v

    @classmethod
    def _parse_combined_csv(
        cls, path: Path, channels: Sequence[int], kind: str
    ) -> Dict[int, _ChannelData]:
        """
        @Author         : Vedang
        @Description    : Parses a combined multi-channel CSV file (e.g. digital.csv/analog.csv) into per-channel _ChannelData.
        @Input          : path            - path to the combined CSV file
                           channels        - requested channel indices to extract
                           kind            - "digital" or "analog"
        @Output         : result          - mapping of channel index to parsed _ChannelData
        @Note           : Digital channels are deduped to a sparse transition list; analog channels keep every sample row.
        """
        with path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            if header is None:
                logger.warning("Combined %s CSV %s is empty.", kind, path)
                return {}

            time_idx = cls._find_time_column(header)
            col_map = cls._match_channel_columns(header, channels, time_idx)
            if not col_map:
                logger.warning(
                    "Could not match any requested %s channels in %s (header=%s).",
                    kind,
                    path,
                    header,
                )
                return {}

            times_by_ch: Dict[int, List[float]] = {ch: [] for ch in col_map}
            values_by_ch: Dict[int, List[float]] = {ch: [] for ch in col_map}
            row_count = 0

            for row in reader:
                if not row:
                    continue
                row_count += 1
                try:
                    t = float(row[time_idx])
                except (IndexError, ValueError):
                    continue
                for ch, col in col_map.items():
                    try:
                        v = float(row[col])
                    except (IndexError, ValueError):
                        continue
                    times_by_ch[ch].append(t)
                    values_by_ch[ch].append(v)

        logger.info(
            "Parsed %d rows from %s for %s channels %s.",
            row_count,
            path,
            kind,
            sorted(col_map),
        )

        result: Dict[int, _ChannelData] = {}
        for ch in col_map:
            t_list, v_list = times_by_ch[ch], values_by_ch[ch]
            if kind == "digital":
                t_list, v_list = cls._dedupe_consecutive(t_list, v_list)
            result[ch] = _ChannelData(
                channel=ch,
                kind=kind,
                times=np.asarray(t_list, dtype=np.float64),
                values=np.asarray(v_list, dtype=np.float64),
            )
        return result

    @classmethod
    def _parse_single_channel_csv(cls, path: Path, ch: int, kind: str) -> _ChannelData:
        """
        @Author         : Vedang
        @Description    : Parses a per-channel, two-column (time, value) CSV file into _ChannelData.
        @Input          : path            - path to the per-channel CSV file
                           ch              - channel index this file belongs to
                           kind            - "digital" or "analog"
        @Output         : data            - parsed _ChannelData for this channel
        @Note           : Used as a fallback when no combined CSV file is present for the requested kind.
        """
        times: List[float] = []
        values: List[float] = []

        with path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            time_idx, val_idx = 0, 1
            if header:
                time_idx = cls._find_time_column(header)
                val_idx = 1 if time_idx == 0 else 0

            for row in reader:
                if not row or len(row) <= max(time_idx, val_idx):
                    continue
                try:
                    t = float(row[time_idx])
                    v = float(row[val_idx])
                except ValueError:
                    continue
                times.append(t)
                values.append(v)

        if kind == "digital":
            times, values = cls._dedupe_consecutive(times, values)

        logger.info("Parsed %d points from %s for %s channel %d.", len(times), path, kind, ch)
        return _ChannelData(
            channel=ch,
            kind=kind,
            times=np.asarray(times, dtype=np.float64),
            values=np.asarray(values, dtype=np.float64),
        )

    # ------------------------------------------------------------------
    # Channel resolution helpers
    # ------------------------------------------------------------------
    def _resolve_channels(self, channel: ChannelArg) -> List[int]:
        """
        @Author         : Vedang
        @Description    : Normalizes a channel argument (single id, list, or None) into a list of loaded channel ids.
        @Input          : channel         - a channel id, a sequence of ids, or None for "all loaded channels"
        @Output         : channels        - list of validated channel indices
        @Note           : Raises WaveformError if any requested channel was not loaded into this waveform.
        """
        if channel is None:
            return self.channels
        chans = list(channel) if isinstance(channel, (list, tuple, set)) else [channel]
        missing = [c for c in chans if c not in self._channels]
        if missing:
            raise WaveformError(
                f"Channel(s) {missing} not present in this waveform "
                f"(available: {self.channels})."
            )
        return chans

    @staticmethod
    def _scalar_or_dict(results: Dict[int, object]) -> object:
        """
        @Author         : Vedang
        @Description    : Unwraps a single-channel results dict to a bare scalar, keeping multi-channel calls as a dict.
        @Input          : results         - dict of channel index to computed metric value
        @Output         : value           - the single value when len(results) == 1, else the results dict unchanged
        @Note           : Shared by every per-channel metric method to keep the single-channel case ergonomic.
        """
        if len(results) == 1:
            return next(iter(results.values()))
        return results

    # ------------------------------------------------------------------
    # Logical (high/low) edge derivation, shared by all timing metrics
    # ------------------------------------------------------------------
    def _logical_edges(self, ch: int) -> Tuple[np.ndarray, np.ndarray]:
        """
        @Author         : Vedang
        @Description    : Returns the cached (times, states) logical 0/1 sequence for a channel, deriving it from analog samples if needed.
        @Input          : ch              - channel index to resolve
        @Output         : result          - tuple (times, states) numpy arrays, states are int8 0/1
        @Note           : Digital channels use their parsed transition list as-is; result is cached in self._logical_cache per channel.
        """
        if ch in self._logical_cache:
            return self._logical_cache[ch]

        data = self._channels[ch]
        if data.kind == "digital":
            times, states = data.times, data.values.astype(np.int8)
        else:
            times, states = self._derive_logical_from_analog(data)

        self._logical_cache[ch] = (times, states)
        return times, states

    @staticmethod
    def _derive_logical_from_analog(data: _ChannelData) -> Tuple[np.ndarray, np.ndarray]:
        """
        @Author         : Vedang
        @Description    : Derives a digital-like (times, states) sequence from analog samples via mid-amplitude threshold crossing with linear interpolation.
        @Input          : data            - analog _ChannelData to derive logic levels from
        @Output         : result          - tuple (times, states) numpy arrays representing the derived logical sequence
        @Note           : Raises WaveformError on too few samples or a flat (zero-amplitude) signal.
        """
        values, times = data.values, data.times
        if values.size < 2:
            raise WaveformError(
                f"Channel {data.channel}: not enough analog samples to derive logic levels."
            )

        vmin, vmax = float(values.min()), float(values.max())
        span = vmax - vmin
        if span < 1e-9:
            raise WaveformError(
                f"Channel {data.channel}: analog signal is flat; cannot derive edges."
            )
        mid = vmin + 0.5 * span

        above = values >= mid
        change_idx = np.nonzero(np.diff(above.astype(np.int8)) != 0)[0]

        out_times = [float(times[0])]
        out_states = [1 if above[0] else 0]
        for i in change_idx:
            t0, t1 = times[i], times[i + 1]
            v0, v1 = values[i], values[i + 1]
            frac = 0.5 if v1 == v0 else (mid - v0) / (v1 - v0)
            frac = min(max(frac, 0.0), 1.0)
            out_times.append(float(t0 + frac * (t1 - t0)))
            out_states.append(1 if above[i + 1] else 0)

        return (
            np.asarray(out_times, dtype=np.float64),
            np.asarray(out_states, dtype=np.int8),
        )

    def _rising_falling_indices(self, ch: int) -> Tuple[np.ndarray, np.ndarray]:
        """
        @Author         : Vedang
        @Description    : Finds the array indices of rising and falling edges within a channel's logical (times, states) sequence.
        @Input          : ch              - channel index to analyze
        @Output         : result          - tuple (rising_idx, falling_idx) numpy integer index arrays
        @Note           : Returns two empty arrays when fewer than two logical samples exist.
        """
        times, states = self._logical_edges(ch)
        if len(states) < 2:
            return np.array([], dtype=np.int64), np.array([], dtype=np.int64)
        diffs = np.diff(states)
        rising_idx = np.nonzero(diffs > 0)[0] + 1
        falling_idx = np.nonzero(diffs < 0)[0] + 1
        return rising_idx, falling_idx

    def _period_for_channel(self, ch: int) -> Optional[float]:
        """
        @Author         : Vedang
        @Description    : Computes the mean rising-edge-to-rising-edge period for one channel.
        @Input          : ch              - channel index to analyze
        @Output         : period          - mean period in seconds, or None if fewer than two rising edges exist
        @Note           : Backing implementation for the public period()/frequency() methods.
        """
        times, _states = self._logical_edges(ch)
        rising_idx, _falling_idx = self._rising_falling_indices(ch)
        if rising_idx.size < 2:
            return None
        periods = np.diff(times[rising_idx])
        return float(np.mean(periods))

    def _high_low_times(self, ch: int) -> Tuple[Optional[float], Optional[float]]:
        """
        @Author         : Vedang
        @Description    : Computes the mean high-state and low-state segment durations for one channel.
        @Input          : ch              - channel index to analyze
        @Output         : result          - tuple (mean_high_time, mean_low_time) in seconds, either may be None if that state never occurred
        @Note           : The final, open-ended segment after the last transition is excluded since its duration is unknown.
        """
        times, states = self._logical_edges(ch)
        if len(times) < 2:
            return None, None
        durations = np.diff(times)
        seg_states = states[:-1]
        high = durations[seg_states == 1]
        low = durations[seg_states == 0]
        high_mean = float(high.mean()) if high.size else None
        low_mean = float(low.mean()) if low.size else None
        return high_mean, low_mean

    # ------------------------------------------------------------------
    # Public API — points
    # ------------------------------------------------------------------
    def coordinates(self, channel: ChannelArg = None) -> List[Dict[str, float]]:
        """
        @Author         : Vedang
        @Description    : Returns raw parsed data points for the requested channel(s) as {"time","channel","value"} dicts, sorted by time.
        @Input          : channel         - a channel id, list of ids, or None for all loaded channels
        @Output         : points          - list of point dicts
        @Note           : For large analog captures prefer to_numpy()/to_dataframe(), which build the result with vectorized ops instead of a Python loop.
        """
        chans = self._resolve_channels(channel)
        result: List[Dict[str, float]] = []
        for ch in chans:
            data = self._channels[ch]
            result.extend(
                {"time": float(t), "channel": ch, "value": float(v)}
                for t, v in zip(data.times, data.values)
            )
        result.sort(key=lambda d: d["time"])
        return result

    def edges(self, channel: ChannelArg = None) -> List[Dict[str, object]]:
        """
        @Author         : Vedang
        @Description    : Returns every logical rising/falling edge for the requested channel(s) as {"time","channel","edge_type"} dicts.
        @Input          : channel         - a channel id, list of ids, or None for all loaded channels
        @Output         : edges           - list of edge dicts, sorted by time
        @Note           : Analog channel edges are derived via mid-amplitude threshold crossing, not measured transition speed.
        """
        chans = self._resolve_channels(channel)
        result: List[Dict[str, object]] = []
        for ch in chans:
            times, _states = self._logical_edges(ch)
            rising_idx, falling_idx = self._rising_falling_indices(ch)
            result.extend(
                {"time": float(times[i]), "channel": ch, "edge_type": "rising"}
                for i in rising_idx
            )
            result.extend(
                {"time": float(times[i]), "channel": ch, "edge_type": "falling"}
                for i in falling_idx
            )
        result.sort(key=lambda d: d["time"])
        return result

    def transitions(self, channel: ChannelArg = None) -> List[Dict[str, object]]:
        """
        @Author         : Vedang
        @Description    : Returns every state transition with its duration as {"time","channel","from","to","duration"} dicts.
        @Input          : channel         - a channel id, list of ids, or None for all loaded channels
        @Output         : transitions     - list of transition dicts, sorted by time
        @Note           : duration is None for the final, open-ended segment on each channel.
        """
        chans = self._resolve_channels(channel)
        result: List[Dict[str, object]] = []
        for ch in chans:
            times, states = self._logical_edges(ch)
            n = len(times)
            for i in range(1, n):
                duration = float(times[i + 1] - times[i]) if i + 1 < n else None
                result.append(
                    {
                        "time": float(times[i]),
                        "channel": ch,
                        "from": int(states[i - 1]),
                        "to": int(states[i]),
                        "duration": duration,
                    }
                )
        result.sort(key=lambda d: d["time"])
        return result

    # ------------------------------------------------------------------
    # Public API — counts
    # ------------------------------------------------------------------
    def edge_count(self, channel: ChannelArg = None) -> Union[int, Dict[int, int]]:
        """
        @Author         : Vedang
        @Description    : Returns the total number of edges (rising + falling) per requested channel.
        @Input          : channel         - a channel id, list of ids, or None for all loaded channels
        @Output         : count           - int for a single channel, or {channel: int} for multiple
        @Note           : Equivalent to transition_count(channel)["total"] per channel.
        """
        chans = self._resolve_channels(channel)
        out = {}
        for ch in chans:
            times, _states = self._logical_edges(ch)
            out[ch] = max(len(times) - 1, 0)
        return self._scalar_or_dict(out)

    def transition_count(
        self, channel: ChannelArg = None
    ) -> Union[Dict[str, int], Dict[int, Dict[str, int]]]:
        """
        @Author         : Vedang
        @Description    : Returns a rising/falling/total transition breakdown per requested channel.
        @Input          : channel         - a channel id, list of ids, or None for all loaded channels
        @Output         : counts          - {"rising","falling","total"} dict for a single channel, or {channel: {...}} for multiple
        @Note           : Complements edge_count() with a polarity breakdown.
        """
        chans = self._resolve_channels(channel)
        out = {}
        for ch in chans:
            rising_idx, falling_idx = self._rising_falling_indices(ch)
            out[ch] = {
                "rising": int(rising_idx.size),
                "falling": int(falling_idx.size),
                "total": int(rising_idx.size + falling_idx.size),
            }
        return self._scalar_or_dict(out)

    # ------------------------------------------------------------------
    # Public API — timing metrics
    # ------------------------------------------------------------------
    def period(self, channel: ChannelArg = None) -> Union[Optional[float], Dict[int, Optional[float]]]:
        """
        @Author         : Vedang
        @Description    : Returns the mean rising-edge-to-rising-edge period per requested channel.
        @Input          : channel         - a channel id, list of ids, or None for all loaded channels
        @Output         : period          - float seconds (or None) for a single channel, or {channel: value} for multiple
        @Note           : None is returned for a channel with fewer than two rising edges.
        """
        chans = self._resolve_channels(channel)
        out = {ch: self._period_for_channel(ch) for ch in chans}
        return self._scalar_or_dict(out)

    def frequency(self, channel: ChannelArg = None) -> Union[Optional[float], Dict[int, Optional[float]]]:
        """
        @Author         : Vedang
        @Description    : Returns the mean signal frequency (1/period) per requested channel.
        @Input          : channel         - a channel id, list of ids, or None for all loaded channels
        @Output         : frequency       - float Hz (or None) for a single channel, or {channel: value} for multiple
        @Note           : None when period() is unavailable for that channel.
        """
        chans = self._resolve_channels(channel)
        out = {}
        for ch in chans:
            p = self._period_for_channel(ch)
            out[ch] = (1.0 / p) if p else None
        return self._scalar_or_dict(out)

    def duty_cycle(self, channel: ChannelArg = None) -> Union[Optional[float], Dict[int, Optional[float]]]:
        """
        @Author         : Vedang
        @Description    : Returns the mean duty cycle as a percentage per requested channel.
        @Input          : channel         - a channel id, list of ids, or None for all loaded channels
        @Output         : duty_cycle_pct  - float 0-100 (or None) for a single channel, or {channel: value} for multiple
        @Note           : Computed as mean(high_time) / (mean(high_time) + mean(low_time)) * 100.
        """
        chans = self._resolve_channels(channel)
        out = {}
        for ch in chans:
            high, low = self._high_low_times(ch)
            out[ch] = (100.0 * high / (high + low)) if (high and low) else None
        return self._scalar_or_dict(out)

    def high_time(self, channel: ChannelArg = None) -> Union[Optional[float], Dict[int, Optional[float]]]:
        """
        @Author         : Vedang
        @Description    : Returns the mean high-state segment duration per requested channel.
        @Input          : channel         - a channel id, list of ids, or None for all loaded channels
        @Output         : high_time_s     - float seconds (or None) for a single channel, or {channel: value} for multiple
        @Note           : None if the channel never went high within the capture.
        """
        chans = self._resolve_channels(channel)
        out = {ch: self._high_low_times(ch)[0] for ch in chans}
        return self._scalar_or_dict(out)

    def low_time(self, channel: ChannelArg = None) -> Union[Optional[float], Dict[int, Optional[float]]]:
        """
        @Author         : Vedang
        @Description    : Returns the mean low-state segment duration per requested channel.
        @Input          : channel         - a channel id, list of ids, or None for all loaded channels
        @Output         : low_time_s      - float seconds (or None) for a single channel, or {channel: value} for multiple
        @Note           : None if the channel never went low within the capture.
        """
        chans = self._resolve_channels(channel)
        out = {ch: self._high_low_times(ch)[1] for ch in chans}
        return self._scalar_or_dict(out)

    def pulse_width(self, channel: ChannelArg = None) -> Union[Optional[float], Dict[int, Optional[float]]]:
        """
        @Author         : Vedang
        @Description    : Returns the mean duration of all pulses (high and low segments combined) per requested channel.
        @Input          : channel         - a channel id, list of ids, or None for all loaded channels
        @Output         : pulse_width_s   - float seconds (or None) for a single channel, or {channel: value} for multiple
        @Note           : Unlike high_time()/low_time(), this does not filter by polarity.
        """
        chans = self._resolve_channels(channel)
        out = {}
        for ch in chans:
            times, _states = self._logical_edges(ch)
            out[ch] = float(np.mean(np.diff(times))) if len(times) >= 2 else None
        return self._scalar_or_dict(out)

    def rise_time(self, channel: ChannelArg = None) -> Union[float, Dict[int, float]]:
        """
        @Author         : Vedang
        @Description    : Returns the mean 10%-90% rise time per requested channel, analog channels only.
        @Input          : channel         - a channel id, list of ids, or None for all loaded channels
        @Output         : rise_time_s     - float seconds for a single channel, or {channel: value} for multiple
        @Note           : Raises WaveformError for any digital channel or one with no clean rising transition to measure.
        """
        chans = self._resolve_channels(channel)
        out = {ch: self._measure_transition_time(ch, "rising") for ch in chans}
        return self._scalar_or_dict(out)

    def fall_time(self, channel: ChannelArg = None) -> Union[float, Dict[int, float]]:
        """
        @Author         : Vedang
        @Description    : Returns the mean 90%-10% fall time per requested channel, analog channels only.
        @Input          : channel         - a channel id, list of ids, or None for all loaded channels
        @Output         : fall_time_s     - float seconds for a single channel, or {channel: value} for multiple
        @Note           : Raises WaveformError for any digital channel or one with no clean falling transition to measure.
        """
        chans = self._resolve_channels(channel)
        out = {ch: self._measure_transition_time(ch, "falling") for ch in chans}
        return self._scalar_or_dict(out)

    def bandwidth(self, channel: ChannelArg = None) -> Union[float, Dict[int, float]]:
        """
        @Author         : Vedang
        @Description    : Returns the estimated signal bandwidth per requested channel.
        @Input          : channel         - a channel id, list of ids, or None for all loaded channels
        @Output         : bandwidth_hz    - float Hz for a single channel, or {channel: value} for multiple
        @Note           : Uses 0.35/rise_time when analog data supports it, else falls back to the sample_rate/2 Nyquist estimate.
        """
        chans = self._resolve_channels(channel)
        out = {}
        for ch in chans:
            out[ch] = self._bandwidth_for_channel(ch)
        return self._scalar_or_dict(out)

    def _bandwidth_for_channel(self, ch: int) -> float:
        """
        @Author         : Vedang
        @Description    : Computes the bandwidth estimate for a single channel, preferring rise-time over the Nyquist fallback.
        @Input          : ch              - channel index to analyze
        @Output         : bandwidth_hz    - float Hz estimate
        @Note           : Raises WaveformError if neither analog rise-time data nor a known sample_rate is available.
        """
        data = self._channels[ch]
        if data.kind == "analog":
            try:
                rt = self._measure_transition_time(ch, "rising")
                if rt:
                    return 0.35 / rt
            except WaveformError as exc:
                logger.warning(
                    "Channel %d: rise-time based bandwidth unavailable (%s); "
                    "falling back to Nyquist estimate.",
                    ch,
                    exc,
                )
        if self.sample_rate:
            logger.info(
                "Channel %d: bandwidth reported as Nyquist limit "
                "(sample_rate / 2), not a measured value.",
                ch,
            )
            return self.sample_rate / 2.0
        raise WaveformError(
            f"Channel {ch}: cannot estimate bandwidth without analog "
            f"rise-time data or a known sample_rate."
        )

    # ------------------------------------------------------------------
    # Rise/fall time measurement (analog only)
    # ------------------------------------------------------------------
    def _measure_transition_time(
        self, ch: int, direction: str, search_limit: int = 5000
    ) -> float:
        """
        @Author         : Vedang
        @Description    : Measures the mean 10%-90% (or 90%-10%) transition time for one analog channel using interpolated threshold crossings.
        @Input          : ch              - channel index, must be analog
                           direction       - "rising" or "falling"
                           search_limit    - max samples to scan outward from each crossing when locating thresholds
        @Output         : duration        - mean transition time in seconds
        @Note           : Raises WaveformError for digital channels, a flat signal, or no measurable transition of the requested direction.
        """
        data = self._channels[ch]
        if data.kind != "analog":
            raise WaveformError(
                f"Channel {ch}: {direction}_time requires analog sample data; "
                "digital captures record instantaneous transitions only. "
                "Enable this channel under analog_channels in config.yml "
                "to measure rise/fall time."
            )

        values, times = data.values, data.times
        vmin, vmax = float(values.min()), float(values.max())
        span = vmax - vmin
        if span < 1e-9:
            raise WaveformError(
                f"Channel {ch}: analog signal is flat; cannot measure {direction} time."
            )
        low_thr = vmin + 0.1 * span
        high_thr = vmin + 0.9 * span
        mid = vmin + 0.5 * span

        above = values >= mid
        change_idx = np.nonzero(np.diff(above.astype(np.int8)) != 0)[0]
        rising = direction == "rising"

        durations: List[float] = []
        for i in change_idx:
            edge_is_rising = values[i + 1] > values[i]
            if edge_is_rising != rising:
                continue
            if rising:
                t_low = self._scan_backward(times, values, i, low_thr, above=False, limit=search_limit)
                t_high = self._scan_forward(times, values, i, high_thr, above=True, limit=search_limit)
                if t_low is not None and t_high is not None and t_high > t_low:
                    durations.append(t_high - t_low)
            else:
                t_high = self._scan_backward(times, values, i, high_thr, above=True, limit=search_limit)
                t_low = self._scan_forward(times, values, i, low_thr, above=False, limit=search_limit)
                if t_high is not None and t_low is not None and t_low > t_high:
                    durations.append(t_low - t_high)

        if not durations:
            raise WaveformError(
                f"Channel {ch}: no clean {direction} transitions found to measure."
            )
        return float(np.mean(durations))

    @staticmethod
    def _scan_forward(
        times: np.ndarray,
        values: np.ndarray,
        start_idx: int,
        threshold: float,
        above: bool,
        limit: int,
    ) -> Optional[float]:
        """
        @Author         : Vedang
        @Description    : Scans forward from a start index for the first interpolated time a signal crosses a threshold.
        @Input          : times           - sample timestamps
                           values          - sample values
                           start_idx       - index to begin scanning from
                           threshold       - voltage/level threshold to detect
                           above           - True to detect rising through threshold, False to detect falling through it
                           limit           - maximum number of samples to scan
        @Output         : time            - interpolated crossing time, or None if not found within limit
        @Note           : Used by _measure_transition_time() to locate the 10%/90% amplitude points.
        """
        end = min(len(values) - 1, start_idx + limit)
        for idx in range(start_idx, end):
            v0, v1 = values[idx], values[idx + 1]
            crossed = (v1 >= threshold) if above else (v1 <= threshold)
            if crossed:
                t0, t1 = times[idx], times[idx + 1]
                frac = 0.0 if v1 == v0 else (threshold - v0) / (v1 - v0)
                frac = min(max(frac, 0.0), 1.0)
                return float(t0 + frac * (t1 - t0))
        return None

    @staticmethod
    def _scan_backward(
        times: np.ndarray,
        values: np.ndarray,
        start_idx: int,
        threshold: float,
        above: bool,
        limit: int,
    ) -> Optional[float]:
        """
        @Author         : Vedang
        @Description    : Mirrors _scan_forward but scans backward from a start index for the nearest threshold crossing.
        @Input          : times           - sample timestamps
                           values          - sample values
                           start_idx       - index to begin scanning from
                           threshold       - voltage/level threshold to detect
                           above           - True to detect the signal was at/above threshold, False for at/below
                           limit           - maximum number of samples to scan
        @Output         : time            - interpolated crossing time, or None if not found within limit
        @Note           : Used by _measure_transition_time() alongside _scan_forward().
        """
        end = max(0, start_idx - limit)
        for idx in range(start_idx, end, -1):
            v0, v1 = values[idx - 1], values[idx]
            crossed = (v0 >= threshold) if above else (v0 <= threshold)
            if crossed:
                frac = 0.0 if v1 == v0 else (threshold - v0) / (v1 - v0)
                frac = min(max(frac, 0.0), 1.0)
                return float(times[idx - 1] + frac * (times[idx] - times[idx - 1]))
        return None

    # ------------------------------------------------------------------
    # Public API — summary
    # ------------------------------------------------------------------
    def statistics(self, channel: ChannelArg = None) -> Union[Dict[str, object], Dict[int, Dict[str, object]]]:
        """
        @Author         : Vedang
        @Description    : Aggregates every timing/count metric for one or more channels into a single summary dict.
        @Input          : channel         - a channel id, list of ids, or None for all loaded channels
        @Output         : stats           - metrics dict for a single channel, or {channel: {...}} for multiple
        @Note           : Metrics that cannot be computed (e.g. rise_time on a digital channel) are reported as None instead of raising.
        """
        chans = self._resolve_channels(channel)
        out: Dict[int, Dict[str, object]] = {}
        for ch in chans:
            out[ch] = {
                "channel": ch,
                "kind": self._channels[ch].kind,
                "edge_count": self.edge_count(ch),
                "transition_count": self.transition_count(ch),
                "period_s": self._safe(self.period, ch),
                "frequency_hz": self._safe(self.frequency, ch),
                "duty_cycle_pct": self._safe(self.duty_cycle, ch),
                "high_time_s": self._safe(self.high_time, ch),
                "low_time_s": self._safe(self.low_time, ch),
                "pulse_width_s": self._safe(self.pulse_width, ch),
                "rise_time_s": self._safe(self.rise_time, ch),
                "fall_time_s": self._safe(self.fall_time, ch),
                "bandwidth_hz": self._safe(self.bandwidth, ch),
            }
        return self._scalar_or_dict(out)

    @staticmethod
    def _safe(fn, ch: int):
        """
        @Author         : Vedang
        @Description    : Calls a per-channel metric method and converts a WaveformError into None instead of propagating it.
        @Input          : fn              - bound metric method to call, e.g. self.rise_time
                           ch              - channel index to pass as the channel argument
        @Output         : value           - the metric's return value, or None if it raised WaveformError
        @Note           : Used exclusively by statistics() so one unmeasurable metric never fails the whole summary.
        """
        try:
            return fn(channel=ch)
        except WaveformError as exc:
            logger.debug("statistics(): %s unavailable for channel %d: %s", fn.__name__, ch, exc)
            return None

    # ------------------------------------------------------------------
    # Export APIs
    # ------------------------------------------------------------------
    def to_json(
        self,
        channel: ChannelArg = None,
        path: Optional[Union[str, Path]] = None,
        indent: Optional[int] = None,
    ) -> str:
        """
        @Author         : Vedang
        @Description    : Serializes coordinates() to a JSON string, optionally writing it to a file.
        @Input          : channel         - a channel id, list of ids, or None for all loaded channels
                           path            - optional output file path
                           indent          - optional pretty-print indent, compact JSON when None
        @Output         : text            - the JSON string
        @Note           : One of the five supported coordinate export formats (list/JSON/CSV/NumPy/DataFrame).
        """
        coords = self.coordinates(channel)
        text = json.dumps(coords, indent=indent)
        if path is not None:
            Path(path).write_text(text, encoding="utf-8")
            logger.info("Waveform coordinates exported to JSON: %s (%d points).", path, len(coords))
        return text

    def to_csv(self, channel: ChannelArg = None, path: Optional[Union[str, Path]] = None) -> str:
        """
        @Author         : Vedang
        @Description    : Serializes coordinates() to CSV text (time,channel,value), optionally writing it to a file.
        @Input          : channel         - a channel id, list of ids, or None for all loaded channels
                           path            - optional output file path
        @Output         : text            - the CSV string
        @Note           : One of the five supported coordinate export formats (list/JSON/CSV/NumPy/DataFrame).
        """
        coords = self.coordinates(channel)
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=["time", "channel", "value"])
        writer.writeheader()
        writer.writerows(coords)
        text = buffer.getvalue()
        if path is not None:
            Path(path).write_text(text, encoding="utf-8")
            logger.info("Waveform coordinates exported to CSV: %s (%d points).", path, len(coords))
        return text

    def to_numpy(self, channel: ChannelArg = None) -> np.ndarray:
        """
        @Author         : Vedang
        @Description    : Builds a structured NumPy array of coordinates using vectorized concatenation instead of a per-point loop.
        @Input          : channel         - a channel id, list of ids, or None for all loaded channels
        @Output         : array           - structured ndarray with fields time (f8), channel (i4), value (f8), sorted by time
        @Note           : Preferred path for large analog captures; also used internally by to_dataframe().
        """
        chans = self._resolve_channels(channel)
        times_parts, chan_parts, value_parts = [], [], []
        for ch in chans:
            data = self._channels[ch]
            times_parts.append(data.times)
            value_parts.append(data.values)
            chan_parts.append(np.full(data.times.shape, ch, dtype=np.int32))

        times = np.concatenate(times_parts) if times_parts else np.array([], dtype=np.float64)
        channels_arr = np.concatenate(chan_parts) if chan_parts else np.array([], dtype=np.int32)
        values = np.concatenate(value_parts) if value_parts else np.array([], dtype=np.float64)
        order = np.argsort(times, kind="stable")

        structured = np.empty(times.shape[0], dtype=[("time", "f8"), ("channel", "i4"), ("value", "f8")])
        structured["time"] = times[order]
        structured["channel"] = channels_arr[order]
        structured["value"] = values[order]

        logger.info("Waveform coordinates exported to NumPy array (%d points).", structured.shape[0])
        return structured

    def to_dataframe(self, channel: ChannelArg = None):
        """
        @Author         : Vedang
        @Description    : Builds a pandas DataFrame of coordinates (columns time, channel, value) from to_numpy().
        @Input          : channel         - a channel id, list of ids, or None for all loaded channels
        @Output         : df              - pandas.DataFrame of the requested coordinates
        @Note           : Raises WaveformError if pandas is not installed; pandas is imported lazily only here.
        """
        try:
            import pandas as pd
        except ImportError as exc:  # pragma: no cover - exercised only when pandas missing
            raise WaveformError(
                "pandas is required for to_dataframe(); install it with: pip install pandas"
            ) from exc

        arr = self.to_numpy(channel)
        df = pd.DataFrame({"time": arr["time"], "channel": arr["channel"], "value": arr["value"]})
        logger.info("Waveform coordinates exported to pandas DataFrame (%d rows).", len(df))
        return df
