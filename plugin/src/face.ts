import { type ChildProcess, execFile, spawn } from "node:child_process"
import { createHash, randomBytes } from "node:crypto"
import { appendFileSync, closeSync, mkdirSync, openSync } from "node:fs"
import { createServer } from "node:net"
import path from "node:path"
import type { FaceModel } from "./mapping.ts"
import { stateDirectory } from "./options.ts"

const TOKEN_ENV = "AMPLIFIER_OPENCODE_FACE_TOKEN"
const READY_TIMEOUT_MS = 180_000

export function log(message: string): void {
  try {
    mkdirSync(stateDirectory(), { recursive: true })
    appendFileSync(path.join(stateDirectory(), "plugin.log"), `${new Date().toISOString()} ${message}\n`)
  } catch {
    // Logging never stops the plugin.
  }
}

/** The face log for a working directory: `<name>-<hash>.log` under the state directory. */
export function faceLogPath(directory: string): string {
  const hash = createHash("sha256").update(directory).digest("hex").slice(0, 8)
  return path.join(stateDirectory(), `face-${path.basename(directory) || "root"}-${hash}.log`)
}

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer()
    server.once("error", reject)
    server.listen(0, "127.0.0.1", () => {
      const address = server.address()
      server.close(() => (typeof address === "object" && address ? resolve(address.port) : reject(new Error("no port"))))
    })
  })
}

/** The version `<command> --version` reports, or undefined when the command does not run. */
export function commandVersion(command: string, environment: NodeJS.ProcessEnv): Promise<string | undefined> {
  return new Promise((resolve) => {
    execFile(command, ["--version"], { env: environment, timeout: 30_000 }, (error, stdout) => {
      resolve(error ? undefined : /(\d+\.\d+\.\d+\S*)/.exec(stdout)?.[1])
    })
  })
}

export interface Face {
  readonly baseURL: string
  readonly token: string
  readonly models: readonly FaceModel[]
  stop(): void
}

export interface FaceSettings {
  readonly command: string
  readonly directory: string
  readonly environment: NodeJS.ProcessEnv
  /** The face's static approval policy. */
  readonly approvals: "allow" | "deny"
}

/** Start `<command> face` in `directory` and wait until it lists its models. */
export async function startFace(settings: FaceSettings): Promise<Face> {
  const port = await freePort()
  const token = randomBytes(32).toString("hex")
  const logFile = faceLogPath(settings.directory)
  mkdirSync(path.dirname(logFile), { recursive: true })
  const output = openSync(logFile, "a")
  appendFileSync(logFile, `\n${new Date().toISOString()} starting face on port ${port} in ${settings.directory}\n`)
  let child: ChildProcess
  try {
    child = spawn(
      settings.command,
      ["face", "--port", String(port), "--token-env", TOKEN_ENV, "--approvals", settings.approvals],
      { cwd: settings.directory, env: { ...settings.environment, [TOKEN_ENV]: token }, stdio: ["ignore", output, output] },
    )
  } finally {
    closeSync(output)
  }
  let exited: string | undefined
  child.once("error", (error) => (exited = `could not run ${settings.command}: ${error.message}`))
  child.once("exit", (code, signal) => (exited ??= `exited with ${signal ?? `code ${code}`}`))
  const stop = () => {
    if (child.exitCode === null && child.signalCode === null) child.kill("SIGTERM")
  }
  process.once("exit", stop)

  const baseURL = `http://127.0.0.1:${port}/v1`
  const deadline = Date.now() + READY_TIMEOUT_MS
  while (Date.now() < deadline) {
    if (exited) break
    try {
      const response = await fetch(`${baseURL}/models`, { headers: { Authorization: `Bearer ${token}` } })
      if (response.ok) {
        const body = (await response.json()) as { data: FaceModel[] }
        log(`face ready on port ${port} for ${settings.directory} (${body.data.length} models)`)
        return {
          baseURL,
          token,
          models: body.data,
          stop: () => {
            process.removeListener("exit", stop)
            stop()
          },
        }
      }
      exited = `answered /v1/models with HTTP ${response.status}`
      break
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 250))
    }
  }
  stop()
  process.removeListener("exit", stop)
  throw new Error(
    `The Amplifier face for ${settings.directory} did not start (${exited ?? `no answer in ${READY_TIMEOUT_MS / 1000} s`}). ` +
      `See ${logFile}, and run: amplifier-opencode doctor`,
  )
}
