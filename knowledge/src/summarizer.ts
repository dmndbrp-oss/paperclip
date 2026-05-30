// TOPOLOGY NOTE: PaperclipTaskSummarizer delegates summarization to a worker
// agent via a child issue. The summarizerAgentId MUST NOT be the same agent
// that runs the digester routine — per-agent heartbeats are serialized, so the
// polling heartbeat would deadlock the summarization heartbeat. Re-pointing
// the production routine to a different caller agent is tracked in SAG-2383.

export interface Summarizer {
  summarize(system: string, user: string): Promise<string>;
}

interface PaperclipTaskSummarizerConfig {
  apiUrl: string;
  apiKey: string;
  companyId: string;
  summarizerAgentId: string;
  runId?: string;
}

const POLL_INTERVAL_MS = 3_000;
const TIMEOUT_MS = 300_000;

export class PaperclipTaskSummarizer implements Summarizer {
  constructor(private readonly cfg: PaperclipTaskSummarizerConfig) {}

  async summarize(system: string, user: string): Promise<string> {
    const { apiUrl, apiKey, companyId, summarizerAgentId, runId } = this.cfg;

    const mutatingHeaders: Record<string, string> = {
      Authorization: `Bearer ${apiKey}`,
      'Content-Type': 'application/json',
    };
    if (runId) mutatingHeaders['X-Paperclip-Run-Id'] = runId;

    // Extract identifier from the trusted header section for a readable title.
    const identifierMatch = user.match(/^identifier:\s+(.+)$/m);
    const identifier = identifierMatch?.[1]?.trim() ?? 'unknown';

    const description = [
      '## System instructions for knowledge digestion',
      '',
      system,
      '',
      '## Issue to digest',
      '',
      user,
      '',
      '## Your task',
      '',
      'Post a single comment whose body is ONLY the YAML block (starting with `task_id:`), then set this issue to done.',
      '',
      '## YAML formatting rules (REQUIRED — the parser is strict)',
      '',
      'The YAML parser uses YAML 1.2 strict mode. A plain (unquoted) scalar must NOT contain `: ` (colon followed by space) anywhere — the parser treats it as a nested mapping and errors.',
      '',
      'RULE: If any string value contains `: ` (colon-space), you MUST wrap it in double quotes.',
      '',
      'Examples:',
      '  summary: "Cancelled SAG-1120: 10d-stale staging audit, unrecoverable"   # colon → quoted',
      '  summary: No colon here, no quotes needed                                 # safe → no quotes',
      '  decisions:',
      '    - "Chose Option C: route via task delegation"                           # list item with colon → quoted',
      '    - DEFER pending Snyk scan                                               # no colon → no quotes',
    ].join('\n');

    const createRes = await fetch(`${apiUrl}/api/companies/${companyId}/issues`, {
      method: 'POST',
      headers: mutatingHeaders,
      body: JSON.stringify({
        title: `digest:${identifier}`,
        description,
        assigneeAgentId: summarizerAgentId,
        status: 'todo',
        priority: 'medium',
      }),
    });

    if (!createRes.ok) {
      const body = await createRes.text().catch(() => '');
      throw new Error(
        `PaperclipTaskSummarizer: create issue HTTP ${createRes.status}: ${body.slice(0, 200)}`,
      );
    }

    const created = (await createRes.json()) as { id: string };
    const delegatedIssueId = created.id;

    const deadline = Date.now() + TIMEOUT_MS;

    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));

      const pollRes = await fetch(`${apiUrl}/api/issues/${delegatedIssueId}`, {
        headers: { Authorization: `Bearer ${apiKey}` },
      });
      if (!pollRes.ok) continue;

      const issue = (await pollRes.json()) as { status: string };
      if (issue.status !== 'done') continue;

      const commentsRes = await fetch(`${apiUrl}/api/issues/${delegatedIssueId}/comments`, {
        headers: { Authorization: `Bearer ${apiKey}` },
      });

      if (!commentsRes.ok) {
        throw new Error(
          `PaperclipTaskSummarizer: fetch comments HTTP ${commentsRes.status} for ${delegatedIssueId}`,
        );
      }

      const comments = (await commentsRes.json()) as Array<{ body: string }>;
      const yamlComment = comments.find((c) => c.body.includes('task_id:'));

      if (!yamlComment) {
        throw new Error(
          `PaperclipTaskSummarizer: issue ${delegatedIssueId} done but no YAML comment found`,
        );
      }

      return yamlComment.body;
    }

    throw new Error(
      `PaperclipTaskSummarizer: timeout after ${TIMEOUT_MS / 1000}s waiting for issue ${delegatedIssueId}`,
    );
  }
}
