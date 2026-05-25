# Destination

_Target-state specification for this project. What + why, in plain language. Technical decisions live in `adr.md`. Last reviewed: 2026-05-16 by Haakon._

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
> **Status marker format** (used by agents when resolving an open question): `> **Status:** resolved <YYYY-MM-DD> — <one-line answer>. See ` `` `adr.md` `` ` <ADR title or date>.`
>
> **Promotion path.** When you want a resolution to graduate into the main spec, edit the original section to write the chosen answer in (replacing the prompt or "open" line), delete the status marker, and delete the receipt from `## AI-resolved questions`. The decision now reads as a normal human-authored answer; rationale stays in `adr.md` as history.

## Purpose

A really fun game that gives players a lot of happiness when they play it.

## Users

A single human player with a laptop.

## Desired behaviour

A 3D game where the player uses `w`, `a`, `s`, `d`, space, `c`, and `v` to control the character.
The player can shoot, and switch to a sword by pressing `c`.
The player can shoot things that explode — because it's fun.

Should look and feel realistic and polished.

## Out of scope

- Music and sound effects.
- Multiplayer or networked play.
- Persistent save state across runs.
- Multiple levels or maps (one level is enough).

## Code-quality invariants

- Python 3.12+. A 3D-capable library (`panda3d`, `ursina`, or `pygame` with `PyOpenGL`) is acceptable for rendering.
- Game logic (player movement, weapon switching, projectile motion, hit detection, explosion lifecycle) lives in pure functions or simple classes that can be tested without opening a real window — clean separation between the logic layer and the rendering layer.
- The full test suite (`pytest -m "not slow"`) passes on every commit.
- The explosion is a small state machine (impact → expanding → fading → done) that is fully testable as data, not pixels.

## Test strategy

Unit tests for: player movement on each of `w` / `a` / `s` / `d`, weapon-switch state (`c` toggles between shoot and sword), projectile motion and despawn, sword-swing hit detection, projectile-target collision, explosion state transitions, and any score / lives bookkeeping the game ends up tracking. A single smoke test that constructs an initial game state is enough for the rendering layer.

## AI-resolved questions

_(autosprint appends here as it answers open questions during sprints.)_

## AI-generated subgoals

_(autosprint appends product/behavioural subgoals it proposes during planning here.)_
