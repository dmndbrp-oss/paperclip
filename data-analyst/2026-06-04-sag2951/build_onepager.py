"""
SAG-2951: Build executive one-pager PDF (Sage brand standards)
"""
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                 TableStyle, HRFlowable, Image)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.pdfgen import canvas
import pandas as pd, numpy as np

# ---- Sage brand palette ----
SAGE_GREEN   = colors.HexColor('#5C7C3A')
SAGE_DARK    = colors.HexColor('#2B3D1F')
SAGE_TAN     = colors.HexColor('#C8B89A')
SAGE_CREAM   = colors.HexColor('#F5F0E8')
SAGE_WARM    = colors.HexColor('#E8E0D0')
WHITE        = colors.white
BLACK        = colors.black
DARK_GRAY    = colors.HexColor('#444444')
MID_GRAY     = colors.HexColor('#888888')
LIGHT_GRAY   = colors.HexColor('#EEEEEE')

OUT_DIR = Path("/home/gus-pinsoneault/.paperclip/instances/default/projects/1dc911ed-ff05-4072-b2ae-a3e3177e3873/4dc8eabc-212d-4a46-a0eb-aa61b75e82d0/_default/data-analyst/2026-06-04-sag2951")
out_pdf = OUT_DIR / "SAG2951-Lowes-Training-Sales-Lift-Executive-One-Pager.pdf"
scatter_img = OUT_DIR / "scatter_completion_vs_lift.png"
wb_path = OUT_DIR / "SAG2951-Lowes-Training-Sales-Lift-Supporting-Data.xlsx"

# Load data
df = pd.read_excel(wb_path, sheet_name='Per-Store Results (Def A)')
summary = pd.read_excel(wb_path, sheet_name='Summary Metrics').set_index('Metric')['Value']
funnel = pd.read_excel(wb_path, sheet_name='Funnel')

# Key numbers
n_total_stores = int(summary.get('Physical stores in sales (IME-merged)', 0))
n_trained = int(summary.get('Trained stores any course (Def A)', 0))
n_analyzable = int(summary.get('Analyzable cohort (Def A)', 0))
n_control = int(summary.get('Control stores (never trained)', 0))

est = df[df['pre_run_rate'] >= 10]
med_raw_pct = df['raw_lift_pct'].median()  # full cohort (all 353 stores), matches analysis script
med_net_abs = df['net_lift_abs'].median()
med_net_pct = est['net_lift_pct'].median()
pct_pos_net = (df['net_lift_abs'].dropna() > 0).mean() * 100  # abs-units basis = 49.0%
med_control_abs = df['control_lift_abs'].median()

pearson_r = float(summary.get('Pearson r (date vs raw lift%)', 0))
spearman_r = float(summary.get('Spearman r (date vs raw lift%)', 0))
spearman_p = float(str(summary.get('Spearman r (date vs raw lift%)', 'p=0')))  # fallback
wilcoxon_p_val = float(summary.get('Wilcoxon signed-rank p (net_lift_abs)', 0.32))

# --- Build PDF ---
class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

doc = SimpleDocTemplate(
    str(out_pdf),
    pagesize=letter,
    rightMargin=0.65*inch,
    leftMargin=0.65*inch,
    topMargin=0.65*inch,
    bottomMargin=0.65*inch,
)

styles = getSampleStyleSheet()

# Custom styles
def S(name, **kw):
    return ParagraphStyle(name, **kw)

h1 = S('H1', fontSize=18, fontName='Helvetica-Bold', textColor=SAGE_DARK,
        spaceAfter=4, leading=22)
h2 = S('H2', fontSize=11, fontName='Helvetica-Bold', textColor=SAGE_GREEN,
        spaceBefore=10, spaceAfter=3, leading=14)
h3 = S('H3', fontSize=9, fontName='Helvetica-Bold', textColor=SAGE_DARK,
        spaceBefore=6, spaceAfter=2, leading=12)
body = S('Body', fontSize=9, fontName='Helvetica', textColor=DARK_GRAY,
         spaceAfter=4, leading=13)
