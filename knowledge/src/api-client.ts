// Shared bare-fetch API client for Paperclip — used by digester and blockers runner.

export interface ApiConfig {
  apiUrl: string;
  apiKey: string;
  companyId: string;
}

export interface BlockerAttention {
  state: string;
  reason?: string;
  unresolvedBlockerCount: number;
  coveredBlockerCount: number;
  stalledBlockerCount: number;
  attentionBlockerCount: number;
  sampleBlockerIdentifier: string | null;
  sampleStalledBlockerIdentifier: string | null;
}

export interface IssueListItem {
  id: string;
  identifier: string;
  title: string;
  description: string;
  status: string;
  priority: 'critical' | 'high' | 'medium' | 'low';
  assigneeAgentId: string | null;
  createdAt: string;
  startedAt: string | null;
  updatedAt: string;
  completedAt: string | null;
  blockerAttention: BlockerAttention | null;
}

export interface TerminalBlocker {
  id: string;
  identifier: string;
  title: string;
  status: string;
  assigneeAgentId: string | null;
}

export interface BlockedByItem {
  id: string;
  identifier: string;
  title: string;
  status: string;
  priority: string;
  assigneeAgentId: string | null;
  terminalBlockers: TerminalBlocker[];
}

export interface BlocksItem {
  id: string;
  identifier: string;
  title?: string;
  status: string;
}

export interface IssueDetail extends IssueListItem {
  blockedBy: BlockedByItem[];
  blocks: BlocksItem[];
}

export interface Interaction {
  id: string;
  kind: string;
  status: 'pending' | 'accepted' | 'rejected' | 'cancelled';
}

export interface PostedComment {
  id: string;
}

export async function listIssuesByStatus(
  config: ApiConfig,
  status: string,
): Promise<IssueListItem[]> {
  const url = new URL(`${config.apiUrl}/api/companies/${config.companyId}/issues`);
  url.searchParams.set('status', status);

  const res = await fetch(url.toString(), {
    headers: { Authorization: `Bearer ${config.apiKey}` },
  });
  if (!res.ok) throw new Error(`listIssuesByStatus(${status}): HTTP ${res.status}`);

  const data = (await res.json()) as unknown;
  return Array.isArray(data)
    ? (data as IssueListItem[])
    : ((data as { issues?: IssueListItem[]; data?: IssueListItem[] }).issues ??
        (data as { data?: IssueListItem[] }).data ??
        []);
}

export async function getIssue(config: ApiConfig, id: string): Promise<IssueDetail> {
  const res = await fetch(`${config.apiUrl}/api/issues/${id}`, {
    headers: { Authorization: `Bearer ${config.apiKey}` },
  });
  if (!res.ok) throw new Error(`getIssue(${id}): HTTP ${res.status}`);
  return res.json() as Promise<IssueDetail>;
}

export async function getInteractions(
  config: ApiConfig,
  id: string,
): Promise<Interaction[]> {
  const res = await fetch(`${config.apiUrl}/api/issues/${id}/interactions`, {
    headers: { Authorization: `Bearer ${config.apiKey}` },
  });
  if (!res.ok) return [];
  const data = (await res.json()) as unknown;
  return Array.isArray(data) ? (data as Interaction[]) : [];
}

export async function postComment(
  config: ApiConfig,
  id: string,
  body: string,
): Promise<PostedComment> {
  const res = await fetch(`${config.apiUrl}/api/issues/${id}/comments`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${config.apiKey}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ body }),
  });
  if (!res.ok) throw new Error(`postComment(${id}): HTTP ${res.status}`);
  return res.json() as Promise<PostedComment>;
}
