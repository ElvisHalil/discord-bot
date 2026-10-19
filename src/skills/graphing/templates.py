"""
Output templates and synthesis instructions for `src/skills/graphing/`.
"""

from src.skills.graphing.models import RenderedVisual


def format_visual_confirmation(visual: RenderedVisual) -> str:
    """Format the tool return payload confirming that the PNG image has been queued for Discord/Telegram attachment."""
    lines = [
        f"=== [VISUALIZATION RENDERED & ATTACHED: {visual.title}] ===",
        f"• **Attachment Queued:** `{visual.filename}` ({len(visual.png_bytes):,} bytes, rendered via `{visual.renderer_used}`)",
    ]
    for s in visual.summary_lines:
        lines.append(f"• {s}")
    lines.append("")
    lines.append(
        "[SYNTHESIS INSTRUCTIONS]:\n"
        "- The chart/diagram image above has ALREADY been attached to your Discord/Telegram reply automatically.\n"
        "- Write a concise, insightful accompaniment summarizing the key takeaway, trend, or structure shown in the visual.\n"
        "- Do NOT output raw ASCII art or raw ```mermaid code blocks in your text reply since the rendered PNG is attached."
    )
    return "\n".join(lines)

