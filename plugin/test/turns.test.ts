import { describe, expect, test } from "bun:test"
import { readFileSync } from "node:fs"
import path from "node:path"
import type { LanguageModelV3Prompt, LanguageModelV3StreamPart } from "@ai-sdk/provider"
import {
  type Envelope,
  envelopes,
  permissionAsk,
  promptMessages,
  toolDisplay,
  TurnMapper,
  turnUsage,
} from "../src/turns.ts"

const encoder = new TextEncoder()

function fixture(name: string): string {
  return readFileSync(path.join(import.meta.dir, "fixtures", name), "utf8")
}

async function parse(chunks: readonly (string | Uint8Array)[]): Promise<Envelope[]> {
  const source = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(typeof chunk === "string" ? encoder.encode(chunk) : chunk)
      controller.close()
    },
  })
  const found: Envelope[] = []
  const reader = source.pipeThrough(envelopes()).getReader()
  for (let read = await reader.read(); !read.done; read = await reader.read()) found.push(read.value)
  return found
}

async function mapped(name: string): Promise<LanguageModelV3StreamPart[]> {
  const mapper = new TurnMapper("openai/gpt-6.1-sol")
  return (await parse([fixture(name)])).flatMap((envelope) => mapper.map(envelope))
}

describe("promptMessages", () => {
  test("keeps user and assistant turns, drops opencode's system prompt and tool traffic", () => {
    const prompt: LanguageModelV3Prompt = [
      { role: "system", content: "You are an AI agent running in OpenCode." },
      { role: "user", content: [{ type: "text", text: "Write hello.txt" }] },
      {
        role: "assistant",
        content: [
          { type: "reasoning", text: "Writing the file." },
          { type: "tool-call", toolCallId: "c1", toolName: "write", input: {}, providerExecuted: true },
          { type: "tool-result", toolCallId: "c1", toolName: "write", output: { type: "text", value: "ok" } },
          { type: "text", text: "Done." },
        ],
      },
      {
        role: "tool",
        content: [{ type: "tool-result", toolCallId: "c2", toolName: "x", output: { type: "text", value: "" } }],
      },
      { role: "user", content: [{ type: "text", text: "<system-update>\nInstructions changed.\n</system-update>" }] },
      {
        role: "user",
        content: [
          { type: "text", text: "What is this?" },
          { type: "file", mediaType: "image/png", data: new Uint8Array([1, 2, 3]) },
          { type: "file", mediaType: "application/pdf", data: "AAAA" },
        ],
      },
    ]

    expect(promptMessages(prompt)).toEqual([
      { role: "user", content: [{ type: "text", text: "Write hello.txt" }] },
      { role: "assistant", content: "Done.", reasoning_content: "Writing the file." },
      { role: "system", content: "Instructions changed." },
      {
        role: "user",
        content: [
          { type: "text", text: "What is this?" },
          { type: "image_url", image_url: { url: "data:image/png;base64,AQID" } },
        ],
      },
    ])
  })

  test("keeps an assistant message whose only parts were tool calls, so the turns line up", () => {
    const prompt: LanguageModelV3Prompt = [
      { role: "user", content: [{ type: "text", text: "a" }] },
      { role: "assistant", content: [{ type: "tool-call", toolCallId: "c", toolName: "bash", input: {} }] },
      { role: "user", content: [{ type: "text", text: "b" }] },
    ]
    const messages = promptMessages(prompt)
    expect(messages.map((message) => message.role)).toEqual(["user", "assistant", "user"])
    expect(messages[1]).toEqual({ role: "assistant", content: "" })
  })
})

