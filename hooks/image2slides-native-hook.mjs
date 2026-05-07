#!/usr/bin/env node
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "fs";
import { dirname, resolve } from "path";
import { homedir } from "os";

const IMAGE2SLIDES_CONTEXT = [
  "Image2Slides Desktop response contract:",
  "- When Image2Slides is triggered from a simple Codex Desktop message, first give the user a short visible guide: what the workflow will do, what inputs are required, and what outputs will be produced.",
  "- Do not make the workflow feel like a black box. Before project creation, explain the checkpoints: intake -> wiki/boundary -> native image_gen completed -> native image_gen backgrounds -> editable PPTX -> QA.",
  "- If the request is underspecified, ask for the missing required inputs instead of creating files.",
  "",
  "Image2Slides intake gate:",
  "- Before creating files or generating images, confirm these required user inputs are explicit: style/tone, aspect ratio, slide count, purpose, scene, and knowledge-base materials.",
  "- If any required input is missing, ask only for the missing fields and stop before `image2slides init`.",
  "- Use this concise user-facing intake prompt when needed: `To start Image2Slides, please provide: 1. base style/color tone; 2. aspect ratio; 3. slide count; 4. purpose (speech/showcase); 5. presentation scene; 6. knowledge-base files or pasted materials. Also note any data/results/figures that must remain exact.`",
  "",
  "Image2Slides native image_gen guard:",
  "- `/image2slides` must produce two GPT-image-2 batches before PPTX work: completed full-slide references, then text-free background edits from those completed images.",
  "- Do not skip the background edit pass. Do not create `completed/` or `background/` from PPTX/PDF renders, screenshots, local templates, or deterministic drawing.",
  "- Native registration must include a receipt manifest proving each PNG was copied from Codex native `image_gen` under `$CODEX_HOME/generated_images/.../ig_*.png`.",
  "- After `image2slides queue`, open `reports/native_imagegen_run.md` and call native `image_gen` for every completed prompt before any PPTX construction.",
  "- Then call native `image_gen` edit for every background prompt, using the matching completed image as the edit input.",
  "- Before `compose-source-locked`, `analyze`, `build-pptx`, `qa`, `audit-layout`, or `audit-boundaries`, require both provenance manifests: `completed/.image2slides_completed_provenance.json` and `background/.image2slides_background_provenance.json`.",
  "- If those manifests are missing or stale, restart from the first invalid image_gen stage instead of continuing downstream.",
].join("\n");

const STATE_TTL_MS = 12 * 60 * 60 * 1000;
const STATE_PATH = process.env.IMAGE2SLIDES_HOOK_STATE ||
  resolve(process.env.CODEX_HOME || resolve(homedir(), ".codex"), "state/image2slides-native-hook.json");

const DOWNSTREAM_COMMANDS = new Set([
  "compose-source-locked",
  "analyze",
  "build-pptx",
  "qa",
  "audit-layout",
  "audit-boundaries",
]);

function safeString(value) {
  return typeof value === "string" ? value : "";
}

function safeObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function nowMs() {
  return Date.now();
}

function workspaceKey(cwd) {
  return resolve(cwd || process.cwd());
}

function readState() {
  const state = readJson(STATE_PATH);
  return safeObject(state);
}

function writeState(state) {
  try {
    mkdirSync(dirname(STATE_PATH), { recursive: true });
    writeFileSync(STATE_PATH, `${JSON.stringify(state, null, 2)}\n`, "utf-8");
  } catch {
    // Hooks must never fail unrelated user work because state persistence failed.
  }
}

function activeRecord(cwd) {
  const key = workspaceKey(cwd);
  const state = readState();
  const record = safeObject(state[key]);
  if (!record.active || Number(record.expires_at || 0) < nowMs()) return {};
  return record;
}

