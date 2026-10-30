"""Plotting routines for 16:9 presentation-ready Urban Heat Island maps."""

import logging
from math import ceil
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import matplotlib
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize, TwoSlopeNorm
from mpl_toolkits.axes_grid1 import make_axes_locatable
import numpy as np

from uhi.io import UHIRaster

logger = logging.getLogger(__name__)

# Constants for 16:9 widescreen PowerPoint presentation layout
SLIDE_FIGSIZE: Tuple[float, float] = (13.333, 7.5)
DEFAULT_DPI: int = 300

TEMP_CMAP: str = "YlOrRd"
ANOM_CMAP: str = "Spectral_r"

FONT_FAMILY = "Figtree"
FALLBACK_FONTS = ["Figtree", "DejaVu Sans", "Arial", "sans-serif"]


def _configure_figtree() -> None:
    """Ensure font family is configured for Matplotlib."""
    plt.rcParams["font.family"] = FALLBACK_FONTS


def compute_temperature_limits(raster: UHIRaster, p_low: float = 2.0, p_high: float = 98.0) -> Tuple[float, float]:
    """Calculate lower and upper temperature percentiles for a raster."""
    valid = raster.data[np.isfinite(raster.data)]
    if valid.size == 0:
        return (0.0, 100.0)
    vmin = float(np.percentile(valid, p_low))
    vmax = float(np.percentile(valid, p_high))
    return (vmin, vmax)


def compute_shared_anomaly_limit(rasters: List[UHIRaster], percentile: float = 98.0) -> float:
    """
    Calculate a shared symmetric anomaly limit across all rasters.
    Rounds up to the nearest 0.5 or 1.0 °C for a clean colorbar.
    """
    max_departure = 0.0
    for r in rasters:
        anom = r.anomaly
        valid = anom[np.isfinite(anom)]
        if valid.size > 0:
            p_lo = abs(float(np.percentile(valid, 100.0 - percentile)))
            p_hi = abs(float(np.percentile(valid, percentile)))
            max_departure = max(max_departure, p_lo, p_hi)

    if max_departure <= 0.0:
        return 5.0

    # Round up to nearest 0.5 °C
    limit = ceil(max_departure * 2.0) / 2.0
    # If fractional part is 0.5, check if integer tick is even cleaner
    if limit < 1.0:
        limit = 1.0
    return float(limit)


# High-quality, watermark-free basemap tiles for thematic raster mapping
ESRI_GRAY_BASE_URL: str = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}"
)
ESRI_GRAY_REF_URL: str = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}"
)


def _apply_basemap(ax, crs_str: str) -> bool:
    """
    Attempt to add light gray basemap under the raster.
    Returns True if successful, False otherwise.
    """
    try:
        import contextily as cx

        cx.add_basemap(
            ax,
            crs=crs_str,
            source=ESRI_GRAY_BASE_URL,
            attribution=False,
            zorder=1,
        )
        return True
    except Exception as exc:
        logger.warning("Could not download basemap tiles: %s. Proceeding without basemap.", exc)
        return False


def _apply_basemap_labels(ax, crs_str: str) -> None:
    """Apply reference label overlay on top of raster if available."""
    try:
        import contextily as cx

        cx.add_basemap(
            ax,
            crs=crs_str,
            source=ESRI_GRAY_REF_URL,
            attribution=False,
            zorder=4,
        )
    except Exception as exc:
        logger.debug("Could not add basemap labels: %s", exc)


