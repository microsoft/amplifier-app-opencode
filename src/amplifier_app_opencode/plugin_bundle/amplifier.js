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
  approvals: "allow",
  environment: {},
  command: "amplifier-opencode"
};
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
  return {
    options: {
      defaultModel: pick("defaultModel", isString, DEFAULT_OPTIONS.defaultModel),
      setDefaultAgent: pick("setDefaultAgent", (input) => typeof input === "boolean", DEFAULT_OPTIONS.setDefaultAgent),
      approvals: pick("approvals", (input) => input === "allow" || input === "deny", DEFAULT_OPTIONS.approvals),
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
function withoutReasoning(body) {
  if (typeof body !== "object" || body === null || !("messages" in body) || !Array.isArray(body.messages))
    return body;
  return {
    ...body,
    messages: body.messages.map((message) => {
      if (typeof message !== "object" || message === null || !("reasoning_content" in message))
        return message;
      const { reasoning_content: _, ...rest } = message;
      return rest;
    })
  };
}

// src/index.ts
var PROVIDER = "amplifier";
var AGENT = "amplifier";
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
        approvals: options.approvals
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
      await register(ctx, live, options.defaultModel, options.setDefaultAgent, stale);
    } catch (error) {
      live.face?.stop();
      throw error;
    }
    const watching = new AbortController;
    watchConnections(ctx, live, options, directory, watching.signal);
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
function watchConnections(ctx, live, options, directory, signal) {
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
        approvals: options.approvals
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
      }
    } catch (error) {
      if (!signal.aborted)
        log(`stopped following opencode connections for ${directory}: ${error.message}`);
    }
  })();
  refresh();
}
async function register(ctx, live, defaultModel, setDefaultAgent, stale) {
  await ctx.provider.transform((providers) => {
    const face = live.face;
    if (!face)
      return;
    providers.update(PROVIDER, (provider) => {
      provider.name = stale ? "Amplifier (run: amplifier-opencode install)" : "Amplifier";
      provider.activation = "enabled";
      provider.package = "@opencode/ai/providers/openai-compatible";
      provider.settings = { baseURL: face.baseURL, apiKey: face.token };
    });
    for (const entry of live.entries) {
      providers.models.update(PROVIDER, entry.key, (model) => {
        model.modelID = entry.modelID;
        model.name = entry.name;
        model.limit = { context: 0, output: 0 };
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
      agent.permissions = [{ action: "*", resource: "*", effect: "deny" }];
    });
    if (setDefaultAgent)
      agents.default(AGENT);
  });
  await ctx.session.hook("model.request", (event) => {
    if (event.kind === "primary")
      event.headers["Amplifier-Session-Id"] = sessionHeader(event.sessionID);
  }, SCOPE);
  await ctx.session.hook("context", (event) => {
    delete event.options.maxTokens;
    event.tools = {};
  }, SCOPE);
  await ctx.session.hook("http.request", async (event) => {
    const text = await event.request.clone().text();
    if (!text.includes('"reasoning_content"'))
      return;
    const headers = new Headers(event.request.headers);
    headers.delete("content-length");
    event.request = new Request(event.request.url, {
      method: event.request.method,
      headers,
      body: JSON.stringify(withoutReasoning(JSON.parse(text))),
      signal: event.request.signal
    });
  }, SCOPE);
  await ctx.session.hook("title", (event) => {
    const user = event.messages.find((message) => message.role === "user");
    const text = (user?.content ?? []).map((part) => part.type === "text" ? part.text : "").join(`
`);
    event.result = titleFrom(text) ?? "Amplifier session";
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
  src_default as default
};
