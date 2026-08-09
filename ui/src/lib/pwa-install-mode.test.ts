import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const uiRoot = resolve(fileURLToPath(new URL("../..", import.meta.url)));

describe("PWA install mode", () => {
  it("uses standalone display so an installed PWA can receive Web Push (SAG-7601)", () => {
    const manifest = JSON.parse(readFileSync(resolve(uiRoot, "public/site.webmanifest"), "utf8")) as {
      display?: string;
    };
    const html = readFileSync(resolve(uiRoot, "index.html"), "utf8");

    // SAG-7601: iOS/iPadOS 16.4+ only delivers Web Push to an installed standalone PWA,
    // so the manifest must declare standalone. This supersedes the earlier
    // display:"browser" choice from the mobile-flow polish (#6550).
    expect(manifest.display).toBe("standalone");
    // Standalone is driven by the manifest; the legacy Apple/Chromium *-capable meta
    // tags remain intentionally absent (modern PWA install path).
    expect(html).not.toContain('name="mobile-web-app-capable"');
    expect(html).not.toContain('name="apple-mobile-web-app-capable"');
    expect(html).not.toContain('name="apple-mobile-web-app-status-bar-style"');
  });
});