def plot_single_map(
    raster: UHIRaster,
    kind: str = "temperature",
    anomaly_limit: Optional[float] = None,
    add_basemap: bool = True,
    output_path: Optional[Union[str, Path]] = None,
    dpi: int = DEFAULT_DPI,
    region_name: str = "Northern Virginia",
    variable: str = "Air Temperature",
    units: str = "°C",
) -> Tuple[plt.Figure, plt.Axes]:
    """
    Render an individual 16:9 presentation map of either temperature or spatial anomaly.

    Parameters
    ----------
    raster : UHIRaster
        The raster object to plot.
    kind : {"temperature", "anomaly"}
        Type of map to generate.
    anomaly_limit : float, optional
        Symmetric limit for anomaly (e.g. 5.0 -> [-5.0, 5.0]). Required if kind="anomaly".
    add_basemap : bool
        Whether to fetch and render the CartoDB Positron basemap.
    output_path : Path or str, optional
        Filepath to save the resulting PNG.
    dpi : int
        Resolution for output image.
    """
    if kind not in ("temperature", "anomaly"):
        raise ValueError(f"Unknown kind '{kind}'. Expected 'temperature' or 'anomaly'.")

    _configure_figtree()

    stats = raster.summary()
    crs_str = raster.crs.to_string()

    fig = plt.figure(figsize=SLIDE_FIGSIZE, dpi=dpi, facecolor="#FFFFFF")
    # Position map centered on 16:9 slide with room for title and right-side colorbar
    # Coordinates in figure fraction: [left, bottom, width, height]
    ax = fig.add_axes([0.18, 0.08, 0.60, 0.78])

    has_basemap = False
    if add_basemap:
        # Pre-set limits so contextily knows the bounding box
        ax.set_xlim(raster.extent[0], raster.extent[1])
        ax.set_ylim(raster.extent[2], raster.extent[3])
        has_basemap = _apply_basemap(ax, crs_str)

    if kind == "temperature":
        cmap = TEMP_CMAP
        vmin, vmax = compute_temperature_limits(raster)
        norm = Normalize(vmin=vmin, vmax=vmax)
        data_to_plot = raster.data
        cbar_label = f"{variable} ({units})"
        title_main = f"{raster.label} — {variable}"
        title_sub = (
            f"{region_name} UHI Campaign • Spatial Mean: {stats['mean']:.1f} {units}  "
            f"[Min: {stats['min']:.1f} {units}, Max: {stats['max']:.1f} {units}]"
        )
    else:
        cmap = ANOM_CMAP
        lim = anomaly_limit if anomaly_limit is not None else 5.0
        vmin, vmax = -lim, lim
        norm = TwoSlopeNorm(vmin=-lim, vcenter=0.0, vmax=lim)
        data_to_plot = raster.anomaly
        cbar_label = f"{variable} Anomaly ({units})"
        anom_min = stats["min"] - stats["mean"]
        anom_max = stats["max"] - stats["mean"]
        title_main = f"{raster.label} — Spatial {variable} Anomaly"
        title_sub = (
            f"Deviation from Spatial Mean ({stats['mean']:.1f} {units}) • "
            f"Range: {anom_min:+.1f} {units} to {anom_max:+.1f} {units}"
        )

    # Plot raster
    raster_alpha = 0.84 if has_basemap else 1.0
    im = ax.imshow(
        data_to_plot,
        extent=raster.extent,
        cmap=cmap,
        norm=norm,
        alpha=raster_alpha,
        origin="upper",
        interpolation="bilinear",
        zorder=2,
    )

    if has_basemap:
        _apply_basemap_labels(ax, crs_str)

    # Map extent and styling
    ax.set_xlim(raster.extent[0], raster.extent[1])
    ax.set_ylim(raster.extent[2], raster.extent[3])
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)

    # Colorbar on the right
    cbar_ax = fig.add_axes([0.80, 0.16, 0.022, 0.62])
    cbar = fig.colorbar(im, cax=cbar_ax, orientation="vertical", extend="both")
    cbar.set_label(cbar_label, fontsize=13, weight="bold", labelpad=12, color="#222222")
    cbar.ax.tick_params(labelsize=11, colors="#222222")
    cbar.outline.set_edgecolor("#444444")
    cbar.outline.set_linewidth(0.8)

    # Titles
    ax.set_title(
        f"{title_main}\n",
        fontsize=18,
        weight="bold",
        color="#111111",
        pad=18,
        loc="center",
    )
    fig.text(
        0.48,
        0.88,
        title_sub,
        ha="center",
        va="center",
        fontsize=12,
        color="#555555",
        style="italic",
    )

    if output_path is not None:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, dpi=dpi, facecolor=fig.get_facecolor(), bbox_inches=None)
        logger.info("Saved single map figure: %s", out)

    return fig, ax