describe("envelopes", () => {
  const text = fixture("failed-tool.sse")

  test("parses every envelope in order", async () => {
    const found = await parse([text])
    expect(found.map((envelope) => envelope.sequence)).toEqual(found.map((_, index) => index + 1))
    expect(found.at(-1)?.type).toBe("terminal")
  })

  test("gives the same envelopes however the stream is split, also inside a UTF-8 character", async () => {
    const expected = await parse([text])
    const bytes = encoder.encode(`${text}data: {"type":"progress","payload":{"note":"caf\u00e9 \u2713"}}\n\n`)
    const all = [...expected, { type: "progress", payload: { note: "caf\u00e9 \u2713" } }]
    for (const at of [1, 7, 100, 501, 1999, bytes.length - 3, bytes.length - 1]) {
      expect(await parse([bytes.subarray(0, at), bytes.subarray(at)])).toEqual(all)
    }
    const bytewise = Array.from(bytes, (_, index) => bytes.subarray(index, index + 1))
    expect(await parse(bytewise)).toEqual(all)
  })

  test("accepts CRLF and CR endings, a CRLF split across chunks, and a last event without a blank line", async () => {
    const one = 'data: {"type":"usage","payload":{"snapshot":{}}}'
    const two = 'data: {"type":"terminal","payload":{"state":"success"}}'
    const types = ["usage", "terminal"]
    expect((await parse([`${one}\r\n\r\n${two}\r\n\r\n`])).map((envelope) => envelope.type)).toEqual(types)
    expect((await parse([`${one}\r\r${two}\r\r`])).map((envelope) => envelope.type)).toEqual(types)
    expect((await parse([`${one}\r`, `\n\r`, `\n${two}\r\n`])).map((envelope) => envelope.type)).toEqual(types)
    expect((await parse([`: comment\n${one}\n\n${two}`])).map((envelope) => envelope.type)).toEqual(types)
  })

  test("skips events that hold no envelope", async () => {
    expect(await parse(["data: not json\n\nevent: ping\n\ndata: {\"no\":\"type\"}\n\n"])).toEqual([])
  })
})

