import { describe, expect, it } from "vitest";
import type { Db } from "@paperclipai/db";
import { issueService } from "../services/issues.ts";

// Minimal drizzle-shaped mock: select().from().where() resolves to rows.
function makeMockDb(agentRows: { id: string; name: string }[]): Db {
  const chainable = {
    from: () => chainable,
    where: () => Promise.resolve(agentRows),
    then: (resolve: (v: typeof agentRows) => unknown) =>
      Promise.resolve(agentRows).then(resolve),
  };
  return { select: () => chainable } as unknown as Db;
}

const COMPANY_ID = "company-1";
const LINKED_AGENT_ID = "3ab7fa06-f831-4631-922a-2fe824005788"; // Coder (Claude)
const NAMED_AGENT_ID = "9a20c1b5-a039-4c18-8962-2825e3f28538"; // Coder

const DB_AGENTS = [
  { id: LINKED_AGENT_ID, name: "Coder (Claude)" },
  { id: NAMED_AGENT_ID, name: "Coder" },
];

describe("findMentionedAgents — link display text stripping", () => {
  it("link-only mention returns only the explicitly linked agent ID", async () => {
    const svc = issueService(makeMockDb(DB_AGENTS));
    const body = `[@Coder (Claude)](agent://${LINKED_AGENT_ID})`;
    const result = await svc.findMentionedAgents(COMPANY_ID, body);
    expect(result).toEqual([LINKED_AGENT_ID]);
    expect(result).not.toContain(NAMED_AGENT_ID);
  });

  it("bare @name mention returns the name-matched agent ID", async () => {
    const svc = issueService(makeMockDb(DB_AGENTS));
    const body = "@Coder please look at this";
    const result = await svc.findMentionedAgents(COMPANY_ID, body);
    expect(result).toContain(NAMED_AGENT_ID);
  });

  it("link + bare mention combined returns both IDs", async () => {
    const svc = issueService(makeMockDb(DB_AGENTS));
    const body = `[@Coder (Claude)](agent://${LINKED_AGENT_ID}) and also @Coder`;
    const result = await svc.findMentionedAgents(COMPANY_ID, body);
    expect(result).toContain(LINKED_AGENT_ID);
    expect(result).toContain(NAMED_AGENT_ID);
    expect(result).toHaveLength(2);
  });
});