function activateRecord(cwd, patch = {}) {
  const key = workspaceKey(cwd);
  const state = readState();
  const previous = safeObject(state[key]);
  state[key] = {
    ...previous,
    ...patch,
    active: true,
    workspace: key,
    updated_at: new Date().toISOString(),
    expires_at: nowMs() + STATE_TTL_MS,
  };
  writeState(state);
  return state[key];
}

function deactivateRecord(cwd, patch = {}) {
  const key = workspaceKey(cwd);
  const state = readState();
  const previous = safeObject(state[key]);
  state[key] = {
    ...previous,
    ...patch,
    active: false,
    workspace: key,
    updated_at: new Date().toISOString(),
    expires_at: nowMs(),
  };
  writeState(state);
  return state[key];
}

function readEventName(payload) {
  return safeString(payload.hook_event_name ?? payload.hookEventName ?? payload.event ?? payload.name).trim();
}

function readPrompt(payload) {
  return safeString(payload.prompt ?? payload.input ?? payload.user_prompt ?? payload.userPrompt ?? payload.text);
}

function shouldInjectPromptContext(prompt) {
  if (/(^|\s)\/image2slides\b/i.test(prompt) || /\bimage2slides\b/i.test(prompt)) return true;
  const material = "(?:pdf|materials?|knowledge(?:-base)?|notes?|images?|figures?|source files?)";
  const deck = "(?:slides?|pptx?|powerpoint|deck)";
  return (
    new RegExp(`\\b(?:turn|convert|make|create|build)\\b[\\s\\S]{0,80}\\b${material}\\b[\\s\\S]{0,80}\\b${deck}\\b`, "i").test(prompt) ||
    new RegExp(`\\b${deck}\\b[\\s\\S]{0,80}\\b(?:from|using|based on)\\b[\\s\\S]{0,80}\\b${material}\\b`, "i").test(prompt)
  );
}

function readToolCommand(payload) {
  const input = safeObject(payload.tool_input ?? payload.toolInput ?? payload.input);
  return safeString(
    input.command ??
      input.cmd ??
      input.script ??
      input.input ??
      payload.command ??
      payload.cmd,
  );
}

function readToolName(payload) {
  return safeString(payload.tool_name ?? payload.toolName ?? payload.tool ?? payload.name);
}

function looksLikeShellTool(payload) {
  const tool = readToolName(payload);
  if (!tool) return true;
  return /\b(Bash|Shell|exec|exec_command|functions\.exec_command)\b/i.test(tool);
}

function parseImage2SlidesCommand(command) {
  const match = command.match(
    /(?:^|[\s;&|])(?:(?:python3?|python)\s+)?(?:[^\s;&|]*image2slides(?:\.py)?|image2slides)\s+([a-z][a-z0-9-]*)\b/i,
  );
  return match ? match[1].toLowerCase() : "";
}

function parseFlag(command, flag) {
  const escaped = flag.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = new RegExp(
    `(?:^|\\s)${escaped}(?:=|\\s+)(?:"([^"]+)"|'([^']+)'|([^\\s;&|]+))`,
    "i",
  );
  const match = command.match(pattern);
  return match ? match[1] ?? match[2] ?? match[3] ?? "" : "";
}

function hasFlag(command, flag) {
  const escaped = flag.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`(?:^|\\s)${escaped}(?:\\s|=|$)`, "i").test(command);
}

function methodRequiresNativeReceipt(method) {
  const raw = safeString(method).toLowerCase();
  if (!raw || raw.startsWith("test_") || raw.startsWith("api_")) return false;
  if (raw.endsWith("_with_source_locked_patch")) return false;
  return raw.includes("native_image_gen");
}

function defaultMethodForSubcommand(subcommand) {
  if (subcommand === "register-completed") return "registered_native_image_gen";
  if (subcommand === "register-background") return "registered_native_image_gen_edit";
  return "";
}

function methodFromCommand(command, subcommand) {
  return parseFlag(command, "--method") || defaultMethodForSubcommand(subcommand);
}

function projectPathFromCommand(command, cwd) {
  const raw = parseFlag(command, "--project");
  if (!raw) return "";
  return resolve(cwd || process.cwd(), raw);
}

