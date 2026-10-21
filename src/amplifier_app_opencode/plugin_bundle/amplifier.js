// @bun
// amplifier-opencode-plugin 0.23.1

// src/index.ts
import { createHash as createHash3 } from "crypto";

// src/credentials.ts
var INTEGRATIONS = ["anthropic", "openai", "google", "github-copilot", "azure"];
var CREDENTIAL_VARIABLES = [
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
  "CHAT_COMPLETIONS_API_KEY"
];
var KEY_VARIABLES = {
  anthropic: "ANTHROPIC_API_KEY",
  openai: "OPENAI_API_KEY",
  google: "GEMINI_API_KEY"
};
var AZURE_RESOURCE = /^[a-zA-Z0-9][a-zA-Z0-9-]*$/;
function connectionVariables(connection) {
  const { integrationID, credential } = connection;
  if (credential.type === "key" && credential.key) {
    const variable = KEY_VARIABLES[integrationID];
    if (variable)
      return { [variable]: credential.key };
    const resource = credential.metadata?.resourceName;
    if (integrationID === "azure" && typeof resource === "string" && AZURE_RESOURCE.test(resource)) {
      return { AZURE_OPENAI_API_KEY: credential.key, AZURE_OPENAI_ENDPOINT: `https://${resource}.openai.azure.com` };
    }
  }
  if (credential.type === "oauth" && integrationID === "github-copilot" && credential.access) {
    return { COPILOT_GITHUB_TOKEN: credential.access };
  }
  return {};
}
function defaultModelVariables(defaultModel) {
  const separator = defaultModel?.indexOf("/") ?? -1;
  if (!defaultModel || separator < 1 || separator === defaultModel.length - 1)
    return {};
  return {
    AMPLIFIER_AGENT_PROVIDER: defaultModel.slice(0, separator),
    AMPLIFIER_AGENT_MODEL: defaultModel.slice(separator + 1)
  };
}
function faceEnvironment(processEnvironment, connections, options) {
  const environment = {};
  const sources = {};
  for (const [name, value] of Object.entries(processEnvironment)) {
    if (value === undefined)
      continue;
    environment[name] = value;
    if (CREDENTIAL_VARIABLES.includes(name) && value)
      sources[name] = "environment";
  }
  for (const connection of connections) {
    for (const [name, value] of Object.entries(connectionVariables(connection))) {
      environment[name] = value;
      sources[name] = `opencode ${connection.integrationID}`;
    }
  }
  for (const [name, value] of Object.entries({ ...defaultModelVariables(options.defaultModel), ...options.environment })) {
    environment[name] = value;
    sources[name] = "amplifier.json";
  }
  return { environment, sources };
}

// src/face.ts
import { execFile, spawn } from "child_process";
import { createHash, randomBytes } from "crypto";
import { appendFileSync, closeSync, mkdirSync, openSync } from "fs";
import { createServer } from "net";
import path2 from "path";

// src/options.ts
import { readFileSync } from "fs";
import { homedir } from "os";
import path from "path";
var DEFAULT_OPTIONS = {
  defaultModel: null,
  setDefaultAgent: true,
  approvals: "opencode",
  environment: {},
  command: "amplifier-opencode"
};
function faceApprovals(approvals) {
  return approvals === "deny" ? "deny" : "allow";
}
function configDirectory() {
  return path.join(process.env.XDG_CONFIG_HOME || path.join(homedir(), ".config"), "opencode");
}
function stateDirectory() {
  return path.join(process.env.XDG_STATE_HOME || path.join(homedir(), ".local", "state"), "amplifier-opencode");
}
function optionsPath() {
  return path.join(configDirectory(), "amplifier.json");
}
function parseOptions(raw) {
  const problems = [];
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    return { options: DEFAULT_OPTIONS, problems: ["amplifier.json is not a JSON object; using defaults"] };
  }
  const value = raw;
  const pick = (key, valid, fallback) => {
    if (value[key] === undefined || value[key] === null)
      return fallback;
    if (valid(value[key]))
      return value[key];
    problems.push(`ignored amplifier.json ${key}: ${JSON.stringify(value[key])}`);
    return fallback;
  };
  const isString = (input) => typeof input === "string" && input.length > 0;
  const environment = pick("environment", (input) => typeof input === "object" && input !== null && !Array.isArray(input) && Object.values(input).every((item) => typeof item === "string"), DEFAULT_OPTIONS.environment);
  const approvals = pick("approvals", (input) => ["opencode", "ask", "allow", "deny"].includes(input), DEFAULT_OPTIONS.approvals);
  return {
    options: {
      defaultModel: pick("defaultModel", isString, DEFAULT_OPTIONS.defaultModel),
      setDefaultAgent: pick("setDefaultAgent", (input) => typeof input === "boolean", DEFAULT_OPTIONS.setDefaultAgent),
      approvals: approvals === "ask" ? "opencode" : approvals,
      environment,
      command: pick("command", isString, DEFAULT_OPTIONS.command)
    },
    problems
  };
}
function readOptions(file = optionsPath()) {
  let text;
  try {
    text = readFileSync(file, "utf8");
  } catch {
    return { options: DEFAULT_OPTIONS, problems: [] };
  }
  try {
    return parseOptions(JSON.parse(text));
  } catch (error) {
    return { options: DEFAULT_OPTIONS, problems: [`${file} is not valid JSON (${String(error)}); using defaults`] };
  }
}

