/* eslint-disable @typescript-eslint/no-require-imports -- CommonJS harness uses Node built-ins and the installed TypeScript compiler. */
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const base = path.resolve(__dirname, "../../../..");
const ts = require(path.join(base, "frontend/node_modules/typescript"));
const file = (name) => path.join(base, "frontend/src", name);
const componentNames = new Proxy({}, { get: (_, name) => String(name) });

// Exercise the real service/component handlers without a browser, network, or
// another test dependency. Hook state and JSX are controlled by this harness;
// DOM behavior such as native fieldset/inert handling still needs browser QA.
function load(name, mocks, globals = {}) {
  const source = fs.readFileSync(file(name), "utf8")
    .replaceAll("import.meta.env.DEV", "false");
  const code = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.React,
      esModuleInterop: true,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  const exports = {};
  vm.runInNewContext(code, {
    exports,
    require: (moduleName) => mocks[moduleName] ?? componentNames,
    console: { error() {} },
    Set, Map, Date, Event, Error,
    sessionStorage: { getItem: () => null },
    ...globals,
  }, { filename: name });
  return exports;
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return { promise, resolve, reject };
}

function hooks(initialConfig) {
  const slots = [];
  let cursor = 0;
  const react = {
    createElement: (type, props, ...children) => ({
      type, props: { ...props, children },
    }),
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) {
        slots[index] = index === 0 && initialConfig
          ? initialConfig
          : typeof initial === "function" ? initial() : initial;
      }
      return [slots[index], (value) => {
        slots[index] = typeof value === "function" ? value(slots[index]) : value;
      }];
    },
    useRef(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { current: initial };
      return slots[index];
    },
    useMemo: (compute) => compute(),
    useCallback: (callback) => callback,
    useEffect() {},
  };
  return {
    react,
    slots,
    render(component) {
      cursor = 0;
      return component();
    },
  };
}

function elements(node, output = []) {
  if (Array.isArray(node)) {
    node.forEach((child) => elements(child, output));
  } else if (node && typeof node === "object" && node.props) {
    output.push(node);
    Object.values(node.props).forEach((child) => elements(child, output));
  }
  return output;
}

function button(tree, text) {
  return elements(tree).find((element) =>
    element.type === "Button" && element.props.children.flat(Infinity).includes(text));
}

function configFixture() {
  // The test needs settings sections to render every tab, without reading any
  // personal configuration or depending on unrelated option defaults.
  const source = fs.readFileSync(file("pages/ConfigurationPage.tsx"), "utf8");
  const config = {};
  for (const match of source.matchAll(/config\??\.([a-z_]+)\.([A-Za-z_]+)/g)) {
    (config[match[1]] ??= {})[match[2]] = false;
  }
  config.general.media_dirs = [];
  config.general.home_widgets = [];
  config.general.is_binary = true;
  config.scan.IMAGE_SUFFIXES = [];
  config.scan.VIDEO_SUFFIXES = [];
  config.tagging.custom_tags = [];
  config.face_recognition.preset = "normal";
  return config;
}

function configurationPage(extraServices = {}) {
  const config = configFixture();
  const state = hooks(config);
  let reloads = 0;
  const profiles = { active_path: "/old", profiles: [{ path: "/old", name: "Old" }] };
  const service = {
    saveConfig: async (accepted) => accepted,
    reloadConfig: async () => config,
    listProfiles: async () => profiles,
    createProfile: async () => {},
    ...extraServices,
  };
  const Page = load("pages/ConfigurationPage.tsx", {
    react: state.react,
    "@mui/material": componentNames,
    "../services/config": service,
    "../homeWidgets": { HOME_WIDGETS: [], mergeHomeWidgets: () => [] },
  }, {
    window: { location: { reload() { reloads++; } } },
  }).default;
  state.render(Page);
  state.slots[1] = false; // Configuration has finished loading.
  state.slots[8] = profiles;
  return { config, render: () => state.render(Page), reloads: () => reloads };
}

function taskPanel(status, cancelTask = async () => {}) {
  const state = hooks();
  const Panel = load("components/TasksPanel.tsx", {
    react: state.react,
    "@mui/material": componentNames,
    "../config": { default: { PRESENTATION_MODE: false } },
    "../services/taskActions": { cancelTask },
    "../utils/taskFormat": { formatTaskStep: (step) => step },
    "../TaskEventsContext": {
      useTaskEvents: () => ({
        activeTasks: [{ id: "task-1", status, task_type: "scan", processed: 0, total: 0 }],
        recentTasks: [],
        lastCompletedTasks: [],
        forceRefresh: async () => {},
      }),
    },
  }).default;
  return () => state.render(() => Panel({ isActive: true }));
}

test("presentation_activation_uses_accepted_snapshot_without_protected_read", async () => {
  const accepted = {
    general: { presentation_mode: true, enable_people: true, meme_mode: false },
    repairs: { enabled: false },
  };
  const calls = [];
  const window = { runtimeConfig: {}, dispatchEvent() {} };
  const service = load("services/config.ts", { "../config": { API: "" } }, {
    window,
    fetch: async (url, options) => {
      calls.push([url, options?.method]);
      return options?.method === "POST" ? { ok: true } : { ok: false };
    },
  });
  assert.equal(await service.reloadConfig(accepted), accepted);
  assert.equal(calls.length, 1);
  assert.equal(window.runtimeConfig.VITE_API_PRESENTATION_MODE, "true");
  assert.equal(await service.getConfig(), accepted);
  assert.equal(calls.length, 1);
});

test("configuration_controls_locked_while_save_pending", async () => {
  const pending = deferred();
  const page = configurationPage({ saveConfig: () => pending.promise });
  const saving = button(page.render(), "Save").props.onClick();
  const locked = elements(page.render()).find((element) =>
    element.props.component === "fieldset" && element.props.disabled && element.props.inert === "");
  assert.ok(locked, "settings must disable native controls and make custom controls inert during Save");
  pending.resolve(page.config);
  await saving;
  assert.ok(!elements(page.render()).some((element) =>
    element.props.component === "fieldset" && element.props.disabled));
});

test("profile_creation_reloads_to_clear_prior_library_requests_and_caches", async () => {
  const page = configurationPage();
  elements(page.render()).find((element) =>
    element.type === "TextField" && element.props.label === "Directory")
    .props.onChange({ target: { value: "/new" } });
  await button(page.render(), "Create").props.onClick();
  assert.equal(page.reloads(), 1, "new library activation must discard old lists and requests");
});

test("pending_tasks_have_cancel_action", () => {
  assert.ok(button(taskPanel("pending")(), "Cancel"), "queued task must expose Cancel");
});

test("cancel_is_single_flight_and_shows_failure_feedback", async () => {
  const pending = deferred();
  let calls = 0;
  const render = taskPanel("running", () => {
    calls++;
    return pending.promise;
  });
  const cancel = button(render(), "Cancel");
  const first = cancel.props.onClick();
  const second = cancel.props.onClick();
  assert.equal(calls, 1, "same-tick repeated clicks must issue one cancellation");
  assert.ok(button(render(), "Cancelling…")?.props.disabled);
  pending.reject(new Error("Cancellation unavailable"));
  await Promise.all([first, second]);
  assert.ok(elements(render()).some((element) =>
    element.type === "Alert" && element.props.severity === "error" &&
    element.props.children.includes("Cancellation unavailable")),
  "request failure must be visible in snackbar");
  assert.equal(button(render(), "Cancel").props.disabled, false);
});