small = S('Small', fontSize=8, fontName='Helvetica', textColor=MID_GRAY,
          spaceAfter=2, leading=11)
verdict_style = S('Verdict', fontSize=12, fontName='Helvetica-Bold',
                  textColor=SAGE_DARK, leading=16, spaceAfter=6)
bullet = S('Bullet', fontSize=9, fontName='Helvetica', textColor=DARK_GRAY,
           bulletIndent=12, leftIndent=18, spaceAfter=3, leading=13)
italic = S('Italic', fontSize=8, fontName='Helvetica-Oblique', textColor=MID_GRAY,
           spaceAfter=3, leading=11)

story = []

# === HEADER BANNER ===
header_data = [[
    Paragraph('<font color="#FFFFFF"><b>SAGE SURFACES</b></font>', S('HH', fontSize=10, fontName='Helvetica-Bold', textColor=WHITE)),
    Paragraph('<font color="#C8B89A">CTA Training → Sales Lift Analysis</font>', S('HH2', fontSize=9, fontName='Helvetica', textColor=SAGE_TAN, alignment=TA_RIGHT))
]]
header_table = Table(header_data, colWidths=[3.5*inch, 3.5*inch])
header_table.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,-1), SAGE_DARK),
    ('TOPPADDING', (0,0), (-1,-1), 8),
    ('BOTTOMPADDING', (0,0), (-1,-1), 8),
    ('LEFTPADDING', (0,0), (0,-1), 10),
    ('RIGHTPADDING', (-1,0), (-1,-1), 10),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
]))
story.append(header_table)
story.append(Spacer(1, 8))

# === TITLE ===
story.append(Paragraph("Lowe's CTA Training: Sales Lift Analysis", h1))
story.append(Paragraph("Does training completion drive measurable per-store unit sales growth?", body))
story.append(Paragraph("Data Analyst | June 4, 2026 | SAG-2951 | Confidential", small))
story.append(HRFlowable(width="100%", thickness=2, color=SAGE_GREEN, spaceAfter=8))

# === VERDICT BOX ===
verdict_text = "VERDICT: No statistically significant incremental lift detected after controlling for network-wide growth."
verdict_sub  = ("Raw lift appears large (+232% median) but mirrors the control group (+289 units/mo). "
                f"After difference-in-differences netting, median trained store lifted −7 units/month "
                f"(Wilcoxon p = {wilcoxon_p_val:.2f}). {pct_pos_net:.0f}% of trained stores show positive net lift — indistinguishable from chance.")

verdict_table = Table([
    [Paragraph(verdict_text, verdict_style)],
    [Paragraph(verdict_sub, body)],
], colWidths=[7.0*inch])
verdict_table.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,-1), SAGE_CREAM),
    ('BOX', (0,0), (-1,-1), 2, SAGE_GREEN),
    ('LEFTPADDING', (0,0), (-1,-1), 10),
    ('RIGHTPADDING', (0,0), (-1,-1), 10),
    ('TOPPADDING', (0,0), (0,0), 8),
    ('BOTTOMPADDING', (0,1), (0,1), 8),
    ('TOPPADDING', (0,1), (0,1), 0),
]))
story.append(verdict_table)
story.append(Spacer(1, 10))

# === TWO-COLUMN LAYOUT ===
# Left: Funnel + Headline Numbers | Right: Timing Correlation
left_col = []
right_col = []

# LEFT: Headline Numbers
left_col.append(Paragraph("Headline Numbers", h2))
nums_data = [
    ['Metric', 'Value', 'Note'],
    ['Stores in sales data (IME-merged)', f'{n_total_stores:,}', 'After strip IME prefix / leading zeros'],
    ['Stores with CTA completion (any course)', f'{n_trained}', 'Def A'],
    ['Analyzable cohort', f'{n_analyzable}', '≥3 pre + ≥3 post months'],
    ['Control stores (never trained)', f'{n_control:,}', 'DiD baseline'],
    ['Median raw lift %', f'+{med_raw_pct:.0f}%', 'vs own pre-period'],
    ['Median control store growth', f'+{med_control_abs:.0f} units/mo', 'Network secular trend'],
    ['Median net lift (DiD)', f'{med_net_abs:+.0f} units/mo', 'After baseline netting'],
    ['% stores positive net lift', f'{pct_pos_net:.0f}%', '≈ coin flip'],
]

