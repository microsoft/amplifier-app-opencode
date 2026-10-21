import { readFileSync } from "node:fs"
import { homedir } from "node:os"
import path from "node:path"

export interface Options {
  readonly defaultModel: string | null
  readonly setDefaultAgent: boolean
  readonly approvals: "allow" | "deny"
  readonly environment: Readonly<Record<string, string>>
  readonly command: string
}

export const DEFAULT_OPTIONS: Options = {
  defaultModel: null,
  setDefaultAgent: true,
  approvals: "allow",
  environment: {},
  command: "amplifier-opencode",
}

export function configDirectory(): string {
  return path.join(process.env.XDG_CONFIG_HOME || path.join(homedir(), ".config"), "opencode")
}

export function stateDirectory(): string {
  return path.join(process.env.XDG_STATE_HOME || path.join(homedir(), ".local", "state"), "amplifier-opencode")
}

export function optionsPath(): string {
  return path.join(configDirectory(), "amplifier.json")
}

/** Options from `amplifier.json`, and a problem for each value that was ignored. */
export function parseOptions(raw: unknown): { options: Options; problems: string[] } {
  const problems: string[] = []
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    return { options: DEFAULT_OPTIONS, problems: ["amplifier.json is not a JSON object; using defaults"] }
  }
  const value = raw as Record<string, unknown>
  const pick = <T>(key: string, valid: (input: unknown) => input is T, fallback: T): T => {
    if (value[key] === undefined || value[key] === null) return fallback
    if (valid(value[key])) return value[key]
    problems.push(`ignored amplifier.json ${key}: ${JSON.stringify(value[key])}`)
    return fallback
  }
  const isString = (input: unknown): input is string => typeof input === "string" && input.length > 0
  const environment = pick(
    "environment",
    (input): input is Record<string, string> =>
      typeof input === "object" &&
      input !== null &&
      !Array.isArray(input) &&
      Object.values(input).every((item) => typeof item === "string"),
    DEFAULT_OPTIONS.environment,
  )
  return {
    options: {
      defaultModel: pick("defaultModel", isString, DEFAULT_OPTIONS.defaultModel as string | null),
      setDefaultAgent: pick(
        "setDefaultAgent",
        (input): input is boolean => typeof input === "boolean",
        DEFAULT_OPTIONS.setDefaultAgent,
      ),
      approvals: pick(
        "approvals",
        (input): input is "allow" | "deny" => input === "allow" || input === "deny",
        DEFAULT_OPTIONS.approvals,
      ),
      environment,
      command: pick("command", isString, DEFAULT_OPTIONS.command),
    },
    problems,
  }
}

export function readOptions(file = optionsPath()): { options: Options; problems: string[] } {
  let text: string
  try {
    text = readFileSync(file, "utf8")
  } catch {
    return { options: DEFAULT_OPTIONS, problems: [] }
  }
  try {
    return parseOptions(JSON.parse(text))
  } catch (error) {
    return { options: DEFAULT_OPTIONS, problems: [`${file} is not valid JSON (${String(error)}); using defaults`] }
  }
}