// src/face.ts
var TOKEN_ENV = "AMPLIFIER_OPENCODE_FACE_TOKEN";
var READY_TIMEOUT_MS = 180000;
function log(message) {
  try {
    mkdirSync(stateDirectory(), { recursive: true });
    appendFileSync(path2.join(stateDirectory(), "plugin.log"), `${new Date().toISOString()} ${message}
`);
  } catch {}
}
function faceLogPath(directory) {
  const hash = createHash("sha256").update(directory).digest("hex").slice(0, 8);
  return path2.join(stateDirectory(), `face-${path2.basename(directory) || "root"}-${hash}.log`);
}
function freePort() {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => typeof address === "object" && address ? resolve(address.port) : reject(new Error("no port")));
    });
  });
}
function commandVersion(command, environment) {
  return new Promise((resolve) => {
    execFile(command, ["--version"], { env: environment, timeout: 30000 }, (error, stdout) => {
      resolve(error ? undefined : /(\d+\.\d+\.\d+\S*)/.exec(stdout)?.[1]);
    });
  });
}
async function startFace(settings) {
  const port = await freePort();
  const token = randomBytes(32).toString("hex");
  const logFile = faceLogPath(settings.directory);
  mkdirSync(path2.dirname(logFile), { recursive: true });
  const output = openSync(logFile, "a");
  appendFileSync(logFile, `
${new Date().toISOString()} starting face on port ${port} in ${settings.directory}
`);
  let child;
  try {
    child = spawn(settings.command, ["face", "--port", String(port), "--token-env", TOKEN_ENV, "--approvals", settings.approvals], { cwd: settings.directory, env: { ...settings.environment, [TOKEN_ENV]: token }, stdio: ["ignore", output, output] });
  } finally {
    closeSync(output);
  }
  let exited;
  child.once("error", (error) => exited = `could not run ${settings.command}: ${error.message}`);
  child.once("exit", (code, signal) => exited ??= `exited with ${signal ?? `code ${code}`}`);
  const stop = () => {
    if (child.exitCode === null && child.signalCode === null)
      child.kill("SIGTERM");
  };
  process.once("exit", stop);
  const baseURL = `http://127.0.0.1:${port}/v1`;
  const deadline = Date.now() + READY_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (exited)
      break;
    try {
      const response = await fetch(`${baseURL}/models`, { headers: { Authorization: `Bearer ${token}` } });
      if (response.ok) {
        const body = await response.json();
        log(`face ready on port ${port} for ${settings.directory} (${body.data.length} models)`);
        return {
          baseURL,
          token,
          models: body.data,
          stop: () => {
            process.removeListener("exit", stop);
            stop();
          }
        };
      }
      exited = `answered /v1/models with HTTP ${response.status}`;
      break;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }
  stop();
  process.removeListener("exit", stop);
  throw new Error(`The Amplifier face for ${settings.directory} did not start (${exited ?? `no answer in ${READY_TIMEOUT_MS / 1000} s`}). ` + `See ${logFile}, and run: amplifier-opencode doctor`);
}

// src/mapping.ts
import { createHash as createHash2 } from "crypto";
var DEFAULT_MODEL_KEY = "default";
function sessionHeader(sessionID) {
  return `oc-${createHash2("sha256").update(sessionID).digest("hex").slice(0, 40)}`;
}
function modelKey(id) {
  return id.replaceAll("/", "-");
}
function modelEntries(models) {
  return models.map((model) => !model.id.includes("/") ? { key: DEFAULT_MODEL_KEY, modelID: model.id, name: "Amplifier default", efforts: [] } : {
    key: modelKey(model.id),
    modelID: model.id,
    name: model.display_name ?? model.id,
    contextWindow: model.context_window,
    efforts: model.reasoning_efforts ?? []
  });
}
function defaultModelKey(entries, defaultModel) {
  if (!defaultModel)
    return DEFAULT_MODEL_KEY;
  const match = entries.find((entry) => entry.modelID === defaultModel || entry.key === defaultModel);
  return match?.key ?? DEFAULT_MODEL_KEY;
}
function titleFrom(text) {
  const line = text.split(`
`).map((part) => part.trim()).find((part) => part.length > 0);
  if (!line)
    return;
  return line.length <= 60 ? line : `${line.slice(0, 57).trimEnd()}...`;
}

