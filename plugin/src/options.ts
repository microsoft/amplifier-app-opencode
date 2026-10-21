import { readFileSync } from "node:fs"
import { homedir } from "node:os"
import path from "node:path"

/**
 * Who decides Amplifier's effects: `opencode` asks opencode's permissions before each one, `allow` runs them all, and
 * `deny` refuses them all. `ask` is read as `opencode`.
 */
export type Approvals = "opencode" | "allow" | "deny"

export interface Options {
  readonly defaultModel: string | null
  readonly setDefaultAgent: boolean
  readonly approvals: Approvals
  readonly environment: Readonly<Record<string, string>>
  readonly command: string
}

export const DEFAULT_OPTIONS: Options = {
  defaultModel: null,
  setDefaultAgent: true,
  approvals: "opencode",
  environment: {},
  command: "amplifier-opencode",
}

/** The face's static approval policy for an `approvals` option; turns narrow `allow` by asking opencode. */
export function faceApprovals(approvals: Approvals): "allow" | "deny" {
  return approvals === "deny" ? "deny" : "allow"
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
  const approvals = pick(
    "approvals",
    (input): input is Approvals | "ask" => ["opencode", "ask", "allow", "deny"].includes(input as string),
    DEFAULT_OPTIONS.approvals as Approvals | "ask",
  )
  return {
    options: {
      defaultModel: pick("defaultModel", isString, DEFAULT_OPTIONS.defaultModel as string | null),
      setDefaultAgent: pick(
        "setDefaultAgent",
        (input): input is boolean => typeof input === "boolean",
        DEFAULT_OPTIONS.setDefaultAgent,
      ),
      approvals: approvals === "ask" ? "opencode" : approvals,
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