function readJson(path) {
  try {
    return JSON.parse(readFileSync(path, "utf-8"));
  } catch {
    return null;
  }
}

function expectedSlideCount(project) {
  const projectJson = readJson(resolve(project, "project.json"));
  const raw = projectJson?.slide_count;
  const count = Number.parseInt(String(raw ?? ""), 10);
  return Number.isInteger(count) && count > 0 ? count : null;
}

function manifestPath(project, kind) {
  return resolve(
    project,
    kind,
    kind === "completed"
      ? ".image2slides_completed_provenance.json"
      : ".image2slides_background_provenance.json",
  );
}

function validateManifest(project, kind) {
  const path = manifestPath(project, kind);
  if (!existsSync(path)) {
    return { ok: false, reason: `missing ${kind} provenance manifest: ${path}` };
  }
  const manifest = readJson(path);
  if (!manifest || manifest.generator !== "gpt-image-2") {
    return { ok: false, reason: `${kind} provenance must declare generator gpt-image-2: ${path}` };
  }
  const method = safeString(manifest.method);
  const source = safeString(manifest.source);
  if (/pptx|powerpoint export|pdf screenshot|render|screenshot|local template|deterministic/i.test(`${method} ${source}`)) {
    return { ok: false, reason: `${kind} provenance points to a forbidden non-image_gen source: ${path}` };
  }
  if (methodRequiresNativeReceipt(method)) {
    const receipt = safeObject(manifest.native_imagegen_manifest);
    if (receipt.phase !== kind || !Array.isArray(receipt.verified_copies) || receipt.verified_copies.length === 0) {
      return {
        ok: false,
        reason: `${kind} native provenance lacks a verified native image_gen receipt manifest: ${path}`,
      };
    }
  }
  const count = expectedSlideCount(project);
  if (count !== null && Array.isArray(manifest.slides) && manifest.slides.length !== count) {
    return { ok: false, reason: `${kind} provenance slide count does not match project.json: ${path}` };
  }
  return { ok: true, reason: "ok" };
}

function nativeStageStatus(project) {
  if (!project) return { hasProject: false, completed: { ok: false }, background: { ok: false } };
  const completed = validateManifest(project, "completed");
  const background = validateManifest(project, "background");
  return { hasProject: true, completed, background };
}