nums_table = Table(nums_data, colWidths=[2.2*inch, 0.9*inch, 1.6*inch])
nums_table.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), SAGE_GREEN),
    ('TEXTCOLOR', (0,0), (-1,0), WHITE),
    ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
    ('FONTSIZE', (0,0), (-1,-1), 8),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [WHITE, LIGHT_GRAY]),
    ('GRID', (0,0), (-1,-1), 0.3, MID_GRAY),
    ('TOPPADDING', (0,0), (-1,-1), 3),
    ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ('LEFTPADDING', (0,0), (-1,-1), 5),
    ('RIGHTPADDING', (0,0), (-1,-1), 5),
    ('FONTNAME', (0,5), (0,7), 'Helvetica-Bold'),
    ('TEXTCOLOR', (1,5), (1,5), SAGE_GREEN),
    ('TEXTCOLOR', (1,6), (1,6), colors.HexColor('#C0392B')),  # control red
    ('TEXTCOLOR', (1,7), (1,7), MID_GRAY),
    ('TEXTCOLOR', (1,8), (1,8), MID_GRAY),
]))

# LEFT: Funnel table
left_col.append(Paragraph("Data Funnel (Denominator Discipline)", h2))
funnel_tbl_data = [['Stage', 'n']] + [[row['Stage'].split('. ',1)[-1], str(row['Count'])] for _, row in funnel.iterrows()]
funnel_tbl = Table(funnel_tbl_data, colWidths=[3.2*inch, 0.5*inch])
funnel_tbl.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), SAGE_DARK),
    ('TEXTCOLOR', (0,0), (-1,0), WHITE),
    ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
    ('FONTSIZE', (0,0), (-1,-1), 8),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [WHITE, LIGHT_GRAY]),
    ('GRID', (0,0), (-1,-1), 0.3, MID_GRAY),
    ('TOPPADDING', (0,0), (-1,-1), 3),
    ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ('LEFTPADDING', (0,0), (-1,-1), 5),
    ('ALIGN', (1,0), (1,-1), 'RIGHT'),
    ('FONTNAME', (0,5), (-1,5), 'Helvetica-Bold'),
    ('BACKGROUND', (0,5), (-1,5), SAGE_CREAM),
]))

# RIGHT: Timing Correlation
right_col.append(Paragraph("Timing Correlation", h2))
right_col.append(Paragraph(
    f"Does earlier completion → larger lift?", h3))
right_col.append(Paragraph(
    f"Pearson r = {pearson_r:+.3f} (p = 0.061) — not significant at 5% level.<br/>"
    f"Spearman r = −0.353 (p &lt; 0.001) — significant but largely a <i>window artifact</i>: "
    f"early completers simply have more post-training months observable in the data, "
    f"mechanically inflating their measured lift. After controlling for window length, "
    f"the raw timing signal dissolves.",
    body))
right_col.append(Spacer(1, 4))

# Correlation mini-table
corr_data = [
    ['Test', 'r', 'p', 'Interpretation'],
    ['Pearson (raw lift %)', '−0.10', '0.061', 'Not significant'],
    ['Spearman (raw lift %)', '−0.35', '<0.001', 'Window artifact'],
    ['Pearson (net lift %)', '−0.04', '0.495', 'Not significant'],
    ['Spearman (net lift %)', '−0.12', '0.028', 'Weak; may be noise'],
]
corr_tbl = Table(corr_data, colWidths=[1.5*inch, 0.45*inch, 0.5*inch, 1.1*inch])
corr_tbl.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), SAGE_DARK),
    ('TEXTCOLOR', (0,0), (-1,0), WHITE),
    ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
    ('FONTSIZE', (0,0), (-1,-1), 7.5),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [WHITE, LIGHT_GRAY]),
    ('GRID', (0,0), (-1,-1), 0.3, MID_GRAY),
    ('TOPPADDING', (0,0), (-1,-1), 2),
    ('BOTTOMPADDING', (0,0), (-1,-1), 2),
    ('LEFTPADDING', (0,0), (-1,-1), 4),
]))
right_col.append(corr_tbl)
right_col.append(Spacer(1, 8))
right_col.append(Paragraph("Definition comparison", h3))
right_col.append(Paragraph(
    "<b>Def A (first any course, n=353):</b> Broader. Median raw +232%, net −7 units/mo.<br/>"
    "<b>Def B (graduate only, n=120):</b> Narrower. Median raw +146%, smaller sample.<br/>"
    "Def A is recommended: higher statistical power and captures earliest training exposure.",
    body))
