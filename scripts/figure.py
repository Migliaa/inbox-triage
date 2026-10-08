"""Draw the flow figure: docs/img/flow.svg (English) and, if report/ exists, the Italian copy.

    python scripts/figure.py
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TEXT = {
    "en": {
        "email": ["Incoming email"],
        "model": ["Decision model", "OpenDecider-nano, on CPU", "a probability for each of", "4 labels + “is it an injection?”"],
        "sure": "top label ≥ 0.90",
        "unsure": ["uncertain · injection · spam"],
        "actions": ["Proposed actions", "from a fixed routing table"],
        "draft": ["Reply draft", "generative model,", "no tools, no token"],
        "person": ["A person", "approves, edits or rejects", "each action"],
        "client": ["Client systems", "mail · CRM · Slack"],
        "held": ["Held: nothing planned", "stays in the list, to review"],
        "label": "a person can give the label",
        "read": "read token only",
        "write": "write token, only after an approval",
        "trace": "LangSmith: one trace per email — decision, draft, human verdict",
        "ci": "GitHub Actions: the tests on the gate, the tokens and the failures run on every push",
    },
    "it": {
        "email": ["Email in arrivo"],
        "model": ["Modello decisionale", "OpenDecider-nano, su CPU", "una probabilità per ciascuna", "di 4 etichette + «è un’iniezione?»"],
        "sure": "etichetta più probabile ≥ 0,90",
        "unsure": ["incerta · iniezione · spam"],
        "actions": ["Azioni proposte", "da una tabella fissa"],
        "draft": ["Bozza di risposta", "modello generativo,", "senza strumenti né permessi"],
        "person": ["Una persona", "approva, corregge o rifiuta", "ogni azione"],
        "client": ["Sistemi del cliente", "posta · CRM · Slack"],
        "held": ["Trattenuta: nessuna azione", "resta in elenco, da rivedere"],
        "label": "una persona può dare l’etichetta",
        "read": "solo permesso di lettura",
        "write": "permesso di scrittura, solo dopo l’approvazione",
        "trace": "LangSmith: una traccia per email — decisione, bozza, verdetto della persona",
        "ci": "GitHub Actions: i test su approvazione, permessi e guasti girano a ogni modifica",
    },
}

INK, MUTED, LINE, ACCENT, SOFT, WARN = "#16263d", "#51607a", "#9aa7bb", "#1d4ed8", "#eef3fb", "#fff4dc"


def box(x, y, w, h, lines, fill="#ffffff", stroke=INK):
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>']
    start = y + h / 2 - (len(lines) - 1) * 9 + 5
    for i, line in enumerate(lines):
        weight, colour, size = ("600", INK, 14) if i == 0 else ("400", MUTED, 12)
        out.append(f'<text x="{x + w / 2}" y="{start + i * 18}" text-anchor="middle" font-size="{size}" font-weight="{weight}" fill="{colour}">{line}</text>')
    return "\n".join(out)


def arrow(points, dashed=False):
    path = "M" + " L".join(f"{x},{y}" for x, y in points)
    dash = ' stroke-dasharray="5 4"' if dashed else ""
    return f'<path d="{path}" fill="none" stroke="{INK}" stroke-width="1.5"{dash} marker-end="url(#tip)"/>'


def band(x, y, w, text, fill):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="30" rx="6" fill="{fill}" stroke="{LINE}"/>'
            f'<text x="{x + w / 2}" y="{y + 20}" text-anchor="middle" font-size="12.5" fill="{INK}">{text}</text>')


def draw(t: dict) -> str:
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 440" font-family="Segoe UI, Helvetica, Arial, sans-serif">',
        '<defs><marker id="tip" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
        f'<path d="M0,0 L10,5 L0,10 z" fill="{INK}"/></marker></defs>',
        '<rect width="1200" height="440" fill="#ffffff"/>',
        box(20, 105, 130, 60, t["email"]),
        box(190, 85, 220, 100, t["model"], fill=SOFT),
        box(480, 40, 150, 70, t["actions"]),
        box(655, 40, 165, 70, t["draft"]),
        box(845, 40, 165, 70, t["person"], fill=SOFT, stroke=ACCENT),
        box(1035, 40, 150, 70, t["client"]),
        box(480, 190, 190, 60, t["held"], fill="#f5f6f8", stroke=LINE),
        arrow([(150, 135), (188, 135)]),
        arrow([(410, 115), (445, 115), (445, 75), (478, 75)]),
        arrow([(410, 155), (445, 155), (445, 220), (478, 220)]),
        arrow([(630, 75), (653, 75)]),
        arrow([(820, 75), (843, 75)]),
        arrow([(1010, 75), (1033, 75)]),
        arrow([(555, 190), (555, 112)], dashed=True),
        f'<text x="438" y="70" font-size="12" fill="{MUTED}" text-anchor="end">{t["sure"]}</text>',
        f'<text x="438" y="208" font-size="12" fill="{MUTED}" text-anchor="end">{t["unsure"][0]}</text>',
        f'<text x="565" y="155" font-size="12" fill="{MUTED}">{t["label"]}</text>',
        band(20, 300, 800, t["read"], "#f5f6f8"),
        band(845, 300, 340, t["write"], WARN),
        band(20, 345, 1165, t["trace"], SOFT),
        band(20, 390, 1165, t["ci"], "#ffffff"),
        "</svg>",
    ]
    return "\n".join(parts) + "\n"


def main() -> None:
    (ROOT / "docs" / "img" / "flow.svg").write_text(draw(TEXT["en"]), encoding="utf-8", newline="\n")
    report = ROOT / "report" / "figure"
    if report.exists():
        (report / "fig-flusso.svg").write_text(draw(TEXT["it"]), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
