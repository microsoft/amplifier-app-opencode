import { describe, expect, test } from "bun:test"
import { defaultModelKey, modelEntries, sessionHeader, titleFrom } from "../src/mapping.ts"

describe("sessionHeader", () => {
  test("is a valid Amplifier session id derived from the opencode session id", () => {
    const header = sessionHeader("ses_ea11ba342ffepFuD8XG0gCJXEX")
    expect(header).toMatch(/^[a-z0-9][a-z0-9-]{7,63}$/)
    expect(header).toHaveLength(43)
    expect(header).toBe(sessionHeader("ses_ea11ba342ffepFuD8XG0gCJXEX"))
    expect(header).not.toBe(sessionHeader("ses_other"))
  })
})

describe("modelEntries", () => {
  const entries = modelEntries([
    { id: "amplifier" },
    {
      id: "anthropic/claude-sonnet-5-5",
      display_name: "Claude Sonnet 5.5",
      context_window: 200000,
      max_output_tokens: 128000,
      reasoning_efforts: ["low", "high"],
    },
    { id: "ollama/llama4:8b" },
  ])

  test("keys the agent default and replaces slashes", () => {
    expect(entries.map((entry) => [entry.key, entry.modelID, entry.name])).toEqual([
      ["default", "amplifier", "Amplifier default"],
      ["anthropic-claude-sonnet-5-5", "anthropic/claude-sonnet-5-5", "Claude Sonnet 5.5"],
      ["ollama-llama4:8b", "ollama/llama4:8b", "ollama/llama4:8b"],
    ])
    expect(entries[1]?.efforts).toEqual(["low", "high"])
    expect(entries[2]?.efforts).toEqual([])
  })

  test("defaultModelKey accepts the face name or the key, else default", () => {
    expect(defaultModelKey(entries, "anthropic/claude-sonnet-5-5")).toBe("anthropic-claude-sonnet-5-5")
    expect(defaultModelKey(entries, "anthropic-claude-sonnet-5-5")).toBe("anthropic-claude-sonnet-5-5")
    expect(defaultModelKey(entries, "openai/gpt-6-astra")).toBe("default")
    expect(defaultModelKey(entries, null)).toBe("default")
  })
})

test("titleFrom uses the first nonempty line, at most 60 characters", () => {
  expect(titleFrom("\n  Fix the parser  \nmore")).toBe("Fix the parser")
  expect(titleFrom("x".repeat(80))).toBe(`${"x".repeat(57)}...`)
  expect(titleFrom("  \n ")).toBeUndefined()
})
