import { describe, expect, test } from "bun:test"
import type { LanguageModelV3StreamPart } from "@ai-sdk/provider"
import * as proxy from "../src/proxy.ts"
import { type Envelope, proxiedCall, TurnMapper } from "../src/turns.ts"

let sequence = 0
const envelope = (type: string, payload: Record<string, unknown> = {}): Envelope => ({
  type,
  turn_id: "turn-1",
  sequence: ++sequence,
  payload,
})
const call = (call_id: string, name: string, args: Record<string, unknown>) =>
  envelope("tool_call", { call: { call_id, name, source: "built-in", arguments: args } })
const result = (call_id: string, content: string, extra: Record<string, unknown> = {}, outcome = "completed") =>
  envelope("tool_result", { resolution: { call_id, outcome, content }, ...extra })
const text = (value: string) => envelope("output_delta", { content: [{ type: "text", text: value }] })
const usage = (cost: string, tokens = 100) =>
  envelope("usage", {
    snapshot: {
      entries: [
        {
          provider: "openai",
          model: "gpt-6.1-sol",
          tokens_in: tokens,
          tokens_out: 10,
          cache_read_tokens: 0,
          cache_write_tokens: 0,
          cost: { USD: cost },
        },
      ],
    },
  })

const CHANGE = {
  path: "notes.txt",
  change: "modified",
  patch: "--- notes.txt\n+++ notes.txt\n@@ -1 +1 @@\n-old\n+new\n",
  additions: 1,
  deletions: 1,
}
const EDIT = { file_path: "notes.txt", old_string: "old", new_string: "new" }
const ALL = new Set(proxy.PROXY_TOOLS)

/** A mapper with a sink that records what it is told. */
function recorded(offered: ReadonlySet<string> = ALL) {
  const events: string[] = []
  const results = new Map<string, proxy.ProxyResult>()
  const sink = {
    register: (id: string) => events.push(`register ${id}`),
    resolve: (id: string, value: proxy.ProxyResult) => {
      events.push(`resolve ${id}`)
      results.set(id, value)
    },
    reject: (id: string, error: Error) => events.push(`reject ${id}: ${error.message}`),
  }
  return { mapper: new TurnMapper("openai/gpt-6.1-sol", { offered, sink, sessionID: "ses_1" }), events, results }
}

/** The parts of each step: a step ends at a `tool-calls` finish. */
function steps(parts: LanguageModelV3StreamPart[]): LanguageModelV3StreamPart[][] {
  const found: LanguageModelV3StreamPart[][] = [[]]
  for (const part of parts) {
    found.at(-1)!.push(part)
    if (part.type === "finish" && part.finishReason.unified === "tool-calls") found.push([])
  }
  return found.filter((step) => step.length > 0)
}

const finishOf = (step: LanguageModelV3StreamPart[]) =>
  step.find((part): part is Extract<LanguageModelV3StreamPart, { type: "finish" }> => part.type === "finish")

