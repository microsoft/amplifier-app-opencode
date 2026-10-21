/**
 * Amplifier tool calls that opencode runs through its own tools, so their results carry the metadata opencode's
 * renderers read. The language model registers each call; opencode's wrapped tool waits here for Amplifier's result.
 */

/** opencode tools a proxied Amplifier call runs through, by Amplifier tool name. */
export const PROXIED: Readonly<Record<string, string>> = {
  edit_file: "edit",
  apply_patch: "patch",
  glob: "glob",
  grep: "grep",
}

/** The opencode tools the plugin wraps. */
export const PROXY_TOOLS = [...new Set(Object.values(PROXIED))]

/** What a wrapped tool returns for a proxied call. */
export interface ProxyResult {
  readonly output: unknown
  readonly content: string
  readonly metadata: Record<string, unknown>
}

interface Pending {
  readonly sessionID: string | undefined
  readonly promise: Promise<ProxyResult>
  resolve(result: ProxyResult): void
  reject(error: Error): void
  settled: boolean
}

const pending = new Map<string, Pending>()

/** Register a call opencode will run through a wrapped tool. */
export function register(callID: string, sessionID: string | undefined): void {
  if (pending.has(callID)) return
  let resolve!: (result: ProxyResult) => void
  let reject!: (error: Error) => void
  const promise = new Promise<ProxyResult>((ok, fail) => {
    resolve = ok
    reject = fail
  })
  // A call whose wrapper never runs must not report an unhandled rejection.
  promise.catch(() => {})
  pending.set(callID, { sessionID, promise, resolve, reject, settled: false })
}

/** Whether the language model registered this call. */
export function registered(callID: string): boolean {
  return pending.has(callID)
}

/** Amplifier's result for a registered call. */
export function resolve(callID: string, result: ProxyResult): void {
  const entry = pending.get(callID)
  if (!entry || entry.settled) return
  entry.settled = true
  entry.resolve(result)
}

/** Amplifier's failure for a registered call; its message is the tool's error. */
export function reject(callID: string, error: Error): void {
  const entry = pending.get(callID)
  if (!entry || entry.settled) return
  entry.settled = true
  entry.reject(error)
}

/** Fail every unsettled call of a session, or every one when no session is named. */
export function rejectAll(error: Error, sessionID?: string): void {
  for (const [callID, entry] of pending) {
    if (sessionID === undefined || entry.sessionID === sessionID) reject(callID, error)
  }
}

/**
 * The result of a registered call once Amplifier reports it. The call is forgotten after it settles, and an aborted
 * wrapper stops waiting.
 */
export async function wait(callID: string, signal?: AbortSignal): Promise<ProxyResult> {
  const entry = pending.get(callID)
  if (!entry) throw new Error(`No Amplifier tool call ${callID} is waiting.`)
  try {
    if (!signal) return await entry.promise
    if (signal.aborted) throw signal.reason ?? new Error("The tool call was interrupted.")
    return await new Promise<ProxyResult>((ok, fail) => {
      const abort = () => fail(signal.reason ?? new Error("The tool call was interrupted."))
      signal.addEventListener("abort", abort, { once: true })
      entry.promise.then(ok, fail).finally(() => signal.removeEventListener("abort", abort))
    })
  } finally {
    pending.delete(callID)
  }
}

/** Forget the settled calls of a session whose wrapper never ran, as when opencode refused the call's input. */
export function forget(sessionID: string): void {
  for (const [callID, entry] of pending) if (entry.sessionID === sessionID && entry.settled) pending.delete(callID)
}

/** The number of calls still registered; for tests. */
export function size(): number {
  return pending.size
}

// ---------------------------------------------------------------------------------------------------------------------
// Results

/** One entry of a tool result's `com.microsoft.amplifier.file_changes`. */
export interface FileChange {
  readonly path: string
  readonly change: "added" | "modified" | "deleted"
  readonly patch: string
  readonly additions: number
  readonly deletions: number
}

export const FILE_CHANGES = "com.microsoft.amplifier.file_changes"

function count(value: unknown): number | undefined {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : undefined
}

/** The well-formed file changes of a `tool_result` payload. */
export function fileChanges(payload: Record<string, unknown>): FileChange[] {
  const raw = payload[FILE_CHANGES]
  if (!Array.isArray(raw)) return []
  return raw.flatMap((item): FileChange[] => {
    const value = (typeof item === "object" && item !== null ? item : {}) as Record<string, unknown>
    const change = value.change
    const additions = count(value.additions)
    const deletions = count(value.deletions)
    if (
      typeof value.path !== "string" ||
      typeof value.patch !== "string" ||
      (change !== "added" && change !== "modified" && change !== "deleted") ||
      additions === undefined ||
      deletions === undefined
    )
      return []
    return [{ path: value.path, change, patch: value.patch, additions, deletions }]
  })
}

/** opencode's `FileDiff.Info` for a file change, the shape its edit and patch tools report. */
function fileDiff(change: FileChange) {
  return {
    file: change.path,
    patch: change.patch,
    additions: change.additions,
    deletions: change.deletions,
    status: change.change,
  }
}

const PATCH_TYPES = { added: "add", modified: "update", deleted: "delete" } as const

/** A search result's JSON object, as Amplifier's glob and grep return it. */
function searchResult(content: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(content)
    return typeof parsed === "object" && parsed !== null && !Array.isArray(parsed) ? parsed : {}
  } catch {
    return {}
  }
}

/** The number of non-empty lines of a result that is not JSON. */
function lines(content: string): number {
  return content.split("\n").filter((line) => line.trim() !== "").length
}

/** The number of matches a grep result shows: the matches it lists, or the lines of a plain result. */
function grepMatches(content: string): number {
  const result = searchResult(content)
  return count(result.matches_count) ?? count(result.total_matches) ?? lines(content)
}

/** The number of files a glob result lists. */
function globCount(content: string): number {
  const result = searchResult(content)
  return count(result.count) ?? count(result.total_files) ?? lines(content)
}

/**
 * What opencode's wrapped tool returns for an Amplifier result: Amplifier's content for the model, an output its
 * schema accepts, and the metadata its renderer reads.
 */
export function proxyResult(tool: string, content: string, changes: readonly FileChange[]): ProxyResult {
  const files = changes.map(fileDiff)
  switch (tool) {
    case "edit":
      return { output: { files, replacements: files.length }, content, metadata: { files } }
    case "patch": {
      const applied = changes.map((change) => ({
        type: PATCH_TYPES[change.change],
        resource: change.path,
        target: change.path,
      }))
      return { output: { applied, files }, content, metadata: { files } }
    }
    case "glob":
      return { output: [], content, metadata: { count: globCount(content), truncated: false } }
    case "grep":
      return { output: [], content, metadata: { matches: grepMatches(content), truncated: false } }
    default:
      return { output: undefined, content, metadata: {} }
  }
}