describe("TurnMapper", () => {
  test("maps parallel delegate calls to hosted task calls, each with its result, and finishes with stop", async () => {
    const parts = await mapped("parallel-delegates.sse")

    expect(parts[0]).toEqual({ type: "stream-start", warnings: [] })
    expect(parts[1]).toMatchObject({ type: "response-metadata", modelId: "openai/gpt-6.1-sol" })
    const calls = parts.filter((part) => part.type === "tool-call")
    expect(calls).toHaveLength(2)
    for (const call of calls) {
      expect(call).toMatchObject({ toolName: "task", providerExecuted: true })
      const input = JSON.parse(call.input)
      expect(input.description).toStartWith("Reply with only the word")
      expect(input.prompt).toStartWith("Reply with only the word")
    }
    const results = parts.filter((part) => part.type === "tool-result")
    expect(results.map((part) => [part.toolCallId, part.result, part.isError])).toEqual([
      [calls[0].toolCallId, "alpha", undefined],
      [calls[1].toolCallId, "beta", undefined],
    ])
    for (const call of calls) {
      const start = parts.findIndex((part) => part.type === "tool-input-start" && part.id === call.toolCallId)
      expect(parts.slice(start, start + 4).map((part) => part.type)).toEqual([
        "tool-input-start",
        "tool-input-delta",
        "tool-input-end",
        "tool-call",
      ])
    }
    const texts = parts.filter((part) => part.type.startsWith("text-"))
    expect(texts.map((part) => part.type)).toEqual(["text-start", "text-delta", "text-delta", "text-end"])
    expect(texts.flatMap((part) => (part.type === "text-delta" ? [part.delta] : [])).join("")).toBe("alpha beta")
    const finish = parts.at(-1)
    expect(finish).toMatchObject({ type: "finish", finishReason: { unified: "stop", raw: "success" } })
    expect(parts.filter((part) => part.type === "finish")).toHaveLength(1)
  })

  test("reports a failed tool as an error result and the turn's usage as the context size and cost", async () => {
    const parts = await mapped("failed-tool.sse")

    const call = parts.find((part) => part.type === "tool-call")
    expect(call).toMatchObject({ toolName: "bash", providerExecuted: true })
    expect(JSON.parse(call?.type === "tool-call" ? call.input : "{}")).toEqual({
      command: "ls /nonexistent-dir-xyz .",
    })
    const result = parts.find((part) => part.type === "tool-result")
    expect(result).toMatchObject({ isError: true })
    expect(result?.type === "tool-result" && String(result.result)).toStartWith(
      "The tool failed. Correct the reported tool failure before trying again.\n{",
    )
    const finish = parts.at(-1)
    expect(finish?.type).toBe("finish")
    if (finish?.type !== "finish") return
    // Context size 3463 from the last progress event, and cost 0.0015060 USD carried in cache writes.
    expect(finish.usage.inputTokens).toEqual({
      total: 3463,
      noCache: 3463 - 0.001506,
      cacheRead: 0,
      cacheWrite: 0.001506,
    })
    expect(finish.usage.outputTokens.total).toBe(0)
  })

  test("ends a failed turn with its usage, then the error", async () => {
    const parts = await mapped("terminal-failure.sse")

    expect(parts.slice(-6).map((part) => part.type)).toEqual([
      "reasoning-end",
      "text-start",
      "text-delta",
      "text-end",
      "finish",
      "error",
    ])
    expect(parts.at(-2)).toMatchObject({ finishReason: { unified: "stop", raw: "failure" } })
    const error = parts.at(-1)
    expect(error?.type === "error" && (error.error as Error).message).toBe("The provider failed. Try again later.")
  })

  test("never emits finish reason other", async () => {
    for (const name of ["parallel-delegates.sse", "failed-tool.sse", "terminal-failure.sse", "approvals.sse"]) {
      for (const part of await mapped(name)) {
        if (part.type === "finish") expect(part.finishReason.unified).toBe("stop")
      }
    }
  })

  test("names hosted calls as opencode renders them", async () => {
    const parts = await mapped("approvals.sse")
    const calls = parts.flatMap((part) => (part.type === "tool-call" ? [[part.toolName, JSON.parse(part.input)]] : []))
    expect(calls).toEqual([
      ["bash", { command: "echo hi" }],
      ["glob", { pattern: "hello.txt" }],
      ["write", { path: "hello.txt", content: "hello" }],
    ])
  })

  test("gives every open call an error result when the stream breaks", () => {
    const mapper = new TurnMapper("m")
    const call = (id: string) => ({
      type: "tool_call",
      payload: { call: { call_id: id, name: "bash", arguments: {} } },
    })
    mapper.map(call("a"))
    mapper.map(call("b"))
    mapper.map({ type: "tool_result", payload: { resolution: { call_id: "a", outcome: "completed", content: "ok" } } })
    mapper.map({ type: "output_delta", payload: { content: [{ type: "text", text: "x" }] } })

    const parts = mapper.fail(new Error("connection reset"))

    expect(parts.map((part) => part.type)).toEqual(["text-end", "tool-result", "error"])
    expect(parts[1]).toMatchObject({ toolCallId: "b", toolName: "bash", result: "connection reset", isError: true })
    expect(mapper.done).toBe(true)
    expect(mapper.fail(new Error("again"))).toEqual([])
  })

  test("shows a reasoning block that arrived only as reasoning_final", () => {
    const mapper = new TurnMapper("m")
    expect(mapper.map({ type: "reasoning_final", payload: { text: "Whole block." } })).toEqual([
      { type: "reasoning-start", id: "reasoning-0" },
      { type: "reasoning-delta", id: "reasoning-0", delta: "Whole block." },
      { type: "reasoning-end", id: "reasoning-0" },
    ])
    mapper.map({ type: "reasoning_delta", payload: { text: "Streamed." } })
    expect(mapper.map({ type: "reasoning_final", payload: { text: "Streamed." } })).toEqual([
      { type: "reasoning-end", id: "reasoning-1" },
    ])
  })

  test("treats cancelled and unknown results as errors", () => {
    const mapper = new TurnMapper("m")
    for (const [id, outcome] of [["c", "cancelled"], ["u", "unknown"]]) {
      mapper.map({ type: "tool_call", payload: { call: { call_id: id, name: "web_fetch", arguments: { url: "x" } } } })
      expect(mapper.map({ type: "tool_result", payload: { resolution: { call_id: id, outcome } } })).toEqual([
        {
          type: "tool-result",
          toolCallId: id,
          toolName: "webfetch",
          result: `The tool call ended ${outcome}.`,
          isError: true,
        },
      ])
    }
  })
})

