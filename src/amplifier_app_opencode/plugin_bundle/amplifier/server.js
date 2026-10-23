// @bun
// amplifier-opencode-plugin 0.25.0
var __defProp = Object.defineProperty;
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, {
      get: all[name],
      enumerable: true,
      configurable: true,
      set: (newValue) => all[name] = () => newValue
    });
};

// src/index.ts
import { createHash as createHash3 } from "crypto";
import path5 from "path";

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
var INPUTS_ENV = "AMPLIFIER_OPENCODE_FACE_INPUTS";
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
    child = spawn(settings.command, [
      "face",
      ...["--port", String(port), "--token-env", TOKEN_ENV, "--inputs-env", INPUTS_ENV],
      ...["--approvals", settings.approvals]
    ], {
      cwd: settings.directory,
      env: { ...settings.environment, [TOKEN_ENV]: token, [INPUTS_ENV]: JSON.stringify(settings.inputs) },
      stdio: ["ignore", output, output]
    });
  } finally {
    closeSync(output);
  }
  let exited;
  let stopping = false;
  let crash = () => {};
  const crashed = new Promise((resolve) => crash = resolve);
  child.once("error", (error) => exited = `could not run ${settings.command}: ${error.message}`);
  child.once("exit", (code, signal) => {
    exited ??= `exited with ${signal ?? `code ${code}`}`;
    if (!stopping)
      crash(exited);
  });
  const stop = () => {
    stopping = true;
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
        let holds = 0;
        let retired = false;
        const end = () => {
          process.removeListener("exit", stop);
          stop();
        };
        return {
          baseURL,
          token,
          models: body.data,
          crashed,
          hold: () => {
            holds++;
            let held = true;
            return () => {
              if (!held)
                return;
              held = false;
              if (--holds === 0 && retired)
                end();
            };
          },
          retire: () => {
            retired = true;
            if (holds === 0)
              end();
          },
          stop: end
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

// src/inputs.ts
import { readFileSync as readFileSync2 } from "fs";
import path3 from "path";
var NO_INPUTS = { skills: [], mcp_servers: [] };
var FRONTMATTER = /^\uFEFF?---\r?\n([\s\S]*?)\r?\n---/;
function skillDirectories(skills, read = readSkill) {
  const names = new Set;
  const directories = [];
  for (const skill of skills) {
    if (path3.basename(skill.path) !== "SKILL.md")
      continue;
    const directory = path3.dirname(skill.path);
    if (directories.includes(directory))
      continue;
    const content = read(skill.path);
    const header = content === undefined ? undefined : FRONTMATTER.exec(content)?.[1];
    if (!header)
      continue;
    const name = /^name:\s*["']?([^"'\r\n]+?)["']?\s*$/m.exec(header)?.[1];
    if (!name || !/^description:\s*\S/m.test(header) || names.has(name))
      continue;
    names.add(name);
    directories.push(directory);
  }
  return directories;
}
function readSkill(file) {
  try {
    return readFileSync2(file, "utf8");
  } catch {
    return;
  }
}
function mcpServers(configs, statuses) {
  const servers = [];
  const skipped = [];
  for (const [name, config] of configs) {
    const status = statuses.get(name);
    if (config.disabled || status === undefined || status === "disabled")
      continue;
    if (status === "failed" || status === "needs_auth") {
      skipped.push(`${name} (${status})`);
      continue;
    }
    if (config.type === "local") {
      const [command, ...args] = config.command;
      if (!command) {
        skipped.push(`${name} (no command)`);
        continue;
      }
      servers.push({
        name,
        transport: "stdio",
        command,
        ...args.length ? { args } : {},
        ...config.environment && Object.keys(config.environment).length ? { env: { ...config.environment } } : {}
      });
    } else {
      servers.push({
        name,
        transport: "http",
        url: config.url,
        ...config.headers && Object.keys(config.headers).length ? { headers: { ...config.headers } } : {}
      });
    }
  }
  return { servers, skipped };
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

// src/proxy.ts
var exports_proxy = {};
__export(exports_proxy, {
  wait: () => wait,
  size: () => size,
  resolve: () => resolve,
  rejectAll: () => rejectAll,
  reject: () => reject,
  registered: () => registered,
  register: () => register,
  proxyResult: () => proxyResult,
  forget: () => forget,
  fileChanges: () => fileChanges,
  PROXY_TOOLS: () => PROXY_TOOLS,
  PROXIED: () => PROXIED,
  FILE_CHANGES: () => FILE_CHANGES
});
var PROXIED = {
  edit_file: "edit",
  apply_patch: "patch",
  glob: "glob",
  grep: "grep"
};
var PROXY_TOOLS = [...new Set(Object.values(PROXIED))];
var pending = new Map;
function register(callID, sessionID) {
  if (pending.has(callID))
    return;
  let resolve;
  let reject;
  const promise = new Promise((ok, fail) => {
    resolve = ok;
    reject = fail;
  });
  promise.catch(() => {});
  pending.set(callID, { sessionID, promise, resolve, reject, settled: false });
}
function registered(callID) {
  return pending.has(callID);
}
function resolve(callID, result) {
  const entry = pending.get(callID);
  if (!entry || entry.settled)
    return;
  entry.settled = true;
  entry.resolve(result);
}
function reject(callID, error) {
  const entry = pending.get(callID);
  if (!entry || entry.settled)
    return;
  entry.settled = true;
  entry.reject(error);
}
function rejectAll(error, sessionID) {
  for (const [callID, entry] of pending) {
    if (sessionID === undefined || entry.sessionID === sessionID)
      reject(callID, error);
  }
}
async function wait(callID, signal) {
  const entry = pending.get(callID);
  if (!entry)
    throw new Error(`No Amplifier tool call ${callID} is waiting.`);
  try {
    if (!signal)
      return await entry.promise;
    if (signal.aborted)
      throw signal.reason ?? new Error("The tool call was interrupted.");
    return await new Promise((ok, fail) => {
      const abort = () => fail(signal.reason ?? new Error("The tool call was interrupted."));
      signal.addEventListener("abort", abort, { once: true });
      entry.promise.then(ok, fail).finally(() => signal.removeEventListener("abort", abort));
    });
  } finally {
    pending.delete(callID);
  }
}
function forget(sessionID) {
  for (const [callID, entry] of pending)
    if (entry.sessionID === sessionID && entry.settled)
      pending.delete(callID);
}
function size() {
  return pending.size;
}
var FILE_CHANGES = "com.microsoft.amplifier.file_changes";
function count(value) {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : undefined;
}
function fileChanges(payload) {
  const raw = payload[FILE_CHANGES];
  if (!Array.isArray(raw))
    return [];
  return raw.flatMap((item) => {
    const value = typeof item === "object" && item !== null ? item : {};
    const change = value.change;
    const additions = count(value.additions);
    const deletions = count(value.deletions);
    if (typeof value.path !== "string" || typeof value.patch !== "string" || change !== "added" && change !== "modified" && change !== "deleted" || additions === undefined || deletions === undefined)
      return [];
    return [{ path: value.path, change, patch: value.patch, additions, deletions }];
  });
}
function fileDiff(change) {
  return {
    file: change.path,
    patch: change.patch,
    additions: change.additions,
    deletions: change.deletions,
    status: change.change
  };
}
var PATCH_TYPES = { added: "add", modified: "update", deleted: "delete" };
function searchResult(content) {
  try {
    const parsed = JSON.parse(content);
    return typeof parsed === "object" && parsed !== null && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}
function lines(content) {
  return content.split(`
`).filter((line) => line.trim() !== "").length;
}
function grepMatches(content) {
  const result = searchResult(content);
  return count(result.matches_count) ?? count(result.total_matches) ?? lines(content);
}
function globCount(content) {
  const result = searchResult(content);
  return count(result.count) ?? count(result.total_files) ?? lines(content);
}
function proxyResult(tool, content, changes) {
  const files = changes.map(fileDiff);
  switch (tool) {
    case "edit":
      return { output: { files, replacements: files.length }, content, metadata: { files } };
    case "patch": {
      const applied = changes.map((change) => ({
        type: PATCH_TYPES[change.change],
        resource: change.path,
        target: change.path
      }));
      return { output: { applied, files }, content, metadata: { files } };
    }
    case "glob":
      return { output: [], content, metadata: { count: globCount(content), truncated: false } };
    case "grep":
      return { output: [], content, metadata: { matches: grepMatches(content), truncated: false } };
    default:
      return { output: undefined, content, metadata: {} };
  }
}

// src/usage.ts
var CONTEXT_FIELD = "com.microsoft.amplifier.context";
var CARRIER_COST = [{ input: 0, output: 0, cache: { read: 0, write: 1e6 } }];
function count2(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : undefined;
}
function record(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value : undefined;
}
function costUSD(value) {
  if (typeof value === "string" && value.trim() !== "")
    return count2(Number(value));
  return count2(value);
}
function recordedUsage(usage) {
  const write = costUSD(usage.cost_usd) ?? 0;
  const details = record(usage.prompt_tokens_details) ?? {};
  const tokens = count2(record(usage[CONTEXT_FIELD])?.tokens);
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
  const prompt = count2(usage.prompt_tokens);
  const completion = count2(usage.completion_tokens);
  if (prompt === undefined || completion === undefined)
    return usage;
  const input = prompt - (count2(details.cache_write_tokens) ?? 0) + write;
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
  let pending2 = "";
  const drain = (controller) => {
    for (let match = BLANK_LINE.exec(pending2);match; match = BLANK_LINE.exec(pending2)) {
      const envelope = envelopeOf(pending2.slice(0, match.index));
      pending2 = pending2.slice(match.index + match[0].length);
      if (envelope)
        controller.enqueue(envelope);
    }
  };
  return new TransformStream({
    transform(chunk, controller) {
      pending2 += decoder.decode(chunk, { stream: true });
      drain(controller);
    },
    flush(controller) {
      pending2 += decoder.decode();
      drain(controller);
      const envelope = envelopeOf(pending2);
      if (envelope)
        controller.enqueue(envelope);
      pending2 = "";
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
function proxiedCall(call, offered) {
  if (call.source === "mcp")
    return;
  const name = PROXIED[call.name];
  if (!name || !offered.has(name))
    return;
  const args = record2(call.arguments);
  const string = (value) => typeof value === "string" ? value : undefined;
  switch (name) {
    case "edit": {
      const path4 = text(args.file_path);
      const oldString = string(args.old_string);
      const newString = string(args.new_string);
      if (!path4 || oldString === undefined || newString === undefined)
        return;
      const replaceAll = typeof args.replace_all === "boolean" ? args.replace_all : undefined;
      return { name, input: defined({ path: path4, oldString, newString, replaceAll }) };
    }
    case "patch": {
      const patchText = text(args.patch) ?? text(args.diff);
      return patchText ? { name, input: { patchText } } : undefined;
    }
    case "glob": {
      const pattern = text(args.pattern);
      return pattern ? { name, input: defined({ pattern, path: text(args.path) }) } : undefined;
    }
    case "grep": {
      const pattern = text(args.pattern);
      if (!pattern)
        return;
      return { name, input: defined({ pattern, path: text(args.path), include: text(args.glob) }) };
    }
    default:
      return;
  }
}
function count3(value) {
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
function entriesOf(snapshot) {
  const entries = record2(snapshot).entries;
  return (Array.isArray(entries) ? entries : []).map(record2);
}
function usd(entry) {
  const value = record2(entry.cost).USD;
  return typeof value === "string" && value.trim() !== "" ? count3(Number(value)) : undefined;
}
function snapshotCost(snapshot) {
  const entries = entriesOf(snapshot);
  return entries.length > 0 ? total(entries, usd) : undefined;
}
function snapshotKnown(snapshot) {
  const entries = entriesOf(snapshot);
  return entries.length > 0 && entries.every((entry) => count3(entry.tokens_in) !== undefined && count3(entry.tokens_out) !== undefined && count3(entry.cache_read_tokens) !== undefined && usd(entry) !== undefined);
}
function turnUsage(snapshot, context, costBefore = 0) {
  const items = entriesOf(snapshot);
  const tokensIn = total(items, (entry) => count3(entry.tokens_in));
  const tokensOut = total(items, (entry) => count3(entry.tokens_out));
  const cacheRead = total(items, (entry) => count3(entry.cache_read_tokens));
  const cacheWrite = total(items, (entry) => count3(entry.cache_write_tokens) ?? 0);
  const cost = snapshotCost(snapshot);
  const usage = {};
  if (items.length > 0 && tokensIn !== undefined && tokensOut !== undefined) {
    const prompt2 = tokensIn + (cacheWrite ?? 0);
    usage.prompt_tokens = prompt2;
    usage.completion_tokens = tokensOut;
    usage.total_tokens = prompt2 + tokensOut;
    usage.prompt_tokens_details = defined({ cached_tokens: cacheRead, cache_write_tokens: cacheWrite });
  }
  if (cost !== undefined)
    usage.cost_usd = String(Math.max(0, cost - costBefore));
  const size2 = record2(context);
  if (count3(size2.tokens) !== undefined)
    usage[CONTEXT_FIELD] = defined({ tokens: size2.tokens, budget: size2.budget });
  const recorded = recordedUsage(usage);
  const prompt = count3(recorded.prompt_tokens);
  const completion = count3(recorded.completion_tokens);
  const details = record2(recorded.prompt_tokens_details);
  const read = count3(details.cached_tokens) ?? 0;
  const write = count3(details.cache_write_tokens) ?? 0;
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
var TOOL_CALLS = { unified: "tool-calls", raw: "tool-calls" };
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
  proxied = new Map;
  offered;
  sink;
  sessionID;
  snapshot;
  known;
  context;
  reported = 0;
  stepProxied = 0;
  ended = false;
  metadata;
  constructor(modelID, options = {}) {
    this.modelID = modelID;
    this.offered = options.sink ? options.offered ?? new Set : new Set;
    this.sink = options.sink;
    this.sessionID = options.sessionID;
  }
  get done() {
    return this.ended;
  }
  get session() {
    return this.sessionID;
  }
  stepStart() {
    return [{ type: "stream-start", warnings: [] }, ...this.metadata ? [this.metadata] : []];
  }
  emptyFinish() {
    return this.finish(STOP);
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
    for (const callID of this.proxied.keys())
      this.sink?.reject(callID, new Error(message));
    this.proxied.clear();
    return parts;
  }
  finish(reason, raw = reason.raw) {
    const snapshot = this.ended ? this.snapshot : this.known;
    const usage = turnUsage(snapshot, this.context, this.reported);
    this.reported = Math.max(this.reported, snapshotCost(snapshot) ?? this.reported);
    return { type: "finish", finishReason: { ...reason, raw }, usage };
  }
  boundary() {
    if (this.stepProxied === 0 || this.open.size > 0)
      return [];
    this.stepProxied = 0;
    return [...this.closeText(), ...this.closeReasoning(), this.finish(TOOL_CALLS)];
  }
  toolCall(call) {
    const proxied = this.sink ? proxiedCall(call, this.offered) : undefined;
    const { name, input } = proxied ?? toolDisplay(call);
    const json = JSON.stringify(input);
    const id = call.call_id;
    const parts = [...this.closeText(), ...this.closeReasoning()];
    if (proxied) {
      this.sink?.register(id, this.sessionID);
      this.proxied.set(id, name);
      this.stepProxied++;
      return [
        ...parts,
        { type: "tool-input-start", id, toolName: name },
        { type: "tool-input-delta", id, delta: json },
        { type: "tool-input-end", id },
        { type: "tool-call", toolCallId: id, toolName: name, input: json }
      ];
    }
    this.open.set(id, name);
    return [
      ...parts,
      { type: "tool-input-start", id, toolName: name, providerExecuted: true },
      { type: "tool-input-delta", id, delta: json },
      { type: "tool-input-end", id },
      { type: "tool-call", toolCallId: id, toolName: name, input: json, providerExecuted: true }
    ];
  }
  toolResult(payload) {
    const resolution = record2(payload.resolution);
    const id = text(resolution.call_id);
    if (!id)
      return [];
    const content = typeof resolution.content === "string" ? resolution.content : "";
    const outcome = text(resolution.outcome) ?? "unknown";
    const reason = outcome === "completed" ? undefined : resolution.error ? errorText(resolution.error) : undefined;
    const failure = reason ?? `The tool call ended ${outcome}.`;
    const proxied = this.proxied.get(id);
    if (proxied) {
      this.proxied.delete(id);
      if (outcome === "completed")
        this.sink?.resolve(id, proxyResult(proxied, content, fileChanges(payload)));
      else
        this.sink?.reject(id, new Error(content ? `${failure}
${content}` : failure));
      return [];
    }
    const name = this.open.get(id);
    if (!name)
      return [];
    this.open.delete(id);
    if (outcome === "completed")
      return [{ type: "tool-result", toolCallId: id, toolName: name, result: content }];
    return [
      {
        type: "tool-result",
        toolCallId: id,
        toolName: name,
        result: content ? `${failure}
${content}` : failure,
        isError: true
      }
    ];
  }
  map(envelope) {
    if (this.ended)
      return [];
    const payload = envelope.payload;
    switch (envelope.type) {
      case "turn_started": {
        const actual = record2(payload.primary_actual);
        const model = text(actual.provider) && text(actual.model) ? `${actual.provider}/${actual.model}` : this.modelID;
        this.metadata = { type: "response-metadata", id: envelope.turn_id, modelId: model };
        return [{ type: "stream-start", warnings: [] }, this.metadata];
      }
      case "output_delta": {
        const content = Array.isArray(payload.content) ? payload.content : [];
        const delta = content.map((part) => record2(part).type === "text" ? text(record2(part).text) : "");
        const joined = delta.join("");
        if (!joined)
          return [];
        const parts = [...this.boundary(), ...this.closeReasoning()];
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
        const parts = [...this.boundary(), ...this.closeText()];
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
          ...this.boundary(),
          ...this.closeText(),
          { type: "reasoning-start", id },
          { type: "reasoning-delta", id, delta: block },
          { type: "reasoning-end", id }
        ];
      }
      case "tool_call": {
        const call = record2(payload.call);
        if (!text(call.call_id) || !text(call.name))
          return [];
        if (this.open.has(call.call_id) || this.proxied.has(call.call_id))
          return [];
        return this.toolCall(call);
      }
      case "tool_result":
        return this.toolResult(payload);
      case "usage":
        this.snapshot = payload.snapshot;
        if (snapshotKnown(payload.snapshot))
          this.known = payload.snapshot;
        return [];
      case "progress": {
        const size2 = record2(record2(payload.data).context_size);
        if (Object.keys(size2).length > 0)
          this.context = size2;
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
        this.stepProxied = 0;
        if (state === "success" || state === "rejected")
          return [...parts, this.finish(STOP, state)];
        return [...parts, this.finish(STOP, state), { type: "error", error: new Error(errorText(payload.error)) }];
      }
      default:
        return [];
    }
  }
  fail(error) {
    if (this.ended)
      return [];
    this.ended = true;
    this.stepProxied = 0;
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
function served() {
  const key = Symbol.for("amplifier-opencode.served");
  const scope = globalThis;
  scope[key] ??= new Map;
  return scope[key];
}
function serve(directory, context) {
  const entry = { context, model: (modelId) => new AmplifierLanguageModel(modelId, directory, context) };
  served().set(directory, entry);
  return () => {
    if (served().get(directory) === entry)
      served().delete(directory);
  };
}
var turns = new Map;
var lastFinish = new Map;
function interrupt(sessionID) {
  const error = new Error("The opencode session was interrupted.");
  turns.get(sessionID)?.abort(error);
  rejectAll(error, sessionID);
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
  directory;
  context;
  specificationVersion = "v3";
  provider = PROVIDER;
  supportedUrls = {};
  constructor(modelId, directory, context) {
    this.modelId = modelId;
    this.directory = directory;
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
    const current = served().get(this.directory);
    if (current && current.context !== this.context)
      return current.model(this.modelId).doStream(options);
    const sessionID = header(options.headers, "x-opencode-session-id");
    const continues = options.prompt.at(-1)?.role === "tool";
    let turn = sessionID ? turns.get(sessionID) : undefined;
    if (turn && !continues) {
      turn.abort(new Error("A newer turn started in this opencode session."));
      turn = undefined;
    }
    if (!turn && continues) {
      const finish = (sessionID ? lastFinish.get(sessionID) : undefined) ?? EMPTY_FINISH;
      return { stream: finished(finish), request: { body: {} } };
    }
    if (!turn) {
      turn = await this.start(options, sessionID);
      if (sessionID)
        turns.set(sessionID, turn);
    }
    return { stream: turn.step(options.abortSignal), request: { body: turn.body } };
  }
  async start(options, sessionID) {
    const face = await this.context.face();
    if (!face)
      throw new Error("The Amplifier face is not running. Run: amplifier-opencode doctor");
    const release = face.hold();
    try {
      return await this.run(face, release, options, sessionID);
    } catch (error) {
      release();
      throw error;
    }
  }
  async run(face, release, options, sessionID) {
    const effort = options.providerOptions?.[PROVIDER]?.reasoningEffort;
    const body = {
      model: this.modelId,
      messages: promptMessages(options.prompt),
      ...typeof effort === "string" ? { reasoning_effort: effort } : {},
      ...this.context.asks ? { approvals: "ask" } : {}
    };
    if (sessionID)
      forget(sessionID);
    const abort = new AbortController;
    const response = await fetch(`${face.baseURL}/amplifier/turns`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${face.token}`,
        "Content-Type": "application/json",
        ...sessionID ? { "Amplifier-Session-Id": sessionHeader(sessionID) } : {}
      },
      body: JSON.stringify(body),
      signal: abort.signal
    });
    if (!response.ok || !response.body)
      throw await failure(response);
    const offered = new Set((options.tools ?? []).map((tool) => tool.name).filter((name) => PROXY_TOOLS.includes(name)));
    const mapper = new TurnMapper(this.modelId, sessionID ? { offered, sink: exports_proxy, sessionID } : {});
    const turn = new Turn(body, mapper, abort, () => {
      if (sessionID && turns.get(sessionID) === turn)
        turns.delete(sessionID);
    });
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
    (async () => {
      while (!mapper.done) {
        let result;
        try {
          result = await reader.read();
        } catch (error) {
          turn.push(mapper.fail(abort.signal.aborted ? new Error("The Amplifier turn was cancelled.") : error));
          break;
        }
        if (result.done) {
          turn.push(mapper.fail(new Error("The Amplifier face ended the turn without a result.")));
          break;
        }
        const envelope = result.value;
        if (envelope.type === "tool_call") {
          const call = envelope.payload.call;
          if (call?.call_id)
            calls.set(call.call_id, call);
        } else if (envelope.type === "approval_request" && this.context.asks) {
          approve(envelope.payload.request ?? {});
        }
        if (sessionID)
          this.context.observe?.(sessionID, envelope);
        turn.push(mapper.map(envelope));
      }
      turn.end();
      reader.releaseLock();
      release();
    })();
    return turn;
  }
}
var EMPTY_FINISH = {
  type: "finish",
  finishReason: { unified: "stop", raw: "success" },
  usage: {
    inputTokens: { total: undefined, noCache: undefined, cacheRead: undefined, cacheWrite: undefined },
    outputTokens: { total: undefined, text: undefined, reasoning: undefined }
  }
};
function finished(finish) {
  return new ReadableStream({
    start(controller) {
      controller.enqueue({ type: "stream-start", warnings: [] });
      controller.enqueue(finish);
      controller.close();
    }
  });
}

class Turn {
  body;
  mapper;
  controller;
  release;
  parts = [];
  wake;
  ended = false;
  steps = 0;
  constructor(body, mapper, controller, release) {
    this.body = body;
    this.mapper = mapper;
    this.controller = controller;
    this.release = release;
  }
  push(parts) {
    if (parts.length === 0)
      return;
    this.parts.push(...parts);
    this.wake?.();
  }
  end() {
    this.ended = true;
    this.wake?.();
  }
  abort(reason) {
    this.controller.abort(reason);
    this.release();
  }
  async next() {
    while (this.parts.length === 0) {
      if (this.ended)
        return;
      await new Promise((wake) => {
        this.wake = wake;
      });
      this.wake = undefined;
    }
    return this.parts.shift();
  }
  step(signal) {
    const first = this.steps++ === 0;
    const forward = () => this.abort(signal?.reason);
    signal?.addEventListener("abort", forward, { once: true });
    const done = () => signal?.removeEventListener("abort", forward);
    let finished2 = false;
    let started = first;
    return new ReadableStream({
      pull: async (controller) => {
        if (!started) {
          started = true;
          for (const part2 of this.mapper.stepStart())
            controller.enqueue(part2);
          return;
        }
        const part = await this.next();
        if (!part) {
          if (!finished2)
            controller.enqueue(this.mapper.emptyFinish());
          done();
          this.release();
          controller.close();
          return;
        }
        controller.enqueue(part);
        if (part.type !== "finish")
          return;
        finished2 = true;
        this.remember(part);
        if (part.finishReason.unified === "tool-calls") {
          done();
          controller.close();
        }
      },
      cancel: (reason) => {
        done();
        this.abort(reason);
      }
    });
  }
  remember(part) {
    const session = this.mapper.session;
    if (!session)
      return;
    const inputTokens = { ...part.usage.inputTokens, cacheWrite: 0 };
    lastFinish.set(session, {
      ...part,
      finishReason: { unified: "stop", raw: "success" },
      usage: { ...part.usage, inputTokens }
    });
  }
}

// src/usage-book.ts
import { mkdirSync as mkdirSync2, readFileSync as readFileSync3, renameSync, writeFileSync } from "fs";
import path4 from "path";
function record3(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value : {};
}
function count4(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}
function usd2(value) {
  const amount = record3(value).USD;
  return typeof amount === "string" && amount.trim() !== "" ? count4(Number(amount)) : null;
}
function usageRows(snapshot) {
  const entries = record3(snapshot).entries;
  return (Array.isArray(entries) ? entries : []).map((item) => {
    const entry = record3(item);
    return {
      provider: typeof entry.provider === "string" ? entry.provider : "unknown",
      model: typeof entry.model === "string" ? entry.model : "unknown",
      tokensIn: count4(entry.tokens_in),
      tokensOut: count4(entry.tokens_out),
      cacheRead: count4(entry.cache_read_tokens),
      cacheWrite: count4(entry.cache_write_tokens),
      costUSD: usd2(entry.cost)
    };
  });
}
var plus = (a, b) => a === null || b === null ? null : a + b;
function addRows(a, b) {
  const rows = new Map;
  for (const row of [...a, ...b]) {
    const key = `${row.provider}/${row.model}`;
    const seen = rows.get(key);
    rows.set(key, seen ? {
      ...seen,
      tokensIn: plus(seen.tokensIn, row.tokensIn),
      tokensOut: plus(seen.tokensOut, row.tokensOut),
      cacheRead: plus(seen.cacheRead, row.cacheRead),
      cacheWrite: plus(seen.cacheWrite, row.cacheWrite),
      costUSD: plus(seen.costUSD, row.costUSD)
    } : row);
  }
  return [...rows.values()];
}
function runningRows(previous, snapshot) {
  const known = new Map(previous.map((row) => [`${row.provider}/${row.model}`, row]));
  return snapshot.map((row) => {
    const last = known.get(`${row.provider}/${row.model}`);
    return {
      ...row,
      tokensIn: row.tokensIn ?? last?.tokensIn ?? 0,
      tokensOut: row.tokensOut ?? last?.tokensOut ?? 0,
      cacheRead: row.cacheRead ?? last?.cacheRead ?? 0,
      cacheWrite: row.cacheWrite ?? last?.cacheWrite ?? 0,
      costUSD: row.costUSD ?? last?.costUSD ?? 0
    };
  });
}
function savedRows(value) {
  return (Array.isArray(value) ? value : []).map((item) => {
    const row = record3(item);
    return {
      provider: typeof row.provider === "string" ? row.provider : "unknown",
      model: typeof row.model === "string" ? row.model : "unknown",
      tokensIn: count4(row.tokensIn),
      tokensOut: count4(row.tokensOut),
      cacheRead: count4(row.cacheRead),
      cacheWrite: count4(row.cacheWrite),
      costUSD: count4(row.costUSD)
    };
  });
}

class UsageBook {
  directory;
  books = new Map;
  constructor(directory) {
    this.directory = directory;
  }
  file(sessionID) {
    return this.directory && /^[\w-]+$/.test(sessionID) ? path4.join(this.directory, `${sessionID}.json`) : undefined;
  }
  load(sessionID) {
    const known = this.books.get(sessionID);
    if (known)
      return known;
    const file = this.file(sessionID);
    if (!file)
      return;
    let saved;
    try {
      saved = record3(JSON.parse(readFileSync3(file, "utf8")));
    } catch {
      return;
    }
    const compactions = count4(saved.compactions) ?? 0;
    const book = { done: savedRows(saved.done), turn: savedRows(saved.turn), running: false, compactions };
    this.books.set(sessionID, book);
    return book;
  }
  save(sessionID, book) {
    const file = this.file(sessionID);
    if (!file)
      return;
    try {
      mkdirSync2(path4.dirname(file), { recursive: true });
      const { done, turn, compactions } = book;
      writeFileSync(`${file}.tmp`, JSON.stringify({ done, turn, compactions }));
      renameSync(`${file}.tmp`, file);
    } catch (error) {
      log(`could not save the Amplifier usage of ${sessionID}: ${error.message}`);
    }
  }
  get(sessionID) {
    const book = this.load(sessionID);
    if (!book)
      return { sessionID, turn: [], session: [], compactions: 0 };
    return {
      sessionID,
      turn: book.turn,
      session: book.running ? addRows(book.done, book.turn) : book.done,
      compactions: book.compactions
    };
  }
  observe(sessionID, envelope) {
    let book = this.load(sessionID);
    if (!book) {
      book = { done: [], turn: [], running: false, compactions: 0 };
      this.books.set(sessionID, book);
    }
    const payload = envelope.payload;
    switch (envelope.type) {
      case "turn_started":
        book.turn = [];
        book.running = true;
        return this.get(sessionID);
      case "usage":
        book.turn = runningRows(book.turn, usageRows(payload.snapshot));
        return this.get(sessionID);
      case "progress":
        if (record3(record3(payload.data).context).compacted !== true)
          return;
        book.compactions++;
        return this.get(sessionID);
      case "terminal":
        if (payload.usage)
          book.turn = usageRows(payload.usage);
        if (book.running)
          book.done = addRows(book.done, book.turn);
        book.running = false;
        this.save(sessionID, book);
        return this.get(sessionID);
      default:
        return;
    }
  }
}
var NUMBER = { type: ["number", "null"] };
var ROW = {
  type: "object",
  properties: {
    provider: { type: "string" },
    model: { type: "string" },
    tokensIn: NUMBER,
    tokensOut: NUMBER,
    cacheRead: NUMBER,
    cacheWrite: NUMBER,
    costUSD: NUMBER
  },
  required: ["provider", "model", "tokensIn", "tokensOut", "cacheRead", "cacheWrite", "costUSD"]
};
var USAGE = {
  type: "object",
  properties: {
    sessionID: { type: "string" },
    turn: { type: "array", items: ROW },
    session: { type: "array", items: ROW },
    compactions: { type: "number" }
  },
  required: ["sessionID", "turn", "session", "compactions"]
};
var USAGE_RPC = {
  id: "amplifier",
  methods: {
    usage: {
      input: { type: "object", properties: { sessionID: { type: "string" } }, required: ["sessionID"] },
      output: USAGE
    }
  },
  events: { usage: { schema: USAGE } }
};

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
    const mcp = { current: [] };
    await ctx.mcp.transform((servers) => {
      mcp.current = servers.list().map(([name, config]) => [name, structuredClone(config)]);
    });
    const resolved = await resolve2(ctx, options, mcp);
    logSources(directory, resolved);
    const [installed, face] = await Promise.all([
      commandVersion(options.command, resolved.environment),
      launch(faceSettings(options, directory, resolved)).catch((error) => error)
    ]);
    if (face instanceof Error)
      log(face.message);
    const stale = installed !== "0.25.0";
    if (stale) {
      log(`plugin ${"0.25.0"} runs with amplifier-opencode ${installed ?? "(unknown)"}. ` + "Run: amplifier-opencode install");
    }
    const live = { face: undefined, entries: [], fingerprint: resolved.fingerprint, ready: async () => {} };
    if (!(face instanceof Error))
      show(live, face, options);
    let unserve;
    try {
      unserve = await register2(ctx, live, options, stale);
    } catch (error) {
      live.face?.stop();
      throw error;
    }
    const watching = new AbortController;
    watchEvents(ctx, live, options, directory, mcp, watching.signal);
    return () => {
      unserve();
      watching.abort();
      live.face?.stop();
    };
  }
};
async function resolve2(ctx, options, mcp) {
  const { environment, sources } = faceEnvironment(process.env, await connections(ctx), options);
  const [skills, servers] = await Promise.all([resolvedSkills(ctx), mcpStatuses(ctx)]);
  const { servers: mcp_servers, skipped } = mcpServers(mcp.current, servers);
  const inputs = { skills, mcp_servers };
  const hash = (value) => createHash3("sha256").update(JSON.stringify(value)).digest("hex");
  const fingerprint = {
    environment: hash(Object.entries(environment).sort(([a], [b]) => a.localeCompare(b))),
    inputs: hash(inputs)
  };
  return { environment, sources, inputs, skipped, fingerprint };
}
async function resolvedSkills(ctx) {
  try {
    return skillDirectories((await ctx.skill.list()).data);
  } catch (error) {
    log(`could not read the opencode skills: ${error.message}`);
    return [];
  }
}
async function mcpStatuses(ctx) {
  try {
    return new Map((await ctx.mcp.list()).data.map((server) => [server.name, server.status.status]));
  } catch (error) {
    log(`could not read the opencode MCP servers: ${error.message}`);
    return new Map;
  }
}
function faceSettings(options, directory, resolved) {
  return {
    command: options.command,
    directory,
    environment: resolved.environment,
    approvals: faceApprovals(options.approvals),
    inputs: resolved.inputs
  };
}
async function launch(settings) {
  try {
    return await startFace(settings);
  } catch (error) {
    const { skills, mcp_servers } = settings.inputs;
    if (!skills.length && !mcp_servers.length)
      throw error;
    log(`${error.message} Starting it without opencode's skills and MCP servers.`);
    return startFace({ ...settings, inputs: NO_INPUTS });
  }
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
function logSources(directory, resolved) {
  const names = Object.entries(resolved.sources).map(([name, source]) => `${name} from ${source}`);
  log(`face environment for ${directory}: ${names.join(", ") || "no provider credentials"}`);
  const { skills, mcp_servers } = resolved.inputs;
  const servers = mcp_servers.map((server) => server.name).join(", ") || "none";
  log(`face inputs for ${directory}: ${skills.length} skill directories, MCP servers: ${servers}`);
  if (resolved.skipped.length)
    log(`MCP servers left out for ${directory}: ${resolved.skipped.join(", ")}`);
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
function watchEvents(ctx, live, options, directory, mcp, signal) {
  let queue = Promise.resolve();
  let waiting;
  const enqueue = (work) => {
    queue = queue.then(work).catch((error) => log(`could not update the face for ${directory}: ${error.message}`));
    return queue;
  };
  const follow = (face) => void face.crashed.then((reason) => {
    if (live.face === face && !signal.aborted)
      restart(face, reason);
  });
  const swap = async (face, fingerprint) => {
    if (signal.aborted) {
      face.stop();
      return;
    }
    const previous = live.face;
    show(live, face, options);
    live.fingerprint = fingerprint;
    follow(face);
    await reload();
    previous?.retire();
  };
  const reload = async () => {
    await ctx.provider.reload();
    await ctx.model.reload();
    await ctx.agent.reload();
  };
  const update = async () => {
    if (signal.aborted)
      return;
    const resolved = await resolve2(ctx, options, mcp);
    if (signal.aborted)
      return;
    const changed = [
      ...resolved.fingerprint.environment === live.fingerprint.environment ? [] : ["connections"],
      ...resolved.fingerprint.inputs === live.fingerprint.inputs ? [] : ["skills or MCP servers"]
    ];
    if (!changed.length)
      return;
    log(`opencode ${changed.join(" and ")} changed; restarting the face for ${directory}`);
    logSources(directory, resolved);
    try {
      await swap(await launch(faceSettings(options, directory, resolved)), resolved.fingerprint);
    } catch (error) {
      log(`${live.face ? "kept the running face" : "no face runs"} for ${directory}: ${error.message}`);
    }
  };
  const refresh = () => {
    waiting ??= enqueue(() => {
      waiting = undefined;
      return update();
    });
    return waiting;
  };
  const restart = (crashed, reason) => enqueue(async () => {
    if (live.face !== crashed || signal.aborted)
      return;
    log(`the face for ${directory} ${reason}; restarting it`);
    const resolved = await resolve2(ctx, options, mcp);
    logSources(directory, resolved);
    try {
      await swap(await launch(faceSettings(options, directory, resolved)), resolved.fingerprint);
    } catch (error) {
      log(error.message);
      live.face = undefined;
      live.entries = [];
      live.fingerprint = resolved.fingerprint;
      await reload();
    }
  });
  live.ready = refresh;
  if (live.face)
    follow(live.face);
  (async () => {
    try {
      for await (const event of ctx.event.subscribe({ signal })) {
        switch (event.type) {
          case "credential.updated":
          case "credential.switched":
          case "skill.updated":
          case "mcp.status.changed":
          case "config.updated":
            refresh();
            break;
          case "session.execution.interrupted":
            interrupt(event.data.sessionID);
        }
      }
    } catch (error) {
      if (!signal.aborted)
        log(`stopped following opencode changes for ${directory}: ${error.message}`);
    }
  })();
  refresh();
}
var PERMISSIONS = [
  { action: "*", resource: "*", effect: "allow" },
  { action: "read", resource: "*.env", effect: "ask" },
  { action: "read", resource: "*.env.*", effect: "ask" },
  { action: "read", resource: "*.env.example", effect: "allow" },
  ...["websearch", "question", "todowrite", "subagent", "skill", "execute", "opencode_*"].map((action) => ({ action, resource: "*", effect: "deny" }))
];
async function register2(ctx, live, options, stale) {
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
      agent.steps = undefined;
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
  const book = new UsageBook(path5.join(stateDirectory(), "usage"));
  let usageEvents;
  try {
    const registration = await ctx.rpc.register(USAGE_RPC, {
      usage: async (input) => book.get(input.sessionID)
    });
    usageEvents = registration.events;
  } catch (error) {
    log(`could not offer Amplifier usage to the TUI: ${error.message}`);
  }
  await ctx.tool.transform((tools) => {
    for (const id of PROXY_TOOLS) {
      tools.update(id, (tool) => {
        const original = tool.execute;
        tool.execute = async (input, context2) => {
          if (!registered(context2.id))
            return original(input, context2);
          return await wait(context2.id, context2.signal);
        };
      });
    }
  });
  const directory = ctx.location.directory;
  const context = {
    face: () => live.ready().then(() => live.face),
    asks,
    assert: (input) => ctx.permission.assert({ ...input, sessionID: input.sessionID }),
    observe: (sessionID, envelope) => {
      const usage = book.observe(sessionID, envelope);
      if (usage)
        usageEvents?.emit("usage", usage).catch(() => {});
    }
  };
  await ctx.aisdk.hook("language", (event) => {
    event.language = new AmplifierLanguageModel(String(event.model.modelID ?? event.model.id), directory, context);
  }, SCOPE);
  await ctx.session.hook("context", (event) => {
    const tools = event.tools;
    event.tools = Object.fromEntries(PROXY_TOOLS.map((name) => [name, tools[name] ?? { description: name, input: { type: "object" } }]));
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
  return serve(directory, context);
}
var src_default = plugin;
export {
  src_default as default,
  createAmplifier
};
