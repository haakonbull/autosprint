# Destination

*Target-state specification for this project. What + why, in plain language. Technical decisions live in XXXXXXXX. Last reviewed: YYYY-MM-DD by <user>.*

## How to use this document

A destination document describes the target shape of the repo.

It is analogous to a destination we want to travel to. It describes where we want to go, not how to get there.
Think of it as a GPS coordinate that a Google Maps user is navigating toward.
In the analogy, Google Maps uses the current location and the destination to plan a route.
In our situation, the AI assistant uses the current state of the codebase and `destination.md` to plan which tasks to do.

The destination will typically contain information that we already know, and many things that we don't know the concrete answer to yet.
When the answer is already known, the document should state the decision directly.
When the answer is not yet known, the destination can name the topic or question, and specify that the destination of the repo will be that we have an answer or solution to this that is well thought through, that has a clear rationale, that is consistent with the project goals, and where the rationale behind the choice is documented in `adr.md`. In that case it will be up to autosprint to determine this choice.

The destination document will describe the state we want to come to. It will not describe how we will come to that state. For instance, the destination document might state that we want good code quality according to best practice. It will not describe refactoring, since that is related to the journey not the destination. So even though good refactoring routines are important, it is not within the scope of the destination document to describe how to refactor — the destination document will describe the state we aim for after refactoring. Because the destination is stable while the journey is not, this document is re-read often but rewritten rarely.

> **Every parameter has a concrete answer or is explicitly open.** If you know the answer, write it directly under the section, replacing the italic prompt. If you don't, write a **destination-shaped sentence** describing what the repo will have once the question is answered, followed by an explicit `*(Open — autosprint to decide.)*` italic marker. Example for an unanswered Test strategy: *"We have a well thought-through testing strategy with a clear rationale specified in `adr.md`. (Open — autosprint to decide.)"*. The destination-shaped sentence keeps the file consistent (every section reads as a destination, not a process); the italic marker is the unambiguous signal to the planner that this section is still pending. Autosprint resolves open parameters by recording the full rationale in `adr.md`, appending a status marker at the end of the resolved section, and adding a one-line receipt to `## AI-resolved questions` at the bottom. Do not write "TBD" or invented placeholders — silent assumptions are the failure mode this rule prevents.
>
> **Section ownership.** Human-authored content lives above the `## AI-resolved questions` and `## AI-generated subgoals` headings at the bottom. Agents may append to those sections only, and may append a single status-marker blockquote at the end of a section once they've resolved that section's open question. Agents never modify the human content above those markers.
>
> **Status marker format** (used by agents when resolving an open question): `> **Status:** resolved <YYYY-MM-DD> — <one-line answer>. See` `` `adr.md` `` `<ADR title or date>.`
>
> **Promotion path.** When you want a resolution to graduate into the main spec, edit the original section to write the chosen answer in (replacing the prompt or "open" line), delete the status marker, and delete the receipt from `## AI-resolved questions`. The decision now reads as a normal human-authored answer; rationale stays in `adr.md` as history.

## Purpose

A genuinely fun 3D shooter with amazing graphics — the kind of game that gives a player a real moment of happiness the first time they play it. The player fights back living, moving enemies, not a shooting gallery. Serves as an autosprint demo on a moderately ambitious 3D project with both gameplay logic and rendering polish on the line.

## Users

A single human player at a laptop with a mouse and a keyboard who wants a few minutes of high-quality arcade-style action. Also: someone evaluating autosprint on a project where "good enough" isn't enough — visual polish is part of the spec, not an afterthought.

## Desired behaviour

In priority order:

1. Running `python -m game` (or `uv run game`) opens a window in third-person or first-person view onto a single 3D scene with the player character standing in it.
2. **Mouse** controls the camera / aim direction. **W / A / S / D** move the character forward / left / back / right relative to the camera.
3. **Space** triggers a jump with a believable arc and landing.
4. **Left mouse button** fires the primary weapon (a ranged projectile that travels in the aim direction and explodes on impact).
5. **Right mouse button** triggers the secondary action — either aim-down-sights for the primary weapon, or a block / parry stance when the melee weapon is equipped.
6. **C** swings the melee weapon (a sword) in a short arc in front of the character. Targets in the arc get hit.
7. **V** cycles between weapon loadouts (primary ranged ↔ melee). The currently-equipped weapon is visible on the character.
8. Enemies exist in the scene and are a real threat: they move toward the player rather than standing still. Some enemies carry swords and press in for close-quarters attacks; some carry ranged weapons and shoot back at the player with projectiles that can be dodged. Hitting an enemy with a projectile triggers an explosion (~0.6 s lifecycle: impact flash → expanding sphere → fading particles → done). Hitting an enemy with the sword triggers a slash effect and destroys the enemy without an explosion.
9. The scene looks amazing: lighting that casts shadows, a textured ground, a skybox or far-distance backdrop, and weapons / characters that read as solid 3D objects (not flat sprites in a 3D scene). The player character and the enemies have deliberate, appealing designs — recognizable figures with faces, weapons and personality, never placeholder primitives left as-is.

## Out of scope

- Music and ambient sound effects.
- Multiplayer or networked play.
- Persistent save state across runs.
- Multiple levels, level transitions, or a level editor (one scene is enough).
- Configurable key bindings (the spec fixes the layout above).
- Mobile / gamepad support.

## Code-quality invariants

- Python 3.12+. A 3D-capable engine (`panda3d`, `ursina`, or `pygame` + `PyOpenGL` / `moderngl`) is acceptable for rendering. Pick one and stick to it; the choice is recorded in `adr.md`.
- Game logic (movement, aim direction, weapon-switch state, projectile motion, hit detection, explosion lifecycle, jump arc) lives in pure functions or simple classes that can be tested without opening a real window — clean separation between the logic layer and the rendering layer.
- The full test suite (`pytest -m "not slow"`) passes on every commit.
- Explosion is a small state machine (impact → expanding → fading → done) that is fully testable as data, not pixels.
- Visual polish (lighting, shadows, textures, models) is intentional, not accidental — each visual element is justified somewhere (the spec, an ADR, or a one-line comment near the asset load). No "we'll improve graphics later" placeholders left in the worktree past the sprint that introduced them.

## Test strategy

Unit tests for: player movement on each of `W` / `A` / `S` / `D` (relative to camera direction), jump arc (rising → peak → falling → grounded), weapon-switch state (`V` cycles, `C` swings melee, `LMB` fires ranged, `RMB` aims/blocks), projectile motion and despawn, projectile-enemy collision, enemy movement toward the player, enemy fire and its effect on the player, melee hit detection (which enemies are inside the swing arc), explosion state transitions, and any score / lives bookkeeping the game ends up tracking. A single smoke test that constructs an initial game state with a few targets is enough for the rendering layer.

## AI-resolved questions

## (autosprint appends here as it answers open questions during sprints.)

## AI-generated subgoals

## (autosprint appends product/behavioural subgoals it proposes during planning here.)
