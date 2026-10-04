# Mamba Premium UI/UX Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Mamba's existing React/Electron frontend into a premium, calm, distinctly-Mamba desktop assistant experience — one coherent design system, a redesigned living-field Orb with the full state model, a quiet outcome-first conversation surface, and native-feeling approval/toast/settings/browser/window chrome — without touching the backend architecture or changing any existing behavior contract.

**Architecture:** This is a **presentation-layer** redesign of the existing `src/` React app and the `electron/` shell that hosts it. The single stylesheet (`src/index.css`) is extended into a real token layer (color, type, spacing, radius, elevation, motion) that every existing component consumes; no new UI framework, no CSS-in-JS, no state-management layer. The Orb keeps its Three.js pipeline (`src/orb/OrbRenderer.ts` + `shaders/particle.ts`) and gains atmosphere, per-state motion and two additional **derived** visual phases (`executing`, `verification`) that are read from the **already-existing** `progress` milestone channel (`core/brain.py:1227,1526` → `api/server.py:226` → `src/audio.ts:316`), so no new transport field, no new state machine and no backend edit is introduced. Conversation keeps the existing `TranscriptEntry[]` shape and the Phase 4 provisional-streaming reconciliation rules (`src/MambaApp.tsx:62-130`) untouched; only how entries are *rendered* changes. Transport, wake, permissions and lifecycle contracts (`window.mambaDesktop`, `/live`) are consumed exactly as today.