// src/usage.ts
var CONTEXT_FIELD = "com.microsoft.amplifier.context";
var CARRIER_COST = [{ input: 0, output: 0, cache: { read: 0, write: 1e6 } }];
function count(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : undefined;
}
function record(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value : undefined;
}
function costUSD(value) {
  if (typeof value === "string" && value.trim() !== "")
    return count(Number(value));
  return count(value);
}
function recordedUsage(usage) {
  const write = costUSD(usage.cost_usd) ?? 0;
  const details = record(usage.prompt_tokens_details) ?? {};
  const tokens = count(record(usage[CONTEXT_FIELD])?.tokens);
  if (tokens !== undefined) {
    const { completion_tokens_details: _, ...rest } = usage;
    return {
      ...rest,
      prompt_tokens: tokens,
      completion_tokens: 0,
      total_tokens: tokens,
      prompt_tokens_details: { ...details, cached_tokens: 0, cache_write_tokens: write }
    };
  }
  const prompt = count(usage.prompt_tokens);
  const completion = count(usage.completion_tokens);
  if (prompt === undefined || completion === undefined)
    return usage;
  const input = prompt - (count(details.cache_write_tokens) ?? 0) + write;
  return {
    ...usage,
    prompt_tokens: input,
    total_tokens: input + completion,
    prompt_tokens_details: { ...details, cache_write_tokens: write }
  };
}