function looksLikeBypassPptxBuild(command) {
  return (
    /\bpython-pptx\b|\bpptxgenjs\b|\bfrom\s+pptx\b|\bimport\s+pptx\b|\bPresentation\s*\(/i.test(command) ||
    /\b(soffice|libreoffice)\b[^\n]*(--convert-to|--headless)/i.test(command) ||
    /\bpdftoppm\b/i.test(command)
  );
}

function block(reason) {
  return {
    decision: "block",
    reason: `Image2Slides workflow gate: ${reason}\n\n${IMAGE2SLIDES_CONTEXT}`,
  };
}

function preToolUseOutput(payload) {
  if (!looksLikeShellTool(payload)) return null;
  const command = readToolCommand(payload);
  const cwd = safeString(payload.cwd ?? payload.working_directory ?? payload.workingDirectory) || process.cwd();
  const record = activeRecord(cwd);
  const isImage2SlidesCommand = /\bimage2slides\b/i.test(command);
  if (!isImage2SlidesCommand && !record.active) return null;

  if (!isImage2SlidesCommand) {
    const project = safeString(record.project);
    if (project && looksLikeBypassPptxBuild(command)) {
      const status = nativeStageStatus(project);
      if (!status.completed.ok || !status.background.ok) {
        return block(
          "Image2Slides is active for this workspace, but native GPT-image-2 completed/background provenance is not valid yet. " +
            "Do not bypass the workflow with python-pptx/PPTX rendering. Run native image_gen from the queued prompts, register both native manifests, then build.",
        );
      }
    }
    return null;
  }

  const subcommand = parseImage2SlidesCommand(command);
  if (!subcommand) return null;

  const project = projectPathFromCommand(command, cwd);
  if (project) activateRecord(cwd, { project, last_subcommand: subcommand });
  if ((DOWNSTREAM_COMMANDS.has(subcommand) || subcommand === "register-background") && !project) {
    return block(`\`${subcommand}\` requires an explicit --project path so the hook can verify image_gen provenance.`);
  }

  if (subcommand === "register-completed" || subcommand === "register-background") {
    const method = methodFromCommand(command, subcommand);
    if (methodRequiresNativeReceipt(method) && !hasFlag(command, "--native-manifest")) {
      return block(
        `\`${subcommand}\` with native method \`${method}\` requires --native-manifest. ` +
          "Do not register locally drawn, PPTX-rendered, or screenshot images as native GPT-image-2 output.",
      );
    }
  }

  if (subcommand === "imagegen" && /\s--phase(?:=|\s+)background\b/i.test(command)) {
    if (!project) return block("background imagegen requires --project so completed provenance can be verified first.");
    const completed = validateManifest(project, "completed");
    if (!completed.ok) return block(`cannot run background image_gen before valid completed references: ${completed.reason}`);
    return null;
  }

  if (subcommand === "register-background") {
    const completed = validateManifest(project, "completed");
    if (!completed.ok) return block(`cannot register background before valid completed references: ${completed.reason}`);
    return null;
  }

  if (DOWNSTREAM_COMMANDS.has(subcommand)) {
    const completed = validateManifest(project, "completed");
    if (!completed.ok) return block(`cannot run \`${subcommand}\` before GPT-image-2 completed references are registered: ${completed.reason}`);
    const background = validateManifest(project, "background");
    if (!background.ok) return block(`cannot run \`${subcommand}\` before GPT-image-2 text-free background edits are registered: ${background.reason}`);
  }

  return null;
}

function stopOutput(payload) {
  const cwd = safeString(payload.cwd ?? payload.working_directory ?? payload.workingDirectory) || process.cwd();
  const record = activeRecord(cwd);
  const project = safeString(record.project);
  if (!record.active || !project) return null;
  const status = nativeStageStatus(project);
  if (!status.completed.ok) {
    return block(
      `cannot finish Image2Slides while completed native image_gen references are invalid: ${status.completed.reason}. ` +
        "Call native image_gen for every completed prompt, copy ig_*.png outputs into completed/, write the receipt manifest, then register-completed.",
    );
  }
  if (!status.background.ok) {
    return block(
      `cannot finish Image2Slides while background native image_gen edits are invalid: ${status.background.reason}. ` +
        "Call native image_gen edit for every background prompt, copy ig_*.png outputs into background/, write the receipt manifest, then register-background.",
    );
  }
  deactivateRecord(cwd, { completed_at: new Date().toISOString() });
  return null;
}

async function readStdinJson() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(Buffer.from(chunk));
  const raw = Buffer.concat(chunks).toString("utf-8").trim();
  return raw ? safeObject(JSON.parse(raw)) : {};
}

const payload = await readStdinJson();
const hookEventName = readEventName(payload);

if (hookEventName === "UserPromptSubmit") {
  const prompt = readPrompt(payload);
  if (shouldInjectPromptContext(prompt)) {
    const cwd = safeString(payload.cwd ?? payload.working_directory ?? payload.workingDirectory) || process.cwd();
    activateRecord(cwd, { prompt_seen: true });
    process.stdout.write(
      `${JSON.stringify({
        hookSpecificOutput: {
          hookEventName,
          additionalContext: IMAGE2SLIDES_CONTEXT,
        },
      })}\n`,
    );
  }
} else if (hookEventName === "PreToolUse") {
  const output = preToolUseOutput(payload);
  if (output) process.stdout.write(`${JSON.stringify(output)}\n`);
} else if (hookEventName === "Stop") {
  const output = stopOutput(payload);
  if (output) process.stdout.write(`${JSON.stringify(output)}\n`);
}