def plot_panel(
    rasters: List[UHIRaster],
    kind: str = "temperature",
    anomaly_limit: Optional[float] = None,
    add_basemap: bool = True,
    output_path: Optional[Union[str, Path]] = None,
    dpi: int = DEFAULT_DPI,
    region_name: str = "Northern Virginia",
    variable: str = "Air Temperature",
    units: str = "°C",
) -> Tuple[plt.Figure, List[plt.Axes]]:
    """
    Render a 3-panel comparison figure (AM, AF, PM) in 16:9 widescreen format.

    For temperature: each panel has an individual horizontal colorbar reflecting its diurnal range.
    For anomaly: all panels share one unified symmetric horizontal colorbar.
    """
    if kind not in ("temperature", "anomaly"):
        raise ValueError(f"Unknown kind '{kind}'. Expected 'temperature' or 'anomaly'.")

    _configure_figtree()

    n_panels = len(rasters)
    if n_panels != 3:
        raise ValueError(f"plot_panel expects exactly 3 rasters, received {n_panels}")

    fig = plt.figure(figsize=SLIDE_FIGSIZE, dpi=dpi, facecolor="#FFFFFF")

    # Layout coordinates for 3 side-by-side maps:
    # 13.333 wide slide:
    # Margin left = 0.06, width per panel = 0.27, gap = 0.045
    # Bottom = 0.22, height = 0.58
    panel_width = 0.275
    panel_gap = 0.038
    left_margin = 0.05
    panel_bottom = 0.22
    panel_height = 0.58

    axes: List[plt.Axes] = []
    images = []

    shared_lim = anomaly_limit if anomaly_limit is not None else compute_shared_anomaly_limit(rasters)

    for i, r in enumerate(rasters):
        ax_left = left_margin + i * (panel_width + panel_gap)
        ax = fig.add_axes([ax_left, panel_bottom, panel_width, panel_height])
        axes.append(ax)

        crs_str = r.crs.to_string()
        has_basemap = False
        if add_basemap:
            ax.set_xlim(r.extent[0], r.extent[1])
            ax.set_ylim(r.extent[2], r.extent[3])
            has_basemap = _apply_basemap(ax, crs_str)

        stats = r.summary()
        raster_alpha = 0.84 if has_basemap else 1.0

        if kind == "temperature":
            vmin, vmax = compute_temperature_limits(r)
            norm = Normalize(vmin=vmin, vmax=vmax)
            im = ax.imshow(
                r.data,
                extent=r.extent,
                cmap=TEMP_CMAP,
                norm=norm,
                alpha=raster_alpha,
                origin="upper",
                interpolation="bilinear",
                zorder=2,
            )
            images.append(im)

            if has_basemap:
                _apply_basemap_labels(ax, crs_str)

            # Individual horizontal colorbar beneath each temperature map
            cbar_ax = fig.add_axes([ax_left + 0.015, 0.10, panel_width - 0.03, 0.028])
            cb = fig.colorbar(im, cax=cbar_ax, orientation="horizontal", extend="both")
            cb.set_label(f"{r.label} {variable} ({units})", fontsize=10, weight="bold", labelpad=4, color="#222222")
            cb.ax.tick_params(labelsize=9, colors="#222222")
            cb.outline.set_edgecolor("#444444")
            cb.outline.set_linewidth(0.8)

            ax.set_title(
                f"{r.label}\nMean: {stats['mean']:.1f} {units}",
                fontsize=13,
                weight="bold",
                color="#111111",
                pad=10,
            )

        else:  # kind == "anomaly"
            norm = TwoSlopeNorm(vmin=-shared_lim, vcenter=0.0, vmax=shared_lim)
            im = ax.imshow(
                r.anomaly,
                extent=r.extent,
                cmap=ANOM_CMAP,
                norm=norm,
                alpha=raster_alpha,
                origin="upper",
                interpolation="bilinear",
                zorder=2,
            )
            images.append(im)

            if has_basemap:
                _apply_basemap_labels(ax, crs_str)

            ax.set_title(
                f"{r.label}\nMean: {stats['mean']:.1f} {units}",
                fontsize=13,
                weight="bold",
                color="#111111",
                pad=10,
            )

        ax.set_xlim(r.extent[0], r.extent[1])
        ax.set_ylim(r.extent[2], r.extent[3])
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)

    # Super titles
    if kind == "temperature":
        fig.suptitle(
            f"{region_name} Urban Heat Island Campaign — Diurnal {variable} Progression",
            fontsize=17,
            weight="bold",
            color="#111111",
            y=0.95,
        )
        fig.text(
            0.5,
            0.89,
            f"{variable} surfaces modeled across {region_name} for Morning (AM), Afternoon (AF), and Evening (PM)",
            ha="center",
            va="center",
            fontsize=11,
            color="#555555",
            style="italic",
        )
    else:
        fig.suptitle(
            f"{region_name} Urban Heat Island Campaign — Diurnal Spatial {variable} Anomalies",
            fontsize=17,
            weight="bold",
            color="#111111",
            y=0.95,
        )
        means_str = ", ".join(f"{r.label}: {r.summary()['mean']:.1f} {units}" for r in rasters)
        fig.text(
            0.5,
            0.89,
            f"Spatial anomaly = Local {variable} − Campaign Spatial Mean ({means_str})",
            ha="center",
            va="center",
            fontsize=11,
            color="#555555",
            style="italic",
        )

        # Single unified horizontal colorbar across all 3 panels
        cbar_ax = fig.add_axes([0.28, 0.09, 0.44, 0.028])
        cb = fig.colorbar(images[0], cax=cbar_ax, orientation="horizontal", extend="both")
        cb.set_label(f"Spatial {variable} Anomaly ({units})", fontsize=11, weight="bold", labelpad=5, color="#222222")
        cb.ax.tick_params(labelsize=10, colors="#222222")
        cb.outline.set_edgecolor("#444444")
        cb.outline.set_linewidth(0.8)

    if output_path is not None:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, dpi=dpi, facecolor=fig.get_facecolor(), bbox_inches=None)
        logger.info("Saved panel figure: %s", out)

    return fig, axes

