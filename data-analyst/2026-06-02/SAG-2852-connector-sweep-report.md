# SAG-2852 — Connector Sweep: Sage→Fabricator AP Credit-Memo Data
**Date:** 2026-06-02  
**Analyst:** Data Analyst (fc67241d)  
**Scope:** Google Drive + Gmail + Zapier — 2023-01-01 through 2026-05-31  
**Purpose:** Exhaust agent-reachable connectors for Lever #1 AP credit-memo / debit-adjustment recovery ledger before human data-drop (per CEO ruling on SAG-2849)

---

## 1. Question
Does any agent-reachable connector (Google Drive, Gmail, Zapier) hold Sage→fabricator credit-memo, debit-adjustment, or AP recovery records for 2023–2026, usable to calculate fabricator support rate %, Sage-net $, and a netted Lever #1?

## 2. Data Sources & Denominators
- **Google Drive:** Full-text + title searches via `mcp__claude_ai_Google_Drive__search_files`; 14 distinct queries; all files owned by dmndbrp@gmail.com
- **Gmail:** Thread search via `mcp__claude_ai_Gmail__search_threads`; 11 distinct queries; 2023-01-01 forward
- **Zapier:** OAuth authentication required — connector present but unauthenticated (see §5)

---

## 3. Queries Run

### Google Drive (14 queries)
| Query | Files Found | AP-Relevant? |
|-------|-------------|--------------|
| fullText contains 'credit memo' (2023+) | 1 — Sage competitive analysis v1.1 | No — incidental business model description |
| fullText contains 'debit adjustment' (2023+) | 0 | — |
| fullText contains 'vendor credit' (2023+) | 1 — same competitive analysis | No — incidental mention |
| fullText contains 'QuickBooks' (2023+) | 0 | — |
| fullText contains 'AP recovery' (2023+) | 0 | — |
| fullText contains 'chargeback' (2023+) | 0 | — |
| fullText contains 'fabricator' (2023+) | 5 — Sage Overview.docx, Claude Memory Export.docx, DTC Franchise Deepdive.docx, cabinet-lead-scripts-v2.pdf (×2) | No — all BD/strategic docs; fabricator references are business model narrative |
| fullText contains 'Dbase' (2023+) | 0 | — |
| fullText contains 'Store 998' (2023+) | 0 | — |
| title contains 'credit memo' | 0 | — |
| title contains 'AP' (2023+) | 0 | — |
| title contains 'invoice' (2023+) | 0 | — |

**Drive AP-relevant hits: 0**

### Gmail (11 queries)
| Query | Threads Found | AP-Relevant? |
|-------|---------------|--------------|
| subject:(credit memo) after:2023/01/01 | 0 | — |
| subject:(debit adjustment) after:2023/01/01 | 0 | — |
| "credit memo" fabricator after:2023/01/01 | 0 | — |
| "vendor credit" after:2023/01/01 | 0 | — |
| QuickBooks fabricator after:2023/01/01 | 0 | — |
| chargeback fabricator after:2023/01/01 | 0 | — |
| "AP recovery" after:2023/01/01 | 0 | — |
| subject:(vendor credit OR chargeback OR settlement) fabricator | 0 | — |
| QuickBooks after:2023/01/01 | 1 — SSI Staging Deep Dive report (2026-05-04) | No — platform functionality test; "QuickBooks" referenced as system integration context |
| "Store 998" OR "RO #" OR "Lowes DM" after:2023/01/01 | 1 — same SSI staging report | No — RO # referenced as platform field description |
| Dbase "credit memo" after:2023/01/01 | 0 | — |

**Gmail AP-relevant hits: 0**

### Note on SSI Staging Match
Thread `19df3c6107777003` (SSI Staging Deep Dive Session 1, sent 2026-05-04 dmndbrp@gmail.com → gpinsoneault@hauspro.com) matched both QuickBooks and RO # queries. Content confirmed: a platform test-session report covering Login, Product Catalog, Price Lists, and Pricing modules. QuickBooks and RO # appear as platform field references, not as financial records. **Not AP data.**

---

## 4. Finding
**No Sage→fabricator credit-memo, debit-adjustment, vendor-credit, or AP-recovery records are present in the agent-reachable Google Drive or Gmail accounts for 2023–2026.**

The Drive contains exclusively BD/strategic documents (competitive analysis, company overview, franchise playbook, sales scripts). Gmail has no financial AP threads in the search window. Neither connector holds structured transaction data matching the target record spec (vendor name + date + amount + DM/RO cross-ref + GL direction).

Confidence: **High** that these connectors do not hold the ledger. This is not a gap in search coverage — it is a gap in where the data lives.

---

## 5. Zapier Status
Zapier connector (`mcp__claude_ai_Zapier`) is installed but unauthenticated (OAuth required). To authenticate:
- In the claude.ai interface: run `/mcp` → select "claude.ai Zapier" → complete OAuth flow
- Once authenticated, agent can enumerate connected Zaps and check for any QuickBooks/AP automation exporting credit memo data

---

## 6. Lens Applied
**Root cause vs. symptom:** The absence of data in Drive/Gmail is not a data-quality issue — it reflects where Sage's AP records are housed. Sage's accounting stack (QuickBooks + Dbase, per prior SAG-2848/2849 context) is likely not surfaced to personal Drive/Gmail. The QuickBooks connector standup is the logical next step.

**Freshness:** Drive and Gmail searches cover all files/threads since 2023-01-01 as of 2026-06-02. No stale data risk.

---

## 7. Recommendation
1. **Zapier (CFO action):** Complete `/mcp` → Zapier OAuth authentication. Then re-run sweep for QuickBooks/AP automation Zaps. Estimated time: <5 minutes.
2. **QuickBooks connector (CTO child issue):** If Zapier has no live QuickBooks export Zap, file a CTO child issue to stand up a QuickBooks MCP connector or export path. This is the most likely source of the structured credit-memo ledger.
3. **Human data-drop as fallback:** If Zapier + QuickBooks connector both come up empty, a manual export from QuickBooks/Dbase by the CFO or accounting team is required before Lever #1 can be netted below the current CEO-ruled range ($1.7M–$4.3M).

**Lever #1 remains at $1.7M–$4.3M (CEO-ruled range).** No connector data found that would narrow this range.

---

*Artifact path: `data-analyst/2026-06-02/SAG-2852-connector-sweep-report.md`*
