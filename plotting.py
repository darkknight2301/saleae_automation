"""
@Author         : Vedang
@Description    : Headless (matplotlib Agg backend) waveform plotting utilities used by Saleae.plot()/save_image(); renders already-computed Waveform data only, no protocol decoding here.
@Input          : None
@Output         : None
@Note           : Never imports pyplot before calling matplotlib.use("Agg"), so no GUI/X11/display is ever required, including over SSH.
"""

from __future__ import annotations

import bisect
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import matplotlib

matplotlib.use("Agg")  # Must precede the pyplot import below.
import matplotlib.pyplot as plt  # noqa: E402  (backend must be set first)

from exceptions import SaleaeException  # noqa: E402

logger = logging.getLogger("saleae_wrapper.plotting")

_SUPPORTED_FORMATS = {"png", "svg"}
_DEFAULT_FIGSIZE = (10.0, 6.0)


class PlottingError(SaleaeException):
    """
    @Author         : Vedang
    @Description    : Raised for waveform plotting/rendering/save failures (bad format, empty channel data, savefig errors).
    @Input          : None
    @Output         : None
    @Note           : Subclasses SaleaeException so it can be caught alongside every other wrapper error.
    """


def _crop(
    times: Sequence[float],
    values: Sequence[float],
    start: Optional[float],
    end: Optional[float],
) -> Tuple[List[float], List[float], float, float]:
    """
    @Author         : Vedang
    @Description    : Crops a channel's (times, values) series to a [start, end] time window, carrying in the pre-window state so a step plot doesn't start blank.
    @Input          : times           - ascending sample/transition timestamps for one channel
                       values          - values paired one-to-one with times
                       start           - window start in seconds, defaults to times[0] when None
                       end             - window end in seconds, defaults to times[-1] when None
    @Output         : result          - tuple (cropped_times, cropped_values, resolved_start, resolved_end)
    @Note           : Raises PlottingError if end < start; internal helper used by plot_waveform, not part of the public API.
    """
    times = list(times)
    values = list(values)
    if not times:
        resolved_start = start if start is not None else 0.0
        resolved_end = end if end is not None else resolved_start
        return [], [], resolved_start, resolved_end

    resolved_start = times[0] if start is None else start
    resolved_end = times[-1] if end is None else end
    if resolved_end < resolved_start:
        raise PlottingError(f"end ({resolved_end}) must be >= start ({resolved_start}).")

    left = bisect.bisect_right(times, resolved_start) - 1
    left = max(left, 0)
    right = bisect.bisect_right(times, resolved_end)

    cropped_times = times[left:right]
    cropped_values = values[left:right]

    if not cropped_times:
        # The window falls entirely outside the recorded transitions;
        # show the state that was active at `resolved_start`.
        cropped_times = [resolved_start]
        cropped_values = [values[left] if left < len(values) else values[-1]]
    elif cropped_times[0] < resolved_start:
        cropped_times[0] = resolved_start

    return cropped_times, cropped_values, resolved_start, resolved_end