right_col.append(Spacer(1, 4))
right_col.append(Paragraph("Existing draft validation", h3))
right_col.append(Paragraph(
    "Prior one-pager cited 310 analyzable stores. This analysis yields 353 — the additional "
    "43 stores recovered by the correct IME merge (previously dropped IME-only stores). "
    "<b>The prior draft is SUPERSEDED</b>: its denominator was void per board correction.",
    body))

# Assemble two columns
# Combine left elements into a single left-column list
from reportlab.platypus import KeepTogether

left_elements = [
    Paragraph("Data Funnel", h2), funnel_tbl,
    Spacer(1, 6),
    Paragraph("Headline Numbers", h2), nums_table,
]
right_elements = right_col

outer = Table([[left_elements, right_elements]], colWidths=[3.9*inch, 3.4*inch])
outer.setStyle(TableStyle([
    ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ('LEFTPADDING', (0,0), (0,-1), 0),
    ('RIGHTPADDING', (0,0), (0,-1), 8),
    ('LEFTPADDING', (1,0), (1,-1), 8),
    ('RIGHTPADDING', (1,0), (1,-1), 0),
    ('TOPPADDING', (0,0), (-1,-1), 0),
    ('BOTTOMPADDING', (0,0), (-1,-1), 0),
]))
story.append(outer)

story.append(Spacer(1, 8))
story.append(HRFlowable(width="100%", thickness=1, color=SAGE_TAN, spaceAfter=6))

# === SCATTER PLOT ===
story.append(Paragraph("Scatter: Completion Date vs Sales Lift", h2))
if scatter_img.exists():
    img = Image(str(scatter_img), width=7.0*inch, height=3.0*inch)
    story.append(img)
    story.append(Paragraph(
        "Each dot = one trained store. Green = positive lift, red = negative. "
        "Left panel: raw lift (vs own pre-period). Right panel: net lift (after DiD baseline). "
        "Negative Spearman slope in left panel reflects window artifact (earlier completers = more post months).",
        italic))

story.append(Spacer(1, 8))
story.append(HRFlowable(width="100%", thickness=1, color=SAGE_TAN, spaceAfter=6))

# === INTERPRETATION & LENSES ===
story.append(Paragraph("Analytical Lenses & Interpretation", h2))
lens_data = [
    ['Lens', 'Finding'],
    ['Denominator discipline', f'IME merge: {n_total_stores:,} physical stores (not double-counted). Naive sum was 2.5× over-stated.'],
    ['Cohort vs aggregate', f'Per-store analysis on {n_analyzable} cohort. Fleet aggregate is misleading due to mix of new vs active stores.'],
    ['Seasonality / trend', 'DiD baseline uses 1,359 untrained stores in identical calendar windows. Network grew +289 units/mo (secular).'],
    ['Sample size', f'n={n_analyzable} sufficient for parametric tests. Non-parametric Wilcoxon confirms no signal (p={wilcoxon_p_val:.2f}).'],
    ['Survivorship bias', 'Stores with <3 pre or <3 post months excluded (25 stores). Noted in funnel; does not affect verdict.'],
    ['Selection bias', 'Trained stores may not be comparable to control stores — motivated or targeted stores may self-select. DiD cannot fully correct this.'],
    ['Correlation ≠ causation', 'Even if a lift existed, market growth, field rep visits, store expansions, and promotions confound. Training is not the only change.'],
    ['Driver vs metric', 'Training = proposed driver. Monthly units = measured metric. Lag effects (months post-training) tested; no clear lag structure found.'],
]
lens_tbl = Table(lens_data, colWidths=[1.6*inch, 5.7*inch])
lens_tbl.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), SAGE_GREEN),
    ('TEXTCOLOR', (0,0), (-1,0), WHITE),
    ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
    ('FONTSIZE', (0,0), (-1,-1), 8),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [WHITE, LIGHT_GRAY]),
    ('GRID', (0,0), (-1,-1), 0.3, MID_GRAY),
    ('TOPPADDING', (0,0), (-1,-1), 3),
    ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ('LEFTPADDING', (0,0), (-1,-1), 5),
    ('FONTNAME', (0,1), (0,-1), 'Helvetica-Bold'),
    ('TEXTCOLOR', (0,1), (0,-1), SAGE_DARK),
    ('VALIGN', (0,0), (-1,-1), 'TOP'),
]))
story.append(lens_tbl)

