import { describe, expect, test } from "bun:test"
import { connectionVariables, defaultModelVariables, faceEnvironment } from "../src/credentials.ts"

describe("connectionVariables", () => {
  test("maps API keys to the provider's variable", () => {
    expect(connectionVariables({ integrationID: "anthropic", credential: { type: "key", key: "a" } })).toEqual({
      ANTHROPIC_API_KEY: "a",
    })
    expect(connectionVariables({ integrationID: "openai", credential: { type: "key", key: "o" } })).toEqual({
      OPENAI_API_KEY: "o",
    })
    expect(connectionVariables({ integrationID: "google", credential: { type: "key", key: "g" } })).toEqual({
      GEMINI_API_KEY: "g",
    })
  })

  test("maps the GitHub Copilot sign-in to its token", () => {
    const credential = { type: "oauth", methodID: "device", access: "gho", refresh: "gho", expires: 0 } as const
    expect(connectionVariables({ integrationID: "github-copilot", credential })).toEqual({
      COPILOT_GITHUB_TOKEN: "gho",
    })
  })

  test("maps an Azure key with its resource to the key and endpoint", () => {
    const credential = { type: "key", key: "z", metadata: { resourceName: "my-models" } } as const
    expect(connectionVariables({ integrationID: "azure", credential })).toEqual({
      AZURE_OPENAI_API_KEY: "z",
      AZURE_OPENAI_ENDPOINT: "https://my-models.openai.azure.com",
    })
  })

  test("skips credentials Amplifier cannot use", () => {
    const skipped = [
      { integrationID: "openai", credential: { type: "oauth", methodID: "chatgpt-browser", access: "t" } },
      { integrationID: "anthropic", credential: { type: "oauth", methodID: "max", access: "t" } },
      { integrationID: "azure", credential: { type: "key", key: "z" } },
      { integrationID: "azure", credential: { type: "key", key: "z", metadata: { resourceName: "a.b/c" } } },
      { integrationID: "azure", credential: { type: "external", methodID: "azure-cli" } },
      { integrationID: "openai", credential: { type: "key", key: "" } },
      { integrationID: "openrouter", credential: { type: "key", key: "r" } },
    ] as const
    for (const connection of skipped) expect(connectionVariables(connection)).toEqual({})
  })
})

describe("faceEnvironment", () => {
  test("amplifier.json overrides opencode, which overrides the process environment", () => {
    const { environment, sources } = faceEnvironment(
      { PATH: "/bin", ANTHROPIC_API_KEY: "env-a", OPENAI_API_KEY: "env-o", GEMINI_API_KEY: "env-g", UNSET: undefined },
      [
        { integrationID: "openai", credential: { type: "key", key: "oc-o" } },
        { integrationID: "google", credential: { type: "key", key: "oc-g" } },
      ],
      { defaultModel: null, environment: { GEMINI_API_KEY: "json-g", OLLAMA_HOST: "http://ollama:11434" } },
    )
    expect(environment).toEqual({
      PATH: "/bin",
      ANTHROPIC_API_KEY: "env-a",
      OPENAI_API_KEY: "oc-o",
      GEMINI_API_KEY: "json-g",
      OLLAMA_HOST: "http://ollama:11434",
    })
    expect(sources).toEqual({
      ANTHROPIC_API_KEY: "environment",
      OPENAI_API_KEY: "opencode openai",
      GEMINI_API_KEY: "amplifier.json",
      OLLAMA_HOST: "amplifier.json",
    })
  })

  test("records only credential variables from the process environment", () => {
    const { sources } = faceEnvironment({ HOME: "/home/user", GH_TOKEN: "t", OPENAI_API_KEY: "" }, [], {
      defaultModel: null,
      environment: {},
    })
    expect(sources).toEqual({ GH_TOKEN: "environment" })
  })

  test("defaultModel selects the agent's provider and model above the process environment", () => {
    const { environment } = faceEnvironment(
      { AMPLIFIER_AGENT_PROVIDER: "anthropic", AMPLIFIER_AGENT_MODEL: "claude-sonnet-5-5" },
      [],
      { defaultModel: "openai/gpt-6.1-sol", environment: { AMPLIFIER_AGENT_MODEL: "gpt-6-luna" } },
    )
    expect(environment).toEqual({ AMPLIFIER_AGENT_PROVIDER: "openai", AMPLIFIER_AGENT_MODEL: "gpt-6-luna" })
  })
})

describe("defaultModelVariables", () => {
  test("splits a face model name at its first slash", () => {
    expect(defaultModelVariables("ollama/library/llama4:8b")).toEqual({
      AMPLIFIER_AGENT_PROVIDER: "ollama",
      AMPLIFIER_AGENT_MODEL: "library/llama4:8b",
    })
  })

  test("ignores a model key, the agent default, and no model", () => {
    for (const value of ["openai-gpt-6.1-sol", "default", "/gpt", "openai/", null]) {
      expect(defaultModelVariables(value)).toEqual({})
    }
  })
})
