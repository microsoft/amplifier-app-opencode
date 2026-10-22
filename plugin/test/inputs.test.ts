import { describe, expect, test } from "bun:test"
import { mcpServers, skillDirectories } from "../src/inputs.ts"

const skill = (name: string, description = "Does a thing.") => `---\nname: ${name}\ndescription: ${description}\n---\nBody`

describe("skillDirectories", () => {
  test("keeps the directories of SKILL.md files that name and describe a skill", () => {
    const files: Record<string, string> = {
      "/p/.opencode/skills/a/SKILL.md": skill("a"),
      "/home/.claude/skills/b/SKILL.md": skill("'b'"),
    }
    const found = skillDirectories(
      [{ path: "/p/.opencode/skills/a/SKILL.md" }, { path: "/home/.claude/skills/b/SKILL.md" }],
      (file) => files[file],
    )
    expect(found).toEqual(["/p/.opencode/skills/a", "/home/.claude/skills/b"])
  })

  test("leaves out built-in, single-file, missing, undescribed, and duplicate skills", () => {
    const files: Record<string, string> = {
      "/p/skills/notes.md": skill("notes"),
      "/p/skills/bare/SKILL.md": "---\nname: bare\n---\nBody",
      "/p/skills/first/SKILL.md": skill("same"),
      "/q/skills/second/SKILL.md": skill("same"),
    }
    const found = skillDirectories(
      [
        { path: "/builtin/opencode.md" },
        { path: "/p/skills/notes.md" },
        { path: "/gone/SKILL.md" },
        { path: "/p/skills/bare/SKILL.md" },
        { path: "/p/skills/first/SKILL.md" },
        { path: "/q/skills/second/SKILL.md" },
      ],
      (file) => files[file],
    )
    expect(found).toEqual(["/p/skills/first"])
  })
})

describe("mcpServers", () => {
  test("maps local servers to stdio and remote servers to http", () => {
    const { servers, skipped } = mcpServers(
      [
        ["files", { type: "local", command: ["npx", "-y", "server"], environment: { TOKEN: "t" } }],
        ["docs", { type: "remote", url: "https://example.com/mcp", headers: { Authorization: "Bearer x" } }],
        ["plain", { type: "local", command: ["server"] }],
      ],
      new Map([
        ["files", "connected"],
        ["docs", "pending"],
        ["plain", "connected"],
      ]),
    )
    expect(servers).toEqual([
      { name: "files", transport: "stdio", command: "npx", args: ["-y", "server"], env: { TOKEN: "t" } },
      { name: "docs", transport: "http", url: "https://example.com/mcp", headers: { Authorization: "Bearer x" } },
      { name: "plain", transport: "stdio", command: "server" },
    ])
    expect(skipped).toEqual([])
  })

  test("leaves out disabled, removed, failed, and unauthorized servers", () => {
    const { servers, skipped } = mcpServers(
      [
        ["off", { type: "local", command: ["a"], disabled: true }],
        ["denied", { type: "local", command: ["b"] }],
        ["broken", { type: "local", command: ["c"] }],
        ["signin", { type: "remote", url: "https://example.com/mcp" }],
        ["stopped", { type: "local", command: ["d"] }],
      ],
      new Map([
        ["off", "disabled"],
        ["broken", "failed"],
        ["signin", "needs_auth"],
        ["stopped", "disabled"],
      ]),
    )
    expect(servers).toEqual([])
    expect(skipped).toEqual(["broken (failed)", "signin (needs_auth)"])
  })
})