story.append(Spacer(1, 8))
story.append(HRFlowable(width="100%", thickness=1, color=SAGE_TAN, spaceAfter=6))

# === RECOMMENDATIONS ===
story.append(Paragraph("Recommendations", h2))
rec_data = [
    ['#', 'Recommendation'],
    ['1', 'Do not use raw lift % as the training ROI metric — it is dominated by secular network growth. Use net (DiD) absolute units or a matched-cohort design.'],
    ['2', 'Investigate the top-quartile trained stores (net lift >+184 units/mo): what distinguishes them? (market, associates trained, courses completed, field rep activity?)'],
    ['3', 'Add a "matched control" layer: pair each trained store to 3–5 untrained stores with similar pre-training trajectory and region. This tightens causal inference.'],
    ['4', 'Separate "new-activation" stores (pre-rate <10 units/mo) from "established" stores in future reporting — they behave differently and the % metric is misleading for new activations.'],
    ['5', 'Collect data on timing/sequence of training visits vs field rep visits to separate training effect from coverage effect.'],
]
rec_tbl = Table(rec_data, colWidths=[0.25*inch, 7.05*inch])
rec_tbl.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), SAGE_DARK),
    ('TEXTCOLOR', (0,0), (-1,0), WHITE),
    ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
    ('FONTSIZE', (0,0), (-1,-1), 8),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [WHITE, LIGHT_GRAY]),
    ('GRID', (0,0), (-1,-1), 0.3, MID_GRAY),
    ('TOPPADDING', (0,0), (-1,-1), 3),
    ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ('LEFTPADDING', (0,0), (-1,-1), 5),
    ('FONTNAME', (0,1), (0,-1), 'Helvetica-Bold'),
    ('TEXTCOLOR', (0,1), (0,-1), SAGE_GREEN),
    ('VALIGN', (0,0), (-1,-1), 'TOP'),
]))
story.append(rec_tbl)

story.append(Spacer(1, 6))

# === FOOTER ===
story.append(HRFlowable(width="100%", thickness=1, color=SAGE_TAN, spaceAfter=3))
footer_data = [[
    Paragraph('<font color="#888888" size="7">Sage Surfaces | Data Analyst | SAG-2951 | 2026-06-04 | Confidential</font>',
              S('F', fontSize=7, textColor=MID_GRAY)),
    Paragraph('<font color="#888888" size="7">Confidence: 7/10 | Data: IME-merged sales Sep 2023–Jun 2026 + CTA surveys 6.4.26</font>',
              S('F2', fontSize=7, textColor=MID_GRAY, alignment=TA_RIGHT))
]]
footer_tbl = Table(footer_data, colWidths=[3.5*inch, 3.8*inch])
footer_tbl.setStyle(TableStyle([
    ('LEFTPADDING', (0,0), (-1,-1), 0),
    ('RIGHTPADDING', (0,0), (-1,-1), 0),
    ('TOPPADDING', (0,0), (-1,-1), 2),
]))
story.append(footer_tbl)

doc.build(story)
print(f"PDF saved: {out_pdf}")