describe("proxied calls", () => {
  test("run as local calls, settle from Amplifier's result, and end the step before the reply", () => {
    const { mapper, events, results } = recorded()
    const parts = [
      envelope("turn_started", { primary_actual: { provider: "openai", model: "gpt-6.1-sol" } }),
      call("c1", "edit_file", EDIT),
      usage("0.01"),
      result("c1", "Edited notes.txt", { "com.microsoft.amplifier.file_changes": [CHANGE] }),
      text("Done."),
      usage("0.03", 300),
      envelope("progress", { data: { context_size: { tokens: 320, budget: 1000 } } }),
      envelope("terminal", { state: "success" }),
    ].flatMap((item) => mapper.map(item))

    const [first, second, ...rest] = steps(parts)
    expect(rest).toEqual([])
    const toolCall = first.find((part) => part.type === "tool-call")
    expect(toolCall).toEqual({
      type: "tool-call",
      toolCallId: "c1",
      toolName: "edit",
      input: JSON.stringify({ path: "notes.txt", oldString: "old", newString: "new" }),
    })
    expect(first.some((part) => part.type === "tool-result")).toBe(false)
    expect(finishOf(first)?.finishReason.unified).toBe("tool-calls")
    expect(second.some((part) => part.type === "text-delta")).toBe(true)
    expect(finishOf(second)?.finishReason.unified).toBe("stop")

    expect(events).toEqual(["register c1", "resolve c1"])
    const files = [{ file: "notes.txt", patch: CHANGE.patch, additions: 1, deletions: 1, status: "modified" }]
    expect(results.get("c1")).toEqual({
      output: { files, replacements: 1 },
      content: "Edited notes.txt",
      metadata: { files },
    })

    // Each step carries the cost added since the one before; the turn's total is not counted twice.
    expect(finishOf(first)?.usage.inputTokens.cacheWrite).toBeCloseTo(0.01)
    expect(finishOf(second)?.usage.inputTokens.cacheWrite).toBeCloseTo(0.02)
    expect(finishOf(second)?.usage.inputTokens.total).toBe(320)
  })

  test("keep several calls in one step and do not end it while a hosted call is open", () => {
    const { mapper, events } = recorded()
    const parts = [
      call("d1", "delegate", { agent: "explorer", instruction: "Look around" }),
      call("g1", "glob", { pattern: "*.md" }),
      call("g2", "grep", { pattern: "TODO", glob: "*.ts" }),
      result("g1", JSON.stringify({ pattern: "*.md", count: 2, total_files: 2, matches: ["a.md", "b.md"] })),
      result("g2", JSON.stringify({ total_matches: 3, matches_count: 3, files: ["a.ts"] })),
      text("Still delegating."),
      result("d1", "Found it."),
      text("Done."),
      envelope("terminal", { state: "success" }),
    ].flatMap((item) => mapper.map(item))

    const found = steps(parts)
    expect(found).toHaveLength(2)
    const [first, second] = found
    expect(first.filter((part) => part.type === "tool-call").map((part) => (part as { toolName: string }).toolName))
      .toEqual(["task", "glob", "grep"])
    // The delegate's result and the text that came while it ran stay in the step of the delegate call.
    expect(first.some((part) => part.type === "tool-result" && part.toolCallId === "d1")).toBe(true)
    expect(first.some((part) => part.type === "text-delta" && part.delta === "Still delegating.")).toBe(true)
    expect(second.some((part) => part.type === "text-delta" && part.delta === "Done.")).toBe(true)
    expect(events).toEqual(["register g1", "register g2", "resolve g1", "resolve g2"])
  })

  test("fail with Amplifier's error, and fail open calls when the turn breaks", () => {
    const { mapper, events } = recorded()
    mapper.map(call("c1", "edit_file", EDIT))
    mapper.map(call("c2", "apply_patch", { patch: "*** Begin Patch\n*** End Patch" }))
    mapper.map(result("c1", "", {}, "failed"))
    const parts = mapper.fail(new Error("The Amplifier turn was cancelled."))
    expect(events).toEqual([
      "register c1",
      "register c2",
      "reject c1: The tool call ended failed.",
      "reject c2: The Amplifier turn was cancelled.",
    ])
    expect(parts.at(-1)?.type).toBe("error")
  })

  test("stay hosted when opencode does not offer the tool or its input would not decode", () => {
    expect(proxiedCall({ name: "edit_file", arguments: EDIT }, new Set(["glob"]))).toBeUndefined()
    expect(proxiedCall({ name: "edit_file", arguments: { file_path: "a" } }, ALL)).toBeUndefined()
    expect(proxiedCall({ name: "grep", arguments: { pattern: "" } }, ALL)).toBeUndefined()
    expect(proxiedCall({ name: "glob", arguments: { pattern: "*" }, source: "mcp" }, ALL)).toBeUndefined()
    expect(proxiedCall({ name: "apply_patch", arguments: { patch: "P" } }, ALL)).toEqual({
      name: "patch",
      input: { patchText: "P" },
    })

    const { mapper, events } = recorded(new Set())
    const parts = [call("c1", "edit_file", EDIT), result("c1", "ok")].flatMap((item) => mapper.map(item))
    expect(parts.find((part) => part.type === "tool-call")).toMatchObject({ toolName: "edit", providerExecuted: true })
    expect(parts.find((part) => part.type === "tool-result")).toMatchObject({ toolCallId: "c1", result: "ok" })
    expect(events).toEqual([])
  })
})

describe("proxy registry", () => {
  test("hands a result to a wrapper that waits before or after it arrives, then forgets the call", async () => {
    proxy.register("early", "ses_a")
    const waiting = proxy.wait("early")
    proxy.resolve("early", proxy.proxyResult("glob", JSON.stringify({ count: 0, total_files: 0, matches: [] }), []))
    expect((await waiting).metadata).toEqual({ count: 0, truncated: false })

    proxy.register("late", "ses_a")
    proxy.resolve("late", proxy.proxyResult("grep", JSON.stringify({ matches_count: 4 }), []))
    expect((await proxy.wait("late")).metadata).toEqual({ matches: 4, truncated: false })
    expect(proxy.registered("early") || proxy.registered("late")).toBe(false)
  })

  test("rejects an interrupted session's waiting wrappers, and an aborted wrapper stops waiting", async () => {
    proxy.register("a1", "ses_a")
    proxy.register("b1", "ses_b")
    const a = proxy.wait("a1")
    proxy.rejectAll(new Error("The opencode session was interrupted."), "ses_a")
    await expect(a).rejects.toThrow("interrupted")
    expect(proxy.registered("b1")).toBe(true)

    const abort = new AbortController()
    const b = proxy.wait("b1", abort.signal)
    abort.abort(new Error("Tool execution interrupted"))
    await expect(b).rejects.toThrow("Tool execution interrupted")
    expect(proxy.registered("b1")).toBe(false)
  })

  test("forgets settled calls whose wrapper never ran", () => {
    proxy.register("x1", "ses_c")
    proxy.register("x2", "ses_c")
    proxy.resolve("x1", proxy.proxyResult("edit", "", []))
    proxy.forget("ses_c")
    expect([proxy.registered("x1"), proxy.registered("x2")]).toEqual([false, true])
    proxy.rejectAll(new Error("done"), "ses_c")
    proxy.forget("ses_c")
  })
})

describe("proxyResult", () => {
  test("gives the patch tool the applied files and diffs of Amplifier's file changes", () => {
    const added = { path: "new.md", change: "added", patch: "--- /dev/null\n+++ new.md\n", additions: 3, deletions: 0 }
    const changes = proxy.fileChanges({ "com.microsoft.amplifier.file_changes": [added, { path: 1 }] })
    expect(changes).toHaveLength(1)
    const value = proxy.proxyResult("patch", "Applied", changes)
    expect(value.output).toEqual({
      applied: [{ type: "add", resource: "new.md", target: "new.md" }],
      files: [{ file: "new.md", patch: added.patch, additions: 3, deletions: 0, status: "added" }],
    })
    expect(value.metadata).toEqual({ files: (value.output as { files: unknown }).files })
  })

  test("counts the lines of a search result that is not JSON", () => {
    expect(proxy.proxyResult("glob", "a.md\nb.md\n", []).metadata).toEqual({ count: 2, truncated: false })
  })
})
