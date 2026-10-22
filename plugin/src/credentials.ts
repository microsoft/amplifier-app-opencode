/** An opencode credential value, as `ctx.integration.connection.resolve` returns it. */
export type Credential =
  | { readonly type: "key"; readonly key: string; readonly metadata?: Readonly<Record<string, unknown>> }
  | { readonly type: "oauth"; readonly methodID: string; readonly access: string }
  | { readonly type: "external"; readonly methodID: string }

/** The active credential of one opencode integration. */
export interface Connection {
  readonly integrationID: string
  readonly credential: Credential
}

/** The opencode integrations whose credentials Amplifier can use. */
export const INTEGRATIONS = ["anthropic", "openai", "google", "github-copilot", "azure"] as const

/** The variables Amplifier reads provider credentials and endpoints from. */
export const CREDENTIAL_VARIABLES = [
  "ANTHROPIC_API_KEY",
  "OPENAI_API_KEY",
  "AZURE_OPENAI_API_KEY",
  "AZURE_OPENAI_ENDPOINT",
  "GOOGLE_API_KEY",
  "GEMINI_API_KEY",
  "COPILOT_AGENT_TOKEN",
  "COPILOT_GITHUB_TOKEN",
  "GH_TOKEN",
  "GITHUB_TOKEN",
  "OLLAMA_API_KEY",
  "VLLM_API_KEY",
  "CHAT_COMPLETIONS_API_KEY",
] as const

const KEY_VARIABLES: Readonly<Record<string, string>> = {
  anthropic: "ANTHROPIC_API_KEY",
  openai: "OPENAI_API_KEY",
  google: "GEMINI_API_KEY",
}

// Azure allows only letters, digits, and hyphens in a resource name, which becomes a hostname label.
const AZURE_RESOURCE = /^[a-zA-Z0-9][a-zA-Z0-9-]*$/

/** The Amplifier variables an opencode credential provides; empty when Amplifier cannot use it. */
export function connectionVariables(connection: Connection): Record<string, string> {
  const { integrationID, credential } = connection
  if (credential.type === "key" && credential.key) {
    const variable = KEY_VARIABLES[integrationID]
    if (variable) return { [variable]: credential.key }
    const resource = credential.metadata?.resourceName
    if (integrationID === "azure" && typeof resource === "string" && AZURE_RESOURCE.test(resource)) {
      return { AZURE_OPENAI_API_KEY: credential.key, AZURE_OPENAI_ENDPOINT: `https://${resource}.openai.azure.com` }
    }
  }
  if (credential.type === "oauth" && integrationID === "github-copilot" && credential.access) {
    return { COPILOT_GITHUB_TOKEN: credential.access }
  }
  return {}
}

export interface FaceEnvironment {
  readonly environment: Record<string, string>
  /** Where each credential or amplifier.json variable came from: `environment`, `opencode <integration>`, or
   * `amplifier.json`. */
  readonly sources: Readonly<Record<string, string>>
}

/** The Amplifier variables selecting the agent's provider and model for a `defaultModel` of `<provider>/<model>`. */
export function defaultModelVariables(defaultModel: string | null): Record<string, string> {
  const separator = defaultModel?.indexOf("/") ?? -1
  if (!defaultModel || separator < 1 || separator === defaultModel.length - 1) return {}
  return {
    AMPLIFIER_AGENT_PROVIDER: defaultModel.slice(0, separator),
    AMPLIFIER_AGENT_MODEL: defaultModel.slice(separator + 1),
  }
}

/**
 * The face's environment: the process environment, overridden by opencode's connections, overridden by amplifier.json:
 * the provider and model its `defaultModel` names, then its `environment`.
 */
export function faceEnvironment(
  processEnvironment: Readonly<Record<string, string | undefined>>,
  connections: readonly Connection[],
  options: { readonly defaultModel: string | null; readonly environment: Readonly<Record<string, string>> },
): FaceEnvironment {
  const environment: Record<string, string> = {}
  const sources: Record<string, string> = {}
  for (const [name, value] of Object.entries(processEnvironment)) {
    if (value === undefined) continue
    environment[name] = value
    if ((CREDENTIAL_VARIABLES as readonly string[]).includes(name) && value) sources[name] = "environment"
  }
  for (const connection of connections) {
    for (const [name, value] of Object.entries(connectionVariables(connection))) {
      environment[name] = value
      sources[name] = `opencode ${connection.integrationID}`
    }
  }
  for (const [name, value] of Object.entries({ ...defaultModelVariables(options.defaultModel), ...options.environment })) {
    environment[name] = value
    sources[name] = "amplifier.json"
  }
  return { environment, sources }
}