// src/turns.ts
function record2(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value : {};
}
function text(value) {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}
function defined(value) {
  return Object.fromEntries(Object.entries(value).filter(([, item]) => item !== undefined));
}
var SYSTEM_UPDATE = /^<system-update>\n([\s\S]*)\n<\/system-update>$/;
function dataURL(data, mediaType) {
  if (data instanceof URL)
    return data.protocol === "data:" ? data.href : undefined;
  if (data instanceof Uint8Array)
    return `data:${mediaType};base64,${Buffer.from(data).toString("base64")}`;
  if (typeof data !== "string")
    return;
  if (data.startsWith("data:"))
    return data;
  if (/^[a-z]+:\/\//i.test(data))
    return;
  return `data:${mediaType};base64,${data}`;
}
function promptMessages(prompt) {
  const messages = [];
  let leading = true;
  for (const message of prompt) {
    if (message.role === "system") {
      if (!leading)
        messages.push({ role: "system", content: message.content });
      continue;
    }
    leading = false;
    if (message.role === "tool")
      continue;
    if (message.role === "user") {
      const only = message.content.length === 1 ? message.content[0] : undefined;
      const update = only?.type === "text" ? SYSTEM_UPDATE.exec(only.text) : null;
      if (update) {
        messages.push({ role: "system", content: update[1] });
        continue;
      }
      const parts = [];
      for (const part of message.content) {
        if (part.type === "text")
          parts.push({ type: "text", text: part.text });
        else if (part.type === "file" && part.mediaType.startsWith("image/")) {
          const url = dataURL(part.data, part.mediaType === "image/*" ? "image/png" : part.mediaType);
          if (url)
            parts.push({ type: "image_url", image_url: { url } });
        }
      }
      messages.push({ role: "user", content: parts });
      continue;
    }
    let content = "";
    let reasoning = "";
    for (const part of message.content) {
      if (part.type === "text")
        content += part.text;
      else if (part.type === "reasoning")
        reasoning += part.text;
    }
    messages.push(reasoning ? { role: "assistant", content, reasoning_content: reasoning } : { role: "assistant", content });
  }
  return messages;
}
function envelopeOf(event) {
  const data = event.split(/\r\n|\r|\n/).filter((line) => line.startsWith("data:")).map((line) => line.slice(line.startsWith("data: ") ? 6 : 5));
  if (data.length === 0)
    return;
  try {
    const parsed = JSON.parse(data.join(`
`));
    return typeof parsed?.type === "string" ? { ...parsed, payload: record2(parsed.payload) } : undefined;
  } catch {
    return;
  }
}
var BLANK_LINE = /(?:\r\n|\r(?!\n)|\n)(?:\r\n|\r(?!\n)|\n)/;
function envelopes() {
  const decoder = new TextDecoder;
  let pending = "";
  const drain = (controller) => {
    for (let match = BLANK_LINE.exec(pending);match; match = BLANK_LINE.exec(pending)) {
      const envelope = envelopeOf(pending.slice(0, match.index));
      pending = pending.slice(match.index + match[0].length);
      if (envelope)
        controller.enqueue(envelope);
    }
  };
  return new TransformStream({
    transform(chunk, controller) {
      pending += decoder.decode(chunk, { stream: true });
      drain(controller);
    },
    flush(controller) {
      pending += decoder.decode();
      drain(controller);
      const envelope = envelopeOf(pending);
      if (envelope)
        controller.enqueue(envelope);
      pending = "";
    }
  });
}
function toolDisplay(call) {
  const args = record2(call.arguments);
  switch (call.name) {
    case "bash":
      return { name: "bash", input: defined({ command: args.command, description: args.description }) };
    case "read_file":
      return { name: "read", input: defined({ path: args.file_path, offset: args.offset, limit: args.limit }) };
    case "write_file":
      return { name: "write", input: defined({ path: args.file_path, content: args.content }) };
    case "edit_file":
      return {
        name: "edit",
        input: defined({
          path: args.file_path,
          oldString: args.old_string,
          newString: args.new_string,
          replaceAll: args.replace_all
        })
      };
    case "apply_patch":
      return { name: "apply_patch", input: defined({ patchText: args.patch ?? args.diff, path: args.path }) };
    case "glob":
      return { name: "glob", input: defined({ pattern: args.pattern, path: args.path }) };
    case "grep":
      return { name: "grep", input: defined({ pattern: args.pattern, path: args.path, include: args.glob }) };
    case "web_fetch":
      return { name: "webfetch", input: defined({ url: args.url }) };
    case "web_search":
      return { name: "websearch", input: defined({ query: args.query }) };
    case "delegate": {
      const prompt = text(args.instruction) ?? "";
      const agent = text(args.agent);
      return {
        name: "task",
        input: defined({ description: titleFrom(prompt) ?? agent ?? "delegate", prompt, subagent_type: agent })
      };
    }
    default:
      return { name: call.name, input: args };
  }
}
function patchPaths(args) {
  const patch = text(args.patch);
  if (!patch)
    return text(args.path) ? [args.path] : [];
  const paths = [...patch.matchAll(/^\*\*\* (?:Add|Update|Delete) File: (.+)$|^\*\*\* Move to: (.+)$/gm)];
  return [...new Set(paths.map((match) => (match[1] ?? match[2]).trim()))];
}
function permissionAsk(call) {
  const args = record2(call.arguments);
  const one = (value, save = ["*"]) => {
    const resource = text(value) ?? "*";
    return { resources: [resource], save };
  };
  if (call.source === "mcp")
    return { action: "mcp", ...one(call.name) };
  switch (call.name) {
    case "bash": {
      const command = text(args.command);
      const head = command?.trim().split(/\s+/)[0];
      return { action: "shell", ...one(command, head ? [`${head} *`] : []) };
    }
    case "write_file":
    case "edit_file":
      return { action: "edit", ...one(args.file_path) };
    case "apply_patch": {
      const paths = patchPaths(args);
      return { action: "edit", resources: paths.length ? paths : ["*"], save: ["*"] };
    }
    case "read_file":
      return { action: "read", ...one(args.file_path) };
    case "glob":
    case "grep":
      return { action: "read", ...one(args.path ?? args.pattern) };
    case "web_fetch":
      return { action: "webfetch", ...one(args.url) };
    case "web_search":
      return { action: "webfetch", ...one(args.query) };
    default:
      return { action: call.name, resources: ["*"], save: ["*"] };
  }
}
function count2(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : undefined;
}
function total(entries, read) {
  let sum = 0;
  for (const entry of entries) {
    const value = read(entry);
    if (value === undefined)
      return;
    sum += value;
  }
  return sum;
}
function turnUsage(snapshot, context) {
  const entries = Array.isArray(record2(snapshot).entries) ? record2(snapshot).entries : [];
  const items = entries.map(record2);
  const tokensIn = total(items, (entry) => count2(entry.tokens_in));
  const tokensOut = total(items, (entry) => count2(entry.tokens_out));
  const cacheRead = total(items, (entry) => count2(entry.cache_read_tokens));
  const cacheWrite = total(items, (entry) => count2(entry.cache_write_tokens) ?? 0);
  const cost = total(items, (entry) => {
    const usd = record2(entry.cost).USD;
    return typeof usd === "string" && usd.trim() !== "" ? count2(Number(usd)) : undefined;
  });
  const usage = {};
  if (items.length > 0 && tokensIn !== undefined && tokensOut !== undefined) {
    const prompt2 = tokensIn + (cacheWrite ?? 0);
    usage.prompt_tokens = prompt2;
    usage.completion_tokens = tokensOut;
    usage.total_tokens = prompt2 + tokensOut;
    usage.prompt_tokens_details = defined({ cached_tokens: cacheRead, cache_write_tokens: cacheWrite });
  }
  if (items.length > 0 && cost !== undefined)
    usage.cost_usd = String(cost);
  const size = record2(context);
  if (count2(size.tokens) !== undefined)
    usage[CONTEXT_FIELD] = defined({ tokens: size.tokens, budget: size.budget });
  const recorded = recordedUsage(usage);
  const prompt = count2(recorded.prompt_tokens);
  const completion = count2(recorded.completion_tokens);
  const details = record2(recorded.prompt_tokens_details);
  const read = count2(details.cached_tokens) ?? 0;
  const write = count2(details.cache_write_tokens) ?? 0;
  return {
    inputTokens: {
      total: prompt,
      noCache: prompt === undefined ? undefined : Math.max(0, prompt - read - write),
      cacheRead: prompt === undefined ? undefined : read,
      cacheWrite: prompt === undefined && write === 0 ? undefined : write
    },
    outputTokens: { total: completion, text: completion, reasoning: undefined }
  };
}
var STOP = { unified: "stop", raw: "success" };
function errorText(error) {
  const value = record2(error);
  const message = text(value.message) ?? "The Amplifier turn failed.";
  const remedy = text(value.remedy);
  return remedy ? `${message} ${remedy}` : message;
}

class TurnMapper {
  modelID;
  next = 0;
  textID;
  reasoningID;
  open = new Map;
  snapshot;
  context;
  ended = false;
  constructor(modelID) {
    this.modelID = modelID;
  }
  get done() {
    return this.ended;
  }
  id(kind) {
    return `${kind}-${this.next++}`;
  }
  closeText() {
    if (!this.textID)
      return [];
    const id = this.textID;
    this.textID = undefined;
    return [{ type: "text-end", id }];
  }
  closeReasoning() {
    if (!this.reasoningID)
      return [];
    const id = this.reasoningID;
    this.reasoningID = undefined;
    return [{ type: "reasoning-end", id }];
  }
  settleOpen(message) {
    const parts = [...this.open].map(([toolCallId, toolName]) => ({
      type: "tool-result",
      toolCallId,
      toolName,
      result: message,
      isError: true
    }));
    this.open.clear();
    return parts;
  }
  finish(raw) {
    return { type: "finish", finishReason: { ...STOP, raw }, usage: turnUsage(this.snapshot, this.context) };
  }
  map(envelope) {
    if (this.ended)
      return [];
    const payload = envelope.payload;
    switch (envelope.type) {
      case "turn_started": {
        const actual = record2(payload.primary_actual);
        const model = text(actual.provider) && text(actual.model) ? `${actual.provider}/${actual.model}` : this.modelID;
        return [
          { type: "stream-start", warnings: [] },
          { type: "response-metadata", id: envelope.turn_id, modelId: model }
        ];
      }
      case "output_delta": {
        const content = Array.isArray(payload.content) ? payload.content : [];
        const delta = content.map((part) => record2(part).type === "text" ? text(record2(part).text) : "");
        const joined = delta.join("");
        if (!joined)
          return [];
        const parts = this.closeReasoning();
        if (!this.textID) {
          this.textID = this.id("text");
          parts.push({ type: "text-start", id: this.textID });
        }
        parts.push({ type: "text-delta", id: this.textID, delta: joined });
        return parts;
      }
      case "reasoning_delta": {
        const delta = text(payload.text);
        if (!delta)
          return [];
        const parts = this.closeText();
        if (!this.reasoningID) {
          this.reasoningID = this.id("reasoning");
          parts.push({ type: "reasoning-start", id: this.reasoningID });
        }
        parts.push({ type: "reasoning-delta", id: this.reasoningID, delta });
        return parts;
      }
      case "reasoning_final": {
        if (this.reasoningID)
          return this.closeReasoning();
        const block = text(payload.text);
        if (!block)
          return [];
        const id = this.id("reasoning");
        return [
          ...this.closeText(),
          { type: "reasoning-start", id },
          { type: "reasoning-delta", id, delta: block },
          { type: "reasoning-end", id }
        ];
      }
      case "tool_call": {
        const call = record2(payload.call);
        if (!text(call.call_id) || !text(call.name) || this.open.has(call.call_id))
          return [];
        const { name, input } = toolDisplay(call);
        const json = JSON.stringify(input);
        this.open.set(call.call_id, name);
        const id = call.call_id;
        return [
          ...this.closeText(),
          ...this.closeReasoning(),
          { type: "tool-input-start", id, toolName: name, providerExecuted: true },
          { type: "tool-input-delta", id, delta: json },
          { type: "tool-input-end", id },
          { type: "tool-call", toolCallId: id, toolName: name, input: json, providerExecuted: true }
        ];
      }
      case "tool_result": {
        const resolution = record2(payload.resolution);
        const id = text(resolution.call_id);
        const name = id ? this.open.get(id) : undefined;
        if (!id || !name)
          return [];
        this.open.delete(id);
        const content = typeof resolution.content === "string" ? resolution.content : "";
        const outcome = text(resolution.outcome) ?? "unknown";
        if (outcome === "completed")
          return [{ type: "tool-result", toolCallId: id, toolName: name, result: content }];
        const reason = resolution.error ? errorText(resolution.error) : `The tool call ended ${outcome}.`;
        return [
          {
            type: "tool-result",
            toolCallId: id,
            toolName: name,
            result: content ? `${reason}
${content}` : reason,
            isError: true
          }
        ];
      }
      case "usage":
        this.snapshot = payload.snapshot;
        return [];
      case "progress": {
        const size = record2(record2(payload.data).context_size);
        if (Object.keys(size).length > 0)
          this.context = size;
        return [];
      }
      case "terminal": {
        if (payload.usage)
          this.snapshot = payload.usage;
        const state = text(payload.state) ?? "failure";
        const parts = [
          ...this.closeText(),
          ...this.closeReasoning(),
          ...this.settleOpen(`The Amplifier turn ended ${state}.`)
        ];
        this.ended = true;
        if (state === "success" || state === "rejected")
          return [...parts, this.finish(state)];
        return [...parts, this.finish(state), { type: "error", error: new Error(errorText(payload.error)) }];
      }
      default:
        return [];
    }
  }
  fail(error) {
    if (this.ended)
      return [];
    this.ended = true;
    const message = error instanceof Error ? error.message : String(error);
    return [
      ...this.closeText(),
      ...this.closeReasoning(),
      ...this.settleOpen(message),
      { type: "error", error: error instanceof Error ? error : new Error(message) }
    ];
  }
}

// src/language.ts
var PROVIDER = "amplifier";
var running = new Map;
function interrupt(sessionID) {
  running.get(sessionID)?.abort(new Error("The opencode session was interrupted."));
}
function header(headers, name) {
  const wanted = name.toLowerCase();
  for (const [key, value] of Object.entries(headers ?? {}))
    if (key.toLowerCase() === wanted && value)
      return value;
  return;
}
function declined(error) {
  const value = error;
  const tag = `${String(value?._tag ?? "")} ${String(value?.name ?? "")}`;
  return /Declined|Corrected|Blocked|Tool\.Error|ToolFailure|Rejected/i.test(tag);
}
function reasonOf(error) {
  const message = error?.message;
  return typeof message === "string" && message.trim() !== "" ? message : undefined;
}
async function failure(response) {
  let detail = `HTTP ${response.status}`;
  try {
    const body = await response.json();
    if (body.error?.message)
      detail = body.error.message;
  } catch {}
  return new Error(`Amplifier refused the turn: ${detail}`);
}

class AmplifierLanguageModel {
  modelId;
  context;
  specificationVersion = "v3";
  provider = PROVIDER;
  supportedUrls = {};
  constructor(modelId, context) {
    this.modelId = modelId;
    this.context = context;
  }
  async doGenerate(options) {
    const { stream } = await this.doStream(options);
    const content = [];
    let finish;
    const reader = stream.getReader();
    for (let read = await reader.read();!read.done; read = await reader.read()) {
      const part = read.value;
      if (part.type === "text-delta")
        content.push({ type: "text", text: part.delta });
      else if (part.type === "reasoning-delta")
        content.push({ type: "reasoning", text: part.delta });
      else if (part.type === "error")
        throw part.error;
      else if (part.type === "finish")
        finish = part;
    }
    if (!finish)
      throw new Error("The Amplifier turn ended without a result.");
    return { content, finishReason: finish.finishReason, usage: finish.usage, warnings: [] };
  }
  async doStream(options) {
    const face = this.context.face();
    if (!face)
      throw new Error("The Amplifier face is not running. Run: amplifier-opencode doctor");
    const sessionID = header(options.headers, "x-opencode-session-id");
    const effort = options.providerOptions?.[PROVIDER]?.reasoningEffort;
    const body = {
      model: this.modelId,
      messages: promptMessages(options.prompt),
      ...typeof effort === "string" ? { reasoning_effort: effort } : {},
      ...this.context.asks ? { approvals: "ask" } : {}
    };
    const abort = new AbortController;
    const forward = () => abort.abort(options.abortSignal?.reason);
    options.abortSignal?.addEventListener("abort", forward, { once: true });
    if (sessionID) {
      running.get(sessionID)?.abort(new Error("A newer turn started in this opencode session."));
      running.set(sessionID, abort);
    }
    const release = () => {
      options.abortSignal?.removeEventListener("abort", forward);
      if (sessionID && running.get(sessionID) === abort)
        running.delete(sessionID);
    };
    let response;
    try {
      response = await fetch(`${face.baseURL}/amplifier/turns`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${face.token}`,
          "Content-Type": "application/json",
          ...sessionID ? { "Amplifier-Session-Id": sessionHeader(sessionID) } : {}
        },
        body: JSON.stringify(body),
        signal: abort.signal
      });
    } catch (error) {
      release();
      throw error;
    }
    if (!response.ok || !response.body) {
      release();
      throw await failure(response);
    }
    const mapper = new TurnMapper(this.modelId);
    const calls = new Map;
    const answer = (requestID, decision, reason) => {
      if (abort.signal.aborted)
        return;
      fetch(`${face.baseURL}/amplifier/approvals/${encodeURIComponent(requestID)}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${face.token}`, "Content-Type": "application/json" },
        body: JSON.stringify(reason ? { decision, reason } : { decision }),
        signal: abort.signal
      }).catch((error) => log(`could not answer approval ${requestID}: ${error.message}`));
    };
    const approve = async (request) => {
      const requestID = String(request.request_id ?? "");
      if (!requestID)
        return;
      if (!sessionID)
        return answer(requestID, "cancel", "The request named no opencode session to ask.");
      const call = calls.get(String(request.call_id ?? ""));
      const ask = permissionAsk(call ?? { name: String(request.name ?? "unknown"), arguments: {} });
      try {
        await this.context.assert({
          sessionID,
          ...ask,
          metadata: { amplifier: { tool: call?.name ?? request.name, summary: request.summary } }
        });
        answer(requestID, "allow");
      } catch (error) {
        if (declined(error))
          answer(requestID, "deny", reasonOf(error) ?? "Declined in opencode.");
        else {
          log(`could not ask opencode about ${ask.action}: ${reasonOf(error) ?? String(error)}`);
          answer(requestID, "cancel", reasonOf(error));
        }
      }
    };
    const reader = response.body.pipeThrough(envelopes()).getReader();
    const read = async () => {
      if (mapper.done)
        return [];
      let result;
      try {
        result = await reader.read();
      } catch (error) {
        return mapper.fail(abort.signal.aborted ? new Error("The Amplifier turn was cancelled.") : error);
      }
      if (result.done)
        return mapper.fail(new Error("The Amplifier face ended the turn without a result."));
      const envelope = result.value;
      if (envelope.type === "tool_call") {
        const call = envelope.payload.call;
        if (call?.call_id)
          calls.set(call.call_id, call);
      } else if (envelope.type === "approval_request" && this.context.asks) {
        approve(envelope.payload.request ?? {});
      }
      return mapper.map(envelope);
    };
    const stream = new ReadableStream({
      async pull(controller) {
        for (;; ) {
          const parts = await read();
          for (const part of parts)
            controller.enqueue(part);
          if (mapper.done) {
            release();
            controller.close();
            return;
          }
          if (parts.length > 0)
            return;
        }
      },
      cancel(reason) {
        release();
        abort.abort(reason);
      }
    });
    return { stream, request: { body } };
  }
}

// src/index.ts
var AGENT = "amplifier";
var PACKAGE = `aisdk:${import.meta.url}`;
function createAmplifier() {
  return {};
}
var SCOPE = { providerID: PROVIDER };
var plugin = {
  id: "amplifier",
  setup: async (ctx) => {
    const directory = ctx.location.directory;
    const { options, problems } = readOptions();
    for (const problem of problems)
      log(problem);
    const resolved = await environment(ctx, options);
    logSources(directory, resolved.sources);
    const [installed, face] = await Promise.all([
      commandVersion(options.command, resolved.environment),
      startFace({
        command: options.command,
        directory,
        environment: resolved.environment,
        approvals: faceApprovals(options.approvals)
      }).catch((error) => error)
    ]);
    if (face instanceof Error)
      log(face.message);
    const stale = installed !== "0.23.1";
    if (stale) {
      log(`plugin ${"0.23.1"} runs with amplifier-opencode ${installed ?? "(unknown)"}. ` + "Run: amplifier-opencode install");
    }
    const live = { face: undefined, entries: [], fingerprint: resolved.fingerprint };
    if (!(face instanceof Error))
      show(live, face, options);
    try {
      await register(ctx, live, options, stale);
    } catch (error) {
      live.face?.stop();
      throw error;
    }
    const watching = new AbortController;
    watchEvents(ctx, live, options, directory, watching.signal);
    return () => {
      watching.abort();
      live.face?.stop();
    };
  }
};
async function environment(ctx, options) {
  const resolved = faceEnvironment(process.env, await connections(ctx), options);
  const fingerprint = createHash3("sha256").update(JSON.stringify(Object.entries(resolved.environment).sort(([a], [b]) => a.localeCompare(b)))).digest("hex");
  return { ...resolved, fingerprint };
}
async function connections(ctx) {
  const found = [];
  for (const integrationID of INTEGRATIONS) {
    try {
      const connection = await ctx.integration.connection.active(integrationID);
      if (!connection)
        continue;
      const usable = integrationID === "github-copilot" ? "oauth" : "key";
      if (connection.type === "credential" && connection.method !== usable)
        continue;
      const credential = await ctx.integration.connection.resolve(connection);
      if (credential)
        found.push({ integrationID, credential });
    } catch (error) {
      log(`could not read the opencode ${integrationID} connection: ${error.message}`);
    }
  }
  return found;
}
function logSources(directory, sources) {
  const names = Object.entries(sources).map(([name, source]) => `${name} from ${source}`);
  log(`face environment for ${directory}: ${names.join(", ") || "no provider credentials"}`);
}
function show(live, face, options) {
  live.face = face;
  live.entries = modelEntries(face.models);
  const { defaultModel } = options;
  if (!defaultModel || defaultModel === DEFAULT_MODEL_KEY)
    return;
  if (defaultModelKey(live.entries, defaultModel) === DEFAULT_MODEL_KEY) {
    log(`defaultModel ${defaultModel} is not a model the face lists; using the Amplifier default`);
  }
}
function watchEvents(ctx, live, options, directory, signal) {
  let queue = Promise.resolve();
  const refresh = () => {
    queue = queue.then(async () => {
      if (signal.aborted)
        return;
      const resolved = await environment(ctx, options);
      if (signal.aborted || resolved.fingerprint === live.fingerprint)
        return;
      log(`opencode connections changed; restarting the face for ${directory}`);
      logSources(directory, resolved.sources);
      const face = await startFace({
        command: options.command,
        directory,
        environment: resolved.environment,
        approvals: faceApprovals(options.approvals)
      });
      if (signal.aborted) {
        face.stop();
        return;
      }
      const previous = live.face;
      show(live, face, options);
      live.fingerprint = resolved.fingerprint;
      await ctx.provider.reload();
      await ctx.model.reload();
      await ctx.agent.reload();
      previous?.stop();
    }).catch((error) => log(`${live.face ? "kept the running face" : "no face runs"} for ${directory}: ${error.message}`));
  };
  (async () => {
    try {
      for await (const event of ctx.event.subscribe({ signal })) {
        if (event.type === "credential.updated" || event.type === "credential.switched")
          refresh();
        else if (event.type === "session.execution.interrupted")
          interrupt(event.data.sessionID);
      }
    } catch (error) {
      if (!signal.aborted)
        log(`stopped following opencode connections for ${directory}: ${error.message}`);
    }
  })();
  refresh();
}
var PERMISSIONS = [
  { action: "*", resource: "*", effect: "allow" },
  { action: "read", resource: "*.env", effect: "ask" },
  { action: "read", resource: "*.env.*", effect: "ask" },
  { action: "read", resource: "*.env.example", effect: "allow" },
  ...["glob", "grep", "websearch", "question", "todowrite", "subagent", "skill", "execute", "opencode_*"].map((action) => ({ action, resource: "*", effect: "deny" }))
];
async function register(ctx, live, options, stale) {
  const { defaultModel, setDefaultAgent } = options;
  await ctx.provider.transform((providers) => {
    const face = live.face;
    if (!face)
      return;
    providers.update(PROVIDER, (provider) => {
      provider.name = stale ? "Amplifier (run: amplifier-opencode install)" : "Amplifier";
      provider.activation = "enabled";
      provider.package = PACKAGE;
    });
    for (const entry of live.entries) {
      providers.models.update(PROVIDER, entry.key, (model) => {
        model.modelID = entry.modelID;
        model.name = entry.name;
        model.limit = { context: entry.contextWindow ?? 0, output: 0 };
        model.cost = CARRIER_COST;
        model.variants = entry.efforts.map((effort) => ({
          id: effort,
          settings: { reasoningEffort: effort }
        }));
      });
    }
  });
  if (setDefaultAgent) {
    await ctx.model.transform((models) => {
      if (live.face)
        models.default.set(PROVIDER, defaultModelKey(live.entries, defaultModel));
    });
  }
  await ctx.agent.transform((agents) => {
    if (!live.face)
      return;
    agents.update(AGENT, (agent) => {
      agent.name = AGENT;
      agent.description = "Amplifier Agent. Runs its own tools in this directory.";
      agent.mode = "primary";
      agent.hidden = false;
      agent.model = { providerID: PROVIDER, id: defaultModelKey(live.entries, defaultModel) };
      const rules = agent.permissions;
      const defaults = rules.findIndex((rule, index) => JSON.stringify(rule) !== JSON.stringify(PERMISSIONS[index]));
      agent.permissions = [...PERMISSIONS, ...rules.slice(defaults < 0 ? rules.length : defaults)];
    });
    if (setDefaultAgent)
      agents.default(AGENT);
  });
  await ctx.aisdk.hook("sdk", (event) => {
    event.sdk ??= {};
  }, SCOPE);
  const asks = options.approvals === "opencode";
  await ctx.aisdk.hook("language", (event) => {
    event.language = new AmplifierLanguageModel(String(event.model.modelID ?? event.model.id), {
      face: () => live.face,
      asks,
      assert: (input) => ctx.permission.assert({ ...input, sessionID: input.sessionID })
    });
  }, SCOPE);
  await ctx.session.hook("context", (event) => {
    event.tools = {};
  }, SCOPE);
  await ctx.session.hook("title", (event) => {
    const user = event.messages.find((message) => message.role === "user");
    const text2 = (user?.content ?? []).map((part) => part.type === "text" ? part.text : "").join(`
`);
    event.result = titleFrom(text2) ?? "Amplifier session";
  }, SCOPE);
  await ctx.session.hook("compaction", (event) => {
    event.result = {
      summary: "Amplifier manages this conversation's context itself.",
      native: { messages: event.messages }
    };
  }, SCOPE);
  await ctx.session.hook("retry", (event) => {
    event.decision = { retry: false };
  }, SCOPE);
}
var src_default = plugin;
export {
  src_default as default,
  createAmplifier
};