describe("turnUsage", () => {
  const entry = {
    provider: "p",
    model: "m",
    tokens_in: 1000,
    tokens_out: 50,
    cache_read_tokens: 800,
    cache_write_tokens: 100,
  }

  test("without a context size keeps the counts and carries the cost in cache writes", () => {
    expect(turnUsage({ entries: [{ ...entry, cost: { USD: "0.25" } }] }, undefined)).toEqual({
      inputTokens: { total: 1000.25, noCache: 200, cacheRead: 800, cacheWrite: 0.25 },
      outputTokens: { total: 50, text: 50, reasoning: undefined },
    })
  })

  test("leaves the counts unknown when a model's counts are unknown", () => {
    const usage = turnUsage({ entries: [entry, { ...entry, tokens_in: null }] }, undefined)
    expect(usage.inputTokens.total).toBeUndefined()
    expect(usage.outputTokens.total).toBeUndefined()
  })
})

describe("toolDisplay", () => {
  test("maps Amplifier's arguments to the inputs opencode's renderers read", () => {
    const show = (name: string, args: Record<string, unknown>) => toolDisplay({ name, arguments: args })
    expect(show("read_file", { file_path: "a.ts", offset: 10, limit: 5 })).toEqual({
      name: "read",
      input: { path: "a.ts", offset: 10, limit: 5 },
    })
    expect(show("edit_file", { file_path: "a.ts", old_string: "x", new_string: "y" })).toEqual({
      name: "edit",
      input: { path: "a.ts", oldString: "x", newString: "y" },
    })
    expect(show("apply_patch", { patch: "*** Begin Patch" })).toEqual({
      name: "apply_patch",
      input: { patchText: "*** Begin Patch" },
    })
    expect(show("grep", { pattern: "TODO", path: "src", glob: "*.ts", output_mode: "content" })).toEqual({
      name: "grep",
      input: { pattern: "TODO", path: "src", include: "*.ts" },
    })
    expect(show("web_search", { query: "q" })).toEqual({ name: "websearch", input: { query: "q" } })
    expect(show("delegate", { agent: "explorer", instruction: "Find the config loader.\nThen report." })).toEqual({
      name: "task",
      input: {
        description: "Find the config loader.",
        prompt: "Find the config loader.\nThen report.",
        subagent_type: "explorer",
      },
    })
    expect(show("load_skill", { skill_name: "x" })).toEqual({ name: "load_skill", input: { skill_name: "x" } })
  })
})

describe("permissionAsk", () => {
  const ask = (name: string, args: Record<string, unknown>, source = "built-in") =>
    permissionAsk({ name, arguments: args, source })

  test("asks with the opencode action and resources of the effect", () => {
    expect(ask("bash", { command: "git status --short" })).toEqual({
      action: "shell",
      resources: ["git status --short"],
      save: ["git *"],
    })
    expect(ask("write_file", { file_path: "a.txt", content: "" })).toMatchObject({
      action: "edit",
      resources: ["a.txt"],
    })
    expect(ask("edit_file", { file_path: "b.txt" })).toMatchObject({ action: "edit", resources: ["b.txt"] })
    expect(
      ask("apply_patch", {
        patch: "*** Begin Patch\n*** Update File: a.ts\n*** Move to: c.ts\n*** Add File: d.ts\n*** End Patch",
      }),
    ).toMatchObject({ action: "edit", resources: ["a.ts", "c.ts", "d.ts"] })
    expect(ask("read_file", { file_path: ".env" })).toMatchObject({ action: "read", resources: [".env"] })
    expect(ask("glob", { pattern: "**/*.ts" })).toMatchObject({ action: "read", resources: ["**/*.ts"] })
    expect(ask("grep", { pattern: "x", path: "src" })).toMatchObject({ action: "read", resources: ["src"] })
    expect(ask("web_fetch", { url: "https://example.com" })).toMatchObject({
      action: "webfetch",
      resources: ["https://example.com"],
    })
    expect(ask("web_search", { query: "q" })).toMatchObject({ action: "webfetch", resources: ["q"] })
    expect(ask("github_search", { q: "x" }, "mcp")).toMatchObject({ action: "mcp", resources: ["github_search"] })
    expect(ask("delegate", { instruction: "x" })).toEqual({ action: "delegate", resources: ["*"], save: ["*"] })
  })
})
