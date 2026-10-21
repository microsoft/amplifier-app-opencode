import { readFileSync } from "node:fs"
import path from "node:path"

/** An MCP server as Amplifier's `AgentOptions.mcp_servers` declares it. */
export interface McpServer {
  readonly name: string
  readonly transport: "stdio" | "http"
  readonly command?: string
  readonly args?: string[]
  readonly env?: Record<string, string>
  readonly url?: string
  readonly headers?: Record<string, string>
}

/** What the face's agent gets from opencode besides credentials: skill directories and MCP servers. */
export interface FaceInputs {
  readonly skills: string[]
  readonly mcp_servers: McpServer[]
}

export const NO_INPUTS: FaceInputs = { skills: [], mcp_servers: [] }

/** An opencode MCP server config, as `ctx.mcp.transform` lists it. */
export type McpConfig =
  | {
      readonly type: "local"
      readonly command: readonly string[]
      readonly cwd?: string
      readonly environment?: Readonly<Record<string, string>>
      readonly disabled?: boolean
    }
  | {
      readonly type: "remote"
      readonly url: string
      readonly headers?: Readonly<Record<string, string>>
      readonly disabled?: boolean
    }

/** A skill opencode resolved: its file and, for `SKILL.md` files, the directory Amplifier loads. */
export interface SkillEntry {
  readonly path: string
}

const FRONTMATTER = /^\uFEFF?---\r?\n([\s\S]*?)\r?\n---/

/**
 * The directories of opencode's resolved skills that Amplifier can load: a `SKILL.md` whose frontmatter has a name and
 * description, in a directory that exists. Amplifier refuses duplicate names, so the first skill with a name wins.
 */
export function skillDirectories(skills: readonly SkillEntry[], read = readSkill): string[] {
  const names = new Set<string>()
  const directories: string[] = []
  for (const skill of skills) {
    if (path.basename(skill.path) !== "SKILL.md") continue
    const directory = path.dirname(skill.path)
    if (directories.includes(directory)) continue
    const content = read(skill.path)
    const header = content === undefined ? undefined : FRONTMATTER.exec(content)?.[1]
    if (!header) continue
    const name = /^name:\s*["']?([^"'\r\n]+?)["']?\s*$/m.exec(header)?.[1]
    if (!name || !/^description:\s*\S/m.test(header) || names.has(name)) continue
    names.add(name)
    directories.push(directory)
  }
  return directories
}

function readSkill(file: string): string | undefined {
  try {
    return readFileSync(file, "utf8")
  } catch {
    return undefined
  }
}

/**
 * Amplifier MCP servers for opencode's enabled MCP servers. A server opencode reports as failed or waiting for sign-in
 * is left out, because one server Amplifier cannot connect stops the face from starting.
 */
export function mcpServers(
  configs: readonly (readonly [string, McpConfig])[],
  statuses: ReadonlyMap<string, string>,
): { servers: McpServer[]; skipped: string[] } {
  const servers: McpServer[] = []
  const skipped: string[] = []
  for (const [name, config] of configs) {
    const status = statuses.get(name)
    if (config.disabled || status === undefined || status === "disabled") continue
    if (status === "failed" || status === "needs_auth") {
      skipped.push(`${name} (${status})`)
      continue
    }
    if (config.type === "local") {
      const [command, ...args] = config.command
      if (!command) {
        skipped.push(`${name} (no command)`)
        continue
      }
      servers.push({
        name,
        transport: "stdio",
        command,
        ...(args.length ? { args } : {}),
        ...(config.environment && Object.keys(config.environment).length ? { env: { ...config.environment } } : {}),
      })
    } else {
      servers.push({
        name,
        transport: "http",
        url: config.url,
        ...(config.headers && Object.keys(config.headers).length ? { headers: { ...config.headers } } : {}),
      })
    }
  }
  return { servers, skipped }
}
