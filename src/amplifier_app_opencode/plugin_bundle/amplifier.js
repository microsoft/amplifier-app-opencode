// @bun
// amplifier-opencode-plugin 0.23.1

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
    const environment = { ...process.env, ...options.environment };
    const [installed, face] = await Promise.all([
      commandVersion(options.command, environment),
      startFace({ command: options.command, directory, environment, approvals: options.approvals }).catch((error) => error)
    ]);
    if (face instanceof Error) {
      log(face.message);
      throw face;
    }
    const stale = installed !== "0.23.1";
    if (stale) {
      log(`plugin ${"0.23.1"} runs with amplifier-opencode ${installed ?? "(unknown)"}. ` + "Run: amplifier-opencode install");
    }
    try {
      await register(ctx, face, options.defaultModel, options.setDefaultAgent, stale);
    } catch (error) {
      face.stop();
      throw error;
    }
    return () => face.stop();
  }
};
async function register(ctx, face, defaultModel, setDefaultAgent, stale) {
  const entries = modelEntries(face.models);
  await ctx.provider.transform((providers) => {
    providers.update(PROVIDER, (provider) => {
      provider.name = stale ? "Amplifier (run: amplifier-opencode install)" : "Amplifier";
      provider.activation = "enabled";
      provider.package = "@opencode/ai/providers/openai-compatible";
      provider.settings = { baseURL: face.baseURL, apiKey: face.token };
    });
    for (const entry of entries) {
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
    const key = defaultModelKey(entries, defaultModel);
    if (defaultModel && key === DEFAULT_MODEL_KEY && defaultModel !== DEFAULT_MODEL_KEY) {
      log(`defaultModel ${defaultModel} is not a model the face lists; using the Amplifier default`);
    }
    await ctx.model.transform((models) => models.default.set(PROVIDER, key));
  }
  await ctx.agent.transform((agents) => {
    agents.update(AGENT, (agent) => {
      agent.name = AGENT;
      agent.description = "Amplifier Agent. Runs its own tools in this directory.";
      agent.mode = "primary";
      agent.hidden = false;
      agent.model = { providerID: PROVIDER, id: defaultModelKey(entries, defaultModel) };
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