**Tech Stack:** React 18.3 + TypeScript 5.6 (strict, `noEmit`) + Vite 6; Three.js 0.176 (Orb, GLSL1 `ShaderMaterial`); `motion` 11 (`motion/react`); `lucide-react` 0.468; Electron 44 shell (`electron/*.cjs`, context-isolated `preload.cjs`). **No Tailwind, no markdown renderer, no frontend test runner** — `node_modules` is installed and `@vitejs/plugin-react` is *not* (no Fast Refresh: every visual check requires a reload). Validation gates for this project are therefore: `npx tsc --noEmit` (type contract), `npm run build` (bundle), `npm run dev` + browser screenshots (visual), and `npm run electron:test` (the shell's existing orb-state smoke test, `electron/main.cjs:632-638`). Do **not** add a JS test framework for visual work (Spec §25).

**Spec:** `C:\Users\Ḥ\.qoder\tmp\C--mamba\attachments\74c3fcd0-a0e3-4abc-9a7b-47e7b918cdee\16ee5525-38e3-446f-9dec-fc476bff3cf1.txt` (MAMBA PREMIUM UI / UX REDESIGN, §1–§26). This plan argues from that spec; executors read both.

## Global Constraints

Copied from the spec; they apply to **every** task below.

- **No backend redesign.** Do not modify `core/`, `agents/`, `tasks/`, `skills/`, `models/`, `permissions/`, `verification/`, `memory/` (Spec §2). No new orchestration layer, UI agent, model system, memory system, permission system, or state-management architecture. Only a *tiny* frontend integration change is permitted where the spec demands it (Task 8's orb-phase derivation from an existing channel).
- **Preserve existing behavior.** Streaming reconciliation, cancellation, barge-in, wake arming, approval round-trip (`{type:"text",text:"yes"|"no"}`), and the `window.mambaDesktop` bridge contract stay functionally identical (Spec §15, §16, docs/ARCHITECTURE.md §16/§20/§21).
- **Do not weaken permissions.** `SudoPopup` stays an explicit, user-clicked approval gate (Spec §15). Redesign its visuals only; never auto-approve, never dismiss on backdrop click.
- **Orb state model:** `idle`, `listening`, `thinking`, `speaking`, `permission`, `error` are never renamed or removed (docs/ARCHITECTURE.md §16 lists them as exact). `executing` and `verification` are **added** as presentation-only derived phases (Spec §11); they never enter `LiveState` or lifecycle/busy logic.
- **"Expose outcomes, not machinery"** (Spec §4): no model/provider/agent/tool/pipeline names in user-visible copy. Status text is natural language ("Listening…", "Working…", "Checking…").
- **Aesthetic floor** (Spec §5–§6, §10, §12): charcoal near-black foundation, restrained cool cyan/blue/violet accent energy, hairline borders, depth from spacing/typography/motion rather than effects; no neon overload, no rainbow gradients, no glassmorphism walls, no giant rounded cards, no mascot/robot face, no HUD, no text inside the Orb.
- **No new npm dependencies** (Spec §21, §24). Nothing is installed; fonts stay on the system stack (Spec §6 — no network font in a desktop app; `index.html` loads no fonts today).
- **No dead code shipped.** Every removed/renamed class, prop, and state field is verified unreferenced before deletion; the two currently-unimported shaders (`src/orb/shaders/sphere.ts`, `src/orb/shaders/backdrop.ts`) and the unreferenced `src/TextChatFallback.tsx` are resolved by Tasks 9 and 14.
- **Reduced motion respected** globally (Spec §12) and `MambaSettings.animations` becomes the in-app master toggle (Spec §18: Settings must avoid dead/technical clutter).
- **Git:** the implementer runs **no** git commands at all — no add, commit, push, stash, diff or log. The user performs every commit. "Checkpoint" steps below mean: *stop and hand back to the user*.
- **No destructive desktop actions.** Visual validation uses `npm run dev` in a browser tab and the existing Electron smoke test; never automate or close user-owned windows.
- **Plan/report location:** this directory (`docs/superpowers/plans/`).

---

## File Structure

Existing files are edited in place; only four new files appear, each with one responsibility.

| File | Responsibility | Change |
| :--- | :--- | :--- |
| `src/index.css` | The whole design system: tokens (`:root`), reset, and every component section (`header`, `stage`, `composer`, `thread`, `presence`, `sudo`, `toast`, `settings`, `browser`, `compact`, `responsive`) | Extend `:root`; rewrite sections onto tokens |
| `src/motionPrefs.ts` | **New.** `prefersReducedMotion()` + `useMotionEnabled(settings)` — the one source of truth for whether anything may animate | Create |
| `src/ThreadEntry.tsx` | **New.** Renders one `TranscriptEntry` in two densities (`rail`, `stage`); the only conversation renderer in the app | Create |
| `src/PresencePreview.tsx` | **New.** Dev/QA grid of every Orb phase, mounted only via `?mode=preview`, used by the visual validation steps | Create |
| `src/orb/shaders/haze.ts` | **New.** GLSL1 vertex+fragment pair for the atmospheric haze shell that replaces the hard sphere boundary | Create |
| `src/orb/OrbRenderer.ts`, `src/orb/shaders/particle.ts` | Orb identity: state table, atmosphere, particle motion, depth, parallax | Rewrite pieces |
| `src/orb/shaders/sphere.ts`, `src/orb/shaders/backdrop.ts` | Unimported since the Three.js port | **Delete** (Task 9) |
| `src/MambaApp.tsx` | Shell composition, orb-phase derivation, status copy, stage answer | Edit |
| `src/MambaPresence.tsx` | State → CSS custom properties, label copy, motion gating | Edit |
| `src/FloatingOrb.tsx`, `src/Composer.tsx`, `src/TranscriptPanel.tsx`, `src/SudoPopup.tsx`, `src/Toast.tsx`, `src/SettingsPanel.tsx`, `src/settingsStore.ts`, `src/BrowserAgent.tsx`, `src/main.tsx`, `src/orb/OrbView.tsx`, `src/wake/controller.ts` | Per-task component work | Edit |
| `electron/main.cjs` | Window chrome (frameless + native caption overlay), background color sync | Edit (Task 16) |
| `docs/ARCHITECTURE.md` | §16 shader list + orb state line, §20 component list must stay truthful | Edit (Task 17) |

**Unchanged, deliberately:** `src/audio.ts` (transport/VAD/barge-in — behavior contract), `src/wake/*Engine`, `src/audio/wav.ts`, `electron/preload.cjs` (`reportState(state: string)` already accepts the two new phase strings, `src/mambaDesktop.d.ts:28`), `electron/lifecycleManager.cjs`, all Python.

---

## PHASE A — Visual system

### Task 1: Token layer, reset, reduced-motion floor

**Files:**
- Modify: `src/index.css:9-33` (replace `:root`), `src/index.css:35-55` (base)
- Create: `src/motionPrefs.ts`
- Modify: `src/index.css` end-of-file (append reduced-motion block)

**Interfaces:**
- Produces: CSS custom properties consumed by every later task — `--bg-0/1/2`, `--surface-1/2/3`, `--hair`, `--hair-strong`, `--ink`, `--ink-2`, `--ink-3`, `--accent`, `--accent-2`, `--accent-3`, `--accent-ink`, `--accent-dim`, `--accent-line`, `--ok`, `--warn`, `--danger`, `--*-dim`, `--font-sans`, `--font-display`, `--font-mono`, `--text-xs…--text-xl`, `--lh-tight/--lh/--lh-loose`, `--track-label/--track-brand`, `--s-1…--s-16`, `--gutter`, `--thread-max`, `--panel-max`, `--radius-xs…--radius-xl`, `--radius-pill`, `--shadow-1/2/3`, `--focus-ring`, `--dur-1…--dur-5`, `--ease-out/--ease-in-out/--ease-glide`, `--orb-size`, `--bg-window`.
- Produces: `prefersReducedMotion(): boolean`, `motionEnabled(settingsAnimations: boolean): boolean` in `src/motionPrefs.ts`.
- Removes: legacy color tokens `--cyan`, `--cyan-dim`, `--purple`, `--purple-dim`, `--amber`, `--rose`, `--emerald`. Every consumer is repointed inside the same phase (Tasks 1–2 own `header`/`buttons`/`stage`/`composer`/`floating`; Tasks 5–15 own the rest). Leaving a dangling `var(--rose)` is a build-visible mistake, caught by Step 4's grep.

- [ ] **Step 1: Replace the `:root` block** (`src/index.css:9-33`) with the token layer. This is the whole Mamba palette/scale decision; later tasks never invent a value.

```css
:root {
  color-scheme: dark;

  /* foundation — deep charcoal, deliberately not navy */
  --bg-0: #0b0d10;
  --bg-1: #12151a;
  --bg-2: #181c22;
  --bg-window: #0b0d10;            /* must match electron/main.cjs backgroundColor */
  --surface-1: rgba(255, 255, 255, 0.035);
  --surface-2: rgba(255, 255, 255, 0.06);
  --surface-3: rgba(255, 255, 255, 0.09);
  --hair: rgba(255, 255, 255, 0.07);
  --hair-strong: rgba(255, 255, 255, 0.12);

  /* ink */
  --ink: #e9ecf1;
  --ink-2: #a5adba;
  --ink-3: #6b7484;

  /* restrained cool accent ramp — the Mamba signature */
  --accent: #7fd4e6;               /* mist cyan */
  --accent-2: #93a8ef;             /* soft blue-violet */
  --accent-3: #b9a7e6;             /* muted violet */
  --accent-ink: #dcf5fa;
  --accent-dim: rgba(127, 212, 230, 0.12);
  --accent-line: rgba(127, 212, 230, 0.28);

  /* signal colors — state only, desaturated so they read as calm, not alarm */
  --ok: #7ccfa9;      --ok-dim: rgba(124, 207, 169, 0.13);
  --warn: #dcb26b;    --warn-dim: rgba(220, 178, 107, 0.15);
  --danger: #d98b98;  --danger-dim: rgba(217, 139, 152, 0.15);

  /* typography — system stack, no network fonts */
  --font-sans: "Segoe UI Variable Text", "Segoe UI", -apple-system,
    BlinkMacSystemFont, Inter, Roboto, "Helvetica Neue", Arial, sans-serif;
  --font-display: "Segoe UI Variable Display", "Segoe UI", -apple-system,
    BlinkMacSystemFont, Inter, Roboto, sans-serif;
  --font-mono: ui-monospace, "Cascadia Mono", "SF Mono", Menlo, Consolas, monospace;
  --text-xs: 11px; --text-sm: 12.5px; --text-base: 14px;
  --text-md: 15.5px; --text-lg: 18px; --text-xl: 22px;
  --lh-tight: 1.3; --lh: 1.62; --lh-loose: 1.75;
  --track-label: 0.075em; --track-brand: 0.18em;

  /* spacing — 4px scale */
  --s-1: 4px;  --s-2: 8px;   --s-3: 12px; --s-4: 16px;  --s-5: 20px;
  --s-6: 24px; --s-8: 32px;  --s-10: 40px; --s-12: 48px; --s-16: 64px;
  --gutter: var(--s-5);
  --thread-max: 66ch;
  --panel-max: 720px;

  /* radius — restrained; no giant rounded cards */
  --radius-xs: 6px; --radius-sm: 8px; --radius-md: 11px;
  --radius-lg: 14px; --radius-xl: 18px; --radius-pill: 999px;

  /* elevation — depth from darkness, not glow */
  --shadow-1: 0 1px 2px rgba(0, 0, 0, 0.45);
  --shadow-2: 0 10px 28px -14px rgba(0, 0, 0, 0.6);
  --shadow-3: 0 28px 70px -24px rgba(0, 0, 0, 0.72);
  --focus-ring: 0 0 0 2px var(--bg-0), 0 0 0 4px var(--accent-line);

  /* motion — short, eased, never springy */
  --dur-1: 110ms; --dur-2: 180ms; --dur-3: 280ms;
  --dur-4: 460ms; --dur-5: 900ms;
  --ease-out: cubic-bezier(0.22, 0.61, 0.36, 1);
  --ease-in-out: cubic-bezier(0.4, 0, 0.2, 1);
  --ease-glide: cubic-bezier(0.16, 1, 0.3, 1);

  /* orb */
  --orb-size: 264px;
}
```

- [ ] **Step 2: Update `base`** (`src/index.css:35-55`): keep the reset, switch `body` to `font-family: var(--font-sans); font-size: var(--text-base); line-height: var(--lh); background: transparent;` (the transparent root is required by the orb window — `index.html:19,25`), add `body { -webkit-font-smoothing: antialiased; }` and a tokenized selection + focus default:

```css
::selection { background: var(--accent-dim); color: var(--ink); }
:focus-visible { outline: none; box-shadow: var(--focus-ring); border-radius: var(--radius-xs); }
```

- [ ] **Step 3: Create `src/motionPrefs.ts`** (the only motion gate; used by Tasks 4, 11, 13, 15):

```ts
const reduceQuery = "(prefers-reduced-motion: reduce)";

export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia(reduceQuery).matches;
}

/** In-app master toggle (settingsStore `animations`) layered over the OS preference. */
export function motionEnabled(settingsAnimations: boolean): boolean {
  return settingsAnimations !== false && !prefersReducedMotion();
}
```

- [ ] **Step 4: Append the global reduced-motion floor** at the end of `src/index.css`, then confirm no legacy color token survives:

```css
/* ---------- reduced motion ---------- */

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 1ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 1ms !important;
    scroll-behavior: auto !important;
  }
  :root { --orb-size: 240px; }
}
```

Run: `cd /c/mamba && npx tsc --noEmit && grep -n "var(--cyan\|var(--purple\|var(--amber\|var(--rose\|var(--emerald" src/index.css | head`
Expected: tsc silent; grep lists only the pre-Phase-A consumers still awaiting their own task (`header`/`buttons`/`composer`/`transcript`/`sudo`/`toast`/`settings`/`browser` sections). Tasks 1–2 must leave it empty for the sections they own.

- [ ] **Step 5: Verify visually.** `cd /c/mamba && npm run dev` → open the printed `http://localhost:5173/`, screenshot the main window at 1100×750. Expected: charcoal background, no white flash, no console errors from `matchMedia`, orb still animating.
- [ ] **Step 6: Checkpoint** — stop; the user commits. Do not run git.

### Task 2: Shared primitives — buttons, pills, surfaces, inputs, focus

**Files:**
- Modify: `src/index.css:156-239` (buttons), `:114-154` (brand/status pill), `:59-73` (`.mamba-shell` background), `:381-424` (`.orb-window`, floating layers)
- Modify: `src/MambaApp.tsx:466-520` (header markup classes only — no logic), `src/TranscriptPanel.tsx` `.icon-btn` consumers (no change needed if names hold)

**Interfaces:**
- Consumes: Task 1 tokens.
- Produces: `.icon-btn` (unchanged name, tokenized), `.icon-btn.is-active`, `.btn` + `.btn-primary` + `.btn-ghost`, `.pill` with `--state` tint variants `.pill.is-listening/.is-thinking/.is-executing/.is-verifying/.is-speaking/.is-permission/.is-error`, `.surface` (panel recipe: `--bg-1` + `--hair` + `--radius-lg` + `--shadow-3`), `.field` (text input recipe), `.label` (small natural-language caption). Later tasks reuse these instead of re-declaring borders.

- [ ] **Step 1: Rewrite `.mamba-shell`** (`src/index.css:59-73`) — depth from one wide, very low-contrast wash instead of three colored radial glows:

```css
.mamba-shell {
  position: relative;
  width: 100vw;
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background:
    radial-gradient(120% 70% at 50% -18%, rgba(127, 212, 230, 0.05), transparent 58%),
    linear-gradient(180deg, var(--bg-1) 0%, var(--bg-0) 46%, var(--bg-0) 100%);
  color: var(--ink);
  user-select: none;
}
```

- [ ] **Step 2: Retire the pulsing brand dot** (Spec §5 "decorative UI noise", §12 "constant movement"). Replace `.brand-dot`'s animation with a static mark, and set `.brand-name` on the display face:

```css
.brand-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 10px -2px var(--accent);
  transition: background var(--dur-2) var(--ease-out), box-shadow var(--dur-2) var(--ease-out);
}
.brand-name {
  font-family: var(--font-display);
  font-size: var(--text-sm);
  font-weight: 500;
  letter-spacing: var(--track-brand);
  color: var(--ink-2);
  text-transform: uppercase;
}
```

Delete the `@keyframes brand-pulse` block (`src/index.css:101-104`) and repoint `.wake-armed-dot` (`:413`) to `transition: opacity var(--dur-3)` with a static glow, so nothing animates forever in the shell (the mic-armed state is already conveyed by presence).

- [ ] **Step 3: Tokenize controls.** `icon-btn` → `--surface-1`/`--hair`, hover `--surface-2`, active `--accent-dim` + `--accent-line`, `transition: background var(--dur-1) var(--ease-out), color var(--dur-1) var(--ease-out), border-color var(--dur-1) var(--ease-out)`; drop `:active { transform: scale(...) }` (Spec §12 "avoid bouncing") in favour of a background step. `.btn` gains `font-size: var(--text-base); font-weight: 500; padding: 9px var(--s-4); border-radius: var(--radius-md);` and three variants:

```css
.btn-primary { background: var(--accent-dim); border-color: var(--accent-line); color: var(--accent-ink); }
.btn-primary:hover { background: rgba(127, 212, 230, 0.19); }
.btn-ghost { background: transparent; border-color: transparent; color: var(--ink-2); }
.btn-ghost:hover { background: var(--surface-1); color: var(--ink); }
.btn-danger { background: var(--danger-dim); border-color: rgba(217, 139, 152, 0.34); color: #f0d3d8; }
```

`.btn-approve`/`.btn-reject` (`:227-239`) are deleted; their call sites are rewritten in Task 12.

- [ ] **Step 4: Add the shared recipes** (single definition, reused by Tasks 5–15):

```css
.surface {
  background: var(--bg-1);
  border: 1px solid var(--hair);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-3);
}
.field {
  background: var(--surface-1);
  border: 1px solid var(--hair);
  border-radius: var(--radius-sm);
  color: var(--ink);
  font: inherit;
  font-size: var(--text-sm);
  padding: 7px var(--s-3);
  transition: border-color var(--dur-2) var(--ease-out), background var(--dur-2) var(--ease-out);
}
.field:focus { outline: none; border-color: var(--accent-line); background: var(--surface-2); }
.label {
  font-size: var(--text-xs);
  letter-spacing: var(--track-label);
  text-transform: uppercase;
  color: var(--ink-3);
}
.pill.is-executing .dot { background: var(--accent-2); }
.pill.is-verifying .dot { background: var(--ok); }
```

- [ ] **Step 5: Verify.** `npx tsc --noEmit && npm run build`, then dev-server screenshot of the header: brand reads as calm small-caps, icon row dims, no element pulses. Hover one icon button — background changes, geometry does not.
- [ ] **Step 6: Checkpoint** — stop; the user commits.

---

## PHASE B — Main shell

### Task 3: Idle composition, header as window chrome, outcome-first status copy

**Files:**
- Modify: `src/MambaApp.tsx:422-550` (status copy + stage markup), `src/index.css:75-113,241-288,291-295,1485-1496`
- Modify: `src/index.css` header rules for `-webkit-app-region`

**Interfaces:**
- Consumes: Task 1 tokens, Task 2 primitives, `statusCopy` shape (`{text, hint, pill}`), `settings.wakeWordEnabled/wakePhrase`.
- Produces: the eight-entry status table keyed by the existing `LiveState` **plus** the two derived orb phases (`executing`, `verification`) that Task 8 supplies; class `.mamba-header` gains `drag` behavior and `.header-actions` `no-drag`; `--orb-size` becomes the only orb dimension (the hard `size={260}` at `src/MambaApp.tsx:540` disappears).

- [ ] **Step 1: Rewrite `statusCopy`** (`src/MambaApp.tsx:423-438`) to natural language with no machinery exposure (Spec §4, §13). Keep the object shape and every existing key so `liveState` lookups are unaffected; add the two new keys:

```tsx
const statusCopy: Record<string, { text: string; hint: string; pill: string }> = {
  idle: {
    text: "Ready",
    hint: settings.wakeWordEnabled
      ? `Say "${settings.wakePhrase || "hey mamba"}" or just start typing`
      : "Ask Mamba anything below",
    pill: "",
  },
  listening:   { text: "Listening…",            hint: "Go ahead",                 pill: "is-listening" },
  thinking:    { text: "Thinking…",             hint: "Working through it",       pill: "is-thinking" },
  executing:   { text: "Working…",              hint: "Getting it done",          pill: "is-executing" },
  verifying:   { text: "Checking…",             hint: "Making sure it worked",    pill: "is-verifying" },
  speaking:    { text: "Speaking…",             hint: "Answering out loud",       pill: "is-speaking" },
  permission:  { text: "Needs your approval",   hint: "Review the request",       pill: "is-permission" },
  error:       { text: "Something went wrong",  hint: "Try again in a moment",    pill: "is-error" },
  connecting:  { text: "Starting up…",          hint: "",                         pill: "" },
  disconnected:{ text: "Not connected",         hint: "Restart Mamba to reconnect", pill: "is-error" },
};
```

- [ ] **Step 2: Make the stage read as an ambient assistant** (`src/index.css:241-288`): orb centred with air, caption quiet, no uppercase letterspaced shout (Spec §6, §7).

```css
.mamba-stage {
  position: relative; flex: 1 1 auto;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  min-height: 0; z-index: 10;
  padding: var(--s-4) var(--gutter) var(--s-6);
  gap: var(--s-5);
}
.orb-wrap { width: var(--orb-size); height: var(--orb-size); display: grid; place-items: center; cursor: pointer; border-radius: 50%; outline: none; }
.orb-wrap:focus-visible { box-shadow: var(--focus-ring); }
.status-block { text-align: center; min-height: 40px; }
.status-text {
  font-family: var(--font-display); font-size: var(--text-lg); font-weight: 400;
  letter-spacing: -0.01em; color: var(--ink-2);
  transition: color var(--dur-3) var(--ease-out);
}
.status-hint { margin-top: var(--s-1); font-size: var(--text-sm); color: var(--ink-3); }
```

and pass the token, not a number: `<MambaPresence … size={orbSize} …/>` where `const orbSize = Number(getComputedStyle(document.documentElement).getPropertyValue("--orb-size").replace("px", "")) || 264;` is computed **once** in a `useState` initialiser inside `MambaApp` (no resize listener, no new state layer).

- [ ] **Step 3: Header becomes desktop chrome** (Spec §20): add to `.mamba-header` (`src/index.css:77-84`) `padding: var(--s-3) var(--gutter) var(--s-2); -webkit-app-region: drag;` and to `.header-actions`, `.header-status`, `.brand` `-webkit-app-region: no-drag;`. Keep the brand dot static (Task 2). Quiet the whole action row until hover:

```css
.header-actions { display: flex; align-items: center; gap: var(--s-1); -webkit-app-region: no-drag; opacity: 0.6; transition: opacity var(--dur-3) var(--ease-out); }
.header-actions:hover { opacity: 1; }
```

- [ ] **Step 4: Verify.** `npx tsc --noEmit && npm run build`; dev screenshot at 1100×750 (idle) and at 640×560. Expected: one dominant orb, minimal branding, quiet invitation, no panel clutter; status says "Ready"; at 640px width the pill label hides (existing `:1490` rule) without overlap.
- [ ] **Step 5: Checkpoint** — stop; the user commits.

### Task 4: Composer — integrated input with focus and cancellation

**Files:**
- Modify: `src/Composer.tsx:66-115` (markup + motion gating only), `src/index.css:297-377`, `src/MambaApp.tsx:562-567` (hint copy)

**Interfaces:**
- Consumes: `--panel-max`, `--surface-*`, `--focus-ring`, `motionEnabled` (Task 1).
- Produces: unchanged props contract (`onMessageSubmit`, `voiceActive`, `onToggleVoice`, `disabled`, `busy`, `onCancelTurn`) and unchanged class names (`composer`, `composer-input`, `composer-btn`, `send`, `mic-live`, `is-stop`) — `MambaApp.tsx:554-561` is not touched.

- [ ] **Step 1: Restyle** — the composer becomes a quiet integrated surface, not a chat textbox (Spec §9):

```css
.composer {
  display: flex; align-items: flex-end; gap: var(--s-2);
  max-width: var(--panel-max); margin: 0 auto;
  padding: var(--s-2) var(--s-2) var(--s-2) var(--s-4);
  border-radius: var(--radius-lg);
  border: 1px solid var(--hair);
  background: var(--surface-1);
  box-shadow: var(--shadow-2);
  transition: border-color var(--dur-2) var(--ease-out), background var(--dur-2) var(--ease-out), box-shadow var(--dur-2) var(--ease-out);
}
.composer:focus-within { border-color: var(--accent-line); background: var(--surface-2); box-shadow: var(--shadow-2), 0 0 0 3px var(--accent-dim); }
.composer-input { font-size: var(--text-base); line-height: var(--lh); padding: var(--s-2) 0; color: var(--ink); }
.composer-btn { width: 34px; height: 34px; border-radius: var(--radius-md); color: var(--ink-3); }
.composer-btn:hover { color: var(--ink); background: var(--surface-2); }
.composer-btn.send { background: var(--accent-dim); border-color: var(--accent-line); color: var(--accent-ink); }
.composer-btn.mic-live { background: var(--accent-dim); border-color: var(--accent-line); color: var(--accent); }
.composer-btn.is-stop { background: var(--danger-dim); border-color: rgba(217, 139, 152, 0.32); color: var(--danger); }
.composer-hint { margin-top: var(--s-2); font-size: var(--text-xs); letter-spacing: 0.02em; color: var(--ink-3); opacity: 0.75; }
```

Note the deliberate change: an active *voice session* now reads accent-cyan (Mamba is listening) instead of rose, because rose is reserved for stop/danger (Spec §5 "carefully controlled contrast"; §15 keeps danger colour for the destructive control).

- [ ] **Step 2: Remove the `backdrop-filter: blur(14px)`** on `.composer` (`src/index.css:307-308`) — glassmorphism over a static shell is cost without meaning (Spec §5, §21).
- [ ] **Step 3: Gate the auto-grow and send-disable transition on motion prefs**: in `Composer.tsx`, import `motionEnabled` and use `transition: none` for the height animation when disabled is unnecessary — concretely, replace the inline `style.height` writes in `autoResize()` (`src/Composer.tsx:53-58`) with a `maxHeight` class toggle only if reduced motion is active:

```tsx
const autoResize = () => {
  const el = inputRef.current;
  if (!el) return;
  el.style.height = "auto";
  const capped = Math.min(el.scrollHeight, 140);
  el.style.height = motionEnabled(true) ? `${capped}px` : "auto";
};
```

- [ ] **Step 4: Verify.** `npx tsc --noEmit`; dev-server interaction: type 4 lines (grows to 140px then scrolls), press Enter (sends), send a request then press Stop (stop button visible only while `busy`, one click, then disabled with "Cancelling…"). Mic toggle starts/stops the continuous session exactly as before (`MambaApp.tsx:217-226` untouched).
- [ ] **Step 5: Checkpoint** — stop; the user commits.

---

## PHASE C — Conversation

### Task 5: One entry renderer — quiet user lines, hierarchical answers

**Files:**
- Create: `src/ThreadEntry.tsx`
- Modify: `src/TranscriptPanel.tsx:119-166,277-352` (delegate rendering, drop dead selection), `src/index.css:426-674` (thread section rewrite)

**Interfaces:**
- Consumes: `TranscriptEntry` shape (`{id, timestamp, role, content, isError?, streaming?}` — `src/MambaApp.tsx:14-22`), Task 2 `.label/.surface`, Task 1 type tokens.
- Produces:

```tsx
export type ThreadVariant = "rail" | "stage";
export interface ThreadEntryProps {
  entry: TranscriptEntry;
  variant?: ThreadVariant;   // default "rail"
  query?: string;            // search highlight, rail only
  defaultExpanded?: boolean;
}
export const ThreadEntry: React.FC<ThreadEntryProps>;
export function highlightParts(text: string, query: string): Array<{ text: string; hit: boolean }>;
```

Class contract used by Tasks 6–7 and CSS: `.thread-entry` + `.is-user|.is-model|.is-error|.is-streaming`, `.thread-role`, `.thread-time`, `.thread-body`, `.thread-body.is-clamped`, `.thread-hit`, `.thread-more`.

- [ ] **Step 1: Write `src/ThreadEntry.tsx`** — the shared, safe renderer. No `dangerouslySetInnerHTML` (the current rail highlights via HTML string injection with Tailwind classes that don't exist, `src/TranscriptPanel.tsx:142`).

```tsx
import React, { useMemo, useState } from "react";

export interface TranscriptEntry {
  id: string; timestamp: string; role: "user" | "model";
  content: string; isError?: boolean; streaming?: boolean;
}

export type ThreadVariant = "rail" | "stage";

const CLAMP_CHARS = 420;

export function highlightParts(text: string, query: string): Array<{ text: string; hit: boolean }> {
  const q = query.trim().toLowerCase();
  if (!q) return [{ text, hit: false }];
  const parts: Array<{ text: string; hit: boolean }> = [];
  const hay = text.toLowerCase();
  let from = 0;
  for (let at = hay.indexOf(q); at !== -1; at = hay.indexOf(q, from)) {
    if (at > from) parts.push({ text: text.slice(from, at), hit: false });
    parts.push({ text: text.slice(at, at + q.length), hit: true });
    from = at + q.length;
  }
  if (from < text.length) parts.push({ text: text.slice(from), hit: false });
  return parts;
}

export const ThreadEntry: React.FC<{ entry: TranscriptEntry; variant?: ThreadVariant; query?: string; defaultExpanded?: boolean }> = ({
  entry, variant = "rail", query = "", defaultExpanded = false,
}) => {
  const [open, setOpen] = useState(defaultExpanded);
  const long = entry.content.length > CLAMP_CHARS || entry.content.split("\n\n").length > 2;
  const clamped = variant === "rail" && long && !open && !entry.streaming;
  const time = useMemo(() => formatTime(entry.timestamp), [entry.timestamp]);
  return (
    <article className={`thread-entry is-${entry.role}${entry.isError ? " is-error" : ""}${entry.streaming ? " is-streaming" : ""}${variant === "stage" ? " is-stage" : ""}`}>
      <header className="thread-head">
        <span className="thread-role">{entry.role === "user" ? "You" : "Mamba"}</span>
        {time && <time className="thread-time" dateTime={entry.timestamp}>{time}</time>}
      </header>
      <div className={`thread-body${clamped ? " is-clamped" : ""}`}>
        {highlightParts(entry.content, query).map((part, i) =>
          part.hit ? <mark className="thread-hit" key={i}>{part.text}</mark> : <React.Fragment key={i}>{part.text}</React.Fragment>
        )}
      </div>
      {long && variant === "rail" && (
        <button type="button" className="thread-more btn btn-ghost" onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? "Less" : "View details"}
        </button>
      )}
    </article>
  );
};
```

Add `formatTime` as a module-local helper that keeps the current `en-US` 24h `HH:MM:SS` behaviour (`src/TranscriptPanel.tsx:128-136`) but renders `HH:MM` only, and returns `""` for an unparseable stamp — timestamps must not push themselves into the hierarchy (Spec §8).

- [ ] **Step 2: Rewrite the thread CSS** (`src/index.css:568-645`) as an editorial thread: no bubbles, alignment carries the role, answers get the hierarchy (Spec §8).

```css
.transcript-list { display: flex; flex-direction: column; gap: var(--s-5); padding: var(--s-4) var(--s-5); }
.thread-entry { display: flex; flex-direction: column; gap: var(--s-1); }
.thread-entry.is-user { opacity: 0.72; }
.thread-entry.is-user .thread-body { font-size: var(--text-base); color: var(--ink-2); }
.thread-entry.is-model .thread-body { font-family: var(--font-display); font-size: var(--text-md); line-height: var(--lh-loose); color: var(--ink); font-weight: 400; }
.thread-head { display: flex; align-items: baseline; gap: var(--s-2); }
.thread-role { font-size: var(--text-xs); letter-spacing: var(--track-label); text-transform: uppercase; color: var(--ink-3); }
.thread-entry.is-model .thread-role { color: var(--accent); opacity: 0.72; }
.thread-time { font-size: var(--text-xs); color: var(--ink-3); font-variant-numeric: tabular-nums; opacity: 0.6; }
.thread-body { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; user-select: text; -webkit-user-select: text; }
.thread-body.is-clamped { display: -webkit-box; -webkit-line-clamp: 6; -webkit-box-orient: vertical; overflow: hidden; }
.thread-hit { background: var(--accent-dim); color: var(--accent-ink); border-radius: var(--radius-xs); padding: 0 2px; }
.thread-entry.is-error { border-left: 2px solid var(--danger); padding-left: var(--s-3); }
.thread-more { align-self: flex-start; padding: 0; font-size: var(--text-sm); color: var(--ink-3); }
.thread-more:hover { color: var(--accent); background: transparent; }
```

Delete `.t-entry*`, `.t-role*`, `.t-time`, `.t-copy`, `.t-body*` rules and their markup usages in the same task (the copy-to-clipboard button at `src/TranscriptPanel.tsx:319` moves into `ThreadEntry`'s `rail` variant as a hover affordance, or is dropped — decision: **dropped**, because a desktop user selects text natively and the button is UI noise; the copy handler and its state are deleted).
- [ ] **Step 3: Rewire `TranscriptPanel`** to import and render `<ThreadEntry entry={e} query={query} />` inside `.transcript-list` (`src/TranscriptPanel.tsx:277-337`); delete `toggleEntrySelection` (`:161-166`, builds a set and discards it), `newSet`-related state, the copy machinery (`:146-149`), and the `dangerouslySetInnerHTML` branch (`:142`). Keep search (`:119-125`), the three-state role filter (`:212-214`), tail-follow (`:89-99`), `F4`/`Esc`/`Ctrl+K` (`:75-79`), and the resizer — all behavior-preserving.
- [ ] **Step 4: Verify.** `npx tsc --noEmit`. Dev-server: send three messages, confirm user lines are visibly quieter than answers; paste a 900-character answer and confirm "View details" reveals it; search a term that appears twice and confirm both marks render (no HTML injection — check `document.querySelector('.thread-hit')` text equals the query); select answer text with the mouse and copy it natively.
- [ ] **Step 5: Checkpoint** — stop; the user commits.

### Task 6: Streaming appearance, verified state, panel surface

**Files:**
- Modify: `src/index.css:428-460,628-674`, `src/ThreadEntry.tsx` (streaming caret + verified marker), `src/TranscriptPanel.tsx:173-178,279-295` (motion tokens)

**Interfaces:**
- Consumes: `entry.streaming` (provisional deltas only — the reconciliation rules in `MambaApp.tsx:102-130` are not modified), Task 5 classes.
- Produces: `.thread-entry.is-streaming`, `.thread-entry.is-verified`, `.transcript-panel` as a `.surface`-derived docked rail.

- [ ] **Step 1: Streaming reads as live, finished answers read as settled** (Spec §16: no fake loading; real streamed text only). Replace the caret block (`src/index.css:647-667`) with a token-driven caret on the provisional entry only:

```css
.thread-entry.is-streaming .thread-body::after {
  content: ""; display: inline-block; width: 5px; height: 1.02em;
  margin-inline-start: 4px; vertical-align: text-bottom;
  background: var(--accent); opacity: 0.55; border-radius: 1px;
  animation: mamba-caret 1.1s var(--ease-in-out) infinite;
}
@keyframes mamba-caret { 0%, 100% { opacity: 0.5; } 50% { opacity: 0.05; } }
.thread-entry.is-verified .thread-role::after { content: " · done"; color: var(--ok); opacity: 0.6; }
```

`is-verified` is applied **only** when `streaming === false && role === "model"`, i.e. the authoritative text has landed; it must never be inferred from a delta (Spec §16, and the Phase 4 rule that streamed text is provisional).
- [ ] **Step 2: Convert the panel into a docked rail** (`src/index.css:428-446`) so it belongs to the shell instead of floating over it: `top: var(--s-2); bottom: var(--s-2); right: var(--s-2); width: 480px; border-radius: var(--radius-lg); background: color-mix(in srgb, var(--bg-1) 92%, transparent); border: 1px solid var(--hair); box-shadow: var(--shadow-3);` and remove `backdrop-filter: blur(18px)` (`:441-442`). The JS `panelWidth` floor (`src/TranscriptPanel.tsx:53,106`) stays but its default becomes 480 and the minimum 360.
- [ ] **Step 3: Align entrance motion** to the token curve (`src/TranscriptPanel.tsx:173-178`): replace `{ type: "spring", stiffness: 300, damping: 30 }` with `transition: { duration: 0.22, ease: [0.22, 0.61, 0.36, 1] }`, honour `motionEnabled(settings.animations)` by switching to `initial={false}`, and fix the `AnimatePresence` misuse (`:172` wraps a child that is never conditionally removed because of the early return at `:169`) by moving the early return *below* the `AnimatePresence` guard or making the panel a conditional child so `exit` actually plays.
- [ ] **Step 4: Verify.** `npx tsc --noEmit && npm run build`. Dev-server: send a typed message and watch a provisional bubble appear with the caret, then finalize (caret gone, `· done` shown); reopen/close the rail twice and confirm the exit animation runs once; set `localStorage["mamba.settings.v1"]` to include `"animations": false`, reload, and confirm the rail appears instantly with no motion.
- [ ] **Step 5: Checkpoint** — stop; the user commits.

### Task 7: The answer in the stage — inline, expandable, single source

**Files:**
- Modify: `src/MambaApp.tsx:546-550` (stage children), `src/index.css` (new `/* ---------- stage answer ---------- */` block), `src/TranscriptPanel.tsx` (export nothing new; it already owns the rail)

**Interfaces:**
- Consumes: `transcript` state, `ThreadEntry variant="stage"`, `isTranscriptOpen` setter, `streamEntryIdRef` (so the stage never shows provisional text).
- Produces: `.stage-answer` region; `latestAnswer` derived with `useMemo`; no new state fields, no new props threading.

Spec §7 says idle must have "no unnecessary panels" while §8 wants the *answer* — not the machinery — to be the emphasis. The resolution: the stage shows at most the last authoritative answer, clamped to two lines, with "View details" opening the existing rail. This reuses `ThreadEntry` (no duplicated component, Spec §24).

- [ ] **Step 1: Derive the latest finalized answer** inside `MambaApp` (no new state, no reducer):

```tsx
const latestAnswer = useMemo(() => {
  for (let i = transcript.length - 1; i >= 0; i--) {
    const e = transcript[i];
    if (e.role === "model" && !e.streaming) return e;
  }
  return null;
}, [transcript]);
```

- [ ] **Step 2: Render it between the orb caption and the composer**, only when it exists:

```tsx
{latestAnswer && (
  <div className="stage-answer">
    <ThreadEntry entry={latestAnswer} variant="stage" defaultExpanded={false} />
    <button type="button" className="thread-more btn btn-ghost" onClick={() => setIsTranscriptOpen(true)}>
      View details
    </button>
  </div>
)}
```

- [ ] **Step 3: Style it as the quietest element that still carries hierarchy**:

```css
.stage-answer {
  max-width: min(560px, 92%); width: 100%;
  display: flex; flex-direction: column; align-items: center; gap: var(--s-1);
  margin-top: var(--s-2);
}
.thread-entry.is-stage .thread-head { display: none; }
.thread-entry.is-stage .thread-body {
  font-family: var(--font-display); font-size: var(--text-base); line-height: var(--lh);
  color: var(--ink-2); text-align: center;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
```

- [ ] **Step 4: Verify idle stays idle.** Dev-server, fresh load: no `.stage-answer` (empty transcript) → composition is orb + invitation + composer only (Spec §7). Send one request: the answer appears under the orb, clamped; "View details" opens the rail and the full text is there. Cancel a turn (`MambaApp.tsx:132-135`): provisional bubble vanishes and the stage keeps the *previous* authoritative answer, never the cancelled partial (this is the Spec §16 source-of-truth check).
- [ ] **Step 5: Checkpoint** — stop; the user commits.

## PHASE D — The Orb

### Task 8: Add `executing` and `verifying` as derived Orb phases

**Files:**
- Modify: `src/orb/OrbRenderer.ts:11-17` (`OrbState`), `src/MambaPresence.tsx:6-12` (`MambaPresenceState`)
- Modify: `src/MambaApp.tsx:137-142` (shell state report), `:275-278` (`onProgress`), `:241-306` (terminal clears), `:408-420` (presence mapping), `:537-544`
- Modify: `src/FloatingOrb.tsx:26-41` (accepted state strings)
- Modify: `electron/main.cjs:633` (smoke-test state list)

**Interfaces:**
- Consumes: the **existing** `progress` milestone frames (`api/server.py:222-230` emits `{type:"progress", milestone, stage}`; `core/brain.py:1197,1227,1526,2181` are the only producers: `"Planning..."`, `"Executing..."`, `"Verifying..."`, `"Completed"`), `src/audio.ts:315-320` (already forwards the string to `onProgress` and sets `LiveState("thinking")`).
- Produces: `MambaPresenceState = "idle" | "listening" | "thinking" | "executing" | "verifying" | "speaking" | "permission" | "error"`; `OrbState` identical. **`LiveState` (`src/audio.ts:26-34`) is unchanged** — the transport, the lifecycle busy-detection (`electron/preload.cjs:71-123`) and `docs/ARCHITECTURE.md` §17/§21 contracts keep their exact vocabulary. Spec §11's "VERIFICATION" maps to the presentation phase `verifying`; that mapping is recorded in Task 17's doc edit.

This is the plan's one "tiny frontend integration change" (Spec §2): a presentation refinement that *reads* an already-transmitted string. No backend edit, no new WS message type, no new state machine, no new orchestrator.

- [ ] **Step 1: Extend the two presentation unions** and the renderer's state table so TypeScript forces every later task to supply both new entries:

```ts
// src/orb/OrbRenderer.ts
export type OrbState =
  | "idle" | "listening" | "thinking" | "executing" | "verifying"
  | "speaking" | "permission" | "error";

// src/MambaPresence.tsx
export type MambaPresenceState = OrbState;   // single source of truth
```

`MambaPresence.tsx` imports the type from `./orb/OrbRenderer` (already reachable through `src/orb/index.ts:1`).
- [ ] **Step 2: Derive the phase in `MambaApp`** — one local `useState`, cleared at every terminal boundary so a stale phase can never outlive its turn:

```tsx
type OrbPhase = "executing" | "verifying";
const [orbPhase, setOrbPhase] = useState<OrbPhase | null>(null);
```

```tsx
onProgress: (milestone) => {
  addToast(milestone, "milestone");
  const m = milestone.toLowerCase();
  if (m.startsWith("executing")) setOrbPhase("executing");
  else if (m.startsWith("verifying")) setOrbPhase("verifying");
  else if (m.startsWith("planning") || m.startsWith("understanding")) setOrbPhase(null);
},
```

and add `setOrbPhase(null)` to the existing `onTranscription` model branch (`src/MambaApp.tsx:254-262`), `onCancelled` (`:265-268`), `onTurnComplete` (`:269-274`), `onError` (`:290-295`), and to `onStateChange` whenever the incoming `LiveState` is not `"thinking"` (`:233-240`).
- [ ] **Step 3: Feed the orb, not the transport**, at the two consumption points:

```tsx
const orbState: MambaPresenceState =
  orbPhase && liveState === "thinking" ? orbPhase : presenceState;   // presenceState stays as-is
```

`<MambaPresence state={orbState} …/>` (`:537`) and `window.mambaDesktop?.reportState?.(orbState)` (`:138-142`, dependency array becomes `[orbState]`). Verified-safe: `electron/main.cjs:403-408` only stores `currentOrbState` and forwards to the orb window — it never interprets the string, and idle/busy logic uses the separate `report-task-state` / `report-voice-state` / `awaiting-permission` channels.
- [ ] **Step 4: Accept the two new strings in the orb renderer and the smoke test**: add `"executing"`, `"verifying"` to `validStates` (`src/FloatingOrb.tsx:27-34`) and to `testStates` (`electron/main.cjs:633`).
- [ ] **Step 5: Add interim visual entries for both states** with placeholder values taken from Task 9's table (this step exists only so `Record<OrbState, StateVisualConfig>` compiles; Tasks 9–10 retune the numbers). `executing`: calm directional field; `verifying`: tightened, stabilised field.
- [ ] **Step 6: Verify.** `npx tsc --noEmit` must now fail until every `Record<OrbState, …>` covers both keys — fix all sites, then pass. Runtime check with the backend up (`npm run electron:dev`): ask Mamba to open Notepad and type; the Orb must move idle → thinking → **working (executing)** → **checking (verifying)** → answer, with no abrupt colour jump. Ask for something needing approval: `permission` must win over any orb phase (it comes from `LiveState`, which is not `"thinking"`).
- [ ] **Step 7: Checkpoint** — stop; the user commits.

### Task 9: Atmosphere — remove the hard sphere, add soft light

**Files:**
- Create: `src/orb/shaders/haze.ts`
- Delete: `src/orb/shaders/sphere.ts`, `src/orb/shaders/backdrop.ts`
- Modify: `src/orb/OrbRenderer.ts:19-105` (state table), `:112-114,197-219,382-402` (core, lights, disposal)

**Interfaces:**
- Consumes: `OrbState` (Task 8), Three.js `ShaderMaterial` (GLSL1), `Analyser` energies.
- Produces: `hazeVS`, `hazeFS` from `src/orb/shaders/haze.ts`; `StateVisualConfig` extended with `hazeCore: number`, `hazeEdge: number`, `hazeIntensity: number`, `breath: number`, `cohesion: number`, `directional: number` — consumed by Task 10 and by `OrbRenderer`'s interpolation loop.
- Verified-dead before deletion: `sphereVS` and `backdropVS/backdropFS` have zero importers (only self-references; `grep -n "shaders/sphere\|shaders/backdrop" src/` returns nothing outside those files), and `backdrop.ts` is Regl-flavoured GLSL (`in`/`out`, WebGL2) that the Three.js pipeline cannot consume as written.

- [ ] **Step 1: Write `src/orb/shaders/haze.ts`** — one billboard quad, additive, radial falloff, no rim, no boundary:

```ts
export const hazeVS = `
varying vec2 vUv;
void main() {
  vUv = uv;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}`;

export const hazeFS = `
precision highp float;
uniform vec3 uCore;
uniform vec3 uEdge;
uniform float uIntensity;
uniform float uTime;
uniform float uBreath;
uniform float uAudio;
varying vec2 vUv;

void main() {
  vec2 p = vUv - 0.5;
  float d = min(length(p) * 2.0, 1.0);
  float core = 1.0 - d;
  float glow = pow(core, 2.6);
  float halo = pow(core, 1.1) * 0.42;
  float shimmer = 0.96 + 0.04 * sin(uTime * 0.7 + p.x * 2.4 + p.y * 1.9);
  float a = (glow + halo) * uIntensity * shimmer * (1.0 + uBreath + uAudio * 0.22);
  vec3 col = mix(uEdge, uCore, glow);
  gl_FragColor = vec4(col * (0.72 + 0.5 * glow), clamp(a, 0.0, 0.72));
}`;
```

- [ ] **Step 2: Replace the core sphere with the haze mesh.** In `init()` (`src/orb/OrbRenderer.ts:197-219`), delete `coreGeo`/`coreMat`/`coreMesh`, `AmbientLight`, and `PointLight`; add, before the particle field:

```ts
const hazeMat = new THREE.ShaderMaterial({
  vertexShader: hazeVS,
  fragmentShader: hazeFS,
  uniforms: {
    uCore: { value: new THREE.Color(initialConfig.hazeCore) },
    uEdge: { value: new THREE.Color(initialConfig.hazeEdge) },
    uIntensity: { value: initialConfig.hazeIntensity },
    uTime: { value: 0 },
    uBreath: { value: 0 },
    uAudio: { value: 0 },
  },
  transparent: true,
  depthWrite: false,
  depthTest: false,
  blending: THREE.AdditiveBlending,
});
const hazeMesh = new THREE.Mesh(new THREE.PlaneGeometry(3.3, 3.3), hazeMat);
hazeMesh.renderOrder = -1;
scene.add(hazeMesh);
this.hazeMesh = hazeMesh;
```

Declare `private hazeMesh!: THREE.Mesh<THREE.PlaneGeometry, THREE.ShaderMaterial>;` and dispose its geometry+material in `dispose()` (`:382-402`).
- [ ] **Step 3: Retune `STATE_VISUALS` to the restrained Mamba ramp** (replace all eight entries; `0x00f5ff`-class electric saturation is gone — Spec §5, §10):

```ts
idle:       { speed: 0.30, turbulence: 0.34, pull: 0.02, expand: 0.03, primary: 0x6fc6da, secondary: 0x2c4a6e, accent: 0xdfeef5, mix: 0.0,
              hazeCore: 0x63bdd2, hazeEdge: 0x1b2740, hazeIntensity: 0.34, breath: 0.010, cohesion: 0.10, directional: 0.00 },
listening:  { speed: 0.48, turbulence: 0.46, pull: 0.46, expand: 0.04, primary: 0x7fd4e6, secondary: 0x2f6f96, accent: 0xeaf7fb, mix: 0.55,
              hazeCore: 0x8addec, hazeEdge: 0x1d3252, hazeIntensity: 0.46, breath: 0.006, cohesion: 0.16, directional: 0.00 },
thinking:   { speed: 1.05, turbulence: 1.20, pull: 0.08, expand: 0.12, primary: 0x9aa6ea, secondary: 0x4a4a86, accent: 0xc9d2ff, mix: 0.85,
              hazeCore: 0x93a0e4, hazeEdge: 0x26264a, hazeIntensity: 0.42, breath: 0.008, cohesion: 0.06, directional: 0.05 },
executing:  { speed: 1.20, turbulence: 0.55, pull: 0.06, expand: 0.10, primary: 0x7fb6e8, secondary: 0x35608c, accent: 0xd7ecff, mix: 0.85,
              hazeCore: 0x79aede, hazeEdge: 0x1e3149, hazeIntensity: 0.44, breath: 0.004, cohesion: 0.10, directional: 0.85 },
verifying:  { speed: 0.55, turbulence: 0.22, pull: 0.10, expand: 0.05, primary: 0x9adfc0, secondary: 0x3b7a68, accent: 0xdff5ea, mix: 0.85,
              hazeCore: 0x92d8bb, hazeEdge: 0x1d3630, hazeIntensity: 0.40, breath: 0.003, cohesion: 0.55, directional: 0.15 },
speaking:   { speed: 0.68, turbulence: 0.58, pull: 0.04, expand: 0.50, primary: 0x8fe0d8, secondary: 0x2f7f78, accent: 0xe8fbf7, mix: 0.85,
              hazeCore: 0x8bdcd4, hazeEdge: 0x1c3a3d, hazeIntensity: 0.46, breath: 0.008, cohesion: 0.08, directional: 0.10 },
permission: { speed: 0.45, turbulence: 0.36, pull: 0.08, expand: 0.08, primary: 0xdcb26b, secondary: 0x8a6b3c, accent: 0xf5e3bd, mix: 0.9,
              hazeCore: 0xd3aa68, hazeEdge: 0x37291a, hazeIntensity: 0.36, breath: 0.004, cohesion: 0.24, directional: 0.00 },
error:      { speed: 0.70, turbulence: 0.78, pull: 0.08, expand: 0.18, primary: 0xd98b98, secondary: 0x8a4c58, accent: 0xf1ccd3, mix: 0.9,
              hazeCore: 0xd1848f, hazeEdge: 0x331b20, hazeIntensity: 0.34, breath: 0.002, cohesion: 0.06, directional: 0.00 },
```

Delete `coreEmissive`/`coreEmissiveIntensity` from the interface and from `setState()` (`:158-168`), which now sets nothing imperatively — `setState` only stores `currentState` and every visual parameter is interpolated in the frame loop (Spec §11: "Transitions between states must be smooth. Never abruptly switch visual parameters.").
- [ ] **Step 4: Interpolate the haze** in `animate()` (`:313-380`): add smoothed `currentBreath`, `currentHazeCore/Edge` (`THREE.Color`), `currentHazeIntensity`, and drive them with the same `lerpFactor`; then

```ts
const haze = this.hazeMesh?.material;
if (haze) {
  haze.uniforms.uTime.value += dt;
  haze.uniforms.uBreath.value = this.currentBreath * Math.sin(haze.uniforms.uTime.value * 0.55);
  haze.uniforms.uAudio.value = Math.max(micEnergy, outEnergy);
  haze.uniforms.uIntensity.value = this.currentHazeIntensity;
  haze.uniforms.uCore.copy(this.currentHazeCore);
  haze.uniforms.uEdge.copy(this.currentHazeEdge);
}
```

Slow the interpolation so state changes never snap: `const lerpFactor = Math.min(dt * 2.6, 1.0);` (`:334`).
- [ ] **Step 5: Delete the dead shaders**: `rm src/orb/shaders/sphere.ts src/orb/shaders/backdrop.ts`, then `grep -rn "sphere\|backdrop" src/orb` must return only `THREE.SphereGeometry`/`PlaneGeometry` usages.
- [ ] **Step 6: Verify.** `npx tsc --noEmit && npm run build`. Dev server, `?mode=preview` (Task 11 adds it; until then use `?mode=orb`): the Orb must read as a luminous field with soft depth — no disc silhouette, no rim, no visible sphere edge at 220px and 264px, and `renderer.info.render.calls ≤ 3`. Check `chrome://gpu`-equivalent frame pacing by eye: motion stays smooth while resizing the window.
- [ ] **Step 7: Checkpoint** — stop; the user commits.

### Task 10: Living-field motion — breath, cohesion, directional flow, depth, parallax

**Files:**
- Modify: `src/orb/shaders/particle.ts:7-86` (vertex), `:88-109` (fragment)
- Modify: `src/orb/OrbRenderer.ts:220-304` (fields, layers, uniforms), `:313-380` (frame loop, pointer parallax)

**Interfaces:**
- Consumes: Task 9's `directional`/`cohesion`/`breath` config values, `Analyser` data, `motionEnabled` (Task 1, imported into `OrbView` and passed down as `animated: boolean`).
- Produces: new `OrbRenderer` constructor option `animated?: boolean` (default `true`); new entries in **both** `ShaderMaterial` uniform dictionaries (`particleMaterial.uniforms` in `init()`, `:280-292`, and the haze material from Task 9) — `stateCohesion`, `stateDirectional`, `stateFlowAxis` (`THREE.Vector3`), `uBreath`, `uParallax` (`THREE.Vector2`); a second particle field (`outerCount = 900`) and a reduced main field (`particleCount = 2600` — fewer pixels than today's 4200 while looking deeper; Spec §21). `stateFlowAxis` is normalised and re-aimed once per frame in `animate()` (`axis.set(Math.sin(t*0.13), Math.cos(t*0.09), Math.sin(t*0.07)).normalize()`), so the executing flow precesses instead of locking to one screen direction.

- [ ] **Step 1: Extend the vertex shader** with three behaviours and depth-cued alpha (replace `:43-77` wholesale; keep the existing spiral at `:31-41`):

```glsl
    // Turbulent reorganisation, scaled by state
    pos += curl * (0.075 * stateTurbulence);

    // Cohesion: verifying/stilled states tighten the shell instead of stopping
    float band = mix(1.16, 0.96, stateCohesion);
    float currentR = length(pos);
    pos = normalize(pos) * (currentR + (band - currentR) * stateCohesion * 0.35);

    // Directional flow: executing pushes the field along a slow precessing axis
    vec3 axis = normalize(stateFlowAxis);
    float along = dot(pos, axis);
    pos += axis * (along * stateDirectional * 0.24 * (0.6 + 0.4 * sin(time * 0.8 + phase * 6.28318)));

    // Breath: idle/soft states swell imperceptibly
    pos *= 1.0 + uBreath;
```

with `uniform float stateCohesion; uniform float stateDirectional; uniform vec3 stateFlowAxis; uniform float uBreath; uniform vec2 uParallax;` added to the uniform list, and the pull/expand block (`:51-63`) kept exactly as-is (it already implements listening-inward / speaking-outward).
- [ ] **Step 2: Depth and parallax instead of a shell boundary.** After the `mvPosition` computation (`:65-71`):

```glsl
    mvPosition.xy += uParallax * (0.55 - 0.45 * (length(pos) - 0.7));
    gl_Position = projectionMatrix * mvPosition;
    float depthFade = clamp((1.0 - (-mvPosition.z - 3.0) / 3.4), 0.35, 1.0);
```

and fold it into alpha: `vAlpha = coreFade * outerFade * (0.55 + 0.35 * sin(time * 1.6 + phase * 6.28318)) * depthFade;`. Lower the size attenuation spike (`:69-71`) to `clamp(pSize, 1.2, 13.0)` so far-field particles stop becoming blobs.
- [ ] **Step 3: Soften the fragment** (Spec §10 "excessive bloom", "obvious AI orb cliché"): replace `:100-105` with a gentler profile and a quarter-strength nucleus:

```glsl
    float gaussian = exp(-dist * dist * 14.0);
    float softEdge = smoothstep(0.5, 0.12, dist);
    float alpha = vAlpha * softEdge * 0.82;
    vec3 color = mix(vColor, vec3(1.0), gaussian * 0.22);
    gl_FragColor = vec4(color, alpha);
```

- [ ] **Step 4: Two layers for volume.** In `init()`, factor the field build into a local `buildField(count, rMin, rSpread, sizeMin, sizeMax, alphaScale)` helper and create (a) the main field at `count = 2600, rMin = 0.76, rSpread = 0.80, size 1.6–3.4`, and (b) an outer atmosphere `count = 900, rMin = 1.5, rSpread = 1.35, size 0.9–2.0` whose material shares the uniform objects (assign the *same* uniform value objects so both layers track state without a second update path) with a lower `alphaScale`. Outer layer rotates in the opposite direction at `dt * 0.03` for parallax depth (in `animate()`), and `dispose()` releases both.
- [ ] **Step 5: Pointer parallax** (Spec §10 "depth/parallax"; subtle, capped): store `private pointer = new THREE.Vector2(0, 0)` and `private pointerTarget = new THREE.Vector2(0, 0)`; `OrbView` attaches `pointermove`/`pointerleave` on the canvas parent and calls `renderer.setPointer(x, y)` with normalised `-1…1`; in `animate()`, lerp `pointer → pointerTarget` at `dt * 1.6` and set `uniforms.uParallax.value.set(pointer.x * 0.055, pointer.y * 0.045)`. When `animated === false`, set parallax to `0, 0` and stop advancing `time` (`prefers-reduced-motion` → a still, beautiful field rather than a frozen glitch).
- [ ] **Step 6: Thread `animated` down**: `OrbView.tsx` reads `motionEnabled(loadSettings().animations)` once per mount and passes `{ animated }` to `new OrbRenderer(canvas, {...})`; `MambaPresence` gains nothing new. Slow rotation (`OrbRenderer.ts:372-375`) becomes `if (this.animated) { … }`, and the thinking-only `2.5×` rotation multiplier is deleted (`executing`/`verifying` express themselves through the new uniforms).
- [ ] **Step 7: Verify.** `npx tsc --noEmit && npm run build`. `?mode=preview` screenshot of all eight phases; then, at 220px (`?mode=orb`), confirm: idle breathes almost imperceptibly; listening draws inward; thinking has richer internal flow but is not frantic; executing visibly flows along one slowly turning axis; verifying looks gathered and stable; speaking expands gently with TTS amplitude (test with a voice turn) and never becomes a spectrum bar; moving the pointer produces a ≤6px apparent shift, not a tilt. Open DevTools Performance for 5s: no long frames, `renderer.info.programs` unchanged, and total particles = 3500 (< 4200).
- [ ] **Step 8: Checkpoint** — stop; the user commits.

### Task 11: Presence component, dev preview grid, copy single-source

**Files:**
- Modify: `src/MambaPresence.tsx` (full rewrite, ~70 lines), `src/index.css:1433-1483` (presence section), `src/main.tsx:8-17`
- Create: `src/PresencePreview.tsx`

**Interfaces:**
- Consumes: `OrbState` (Task 8), `OrbView`, `motionEnabled`.
- Produces: `MambaPresence` with props `{ state, size?, inputNode?, outputNode?, className?, showCaption? }`; `.presence[data-state]` carrying per-state CSS custom properties `--p-glow`, `--p-ring`, `--p-tint`; `src/PresencePreview.tsx` default export.
- Removes: the `variant` prop and its `pulse`/`circle` motion-ring branch (`src/MambaPresence.tsx:16-20,100-169`) — no call site uses them (`src/MambaApp.tsx:537-544` and `src/FloatingOrb.tsx:128-133` both pass `variant="orb"`), and they place `ShieldAlert`/`AlertTriangle` icons inside the Orb, which Spec §10 forbids. Removes `label`/`showLabel` and `.presence-label` (`:173-178`, `src/index.css:1470-1483`) because the app already renders status text in `.status-block` — one copy source, not two (Spec §24: no duplication). The per-state `text`/`subtext` strings (`:39-92`) are deleted with it; `MambaApp.statusCopy` (Task 3) is the only status vocabulary.

- [ ] **Step 1: Rewrite `src/MambaPresence.tsx`** — the component becomes Orb + optional caption slot, driven by data attributes instead of 60 lines of hardcoded `rgba()` literals:

```tsx
import React from "react";
import { OrbView } from "./orb/OrbView";
import type { OrbState } from "./orb/OrbRenderer";

export type MambaPresenceState = OrbState;

export interface MambaPresenceProps {
  state: MambaPresenceState;
  size?: number;
  inputNode?: AudioNode | AnalyserNode | null;
  outputNode?: AudioNode | AnalyserNode | null;
  className?: string;
  /** Optional natural-language caption; MambaApp normally renders its own status block. */
  caption?: string;
}

export const MambaPresence: React.FC<MambaPresenceProps> = ({
  state = "idle",
  size = 220,
  inputNode,
  outputNode,
  className = "",
  caption,
}) => (
  <div className={`presence ${className}`} data-state={state} style={{ width: size, height: size }}>
    <div className="presence-aura" aria-hidden="true" />
    <div className="presence-orb-slot">
      <OrbView state={state} inputNode={inputNode} outputNode={outputNode} size={size} />
    </div>
    {caption && <div className="presence-caption">{caption}</div>}
  </div>
);

export default MambaPresence;
```

Update both call sites to the reduced prop set (`variant`/`showLabel` deleted; `label` → nothing).
- [ ] **Step 2: Replace the presence CSS** (`src/index.css:1433-1483`) with a token-driven aura that reads state without labels (Spec §13):

```css
.presence { position: relative; display: grid; place-items: center; }
.presence-orb-slot { position: relative; z-index: 2; display: grid; place-items: center; }
.presence-aura {
  position: absolute; inset: -14%;
  border-radius: 50%;
  background: radial-gradient(circle at 50% 50%, var(--p-tint) 0%, transparent 62%);
  opacity: 0.55;
  transition: opacity var(--dur-4) var(--ease-out), background var(--dur-4) var(--ease-out);
  pointer-events: none;
}
.presence[data-state="idle"]       { --p-tint: rgba(127, 212, 230, 0.10); }
.presence[data-state="listening"]  { --p-tint: rgba(127, 212, 230, 0.17); }
.presence[data-state="thinking"]   { --p-tint: rgba(147, 168, 239, 0.15); }
.presence[data-state="executing"]  { --p-tint: rgba(121, 174, 222, 0.15); }
.presence[data-state="verifying"]  { --p-tint: rgba(124, 207, 169, 0.13); }
.presence[data-state="speaking"]   { --p-tint: rgba(139, 220, 212, 0.15); }
.presence[data-state="permission"] { --p-tint: rgba(220, 178, 107, 0.16); }
.presence[data-state="error"]      { --p-tint: rgba(217, 139, 152, 0.14); }
.presence[data-state="listening"] .presence-aura,
.presence[data-state="speaking"] .presence-aura { opacity: 0.75; }
.presence-caption { margin-top: var(--s-2); font-size: var(--text-sm); color: var(--ink-3); }
```

No `filter: blur(48px)` (old `:1440`) — the haze shader already provides the atmosphere, and blurring a 264px layer costs a compositing pass (Spec §21).
- [ ] **Step 3: Fix `OrbView`'s non-existent Tailwind classes** (`src/orb/OrbView.tsx:77`: `relative flex items-center justify-center pointer-events-none bg-transparent` resolve to nothing — the file's own stylesheet header at `src/index.css:4-6` states Tailwind is not used). Replace the wrapper with the `presence-orb-slot`/canvas classes and keep the inline size, so the component stops depending on phantom utilities:

```tsx
return (
  <div className={`orb-view ${className}`} style={{ width: size, height: size }}>
    <canvas ref={canvasRef} className="orb-canvas" />
  </div>
);
```

plus `.orb-view { position: relative; display: grid; place-items: center; pointer-events: none; background: transparent; } .orb-canvas { width: 100%; height: 100%; display: block; background: transparent; }` in the presence CSS block. Keep the `ResizeObserver` and the dispose path exactly as-is (`:34-52`).
- [ ] **Step 4: Create `src/PresencePreview.tsx`** (visual-QA surface for Validation §25.4; never mounted by the shell) and mount it in `main.tsx` when `params.get("mode") === "preview"`, with an `aria-label` state caption under each cell. ~45 lines: an 8-cell responsive grid of `<MambaPresence size={168} state={…} caption={…} />`, cycling to a clicked state so screenshots can be taken of each; no transport, no audio nodes, no backend needed.
- [ ] **Step 5: Verify.** `npx tsc --noEmit && npm run build` (the deleted props must not be referenced anywhere — tsc proves it). Open `?mode=preview`: eight distinct, calm fields; nothing labelled "AGENT"/"MODEL"; aura transitions are smooth when clicking between states; `?mode=orb` still renders at 220px with no console errors and no `window.mambaDesktop` requirement.
- [ ] **Step 6: Checkpoint** — stop; the user commits.

---

## PHASE E — Supporting UI

### Task 12: Approval — "Mamba needs your permission", native and unweakened

**Files:**
- Modify: `src/SudoPopup.tsx` (structure, copy, focus/keyboard), `src/index.css:675-810`
- Modify: `src/MambaApp.tsx:278-289` (pass `reason` through — it is currently only toasted, so the user sees *why*)

**Interfaces:**
- Consumes: Task 2 `.btn/.btn-primary/.btn-ghost/.btn-danger`, `--warn-dim`, the existing `SudoRequest` shape (`src/SudoPopup.tsx:5-12`), `onApprove/onReject` → `sendPermissionResponse(true|false)` (`src/MambaApp.tsx:336-347`), which still sends `{type:"text",text:"yes"|"no"}`.
- Produces: `SudoPopup` props `pendingRequests`, `onApprove`, `onReject`, plus new optional `reason?: string`.
- Preserved, explicitly: no backdrop dismissal, no auto-approve, no timeout-silent-approve, and approval remains a deliberate click (Spec §15). `docs/ARCHITECTURE.md` §21's "an approval is delivered as an ordinary text message" stays true.

- [ ] **Step 1: Rewrite the dialog markup** to a single clear request: title `Mamba needs your permission`, one-line natural explanation, the requested action in a `--bg-1` block (mono only for the command itself), risk as a short sentence, then `Deny` (ghost) and `Allow` (primary). Delete the duplicated double-warning (`notice.amber` + `notice.rose`, `:144-160`), the `Sudo pending`/`Sudo secure` dot chip (`:87-97`, and `.sudo-dot` CSS `:797-803`), and the `.dialog-icon` shield (`:117`); those are what make it read "SECURITY ALERT" (Spec §15).
- [ ] **Step 2: Add keyboard + focus handling** (the current dialog has none — `role="alertdialog"` with no focus move, `:114-115`):

```tsx
useEffect(() => {
  if (!active) return;
  denyRef.current?.focus();
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Escape") { e.preventDefault(); handleReject(); }
  };
  window.addEventListener("keydown", onKey);
  return () => window.removeEventListener("keydown", onKey);
}, [active?.id]);
```

Escape = deny (never approve), `aria-modal="true"`, and a `Tab` cycle trapped between `denyRef`/`approveRef` (10-line keydown guard on the container, not a focus-trap library).
- [ ] **Step 3: Make the countdown honest**: at `expiresAt` the dialog shows "This request has expired", disables both buttons, and stops the 1s timer (`src/SudoPopup.tsx:33-43` currently clamps to `0s` forever). The backend's pending approval is untouched — the user can still answer from the composer, exactly as today; the UI simply stops implying urgency that expired.
- [ ] **Step 4: Restyle** with tokens: backdrop `rgba(5, 6, 9, 0.52)` + `blur(10px)` (down from full-screen `z-index:200` heavy blur, `:706-717`), dialog `--radius-lg`, `--shadow-3`, `--hair` border, no indigo glow (delete `0 0 50px rgba(99,102,241,.18)` at `:726`), command block `word-break: break-word` and `white-space: pre-wrap` so a long path stays readable (`:774-779`).
- [ ] **Step 5: Verify.** `npx tsc --noEmit`. Live check with the backend up: trigger a high-risk desktop action (Mamba's own approval flow, e.g. ask it to close a window it launched) and confirm — dialog blocks nothing else visually, Escape denies and sends `no` (turn resolves as cancelled), Allow sends `yes` and the paused step resumes (the `Brain._approved_step_ids` path), expiry disables the buttons without auto-resolving, and no `yes` is ever sent without a click. Also confirm the transcript shows the reason line (`reason` now rendered, `MambaApp.tsx:288`).
- [ ] **Step 6: Checkpoint** — stop; the user commits.

### Task 13: Toasts — short-lived, quiet, correct lifecycle

**Files:**
- Modify: `src/Toast.tsx` (full rewrite, ~110 lines), `src/index.css:811-886`, `src/MambaApp.tsx:53`

**Interfaces:**
- Consumes: Task 2 tokens/primitives.
- Produces: `useToast()` returning `{ toasts, addToast(text, kind?), dismiss }` with per-kind durations `{ milestone: 2400, info: 3600, success: 2600, reminder: 5200, error: 6000 }`, a `maxStack = 3` collapse, and pause-on-hover. Class names unchanged (`.toast-stack`, `.toast`, `.t-milestone` etc.), so no call-site edits beyond `useToast()`.

- [ ] **Step 1: Fix the timer leak and add hover pause**: store `setTimeout` ids per toast, clear on `dismiss` **and** unmount, and on `mouseenter` clear + remember remaining time, on `mouseleave` restart (Spec §18 "short-lived, non-intrusive" — a toast that vanishes while you read it is neither).
- [ ] **Step 2: Emit the milestone tone** (`.toast.t-milestone` exists at `src/index.css:858` but is never produced because `milestone` collapses to `t-info`, `src/Toast.tsx:21-28`). Milestones are the Orb's job, not a persistent notification: render them as one-line, low-contrast, centered text under the status caption and give them the shortest duration.
- [ ] **Step 3: Remove per-type icons** (every toast today shows `Bell`, `:49`) — Spec §5 forbids excessive icons. Use a 2px left rail tinted `--ok/--danger/--warn/--accent` plus the existing label.
- [ ] **Step 4: Restyle the stack** to tokens (`--bg-1` surface, `--hair`, `--radius-md`, `--shadow-2`, `--dur-3` `--ease-glide` entrance, no spring, `motionEnabled` gated, `prefers-reduced-motion` already handled globally by Task 1).
- [ ] **Step 5: Verify.** `npx tsc --noEmit && npm run build`; dev-server: trigger an error (stop the backend) and a milestone, confirm ≤3 stacked, hover holds a toast, dismissal clears its timer (assert no state update after unmount in the console), and `useToast()` is called with no arguments in `MambaApp.tsx:53`.
- [ ] **Step 6: Checkpoint** — stop; the user commits.

### Task 14: Settings — premium, complete, no dead knobs

**Files:**
- Modify: `src/SettingsPanel.tsx` (full rewrite), `src/settingsStore.ts`, `src/index.css:887-980`
- Modify: `src/wake/controller.ts` (add `updateOptions`), `src/FloatingOrb.tsx` (apply it), `electron/preload.cjs` + `src/mambaDesktop.d.ts` (one optional wake-opts channel)

**Interfaces:**
- Consumes: Task 2 `.surface/.field/.label/.switch` (the `.switch` rule keeps its name and its `aria-checked` selector, `:977-978`), `motionEnabled`.
- Produces: `MambaSettings = { autoStart: boolean; wakeWordEnabled: boolean; wakePhrase: string; sensitivity: number; animations: boolean }` (defaults unchanged for the five kept keys); `WakeController.updateOptions(opts: Partial<WakeControllerOptions>): void` that stores the options and rearms; `window.mambaDesktop.notifyWakeOptions?.({phrase, sensitivity})` → `mamba:wake-options` → forwarded to the orb window's `onWakeOptions` handler.
- Removes: `micDeviceId`, `voice`, `backgroundVideo`, `avatarStyle` (all four are written and never read — verified: `src/audio.ts:212,222` accepts `voice`/`avatarStyle` but `MambaApp.tsx:310` calls `connect()` with no arguments, and nothing reads the other two), and `src/TextChatFallback.tsx` (unreferenced anywhere except its own definition and `docs/ARCHITECTURE.md:524`; its ~40 Tailwind utilities don't exist in this build, so it could only render unstyled if mounted — the composer is already the text path).

- [ ] **Step 1: Reduce and retype the store.** Keep `loadSettings`/`saveSettings` semantics identical (`src/settingsStore.ts:57-103`, including the `POST /api/settings` mirror — `api/server.py:499-504` stores the dict opaquely, so removing keys is safe). Delete the four inert fields and their defaults; `NEVER_PERSIST` (`:51`) stays as-is.
- [ ] **Step 2: Add `WakeController.updateOptions`** (mirrors the existing `setPhrase` precedent, `src/wake/controller.ts:97-103`):

```ts
updateOptions(opts: Partial<WakeControllerOptions>): void {
  this.opts = { ...this.opts, ...opts };
  if (this.running) this.rearm();
}
```

`start()` already re-reads `this.opts.phrase`/`sensitivity` (`:56-58`), so a re-arm applies them without touching the engine.
- [ ] **Step 3: Wire the settings → orb-window path** (the wake listener lives in the orb renderer, not the main window): `preload.cjs` gains `notifyWakeOptions: (opts) => ipcRenderer.send("mamba:wake-options", opts)` and `onWakeOptions: (cb) => { … }` beside the existing `onWakeSetting` (`electron/preload.cjs:42-43`); `main.cjs` forwards to `orbWindow.webContents.send("mamba:wake-options", opts)` exactly like `mamba:wake-setting` does; `FloatingOrb` subscribes and calls `controller.updateOptions({ phrase, sensitivity })`. Add both to `src/mambaDesktop.d.ts` as **optional** members (every bridge field is optional by contract).
- [ ] **Step 4: Rebuild the panel as three labelled groups** — *General* (Start with Windows, Motion), *Voice & activation* (Wake word, Wake phrase, Sensitivity) — using `.settings-section` + `.label` headings, one `.settings-row` per control, `--s-4` rhythm, and a `range` input styled to tokens for sensitivity (60 default, 0–100) and a `.field` text input for the phrase. `SettingsPanel` props gain `wakePhrase`, `sensitivity`, `animations` and their `onChange` handlers; `MambaApp` wires them to `saveSettings` + `notifyWakeOptions` (mirroring how `handleWakeWordChange` already works, `src/MambaApp.tsx:371-384`). Copy stays human: "Motion", "Wake word", "How ready Mamba should be to hear the wake word" — no "sensitivity threshold" jargon (Spec §18).
- [ ] **Step 5: Make `animations` actually mean something**: `MambaApp` passes nothing new, but Tasks 6/10/13 read `loadSettings().animations`; on change, `window.dispatchEvent(new Event("mamba:settings-changed"))` so mounted surfaces pick it up on their next render (no new state layer), and the Motion switch is documented as taking effect on the next interaction.
- [ ] **Step 6: Delete `src/TextChatFallback.tsx`**, then `grep -rn "TextChatFallback" src/ electron/` must be empty (the doc line is corrected in Task 17).
- [ ] **Step 7: Verify.** `npx tsc --noEmit && npm run build`; dev-server: toggle each control, reload, confirm persistence in `localStorage["mamba.settings.v1"]` and in `GET /api/settings` (backend up), and that Motion off removes the rail/toast motion. Desktop: change the phrase to "hello mamba", say it — the wake listener must rearm and trigger (this is the only functional change in this task; it is Spec §18's "clear hierarchy" made real rather than a read-only field).
- [ ] **Step 8: Checkpoint** — stop; the user commits.

### Task 15: Browser surface — belongs to Mamba

**Files:**
- Modify: `src/BrowserAgent.tsx` (chrome markup, dead-code removal), `src/index.css:981-1432`

**Interfaces:**
- Consumes: Task 2 `.surface/.field/.icon-btn/.pill`, Task 1 tokens, `motionEnabled`.
- Produces: unchanged props contract (`url`, `onClose`, optional `actionTrigger`) and unchanged iframe/tab model; **all class names the voice-automation switch touches stay** (`src/BrowserAgent.tsx:254-409` resolves against the iframe's document, not the shell, but the tab/panel selectors `.browser-tabbar`, `.browser-tab`, `.browser-frame-wrap`, `.browser-panel` are kept verbatim so nothing else has to be re-learned).

Spec §17: functionality untouched — no new tabs model, no proxy change, no automation change. This task is layout, spacing, hierarchy, transitions, and visual belonging.

- [ ] **Step 1: Delete dead code** (verified unreferenced): `isLocalConnected`, `localLogs`, `showLocalConsole` (`:68-70`), `copiedSection` + `copyToClipboard` (`:71, 615-619`), `showDebugPanel`, `iframeOnLoadCount`, `jsErrors`, `networkErrors` (`:74-80`), the unused `onActionComplete` prop (`:45-55, 57-61` — `MambaApp.tsx:579-582` never passes it), and the unused `AnimatePresence` import (`:27`).
- [ ] **Step 2: Rename the off-brand identifiers**: `id="elysia-playwright-automation-hud"` → `id="mamba-browser-surface"` (`:623`), console prefixes `[Elysia Browser]`/`[Elysia Browser Hub]` → `[Mamba Browser]` (`:245, 258`). No behaviour change; these are the clearest "not Mamba" tells in the app.
- [ ] **Step 3: Replace the hover-only floating navbar** (`:899-957`, `src/index.css:1393`) with a persistent 40px toolbar inside `.browser-panel`: back / forward / reload / home as `.icon-btn`, then the address `.field` growing to fill, then close. Hover-only controls fail Spec §12 ("animations that slow the user down") and §20 ("desktop feel"): the toolbar is where a desktop user looks for them.
- [ ] **Step 4: Tokenize the whole browser CSS block** (`src/index.css:983-1432`): every `rgba(232,236,244,α)` → `--ink/--ink-2/--ink-3`; surfaces → `--surface-*`/`--bg-1`; duplicated cyan/rose literals → `--accent*`/`--danger*`; `radius: 18px` (`:1005`) → `var(--radius-lg)`; geometry → `width: min(1100px, calc(100vw - 2 * var(--s-6)))`, `height: calc(100vh - 2 * var(--s-6))`; delete the two ambient glow divs (`src/BrowserAgent.tsx:629-630`) and the `#000` gradients (`:690`, `:1331`, `:1356`) in favour of `--bg-0`; the YouTube-red `#ef4444` (`:808`) becomes `var(--danger)`.
- [ ] **Step 5: Re-hierarchy the home dashboard** (quicklinks at `:736-748`, cards at `:753-802`): one search field (`.field`, `--text-md`), a `.label` caption per group, pills in a wrap layout with `--s-2` gap, and no `motion.div` infinite opacity pulses (replace `:691-708` looping animations with a single `--dur-3` `--ease-glide` fade, gated by `motionEnabled`).
- [ ] **Step 6: Verify.** `npx tsc --noEmit && npm run build`. Dev-server: open the browser view, search, open a link in a new tab, close a tab, use back/forward/reload, and trigger a restricted/unknown-URL state; confirm the surface reads as part of Mamba (same charcoal, same controls, same radii) and that no iframe/automation wiring broke (`window.postMessage` `NAVIGATE` still handled, `:242-251`).
- [ ] **Step 7: Checkpoint** — stop; the user commits.

---

## PHASE F — Window modes and completion

### Task 16: Full / compact / orb / voice all feel like one product

**Files:**
- Modify: `electron/main.cjs:265-281` (main window chrome), `:314-384` (orb window), `src/index.css:1485-1496` (responsive) and new `.is-compact` rules
- Modify: `src/MambaApp.tsx:442` (mode class), `src/FloatingOrb.tsx:118-153`

**Interfaces:**
- Consumes: `--bg-window` (must equal `main.cjs` `backgroundColor`), Task 3's header drag region, `--orb-size`.
- Produces: `.mamba-shell.is-compact` (window width ≤ 720) and `.mamba-shell.is-short` (height ≤ 620) modifiers computed from a single `useState` + one `resize` listener in `MambaApp`; main window becomes frameless with native caption buttons.

- [ ] **Step 1: Make the main window read as desktop software, not a trapped website** (Spec §20). In `electron/main.cjs:265-281` add:

```js
frame: false,
titleBarStyle: "hidden",
titleBarOverlay: { color: "#0b0d10", symbolColor: "#a5adba", height: 34 },
backgroundColor: "#0b0d10",
```

Keep `width: 1100, height: 750, minWidth: 480, minHeight: 600, show: false, autoHideMenuBar: true`, the close→hide handler (`:284-291`) and every `webPreferences` value unchanged (`contextIsolation: true`, `nodeIntegration: false`, `webSecurity: true`). The header drag region from Task 3 now does real work; the native overlay keeps Windows caption buttons (and therefore `Win+…` shortcuts and Snap layouts) functioning.
- [ ] **Step 2: Add the mode modifiers** in `MambaApp` and the compact treatments:

```css
.mamba-shell.is-compact .header-actions { opacity: 1; }
.mamba-shell.is-compact :root, .mamba-shell.is-compact { --orb-size: 196px; }
.mamba-shell.is-compact .status-hint, .mamba-shell.is-compact .composer-hint { display: none; }
.mamba-shell.is-compact .mamba-header { padding: var(--s-2) var(--s-3); }
.mamba-shell.is-compact .mamba-footer { padding: var(--s-2) var(--s-3) var(--s-3); }
.mamba-shell.is-short { --orb-size: 176px; }
.mamba-shell.is-short .status-block { min-height: 0; }
```

so the narrow/resized window is the same product with less, not a different design (Spec §19).
- [ ] **Step 3: The floating Orb window** (`main.cjs:314-384`): keep `220×220`, `frame: false`, `transparent: true`, `alwaysOnTop`, `skipTaskbar`, `hasShadow: false`, `backgroundColor: "#00000000"`, close→hide, and `?mode=orb`. Set `MambaPresence size={208}` inside `.orb-window` (padding for the aura at `inset: -14%`), keep `WebkitAppRegion: "drag"` on the shell and `no-drag` on the click target (`src/FloatingOrb.tsx:122,149`), and give `.wake-armed-dot` a static `--accent` glow (Task 2). Voice mode is not a separate screen: the same orb window already shows listening/speaking via `mamba:state` (Spec §14, §19).
- [ ] **Step 4: Replace the two legacy `@media` blocks** (`src/index.css:1485-1496`) with the mode system: keep the 640px header/footer compaction but express it through the same token values, and let `.transcript-panel` clamp to `min(480px, calc(100vw - var(--s-4)))`.
- [ ] **Step 5: Verify.** `npm run build && npm run electron:test` — the existing smoke test walks all Orb states (`main.cjs:632-638`, updated in Task 8) and must still report success. Then `npm run electron:dev` and check by hand: main window has no browser-y title bar, is draggable from the header, caption buttons work, snapping works; shrink to 520×640 → compact layout, nothing clipped or overlapping; the floating orb reads at 220px and stays draggable and clickable; wake word still activates from DORMANT.
- [ ] **Step 6: Checkpoint** — stop; the user commits.

### Task 17: Whole-product validation pass and documentation truth

**Files:**
- Modify: `docs/ARCHITECTURE.md:470-473` (§16 Orb), `:524` (§20 component list)
- No source changes unless a validation step fails.

- [ ] **Step 1: Token discipline sweep.** Run:

```bash
cd /c/mamba && npx tsc --noEmit && npm run build
grep -nE "#[0-9a-fA-F]{3,8}\b" src/index.css | grep -v ":root" | head -30
grep -ncE "rgba\(" src/index.css
grep -nE "transition:.*[0-9](\.[0-9])?s|animation:.*[0-9](\.[0-9])?s" src/index.css | grep -v "var(--dur" | head -30
```

Expected: no hex outside the `:root` block; the only `rgba(` occurrences are the alpha token definitions inside `:root` plus the eight `--p-tint` presence values (Task 11) and any documented `color-mix` fallback; zero hardcoded durations/easings. Anything left is converted in this task.
- [ ] **Step 2: Dead-reference sweep**: `grep -rn "TextChatFallback\|shaders/sphere\|shaders/backdrop\|elysia\|Elysia\|--cyan\|--rose\|--amber\|--emerald" src/ electron/` → empty.
- [ ] **Step 3: Backend untouched proof**: `grep -rn "^from src\|import src" api/ core/ agents/ tasks/ skills/ models/ permissions/ verification/ memory/ 2>/dev/null` is empty and the only non-`src/`, non-`electron/`, non-`docs/` file this plan may have touched is `src/wake/controller.ts` (presentation service) — list the actual changed files from the working tree with the user, and confirm no `.py` file was edited by anyone executing this plan.
- [ ] **Step 4: Behaviour regression checklist** (each item is a manual check in `npm run electron:dev`, and each must be reported as observed, not assumed):
  1. typed question → streamed provisional text → authoritative final text (`MambaApp.tsx:102-130`);
  2. Stop button cancels mid-turn and the cancelled text never persists;
  3. voice turn: listening → orb inward pull → answer spoken → orb outward breathing; barge-in stops playback;
  4. wake word from DORMANT activates the session;
  5. approval: pause → SudoPopup → Allow resumes the pending step, Deny cancels;
  6. transcript search/filter/minimize/resize/`Ctrl+K`/`F4`/`Esc`;
  7. browser view opens/navigates/closes;
  8. `prefers-reduced-motion: reduce` (OS setting) → no caret pulse, no parallax, still-orb field, instant rail/toast motion;
  9. settings: Motion + wake phrase + sensitivity persist and rearm;
  10. tray Show/Hide/Quit and `Ctrl+Space` unchanged.
- [ ] **Step 5: Fix whatever Step 4 exposes**, then re-run Steps 1–4. If a check cannot be performed (e.g. microphone absent), say so explicitly in the report rather than claiming it passed.
- [ ] **Step 6: Update the architecture doc so it stays truthful** — `docs/ARCHITECTURE.md:471` shader list becomes "particle + haze shaders" (sphere/backdrop deleted); `:472`'s exact-state list gains the two presentation-only phases with an explicit note that `LiveState`, `mamba:state` vocabulary and lifecycle logic are unchanged and that `executing`/`verifying` are derived from the existing `progress` milestones; `:524` loses `TextChatFallback (no-microphone fallback)` and gains `ThreadEntry` + the rail/stage split. Do not restate anything else in the doc.
- [ ] **Step 7: Final report** (Spec §26): design system created; components redesigned; Orb changes; motion changes; files changed; dependencies added (expected: **none**); `tsc` result; build result; remaining visual issues; recommended next refinement. No git commands, no commits, no pushes.

---

## Self-review notes (author, 2026-10-04)

- **Spec coverage:** §1 identity → Tasks 1, 3, 9, 10; §2 architecture rule → Global Constraints + Tasks 3/8/14/16 scope limits; §3 inspect-before-coding → done (this plan is written against `file:line` facts, all verified by reading the sources); §4 outcomes-not-machinery → Tasks 3, 5, 7, 11, 13; §5/§6 visual system + typography → Tasks 1, 2; §7 main experience → Task 3, 7; §8 conversation → Tasks 5–7; §9 composer → Task 4; §10/§11 Orb + states → Tasks 8–11; §12 motion → Tasks 1, 2, 6, 10, 11, 13, 15 + reduced-motion floor; §13 presence → Task 11; §14 voice → Tasks 8, 10, 16 (voice is not a separate screen — the orb *is* the voice UI); §15 approval → Task 12; §16 streaming → Tasks 6, 7; §17 browser → Task 15; §18 transcript/toast/settings → Tasks 5, 6, 13, 14; §19 window modes → Task 16; §20 desktop feel → Tasks 3, 16; §21 performance → Tasks 9, 10 (particle count reduced 4200 → 3500, standard-material + two lights removed, `backdrop-filter` uses removed), §24 → Tasks 5, 11, 14, 15; §25 validation → Task 17.
- **Known scope decisions, flagged for the owner:** (1) `executing`/`verifying` are added as *derived presentation phases* read from the existing `progress` string channel — the alternative (a new WS field) would have meant touching `api/` and is rejected; (2) Task 14 adds one optional IPC message (`mamba:wake-options`) so Settings edits take effect without restarting the orb renderer — the alternative was silently ignoring the fields, which Spec §18 forbids; (3) Task 12's expiry now disables the buttons instead of showing `0s` forever — stricter, never weaker; (4) task 14 deletes four unread settings keys and `TextChatFallback.tsx`; (5) no web fonts are introduced (system "Segoe UI Variable" text/display faces), so a self-hosted brand face remains an open refinement; (6) `micDeviceId` is deleted rather than wired, because selecting a capture device means touching `getUserMedia` in `src/audio.ts`, which is behavior, not presentation.

- **Type consistency:** `OrbState` (Task 8) is the single definition and `MambaPresenceState = OrbState`; the `executing`/`verifying` spellings match everywhere (statusCopy keys in Task 3, `STATE_VISUALS` in Task 9, aura tokens in Task 11, accepted-state lists in Task 8 Step 4); `ThreadEntry`'s `TranscriptEntry` shape matches `src/MambaApp.tsx:14-22`; `motionEnabled(settingsAnimations: boolean)` is defined once in Task 1 and called identically in Tasks 4, 6, 10, 11, 15; the `StateVisualConfig` keys introduced in Task 9 (`hazeCore`, `hazeEdge`, `hazeIntensity`, `breath`, `cohesion`, `directional`) are exactly the ones consumed by Task 9 Step 4 and Task 10 Step 1, where the TS field `breath` maps to the shader uniform `uBreath` and `cohesion`/`directional` map to `stateCohesion`/`stateDirectional`.


