"""oram.ears.report — markdown listening report generation."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from oram.ears.analyzer import ListeningReport


def report_to_markdown(report: ListeningReport, date: datetime | None = None) -> str:
    """generate a markdown listening report."""
    if date is None:
        date = datetime.now()

    lines = [
        "# oram listening report",
        "",
        f"session: {report.session_id}",
        f"scene: {report.scene}",
        f"date: {date.strftime('%Y-%m-%d')}",
        "",
        "## oram hears",
        "",
    ]

    for obs in report.observations:
        lines.append(f"- {obs}")

    if not report.observations:
        lines.append("- silence")

    lines.append("")
    lines.append("## layer notes")
    lines.append("")

    for la in report.layer_analyses:
        parts = [f"{la.duration_seconds:.1f}s"]

        if la.is_generated:
            parts.append("generated bed")
        if la.muted:
            parts.append("muted")
        if la.effects:
            parts.extend(la.effects)

        rms_desc = "low" if la.analysis.rms < 0.1 else "moderate" if la.analysis.rms < 0.3 else "high"
        parts.append(f"{rms_desc} RMS")

        lines.append(f"- L{la.layer_id}: {', '.join(parts)}")

    if not report.layer_analyses:
        lines.append("- no active layers")

    lines.append("")
    return "\n".join(lines)


def save_report(report: ListeningReport, path: Path, date: datetime | None = None) -> Path:
    """save a listening report to a markdown file."""
    markdown = report_to_markdown(report, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
    return path