def plot_waveform(
    waveform: Any,
    channels: Optional[Sequence[int]] = None,
    start: Optional[float] = None,
    end: Optional[float] = None,
    markers: Optional[Sequence[Any]] = None,
    channel_labels: Optional[Dict[int, str]] = None,
    title: Optional[str] = None,
    figsize: Optional[Sequence[float]] = None,
    dpi: Optional[int] = None,
    save: Optional[Union[str, Path]] = None,
) -> Union[Path, "plt.Figure"]:
    """
    @Author         : Vedang
    @Description    : Renders a stacked, per-channel waveform plot (digital = step, analog = line) from a Waveform instance and optionally saves it as PNG/SVG.
    @Input          : waveform        - waveform.Waveform instance to read coordinates()/statistics() from
                       channels        - channel indices to plot, defaults to all channels on waveform
                       start           - plot window start in seconds, defaults to earliest data point
                       end             - plot window end in seconds, defaults to latest data point
                       markers         - marker-like objects (label, segment_elapsed) to overlay as vertical lines
                       channel_labels  - optional {channel: label} overrides for row labels
                       title           - plot title, auto-generated from channels/time-range when None
                       figsize         - (width, height) inches, defaults to (10, 6)
                       dpi             - figure resolution in dots per inch
                       save            - output file path (.png/.svg); returns an unsaved Figure when None
    @Output         : result          - saved image Path when save is given, else the open matplotlib Figure
    @Note           : Raises PlottingError on empty channel data, an unsupported save extension, or a savefig failure; the figure is always closed once saved.
    """
    channel_list = list(channels) if channels is not None else list(waveform.channels)
    if not channel_list:
        raise PlottingError("No channels available to plot.")

    figsize = tuple(figsize) if figsize is not None else _DEFAULT_FIGSIZE
    savefig_kwargs: Dict[str, Any] = {}
    if dpi is not None:
        savefig_kwargs["dpi"] = dpi

    fig, axes = plt.subplots(
        len(channel_list), 1, sharex=True, figsize=figsize, squeeze=False, dpi=dpi
    )
    axes = axes[:, 0]

    window_start, window_end = start, end
    per_channel_kind: Dict[int, str] = {}

    for ax, ch in zip(axes, channel_list):
        points = waveform.coordinates(ch)
        if not points:
            plt.close(fig)
            raise PlottingError(f"No data points available for channel {ch}.")

        times = [p["time"] for p in points]
        values = [p["value"] for p in points]
        t, v, resolved_start, resolved_end = _crop(times, values, start, end)
        if window_start is None or resolved_start < window_start:
            window_start = resolved_start
        if window_end is None or resolved_end > window_end:
            window_end = resolved_end

        kind = waveform.statistics(channel=ch).get("kind", "digital")
        per_channel_kind[ch] = kind

        if kind == "digital":
            ax.step(t, v, where="post", linewidth=1.5, color="tab:blue")
            ax.set_ylim(-0.3, 1.3)
            ax.set_yticks([0, 1])
        else:
            ax.plot(t, v, linewidth=1.0, color="tab:orange")
            ax.margins(y=0.1)

        label = (channel_labels or {}).get(ch, f"Ch {ch}\n({kind})")
        ax.set_ylabel(label, rotation=0, ha="right", va="center", fontsize=9)
        ax.grid(True, alpha=0.3)

    axes[-1].set_xlim(window_start, window_end)
    axes[-1].set_xlabel("Time (s)")

    if markers:
        _overlay_markers(fig, axes, markers, window_start, window_end)

    if title is None:
        chan_str = ", ".join(str(c) for c in channel_list)
        title = f"Waveform — Channel(s) {chan_str} — {window_start:.6g}s to {window_end:.6g}s"
    fig.suptitle(title)
    fig.tight_layout(rect=(0, 0, 1, 0.94))

    if save is None:
        return fig

    save_path = Path(save)
    fmt = save_path.suffix.lstrip(".").lower()
    if fmt not in _SUPPORTED_FORMATS:
        plt.close(fig)
        raise PlottingError(
            f"Unsupported image format '{fmt}' for {save_path}. "
            f"Supported formats: {sorted(_SUPPORTED_FORMATS)}."
        )

    save_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fig.savefig(save_path, format=fmt, **savefig_kwargs)
    except Exception as exc:  # noqa: BLE001
        raise PlottingError(f"Failed to save waveform image to {save_path}: {exc}") from exc
    finally:
        plt.close(fig)

    logger.info(
        "Waveform plot saved: %s (channels=%s, window=%.6gs-%.6gs, format=%s).",
        save_path,
        channel_list,
        window_start,
        window_end,
        fmt,
    )
    return save_path


def _overlay_markers(
    fig: "plt.Figure",
    axes: Sequence["plt.Axes"],
    markers: Sequence[Any],
    window_start: Optional[float],
    window_end: Optional[float],
) -> None:
    """
    @Author         : Vedang
    @Description    : Draws vertical dashed marker lines and rotated labels on every axis of a rendered waveform plot.
    @Input          : fig             - the matplotlib Figure being annotated
                       axes            - the per-channel Axes list, sharing the time axis
                       markers         - marker-like objects with .label and .segment_elapsed
                       window_start    - plotted window start in seconds, used to skip out-of-range markers
                       window_end      - plotted window end in seconds, used to skip out-of-range markers
    @Output         : None            - mutates fig/axes in place
    @Note           : Markers with segment_elapsed is None (recorded outside any active capture) are skipped and logged at debug level.
    """
    plotted = 0
    for marker in markers:
        x = getattr(marker, "segment_elapsed", None)
        label = getattr(marker, "label", "")
        if x is None:
            logger.debug("Marker '%s' has no segment_elapsed; skipping overlay.", label)
            continue
        if window_start is not None and x < window_start:
            continue
        if window_end is not None and x > window_end:
            continue
        for ax in axes:
            ax.axvline(x, color="red", linestyle="--", linewidth=0.8, alpha=0.7)
        axes[0].annotate(
            label,
            xy=(x, 1.02),
            xycoords=("data", "axes fraction"),
            rotation=90,
            va="bottom",
            ha="center",
            fontsize=8,
            color="red",
        )
        plotted += 1
    logger.info("Overlaid %d marker(s) on waveform plot.", plotted)
